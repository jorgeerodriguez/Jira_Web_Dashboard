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


def test_an_inverted_window_is_rejected_rather_than_served_empty(monkeypatch, tmp_path):
    """Empty panels read as "the team did nothing", not as "you asked for nothing"."""
    client, _ = _client(monkeypatch, tmp_path, "inv.duckdb")
    response = client.get("/api/slas", params={"since": "2026-08-01", "until": "2026-07-01"})
    assert response.status_code == 400
    assert "must be after" in response.json()["detail"]


def test_a_zero_width_window_is_rejected(monkeypatch, tmp_path):
    """until is exclusive, so since == until can only ever return nothing."""
    client, _ = _client(monkeypatch, tmp_path, "zero.duckdb")
    assert client.get("/api/slas",
                      params={"since": "2026-08-01", "until": "2026-08-01"}).status_code == 400


def test_a_malformed_until_is_rejected_and_names_the_field(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, "mal.duckdb")
    response = client.get("/api/slas", params={"since": "2026-08-01", "until": "last-tuesday"})
    assert response.status_code == 400
    assert "until" in response.json()["detail"]


def test_both_bounds_reach_the_mr_turnaround_route(monkeypatch, tmp_path):
    """The same control drives both endpoints, so both must accept the pair."""
    client, _ = _client(monkeypatch, tmp_path, "mrb.duckdb")
    assert client.get("/api/mr-turnaround",
                      params={"since": "2026-07-01", "until": "2026-08-01"}).status_code == 200
    assert client.get("/api/mr-turnaround",
                      params={"since": "2026-08-01", "until": "2026-07-01"}).status_code == 400


def _slas_page(monkeypatch, tmp_path, name):
    client, _ = _client(monkeypatch, tmp_path, name)
    response = client.get("/slas")
    assert response.status_code == 200
    return response.text


def test_the_hidden_custom_range_is_hidden_against_the_filter_display_rule(monkeypatch, tmp_path):
    """`hidden` alone loses to an author `display` rule, which is how it broke.

    `.filter{display:inline-flex}` outranks the UA stylesheet's `[hidden]{display:none}`, so the
    custom from/to inputs stayed on screen while the code believed it had put them away. Anyone
    reaching them while a preset was selected then found their dates ignored. The override has to be
    at least as specific as `.filter`, so a bare `[hidden]` rule is not enough.
    """
    page = _slas_page(monkeypatch, tmp_path, "hid.duckdb")
    assert ".controls [hidden]{display:none}" in page, "hidden must outrank .filter's display"
    assert 'id="customRange"' in page and "hidden" in page


def test_editing_a_custom_date_selects_the_custom_preset(monkeypatch, tmp_path):
    """Typing a date must never be a no-op.

    presetRange() resolves from the SELECT, so a date typed while the select still read "Panel
    defaults" resolved to null and was silently dropped — the lookback appeared not to work at all.
    """
    page = _slas_page(monkeypatch, tmp_path, "cust.duckdb")
    assert 'preset.value = "custom"' in page, "a date edit must switch the select to custom"
    assert 'since.addEventListener("change", applyCustom)' in page
    assert 'until.addEventListener("change", applyCustom)' in page


def test_every_lookback_preset_the_page_offers_is_handled_by_the_resolver(monkeypatch, tmp_path):
    """An option with no case in presetRange() falls to `default: return null` — a silent no-op."""
    import re
    page = _slas_page(monkeypatch, tmp_path, "pre.duckdb")
    control = page[page.index('<select id="preset">'):page.index("</select>")]
    offered = {v for v in re.findall(r'<option value="([^"]*)"', control) if v}
    resolver = page[page.index("function presetRange("):page.index("function query(")]
    for preset in offered:
        assert f'case "{preset}"' in resolver, f'preset {preset!r} has no case in presetRange()'


def test_every_element_the_page_script_looks_up_exists_in_the_markup(monkeypatch, tmp_path):
    """A getElementById that returns null throws, and one throw took out the whole page.

    Merging two panels dropped `<p id="hiddenNote">` while renderHidden() still targeted it. That
    threw out of reload(), init() caught it and returned, and every listener after that point was
    never attached — the lookback picker, author filter, environment filter, add and hide all went
    dead at once, with no error visible on the panels themselves. Nothing but this test connects a
    render target to the markup that has to carry it.
    """
    import re
    page = _slas_page(monkeypatch, tmp_path, "ids.duckdb")
    wanted = set(re.findall(r'getElementById\("([^"]+)"\)', page))
    # Ignore interpolated ids — those are built at render time, not declared in the template.
    present = set(re.findall(r'id="([^"${]+)"', page))
    assert wanted, "the page should look elements up at all"
    assert not (wanted - present), f"script targets missing from markup: {sorted(wanted - present)}"


def test_the_first_load_runs_after_every_listener_is_wired(monkeypatch, tmp_path):
    """Ordering is the reason a broken panel cost the whole page rather than just that panel.

    If the initial `await reload()` sits before the addEventListener calls, any render failure skips
    the wiring. It has to be the last thing init() does.
    """
    page = _slas_page(monkeypatch, tmp_path, "order.duckdb")
    init = page[page.index("async function init(){"):page.index("\ninit();")]
    assert init.index("addEventListener") < init.rindex("await reload()"), \
        "init() must wire its listeners before the first reload"
    # Nested callbacks legitimately return; what must not happen is init's own body bailing out
    # between the load and the wiring. With the load last, there is no "between" left.
    assert init.rindex("await reload()") > init.rindex('addEventListener("click"'), \
        "the first reload must come after the last listener init() attaches"


def test_no_dashboard_uses_a_css_token_it_never_declares(monkeypatch, tmp_path):
    """Each page carries its own copy of :root, so a token added to one is missing from the others.

    Propagating the surface tokens hit exactly this: two pages' :root blocks had diverged, so they
    referenced var(--well) and var(--accent) without declaring them. A CSS variable with no value
    fails silently — the colour just does not apply — so nothing surfaces it but a check like this.
    """
    import pathlib
    import re
    pages = sorted(pathlib.Path("darkstar/dashboards").glob("*.html"))
    assert pages, "no dashboards found"
    for page in pages:
        css = page.read_text()
        css = css[css.index("<style>") + 7:css.index("</style>")]
        # --c is assigned per element inline by the chip renderer, never in :root.
        used = set(re.findall(r"var\((--[a-z0-9-]+)", css)) - {"--c"}
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
        assert not (used - declared), f"{page.name} uses undeclared {sorted(used - declared)}"


def test_panel_headings_outrank_sub_headings(monkeypatch, tmp_path):
    """They were 13px and 12px, both --muted at weight 600 — one pixel apart and nothing else.

    That is what made the page read flat: a panel title was painted the dimmest ink available, the
    same as captions and hints. The scale only works if h2 is brighter AND larger than h3.
    """
    import re
    page = _slas_page(monkeypatch, tmp_path, "type.duckdb")
    css = page[page.index("<style>") + 7:page.index("</style>")]
    h2 = re.search(r"\bh2\{([^}]*)\}", css).group(1)
    h3 = re.search(r"\bh3\{([^}]*)\}", css).group(1)
    h2_size = float(re.search(r"font-size:([\d.]+)px", h2).group(1))
    h3_size = float(re.search(r"font-size:([\d.]+)px", h3).group(1))
    assert h2_size > h3_size, f"h2 ({h2_size}px) must be larger than h3 ({h3_size}px)"
    assert "var(--ink)" in h2, "a panel title must use the brightest ink, not --muted"
    assert "var(--muted)" in h3, "a sub-heading should stay dim so the tiers separate"


def test_score_cards_do_not_share_a_surface_with_the_panel_holding_them(monkeypatch, tmp_path):
    """.stat, .fc and .panel all used var(--panel), leaving a 1px border as the only separation."""
    import re
    page = _slas_page(monkeypatch, tmp_path, "surf.duckdb")
    css = page[page.index("<style>") + 7:page.index("</style>")]
    for selector in (r"\.stat\{", r"\.fc\{"):
        rule = re.search(selector + r"([^}]*)\}", css).group(1)
        assert "var(--well)" in rule, f"{selector} must sit on the recessed surface"
        assert "background:var(--panel)" not in rule
