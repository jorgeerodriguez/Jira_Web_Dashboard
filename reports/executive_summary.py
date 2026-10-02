"""Executive Summary: the state of Platform Engineering's work on one page, for leadership.

Read top to bottom: generated headlines, health tiles compared with the previous period (the sidebar
Lookback), flow of work in vs out, SLA compliance trend, where open work sits and how much of it is at
risk, the short list of tickets that need attention, aging, and two ways-of-working signals.

Tickets only (no Features or Initiatives, see reports/in_progress_report.prepare_tickets). Risk and
forecasts come from the In Progress and Backlog reports, and the conversation signals from Teams
Conversations, so the numbers here match the detail pages.
"""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from reports import in_progress_report as ipr
from reports.backlog_report import build_backlog_visuals

try:
    from reports.word_of_the_month_report import build_word_of_the_month_visuals
except ImportError:  # optional: the page still renders without the conversation signals
    build_word_of_the_month_visuals = None


JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
SLA_TARGET = 0.90                     # drawn on the SLA trend; edit to the team's goal
FLOW_WEEKS = 12
SLA_TREND_MONTHS = 6
ATTENTION_ROWS = 10
AT_RISK_SLA_USED = 0.80               # stages without a forecast: At Risk once 80% of the SLA is used
CLOSED_STATUSES = {"done": "Done", "released successfully to production": "Released",
                   "will not do": "Will Not Do", "❌ rolled back": "Rolled Back"}
STAGES = {
    "Backlog": {"to do", "tech discovery required", "triage"},
    "In Progress": {"in progress"},
    "Validating": {"validating", "reviewing", "test and validate"},
    "Blocked & On Hold": {"blocked", "on hold"},
    "Release": {"plan release", "prepare release", "review release plan", "build release", "deploy release",
                "staged car", "post implementation review"},
}
STAGE_ORDER = list(STAGES) + ["Other"]
RISK_ORDER = ["Breached", "Likely Late", "At Risk", "On Track", "Not Assessed"]
RISK_LABELS = {**ipr.RISK_LABELS, "Not Assessed": "○ Not assessed"}
RISK_COLORS = {**ipr.RISK_COLORS, RISK_LABELS["Not Assessed"]: "#94a3b8"}
SERIES_COLORS = ("#2a78d6", "#eb6834")                       # categorical slots 1 and 2
PRIORITY_SHADES = {"Urgent": "#0b3d91", "High": "#2a78d6", "Medium": "#7fb0ea", "Low/None": "#cfe0f6"}
AGE_BANDS = [(-1, 7, "0–7 days"), (7, 30, "8–30 days"), (30, 90, "31–90 days"), (90, 100_000, "90+ days")]
INK = ipr.INK
GRID = "rgba(148,163,184,0.25)"


# ── Small helpers ───────────────────────────────────────────────────────────────

def _stage(status: str) -> str:
    s = str(status).strip().casefold()
    return next((stage for stage, statuses in STAGES.items() if s in statuses), "Other")


def _pct_change(now: float, before: float) -> float | None:
    return None if not before else (now - before) / before


def _link(keys: pd.Series) -> pd.Series:
    return JIRA_BROWSE_BASE_URL + keys.astype(str)


def _empty_payload() -> dict:
    return {"total_open": 0, "error_message": None}


# ── Facts ───────────────────────────────────────────────────────────────────────

def _facts(df_issues: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray) -> pd.DataFrame:
    t = ipr.prepare_tickets(df_issues)
    status_norm = t["status"].astype(str).str.strip().str.casefold()
    t["outcome"] = status_norm.map(CLOSED_STATUSES)
    t["is_closed"] = t["outcome"].notna()
    t["stage"] = np.where(t["is_closed"], "Closed", t["status"].map(_stage))
    t["created_day"] = ipr._to_day(t["created"], ipr.LOCAL_TZ)
    finished = t.get("status_category_changed", pd.Series(pd.NaT, index=t.index))
    t["closed_day"] = (ipr._to_day(finished, ipr.LOCAL_TZ).fillna(ipr._to_day(t["updated"], ipr.LOCAL_TZ))
                       .where(t["is_closed"]))
    t["start_day"] = ipr._to_day(t["planned_start_date"], timezone.utc)
    t["target_end_day"] = ipr._to_day(t.get("target_end_date", pd.Series(pd.NaT, index=t.index)), timezone.utc)
    t["sla_size"] = t["size"].where(t["size"] != "Unestimated", ipr.ASSUMED_SIZE)
    t["sla_bd"] = [ipr.SLA_BUSINESS_DAYS[p][s] for p, s in zip(t["priority_bucket"], t["sla_size"])]
    # No PE SLA for Release Management (CAR) tickets: no due date, so never judged or counted.
    t["sla_due"] = ipr._add_busdays(t["start_day"], t["sla_bd"].astype(float), hol).where(t["sla_applies"])
    t["age_days"] = (today - t["created_day"]).dt.days.clip(lower=0)
    t["lead"] = (t.get("business_lead", pd.Series(index=t.index)).fillna("Unknown").astype(str)
                 .replace({"": "Unknown", "None": "Unknown", "nan": "Unknown"}))
    return t


def _open_at(t: pd.DataFrame, day: pd.Timestamp) -> int:
    return int(((t["created_day"] <= day) & (t["closed_day"].isna() | (t["closed_day"] > day))).sum())


def _sla_met(done: pd.DataFrame) -> pd.Series:
    """Per completed ticket with a Target start: finished on or before its SLA due date."""
    judged = done[done["sla_due"].notna()]
    return judged["closed_day"] <= judged["sla_due"]


# ── Sections ────────────────────────────────────────────────────────────────────

def _tiles(t: pd.DataFrame, today: pd.Timestamp, lookback: int, ip: dict, coverage: dict | None) -> dict:
    now_from = today - pd.Timedelta(days=lookback)
    prev_from = now_from - pd.Timedelta(days=lookback)

    def window(col: str, start, end) -> pd.Series:
        return (t[col] > start) & (t[col] <= end)

    done = t["outcome"].eq("Done")
    completed_now = int((window("closed_day", now_from, today) & done).sum())
    completed_prev = int((window("closed_day", prev_from, now_from) & done).sum())
    closed_now = int(window("closed_day", now_from, today).sum())
    created_now = int(window("created_day", now_from, today).sum())
    closed_prev = int(window("closed_day", prev_from, now_from).sum())
    created_prev = int(window("created_day", prev_from, now_from).sum())
    sla_now = _sla_met(t[window("closed_day", now_from, today) & done])
    sla_prev = _sla_met(t[window("closed_day", prev_from, now_from) & done])
    open_now, open_before = _open_at(t, today), _open_at(t, now_from)
    status_norm = t["status"].astype(str).str.strip().str.casefold()
    return {
        "open": open_now, "open_delta": open_now - open_before,
        "completed": completed_now, "completed_prev": completed_prev,
        "completed_change": _pct_change(completed_now, completed_prev),
        "created": created_now, "closed": closed_now,
        "net_flow": closed_now - created_now,
        "net_flow_delta": (closed_now - created_now) - (closed_prev - created_prev),
        "sla_compliance": float(sla_now.mean()) if len(sla_now) else None,
        "sla_compliance_delta": (float(sla_now.mean()) - float(sla_prev.mean())) if len(sla_now) and len(sla_prev) else None,
        "sla_judged": int(len(sla_now)),
        "in_progress_on_track": (ip["on_track"] / ip["total_in_progress"]) if ip.get("total_in_progress") else None,
        "in_progress_total": ip.get("total_in_progress", 0),
        "blocked": int(status_norm.eq("blocked").sum()),
        "on_hold": int(status_norm.eq("on hold").sum()),
        "coverage": coverage.get("coverage") if coverage else None,
        "coverage_delta": coverage.get("coverage_delta") if coverage else None,
        "created_24h": int((t["created_day"] >= today - pd.Timedelta(days=1)).sum()),
        "resolved_24h": int(((t["closed_day"] >= today - pd.Timedelta(days=1)) & done).sum()),
    }


def _flow_figure(t: pd.DataFrame, today: pd.Timestamp) -> tuple[go.Figure, pd.DataFrame]:
    this_week = today.to_period("W-SUN").start_time
    weeks = pd.date_range(end=this_week - pd.Timedelta(days=7), periods=FLOW_WEEKS, freq="W-MON")
    week_of = lambda s: s.dt.to_period("W-SUN").dt.start_time  # noqa: E731
    created = t.groupby(week_of(t["created_day"])).size().reindex(weeks, fill_value=0)
    closed_rows = t[t["is_closed"] & t["closed_day"].notna()]
    closed = (closed_rows.groupby([week_of(closed_rows["closed_day"]), "outcome"]).size()
              .unstack(fill_value=0).reindex(weeks, fill_value=0))
    for outcome in CLOSED_STATUSES.values():
        if outcome not in closed.columns:
            closed[outcome] = 0
    outcomes = list(CLOSED_STATUSES.values())
    flow = pd.DataFrame({"week": weeks, "Created": created.values, "Closed": closed[outcomes].sum(axis=1).values,
                         **{o: closed[o].values for o in outcomes}})
    fig = go.Figure()
    breakdown = flow[outcomes].values
    for col, color in zip(["Created", "Closed"], SERIES_COLORS):
        detail = ("<br>Done %{customdata[0]} · Released %{customdata[1]}"
                  "<br>Will Not Do %{customdata[2]} · Rolled Back %{customdata[3]}") if col == "Closed" else ""
        fig.add_trace(go.Scatter(
            x=flow["week"], y=flow[col], name=col, mode="lines+markers", line=dict(color=color, width=2),
            marker=dict(size=8), customdata=breakdown,
            hovertemplate=f"{col}: %{{y}}{detail}<extra></extra>",
        ))
    fig.update_xaxes(title=None, tickformat="%b %d", gridcolor=GRID)
    fig.update_yaxes(title="Tickets per week", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=320, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig, flow


def _sla_trend_figure(t: pd.DataFrame, today: pd.Timestamp) -> tuple[go.Figure | None, pd.DataFrame]:
    done = t[t["outcome"].eq("Done") & t["sla_due"].notna()].copy()
    done["month"] = done["closed_day"].dt.to_period("M")
    this_month = today.to_period("M")
    months = pd.period_range(this_month - (SLA_TREND_MONTHS - 1), this_month, freq="M")
    done = done[done["month"].isin(months)]
    if done.empty:
        return None, pd.DataFrame()
    done["met"] = done["closed_day"] <= done["sla_due"]
    trend = (done.groupby("month").agg(met=("met", "mean"), tickets=("key", "size"))
             .reindex(months).rename_axis("month").reset_index())
    trend["label"] = trend["month"].dt.strftime("%b %Y") + np.where(trend["month"] == this_month, " (so far)", "")
    fig = go.Figure(go.Scatter(
        x=trend["label"], y=trend["met"], mode="lines+markers+text", line=dict(color=SERIES_COLORS[0], width=2),
        marker=dict(size=9), text=[f"{v:.0%}" if pd.notna(v) else "" for v in trend["met"]],
        textposition="top center", customdata=trend[["tickets"]], name="SLA met",
        hovertemplate="%{x}: %{y:.0%} of %{customdata[0]} completed tickets met SLA<extra></extra>",
    ))
    fig.add_hline(y=SLA_TARGET, line_dash="dash", line_color=INK, line_width=1.5,
                  annotation_text=f"Target {SLA_TARGET:.0%}", annotation_position="bottom right",
                  annotation_font_color=INK)
    low = min(trend["met"].min(skipna=True), SLA_TARGET)
    fig.update_yaxes(tickformat=".0%", range=[max(0.0, low - 0.1), 1.05], title=None, gridcolor=GRID)
    fig.update_layout(height=320, showlegend=False, margin=dict(l=10, r=10, t=20, b=10))
    return fig, trend


def _stage_risk(t: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray, ip: dict, bl: dict) -> pd.DataFrame:
    """One row per open ticket: stage, risk (SLA basis) and the reason it needs attention, if any."""
    open_t = t[~t["is_closed"]].copy()
    open_t["forecast_p85"] = pd.NaT
    key_risk: dict[str, str] = {}
    for frame, p85_col in ((ip.get("forecast_df", pd.DataFrame()), "Forecast P85"),
                           (bl.get("forecast_df", pd.DataFrame()), "Finish P85")):
        if frame.empty:
            continue
        keys = frame["Ticket"].str.split("/").str[-1]
        labels = frame["SLA Status"].map({label: risk for risk, label in RISK_LABELS.items()})
        key_risk.update(dict(zip(keys, labels)))
        p85 = dict(zip(keys, frame[p85_col]))
        mask = open_t["key"].isin(p85)
        open_t.loc[mask, "forecast_p85"] = open_t.loc[mask, "key"].map(p85)

    # Stages without a forecast: judged on how much of the SLA has already been used.
    started = open_t["start_day"].where(open_t["start_day"] <= today)
    used = ipr._busdays_between(started, pd.Series(today, index=open_t.index), hol) / open_t["sla_bd"]

    def simple(used_share, due) -> str:
        if pd.isna(due):
            return "Not Assessed"
        if today > due:
            return "Breached"
        return "At Risk" if used_share >= AT_RISK_SLA_USED else "On Track"

    open_t["risk"] = [key_risk.get(k) or simple(u, d) for k, u, d in zip(open_t["key"], used, open_t["sla_due"])]
    past_due = open_t["sla_due"].notna() & (open_t["sla_due"] < today)
    open_t["days_past_sla"] = np.where(
        past_due, ipr._busdays_between(open_t["sla_due"], pd.Series(today, index=open_t.index), hol), 0)
    target_passed = open_t["target_end_day"].notna() & (open_t["target_end_day"] < today)
    waiting_bd = ipr._busdays_between(open_t["created_day"], pd.Series(today, index=open_t.index), hol)

    def reason(row, waited, passed) -> str | None:
        if row["risk"] == "Breached":
            return f"SLA breached {int(row['days_past_sla'])} business days ago"
        if row["risk"] == "Likely Late":
            return "Forecast to miss its SLA"
        if row["stage"] == "Blocked & On Hold" and passed:
            return f"{row['status']} and past its Target End Date"
        if row["stage"] == "Backlog" and row["sla_applies"] and waited > row["sla_bd"]:
            return f"Waiting {int(waited)} business days, longer than its {row['sla_bd']}-day SLA"
        return None

    open_t["reason"] = [reason(row, w, p) for (_, row), w, p in zip(open_t.iterrows(), waiting_bd, target_passed)]
    return open_t


def _stage_figure(open_t: pd.DataFrame) -> go.Figure:
    counts = open_t.groupby(["stage", "risk"]).size().unstack(fill_value=0)
    stages = [s for s in STAGE_ORDER if s in counts.index]
    fig = go.Figure()
    for risk in RISK_ORDER:
        if risk not in counts.columns:
            continue
        label = RISK_LABELS[risk]
        fig.add_trace(go.Bar(
            y=stages, x=counts.loc[stages, risk], name=label, orientation="h",
            marker=dict(color=RISK_COLORS[label], line=dict(color="rgba(255,255,255,0.9)", width=2)),
            hovertemplate="%{y} · " + label + ": %{x} ticket(s)<extra></extra>",
        ))
    totals = counts.loc[stages].sum(axis=1)
    fig.add_trace(go.Scatter(y=stages, x=totals, mode="text", text=[f"  {n}" for n in totals],
                             textposition="middle right", showlegend=False, hoverinfo="skip"))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Open tickets", gridcolor=GRID, range=[0, totals.max() * 1.15 if len(totals) else 1])
    fig.update_layout(barmode="stack", height=max(280, 46 * len(stages) + 110), legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _attention_table(open_t: pd.DataFrame) -> pd.DataFrame:
    flagged = open_t[open_t["reason"].notna()].copy()
    if flagged.empty:
        return pd.DataFrame()
    risk_rank = {r: i for i, r in enumerate(RISK_ORDER)}
    priority_rank = {p: i for i, p in enumerate(ipr.PRIORITY_ORDER)}
    flagged["_r"] = flagged["risk"].map(risk_rank)
    flagged["_p"] = flagged["priority_bucket"].map(priority_rank)
    flagged = flagged.sort_values(["_r", "_p", "days_past_sla", "age_days"], ascending=[True, True, False, False])
    top = flagged.head(ATTENTION_ROWS)
    summary = top["summary"] if "summary" in top.columns else pd.Series("", index=top.index)
    return pd.DataFrame({
        "Ticket": _link(top["key"]),
        "Why it needs attention": top["reason"],
        "Stage": top["stage"],
        "Priority": top["priority_bucket"],
        "Assignee": top["assignee_name"],
        "Business Lead": top["lead"],
        "Days Open": top["age_days"].astype(int),
        "Forecast P85": pd.to_datetime(top["forecast_p85"], errors="coerce").dt.date,
        "Summary": summary.fillna("").astype(str).str[:90],
    })


def _aging_figures(open_t: pd.DataFrame) -> tuple[go.Figure, go.Figure]:
    open_t = open_t.copy()
    bands = [b[2] for b in AGE_BANDS]
    open_t["band"] = pd.cut(open_t["age_days"], [b[0] for b in AGE_BANDS] + [AGE_BANDS[-1][1]], labels=bands)
    counts = (open_t.groupby(["band", "priority_bucket"], observed=False).size()
              .unstack(fill_value=0).reindex(bands, fill_value=0))
    age_fig = go.Figure()
    for priority in ipr.PRIORITY_ORDER:
        if priority in counts.columns:
            age_fig.add_trace(go.Bar(
                y=bands, x=counts[priority], name=priority, orientation="h",
                marker=dict(color=PRIORITY_SHADES[priority], line=dict(color="rgba(255,255,255,0.9)", width=2)),
                hovertemplate="%{y} · " + priority + ": %{x} ticket(s)<extra></extra>",
            ))
    age_fig.update_yaxes(autorange="reversed", title=None)
    age_fig.update_xaxes(title="Open tickets", gridcolor=GRID)
    age_fig.update_layout(barmode="stack", height=280, legend_title_text="Priority",
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                          margin=dict(l=10, r=10, t=40, b=10))

    leads = (open_t.groupby("lead").agg(avg_age=("age_days", "mean"), tickets=("key", "size"))
             .query("tickets >= 2").sort_values("avg_age", ascending=False).head(10))
    lead_fig = go.Figure(go.Bar(
        y=leads.index, x=leads["avg_age"], orientation="h", marker_color=SERIES_COLORS[0],
        text=[f"{a:.0f} days · {n} open" for a, n in zip(leads["avg_age"], leads["tickets"])],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: average %{x:.0f} days old<extra></extra>",
    ))
    lead_fig.update_yaxes(autorange="reversed", title=None)
    lead_fig.update_xaxes(title="Average age of open tickets (days)", gridcolor=GRID,
                          range=[0, (leads["avg_age"].max() if len(leads) else 1) * 1.35])
    lead_fig.update_layout(height=max(260, 30 * len(leads) + 90), margin=dict(l=10, r=10, t=10, b=10))
    return age_fig, lead_fig


def _headlines(tiles: dict, sla_trend: pd.DataFrame, ip: dict, lookback: int, open_t: pd.DataFrame,
               conversations: dict | None) -> list[tuple[str, str]]:
    """Plain-English bullets as (tone, text); tone is good / warn / bad / info."""
    lines: list[tuple[str, str]] = []
    # Full months only: a partial current month (a handful of tickets) would swing the headline.
    trend = sla_trend.dropna(subset=["met"]) if not sla_trend.empty else sla_trend
    if len(trend):
        trend = trend[~trend["label"].str.endswith("(so far)")]
    if len(trend) >= 2:
        first, last = trend.iloc[0], trend.iloc[-1]
        change = (last["met"] - first["met"]) * 100
        lines.append(("good" if last["met"] >= SLA_TARGET else "warn",
                      f"SLA compliance is **{last['met']:.0%}** in {last['label']}, "
                      f"{'up' if change >= 0 else 'down'} {abs(change):.0f} pts from {first['label']} "
                      f"(target {SLA_TARGET:.0%})."))
    change = tiles["completed_change"]
    if change is not None:
        lines.append(("good" if change >= 0 else "warn",
                      f"**{tiles['completed']}** tickets completed in the last {lookback} days, "
                      f"{'up' if change >= 0 else 'down'} {abs(change):.0%} on the previous {lookback} days."))
    flow = tiles["net_flow"]
    lines.append(("good" if flow >= 0 else "warn",
                  f"{tiles['closed']} closed vs {tiles['created']} created in the last {lookback} days: "
                  f"open work is {'shrinking' if flow >= 0 else 'growing'} by {abs(flow)}."))
    breached = int((open_t["risk"] == "Breached").sum())
    likely = int((open_t["risk"] == "Likely Late").sum())
    if breached or likely:
        lines.append(("bad" if breached else "warn",
                      f"**{breached}** open tickets have breached their SLA and **{likely}** more are forecast to miss it."))
    if ip.get("total_in_progress"):
        lines.append(("info", f"{ip['on_track']} of {ip['total_in_progress']} in-progress tickets are on track "
                              "for their SLA."))
    if tiles["blocked"] or tiles["on_hold"]:
        lines.append(("warn", f"{tiles['blocked']} tickets are Blocked and {tiles['on_hold']} are On Hold."))
    if conversations and conversations.get("recommendations"):
        top = conversations["recommendations"][0]
        lines.append(("info", f"Biggest friction in ticket comments: **{top['theme']}** "
                              f"(~{top['total_extra']:.0f} extra business days over "
                              f"{conversations['start_month']} to {conversations['end_month']})."))
    return lines


# ── Entry points ────────────────────────────────────────────────────────────────

def build_executive_summary_data(df_issues: pd.DataFrame, lookback_days: int = 7) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload()
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return {**_empty_payload(), "error_message": "Ticket data is missing required columns."}

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    t = _facts(df_issues, today, hol)
    ip = ipr.build_in_progress_visuals(df_issues, risk_basis="SLA")
    bl = build_backlog_visuals(df_issues, risk_basis="SLA")
    conversations = None
    if build_word_of_the_month_visuals is not None and "comments" in df_issues.columns:
        conversations = build_word_of_the_month_visuals(df_issues)
        if conversations.get("error_message"):
            conversations = None

    tiles = _tiles(t, today, lookback_days, ip, conversations)
    flow_fig, flow = _flow_figure(t, today)
    sla_fig, sla_trend = _sla_trend_figure(t, today)
    open_t = _stage_risk(t, today, hol, ip, bl)
    age_fig, lead_fig = _aging_figures(open_t)
    return {
        "total_open": int(len(open_t)),
        "error_message": None,
        "as_of": today.date(),
        "lookback_days": lookback_days,
        "tiles": tiles,
        "headlines": _headlines(tiles, sla_trend, ip, lookback_days, open_t, conversations),
        "flow_fig": flow_fig,
        "flow_df": flow,
        "sla_fig": sla_fig,
        "sla_trend_df": sla_trend,
        "stage_fig": _stage_figure(open_t),
        "stage_counts": open_t.groupby(["stage", "risk"]).size().unstack(fill_value=0),
        "attention_df": _attention_table(open_t),
        "attention_total": int(open_t["reason"].notna().sum()),
        "age_fig": age_fig,
        "lead_age_fig": lead_fig,
        "conversations": conversations,
        "open_df": open_t,
    }


def _delta(value, fmt: str = "{:+,}", suffix: str = "") -> str | None:
    return None if value is None else fmt.format(value) + suffix


def render_executive_summary(df_issues: pd.DataFrame, report_date, lookback_days: int) -> None:
    st.title("📋 Executive Summary")
    data = build_executive_summary_data(df_issues, lookback_days=lookback_days)
    if data["total_open"] == 0:
        st.info(data["error_message"] or "📥 Fetch Jira tickets from the sidebar to display the Executive Summary.")
        return
    st.caption(f"Platform Engineering · Data as of {data['as_of']:%B %d, %Y} · Compared with the previous "
               f"{lookback_days} days (sidebar Lookback) · Tickets only (no Features or Initiatives)")

    # ── Headlines ──
    icon = {"good": "✓", "warn": "!", "bad": "✖", "info": "•"}
    st.markdown("\n".join(f"- {icon[tone]} {text}" for tone, text in data["headlines"]))

    # ── Health tiles ──
    tl = data["tiles"]
    k = st.columns(7)
    k[0].metric("Open Tickets", f"{tl['open']:,}", _delta(tl["open_delta"]), delta_color="inverse",
                help=f"Open now, and the change since {lookback_days} days ago.")
    k[1].metric(f"Completed ({lookback_days}d)", f"{tl['completed']:,}",
                _delta(tl["completed_change"], "{:+.0%}"),
                help="Tickets moved to Done, vs the previous period.")
    k[2].metric(f"Net Flow ({lookback_days}d)", f"{tl['net_flow']:+,}", _delta(tl["net_flow_delta"]),
                help="Closed minus created (all closing outcomes). Positive means open work is shrinking.")
    k[3].metric("SLA Compliance", f"{tl['sla_compliance']:.0%}" if tl["sla_compliance"] is not None else "—",
                _delta(None if tl["sla_compliance_delta"] is None else tl["sla_compliance_delta"] * 100,
                       "{:+.0f}", " pts"),
                help=f"Tickets completed in the period that finished within their SLA "
                     f"({tl['sla_judged']} with a Target start).")
    k[4].metric("In Progress On Track",
                f"{tl['in_progress_on_track']:.0%}" if tl["in_progress_on_track"] is not None else "—",
                help=f"Of {tl['in_progress_total']} in-progress tickets, forecast to finish within SLA "
                     "(In Progress page).")
    k[5].metric("Blocked / On Hold", f"{tl['blocked']} / {tl['on_hold']}")
    k[6].metric("Comment Coverage", f"{tl['coverage']:.0%}" if tl["coverage"] is not None else "—",
                _delta(None if tl["coverage_delta"] is None else tl["coverage_delta"] * 100, "{:+.0f}", " pts"),
                help="Completed tickets (last 3 months) with a human comment (Teams Conversations page).")

    st.divider()

    # ── Flow and SLA ──
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Flow: Created vs Closed")
        st.caption(f"Per week, last {FLOW_WEEKS} full weeks. Closed counts every outcome (Done, Released, "
                   f"Will Not Do, Rolled Back). Last 24h: {tl['created_24h']} created, "
                   f"{tl['resolved_24h']} resolved (Done).")
        st.plotly_chart(data["flow_fig"], width="stretch")
    with c2:
        st.subheader("SLA Compliance Trend")
        st.caption("Share of completed tickets that finished within their SLA (business days from Target start). "
                   "Release Management (CAR) tickets have no PE SLA and are not counted.")
        if data["sla_fig"] is not None:
            st.plotly_chart(data["sla_fig"], width="stretch")
        else:
            st.info("No completed tickets with a Target start yet.")

    # ── Where the work is ──
    st.subheader("Where the Work Is")
    st.caption("Open tickets by stage, coloured by SLA risk. In Progress and Backlog use their forecasts; other "
               f"stages are At Risk once {AT_RISK_SLA_USED:.0%} of the SLA is used. Not assessed = no Target start, or a "
               "Release Management (CAR) ticket, which has no PE SLA.")
    st.plotly_chart(data["stage_fig"], width="stretch")

    # ── Needs attention ──
    st.subheader("Needs Attention")
    if data["attention_df"].empty:
        st.success("Nothing needs attention right now.")
    else:
        st.caption(f"Top {len(data['attention_df'])} of {data['attention_total']} flagged tickets, most serious first. "
                   "Full lists are on the In Progress, Backlog and Blocked & On Hold pages.")
        st.dataframe(
            data["attention_df"], width="stretch", hide_index=True,
            column_config={
                "Ticket": st.column_config.LinkColumn("Ticket", help="Open Jira ticket", display_text=r".*/([^/]+)$"),
                "Why it needs attention": st.column_config.TextColumn("Why it needs attention", width="large"),
            },
        )

    # ── Aging ──
    a1, a2 = st.columns(2)
    with a1:
        st.subheader("Age of Open Work")
        st.caption("Open tickets by how long ago they were created, split by priority.")
        st.plotly_chart(data["age_fig"], width="stretch")
    with a2:
        st.subheader("Oldest Work by Business Lead")
        st.caption("Average age of open tickets, oldest first (leads with at least 2 open tickets).")
        st.plotly_chart(data["lead_age_fig"], width="stretch")

    # ── Ways of working ──
    conv = data["conversations"]
    if conv:
        st.subheader("Ways of Working")
        w1, w2 = st.columns(2)
        with w1:
            top = conv["recommendations"][0] if conv.get("recommendations") else None
            if top:
                st.info(f"**Biggest friction: {top['theme']}** · {top['tickets']} tickets, about "
                        f"+{top['extra_per_ticket']:.1f} business days each.\n\n{top['action']}")
        with w2:
            st.info(f"**Comment coverage {conv['coverage']:.0%}** of completed tickets "
                    f"({conv['start_month']} to {conv['end_month']}); the assignee commented on "
                    f"{conv['closing_note']:.0%}.")
        st.caption("Details on the Teams Conversations page.")
