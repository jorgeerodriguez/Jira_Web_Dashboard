"""Trend: monthly improvement measures and the scorecard built from them.

The page answers "are we getting better?", so the rules are pinned: PE Done tickets only, each in the
month it moved to Done; lead time from creation and cycle time from Target start in business days;
the scorecard compares the last *full* month with the three before it (never the partial current
month) and colours each delta by whether up is good for that measure.
"""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import in_progress_report as ipr  # noqa: E402
from reports import trend_report as trend  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()
_CURRENT = _TODAY.to_period("M")


def _mid(months_ago: int) -> pd.Timestamp:
    """A Wednesday near the middle of the month `months_ago` before the current one."""
    day = (_CURRENT - months_ago).to_timestamp() + pd.Timedelta(days=14)
    return day + pd.Timedelta(days=(2 - day.weekday()) % 7)


def _done(key, months_ago, lead_days=7, start_days=2, assignee="Ana", priority="Medium", issuetype="Story",
          project="DevOps", comments=True):
    closed = _mid(months_ago)
    created = closed - pd.Timedelta(days=lead_days)
    start = closed - pd.Timedelta(days=start_days)
    return {
        "key": key, "status": "Done", "issuetype": issuetype, "project_name": project, "assignee_name": assignee,
        "priority_name": priority, "estimated_size_name": "Medium", "business_lead": "Lead", "summary": key,
        "created": created.tz_localize(_LOCAL), "updated": closed.tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC"),
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"), "target_end_date": pd.NaT,
        "comments": [{"author": assignee, "body": "done", "created": closed.tz_localize(_LOCAL)}] if comments else [],
    }


def test_monthly_measures_use_pe_done_tickets_in_business_days():
    rows = [
        _done("A", 1, lead_days=7),                                  # Wed -> Wed: 5 business days unless a holiday
        _done("B", 1, lead_days=7, priority="Urgent", issuetype="Bug", comments=False),
        _done("F", 1, issuetype="Feature"),                          # not a ticket
        _done("C", 1, issuetype="Change and Release", project="Release Management"),  # CAR
    ]
    month = trend.build_trend_visuals(pd.DataFrame(rows))["monthly"].loc[_CURRENT - 1]
    assert month["delivered"] == 2
    week_start = (_mid(1) - pd.Timedelta(days=7)).date()
    expected = int(np.busday_count(week_start, _mid(1).date(), holidays=ipr._calendar_holidays(_TODAY)))
    assert month["lead_median"] == expected
    assert month["urgent_share"] == pytest.approx(0.5)
    assert month["reactive_share"] == pytest.approx(0.5)
    assert month["comment_coverage"] == pytest.approx(0.5)
    assert month["engineers"] == 1


def test_scorecard_compares_last_full_month_with_the_three_before():
    rows = [_done(f"P{m}-{i}", m) for m in (2, 3, 4) for i in range(10)]   # baseline: 10 a month
    rows += [_done(f"L-{i}", 1) for i in range(20)]                       # last full month: 20
    rows += [_done(f"NOW-{i}", 0) for i in range(1)] if _mid(0) <= _TODAY else []
    out = trend.build_trend_visuals(pd.DataFrame(rows))
    cards = {c["key"]: c for c in out["scorecard"]}
    assert out["last_full_month"] == (_CURRENT - 1).strftime("%B %Y")
    assert cards["delivered"]["value"] == "20"
    assert cards["delivered"]["delta"].startswith("+100%")
    assert cards["delivered"]["delta_color"] == "normal"
    assert cards["lead_median"]["delta_color"] == "inverse"   # lower lead time is better
    assert cards["engineers"]["delta_color"] == "off"


def test_current_month_is_labelled_so_far():
    out = trend.build_trend_visuals(pd.DataFrame([_done("A", 1)]))
    assert out["monthly_df"]["Month"].iloc[0].endswith("(so far)")


def test_team_heatmap_shows_people_with_enough_deliveries():
    rows = [_done(f"A-{i}", 1, assignee="Ana") for i in range(trend.CORE_MIN_DELIVERED)]
    rows += [_done("B-1", 1, assignee="Ben")]
    team = trend.build_trend_visuals(pd.DataFrame(rows))["team_fig"]
    assert list(team.data[0].y) == ["Ana"]
