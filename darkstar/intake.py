"""Intake dashboard aggregation — live data only.

Editorial config (domain keyword patterns, SME overrides, engineer colors, thresholds) lives
in the dashboard, where the lead curates it. This provides:
  - queue:  issues in Triage / Reviewing (excluding epics/features), oldest first
  - roster: per engineer, predicted velocity (reused from the velocity view), current active WIP,
            and completions so far this business-tz month (for net-of-completions spare)
  - corpus: historical ticket summaries per engineer, weighted (done 1.0 / active 0.5), which the
            dashboard tags into the SME-by-domain matrix using its own keyword patterns

Roster keys are lowercased first names, matching the dashboard's editorial config keys.
"""
from __future__ import annotations

from datetime import datetime

import duckdb

from darkstar import gitlab_domains, velocity
from darkstar.metrics import DELIVERY_TYPES
from darkstar.roster import ROSTER

# WIP = active delivery work (Reviewing is intake, not WIP; Staged CAR is real in-progress work).
_WIP_STATUSES: tuple[str, ...] = ("In Progress", "Blocked", "On Hold", "Validating", "Staged CAR")
_QUEUE_STATUSES: tuple[str, ...] = ("Triage", "Reviewing")
_NON_QUEUE_TYPES: tuple[str, ...] = ("Feature", "Initiative", "Epic")
_DONE_WEIGHT: float = 1.0
_ACTIVE_WEIGHT: float = 0.5

# -- WIP size weighting ---------------------------------------------------------------------------
# What an Estimated Size is worth as load, relative to one average ticket.
#
# These ratios are a TEAM CONVENTION, not a measurement, and the page says so. DEVOPS-10567 tried to
# derive them from observed cycle time and returned a negative result: the sizes do order
# monotonically (2.0 / 5.5 / 14.0 / 31.0 median days) but only 1 of 7 Large/XL issues was sized
# BEFORE work started. Six were sized mid-flight or later and every revision was upward, so "Large
# took 3x as long" is substantially tautological -- they were called Large because they were running
# long. A multiplier derived from that restates its own input.
#
# A convention is enough here. This weights WIP to compare engineers against each other at one
# instant, not against a historical baseline, so the scale has to be MONOTONE, not calibrated. Any
# sane increasing weights rank the roster the same way.
_SIZE_RATIOS: dict[str, float] = {"Small": 0.7, "Medium": 1.6, "Large": 2.8, "XL": 4.5}

# The sized-completion mix the ratios are normalized against: DEVOPS completions in the 180 days to
# 2026-09-18. Held as counts rather than a precomputed factor so the normalization is auditable and
# re-derivable, and pinned rather than recomputed per poll -- a live mix would make every engineer's
# capacity drift for reasons that have nothing to do with their own workload.
_REFERENCE_MIX: dict[str, int] = {"Small": 250, "Medium": 88, "Large": 11, "XL": 4}

# Scale the ratios so the reference mix averages exactly 1.0. That is what keeps weighted WIP in the
# same units as the count-based velocity `spare` subtracts it from, and what makes an unsized ticket
# weigh exactly 1.0 -- i.e. today's behaviour. The change is therefore safe at partial coverage with
# no threshold, no fallback branch and no caveat on the page: at 0% sized it reproduces the old
# numbers exactly, and it improves continuously as sizing fills in.
_NORMALISATION: float = (
    sum(_REFERENCE_MIX.values())
    / sum(_SIZE_RATIOS[size] * count for size, count in _REFERENCE_MIX.items())
)
SIZE_WEIGHTS: dict[str, float] = {
    size: ratio * _NORMALISATION for size, ratio in _SIZE_RATIOS.items()
}

# An unsized ticket weighs one average ticket. NULL here means "Jira holds no size", which is true
# of ~41% of current WIP, so this is the common path and not an error case.
UNSIZED_WEIGHT: float = 1.0


def weight_of(estimated_size: str | None) -> float:
    """Load weight for one ticket. An unrecognised size falls back to the unsized weight.

    Unrecognised rather than raising because the option set is edited in Jira, not here: a new size
    added there must not take the capacity panel down, it must degrade to today's behaviour.
    """
    if estimated_size is None:
        return UNSIZED_WEIGHT
    return SIZE_WEIGHTS.get(estimated_size, UNSIZED_WEIGHT)

# accountId -> short key (lowercased first name), matching the dashboard's editorial config keys.
_KEY_BY_ACCOUNT: dict[str, str] = {account_id: name.lower() for account_id, name in ROSTER.items()}


def _velocity_by_key(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict[str, int]:
    """Typical monthly velocity per roster key — the velocity view's unblended baseline.

    Uses `forecast.baseline` (the recency-weighted typical month), not the headline `value`, which
    the velocity dashboard blends toward current-month pace. Capacity subtracts `done_this_month`
    separately, so a current-month-aware velocity here would double-count it.
    """
    report = velocity.velocity_report(connection, now)
    return {member["name"].lower(): member["forecast"]["baseline"] for member in report["members"]}


def _mr_domains_by_key(connection: duckdb.DuckDBPyConnection) -> dict[str, dict[str, int]]:
    """Per-roster-key domain counts from merged-MR authorship (each domain counted once per MR).

    Tagged from repo path + changed file paths (see gitlab_domains); merged additively into the
    corpus-derived SME matrix client-side.
    """
    files: dict[int, list[str]] = {}
    for mr_id, path in connection.execute("SELECT mr_id, path FROM mr_files").fetchall():
        files.setdefault(mr_id, []).append(path)
    counts: dict[str, dict[str, int]] = {}
    for mr_id, project_path, account_id in connection.execute(
        "SELECT id, project_path, author_account_id FROM merge_requests"
    ).fetchall():
        key = _KEY_BY_ACCOUNT.get(account_id)
        if key is None:
            continue
        for domain in gitlab_domains.domains_for(project_path, files.get(mr_id, [])):
            counts.setdefault(key, {})[domain] = counts.setdefault(key, {}).get(domain, 0) + 1
    return counts


def intake_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Triage queue, per-engineer capacity, and the SME corpus — all read from the store."""
    delivery = ", ".join(["?"] * len(DELIVERY_TYPES))
    wip_statuses = ", ".join(["?"] * len(_WIP_STATUSES))

    vel_by_key = _velocity_by_key(connection, now)
    done_by_key = {name.lower(): count for name, count in velocity.completions_this_month(connection, now).items()}

    # WIP is summed by size weight, not counted: an engineer holding an XL is not as free as one
    # holding a Small, and a plain count said they were. Grouped by size so the weighting happens
    # here rather than in SQL, keeping the weight table the single place sizes turn into numbers.
    wip_by_key: dict[str, float] = {}
    for account_id, estimated_size, count in connection.execute(
        f"SELECT assignee_account_id, estimated_size, count(*) FROM issues "
        f"WHERE issuetype IN ({delivery}) AND status IN ({wip_statuses}) "
        f"GROUP BY assignee_account_id, estimated_size",
        list(DELIVERY_TYPES) + list(_WIP_STATUSES),
    ).fetchall():
        key = _KEY_BY_ACCOUNT.get(account_id)
        if key:
            wip_by_key[key] = wip_by_key.get(key, 0.0) + weight_of(estimated_size) * count

    roster = {
        # wip is rounded to one decimal: the gauge and the spare arithmetic read better in whole-ish
        # ticket units, and the client sums these, so full floats produced artifacts like
        # 4.300000000000001 (the same reason velocity's forecast rounds).
        name.lower(): {"name": name, "vel": vel_by_key.get(name.lower(), 0),
                       "wip": round(wip_by_key.get(name.lower(), 0.0), 1),
                       "done": done_by_key.get(name.lower(), 0)}
        for name in ROSTER.values()
    }

    queue_statuses = ", ".join(["?"] * len(_QUEUE_STATUSES))
    non_queue = ", ".join(["?"] * len(_NON_QUEUE_TYPES))
    queue: list[dict] = []
    for key, created, status, issuetype, priority, reporter, assignee, account_id, summary in connection.execute(
        f"SELECT key, created, status, issuetype, priority, reporter, assignee, assignee_account_id, summary "
        f"FROM issues WHERE status IN ({queue_statuses}) AND issuetype NOT IN ({non_queue}) ORDER BY created ASC",
        list(_QUEUE_STATUSES) + list(_NON_QUEUE_TYPES),
    ).fetchall():
        roster_key = _KEY_BY_ACCOUNT.get(account_id)
        assigned = roster_key or (f"ext:{assignee}" if assignee else None)
        queue.append({
            "k": key, "created": created.date().isoformat(), "st": status, "t": issuetype,
            "p": priority, "rep": reporter or "", "asg": assigned, "sum": summary or "",
        })

    corpus: list[dict] = []
    for account_id, summary, status_category, status in connection.execute(
        f"SELECT assignee_account_id, summary, status_category, status FROM issues "
        f"WHERE issuetype IN ({delivery}) "
        f"AND ((status_category = 'done' AND status <> 'Will Not Do') OR status IN ({wip_statuses}))",
        list(DELIVERY_TYPES) + list(_WIP_STATUSES),
    ).fetchall():
        key = _KEY_BY_ACCOUNT.get(account_id)
        if not key:
            continue
        weight = _DONE_WEIGHT if status_category == "done" else _ACTIVE_WEIGHT
        corpus.append({"key": key, "sum": summary or "", "w": weight})

    return {"roster": roster, "queue": queue, "corpus": corpus, "mr_domains": _mr_domains_by_key(connection)}
