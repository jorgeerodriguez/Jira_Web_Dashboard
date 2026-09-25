"""In Progress forecast: SLA clock, ticket scope, risk basis, and how history is weighted.

The report forecasts when each In Progress ticket finishes and compares that with its SLA (Priority x
Size, in business days from Target start) and its Target End Date. These tests pin the rules a reader
of the page relies on: which tickets are in scope, how the SLA due date is counted, which deadline the
risk is judged against, and that one slow ticket cannot label an assignee as slow on its own.
"""
from datetime import date, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")

from reports import in_progress_report as ipr  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _issue(key, status="In Progress", issuetype="Story", assignee="Ana", priority="Medium", size="Medium",
           start_days_ago=2, target_end_in_days=20, done_days_ago=None):
    start = _TODAY - pd.Timedelta(days=start_days_ago)
    done = None if done_days_ago is None else _TODAY - pd.Timedelta(days=done_days_ago)
    return {
        "key": key, "status": status, "issuetype": issuetype, "assignee_name": assignee,
        "priority_name": priority, "estimated_size_name": size,
        "created": (start - pd.Timedelta(days=1)).tz_localize(_LOCAL),
        "updated": (done or _TODAY).tz_localize(_LOCAL),
        "status_category_changed": done.tz_localize(_LOCAL).tz_convert("UTC") if done is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"),
        "target_end_date": pd.Timestamp((_TODAY + pd.Timedelta(days=target_end_in_days)).date(), tz="UTC"),
        "days_old": start_days_ago, "summary": key,
    }


def _history(n=40, assignee="Ana", priority="Medium", size="Medium", duration_days=6):
    return [_issue(f"DONE-{assignee}-{priority}-{i}", status="Done", assignee=assignee, priority=priority, size=size,
                   start_days_ago=10 + i + duration_days, done_days_ago=10 + i) for i in range(n)]


def test_sla_matrix_matches_the_published_table():
    assert ipr.SLA_BUSINESS_DAYS == {
        "Low/None": {"Small": 30, "Medium": 33, "Large": 37, "XL": 45},
        "Medium":   {"Small": 15, "Medium": 18, "Large": 22, "XL": 30},
        "High":     {"Small": 7,  "Medium": 10, "Large": 17, "XL": 22},
        "Urgent":   {"Small": 1,  "Medium": 4,  "Large": 8,  "XL": 16},
    }


def test_priority_and_size_normalisation():
    priorities = ipr._normalize_priority(pd.Series(["Urgent", "Highest", "High", "Medium", "Low", "None", None]))
    assert priorities.tolist() == ["Urgent", "Urgent", "High", "Medium", "Low/None", "Low/None", "Low/None"]
    sizes = ipr._normalize_size(pd.Series(["Small", "XL", "XLarge", "", None]))
    assert sizes.tolist() == ["Small", "XL", "XL", "Unestimated", "Unestimated"]


def test_sla_due_date_skips_weekends_and_company_holidays():
    hol = ipr._holidays(2026, 2026)
    # Thu 2026-07-02 + 1 business day: Fri 07-03 is Independence Day (observed), so Mon 07-06.
    due = ipr._add_busdays(pd.Series([pd.Timestamp("2026-07-02")]), pd.Series([1.0]), hol)
    assert due.iloc[0] == pd.Timestamp("2026-07-06")
    # Fri 2026-09-25 + 1 business day skips the weekend.
    due = ipr._add_busdays(pd.Series([pd.Timestamp("2026-09-25")]), pd.Series([1.0]), hol)
    assert due.iloc[0] == pd.Timestamp("2026-09-28")


def test_only_in_progress_tickets_are_forecast_not_features_initiatives_or_excluded_people():
    df = pd.DataFrame(_history() + [
        _issue("DEVOPS-1"),
        _issue("DEVOPS-2", issuetype="Feature"),
        _issue("DEVOPS-3", issuetype="Initiative"),
        _issue("DEVOPS-4", assignee="Denys Loboda"),
        _issue("DEVOPS-5", status="To Do"),
    ])
    out = ipr.build_in_progress_visuals(df)
    assert out["total_in_progress"] == 1
    assert out["forecast_df"]["Ticket"].str.endswith("DEVOPS-1").all()
    assert out["tickets_df"]["Ticket"].str.endswith("DEVOPS-1").all()


def test_unsized_ticket_uses_the_medium_sla_and_is_flagged():
    df = pd.DataFrame(_history() + [_issue("DEVOPS-1", priority="High", size=None)])
    row = ipr.build_in_progress_visuals(df)["forecast_df"].iloc[0]
    assert row["SLA (bd)"] == ipr.SLA_BUSINESS_DAYS["High"]["Medium"]
    assert "assumed" in row["Size"]


def test_risk_basis_picks_the_deadline_and_the_table_shows_both():
    # Long SLA (Low/Medium = 33 bd) but a Target End Date that has already passed.
    df = pd.DataFrame(_history(priority="Low") + [_issue("DEVOPS-1", priority="Low", target_end_in_days=-3)])
    by_sla = ipr.build_in_progress_visuals(df, risk_basis="SLA")
    by_target = ipr.build_in_progress_visuals(df, risk_basis="Target End Date")
    by_both = ipr.build_in_progress_visuals(df, risk_basis="Both (earliest deadline)")
    assert by_sla["breached"] == 0 and by_sla["on_track"] == 1
    assert by_target["breached"] == 1
    assert by_both["breached"] == 1
    row = by_sla["forecast_df"].iloc[0]
    assert row["SLA Status"] == ipr.RISK_LABELS["On Track"]
    assert row["Target End Status"] == ipr.RISK_LABELS["Breached"]


def test_one_slow_ticket_is_shrunk_toward_the_team():
    hol = ipr._calendar_holidays(_TODAY)
    rows = _history(n=40, assignee="Ana") + [
        _issue("SLOW-1", status="Done", assignee="Ben", start_days_ago=70, done_days_ago=10)]
    tickets = pd.DataFrame(rows)
    tickets["priority_bucket"] = ipr._normalize_priority(tickets["priority_name"])
    tickets["size"] = ipr._normalize_size(tickets["estimated_size_name"])
    history = ipr._build_history(tickets, _TODAY, hol)
    _, effects = ipr._assignee_effects(history)
    ben = history[history["assignee_name"] == "Ben"].iloc[0]
    raw = ben["log_duration"] - np.log1p(history[history["assignee_name"] == "Ana"]["duration_bd"].median())
    assert 0 < effects[("Ben", "Medium")] < 0.5 * raw


def test_ticket_older_than_all_history_leans_on_time_spent():
    df = pd.DataFrame(_history(duration_days=3) + [_issue("DEVOPS-1", start_days_ago=120)])
    row = ipr.build_in_progress_visuals(df)["forecast_df"].iloc[0]
    assert row["Based On"].startswith("Beyond history")
    assert row["Confidence"] == "Low"
    assert row["Forecast P85"] > row["Forecast P50"] >= _TODAY.date()
