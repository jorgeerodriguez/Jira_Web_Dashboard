"""Due date and Estimated Size reach the delivery forecast.

The forecast page compares an Initiative's or Feature's Due date with its P85 date and flags it
"at risk" when the forecast lands later, and shows Size and Due date on every card and story row.
Estimated Size was already ingested (for intake's weighted WIP); Due date is Jira's built-in
`duedate`, which the poller did not ask for, so every date the team entered was invisible here.
"""
from datetime import date, datetime
from types import SimpleNamespace

import duckdb

from darkstar import delivery, ingest, store

_NOW = datetime(2026, 9, 23, 12, 0, 0)


def _jira_issue(**fields):
    base = {"summary": "s", "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
            "issuetype": {"name": "Feature"}, "priority": {"name": "Low"}, "labels": [],
            "created": "2026-08-05T16:00:00.000-0600", "updated": "2026-08-05T17:00:00.000-0600",
            "project": {"key": "DEVOPS"}}
    base.update(fields)
    return SimpleNamespace(raw={"key": "DEVOPS-1", "id": "1", "fields": base})


def test_the_issue_sync_asks_jira_for_the_due_date():
    """Dropping it from the field list costs every due date silently: the payload just arrives thinner."""
    assert "duedate" in ingest._ISSUE_FIELDS.split(",")


def test_a_due_date_reaches_the_row_as_a_date_and_an_unset_one_as_none():
    assert ingest._map_issue(_jira_issue(duedate="2026-11-30"), _NOW).due_date == date(2026, 11, 30)
    assert ingest._map_issue(_jira_issue(duedate=None), _NOW).due_date is None
    assert ingest._map_issue(_jira_issue(), _NOW).due_date is None


def test_a_store_that_predates_the_column_gains_it():
    """The deployed store has ~10,000 rows and no due_date column; initialize_schema must add it."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    conn.execute("ALTER TABLE issues DROP COLUMN due_date")
    store.initialize_schema(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info('issues')").fetchall()}
    assert "due_date" in columns


def _row(key, issuetype, status, category, parent_key, size, due):
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype=issuetype, status=status,
        status_category=category, priority="Medium", summary=key, assignee=None,
        assignee_account_id=None, reporter=None, business_lead=None, parent_key=parent_key,
        created=_NOW, updated=_NOW, resolutiondate=None, planned_start=None, target_end=None,
        labels=[], mr_field_url=None, dev_has_pr=False, dev_has_commits=False, estimated_size=size,
        due_date=due, fetched_at=_NOW)


def test_the_forecast_carries_size_and_due_date_on_cards_and_story_rows():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, [
        _row("DEVOPS-1", "Initiative", "In Progress", "indeterminate", None, None, date(2026, 12, 31)),
        _row("DEVOPS-2", "Feature", "In Progress", "indeterminate", "DEVOPS-1", "Large", date(2026, 11, 30)),
        _row("DEVOPS-3", "Story", "To Do", "new", "DEVOPS-2", "Small", date(2026, 10, 15)),
        _row("DEVOPS-4", "Story", "To Do", "new", "DEVOPS-2", None, None),
    ])
    report = delivery.delivery_report(conn, _NOW)
    items = {item["key"]: (item["size"], item["due"]) for item in report["items"]}
    assert items == {"DEVOPS-1": (None, "2026-12-31"), "DEVOPS-2": ("Large", "2026-11-30")}
    stories = {child["k"]: (child["sz"], child["dd"]) for child in report["children"]["DEVOPS-2"]}
    assert stories == {"DEVOPS-3": ("Small", "2026-10-15"), "DEVOPS-4": (None, None)}, \
        "an unset size or due date is None, not a guessed default"
