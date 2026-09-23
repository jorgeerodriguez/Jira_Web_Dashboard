"""Velocity dashboard aggregation: monthly delivery completions per engineer.

Completion = the earliest changelog transition to 'Done' (dedupes reopens). Scope is the
delivery issue types; months are attributed in metrics.BUSINESS_TZ, the one business timezone
shared with every other view; only PE roster members are counted. All reads come from the store
(no Jira call).
"""
from __future__ import annotations

import calendar
import statistics
from datetime import date, datetime, timezone

import duckdb

from darkstar.metrics import BUSINESS_TZ, business_month
from darkstar.roster import ROSTER

_DELIVERY_TYPES: tuple[str, ...] = ("Story", "Task", "Bug", "Hotfix", "Sub-task")
_WINDOW_MONTHS: int = 6
# Estimated Size's options (customfield_10968), smallest first. A completion with no size, or with an
# option added in Jira later, counts as unsized -- the rule intake's weight_of applies to WIP.
_SIZES: tuple[str, ...] = ("Small", "Medium", "Large", "XL")
_UNSIZED: str = "Unsized"

_COMPLETIONS_SQL: str = (
    "SELECT i.assignee_account_id AS account_id, i.estimated_size AS size, MIN(t.changed_at) AS done_at "
    "FROM issues i "
    "JOIN transitions t ON t.key = i.key AND t.to_status = 'Done' "
    "WHERE i.issuetype IN ({placeholders}) "
    "GROUP BY i.key, i.assignee_account_id, i.estimated_size"
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


def _business_days(year: int, month: int, through_day: int) -> int:
    """Count of weekday (Mon–Fri) dates in `month`, from the 1st through `through_day` inclusive."""
    return sum(1 for day in range(1, through_day + 1) if date(year, month, day).weekday() < 5)


def _month_business_days(now_local: datetime) -> tuple[int, int]:
    """The current month's business days (business tz) elapsed through today, and in the month."""
    days_in_month = calendar.monthrange(now_local.year, now_local.month)[1]
    total = _business_days(now_local.year, now_local.month, days_in_month)
    elapsed = _business_days(now_local.year, now_local.month, now_local.day)
    return elapsed, total


def _forecast(counts: list[int], current_done: int, month_elapsed: float) -> dict:
    """This month's projected total in whole tickets, updated by current-month performance.

    `baseline` is the recency-weighted average of the last three complete months (June heaviest).
    The headline `value` blends that baseline toward the current month's run-rate — `current_done`
    extrapolated over the elapsed fraction of the month — weighted by how far the month has
    progressed: early on it tracks the baseline, and it converges to the actual as the month fills
    in. The ±1 s.d. band (recent-month volatility) is recentered on the blend. Whole numbers keep
    client-side team sums exact (summing 1-decimal floats in JS produced artifacts like
    46.900000000000006). `baseline` is exposed so capacity math can use the typical month without
    double-counting current-month completions (see intake).
    """
    recent = counts[-3:]
    baseline = (recent[0] * 1 + recent[1] * 2 + recent[2] * 3) / 6
    spread = statistics.pstdev(recent)
    if month_elapsed <= 0:
        value = baseline
    else:
        run_rate = current_done / month_elapsed
        value = (1 - month_elapsed) * baseline + month_elapsed * run_rate
    return {
        "value": round(value),
        "low": round(max(0.0, value - spread)),
        "high": round(value + spread),
        "baseline": round(baseline),
    }


def velocity_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Monthly delivery completions per roster engineer over the trailing complete months.

    Each member also carries `sizes`: the same monthly completions split by Estimated Size, unsized
    included. `month_progress` is the business days elapsed and in the current month, the weight the
    forecast's blend gives this month's pace.
    """
    placeholders = ", ".join(["?"] * len(_DELIVERY_TYPES))
    completions = connection.execute(
        _COMPLETIONS_SQL.format(placeholders=placeholders), list(_DELIVERY_TYPES)
    ).fetchall()

    now_local = now.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    window = _window_months(now_local.year, now_local.month, _WINDOW_MONTHS)
    month_index = {year_month: i for i, year_month in enumerate(window)}
    month_labels = [f"{year:04d}-{month:02d}" for (year, month) in window]
    current_done_by_name = completions_this_month(connection, now)
    elapsed_days, total_days = _month_business_days(now_local)
    month_elapsed = elapsed_days / total_days if total_days else 0.0

    counts_by_name: dict[str, list[int]] = {name: [0] * _WINDOW_MONTHS for name in ROSTER.values()}
    # The same completions split by size: each one lands in exactly one bucket, so a member's size
    # buckets always sum to their monthly counts.
    sizes_by_name: dict[str, dict[str, list[int]]] = {
        name: {size: [0] * _WINDOW_MONTHS for size in (*_SIZES, _UNSIZED)} for name in ROSTER.values()
    }
    for account_id, size, done_at in completions:
        name = ROSTER.get(account_id)
        if name is None or done_at is None:
            continue
        index = month_index.get(business_month(done_at))
        if index is None:
            continue
        counts_by_name[name][index] += 1
        sizes_by_name[name][size if size in _SIZES else _UNSIZED][index] += 1

    members = [
        {
            "name": name,
            "counts": counts,
            "total": sum(counts),
            "sizes": sizes_by_name[name],
            "forecast": _forecast(counts, current_done_by_name.get(name, 0), month_elapsed),
        }
        for name, counts in counts_by_name.items()
    ]
    members.sort(key=lambda member: (-member["total"], member["name"]))

    team_counts = [sum(counts_by_name[name][i] for name in counts_by_name) for i in range(_WINDOW_MONTHS)]
    team_done = sum(current_done_by_name.values())
    team = {"counts": team_counts, "total": sum(team_counts), "forecast": _forecast(team_counts, team_done, month_elapsed)}

    return {
        "months": month_labels,
        "sizes": list(_SIZES),
        "month_progress": {"elapsed": elapsed_days, "total": total_days},
        "members": members,
        "team": team,
    }


def completions_this_month(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict[str, int]:
    """Per-roster-name delivery completions attributed to the current business-tz month.

    Same earliest-Done derivation as the forecast, but counts the partial current month, which
    the trailing-window forecast (`_window_months`) deliberately excludes.
    """
    target = business_month(now)
    placeholders = ", ".join(["?"] * len(_DELIVERY_TYPES))
    completions = connection.execute(
        _COMPLETIONS_SQL.format(placeholders=placeholders), list(_DELIVERY_TYPES)
    ).fetchall()
    counts: dict[str, int] = {name: 0 for name in ROSTER.values()}
    for account_id, _size, done_at in completions:
        name = ROSTER.get(account_id)
        if name is None or done_at is None:
            continue
        if business_month(done_at) == target:
            counts[name] += 1
    return counts
