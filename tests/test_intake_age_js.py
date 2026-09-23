"""A ticket's age on the intake queue counts from today, in Pacific calendar days.

The page carried `ASOF = "2026-07-01"` from the static snapshot it was ported from, so every ticket
created after July 1 showed a negative age: DEVOPS-10611, created 2026-09-22, read "-83d" on
2026-09-23. A negative age never reaches the aging (>=5d) or stale (>=7d) thresholds either, so no
ticket had been flagged since July -- including the months-old ones the queue was full of.

These run the page's own `ageOf` under a pinned clock and a pinned viewer timezone, on the `created`
value the real API emits, for the reason set out in test_capacity_gauge_js.py: a check fed
arguments the test invented cannot catch a mismatch between two pieces of production code.

Like the other dashboard JS tests, these SKIP without node, which the CI image does not have.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from darkstar import intake, store

_DASHBOARD = Path(__file__).resolve().parents[1] / "darkstar" / "dashboards" / "intake.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed (CI image is python:3.12-slim)")

# The page reads the clock once, at load, so the clock is pinned before its scripts run.
_HARNESS = """
globalThis.document = { getElementById: () => ({ set innerHTML(v){}, addEventListener(){},
                          querySelector: () => null, querySelectorAll: () => [] }),
                        querySelector: () => ({ insertAdjacentHTML(){}, querySelectorAll: () => [] }),
                        querySelectorAll: () => [], addEventListener(){} };
globalThis.window = globalThis;
globalThis.fetch = () => Promise.reject(new Error("no network in tests"));
const __RealDate = Date;
globalThis.Date = class extends __RealDate {
  constructor(...args){ super(...(args.length ? args : [process.env.DARKSTAR_TEST_NOW])); }
  static now(){ return new __RealDate(process.env.DARKSTAR_TEST_NOW).getTime(); }
};
"""

_DRIVE = """
console.log(JSON.stringify({age: ageOf(process.env.DARKSTAR_TEST_CREATED), stale: STALE}));
"""


def _api_created(created_utc: datetime) -> str:
    """The `created` value /api/intake emits for a queue ticket created at `created_utc`."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, [store.IssueRow(
        key="DEVOPS-10611", id=10611, project="DEVOPS", issuetype="Story", status="Reviewing",
        status_category="new", priority="Medium", summary="s", assignee=None,
        assignee_account_id=None, reporter=None, business_lead=None, parent_key=None,
        created=created_utc, updated=created_utc, resolutiondate=None, planned_start=None,
        target_end=None, labels=[], mr_field_url=None, dev_has_pr=False, dev_has_commits=False,
        estimated_size=None, fetched_at=created_utc)])
    return intake.intake_report(conn, created_utc)["queue"][0]["created"]


def _age(created: str, now_utc: str, viewer_tz: str) -> dict:
    """Run the page's own ageOf at `now_utc`, as seen by a browser in `viewer_tz`."""
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>",
                                   _DASHBOARD.read_text(encoding="utf-8"), re.S))
    result = subprocess.run(["node", "-e", _HARNESS + scripts + _DRIVE],
                            capture_output=True, text=True, timeout=30,
                            env={**os.environ, "TZ": viewer_tz, "DARKSTAR_TEST_NOW": now_utc,
                                 "DARKSTAR_TEST_CREATED": created})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_ticket_created_yesterday_is_one_day_old():
    """The report exactly: DEVOPS-10611, created 2026-09-22 14:36 MDT, viewed 2026-09-23 14:45 MDT."""
    created = _api_created(datetime(2026, 9, 22, 20, 36, 40))
    assert _age(created, "2026-09-23T20:45:00Z", "America/Denver")["age"] == 1


def test_every_viewer_sees_the_same_age_whatever_their_timezone():
    """PE has engineers on EET, whose morning is still the previous evening in Pacific.

    At 06:30 UTC on 2026-09-24 it is 09:30 in Kyiv but 23:30 on the 23rd in Pacific. Counting from
    the viewer's date, or from UTC's, would call yesterday's ticket two days old for them while the
    lead in Denver saw one.
    """
    created = _api_created(datetime(2026, 9, 22, 20, 36, 40))
    kyiv = _age(created, "2026-09-24T06:30:00Z", "Europe/Kyiv")["age"]
    denver = _age(created, "2026-09-24T06:30:00Z", "America/Denver")["age"]
    assert kyiv == denver == 1


def test_a_week_old_ticket_is_flagged_stale():
    """The consequence nobody saw: a negative age never reached the stale threshold."""
    created = _api_created(datetime(2026, 9, 16, 16, 0, 0))
    result = _age(created, "2026-09-23T20:45:00Z", "America/Denver")
    assert result["age"] == 7 and result["age"] >= result["stale"]
