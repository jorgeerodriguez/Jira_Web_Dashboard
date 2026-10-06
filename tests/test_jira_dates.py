"""Safe-mode Target date updates: every safeguard between the dashboard and a write to Jira.

Uses a fake Jira, so nothing here touches a real instance. Pinned: writes are off unless enabled and
only from localhost; nothing is proposed as selected; bad batches are rejected before any write; a
ticket edited in Jira after it was reviewed is skipped, never overwritten; every change gets a Jira
comment and an audit record; undo only restores what the batch wrote; and the SLA page judges
re-planned tickets on their original Target start.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from reports import jira_dates as jd

_TODAY = pd.Timestamp("2026-10-06")          # a Tuesday
_HOL = np.array([], dtype="datetime64[D]")


class _FakeIssue:
    def __init__(self, store, key):
        self._store, self._key = store, key
        self.raw = {"fields": {jd.CF_TARGET_START: store[key][0], jd.CF_TARGET_END: store[key][1]}}

    def update(self, fields):
        self._store[self._key] = (fields[jd.CF_TARGET_START], fields[jd.CF_TARGET_END])


class FakeJira:
    def __init__(self, store, fail=()):
        self.store, self.fail, self.comments, self.updates = dict(store), set(fail), [], []

    def issue(self, key, fields=None):
        if key in self.fail:
            raise RuntimeError("403 Forbidden")
        issue = _FakeIssue(self.store, key)
        original = issue.update

        def update(fields):
            self.updates.append((key, fields))
            original(fields)
        issue.update = update
        return issue

    def add_comment(self, key, body):
        self.comments.append((key, body))


@pytest.fixture(autouse=True)
def _tmp_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(jd, "AUDIT_LOG", tmp_path / "audit.jsonl")


def _row(key, cur_start, new_start, cur_end, new_end):
    return {"Apply": True, "Ticket": key, "Why": "", "Assignee": "Ana",
            "Current Target Start": cur_start and date.fromisoformat(cur_start),
            "New Target Start": date.fromisoformat(new_start),
            "Current Target End": cur_end and date.fromisoformat(cur_end),
            "New Target End": date.fromisoformat(new_end), "Start Confidence": "High", "Summary": key}


def test_env_file_switch_is_read_live_and_defaults_to_off(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(jd, "PROJECT_ENV", env)
    monkeypatch.delenv("JIRA_WRITE_ENABLED", raising=False)
    assert jd.write_flag() == (False, "not set")                       # no .env file at all
    env.write_text("JIRA_SERVER=x\nJIRA_WRITE_ENABLED=\n")
    assert jd.write_flag() == (False, "not set")                       # empty value = off
    env.write_text("JIRA_WRITE_ENABLED=true\n")
    assert jd.write_flag() == (True, ".env")                           # picked up without a restart
    env.write_text("JIRA_WRITE_ENABLED=TRUE\n")
    assert jd.write_flag() == (True, ".env")
    env.write_text('# comment\nexport JIRA_WRITE_ENABLED="true"  \n')
    assert jd.write_flag() == (True, ".env")
    env.write_text("JIRA_WRITE_ENABLED=true # on for date updates\n")
    assert jd.write_flag() == (True, ".env")
    env.write_text("JIRA_WRITE_ENABLED=false\n")
    monkeypatch.setenv("JIRA_WRITE_ENABLED", "true")
    assert jd.write_flag() == (False, ".env")                          # the .env value wins when set
    env.write_text("JIRA_WRITE_ENABLED=\n")
    assert jd.write_flag() == (True, "environment")                    # empty .env value: environment applies
    monkeypatch.setenv("JIRA_WRITE_ENABLED", "maybe")
    assert jd.write_flag() == (False, "environment")                   # anything not true-ish is off


def test_writes_are_off_by_default_and_local_only(tmp_path, monkeypatch):
    monkeypatch.setattr(jd, "PROJECT_ENV", tmp_path / "missing.env")
    monkeypatch.delenv("JIRA_WRITE_ENABLED", raising=False)
    monkeypatch.delenv("JIRA_WRITE_ALLOW_REMOTE", raising=False)
    assert jd.write_permission("localhost:8501")[0] is False
    monkeypatch.setenv("JIRA_WRITE_ENABLED", "true")
    assert jd.write_permission("localhost:8501")[0] is True
    assert jd.write_permission("127.0.0.1:8501")[0] is True
    assert jd.write_permission("[::1]:8501")[0] is True
    assert jd.write_permission("pe-reports.audacy.internal")[0] is False
    monkeypatch.setenv("JIRA_WRITE_ALLOW_REMOTE", "true")
    assert jd.write_permission("pe-reports.audacy.internal")[0] is True


def test_proposals_flag_stale_dates_and_select_nothing():
    backlog = pd.DataFrame([
        {"key": "A", "is_assigned": True, "assignee_name": "Ana", "summary": "a",
         "target_start_day": pd.Timestamp("2026-09-01"), "target_end_day": pd.Timestamp("2026-09-10"),
         "start_p50": pd.Timestamp("2026-10-08"), "start_safe": pd.Timestamp("2026-10-30"),
         "finish_p85": pd.Timestamp("2026-10-20"), "start_confidence": "High"},
        {"key": "OK", "is_assigned": True, "assignee_name": "Ana", "summary": "ok",
         "target_start_day": pd.Timestamp("2026-10-08"), "target_end_day": pd.Timestamp("2026-11-30"),
         "start_p50": pd.Timestamp("2026-10-08"), "start_safe": pd.Timestamp("2026-10-30"),
         "finish_p85": pd.Timestamp("2026-10-20"), "start_confidence": "High"},
        {"key": "UNASSIGNED", "is_assigned": False, "assignee_name": "Unassigned", "summary": "",
         "target_start_day": pd.NaT, "target_end_day": pd.NaT, "start_p50": pd.NaT, "start_safe": pd.NaT,
         "finish_p85": pd.NaT, "start_confidence": None},
    ])
    likely = jd.build_proposals(backlog, _TODAY, _HOL, "likely")
    assert likely["Ticket"].tolist() == ["A"]
    assert not likely["Apply"].any()
    row = likely.iloc[0]
    assert "Target start has passed" in row["Why"] and "Forecast finish after Target end" in row["Why"]
    assert row["New Target Start"] == date(2026, 10, 8) and row["New Target End"] == date(2026, 10, 20)
    safe = jd.build_proposals(backlog, _TODAY, _HOL, "safe")
    assert safe.iloc[0]["New Target Start"] == date(2026, 10, 30)
    assert safe.iloc[0]["New Target End"] >= safe.iloc[0]["New Target Start"]


def test_validation_blocks_bad_batches_before_any_write():
    good = _row("A", "2026-09-01", "2026-10-08", "2026-09-10", "2026-10-20")
    backwards = _row("B", "2026-09-01", "2026-10-20", "2026-09-10", "2026-10-08")
    past = _row("C", "2026-09-01", "2026-10-01", "2026-09-10", "2026-10-20")
    errors, _ = jd.validate(pd.DataFrame([good, backwards, past]), _TODAY, _HOL)
    assert any("B: Target end" in e for e in errors) and any("C: Target start" in e for e in errors)
    errors, _ = jd.validate(pd.DataFrame([good] * (jd.MAX_BATCH + 1)), _TODAY, _HOL)
    assert any("At most" in e for e in errors)
    errors, warnings = jd.validate(pd.DataFrame([_row("D", "2026-06-01", "2026-10-10", None, "2026-10-12")]), _TODAY, _HOL)
    assert not errors
    assert any("not a business day" in w for w in warnings) and any("moves" in w for w in warnings)
    assert jd.validate(pd.DataFrame([good])[0:0], _TODAY, _HOL)[0] == ["No tickets selected."]


def test_apply_updates_writes_comments_audits_and_never_overwrites_newer_edits():
    jira = FakeJira({"A": ("2026-09-01", "2026-09-10"), "B": ("2026-09-15", "2026-09-30"), "C": (None, None)},
                    fail={"C"})
    rows = pd.DataFrame([
        _row("A", "2026-09-01", "2026-10-08", "2026-09-10", "2026-10-20"),
        _row("B", "2026-09-01", "2026-10-08", "2026-09-10", "2026-10-20"),   # someone changed B in Jira since
        _row("C", None, "2026-10-08", None, "2026-10-20"),                    # Jira refuses
    ])
    results = jd.apply_updates(jira, rows, _TODAY.date(), actor="Tester").set_index("Ticket")
    assert results.loc["A", "Result"] == "updated"
    assert results.loc["B", "Result"] == "skipped" and "Changed in Jira" in results.loc["B", "Detail"]
    assert results.loc["C", "Result"] == "failed"
    assert jira.store["A"] == ("2026-10-08", "2026-10-20")
    assert jira.store["B"] == ("2026-09-15", "2026-09-30")            # untouched
    assert [k for k, _ in jira.comments] == ["A"]
    assert "2026-09-01 → 2026-10-08" in jira.comments[0][1]
    log = jd.read_audit()
    assert len(log) == 3 and set(log["result"]) == {"updated", "skipped", "failed"}
    assert jd.original_target_starts() == {"A": date(2026, 9, 1)}


def test_undo_restores_only_what_the_batch_wrote():
    jira = FakeJira({"A": ("2026-09-01", "2026-09-10"), "B": ("2026-09-02", "2026-09-11")})
    rows = pd.DataFrame([_row("A", "2026-09-01", "2026-10-08", "2026-09-10", "2026-10-20"),
                         _row("B", "2026-09-02", "2026-10-09", "2026-09-11", "2026-10-21")])
    jd.apply_updates(jira, rows, _TODAY.date())
    jira.store["B"] = ("2026-10-15", "2026-10-25")                    # edited again after the batch
    batch = jd.last_batch()
    assert set(batch["key"]) == {"A", "B"}
    results = jd.undo_batch(jira, batch).set_index("Ticket")
    assert results.loc["A", "Result"] == "restored" and jira.store["A"] == ("2026-09-01", "2026-09-10")
    assert results.loc["B", "Result"] == "skipped" and jira.store["B"] == ("2026-10-15", "2026-10-25")
    assert jd.last_batch().empty                                      # an undone batch is not offered again


def test_sla_judges_replanned_tickets_on_their_original_start():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    pytest.importorskip("streamlit")
    from datetime import timedelta, timezone
    from reports import service_level_agreement_report as sla

    local = timezone(timedelta(hours=-6))
    today = pd.Timestamp.now(tz=local).tz_localize(None).normalize()
    start_now = today - pd.Timedelta(days=2)          # current Target start: recent, so not breached yet
    row = {"key": "R", "status": "In Progress", "issuetype": "Story", "project_name": "DevOps", "assignee_name": "Ana",
           "priority_name": "High", "estimated_size_name": "Small", "business_lead": "Lead", "summary": "r",
           "created": (today - pd.Timedelta(days=60)).tz_localize(local), "updated": today.tz_localize(local),
           "status_category_changed": pd.NaT, "planned_start_date": pd.Timestamp(start_now.date(), tz="UTC"),
           "target_end_date": pd.NaT, "comments": []}
    original = {"R": (today - pd.Timedelta(days=40)).date()}      # it was due weeks ago before re-planning
    honest = sla.build_sla_visuals(pd.DataFrame([row]), 90, original_starts=original)
    replanned = sla.build_sla_visuals(pd.DataFrame([row]), 90, judge_original_start=False)
    assert honest["kpis"]["replanned"] == 1
    assert honest["kpis"]["due_breached"] == 1
    assert replanned["kpis"]["due_n"] == 0
