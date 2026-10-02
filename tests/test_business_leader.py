"""Distribution per Business Leader: the service each requesting business lead gets.

Pins the scope and labels the page depends on: PE tickets only (Release Management CAR tickets left
out, with no reassignment of their business lead), PE's own work shown as one internal row that the
charts leave out unless asked, and the scorecard's requested / delivered / wait / SLA numbers.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import distribution_by_business_leader as biz  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()
_MONTH = _TODAY.strftime("%Y-%m")


def _issue(key, lead, status="Done", created_days_ago=0, closed_days_ago=0, start_days_ago=0, issuetype="Story",
           project="DevOps", priority="Medium", size="Medium"):
    created = _TODAY - pd.Timedelta(days=created_days_ago)
    closed = None if closed_days_ago is None else _TODAY - pd.Timedelta(days=closed_days_ago)
    start = _TODAY - pd.Timedelta(days=start_days_ago)
    return {
        "key": key, "status": status, "issuetype": issuetype, "project_name": project, "assignee_name": "Ana",
        "business_lead": lead, "priority_name": priority, "estimated_size_name": size, "summary": key,
        "created": created.tz_localize(_LOCAL), "updated": (closed or _TODAY).tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC") if closed is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"), "target_end_date": pd.NaT,
    }


def _scorecard(rows, **kwargs):
    out = biz.build_business_leader_visuals(pd.DataFrame(rows), start_month=_MONTH, end_month=_MONTH, **kwargs)
    return out, out["scorecard_df"].set_index("Business Lead")


def test_car_tickets_are_left_out_and_never_reassigned():
    out, card = _scorecard([
        _issue("DEVOPS-1", "Ben Bonora"),
        _issue("CAR-1", None, status="Released Successfully to Production", issuetype="Change and Release",
               project="Release Management"),
    ])
    assert out["kpis"]["requested"] == 1
    assert "Jorge Rodriguez" not in card.index and biz.INTERNAL_LABEL not in card.index
    assert biz.UNKNOWN_LABEL not in card.index


def test_pe_internal_work_is_one_row_and_left_out_of_charts_by_default():
    rows = [_issue("D-1", "Jorge Rodriguez"), _issue("D-2", "jorge rodriguez"), _issue("D-3", "Ben Bonora"),
            _issue("D-4", None)]
    out, card = _scorecard(rows)
    assert card.loc[biz.INTERNAL_LABEL, "Requested"] == 2
    assert list(card.index)[-2:] == [biz.INTERNAL_LABEL, biz.UNKNOWN_LABEL]  # requesting leads first
    charted = set(out["demand_fig"].data[-1].y)
    assert charted == {"Ben Bonora"}
    with_internal, _ = _scorecard(rows, include_internal=True)
    assert biz.INTERNAL_LABEL in set(with_internal["demand_fig"].data[-1].y)
    assert out["kpis"]["internal_share"] == pytest.approx(0.5)
    assert out["kpis"]["unknown_share"] == pytest.approx(0.25)


def test_scorecard_counts_requested_delivered_wait_and_sla():
    # Medium/Medium SLA = 18 business days from Target start.
    rows = [
        _issue("A-1", "Ben Bonora", created_days_ago=0, closed_days_ago=None, status="To Do"),   # requested, open
        _issue("A-2", "Ben Bonora", created_days_ago=0, closed_days_ago=0),                      # met SLA
        _issue("A-3", "Ben Bonora", created_days_ago=0, closed_days_ago=0, start_days_ago=60),   # missed SLA
        _issue("A-4", "Ben Bonora", status="Will Not Do", created_days_ago=0, closed_days_ago=0),
        _issue("F-1", "Ben Bonora", issuetype="Feature"),                                        # never counted
    ]
    _, card = _scorecard(rows)
    row = card.loc["Ben Bonora"]
    assert row["Requested"] == 4
    assert row["Delivered"] == 2
    assert row["Won't Do"] == 1
    assert row["SLA Met %"] == 50
    assert row["Open Now"] == 1
    assert row["Median Wait (bd)"] == 0
