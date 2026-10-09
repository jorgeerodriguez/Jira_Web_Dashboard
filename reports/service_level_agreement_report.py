"""SLA: the daily view of where work is breached, late or at risk, and the breach rate against the goal.

SLA = the Priority x Size table in business days (in_progress_report.SLA_BUSINESS_DAYS), counted from
each ticket's Target start; unsized tickets use Medium. PE tickets only: no Features or Initiatives, and
Release Management (CAR) tickets have no PE SLA. Tickets without a Target start have no SLA clock and
are counted separately as a data gap.

Two breach rates, both measured against BREACH_GOAL (10%):
- SLA came due: of tickets whose SLA due date fell in the window, the share that missed it. A ticket
  still open past its due date counts as breached now, so a breach cannot hide until it is finished.
  Tickets closed without delivery (Will Not Do, Rolled Back) are not judged at all: closing stale
  work is backlog hygiene, not a late delivery.
- Completed late: of tickets moved to Done in the window, the share finished after their SLA due date
  (the definition used on the Trend and Executive Summary pages).

Re-planning guard: by default, tickets whose Target start was moved from the dashboard
(reports/jira_dates.py audit log) are judged on their *original* Target start in the breach rates and
the Breached Tickets table, so moving dates cannot quietly lower the breach rate.

SLA clock set after the fact (needs the change history, data/fetch_change_history.py): a completed ticket
whose Target start was set or moved on or after the day it moved to Done. Its SLA result was decided after
the work finished, so it is flagged in the KPIs, the Breached Tickets table and its own list, and can
optionally be left out of the breach rates.

Open tickets use the same risk as the Executive Summary: In Progress and Backlog from their forecasts,
other stages Breached past due and At Risk once 80% of the SLA is used.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import change_history as chg
from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports.backlog_report import build_backlog_visuals

try:
    from reports.word_of_the_month_report import clean_comment
except ImportError:
    def clean_comment(text: str) -> str:
        return str(text or "")


JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
BREACH_GOAL = 0.10
WINDOWS = (7, 30, 90)
DEFAULT_WINDOW = 30
HEATMAP_DAYS = 90         # where breaches come from: a 90-day window keeps cells big enough to read
TREND_MONTHS = 6
DUE_SOON_BD = 10          # "coming due": the next 10 business days
MIN_CELL = 5
ACTION_RISKS = ("Breached", "Likely Late", "At Risk")
RATE_COLORS = ("#2a78d6", "#eb6834")   # SLA came due, completed late (categorical slots 1 and 2)
INK = ipr.INK
GRID = es.GRID


def _empty_payload(message: str | None = None) -> dict[str, Any]:
    return {"error_message": message, "kpis": {}, "filter_options": {}, "trend_fig": None, "heatmap_fig": None,
            "stage_fig": None, "due_soon_fig": None, "detail_df": pd.DataFrame(), "breached_df": pd.DataFrame(),
            "after_fact_df": pd.DataFrame(), "sla_table_df": sla_table()}


def sla_table() -> pd.DataFrame:
    rows = [{"Priority": p, **{s: f"{d} bd" for s, d in sizes.items()}} for p, sizes in ipr.SLA_BUSINESS_DAYS.items()]
    return pd.DataFrame(rows).rename(columns={"XL": "XLarge"})


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _signed_busdays(today: pd.Timestamp, due: pd.Series, hol: np.ndarray) -> pd.Series:
    """Business days from today to the due date: positive = left, negative = overdue."""
    now = pd.Series(today, index=due.index)
    ahead = ipr._busdays_between(now, due, hol)
    behind = ipr._busdays_between(due, now, hol)
    return pd.Series(np.where(due >= today, ahead, -behind), index=due.index).where(due.notna())


def _last_comment(comments) -> tuple[pd.Timestamp, str]:
    if not isinstance(comments, list) or not comments:
        return pd.NaT, ""
    latest = max((c for c in comments if pd.notna(c.get("created"))), key=lambda c: c["created"], default=None)
    if latest is None:
        return pd.NaT, ""
    text = " ".join(clean_comment(latest.get("body", "")).split())
    return latest["created"], f"{latest.get('author', '')}: {text[:140]}{'…' if len(text) > 140 else ''}"


def _apply_filters(frame: pd.DataFrame, filters: dict | None, with_stage: bool) -> pd.DataFrame:
    if not filters:
        return frame
    out = frame
    for key, col in (("assignees", "assignee_name"), ("leads", "lead"), ("priorities", "priority_bucket")):
        chosen = filters.get(key)
        if chosen:
            out = out[out[col].isin(chosen)]
    if with_stage and filters.get("stages"):
        out = out[out["stage"].isin(filters["stages"])]
    return out


def _judged(t: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """PE tickets with an SLA clock, with whether each met its SLA (NaN = not judged)."""
    j = t[t["sla_applies"] & t["sla_due"].notna()].copy()
    done = j["outcome"].eq("Done")
    not_delivered = j["is_closed"] & ~done                       # Will Not Do, Rolled Back: never judged
    j = j[~not_delivered].copy()
    done = j["outcome"].eq("Done")
    j["breached"] = np.where(done, j["closed_day"] > j["sla_due"], j["sla_due"] < today)
    j["judged_on_due"] = j["sla_due"] < today
    return j


def _rates(j: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    due = j[j["judged_on_due"] & (j["sla_due"] >= start) & (j["sla_due"] < end)]
    done = j[j["outcome"].eq("Done") & (j["closed_day"] >= start) & (j["closed_day"] < end)]
    return {
        "due_rate": float(due["breached"].mean()) if len(due) else None, "due_n": int(len(due)),
        "due_breached": int(due["breached"].sum()),
        "done_rate": float(done["breached"].mean()) if len(done) else None, "done_n": int(len(done)),
        "done_late": int(done["breached"].sum()),
    }


# ── Figures ─────────────────────────────────────────────────────────────────────

def _trend_figure(j: pd.DataFrame, today: pd.Timestamp) -> tuple[go.Figure, pd.DataFrame]:
    current = today.to_period("M")
    months = pd.period_range(current - (TREND_MONTHS - 1), current, freq="M")
    rows = []
    for m in months:
        start, end = m.start_time, min(m.end_time.normalize() + pd.Timedelta(days=1), today)
        r = _rates(j, start, end)
        rows.append({"month": m, "label": m.strftime("%b %Y") + (" (so far)" if m == current else ""), **r})
    trend = pd.DataFrame(rows)
    fig = go.Figure()
    for col, n_col, name, color in [("due_rate", "due_n", "SLA came due: missed", RATE_COLORS[0]),
                                    ("done_rate", "done_n", "Completed late", RATE_COLORS[1])]:
        fig.add_trace(go.Scatter(
            x=trend["label"], y=trend[col], name=name, mode="lines+markers+text", line=dict(color=color, width=2),
            marker=dict(size=9, color=np.where(trend["month"] == current, "white", color), line=dict(color=color, width=2)),
            text=[f"{v:.0%}" if pd.notna(v) else "" for v in trend[col]], textposition="top center",
            customdata=trend[[n_col]], hovertemplate=f"{name}: %{{y:.1%}} of %{{customdata[0]}} tickets<extra></extra>",
        ))
    fig.add_hline(y=BREACH_GOAL, line_dash="dash", line_color=INK, line_width=1.5,
                  annotation_text=f"Goal: under {BREACH_GOAL:.0%}", annotation_position="top left",
                  annotation_font_color=INK)
    top = max(float(trend[["due_rate", "done_rate"]].max().max() or 0), BREACH_GOAL) * 1.35
    fig.update_yaxes(tickformat=".0%", range=[0, top], title="Breach rate", gridcolor=GRID)
    fig.update_xaxes(title=None)
    fig.update_layout(height=340, legend_title_text="", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      margin=dict(l=10, r=10, t=40, b=10))
    return fig, trend


def _heatmap_figure(j: pd.DataFrame, today: pd.Timestamp) -> go.Figure | None:
    due = j[j["judged_on_due"] & (j["sla_due"] >= today - pd.Timedelta(days=HEATMAP_DAYS))]
    if due.empty:
        return None
    sizes = ["Small", "Medium", "Large", "XL", "Unestimated"]
    z, text = [], []
    for p in ipr.PRIORITY_ORDER:
        z_row, t_row = [], []
        for s in sizes:
            cell = due[(due["priority_bucket"] == p) & (due["size"] == s)]
            if cell.empty:
                z_row.append(np.nan)
                t_row.append("")
                continue
            rate = float(cell["breached"].mean())
            z_row.append(rate)
            flag = "*" if len(cell) < MIN_CELL else ""
            t_row.append(f"{rate:.0%}{flag}<br>{int(cell['breached'].sum())}/{len(cell)}")
        z.append(z_row)
        text.append(t_row)
    fig = go.Figure(go.Heatmap(
        z=z, x=["Small", "Medium", "Large", "XLarge", "Unsized"], y=ipr.PRIORITY_ORDER, text=text,
        texttemplate="%{text}", zmin=0, zmax=max(0.3, np.nanmax(z) if np.isfinite(np.nanmax(z)) else 0.3),
        colorscale=[[0, "#f8fafc"], [BREACH_GOAL / 0.3, "#fde2d4"], [1, "#eb6834"]], xgap=2, ygap=2,
        colorbar=dict(title="Missed", tickformat=".0%"),
        hovertemplate="%{y} × %{x}: %{z:.0%} missed their SLA<br>%{text}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _due_soon_figure(open_t: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray) -> go.Figure | None:
    rows = open_t[open_t["sla_due"].notna()].copy()
    if rows.empty:
        return None
    horizon = ipr._add_busdays(pd.Series([today]), pd.Series([float(DUE_SOON_BD)]), hol).iloc[0]
    rows = rows[rows["sla_due"] <= horizon]
    if rows.empty:
        return None
    rows["bucket"] = np.where(rows["sla_due"] < today, "Overdue", rows["sla_due"].dt.strftime("%a %b %d"))
    order = ["Overdue"] + [d.strftime("%a %b %d") for d in pd.bdate_range(today, horizon) if d >= today]
    counts = rows.groupby(["bucket", "risk"]).size().unstack(fill_value=0).reindex(order, fill_value=0)
    fig = go.Figure()
    for risk in es.RISK_ORDER:
        if risk in counts.columns and counts[risk].sum():
            label = es.RISK_LABELS[risk]
            fig.add_trace(go.Bar(
                x=counts.index, y=counts[risk], name=label,
                marker=dict(color=es.RISK_COLORS[label], line=dict(color="rgba(255,255,255,0.9)", width=2)),
                hovertemplate="%{x} · " + label + ": %{y} ticket(s)<extra></extra>",
            ))
    fig.update_yaxes(title="Open tickets", gridcolor=GRID, dtick=1 if counts.values.max() <= 8 else None)
    fig.update_xaxes(title="SLA due")
    fig.update_layout(barmode="stack", height=320, legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


# ── Tables ──────────────────────────────────────────────────────────────────────

def _comment_columns(frame: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray) -> pd.DataFrame:
    comments = frame["comments"] if "comments" in frame.columns else pd.Series([None] * len(frame), index=frame.index)
    last = comments.apply(_last_comment)
    when = ipr._to_day(pd.Series([w for w, _ in last], index=frame.index), ipr.LOCAL_TZ)
    return pd.DataFrame({
        "Last Comment": when.dt.date,
        "Silent (bd)": ipr._busdays_between(when.fillna(frame["created_day"]), pd.Series(today, index=frame.index),
                                            hol).astype("Int64"),
        "Latest Comment": [text for _, text in last],
    }, index=frame.index)


def _detail_table(open_t: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray, include_on_track: bool) -> pd.DataFrame:
    risks = list(ACTION_RISKS) + (["On Track", "Not Assessed"] if include_on_track else [])
    rows = open_t[open_t["risk"].isin(risks)].copy()
    if rows.empty:
        return pd.DataFrame()
    rows["to_sla"] = _signed_busdays(today, rows["sla_due"], hol)
    rank = {r: i for i, r in enumerate(es.RISK_ORDER)}
    rows = rows.assign(_r=rows["risk"].map(rank)).sort_values(["_r", "to_sla"], na_position="last")
    started = rows["start_day"].where(rows["start_day"] <= today)
    used = ipr._busdays_between(started, pd.Series(today, index=rows.index), hol) / rows["sla_bd"]
    table = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + rows["key"].astype(str),
        "SLA Status": rows["risk"].map(es.RISK_LABELS),
        "Days to SLA (bd)": rows["to_sla"].round().astype("Int64"),
        "SLA Due": rows["sla_due"].dt.date,
        "Forecast Finish": pd.to_datetime(rows["forecast_p85"], errors="coerce").dt.date,
        "SLA Used %": (used * 100).round(0),
        "Priority": rows["priority_bucket"],
        "Size": rows["size"],
        "SLA (bd)": rows["sla_bd"],
        "Stage": rows["stage"],
        "Jira Status": rows["status"],
        "Assignee": rows["assignee_name"],
        "Business Lead": rows["lead"],
        "Target End": rows["target_end_day"].dt.date,
        "Summary": rows.get("summary", pd.Series("", index=rows.index)).fillna("").astype(str).str[:120],
    })
    return pd.concat([table, _comment_columns(rows, today, hol)], axis=1).reset_index(drop=True)


def _breached_table(j: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray, start: pd.Timestamp,
                    include_completed: bool, stages: list | None = None) -> pd.DataFrame:
    open_breached = j[~j["is_closed"] & j["breached"].astype(bool)].copy()
    if stages:
        open_breached = open_breached[open_breached["stage"].isin(stages)]
    open_breached["state"] = "Open: still breached"
    open_breached["overdue"] = ipr._busdays_between(open_breached["sla_due"], pd.Series(today, index=open_breached.index), hol)
    frames = [open_breached]
    if include_completed:
        late = j[j["outcome"].eq("Done") & j["breached"].astype(bool) & (j["closed_day"] >= start)].copy()
        late["state"] = "Completed late"
        late["overdue"] = ipr._busdays_between(late["sla_due"], late["closed_day"], hol)
        frames.append(late)
    rows = pd.concat(frames)
    if rows.empty:
        return pd.DataFrame()
    rows = rows.assign(_s=rows["state"].ne("Open: still breached")).sort_values(["_s", "overdue"], ascending=[True, False])
    flagged = rows["clock_after_fact"] if "clock_after_fact" in rows.columns else pd.Series(False, index=rows.index)
    table = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + rows["key"].astype(str),
        "State": rows["state"],
        "SLA Clock": np.where(flagged.fillna(False).astype(bool), "⚠ Set after the fact", ""),
        "Days Overdue (bd)": rows["overdue"].astype("Int64"),
        "SLA Due": rows["sla_due"].dt.date,
        "Completed": rows["closed_day"].dt.date,
        "Priority": rows["priority_bucket"],
        "Size": rows["size"],
        "SLA (bd)": rows["sla_bd"],
        "Jira Status": rows["status"],
        "Assignee": rows["assignee_name"],
        "Business Lead": rows["lead"],
        "Summary": rows.get("summary", pd.Series("", index=rows.index)).fillna("").astype(str).str[:120],
    })
    return pd.concat([table, _comment_columns(rows, today, hol)], axis=1).reset_index(drop=True)


def _after_fact_table(j: pd.DataFrame, dates: pd.DataFrame, start: pd.Timestamp, hol: np.ndarray) -> pd.DataFrame:
    rows = j[j["outcome"].eq("Done") & j["clock_after_fact"] & (j["closed_day"] >= start)].copy()
    if rows.empty:
        return pd.DataFrame()
    rows["changed_on"] = rows["key"].map(dates["clock_changed_on"])
    rows["after_bd"] = ipr._busdays_between(rows["closed_day"], rows["changed_on"], hol)
    rows = rows.sort_values("closed_day", ascending=False)
    return pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + rows["key"].astype(str),
        "Completed": rows["closed_day"].dt.date,
        "Target Start Set/Moved On": rows["changed_on"].dt.date,
        "Business Days After Done": rows["after_bd"].astype("Int64"),
        "Target Start Now": rows["start_day"].dt.date,
        "SLA Result As Recorded": np.where(rows["breached"].astype(bool), "Late", "Met"),
        "Priority": rows["priority_bucket"],
        "Size": rows["size"],
        "Assignee": rows["assignee_name"],
        "Business Lead": rows["lead"],
        "Summary": rows.get("summary", pd.Series("", index=rows.index)).fillna("").astype(str).str[:120],
    }).reset_index(drop=True)


# ── Entry point ─────────────────────────────────────────────────────────────────

def _original_starts(t: pd.DataFrame, originals: dict, hol: np.ndarray) -> tuple[pd.DataFrame, int]:
    """Judge re-planned tickets on the Target start they had before their first dashboard update."""
    hit = t["key"].isin(originals)
    if not hit.any():
        return t, 0
    t = t.copy()
    t.loc[hit, "start_day"] = pd.to_datetime(t.loc[hit, "key"].map(originals))
    t.loc[hit, "sla_due"] = ipr._add_busdays(t.loc[hit, "start_day"], t.loc[hit, "sla_bd"].astype(float), hol)
    return t, int(hit.sum())


def build_sla_visuals(df_issues: pd.DataFrame, time_period_days: int = DEFAULT_WINDOW, filters: dict | None = None,
                      include_on_track: bool = False, include_completed_late: bool = True,
                      judge_original_start: bool = True, original_starts: dict | None = None,
                      history: pd.DataFrame | None = None, exclude_after_fact: bool = False) -> dict[str, Any]:
    """`filters` keys: assignees, leads, priorities, stages (lists; empty = all)."""
    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    pe = facts[facts["sla_applies"]]
    ip = ipr.build_in_progress_visuals(df_issues, risk_basis="SLA")
    bl = build_backlog_visuals(df_issues, risk_basis="SLA")
    open_all = es._stage_risk(facts, today, hol, ip, bl)
    open_all = open_all[open_all["sla_applies"]]

    payload = _empty_payload()
    payload["filter_options"] = {
        "assignees": sorted(set(pe["assignee_name"].dropna())),
        "leads": sorted(set(pe["lead"].dropna())),
        "priorities": [p for p in ipr.PRIORITY_ORDER if (pe["priority_bucket"] == p).any()],
        "stages": [s for s in es.STAGE_ORDER if (open_all["stage"] == s).any()],
    }

    t = _apply_filters(pe, filters, with_stage=False)
    open_t = _apply_filters(open_all, filters, with_stage=True)
    replanned = 0
    if judge_original_start:
        if original_starts is None:
            try:
                from reports.jira_dates import original_target_starts
                original_starts = original_target_starts()
            except Exception:
                original_starts = {}
        t, replanned = _original_starts(t, original_starts, hol)
    j = _judged(t, today)
    has_history = chg.has_history(history)
    dates = chg.ticket_date_summary(chg.date_changes(history, hol), pe) if has_history else None
    j["clock_after_fact"] = j["key"].map(dates["clock_after_fact"]).fillna(False).astype(bool) if has_history else False
    start = today - pd.Timedelta(days=int(time_period_days))
    prev_start = start - pd.Timedelta(days=int(time_period_days))
    rated = j[~j["clock_after_fact"]] if exclude_after_fact else j
    now_r, prev_r = _rates(rated, start, today), _rates(rated, prev_start, start)
    done_now = j[j["outcome"].eq("Done") & (j["closed_day"] >= start) & (j["closed_day"] < today)]
    after_now = done_now[done_now["clock_after_fact"]]

    def delta(key):
        return None if now_r[key] is None or prev_r[key] is None else now_r[key] - prev_r[key]

    horizon = ipr._add_busdays(pd.Series([today]), pd.Series([float(DUE_SOON_BD)]), hol).iloc[0]
    open_with_clock = open_t["sla_due"].notna()
    payload["kpis"] = {
        **now_r,
        "due_rate_delta": delta("due_rate"), "done_rate_delta": delta("done_rate"),
        "open_breached": int(open_t["risk"].eq("Breached").sum()),
        "open_at_risk": int(open_t["risk"].isin(["Likely Late", "At Risk"]).sum()),
        "due_soon": int((open_with_clock & (open_t["sla_due"] >= today) & (open_t["sla_due"] <= horizon)).sum()),
        "open_total": int(len(open_t)),
        "no_clock": int((~open_with_clock).sum()),
        "goal": BREACH_GOAL,
        "replanned": replanned,
        "window_days": int(time_period_days),
        "history": has_history,
        "after_fact": int(len(after_now)),
        "after_fact_n": int(len(done_now)),
        "after_fact_met": int((~after_now["breached"].astype(bool)).sum()),
        "excluding_after_fact": bool(exclude_after_fact and has_history),
    }
    payload["trend_fig"], payload["trend_df"] = _trend_figure(j, today)
    payload["heatmap_fig"] = _heatmap_figure(j, today)
    payload["stage_fig"] = es._stage_figure(open_t) if not open_t.empty else None
    payload["due_soon_fig"] = _due_soon_figure(open_t, today, hol)
    payload["detail_df"] = _detail_table(open_t, today, hol, include_on_track)
    payload["breached_df"] = _breached_table(j, today, hol, start, include_completed_late,
                                             (filters or {}).get("stages"))
    payload["after_fact_df"] = _after_fact_table(j, dates, start, hol) if has_history else pd.DataFrame()
    payload["as_of"] = today.date()
    return payload
