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
        web_url="u", fetched_at=_NOW, description="")


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
