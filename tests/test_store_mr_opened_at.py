from datetime import datetime
import duckdb
from darkstar import store


def _row(**over):
    base = dict(id=1, project_path="audacy-inc/devops/x", iid=7,
                author_account_id="acct-1", title="DEVOPS-42 do a thing",
                opened_at=datetime(2026, 7, 20, 9, 0, 0),
                merged_at=datetime(2026, 7, 22, 15, 0, 0),
                labels=["pe:iac-request"],
                web_url="https://gitlab.com/x/-/merge_requests/7",
                fetched_at=datetime(2026, 7, 22, 16, 0, 0), events_fetched_at=datetime(2026, 7, 22, 16, 0, 0),
                description="")
    base.update(over)
    return store.MergeRequestRow(**base)


def test_opened_at_round_trips():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_merge_requests(conn, [_row()])
    opened, merged = conn.execute(
        "SELECT opened_at, merged_at FROM merge_requests WHERE id = 1").fetchone()
    assert opened == datetime(2026, 7, 20, 9, 0, 0)
    assert merged == datetime(2026, 7, 22, 15, 0, 0)
