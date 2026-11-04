"""Executive Report: one month for leadership, built only on the machine running the app.

Pinned: the period is a full month against the month before (default: the last full month; the current month is
never offered); the build is allowed only with EXECUTIVE_REPORT_ENABLED and a localhost Host; the month's
delivery and SLA numbers follow the SLA page's rules, including clocks set after the work and the rate without
them; and the HTML is self-contained, links tickets, names business leads and never names engineers.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

from reports import jira_dates as jd

_LOCAL = timezone(timedelta(hours=-6))
TODAY = pd.Timestamp("2026-11-04")


@pytest.fixture
def er():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import executive_report
    return executive_report


def test_local_host_check():
    assert all(jd.is_local_host(h) for h in ("localhost:8501", "127.0.0.1", "[::1]:8501", "LOCALHOST"))
    assert not any(jd.is_local_host(h) for h in ("dash.example.com", "10.0.0.5:8501", "", None))


def test_report_needs_the_flag_and_this_machine(er, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(jd, "PROJECT_ENV", env)
    monkeypatch.delenv(er.ENABLE_FLAG, raising=False)
    assert not er.report_permission("localhost:8501")[0]
    env.write_text(f"{er.ENABLE_FLAG}=true\n")
    assert er.report_permission("localhost:8501")[0]
    allowed, reason = er.report_permission("dash.example.com")
    assert not allowed and "machine running the app" in reason
    monkeypatch.setenv("JIRA_WRITE_ALLOW_REMOTE", "true")          # the Jira-write escape hatch does not apply
    assert not er.report_permission("dash.example.com")[0]


def test_period_and_months(er):
    assert er.last_full_month(TODAY) == "2026-10"
    p = er.period("2026-01")
    assert (p["start"], p["end"], p["prev_start"]) == (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01"),
                                                        pd.Timestamp("2025-12-01"))
    assert p["label"] == "January 2026" and p["prev_label"] == "December 2025"
    df = pd.DataFrame({"created": [pd.Timestamp("2026-08-15 12:00", tz=_LOCAL)]})
    assert er.available_months(df, TODAY) == ["2026-10", "2026-09", "2026-08"]


def _issue(key, created, *, done=None, start=None, priority="Medium", size="Small", assignee="Zed Engineer"):
    created = pd.Timestamp(created).tz_localize(_LOCAL) + pd.Timedelta(hours=10)
    done_at = pd.Timestamp(done).tz_localize(_LOCAL) + pd.Timedelta(hours=12) if done else pd.NaT
    return {"key": key, "id": key, "status": "Done" if done else "To Do", "issuetype": "Story",
            "project_name": "DevOps", "assignee_name": assignee, "reporter_name": "Rae Requester",
            "priority_name": priority, "estimated_size_name": size, "business_lead": "Lena Lead",
            "summary": f"Work for {key}", "created": created, "updated": done_at if done else created,
            "status_category_changed": done_at,
            "planned_start_date": pd.Timestamp(start, tz="UTC") if start else pd.NaT, "target_end_date": pd.NaT,
            "comments": [], "dashboard_comments": [], "links": [], "parent": None}


def _set_start(key, day, value):
    at = (pd.Timestamp(day) + pd.Timedelta(hours=15)).tz_localize(_LOCAL).tz_convert("UTC")
    return {"key": key, "at": at, "author": "Lead", "field": "target_start", "frm": None, "to": value}


def _data():
    df = pd.DataFrame([
        _issue("DEVOPS-1", "2026-10-01", start="2026-10-01", done="2026-10-06"),                   # met, clock in time
        _issue("DEVOPS-2", "2026-10-01", start="2026-10-01", done="2026-10-28"),                   # late (15 bd SLA)
        _issue("DEVOPS-3", "2026-10-05", start="2026-10-08", done="2026-10-09"),                   # clock set after
        _issue("DEVOPS-4", "2026-09-01", start="2026-09-01", done="2026-09-08"),                   # previous month
        _issue("DEVOPS-5", "2026-10-20", assignee="Quinn Hidden"),                                 # still open
    ])
    history = pd.DataFrame([_set_start("DEVOPS-1", "2026-10-01", "2026-10-01"),
                            _set_start("DEVOPS-2", "2026-10-01", "2026-10-01"),
                            _set_start("DEVOPS-3", "2026-10-09", "2026-10-08"),
                            _set_start("DEVOPS-4", "2026-09-01", "2026-09-01")])
    return df, history


def test_month_numbers_follow_sla_rules(er):
    df, history = _data()
    r = er.build_executive_report(df, history, month="2026-10", today=TODAY, original_starts={})
    now, prev = r["now"], r["prev"]
    assert (now["delivered"], now["created"], prev["delivered"]) == (3, 4, 1)
    assert now["sla_n"] == 3 and now["sla_late"] == 1 and now["sla_met"] == pytest.approx(2 / 3)
    assert now["after_fact"] == 1 and now["after_fact_share"] == pytest.approx(1 / 3)
    assert now["sla_n_honest"] == 2 and now["sla_met_honest"] == pytest.approx(0.5)
    assert list(r["late_df"]["key"]) == ["DEVOPS-2"] and list(r["after_fact_df"]["key"]) == ["DEVOPS-3"]
    assert any("Target start" in x["ask"] for x in r["recommendations"] if x["decision"])
    assert r["monthly"]["month"].iloc[-1] == "2026-10" and len(r["monthly"]) == er.TREND_MONTHS

    no_history = er.build_executive_report(df, None, month="2026-10", today=TODAY, original_starts={})
    assert not no_history["history"] and no_history["now"]["after_fact"] == 0
    assert not any(t["label"].startswith("SLA met, clock") for t in no_history["tiles"])


def test_html_is_self_contained_and_names_no_engineers(er):
    df, history = _data()
    r = er.build_executive_report(df, history, month="2026-10", today=TODAY, original_starts={})
    page = er.render_html(r)
    assert page.startswith("<!doctype html>") and "October 2026" in page and "September 2026" in page
    assert "<script src=" not in page and page.count("Plotly.newPlot") >= 3    # plotly.js inline, works offline
    assert er.ipr.JIRA_BROWSE_BASE_URL + "DEVOPS-2" in page and "Lena Lead" in page
    assert "Zed Engineer" not in page and "Quinn Hidden" not in page and "Rae Requester" not in page
    assert er.filename(r) == "PE_Executive_Report_2026-10.html"
    assert er.build_executive_report(pd.DataFrame())["error_message"]
