"""Change history: loading it from Jira, and the Trend, SLA and Validating views built on it.

Pinned: the loader keeps only status / Target start / Target end, reads Target dates as ISO values (never
the ambiguous display strings), pages through the bulk endpoint and returns an empty frame instead of
failing; a Target date change is classed as set / moved / cleared, with moves after the old date passed
flagged; a ticket whose Target start was set on or after the day it was done has its SLA clock "set after
the fact", is flagged on the SLA page and can be left out of the breach rates; Trend gains the date-change
measures only when history is loaded; and Validating measures waiting since entering Validating, rework,
and late tickets that would have met their SLA without validation time.
"""
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from data import fetch_change_history as fch

_LOCAL = timezone(timedelta(hours=-6))


# ── Loader (no Plotly needed: runs in CI) ───────────────────────────────────────

def _log(issue_id, created_ms, items, author="Ana"):
    return {"issueId": issue_id, "changeHistories": [{"created": created_ms, "author": {"displayName": author},
                                                      "items": items}]}


def test_loader_keeps_three_fields_and_reads_dates_as_iso():
    logs = [_log("1", 1789487114594, [
        {"fieldId": "customfield_10946", "from": None, "fromString": None, "to": "2026-09-11", "toString": "11/Sep/26"},
        {"fieldId": "status", "from": "3", "fromString": "In Progress", "to": "10001", "toString": "Done"},
        {"fieldId": "labels", "fromString": "a", "toString": "b"},
    ])]
    rows = fch._parse_items(logs, {"1": "PE-1"})
    assert [(r["field"], r["frm"], r["to"]) for r in rows] == [("target_start", None, "2026-09-11"),
                                                             ("status", "In Progress", "Done")]
    assert rows[0]["at"] == pd.Timestamp(1789487114594, unit="ms", tz="UTC")


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Session:
    def __init__(self, pages, fail=False):
        self.pages, self.fail, self.bodies = list(pages), fail, []

    def post(self, url, json):
        if self.fail:
            raise RuntimeError("401")
        self.bodies.append(dict(json))
        return _Response(self.pages.pop(0))


class _Jira:
    def __init__(self, session):
        self._session = session
        self._options = {"server": "https://example.atlassian.net"}


def _issues():
    now = pd.Timestamp.now(tz="UTC")
    return pd.DataFrame({"id": ["1", "2", "3"], "key": ["PE-1", "PE-2", "CAR-1"],
                         "project_name": ["DevOps", "DevOps", "Release Management"],
                         "updated": [now, now - pd.Timedelta(days=fch.HISTORY_DAYS + 5), now]})


def test_loader_pages_scopes_and_never_raises():
    page1 = {"issueChangeLogs": [_log("1", 1789487114594, [{"fieldId": "status", "fromString": "To Do", "toString": "In Progress"}])],
             "nextPageToken": "abc"}
    page2 = {"issueChangeLogs": [_log("1", 1789587114594, [{"fieldId": "status", "fromString": "In Progress", "toString": "Done"}])]}
    session = _Session([page1, page2])
    out = fch.fetch_change_history(_Jira(session), _issues())
    assert session.bodies[0]["issueIdsOrKeys"] == ["1"]           # CAR and stale issues left out
    assert session.bodies[1]["nextPageToken"] == "abc"
    assert out["to"].tolist() == ["In Progress", "Done"]
    assert fch.fetch_change_history(_Jira(_Session([], fail=True)), _issues()).empty
    assert fch.fetch_change_history(None, _issues()).empty


# ── Views (need the app's packages) ─────────────────────────────────────────────

@pytest.fixture
def mods():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    pytest.importorskip("streamlit")
    from reports import change_history, executive_summary, in_progress_report, service_level_agreement_report
    from reports import trend_report, validating_report
    return {"chg": change_history, "es": executive_summary, "ipr": in_progress_report,
            "sla": service_level_agreement_report, "trend": trend_report, "val": validating_report}


def _today(ipr):
    return ipr.today_local()


def _bd_back(ipr, n):
    """The business day n business days before today (company calendar)."""
    today = _today(ipr)
    hol = ipr._calendar_holidays(today)
    return pd.Timestamp(np.busday_offset(today.date(), -n, roll="backward", holidays=hol))


def _issue(key, *, status="Done", start, done=None, priority="Medium", size="Small", reporter="Req", assignee="Ana"):
    return {
        "id": key, "key": key, "status": status, "issuetype": "Story", "project_name": "DevOps",
        "assignee_name": assignee, "reporter_name": reporter, "priority_name": priority, "estimated_size_name": size,
        "business_lead": "Lead", "summary": key, "comments": [],
        "created": (start - pd.Timedelta(days=2)).tz_localize(_LOCAL), "updated": (done or start).tz_localize(_LOCAL),
        "status_category_changed": done.tz_localize(_LOCAL).tz_convert("UTC") + pd.Timedelta(hours=12) if done is not None else pd.NaT,
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"), "target_end_date": pd.NaT,
    }


def _h(key, day, field, frm, to, author="Ana", hour=10):
    at = (pd.Timestamp(day) + pd.Timedelta(hours=hour)).tz_localize(_LOCAL).tz_convert("UTC")
    return {"key": key, "at": at, "author": author, "field": field, "frm": frm, "to": to}


def test_date_changes_and_clock_after_the_fact(mods):
    chg, es, ipr = mods["chg"], mods["es"], mods["ipr"]
    d = lambda n: _bd_back(ipr, n)  # noqa: E731
    issues = pd.DataFrame([_issue("PE-1", start=d(20), done=d(10)), _issue("PE-2", start=d(20), done=d(10))])
    history = pd.DataFrame([
        _h("PE-1", d(25), "target_start", None, str(d(22).date())),               # set
        _h("PE-1", d(21), "target_start", str(d(22).date()), str(d(20).date())),  # moved after the old date passed
        _h("PE-1", d(24), "target_end", str(d(14).date()), str(d(18).date())),    # moved ahead of time, earlier
        _h("PE-2", d(10), "target_start", None, str(d(20).date())),               # set the day it was done
    ])
    today = _today(ipr)
    hol = ipr._calendar_holidays(today)
    changes = chg.date_changes(history, hol)
    assert changes["kind"].tolist() == ["set", "moved", "moved", "set"]
    assert changes["after_passed"].tolist() == [False, True, False, False]
    assert changes.loc[2, "shift_bd"] < 0
    summary = chg.ticket_date_summary(changes, es._facts(issues, today, hol))
    assert summary.loc["PE-1", ["moves", "start_moves", "end_moves", "late_moves"]].tolist() == [2, 1, 1, 1]
    assert not summary.loc["PE-1", "clock_after_fact"]
    assert summary.loc["PE-2", "clock_after_fact"]


def test_sla_flags_and_can_exclude_clock_set_after_the_fact(mods):
    ipr, sla = mods["ipr"], mods["sla"]
    d = lambda n: _bd_back(ipr, n)  # noqa: E731
    # Medium/Small SLA = 15 bd. LATE took 20 bd; BACKFILL's start was written the day it was done.
    issues = pd.DataFrame([_issue("LATE", start=d(25), done=d(5)), _issue("OK", start=d(12), done=d(5)),
                           _issue("BACKFILL", start=d(6), done=d(5))])
    history = pd.DataFrame([_h("BACKFILL", d(5), "target_start", None, str(d(6).date()))])
    out = sla.build_sla_visuals(issues, 30, history=history, original_starts={})
    k = out["kpis"]
    assert (k["after_fact"], k["after_fact_n"], k["after_fact_met"]) == (1, 3, 1)
    assert k["done_rate"] == pytest.approx(1 / 3)
    assert out["after_fact_df"]["Ticket"].str.endswith("BACKFILL").all()
    excluded = sla.build_sla_visuals(issues, 30, history=history, original_starts={}, exclude_after_fact=True)
    assert excluded["kpis"]["done_rate"] == pytest.approx(1 / 2)
    plain = sla.build_sla_visuals(issues, 30, original_starts={})
    assert not plain["kpis"]["history"] and plain["after_fact_df"].empty


def test_trend_adds_date_measures_only_with_history(mods):
    ipr, trend = mods["ipr"], mods["trend"]
    d = lambda n: _bd_back(ipr, n)  # noqa: E731
    issues = pd.DataFrame([_issue(f"S-{i}", start=d(30 + i), done=d(25 + i)) for i in range(4)]
                          + [_issue("MOVED", start=d(30), done=d(25)),
                             _issue("OPEN", status="In Progress", start=d(5))])
    history = pd.DataFrame([
        _h("MOVED", d(40), "target_start", str(d(38).date()), str(d(34).date())),
        _h("MOVED", d(33), "target_start", str(d(34).date()), str(d(30).date())),
        _h("OPEN", d(8), "target_end", str(d(7).date()), str(d(2).date())),
        _h("OPEN", d(3), "target_end", str(d(2).date()), str(d(-3).date())),
    ])
    plain = trend.build_trend_visuals(issues)
    assert plain["date_changes"] is None and "date_moves" not in plain["monthly"]
    out = trend.build_trend_visuals(issues, history=history)
    assert {c["key"] for c in out["scorecard"]} >= {"date_moves", "stable_dates", "late_moves", "clock_after_fact"}
    k = out["date_changes"]["kpis"]
    assert k["moves_per_ticket"] == pytest.approx(2 / 5) and k["stable"] == pytest.approx(4 / 5)
    assert out["date_changes"]["replanned_df"]["Ticket"].str.endswith("OPEN").all()


def test_validating_waiting_rework_and_sla_impact(mods):
    ipr, val = mods["ipr"], mods["val"]
    d = lambda n: _bd_back(ipr, n)  # noqa: E731
    # Medium/Small SLA = 15 bd from Target start.
    issues = pd.DataFrame([
        _issue("WAIT", status="Validating", start=d(10)),
        _issue("REWORK", start=d(14), done=d(2)),
        _issue("SAVED", start=d(18), done=d(1)),      # 17 bd: late by 2, of which 6 bd in Validating
    ])
    history = pd.DataFrame([
        _h("WAIT", d(4), "status", "In Progress", "Validating"),
        _h("REWORK", d(8), "status", "In Progress", "Validating"),
        _h("REWORK", d(6), "status", "Validating", "In Progress"),
        _h("REWORK", d(4), "status", "In Progress", "Validating"),
        _h("REWORK", d(2), "status", "Validating", "Done", author="Req"),
        _h("SAVED", d(7), "status", "In Progress", "Validating"),
        _h("SAVED", d(1), "status", "Validating", "Done", author="Ana"),
    ])
    out = val.build_validating_visuals(issues, history)
    w = out["waiting_df"].set_index(out["waiting_df"]["Ticket"].str.split("/").str[-1])
    assert w.loc["WAIT", "Waiting (bd)"] == 4 and w.loc["WAIT", "Round"] == 1
    k = out["kpis"]
    assert k["rework"] == pytest.approx(1 / 3) and k["rework_n"] == 1
    assert k["late"] == 1 and k["late_only_validating"] == 1
    assert k["breach_paused"] == 0
    assert out["what_if"][5]["episodes"] == 1                      # SAVED waited 6 bd
    fallback = val.build_validating_visuals(issues)
    assert not fallback["history"] and fallback["kpis"]["in_validating"] == 1
    assert fallback["waiting_df"]["Waiting (bd)"].isna().all()
