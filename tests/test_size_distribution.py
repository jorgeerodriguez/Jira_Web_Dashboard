"""Distribution of Ticket by Estimated Size: coverage, the sizing guide, accuracy and the action lists.

Pins what the page tells the team to change: coverage counts PE tickets only; the sizing guide comes from
the team's own completed work (with a fallback when there is too little); each completed ticket is
right-sized, undersized or oversized against it; open tickets without a size and in-progress tickets
running past their size's range are listed; and recommendations appear only when the data supports them.
"""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")
pytest.importorskip("streamlit")

from reports import estimated_size_distribution_report as sz  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _issue(key, size="Small", status="Done", work_days=1, closed_days_ago=5, created_days_ago=None,
           issuetype="Story", project="DevOps", assignee="Ana", priority="Medium"):
    closed = None if status != "Done" else _TODAY - pd.Timedelta(days=closed_days_ago)
    anchor = closed if closed is not None else _TODAY
    start = anchor - pd.Timedelta(days=work_days)
    created = (_TODAY - pd.Timedelta(days=created_days_ago)) if created_days_ago is not None else start - pd.Timedelta(days=1)
    return {
        "key": key, "status": status, "issuetype": issuetype, "project_name": project, "assignee_name": assignee,
        "priority_name": priority, "estimated_size_name": size, "business_lead": "Lead", "summary": key,
        "created": created.tz_localize(_LOCAL), "updated": anchor.tz_localize(_LOCAL),
        "status_category_changed": closed.tz_localize(_LOCAL).tz_convert("UTC") if closed is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"), "target_end_date": pd.NaT,
    }


def test_guide_is_derived_from_typical_work_with_a_fallback():
    done = pd.DataFrame({"size": ["Small"] * 10 + ["Medium"] * 10 + ["Large"] * 10 + ["XL"] * 10,
                         "work_bd": [1] * 10 + [4] * 10 + [9] * 10 + [25] * 10})
    guide, from_data = sz.derive_guide(done)
    assert from_data
    assert guide == {"Small": (0, 2), "Medium": (3, 6), "Large": (7, 15), "XL": (16, None)}   # sqrt(1*4)=2, sqrt(36)=6, sqrt(225)=15
    sparse = done[done["size"] != "XL"].head(15)
    guide, from_data = sz.derive_guide(sparse)
    assert guide["XL"][1] is None and guide["Small"][0] == 0
    assert all(guide[a][1] < guide[b][0] for a, b in zip(sz.SIZES, sz.SIZES[1:]))           # bands never overlap


def test_size_fit_labels():
    guide = sz.DEFAULT_GUIDE_BD
    assert sz.size_fit("Medium", 4, guide) == "Right size"
    assert sz.size_fit("Medium", 9, guide) == "Undersized"
    assert sz.size_fit("Medium", 1, guide) == "Oversized"
    assert sz.size_fit("XL", 100, guide) == "Right size"
    assert sz.size_fit("Unestimated", 3, guide) is None


def test_coverage_action_lists_and_scope():
    rows = [_issue(f"S-{i}", "Small", work_days=1) for i in range(12)]
    rows += [_issue("OPEN-SIZED", "Medium", status="To Do", created_days_ago=3),
             _issue("OPEN-UNSIZED", None, status="To Do", created_days_ago=40),
             _issue("RUNNING-LONG", "Small", status="In Progress", work_days=20),
             _issue("FEATURE", None, status="To Do", issuetype="Feature"),
             _issue("CAR", None, status="Plan Release", issuetype="Change and Release", project="Release Management")]
    out = sz.build_estimated_size_distribution_visuals(pd.DataFrame(rows))
    k = out["kpis"]
    assert k["open_total"] == 3 and k["open_unsized"] == 1           # Feature and CAR are out of scope
    assert k["coverage_open"] == pytest.approx(2 / 3)
    assert out["needs_size_df"]["Ticket"].str.endswith("OPEN-UNSIZED").all()
    undersized = out["undersized_df"]
    assert undersized["Ticket"].str.endswith("RUNNING-LONG").all()
    assert undersized["Suggested Size"].iloc[0] != "Small"


def test_recommendations_follow_the_data():
    well_sized = [_issue(f"S-{i}", "Small", work_days=1, created_days_ago=5) for i in range(12)]
    out = sz.build_estimated_size_distribution_visuals(pd.DataFrame(well_sized))
    assert not any("Size at intake" in r for r in out["recommendations"])
    unsized = well_sized + [_issue(f"U-{i}", None, work_days=1, created_days_ago=5) for i in range(12)]
    recs = sz.build_estimated_size_distribution_visuals(pd.DataFrame(unsized))["recommendations"]
    assert any("Size at intake" in r for r in recs)
    assert any("Revisit the SLA for unsized tickets" in r for r in recs)      # unsized work looks like Small
