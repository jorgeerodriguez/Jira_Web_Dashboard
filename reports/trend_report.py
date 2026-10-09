"""Trend: are we getting better, month over month?

PE tickets only (no Features, Initiatives or Release Management CAR tickets). Each month is the month
a ticket moved to Done (status_category_changed). Measures are in business days.

- Improvement scorecard: each measure's last full month vs the average of the three months before,
  with the delta coloured by whether up is good or bad for that measure.
- Small multiples: one line per measure over the last 12 months plus the current month ("so far"),
  with target lines where the team has one.
- Team contribution: delivered tickets per core engineer per month.
- Target date changes (when the change history is loaded, data/fetch_change_history.py): how often
  Target start / Target end are moved on delivered tickets, how often only after the date had passed,
  how many tickets had their SLA clock set after the work was done, how re-planning relates to
  outcomes, and the open tickets re-planned most.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from reports import change_history as chg
from reports import executive_summary as es
from reports import in_progress_report as ipr

try:
    from reports.word_of_the_month_report import DEFAULT_COVERAGE_TARGET
except ImportError:
    DEFAULT_COVERAGE_TARGET = 0.80


TREND_MONTHS = 12
BASELINE_MONTHS = 3            # the scorecard compares the last full month with the 3 before it
CORE_MIN_DELIVERED = 10        # team heatmap: people with at least 10 delivered in the window
OUTCOME_DAYS = 180             # Target date changes vs outcomes: tickets delivered in the last 180 days
MOVE_BUCKETS = [(0, 0, "0"), (1, 1, "1"), (2, 3, "2–3"), (4, 10_000, "4+")]
REPLANNED_MIN_MOVES = 2        # open tickets listed as "most re-planned" from this many moves
REACTIVE_TYPES = {"bug", "hotfix", "incident", "support", "security"}
LINE = "#2a78d6"
INK = ipr.INK
GRID = es.GRID


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    better: str            # "up", "down" or "neutral"
    kind: str              # "count", "days", "share" or "rate"
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
# Shown when the change history is loaded.
HISTORY_MEASURES = [
    Measure("date_moves", "Target Date Moves per Ticket", "down", "rate",
            help="Times Target start or Target end was moved, per ticket delivered in the month (over each "
                 "ticket's life). Setting a date for the first time is not a move."),
    Measure("stable_dates", "Delivered Without Date Moves", "up", "share",
            help="Share of delivered tickets whose Target dates were never moved."),
    Measure("late_moves", "Moves After the Date Passed", "down", "share",
            help="Share of date moves made after the old date had already passed: re-planning after the fact "
                 "rather than ahead of time."),
    Measure("clock_after_fact", "SLA Clock Set After the Fact", "down", "share",
            help="Share of delivered tickets whose Target start (the SLA clock) was set or moved on or after "
                 "the day the ticket moved to Done. Their SLA result is not a real measurement."),
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
    if measure.kind == "rate":
        return f"{value:.2f}"
    return f"{value:,.0f}"


def _delta_text(measure: Measure, now, base) -> str | None:
    if now is None or base is None or pd.isna(now) or pd.isna(base):
        return None
    if measure.kind == "share":
        return f"{(now - base) * 100:+.0f} pts vs prior 3-mo avg"
    if measure.kind == "days":
        return f"{now - base:+.1f} bd vs prior 3-mo avg"
    if measure.kind == "rate":
        return f"{now - base:+.2f} vs prior 3-mo avg"
    return (f"{(now - base) / base:+.0%} vs prior 3-mo avg" if base else None)


def _monthly(t: pd.DataFrame, months: pd.PeriodIndex, hol: np.ndarray,
             dates: pd.DataFrame | None = None) -> pd.DataFrame:
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
    if dates is not None:
        per = dates.reindex(done["key"]).set_axis(done.index)
        done = done.join(per)
        grouped = done.groupby("month")
        moves = grouped["moves"].sum()
        monthly["date_moves"] = moves / grouped.size()
        monthly["stable_dates"] = grouped["moves"].apply(lambda m: float((m == 0).mean()))
        monthly["late_moves"] = (grouped["late_moves"].sum() / moves.where(moves > 0))
        monthly["clock_after_fact"] = grouped["clock_after_fact"].mean()
    monthly["delivered"] = monthly["delivered"].fillna(0).astype(int)
    monthly["engineers"] = monthly["engineers"].fillna(0).astype(int)
    return monthly, done


def _scorecard(monthly: pd.DataFrame, current: pd.Period, measures: list[Measure]) -> tuple[list[dict], pd.Period | None]:
    full = monthly[monthly.index < current]
    if full.empty:
        return [], None
    last = full.index[-1]
    base = full.iloc[-1 - BASELINE_MONTHS:-1]
    cards = []
    for m in measures:
        now = full.loc[last, m.key]
        before = base[m.key].mean() if len(base) and base[m.key].notna().any() else None
        if m.better == "neutral":
            color = "off"
        else:
            color = "normal" if m.better == "up" else "inverse"
        cards.append({"label": m.label, "value": _fmt(m, now), "delta": _delta_text(m, now, before),
                      "delta_color": color, "help": m.help, "key": m.key})
    return cards, last


def _multiples_figure(monthly: pd.DataFrame, current: pd.Period, measures: list[Measure]) -> go.Figure:
    cols = 3
    rows = int(np.ceil(len(measures) / cols))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=[m.label for m in measures],
                        vertical_spacing=0.14, horizontal_spacing=0.07)
    labels = [p.strftime("%b %y") for p in monthly.index]
    partial = monthly.index == current
    for i, m in enumerate(measures):
        r, c = i // cols + 1, i % cols + 1
        values = monthly[m.key]
        fmt = {"share": ":.0%", "days": ":.1f", "count": ":,.0f", "rate": ":.2f"}[m.kind]
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


def build_trend_visuals(df_issues: pd.DataFrame, months: int = TREND_MONTHS, history: pd.DataFrame | None = None) -> dict:
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
    dates = changes = None
    measures = list(MEASURES)
    if chg.has_history(history):
        changes = chg.date_changes(history, hol)
        dates = chg.ticket_date_summary(changes, t)
        measures += HISTORY_MEASURES
    monthly, done = _monthly(t, window, hol, dates)
    scorecard, last_full = _scorecard(monthly, current, measures)

    table = monthly.copy()
    table.insert(0, "Month", [p.strftime("%b %Y") + (" (so far)" if p == current else "") for p in table.index])
    for m in measures:
        if m.kind == "share":
            table[m.key] = (table[m.key] * 100).round(0)
        elif m.kind == "days":
            table[m.key] = table[m.key].round(1)
        elif m.kind == "rate":
            table[m.key] = table[m.key].round(2)
    def column(m: Measure) -> str:
        if m.kind == "share":
            return f"{m.label} %"
        if m.kind == "days":
            return f"{m.label[:-1]}, bd)" if m.label.endswith(")") else f"{m.label} (bd)"
        return m.label

    table = table.rename(columns={m.key: column(m) for m in measures} | {"sla_sample": "SLA Sample"})
    return {
        "error_message": None,
        "scorecard": scorecard,
        "last_full_month": last_full.strftime("%B %Y") if last_full is not None else None,
        "multiples_fig": _multiples_figure(monthly, current, measures),
        "team_fig": _team_figure(done, window),
        "monthly_df": table.iloc[::-1].reset_index(drop=True),
        "monthly": monthly,
        "date_changes": _date_changes_section(t, dates, changes, today) if dates is not None else None,
    }


# ── Target date changes ─────────────────────────────────────────────────────────

def _bucket(moves: pd.Series) -> pd.Series:
    out = pd.Series(None, index=moves.index, dtype=object)
    for lo, hi, label in MOVE_BUCKETS:
        out[(moves >= lo) & (moves <= hi)] = label
    return out


def _outcome_by_moves(done: pd.DataFrame) -> tuple[go.Figure | None, pd.DataFrame]:
    """SLA met and Target end met for delivered tickets, by how often their dates were moved."""
    judged = done[done["sla_due"].notna()].copy()
    if judged.empty:
        return None, pd.DataFrame()
    judged["bucket"] = _bucket(judged["moves"])
    judged["met"] = judged["closed_day"] <= judged["sla_due"]
    judged["end_met"] = (judged["closed_day"] <= judged["target_end_day"]).where(judged["target_end_day"].notna())
    order = [b[2] for b in MOVE_BUCKETS]
    table = judged.groupby("bucket").agg(tickets=("key", "size"), sla_met=("met", "mean"),
                                         end_met=("end_met", "mean")).reindex(order).dropna(how="all")
    fig = go.Figure()
    for col, name, color in (("sla_met", "SLA met", LINE), ("end_met", "Target end met", "#eb6834")):
        fig.add_trace(go.Bar(
            x=table.index, y=table[col], name=name, marker=dict(color=color, line=dict(color="white", width=2)),
            text=[f"{v:.0%}" if pd.notna(v) else "" for v in table[col]], textposition="outside", cliponaxis=False,
            customdata=table["tickets"], hovertemplate="%{x} moves: %{y:.0%} " + name + " (%{customdata} tickets)<extra></extra>",
        ))
    fig.update_layout(barmode="group", height=320, legend=dict(orientation="h", y=1.12, x=0), legend_title_text="",
                      margin=dict(l=10, r=10, t=40, b=10))
    fig.update_yaxes(tickformat=".0%", range=[0, 1.12], gridcolor=GRID, title=None)
    fig.update_xaxes(title="Target date moves during the ticket's life",
                     ticktext=[f"{b}<br>n={int(n)}" for b, n in zip(table.index, table["tickets"])],
                     tickvals=list(table.index))
    out = pd.DataFrame({"Date Moves": table.index, "Delivered Tickets": table["tickets"].astype(int).values,
                        "SLA Met %": (table["sla_met"] * 100).round(0).values,
                        "Target End Met %": (table["end_met"] * 100).round(0).values})
    return fig, out


def _moves_heatmap(done: pd.DataFrame) -> go.Figure | None:
    if done.empty:
        return None
    sizes = [s for s in ipr.SIZE_ORDER if (done["size"] == s).any()]
    prios = [p for p in ipr.PRIORITY_ORDER if (done["priority_bucket"] == p).any()]
    grid = done.groupby(["priority_bucket", "size"])["moves"].agg(["mean", "size"])
    z = [[grid["mean"].get((p, s), np.nan) for s in sizes] for p in prios]
    n = [[int(grid["size"].get((p, s), 0)) for s in sizes] for p in prios]
    text = [[f"{v:.1f}<br>n={k}" if k else "" for v, k in zip(zr, nr)] for zr, nr in zip(z, n)]
    fig = go.Figure(go.Heatmap(z=z, x=[s.replace("Unestimated", "Unsized") for s in sizes], y=prios,
                               text=text, texttemplate="%{text}", colorscale="Blues", zmin=0, xgap=2, ygap=2,
                               colorbar=dict(title="Moves per<br>ticket"),
                               hovertemplate="%{y} · %{x}: %{z:.2f} moves per ticket<extra></extra>"))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _replanned_open(t: pd.DataFrame, dates: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    open_t = t[~t["is_closed"]].copy()
    open_t = open_t.join(dates, on="key")
    open_t = open_t[open_t["moves"] >= REPLANNED_MIN_MOVES]
    if open_t.empty:
        return pd.DataFrame()
    last = changes.groupby("key")["day"].max()
    open_t = open_t.sort_values(["moves", "late_moves"], ascending=False)
    return pd.DataFrame({
        "Ticket": ipr.JIRA_BROWSE_BASE_URL + open_t["key"].astype(str),
        "Date Moves": open_t["moves"].astype(int),
        "Start Moves": open_t["start_moves"].astype(int),
        "End Moves": open_t["end_moves"].astype(int),
        "Moved After Date Passed": open_t["late_moves"].astype(int),
        "Last Change": open_t["key"].map(last).dt.date,
        "Target Start": open_t["start_day"].dt.date,
        "Target End": open_t["target_end_day"].dt.date,
        "Jira Status": open_t["status"],
        "Priority": open_t["priority_bucket"],
        "Size": open_t["size"],
        "Assignee": open_t["assignee_name"],
        "Summary": open_t.get("summary", pd.Series("", index=open_t.index)).fillna("").astype(str).str[:100],
    }).reset_index(drop=True)


def _date_changes_section(t: pd.DataFrame, dates: pd.DataFrame, changes: pd.DataFrame, today: pd.Timestamp) -> dict:
    done = t[t["outcome"].eq("Done") & (t["closed_day"] >= today - pd.Timedelta(days=OUTCOME_DAYS))].join(dates, on="key")
    moved = changes[changes["kind"].eq("moved") & changes["key"].isin(done["key"])]
    outcome_fig, outcome_df = _outcome_by_moves(done)
    return {
        "kpis": {
            "delivered": int(len(done)),
            "moves_per_ticket": float(done["moves"].mean()) if len(done) else None,
            "stable": float((done["moves"] == 0).mean()) if len(done) else None,
            "later": float((moved["shift_bd"] > 0).mean()) if len(moved) else None,
            "after_passed": float(moved["after_passed"].mean()) if len(moved) else None,
            "clock_after_fact": float(done["clock_after_fact"].mean()) if len(done) else None,
            "median_shift_bd": float(moved["shift_bd"].abs().median()) if len(moved) else None,
        },
        "outcome_fig": outcome_fig,
        "outcome_df": outcome_df,
        "heatmap_fig": _moves_heatmap(done),
        "replanned_df": _replanned_open(t, dates, changes),
        "days": OUTCOME_DAYS,
    }
