"""Safe-mode ticket assignment in Jira from the Suggested Assignments page.

Same safeguards as the Target date updates (reports/jira_dates.py), whose permission switch and audit
log it shares:
- Off unless JIRA_WRITE_ENABLED=true (project .env or environment) and opened from localhost.
- Nothing preselected; the person can be changed per row; validation before anything is sent.
- People are assigned by Jira account id taken from the team's own tickets -- never guessed from a
  name. A person with no known account id cannot be chosen.
- Each ticket is re-read just before writing and skipped if its assignee changed since review.
- A Jira comment on every assigned ticket, an audit record per row, and undo of the last batch.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import uuid

import pandas as pd

from reports import jira_dates

MAX_BATCH = jira_dates.MAX_BATCH
ACTION = "assign"


def account_ids(df_issues: pd.DataFrame) -> dict[str, str]:
    """Display name -> Jira account id, from the most recently updated ticket for each person."""
    if "assignee_account_id" not in df_issues.columns:
        return {}
    known = df_issues[df_issues["assignee_account_id"].notna() & df_issues["assignee_name"].ne("Unassigned")]
    if "updated" in known.columns:
        known = known.sort_values("updated")
    return dict(zip(known["assignee_name"], known["assignee_account_id"]))


def build_rows(plan: pd.DataFrame, current_accounts: dict[str, str | None]) -> pd.DataFrame:
    """Editable rows for the dialog: one per planned ticket, nothing selected, Assign To = Suggested."""
    if plan.empty:
        return pd.DataFrame()
    keys = plan["Ticket"].str.split("/").str[-1]
    return pd.DataFrame({
        "Apply": False,
        "Ticket": keys.values,
        "Current Owner": plan["Current Owner"].replace("", "Unassigned").values,
        "Assign To": plan["Suggested"].values,
        "Suggested": plan["Suggested"].values,
        "Backup": plan["Backup"].values,
        "SLA Fit": plan["SLA Fit"].values,
        "Why": plan["Why"].values,
        "Current Account": [current_accounts.get(k) for k in keys],
    })


def validate(selected: pd.DataFrame, accounts: dict[str, str]) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if selected.empty:
        errors.append("No tickets selected.")
    if len(selected) > MAX_BATCH:
        errors.append(f"At most {MAX_BATCH} tickets per batch (selected {len(selected)}).")
    for _, row in selected.iterrows():
        person = row["Assign To"]
        if not person or pd.isna(person):
            errors.append(f"{row['Ticket']}: choose who to assign.")
        elif person not in accounts:
            errors.append(f"{row['Ticket']}: no Jira account is known for {person}; fetch Jira tickets again or pick "
                          "someone else.")
        elif row["Current Owner"] == person:
            warnings.append(f"{row['Ticket']}: already assigned to {person}; it will be skipped.")
        elif row["Current Owner"] not in ("", "Unassigned"):
            warnings.append(f"{row['Ticket']}: reassigns from {row['Current Owner']} to {person}.")
        if person and person != row["Suggested"]:
            warnings.append(f"{row['Ticket']}: assigning {person} instead of the suggested {row['Suggested']}.")
    return errors, warnings


def _current_account(jira, key: str) -> str | None:
    assignee = jira.issue(key, fields="assignee").raw.get("fields", {}).get("assignee")
    return (assignee or {}).get("accountId")


def _set_assignee(jira, key: str, account: str | None) -> None:
    jira.issue(key).update(fields={"assignee": {"accountId": account} if account else None})


def apply_assignments(jira, selected: pd.DataFrame, accounts: dict[str, str], as_of: date, actor: str = "") -> pd.DataFrame:
    batch = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    results, audit = [], []
    for _, row in selected.iterrows():
        key, person = row["Ticket"], row["Assign To"]
        seen = row["Current Account"] if isinstance(row["Current Account"], str) else None
        new = accounts.get(person)
        outcome, detail = "updated", ""
        try:
            now = _current_account(jira, key)
            if now != seen:
                outcome, detail = "skipped", "Assignee changed in Jira since it was loaded; refresh and review again."
            elif new is None:
                outcome, detail = "skipped", f"No Jira account known for {person}."
            elif now == new:
                outcome, detail = "skipped", "Already assigned to this person."
            else:
                _set_assignee(jira, key, new)
                jira.add_comment(key, f"Assigned to {person} from the PE dashboard (Suggested Assignments).\n"
                                      f"* Why: {row['Why']}\n"
                                      f"* Previous owner: {row['Current Owner']}\n"
                                      f"Suggestion as of {as_of}. Batch {batch}.")
                detail = "Assigned and a comment added."
        except Exception as exc:  # report and carry on with the rest of the batch
            outcome, detail = "failed", str(exc)[:200]
        results.append({"Ticket": key, "Assigned To": person, "Result": outcome, "Detail": detail})
        audit.append({"at": datetime.now(timezone.utc).isoformat(), "batch": batch, "action": ACTION, "key": key,
                      "actor": actor, "result": outcome, "old_owner": row["Current Owner"], "old_account": seen,
                      "new_owner": person, "new_account": new, "detail": detail, "as_of": as_of})
    jira_dates._write_audit(audit)
    return pd.DataFrame(results)


def last_batch() -> pd.DataFrame:
    log = jira_dates.read_audit()
    if log.empty or "action" not in log.columns:
        return pd.DataFrame()
    done = log[(log["action"] == ACTION) & (log["result"] == "updated")]
    if done.empty:
        return done
    batch = done.sort_values("at")["batch"].iloc[-1]
    undone = set(log.loc[log["action"] == "unassign-undo", "undoes"].dropna()) if "undoes" in log.columns else set()
    return pd.DataFrame() if batch in undone else done[done["batch"] == batch]


def undo_batch(jira, batch_rows: pd.DataFrame, actor: str = "") -> pd.DataFrame:
    """Restore each ticket's previous assignee, only where Jira still has the person this batch set."""
    results, audit = [], []
    undo_id = f"undo-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for _, rec in batch_rows.iterrows():
        key = rec["key"]
        old = rec["old_account"] if isinstance(rec["old_account"], str) else None
        outcome, detail = "restored", ""
        try:
            if _current_account(jira, key) != rec["new_account"]:
                outcome, detail = "skipped", "Assignee was changed again after the batch; left as it is."
            else:
                _set_assignee(jira, key, old)
                jira.add_comment(key, f"Undo of dashboard assignment batch {rec['batch']}: assignee back to "
                                      f"{rec['old_owner'] or 'Unassigned'}.")
                detail = "Previous assignee restored and a comment added."
        except Exception as exc:
            outcome, detail = "failed", str(exc)[:200]
        results.append({"Ticket": key, "Result": outcome, "Detail": detail})
        audit.append({"at": datetime.now(timezone.utc).isoformat(), "batch": undo_id, "action": "unassign-undo",
                      "undoes": rec["batch"], "key": key, "actor": actor, "result": outcome,
                      "old_owner": rec["new_owner"], "new_owner": rec["old_owner"], "detail": detail})
    jira_dates._write_audit(audit)
    return pd.DataFrame(results)
