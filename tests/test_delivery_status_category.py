"""Forecast items carry their status category, so the page can colour Initiative and Feature pills.

The delivery forecast page colours every status pill -- stories, Features and Initiatives -- with one
rule, `statusTone(status, category)`. Stories always carried their category ("c"); the Initiative and
Feature items did not, so the page could only have guessed from the status name, and that guess is
wrong exactly where it matters: a Done Feature still rolled up under an open Initiative has to read as
delivered.
"""
from datetime import datetime

import duckdb

from darkstar import delivery, store

_NOW = datetime(2026, 9, 23, 12, 0, 0)


def _issue(key, issuetype, status, category, parent_key):
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype=issuetype, status=status,
        status_category=category, priority="Medium", summary=key, assignee=None,
        assignee_account_id=None, reporter=None, business_lead=None, parent_key=parent_key,
        created=_NOW, updated=_NOW, resolutiondate=None, planned_start=None, target_end=None,
        labels=[], mr_field_url=None, dev_has_pr=False, dev_has_commits=False, estimated_size=None, due_date=None,
        fetched_at=_NOW)


def test_every_item_carries_the_category_its_pill_is_coloured_by():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, [
        _issue("DEVOPS-1", "Initiative", "In Progress", "indeterminate", None),
        _issue("DEVOPS-2", "Feature", "Done", "done", "DEVOPS-1"),   # delivered, still rolled up
        _issue("DEVOPS-3", "Feature", "Reviewing", "new", None),
    ])
    items = delivery.delivery_report(conn, _NOW)["items"]
    assert {item["key"]: item["c"] for item in items} == {
        "DEVOPS-1": "indeterminate", "DEVOPS-2": "done", "DEVOPS-3": "new"}
