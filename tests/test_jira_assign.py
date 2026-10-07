"""Safe-mode "Assign in Jira": every safeguard between the Suggested Assignments plan and a write to Jira.

Uses a fake Jira, so nothing here touches a real instance. Pinned: people are assigned by account id
taken from their own tickets, never by name; nothing is preselected; bad batches are rejected before any
write; a ticket whose assignee changed after review is skipped, never overwritten; every assignment gets
a Jira comment and an audit record; and undo restores the previous owner (or unassigns) only where Jira
still holds what the batch wrote. The permission switch itself is shared with, and tested in,
test_jira_dates.py.
"""
from datetime import date

import pandas as pd
import pytest

from reports import jira_assign as ja
from reports import jira_dates as jd


class _FakeIssue:
    def __init__(self, store, key):
        self._store, self._key = store, key
        account = store[key]
        self.raw = {"fields": {"assignee": {"accountId": account} if account else None}}

    def update(self, fields):
        self._store[self._key] = (fields["assignee"] or {}).get("accountId")


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


ACCOUNTS = {"Ana": "acc-ana", "Ben": "acc-ben"}


def _row(key, assign_to, owner="Unassigned", account=None, suggested=None):
    return {"Apply": True, "Ticket": key, "Current Owner": owner, "Assign To": assign_to,
            "Suggested": suggested or assign_to, "Backup": "", "SLA Fit": "✓", "Why": "BigQuery/Data experience",
            "Current Account": account}


def test_account_ids_come_from_the_latest_ticket_and_skip_unassigned():
    df = pd.DataFrame({"assignee_name": ["Ana", "Ana", "Unassigned", "Ben"],
                       "assignee_account_id": ["old-ana", "acc-ana", None, None],
                       "updated": pd.to_datetime(["2026-01-01", "2026-06-01", "2026-06-01", "2026-06-01"])})
    assert ja.account_ids(df) == {"Ana": "acc-ana"}           # Ben has no known id: cannot be chosen
    assert ja.account_ids(df.drop(columns="assignee_account_id")) == {}


def test_rows_start_unselected_with_the_suggestion():
    plan = pd.DataFrame({"Ticket": ["https://x/browse/PE-1"], "Current Owner": [""], "Suggested": ["Ana"],
                         "Backup": ["Ben"], "SLA Fit": ["✓"], "Why": ["w"]})
    rows = ja.build_rows(plan, {"PE-1": None})
    assert rows.loc[0, "Ticket"] == "PE-1" and rows.loc[0, "Assign To"] == "Ana"
    assert not rows["Apply"].any() and rows.loc[0, "Current Owner"] == "Unassigned"


def test_validation_blocks_bad_batches_before_any_write():
    errors, _ = ja.validate(pd.DataFrame([_row("PE-1", "Cara")]), ACCOUNTS)
    assert any("no Jira account" in e for e in errors)          # never looked up by name
    errors, _ = ja.validate(pd.DataFrame(columns=list(_row("x", "Ana"))), ACCOUNTS)
    assert errors
    errors, _ = ja.validate(pd.DataFrame([_row(f"PE-{i}", "Ana") for i in range(ja.MAX_BATCH + 1)]), ACCOUNTS)
    assert any("At most" in e for e in errors)
    errors, warnings = ja.validate(pd.DataFrame([_row("PE-1", "Ben", owner="Ana", account="acc-ana",
                                                      suggested="Ana")]), ACCOUNTS)
    assert not errors
    assert any("reassigns" in w for w in warnings) and any("instead of the suggested" in w for w in warnings)


def test_apply_assigns_comments_audits_and_never_overwrites_newer_edits():
    jira = FakeJira({"PE-1": None, "PE-2": "acc-ben", "PE-3": None}, fail={"PE-3"})
    selected = pd.DataFrame([_row("PE-1", "Ana"),
                             _row("PE-2", "Ana"),               # loaded unassigned, Ben took it since
                             _row("PE-3", "Ana")])
    results = ja.apply_assignments(jira, selected, ACCOUNTS, date(2026, 10, 7), "Tester").set_index("Ticket")
    assert results.loc["PE-1", "Result"] == "updated" and jira.store["PE-1"] == "acc-ana"
    assert results.loc["PE-2", "Result"] == "skipped" and jira.store["PE-2"] == "acc-ben"
    assert results.loc["PE-3", "Result"] == "failed"
    assert [k for k, _ in jira.updates] == ["PE-1"]
    assert [k for k, _ in jira.comments] == ["PE-1"] and "BigQuery/Data" in jira.comments[0][1]
    log = jd.read_audit()
    assert set(log["key"]) == {"PE-1", "PE-2", "PE-3"} and set(log["action"]) == {"assign"}
    assert jd.last_batch().empty and jd.original_target_starts() == {}   # date features ignore assignments


def test_undo_restores_only_what_the_batch_wrote():
    jira = FakeJira({"PE-1": None, "PE-2": "acc-ben"})
    selected = pd.DataFrame([_row("PE-1", "Ana"), _row("PE-2", "Ana", owner="Ben", account="acc-ben")])
    ja.apply_assignments(jira, selected, ACCOUNTS, date(2026, 10, 7))
    assert jira.store == {"PE-1": "acc-ana", "PE-2": "acc-ana"}
    jira.store["PE-1"] = "acc-ben"                               # someone changed it again in Jira
    batch = ja.last_batch()
    assert set(batch["key"]) == {"PE-1", "PE-2"}
    results = ja.undo_batch(jira, batch).set_index("Ticket")
    assert results.loc["PE-1", "Result"] == "skipped" and jira.store["PE-1"] == "acc-ben"
    assert results.loc["PE-2", "Result"] == "restored" and jira.store["PE-2"] == "acc-ben"
    assert ja.last_batch().empty                                  # a batch is undone once


def test_undo_unassigns_tickets_that_were_unassigned():
    jira = FakeJira({"PE-1": None})
    ja.apply_assignments(jira, pd.DataFrame([_row("PE-1", "Ana")]), ACCOUNTS, date(2026, 10, 7))
    ja.undo_batch(jira, ja.last_batch())
    assert jira.store["PE-1"] is None and jira.updates[-1] == ("PE-1", {"assignee": None})
