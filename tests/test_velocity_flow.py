"""Velocity page: flow times, what we can promise, and the SLA reality check.

Pins the definitions leadership will quote: tickets are picked by *completion* date (an old ticket
finished last week counts, so slow work is not hidden); lead = created -> Done, waiting = created ->
Target start, in progress = Target start -> Done, all in business days, with no waiting or work
before the request existed; and the SLA reality check is the 85th-percentile time in progress over
the cell's SLA, flagged when the sample is small.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import in_progress_report as ipr  # noqa: E402
from reports import velocity_report as vel  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()
# A Wednesday at least a week back, so every interval below is whole business weeks.
_WED = _TODAY - pd.Timedelta(days=7 + (_TODAY.weekday() - 2) % 7)


def _done(key, created_days_before, start_days_before, priority="Medium", size="Small", issuetype="Story",
          project="DevOps", closed=_WED):
    created = closed - pd.Timedelta(days=created_days_before)
    start = None if start_days_before is None else closed - pd.Timedelta(days=start_days_before)
    return {
        "key": key, "status": "Done", "issuetype": issuetype, "project_name": project, "assignee_name": "Ana",
        "priority_name": priority, "estimated_size_name": size, "business_lead": "Lead", "summary": key,
        "created": created.tz_localize(_LOCAL), "updated": closed.tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC"),
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC") if start is not None else pd.NaT,
        "target_end_date": pd.NaT,
    }


def _bd(start_days_before: int, end_days_before: int = 0) -> int:
    """Business days between two days relative to _WED, on the company calendar (holidays excluded)."""
    import numpy as np
    start = (_WED - pd.Timedelta(days=start_days_before)).date()
    end = (_WED - pd.Timedelta(days=end_days_before)).date()
    return int(np.busday_count(start, end, holidays=ipr._calendar_holidays(_TODAY)))


def _flow(rows):
    return vel.build_velocity_visuals(pd.DataFrame(rows))["flow_df"].set_index("key")


def test_lead_waiting_and_in_progress_are_business_days():
    flow = _flow([_done("A", created_days_before=14, start_days_before=7)])
    assert flow.loc["A", "lead_bd"] == _bd(14)
    assert flow.loc["A", "wait_bd"] == _bd(14, 7)
    assert flow.loc["A", "work_bd"] == _bd(7)


def test_no_waiting_or_work_before_the_request_existed():
    flow = _flow([_done("EARLY", created_days_before=7, start_days_before=21)])   # Target start before creation
    assert flow.loc["EARLY", "wait_bd"] == 0
    assert flow.loc["EARLY", "work_bd"] == flow.loc["EARLY", "lead_bd"] == _bd(7)


def test_tickets_are_picked_by_completion_date_and_scope_is_pe_only():
    rows = [
        _done("OLD", created_days_before=200, start_days_before=10),          # created long ago, done recently
        _done("F", 7, 2, issuetype="Feature"),
        _done("CAR", 7, 2, issuetype="Change and Release", project="Release Management"),
        _done("LONG-AGO", 7, 2, closed=_TODAY - pd.Timedelta(days=200)),       # done outside the window
    ]
    flow = _flow(rows)
    assert list(flow.index) == ["OLD"]


def test_sla_reality_is_p85_in_progress_over_the_cell_sla():
    # High/Small SLA = 7 business days; every ticket spent _bd(14) (about 10) business days in progress.
    rows = [_done(f"H-{i}", 14, 14, priority="High", size="Small") for i in range(6)]
    rows += [_done("U-1", 7, 7, priority="Urgent", size="Small")]               # Urgent/Small SLA = 1, n=1
    table = vel.build_velocity_visuals(pd.DataFrame(rows))["sla_reality_df"].set_index(["Priority", "Size"])
    assert table.loc[("High", "Small"), "P85 / SLA %"] == round(_bd(14) / 7 * 100)
    assert table.loc[("Urgent", "Small"), "Tickets"] == 1   # shown, flagged as a small sample on the chart


def test_promise_kpis_are_lead_time_percentiles():
    rows = [_done(f"T-{i}", created_days_before=7 * (i + 1), start_days_before=None) for i in range(10)]
    out = vel.build_velocity_visuals(pd.DataFrame(rows))
    kpis, lead = out["kpis"], out["flow_df"]["lead_bd"]
    assert kpis["delivered"] == 10
    # 1..10 weeks = 5..50 business days, less any company holidays in the span.
    assert all(5 * w - 2 <= v <= 5 * w for w, v in zip(range(1, 11), sorted(lead)))
    assert kpis["lead_p50"] == pytest.approx(lead.median())
    assert kpis["lead_p85"] == pytest.approx(lead.quantile(0.85))
    assert kpis["with_start_share"] == 0
