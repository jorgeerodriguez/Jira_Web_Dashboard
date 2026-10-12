"""Post the Backlog forecast to Jira as comments (no field changes), and measure posted forecasts later.

Writing uses the same safeguards as the Target date updates (reports/jira_dates.py), whose permission switch
and audit log it shares: off unless JIRA_WRITE_ENABLED=true and opened from localhost, nothing preselected,
a preview, typed confirmation, at most MAX_BATCH tickets, and an audit record per ticket. MAX_BATCH is higher
than for date updates (25): posting only adds a comment and skips unchanged forecasts, and a weekly post should
cover the whole backlog in one batch. It only adds a
comment, so there is nothing to undo; a ticket whose last posted forecast has the same dates is skipped.

Each comment ends with a machine-readable tag (data/dashboard_comments.py). The loader keeps these comments
out of the human-comment metrics and in `dashboard_comments`, and `track_record` reads them back: for every
posted forecast, did the ticket start by the likely / safe start, and finish by the likely / safe finish?
That is a forward-looking accuracy record that needs no reconstruction from change history, and the data
for re-tuning the forecast.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import uuid

import numpy as np
import pandas as pd

from data.dashboard_comments import format_forecast_tag, parse_forecast
from reports import change_history as chg
from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports import jira_dates

MAX_BATCH = 100
ACTION = "forecast"
BACKLOG_STATUSES = {"to do", "tech discovery required", "triage"}
TAG_FIELDS = ["as_of", "start_p50", "safe_start", "finish_p50", "finish_p85", "sla_due", "target_start", "target_end",
              "queue", "confidence", "basis", "priority", "size", "assignee", "batch"]
COMPARED = ["start_p50", "safe_start", "finish_p50", "finish_p85", "target_start", "target_end"]


def _iso(value) -> str | None:
    return pd.Timestamp(value).date().isoformat() if value is not None and pd.notna(value) else None


def _slug(value) -> str | None:
    """Tag-safe text: spaces become '_' (the tag is space-separated)."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or str(value).strip() == "":
        return None
    return str(value).strip().replace(" ", "_").replace("]", ")")


def forecast_fields(b: pd.Series, as_of: date) -> dict:
    """The values posted for one ticket of the Backlog report's planning frame."""
    queue = b.get("queue_position")
    return {
        "as_of": as_of.isoformat(),
        "start_p50": _iso(b.get("start_p50")), "safe_start": _iso(b.get("start_safe")),
        "finish_p50": _iso(b.get("finish_p50")), "finish_p85": _iso(b.get("finish_p85")),
        "sla_due": _iso(b.get("sla_due")),
        "target_start": _iso(b.get("target_start_day")), "target_end": _iso(b.get("target_end_day")),
        "queue": int(queue) if queue is not None and pd.notna(queue) else None,
        "confidence": _slug(b.get("start_confidence")), "basis": _slug(b.get("basis")),
        "priority": _slug(b.get("priority_bucket")), "size": _slug(b.get("size")),
        "assignee": _slug(b.get("assignee_name")),
    }


def last_posted(dashboard_comments) -> dict | None:
    """The most recent forecast tag among a ticket's dashboard comments."""
    if not isinstance(dashboard_comments, list):
        return None
    tagged = [(c.get("created"), parse_forecast(c.get("body"))) for c in dashboard_comments]
    tagged = [(when, f) for when, f in tagged if f is not None]
    if not tagged:
        return None
    return max(tagged, key=lambda t: (t[1].get("as_of") or "", t[0] if pd.notna(t[0]) else pd.Timestamp(0, tz="UTC")))[1]


def build_rows(planning: pd.DataFrame, as_of: date, dashboard_comments: dict | None = None) -> pd.DataFrame:
    """One row per assigned backlog ticket with a forecast. Nothing is selected."""
    cols = ["Apply", "Ticket", "Assignee", "Queue #", "Start Confidence", "Likely Start", "Safe Start", "Likely Finish",
            "Safe Finish (P85)", "SLA Due", "Target Start", "Target End", "Last Posted", "Same As Last", "Summary"]
    if planning is None or planning.empty:
        return pd.DataFrame(columns=cols)
    rows, fields_by_key = [], {}
    for _, b in planning[planning["is_assigned"] & planning["start_p50"].notna()].iterrows():
        fields = forecast_fields(b, as_of)
        last = last_posted((dashboard_comments or {}).get(b["key"]))
        same = last is not None and all(last.get(k) == (None if fields[k] is None else str(fields[k])) for k in COMPARED)
        fields_by_key[b["key"]] = fields
        rows.append({
            "Apply": False, "Ticket": b["key"], "Assignee": b["assignee_name"], "Queue #": fields["queue"],
            "Start Confidence": fields["confidence"] or "",
            "Likely Start": fields["start_p50"], "Safe Start": fields["safe_start"],
            "Likely Finish": fields["finish_p50"], "Safe Finish (P85)": fields["finish_p85"],
            "SLA Due": fields["sla_due"], "Target Start": fields["target_start"], "Target End": fields["target_end"],
            "Last Posted": last.get("as_of") if last else None, "Same As Last": same,
            "Summary": str(b.get("summary", "") or "")[:90],
        })
    out = pd.DataFrame(rows, columns=cols).sort_values(["Assignee", "Queue #"]).reset_index(drop=True)
    out.attrs["fields"] = fields_by_key
    return out


def validate(selected: pd.DataFrame) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    if selected.empty:
        errors.append("No tickets selected.")
    if len(selected) > MAX_BATCH:
        errors.append(f"At most {MAX_BATCH} tickets per batch (selected {len(selected)}).")
    same = selected[selected["Same As Last"].astype(bool)] if not selected.empty else selected
    for key in same.get("Ticket", []):
        warnings.append(f"{key}: same dates as its last posted forecast; it will be skipped.")
    return errors, warnings


def format_comment(fields: dict, batch: str) -> str:
    def day(value):
        return pd.Timestamp(value).strftime("%b %d") if value else "—"

    queue = f"queue #{fields['queue']}" if fields.get("queue") else "not queued"
    confidence = (fields.get("confidence") or "").replace("_", " ")
    tag = format_forecast_tag({**{k: fields.get(k) for k in TAG_FIELDS if k != "batch"}, "batch": batch})
    return (f"Backlog forecast from the PE dashboard ({pd.Timestamp(fields['as_of']):%b %d, %Y}). No dates were changed.\n"
            f"* Likely start: {day(fields['start_p50'])} · Safe start: {day(fields['safe_start'])} "
            f"({queue}{', ' + confidence + ' confidence' if confidence else ''})\n"
            f"* Likely finish: {day(fields['finish_p50'])} · Safe finish (P85): {day(fields['finish_p85'])} · "
            f"SLA due: {day(fields['sla_due'])}\n"
            f"* Target start / end in Jira today: {day(fields['target_start'])} / {day(fields['target_end'])}\n"
            f"{tag}")


def post_forecasts(jira, selected: pd.DataFrame, fields_by_key: dict, actor: str = "") -> pd.DataFrame:
    batch = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    results, audit = [], []
    for _, row in selected.iterrows():
        key, fields = row["Ticket"], fields_by_key.get(row["Ticket"])
        outcome, detail = "posted", "Forecast comment added."
        try:
            if fields is None:
                outcome, detail = "skipped", "No forecast for this ticket."
            elif bool(row["Same As Last"]):
                outcome, detail = "skipped", "Same dates as its last posted forecast."
            else:
                jira.add_comment(key, format_comment(fields, batch))
        except Exception as exc:  # report and carry on with the rest of the batch
            outcome, detail = "failed", str(exc)[:200]
        results.append({"Ticket": key, "Result": outcome, "Detail": detail})
        audit.append({"at": datetime.now(timezone.utc).isoformat(), "batch": batch, "action": ACTION, "key": key,
                      "actor": actor, "result": outcome, "detail": detail, **(fields or {})})
    jira_dates._write_audit(audit)
    return pd.DataFrame(results)


# ── Track record ────────────────────────────────────────────────────────────────

def _posted(df_issues: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if "dashboard_comments" not in df_issues.columns:
        return pd.DataFrame()
    for key, comments in zip(df_issues["key"], df_issues["dashboard_comments"]):
        if not isinstance(comments, list):
            continue
        for c in comments:
            f = parse_forecast(c.get("body"))
            if f is not None and f.get("as_of"):
                rows.append({"key": key, **f})
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    for col in ("queue", "confidence"):          # optional fields: keep the columns even if no tag carries them
        if col not in out.columns:
            out[col] = None
    for col in ("as_of", "start_p50", "safe_start", "finish_p50", "finish_p85", "sla_due", "target_start"):
        out[col] = pd.to_datetime(out.get(col), format="%Y-%m-%d", errors="coerce")
    return out


def _actual_starts(history: pd.DataFrame) -> pd.DataFrame:
    """Each time a ticket left the backlog for active work: (key, day)."""
    if not chg.has_history(history):
        return pd.DataFrame(columns=["key", "day"])
    s = history[history["field"].eq("status")].copy()
    frm = s["frm"].astype(str).str.strip().str.casefold()
    to = s["to"].astype(str).str.strip().str.casefold()
    left = s[frm.isin(BACKLOG_STATUSES) & ~to.isin(BACKLOG_STATUSES | {"will not do"})]
    day = pd.to_datetime(left["at"], utc=True).dt.tz_convert(ipr.LOCAL_TZ).dt.tz_localize(None).dt.normalize()
    return pd.DataFrame({"key": left["key"].values, "day": day.values})


def track_record(df_issues: pd.DataFrame, history: pd.DataFrame | None = None) -> dict:
    """Posted forecasts against what happened. Each posted forecast counts once."""
    empty = {"kpis": {"posted": 0}, "detail_df": pd.DataFrame(), "by_confidence_df": pd.DataFrame(),
             "history": chg.has_history(history)}
    if df_issues is None or df_issues.empty:
        return empty
    posted = _posted(df_issues)
    if posted.empty:
        return empty
    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol).set_index("key")
    starts = _actual_starts(history)

    def actual_start(key, as_of):
        mine = starts[(starts["key"] == key) & (starts["day"] >= as_of)]["day"]
        return mine.min() if len(mine) else pd.NaT

    posted["actual_start"] = [actual_start(k, a) for k, a in zip(posted["key"], posted["as_of"])]
    posted["outcome"] = posted["key"].map(facts["outcome"])
    posted["closed_day"] = posted["key"].map(facts["closed_day"])
    posted["actual_finish"] = posted["closed_day"].where(posted["outcome"].eq("Done"))

    def bd(a, b):
        return ipr._busdays_between(a, b, hol) - ipr._busdays_between(b, a, hol)   # signed: b - a

    posted["start_error_bd"] = bd(posted["start_p50"], posted["actual_start"])
    posted["finish_error_bd"] = bd(posted["finish_p50"], posted["actual_finish"])
    started = posted[posted["actual_start"].notna()]
    finished = posted[posted["actual_finish"].notna()]

    def share(frame, col_actual, col_pred):
        ok = frame[frame[col_pred].notna()]
        return float((ok[col_actual] <= ok[col_pred]).mean()) if len(ok) else None

    kpis = {
        "posted": int(len(posted)), "tickets": int(posted["key"].nunique()),
        "first_posted": posted["as_of"].min().date(), "started": int(len(started)), "finished": int(len(finished)),
        "start_by_likely": share(started, "actual_start", "start_p50"),
        "start_by_safe": share(started, "actual_start", "safe_start"),
        "start_by_target": share(started, "actual_start", "target_start"),
        "start_median_abs_bd": float(started["start_error_bd"].abs().median()) if len(started) else None,
        "finish_by_likely": share(finished, "actual_finish", "finish_p50"),
        "finish_by_p85": share(finished, "actual_finish", "finish_p85"),
        "finish_median_abs_bd": float(finished["finish_error_bd"].abs().median()) if len(finished) else None,
    }
    if len(started):
        g = started.assign(conf=started["confidence"].fillna("—").str.replace("_", " ")).groupby("conf")
        by_conf = pd.DataFrame({
            "Forecasts Started": g.size(),
            "Started by Likely Start %": g.apply(lambda x: (x["actual_start"] <= x["start_p50"]).mean() * 100).round(0),
            "Started by Safe Start %": g.apply(lambda x: (x["actual_start"] <= x["safe_start"]).mean() * 100).round(0),
            "Typical Miss (bd)": g["start_error_bd"].apply(lambda s: s.abs().median()).round(1),
        }).reset_index().rename(columns={"conf": "Start Confidence"})
    else:
        by_conf = pd.DataFrame()
    detail = posted.sort_values(["as_of", "key"], ascending=[False, True])
    detail_df = pd.DataFrame({
        "Ticket": ipr.JIRA_BROWSE_BASE_URL + detail["key"].astype(str),
        "Posted": detail["as_of"].dt.date,
        "Likely Start": detail["start_p50"].dt.date, "Safe Start": detail["safe_start"].dt.date,
        "Actual Start": pd.to_datetime(detail["actual_start"]).dt.date,
        "Start Miss (bd)": detail["start_error_bd"].astype("Int64"),
        "Likely Finish": detail["finish_p50"].dt.date, "Safe Finish (P85)": detail["finish_p85"].dt.date,
        "Actual Finish": pd.to_datetime(detail["actual_finish"]).dt.date,
        "Finish Miss (bd)": detail["finish_error_bd"].astype("Int64"),
        "Confidence": detail["confidence"].fillna("").str.replace("_", " "),
        "Queue #": pd.to_numeric(detail.get("queue"), errors="coerce").astype("Int64"),
        "Status Now": detail["key"].map(facts["status"]),
    }).reset_index(drop=True)
    return {"kpis": kpis, "detail_df": detail_df, "by_confidence_df": by_conf, "history": chg.has_history(history)}
