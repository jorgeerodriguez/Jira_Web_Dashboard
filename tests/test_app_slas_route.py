import duckdb
from fastapi.testclient import TestClient
from darkstar import app as app_module, store


def test_api_slas_returns_report(monkeypatch, tmp_path):
    conn = duckdb.connect(str(tmp_path / "t.duckdb"))
    store.initialize_schema(conn)
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    client = TestClient(app_module.app)
    res = client.get("/api/slas")
    assert res.status_code == 200
    body = res.json()
    assert "buckets" in body and "agent_success" in body
