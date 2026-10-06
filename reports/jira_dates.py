"""Safe-mode updates of Jira Target start / Target end dates from the Backlog forecast.

Nothing here writes on its own. The Backlog page builds *proposals*; a person reviews, edits and ticks
the rows to change, confirms, and only then `apply_updates` writes. Safeguards:

- Off by default: writes need JIRA_WRITE_ENABLED=true -- in the project's .env file (read fresh every
  time, so editing that line takes effect without a restart) or, when .env has no value for it, in the
  environment -- and only from the machine running the app (localhost) unless
  JIRA_WRITE_ALLOW_REMOTE=true. The shared deployment has no login, so anyone with
  its URL would otherwise write to Jira as the configured token's account.
- Validation before anything is sent: dates present, end on/after start, start not in the past, at most
  MAX_BATCH tickets; big moves are flagged.
- No overwriting someone else's edit: each ticket is re-read just before writing and skipped if its
  dates differ from what the person reviewed.
- Audit trail: a comment on every changed ticket, and an append-only local log (AUDIT_LOG) of old and
  new values per batch, which also powers "Undo last batch" and lets the SLA page judge re-planned
  tickets on their original Target start.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

CF_TARGET_START = "customfield_10946"
CF_TARGET_END = "customfield_10947"
MAX_BATCH = 25
BIG_MOVE_BD = 20                       # moving a date by more than ~4 weeks is flagged for a second look
SLIP_THRESHOLD_BD = 2                  # propose when the likely start is this many business days after Target start
AUDIT_LOG = Path(os.environ.get("JIRA_AUDIT_LOG", Path(__file__).resolve().parent.parent / ".dashboard_audit"
                                / "jira_date_changes.jsonl"))
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"
TRUE_VALUES = {"true", "1", "yes", "on"}


def _env_file_value(name: str) -> str | None:
    """Value of `name` in the project .env, read now (None if the file, the line or the value is missing).

    A minimal KEY=VALUE reader (comments, `export`, quotes), so no extra dependency is needed.
    """
    if not PROJECT_ENV.exists():
        return None
    value = None
    for line in PROJECT_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key != name:
            continue
        raw = raw.strip()
        if raw[:1] in ("'", '"') and raw[-1:] == raw[:1] and len(raw) >= 2:
            raw = raw[1:-1]
        else:
            raw = raw.split(" #")[0].strip()
        value = raw.strip()                       # the last occurrence wins, as in shells
    return value or None


def write_flag() -> tuple[bool, str]:
    """(enabled, where the setting came from). The .env value wins; an empty or missing one falls back to
    the environment; anything not true-ish (or nothing at all) means off."""
    value, source = _env_file_value("JIRA_WRITE_ENABLED"), ".env"
    if value is None:
        value, source = os.environ.get("JIRA_WRITE_ENABLED", "").strip() or None, "environment"
    if value is None:
        return False, "not set"
    return value.lower() in TRUE_VALUES, source


# ── Permission ──────────────────────────────────────────────────────────────────

def write_permission(host: str | None) -> tuple[bool, str]:
    """(allowed, reason). `host` is the browser's Host header (e.g. 'localhost:8501')."""
    enabled, source = write_flag()
    if not enabled:
        return False, ("Jira updates are off. To enable them on your own machine, set JIRA_WRITE_ENABLED=true "
                       f"in the project .env file (currently: {source if source == 'not set' else 'off in ' + source}).")
    host = (host or "").strip().lower()
    hostname = host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]
    if hostname not in LOCAL_HOSTS and os.environ.get("JIRA_WRITE_ALLOW_REMOTE", "").strip().lower() != "true":
        return False, ("Jira updates are only allowed from the machine running the app (localhost). "
                       "This copy has no login, so updates are blocked here.")
    return True, f"Jira updates are enabled (JIRA_WRITE_ENABLED=true in {source})."


# ── Proposals ───────────────────────────────────────────────────────────────────

def _busdays(start, end, hol) -> int:
    a, b = np.datetime64(start, "D"), np.datetime64(end, "D")
    return int(np.busday_count(a, b, holidays=hol)) if a <= b else -int(np.busday_count(b, a, holidays=hol))


def _next_business_day(day: pd.Timestamp, hol) -> pd.Timestamp:
    return pd.Timestamp(np.busday_offset(np.datetime64(day.date(), "D"), 0, roll="forward", holidays=hol))


def build_proposals(backlog: pd.DataFrame, today: pd.Timestamp, hol, start_rule: str = "likely") -> pd.DataFrame:
    """Rows where the Target dates look out of date, with proposed dates. Nothing is selected.

    `backlog` is the Backlog report's frame (assigned tickets have start_p50 / start_safe / finish_p85).
    start_rule: "likely" proposes the P50 start, "safe" the calibrated safe start.
    """
    rows = []
    for _, b in backlog[backlog["is_assigned"]].iterrows():
        likely = b["start_safe"] if start_rule == "safe" else b["start_p50"]
        if pd.isna(likely):
            continue
        current_start, current_end = b["target_start_day"], b["target_end_day"]
        reasons = []
        if pd.isna(current_start):
            reasons.append("No Target start")
        elif current_start < today:
            reasons.append("Target start has passed")
        elif _busdays(current_start, likely, hol) > SLIP_THRESHOLD_BD:
            reasons.append(f"Projected start {_busdays(current_start, likely, hol)} bd after Target start")
        if pd.notna(current_end) and pd.notna(b["finish_p85"]) and current_end < b["finish_p85"]:
            reasons.append("Forecast finish after Target end")
        if pd.isna(current_end):
            reasons.append("No Target end")
        if not reasons:
            continue
        new_start = _next_business_day(max(pd.Timestamp(likely), today), hol)
        new_end = _next_business_day(max(pd.Timestamp(b["finish_p85"]) if pd.notna(b["finish_p85"]) else new_start,
                                         new_start), hol)
        rows.append({
            "Apply": False,
            "Ticket": b["key"],
            "Why": "; ".join(reasons),
            "Assignee": b["assignee_name"],
            "Current Target Start": current_start.date() if pd.notna(current_start) else None,
            "New Target Start": new_start.date(),
            "Current Target End": current_end.date() if pd.notna(current_end) else None,
            "New Target End": new_end.date(),
            "Start Confidence": b.get("start_confidence", ""),
            "Summary": str(b.get("summary", "") or "")[:90],
        })
    return pd.DataFrame(rows, columns=["Apply", "Ticket", "Why", "Assignee", "Current Target Start", "New Target Start",
                                       "Current Target End", "New Target End", "Start Confidence", "Summary"])


# ── Validation ──────────────────────────────────────────────────────────────────

def _as_date(value) -> date | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def validate(selected: pd.DataFrame, today: pd.Timestamp, hol) -> tuple[list[str], list[str]]:
    """(errors, warnings) for the rows about to be written. Any error blocks the whole batch."""
    errors, warnings = [], []
    if selected.empty:
        errors.append("No tickets selected.")
    if len(selected) > MAX_BATCH:
        errors.append(f"At most {MAX_BATCH} tickets per batch (selected {len(selected)}).")
    for _, row in selected.iterrows():
        key = row["Ticket"]
        start, end = _as_date(row["New Target Start"]), _as_date(row["New Target End"])
        if start is None or end is None:
            errors.append(f"{key}: both new dates are required.")
            continue
        if end < start:
            errors.append(f"{key}: Target end {end} is before Target start {start}.")
        if start < today.date():
            errors.append(f"{key}: Target start {start} is in the past.")
        for label, new, old in (("start", start, _as_date(row["Current Target Start"])),
                                ("end", end, _as_date(row["Current Target End"]))):
            if not np.is_busday(np.datetime64(new, "D"), holidays=hol):
                warnings.append(f"{key}: Target {label} {new} is not a business day.")
            if old is not None and abs(_busdays(old, new, hol)) > BIG_MOVE_BD:
                warnings.append(f"{key}: Target {label} moves {abs(_busdays(old, new, hol))} business days ({old} → {new}).")
        if (start == _as_date(row["Current Target Start"])) and (end == _as_date(row["Current Target End"])):
            warnings.append(f"{key}: no change; it will be skipped.")
    return errors, warnings


# ── Audit log ───────────────────────────────────────────────────────────────────

def _write_audit(records: list[dict]) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, default=str) + "\n")


def read_audit() -> pd.DataFrame:
    if not AUDIT_LOG.exists():
        return pd.DataFrame()
    with AUDIT_LOG.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return pd.DataFrame(rows)


def original_target_starts() -> dict[str, date]:
    """Each re-planned ticket's Target start before its first dashboard update (for honest SLA judging)."""
    log = read_audit()
    if log.empty:
        return {}
    applied = log[(log["action"] == "update") & (log["result"] == "updated") & log["old_start"].notna()]
    first = applied.sort_values("at").groupby("key").first()
    return {key: pd.Timestamp(v).date() for key, v in first["old_start"].items()}


def last_batch() -> pd.DataFrame:
    log = read_audit()
    if log.empty:
        return log
    updates = log[(log["action"] == "update") & (log["result"] == "updated")]
    if updates.empty:
        return updates
    batch = updates.sort_values("at")["batch"].iloc[-1]
    undone = set(log.loc[log["action"] == "undo", "undoes"].dropna()) if "undoes" in log.columns else set()
    return pd.DataFrame() if batch in undone else updates[updates["batch"] == batch]


# ── Writing ─────────────────────────────────────────────────────────────────────

def _jira_dates(jira, key: str) -> tuple[date | None, date | None]:
    issue = jira.issue(key, fields=f"{CF_TARGET_START},{CF_TARGET_END}")
    fields = issue.raw.get("fields", {})
    return _as_date(fields.get(CF_TARGET_START)), _as_date(fields.get(CF_TARGET_END))


def _comment(old_start, new_start, old_end, new_end, note: str) -> str:
    return ("Target dates updated from the PE dashboard (Backlog forecast).\n"
            f"* Target start: {old_start or 'none'} → {new_start}\n"
            f"* Target end: {old_end or 'none'} → {new_end}\n"
            f"{note}")


def apply_updates(jira, selected: pd.DataFrame, as_of: date, actor: str = "") -> pd.DataFrame:
    """Write the selected rows. Re-reads each ticket first and skips it if Jira changed since review."""
    batch = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    results, audit = [], []
    for _, row in selected.iterrows():
        key = row["Ticket"]
        seen_start, seen_end = _as_date(row["Current Target Start"]), _as_date(row["Current Target End"])
        new_start, new_end = _as_date(row["New Target Start"]), _as_date(row["New Target End"])
        outcome, detail = "updated", ""
        try:
            now_start, now_end = _jira_dates(jira, key)
            if (now_start, now_end) != (seen_start, seen_end):
                outcome = "skipped"
                detail = f"Changed in Jira since it was loaded (now {now_start} / {now_end}); refresh and review again."
            elif (new_start, new_end) == (seen_start, seen_end):
                outcome, detail = "skipped", "No change."
            else:
                jira.issue(key).update(fields={CF_TARGET_START: new_start.isoformat(), CF_TARGET_END: new_end.isoformat()})
                jira.add_comment(key, _comment(seen_start, new_start, seen_end, new_end,
                                               f"Forecast as of {as_of}. Batch {batch}."))
                detail = "Dates updated and a comment added."
        except Exception as exc:  # report and carry on with the rest of the batch
            outcome, detail = "failed", str(exc)[:200]
        results.append({"Ticket": key, "Result": outcome, "Detail": detail})
        audit.append({"at": datetime.now(timezone.utc).isoformat(), "batch": batch, "action": "update", "key": key,
                      "actor": actor, "result": outcome, "old_start": seen_start, "new_start": new_start,
                      "old_end": seen_end, "new_end": new_end, "detail": detail, "as_of": as_of})
    _write_audit(audit)
    return pd.DataFrame(results)


def undo_batch(jira, batch_rows: pd.DataFrame, actor: str = "") -> pd.DataFrame:
    """Restore a batch's old dates, but only where Jira still holds the dates that batch wrote."""
    results, audit = [], []
    undo_id = f"undo-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for _, rec in batch_rows.iterrows():
        key = rec["key"]
        wrote = (_as_date(rec["new_start"]), _as_date(rec["new_end"]))
        restore = (_as_date(rec["old_start"]), _as_date(rec["old_end"]))
        outcome, detail = "restored", ""
        try:
            if _jira_dates(jira, key) != wrote:
                outcome, detail = "skipped", "Dates were changed again after the update; left as they are."
            else:
                jira.issue(key).update(fields={CF_TARGET_START: restore[0].isoformat() if restore[0] else None,
                                               CF_TARGET_END: restore[1].isoformat() if restore[1] else None})
                jira.add_comment(key, f"Undo of dashboard batch {rec['batch']}: Target start back to "
                                      f"{restore[0] or 'none'}, Target end back to {restore[1] or 'none'}.")
                detail = "Previous dates restored and a comment added."
        except Exception as exc:
            outcome, detail = "failed", str(exc)[:200]
        results.append({"Ticket": key, "Result": outcome, "Detail": detail})
        audit.append({"at": datetime.now(timezone.utc).isoformat(), "batch": undo_id, "action": "undo",
                      "undoes": rec["batch"], "key": key, "actor": actor, "result": outcome,
                      "old_start": wrote[0], "new_start": restore[0], "old_end": wrote[1], "new_end": restore[1],
                      "detail": detail})
    _write_audit(audit)
    return pd.DataFrame(results)
