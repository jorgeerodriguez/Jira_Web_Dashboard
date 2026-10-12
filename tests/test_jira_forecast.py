"""Posting the Backlog forecast as Jira comments, keeping dashboard comments out of human metrics, and reading
posted forecasts back.

Pinned: every kind of comment the dashboard writes is recognised (and ordinary mentions of the dashboard are
not); the loader keeps them out of the human comments; the forecast tag round-trips exactly; nothing is
preselected and an unchanged forecast is skipped; posting only adds comments (no field updates) and audits
each ticket; and the track record compares each posted forecast with when the ticket really started and
finished.
"""
from datetime import date, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from data import build_dataframe_new as loader
from data.dashboard_comments import format_forecast_tag, is_dashboard_comment, parse_forecast

_LOCAL = timezone(timedelta(hours=-6))


# ── No Plotly needed: runs in CI ────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    "Target dates updated from the PE dashboard (Backlog forecast).\n* Target start: none → 2026-10-16",
    "Assigned to Ana from the PE dashboard (Suggested Assignments).\n* Why: x",
    "Undo of dashboard batch 2026: Target start back to none",            # posted before the naming convention
    "Undo of PE dashboard assignment batch 2026: assignee back to Unassigned.",
    "Backlog forecast from the PE dashboard (Oct 12, 2026). No dates were changed.",
    "anything\n[pe-forecast v1 as_of=2026-10-12 start_p50=2026-10-20]",
])
def test_dashboard_comments_are_recognised(body):
    assert is_dashboard_comment(body)


@pytest.mark.parametrize("body", ["Please check the PE dashboard for the dates", "Deployed. Assigned to me.", "", None])
def test_people_mentioning_the_dashboard_are_not(body):
    assert not is_dashboard_comment(body)


def test_loader_keeps_dashboard_comments_apart():
    fields = {"comment": {"total": 3, "comments": [
        {"author": {"displayName": "Ana", "accountType": "atlassian"}, "created": "2026-10-01T10:00:00.000+0000", "body": "Done, please check"},
        {"author": {"displayName": "Ana", "accountType": "atlassian"}, "created": "2026-10-02T10:00:00.000+0000",
         "body": "Backlog forecast from the PE dashboard (Oct 02, 2026). No dates were changed."},
        {"author": {"displayName": "Automation for Jira", "accountType": "app"}, "created": "2026-10-03T10:00:00.000+0000", "body": "x"},
    ]}}
    human, total, bots, dashboard = loader._human_comments(fields)
    assert [c["body"] for c in human] == ["Done, please check"]
    assert (total, bots, len(dashboard)) == (3, 1, 1)


def test_forecast_tag_round_trips():
    fields = {"as_of": "2026-10-12", "start_p50": "2026-10-20", "queue": 2, "basis": "High_×_Small", "target_end": None}
    parsed = parse_forecast("summary line\n" + format_forecast_tag(fields))
    assert parsed == {"as_of": "2026-10-12", "start_p50": "2026-10-20", "queue": "2", "basis": "High_×_Small",
                      "target_end": None, "version": "v1"}
    with pytest.raises(ValueError):
        format_forecast_tag({"basis": "High × Small"})
    assert parse_forecast("no tag here") is None


# ── Writing and the track record (need the app's packages) ──────────────────────

@pytest.fixture
def jf(tmp_path, monkeypatch):
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import jira_dates, jira_forecast
    monkeypatch.setattr(jira_dates, "AUDIT_LOG", tmp_path / "audit.jsonl")
    return jira_forecast


def _planning():
    d = pd.Timestamp
    return pd.DataFrame([
        {"key": "PE-1", "is_assigned": True, "assignee_name": "Ana", "queue_position": 1.0, "start_confidence": "High",
         "start_p50": d("2026-10-20"), "start_safe": d("2026-11-11"), "finish_p50": d("2026-10-24"),
         "finish_p85": d("2026-10-30"), "sla_due": d("2026-11-03"), "target_start_day": d("2026-10-14"),
         "target_end_day": pd.NaT, "basis": "High × Small", "priority_bucket": "High", "size": "Small", "summary": "one"},
        {"key": "PE-2", "is_assigned": True, "assignee_name": "Ben", "queue_position": 2.0, "start_confidence": "Medium",
         "start_p50": d("2026-10-22"), "start_safe": d("2026-11-13"), "finish_p50": d("2026-10-28"),
         "finish_p85": d("2026-11-05"), "sla_due": pd.NaT, "target_start_day": pd.NaT, "target_end_day": pd.NaT,
         "basis": "Team-wide", "priority_bucket": "Medium", "size": "Unestimated", "summary": "two"},
        {"key": "PE-3", "is_assigned": False, "assignee_name": "Unassigned", "queue_position": np.nan, "start_confidence": None,
         "start_p50": pd.NaT, "start_safe": pd.NaT, "finish_p50": pd.NaT, "finish_p85": pd.NaT, "sla_due": pd.NaT,
         "target_start_day": pd.NaT, "target_end_day": pd.NaT, "basis": "", "priority_bucket": "Low/None",
         "size": "Small", "summary": "three"},
    ])


class FakeJira:
    def __init__(self, fail=()):
        self.comments, self.fail, self.updates = [], set(fail), []

    def add_comment(self, key, body):
        if key in self.fail:
            raise RuntimeError("403")
        self.comments.append((key, body))

    def issue(self, *a, **k):          # posting must never read-modify a ticket
        raise AssertionError("post_forecasts must not touch issue fields")


def test_rows_skip_unchanged_forecasts_and_posting_only_comments(jf):
    as_of = date(2026, 10, 12)
    first = jf.build_rows(_planning(), as_of)
    assert list(first["Ticket"]) == ["PE-1", "PE-2"] and not first["Apply"].any()      # unassigned: no forecast
    jira = FakeJira(fail={"PE-2"})
    results = jf.post_forecasts(jira, first.assign(Apply=True), first.attrs["fields"], "Tester").set_index("Ticket")
    assert results.loc["PE-1", "Result"] == "posted" and results.loc["PE-2", "Result"] == "failed"
    body = jira.comments[0][1]
    tag = parse_forecast(body)
    assert is_dashboard_comment(body) and "No dates were changed" in body
    assert tag["start_p50"] == "2026-10-20" and tag["target_start"] == "2026-10-14" and tag["basis"] == "High_×_Small"
    from reports import jira_dates
    log = jira_dates.read_audit()
    assert set(log["action"]) == {"forecast"} and set(log["key"]) == {"PE-1", "PE-2"}

    again = jf.build_rows(_planning(), date(2026, 10, 13), {"PE-1": [{"created": pd.Timestamp("2026-10-12", tz="UTC"), "body": body}]})
    assert again.set_index("Ticket").loc["PE-1", "Same As Last"] and not again.set_index("Ticket").loc["PE-2", "Same As Last"]
    _, warnings = jf.validate(again.assign(Apply=True))
    assert any("PE-1" in w for w in warnings)
    jira2 = FakeJira()
    res2 = jf.post_forecasts(jira2, again.assign(Apply=True), again.attrs["fields"]).set_index("Ticket")
    assert res2.loc["PE-1", "Result"] == "skipped" and [k for k, _ in jira2.comments] == ["PE-2"]


def test_track_record_compares_posted_forecasts_with_reality(jf):
    from reports import in_progress_report as ipr
    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    d = lambda n: pd.Timestamp(np.busday_offset(today.date(), -n, roll="backward", holidays=hol))  # noqa: E731

    def tagged(as_of, start, safe, finish, p85, target):
        return [{"author": "Ana", "created": pd.Timestamp(as_of, tz="UTC"), "body": "Backlog forecast from the PE dashboard.\n" +
                 format_forecast_tag({"as_of": as_of.date().isoformat(), "start_p50": start.date().isoformat(),
                                      "safe_start": safe.date().isoformat(), "finish_p50": finish.date().isoformat(),
                                      "finish_p85": p85.date().isoformat(), "target_start": target.date().isoformat(),
                                      "confidence": "High"})}]

    def issue(key, status, done=None, comments=()):
        return {"key": key, "id": key, "status": status, "issuetype": "Story", "project_name": "DevOps",
                "assignee_name": "Ana", "priority_name": "Medium", "estimated_size_name": "Small", "summary": key,
                "created": d(40).tz_localize(_LOCAL), "updated": (done or d(1)).tz_localize(_LOCAL),
                "status_category_changed": done.tz_localize(_LOCAL) + pd.Timedelta(hours=12) if done is not None else pd.NaT,
                "planned_start_date": pd.Timestamp(d(20).date(), tz="UTC"), "target_end_date": pd.NaT,
                "comments": [], "dashboard_comments": list(comments)}

    issues = pd.DataFrame([
        # Started a day after the likely start (before the safe start), finished before the P85.
        issue("ON", "Done", done=d(5), comments=tagged(d(20), d(15), d(10), d(8), d(4), d(18))),
        # Not started yet.
        issue("WAIT", "To Do", comments=tagged(d(10), d(8), d(2), d(6), d(1), d(9))),
        issue("NONE", "To Do"),
    ])
    at = lambda day: (day + pd.Timedelta(hours=10)).tz_localize(_LOCAL).tz_convert("UTC")  # noqa: E731
    history = pd.DataFrame([{"key": "ON", "at": at(d(14)), "author": "Ana", "field": "status", "frm": "To Do", "to": "In Progress"},
                            {"key": "ON", "at": at(d(5)), "author": "Ana", "field": "status", "frm": "In Progress", "to": "Done"}])
    out = jf.track_record(issues, history)
    k = out["kpis"]
    assert (k["posted"], k["tickets"], k["started"], k["finished"]) == (2, 2, 1, 1)
    assert k["start_by_likely"] == 0 and k["start_by_safe"] == 1 and k["start_by_target"] == 0   # Jira Target start was too early
    assert k["finish_by_p85"] == 1
    row = out["detail_df"].set_index(out["detail_df"]["Ticket"].str.split("/").str[-1]).loc["ON"]
    assert row["Start Miss (bd)"] == 1
    assert jf.track_record(issues.drop(columns="dashboard_comments"))["kpis"]["posted"] == 0


def test_batch_limit_is_higher_for_comment_only_posts(jf):
    from reports import jira_dates
    assert jf.MAX_BATCH > jira_dates.MAX_BATCH              # a weekly post covers the whole backlog in one batch
    rows = pd.DataFrame({"Ticket": [f"PE-{i}" for i in range(jf.MAX_BATCH + 1)], "Same As Last": False})
    errors, _ = jf.validate(rows.iloc[:49])
    assert not errors
    errors, _ = jf.validate(rows)
    assert any("At most" in e for e in errors)
