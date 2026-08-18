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

# Denver is UTC-6 in August (MDT) and UTC-7 in January/March (MST); inputs are naive UTC, as in the store.

_NO_FLOOR = datetime(2020, 1, 1)


def test_overnight_gap_is_not_charged():
    """Filed 16:00 Tue, closed 09:00 Wed = 1 working hour, not the 17 calendar hours."""
    filed = datetime(2026, 8, 18, 22, 0)     # Tue 16:00 MDT
    closed = datetime(2026, 8, 19, 15, 0)    # Wed 09:00 MDT
    assert (closed - filed).total_seconds() / 3600 == 17.0
    assert business_hours_between(filed, closed) == 1.0


def test_weekend_is_not_charged():
    """A Friday-afternoon request closed Monday morning must not be penalised for the weekend."""
    filed = datetime(2026, 8, 14, 21, 0)     # Fri 15:00 MDT
    closed = datetime(2026, 8, 17, 16, 0)    # Mon 10:00 MDT
    assert (closed - filed).total_seconds() / 3600 == 67.0
    assert business_hours_between(filed, closed) == 3.0


def test_work_entirely_outside_business_hours_is_zero_not_negative():
    """Opened and merged on a Saturday: no working hours elapsed, and the clock must not go negative."""
    assert business_hours_between(datetime(2026, 8, 15, 16, 0), datetime(2026, 8, 16, 22, 0)) == 0.0


def test_end_before_start_is_zero():
    """Clock skew between Jira and GitLab must never produce a negative turnaround."""
    assert business_hours_between(datetime(2026, 8, 18, 22, 0), datetime(2026, 8, 18, 21, 0)) == 0.0


def test_one_full_business_week_is_forty_hours():
    """The week is 40h, so an SLA target can be read as days without a conversion table."""
    assert business_hours_between(datetime(2026, 8, 17, 6, 0), datetime(2026, 8, 22, 0, 0)) == 40.0


def test_dst_transition_does_not_shift_the_business_day():
    """The business day is 09:00-17:00 *local*, so a spring-forward weekend still counts 3h."""
    filed = datetime(2026, 3, 6, 22, 0)      # Fri 15:00 MST
    closed = datetime(2026, 3, 9, 16, 0)     # Mon 10:00 MDT (clocks moved on Sun the 8th)
    assert business_hours_between(filed, closed) == 3.0


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
    """Wed 16:00 -> Fri 10:00 over Thanksgiving: 1h Wed + 0h Thu (holiday) + 1h Fri."""
    filed = datetime(2026, 11, 25, 23, 0)    # Wed 16:00 MST
    closed = datetime(2026, 11, 27, 17, 0)   # Fri 10:00 MST
    assert (closed - filed).total_seconds() / 3600 == 42.0
    assert business_hours_between(filed, closed) == 2.0


def test_holiday_week_is_shorter_than_a_full_week():
    """Thanksgiving week is 32 business hours, not 40, so an SLA must not expect 40."""
    monday = datetime(2026, 11, 23, 7, 0)    # Mon 00:00 MST
    saturday = datetime(2026, 11, 28, 7, 0)  # Sat 00:00 MST
    assert business_hours_between(monday, saturday) == 32.0


def test_window_start_includes_the_current_month():
    """A live SLA table read mid-month must show this month's requests, unlike window_months."""
    assert window_start(datetime(2026, 8, 18, 12, 0), 6, _NO_FLOOR) == datetime(2026, 3, 1, 7, 0)


def test_window_start_crosses_the_year_boundary():
    assert window_start(datetime(2026, 2, 10, 12, 0), 6, _NO_FLOOR) == datetime(2025, 9, 1, 6, 0)


def test_window_never_opens_before_the_self_service_epoch():
    """Reaching back past the workflows' first month pads every rate with dead months.

    The first MR carrying an agent footer opened 2026-03-18, so a window reaching into 2025 would
    dilute volume and SLA-met with months in which no self-service request could exist.
    """
    assert SELF_SERVICE_EPOCH == datetime(2026, 3, 1, 7, 0)          # 2026-03-01 00:00 MST
    # May 2026 less six months would reach back to 2025-12-01; the epoch clamps it.
    assert window_start(datetime(2026, 5, 15, 12, 0), 6, SELF_SERVICE_EPOCH) == SELF_SERVICE_EPOCH


def test_rolling_window_takes_over_once_it_starts_after_the_epoch():
    """The floor must not freeze the window open forever — six months on, rolling wins again."""
    assert window_start(datetime(2026, 11, 15, 12, 0), 6, SELF_SERVICE_EPOCH) == datetime(2026, 6, 1, 6, 0)
