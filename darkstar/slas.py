"""Self-service SLA + agent-success aggregation (DEVOPS-9580).

Reads only from the store. Two independent properties, never merged:

  SELF-SERVICE  a *label* is present -- pe-*/ai-generated/self-service on the Jira issue, or
                pe:<skill> on the linked MR. The skills stamp these, so a label means the request
                came through a self-service skill. This is the population the SLA buckets measure.
  AI-GENERATED  the *footer* is present -- "Generated with Claude Code" in the MR description. The
                code was agent-written, whatever the origin of the request.

They overlap but are not the same, and treating the footer as self-service overstated it badly:
96 issues created since the labels existed are footer-only, and they are ordinary human-filed
tickets that PE delivered with the agent's help.

A request's bucket comes from the linked MR's pe:<skill> label, then the footer's "via /<skill>"
fragment (which names the skill reliably even though it does not qualify the issue), then the Jira
label; the MR is matched to the issue by the DEVOPS-<n> key in its title. Delivery turnaround
is created->earliest Done; SLA compliance scores the bucket's median against a p50 target and its p90 against a p90 target.
Review turnaround is opened->merged on the linked MR. Agent success rate is the share of terminal
AI requests that reached Done (v1 store-derived; CloudWatch source deferred -- see DEVOPS-9580).

The headline block answers the three questions a human actually asks of a self-service programme:
is it used (requests this month vs last), does it carry real load (share of ALL delivered PE work
in the window), and is it faster (self-service median vs the non-self-service delivery median, both
on the same business clock). Computing the comparison is why the main loop measures every delivered
delivery-type issue, not only the agent-created ones.

Every turnaround here is measured in BUSINESS hours (metrics.business_hours_between: Mon-Fri
08:00-17:00 US/Pacific), not calendar hours -- the team is not on call for self-service
requests overnight, so charging a request for hours nobody was working made a ticket filed at
15:00 and closed 08:00 next morning read as 17h rather than 2h. Targets are business hours too.

Population is the trailing _WINDOW_MONTHS months of requests *by creation date* -- a rolling
window that includes the current month, unlike metrics.window_months, which yields complete
months only and suits the month-bucketed charts. Unwindowed (the previous behaviour) these
medians could never recover from an old outlier.
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timedelta, timezone

import duckdb

from darkstar.mrflow import first_review_at
from darkstar.metrics import (
    BUSINESS_HOURS_PER_DAY,
    BUSINESS_TZ,
    DELIVERY_TYPES,
    SELF_SERVICE_EPOCH,
    business_hours_between,
    business_date,
    business_month,
    choose_grain,
    pctile,
    period_end,
    period_start,
    ready_hours,
    window_start,
)

# TWO DIFFERENT THINGS, deliberately not merged:
#
#   SELF-SERVICE -- a *label* is present. The skills stamp pe-* on the Jira issue and pe:<skill> on
#                   the MR, so a label means the request itself came through a self-service skill.
#   AI-GENERATED -- the *footer* is present. "Generated with Claude Code" in the MR description
#                   means the code was agent-written, whatever the origin of the request.
#
# Conflating them overstated self-service badly: 96 issues created since the labels existed are
# footer-only, and they are ordinary human-filed tickets that PE happened to deliver with the agent
# (reported by Adam, Omar, Samia...). Those are AI-generated, not self-service.
_SELF_SERVICE_PREFIX: str = "pe-"   # Jira watermark; GitLab's equivalent is the pe:<skill> label
# Jira labels the skills stamp that do not carry the pe- prefix.
_SELF_SERVICE_JIRA_LABELS: frozenset[str] = frozenset({"ai-generated", "self-service"})
# The MR-description footer. Present on 521 of 2477 crawled MRs against 288 for the pe:* label, and
# it predates the labels by two months. Matches the emoji and :robot: variants alike, plus the
# trailing Anthropic co-author trailer.
_AGENT_FOOTER_RE = re.compile(
    r"generated\s+with\s+\[?claude\s+code|authored-by:\s*claude|claude\.com/claude-code",
    re.IGNORECASE,
)
# The same footer usually names the originating skill: "Generated with Claude Code via
# /iac-request" or "... via /audacy-platform-engineering:iac-request".
_FOOTER_SKILL_RE = re.compile(
    r"generated\s+with\s+claude\s+code\s+via\s+/(?:audacy-platform-engineering:)?([a-z0-9][a-z0-9-]*)",
    re.IGNORECASE,
)
_OTHER: str = "other"
_DONE: str = "Done"
# DEVOPS' abandon status is spelled "Will Not Do" (24 of the 272 self-service issues). The old set
# only held "Won't Do", which matches nothing here, so agent success rate was pinned at 100%.
# Both spellings are kept: workflows differ per project and a stale name costs nothing.
_ABANDONED: frozenset[str] = frozenset(
    {"Will Not Do", "Won't Do", "Wont Do", "Cancelled", "Canceled", "Rejected"}
)
_KEY_RE = re.compile(r"DEVOPS-\d+")
# Three months, not six: delivery turnaround has improved roughly 100x since the workflows began
# (p50 by month created: Apr 406.7h, May 114.0h, Jun 18.0h, Jul 13.9h, Aug 3.0h), so a six-month
# window calibrates the targets against a team that no longer exists. Three keeps ~220 delivered
# requests — enough for a stable p90 — while dropping the worst of the learning curve. mrflow keeps
# its own six-month window on purpose; MR turnaround is tracked back to the epoch.
_WINDOW_MONTHS: int = 3
_BUCKETS: tuple[str, ...] = ("iac-request", "k8s-request", "tf-module", "troubleshoot")

# PRIMARY (Option B): GitLab MR label -> bucket. This is the clean per-skill signal.
_MR_BUCKET_BY_LABEL: dict[str, str] = {
    "pe:iac-request": "iac-request",
    "pe:k8s-request": "k8s-request",
    "pe:tf-module":   "tf-module",
    "pe:troubleshoot": "troubleshoot",
    # 15 crawled MRs carry the label as the literal JSON array text — a quoting bug in whatever
    # sets it. Accepted here so those MRs bucket correctly; the producer still wants fixing.
    '["pe:iac-request"]': "iac-request",
}

# Skill name as written in the footer -> bucket. The skills are named "<x>-request" but the label
# and the bucket drop the suffix for tf-module, so normalise here rather than at every call site.
_FOOTER_SKILL_TO_BUCKET: dict[str, str] = {
    "iac-request":       "iac-request",
    "k8s-request":       "k8s-request",
    "tf-module":         "tf-module",
    "tf-module-request": "tf-module",
    "troubleshoot":      "troubleshoot",
}
# FALLBACK: Jira label -> bucket, for a self-service issue with no linked MR. Order matters --
# pe-tf-module issues ALSO carry pe-iac-request, so the specific label wins; k8s collapses into
# iac here (Jira can't separate them). troubleshoot's Jira label unconfirmed; harmless if absent.
_JIRA_FALLBACK: tuple[tuple[str, str], ...] = (
    ("pe-tf-module", "tf-module"),
    ("pe-troubleshoot", "troubleshoot"),
    ("pe-iac-request", "iac-request"),
)

# -- SLA TARGETS (BUSINESS hours) -- two tiers per bucket, keyed [audience][bucket]. --
#
# Two numbers, not one, because delivery turnaround is bimodal: on August data 39% of requests
# closed inside 2h while the p90 sat at 29.8h. A single "% under T" score cannot separate "slightly
# late" from "a week late" — it just reports one blended percentage that is wrong about both ends.
# `p50` is what the common case should hit; `p90` is the tail backstop.
#
# Calibrated against August actuals -- see the README for the current figures.
# One business day = 9h (08:00-17:00), one business week = 45h.
SLA_TARGETS_HOURS: dict[str, dict[str, dict[str, float]]] = {
    "ai": {
        "iac-request":  {"p50": 4,  "p90": 24},
        "k8s-request":  {"p50": 4,  "p90": 16},
        "tf-module":    {"p50": 2,  "p90": 8},
        "troubleshoot": {"p50": 2,  "p90": 16},
        _OTHER:         {"p50": 4,  "p90": 24},
    },
    "human": {
        "iac-request":  {"p50": 8,  "p90": 40},
        "k8s-request":  {"p50": 8,  "p90": 32},
        "tf-module":    {"p50": 4,  "p90": 16},
        "troubleshoot": {"p50": 4,  "p90": 32},
        _OTHER:         {"p50": 8,  "p90": 40},
    },
}


def _is_self_service_issue(labels: list[str]) -> bool:
    """True iff the Jira issue carries a skill-stamped label: pe-*, ai-generated or self-service."""
    return any(
        label.startswith(_SELF_SERVICE_PREFIX) or label in _SELF_SERVICE_JIRA_LABELS
        for label in (labels or [])
    )


def has_agent_footer(description: str) -> bool:
    """True iff an MR description carries the skills' "Generated with Claude Code" footer."""
    return bool(_AGENT_FOOTER_RE.search(description or ""))


def _footer_bucket(description: str) -> str | None:
    """Bucket named by the MR footer's "via /<skill>" fragment, or None if it names no known skill."""
    match = _FOOTER_SKILL_RE.search(description or "")
    return _FOOTER_SKILL_TO_BUCKET.get(match.group(1).lower()) if match else None


def _mr_bucket(mr_labels: list[str]) -> str | None:
    """The bucket for an MR from its pe:<skill> label, or None if it has none."""
    for label in mr_labels or []:
        if label in _MR_BUCKET_BY_LABEL:
            return _MR_BUCKET_BY_LABEL[label]
    return None


def _jira_fallback_bucket(labels: list[str]) -> str:
    """Bucket for a pe-* issue with no linked MR -- most specific Jira label wins, else _OTHER."""
    for label, bucket in _JIRA_FALLBACK:
        if label in (labels or []):
            return bucket
    return _OTHER


def _earliest_done(transitions: list[tuple[str, datetime]]) -> datetime | None:
    for to_status, changed_at in transitions:
        if to_status == _DONE:
            return changed_at
    return None


def default_window_start(now: datetime) -> datetime:
    """The window this view uses unless the page overrides it: three months, floored at the epoch."""
    return window_start(now, _WINDOW_MONTHS, SELF_SERVICE_EPOCH)


def _is_partial(start: date, grain: str, since: datetime, until: datetime | None,
                now: datetime) -> bool:
    """True when the window or the clock cuts this period short, so its counts are not a full one.

    Three ways a period is truncated, and all three read as a real dip if unflagged: the first period
    of a lookback that began mid-period ("last 30 days" almost never starts on a Monday, and a
    monthly window almost never starts on the 1st), the last period of a bounded window, and the
    period in progress right now.

    Comparison is on business-tz calendar dates, because that is what a period label means.
    """
    ceiling = min(until, now) if until is not None else now
    return start < business_date(since) or period_end(start, grain) > business_date(ceiling)


def slas_report(connection: duckdb.DuckDBPyConnection, now: datetime, since: datetime,
                until: datetime | None = None, grain: str | None = None) -> dict:
    """Volume, turnaround, SLA compliance (per audience x type) + agent success rate.

    `since` is the population floor (requests created on or after it), passed in rather than
    derived so the dashboard's lookback control drives every panel from one value.
    """
    # Scope to delivery types, as leadtime/velocity/intake do: a Feature or Epic is a container for
    # requests, not a request, and its months-long lifetime badly inflates a turnaround median.
    # Row grain follows the window unless the page forces one. Medians cannot be re-aggregated from
    # coarser medians, so this has to happen here rather than by folding rows in the browser.
    grain = grain or choose_grain(since, until, now)
    delivery = ", ".join(["?"] * len(DELIVERY_TYPES))
    issues = connection.execute(
        f"SELECT key, status, status_category, created, labels FROM issues "
        f"WHERE created >= ? AND (? IS NULL OR created < ?) AND issuetype IN ({delivery})",
        [since, until, until, *DELIVERY_TYPES],
    ).fetchall()

    transitions_by_key: dict[str, list[tuple[str, datetime]]] = {}
    for key, to_status, changed_at in connection.execute(
        "SELECT key, to_status, changed_at FROM transitions ORDER BY key, seq"
    ).fetchall():
        transitions_by_key.setdefault(key, []).append((to_status, changed_at))

    # Per-issue-key MR info: bucket (from the MR's pe:<skill> label) + review turnaround. The MR
    # title references the DEVOPS-<n> key. First MR with a recognized bucket / valid times wins.
    events_by_mr: dict[int, list[tuple[str, datetime]]] = {}
    for mr_id, kind, happened_at in connection.execute(
        "SELECT mr_id, kind, happened_at FROM mr_events ORDER BY mr_id, seq"
    ).fetchall():
        events_by_mr.setdefault(mr_id, []).append((kind, happened_at))

    mr_by_key: dict[str, dict] = {}
    for mr_id, title, opened_at, merged_at, mr_labels, description, author_account_id, merged_by in connection.execute(
        "SELECT id, title, opened_at, merged_at, labels, description, author_account_id, merged_by "
        "FROM merge_requests WHERE merged_at >= ? ORDER BY id",
        [since],
    ).fetchall():
        match = _KEY_RE.search(title or "")
        if not match:
            continue
        entry = mr_by_key.setdefault(
            match.group(0),
            {"bucket": None, "review_hours": None, "skill_label": False, "footer": False,
             "first_review_hours": None})
        # Bucketing may still read the footer's "via /<skill>" — it names the skill reliably. That is
        # a separate question from whether the footer *qualifies* the issue as self-service.
        if entry["bucket"] is None:
            entry["bucket"] = _mr_bucket(mr_labels or []) or _footer_bucket(description)
        entry["skill_label"] = entry["skill_label"] or bool(_mr_bucket(mr_labels or []))
        entry["footer"] = entry["footer"] or has_agent_footer(description)
        if entry["review_hours"] is None and opened_at and merged_at:
            events = events_by_mr.get(mr_id, [])
            entry["review_hours"] = ready_hours(opened_at, merged_at, events)
            author_is_merger = (not merged_by) or merged_by == author_account_id
            review_at = first_review_at(events, merged_at, author_is_merger)
            if review_at is not None:
                entry["first_review_hours"] = ready_hours(opened_at, min(review_at, merged_at), events)

    # bucket key -> accumulator
    buckets: dict[tuple[str, str], dict] = {}
    success_terminal = 0
    success_done = 0
    # Headline counters. The comparison group is the whole non-self-service delivery population in
    # the same window, measured on the same clock -- that is the only honest baseline for "is
    # self-service faster", and it is why this loop no longer skips non-AI issues outright.
    self_service_hours: list[float] = []
    other_hours: list[float] = []
    delivered_self_service = 0
    delivered_ai_generated = 0
    delivered_total = 0
    first_review_waits: list[float] = []
    self_service_reviewed = 0
    turnaround_by_period: dict[date, list[float]] = {}
    created_by_period: dict[date, int] = {}
    origin_by_period: dict[date, dict[str, int]] = {}
    created_by_month: dict[tuple[int, int], int] = {}

    for key, status, status_category, created, labels in issues:
        mr = mr_by_key.get(key)
        # Agent-created if ANY signal fires: the Jira watermark, the linked MR's pe:* label, or the
        # linked MR's "Generated with Claude Code" footer. Any one alone misses a slice of the work.
        # Self-service = a label somewhere. AI-generated = a footer on the MR. Independent.
        is_self_service = _is_self_service_issue(labels) or (mr is not None and mr["skill_label"])
        is_ai_generated = mr is not None and mr["footer"]
        done_at = _earliest_done(transitions_by_key.get(key, []))

        if done_at is not None:
            delivered_total += 1
            hours = business_hours_between(created, done_at)
            (self_service_hours if is_self_service else other_hours).append(hours)
            if is_self_service:
                delivered_self_service += 1
                turnaround_by_period.setdefault(period_start(created, grain), []).append(hours)
            if is_ai_generated:
                delivered_ai_generated += 1
        if is_self_service and mr is not None and mr["first_review_hours"] is not None:
            self_service_reviewed += 1
            first_review_waits.append(mr["first_review_hours"])
        bucket = period_start(created, grain)
        origin = origin_by_period.setdefault(bucket, {"agent": 0, "human": 0})
        origin["agent" if is_self_service else "human"] += 1
        if is_self_service:
            month = business_month(created)
            created_by_month[month] = created_by_month.get(month, 0) + 1
            created_by_period[bucket] = created_by_period.get(bucket, 0) + 1

        if not is_self_service:
            continue
        rtype = (mr and mr["bucket"]) or _jira_fallback_bucket(labels)  # MR signal wins; Jira fallback
        audience = "ai"  # every issue reaching here carries a pe-* watermark; human-side is a follow-up
        acc = buckets.setdefault((audience, rtype), {"volume": 0, "turnarounds": [], "reviews": [], "closed": 0})
        acc["volume"] += 1

        if done_at is not None:
            acc["turnarounds"].append(business_hours_between(created, done_at))
            acc["closed"] += 1
        if mr and mr["review_hours"] is not None:
            acc["reviews"].append(mr["review_hours"])

        # Terminal/success derive from ONE signal — the issue's *current* status category — so a
        # reopened issue (has a past Done transition but is active again) is not counted, and the
        # two counters can't disagree. status_category "done" covers Done + Won't Do/Cancelled;
        # a request succeeded iff it's in that terminal category and not an abandon status.
        if status_category == "done":
            success_terminal += 1
            if status not in _ABANDONED:
                success_done += 1

    out_buckets: list[dict] = []
    for (audience, rtype), acc in sorted(buckets.items()):
        turnarounds, reviews = acc["turnarounds"], acc["reviews"]
        target = SLA_TARGETS_HOURS.get(audience, {}).get(rtype) or {}
        median = round(statistics.median(turnarounds), 1) if turnarounds else None
        p90 = pctile(turnarounds, 0.9)
        target_p50, target_p90 = target.get("p50"), target.get("p90")
        # Each tier is scored on the distribution, not per request: the median must clear the p50
        # target and the p90 must clear the p90 target. within_target_pct is kept alongside as the
        # "how often does the fast path actually happen" read, scored against the p50 target only.
        within = [hours for hours in turnarounds if target_p50 is not None and hours <= target_p50]
        out_buckets.append({
            "audience": audience,
            "type": rtype,
            "volume": acc["volume"],
            "closed": acc["closed"],
            "target_p50_hours": target_p50,
            "target_p90_hours": target_p90,
            "turnaround_hours_median": median,
            "turnaround_hours_p90": p90,
            "meets_p50": None if (median is None or target_p50 is None) else median <= target_p50,
            "meets_p90": None if (p90 is None or target_p90 is None) else p90 <= target_p90,
            "within_target_pct": round(len(within) / acc["closed"] * 100, 1) if acc["closed"] and target_p50 is not None else None,
            "review_hours_median": round(statistics.median(reviews), 1) if reviews else None,
            "review_hours_p90": pctile(reviews, 0.9),
        })

    def _prev(month: tuple[int, int]) -> tuple[int, int]:
        year, mon = month
        return (year - 1, 12) if mon == 1 else (year, mon - 1)

    def _month_start(month: tuple[int, int]) -> datetime:
        """Naive-UTC first instant of a business-tz month."""
        return (datetime(month[0], month[1], 1, tzinfo=BUSINESS_TZ)
                .astimezone(timezone.utc).replace(tzinfo=None))

    this_month = business_month(now)
    previous_month = _prev(this_month)
    # Only compare against last month if the window actually covers the whole of it. Counting from
    # a window that opens mid-comparison reports "up from 0" — a fact about the lookback, not the
    # team, and one that renders as spectacular growth.
    previous_comparable = since <= _month_start(previous_month)
    headline = {
        # Share of ALL delivered PE work in the window that came through self-service. Self-
        # normalising: filing more requests cannot flatter it, because the denominator grows too.
        "share_pct": round(delivered_self_service / delivered_total * 100, 1) if delivered_total else None,
        "share_delivered": delivered_self_service,
        "share_total": delivered_total,
        # The other dimension: delivered work whose MR carries a Claude footer, whoever filed the
        # ticket. Strictly broader than self-service and answers a different question.
        "ai_share_pct": round(delivered_ai_generated / delivered_total * 100, 1) if delivered_total else None,
        "ai_delivered": delivered_ai_generated,
        # Adoption. A share without its volume is unreadable -- 53% of 5 is not 53% of 172.
        "requests_this_month": created_by_month.get(this_month, 0),
        "requests_prev_month": created_by_month.get(previous_month, 0) if previous_comparable else None,
        # The value proposition, both sides measured on the same business clock.
        "self_service_median_hours": round(statistics.median(self_service_hours), 1) if self_service_hours else None,
        "other_median_hours": round(statistics.median(other_hours), 1) if other_hours else None,
        "self_service_sample": len(self_service_hours),
        "other_sample": len(other_hours),
        # Time to the first human review comment on a self-service request's MR, on the ready-clock
        # (bots and the author's own notes excluded at ingest). Separates "nobody looked" from
        # "reviewed, then iterated" — the split the raw turnaround cannot express.
        "first_review_reviewed": self_service_reviewed,
        "first_review_median_hours": round(statistics.median(first_review_waits), 1) if first_review_waits else None,
        "first_review_p90_hours": pctile(first_review_waits, 0.9),
    }

    # Turnaround by the month the request was CREATED. A single blended figure over the window
    # libels current performance when the team is improving fast: on the pilot data the p50 ran
    # 114.0h (May), 6.3h (Jun), 7.5h (Jul), 2.7h (Aug). The share within a working day is carried
    # alongside because the p50 range is wide enough that a linear axis buries the recent months.
    # Grouped by period rather than always by week: a 90-day lookback is 14 weekly rows, which is a
    # list to scroll instead of a trend to read, and a 7-day lookback is one row that says nothing.
    # The grain comes from the window, so the table stays roughly four to thirteen rows throughout.
    #
    # No rest-of-PE column. Measured head to head on live Jira for June, with abandoned statuses
    # excluded from both arms, self-service ran 15.9h p50 against 37.9h and 174.8h p90 against
    # 144.0h -- better at the median, worse in the tail, Mann-Whitney z=+1.44, not significant at
    # n=26. A ratio printed per period would read as a finding when the data does not carry one. The
    # origin series below is what the self-service work actually demonstrates.
    #
    # `created` sits beside `delivered` because these rows group by the period a request ARRIVED. A
    # period whose cohort has not closed yet shows only the requests that finished quickly, which
    # reads as a fast period; delivered < created is how a reader sees that.
    turnaround = []
    for start in sorted(set(turnaround_by_period) | set(created_by_period)):
        hours = turnaround_by_period.get(start, [])
        turnaround.append({
            "period": start.isoformat(),
            "created": created_by_period.get(start, 0),
            "delivered": len(hours),
            "p50_hours": round(statistics.median(hours), 1) if hours else None,
            "p90_hours": pctile(hours, 0.9),
            "within_day_pct": (round(sum(1 for h in hours if h <= BUSINESS_HOURS_PER_DAY) / len(hours) * 100)
                               if hours else None),
            "partial": _is_partial(start, grain, since, until, now),
        })

    # Origin: what share of the requests PE takes on arrive through a skill rather than a person
    # filing them. Counted in TICKETS, per period of creation, so it lines up with the turnaround
    # panel beside it. Merge requests are deliberately not the unit -- one request routinely spawns
    # several (July: 643 MRs against 117 distinct tickets), so an MR count answers a question about
    # branches rather than about demand.
    #
    # Classification is how the ticket was CREATED: the Jira watermark a skill stamps, or the pe:*
    # label on a merge request it opened when the watermark is missing. Reading an MR for that signal
    # is not the same as counting it.
    origin = []
    for start in sorted(origin_by_period):
        slot = origin_by_period[start]
        total = slot["agent"] + slot["human"]
        origin.append({
            "period": start.isoformat(),
            "agent": slot["agent"],
            "human": slot["human"],
            "total": total,
            "agent_share_pct": round(slot["agent"] / total * 100),
            "partial": _is_partial(start, grain, since, until, now),
        })

    agent_success = {
        "terminal": success_terminal,
        "succeeded": success_done,
        "rate_pct": round(success_done / success_terminal * 100, 1) if success_terminal else None,
    }

    generated_local = now.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    return {
        "buckets": out_buckets,
        "headline": headline,
        "grain": grain,
        "turnaround": turnaround,
        "origin": origin,
        "agent_success": agent_success,
        "types": list(_BUCKETS) + [_OTHER],
        "targets": SLA_TARGETS_HOURS,
        "window_months": _WINDOW_MONTHS,
        "window_start": since.date().isoformat(),
        "generated_at": generated_local.strftime("%Y-%m-%d %H:%M %Z"),
        "targets_are_placeholder": False,
    }
