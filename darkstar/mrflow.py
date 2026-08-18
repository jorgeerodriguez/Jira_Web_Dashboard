"""Merge-request turnaround per author: opened -> merged.

Reads only from the store. Answers "how fast does an author's work actually land" for every
author the GitLab ingest attributes (roster.MR_AUTHORS = the PE roster plus the non-roster
contributors in roster.TRACKED_MR_AUTHORS), over the trailing window of MRs *merged* in it.

Business hours (Mon-Fri 09:00-17:00 America/Denver) are the reported figure, one standard clock
shared with the SLA view, because Audacy's users are overwhelmingly North American and turnaround
is judged against their working day. The raw calendar span sits alongside it: the gap between the
two is the share that was nights and weekends.

The standard clock has a known cost to read with care. PE also has engineers working EET, whose
own working day falls inside Denver's night, so their business-hour figure understates how long an
MR really sat -- compare the calendar column before drawing a conclusion about an individual.

Every merged MR counts -- unlike slas.py, which keeps one MR per Jira issue for bucketing, this
makes no per-issue pick.

Only merged MRs reach the store (the ingest crawls state=merged), so this is time-to-merge for
work that landed, not a queue depth: an MR still sitting open is invisible here until it merges.
"""
from __future__ import annotations

import statistics
from datetime import datetime

import duckdb

from darkstar.metrics import SELF_SERVICE_EPOCH, business_hours_between, pctile, window_start
from darkstar.roster import MR_AUTHOR_NAMES, TRACKED_MR_AUTHORS

_WINDOW_MONTHS: int = 6
_HOUR_SECONDS: float = 3600.0
_TRACKED_ACCOUNTS: frozenset[str] = frozenset(TRACKED_MR_AUTHORS.values())


def _stats(business: list[float], calendar: list[float]) -> dict:
    """Business-hour median/p90 opened->merged, plus the raw calendar median, for one group."""
    return {
        "merged": len(calendar),
        "biz_hours_median": round(statistics.median(business), 1) if business else None,
        "biz_hours_p90": pctile(business, 0.9),
        "cal_hours_median": round(statistics.median(calendar), 1) if calendar else None,
    }


def mr_turnaround_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Per-author and team-wide opened->merged turnaround for the trailing window."""
    since = window_start(now, _WINDOW_MONTHS, SELF_SERVICE_EPOCH)
    business_by_account: dict[str, list[float]] = {}
    calendar_by_account: dict[str, list[float]] = {}
    for account_id, opened_at, merged_at in connection.execute(
        "SELECT author_account_id, opened_at, merged_at FROM merge_requests "
        "WHERE merged_at >= ? AND opened_at IS NOT NULL",
        [since],
    ).fetchall():
        business_by_account.setdefault(account_id, []).append(business_hours_between(opened_at, merged_at))
        calendar_by_account.setdefault(account_id, []).append(
            max(0.0, (merged_at - opened_at).total_seconds() / _HOUR_SECONDS)
        )

    authors: list[dict] = []
    for account_id, business in business_by_account.items():
        name = MR_AUTHOR_NAMES.get(account_id)
        if name is None:
            continue  # attributed at ingest under a mapping since removed from the roster
        authors.append({
            "name": name,
            "tracked": account_id in _TRACKED_ACCOUNTS,   # non-roster; flagged in the table
            **_stats(business, calendar_by_account[account_id]),
        })
    # Slowest first on the standard clock, matching the lead-time dashboard; no median sorts last.
    authors.sort(key=lambda a: (a["biz_hours_median"] is None, -(a["biz_hours_median"] or 0.0), a["name"]))

    all_business = [hours for values in business_by_account.values() for hours in values]
    all_calendar = [hours for values in calendar_by_account.values() for hours in values]
    return {
        "authors": authors,
        "team": _stats(all_business, all_calendar),
        "window_months": _WINDOW_MONTHS,
        "window_start": since.date().isoformat(),
    }
