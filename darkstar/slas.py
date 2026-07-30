"""Self-service SLA + agent-success aggregation (DEVOPS-9580).

Reads only from the store. A DEVOPS issue is an AI self-service request iff it carries an agent
watermark (any pe-* Jira label, stamped by the skills). Its bucket comes from the linked GitLab
MR's pe:<skill> label (Option B, four clean buckets), matched to the issue by the DEVOPS-<n> key
in the MR title; an issue with no linked MR falls back to its Jira pe-* label. Delivery turnaround
is created->earliest Done; SLA compliance compares it to a placeholder target per (audience, bucket).
Review turnaround is opened->merged on the linked MR. Agent success rate is the share of terminal
AI requests that reached Done (v1 store-derived; CloudWatch source deferred -- see DEVOPS-9580).
"""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone

import duckdb

from darkstar.metrics import BUSINESS_TZ

_AI_PREFIX: str = "pe-"        # Jira watermark prefix (GitLab uses "pe:"); presence => agent-created
_OTHER: str = "other"
_DONE: str = "Done"
_ABANDONED: frozenset[str] = frozenset({"Won't Do", "Cancelled", "Rejected"})
_KEY_RE = re.compile(r"DEVOPS-\d+")
_HOUR_SECONDS: float = 3600.0
_BUCKETS: tuple[str, ...] = ("iac-request", "k8s-request", "tf-module", "troubleshoot")

# PRIMARY (Option B): GitLab MR label -> bucket. This is the clean per-skill signal.
_MR_BUCKET_BY_LABEL: dict[str, str] = {
    "pe:iac-request": "iac-request",
    "pe:k8s-request": "k8s-request",
    "pe:tf-module":   "tf-module",
    "pe:troubleshoot": "troubleshoot",
}
# FALLBACK: Jira label -> bucket, for a self-service issue with no linked MR. Order matters --
# pe-tf-module issues ALSO carry pe-iac-request, so the specific label wins; k8s collapses into
# iac here (Jira can't separate them). troubleshoot's Jira label unconfirmed; harmless if absent.
_JIRA_FALLBACK: tuple[tuple[str, str], ...] = (
    ("pe-tf-module", "tf-module"),
    ("pe-troubleshoot", "troubleshoot"),
    ("pe-iac-request", "iac-request"),
)

# -- PLACEHOLDER SLA TARGETS (hours) -- DEVOPS-9580, tuned with the team later. --
# Keyed [audience][bucket]; AI and human have deliberately different targets.
SLA_TARGETS_HOURS: dict[str, dict[str, float]] = {
    "ai":    {"iac-request": 48, "k8s-request": 24, "tf-module": 48, "troubleshoot": 8,  _OTHER: 48},
    "human": {"iac-request": 80, "k8s-request": 40, "tf-module": 80, "troubleshoot": 16, _OTHER: 80},
}


def _is_ai(labels: list[str]) -> bool:
    """True iff the issue carries an agent watermark (any pe-* Jira label)."""
    return any(label.startswith(_AI_PREFIX) for label in (labels or []))


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


def _pctile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in [0,1]) of a non-empty list, rounded to 1 dp."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def slas_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Volume, turnaround, SLA compliance (per audience x type) + agent success rate."""
    issues = connection.execute(
        "SELECT key, status, status_category, created, labels FROM issues"
    ).fetchall()

    transitions_by_key: dict[str, list[tuple[str, datetime]]] = {}
    for key, to_status, changed_at in connection.execute(
        "SELECT key, to_status, changed_at FROM transitions ORDER BY key, seq"
    ).fetchall():
        transitions_by_key.setdefault(key, []).append((to_status, changed_at))

    # Per-issue-key MR info: bucket (from the MR's pe:<skill> label) + review turnaround. The MR
    # title references the DEVOPS-<n> key. First MR with a recognized bucket / valid times wins.
    mr_by_key: dict[str, dict] = {}
    for title, opened_at, merged_at, mr_labels in connection.execute(
        "SELECT title, opened_at, merged_at, labels FROM merge_requests"
    ).fetchall():
        match = _KEY_RE.search(title or "")
        if not match:
            continue
        entry = mr_by_key.setdefault(match.group(0), {"bucket": None, "review_hours": None})
        if entry["bucket"] is None:
            entry["bucket"] = _mr_bucket(mr_labels or [])
        if entry["review_hours"] is None and opened_at and merged_at:
            entry["review_hours"] = max(0.0, (merged_at - opened_at).total_seconds() / _HOUR_SECONDS)

    # bucket key -> accumulator
    buckets: dict[tuple[str, str], dict] = {}
    success_terminal = 0
    success_done = 0

    for key, status, status_category, created, labels in issues:
        if not _is_ai(labels):
            continue  # no pe-* watermark → not an agent self-service request (v1 = AI only)
        mr = mr_by_key.get(key)
        rtype = (mr and mr["bucket"]) or _jira_fallback_bucket(labels)  # MR label wins; Jira fallback
        audience = "ai"  # every issue reaching here carries a pe-* watermark; human-side is a follow-up
        acc = buckets.setdefault((audience, rtype), {"volume": 0, "turnarounds": [], "reviews": [], "met": 0, "closed": 0})
        acc["volume"] += 1

        done_at = _earliest_done(transitions_by_key.get(key, []))
        if done_at is not None:
            hours = max(0.0, (done_at - created).total_seconds() / _HOUR_SECONDS)
            acc["turnarounds"].append(hours)
            acc["closed"] += 1
            target = SLA_TARGETS_HOURS.get(audience, {}).get(rtype)
            if target is not None and hours <= target:
                acc["met"] += 1
        if mr and mr["review_hours"] is not None:
            acc["reviews"].append(mr["review_hours"])

        terminal = status_category == "done" or status in _ABANDONED or done_at is not None
        if terminal:
            success_terminal += 1
            if done_at is not None and status not in _ABANDONED:
                success_done += 1

    out_buckets: list[dict] = []
    for (audience, rtype), acc in sorted(buckets.items()):
        turnarounds, reviews = acc["turnarounds"], acc["reviews"]
        out_buckets.append({
            "audience": audience,
            "type": rtype,
            "volume": acc["volume"],
            "closed": acc["closed"],
            "target_hours": SLA_TARGETS_HOURS.get(audience, {}).get(rtype),
            "turnaround_hours_median": round(statistics.median(turnarounds), 1) if turnarounds else None,
            "turnaround_hours_p90": _pctile(turnarounds, 0.9),
            "sla_met_pct": round(acc["met"] / acc["closed"] * 100, 1) if acc["closed"] else None,
            "review_hours_median": round(statistics.median(reviews), 1) if reviews else None,
            "review_hours_p90": _pctile(reviews, 0.9),
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
        "generated_at": generated_local.strftime("%Y-%m-%d %H:%M %Z"),
        "targets_are_placeholder": True,
    }
