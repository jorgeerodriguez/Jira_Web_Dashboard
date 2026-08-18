"""Shared metric helpers used across the dashboard aggregations."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DELIVERY_TYPES: tuple[str, ...] = ("Story", "Task", "Bug", "Hotfix", "Sub-task")
BUSINESS_TZ: ZoneInfo = ZoneInfo("America/Denver")
# The business day used by every turnaround/SLA clock: Mon-Fri, 09:00-17:00 in BUSINESS_TZ.
# Company holidays are not modelled — a holiday counts as a normal working day.
BUSINESS_DAY_START: time = time(9, 0)
BUSINESS_DAY_END: time = time(17, 0)
BUSINESS_HOURS_PER_DAY: float = 8.0


def denver_month(when: datetime) -> tuple[int, int]:
    """(year, month) of a naive-UTC timestamp, in the business timezone."""
    local = when.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    return (local.year, local.month)


# The self-service workflows did not exist before this. Measured, not assumed: the first MR
# carrying a "Generated with Claude Code" footer opened 2026-03-18, ramping 8 (Mar) / 22 (Apr) /
# 74 (May) / 176 (Jun) / 215 (Jul). Reaching back past it pads every rate with dead months, so it
# is the hard floor on any self-service window. The pe-* Jira labels only began 2026-05-27 and so
# date the *labelling*, not the workflows.
SELF_SERVICE_EPOCH: datetime = (
    datetime(2026, 3, 1, tzinfo=BUSINESS_TZ).astimezone(timezone.utc).replace(tzinfo=None)
)


def window_start(now: datetime, months: int, floor: datetime) -> datetime:
    """Naive-UTC start of the trailing `months`-month window, never earlier than `floor`.

    The rolling counterpart to window_months: that one yields complete months only (right for
    month-bucketed charts, wrong for a live SLA table, which must show the current month). `floor`
    clamps it so a window can never open before the thing it measures existed — pass
    SELF_SERVICE_EPOCH for anything self-service.
    """
    local = now.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    year, month = local.year, local.month
    for _ in range(months - 1):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    rolling = datetime(year, month, 1, tzinfo=BUSINESS_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    return max(rolling, floor)


def pctile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in [0,1]) of a non-empty list, rounded to 1 dp; None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


def window_months(year: int, month: int, months: int) -> list[tuple[int, int]]:
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


def business_hours_between(start: datetime, end: datetime) -> float:
    """Working hours between two naive-UTC timestamps, counting only the business day.

    Calendar elapsed time charges a request for nights and weekends nobody was working, so a
    ticket filed at 16:00 and closed at 09:00 the next morning reads as 17 hours. This walks the
    span day by day in BUSINESS_TZ and sums only each weekday's BUSINESS_DAY_START..END overlap,
    making the same ticket 1.0. Returns 0.0 when `end` is at or before `start`.
    """
    if end <= start:
        return 0.0
    local_start = start.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    local_end = end.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)

    total_seconds = 0.0
    day = local_start.date()
    last_day = local_end.date()
    while day <= last_day:
        if day.weekday() < 5:  # Mon-Fri
            opens = datetime.combine(day, BUSINESS_DAY_START, tzinfo=BUSINESS_TZ)
            closes = datetime.combine(day, BUSINESS_DAY_END, tzinfo=BUSINESS_TZ)
            overlap_start = max(local_start, opens)
            overlap_end = min(local_end, closes)
            if overlap_end > overlap_start:
                total_seconds += (overlap_end - overlap_start).total_seconds()
        day += timedelta(days=1)
    return total_seconds / 3600.0
