"""Distribution of Ticket's Age: open-ticket age against SLA, silence, and the weekly age trend.

Pins the rules behind the page: open tickets only and tickets-only, SLA use counted from Target start
(never for Release Management CAR tickets), silence measured from the last human comment or from
creation when nobody has commented, past-SLA tickets listed first, and the age trend reconstructed
from created/closed dates.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import distribution_of_tickets_report as dist  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _issue(key, status="To Do", created_days_ago=30, start_days_ago=None, closed_days_ago=None, priority="Medium",
           size="Medium", issuetype="Story", comment_days_ago=None, project="DevOps"):
    created = _TODAY - pd.Timedelta(days=created_days_ago)
    closed = None if closed_days_ago is None else _TODAY - pd.Timedelta(days=closed_days_ago)
    start = None if start_days_ago is None else _TODAY - pd.Timedelta(days=start_days_ago)
    comments = [] if comment_days_ago is None else [{
        "author": "Ana", "body": "update",
        "created": (_TODAY - pd.Timedelta(days=comment_days_ago)).tz_localize(_LOCAL).tz_convert("UTC")}]
    return {
        "key": key, "status": status, "issuetype": issuetype, "project_name": project, "assignee_name": "Ana",
        "priority_name": priority, "estimated_size_name": size, "business_lead": "Lead", "summary": key,
        "created": created.tz_localize(_LOCAL), "updated": (closed or _TODAY).tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC") if closed is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC") if start is not None else pd.NaT,
        "target_end_date": pd.NaT, "comments": comments,
    }


def _open_by_key(rows):
    out = dist.build_distribution_visuals(pd.DataFrame(rows))
    return out, out["open_df"].set_index("key")


def test_only_open_tickets_and_no_features_or_initiatives():
    out, open_df = _open_by_key([
        _issue("T-1"), _issue("F-1", issuetype="Feature"), _issue("I-1", issuetype="Initiative"),
        _issue("D-1", status="Done", closed_days_ago=1),
    ])
    assert out["open_count"] == 1 and list(open_df.index) == ["T-1"]


def test_sla_use_counts_from_target_start_and_skips_car_tickets():
    # Medium/Medium SLA = 18 business days; started ~6 weeks ago = past SLA.
    out, open_df = _open_by_key([
        _issue("LATE", status="In Progress", start_days_ago=42),
        _issue("FRESH", status="In Progress", start_days_ago=1),
        _issue("NOSTART", status="In Progress"),
        _issue("CAR", status="Plan Release", start_days_ago=200, issuetype="Change and Release",
               project="Release Management"),
    ])
    assert open_df.loc["LATE", "sla_used"] > 1
    assert open_df.loc["FRESH", "sla_used"] < 0.2
    assert pd.isna(open_df.loc["NOSTART", "sla_used"])
    assert pd.isna(open_df.loc["CAR", "sla_used"])
    assert out["past_sla"] == 1
    assert out["tickets_df"]["Ticket"].str.endswith("LATE").iloc[0]  # past-SLA tickets listed first


def test_silence_is_measured_from_the_last_human_comment_or_creation():
    out, open_df = _open_by_key([
        _issue("TALKED", created_days_ago=60, comment_days_ago=0),
        _issue("QUIET", created_days_ago=60, comment_days_ago=30),
        _issue("NEVER", created_days_ago=30),
    ])
    assert open_df.loc["TALKED", "silent_bd"] == 0
    assert open_df.loc["QUIET", "silent_bd"] >= dist.SILENT_THRESHOLD_BD
    assert open_df.loc["NEVER", "silent_bd"] == open_df.loc["NEVER", "age_bd"]
    assert out["silent_count"] == 2


def test_age_trend_reconstructs_what_was_open_each_week():
    out = dist.build_distribution_visuals(pd.DataFrame([
        _issue("OPEN", created_days_ago=200),
        _issue("CLOSED-LONG-AGO", status="Done", created_days_ago=200, closed_days_ago=150),
        _issue("NEW", created_days_ago=1),
    ]))
    trend = out["trend_df"]
    assert len(trend) == dist.TREND_WEEKS
    # Every week in the window: OPEN was open; CLOSED-LONG-AGO was not; NEW only in the latest week.
    assert trend["open"].iloc[0] == 1
    assert trend["open"].iloc[-1] in (1, 2)  # NEW counts only if the latest week end is on/after its creation
