"""Velocity: how fast work flows from request to done, and what slows it.

PE tickets only (no Features, Initiatives or Release Management CAR tickets) completed (moved to Done)
in the last N days, picked by completion date so slow tickets are not dropped. Times are business days:

- Lead time: created -> Done (what the requester experiences).
- Waiting: created -> Target start. In progress: Target start -> Done (from creation when Target start
  is earlier). Flow efficiency = time in progress / lead time, summed over tickets with a Target start.

Views: what we can promise (lead-time distribution with P50/P85/P95), waiting vs in progress by
priority, whether sizes predict effort, an SLA reality check (85th-percentile time in progress as a
share of each Priority x Size SLA), and what was delivered. Per-person speed is on the In Progress page.

PE_TEAM_MEMBERS is shared with the probability report and the Personal Dashboard.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go


PE_TEAM_MEMBERS = [
    "Adam Shero", "Bolanle", "Denys Loboda", "Denys Naumenko", "Jeff Stuewe", "Oleh Kuzo",
    "Omar Saunders", "Owen Tregoning", "Pavlo Myshok", "Randall Puterbaugh", "simon.davison",
    "Taras Protsiv", "Tom Terry", "Trevor Atchley", "vladyslav.zhyhulin", "Zack.Amadi", "Unassigned",
]

SIZES = ["Small", "Medium", "Large", "XL"]
SIZE_SHADES = {"Small": "#cfe0f6", "Medium": "#7fb0ea", "Large": "#2a78d6", "XL": "#0b3d91", "Unestimated": "#cbd5e1"}
WORK_COLOR, WAIT_COLOR = "#2a78d6", "#eb6834"         # categorical slots 1 and 2
MIN_CELL = 5                                          # SLA reality check: fewer tickets than this are flagged
INK = "#334155"
GRID = "rgba(148,163,184,0.25)"


def _empty_payload(message: str | None = None) -> dict:
    return {"error_message": message, "kpis": {}, "promise_fig": None, "wait_work_fig": None, "size_fig": None,
            "sla_reality_fig": None, "delivered_fig": None, "priority_df": pd.DataFrame(), "sla_reality_df": pd.DataFrame()}


def _flow_times(done: pd.DataFrame, hol: np.ndarray, ipr) -> pd.DataFrame:
    d = done.copy()
    d["lead_bd"] = ipr._busdays_between(d["created_day"], d["closed_day"], hol).fillna(0)
    d["has_start"] = d["start_day"].notna() & (d["start_day"] <= d["closed_day"])
    began = d["start_day"].where(d["has_start"])
    began = began.where(began >= d["created_day"], d["created_day"].where(d["has_start"]))  # no work before the request
    d["wait_bd"] = ipr._busdays_between(d["created_day"], began, hol)
    d["work_bd"] = ipr._busdays_between(began, d["closed_day"], hol)
    return d


def _promise_figure(d: pd.DataFrame) -> go.Figure:
    p50, p85, p95 = d["lead_bd"].quantile([0.5, 0.85, 0.95])
    cap = int(np.ceil(max(d["lead_bd"].quantile(0.97), p95 + 1)))
    shown = d["lead_bd"].clip(upper=cap)
    counts = shown.value_counts().reindex(range(cap + 1), fill_value=0)
    fig = go.Figure(go.Bar(
        x=counts.index, y=counts.values, marker_color=WORK_COLOR,
        customdata=[f"{cap}+" if v == cap else str(v) for v in counts.index],
        hovertemplate="%{customdata} business days: %{y} tickets<extra></extra>",
    ))
    for value, label, dash in [(p50, "50%", "dot"), (p85, "85%", "dash"), (p95, "95%", "dashdot")]:
        fig.add_vline(x=value, line_dash=dash, line_color=INK, line_width=1.5,
                      annotation_text=f"{label} within {value:.0f} bd", annotation_position="top right",
                      annotation_font_color=INK)
    fig.update_xaxes(title=f"Lead time, created → Done (business days; {cap}+ grouped)", gridcolor=GRID)
    fig.update_yaxes(title="Tickets", gridcolor=GRID)
    fig.update_layout(height=340, bargap=0.1, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _wait_work_figure(d: pd.DataFrame, priorities: list[str]) -> tuple[go.Figure, pd.DataFrame]:
    timed = d[d["has_start"] & (d["lead_bd"] > 0)]
    sums = timed.groupby("priority_bucket")[["wait_bd", "work_bd"]].sum().reindex(priorities).fillna(0)
    total = (sums["wait_bd"] + sums["work_bd"]).replace(0, np.nan)
    share = pd.DataFrame({"Waiting to start": sums["wait_bd"] / total, "In progress": sums["work_bd"] / total})
    stats = d.groupby("priority_bucket").agg(
        tickets=("key", "size"), lead_p50=("lead_bd", "median"), lead_p85=("lead_bd", lambda s: s.quantile(0.85)),
    ).reindex(priorities)
    labels = [f"{p} · {stats.loc[p, 'tickets']:.0f} tickets, median {stats.loc[p, 'lead_p50']:.0f} bd"
              if pd.notna(stats.loc[p, "tickets"]) else p for p in priorities]
    fig = go.Figure()
    for col, color in [("In progress", WORK_COLOR), ("Waiting to start", WAIT_COLOR)]:
        fig.add_trace(go.Bar(
            y=labels, x=share[col], name=col, orientation="h",
            marker=dict(color=color, line=dict(color="rgba(255,255,255,0.9)", width=2)),
            text=[f"{v:.0%}" if pd.notna(v) else "" for v in share[col]], textposition="inside",
            hovertemplate="%{y}<br>" + col + ": %{x:.0%} of lead time<extra></extra>",
        ))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(tickformat=".0%", range=[0, 1], title="Share of total lead time", gridcolor=GRID)
    fig.update_layout(barmode="stack", height=300, legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    table = pd.DataFrame({
        "Priority": priorities,
        "Delivered": stats["tickets"].fillna(0).astype(int).values,
        "Lead Time P50 (bd)": stats["lead_p50"].round(1).values,
        "Lead Time P85 (bd)": stats["lead_p85"].round(1).values,
        "Flow Efficiency %": (share["In progress"] * 100).round(0).values,
    })
    return fig, table


def _size_figure(d: pd.DataFrame) -> go.Figure:
    timed = d[d["has_start"]]
    order = SIZES + ["Unestimated"]
    cap = float(timed["work_bd"].quantile(0.97)) if len(timed) else 10
    fig = go.Figure()
    for size in order:
        rows = timed[timed["size"] == size]
        if rows.empty:
            continue
        fig.add_trace(go.Box(
            y=rows["work_bd"], name=f"{size} ({len(rows)})", boxpoints="outliers",
            marker=dict(color=SIZE_SHADES[size] if size == "Unestimated" else WORK_COLOR, size=5),
            line=dict(color=INK if size == "Unestimated" else WORK_COLOR, width=2),
            fillcolor="rgba(42,120,214,0.15)" if size != "Unestimated" else "rgba(148,163,184,0.25)",
            hovertemplate=f"{size}: %{{y:.0f}} business days in progress<extra></extra>", showlegend=False,
        ))
    fig.update_yaxes(title="In progress, Target start → Done (business days)", gridcolor=GRID,
                     range=[0, max(cap * 1.1, 5)])
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=20, b=10))
    return fig


def _sla_reality(d: pd.DataFrame, priorities: list[str], sla_table: dict) -> tuple[go.Figure, pd.DataFrame]:
    timed = d[d["has_start"] & d["size"].isin(SIZES)]
    z, text, rows = [], [], []
    for p in priorities:
        z_row, t_row = [], []
        for s in SIZES:
            cell = timed[(timed["priority_bucket"] == p) & (timed["size"] == s)]
            sla = sla_table[p][s]
            if cell.empty:
                z_row.append(np.nan)
                t_row.append(f"SLA {sla}d<br>no data")
                continue
            p85 = float(cell["work_bd"].quantile(0.85))
            ratio = p85 / sla
            flag = "*" if len(cell) < MIN_CELL else ""
            z_row.append(ratio)
            t_row.append(f"{ratio:.0%}{flag}<br>P85 {p85:.0f} / SLA {sla}d<br>n={len(cell)}")
            rows.append({"Priority": p, "Size": s, "Tickets": len(cell), "SLA (bd)": sla,
                         "P85 In Progress (bd)": round(p85, 1), "P85 / SLA %": round(ratio * 100)})
        z.append(z_row)
        text.append(t_row)
    fig = go.Figure(go.Heatmap(
        z=z, x=SIZES, y=priorities, text=text, texttemplate="%{text}", zmin=0, zmax=1.2, zmid=0.6,
        colorscale=[[0, "#2a78d6"], [0.5, "#f1f5f9"], [1, "#eb6834"]], xgap=2, ygap=2,
        colorbar=dict(title="P85 / SLA", tickformat=".0%"),
        hovertemplate="%{y} × %{x}<br>%{text}<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Estimated size")
    return fig, pd.DataFrame(rows)


def _delivered_figure(d: pd.DataFrame, priorities: list[str]) -> go.Figure:
    counts = d.groupby(["priority_bucket", "size"]).size().unstack(fill_value=0).reindex(priorities, fill_value=0)
    fig = go.Figure()
    for size in SIZES + ["Unestimated"]:
        if size in counts.columns:
            fig.add_trace(go.Bar(
                x=priorities, y=counts[size], name=size,
                marker=dict(color=SIZE_SHADES[size], line=dict(color="rgba(255,255,255,0.9)", width=2)),
                hovertemplate="%{x} · " + size + ": %{y} delivered<extra></extra>",
            ))
    totals = counts.sum(axis=1)
    fig.add_trace(go.Scatter(x=priorities, y=totals, mode="text", text=[f"{n:,}" for n in totals],
                             textposition="top center", showlegend=False, hoverinfo="skip"))
    fig.update_yaxes(title="Tickets delivered", gridcolor=GRID, range=[0, totals.max() * 1.15 if len(totals) else 1])
    fig.update_layout(barmode="stack", height=340, legend_title_text="Size",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def build_velocity_visuals(df_issues: pd.DataFrame, time_period_days: int = 90) -> dict:
    from reports import executive_summary as es   # local import: probability report imports this module
    from reports import in_progress_report as ipr

    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    since = today - pd.Timedelta(days=time_period_days)
    done = facts[facts["sla_applies"] & facts["outcome"].eq("Done") & (facts["closed_day"] > since)]
    if done.empty:
        return _empty_payload(f"No PE tickets completed in the last {time_period_days} days.")
    d = _flow_times(done, hol, ipr)
    priorities = [p for p in ipr.PRIORITY_ORDER if (d["priority_bucket"] == p).any()]

    timed = d[d["has_start"] & (d["lead_bd"] > 0)]
    p50, p85, p95 = d["lead_bd"].quantile([0.5, 0.85, 0.95])
    payload = _empty_payload()
    payload["kpis"] = {
        "delivered": int(len(d)),
        "lead_p50": float(p50), "lead_p85": float(p85), "lead_p95": float(p95),
        "work_median": float(d.loc[d["has_start"], "work_bd"].median()) if d["has_start"].any() else None,
        "flow_efficiency": float(timed["work_bd"].sum() / timed["lead_bd"].sum()) if len(timed) else None,
        "sized_share": float(d["size"].isin(SIZES).mean()),
        "with_start_share": float(d["has_start"].mean()),
    }
    payload["promise_fig"] = _promise_figure(d)
    payload["wait_work_fig"], payload["priority_df"] = _wait_work_figure(d, priorities)
    payload["size_fig"] = _size_figure(d)
    payload["sla_reality_fig"], payload["sla_reality_df"] = _sla_reality(d, priorities, ipr.SLA_BUSINESS_DAYS)
    payload["delivered_fig"] = _delivered_figure(d, priorities)
    payload["flow_df"] = d
    return payload
