"""An opened epic's story table sorts on Status, Size, Due and In progress.

A sort is only useful if it groups rows the way a reader thinks about them. Sorting Size or Status
alphabetically would put Large before Small and Blocked before To Do, and sorting In progress on its
"12d" text would put 100 days before 9. A story with No Data in the sorted column has to stay at the
bottom whichever way the sort runs, or reversing a mostly-unsized table buries every real value.

These run the page's own `storyOrder` on the story rows `delivery_report` emits, for the reason set
out in test_capacity_gauge_js.py: a check fed rows the test invented cannot catch a mismatch between
the API and the page.

Like the other dashboard JS tests, these SKIP without node, which the CI image does not have.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from darkstar import delivery, store

_DASHBOARD = Path(__file__).resolve().parents[1] / "darkstar" / "dashboards" / "delivery-forecast.html"
_NOW = datetime(2026, 9, 23, 18, 0, 0)
_EPIC = "DEVOPS-100"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed (CI image is python:3.12-slim)")

# Enough of a DOM for the page's module-scope code to load. The page reads the clock once, at load,
# so the clock is pinned before its scripts run; init() fetches, so fetch rejects.
_HARNESS = """
const __el = () => ({ set innerHTML(v){}, insertAdjacentHTML(){}, querySelector: () => null, querySelectorAll: () => [] });
globalThis.document = { getElementById: __el, querySelector: __el, querySelectorAll: () => [], addEventListener(){} };
globalThis.window = globalThis;
globalThis.matchMedia = () => ({ matches: true });
globalThis.fetch = () => Promise.reject(new Error("no network in tests"));
const __RealDate = Date;
globalThis.Date = class extends __RealDate {
  constructor(...args){ super(...(args.length ? args : [process.env.DARKSTAR_TEST_NOW])); }
  static now(){ return new __RealDate(process.env.DARKSTAR_TEST_NOW).getTime(); }
};
"""

_DRIVE = """
const report = JSON.parse(process.env.DARKSTAR_TEST_REPORT);
CHILDREN = report.children; CYCLE = report.cycle;
const order = dir => [...CHILDREN[process.env.DARKSTAR_TEST_EPIC]]
  .sort(storyOrder(process.env.DARKSTAR_TEST_FIELD, dir)).map(s => s.k);
console.log(JSON.stringify({ascending: order(1), descending: order(-1)}));
"""


def _row(key: str, issuetype: str, parent_key: str | None, status: str, category: str,
         size: str | None, due: date | None, updated: datetime) -> store.IssueRow:
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype=issuetype, status=status,
        status_category=category, priority="Medium", summary=key, assignee=None,
        assignee_account_id=None, reporter=None, business_lead=None, parent_key=parent_key,
        created=_NOW - timedelta(days=200), updated=updated, resolutiondate=None, planned_start=None,
        target_end=None, labels=[], mr_field_url=None, dev_has_pr=False, dev_has_commits=False,
        estimated_size=size, due_date=due, fetched_at=_NOW)


def _story(key: str, status: str, category: str, size: str | None, due: date | None,
           updated: datetime) -> store.IssueRow:
    return _row(key, "Story", _EPIC, status, category, size, due, updated)


def _sorted(stories: list[store.IssueRow], field: str) -> dict[str, list[str]]:
    """Story keys in the order the page sorts them on `field`, both ways."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    epic = _row(_EPIC, "Feature", None, "In Progress", "indeterminate", None, None, _NOW)
    store.upsert_issues(conn, [epic, *stories])
    report = delivery.delivery_report(conn, _NOW)
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", _DASHBOARD.read_text(encoding="utf-8"), re.S))
    result = subprocess.run(["node", "-e", _HARNESS + scripts + _DRIVE],
                            capture_output=True, text=True, timeout=30,
                            env={**os.environ, "TZ": "America/Denver", "DARKSTAR_TEST_NOW": "2026-09-23T18:00:00Z",
                                 "DARKSTAR_TEST_REPORT": json.dumps(report, default=str),
                                 "DARKSTAR_TEST_EPIC": _EPIC, "DARKSTAR_TEST_FIELD": field})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_size_sorts_by_magnitude_and_unsized_stories_stay_last_either_way():
    """Alphabetical would read Large, Medium, Small, XL; most stories are unsized, so they go last."""
    order = _sorted([
        _story("DEVOPS-1", "To Do", "new", "XL", None, _NOW),
        _story("DEVOPS-2", "To Do", "new", None, None, _NOW),
        _story("DEVOPS-3", "To Do", "new", "Small", None, _NOW),
        _story("DEVOPS-4", "To Do", "new", "Large", None, _NOW),
        _story("DEVOPS-5", "To Do", "new", "Medium", None, _NOW),
    ], "size")
    assert order["ascending"] == ["DEVOPS-3", "DEVOPS-5", "DEVOPS-4", "DEVOPS-1", "DEVOPS-2"]
    assert order["descending"] == ["DEVOPS-1", "DEVOPS-4", "DEVOPS-5", "DEVOPS-3", "DEVOPS-2"]


def test_status_sorts_by_stage_so_open_work_leads():
    """Open work comes before closed work, and Will Not Do after Done: closed but not delivered.

    Triage sorts with To Do although Jira files it under the In Progress category -- nobody has
    started it, which is the same rule its status pill follows.
    """
    order = _sorted([
        _story("DEVOPS-1", "Done", "done", None, None, _NOW),
        _story("DEVOPS-2", "Will Not Do", "done", None, None, _NOW),
        _story("DEVOPS-3", "On Hold", "indeterminate", None, None, _NOW),
        _story("DEVOPS-4", "In Progress", "indeterminate", None, None, _NOW),
        _story("DEVOPS-5", "Triage", "indeterminate", None, None, _NOW),
        _story("DEVOPS-6", "Blocked", "indeterminate", None, None, _NOW),
        _story("DEVOPS-7", "To Do", "new", None, None, _NOW),
    ], "status")
    assert order["ascending"] == ["DEVOPS-7", "DEVOPS-5", "DEVOPS-4", "DEVOPS-6", "DEVOPS-3", "DEVOPS-1", "DEVOPS-2"]


def test_in_progress_sorts_by_days_as_numbers():
    """Sorted as text, "100d" would come before "9d". A story not started has no figure and goes last."""
    order = _sorted([
        _story("DEVOPS-1", "In Progress", "indeterminate", None, None, _NOW - timedelta(days=100)),
        _story("DEVOPS-2", "To Do", "new", None, None, _NOW),
        _story("DEVOPS-3", "In Progress", "indeterminate", None, None, _NOW - timedelta(days=9)),
        _story("DEVOPS-4", "In Progress", "indeterminate", None, None, _NOW - timedelta(days=12)),
    ], "age")
    assert order["ascending"] == ["DEVOPS-3", "DEVOPS-4", "DEVOPS-1", "DEVOPS-2"]
    assert order["descending"] == ["DEVOPS-1", "DEVOPS-4", "DEVOPS-3", "DEVOPS-2"]


def test_due_sorts_by_date_and_undated_stories_stay_last_either_way():
    order = _sorted([
        _story("DEVOPS-1", "To Do", "new", None, date(2027, 1, 5), _NOW),
        _story("DEVOPS-2", "To Do", "new", None, None, _NOW),
        _story("DEVOPS-3", "To Do", "new", None, date(2026, 9, 30), _NOW),
        _story("DEVOPS-4", "To Do", "new", None, date(2026, 10, 15), _NOW),
    ], "due")
    assert order["ascending"] == ["DEVOPS-3", "DEVOPS-4", "DEVOPS-1", "DEVOPS-2"]
    assert order["descending"] == ["DEVOPS-1", "DEVOPS-4", "DEVOPS-3", "DEVOPS-2"]
