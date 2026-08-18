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


def test_lead_time_is_unlinked_but_still_served(tmp_path, monkeypatch):
    """Hidden means absent from the nav, not removed: bookmarks and the API must keep working.

    Restoring it is putting the `<a href="lead-time">` entry back in the four dashboard navs, so
    this guards against someone "finishing the job" by deleting the route.
    """
    import duckdb
    from darkstar import app as app_module, store
    conn = duckdb.connect(str(tmp_path / "n.duckdb"))
    store.initialize_schema(conn)
    monkeypatch.setenv("DARKSTAR_DB_PATH", str(tmp_path / "n.duckdb"))
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    client = TestClient(app_module.app)

    assert client.get("/lead-time").status_code == 200
    assert client.get("/api/lead-time").status_code == 200
    for page in ("/intake", "/velocity", "/delivery-forecast", "/slas"):
        assert 'href="lead-time"' not in client.get(page).text, page


def test_nav_order_is_the_same_on_every_page(tmp_path, monkeypatch):
    """One nav, four pages, one order — they are hand-maintained copies and drift silently."""
    import re
    import duckdb
    from darkstar import app as app_module, store
    conn = duckdb.connect(str(tmp_path / "o.duckdb"))
    store.initialize_schema(conn)
    monkeypatch.setenv("DARKSTAR_DB_PATH", str(tmp_path / "o.duckdb"))
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    client = TestClient(app_module.app)

    expected = ["Intake", "Self-Service", "Delivery Forecast", "Velocity"]
    for page in ("/intake", "/slas", "/delivery-forecast", "/velocity"):
        nav = re.search(r'<nav class="nav">(.*?)</nav>', client.get(page).text, re.S)
        assert nav, page
        assert re.findall(r">([^<>]+)</a>", nav.group(1)) == expected, page


def test_the_sla_table_is_gone_from_the_page_but_still_computed(tmp_path, monkeypatch):
    """Dropped from the view, not from the aggregation: restoring it is a dashboard change only."""
    import duckdb
    from darkstar import app as app_module, store
    conn = duckdb.connect(str(tmp_path / "p.duckdb"))
    store.initialize_schema(conn)
    monkeypatch.setenv("DARKSTAR_DB_PATH", str(tmp_path / "p.duckdb"))
    monkeypatch.setattr(app_module, "_db_handle", conn, raising=False)
    client = TestClient(app_module.app)

    page = client.get("/slas").text
    assert "By request type" not in page and 'id="slaTbl"' not in page
    assert "buckets" in client.get("/api/slas").json()
