"""business_hours_between must charge a request only for hours the team was working.

Calendar elapsed time is what made the SLA dashboard read 17h for a request that took one
working hour, so these encode *why* the clock is shaped this way: nights, weekends and DST
shifts must not inflate a turnaround the team had no chance to act on.

window_start's floor matters for the same reason in the other direction — a window that opens
before the self-service workflows existed pads every rate with months that could hold no requests.
"""
from datetime import date, datetime

from darkstar.metrics import (
    SELF_SERVICE_EPOCH,
    business_hours_between,
    is_holiday,
    window_start,
)

# Pacific is UTC-7 in August (PDT) and UTC-8 in Nov/Mar (PST); inputs are naive UTC, as in the store.
# The business day is 08:00-17:00 local, i.e. nine hours.

_NO_FLOOR = datetime(2020, 1, 1)


def test_overnight_gap_is_not_charged():
    """Filed 15:00 Tue, closed 08:00 Wed = 2 working hours, not the 17 calendar hours."""
    filed = datetime(2026, 8, 18, 22, 0)     # Tue 15:00 PDT
    closed = datetime(2026, 8, 19, 15, 0)    # Wed 08:00 PDT
    assert (closed - filed).total_seconds() / 3600 == 17.0
    assert business_hours_between(filed, closed) == 2.0


def test_weekend_is_not_charged():
    """A Friday-afternoon request closed Monday morning must not be penalised for the weekend."""
    filed = datetime(2026, 8, 14, 21, 0)     # Fri 14:00 PDT
    closed = datetime(2026, 8, 17, 16, 0)    # Mon 09:00 PDT
    assert (closed - filed).total_seconds() / 3600 == 67.0
    assert business_hours_between(filed, closed) == 4.0


def test_work_entirely_outside_business_hours_is_zero_not_negative():
    """Opened and merged on a Saturday: no working hours elapsed, and the clock must not go negative."""
    assert business_hours_between(datetime(2026, 8, 15, 16, 0), datetime(2026, 8, 16, 22, 0)) == 0.0


def test_end_before_start_is_zero():
    """Clock skew between Jira and GitLab must never produce a negative turnaround."""
    assert business_hours_between(datetime(2026, 8, 18, 22, 0), datetime(2026, 8, 18, 21, 0)) == 0.0


def test_one_full_business_week_is_forty_five_hours():
    """Nine-hour days, so the week is 45h; an SLA target still reads as days without a table."""
    assert business_hours_between(datetime(2026, 8, 17, 6, 0), datetime(2026, 8, 22, 0, 0)) == 45.0


def test_dst_transition_does_not_shift_the_business_day():
    """The business day is 08:00-17:00 *local*, so a spring-forward weekend still counts 4h."""
    filed = datetime(2026, 3, 6, 22, 0)      # Fri 14:00 PST
    closed = datetime(2026, 3, 9, 16, 0)     # Mon 09:00 PDT (clocks moved on Sun the 8th)
    assert business_hours_between(filed, closed) == 4.0


def test_company_holidays_are_not_working_days():
    """A holiday is as much a non-working day as a Sunday; billing a request for it is wrong."""
    assert is_holiday(date(2026, 11, 26)) is True     # Thanksgiving
    assert is_holiday(date(2026, 12, 25)) is True     # Christmas Day
    assert is_holiday(date(2026, 8, 18)) is False     # an ordinary Tuesday


def test_observed_shift_moves_the_holiday_to_the_working_day():
    """Independence Day 2026 is a Saturday, so the closure actually falls on Friday the 3rd."""
    assert is_holiday(date(2026, 7, 3)) is True       # observed
    assert is_holiday(date(2026, 7, 4)) is True       # the date itself, a Saturday anyway


def test_federal_days_the_company_works_are_not_holidays():
    """OBSERVED_HOLIDAY_NAMES is the company calendar, not the full federal one."""
    assert is_holiday(date(2026, 10, 12)) is False    # Columbus Day
    assert is_holiday(date(2026, 11, 11)) is False    # Veterans Day
    assert is_holiday(date(2026, 2, 16)) is False     # Washington's Birthday


def test_a_holiday_is_skipped_inside_a_span():
    """Wed 15:00 -> Fri 09:00 over Thanksgiving: 2h Wed + 0h Thu (holiday) + 1h Fri."""
    filed = datetime(2026, 11, 25, 23, 0)    # Wed 15:00 PST
    closed = datetime(2026, 11, 27, 17, 0)   # Fri 09:00 PST
    assert (closed - filed).total_seconds() / 3600 == 42.0
    assert business_hours_between(filed, closed) == 3.0


def test_holiday_week_is_shorter_than_a_full_week():
    """Thanksgiving week is 36 business hours, not 45, so an SLA must not expect a full week."""
    monday = datetime(2026, 11, 23, 8, 0)    # Mon 00:00 PST
    saturday = datetime(2026, 11, 28, 8, 0)  # Sat 00:00 PST
    assert business_hours_between(monday, saturday) == 36.0


def test_window_start_includes_the_current_month():
    """A live SLA table read mid-month must show this month's requests, unlike window_months."""
    assert window_start(datetime(2026, 8, 18, 12, 0), 6, _NO_FLOOR) == datetime(2026, 3, 1, 8, 0)


def test_window_start_crosses_the_year_boundary():
    assert window_start(datetime(2026, 2, 10, 12, 0), 6, _NO_FLOOR) == datetime(2025, 9, 1, 7, 0)


def test_window_never_opens_before_the_self_service_epoch():
    """Reaching back past the workflows' first month pads every rate with dead months.

    The first MR carrying an agent footer opened 2026-03-18, so a window reaching into 2025 would
    dilute volume and SLA-met with months in which no self-service request could exist.
    """
    assert SELF_SERVICE_EPOCH == datetime(2026, 3, 1, 8, 0)          # 2026-03-01 00:00 PST
    # May 2026 less six months would reach back to 2025-12-01; the epoch clamps it.
    assert window_start(datetime(2026, 5, 15, 12, 0), 6, SELF_SERVICE_EPOCH) == SELF_SERVICE_EPOCH


def test_rolling_window_takes_over_once_it_starts_after_the_epoch():
    """The floor must not freeze the window open forever — six months on, rolling wins again."""
    assert window_start(datetime(2026, 11, 15, 12, 0), 6, SELF_SERVICE_EPOCH) == datetime(2026, 6, 1, 7, 0)


def test_business_week_is_the_monday_in_the_business_timezone():
    """Weeks are Monday-commencing in Pacific, so a week label is a date a person can look up."""
    from darkstar.metrics import business_week
    # Wed 2026-08-19 15:00 PDT -> Monday of that week
    assert business_week(datetime(2026, 8, 19, 22, 0)) == date(2026, 8, 17)
    assert business_week(datetime(2026, 8, 17, 15, 0)) == date(2026, 8, 17)     # the Monday itself


def test_a_sunday_night_merge_belongs_to_the_week_that_is_ending():
    """Grouping on the raw UTC stamp files the last merge of a week as the first of the next.

    23:30 Pacific on Sunday is already Monday 06:30 UTC, so the naive timestamp's weekday is 0 and
    the merge would open a new week containing just itself.
    """
    from darkstar.metrics import business_week
    sunday_late = datetime(2026, 8, 24, 6, 30)          # Sun 2026-08-23 23:30 PDT
    assert sunday_late.weekday() == 0, "the raw UTC stamp really is a Monday"
    assert business_week(sunday_late) == date(2026, 8, 17), "must stay in the week that is ending"


def test_grain_coarsens_as_the_window_grows():
    """A table stops being readable past a dozen or so rows, so the grain adapts instead.

    Every lookback preset must land on a grain producing a handful of rows: 90 days in weekly
    buckets is 14 rows, which is a list to scroll rather than a trend to read.
    """
    from darkstar.metrics import choose_grain
    now = datetime(2026, 8, 19, 12, 0)
    cases = [
        (datetime(2026, 8, 18), datetime(2026, 8, 19), "day"),      # yesterday
        (datetime(2026, 8, 10), datetime(2026, 8, 17), "day"),      # last week
        (datetime(2026, 8, 1), None, "week"),                       # month to date
        (datetime(2026, 7, 1), datetime(2026, 8, 1), "week"),       # last month
        (datetime(2026, 7, 20), None, "week"),                      # last 30 days
        (datetime(2026, 6, 20), None, "month"),                     # last 60 days
        (datetime(2026, 5, 21), None, "month"),                     # last 90 days
    ]
    for since, until, expected in cases:
        assert choose_grain(since, until, now) == expected, f"{since.date()}..{until and until.date()}"


def test_grain_is_measured_to_now_not_to_an_open_upper_bound():
    """An unbounded window ends at the clock, so a future `until` cannot inflate the span."""
    from darkstar.metrics import choose_grain
    now = datetime(2026, 8, 19, 12, 0)
    assert choose_grain(datetime(2026, 8, 10), datetime(2027, 1, 1), now) == "day"


def test_period_start_and_end_bracket_each_grain():
    """Period bounds decide which bucket a row lands in and whether it is flagged partial."""
    from darkstar.metrics import period_end, period_start
    wed = datetime(2026, 8, 19, 22, 0)                       # Wed 2026-08-19 15:00 PDT
    assert period_start(wed, "day") == date(2026, 8, 19)
    assert period_start(wed, "week") == date(2026, 8, 17)    # the Monday
    assert period_start(wed, "month") == date(2026, 8, 1)
    assert period_end(date(2026, 8, 19), "day") == date(2026, 8, 20)
    assert period_end(date(2026, 8, 17), "week") == date(2026, 8, 24)
    assert period_end(date(2026, 8, 1), "month") == date(2026, 9, 1)
    # December has to roll the year, which day-arithmetic gets wrong if written naively.
    assert period_end(date(2026, 12, 1), "month") == date(2027, 1, 1)


def test_an_unknown_grain_is_rejected_rather_than_silently_bucketed():
    from darkstar.metrics import period_start
    import pytest
    with pytest.raises(ValueError, match="grain must be one of"):
        period_start(datetime(2026, 8, 19, 22, 0), "fortnight")
