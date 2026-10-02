"""SLA page: the breach rates against the 10% goal and the two action tables.

These are the numbers the team is measured on, so the rules are pinned: the SLA is the Priority x Size
business-day table from Target start; "SLA came due" counts a ticket still open past its due date as
breached; "completed late" judges Done tickets by completion; tickets closed without delivery are never
judged; CAR tickets and Features are out of scope; filters narrow everything; and both tables put the
most urgent tickets first with their latest human comment.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import service_level_agreement_report as sla  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _issue(key, status="Done", start_days_ago=10, closed_days_ago=None, priority="High", size="Small",
           assignee="Ana", issuetype="Story", project="DevOps", comment=None):
    # High/Small SLA = 7 business days from Target start.
    start = _TODAY - pd.Timedelta(days=start_days_ago)
    closed = None if closed_days_ago is None else _TODAY - pd.Timedelta(days=closed_days_ago)
    comments = [] if comment is None else [{"author": "Lead", "body": comment,
                                            "created": (_TODAY - pd.Timedelta(days=1)).tz_localize(_LOCAL)}]
    return {
        "key": key, "status": status, "issuetype": issuetype, "project_name": project, "assignee_name": assignee,
        "priority_name": priority, "estimated_size_name": size, "business_lead": "Lead A", "summary": key,
        "created": (start - pd.Timedelta(days=1)).tz_localize(_LOCAL), "updated": (closed or _TODAY).tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC") if closed is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"), "target_end_date": pd.NaT, "comments": comments,
    }


def _build(rows, **kwargs):
    return sla.build_sla_visuals(pd.DataFrame(rows), time_period_days=30, **kwargs)


def test_sla_came_due_counts_open_overdue_tickets_as_breached():
    rows = [
        _issue("ON-TIME", start_days_ago=20, closed_days_ago=18),           # done within 7 bd
        _issue("LATE", start_days_ago=25, closed_days_ago=5),                # done after its due date
        _issue("OPEN-LATE", status="In Progress", start_days_ago=20),        # still open, past due
        _issue("WND", status="Will Not Do", start_days_ago=20, closed_days_ago=2),  # never judged
    ]
    k = _build(rows)["kpis"]
    assert k["due_n"] == 3 and k["due_breached"] == 2
    assert k["due_rate"] == pytest.approx(2 / 3)
    assert k["done_n"] == 2 and k["done_late"] == 1            # completed late: Done tickets only
    assert k["open_breached"] == 1


def test_car_and_features_are_out_of_scope():
    rows = [
        _issue("PE", start_days_ago=20, closed_days_ago=18),
        _issue("CAR", start_days_ago=25, closed_days_ago=5, issuetype="Change and Release", project="Release Management"),
        _issue("FEAT", start_days_ago=25, closed_days_ago=5, issuetype="Feature"),
    ]
    k = _build(rows)["kpis"]
    assert k["due_n"] == 1 and k["due_breached"] == 0


def test_filters_narrow_rates_and_tables():
    rows = [_issue("A-LATE", status="In Progress", start_days_ago=20, assignee="Ana"),
            _issue("B-LATE", status="In Progress", start_days_ago=20, assignee="Ben")]
    out = _build(rows, filters={"assignees": ["Ben"]})
    assert out["kpis"]["open_breached"] == 1
    assert out["detail_df"]["Ticket"].str.endswith("B-LATE").all()


def test_detail_table_is_most_urgent_first_with_latest_comment():
    rows = [
        _issue("LITTLE-LATE", status="Validating", start_days_ago=12),
        _issue("VERY-LATE", status="Validating", start_days_ago=40, comment="Any update? [~accountid:abc] thanks"),
        _issue("FINE", status="Validating", start_days_ago=1),
    ]
    detail = _build(rows)["detail_df"]
    keys = detail["Ticket"].str.split("/").str[-1].tolist()
    assert keys[:2] == ["VERY-LATE", "LITTLE-LATE"]
    assert "FINE" not in keys
    assert detail.loc[0, "Days to SLA (bd)"] < detail.loc[1, "Days to SLA (bd)"] < 0
    assert detail.loc[0, "Latest Comment"].startswith("Lead: Any update?")
    assert "accountid" not in detail.loc[0, "Latest Comment"]
    with_all = _build(rows, include_on_track=True)["detail_df"]
    assert "FINE" in with_all["Ticket"].str.split("/").str[-1].tolist()


def test_breached_table_lists_open_first_then_completed_late():
    rows = [_issue("DONE-LATE", start_days_ago=25, closed_days_ago=5),
            _issue("OPEN-LATE", status="Blocked", start_days_ago=20)]
    table = _build(rows)["breached_df"]
    assert table["State"].tolist() == ["Open: still breached", "Completed late"]
    only_open = _build(rows, include_completed_late=False)["breached_df"]
    assert only_open["Ticket"].str.endswith("OPEN-LATE").all()
