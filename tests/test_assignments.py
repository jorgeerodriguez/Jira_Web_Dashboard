"""Suggested Assignments: the shared domain taxonomy and how owners are chosen.

Pinned: the Streamlit taxonomy stays identical to darkstar's Intake page (so both apps classify work the
same way); the domain expert is suggested when they can meet the SLA; someone available is suggested
when the expert cannot; departed members are never suggested; and the plan spreads work instead of
handing everything to one person.
"""
import json
import re
from datetime import timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from reports import domains

_HTML = Path(__file__).resolve().parent.parent / "darkstar" / "dashboards" / "intake.html"


def test_taxonomy_matches_darkstar_intake_page():
    html = _HTML.read_text(encoding="utf-8")
    patterns = json.loads(re.search(r"const DOMAIN_PATTERNS = (\{.*?\});\n", html).group(1))
    priority = json.loads(re.search(r"const DOMAIN_PRIORITY = (\[.*?\]);", html).group(1))
    group = json.loads(re.search(r"const DOMAIN_GROUP = (\{.*?\})", html).group(1))
    order = json.loads(re.search(r"const GROUP_ORDER = (\[.*?\]);", html).group(1))
    assert domains.DOMAIN_PATTERNS == patterns
    assert domains.DOMAIN_PRIORITY == priority
    assert domains.DOMAIN_GROUP == group
    assert domains.GROUP_ORDER == order
    # A group missing from GROUP_ORDER would silently drop its domains from the Intake SME matrix.
    assert set(group.values()) | {"Other"} <= set(order)
    assert domains.group_of("Upwind") == "Security" and domains.group_of("CloudFront") == "AWS"


def test_tagging_and_primary_domain():
    tags = domains.tag("Grant IAM role for GKE workload identity")
    assert "IAM/RBAC" in tags and "GKE" in tags
    assert domains.primary(tags) == "GKE"                  # niche domains outrank broad ones
    assert domains.primary(domains.tag("Update the onboarding wiki")) is None


# ── Suggester ──────────────────────────────────────────────────────────────────

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _issue(key, assignee, summary, status="Done", done_days_ago=10, work_days=2, priority="Medium", size="Small",
           start_in_days=None):
    done = None if status != "Done" else _TODAY - pd.Timedelta(days=done_days_ago)
    anchor = done if done is not None else _TODAY
    start = (anchor - pd.Timedelta(days=work_days)) if start_in_days is None else _TODAY + pd.Timedelta(days=start_in_days)
    return {
        "key": key, "status": status, "issuetype": "Story", "project_name": "DevOps", "assignee_name": assignee,
        "priority_name": priority, "estimated_size_name": size, "business_lead": "Lead", "summary": summary,
        "created": (start - pd.Timedelta(days=1)).tz_localize(_LOCAL), "updated": anchor.tz_localize(_LOCAL),
        "status_category_changed": done.tz_localize(_LOCAL).tz_convert("UTC") if done is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"), "target_end_date": pd.NaT, "comments": [],
    }


def _team(extra=(), ana_queue=0):
    rows = [_issue(f"A-{i}", "Ana", "BigQuery reservation change", done_days_ago=5 + i) for i in range(12)]
    rows += [_issue(f"B-{i}", "Ben", "Update the onboarding wiki", done_days_ago=5 + i) for i in range(12)]
    rows += [_issue(f"C-{i}", "Cara", "Update the onboarding wiki", done_days_ago=5 + i) for i in range(12)]
    # A queue of higher-priority work already assigned to Ana, ahead of anything new in ATC order.
    rows += [_issue(f"AQ-{i}", "Ana", "BigQuery dataset", status="To Do", priority="High", start_in_days=0)
             for i in range(ana_queue)]
    return rows + list(extra)


@pytest.fixture
def asg():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    pytest.importorskip("streamlit")
    from reports import assignment_report
    return assignment_report


def _plan(asg, rows):
    out = asg.build_assignment_visuals(pd.DataFrame(rows))
    return out, out["plan_df"].assign(key=lambda d: d["Ticket"].str.split("/").str[-1]).set_index("key")


def test_domain_expert_is_suggested_when_available(asg):
    out, plan = _plan(asg, _team([_issue("NEW", "Unassigned", "BigQuery slot reservation", status="To Do", start_in_days=1)]))
    assert plan.loc["NEW", "Suggested"] == "Ana"
    assert plan.loc["NEW", "Domain"] == "BigQuery/Data"
    assert "BigQuery/Data" in plan.loc["NEW", "Why"]
    assert out["options"]["NEW"]["Person"].iloc[0] == "Ana"


def test_someone_available_is_suggested_when_the_expert_cannot_meet_the_sla(asg):
    # Medium/Small SLA = 15 business days; Ana has ~12 High tickets queued ahead of it.
    new = _issue("NEW", "Unassigned", "BigQuery reservation", status="To Do", priority="Medium", size="Small",
                 start_in_days=0)
    out, plan = _plan(asg, _team([new], ana_queue=12))
    options = out["options"]["NEW"].set_index("Person")
    assert options.loc["Ana", "Fits SLA"] == "✖"
    assert plan.loc["NEW", "Suggested"] != "Ana"
    assert plan.loc["NEW", "SLA Fit"] == "✓"


def test_departed_members_are_never_suggested(asg, monkeypatch):
    monkeypatch.setattr(asg, "DEPARTED_MEMBERS", {"ana"})
    out, plan = _plan(asg, _team([_issue("NEW", "Unassigned", "BigQuery slot reservation", status="To Do", start_in_days=1)]))
    assert "Ana" not in set(plan[["Suggested", "Backup", "Stretch"]].values.ravel())
    assert "Ana" not in set(out["options"]["NEW"]["Person"])


def test_plan_spreads_work_across_the_team(asg):
    queue = [_issue(f"Q-{i}", "Unassigned", "Update the onboarding wiki", status="To Do", start_in_days=1) for i in range(6)]
    out, plan = _plan(asg, _team(queue))
    assert plan["Suggested"].nunique() >= 2
    assert plan["Suggested"].value_counts().max() <= 4
    assert out["kpis"]["to_assign"] == 6
