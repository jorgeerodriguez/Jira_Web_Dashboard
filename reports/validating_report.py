"""Validating: work that is finished and waiting for the requester to confirm it.

PE tickets only (no Features, Initiatives or Release Management CAR tickets); times in business days.
Built on the status history (data/fetch_change_history.py): every stay in Validating is an *episode*
from entering the status to the next status change.

- Waiting now: tickets in Validating today and how long each has waited since it entered Validating
  (not since the ticket was created), with SLA time left and the latest human comment, so the oldest can
  be nudged.
- Validation time: how long episodes took (P50 / P85), monthly, overall and by business lead (which
  requesting teams are slow to confirm).
- Rework: the share of episodes that went back to In Progress / To Do / Triage instead of being closed,
  a quality signal.
- SLA impact: the SLA clock keeps running during Validating, so the page counts late tickets that would
  have met their SLA without the time spent waiting in Validating.
- Policy what-ifs (views only, nothing changes): pausing the SLA clock during Validating, and closing
  tickets after N business days without a reply.

Without the history the page falls back to the list of tickets in Validating now.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import change_history as chg
from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports.service_level_agreement_report import _last_comment

JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
VALIDATING = "validating"
NUDGE_BD = 5                   # waiting this many business days or more: nudge the requester
WINDOW_DAYS = 90               # KPIs: episodes that ended in the last 90 days vs the 90 before
OUTCOME_DAYS = 180             # SLA impact, by business lead, what-ifs
TREND_MONTHS = 12
MIN_LEAD_EPISODES = 5
AUTO_CLOSE_OPTIONS = (5, 10, 15)
# Leaving Validating for one of these means the work was not accepted and went back to be worked on.
REWORK_STATUSES = {"in progress", "to do", "triage", "tech discovery required"}
CLOSED_EXITS = {"done", "will not do", "released successfully to production"}
LINE, LINE2 = "#2a78d6", "#eb6834"
INK, GRID = ipr.INK, es.GRID


def _empty_payload(message: str | None = None) -> dict:
    return {"error_message": message, "history": False, "kpis": {}, "waiting_df": pd.DataFrame(),
            "trend_fig": None, "rework_fig": None, "lead_fig": None, "dist_fig": None, "what_if": {},
            "lead_df": pd.DataFrame()}


def _pct(values: pd.Series, q: float):
    return float(values.quantile(q)) if len(values) else None


def _comment_cols(rows: pd.DataFrame) -> tuple[list, list]:
    last = [(_last_comment(c)) for c in rows.get("comments", pd.Series([[]] * len(rows), index=rows.index))]
    when = [pd.to_datetime(w).tz_convert(ipr.LOCAL_TZ).date() if pd.notna(w) else None for w, _ in last]
    return when, [text for _, text in last]


def _waiting_table(now: pd.DataFrame) -> pd.DataFrame:
    if now.empty:
        return pd.DataFrame()
    now = now.sort_values(["waiting_bd", "sla_left_bd"], ascending=[False, True], na_position="last")
    when, text = _comment_cols(now)
    return pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + now["key"].astype(str),
        "Waiting (bd)": now["waiting_bd"].astype("Int64"),
        "In Validating Since": now["entered"].dt.date,
        "Round": now["rounds"].astype("Int64"),
        "SLA Due": now["sla_due"].dt.date,
        "SLA Left (bd)": now["sla_left_bd"].astype("Int64"),
        "Requester": now.get("reporter_name", pd.Series("Unknown", index=now.index)).fillna("Unknown"),
        "Business Lead": now["lead"],
        "Assignee": now["assignee_name"],
        "Priority": now["priority_bucket"],
        "Size": now["size"],
        "Last Comment": when,
        "Latest Comment": text,
        "Summary": now.get("summary", pd.Series("", index=now.index)).fillna("").astype(str).str[:100],
    }).reset_index(drop=True)


def _trend_figures(ended: pd.DataFrame, today: pd.Timestamp) -> tuple[go.Figure | None, go.Figure | None]:
    current = today.to_period("M")
    months = pd.period_range(current - TREND_MONTHS, current, freq="M")
    e = ended.assign(month=ended["left"].dt.to_period("M"))
    e = e[e["month"].isin(months)]
    if e.empty:
        return None, None
    g = e.groupby("month")
    labels = [m.strftime("%b %y") for m in months]
    partial = months == current
    p50 = g["bd"].median().reindex(months)
    p85 = g["bd"].quantile(0.85).reindex(months)
    n = g.size().reindex(months, fill_value=0)
    rework = g["rework"].mean().reindex(months)

    def marker(color):
        return dict(size=7, color=np.where(partial, "white", color), line=dict(color=color, width=2))

    t = go.Figure()
    for series, name, color in ((p85, "P85 (most finish within)", LINE2), (p50, "Median", LINE)):
        t.add_trace(go.Scatter(x=labels, y=series, name=name, mode="lines+markers", line=dict(color=color, width=2),
                               marker=marker(color), customdata=n,
                               hovertemplate="%{x}: %{y:.1f} bd · %{customdata} validations<extra>" + name + "</extra>"))
    t.update_layout(height=320, legend=dict(orientation="h", y=1.12, x=0), margin=dict(l=10, r=10, t=40, b=10))
    t.update_yaxes(title="Business days in Validating", gridcolor=GRID, rangemode="tozero")
    t.update_xaxes(nticks=7)

    r = go.Figure(go.Scatter(x=labels, y=rework, mode="lines+markers", line=dict(color=LINE, width=2),
                             marker=marker(LINE), customdata=np.stack([n, g["rework"].sum().reindex(months, fill_value=0)], axis=1),
                             hovertemplate="%{x}: %{y:.0%} sent back (%{customdata[1]} of %{customdata[0]})<extra></extra>"))
    r.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10), showlegend=False)
    r.update_yaxes(tickformat=".0%", gridcolor=GRID, rangemode="tozero", title="Sent back to be worked on")
    r.update_xaxes(nticks=7)
    return t, r


def _lead_outputs(recent: pd.DataFrame) -> tuple[go.Figure | None, pd.DataFrame]:
    g = recent.groupby("lead").agg(validations=("bd", "size"), median=("bd", "median"),
                                   p85=("bd", lambda s: s.quantile(0.85)), rework=("rework", "mean"))
    g = g[g["validations"] >= MIN_LEAD_EPISODES].sort_values("median")
    if g.empty:
        return None, pd.DataFrame()
    fig = go.Figure(go.Bar(
        y=g.index, x=g["median"], orientation="h", marker=dict(color=LINE, line=dict(color="white", width=2)),
        customdata=np.stack([g["validations"], g["p85"]], axis=1),
        text=[f"{m:.1f} bd · n={int(n)}" for m, n in zip(g["median"], g["validations"])], textposition="outside",
        cliponaxis=False, hovertemplate="%{y}<br>Median %{x:.1f} bd · P85 %{customdata[1]:.1f} bd · "
                                        "%{customdata[0]} validations<extra></extra>"))
    fig.update_layout(height=max(260, 30 * len(g) + 90), margin=dict(l=10, r=40, t=10, b=10))
    fig.update_xaxes(title="Median business days in Validating", gridcolor=GRID,
                     range=[0, max(float(g["median"].max()) * 1.35, 1)])
    table = pd.DataFrame({"Business Lead": g.index, "Validations": g["validations"].astype(int).values,
                          "Median (bd)": g["median"].round(1).values, "P85 (bd)": g["p85"].round(1).values,
                          "Sent Back %": (g["rework"] * 100).round(0).values}).iloc[::-1].reset_index(drop=True)
    return fig, table


def _distribution_figure(recent: pd.DataFrame) -> go.Figure | None:
    if recent.empty:
        return None
    values = recent["bd"].clip(upper=30)
    counts = values.value_counts().sort_index()
    fig = go.Figure(go.Bar(x=counts.index, y=counts.values,
                           marker=dict(color=np.where(counts.index >= NUDGE_BD, LINE2, LINE), line=dict(color="white", width=1)),
                           hovertemplate="%{x} business days: %{y} validations<extra></extra>"))
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), showlegend=False, bargap=0.1)
    fig.update_xaxes(title=f"Business days in Validating (30 = 30+; orange = {NUDGE_BD}+)", dtick=5)
    fig.update_yaxes(title="Validations", gridcolor=GRID)
    return fig


def build_validating_visuals(df_issues: pd.DataFrame, history: pd.DataFrame | None = None) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload("📥 Fetch Jira tickets from the sidebar to see Validating visuals.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    pe = facts[facts["sla_applies"]].copy()
    in_val = pe[pe["status"].astype(str).str.strip().str.casefold().eq(VALIDATING)].copy()
    payload = _empty_payload()
    payload["history"] = chg.has_history(history)

    episodes = chg.status_episodes(history, VALIDATING, today, hol)
    episodes = episodes[episodes["key"].isin(pe["key"])].copy()
    exit_norm = episodes["exit_to"].astype(str).str.strip().str.casefold()
    episodes["rework"] = exit_norm.isin(REWORK_STATUSES)
    episodes["closed"] = exit_norm.isin(CLOSED_EXITS)

    # ── Waiting now ──
    open_ep = episodes[episodes["open"].astype(bool)] if len(episodes) else episodes
    entered = open_ep.sort_values("entered").groupby("key")["entered"].last() if len(open_ep) else pd.Series(dtype="datetime64[ns]")
    rounds = episodes.groupby("key").size() if len(episodes) else pd.Series(dtype=int)
    in_val["entered"] = pd.to_datetime(in_val["key"].map(entered.to_dict()))
    in_val["rounds"] = in_val["key"].map(rounds.to_dict())
    in_val["waiting_bd"] = ipr._busdays_between(in_val["entered"].dt.normalize(), pd.Series(today, index=in_val.index), hol)
    in_val["sla_left_bd"] = np.where(
        in_val["sla_due"].notna(),
        [np.busday_count(today.date(), d.date(), holidays=hol) if pd.notna(d) else np.nan for d in in_val["sla_due"]],
        np.nan)
    payload["waiting_df"] = _waiting_table(in_val)

    waiting = in_val["waiting_bd"].dropna()
    kpis = {
        "in_validating": int(len(in_val)),
        "waiting_median": float(waiting.median()) if len(waiting) else None,
        "nudge": int((waiting >= NUDGE_BD).sum()),
        "past_sla": int((in_val["sla_left_bd"] < 0).sum()),
        "nudge_bd": NUDGE_BD, "window_days": WINDOW_DAYS, "outcome_days": OUTCOME_DAYS,
    }
    payload["kpis"] = kpis
    if not payload["history"]:
        return payload

    # ── Validation time and rework ──
    ended = episodes[~episodes["open"].astype(bool)].copy()
    start = today - pd.Timedelta(days=WINDOW_DAYS)
    prev = start - pd.Timedelta(days=WINDOW_DAYS)
    now_e = ended[ended["left"] >= start]
    prev_e = ended[(ended["left"] >= prev) & (ended["left"] < start)]
    kpis.update({
        "validations": int(len(now_e)),
        "p50": _pct(now_e["bd"], 0.5), "p50_prev": _pct(prev_e["bd"], 0.5),
        "p85": _pct(now_e["bd"], 0.85), "p85_prev": _pct(prev_e["bd"], 0.85),
        "rework": float(now_e["rework"].mean()) if len(now_e) else None,
        "rework_prev": float(prev_e["rework"].mean()) if len(prev_e) else None,
        "rework_n": int(now_e["rework"].sum()),
        "same_day": float((now_e["bd"] == 0).mean()) if len(now_e) else None,
    })
    lookup = pe.set_index("key")
    closers = now_e[now_e["closed"]].join(lookup[["reporter_name", "assignee_name"]], on="key")
    if len(closers):
        kpis["closed_by_requester"] = float((closers["exit_by"] == closers["reporter_name"]).mean())
        kpis["closed_by_assignee"] = float((closers["exit_by"] == closers["assignee_name"]).mean())
    payload["trend_fig"], payload["rework_fig"] = _trend_figures(ended, today)

    recent = ended[ended["left"] >= today - pd.Timedelta(days=OUTCOME_DAYS)].join(lookup[["lead"]], on="key")
    payload["lead_fig"], payload["lead_df"] = _lead_outputs(recent)
    payload["dist_fig"] = _distribution_figure(recent)

    # ── SLA impact and what-ifs (Done tickets with an SLA clock, last OUTCOME_DAYS) ──
    done = pe[pe["outcome"].eq("Done") & pe["sla_due"].notna()
              & (pe["closed_day"] >= today - pd.Timedelta(days=OUTCOME_DAYS))].copy()
    in_window = ended[ended["left"].notna()].copy()
    in_window = in_window.join(lookup[["start_day", "closed_day"]], on="key")
    # Only validation time inside the SLA clock (after Target start, before Done) moves the result.
    clip_start = in_window[["entered", "start_day"]].max(axis=1).dt.normalize()
    clip_end = in_window[["left", "closed_day"]].min(axis=1).dt.normalize()
    in_window["clock_bd"] = ipr._busdays_between(clip_start, clip_end, hol).fillna(0)
    val_bd = in_window.groupby("key")["clock_bd"].sum()
    done["val_bd"] = done["key"].map(val_bd).fillna(0)
    done["late"] = done["closed_day"] > done["sla_due"]
    done["late_bd"] = ipr._busdays_between(done["sla_due"], done["closed_day"], hol).fillna(0)
    done["late_paused"] = done["late"] & (done["late_bd"] > done["val_bd"])
    late = int(done["late"].sum())
    kpis.update({
        "done_judged": int(len(done)),
        "through_validating": float((done["val_bd"] > 0).mean()) if len(done) else None,
        "late": late,
        "late_only_validating": int((done["late"] & ~done["late_paused"]).sum()),
        "breach_now": float(done["late"].mean()) if len(done) else None,
        "breach_paused": float(done["late_paused"].mean()) if len(done) else None,
    })
    payload["what_if"] = {
        n: {"episodes": int((recent["bd"] > n).sum()), "share": float((recent["bd"] > n).mean()) if len(recent) else 0.0,
            "days_saved": float((recent["bd"] - n).clip(lower=0).sum())}
        for n in AUTO_CLOSE_OPTIONS
    }
    payload["recent_n"] = int(len(recent))
    return payload


def build_in_progress_visuals(df_issues: pd.DataFrame) -> dict:
    """Backward-compatible alias for existing callers."""
    return build_validating_visuals(df_issues)
