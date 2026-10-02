"""Delivery Forecast: the engine leadership commitments are based on.

Pins the method's promises: the damped trend slows growth instead of extrapolating it forever; the
back-test never peeks at the future; ranges come from past errors once there are enough of them (and
say so when there are not); the accuracy shown on the page is out-of-sample; the "can we commit?"
calculator honours the capacity share; and only full weeks of PE Done tickets are counted.
"""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import forecast_report as fr  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()
_THIS_MONDAY = _TODAY.to_period("W-SUN").start_time


def test_flat_history_forecasts_flat():
    point = fr.damped_trend(np.full(20, 10.0), 8)
    assert np.allclose(point, 10.0)


def test_growth_is_damped_not_extrapolated_forever():
    history = np.linspace(20, 80, 12)                      # strong recent growth
    point = fr.damped_trend(history, 30)
    increments = np.diff(point)
    assert (increments > 0).all()                          # still growing…
    assert increments[-1] < increments[0] * 0.05           # …but the growth fades
    assert point[-1] < 80 * 2                              # nowhere near a straight-line projection


def test_backtest_never_uses_the_future():
    series = np.arange(1, 41, dtype=float)
    changed = series.copy()
    changed[30:] = 1000                                    # change everything from week 30 on
    a = fr.backtest(series).query("origin == 25 and h == 1")["predicted"].iloc[0]
    b = fr.backtest(changed).query("origin == 25 and h == 1")["predicted"].iloc[0]
    assert a == b


def test_ranges_are_calibrated_with_enough_history_and_flagged_otherwise():
    short = fr.delivery_forecast(np.full(12, 10.0), max_weeks=8)
    assert not short["calibrated"].any()
    assert short.loc[short["h"] == 4, "likely"].iloc[0] == pytest.approx(40)
    assert short.loc[short["h"] == 4, "low"].iloc[0] == pytest.approx(40)   # no variation, no range

    rng = np.random.default_rng(1)
    noisy = 50 + rng.normal(0, 8, 80)
    long = fr.delivery_forecast(noisy, max_weeks=12)
    assert long["calibrated"].all()
    assert (long["low"] <= long["likely"]).all() and (long["likely"] <= long["high"]).all()
    assert (np.diff(long["likely"]) > 0).all()


def test_accuracy_is_out_of_sample_and_roughly_calibrated_on_stable_data():
    rng = np.random.default_rng(2)
    series = 50 + rng.normal(0, 8, 120)
    acc = fr.accuracy(fr.backtest(series), h=4, last=40)
    assert len(acc) == 40
    assert 0.6 <= acc["inside"].mean() <= 0.95


def test_commit_calculator_uses_the_capacity_share():
    forecast = fr.delivery_forecast(np.full(12, 10.0), max_weeks=52)       # 10 tickets a week, no range
    assert fr.weeks_to_deliver(30, forecast) == {"likely": 3, "safe": 3}
    assert fr.weeks_to_deliver(30, forecast, share=0.5) == {"likely": 6, "safe": 6}
    assert fr.weeks_to_deliver(10_000, forecast) == {"likely": None, "safe": None}
    assert fr.weeks_to_deliver(0, forecast) is None


def _issue(key, closed, issuetype="Story", project="DevOps", status="Done"):
    return {
        "key": key, "status": status, "issuetype": issuetype, "project_name": project, "assignee_name": "Ana",
        "priority_name": "Medium", "estimated_size_name": "Small", "business_lead": "Lead", "summary": key,
        "created": (closed - pd.Timedelta(days=2)).tz_localize(_LOCAL), "updated": closed.tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC"),
        "planned_start_date": pd.NaT, "target_end_date": pd.NaT,
    }


def test_only_full_weeks_of_pe_done_tickets_are_counted():
    last_wed = _THIS_MONDAY - pd.Timedelta(days=5)
    rows = [_issue("A", last_wed), _issue("B", last_wed),
            _issue("F", last_wed, issuetype="Feature"),
            _issue("C", last_wed, issuetype="Change and Release", project="Release Management"),
            _issue("W", last_wed, status="Will Not Do"),
            _issue("NOW", _TODAY)]                                        # current week: never counted
    weeks, delivered, _, _ = fr.weekly_series(pd.DataFrame(rows))
    assert weeks[-1] == _THIS_MONDAY - pd.Timedelta(days=7)
    assert delivered[-1] == 2
