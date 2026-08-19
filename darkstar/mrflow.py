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

Two cuts of the same population: by author, and by the business-timezone day the MR merged. Both
honour the editable roster, so a hidden author leaves both the rows and the totals.

Name filtering happens HERE rather than in the page, because a median cannot be re-aggregated from
per-author medians -- filtering the daily series client-side would silently produce wrong numbers.
One filter, applied once, and every cut stays consistent with it.

Only merged MRs reach the store (the ingest crawls state=merged), so this is time-to-merge for
work that landed, not a queue depth: an MR still sitting open is invisible here until it merges.

Rows crawled before `opened_at` existed cannot be measured and are excluded, but the count is
REPORTED as `incomplete` rather than dropped in silence. Without that, a store mid-backfill looks
exactly like a team that did no work before a certain date — the chart simply starts late and says
nothing about why.
"""
from __future__ import annotations

import statistics
from datetime import datetime

import duckdb

from darkstar.gitlab_domains import environment_of
from darkstar.metrics import (
    SELF_SERVICE_EPOCH,
    business_date,
    business_hours_between,
    pctile,
    ready_hours,
    window_start,
)
from darkstar import store
from darkstar.roster import MR_AUTHOR_NAMES, TRACKED_MR_AUTHORS

_WINDOW_MONTHS: int = 6
_ALL_ENVIRONMENTS: str = "all"
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


def _matches(name: str, terms: list[str]) -> bool:
    """True if no filter is set, or any term is a substring of the name (case-insensitive)."""
    return not terms or any(term in name.lower() for term in terms)


def crawl_state(connection: duckdb.DuckDBPyConnection, roster: dict) -> dict:
    """Whether the store has caught up with the roster, and when it last did.

    An added author cannot appear until a crawl has fetched their merge requests. Without this the
    page has no way to distinguish "the crawl is still running" from "the add did nothing", which
    is exactly how a working add gets reported as broken.
    """
    wanted = int(roster.get("version", 0))
    crawled = store.get_roster_version(connection)
    last = store.get_gitlab_watermark(connection)
    return {
        "roster_version": wanted,
        "crawled_version": crawled,
        "pending": wanted != crawled,
        "last_crawl": last.isoformat(timespec="minutes") if last else None,
    }


def mr_turnaround_report(
    connection: duckdb.DuckDBPyConnection, since: datetime, roster: dict, name_filter: list[str],
    environment: str,
) -> dict:
    """Per-author, per-day and team-wide opened->merged turnaround for MRs merged since `since`.

    `roster` is the persisted editable roster (mr_authors.read): its "added" map names authors the
    static roster does not know, and its "hidden" list drops names from the rows *and* the totals,
    so the totals always describe what is actually on screen. `name_filter` is a list of lowercase
    substrings; empty means no filtering.
    """
    names = {**MR_AUTHOR_NAMES, **{username: name for username, name in (roster.get("added") or {}).items()}}
    hidden = set(roster.get("hidden") or [])
    # One pass, two cuts: keep (account, merged-day, hours) per MR so the per-author and per-day
    # views are guaranteed to describe exactly the same population.
    events_by_mr: dict[int, list[tuple[str, datetime]]] = {}
    for mr_id, kind, happened_at in connection.execute(
        "SELECT mr_id, kind, happened_at FROM mr_events ORDER BY mr_id, seq"
    ).fetchall():
        events_by_mr.setdefault(mr_id, []).append((kind, happened_at))

    # In-window rows that predate the opened_at column and so cannot be measured yet. The next
    # full crawl repairs them; until then the chart would otherwise just start late for no visible
    # reason.
    incomplete, earliest_measurable = connection.execute(
        "SELECT count(*) FILTER (WHERE opened_at IS NULL), min(merged_at) FILTER (WHERE opened_at IS NOT NULL) "
        "FROM merge_requests WHERE merged_at >= ?", [since],
    ).fetchone()

    business_by_account: dict[str, list[float]] = {}
    by_day: dict[str, list[float]] = {}
    by_author_day: dict[str, dict[str, list[float]]] = {}
    review_waits: list[float] = []
    reviewed = 0
    for mr_id, account_id, project_path, opened_at, merged_at in connection.execute(
        "SELECT id, author_account_id, project_path, opened_at, merged_at FROM merge_requests "
        "WHERE merged_at >= ? AND opened_at IS NOT NULL",
        [since],
    ).fetchall():
        name = names.get(account_id)
        if name is None or name in hidden or not _matches(name, name_filter):
            continue
        if environment != _ALL_ENVIRONMENTS and environment_of(project_path) != environment:
            continue
        events = events_by_mr.get(mr_id, [])
        hours = ready_hours(opened_at, merged_at, events)
        review_at = next((when for kind, when in events if kind == "review"), None)
        if review_at is not None:
            reviewed += 1
            review_waits.append(ready_hours(opened_at, min(review_at, merged_at), events))
        day = business_date(merged_at).isoformat()
        business_by_account.setdefault(account_id, []).append(hours)
        by_day.setdefault(day, []).append(hours)
        by_author_day.setdefault(name, {}).setdefault(day, []).append(hours)

    authors: list[dict] = []
    shown_business: list[float] = []
    for account_id, business in business_by_account.items():
        name = names[account_id]   # unmapped/hidden/filtered-out accounts never got this far
        shown_business.extend(business)
        authors.append({
            "name": name,
            "tracked": account_id in _TRACKED_ACCOUNTS or account_id in (roster.get("added") or {}),
            **_stats(business),
        })
    # Slowest first on the standard clock, matching the lead-time dashboard; no median sorts last.
    authors.sort(key=lambda a: (a["biz_hours_median"] is None, -(a["biz_hours_median"] or 0.0), a["name"]))

    # Most recent day first: this reads as a log, not a chart axis.
    daily = [{"day": day, **_stats(hours)} for day, hours in sorted(by_day.items(), reverse=True)]

    # One series per author, each point a day that author actually merged on. Computed here for the
    # same reason the filter is: a per-author median cannot be recovered from the combined one.
    # `days` is the shared x axis, ascending, so the page never has to reconcile two orderings.
    series = [
        {
            "name": name,
            "points": [{"day": day, **_stats(hours)} for day, hours in sorted(days.items())],
        }
        for name, days in sorted(by_author_day.items())
    ]

    return {
        "authors": authors,
        "daily": daily,
        "series": series,
        "days": sorted(by_day),
        "team": _stats(shown_business),
        "window_months": _WINDOW_MONTHS,
        "window_start": since.date().isoformat(),
        "added": roster.get("added") or {},
        "hidden": sorted(hidden),
        "filter": name_filter,
        "environment": environment,
        "crawl": crawl_state(connection, roster),
        "incomplete": int(incomplete or 0),
        "earliest_measurable": earliest_measurable.date().isoformat() if earliest_measurable else None,
        # Time to the first human review comment (bots and the author's own notes excluded at
        # ingest), on the same ready-clock. Separates "nobody looked" from "reviewed, then iterated".
        "first_review": {
            "reviewed": reviewed,
            "hours_median": round(statistics.median(review_waits), 1) if review_waits else None,
            "hours_p90": pctile(review_waits, 0.9),
        },
    }
