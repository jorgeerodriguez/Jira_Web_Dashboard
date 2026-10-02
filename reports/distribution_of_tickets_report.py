"""Distribution of Ticket's Age: how old open work is against its SLA, and where it has gone quiet.

Open tickets only, tickets-only (no Features or Initiatives), grouped into the same stages as the
Executive Summary. Age is in business days. Four views:

- Age against SLA -- share of each ticket's SLA already used (business days since Target start /
  Priority x Size SLA), by stage, with the 100% line. Release Management (CAR) tickets have no PE SLA
  and are not judged.
- Silence -- business days since the last human comment (or since creation when nobody has
  commented), by stage. Long silence flags forgotten work.
- Age trend -- median and 75th-percentile age of the tickets that were open at the end of each of the
  last 12 weeks, reconstructed from created and closed dates.
- Table -- every open ticket with its age, SLA used and silence, past-SLA and quietest first.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import executive_summary as es
from reports import in_progress_report as ipr


JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
SILENT_THRESHOLD_BD = 10
TREND_WEEKS = 12
SILENCE_BANDS = [(-1, 4, "Under 5 days"), (4, 9, "5–9 days"), (9, 19, "10–19 days"), (19, 100_000, "20+ days")]
SILENCE_SHADES = ["#cfe0f6", "#7fb0ea", "#2a78d6", "#0b3d91"]   # one hue, light = recent, dark = quiet
SERIES_COLORS = es.SERIES_COLORS
INK = ipr.INK
GRID = es.GRID


def _empty_payload(message: str | None = None) -> dict:
    return {
        "open_count": 0, "median_age_bd": None, "past_sla": 0, "silent_count": 0, "oldest": None,
        "sla_fig": None, "silence_fig": None, "trend_fig": None,
        "status_df": pd.DataFrame(), "tickets_df": pd.DataFrame(), "error_message": message,
    }


def _last_human_comment(comments) -> pd.Timestamp:
    if not isinstance(comments, list):
        return pd.NaT
    times = [c["created"] for c in comments if pd.notna(c.get("created"))]
    return max(times) if times else pd.NaT


def _open_tickets(df_issues: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    t = es._facts(df_issues, today, hol)
    o = t[~t["is_closed"]].copy()
    now = pd.Series(today, index=o.index)
    o["age_bd"] = ipr._busdays_between(o["created_day"], now, hol).fillna(0)
    started = o["start_day"].where(o["start_day"] <= today)
    o["sla_used"] = (ipr._busdays_between(started, now, hol) / o["sla_bd"]).where(o["sla_applies"])
    comments = o["comments"] if "comments" in o.columns else pd.Series([None] * len(o), index=o.index)
    o["last_human_day"] = ipr._to_day(comments.apply(_last_human_comment), ipr.LOCAL_TZ)
    o["silent_bd"] = ipr._busdays_between(o["last_human_day"].fillna(o["created_day"]), now, hol).fillna(0)
    o["silence_band"] = pd.cut(o["silent_bd"], [b[0] for b in SILENCE_BANDS] + [SILENCE_BANDS[-1][1]],
                               labels=[b[2] for b in SILENCE_BANDS])
    return t, o


def _stage_label(o: pd.DataFrame) -> dict:
    counts = o["stage"].value_counts()
    return {s: f"{s} ({counts[s]})" for s in counts.index}


def _sla_figure(o: pd.DataFrame) -> go.Figure | None:
    judged = o[o["sla_used"].notna()]
    if judged.empty:
        return None
    stages = [s for s in es.STAGE_ORDER if s in set(judged["stage"])]
    fig = go.Figure()
    for stage in stages:
        rows = judged[judged["stage"] == stage]
        fig.add_trace(go.Box(
            x=rows["sla_used"], y=[f"{stage} ({len(rows)})"] * len(rows), orientation="h", name=stage,
            marker=dict(color=SERIES_COLORS[0], size=7, opacity=0.75), line=dict(color=SERIES_COLORS[0], width=2),
            fillcolor="rgba(42,120,214,0.15)", boxpoints="all", jitter=0.4, pointpos=0,
            customdata=np.stack([rows["key"], rows["priority_bucket"], rows["sla_bd"], rows["age_bd"]], axis=-1),
            hovertemplate="<b>%{customdata[0]}</b><br>%{x:.0%} of a %{customdata[2]}-day SLA used"
                          "<br>%{customdata[1]} priority · %{customdata[3]} business days old<extra></extra>",
            showlegend=False,
        ))
    fig.add_vline(x=1.0, line_width=2, line_dash="dash", line_color=INK)
    fig.add_annotation(x=1.0, y=1, xref="x", yref="paper", text="SLA due", showarrow=False,
                       xanchor="left", yanchor="bottom", font={"color": INK})
    upper = max(1.25, float(judged["sla_used"].quantile(0.97)) * 1.1)
    fig.update_xaxes(tickformat=".0%", range=[0, upper], title="Share of SLA used", gridcolor=GRID)
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_layout(height=max(300, 70 * len(stages) + 100), margin=dict(l=10, r=10, t=30, b=10))
    return fig


def _silence_figure(o: pd.DataFrame) -> go.Figure:
    labels = _stage_label(o)
    stages = [s for s in es.STAGE_ORDER if s in labels]
    counts = (o.groupby(["stage", "silence_band"], observed=False).size().unstack(fill_value=0)
              .reindex(stages, fill_value=0))
    fig = go.Figure()
    for band, shade in zip([b[2] for b in SILENCE_BANDS], SILENCE_SHADES):
        fig.add_trace(go.Bar(
            y=[labels[s] for s in stages], x=counts[band] if band in counts else [0] * len(stages),
            name=band, orientation="h",
            marker=dict(color=shade, line=dict(color="rgba(255,255,255,0.9)", width=2)),
            hovertemplate="%{y} · last human comment " + band + " ago: %{x} ticket(s)<extra></extra>",
        ))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Open tickets", gridcolor=GRID)
    fig.update_layout(barmode="stack", height=max(280, 46 * len(stages) + 110),
                      legend_title_text="Since last human comment (business days)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=50, b=10))
    return fig


def _trend_figure(t: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray) -> tuple[go.Figure, pd.DataFrame]:
    week_ends = pd.date_range(end=today, periods=TREND_WEEKS, freq="W-SUN")
    rows = []
    for end in week_ends:
        open_then = t[(t["created_day"] <= end) & (t["closed_day"].isna() | (t["closed_day"] > end))]
        ages = ipr._busdays_between(open_then["created_day"], pd.Series(end, index=open_then.index), hol).dropna()
        rows.append({"week_end": end, "open": len(open_then),
                     "median": float(ages.median()) if len(ages) else np.nan,
                     "p75": float(ages.quantile(0.75)) if len(ages) else np.nan})
    trend = pd.DataFrame(rows)
    fig = go.Figure()
    for col, name, color in [("median", "Median age", SERIES_COLORS[0]), ("p75", "75th percentile", SERIES_COLORS[1])]:
        fig.add_trace(go.Scatter(
            x=trend["week_end"], y=trend[col], name=name, mode="lines+markers", line=dict(color=color, width=2),
            marker=dict(size=8), customdata=trend[["open"]],
            hovertemplate=f"{name}: %{{y:.0f}} business days (%{{customdata[0]}} open)<extra></extra>",
        ))
    fig.update_xaxes(title="Week ending", tickformat="%b %d", gridcolor=GRID)
    fig.update_yaxes(title="Age of open tickets (business days)", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=320, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig, trend


def build_distribution_visuals(df_issues: pd.DataFrame) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    t, o = _open_tickets(df_issues, today, hol)
    if o.empty:
        return _empty_payload("No open tickets.")

    trend_fig, trend = _trend_figure(t, today, hol)
    oldest = o.sort_values("age_bd", ascending=False).iloc[0]

    status_df = (o.groupby(["stage", "status"])
                 .agg(tickets=("key", "size"), median_age=("age_bd", "median"), median_silent=("silent_bd", "median"),
                      past_sla=("sla_used", lambda s: int((s > 1).sum())))
                 .reset_index())
    status_df["_order"] = status_df["stage"].map({s: i for i, s in enumerate(es.STAGE_ORDER)})
    status_df = status_df.sort_values(["_order", "tickets"], ascending=[True, False]).drop(columns="_order")
    status_df.columns = ["Stage", "Status", "Open Tickets", "Median Age (bd)", "Median Silence (bd)", "Past SLA"]

    table = o.assign(_past=o["sla_used"].fillna(0) > 1).sort_values(
        ["_past", "sla_used", "silent_bd"], ascending=[False, False, False], na_position="last")
    summary = table["summary"] if "summary" in table.columns else pd.Series("", index=table.index)
    tickets_df = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + table["key"].astype(str),
        "Stage": table["stage"],
        "Status": table["status"],
        "Priority": table["priority_bucket"],
        "Size": table["size"],
        "Age (bd)": table["age_bd"].astype(int),
        "SLA Used %": (table["sla_used"] * 100).round(0),
        "Silent (bd)": table["silent_bd"].astype(int),
        "Last Human Comment": table["last_human_day"].dt.date,
        "Assignee": table["assignee_name"],
        "Business Lead": table["lead"],
        "Summary": summary.fillna("").astype(str).str[:90],
    })

    median_age = float(o["age_bd"].median())
    return {
        "open_count": int(len(o)),
        "median_age_bd": median_age,
        "past_sla": int((o["sla_used"] > 1).sum()),
        "silent_count": int((o["silent_bd"] >= SILENT_THRESHOLD_BD).sum()),
        "oldest": {"key": oldest["key"], "age_bd": int(oldest["age_bd"]), "stage": oldest["stage"]},
        "sla_fig": _sla_figure(o),
        "silence_fig": _silence_figure(o),
        "trend_fig": trend_fig,
        "trend_df": trend,
        "status_df": status_df,
        "tickets_df": tickets_df,
        "open_df": o,
        "p75_age_bd": float(o["age_bd"].quantile(0.75)),
        "error_message": None,
    }
