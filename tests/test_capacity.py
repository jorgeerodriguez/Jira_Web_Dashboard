"""Capacity: throughput, demand vs capacity, the delivery forecast and load balance.

Leadership uses this page to decide whether the team can take on more, so the rules are pinned:
only PE tickets moved to Done count as delivered, only full weeks are used,
the forecast is the same engine as the Forecast page, and the load charts show the core team rather
than one-off contributors.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import capacity_report as cap  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()
_LAST_WEEK_MONDAY = _TODAY.to_period("W-SUN").start_time - pd.Timedelta(days=7)


def _issue(key, status="Done", closed=None, created=None, assignee="Ana", issuetype="Story", project="DevOps"):
    created = created if created is not None else (closed if closed is not None else _TODAY) - pd.Timedelta(days=3)
    return {
        "key": key, "status": status, "issuetype": issuetype, "project_name": project, "assignee_name": assignee,
        "priority_name": "Medium", "estimated_size_name": "Small", "business_lead": "Lead", "summary": key,
        "created": created.tz_localize(_LOCAL), "updated": (closed or _TODAY).tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC") if closed is not None else pd.NaT,
        "planned_start_date": pd.NaT, "target_end_date": pd.NaT,
    }


def test_capacity_forecast_matches_the_forecast_page():
    from reports import forecast_report as fr
    wed = _LAST_WEEK_MONDAY + pd.Timedelta(days=2)
    rows = [_issue(f"D-{w}-{i}", closed=wed - pd.Timedelta(days=7 * w)) for w in range(10) for i in range(5)]
    df = pd.DataFrame(rows)
    out = cap.build_capacity_visuals(df)
    _, delivered, _, _ = fr.weekly_series(df)
    engine = fr.delivery_forecast(delivered).set_index("h")
    for weeks, card in out["forecast"].items():
        assert card["likely"] == round(engine.loc[weeks, "likely"])
        assert card["at_least"] == round(engine.loc[weeks, "low"])


def test_only_pe_done_tickets_in_full_weeks_count_as_delivered():
    wed = _LAST_WEEK_MONDAY + pd.Timedelta(days=2)
    rows = [
        _issue("D-1", closed=wed), _issue("D-2", closed=wed, assignee="Ben"),
        _issue("W-1", status="Will Not Do", closed=wed),                                   # not delivered
        _issue("F-1", closed=wed, issuetype="Feature"),                                    # not a ticket
        _issue("C-1", closed=wed, issuetype="Change and Release", project="Release Management"),  # CAR
        _issue("NOW", closed=_TODAY),                                                       # current week: excluded
    ]
    out = cap.build_capacity_visuals(pd.DataFrame(rows))
    last = out["weekly_df"].iloc[0]                     # newest full week first
    assert last["Delivered"] == 2
    assert last["Engineers Delivering"] == 2
    assert last["Per Engineer"] == 1.0


def test_demand_ratio_compares_requested_with_delivered():
    monday = _LAST_WEEK_MONDAY
    rows = [_issue(f"D-{i}", closed=monday + pd.Timedelta(days=1), created=monday) for i in range(4)]
    rows += [_issue(f"N-{i}", status="To Do", created=monday + pd.Timedelta(days=2)) for i in range(4)]
    kpis = cap.build_capacity_visuals(pd.DataFrame(rows))["kpis"]
    assert kpis["demand_ratio"] == pytest.approx(2.0)   # 8 requested vs 4 delivered in the last 4 weeks
    assert kpis["queue"] == 4


def test_load_charts_show_the_core_team_only():
    wed = _LAST_WEEK_MONDAY + pd.Timedelta(days=2)
    rows = [_issue(f"A-{i}", closed=wed, assignee="Ana") for i in range(5)]
    rows += [_issue("O-1", closed=wed, assignee="Occasional")]
    rows += [_issue("B-WIP", status="In Progress", assignee="Ben")]
    out = cap.build_capacity_visuals(pd.DataFrame(rows))
    assert set(out["wip_fig"].data[0].y) == {"Ana", "Ben"}
    assert out["kpis"]["core_team"] == 2 and out["kpis"]["occasional"] == 1
    assert set(out["people_df"]["Engineer"]) == {"Ana", "Ben", "Occasional"}
