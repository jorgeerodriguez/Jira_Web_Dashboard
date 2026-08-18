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
        labels=["pe:iac-request"], web_url="u", fetched_at=datetime(2026, 7, 28, 0, 0, 0), events_fetched_at=datetime(2026, 7, 28, 0, 0, 0),
        description="")])
    assert conn.execute("SELECT labels FROM merge_requests WHERE id = 2").fetchone()[0] == ["pe:iac-request"]
