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

# accountId -> short key (lowercased first name), matching the dashboard's editorial config keys.
_KEY_BY_ACCOUNT: dict[str, str] = {account_id: name.lower() for account_id, name in ROSTER.items()}


def _velocity_by_key(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict[str, int]:
    """Predicted velocity per roster key, reused from the velocity view's forecast."""
    report = velocity.velocity_report(connection, now)
    return {member["name"].lower(): round(member["forecast"]["value"]) for member in report["members"]}


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

    wip_by_key: dict[str, int] = {}
    for account_id, count in connection.execute(
        f"SELECT assignee_account_id, count(*) FROM issues "
        f"WHERE issuetype IN ({delivery}) AND status IN ({wip_statuses}) GROUP BY assignee_account_id",
        list(DELIVERY_TYPES) + list(_WIP_STATUSES),
    ).fetchall():
        key = _KEY_BY_ACCOUNT.get(account_id)
        if key:
            wip_by_key[key] = count

    roster = {
        name.lower(): {"name": name, "vel": vel_by_key.get(name.lower(), 0), "wip": wip_by_key.get(name.lower(), 0), "done": done_by_key.get(name.lower(), 0)}
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
