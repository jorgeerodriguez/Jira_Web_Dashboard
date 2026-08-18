"""Self-service SLA + agent-success aggregation (DEVOPS-9580).

Reads only from the store. A DEVOPS delivery-type issue is an AI self-service request iff it carries an agent
watermark (any pe-* Jira label, stamped by the skills). Its bucket comes from the linked GitLab
MR's pe:<skill> label (Option B, four clean buckets), matched to the issue by the DEVOPS-<n> key
in the MR title; an issue with no linked MR falls back to its Jira pe-* label. Delivery turnaround
is created->earliest Done; SLA compliance scores the bucket's median against a p50 target and its p90 against a p90 target.
Review turnaround is opened->merged on the linked MR. Agent success rate is the share of terminal
AI requests that reached Done (v1 store-derived; CloudWatch source deferred -- see DEVOPS-9580).

Every turnaround here is measured in BUSINESS hours (metrics.business_hours_between: Mon-Fri
09:00-17:00 America/Denver), not calendar hours -- the team is not on call for self-service
requests overnight, so charging a request for hours nobody was working made a ticket filed at
16:00 and closed 09:00 next morning read as 17h rather than 1h. Targets are business hours too.

Population is the trailing _WINDOW_MONTHS months of requests *by creation date* -- a rolling
window that includes the current month, unlike metrics.window_months, which yields complete
months only and suits the month-bucketed charts. Unwindowed (the previous behaviour) these
medians could never recover from an old outlier.
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone

import duckdb

from darkstar.metrics import (
    BUSINESS_TZ,
    DELIVERY_TYPES,
    SELF_SERVICE_EPOCH,
    business_hours_between,
    pctile,
    window_start,
)

_AI_PREFIX: str = "pe-"        # Jira watermark prefix (GitLab uses "pe:"); presence => agent-created
# Jira labels that mark an agent-created request on their own, without a pe-* watermark. The
# pe-* labels only began 2026-05-27, so on the March-May work these carry the signal instead.
_AI_JIRA_LABELS: frozenset[str] = frozenset({"ai-generated", "self-service"})
# The MR-description footer the skills stamp. Present on 521 of 2477 crawled MRs versus 288 for
# the pe:* label, and it predates the labels, so it is the widest AI signal available. Matches the
# emoji and :robot: variants alike, plus the trailing Anthropic co-author trailer.
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
# (p50 by month created: Apr 369.7h, May 102.0h, Jun 7.5h, Jul 12.9h, Aug 2.5h), so a six-month
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
# Two numbers, not one, because delivery turnaround is bimodal: on August data 45% of requests
# closed inside 2h while the p90 sat at 25h. A single "% under T" score cannot separate "slightly
# late" from "a week late" — it just reports one blended percentage that is wrong about both ends.
# `p50` is what the common case should hit; `p90` is the tail backstop.
#
# Calibrated against August actuals (p50 / p90): iac-request 2.7 / 24.9, troubleshoot 0.6 / 12.8,
# tf-module 1.3 / 1.8. One business day = 8h, one business week = 40h.
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


def _is_ai_issue(labels: list[str]) -> bool:
    """True iff the Jira issue itself is watermarked: any pe-* label, or ai-generated/self-service."""
    return any(
        label.startswith(_AI_PREFIX) or label in _AI_JIRA_LABELS
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


def slas_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Volume, turnaround, SLA compliance (per audience x type) + agent success rate."""
    since = window_start(now, _WINDOW_MONTHS, SELF_SERVICE_EPOCH)
    # Scope to delivery types, as leadtime/velocity/intake do: a Feature or Epic is a container for
    # requests, not a request, and its months-long lifetime badly inflates a turnaround median.
    delivery = ", ".join(["?"] * len(DELIVERY_TYPES))
    issues = connection.execute(
        f"SELECT key, status, status_category, created, labels FROM issues "
        f"WHERE created >= ? AND issuetype IN ({delivery})",
        [since, *DELIVERY_TYPES],
    ).fetchall()

    transitions_by_key: dict[str, list[tuple[str, datetime]]] = {}
    for key, to_status, changed_at in connection.execute(
        "SELECT key, to_status, changed_at FROM transitions ORDER BY key, seq"
    ).fetchall():
        transitions_by_key.setdefault(key, []).append((to_status, changed_at))

    # Per-issue-key MR info: bucket (from the MR's pe:<skill> label) + review turnaround. The MR
    # title references the DEVOPS-<n> key. First MR with a recognized bucket / valid times wins.
    mr_by_key: dict[str, dict] = {}
    for title, opened_at, merged_at, mr_labels, description in connection.execute(
        "SELECT title, opened_at, merged_at, labels, description FROM merge_requests "
        "WHERE merged_at >= ? ORDER BY id",
        [since],
    ).fetchall():
        match = _KEY_RE.search(title or "")
        if not match:
            continue
        entry = mr_by_key.setdefault(match.group(0), {"bucket": None, "review_hours": None, "agent": False})
        # The MR's pe:* label is the cleanest per-skill signal; its footer's "via /<skill>" is the
        # widest. Take whichever the first MR offers, label first.
        if entry["bucket"] is None:
            entry["bucket"] = _mr_bucket(mr_labels or []) or _footer_bucket(description)
        entry["agent"] = entry["agent"] or has_agent_footer(description) or bool(_mr_bucket(mr_labels or []))
        if entry["review_hours"] is None and opened_at and merged_at:
            entry["review_hours"] = business_hours_between(opened_at, merged_at)

    # bucket key -> accumulator
    buckets: dict[tuple[str, str], dict] = {}
    success_terminal = 0
    success_done = 0

    for key, status, status_category, created, labels in issues:
        mr = mr_by_key.get(key)
        # Agent-created if ANY signal fires: the Jira watermark, the linked MR's pe:* label, or the
        # linked MR's "Generated with Claude Code" footer. Any one alone misses a slice of the work.
        if not (_is_ai_issue(labels) or (mr is not None and mr["agent"])):
            continue
        rtype = (mr and mr["bucket"]) or _jira_fallback_bucket(labels)  # MR signal wins; Jira fallback
        audience = "ai"  # every issue reaching here carries a pe-* watermark; human-side is a follow-up
        acc = buckets.setdefault((audience, rtype), {"volume": 0, "turnarounds": [], "reviews": [], "closed": 0})
        acc["volume"] += 1

        done_at = _earliest_done(transitions_by_key.get(key, []))
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

    agent_success = {
        "terminal": success_terminal,
        "succeeded": success_done,
        "rate_pct": round(success_done / success_terminal * 100, 1) if success_terminal else None,
    }

    generated_local = now.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    return {
        "buckets": out_buckets,
        "agent_success": agent_success,
        "types": list(_BUCKETS) + [_OTHER],
        "targets": SLA_TARGETS_HOURS,
        "window_months": _WINDOW_MONTHS,
        "window_start": since.date().isoformat(),
        "generated_at": generated_local.strftime("%Y-%m-%d %H:%M %Z"),
        "targets_are_placeholder": False,
    }
