"""Merge-request turnaround per author: opened -> merged.

Reads only from the store. Answers "how fast does an author's work actually land" for every
author the GitLab ingest attributes (roster.MR_AUTHORS = the PE roster plus the non-roster
contributors in roster.TRACKED_MR_AUTHORS), over the trailing window of MRs *merged* in it.

Reported in business hours only (Mon-Fri 08:00-17:00 US/Pacific, holidays excluded) -- one
standard clock shared with the SLA view, because Audacy's users are overwhelmingly North American
and turnaround is judged against their working day. Raw calendar elapsed time is deliberately not
reported: it bills a request for nights, weekends and holidays nobody was working, which says
nothing useful about delivery speed.

One property to know when reading a single row: PE also has engineers working EET, whose own
working day falls inside Pacific's night, so an MR they open and merge inside their own hours can
score near 0.0 on the business clock.

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
_TRACKED_ACCOUNTS: frozenset[str] = frozenset(TRACKED_MR_AUTHORS.values())


def _stats(business: list[float]) -> dict:
    """Business-hour median and p90 of opened->merged, for one author or the whole team."""
    return {
        "merged": len(business),
        "biz_hours_median": round(statistics.median(business), 1) if business else None,
        "biz_hours_p90": pctile(business, 0.9),
    }


def default_window_start(now: datetime) -> datetime:
    """The window this view uses unless the page overrides it: six months, floored at the epoch."""
    return window_start(now, _WINDOW_MONTHS, SELF_SERVICE_EPOCH)


def mr_turnaround_report(
    connection: duckdb.DuckDBPyConnection, since: datetime, roster: dict
) -> dict:
    """Per-author and team-wide opened->merged turnaround for MRs merged on or after `since`.

    `roster` is the persisted editable roster (mr_authors.read): its "added" map names authors the
    static roster does not know, and its "hidden" list drops names from the rows *and* the team
    totals, so the totals always describe what is actually on screen.
    """
    names = {**MR_AUTHOR_NAMES, **{username: name for username, name in (roster.get("added") or {}).items()}}
    hidden = set(roster.get("hidden") or [])
    business_by_account: dict[str, list[float]] = {}
    for account_id, opened_at, merged_at in connection.execute(
        "SELECT author_account_id, opened_at, merged_at FROM merge_requests "
        "WHERE merged_at >= ? AND opened_at IS NOT NULL",
        [since],
    ).fetchall():
        business_by_account.setdefault(account_id, []).append(business_hours_between(opened_at, merged_at))

    authors: list[dict] = []
    shown_business: list[float] = []
    for account_id, business in business_by_account.items():
        name = names.get(account_id)
        if name is None:
            continue  # attributed at ingest under a mapping since removed from the roster
        if name in hidden:
            continue
        shown_business.extend(business)
        authors.append({
            "name": name,
            "tracked": account_id in _TRACKED_ACCOUNTS or account_id in (roster.get("added") or {}),
            **_stats(business),
        })
    # Slowest first on the standard clock, matching the lead-time dashboard; no median sorts last.
    authors.sort(key=lambda a: (a["biz_hours_median"] is None, -(a["biz_hours_median"] or 0.0), a["name"]))

    return {
        "authors": authors,
        "team": _stats(shown_business),
        "window_months": _WINDOW_MONTHS,
        "window_start": since.date().isoformat(),
        "added": roster.get("added") or {},
        "hidden": sorted(hidden),
    }
