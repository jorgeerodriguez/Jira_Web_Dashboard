"""Backlog forecast: which tickets are in scope, how each person's queue is simulated, and when a
ticket can or cannot be judged against its SLA.

The page projects when each To Do / Tech Discovery Required ticket starts and finishes by queueing
it behind the assignee's In Progress work, in the Apparent Tardiness Cost order the Personal
Dashboard uses. The SLA clock starts at Target start, so a ticket without one (or without an
assignee) is "Not assessed" rather than given a made-up date.
"""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")

from reports import backlog_report as br  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _issue(key, status="To Do", issuetype="Story", assignee="Ana", priority="Medium", size="Medium",
           start_in_days=5, target_end_in_days=30, created_days_ago=3, done_days_ago=None, duration_days=6):
    if done_days_ago is not None:
        done = _TODAY - pd.Timedelta(days=done_days_ago)
        start = done - pd.Timedelta(days=duration_days)
    else:
        done = None
        start = None if start_in_days is None else _TODAY + pd.Timedelta(days=start_in_days)
    created = (start if start is not None else _TODAY) - pd.Timedelta(days=created_days_ago)
    return {
        "key": key, "status": status, "issuetype": issuetype, "assignee_name": assignee,
        "priority_name": priority, "estimated_size_name": size,
        "created": created.tz_localize(_LOCAL),
        "updated": (done or _TODAY).tz_localize(_LOCAL),
        "status_category_changed": done.tz_localize(_LOCAL).tz_convert("UTC") if done is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC") if start is not None else pd.NaT,
        "target_end_date": (pd.Timestamp((_TODAY + pd.Timedelta(days=target_end_in_days)).date(), tz="UTC")
                            if target_end_in_days is not None else pd.NaT),
        "days_old": created_days_ago, "summary": key, "creator_name": "Creator", "business_lead": "Lead",
    }


def _history(n=40, assignee="Ana"):
    return [_issue(f"DONE-{assignee}-{i}", status="Done", assignee=assignee, done_days_ago=10 + i) for i in range(n)]


def _by_key(out):
    detail = out["forecast_df"].copy()
    detail["key"] = detail["Ticket"].str.split("/").str[-1]
    return detail.set_index("key")


def test_queue_waits_for_a_free_slot_and_for_target_start():
    fixed = lambda value: np.full(br.SIMULATIONS, float(value))  # noqa: E731
    # One slot, busy for 5 days: the queue runs back to back after it.
    starts, finishes, clear = br._simulate_queue([fixed(5)], [fixed(3), fixed(2)], [0, 0], slots=1)
    assert starts[:, 0].tolist() == [5, 8] and finishes[:, 0].tolist() == [8, 10] and clear[0] == 5
    # Two slots: the first backlog ticket starts now, the second when the shorter job frees a slot.
    starts, _, _ = br._simulate_queue([fixed(5)], [fixed(3), fixed(2)], [0, 0], slots=2)
    assert starts[:, 0].tolist() == [0, 3]
    # A ticket never starts before its Target start.
    starts, _, _ = br._simulate_queue([], [fixed(3)], [10], slots=1)
    assert starts[0, 0] == 10


def test_only_backlog_tickets_are_forecast_not_features_initiatives_or_other_statuses():
    df = pd.DataFrame(_history() + [
        _issue("DEVOPS-1"),
        _issue("DEVOPS-2", status="Tech Discovery Required"),
        _issue("DEVOPS-3", issuetype="Feature"),
        _issue("DEVOPS-4", issuetype="Initiative"),
        _issue("DEVOPS-5", status="In Progress", start_in_days=-2),
        _issue("DEVOPS-6", assignee="Denys Loboda"),
    ])
    out = br.build_backlog_visuals(df)
    assert out["total_backlog"] == 2
    assert set(_by_key(out).index) == {"DEVOPS-1", "DEVOPS-2"}
    assert len(out["tickets_df"]) == 2


def test_tickets_without_assignee_or_target_start_are_not_assessed_and_say_why():
    df = pd.DataFrame(_history() + [
        _issue("DEVOPS-1", assignee="Unassigned"),
        _issue("DEVOPS-2", start_in_days=None, size=None),
        _issue("DEVOPS-3"),
    ])
    detail = _by_key(br.build_backlog_visuals(df))
    assert detail.loc["DEVOPS-1", "SLA Status"] == br.RISK_LABELS[br.NOT_ASSESSED]
    assert "Assignee" in detail.loc["DEVOPS-1", "Missing"]
    assert pd.isna(detail.loc["DEVOPS-1", "Projected Start"])
    assert detail.loc["DEVOPS-2", "SLA Status"] == br.RISK_LABELS[br.NOT_ASSESSED]
    assert detail.loc["DEVOPS-2", "Missing"] == "Size, Target start"
    assert detail.loc["DEVOPS-3", "SLA Status"] != br.RISK_LABELS[br.NOT_ASSESSED]
    assert detail.loc["DEVOPS-3", "Missing"] == "—"


def test_queue_follows_the_personal_dashboard_atc_order():
    df = pd.DataFrame(_history() + [
        _issue("DEVOPS-LOW", priority="Low", start_in_days=0),
        _issue("DEVOPS-URGENT", priority="Urgent", start_in_days=0),
    ])
    detail = _by_key(br.build_backlog_visuals(df))
    assert detail.loc["DEVOPS-URGENT", "Queue #"] == 1
    assert detail.loc["DEVOPS-LOW", "Queue #"] == 2
    assert detail.loc["DEVOPS-URGENT", "Projected Start"] <= detail.loc["DEVOPS-LOW", "Projected Start"]


def test_sla_counts_from_target_start_so_a_long_overdue_start_is_breached():
    # Urgent/Small SLA is 1 business day; Target start was a month ago.
    df = pd.DataFrame(_history() + [_issue("DEVOPS-1", priority="Urgent", size="Small", start_in_days=-30)])
    out = br.build_backlog_visuals(df)
    row = _by_key(out).loc["DEVOPS-1"]
    assert row["SLA Status"] == br.RISK_LABELS["Breached"]
    assert out["should_have_started"] == 1
    assert row["Start Slip (bd)"] > 15


def test_readiness_and_waiting_counts():
    df = pd.DataFrame(_history() + [
        _issue("DEVOPS-READY"),
        _issue("DEVOPS-NOSIZE", size=None),
        _issue("DEVOPS-OLD", priority="Urgent", size="Small", created_days_ago=20),
    ])
    out = br.build_backlog_visuals(df)
    assert out["ready"] == 2
    assert out["waiting_past_sla"] == 1
    assert _by_key(out).loc["DEVOPS-OLD", "Waiting (bd)"] > 1
