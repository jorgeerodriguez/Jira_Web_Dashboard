"""Issues that leave DEVOPS -- moved to another project, or deleted -- must leave the store too.

The poller asks Jira only for `project = DEVOPS AND updated >= <watermark>`. An issue moved out of
DEVOPS, or deleted, never matches that again, so its row kept the status it last had in DEVOPS
forever. On 2026-09-23, 10 of the 17 tickets in the intake queue were ghosts of that kind: eight
moved to DAT/ADTECH/AWQA/SECOPS/ST (some already Done there), two deleted outright.

The fix has to delete rows, and deleting is the one thing this store could not undo: the slice
never revisits an issue nobody edits, so a row removed in error is gone for good. Most of these
tests pin the evidence a deletion requires rather than the deletion itself.
"""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import duckdb
import pytest
from jira.exceptions import JIRAError

from darkstar import ingest, intake, store

_NOW = datetime(2026, 9, 23, 12, 0, 0)


def _issue(key, status="Triage", status_category="indeterminate"):
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype="Story",
        status=status, status_category=status_category, priority="Medium", summary=key,
        assignee=None, assignee_account_id=None, reporter=None, business_lead=None,
        parent_key=None, created=_NOW, updated=_NOW, resolutiondate=None,
        planned_start=None, target_end=None, labels=[], mr_field_url=None,
        dev_has_pr=False, dev_has_commits=False, estimated_size=None, fetched_at=_NOW)


def _store(issues):
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, issues)
    for issue in issues:
        store.replace_transitions(conn, [issue.key], [store.TransitionRow(
            key=issue.key, to_status=issue.status, changed_at=_NOW, seq=0)])
    return conn


def _keys(conn, table):
    return {key for (key,) in conn.execute(f"SELECT DISTINCT key FROM {table}").fetchall()}


class _Page(list):
    """A search page as the jira client returns it: a list carrying the next page's token."""

    def __init__(self, keys, next_token):
        super().__init__(SimpleNamespace(key=key) for key in keys)
        self.nextPageToken = next_token


class _FakeJira:
    """The two calls the reconcile makes: the open-set search, and a lookup by key.

    `moved` maps a stored key to the key Jira files the issue under now, or None for a 404. A key
    not in it is still filed under its own key. Every lookup is recorded, because WHICH rows get
    looked up is the cost side of the design.
    """

    def __init__(self, open_keys, moved, failure=None):
        self._open = sorted(open_keys)
        self._moved = moved
        self._failure = failure
        self.looked_up: list[str] = []

    def enhanced_search_issues(self, jql_str, **kwargs):
        return _Page(self._open, None)

    def issue(self, key, fields=None):
        self.looked_up.append(key)
        if self._failure is not None:
            raise self._failure
        current = self._moved.get(key, key)
        if current is None:
            raise JIRAError(status_code=404,
                            text="Issue does not exist or you do not have permission to see it.")
        return SimpleNamespace(key=current)


def test_a_ticket_moved_to_another_project_leaves_the_intake_queue():
    """The symptom as reported: DEVOPS-9888 is DAT-5323 now, Done there, and was still in Triage here."""
    conn = _store([_issue("DEVOPS-9888")])
    assert [row["k"] for row in intake.intake_report(conn, _NOW)["queue"]] == ["DEVOPS-9888"]

    jira = _FakeJira(open_keys=set(), moved={"DEVOPS-9888": "DAT-5323"})
    assert ingest.reconcile_departed_issues(jira, conn) == ["DEVOPS-9888"]

    assert intake.intake_report(conn, _NOW)["queue"] == []
    assert _keys(conn, "issues") == set() and _keys(conn, "transitions") == set(), \
        "its transitions go with it, or a dangling history outlives the issue"


def test_a_deleted_ticket_is_removed_on_the_404_alone():
    """Jira answers 404 for a deleted issue. That is an answer, so it is not retried."""
    conn = _store([_issue("DEVOPS-9769", status="Reviewing", status_category="new")])
    jira = _FakeJira(open_keys=set(), moved={"DEVOPS-9769": None})

    assert ingest.reconcile_departed_issues(jira, conn) == ["DEVOPS-9769"]
    assert _keys(conn, "issues") == set()
    assert jira.looked_up == ["DEVOPS-9769"], "one lookup; a 404 is not a failure to retry"


def test_missing_from_the_open_search_alone_never_deletes():
    """The safety property, and the reason every candidate is looked up by key.

    An issue can drop out of Jira's open set for ordinary reasons -- it closed, or was created,
    after this cycle's slice was read -- and a search that came back short would drop ALL of them.
    Deleting on absence would remove rows the slice will never bring back.
    """
    conn = _store([_issue("DEVOPS-10604"), _issue("DEVOPS-10632")])
    jira = _FakeJira(open_keys=set(), moved={})     # the search returned nothing at all

    assert ingest.reconcile_departed_issues(jira, conn) == []
    assert _keys(conn, "issues") == {"DEVOPS-10604", "DEVOPS-10632"}


def test_a_ticket_moved_out_and_back_leaves_no_stale_duplicate():
    """Jira gives a moved issue a new key every time, so a round trip lands on a NEW DEVOPS key.

    The slice has already stored that new key (the move bumps `updated`). Comparing projects would
    keep the old row, since the issue is in DEVOPS again; comparing keys removes it.
    """
    conn = _store([_issue("DEVOPS-9888"), _issue("DEVOPS-10700")])
    jira = _FakeJira(open_keys={"DEVOPS-10700"}, moved={"DEVOPS-9888": "DEVOPS-10700"})

    assert ingest.reconcile_departed_issues(jira, conn) == ["DEVOPS-9888"]
    assert _keys(conn, "issues") == {"DEVOPS-10700"}


def test_a_lookup_that_fails_raises_and_deletes_nothing(monkeypatch):
    """An outage must never read as "deleted": only a 404 counts as evidence."""
    monkeypatch.setattr(ingest, "_RETRY_BACKOFF_SECONDS", 0.0)
    conn = _store([_issue("DEVOPS-10604")])
    jira = _FakeJira(open_keys=set(), moved={}, failure=JIRAError(status_code=503, text="unavailable"))

    with pytest.raises(JIRAError):
        ingest.reconcile_departed_issues(jira, conn)
    assert _keys(conn, "issues") == {"DEVOPS-10604"}
    assert len(jira.looked_up) == ingest._RETRY_ATTEMPTS, "retried, then raised"


def test_only_stored_open_rows_that_jira_does_not_list_as_open_are_looked_up():
    """The cost side. One lookup per stored row would be ~9,800 requests every 15 minutes.

    A done row is not a candidate at all: it puts nothing on screen as live work, and if it later
    leaves DEVOPS it keeps counting in the history it was part of.
    """
    conn = _store([
        _issue("DEVOPS-1", status="Done", status_category="done"),   # absent from the open set
        _issue("DEVOPS-2", status="In Progress"),                    # listed as open
        _issue("DEVOPS-3", status="Reviewing", status_category="new"),
    ])
    jira = _FakeJira(open_keys={"DEVOPS-2"}, moved={})

    ingest.reconcile_departed_issues(jira, conn)
    assert jira.looked_up == ["DEVOPS-3"]
    assert _keys(conn, "issues") == {"DEVOPS-1", "DEVOPS-2", "DEVOPS-3"}


def test_the_open_set_is_read_across_every_page():
    """A key on page two missed here becomes a lookup on every cycle."""
    pages = {None: _Page(["DEVOPS-1", "DEVOPS-2"], "t2"), "t2": _Page(["DEVOPS-3"], None)}
    jira = SimpleNamespace(enhanced_search_issues=lambda jql_str, **kwargs:
                           pages[kwargs.get("nextPageToken")])

    assert ingest.fetch_keys(jira, ingest._OPEN_JQL, "open") == {"DEVOPS-1", "DEVOPS-2", "DEVOPS-3"}


def test_a_sync_cycle_removes_tickets_that_left_devops(monkeypatch):
    """The reconcile has to be wired into the cycle, not merely exist: nothing else will call it."""
    conn = _store([_issue("DEVOPS-10127", status="Reviewing", status_category="new")])
    monkeypatch.setattr(ingest, "fetch_issues", lambda jira, jql: [])
    monkeypatch.setattr(ingest, "fetch_dev_panel_keys", lambda jira, jql, predicate: set())
    monkeypatch.setattr(ingest, "fetch_transitions", lambda jira, keys: [])
    store.set_fields_version(conn, ingest._FIELDS_VERSION)

    plan = ingest.SyncPlan(watermark=datetime(2026, 9, 23, 11, 45), last_full_sync=_NOW)
    jira = _FakeJira(open_keys=set(), moved={"DEVOPS-10127": "ST-1066"})
    result = ingest.run_sync(conn, jira, plan, _NOW, ZoneInfo("America/Denver"))

    assert result.departed_issues == 1 and result.total_issues == 0
    assert intake.intake_report(conn, _NOW)["queue"] == []
