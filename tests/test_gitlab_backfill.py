"""A store carrying incomplete MR rows must re-crawl in full, exactly once.

Incremental crawls re-fetch only MRs *updated* since the watermark, so rows written before
opened_at existed — and MRs by an author added to MR_AUTHORS afterwards — would never be
repaired, leaving the MR-turnaround view permanently near-empty on a long-lived store.
"""
from datetime import datetime, timedelta

import duckdb

from darkstar import gitlab_ingest, store

_NOW = datetime(2026, 8, 18, 12, 0, 0)
_WINDOW_DAYS = 183


# The pre-SLA merge_requests shape: no opened_at, no labels. initialize_schema migrates it by
# adding those columns as NULLABLE, which is the only way an in-window row can lack opened_at --
# on a store created fresh the column is NOT NULL, so the backfill can never fire there.
_LEGACY_DDL: str = """
CREATE TABLE merge_requests (
    id                BIGINT PRIMARY KEY,
    project_path      VARCHAR NOT NULL,
    iid               BIGINT NOT NULL,
    author_account_id VARCHAR NOT NULL,
    title             VARCHAR NOT NULL,
    merged_at         TIMESTAMP NOT NULL,
    web_url           VARCHAR NOT NULL,
    fetched_at        TIMESTAMP NOT NULL
);
"""


def _conn():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    return conn


def _migrated_conn_with_legacy_row(merged_at):
    """A store as it exists in prod: legacy rows migrated, so opened_at is NULL on them."""
    conn = duckdb.connect(":memory:")
    conn.execute(_LEGACY_DDL)
    conn.execute(
        "INSERT INTO merge_requests VALUES (1, 'audacy-inc/devops/x', 1, 'a', 'MR 1', ?, 'u', ?)",
        [merged_at, _NOW],
    )
    store.initialize_schema(conn)   # adds opened_at/labels as nullable; row 1 keeps NULL
    assert conn.execute("SELECT opened_at FROM merge_requests").fetchone()[0] is None
    return conn


def _mr_row(id, opened, merged):
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id="a",
        title=f"MR {id}", opened_at=opened, merged_at=merged, labels=[],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="",
        source_branch="")


def test_in_window_row_missing_opened_at_forces_a_full_crawl():
    conn = _migrated_conn_with_legacy_row(_NOW - timedelta(days=9))
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is True


def test_complete_in_window_rows_stay_incremental():
    """The backfill must terminate — once repaired, crawls go back to cheap incremental pulls."""
    conn = _conn()
    store.upsert_merge_requests(conn, [_mr_row(1, _NOW - timedelta(days=10), _NOW - timedelta(days=9))])
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is False


def test_incomplete_row_older_than_the_window_is_ignored():
    """Ancient rows are never re-crawled, so they must not pin the sync to full crawls forever."""
    conn = _migrated_conn_with_legacy_row(_NOW - timedelta(days=399))
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is False


def test_empty_store_needs_no_backfill():
    """A first run already crawls the full window via the absent watermark, not via backfill."""
    assert gitlab_ingest._needs_backfill(_conn(), _NOW - timedelta(days=_WINDOW_DAYS)) is False


def test_rows_missing_a_source_branch_force_one_full_recrawl(tmp_path):
    """source_branch was added after the store existed, so old rows carry NULL.

    An incremental crawl only re-fetches MRs updated since the watermark, so without this the branch
    would stay NULL forever on every historical row — and branch names are what link a request whose
    title mangles its key. Silent, permanent, and invisible in any panel.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    conn.execute(
        "INSERT INTO merge_requests (id, project_path, iid, author_account_id, title, opened_at, "
        "merged_at, labels, web_url, merged_by, fetched_at, events_fetched_at, description) "
        "VALUES (1, 'p', 1, 'a', 'DEVOPS-1 x', '2026-08-01 16:00:00', '2026-08-02 17:00:00', "
        "[], 'u', '', '2026-08-03 00:00:00', '2026-08-03 00:00:00', '')")
    assert conn.execute(
        "SELECT count(*) FROM merge_requests WHERE source_branch IS NULL").fetchone()[0] == 1
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is True

    conn.execute("UPDATE merge_requests SET source_branch = 'DEVOPS-1-thing'")
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is False, \
        "and the forcing has to stop once the column is filled, or every crawl is a full one"


def test_the_ingest_stores_the_branch_gitlab_reports(monkeypatch):
    """The branch has to survive the round trip, or the linking change is decoration.

    Nothing else in the suite reaches _sync_scopes, so without this the ingest could quietly write ""
    for every branch and every panel would still look right — the links would just be missing.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    author = "audacy-ben.bonora"
    api_mr = {
        "id": 501, "iid": 7, "title": "Devops 9426",
        "source_branch": "DEVOPS-9426-rotate-certs",
        "description": "", "labels": ["pe:iac-request"], "web_url": "u",
        "created_at": "2026-08-03T15:00:00.000Z", "merged_at": "2026-08-04T17:00:00.000Z",
        "author": {"username": author}, "merged_by": {"username": "someone-else"},
        "project_id": 99, "references": {"full": "audacy-inc/devops/x!7"},
        "target_project_id": 99,
    }
    monkeypatch.setattr(gitlab_ingest, "_token", lambda: "t")
    monkeypatch.setattr(gitlab_ingest, "_merged_mrs", lambda session, scope, iso: [api_mr])
    monkeypatch.setattr(gitlab_ingest, "_changed_paths", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_mr_notes", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_project_path", lambda mr: "audacy-inc/devops/x")

    gitlab_ingest._sync_scopes(conn, datetime(2026, 7, 1), (1,), (), {})

    stored = conn.execute("SELECT title, source_branch FROM merge_requests WHERE id = 501").fetchone()
    assert stored is not None, "the MR should have been ingested at all"
    assert stored[1] == "DEVOPS-9426-rotate-certs", "the branch GitLab reported must be stored"
