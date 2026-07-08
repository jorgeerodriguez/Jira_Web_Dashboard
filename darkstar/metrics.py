"""Shared metric helpers used across the dashboard aggregations."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DELIVERY_TYPES: tuple[str, ...] = ("Story", "Task", "Bug", "Hotfix", "Sub-task")
BUSINESS_TZ: ZoneInfo = ZoneInfo("America/Denver")


def denver_month(when: datetime) -> tuple[int, int]:
    """(year, month) of a naive-UTC timestamp, in the business timezone."""
    local = when.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    return (local.year, local.month)


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
