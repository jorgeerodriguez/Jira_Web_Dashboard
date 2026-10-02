"""Trend: are we getting better, month over month?

PE tickets only (no Features, Initiatives or Release Management CAR tickets). Each month is the month
a ticket moved to Done (status_category_changed). Measures are in business days.

- Improvement scorecard: each measure's last full month vs the average of the three months before,
  with the delta coloured by whether up is good or bad for that measure.
- Small multiples: one line per measure over the last 12 months plus the current month ("so far"),
  with target lines where the team has one.
- Team contribution: delivered tickets per core engineer per month.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from reports import executive_summary as es
from reports import in_progress_report as ipr

try:
    from reports.word_of_the_month_report import DEFAULT_COVERAGE_TARGET
except ImportError:
    DEFAULT_COVERAGE_TARGET = 0.80


TREND_MONTHS = 12
BASELINE_MONTHS = 3            # the scorecard compares the last full month with the 3 before it
CORE_MIN_DELIVERED = 10        # team heatmap: people with at least 10 delivered in the window
REACTIVE_TYPES = {"bug", "hotfix", "incident", "support", "security"}
LINE = "#2a78d6"
INK = ipr.INK
GRID = es.GRID


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    better: str            # "up", "down" or "neutral"
    kind: str              # "count", "days" or "share"
    target: float | None = None
    help: str = ""


MEASURES = [
    Measure("delivered", "Delivered", "up", "count", help="PE tickets moved to Done in the month."),
    Measure("lead_median", "Lead Time (median)", "down", "days",
            help="Business days from request (created) to Done, median."),
    Measure("lead_p85", "Lead Time (85th pct)", "down", "days",
            help="85% of tickets finish within this many business days: how predictable delivery is."),
    Measure("cycle_median", "Cycle Time (median)", "down", "days",
            help="Business days from Target start to Done, median (tickets with a Target start)."),
    Measure("sla_met", "SLA Met", "up", "share", es.SLA_TARGET,
            help="Completed tickets that finished within their SLA (tickets with a Target start)."),
    Measure("urgent_share", "Urgent Share", "down", "share",
            help="Share of delivered tickets that were Urgent: fewer means less firefighting."),
    Measure("reactive_share", "Reactive Share", "down", "share",
            help="Share of delivered tickets that were Bugs, Hotfixes, Incidents, Support or Security."),
    Measure("comment_coverage", "Comment Coverage", "up", "share", DEFAULT_COVERAGE_TARGET,
            help="Completed tickets with at least one human comment."),
    Measure("engineers", "People Delivering", "neutral", "count",
            help="People who delivered at least one ticket in the month."),
]


def _empty_payload(message: str | None = None) -> dict:
    return {"error_message": message, "scorecard": [], "multiples_fig": None, "team_fig": None,
            "monthly_df": pd.DataFrame(), "last_full_month": None}


def _fmt(measure: Measure, value) -> str:
    if value is None or pd.isna(value):
        return "—"
    if measure.kind == "share":
        return f"{value:.0%}"
    if measure.kind == "days":
        return f"{value:.1f} bd"
    return f"{value:,.0f}"


def _delta_text(measure: Measure, now, base) -> str | None:
    if now is None or base is None or pd.isna(now) or pd.isna(base):
        return None
    if measure.kind == "share":
        return f"{(now - base) * 100:+.0f} pts vs prior 3-mo avg"
    if measure.kind == "days":
        return f"{now - base:+.1f} bd vs prior 3-mo avg"
    return (f"{(now - base) / base:+.0%} vs prior 3-mo avg" if base else None)


def _monthly(t: pd.DataFrame, months: pd.PeriodIndex, hol: np.ndarray) -> pd.DataFrame:
    done = t[t["outcome"].eq("Done") & t["closed_day"].notna()].copy()
    done["month"] = done["closed_day"].dt.to_period("M")
    done = done[done["month"].isin(months)]
    done["lead_bd"] = ipr._busdays_between(done["created_day"], done["closed_day"], hol)
    started = done["start_day"].notna() & (done["start_day"] <= done["closed_day"])
    done["cycle_bd"] = ipr._busdays_between(done["start_day"].where(started), done["closed_day"], hol)
    done["met"] = (done["closed_day"] <= done["sla_due"]).where(done["sla_due"].notna())
    done["urgent"] = done["priority_bucket"].eq("Urgent")
    done["reactive"] = done["issuetype"].astype(str).str.strip().str.casefold().isin(REACTIVE_TYPES)
    if "comments" in done.columns:
        done["commented"] = done["comments"].apply(lambda c: isinstance(c, list) and len(c) > 0)
    else:
        done["commented"] = np.nan
    people = done[done["assignee_name"].ne("Unassigned")]

    grouped = done.groupby("month")
    monthly = pd.DataFrame(index=months)
    monthly["delivered"] = grouped.size()
    monthly["lead_median"] = grouped["lead_bd"].median()
    monthly["lead_p85"] = grouped["lead_bd"].quantile(0.85)
    monthly["cycle_median"] = grouped["cycle_bd"].median()
    monthly["sla_met"] = grouped["met"].mean()
    monthly["sla_sample"] = grouped["met"].count()
    monthly["urgent_share"] = grouped["urgent"].mean()
    monthly["reactive_share"] = grouped["reactive"].mean()
    monthly["comment_coverage"] = grouped["commented"].mean() if done["commented"].notna().any() else np.nan
    monthly["engineers"] = people.groupby("month")["assignee_name"].nunique()
    monthly["delivered"] = monthly["delivered"].fillna(0).astype(int)
    monthly["engineers"] = monthly["engineers"].fillna(0).astype(int)
    return monthly, done


def _scorecard(monthly: pd.DataFrame, current: pd.Period) -> tuple[list[dict], pd.Period | None]:
    full = monthly[monthly.index < current]
    if full.empty:
        return [], None
    last = full.index[-1]
    base = full.iloc[-1 - BASELINE_MONTHS:-1]
    cards = []
    for m in MEASURES:
        now = full.loc[last, m.key]
        before = base[m.key].mean() if len(base) and base[m.key].notna().any() else None
        if m.better == "neutral":
            color = "off"
        else:
            color = "normal" if m.better == "up" else "inverse"
        cards.append({"label": m.label, "value": _fmt(m, now), "delta": _delta_text(m, now, before),
                      "delta_color": color, "help": m.help, "key": m.key})
    return cards, last


def _multiples_figure(monthly: pd.DataFrame, current: pd.Period) -> go.Figure:
    cols = 3
    rows = int(np.ceil(len(MEASURES) / cols))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=[m.label for m in MEASURES],
                        vertical_spacing=0.14, horizontal_spacing=0.07)
    labels = [p.strftime("%b %y") for p in monthly.index]
    partial = monthly.index == current
    for i, m in enumerate(MEASURES):
        r, c = i // cols + 1, i % cols + 1
        values = monthly[m.key]
        fmt = {"share": ":.0%", "days": ":.1f", "count": ":,.0f"}[m.kind]
        suffix = " bd" if m.kind == "days" else ""
        fig.add_trace(go.Scatter(
            x=labels, y=values, mode="lines+markers", line=dict(color=LINE, width=2),
            marker=dict(size=7, color=np.where(partial, "white", LINE), line=dict(color=LINE, width=2)),
            customdata=np.where(partial, " (so far)", ""),
            hovertemplate=f"%{{x}}%{{customdata}}: %{{y{fmt}}}{suffix}<extra>{m.label}</extra>", showlegend=False,
        ), row=r, col=c)
        if m.target is not None:
            fig.add_hline(y=m.target, line_dash="dash", line_color=INK, line_width=1, row=r, col=c,
                          annotation_text=f"Target {m.target:.0%}", annotation_position="bottom right",
                          annotation_font=dict(color=INK, size=10))
        axis = dict(gridcolor=GRID, rangemode="tozero")
        if m.kind == "share":
            axis.update(tickformat=".0%", range=[0, 1.05])
        fig.update_yaxes(row=r, col=c, **axis)
        fig.update_xaxes(row=r, col=c, tickangle=0, nticks=6, showgrid=False)
    fig.update_layout(height=270 * rows, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _team_figure(done: pd.DataFrame, months: pd.PeriodIndex) -> go.Figure | None:
    people = done[done["assignee_name"].ne("Unassigned")]
    totals = people.groupby("assignee_name").size()
    core = totals[totals >= CORE_MIN_DELIVERED].sort_values(ascending=False).index.tolist()
    if not core:
        return None
    grid = (people[people["assignee_name"].isin(core)].groupby(["assignee_name", "month"]).size()
            .unstack(fill_value=0).reindex(index=core, columns=months, fill_value=0))
    fig = go.Figure(go.Heatmap(
        z=grid.values, x=[p.strftime("%b %y") for p in months], y=core, colorscale="Blues", zmin=0,
        text=grid.values, texttemplate="%{text}", xgap=2, ygap=2, colorbar=dict(title="Delivered"),
        hovertemplate="%{y} · %{x}: %{z} delivered<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=max(300, 28 * len(core) + 110), margin=dict(l=10, r=10, t=10, b=10))
    return fig


def build_trend_visuals(df_issues: pd.DataFrame, months: int = TREND_MONTHS) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    t = facts[facts["sla_applies"]]
    if t.empty:
        return _empty_payload("No Platform Engineering tickets found.")

    current = today.to_period("M")
    window = pd.period_range(current - int(months), current, freq="M")   # 12 full months + this one
    monthly, done = _monthly(t, window, hol)
    scorecard, last_full = _scorecard(monthly, current)

    table = monthly.copy()
    table.insert(0, "Month", [p.strftime("%b %Y") + (" (so far)" if p == current else "") for p in table.index])
    for m in MEASURES:
        if m.kind == "share":
            table[m.key] = (table[m.key] * 100).round(0)
        elif m.kind == "days":
            table[m.key] = table[m.key].round(1)
    def column(m: Measure) -> str:
        if m.kind == "share":
            return f"{m.label} %"
        if m.kind == "days":
            return f"{m.label[:-1]}, bd)" if m.label.endswith(")") else f"{m.label} (bd)"
        return m.label

    table = table.rename(columns={m.key: column(m) for m in MEASURES} | {"sla_sample": "SLA Sample"})
    return {
        "error_message": None,
        "scorecard": scorecard,
        "last_full_month": last_full.strftime("%B %Y") if last_full is not None else None,
        "multiples_fig": _multiples_figure(monthly, current),
        "team_fig": _team_figure(done, window),
        "monthly_df": table.iloc[::-1].reset_index(drop=True),
        "monthly": monthly,
    }
