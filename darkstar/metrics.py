"""Shared metric helpers used across the dashboard aggregations."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import holidays

DELIVERY_TYPES: tuple[str, ...] = ("Story", "Task", "Bug", "Hotfix", "Sub-task")
BUSINESS_TZ: ZoneInfo = ZoneInfo("America/Los_Angeles")
# The business day used by every turnaround/SLA clock: Mon-Fri, 08:00-17:00 in BUSINESS_TZ,
# excluding the company holidays below. Nine hours, so a business week is 45h.
BUSINESS_DAY_START: time = time(8, 0)
BUSINESS_DAY_END: time = time(17, 0)
BUSINESS_HOURS_PER_DAY: float = 9.0

# Which US federal holidays Audacy actually closes for. The `holidays` package supplies the rules
# (including observance shifts — Independence Day 2026 falls on a Saturday and is observed on
# Friday 2026-07-03), so this never goes stale; this set only decides which of them count.
# Deliberately excludes the federal days most private employers work: Washington's Birthday,
# Columbus Day, Veterans Day. Edit this one set if HR's calendar differs — e.g. add
# "Day After Thanksgiving" as a custom entry via EXTRA_HOLIDAYS.
OBSERVED_HOLIDAY_NAMES: frozenset[str] = frozenset({
    "New Year's Day",
    "Martin Luther King Jr. Day",
    "Memorial Day",
    "Juneteenth National Independence Day",
    "Independence Day",
    "Labor Day",
    "Thanksgiving Day",
    "Christmas Day",
})

# Company-specific closures the federal calendar does not carry (floating days, shutdown weeks).
EXTRA_HOLIDAYS: frozenset[date] = frozenset()

_US_HOLIDAYS = holidays.UnitedStates(observed=True)


def is_holiday(day: date) -> bool:
    """True iff `day` is a company holiday: an observed federal day we close for, or an extra.

    `holidays` expands years lazily on lookup, so this stays correct for future years without a
    code change. Observed shifts arrive already resolved, hence stripping the "(observed)" suffix
    before matching the name.
    """
    if day in EXTRA_HOLIDAYS:
        return True
    name = _US_HOLIDAYS.get(day)
    if name is None:
        return False
    return any(part.replace(" (observed)", "").strip() in OBSERVED_HOLIDAY_NAMES
               for part in name.split("; "))


def business_month(when: datetime) -> tuple[int, int]:
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


def ready_hours(opened_at: datetime, merged_at: datetime,
                events: list[tuple[str, datetime]]) -> float:
    """Business hours the MR spent marked ready, i.e. actually waiting on PE.

    An MR sitting in draft is not waiting on anyone -- the author is still working. Measuring from
    `opened_at` charged that time to review: on the 20 slowest MRs, 67% of all attributed hours
    were draft time, and one 612-hour MR was marked ready 15 minutes before it merged.

    The clock starts ready and stops on every "draft", restarting on every "ready", so an MR that
    toggles repeatedly accrues only its ready spells. An MR whose first event is "ready" was opened
    as a draft, so the clock does not start until then.
    """
    toggles = [(kind, when) for kind, when in events if kind in ("ready", "draft")]
    if not toggles:
        return business_hours_between(opened_at, merged_at)

    total = 0.0
    is_ready = toggles[0][0] != "ready"   # first event "ready" => it was a draft before that
    spell_start = opened_at
    for kind, when in toggles:
        moment = min(max(when, opened_at), merged_at)
        if is_ready and kind == "draft":
            total += business_hours_between(spell_start, moment)
            is_ready = False
        elif not is_ready and kind == "ready":
            spell_start = moment
            is_ready = True
    if is_ready:
        total += business_hours_between(spell_start, merged_at)
    return total


def business_date(when: datetime) -> date:
    """Calendar date of a naive-UTC timestamp in the business timezone.

    The day-level counterpart to business_month: an MR merged at 23:30 Pacific belongs to that
    Pacific day, not to the following UTC one.
    """
    return when.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ).date()


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

    Calendar elapsed time charges a request for nights, weekends and holidays nobody was working,
    so a ticket filed at 16:00 and closed at 09:00 the next morning reads as 17 hours. This walks
    the span day by day in BUSINESS_TZ and sums only each working day's BUSINESS_DAY_START..END
    overlap, making the same ticket 1.0. Returns 0.0 when `end` is at or before `start`.
    """
    if end <= start:
        return 0.0
    local_start = start.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)
    local_end = end.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)

    total_seconds = 0.0
    day = local_start.date()
    last_day = local_end.date()
    while day <= last_day:
        if day.weekday() < 5 and not is_holiday(day):  # Mon-Fri, excluding company holidays
            opens = datetime.combine(day, BUSINESS_DAY_START, tzinfo=BUSINESS_TZ)
            closes = datetime.combine(day, BUSINESS_DAY_END, tzinfo=BUSINESS_TZ)
            overlap_start = max(local_start, opens)
            overlap_end = min(local_end, closes)
            if overlap_end > overlap_start:
                total_seconds += (overlap_end - overlap_start).total_seconds()
        day += timedelta(days=1)
    return total_seconds / 3600.0
