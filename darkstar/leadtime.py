"""Lead-time dashboard aggregation: lead (created→Done) and cycle (active spells).

Population: delivery-type stories that are direct children of a Feature and reached Done
within the trailing complete months. Lead = created→last Done; cycle = time summed across
active statuses (In Progress/Blocked/On Hold/Validating/Reviewing/Staged CAR), floor 1 day.
Reproduces the audacy-jira-reports cl_compute methodology; grouped by Feature.
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb

from darkstar.metrics import BUSINESS_TZ, DELIVERY_TYPES, denver_month, window_months

_ACTIVE: frozenset[str] = frozenset(
    {"In Progress", "Blocked", "On Hold", "Validating", "Reviewing", "Staged CAR"}
)
_WINDOW_MONTHS: int = 6
_DAY_SECONDS: float = 86400.0

_STORIES_SQL: str = (
    "SELECT c.key, c.parent_key, c.summary, c.assignee, c.created, f.summary "
    "FROM issues c "
    "JOIN issues f ON f.key = c.parent_key AND f.issuetype = 'Feature' "
    "WHERE c.issuetype IN ({placeholders}) "
    "AND EXISTS (SELECT 1 FROM transitions t WHERE t.key = c.key AND t.to_status = 'Done')"
)


def _lead_and_cycle(created: datetime, transitions: list[tuple[str, datetime]]) -> tuple[int, int, datetime]:
    """Lead (created→last Done) and cycle (summed active spells) for one issue's transitions."""
    current, start, active_seconds, done_at = "To Do", created, 0.0, None
    for to_status, changed_at in transitions:
        if current in _ACTIVE:
            active_seconds += max(0.0, (changed_at - start).total_seconds())
        current, start = to_status, changed_at
        if to_status == "Done":
            done_at = changed_at
    done = done_at or (transitions[-1][1] if transitions else created)
    cycle = max(1, round(active_seconds / _DAY_SECONDS))
    lead = max(0, round((done - created).total_seconds() / _DAY_SECONDS))
    return lead, cycle, done


def lead_time_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Lead/cycle per delivered story (children of Features) within the trailing months."""
    placeholders = ", ".join(["?"] * len(DELIVERY_TYPES))
    candidates = connection.execute(
        _STORIES_SQL.format(placeholders=placeholders), list(DELIVERY_TYPES)
    ).fetchall()

    keys = [row[0] for row in candidates]
    transitions_by_key: dict[str, list[tuple[str, datetime]]] = {}
    if keys:
        key_placeholders = ", ".join(["?"] * len(keys))
        for key, to_status, changed_at in connection.execute(
            f"SELECT key, to_status, changed_at FROM transitions WHERE key IN ({key_placeholders}) ORDER BY key, seq",
            keys,
        ).fetchall():
            transitions_by_key.setdefault(key, []).append((to_status, changed_at))

    now_local = now.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    window = set(window_months(now_local.year, now_local.month, _WINDOW_MONTHS))

    stories: list[dict] = []
    feature_names: dict[str, str] = {}
    for key, feature, summary, assignee, created, feature_summary in candidates:
        lead, cycle, done = _lead_and_cycle(created, transitions_by_key.get(key, []))
        if denver_month(done) not in window:
            continue
        stories.append({
            "k": key, "feature": feature, "s": summary or "", "a": assignee,
            "lead": lead, "cyc": cycle,
            "done": done.date().isoformat(), "created": created.date().isoformat(),
        })
        feature_names[feature] = feature_summary or feature
    return {"stories": stories, "feature_names": feature_names}
