"""Distribution per Business Leader: the service each requesting business lead gets from PE.

Tickets-only (no Features or Initiatives) and PE work only: Release Management (CAR) tickets are left
out. Platform Engineering's own work (the business leads in INTERNAL_LEADS) is shown as one
"Platform Engineering (internal)" row and kept out of the comparison charts by default, because it is
most of the volume and would hide the requesting leads.

For the selected months (by when tickets were created or completed):
- Scorecard per business lead: requested, delivered, won't do, median wait (created -> Done, business
  days), SLA met, open now, open past SLA, oldest open ticket, top friction theme in comments.
- Demand vs delivery, SLA met and wait time by lead, monthly demand trend (last 12 months), priority
  mix, and open work by SLA risk.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports.backlog_report import build_backlog_visuals

try:
    from reports.word_of_the_month_report import SYMPTOM_THEMES, THEMES, tag_themes
except ImportError:  # friction column is optional
    THEMES, SYMPTOM_THEMES, tag_themes = {}, set(), None


# Business leads whose tickets are Platform Engineering's own work, shown as one internal row.
INTERNAL_LEADS = {"jorge rodriguez"}
INTERNAL_LABEL = "Platform Engineering (internal)"
UNKNOWN_LABEL = "Unknown"
TREND_MONTHS = 12
TREND_TOP_LEADS = 5
SMALL_SAMPLE = 5           # fewer delivered tickets than this: SLA % shown hollow and flagged
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#94a3b8"]  # slots 1-5 + Other
INK = ipr.INK
GRID = es.GRID


def _empty_payload(message: str | None = None) -> dict:
    return {
        "available_months": [], "start_month": None, "end_month": None, "error_message": message,
        "kpis": {}, "scorecard_df": pd.DataFrame(), "demand_fig": None, "sla_fig": None, "wait_fig": None,
        "trend_fig": None, "priority_fig": None, "open_fig": None,
    }


def _lead_group(lead: pd.Series) -> pd.Series:
    clean = lead.fillna(UNKNOWN_LABEL).astype(str).str.strip()
    clean = clean.where(~clean.isin(["", "None", "nan"]), UNKNOWN_LABEL)
    return clean.where(~clean.str.casefold().isin(INTERNAL_LEADS), INTERNAL_LABEL)


def _is_requesting(lead: pd.Series) -> pd.Series:
    return ~lead.isin([INTERNAL_LABEL, UNKNOWN_LABEL])


def _top_friction(frame: pd.DataFrame) -> pd.Series:
    """Most common cause theme in human comments per lead, as 'Theme (share)'."""
    if tag_themes is None or "comments" not in frame.columns or frame.empty:
        return pd.Series(dtype=object)
    causes = [t for t in THEMES if t not in SYMPTOM_THEMES]
    out = {}
    for lead, rows in frame.groupby("lead"):
        counts: Counter = Counter()
        for comments in rows["comments"]:
            if isinstance(comments, list) and comments:
                found = tag_themes([c.get("body", "") for c in comments])
                counts.update(t for t in causes if found[t] is not None)
        if counts:
            theme, n = counts.most_common(1)[0]
            out[lead] = f"{theme} ({n / len(rows):.0%})"
    return pd.Series(out, dtype=object)


# ── Figures ─────────────────────────────────────────────────────────────────────

def _lead_order(card: pd.DataFrame, include_internal: bool) -> list[str]:
    rows = card if include_internal else card[_is_requesting(card["lead"])]
    return rows.sort_values("requested", ascending=False)["lead"].tolist()


def _demand_figure(card: pd.DataFrame, leads: list[str]) -> go.Figure:
    plot = card.set_index("lead").loc[leads]
    fig = go.Figure()
    for lead in leads:
        fig.add_trace(go.Scatter(x=[plot.loc[lead, "requested"], plot.loc[lead, "delivered"]], y=[lead, lead],
                                 mode="lines", line=dict(color="rgba(148,163,184,0.7)", width=3),
                                 showlegend=False, hoverinfo="skip"))
    for col, name, color in [("requested", "Requested", CATEGORICAL[0]), ("delivered", "Delivered (Done)", CATEGORICAL[1])]:
        fig.add_trace(go.Scatter(
            x=plot[col], y=leads, mode="markers", name=name,
            marker=dict(size=12, color=color, line=dict(color="white", width=2)),
            hovertemplate="%{y}: %{x} " + name.lower() + "<extra></extra>",
        ))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Tickets in the selected months", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=max(280, 32 * len(leads) + 110), legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _sla_figure(card: pd.DataFrame, leads: list[str]) -> go.Figure:
    plot = card.set_index("lead").loc[leads]
    plot = plot[plot["sla_judged"] > 0]
    small = plot["sla_judged"] < SMALL_SAMPLE
    fig = go.Figure(go.Scatter(
        x=plot["sla_met"], y=plot.index, mode="markers+text",
        marker=dict(size=12, color=np.where(small, "white", CATEGORICAL[0]),
                    line=dict(color=CATEGORICAL[0], width=2)),
        text=[f"  {v:.0%} (n={n})" for v, n in zip(plot["sla_met"], plot["sla_judged"])], textposition="middle right",
        hovertemplate="%{y}: %{x:.0%} of delivered tickets met SLA<extra></extra>", showlegend=False,
    ))
    fig.add_vline(x=es.SLA_TARGET, line_dash="dash", line_color=INK, line_width=1.5,
                  annotation_text=f"Target {es.SLA_TARGET:.0%}", annotation_position="top",
                  annotation_font_color=INK)
    fig.update_xaxes(tickformat=".0%", range=[max(0.0, min(plot["sla_met"].min() if len(plot) else 0.5, 0.5) - 0.05), 1.18],
                     title="Delivered tickets that met their SLA", gridcolor=GRID)
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_layout(height=max(280, 32 * len(plot) + 110), margin=dict(l=10, r=10, t=30, b=10))
    return fig


def _wait_figure(card: pd.DataFrame, leads: list[str]) -> go.Figure:
    plot = card.set_index("lead").loc[leads]
    plot = plot[plot["delivered"] > 0]
    fig = go.Figure(go.Bar(
        y=plot.index, x=plot["median_wait_bd"], orientation="h", marker_color=CATEGORICAL[0],
        text=[f"{w:.0f} bd · n={n}" for w, n in zip(plot["median_wait_bd"], plot["delivered"])],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: median %{x:.1f} business days from request to Done<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Median wait, created → Done (business days)", gridcolor=GRID,
                     range=[0, max(plot["median_wait_bd"].max() if len(plot) else 1, 1) * 1.35])
    fig.update_layout(height=max(280, 32 * len(plot) + 110), margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _trend_figure(t: pd.DataFrame, today: pd.Timestamp, include_internal: bool) -> go.Figure:
    months = pd.period_range(today.to_period("M") - (TREND_MONTHS - 1), today.to_period("M"), freq="M")
    rows = t[t["created_day"].dt.to_period("M").isin(months)]
    if not include_internal:
        rows = rows[_is_requesting(rows["lead"])]
    top = rows["lead"].value_counts().head(TREND_TOP_LEADS).index.tolist()
    rows = rows.assign(group=rows["lead"].where(rows["lead"].isin(top), "Other"))
    counts = (rows.groupby([rows["created_day"].dt.to_period("M"), "group"]).size()
              .unstack(fill_value=0).reindex(months, fill_value=0))
    fig = go.Figure()
    for group, color in zip(top + ["Other"], CATEGORICAL):
        if group in counts.columns:
            fig.add_trace(go.Scatter(
                x=counts.index.strftime("%b %Y"), y=counts[group], name=group, mode="lines+markers",
                line=dict(color=color, width=2), marker=dict(size=7),
                hovertemplate=f"{group}: %{{y}} requested<extra></extra>",
            ))
    fig.update_xaxes(title=None, gridcolor=GRID)
    fig.update_yaxes(title="Tickets requested per month", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=340, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _priority_figure(requested: pd.DataFrame, leads: list[str]) -> go.Figure:
    mix = (requested.groupby(["lead", "priority_bucket"]).size().unstack(fill_value=0)
           .reindex(leads, fill_value=0))
    share = mix.div(mix.sum(axis=1).replace(0, 1), axis=0)
    fig = go.Figure()
    for priority in ipr.PRIORITY_ORDER:
        if priority in share.columns:
            fig.add_trace(go.Bar(
                y=leads, x=share[priority], name=priority, orientation="h", customdata=mix[priority],
                marker=dict(color=es.PRIORITY_SHADES[priority], line=dict(color="rgba(255,255,255,0.9)", width=2)),
                hovertemplate="%{y} · " + priority + ": %{x:.0%} (%{customdata} tickets)<extra></extra>",
            ))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(tickformat=".0%", range=[0, 1], title="Share of requested tickets", gridcolor=GRID)
    fig.update_layout(barmode="stack", height=max(280, 32 * len(leads) + 110), legend_title_text="Priority",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _open_figure(open_t: pd.DataFrame, leads: list[str]) -> go.Figure | None:
    rows = open_t[open_t["lead"].isin(leads)]
    if rows.empty:
        return None
    counts = rows.groupby(["lead", "risk"]).size().unstack(fill_value=0)
    order = [lead for lead in leads if lead in counts.index]
    fig = go.Figure()
    for risk in es.RISK_ORDER:
        if risk in counts.columns:
            label = es.RISK_LABELS[risk]
            fig.add_trace(go.Bar(
                y=order, x=counts.loc[order, risk], name=label, orientation="h",
                marker=dict(color=es.RISK_COLORS[label], line=dict(color="rgba(255,255,255,0.9)", width=2)),
                hovertemplate="%{y} · " + label + ": %{x} ticket(s)<extra></extra>",
            ))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Open tickets", gridcolor=GRID)
    fig.update_layout(barmode="stack", height=max(280, 32 * len(order) + 110), legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


# ── Entry point ─────────────────────────────────────────────────────────────────

def build_business_leader_visuals(df_issues: pd.DataFrame, start_month: str | None = None,
                                  end_month: str | None = None, include_internal: bool = False) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    t = facts[facts["sla_applies"]].copy()          # PE work only: Release Management (CAR) left out
    if t.empty:
        return _empty_payload("No Platform Engineering tickets found.")
    t["lead"] = _lead_group(t.get("business_lead", pd.Series(index=t.index)))
    t["created_month"] = t["created_day"].dt.to_period("M").astype(str)
    t["closed_month"] = t["closed_day"].dt.to_period("M").astype(str).where(t["closed_day"].notna())

    payload = _empty_payload()
    months = sorted(m for m in t["created_month"].dropna().unique() if m <= today.strftime("%Y-%m"))
    payload["available_months"] = months
    start = start_month or months[max(0, len(months) - 3)]
    end = end_month or months[-1]
    start, end = min(start, end), max(start, end)
    payload["start_month"], payload["end_month"] = start, end

    requested = t[(t["created_month"] >= start) & (t["created_month"] <= end)]
    closed_in = t[(t["closed_month"] >= start) & (t["closed_month"] <= end)]
    delivered = closed_in[closed_in["outcome"].eq("Done")].copy()
    if requested.empty and delivered.empty:
        return {**payload, "error_message": f"No tickets requested or delivered between {start} and {end}."}
    delivered["wait_bd"] = ipr._busdays_between(delivered["created_day"], delivered["closed_day"], hol)
    delivered["met"] = (delivered["closed_day"] <= delivered["sla_due"]).where(delivered["sla_due"].notna())

    ip = ipr.build_in_progress_visuals(df_issues, risk_basis="SLA")
    bl = build_backlog_visuals(df_issues, risk_basis="SLA")
    open_t = es._stage_risk(facts, today, hol, ip, bl)
    open_t = open_t[open_t["sla_applies"]].copy()
    open_t["lead"] = _lead_group(open_t.get("business_lead", pd.Series(index=open_t.index)))

    leads = sorted(set(requested["lead"]) | set(delivered["lead"]) | set(open_t["lead"]))
    card = pd.DataFrame({"lead": leads}).set_index("lead")
    card["requested"] = requested.groupby("lead").size()
    card["delivered"] = delivered.groupby("lead").size()
    card["wont_do"] = closed_in[closed_in["outcome"].eq("Will Not Do")].groupby("lead").size()
    card["median_wait_bd"] = delivered.groupby("lead")["wait_bd"].median()
    card["sla_judged"] = delivered.groupby("lead")["met"].count()
    card["sla_met"] = delivered.groupby("lead")["met"].mean()
    card["open_now"] = open_t.groupby("lead").size()
    card["open_past_sla"] = open_t[open_t["risk"].eq("Breached")].groupby("lead").size()
    card["oldest_open_days"] = open_t.groupby("lead")["age_days"].max()
    card["top_friction"] = _top_friction(delivered)
    for col in ["requested", "delivered", "wont_do", "sla_judged", "open_now", "open_past_sla"]:
        card[col] = card[col].fillna(0).astype(int)
    card = card.reset_index()
    card["_group"] = np.select([_is_requesting(card["lead"]), card["lead"].eq(INTERNAL_LABEL)], [0, 1], 2)
    card = card.sort_values(["_group", "requested", "delivered"], ascending=[True, False, False]).drop(columns="_group")

    requesting = card[_is_requesting(card["lead"])]
    req_delivered = delivered[_is_requesting(delivered["lead"])]
    payload["kpis"] = {
        "requesting_leads": int((requesting["requested"] > 0).sum()),
        "requested": int(len(requested)),
        "requested_by_leads": int(requesting["requested"].sum()),
        "delivered": int(len(delivered)),
        "sla_met_leads": float(req_delivered["met"].mean()) if req_delivered["met"].notna().any() else None,
        "internal_share": float((requested["lead"] == INTERNAL_LABEL).mean()) if len(requested) else 0.0,
        "unknown_share": float((requested["lead"] == UNKNOWN_LABEL).mean()) if len(requested) else 0.0,
    }

    chart_leads = _lead_order(card[card[["requested", "delivered", "open_now"]].sum(axis=1) > 0], include_internal)
    payload["demand_fig"] = _demand_figure(card, chart_leads) if chart_leads else None
    payload["sla_fig"] = _sla_figure(card, chart_leads) if chart_leads else None
    payload["wait_fig"] = _wait_figure(card, chart_leads) if chart_leads else None
    priority_leads = [lead for lead in chart_leads if lead in set(requested["lead"])]
    payload["priority_fig"] = _priority_figure(requested, priority_leads) if priority_leads else None
    payload["open_fig"] = _open_figure(open_t, chart_leads)
    payload["trend_fig"] = _trend_figure(t, today, include_internal)
    payload["scorecard_df"] = pd.DataFrame({
        "Business Lead": card["lead"],
        "Requested": card["requested"],
        "Delivered": card["delivered"],
        "Won't Do": card["wont_do"],
        "Median Wait (bd)": card["median_wait_bd"].round(1),
        "SLA Met %": (card["sla_met"] * 100).round(0),
        "SLA Sample": card["sla_judged"],
        "Open Now": card["open_now"],
        "Open Past SLA": card["open_past_sla"],
        "Oldest Open (days)": card["oldest_open_days"].astype("Int64"),
        "Top Friction in Comments": card["top_friction"].fillna("—"),
    })
    payload["delivered_df"] = delivered
    payload["requested_df"] = requested
    return payload
