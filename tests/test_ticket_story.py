"""Status of My Ticket: one ticket's status and history.

Pinned: lookup covers PE tickets plus CAR tickets linked to PE work (and no other CAR tickets); the loader keeps
assignee changes; status and assignee spans come from the change history; only finished work sent back counts as
rework ("Reviewing" is intake); the conversation log knows roles, reply gaps, themes and mentions; the verdict reads
right for closed and open tickets; and the page still works without change history.
"""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from data import fetch_change_history as fch

_LOCAL = timezone(timedelta(hours=-6))


def test_loader_keeps_assignee_changes_as_names():
    logs = [{"issueId": "1", "changeHistories": [{"created": 1789487114594, "author": {"displayName": "Lead"}, "items": [
        {"fieldId": "assignee", "from": "acc-1", "fromString": "Ana", "to": "acc-2", "toString": "Ben"}]}]}]
    rows = fch._parse_items(logs, {"1": "PE-1"})
    assert [(r["field"], r["frm"], r["to"]) for r in rows] == [("assignee", "Ana", "Ben")]


@pytest.fixture
def ts():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    pytest.importorskip("networkx")
    from reports import ticket_story
    return ticket_story


def _bd(ipr, n):
    today = ipr.today_local()
    return pd.Timestamp(np.busday_offset(today.date(), -n, roll="backward", holidays=ipr._calendar_holidays(today)))


def _issue(key, status, created, *, project="DevOps", issuetype="Story", assignee="Ana", reporter="Rae", start=None,
           done=None, comments=(), links=(), summary=None):
    return {"key": key, "id": key, "status": status, "issuetype": issuetype, "project_name": project,
            "assignee_name": assignee, "reporter_name": reporter, "priority_name": "Medium", "estimated_size_name": "Small",
            "business_lead": "Lead", "summary": summary or key, "created": created.tz_localize(_LOCAL),
            "updated": (done or created).tz_localize(_LOCAL),
            "status_category_changed": done.tz_localize(_LOCAL) + pd.Timedelta(hours=12) if done is not None else pd.NaT,
            "planned_start_date": pd.Timestamp(start.date(), tz="UTC") if start is not None else pd.NaT,
            "target_end_date": pd.NaT, "comments": list(comments), "dashboard_comments": [], "links": list(links),
            "parent": None}


def _h(key, day, field, frm, to, author="Lead"):
    return {"key": key, "at": (day + pd.Timedelta(hours=10)).tz_localize(_LOCAL).tz_convert("UTC"), "author": author,
            "field": field, "frm": frm, "to": to}


def _c(author, day, body="", hour=10, author_id=None):
    return {"author": author, "author_id": author_id, "body": body,
            "created": (day + pd.Timedelta(hours=hour)).tz_localize(_LOCAL).tz_convert("UTC")}


def test_lookup_covers_pe_and_linked_car_tickets_only(ts):
    from reports import in_progress_report as ipr
    d = lambda n: _bd(ipr, n)  # noqa: E731
    df = pd.DataFrame([
        _issue("DEVOPS-1", "To Do", d(5), links=[{"type": "Relates", "relation": "relates to", "key": "CAR-1"}]),
        _issue("CAR-1", "Plan Release", d(4), project="Release Management", issuetype="Change and Release"),
        _issue("CAR-2", "Plan Release", d(4), project="Release Management", issuetype="Change and Release",
               links=[{"type": "Blocks", "relation": "blocks", "key": "DEVOPS-1"}]),
        _issue("CAR-3", "Plan Release", d(4), project="Release Management", issuetype="Change and Release"),
    ])
    assert set(ts.searchable(df)["key"]) == {"DEVOPS-1", "CAR-1", "CAR-2"}


def test_story_timeline_rework_conversation_and_verdict(ts):
    from reports import in_progress_report as ipr
    d = lambda n: _bd(ipr, n)  # noqa: E731
    issue = _issue("DEVOPS-9", "In Progress", d(20), start=d(14), comments=[
        _c("Rae", d(19), "Can you look at this? [~accountid:acc-ben]"),
        _c("Ana", d(18), "Waiting on access to the VPN"),
        _c("Rae", d(3), "Still not working after validation"),
    ])
    history = pd.DataFrame([
        _h("DEVOPS-9", d(19), "status", "Reviewing", "To Do"),          # intake, not rework
        _h("DEVOPS-9", d(15), "status", "To Do", "In Progress"),
        _h("DEVOPS-9", d(6), "status", "In Progress", "Validating"),
        _h("DEVOPS-9", d(3), "status", "Validating", "In Progress"),     # rework
        _h("DEVOPS-9", d(16), "assignee", None, "Ben"),
        _h("DEVOPS-9", d(10), "assignee", "Ben", "Ana"),
        _h("DEVOPS-9", d(16), "target_start", None, str(d(14).date())),
    ])
    s = ts.build_ticket_story(pd.DataFrame([issue]), history, "DEVOPS-9", names={"acc-ben": "Ben"})
    assert s["error_message"] is None
    k = s["kpis"]
    assert k["rework"] == 1 and k["status_changes"] == 4 and k["comments"] == 3
    assert k["in_status_bd"] == 3 and k["first_reply_h"] is not None
    conv = s["conversation_df"]
    assert list(conv["Role"]) == ["Requester", "Assignee", "Requester"]
    assert conv.iloc[0]["Mentions"] == "Ben" and "Access / permissions" in conv.iloc[1]["Themes"]
    assert conv.iloc[1]["Reply After (business h)"] is not None
    people = s["people_df"].set_index("Person")
    assert {"Rae", "Ana", "Ben"} <= set(people.index) and "assignee" in people.loc["Ben", "Role"]
    times = s["time_df"].set_index("status")["bd"]
    assert times["In Progress"] > 0 and times["Validating"] > 0
    text = " ".join(t for _, t in s["verdict"])
    assert "**In Progress** for 3 business days" in text and "Sent back to be worked on 1 time" in text
    assert "SLA due" in text
    assert "DEVOPS-9" in ts.summary_html(s) and s["timeline_fig"] is not None


def test_closed_ticket_and_no_history(ts):
    from reports import in_progress_report as ipr
    d = lambda n: _bd(ipr, n)  # noqa: E731
    df = pd.DataFrame([_issue("DEVOPS-5", "Done", d(10), start=d(8), done=d(2))])
    s = ts.build_ticket_story(df, None, "DEVOPS-5")
    text = " ".join(t for _, t in s["verdict"])
    assert "**Done**" in text and "SLA **met**" in text
    assert not s["history"] and s["kpis"]["rework"] == 0
    assert ts.build_ticket_story(df, None, "DEVOPS-404")["error_message"]
