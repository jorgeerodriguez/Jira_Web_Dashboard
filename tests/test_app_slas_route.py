from datetime import datetime

import duckdb
from fastapi.testclient import TestClient
from darkstar import app as app_module, store


def test_api_slas_returns_report(monkeypatch, tmp_path):
    conn = duckdb.connect(str(tmp_path / "t.duckdb"))
    store.initialize_schema(conn)
    # the editable MR-author roster is persisted next to the store, so point it at tmp_path too
    monkeypatch.setenv("DARKSTAR_DB_PATH", str(tmp_path / "t.duckdb"))
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    client = TestClient(app_module.app)
    res = client.get("/api/slas")
    assert res.status_code == 200
    body = res.json()
    assert "buckets" in body and "agent_success" in body


def test_api_mr_turnaround_returns_report(monkeypatch, tmp_path):
    conn = duckdb.connect(str(tmp_path / "t2.duckdb"))
    store.initialize_schema(conn)
    # the editable MR-author roster is persisted next to the store, so point it at tmp_path too
    monkeypatch.setenv("DARKSTAR_DB_PATH", str(tmp_path / "t.duckdb"))
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    client = TestClient(app_module.app)
    res = client.get("/api/mr-turnaround")
    assert res.status_code == 200
    body = res.json()
    assert "authors" in body and "team" in body and body["window_months"] == 6


def _client(monkeypatch, tmp_path, name):
    conn = duckdb.connect(str(tmp_path / name))
    store.initialize_schema(conn)
    monkeypatch.setenv("DARKSTAR_DB_PATH", str(tmp_path / name))
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    return TestClient(app_module.app), conn


def test_since_overrides_the_window_on_every_panel(monkeypatch, tmp_path):
    """One lookback control drives the whole page, so both endpoints must honour the same param."""
    client, _ = _client(monkeypatch, tmp_path, "s.duckdb")
    for path in ("/api/slas", "/api/mr-turnaround"):
        body = client.get(path, params={"since": "2026-05-01"}).json()
        assert body["window_start"] == "2026-05-01", path


def test_since_defaults_differ_per_panel(monkeypatch, tmp_path):
    """Without an override each view keeps its own window: SLA 3 months, MR turnaround 6."""
    client, _ = _client(monkeypatch, tmp_path, "d.duckdb")
    assert client.get("/api/slas").json()["window_months"] == 3
    assert client.get("/api/mr-turnaround").json()["window_months"] == 6


def test_a_malformed_since_is_rejected_not_ignored(monkeypatch, tmp_path):
    """Silently showing a different window than the box says is worse than an error."""
    client, _ = _client(monkeypatch, tmp_path, "b.duckdb")
    res = client.get("/api/slas", params={"since": "last tuesday"})
    assert res.status_code == 400 and "YYYY-MM-DD" in res.json()["detail"]


def test_adding_an_author_clears_the_gitlab_watermark(monkeypatch, tmp_path):
    """The API must queue the re-crawl, or the added author's table row stays empty forever."""
    client, conn = _client(monkeypatch, tmp_path, "a.duckdb")
    store.set_gitlab_watermark(conn, datetime(2026, 8, 18, 12, 0))
    res = client.post("/api/mr-authors", json={"op": "add", "username": "audacy-new.person",
                                               "display_name": "New Person"})
    assert res.status_code == 200 and res.json()["recrawl_queued"] is True
    assert store.get_gitlab_watermark(conn) is None


def test_hiding_an_author_does_not_clear_the_watermark(monkeypatch, tmp_path):
    """A presentation edit must not trigger an expensive full crawl."""
    client, conn = _client(monkeypatch, tmp_path, "h.duckdb")
    store.set_gitlab_watermark(conn, datetime(2026, 8, 18, 12, 0))
    res = client.post("/api/mr-authors", json={"op": "hide", "display_name": "Adam"})
    assert res.status_code == 200 and res.json()["recrawl_queued"] is False
    assert store.get_gitlab_watermark(conn) is not None
    assert client.get("/api/mr-authors").json()["hidden"] == ["Adam"]


def test_author_edits_are_validated(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, "v.duckdb")
    assert client.post("/api/mr-authors", json={"op": "add"}).status_code == 400
    assert client.post("/api/mr-authors", json={"op": "hide"}).status_code == 400
    assert client.post("/api/mr-authors", json={"op": "nope", "username": "x"}).status_code == 400
