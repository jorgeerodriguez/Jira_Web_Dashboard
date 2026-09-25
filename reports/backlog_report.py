"""Backlog forecast: when will each backlog ticket start and finish, and will it make its SLA?

Backlog = To Do + Tech Discovery Required tickets (Features and Initiatives excluded). It reuses the
In Progress execution-velocity model (reports/in_progress_report.py) and adds a queue:

- Each assignee's backlog is ordered with the Apparent Tardiness Cost rule (reports/atc_sequence.py),
  the same order the Personal Dashboard suggests.
- A Monte Carlo simulation runs each assignee's queue many times. The assignee works on as many
  tickets at once as they typically do (their historical work in progress, at least one). Current
  In Progress tickets hold those slots until they finish (remaining time from the In Progress
  forecast); each backlog ticket then starts when a slot frees up, but not before its Target start,
  and takes a duration drawn from comparable Done tickets.
- The P50/P85 of each ticket's simulated start and finish give projected dates. The SLA clock starts
  at Target start, so a ticket is judged against Target start + SLA (business days). Tickets without
  a Target start, or without an assignee, cannot be judged and are flagged instead.
"""
from __future__ import annotations

import heapq
from datetime import timezone

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from reports import in_progress_report as ipr
from reports.atc_sequence import build_atc_sequence


JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
BACKLOG_STATUSES = {"to do", "tech discovery required"}
SIMULATIONS = 1000
RNG_SEED = 7  # fixed so the page shows the same dates on every rerun of the same data
UNASSIGNED = "Unassigned"

NOT_ASSESSED = "Not Assessed"
RISK_ORDER = ipr.RISK_ORDER + [NOT_ASSESSED]
RISK_LABELS = {**ipr.RISK_LABELS, NOT_ASSESSED: "○ Not assessed"}
RISK_COLORS = {**ipr.RISK_COLORS, RISK_LABELS[NOT_ASSESSED]: "#94a3b8"}
RISK_BASES = ipr.RISK_BASES
# Categorical slots 1 and 2 of the chart palette.
RUNWAY_COLORS = {"In Progress work": "#2a78d6", "Backlog work": "#eb6834"}
INK = ipr.INK


def _empty_payload() -> dict:
    return {
        "total_backlog": 0,
        "ready": 0,
        "should_have_started": 0,
        "at_risk": 0,
        "breached": 0,
        "unassigned": 0,
        "waiting_past_sla": 0,
        "all_done_p85": None,
        "runway_fig": None,
        "readiness_fig": None,
        "timeline_fig": None,
        "sla_grid_fig": None,
        "forecast_df": pd.DataFrame(),
        "waiting_df": pd.DataFrame(),
        "tickets_df": pd.DataFrame(),
    }


# ── Queue simulation ────────────────────────────────────────────────────────────

def _slots(typical_wip: float) -> int:
    return max(1, int(round(typical_wip)))


def _simulate_queue(in_progress_samples: list[np.ndarray], queue_samples: list[np.ndarray],
                    earliest_start: list[float], slots: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One assignee's queue, simulated column-by-column over the sample draws.

    Returns (starts, finishes) shaped [ticket, simulation] in business days from today, and the day
    each simulation's In Progress work is all finished.
    """
    n_sim = SIMULATIONS
    starts = np.zeros((len(queue_samples), n_sim))
    finishes = np.zeros((len(queue_samples), n_sim))
    in_progress_clear = np.zeros(n_sim)
    for s in range(n_sim):
        busy = [sample[s] for sample in in_progress_samples]
        in_progress_clear[s] = max(busy, default=0.0)
        heapq.heapify(busy)
        clock = 0.0
        for j, sample in enumerate(queue_samples):
            while len(busy) >= slots:
                clock = max(clock, heapq.heappop(busy))
            start = max(clock, earliest_start[j])
            finish = start + sample[s]
            starts[j, s], finishes[j, s] = start, finish
            heapq.heappush(busy, finish)
    return starts, finishes, in_progress_clear


# ── Figures ─────────────────────────────────────────────────────────────────────

def _runway_figure(runway: pd.DataFrame) -> go.Figure:
    plot = runway.sort_values("free_p50_bd", ascending=True)
    fig = go.Figure()
    for name, col in [("In Progress work", "in_progress_bd"), ("Backlog work", "backlog_bd")]:
        fig.add_trace(go.Bar(
            y=plot["assignee_name"], x=plot[col], name=name, orientation="h",
            marker=dict(color=RUNWAY_COLORS[name], line=dict(color="rgba(255,255,255,0.9)", width=2)),
            customdata=plot[["tickets_in_progress", "tickets_backlog", "free_p50", "free_p85"]],
            hovertemplate="<b>%{y}</b><br>" + name + ": %{x:.0f} business days"
                          "<br>%{customdata[0]} in progress · %{customdata[1]} in backlog"
                          "<br>Free by %{customdata[2]|%b %d} (P50) · %{customdata[3]|%b %d} (P85)<extra></extra>",
        ))
    fig.add_trace(go.Scatter(
        y=plot["assignee_name"], x=plot["free_p85_bd"], mode="markers", name="Free by (P85)",
        marker=dict(symbol="line-ns-open", size=16, color=INK, line=dict(width=3, color=INK)),
        hovertemplate="<b>%{y}</b><br>Free by P85: %{x:.0f} business days<extra></extra>",
    ))
    fig.update_layout(barmode="stack", height=max(320, 32 * len(plot) + 130), legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      xaxis_title="Business days from today", yaxis_title=None,
                      margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _readiness_figure(backlog: pd.DataFrame) -> go.Figure:
    total = len(backlog)
    checks = [
        ("Assigned", backlog["is_assigned"]),
        ("Sized", backlog["is_sized"]),
        ("Has Target start", backlog["target_start_day"].notna()),
        ("Has Target end", backlog["target_end_day"].notna()),
        ("Ready (all four)", backlog["is_ready"]),
    ]
    data = pd.DataFrame({"Check": [c for c, _ in checks], "Tickets": [int(m.sum()) for _, m in checks]})
    data["Share"] = data["Tickets"] / max(total, 1)
    data["Label"] = [f"{n} / {total} ({p:.0%})" for n, p in zip(data["Tickets"], data["Share"])]
    fig = go.Figure(go.Bar(
        y=data["Check"], x=data["Share"], orientation="h", text=data["Label"], textposition="outside",
        marker=dict(color=RUNWAY_COLORS["In Progress work"]),
        hovertemplate="%{y}: %{text}<extra></extra>", cliponaxis=False,
    ))
    fig.update_xaxes(range=[0, 1.25], tickformat=".0%", title=None, showgrid=True,
                     gridcolor="rgba(148,163,184,0.25)")
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
    return fig


def _timeline_figure(fc: pd.DataFrame, today: pd.Timestamp) -> go.Figure | None:
    plot = fc[fc["is_assigned"]].sort_values(["start_p50", "finish_p85"], ascending=False).copy()
    if plot.empty:
        return None
    plot["label"] = plot["key"] + " · " + plot["assignee_name"]
    fig = px.timeline(
        plot, x_start="start_p50", x_end="finish_p85", y="label", color="risk_label",
        color_discrete_map=RISK_COLORS, category_orders={"risk_label": [RISK_LABELS[r] for r in RISK_ORDER]},
        custom_data=["queue_position", "priority_bucket", "size_label", "sla_bd"],
    )
    fig.update_traces(
        marker_line_color="rgba(255,255,255,0.9)", marker_line_width=2, opacity=0.85,
        hovertemplate="<b>%{y}</b><br>Projected start %{base|%b %d} → P85 finish %{x|%b %d}"
                      "<br>Queue #%{customdata[0]} · %{customdata[1]} · %{customdata[2]}"
                      "<br>SLA %{customdata[3]} business days<extra></extra>",
    )
    for col, name, symbol, size in [("finish_p50", "Finish P50 (likely)", "circle", 9),
                                    ("target_start_day", "Target start", "triangle-right-open", 11),
                                    ("sla_due", "SLA due", "diamond-open", 11),
                                    ("target_end_day", "Target End Date", "x-thin-open", 11)]:
        fig.add_trace(go.Scatter(
            x=plot[col], y=plot["label"], mode="markers", name=name,
            marker=dict(symbol=symbol, size=size, color=INK, line=dict(width=2, color=INK)),
            hovertemplate=f"<b>%{{y}}</b><br>{name}: %{{x|%a %b %d, %Y}}<extra></extra>",
        ))
    fig.add_vline(x=today, line_width=2, line_dash="dash", line_color=INK)
    fig.add_annotation(x=today, y=1, xref="x", yref="paper", text="Today", showarrow=False,
                       xanchor="left", yanchor="bottom", font={"color": INK})
    fig.update_yaxes(title=None, autorange="reversed")
    fig.update_xaxes(title=None, showgrid=True, gridcolor="rgba(148,163,184,0.25)")
    fig.update_layout(height=max(360, 28 * len(plot) + 140), legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      margin=dict(l=10, r=10, t=40, b=10))
    return fig


# ── Tables ──────────────────────────────────────────────────────────────────────

def _all_tickets_table(backlog: pd.DataFrame) -> pd.DataFrame:
    """The unchanged "All Backlog Tickets" table."""
    df = backlog.copy()
    lead_col = ipr._first_existing_column(df, ["bussiness_lead", "business_lead", "Business Lead"])
    if lead_col is None:
        df["bussiness_lead"], lead_col = "Unknown", "bussiness_lead"
    for col, default in {"priority_name": "Unknown", "creator_name": "Unknown", "days_old": 0, "summary": "",
                         "target_end_date": pd.NaT}.items():
        if col not in df.columns:
            df[col] = default
    today_utc = pd.Timestamp.now(tz="UTC").normalize().date()
    target_end = pd.to_datetime(df["target_end_date"], errors="coerce").dt.date
    table = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + df["key"].astype(str),
        "Priority": df["priority_name"],
        "Business Lead": df[lead_col],
        "Creator": df["creator_name"],
        "Assognee Name": df["assignee_name"],
        "Last Updated": pd.to_datetime(df["updated"], errors="coerce").dt.date,
        "Target Start Date": pd.to_datetime(df["planned_start_date"], errors="coerce").dt.date,
        "Days Old": pd.to_numeric(df["days_old"], errors="coerce").fillna(0),
        "Days Left": target_end.apply(lambda d: (d - today_utc).days if pd.notnull(d) else None),
        "Estimated Size": df["size"],
        "Summary": df["summary"].fillna("").astype(str).str[:160],
    })
    return table.sort_values("Days Old", ascending=False)


# ── Entry point ─────────────────────────────────────────────────────────────────

def build_backlog_visuals(df_issues: pd.DataFrame, risk_basis: str = "SLA") -> dict:
    """Queue-aware start/finish forecast for backlog tickets (Features/Initiatives excluded).

    `risk_basis` (one of RISK_BASES) picks the deadline the headline risk, KPIs and charts use.
    """
    if df_issues is None or df_issues.empty:
        return _empty_payload()
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload()

    tickets = ipr.prepare_tickets(df_issues)
    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    today_series = lambda idx: pd.Series(today, index=idx)  # noqa: E731

    status_norm = tickets["status"].astype(str).str.strip().str.casefold()
    backlog = tickets[status_norm.isin(BACKLOG_STATUSES)].copy()
    if backlog.empty:
        return _empty_payload()
    in_progress = ipr.prepare_in_progress(tickets, today, hol)

    history = ipr._build_history(tickets, today, hol)
    _, effects = ipr._assignee_effects(history)
    typical = ipr._typical_wip(history, in_progress, today, hol)
    wip = ipr._wip_factors(typical, in_progress)

    # ── Backlog ticket facts ──
    backlog["target_start_day"] = ipr._to_day(backlog["planned_start_date"], timezone.utc)
    backlog["target_end_day"] = ipr._to_day(
        backlog.get("target_end_date", pd.Series(pd.NaT, index=backlog.index)), timezone.utc)
    backlog["created_day"] = ipr._to_day(backlog["created"], ipr.LOCAL_TZ)
    backlog["waiting_bd"] = ipr._busdays_between(backlog["created_day"], today_series(backlog.index), hol).fillna(0)
    backlog["elapsed_bd"] = 0.0
    backlog["is_assigned"] = backlog["assignee_name"].ne(UNASSIGNED)
    backlog["is_sized"] = backlog["size"].ne("Unestimated")
    backlog["is_ready"] = (backlog["is_assigned"] & backlog["is_sized"]
                           & backlog["target_start_day"].notna() & backlog["target_end_day"].notna())
    backlog["sla_size"] = backlog["size"].where(backlog["is_sized"], ipr.ASSUMED_SIZE)
    backlog["size_label"] = np.where(backlog["is_sized"], backlog["size"], f"{ipr.ASSUMED_SIZE}* (assumed)")
    backlog["sla_bd"] = [ipr.SLA_BUSINESS_DAYS[p][s] for p, s in zip(backlog["priority_bucket"], backlog["sla_size"])]
    backlog["sla_due"] = ipr._add_busdays(backlog["target_start_day"], backlog["sla_bd"].astype(float), hol)

    # Duration of each backlog ticket once started (no load factor: the queue models the load).
    dists = {idx: ipr._remaining_distribution(row, history, effects, {}) for idx, row in backlog.iterrows()}
    backlog["duration_p50_bd"] = [dists[i]["p50"] for i in backlog.index]
    backlog["duration_p85_bd"] = [dists[i]["p85"] for i in backlog.index]
    backlog["confidence"] = [dists[i]["confidence"] for i in backlog.index]
    backlog["basis"] = [dists[i]["basis"] for i in backlog.index]

    # ── ATC order per assignee (same per-person sequencing as the Personal Dashboard) ──
    atc_input = pd.DataFrame({
        "Ticket": backlog["key"],
        "Priority": backlog.get("priority_name", pd.Series("None", index=backlog.index)).fillna("None"),
        "Size": backlog["size"],
        "Days Left": (backlog["target_end_day"] - today).dt.days,
        "Days Old": pd.to_numeric(backlog.get("days_old", 0), errors="coerce").fillna(0),
        "Status": "To Do",
        "assignee_name": backlog["assignee_name"],
    })
    atc_rank = {}
    for _, pool in atc_input.groupby("assignee_name"):
        seq = build_atc_sequence(pool.drop(columns="assignee_name"))
        atc_rank.update(zip(seq["Ticket"], seq["Seq"]))
    backlog["queue_position"] = backlog["key"].map(atc_rank)
    backlog = backlog.sort_values(["assignee_name", "queue_position"])
    backlog["queue_position"] = backlog["queue_position"].where(backlog["is_assigned"])

    # ── Monte Carlo queue per assignee ──
    rng = np.random.default_rng(RNG_SEED)
    for col in ["start_p50_bd", "start_p85_bd", "finish_p50_bd", "finish_p85_bd"]:
        backlog[col] = np.nan
    runway_rows = []
    for assignee, queue in backlog[backlog["is_assigned"]].groupby("assignee_name", sort=False):
        mine = in_progress[in_progress["assignee_name"] == assignee]
        ip_samples = [ipr.sample_remaining(ipr._remaining_distribution(row, history, effects, wip), rng, SIMULATIONS)
                      for _, row in mine.iterrows()]
        q_samples = [ipr.sample_remaining(dists[i], rng, SIMULATIONS) for i in queue.index]
        earliest = ipr._busdays_between(today_series(queue.index), queue["target_start_day"], hol).fillna(0).tolist()
        starts, finishes, ip_clear = _simulate_queue(ip_samples, q_samples, earliest, _slots(typical.get(assignee, 1.0)))

        backlog.loc[queue.index, "start_p50_bd"] = np.quantile(starts, 0.50, axis=1)
        backlog.loc[queue.index, "start_p85_bd"] = np.quantile(starts, 0.85, axis=1)
        backlog.loc[queue.index, "finish_p50_bd"] = np.quantile(finishes, 0.50, axis=1)
        backlog.loc[queue.index, "finish_p85_bd"] = np.quantile(finishes, 0.85, axis=1)
        free = np.maximum(finishes.max(axis=0), ip_clear)
        runway_rows.append({
            "assignee_name": assignee,
            "tickets_in_progress": len(mine),
            "tickets_backlog": len(queue),
            "in_progress_bd": float(np.median(ip_clear)),
            "free_p50_bd": float(np.median(free)),
            "free_p85_bd": float(np.quantile(free, 0.85)),
        })

    for bd_col, date_col in [("start_p50_bd", "start_p50"), ("start_p85_bd", "start_p85"),
                             ("finish_p50_bd", "finish_p50"), ("finish_p85_bd", "finish_p85")]:
        backlog[date_col] = ipr._add_busdays(today_series(backlog.index), backlog[bd_col], hol)

    # ── Risk: SLA runs from Target start; unassigned / undated tickets are not assessed ──
    def risk_against(deadline: pd.Series, can_judge: pd.Series) -> list[str]:
        return [ipr._risk(today, d, p50, p85) if ok else NOT_ASSESSED
                for d, p50, p85, ok in zip(deadline, backlog["finish_p50"], backlog["finish_p85"], can_judge)]

    rank = {r: i for i, r in enumerate(RISK_ORDER)}
    backlog["risk_sla"] = risk_against(backlog["sla_due"],
                                       backlog["is_assigned"] & backlog["target_start_day"].notna())
    backlog["risk_target"] = risk_against(backlog["target_end_day"],
                                          backlog["is_assigned"] & backlog["target_end_day"].notna())
    backlog["risk_both"] = [min(a, b, key=rank.get) for a, b in zip(backlog["risk_sla"], backlog["risk_target"])]
    backlog["risk"] = {"SLA": backlog["risk_sla"], "Target End Date": backlog["risk_target"]}.get(
        risk_basis, backlog["risk_both"])
    backlog["risk_label"] = backlog["risk"].map(RISK_LABELS)

    backlog["start_slip_bd"] = np.where(
        backlog["target_start_day"].notna() & backlog["start_p50"].notna(),
        ipr._busdays_between(backlog["target_start_day"], backlog["start_p50"], hol), np.nan)
    backlog["missing"] = [
        ", ".join(label for label, ok in [("Assignee", a), ("Size", s), ("Target start", ts), ("Target end", te)]
                  if not ok) or "—"
        for a, s, ts, te in zip(backlog["is_assigned"], backlog["is_sized"],
                                backlog["target_start_day"].notna(), backlog["target_end_day"].notna())]
    backlog["waiting_past_sla"] = backlog["waiting_bd"] > backlog["sla_bd"]

    fc = backlog.sort_values(["risk", "start_p50_bd"], key=lambda s: s.map(rank) if s.name == "risk" else s)
    forecast_df = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + fc["key"].astype(str),
        "Risk": fc["risk_label"],
        "SLA Status": fc["risk_sla"].map(RISK_LABELS),
        "Target End Status": fc["risk_target"].map(RISK_LABELS),
        "Missing": fc["missing"],
        "Assignee": fc["assignee_name"],
        "Queue #": fc["queue_position"].astype("Int64"),
        "Priority": fc["priority_bucket"],
        "Size": fc["size_label"],
        "Waiting (bd)": fc["waiting_bd"].astype(int),
        "Target Start": fc["target_start_day"].dt.date,
        "Projected Start": fc["start_p50"].dt.date,
        "Start Slip (bd)": pd.to_numeric(fc["start_slip_bd"]).round(0).astype("Int64"),
        "Finish P50": fc["finish_p50"].dt.date,
        "Finish P85": fc["finish_p85"].dt.date,
        "SLA (bd)": fc["sla_bd"],
        "SLA Due": fc["sla_due"].dt.date,
        "Target End": fc["target_end_day"].dt.date,
        "Work P50–P85 (bd)": [f"{a:.0f}–{b:.0f}" for a, b in zip(fc["duration_p50_bd"], fc["duration_p85_bd"])],
        "Confidence": fc["confidence"],
        "Based On": fc["basis"],
    })

    waiting = fc[fc["waiting_past_sla"]].sort_values("waiting_bd", ascending=False)
    waiting_df = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + waiting["key"].astype(str),
        "Assignee": waiting["assignee_name"],
        "Priority": waiting["priority_bucket"],
        "Size": waiting["size_label"],
        "Waiting (bd)": waiting["waiting_bd"].astype(int),
        "SLA (bd)": waiting["sla_bd"],
        "Summary": waiting.get("summary", pd.Series("", index=waiting.index)).fillna("").astype(str).str[:120],
    })

    runway = pd.DataFrame(runway_rows)
    all_done_p85 = None
    if not runway.empty:
        runway["backlog_bd"] = (runway["free_p50_bd"] - runway["in_progress_bd"]).clip(lower=0)
        runway["free_p50"] = ipr._add_busdays(today_series(runway.index), runway["free_p50_bd"], hol)
        runway["free_p85"] = ipr._add_busdays(today_series(runway.index), runway["free_p85_bd"], hol)
        all_done_p85 = runway["free_p85"].max().date()

    return {
        "total_backlog": int(len(backlog)),
        "ready": int(backlog["is_ready"].sum()),
        "should_have_started": int((backlog["target_start_day"] < today).sum()),
        "at_risk": int(backlog["risk"].isin(["At Risk", "Likely Late"]).sum()),
        "breached": int((backlog["risk"] == "Breached").sum()),
        "unassigned": int((~backlog["is_assigned"]).sum()),
        "waiting_past_sla": int(backlog["waiting_past_sla"].sum()),
        "all_done_p85": all_done_p85,
        "runway_fig": _runway_figure(runway) if not runway.empty else None,
        "readiness_fig": _readiness_figure(backlog),
        "timeline_fig": _timeline_figure(fc, today),
        "sla_grid_fig": ipr._sla_grid_figure(backlog),
        "forecast_df": forecast_df,
        "waiting_df": waiting_df,
        "tickets_df": _all_tickets_table(backlog),
    }
