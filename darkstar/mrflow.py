"""Merge-request turnaround per author: opened -> merged.

Reads only from the store. Answers "how fast does an author's work actually land" for every
author added to the view, over the trailing window of MRs *merged* in it. The ingest attributes
every author it finds; this view lists only the ones somebody asked for.

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
from datetime import date, datetime

import duckdb

from darkstar.gitlab_domains import (
    MIXED_ENVIRONMENT,
    OTHER_ENVIRONMENT,
    environment_of,
    is_self_service_mr,
)
from darkstar.metrics import (
    SELF_SERVICE_EPOCH,
    business_date,
    business_hours_between,
    choose_grain,
    is_partial_period,
    pctile,
    period_start,
    ready_hours,
    ready_spans,
    red_spans,
    window_start,
)
from darkstar import store
from darkstar.roster import GITLAB_USERNAMES, NON_HUMAN_GROUP_MEMBERS, ROSTER

_WINDOW_MONTHS: int = 6
# The drill-down list is for inspecting outliers, not for browsing the whole window: a 6-month
# lookback is well over a thousand merge requests. Capped, and the count left out is reported rather
# than the list quietly ending. The page flips through it ten at a time, which is why the cap can be
# this generous -- at 25 the tail was unreachable rather than merely unlisted.
_SLOWEST_LIMIT: int = 100
# Readability is the page size's job now that the chart pages; this cap only bounds the payload over
# an open population. Keeping it equal to the page size would put every author past the twentieth
# permanently out of reach, since no page could ever scroll to them. Whoever still falls off is
# counted rather than dropped, so a truncated chart cannot read as the whole population.
_AUTHOR_LIMIT: int = 100
_ALL_ENVIRONMENTS: str = "all"


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


def first_review_at(events: list[tuple[str, datetime]], merged_at: datetime,
                    author_is_merger: bool) -> datetime | None:
    """When someone other than the author first engaged with the MR, or None if nobody did.

    Three signals, earliest wins, because PE's workflow produces different evidence depending on
    who raised the MR:

      comment   a human other than the author said something.
      approval  a colleague approved it. PE engineers merge their OWN work once another engineer
                approves, so for PE-authored MRs this is the review — the merge that follows is
                just the mechanic. Counting comments alone measured conversation, not review.
      merge     someone other than the author merged it. Requests from outside PE cannot be merged
                by the requester, so for that work the merge itself IS PE's review action.

    A self-merge is deliberately NOT a signal on its own: it is normal for PE and says nothing
    about whether anyone looked.
    """
    candidates = [when for kind, when in events if kind in ("review", "approval")]
    if not author_is_merger:
        candidates.append(merged_at)
    return min(candidates) if candidates else None


def first_review_signal(events: list[tuple[str, datetime]], merged_at: datetime,
                        author_is_merger: bool) -> tuple[datetime | None, str | None]:
    """`first_review_at` plus WHICH signal it was: "comment", "approval" or "merge".

    Reported because the wait can legitimately come out as zero and a bare "0m" reads as broken:
    31.6% of merge requests were reviewed outside business hours and 11.1% before the author marked
    the MR ready. Naming the signal lets the panel say which, instead of showing an unexplained zero.
    It also makes "every MR was reviewed" credible -- 92.6% carry a comment or an approval and only
    7.4% rest on the merge alone.
    """
    candidates: list[tuple[datetime, str]] = [
        (when, "comment" if kind == "review" else "approval")
        for kind, when in events if kind in ("review", "approval")
    ]
    if not author_is_merger:
        candidates.append((merged_at, "merge"))
    if not candidates:
        return (None, None)
    return min(candidates, key=lambda pair: pair[0])


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
    environment: str, until: datetime | None = None,
) -> dict:
    """Per-author, per-day and team-wide opened->merged turnaround for MRs merged in the window.

    Unlike the slas report, both ends bound the SQL here: this population *is* the merge requests
    merged in the window, so an MR outside it contributes nothing and reading it would only widen
    the day axis past the range the page asked for.

    `roster` is the persisted editable roster (mr_authors.read): its "added" map names authors the
    static roster does not know, and its "hidden" list drops names from the rows *and* the totals,
    so the totals always describe what is actually on screen. `name_filter` is a list of lowercase
    substrings; empty means no filtering.
    """
    names = {**ROSTER, **{username: name for username, name in (roster.get("added") or {}).items()}}
    hidden = set(roster.get("hidden") or [])
    # This table is an opt-in comparison, not a census -- the adoption panels above answer "who is
    # using this". Nobody is listed until somebody is added, so the table starts empty rather than
    # dumping every author the crawl found. `in_view` is who has been asked for; `hidden` then takes
    # names back out of the display WITHOUT touching any total, because the x button is a view
    # control and a number that moves when you tidy a table cannot be quoted.
    in_view = {GITLAB_USERNAMES.get(username, username) for username in (roster.get("added") or {})}
    # One pass, two cuts: keep (account, merged-day, hours) per MR so the per-author and per-day
    # views are guaranteed to describe exactly the same population.
    # Changed paths are only needed where the repo name says nothing, so fetch them for that
    # subset rather than loading every path row on every request.
    unnamed = [
        mr_id for mr_id, project_path in connection.execute(
            "SELECT id, project_path FROM merge_requests "
            "WHERE merged_at >= ? AND (? IS NULL OR merged_at < ?) AND opened_at IS NOT NULL",
            [since, until, until],
        ).fetchall()
        if environment_of(project_path, []) == OTHER_ENVIRONMENT
    ]
    paths_by_mr: dict[int, list[str]] = {}
    if unnamed:
        placeholders = ", ".join(["?"] * len(unnamed))
        for mr_id, path in connection.execute(
            f"SELECT mr_id, path FROM mr_files WHERE mr_id IN ({placeholders})", unnamed
        ).fetchall():
            paths_by_mr.setdefault(mr_id, []).append(path)

    pipelines_by_mr: dict[int, list[tuple[str, datetime]]] = {}
    for mr_id, status, happened_at in connection.execute(
        "SELECT mr_id, status, happened_at FROM mr_pipelines ORDER BY mr_id, seq"
    ).fetchall():
        pipelines_by_mr.setdefault(mr_id, []).append((status, happened_at))

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
        "FROM merge_requests WHERE merged_at >= ? AND (? IS NULL OR merged_at < ?)",
        [since, until, until],
    ).fetchone()

    business_by_account: dict[str, list[float]] = {}
    team_business: list[float] = []
    by_day: dict[str, list[float]] = {}
    by_author_day: dict[str, dict[str, list[float]]] = {}
    review_waits: list[float] = []
    reviewed = 0
    by_source: dict[str, int] = {}
    mixed = 0
    slowest: list[dict] = []
    not_self_service = 0
    for (mr_id, account_id, project_path, opened_at, merged_at, merged_by, iid, title,
         web_url, description, mr_labels) in connection.execute(
        "SELECT id, author_account_id, project_path, opened_at, merged_at, merged_by, iid, title, "
        "web_url, description, labels FROM merge_requests "
        "WHERE merged_at >= ? AND (? IS NULL OR merged_at < ?) AND opened_at IS NOT NULL",
        [since, until, until],
    ).fetchall():
        name = names.get(account_id) or account_id
        # Every in-window MR reaches the accumulators; only the per-author view is narrowed, so the
        # team totals below describe the whole self-service population however the view is curated.
        in_this_view = (account_id in in_view and name not in hidden
                        and _matches(name, name_filter))
        # This page exists to score how well self-service is working, so ordinary PE work is out of
        # scope. Unscoped, the panel was 71% unrelated merge requests -- only 29% of 1,571 carried an
        # agent footer and 20% a pe:* label -- and engineers with no access to the skills at all
        # showed turnaround figures on it. Counted rather than silently dropped, so a thin panel reads
        # as "scoped" and not as "the team delivered little".
        if not is_self_service_mr(description, mr_labels):
            not_self_service += 1
            continue
        env = environment_of(project_path, paths_by_mr.get(mr_id, []))
        if env == MIXED_ENVIRONMENT:
            # An MR spanning both trees belongs to neither bucket; counted, never folded in.
            mixed += 1
            if environment != _ALL_ENVIRONMENTS:
                continue
        elif environment != _ALL_ENVIRONMENTS and env != environment:
            continue
        events = events_by_mr.get(mr_id, [])
        # Red time is not PE being slow to review: the MR cannot merge whoever looks at it, and the
        # ball is with whoever pushes the fix. Excluded from the clock, reported beside it.
        reds = red_spans(pipelines_by_mr.get(mr_id, []), opened_at, merged_at)
        red = sum(business_hours_between(start, end) for start, end in reds)
        hours = ready_hours(opened_at, merged_at, events, reds)
        # merged_by holds a GitLab username; account_id is the Jira accountId for roster members,
        # so compare on the username the ingest attributed the MR under where it has one.
        # An unknown merger counts as a self-merge: absence of evidence is not review.
        author_is_merger = (not merged_by) or merged_by == account_id
        review_at, review_source = first_review_signal(events, merged_at, author_is_merger)
        review_wait = None
        # A review arriving before the author marks the MR ready yields a genuine zero: no ready time
        # preceded it. Recorded so the panel can say so rather than show a bare 0m.
        review_before_ready = False
        if review_at is not None:
            reviewed += 1
            review_wait = ready_hours(opened_at, min(review_at, merged_at), events, reds)
            review_waits.append(review_wait)
            spans = ready_spans(opened_at, merged_at, events)
            review_before_ready = bool(spans) and review_at < spans[0][0]
            by_source[review_source] = by_source.get(review_source, 0) + 1
        # Per-MR detail for the drill-down. `open_hours` is the whole span in business hours and
        # `ready_hours` only the spells it was marked ready, so the gap between them IS the draft
        # time -- which is the usual answer to "why was this open for days". On the 20 slowest MRs
        # measured when the ready clock was introduced, 67% of attributed hours were draft.
        slowest.append({
            "iid": iid,
            "project": project_path,
            "title": title,
            "url": web_url,
            "author": name,
            "opened": opened_at.isoformat(sep=" ", timespec="minutes"),
            "merged": merged_at.isoformat(sep=" ", timespec="minutes"),
            "open_hours": round(business_hours_between(opened_at, merged_at), 1),
            "ready_hours": round(hours, 1),
            "red_hours": round(red, 1),
            "first_review_hours": (round(review_wait, 1) if review_wait is not None else None),
            "first_review_source": review_source,
            "first_review_before_ready": review_before_ready,
            "environment": env,
        })
        day = business_date(merged_at).isoformat()
        team_business.append(hours)
        if not in_this_view:
            continue
        business_by_account.setdefault(account_id, []).append(hours)
        by_day.setdefault(day, []).append(hours)
        by_author_day.setdefault(name, {}).setdefault(day, []).append(hours)

    slowest.sort(key=lambda row: row["ready_hours"], reverse=True)
    slowest_rows = slowest[:_SLOWEST_LIMIT]

    authors: list[dict] = []
    for account_id, business in business_by_account.items():
        name = names.get(account_id) or account_id
        authors.append({
            "name": name,
            # Marks a row as coming from outside PE. Derived from roster membership rather than a
            # hand-kept list of "known outsiders", which is the thing this page stopped needing.
            "tracked": not is_pe_author(account_id),
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
        # Every self-service author in the window, not just the ones in view. "Team (all authors)"
        # has to mean that, or hiding a name silently rewrites the figure beside it.
        "team": _stats(team_business),
        "window_months": _WINDOW_MONTHS,
        "window_start": since.date().isoformat(),
        "added": roster.get("added") or {},
        "hidden": sorted(hidden),
        "filter": name_filter,
        "environment": environment,
        "crawl": crawl_state(connection, roster),
        "mixed": mixed,
        # Merge requests by tracked authors carrying neither an agent footer nor a pe:* label, so
        # excluded from this page by scope. Reported for the same reason every other omission is.
        "not_self_service": not_self_service,
        "incomplete": int(incomplete or 0),
        # Slowest first, because the question this list answers is always about an outlier. Each row
        # carries both clocks: open_hours is the whole span, ready_hours only the ready spells, so
        # the gap between them is draft time and the two together say WHERE the days went.
        "slowest": slowest_rows,
        "slowest_omitted": max(0, len(slowest) - _SLOWEST_LIMIT),
        "measured": len(slowest),
        "earliest_measurable": earliest_measurable.date().isoformat() if earliest_measurable else None,
        # Time to the first human review comment (bots and the author's own notes excluded at
        # ingest), on the same ready-clock. Separates "nobody looked" from "reviewed, then iterated".
        "first_review": {
            "reviewed": reviewed,
            # Which signal was earliest, so "every MR was reviewed" can be read for what it is.
            "by_source": by_source,
            "hours_median": round(statistics.median(review_waits), 1) if review_waits else None,
            "hours_p90": pctile(review_waits, 0.9),
        },
    }


def is_pe_author(account_id: str) -> bool:
    """True when this MR author is a Platform Engineering roster member.

    ROSTER is keyed by Jira accountId and holds PE only. Everyone else the ingest attributes is by
    construction outside PE: the ingest keys anyone who is not a roster member by their GitLab
    username, which can never collide with a Jira accountId. So one membership test splits the two
    populations, on identity rather than on a username spelling that varies between `audacy-` and
    bare accounts.
    """
    return account_id in ROSTER


def adoption_report(
    connection: duckdb.DuckDBPyConnection, since: datetime, until: datetime | None,
    now: datetime, grain: str | None, roster: dict,
) -> dict:
    """Who authors self-service merge requests: the PE roster, or the teams PE built the tools for.

    The question this answers is not "how fast is PE" but "has authorship moved off PE at all", so
    the population is the same self-service MRs the turnaround panel measures, cut by author
    affiliation instead of by person.

    This is a census, not a sample. The ingest attributes every author it finds rather than a curated
    list, so an author appears here because they merged self-service work -- not because somebody
    remembered to add them. Service accounts are the one exclusion, counted and reported.

    Deliberately ignores the editable roster's "hidden" list and the author/environment filters that
    shape the table further down the page. Those curate a table; an author hidden from a table has
    not stopped adopting the tooling. The two therefore disagree on totals by design, and the panel
    says so rather than quietly reconciling them.

    Two figures the artifact version of this scorecard carries are absent here and reported as
    absent rather than approximated: the merge RATE needs merge requests that never merged, and the
    GitLab crawl fetches `state=merged` only; and "who approved" needs approver identity, which
    mr_events does not store (it records that an independent approval happened, not by whom).
    """
    grain = grain or choose_grain(since, until, now)
    names = {**ROSTER,
             **{username: name for username, name in (roster.get("added") or {}).items()}}

    # Who approved, not just that somebody did. The approver is a GitLab username, so roster
    # membership is a direct lookup -- and "authoring moved but reviewing did not" is only sayable
    # with this. Rows crawled before the actor column existed name nobody and are counted as
    # unattributed rather than guessed at.
    approved: dict[int, str | None] = {}
    for mr_id, actor in connection.execute(
        "SELECT mr_id, actor FROM mr_events WHERE kind = 'approval'"
    ).fetchall():
        approved[mr_id] = actor

    buckets: dict[date, dict[str, int]] = {}
    per_side: dict[str, list[float]] = {"pe": [], "non_pe": []}
    counts: dict[str, int] = {"pe": 0, "non_pe": 0}
    independent: dict[str, int] = {"pe": 0, "non_pe": 0}
    # Approvals ON non-PE work, split by whether a PE member gave them. The cost line: every merge
    # request a team stops asking PE to write, PE may still be reviewing.
    approvals_on_non_pe: dict[str, int] = {"pe": 0, "other": 0, "unknown": 0}
    approvers: dict[str, int] = {}
    by_author: dict[str, dict] = {}
    robots = 0
    for (mr_id, account_id, author_name, opened_at, merged_at, description,
         mr_labels) in connection.execute(
        "SELECT id, author_account_id, author_name, opened_at, merged_at, description, labels "
        "FROM merge_requests "
        "WHERE merged_at >= ? AND (? IS NULL OR merged_at < ?) AND opened_at IS NOT NULL",
        [since, until, until],
    ).fetchall():
        if not is_self_service_mr(description, mr_labels):
            continue
        if account_id in NON_HUMAN_GROUP_MEMBERS:
            # Service accounts carry agent footers by their nature, so they would otherwise dominate
            # the very population this panel measures. Counted and stated, never silently dropped --
            # and excluded at report time, so the decision stays reversible without a re-crawl.
            robots += 1
            continue
        # Keyed on the account, never the display name: two people can share a name, and merging
        # them was harmless only while the roster was a curated list of sixteen.
        name = names.get(account_id) or author_name or account_id
        side = "pe" if is_pe_author(account_id) else "non_pe"
        counts[side] += 1
        per_side[side].append(business_hours_between(opened_at, merged_at))
        if mr_id in approved:
            independent[side] += 1
            approver = approved[mr_id]
            if side == "non_pe":
                bucket = ("unknown" if approver is None
                          else "pe" if approver in GITLAB_USERNAMES else "other")
                approvals_on_non_pe[bucket] += 1
            if approver is not None:
                approvers[approver] = approvers.get(approver, 0) + 1
        author = by_author.setdefault(
            account_id, {"name": name, "mrs": 0, "pe": side == "pe"})
        author["mrs"] += 1
        bucket = buckets.setdefault(period_start(merged_at, grain), {"pe": 0, "non_pe": 0})
        bucket[side] += 1

    total = counts["pe"] + counts["non_pe"]
    _ranked_authors = sorted(by_author.values(), key=lambda a: (-a["mrs"], a["name"]))
    return {
        "grain": grain,
        "periods": [
            {
                "period": start.isoformat(),
                "pe": side["pe"],
                "non_pe": side["non_pe"],
                "total": side["pe"] + side["non_pe"],
                "share": _share(side["non_pe"], side["pe"] + side["non_pe"]),
                "partial": is_partial_period(start, grain, since, until, now),
            }
            for start, side in sorted(buckets.items())
        ],
        "pe": {"mrs": counts["pe"], "independent_approvals": independent["pe"]},
        "non_pe": {
            "mrs": counts["non_pe"],
            "authors": sum(1 for a in by_author.values() if not a["pe"]),
            "independent_approvals": independent["non_pe"],
        },
        "total": total,
        "share": _share(counts["non_pe"], total),
        # Everyone who merged self-service work, PE and not, each flagged with which side they are
        # on. Restricting this to non-PE answered "who outside PE self-serves" when the question the
        # panel asks is "who self-serves" -- and PE members using their own tooling are most of it.
        "by_author": _ranked_authors[:_AUTHOR_LIMIT],
        "authors_omitted": max(0, len(_ranked_authors) - _AUTHOR_LIMIT),
        "authors_total": len(_ranked_authors),
        "turnaround": {"pe": _stats(per_side["pe"]), "non_pe": _stats(per_side["non_pe"])},
        # Service accounts in the PE group, excluded from the population above. Stated rather than
        # dropped: these carry agent footers by their nature and would otherwise read as adoption.
        "robots": robots,
        # Who reviews the work PE no longer writes. Split three ways rather than two: an approval
        # crawled before the actor column existed names nobody, and calling that "not PE" would
        # understate the very load this figure exists to show.
        "approvals_on_non_pe": approvals_on_non_pe,
        "top_approvers": [{"actor": actor, "approvals": n} for actor, n
                          in sorted(approvers.items(), key=lambda kv: (-kv[1], kv[0]))[:10]],
        # Named, not silently missing: needs an ingest change, not a query change.
        "unmeasurable": {
            "merge_rate": "the GitLab crawl fetches state=merged only, so MRs that never merged "
                          "are absent and no rate has a denominator",
        },
    }


def _share(part: int, whole: int) -> float | None:
    """Share as a fraction, or None when there is nothing to take a share of.

    Zero of zero is not zero percent; a period with no self-service MRs at all has no share, and
    rendering it as 0% would draw a floor line through the chart that no data supports.
    """
    return round(part / whole, 4) if whole else None
