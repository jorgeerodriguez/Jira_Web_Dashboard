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
    # The "Merge Request" field (customfield_11534), a free-text field holding a full GitLab MR URL.
    # The most authoritative link a request has: someone stated it deliberately, rather than it being
    # inferred from text a human typed for another purpose. Sampled clean -- 18 of 18 were a single
    # canonical https://gitlab.com/<group>/<project>/-/merge_requests/<iid>.
    mr_field_url: str | None
    # Whether Jira's development panel shows a pull request / commits for this request. Booleans, and
    # they come from a JQL predicate rather than from the Development summary field: that field
    # (customfield_10400) serves a CACHE, and on DEVOPS-10117 it reported five builds where the panel
    # showed two, omitted the merged pull request entirely, and carried "isStale":true. JQL's
    # development[pullrequests] index agreed with the panel. These identify no particular merge
    # request, so they cannot link anything -- but a request Jira says has code, with no link found
    # here, is a hole in our own crawl rather than a request without code.
    # None means the flag has not been queried yet, which is not the same as False.
    dev_has_pr: bool | None
    dev_has_commits: bool | None
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
class MergeRequestEventRow:
    """One draft/ready/review moment on a merge request, from its notes.

    Stored as events rather than as a derived duration so the business-hour rules can change
    without a re-crawl, and so an MR that toggles draft->ready more than once is representable.
    kind is "ready", "draft", "review" (first human non-bot comment by someone other than the
    author) or "approval" (first approval by someone other than the author).
    """

    mr_id: int
    kind: str
    happened_at: datetime
    seq: int


@dataclass(frozen=True)
class MergeRequestRow:
    """A merged GitLab merge request attributed to a tracked author."""

    id: int
    project_path: str
    iid: int
    author_account_id: str
    title: str
    opened_at: datetime
    merged_at: datetime
    labels: list[str]
    web_url: str
    # GitLab username of whoever pressed merge, or "" when GitLab reports none. Never NULL once
    # crawled, so NULL strictly means "predates this column" and the backfill terminates.
    merged_by: str
    fetched_at: datetime
    # When the MR's notes were last read for draft/ready/review events. Distinct from having any
    # events: an MR that was never a draft and drew no comments legitimately has none, so absence
    # of events cannot mean "not yet crawled" or the backfill would never terminate.
    events_fetched_at: datetime
    # Stored verbatim so the agent-footer heuristics in slas.py can be retuned without a re-crawl;
    # the whole corpus is ~1.3 MiB, and the "Generated with Claude Code via /<skill>" footer is a
    # denser AI signal than the pe:* label (it predates the labels by two months).
    description: str
    # The source branch, kept because it is often the ONLY place a DEVOPS key appears: a title like
    # "feat(10117): add flux-reader iam role" carries the number without the project prefix, so the
    # branch DEVOPS-10117 is the only reliable link. Measured across 5,878 merged MRs, adding the
    # branch as a linking signal lifted the share of requests reachable from an MR by ~16% relative.
    source_branch: str


# Column order shared by the issues DDL and the upsert statement; keep in sync with IssueRow.
_ISSUE_COLUMNS: tuple[str, ...] = (
    "key", "id", "project", "issuetype", "status", "status_category", "priority",
    "summary", "assignee", "assignee_account_id", "reporter", "business_lead", "parent_key",
    "created", "updated", "resolutiondate", "planned_start", "target_end",
    "labels", "mr_field_url", "dev_has_pr", "dev_has_commits", "fetched_at",
)

# Column order shared by the merge_requests DDL and its upsert; keep in sync with MergeRequestRow.
_MR_COLUMNS: tuple[str, ...] = (
    "id", "project_path", "iid", "author_account_id", "title",
    "opened_at", "merged_at", "labels", "web_url", "merged_by", "fetched_at", "events_fetched_at",
    "description", "source_branch",
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
    mr_field_url        VARCHAR,
    dev_has_pr          BOOLEAN,
    dev_has_commits     BOOLEAN,
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
    opened_at         TIMESTAMP,
    merged_at         TIMESTAMP NOT NULL,
    labels            VARCHAR[],
    web_url           VARCHAR NOT NULL,
    merged_by         VARCHAR,
    fetched_at        TIMESTAMP NOT NULL,
    events_fetched_at TIMESTAMP,
    description       VARCHAR,
    source_branch     VARCHAR
);

CREATE TABLE IF NOT EXISTS mr_events (
    mr_id       BIGINT NOT NULL,
    kind        VARCHAR NOT NULL,
    happened_at TIMESTAMP NOT NULL,
    seq         INTEGER NOT NULL,
    PRIMARY KEY (mr_id, seq)
);

CREATE TABLE IF NOT EXISTS mr_files (
    mr_id BIGINT NOT NULL,
    path  VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS gitlab_sync_meta (
    id             INTEGER PRIMARY KEY,
    last_sync      TIMESTAMP,
    roster_version INTEGER
);
"""


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    """Open a read-write DuckDB connection at db_path, creating the file if absent."""
    return duckdb.connect(db_path)


def initialize_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the tables if absent, and idempotently migrate existing ones.

    CREATE TABLE IF NOT EXISTS does not add columns to a table that already exists (e.g. the
    persistent prod store on its PVC), so newer columns are added here with ADD COLUMN IF NOT
    EXISTS. They are nullable — pre-existing rows have no value and the next crawl backfills them;
    every new insert supplies them. This keeps a deploy from breaking MR ingestion on an old store.
    """
    connection.execute(_SCHEMA_SQL)
    connection.execute("ALTER TABLE merge_requests ADD COLUMN IF NOT EXISTS opened_at TIMESTAMP")
    connection.execute("ALTER TABLE merge_requests ADD COLUMN IF NOT EXISTS labels VARCHAR[]")
    connection.execute("ALTER TABLE merge_requests ADD COLUMN IF NOT EXISTS description VARCHAR")
    connection.execute("ALTER TABLE merge_requests ADD COLUMN IF NOT EXISTS events_fetched_at TIMESTAMP")
    connection.execute("ALTER TABLE gitlab_sync_meta ADD COLUMN IF NOT EXISTS roster_version INTEGER")
    connection.execute("ALTER TABLE merge_requests ADD COLUMN IF NOT EXISTS merged_by VARCHAR")
    connection.execute("ALTER TABLE merge_requests ADD COLUMN IF NOT EXISTS source_branch VARCHAR")
    connection.execute("ALTER TABLE issues ADD COLUMN IF NOT EXISTS mr_field_url VARCHAR")
    connection.execute("ALTER TABLE issues ADD COLUMN IF NOT EXISTS dev_has_pr BOOLEAN")
    connection.execute("ALTER TABLE issues ADD COLUMN IF NOT EXISTS dev_has_commits BOOLEAN")
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


def replace_mr_events(connection: duckdb.DuckDBPyConnection, mr_id: int,
                      events: list[MergeRequestEventRow]) -> None:
    """Replace all stored events for one MR. Idempotent, so a re-crawl cannot duplicate them."""
    connection.execute("DELETE FROM mr_events WHERE mr_id = ?", [mr_id])
    if events:
        connection.executemany(
            "INSERT INTO mr_events (mr_id, kind, happened_at, seq) VALUES (?, ?, ?, ?)",
            [[e.mr_id, e.kind, e.happened_at, e.seq] for e in events],
        )


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


def get_roster_version(connection: duckdb.DuckDBPyConnection) -> int:
    """The MR-author roster version the last successful crawl was built from (0 if never)."""
    row = connection.execute("SELECT roster_version FROM gitlab_sync_meta WHERE id = 1").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def set_roster_version(connection: duckdb.DuckDBPyConnection, version: int) -> None:
    """Record the roster version a crawl was built from, without disturbing the watermark."""
    connection.execute(
        "INSERT INTO gitlab_sync_meta (id, last_sync, roster_version) VALUES (1, NULL, ?) "
        "ON CONFLICT (id) DO UPDATE SET roster_version = excluded.roster_version", [version]
    )


def clear_gitlab_watermark(connection: duckdb.DuckDBPyConnection) -> None:
    """Drop the GitLab sync watermark so the next crawl covers the full window again.

    Used when a newly tracked author is added: an incremental crawl only returns MRs *updated*
    since the watermark, so their existing merged MRs would never be fetched.
    """
    connection.execute("DELETE FROM gitlab_sync_meta")


def set_gitlab_watermark(connection: duckdb.DuckDBPyConnection, last_sync: datetime) -> None:
    """Record the GitLab sync watermark (id = 1); later crawls pull only MRs updated after it."""
    connection.execute(
        "INSERT OR REPLACE INTO gitlab_sync_meta (id, last_sync) VALUES (1, ?)", [last_sync]
    )
