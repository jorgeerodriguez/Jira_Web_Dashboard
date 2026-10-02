"""Executive Summary: the leadership view's numbers and the rules behind them.

Leadership reads the headlines and tiles without opening the detail pages, so the definitions are
pinned here: period comparisons follow the sidebar Lookback, "closed" counts every closing outcome
while "completed" means Done, the SLA headline ignores a partial current month, stages without a
forecast are judged on how much SLA they have used, and the attention list puts the most serious
tickets first with a reason.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import executive_summary as es  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _issue(key, status="Done", created_days_ago=10, closed_days_ago=2, start_days_ago=8, priority="Medium",
           size="Medium", issuetype="Story", assignee="Ana", target_end_in_days=20, lead="Lead A"):
    created = _TODAY - pd.Timedelta(days=created_days_ago)
    closed = None if closed_days_ago is None else _TODAY - pd.Timedelta(days=closed_days_ago)
    start = None if start_days_ago is None else _TODAY - pd.Timedelta(days=start_days_ago)
    return {
        "key": key, "status": status, "issuetype": issuetype, "assignee_name": assignee,
        "priority_name": priority, "estimated_size_name": size, "business_lead": lead, "summary": key,
        "created": created.tz_localize(_LOCAL), "updated": (closed or _TODAY).tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC") if closed is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC") if start is not None else pd.NaT,
        "target_end_date": pd.Timestamp((_TODAY + pd.Timedelta(days=target_end_in_days)).date(), tz="UTC"),
        "days_old": created_days_ago,
    }


def _history(n=30):
    return [_issue(f"H-{i}", created_days_ago=40 + i, closed_days_ago=30 + i, start_days_ago=36 + i) for i in range(n)]


def test_tiles_compare_the_lookback_window_with_the_one_before():
    rows = _history() + [
        _issue("D-1", closed_days_ago=1), _issue("D-2", closed_days_ago=3),            # completed this week
        _issue("D-3", closed_days_ago=9),                                                # completed last week
        _issue("R-1", status="Released Successfully to Production", closed_days_ago=2),  # closed, not completed
        _issue("N-1", status="To Do", created_days_ago=2, closed_days_ago=None, start_days_ago=None),
        _issue("N-2", status="To Do", created_days_ago=3, closed_days_ago=None, start_days_ago=None),
        _issue("F-1", status="Done", issuetype="Feature", closed_days_ago=1),             # never counted
    ]
    tiles = es.build_executive_summary_data(pd.DataFrame(rows), lookback_days=7)["tiles"]
    assert tiles["completed"] == 2 and tiles["completed_prev"] == 1
    assert tiles["completed_change"] == pytest.approx(1.0)
    assert tiles["closed"] == 3          # Done x2 + Released
    assert tiles["created"] == 2
    assert tiles["net_flow"] == 1
    # Open now: N-1, N-2. Open 7 days ago: D-1, D-2, R-1 (created 10 days ago, closed since).
    assert tiles["open"] == 2 and tiles["open_delta"] == -1


def test_stages_without_a_forecast_are_judged_on_sla_used():
    # Medium/Medium SLA = 18 business days.
    rows = _history() + [
        _issue("V-OK", status="Validating", closed_days_ago=None, start_days_ago=3),
        _issue("V-RISK", status="Validating", closed_days_ago=None, start_days_ago=22),
        _issue("V-LATE", status="Validating", closed_days_ago=None, start_days_ago=40),
        _issue("V-NONE", status="Validating", closed_days_ago=None, start_days_ago=None),
    ]
    open_df = es.build_executive_summary_data(pd.DataFrame(rows))["open_df"].set_index("key")
    assert open_df.loc["V-OK", "risk"] == "On Track"
    assert open_df.loc["V-RISK", "risk"] == "At Risk"
    assert open_df.loc["V-LATE", "risk"] == "Breached"
    assert open_df.loc["V-NONE", "risk"] == "Not Assessed"
    assert set(open_df["stage"]) == {"Validating"}


def test_attention_list_puts_breached_high_priority_first_with_a_reason():
    rows = _history() + [
        _issue("LOW-LATE", status="Validating", closed_days_ago=None, start_days_ago=120, priority="Low"),
        _issue("HIGH-LATE", status="Validating", closed_days_ago=None, start_days_ago=40, priority="High"),
        _issue("HELD", status="On Hold", closed_days_ago=None, start_days_ago=2, target_end_in_days=-3),
        _issue("FINE", status="Validating", closed_days_ago=None, start_days_ago=2),
    ]
    data = es.build_executive_summary_data(pd.DataFrame(rows))
    table = data["attention_df"]
    keys = table["Ticket"].str.split("/").str[-1].tolist()
    assert keys[:2] == ["HIGH-LATE", "LOW-LATE"]
    assert "SLA breached" in table["Why it needs attention"].iloc[0]
    assert "HELD" in keys and "past its Target End Date" in table.set_index(pd.Index(keys)).loc["HELD", "Why it needs attention"]
    assert "FINE" not in keys
    assert data["attention_total"] == 3


def test_sla_headline_ignores_the_partial_current_month():
    trend = pd.DataFrame({"label": ["Jul 2026", "Aug 2026", "Sep 2026 (so far)"],
                          "met": [0.80, 0.95, 0.10], "tickets": [100, 100, 3]})
    tiles = {"completed_change": None, "net_flow": 0, "closed": 0, "created": 0, "blocked": 0, "on_hold": 0,
             "completed": 0}
    open_df = pd.DataFrame({"risk": []})
    lines = es._headlines(tiles, trend, {}, 7, open_df, None)
    assert "**95%** in Aug 2026" in lines[0][1] and "up 15 pts from Jul 2026" in lines[0][1]


def test_flow_counts_every_closing_outcome():
    rows = _history() + [
        _issue("D", closed_days_ago=10), _issue("W", status="Will Not Do", closed_days_ago=10),
        _issue("RB", status="❌ Rolled Back", closed_days_ago=10),
    ]
    flow = es.build_executive_summary_data(pd.DataFrame(rows))["flow_df"]
    assert (flow["Closed"] == flow[["Done", "Released", "Will Not Do", "Rolled Back"]].sum(axis=1)).all()
    assert flow["Will Not Do"].sum() == 1 and flow["Rolled Back"].sum() == 1


def test_release_management_tickets_are_never_judged_against_the_pe_sla():
    car = dict(issuetype="Change and Release")
    rows = _history() + [
        _issue("CAR-OPEN", status="Prepare Release", closed_days_ago=None, start_days_ago=200, **car),
        _issue("CAR-DONE", status="Done", closed_days_ago=2, start_days_ago=200, **car),
        _issue("PE-OPEN", status="Prepare Release", closed_days_ago=None, start_days_ago=200),
    ]
    df = pd.DataFrame(rows)
    df.loc[df["key"].str.startswith("CAR"), "project_name"] = "Release Management"
    data = es.build_executive_summary_data(df)
    open_df = data["open_df"].set_index("key")
    assert open_df.loc["CAR-OPEN", "risk"] == "Not Assessed"
    assert open_df.loc["PE-OPEN", "risk"] == "Breached"
    keys = data["attention_df"]["Ticket"].str.split("/").str[-1].tolist()
    assert "CAR-OPEN" not in keys and "PE-OPEN" in keys
    assert data["tiles"]["sla_judged"] == 0  # the only completed ticket this week is CAR: not counted
