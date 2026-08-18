from datetime import datetime
import duckdb
from darkstar import store


def test_mr_labels_round_trip():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_merge_requests(conn, [store.MergeRequestRow(
        id=1, project_path="audacy-inc/devops/x", iid=1, author_account_id="a",
        title="DEVOPS-1 add module", opened_at=datetime(2026, 7, 20, 9, 0, 0),
        merged_at=datetime(2026, 7, 21, 9, 0, 0), labels=["pe:k8s-request"],
        web_url="u", fetched_at=datetime(2026, 7, 28, 0, 0, 0), description="")])
    labels = conn.execute("SELECT labels FROM merge_requests WHERE id = 1").fetchone()[0]
    assert labels == ["pe:k8s-request"]
