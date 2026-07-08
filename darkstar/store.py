"""darkstar persistence: a single DuckDB file holding the polled Jira snapshot.

Three datasets, written by the poller and read (read-only) by the dashboards:
  - issues:      one row per Jira issue (current snapshot, upserted by key)
  - transitions: append-only status changes from each issue's changelog
  - sync_meta:   one row tracking the incremental watermark and last full sync

All TIMESTAMP columns hold naive UTC datetimes; the poller normalizes Jira's tz-aware
values to UTC before writing. Every function takes an explicit DuckDB connection so the
caller owns its lifecycle.
"""
from __future__ import annotations

import logging
from dataclasses import astuple, dataclass
from datetime import date, datetime

import duckdb

logger = logging.getLogger("darkstar.store")


@dataclass(frozen=True)
class IssueRow:
    """Current snapshot of a single Jira issue."""

    key: str
    id: int
    project: str
    issuetype: str
    status: str
    status_category: str
    priority: str
    summary: str
    assignee: str | None
    assignee_account_id: str | None
    reporter: str | None
    business_lead: str | None
    parent_key: str | None
    created: datetime
    updated: datetime
    resolutiondate: datetime | None
    planned_start: date | None
    target_end: date | None
    labels: list[str]
    fetched_at: datetime


@dataclass(frozen=True)
class TransitionRow:
    """A single status change taken from an issue's changelog."""

    key: str
    to_status: str
    changed_at: datetime
    seq: int


@dataclass(frozen=True)
class SyncMeta:
    """Bookkeeping for the poll loop; a single row in sync_meta."""

    last_incremental_sync: datetime | None
    last_full_sync: datetime | None
    issue_count: int
    transition_count: int
    updated_at: datetime | None


@dataclass(frozen=True)
class MergeRequestRow:
    """A merged GitLab merge request attributed to a roster member."""

    id: int
    project_path: str
    iid: int
    author_account_id: str
    title: str
    merged_at: datetime
    web_url: str
    fetched_at: datetime


# Column order shared by the issues DDL and the upsert statement; keep in sync with IssueRow.
_ISSUE_COLUMNS: tuple[str, ...] = (
    "key", "id", "project", "issuetype", "status", "status_category", "priority",
    "summary", "assignee", "assignee_account_id", "reporter", "business_lead", "parent_key",
    "created", "updated", "resolutiondate", "planned_start", "target_end",
    "labels", "fetched_at",
)

# Column order shared by the merge_requests DDL and its upsert; keep in sync with MergeRequestRow.
_MR_COLUMNS: tuple[str, ...] = (
    "id", "project_path", "iid", "author_account_id", "title", "merged_at", "web_url", "fetched_at",
)

_SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS issues (
    key                 VARCHAR PRIMARY KEY,
    id                  BIGINT NOT NULL,
    project             VARCHAR NOT NULL,
    issuetype           VARCHAR NOT NULL,
    status              VARCHAR NOT NULL,
    status_category     VARCHAR NOT NULL,
    priority            VARCHAR NOT NULL,
    summary             VARCHAR NOT NULL,
    assignee            VARCHAR,
    assignee_account_id VARCHAR,
    reporter            VARCHAR,
    business_lead       VARCHAR,
    parent_key          VARCHAR,
    created             TIMESTAMP NOT NULL,
    updated             TIMESTAMP NOT NULL,
    resolutiondate      TIMESTAMP,
    planned_start       DATE,
    target_end          DATE,
    labels              VARCHAR[] NOT NULL,
    fetched_at          TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS transitions (
    key        VARCHAR NOT NULL,
    to_status  VARCHAR NOT NULL,
    changed_at TIMESTAMP NOT NULL,
    seq        INTEGER NOT NULL,
    PRIMARY KEY (key, seq)
);

CREATE TABLE IF NOT EXISTS sync_meta (
    id                    INTEGER PRIMARY KEY,
    last_incremental_sync TIMESTAMP,
    last_full_sync        TIMESTAMP,
    issue_count           INTEGER NOT NULL,
    transition_count      INTEGER NOT NULL,
    updated_at            TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS merge_requests (
    id                BIGINT PRIMARY KEY,
    project_path      VARCHAR NOT NULL,
    iid               BIGINT NOT NULL,
    author_account_id VARCHAR NOT NULL,
    title             VARCHAR NOT NULL,
    merged_at         TIMESTAMP NOT NULL,
    web_url           VARCHAR NOT NULL,
    fetched_at        TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS mr_files (
    mr_id BIGINT NOT NULL,
    path  VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS gitlab_sync_meta (
    id        INTEGER PRIMARY KEY,
    last_sync TIMESTAMP
);
"""


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    """Open a read-write DuckDB connection at db_path, creating the file if absent."""
    return duckdb.connect(db_path)


def initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the issues, transitions, and sync_meta tables if they do not exist."""
    connection.execute(_SCHEMA_SQL)
    logger.debug("schema initialized")


def upsert_issues(connection: duckdb.DuckDBPyConnection, issues: list[IssueRow]) -> int:
    """Insert or replace issue rows keyed by issue key. Returns the number written."""
    if not issues:
        return 0
    placeholders = ", ".join(["?"] * len(_ISSUE_COLUMNS))
    sql = f"INSERT OR REPLACE INTO issues ({', '.join(_ISSUE_COLUMNS)}) VALUES ({placeholders})"
    connection.executemany(sql, [list(astuple(issue)) for issue in issues])
    return len(issues)


def replace_transitions(
    connection: duckdb.DuckDBPyConnection,
    keys: list[str],
    transitions: list[TransitionRow],
) -> int:
    """Replace all transitions for the given issue keys with the supplied rows.

    Existing transitions for the keys are deleted first so a re-fetched changelog
    does not duplicate rows. Returns the number of transition rows written.
    """
    if keys:
        connection.executemany("DELETE FROM transitions WHERE key = ?", [[key] for key in keys])
    if transitions:
        connection.executemany(
            "INSERT INTO transitions (key, to_status, changed_at, seq) VALUES (?, ?, ?, ?)",
            [list(astuple(transition)) for transition in transitions],
        )
    return len(transitions)


def get_sync_meta(connection: duckdb.DuckDBPyConnection) -> SyncMeta | None:
    """Return the sync bookkeeping row, or None if the poller has never run."""
    row = connection.execute(
        "SELECT last_incremental_sync, last_full_sync, issue_count, transition_count, updated_at "
        "FROM sync_meta WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return SyncMeta(
        last_incremental_sync=row[0],
        last_full_sync=row[1],
        issue_count=row[2],
        transition_count=row[3],
        updated_at=row[4],
    )


def set_sync_meta(
    connection: duckdb.DuckDBPyConnection,
    last_incremental_sync: datetime,
    last_full_sync: datetime | None,
    issue_count: int,
    transition_count: int,
    updated_at: datetime,
) -> None:
    """Write the single sync_meta row (id = 1), replacing any existing values."""
    connection.execute(
        "INSERT OR REPLACE INTO sync_meta "
        "(id, last_incremental_sync, last_full_sync, issue_count, transition_count, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        [last_incremental_sync, last_full_sync, issue_count, transition_count, updated_at],
    )


def upsert_merge_requests(connection: duckdb.DuckDBPyConnection, mrs: list[MergeRequestRow]) -> int:
    """Insert or replace merge-request rows keyed by MR id. Returns the number written."""
    if not mrs:
        return 0
    placeholders = ", ".join(["?"] * len(_MR_COLUMNS))
    sql = f"INSERT OR REPLACE INTO merge_requests ({', '.join(_MR_COLUMNS)}) VALUES ({placeholders})"
    connection.executemany(sql, [list(astuple(mr)) for mr in mrs])
    return len(mrs)


def replace_mr_files(
    connection: duckdb.DuckDBPyConnection,
    mr_ids: list[int],
    files: list[tuple[int, str]],
) -> int:
    """Replace the changed-file rows for the given MR ids. Returns the number written."""
    if mr_ids:
        connection.executemany("DELETE FROM mr_files WHERE mr_id = ?", [[mr_id] for mr_id in mr_ids])
    if files:
        connection.executemany(
            "INSERT INTO mr_files (mr_id, path) VALUES (?, ?)", [list(row) for row in files]
        )
    return len(files)


def get_gitlab_watermark(connection: duckdb.DuckDBPyConnection) -> datetime | None:
    """Return the last successful GitLab sync time, or None if never crawled (→ full window)."""
    row = connection.execute("SELECT last_sync FROM gitlab_sync_meta WHERE id = 1").fetchone()
    return row[0] if row else None


def set_gitlab_watermark(connection: duckdb.DuckDBPyConnection, last_sync: datetime) -> None:
    """Record the GitLab sync watermark (id = 1); later crawls pull only MRs updated after it."""
    connection.execute(
        "INSERT OR REPLACE INTO gitlab_sync_meta (id, last_sync) VALUES (1, ?)", [last_sync]
    )
