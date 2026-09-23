"""The two Jira fields that carry the most trustworthy link from a request to its code.

`customfield_11534` ("Merge Request") is a URL somebody entered deliberately; `customfield_10400`
("Development") is Jira's cached dev-panel summary, which names no merge request but does say whether
any code exists. Both arrive free with the issue sync — but only if the sync asks for them, which is
what these tests pin.
"""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import duckdb

from darkstar import ingest, store

_NOW = datetime(2026, 8, 19, 12, 0, 0)


def test_the_issue_sync_requests_the_merge_request_field():
    """Dropping it from the field list costs the best link silently — the payload arrives thinner."""
    assert "customfield_11534" in ingest._ISSUE_FIELDS, "the Merge Request field must be fetched"


def _issue(created=None, **fields):
    base = {
        "summary": "s", "status": {"name": "Done", "statusCategory": {"key": "done"}},
        "issuetype": {"name": "Task"}, "priority": {"name": "Low"}, "labels": [],
        "created": "2026-08-05T16:00:00.000-0600", "updated": "2026-08-05T17:00:00.000-0600",
        "project": {"key": "DEVOPS"},
    }
    if created is not None:
        base["created"] = created.strftime("%Y-%m-%dT%H:%M:%S.000-0000")
    base.update(fields)
    return SimpleNamespace(raw={"key": "DEVOPS-1", "id": "1", "fields": base})


def test_the_merge_request_url_reaches_the_row_verbatim():
    """Parsing happens in slas.py; the ingest must not sanitise or interpret a free-text field."""
    url = "https://gitlab.com/audacy-inc/devops/terraform/tf-aardvark2-prod/-/merge_requests/480"
    row = ingest._map_issue(_issue(customfield_11534=url), _NOW)
    assert row.mr_field_url == url


def test_an_empty_merge_request_field_becomes_none_not_an_empty_string():
    """None means "nothing to link"; "" would look like a value that failed to parse."""
    assert ingest._map_issue(_issue(customfield_11534=""), _NOW).mr_field_url is None
    assert ingest._map_issue(_issue(), _NOW).mr_field_url is None


def test_the_development_summary_field_is_not_read_at_all():
    """It is a cache, and it lies. Reading it looked cheap and was wrong.

    On DEVOPS-10117 the panel showed 4 commits, 1 merged pull request and 2 builds. The same issue's
    customfield_10400 reported build count 5, repository count 5, **no pullrequest member at all**,
    and carried "isStale": true. A gap counter built on it silently reported "no pull request" for an
    issue with a merged one. JQL's development[pullrequests] index agreed with the panel, so the flags
    come from one query per batch instead.
    """
    assert "customfield_10400" not in ingest._ISSUE_FIELDS, "the cached summary must not be fetched"
    assert not hasattr(ingest, "_dev_counts"), "and its parser must be gone, not merely unused"


def test_the_batch_flags_come_from_the_key_sets_and_default_to_false():
    """False means Jira was asked and said no; None means nobody asked. Only the first is evidence."""
    rows = [ingest._map_issue(_issue(), _NOW)]
    assert rows[0].dev_has_pr is None, "unqueried until the flags are applied"

    flagged = ingest.apply_dev_panel_flags(rows, with_pr={"DEVOPS-1"}, with_commits=set())
    assert flagged[0].dev_has_pr is True
    assert flagged[0].dev_has_commits is False, "queried and absent is False, not None"

    neither = ingest.apply_dev_panel_flags(rows, with_pr=set(), with_commits=set())
    assert neither[0].dev_has_pr is False and neither[0].dev_has_commits is False


def test_applying_flags_leaves_every_other_field_untouched():
    """It rebuilds frozen rows, so a typo there would quietly blank a column."""
    url = "https://gitlab.com/audacy-inc/devops/x/-/merge_requests/1"
    row = ingest._map_issue(_issue(customfield_11534=url), _NOW)
    flagged = ingest.apply_dev_panel_flags([row], {"DEVOPS-1"}, {"DEVOPS-1"})[0]
    assert flagged.mr_field_url == url and flagged.key == row.key
    assert flagged.created == row.created and flagged.labels == row.labels


def _stored_issue(conn, key, created, **overrides):
    row = ingest._map_issue(_issue(created=created), _NOW)
    from dataclasses import replace
    store.upsert_issues(conn, [replace(row, key=key, id=int(key.split("-")[1]), **overrides)])


def test_issues_predating_a_column_are_detected_because_incremental_never_revisits_them():
    """The gap this closes: a watermark-driven sync only fetches issues that CHANGED.

    After the deploy that added these columns, all 9,811 stored issues kept NULL in them and nothing
    would ever have refilled them — GitLab's side self-heals through _needs_backfill, Jira's had no
    equivalent.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    _stored_issue(conn, "DEVOPS-1", datetime(2026, 6, 1, 12, 0))
    assert ingest.issues_missing_link_fields(conn, datetime(2026, 3, 1)) == 1

    conn.execute("UPDATE issues SET dev_has_pr = FALSE")
    assert ingest.issues_missing_link_fields(conn, datetime(2026, 3, 1)) == 0, \
        "and it must stop once filled, or every cycle re-runs the backfill"


def test_an_empty_merge_request_field_does_not_look_like_a_missing_backfill():
    """Keying on mr_field_url would re-fetch the same issues forever.

    Most issues legitimately have no Merge Request field set, so NULL there is a value, not an
    absence. dev_has_pr is written False for every issue queried, which makes NULL unambiguous.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    _stored_issue(conn, "DEVOPS-2", datetime(2026, 6, 1, 12, 0))
    conn.execute("UPDATE issues SET dev_has_pr = FALSE, dev_has_commits = FALSE, mr_field_url = NULL")
    assert ingest.issues_missing_link_fields(conn, datetime(2026, 3, 1)) == 0


def test_the_backfill_is_scoped_to_the_window_the_panels_display():
    """8,525 of the store's 9,811 issues predate anything on screen; re-reading them is waste."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    _stored_issue(conn, "DEVOPS-3", datetime(2025, 11, 1, 12, 0))   # long before the epoch
    _stored_issue(conn, "DEVOPS-4", datetime(2026, 6, 1, 12, 0))
    assert ingest.issues_missing_link_fields(conn, datetime(2026, 3, 1)) == 1


def test_the_backfill_reads_fields_and_never_changelogs(monkeypatch):
    """Changelogs are one request per issue — the whole reason this is not just a full re-sync.

    A full sync of the deployed store is ~9,800 requests; this is ~15. The transitions already stored
    must be left exactly as they are.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    _stored_issue(conn, "DEVOPS-5", datetime(2026, 6, 1, 12, 0))
    store.replace_transitions(conn, ["DEVOPS-5"], [store.TransitionRow(
        key="DEVOPS-5", to_status="Done", changed_at=datetime(2026, 6, 2, 12, 0), seq=0)])

    fetched = ingest._map_issue(_issue(customfield_11534="https://gitlab.com/g/p/-/merge_requests/1"),
                                _NOW)
    from dataclasses import replace
    monkeypatch.setattr(ingest, "fetch_issues",
                        lambda jira, jql: [replace(fetched, key="DEVOPS-5", id=5)])
    monkeypatch.setattr(ingest, "fetch_dev_panel_keys",
                        lambda jira, jql, predicate: {"DEVOPS-5"})
    def _explode(*a, **k):
        raise AssertionError("the backfill must not fetch changelogs")
    monkeypatch.setattr(ingest, "fetch_transitions", _explode)

    assert ingest.backfill_link_fields(object(), conn, datetime(2026, 3, 1)) == 1
    row = conn.execute(
        "SELECT mr_field_url, dev_has_pr FROM issues WHERE key = 'DEVOPS-5'").fetchone()
    assert row[0] == "https://gitlab.com/g/p/-/merge_requests/1" and row[1] is True
    assert conn.execute("SELECT count(*) FROM transitions").fetchone()[0] == 1, \
        "existing transitions must survive untouched"


def test_a_sync_cycle_self_heals_issues_the_incremental_slice_never_touches(monkeypatch):
    """The backfill has to be wired into the cycle, not merely exist.

    This is the whole point: an ordinary incremental sync fetches only what changed, and the stored
    issue below did not. Without the call in run_sync it keeps NULL forever and no panel ever knows.
    """
    from zoneinfo import ZoneInfo
    from dataclasses import replace

    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    _stored_issue(conn, "DEVOPS-9", datetime(2026, 6, 1, 12, 0))     # untouched since the column landed
    assert conn.execute("SELECT dev_has_pr FROM issues WHERE key='DEVOPS-9'").fetchone()[0] is None

    changed = ingest._map_issue(_issue(created=datetime(2026, 8, 1, 12, 0)), _NOW)
    stale = ingest._map_issue(_issue(created=datetime(2026, 6, 1, 12, 0)), _NOW)

    def _fetch(jira, jql):
        # the incremental slice returns only the changed issue; the backfill re-reads the window
        return ([replace(changed, key="DEVOPS-10", id=10)] if "updated >=" in jql
                else [replace(stale, key="DEVOPS-9", id=9)])

    monkeypatch.setattr(ingest, "fetch_issues", _fetch)
    monkeypatch.setattr(ingest, "fetch_dev_panel_keys", lambda jira, jql, predicate: {"DEVOPS-9"})
    monkeypatch.setattr(ingest, "fetch_keys", lambda jira, jql, description: set())
    monkeypatch.setattr(ingest, "fetch_transitions", lambda jira, keys: [])

    plan = ingest.SyncPlan(watermark=datetime(2026, 8, 19, 0, 0), last_full_sync=_NOW)
    ingest.run_sync(conn, object(), plan, _NOW, ZoneInfo("America/Denver"))

    healed = conn.execute("SELECT dev_has_pr FROM issues WHERE key='DEVOPS-9'").fetchone()[0]
    assert healed is True, "the untouched issue must be refilled by the backfill"
    assert ingest.issues_missing_link_fields(conn, datetime(2026, 3, 1)) == 0


def test_the_backfill_is_skipped_once_the_window_is_filled(monkeypatch):
    """Otherwise every cycle pays 13 extra pages forever.

    "Caught up" now means both triggers are satisfied: the link-field window is filled AND the store
    is stamped with the current _ISSUE_FIELDS version. Either one alone re-arms the pass.
    """
    from zoneinfo import ZoneInfo
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    _stored_issue(conn, "DEVOPS-11", datetime(2026, 6, 1, 12, 0))
    conn.execute("UPDATE issues SET dev_has_pr = FALSE, dev_has_commits = FALSE")
    store.set_fields_version(conn, ingest._FIELDS_VERSION)

    calls: list[str] = []
    monkeypatch.setattr(ingest, "fetch_issues", lambda jira, jql: (calls.append(jql) or []))
    monkeypatch.setattr(ingest, "fetch_dev_panel_keys", lambda jira, jql, predicate: set())
    monkeypatch.setattr(ingest, "fetch_keys", lambda jira, jql, description: set())
    monkeypatch.setattr(ingest, "fetch_transitions", lambda jira, keys: [])

    plan = ingest.SyncPlan(watermark=datetime(2026, 8, 19, 0, 0), last_full_sync=_NOW)
    ingest.run_sync(conn, object(), plan, _NOW, ZoneInfo("America/Denver"))
    assert len(calls) == 1, "only the incremental slice; no backfill pass"


def test_the_incremental_jql_builds_at_all():
    """A regression guard for a NameError that reached production.

    Commit 1cb4914 deleted a block of module constants along with the dev-summary parser it was
    meant to remove, taking `_WATERMARK_MARGIN` with it. `build_incremental_jql` still referenced it,
    so every incremental Jira sync on the deployed pod raised NameError — and nothing in the suite
    called this function, so it merged green.
    """
    from zoneinfo import ZoneInfo
    jql = ingest.build_incremental_jql(datetime(2026, 8, 19, 21, 21), ZoneInfo("America/Denver"))
    assert 'updated >= "2026-08-19 15:19"' in jql, "watermark, converted to the account tz, less the margin"
    assert jql.startswith("project = DEVOPS")


def test_the_watermark_margin_overlaps_rather_than_butting_up():
    """Jira's `updated` has minute resolution, so an exact floor drops same-minute updates."""
    from zoneinfo import ZoneInfo
    exact = datetime(2026, 8, 19, 21, 21)
    jql = ingest.build_incremental_jql(exact, ZoneInfo("UTC"))
    assert '"2026-08-19 21:19"' in jql, "two minutes of overlap"


class _RecordingJira:
    """Captures the JQL actually sent, which is the thing that was wrong in production."""

    def __init__(self):
        self.jql: list[str] = []

    def enhanced_search_issues(self, jql_str, **kwargs):
        self.jql.append(jql_str)
        return []


def test_the_dev_panel_query_strips_the_scopes_order_by():
    """Jira rejects an ORDER BY inside parentheses, and every JQL this module builds ends with one.

    Shipped broken: `(project = DEVOPS AND updated >= "..." ORDER BY updated ASC) AND
    development[pullrequests].all > 0` returned HTTP 400 "Expecting ')' but got 'ORDER'", which killed
    the whole sync. Every test that touched the sync had stubbed fetch_dev_panel_keys, so none of them
    ever built this string.
    """
    jira = _RecordingJira()
    scope = ingest.build_incremental_jql(datetime(2026, 8, 19, 21, 21), ZoneInfo("UTC"))
    assert "ORDER BY" in scope, "the scope really does carry one"

    ingest.fetch_dev_panel_keys(jira, scope, "development[pullrequests].all > 0")

    sent = jira.jql[0]
    assert "ORDER BY" not in sent, f"ORDER BY must not survive into the sub-clause: {sent}"
    assert sent == ('(project = DEVOPS AND updated >= "2026-08-19 21:19") '
                    'AND development[pullrequests].all > 0')


def test_every_scope_this_module_builds_survives_being_wrapped():
    """The full crawl, the incremental slice and the backfill all end with ORDER BY."""
    scopes = [
        ingest._FULL_JQL,
        ingest.build_incremental_jql(datetime(2026, 8, 19, 21, 21), ZoneInfo("UTC")),
        'project = DEVOPS AND created >= "2026-03-01" ORDER BY created ASC',
    ]
    for scope in scopes:
        jira = _RecordingJira()
        ingest.fetch_dev_panel_keys(jira, scope, "development[commits].all > 0")
        sent = jira.jql[0]
        assert "ORDER BY" not in sent, sent
        assert sent.startswith("(project = DEVOPS") and sent.endswith("development[commits].all > 0")
