"""Capacity: how much Platform Engineering delivers, whether it keeps up with demand, how much more it
can take on, where the time goes, and how evenly the load is spread.

PE tickets only (no Features, Initiatives or Release Management CAR tickets); delivered means moved to
Done, dated when it moved (status_category_changed). Weeks run Monday to Sunday and only full weeks
are used, so the current week never drags an average down.

`build_capacity_data` (monthly created/completed counts) is kept unchanged for the Forecast report.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go


DEFAULT_COMPLETED_STATUSES = ("Done", "Released Successfully to Production")


def build_capacity_data(
    df_issues: pd.DataFrame,
    completed_statuses: tuple[str, ...] = DEFAULT_COMPLETED_STATUSES,
) -> pd.DataFrame:
    """Build monthly created/completed capacity dataframe from Jira issues.

    A ticket counts as completed when its status is one of `completed_statuses`.
    """
    if df_issues is None or df_issues.empty:
        return pd.DataFrame(columns=["date", "created", "completed"])

    required = {"year_created", "month_created", "year_updated", "month_updated", "status"}
    if not required.issubset(df_issues.columns):
        return pd.DataFrame(columns=["date", "created", "completed"])

    scope = df_issues.copy()
    if "project_name" in scope.columns:
        scope = scope[scope["project_name"].isin(["DevOps", "Release Management"])].copy()

    created = (
        scope.groupby(["year_created", "month_created"])
        .size()
        .reset_index(name="created")
        .rename(columns={"year_created": "year", "month_created": "month"})
    )

    completed_scope = scope[scope["status"].isin(completed_statuses)].copy()
    completed = (
        completed_scope.groupby(["year_updated", "month_updated"])
        .size()
        .reset_index(name="completed")
        .rename(columns={"year_updated": "year", "month_updated": "month"})
    )

    for df in (created, completed):
        if not df.empty:
            df["date"] = pd.to_datetime(df[["year", "month"]].assign(day=1), errors="coerce")
            df.drop(columns=["year", "month"], inplace=True)

    total = pd.merge(created, completed, on="date", how="outer")
    if total.empty:
        return pd.DataFrame(columns=["date", "created", "completed"])

    total["created"] = total["created"].fillna(0).astype(int)
    total["completed"] = total["completed"].fillna(0).astype(int)
    total = total[total["date"].dt.year > 2022].sort_values("date")
    return total


# ── Capacity page ───────────────────────────────────────────────────────────────

WEEKS = 26
KPI_WEEKS = 4                 # KPIs compare the last 4 full weeks with the 4 before
FORECAST_SAMPLE_WEEKS = 12    # the Monte Carlo samples from the last 12 full weeks of throughput
FORECAST_HORIZONS = (4, 8, 12)
SIMULATIONS = 10_000
RNG_SEED = 7
LOAD_WEEKS = 8                # load balance: delivery share over the last 8 full weeks
CORE_MIN_DELIVERED = 4        # charts show the core team: work in progress now, or 4+ delivered in LOAD_WEEKS
MIX_MONTHS = 6
REACTIVE_TYPES = {"bug", "hotfix", "incident", "support", "security"}
PLANNED_TYPES = {"story", "task", "sub-task", "subtask"}
WORK_TYPE_COLORS = {"Planned": "#2a78d6", "Reactive": "#eb6834", "Other": "#94a3b8"}
SERIES_COLORS = ("#2a78d6", "#eb6834")
INK = "#334155"
GRID = "rgba(148,163,184,0.25)"


def _empty_payload(message: str | None = None) -> dict:
    return {"error_message": message, "kpis": {}, "flow_fig": None, "forecast": {}, "weekly_throughput": [],
            "type_mix_fig": None, "priority_mix_fig": None, "wip_fig": None, "share_fig": None,
            "weekly_df": pd.DataFrame(), "people_df": pd.DataFrame()}


def forecast_throughput(weekly: list[float], horizons=FORECAST_HORIZONS, simulations: int = SIMULATIONS,
                        seed: int = RNG_SEED) -> dict:
    """Monte Carlo: tickets delivered over each horizon, resampling past weeks' throughput.

    Returns {weeks: {"likely": P50, "at_least": the total reached in 85% of runs}}.
    """
    sample = np.asarray([w for w in weekly if pd.notna(w)], dtype=float)
    if sample.size == 0:
        return {}
    rng = np.random.default_rng(seed)
    out = {}
    for weeks in horizons:
        totals = rng.choice(sample, size=(simulations, weeks)).sum(axis=1)
        out[weeks] = {"likely": int(np.percentile(totals, 50)), "at_least": int(np.percentile(totals, 15))}
    return out


def weeks_to_deliver(tickets: int, weekly: list[float], simulations: int = SIMULATIONS,
                     seed: int = RNG_SEED, max_weeks: int = 520) -> dict | None:
    """Monte Carlo: weeks until `tickets` more are delivered at the sampled pace (P50 and P85)."""
    sample = np.asarray([w for w in weekly if pd.notna(w)], dtype=float)
    if tickets <= 0 or sample.size == 0 or sample.max() <= 0:
        return None
    rng = np.random.default_rng(seed)
    horizon = min(max_weeks, int(np.ceil(tickets / max(sample.mean(), 0.1) * 3)) + 4)
    cumulative = rng.choice(sample, size=(simulations, horizon)).cumsum(axis=1)
    reached = cumulative >= tickets
    weeks = np.where(reached.any(axis=1), reached.argmax(axis=1) + 1, horizon)
    return {"likely": int(np.percentile(weeks, 50)), "safe": int(np.percentile(weeks, 85))}


def _work_type(issuetype: pd.Series) -> pd.Series:
    norm = issuetype.astype(str).str.strip().str.casefold()
    return np.select([norm.isin(PLANNED_TYPES), norm.isin(REACTIVE_TYPES)], ["Planned", "Reactive"], "Other")


def _flow_figure(weekly: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col, name, color in [("created", "Requested", SERIES_COLORS[0]), ("delivered", "Delivered", SERIES_COLORS[1])]:
        fig.add_trace(go.Scatter(
            x=weekly["week"], y=weekly[col], name=f"{name} per week", mode="lines+markers", legendgroup=col,
            line=dict(color=color, width=1), marker=dict(size=5), opacity=0.45,
            hovertemplate=f"{name}: %{{y}}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=weekly["week"], y=weekly[col].rolling(4, min_periods=1).mean(), name=f"{name} (4-week average)",
            mode="lines", legendgroup=col, line=dict(color=color, width=3),
            hovertemplate=f"{name}, 4-week average: %{{y:.0f}}<extra></extra>",
        ))
    fig.update_xaxes(title=None, tickformat="%b %d", gridcolor=GRID)
    fig.update_yaxes(title="PE tickets per week", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=360, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _mix_figure(delivered: pd.DataFrame, col: str, order: list[str], colors: dict, title: str) -> go.Figure:
    counts = delivered.groupby(["month", col]).size().unstack(fill_value=0)
    share = counts.div(counts.sum(axis=1).replace(0, 1), axis=0)
    labels = [p.strftime("%b %Y") for p in counts.index]
    fig = go.Figure()
    for key in order:
        if key in share.columns:
            fig.add_trace(go.Bar(
                x=labels, y=share[key], name=key, customdata=counts[key],
                marker=dict(color=colors[key], line=dict(color="rgba(255,255,255,0.9)", width=2)),
                hovertemplate="%{x} · " + key + ": %{y:.0%} (%{customdata} tickets)<extra></extra>",
            ))
    fig.update_yaxes(tickformat=".0%", range=[0, 1], title=title, gridcolor=GRID)
    fig.update_xaxes(title=None)
    fig.update_layout(barmode="stack", height=320, legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _people_figures(people: pd.DataFrame) -> tuple[go.Figure, go.Figure]:
    by_wip = people.sort_values(["wip", "delivered"], ascending=False)
    typical = float(people.loc[people["wip"] > 0, "wip"].median()) if (people["wip"] > 0).any() else 0.0
    wip_fig = go.Figure(go.Bar(
        y=by_wip["person"], x=by_wip["wip"], orientation="h", marker_color=SERIES_COLORS[0],
        text=by_wip["wip"], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x} ticket(s) in progress<extra></extra>",
    ))
    wip_fig.add_vline(x=typical, line_dash="dash", line_color=INK, annotation_text=f"Typical {typical:.0f}",
                      annotation_position="top", annotation_font_color=INK)
    wip_fig.update_yaxes(autorange="reversed", title=None)
    wip_fig.update_xaxes(title="Tickets in progress now", gridcolor=GRID, range=[0, max(by_wip["wip"].max(), 1) * 1.25])
    wip_fig.update_layout(height=max(300, 26 * len(by_wip) + 100), margin=dict(l=10, r=10, t=30, b=10))

    by_share = people.sort_values("share", ascending=False)
    even = 1 / max(len(people), 1)
    share_fig = go.Figure(go.Bar(
        y=by_share["person"], x=by_share["share"], orientation="h", marker_color=SERIES_COLORS[0],
        text=[f"{s:.0%} · {n}" for s, n in zip(by_share["share"], by_share["delivered"])],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:.0%} of delivered tickets<extra></extra>",
    ))
    share_fig.add_vline(x=even, line_dash="dash", line_color=INK, annotation_text=f"Even split {even:.0%}",
                        annotation_position="top", annotation_font_color=INK)
    share_fig.update_yaxes(autorange="reversed", title=None)
    share_fig.update_xaxes(tickformat=".0%", title=f"Share of PE tickets delivered, last {LOAD_WEEKS} weeks",
                           gridcolor=GRID, range=[0, max(by_share["share"].max(), even) * 1.35])
    share_fig.update_layout(height=max(300, 26 * len(by_share) + 100), margin=dict(l=10, r=10, t=30, b=10))
    return wip_fig, share_fig


def build_capacity_visuals(df_issues: pd.DataFrame) -> dict:
    """Capacity page: throughput vs demand, capacity forecast, where capacity goes, load balance."""
    from reports import executive_summary as es   # local import: Forecast imports this module
    from reports import in_progress_report as ipr

    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    t = facts[facts["sla_applies"]].copy()                       # PE work only
    if t.empty:
        return _empty_payload("No Platform Engineering tickets found.")

    week_of = lambda s: s.dt.to_period("W-SUN").dt.start_time  # noqa: E731
    this_week = today.to_period("W-SUN").start_time
    weeks = pd.date_range(end=this_week - pd.Timedelta(days=7), periods=WEEKS, freq="W-MON")
    delivered = t[t["outcome"].eq("Done") & t["closed_day"].notna()].copy()
    delivered["week"] = week_of(delivered["closed_day"])
    people_done = delivered[delivered["assignee_name"].ne("Unassigned")]
    weekly = pd.DataFrame({
        "week": weeks,
        "created": t.groupby(week_of(t["created_day"])).size().reindex(weeks, fill_value=0).values,
        "delivered": delivered.groupby("week").size().reindex(weeks, fill_value=0).values,
        "engineers": people_done.groupby("week")["assignee_name"].nunique().reindex(weeks, fill_value=0).values,
    })
    weekly["per_engineer"] = weekly["delivered"] / weekly["engineers"].replace(0, np.nan)

    last, prev = weekly.tail(KPI_WEEKS), weekly.iloc[-2 * KPI_WEEKS:-KPI_WEEKS]
    throughput = float(last["delivered"].mean())
    throughput_prev = float(prev["delivered"].mean()) if len(prev) else None
    open_t = t[~t["is_closed"]]
    queue = int(open_t["stage"].isin(["Backlog", "In Progress"]).sum())
    sample = weekly["delivered"].tail(FORECAST_SAMPLE_WEEKS).tolist()
    payload = _empty_payload()
    payload["kpis"] = {
        "throughput": throughput,
        "throughput_change": (throughput - throughput_prev) / throughput_prev if throughput_prev else None,
        "engineers": float(last["engineers"].mean()),
        "per_engineer": throughput / float(last["engineers"].mean()) if last["engineers"].mean() else None,
        "queue": queue,
        "weeks_queued": queue / throughput if throughput else None,
        "demand_ratio": float(last["created"].sum()) / float(last["delivered"].sum()) if last["delivered"].sum() else None,
        "spare_per_week": float(last["delivered"].mean() - last["created"].mean()),
        "sized_share": float((delivered[delivered["week"].isin(last["week"])]["size"] != "Unestimated").mean())
        if len(delivered[delivered["week"].isin(last["week"])]) else None,
    }
    payload["flow_fig"] = _flow_figure(weekly)
    payload["forecast"] = forecast_throughput(sample)
    payload["weekly_throughput"] = sample

    # ── Where capacity goes: last 6 months of delivered work ──
    months = pd.period_range(today.to_period("M") - (MIX_MONTHS - 1), today.to_period("M"), freq="M")
    mix = delivered[delivered["closed_day"].dt.to_period("M").isin(months)].copy()
    mix["month"] = mix["closed_day"].dt.to_period("M")
    mix["work_type"] = _work_type(mix["issuetype"])
    payload["type_mix_fig"] = _mix_figure(mix, "work_type", list(WORK_TYPE_COLORS), WORK_TYPE_COLORS,
                                          "Share of delivered tickets")
    payload["priority_mix_fig"] = _mix_figure(mix, "priority_bucket", ipr.PRIORITY_ORDER, es.PRIORITY_SHADES,
                                              "Share of delivered tickets")
    payload["reactive_share"] = float((mix["work_type"] == "Reactive").mean()) if len(mix) else None
    payload["urgent_share"] = float((mix["priority_bucket"] == "Urgent").mean()) if len(mix) else None

    # ── Load balance ──
    recent = people_done[people_done["week"].isin(weekly["week"].tail(LOAD_WEEKS))]
    wip = open_t[open_t["stage"].eq("In Progress") & open_t["assignee_name"].ne("Unassigned")].groupby("assignee_name").size()
    done_by = recent.groupby("assignee_name").size()
    people = pd.DataFrame({"wip": wip, "delivered": done_by}).fillna(0).astype(int)
    people = people[(people["wip"] > 0) | (people["delivered"] > 0)].rename_axis("person").reset_index()
    people["share"] = people["delivered"] / max(int(people["delivered"].sum()), 1)
    people["per_week"] = people["delivered"] / LOAD_WEEKS
    core = people[(people["wip"] > 0) | (people["delivered"] >= CORE_MIN_DELIVERED)]
    payload["kpis"]["core_team"] = int(len(core))
    payload["kpis"]["occasional"] = int(len(people) - len(core))
    if not core.empty:
        payload["wip_fig"], payload["share_fig"] = _people_figures(core)
    if not people.empty:
        top3 = people.sort_values("share", ascending=False).head(3)
        payload["kpis"]["top3_share"] = float(top3["share"].sum())
        payload["kpis"]["top3_names"] = top3["person"].tolist()
    payload["people_df"] = (people.sort_values("delivered", ascending=False)
                            .assign(share=lambda d: (d["share"] * 100).round(0), per_week=lambda d: d["per_week"].round(1))
                            .rename(columns={"person": "Engineer", "wip": "In Progress Now",
                                             "delivered": f"Delivered (last {LOAD_WEEKS} wks)",
                                             "share": "Share %", "per_week": "Per Week"}))
    payload["weekly_df"] = weekly.assign(
        week=weekly["week"].dt.date, per_engineer=weekly["per_engineer"].round(1)
    ).rename(columns={"week": "Week Of", "created": "Requested", "delivered": "Delivered",
                      "engineers": "Engineers Delivering", "per_engineer": "Per Engineer"}).iloc[::-1]
    return payload
