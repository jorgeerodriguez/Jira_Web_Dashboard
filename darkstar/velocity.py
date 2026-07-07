"""Velocity dashboard aggregation: monthly delivery completions per engineer.

Completion = the earliest changelog transition to 'Done' (dedupes reopens). Scope is the
delivery issue types; months are attributed in the America/Denver business timezone; only
PE roster members are counted. All reads come from the store (no Jira call).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import duckdb

from darkstar.roster import ROSTER

_DELIVERY_TYPES: tuple[str, ...] = ("Story", "Task", "Bug", "Hotfix", "Sub-task")
_BUSINESS_TZ: ZoneInfo = ZoneInfo("America/Denver")
_WINDOW_MONTHS: int = 6

_COMPLETIONS_SQL: str = (
    "SELECT i.assignee_account_id AS account_id, MIN(t.changed_at) AS done_at "
    "FROM issues i "
    "JOIN transitions t ON t.key = i.key AND t.to_status = 'Done' "
    "WHERE i.issuetype IN ({placeholders}) "
    "GROUP BY i.key, i.assignee_account_id"
)


def _window_months(year: int, month: int, months: int) -> list[tuple[int, int]]:
    """The `months` complete calendar months ending the month before (year, month)."""
    result: list[tuple[int, int]] = []
    current_year, current_month = year, month
    for _ in range(months):
        current_month -= 1
        if current_month == 0:
            current_year -= 1
            current_month = 12
        result.append((current_year, current_month))
    return list(reversed(result))


def _denver_month(done_at: datetime) -> tuple[int, int]:
    """(year, month) of a naive-UTC completion timestamp, in the business timezone."""
    local = done_at.replace(tzinfo=timezone.utc).astimezone(_BUSINESS_TZ)
    return (local.year, local.month)


def _forecast(counts: list[int]) -> dict:
    """Recency-weighted next-month estimate from the last three months, with a ±1 s.d. band."""
    recent = counts[-3:]
    value = (recent[0] * 1 + recent[1] * 2 + recent[2] * 3) / 6
    spread = statistics.pstdev(recent)
    return {"value": round(value, 1), "low": round(max(0.0, value - spread), 1), "high": round(value + spread, 1)}


def velocity_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Monthly delivery completions per roster engineer over the trailing complete months."""
    placeholders = ", ".join(["?"] * len(_DELIVERY_TYPES))
    completions = connection.execute(
        _COMPLETIONS_SQL.format(placeholders=placeholders), list(_DELIVERY_TYPES)
    ).fetchall()

    now_local = now.replace(tzinfo=timezone.utc).astimezone(_BUSINESS_TZ)
    window = _window_months(now_local.year, now_local.month, _WINDOW_MONTHS)
    month_index = {year_month: i for i, year_month in enumerate(window)}
    month_labels = [f"{year:04d}-{month:02d}" for (year, month) in window]

    counts_by_name: dict[str, list[int]] = {name: [0] * _WINDOW_MONTHS for name in ROSTER.values()}
    for account_id, done_at in completions:
        name = ROSTER.get(account_id)
        if name is None or done_at is None:
            continue
        index = month_index.get(_denver_month(done_at))
        if index is None:
            continue
        counts_by_name[name][index] += 1

    members = [
        {"name": name, "counts": counts, "total": sum(counts), "forecast": _forecast(counts)}
        for name, counts in counts_by_name.items()
    ]
    members.sort(key=lambda member: (-member["total"], member["name"]))

    team_counts = [sum(counts_by_name[name][i] for name in counts_by_name) for i in range(_WINDOW_MONTHS)]
    team = {"counts": team_counts, "total": sum(team_counts), "forecast": _forecast(team_counts)}

    return {"months": month_labels, "members": members, "team": team}


def completions_this_month(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict[str, int]:
    """Per-roster-name delivery completions attributed to the current business-tz month.

    Same earliest-Done derivation as the forecast, but counts the partial current month, which
    the trailing-window forecast (`_window_months`) deliberately excludes.
    """
    target = _denver_month(now)
    placeholders = ", ".join(["?"] * len(_DELIVERY_TYPES))
    completions = connection.execute(
        _COMPLETIONS_SQL.format(placeholders=placeholders), list(_DELIVERY_TYPES)
    ).fetchall()
    counts: dict[str, int] = {name: 0 for name in ROSTER.values()}
    for account_id, done_at in completions:
        name = ROSTER.get(account_id)
        if name is None or done_at is None:
            continue
        if _denver_month(done_at) == target:
            counts[name] += 1
    return counts
