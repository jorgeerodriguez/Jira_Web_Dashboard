"""Blocked & On Hold page: both statuses are included and kept apart in every view.

The page was "Blocked" only. On Hold tickets are stalled too, so they now appear next to Blocked, with
their own KPI, their own colour in each chart and a State column in the table, never merged into one
count.
"""
import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")

from reports.blocked_report import build_blocked_visuals  # noqa: E402

_TODAY = pd.Timestamp.now(tz="UTC").normalize()


def _issue(key, status, days_left=10, priority="Medium", lead="Lead A"):
    return {"key": key, "status": status, "assignee_name": "Ana", "business_lead": lead,
            "priority_name": priority, "days_old": 5,
            "target_end_date": _TODAY + pd.Timedelta(days=days_left) if days_left is not None else pd.NaT}


def _frame():
    return pd.DataFrame([
        _issue("T-1", "Blocked", days_left=-2, priority="High"),
        _issue("T-2", "Blocked", days_left=3),
        _issue("T-3", "On Hold", days_left=20, lead="Lead B"),
        _issue("T-4", "on hold", days_left=None),
        _issue("T-5", "In Progress"),
    ])


def test_blocked_and_on_hold_are_counted_separately():
    out = build_blocked_visuals(_frame())
    assert out["total_blocked"] == 2
    assert out["total_on_hold"] == 2
    assert out["overdue_tickets"] == 1
    assert out["high_priority_tickets"] == 1


def test_table_has_a_state_column_blocked_first():
    detail = build_blocked_visuals(_frame())["detail_df"]
    assert detail["State"].tolist() == ["Blocked", "Blocked", "On Hold", "On Hold"]
    assert detail["Ticket"].str.endswith("T-1").iloc[0]  # most overdue Blocked ticket first
    assert "T-5" not in " ".join(detail["Ticket"])


def test_charts_split_every_bar_by_state():
    out = build_blocked_visuals(_frame())
    for fig in (out["blocked_fig"], out["risk_fig"]):
        assert [trace.name for trace in fig.data] == ["Blocked", "On Hold"]
        assert sum(sum(trace.x) for trace in fig.data) == 4


def test_only_on_hold_tickets_still_render():
    out = build_blocked_visuals(pd.DataFrame([_issue("T-1", "On Hold")]))
    assert out["total_blocked"] == 0 and out["total_on_hold"] == 1
    assert out["blocked_fig"] is not None
