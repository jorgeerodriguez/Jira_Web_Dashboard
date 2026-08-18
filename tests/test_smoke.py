from fastapi.testclient import TestClient


def test_health_ok(tmp_path, monkeypatch):
    import duckdb
    from darkstar import app as app_module, store
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    store.initialize_schema(conn)
    # the editable MR-author roster is persisted next to the store, so point it at tmp_path too
    monkeypatch.setenv("DARKSTAR_DB_PATH", str(tmp_path / "t.duckdb"))
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    client = TestClient(app_module.app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/slas").status_code == 200
