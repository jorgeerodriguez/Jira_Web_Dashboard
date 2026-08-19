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


def test_adding_an_author_bumps_the_roster_version(monkeypatch, tmp_path):
    """The version, not the watermark, is what forces the next crawl to cover the full window."""
    client, conn = _client(monkeypatch, tmp_path, "a.duckdb")
    monkeypatch.setattr(app_module, "_run_gitlab_cycle", lambda: None)
    res = client.post("/api/mr-authors", json={"op": "add", "username": "audacy-new.person",
                                               "display_name": "New Person"})
    assert res.status_code == 200 and res.json()["recrawl_queued"] is True
    assert client.get("/api/mr-authors").json()["version"] == 1


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


def test_authors_param_filters_the_daily_series(monkeypatch, tmp_path):
    """The page sends the filter to the server; both cuts must come back narrowed."""
    client, conn = _client(monkeypatch, tmp_path, "f.duckdb")
    body = client.get("/api/mr-turnaround", params={"authors": "ben, jeremy"}).json()
    assert body["filter"] == ["ben", "jeremy"]
    assert "daily" in body


def test_blank_authors_param_is_no_filter(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, "g.duckdb")
    assert client.get("/api/mr-turnaround", params={"authors": " , "}).json()["filter"] == []


def test_email_addresses_are_rejected_with_an_explanation(monkeypatch, tmp_path):
    """The field silently accepted two emails and stored them; they matched no MRs at all.

    Merge requests are attributed by GitLab username, so an email can never resolve. Failing
    loudly is the difference between "nothing happened" and knowing why.
    """
    client, _ = _client(monkeypatch, tmp_path, "e.duckdb")
    res = client.post("/api/mr-authors", json={"op": "add", "username": "jeremy.williams@audacy.com"})
    assert res.status_code == 400
    assert "email" in res.json()["detail"].lower()
    assert client.get("/api/mr-authors").json()["added"] == {}


def test_user_search_needs_two_characters(monkeypatch, tmp_path):
    """Avoids hammering GitLab on the first keystroke."""
    client, _ = _client(monkeypatch, tmp_path, "u.duckdb")
    assert client.get("/api/gitlab-users", params={"q": "j"}).json()["users"] == []


def test_user_search_says_so_when_it_cannot_reach_gitlab(monkeypatch, tmp_path):
    """A 503 lets the page explain itself; an empty list would look like nobody matched."""
    client, _ = _client(monkeypatch, tmp_path, "v.duckdb")
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    res = client.get("/api/gitlab-users", params={"q": "jeremy"})
    assert res.status_code == 503
    assert "GITLAB_TOKEN" in res.json()["detail"]


def test_adding_an_author_starts_the_crawl_immediately(monkeypatch, tmp_path):
    """Clearing the watermark alone left the author invisible for up to a poll interval.

    The GitLab poller runs every 86400s by default, so "queued a full re-crawl" meant the added
    author's merge requests might not appear for a day - indistinguishable from the add failing.
    """
    client, conn = _client(monkeypatch, tmp_path, "r.duckdb")
    store.set_gitlab_watermark(conn, datetime(2026, 8, 18, 12, 0))
    calls = []
    monkeypatch.setattr(app_module, "_run_gitlab_cycle", lambda: calls.append("crawled"))

    res = client.post("/api/mr-authors", json={"op": "add", "username": "audacy-new.person",
                                               "display_name": "New Person"})
    assert res.status_code == 200 and res.json()["recrawl_queued"] is True
    assert calls == ["crawled"]                          # the crawl already ran


def test_hiding_an_author_does_not_start_a_crawl(monkeypatch, tmp_path):
    """A presentation edit must not trigger an expensive full crawl."""
    client, _ = _client(monkeypatch, tmp_path, "s2.duckdb")
    calls = []
    monkeypatch.setattr(app_module, "_run_gitlab_cycle", lambda: calls.append("crawled"))
    assert client.post("/api/mr-authors", json={"op": "hide", "display_name": "Adam"}).status_code == 200
    assert calls == []


def test_a_failing_recrawl_does_not_break_the_add(monkeypatch, tmp_path):
    """The roster edit is already persisted; a crawl failure must not surface as a 500."""
    client, _ = _client(monkeypatch, tmp_path, "t2.duckdb")
    def boom():
        raise RuntimeError("missing required environment variable: GITLAB_TOKEN")
    monkeypatch.setattr(app_module, "_run_gitlab_cycle", boom)
    res = client.post("/api/mr-authors", json={"op": "add", "username": "audacy-new.person"})
    assert res.status_code == 200
    assert client.get("/api/mr-authors").json()["added"] == {"audacy-new.person": "audacy-new.person"}
