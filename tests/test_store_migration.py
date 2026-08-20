from datetime import datetime
import duckdb
from darkstar import store


_OLD_MERGE_REQUESTS_DDL = """
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


def test_initialize_schema_migrates_existing_merge_requests_table():
    """A store created before opened_at/labels/description existed gains them on initialize_schema,
    and the upsert (which references those columns) then succeeds instead of a binder error."""
    conn = duckdb.connect(":memory:")
    conn.execute(_OLD_MERGE_REQUESTS_DDL)
    conn.execute(
        "INSERT INTO merge_requests VALUES "
        "(1, 'p', 1, 'a', 't', TIMESTAMP '2026-07-21 09:00:00', 'u', TIMESTAMP '2026-07-28 00:00:00')"
    )

    store.initialize_schema(conn)  # must add the missing columns to the existing table

    cols = {row[1] for row in conn.execute("PRAGMA table_info('merge_requests')").fetchall()}
    assert "opened_at" in cols and "labels" in cols and "description" in cols
    # pre-existing row has NULLs for the new columns; the crawl backfills on the next sync
    assert conn.execute(
        "SELECT opened_at, labels, description FROM merge_requests WHERE id = 1"
    ).fetchone() == (None, None, None)
    # the upsert path (positional, references the new columns) now works
    store.upsert_merge_requests(conn, [store.MergeRequestRow(
        id=2, project_path="p", iid=2, author_account_id="a", title="DEVOPS-9 x",
        opened_at=datetime(2026, 7, 20, 9, 0, 0), merged_at=datetime(2026, 7, 21, 9, 0, 0),
        labels=["pe:iac-request"], web_url="u", merged_by="", fetched_at=datetime(2026, 7, 28, 0, 0, 0), events_fetched_at=datetime(2026, 7, 28, 0, 0, 0),
        description="", source_branch="",
        pipelines_fetched_at=datetime(2026, 7, 28, 0, 0, 0))])
    assert conn.execute("SELECT labels FROM merge_requests WHERE id = 2").fetchone()[0] == ["pe:iac-request"]


def test_a_fresh_store_has_the_same_nullability_as_a_migrated_one():
    """A fresh CREATE and an ALTER-migrated store must agree, or code is written against two schemas.

    The ALTER-added merge_request columns are deliberately nullable: NULL means "predates this
    column, the next crawl backfills it", which is exactly what `_needs_backfill` selects on. While
    CREATE declared them NOT NULL, the deployed store held 117 NULL descriptions that a fresh store
    could not represent at all — so a test could not reproduce production state, and any handling of
    the unread case was untestable. Nothing enforces the pairing but this test.
    """
    migrated = ["opened_at", "labels", "description", "events_fetched_at", "merged_by",
                "source_branch"]

    fresh = duckdb.connect(":memory:")
    store.initialize_schema(fresh)
    nullable = {row[0]: row[1] for row in fresh.execute(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'merge_requests'").fetchall()}
    for column in migrated:
        assert nullable[column] == "YES", f"{column} must stay nullable to mark 'not yet backfilled'"

    # merged_at and the identity columns are supplied by every insert and must stay NOT NULL, or a
    # half-written row becomes indistinguishable from one still awaiting backfill.
    for column in ("id", "project_path", "iid", "title", "merged_at", "fetched_at"):
        assert nullable[column] == "NO", f"{column} must stay NOT NULL"


def test_an_unread_description_is_representable_so_backfill_detection_can_be_tested():
    """The state the deployed store is actually in must be constructible in a test."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    conn.execute(
        "INSERT INTO merge_requests (id, project_path, iid, author_account_id, title, merged_at, "
        "web_url, fetched_at) VALUES (1, 'p', 1, 'a', 'DEVOPS-1 x', '2026-08-05 17:00:00', 'u', "
        "'2026-08-06 00:00:00')")
    assert conn.execute(
        "SELECT count(*) FROM merge_requests WHERE description IS NULL").fetchone()[0] == 1


def test_the_row_dataclass_and_the_column_list_stay_in_the_same_order():
    """upsert uses astuple(), so a field inserted in the wrong place shifts every value after it.

    Adding pipelines_fetched_at before source_branch in the dataclass while appending it after in
    _MR_COLUMNS wrote "" into a TIMESTAMP column. DuckDB caught it as a conversion error, but a
    same-typed pair of columns would have silently swapped values instead.
    """
    from dataclasses import fields
    assert tuple(f.name for f in fields(store.MergeRequestRow)) == store._MR_COLUMNS
    assert tuple(f.name for f in fields(store.IssueRow)) == store._ISSUE_COLUMNS
