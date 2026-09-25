"""In Progress forecast: when will each in-progress ticket finish, and is it inside its SLA?

For every In Progress ticket (Features and Initiatives excluded) the report combines:

- SLA budget -- business days from the priority x estimated-size matrix, counted from the
  ticket's Target start date on the company business calendar (weekends + Audacy holidays).
- Execution velocity -- business days from Target start to Done over the last year of Done
  tickets, recency weighted. A baseline per size x priority cell (falling back to size, then
  priority, then team-wide when a cell is thin) is adjusted by an empirical-Bayes assignee x
  priority effect, so people with little history are pulled toward the team instead of
  trusting two data points.
- Time already spent -- the remaining-time distribution is conditioned on the ticket having
  already run `elapsed` business days (only history that ran at least that long counts), which
  keeps old tickets from being forecast to finish "yesterday".
- Current load -- a dampened Little's-law factor scales durations by the assignee's current
  work in progress relative to their typical concurrent load.

The result is a P50 (likely) and P85 (safe-to-commit) completion date per ticket, compared with
both the SLA due date and the Target End Date to give a risk status.
"""
from __future__ import annotations

from datetime import date, timedelta, timezone
from functools import lru_cache

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

try:
    from darkstar.metrics import is_holiday
except ImportError:  # `holidays` not installed: fall back to a weekends-only calendar
    is_holiday = None


JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"
EXCLUDED_ASSIGNEES = {
    "satish kumar marana",
    "denys loboda",
    "cullen philippson",
    "emmanuel adjei",
}
# Features and Initiatives are containers, not tickets; matched on either issuetype or status.
EXCLUDED_WORK_TYPES = {"feature", "iniciative", "initiative"}
# Jira stores created/updated in this offset (see data/build_dataframe_new.py); Target start and
# Target end are date-only fields stored at UTC midnight, so they are read in UTC.
LOCAL_TZ = timezone(timedelta(hours=-6))

SIZE_ORDER = ["Small", "Medium", "Large", "XL", "Unestimated"]
_SIZE_ALIASES = {"small": "Small", "medium": "Medium", "large": "Large", "xl": "XL",
                 "xlarge": "XL", "x-large": "XL", "extra large": "XL"}
PRIORITY_ORDER = ["Urgent", "High", "Medium", "Low/None"]

# SLA in business days, keyed [priority][size].
SLA_BUSINESS_DAYS = {
    "Low/None": {"Small": 30, "Medium": 33, "Large": 37, "XL": 45},
    "Medium":   {"Small": 15, "Medium": 18, "Large": 22, "XL": 30},
    "High":     {"Small": 7,  "Medium": 10, "Large": 17, "XL": 22},
    "Urgent":   {"Small": 1,  "Medium": 4,  "Large": 8,  "XL": 16},
}
# Unsized tickets get the SLA of this size; the table marks them so the gap stays visible.
ASSUMED_SIZE = "Medium"

HISTORY_DAYS = 365
RECENCY_HALF_LIFE_DAYS = 120
SHRINK_K = 5.0                 # pseudo-count pulling thin assignee/priority effects toward 0
MAX_LOG_EFFECT = 1.5           # an assignee is at most ~4.5x faster/slower than the baseline
MIN_CELL_SAMPLES = 8           # fewer Done tickets than this and the next-coarser cell is used
MIN_CONDITIONAL_SAMPLES = 5    # fewer history tickets outlasting `elapsed` -> beyond-history rule
# Remaining-time multiples of the time already spent, for tickets older than nearly all history.
BEYOND_HISTORY_P50 = 0.5   # back-tested: ~50% finish by P50, ~85% by P85
BEYOND_HISTORY_P85 = 2.0
WIP_WINDOW_DAYS = 180
WIP_FACTOR_BOUNDS = (0.85, 1.5)

RISK_ORDER = ["Breached", "Likely Late", "At Risk", "On Track"]
RISK_LABELS = {
    "Breached": "✖ Breached",
    "Likely Late": "▲ Likely Late",
    "At Risk": "! At Risk",
    "On Track": "✓ On Track",
}
RISK_COLORS = {
    RISK_LABELS["Breached"]: "#d03b3b",
    RISK_LABELS["Likely Late"]: "#ec835a",
    RISK_LABELS["At Risk"]: "#fab219",
    RISK_LABELS["On Track"]: "#0ca30c",
}
INK = "#334155"
# What the headline risk is judged against; the table always shows both.
RISK_BASES = ("SLA", "Target End Date", "Both (earliest deadline)")


# ── Normalisation helpers ───────────────────────────────────────────────────────

def _normalize_assignee(series: pd.Series) -> pd.Series:
    return series.fillna("Unassigned").astype(str).str.strip()


def _normalize_size(series: pd.Series) -> pd.Series:
    norm = series.fillna("").astype(str).str.strip().str.casefold().map(_SIZE_ALIASES)
    return norm.fillna("Unestimated")


def _normalize_priority(series: pd.Series) -> pd.Series:
    norm = series.fillna("").astype(str).str.strip().str.casefold()
    mapping = {"urgent": "Urgent", "highest": "Urgent", "blocker": "Urgent", "critical": "Urgent",
               "high": "High", "medium": "Medium"}
    return norm.map(mapping).fillna("Low/None")


def _exclude_container_items(df: pd.DataFrame) -> pd.DataFrame:
    keep = pd.Series(True, index=df.index)
    for col in ("issuetype", "status"):
        if col in df.columns:
            keep &= ~df[col].astype(str).str.strip().str.casefold().isin(EXCLUDED_WORK_TYPES)
    return df[keep]


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _to_day(series: pd.Series, tz) -> pd.Series:
    """Calendar day (datetime64[D]-compatible, naive) of each timestamp in `tz`."""
    ts = pd.to_datetime(series, errors="coerce", utc=True)
    return ts.dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()


# ── Business calendar ───────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def _holidays(first_year: int, last_year: int) -> np.ndarray:
    if is_holiday is None:
        return np.array([], dtype="datetime64[D]")
    days = pd.date_range(date(first_year, 1, 1), date(last_year, 12, 31), freq="D")
    return np.array([d for d in days.date if is_holiday(d)], dtype="datetime64[D]")


def _calendar_holidays(today: pd.Timestamp) -> np.ndarray:
    return _holidays(today.year - 3, today.year + 3)


def _busdays_between(start: pd.Series, end: pd.Series, hol: np.ndarray) -> pd.Series:
    """Business days in [start, end), NaN where either side is missing; never negative."""
    out = pd.Series(np.nan, index=start.index)
    ok = start.notna() & end.notna()
    if ok.any():
        s = start[ok].values.astype("datetime64[D]")
        e = end[ok].values.astype("datetime64[D]")
        out[ok] = np.maximum(np.busday_count(s, e, holidays=hol), 0)
    return out


def _add_busdays(start: pd.Series, days: pd.Series, hol: np.ndarray) -> pd.Series:
    """The business day `days` business days after `start` (rolled forward onto a business day)."""
    out = pd.Series(pd.NaT, index=start.index, dtype="datetime64[ns]")
    ok = start.notna() & days.notna()
    if ok.any():
        s = start[ok].values.astype("datetime64[D]")
        n = np.ceil(days[ok].astype(float).values).astype(int)
        out[ok] = np.busday_offset(s, n, roll="forward", holidays=hol).astype("datetime64[ns]")
    return out


# ── Statistics helpers ──────────────────────────────────────────────────────────

def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = (np.cumsum(w) - 0.5 * w) / w.sum()
    return float(np.interp(q, cum, v))


def _cell_levels(priority: str, size: str) -> list[tuple[str, dict]]:
    """History cells from most to least specific; unsized tickets skip the size levels."""
    levels = []
    if size != "Unestimated":
        levels.append((f"{priority} × {size}", {"priority_bucket": priority, "size": size}))
        levels.append((f"{size} (any priority)", {"size": size}))
    levels.append((f"{priority} (any size)", {"priority_bucket": priority}))
    levels.append(("Team-wide", {}))
    return levels


def _reference_set(history: pd.DataFrame, priority: str, size: str) -> tuple[pd.DataFrame, str]:
    for label, filters in _cell_levels(priority, size):
        subset = history
        for col, value in filters.items():
            subset = subset[subset[col] == value]
        if len(subset) >= MIN_CELL_SAMPLES or not filters:
            return subset, label
    return history, "Team-wide"


# ── History: execution velocity from Done tickets ───────────────────────────────

def _build_history(tickets: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray) -> pd.DataFrame:
    done = tickets[tickets["status"].astype(str).str.strip().str.casefold().eq("done")].copy()
    if done.empty or "planned_start_date" not in done.columns or "updated" not in done.columns:
        return pd.DataFrame(columns=["assignee_name", "priority_bucket", "size", "duration_bd",
                                     "log_duration", "weight", "start_day", "end_day"])

    # Finish time: when the ticket moved to Done; `updated` (the resolution date when Jira has
    # one, else the last edit) only for data fetched before that field was loaded.
    done["start_day"] = _to_day(done["planned_start_date"], timezone.utc)
    finished = done.get("status_category_changed", pd.Series(pd.NaT, index=done.index))
    done["end_day"] = _to_day(finished, LOCAL_TZ).fillna(_to_day(done["updated"], LOCAL_TZ))
    done = done[done["start_day"].notna() & done["end_day"].notna()]
    done = done[(done["end_day"] >= done["start_day"])
                & (done["end_day"] >= today - pd.Timedelta(days=HISTORY_DAYS))].copy()
    if done.empty:
        return done

    done["duration_bd"] = _busdays_between(done["start_day"], done["end_day"], hol)
    done["log_duration"] = np.log1p(done["duration_bd"])
    age_days = (today - done["end_day"]).dt.days.clip(lower=0)
    done["weight"] = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
    return done[["assignee_name", "priority_bucket", "size", "duration_bd", "log_duration",
                 "weight", "start_day", "end_day"]]


def _assignee_effects(history: pd.DataFrame) -> tuple[dict, dict]:
    """Shrunk log-scale speed effects: per assignee, and per assignee x priority on top of it."""
    if history.empty:
        return {}, {}

    baseline = pd.Series(np.nan, index=history.index)
    for (priority, size), group in history.groupby(["priority_bucket", "size"]):
        ref, _ = _reference_set(history, priority, size)
        baseline[group.index] = _weighted_quantile(ref["log_duration"].values, ref["weight"].values, 0.5)
    resid = history["log_duration"] - baseline
    w = history["weight"]

    assignee_effect = {}
    for assignee, idx in history.groupby("assignee_name").groups.items():
        effect = float((w[idx] * resid[idx]).sum() / (w[idx].sum() + SHRINK_K))
        assignee_effect[assignee] = float(np.clip(effect, -MAX_LOG_EFFECT, MAX_LOG_EFFECT))

    priority_effect = {}
    for (assignee, priority), idx in history.groupby(["assignee_name", "priority_bucket"]).groups.items():
        base = assignee_effect.get(assignee, 0.0)
        extra = float((w[idx] * (resid[idx] - base)).sum() / (w[idx].sum() + SHRINK_K))
        priority_effect[(assignee, priority)] = float(np.clip(base + extra, -MAX_LOG_EFFECT, MAX_LOG_EFFECT))
    return assignee_effect, priority_effect


def _typical_wip(history: pd.DataFrame, in_progress: pd.DataFrame, today: pd.Timestamp,
                 hol: np.ndarray) -> pd.Series:
    """Average number of tickets each assignee had in flight per business day, last WIP_WINDOW_DAYS."""
    window_start = today - pd.Timedelta(days=WIP_WINDOW_DAYS)
    window_bd = max(int(np.busday_count(np.datetime64(window_start.date()), np.datetime64(today.date()),
                                        holidays=hol)), 1)
    spans = []
    if not history.empty:
        h = history[history["end_day"] >= window_start]
        spans.append(pd.DataFrame({
            "assignee_name": h["assignee_name"],
            "busy_bd": _busdays_between(h["start_day"].clip(lower=window_start), h["end_day"], hol),
        }))
    spans.append(pd.DataFrame({
        "assignee_name": in_progress["assignee_name"],
        "busy_bd": _busdays_between(in_progress["start_day"].clip(lower=window_start),
                                    pd.Series(today, index=in_progress.index), hol),
    }))
    return pd.concat(spans).groupby("assignee_name")["busy_bd"].sum() / window_bd


def _wip_factors(typical: pd.Series, in_progress: pd.DataFrame) -> dict:
    """Little's law, dampened: cycle time grows with WIP relative to the assignee's usual WIP."""
    current = in_progress.groupby("assignee_name").size()

    factors = {}
    for assignee, now_wip in current.items():
        usual = max(float(typical.get(assignee, 0.0)), 1.0)
        factors[assignee] = float(np.clip(np.sqrt(now_wip / usual), *WIP_FACTOR_BOUNDS))
    return factors


# ── Per-ticket forecast ─────────────────────────────────────────────────────────

def _remaining_distribution(row: pd.Series, history: pd.DataFrame, effects: dict, wip: dict) -> dict:
    """Remaining business days for one ticket: P50/P85 plus the weighted sample behind them.

    `values`/`weights` are None when the ticket is beyond comparable history (or there is no
    history); `sample_remaining` then draws from a lognormal matched to P50/P85.
    """
    elapsed = float(row["elapsed_bd"])
    if history.empty:
        p50, p85 = max(1.0, BEYOND_HISTORY_P50 * elapsed), max(2.0, BEYOND_HISTORY_P85 * elapsed)
        return {"values": None, "weights": None, "p50": p50, "p85": max(p85, p50),
                "basis": "No Done history", "confidence": "Low"}

    ref, basis = _reference_set(history, row["priority_bucket"], row["size"])
    effect = effects.get((row["assignee_name"], row["priority_bucket"]), 0.0)
    durations = np.expm1(ref["log_duration"].values + effect) * wip.get(row["assignee_name"], 1.0)
    weights = ref["weight"].values

    outlasting = durations > elapsed
    if outlasting.sum() >= MIN_CONDITIONAL_SAMPLES:
        remaining = durations[outlasting] - elapsed
        p50 = _weighted_quantile(remaining, weights[outlasting], 0.50)
        p85 = _weighted_quantile(remaining, weights[outlasting], 0.85)
        n_eff = float(weights[outlasting].sum() ** 2 / (weights[outlasting] ** 2).sum())
        confidence = "High" if n_eff >= 30 and "×" in basis else "Medium" if n_eff >= 10 else "Low"
        return {"values": remaining, "weights": weights[outlasting], "p50": max(p50, 0.0),
                "p85": max(p85, p50, 0.0), "basis": basis, "confidence": confidence}

    # Already older than almost everything comparable: lean on the time already spent.
    p50, p85 = max(1.0, BEYOND_HISTORY_P50 * elapsed), max(2.0, BEYOND_HISTORY_P85 * elapsed)
    return {"values": None, "weights": None, "p50": p50, "p85": max(p85, p50),
            "basis": "Beyond history (uses time spent)", "confidence": "Low"}


def sample_remaining(dist: dict, rng: np.random.Generator, n: int) -> np.ndarray:
    """`n` draws of remaining business days: a weighted bootstrap of the history sample, or a
    lognormal with the same P50/P85 when there is no usable sample."""
    if dist["values"] is not None:
        return rng.choice(dist["values"], size=n, p=dist["weights"] / dist["weights"].sum())
    p50 = max(dist["p50"], 0.5)
    sigma = np.log(max(dist["p85"], p50 * 1.01) / p50) / 1.0364  # z(0.85) = 1.0364
    return rng.lognormal(np.log(p50), sigma, size=n)


def _forecast_ticket(row: pd.Series, history: pd.DataFrame, effects: dict, wip: dict) -> dict:
    dist = _remaining_distribution(row, history, effects, wip)
    return {"remaining_p50_bd": dist["p50"], "remaining_p85_bd": dist["p85"],
            "basis": dist["basis"], "confidence": dist["confidence"]}


def _risk(today: pd.Timestamp, deadline, p50, p85) -> str:
    if pd.isna(deadline):
        return "On Track"
    if today > deadline:
        return "Breached"
    if p50 > deadline:
        return "Likely Late"
    if p85 > deadline:
        return "At Risk"
    return "On Track"


# ── Figures ─────────────────────────────────────────────────────────────────────

def _timeline_figure(fc: pd.DataFrame, today: pd.Timestamp) -> go.Figure:
    plot = fc.sort_values(["forecast_p85", "forecast_p50"], ascending=False).copy()
    plot["label"] = plot["key"] + " · " + plot["assignee_name"]
    fig = px.timeline(
        plot, x_start="start_day", x_end="forecast_p85", y="label", color="risk_label",
        color_discrete_map=RISK_COLORS, category_orders={"risk_label": [RISK_LABELS[r] for r in RISK_ORDER]},
        custom_data=["priority_bucket", "size_label", "sla_bd", "elapsed_bd"],
    )
    fig.update_traces(
        marker_line_color="rgba(255,255,255,0.9)", marker_line_width=2, opacity=0.85,
        hovertemplate="<b>%{y}</b><br>Target start %{base|%b %d} → P85 %{x|%b %d}"
                      "<br>Priority %{customdata[0]} · Size %{customdata[1]}"
                      "<br>SLA %{customdata[2]} business days · %{customdata[3]} used<extra></extra>",
    )
    for col, name, symbol, size in [("forecast_p50", "Forecast P50 (likely)", "circle", 9),
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
    fig.update_layout(height=max(360, 30 * len(plot) + 140), legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _velocity_heatmap(history: pd.DataFrame, effects: dict, assignees: list[str]) -> go.Figure | None:
    if history.empty or not assignees:
        return None
    priorities = [p for p in PRIORITY_ORDER if (history["priority_bucket"] == p).any()]
    z, text, hover = [], [], []
    for assignee in assignees:
        z_row, t_row, h_row = [], [], []
        mine = history[history["assignee_name"] == assignee]
        for priority in priorities:
            ref, _ = _reference_set(history, priority, "Unestimated")
            base = _weighted_quantile(ref["log_duration"].values, ref["weight"].values, 0.5)
            days = float(np.expm1(base + effects.get((assignee, priority), 0.0)))
            n = int((mine["priority_bucket"] == priority).sum())
            z_row.append(days)
            t_row.append(f"{days:.1f}d<br>n={n}")
            h_row.append(f"{assignee} · {priority}<br>Typical execution {days:.1f} business days"
                         f"<br>Own Done tickets (last {HISTORY_DAYS}d): {n}")
        z.append(z_row)
        text.append(t_row)
        hover.append(h_row)
    fig = go.Figure(go.Heatmap(
        z=z, x=priorities, y=assignees, text=text, texttemplate="%{text}", hovertext=hover,
        hovertemplate="%{hovertext}<extra></extra>", colorscale="Blues", xgap=2, ygap=2,
        colorbar=dict(title="Business<br>days"),
    ))
    fig.update_layout(height=max(320, 34 * len(assignees) + 120), margin=dict(l=10, r=10, t=10, b=10))
    fig.update_yaxes(autorange="reversed")
    return fig


def _assignee_risk_figure(fc: pd.DataFrame) -> go.Figure:
    counts = fc.groupby(["assignee_name", "risk_label"]).size().reset_index(name="Tickets")
    order = fc.groupby("assignee_name").size().sort_values(ascending=True).index.tolist()
    fig = px.bar(counts, x="Tickets", y="assignee_name", color="risk_label", orientation="h",
                 color_discrete_map=RISK_COLORS,
                 category_orders={"assignee_name": order,
                                  "risk_label": [RISK_LABELS[r] for r in RISK_ORDER]})
    fig.update_traces(marker_line_color="rgba(255,255,255,0.9)", marker_line_width=2,
                      hovertemplate="%{y}: %{x} ticket(s)<extra></extra>")
    fig.update_layout(height=max(320, 30 * len(order) + 120), barmode="stack", legend_title_text="",
                      xaxis_title="In Progress tickets", yaxis_title=None,
                      margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _sla_grid_figure(fc: pd.DataFrame) -> go.Figure:
    sizes = SIZE_ORDER[:-1]
    z, text = [], []
    for priority in PRIORITY_ORDER:
        z_row, t_row = [], []
        for size in sizes:
            n = int(((fc["priority_bucket"] == priority) & (fc["sla_size"] == size)).sum())
            z_row.append(n)
            t_row.append(f"{n} · SLA {SLA_BUSINESS_DAYS[priority][size]}d")
        z.append(z_row)
        text.append(t_row)
    fig = go.Figure(go.Heatmap(
        z=z, x=sizes, y=PRIORITY_ORDER, text=text, texttemplate="%{text}", colorscale="Blues",
        xgap=2, ygap=2, hovertemplate="%{y} × %{x}: %{z} in progress<extra></extra>",
        colorbar=dict(title="Tickets"),
    ))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Estimated size")
    return fig


# ── Entry point ─────────────────────────────────────────────────────────────────

def _empty_payload() -> dict:
    return {
        "total_in_progress": 0,
        "on_track": 0,
        "at_risk": 0,
        "breached": 0,
        "all_done_p85": None,
        "unsized": 0,
        "timeline_fig": None,
        "velocity_fig": None,
        "assignee_risk_fig": None,
        "sla_grid_fig": None,
        "forecast_df": pd.DataFrame(),
        "tickets_df": pd.DataFrame(),
    }


def _all_tickets_table(in_progress_df: pd.DataFrame, today_date) -> pd.DataFrame:
    """The unchanged "All In Progress Tickets" table."""
    df = in_progress_df.copy()
    defaults = {"key": "", "priority_name": "Unknown", "issuetype": "Unknown", "bussiness_lead": "Unknown",
                "creator_name": "Unknown", "updated": pd.NaT, "target_end_date": pd.NaT, "days_old": 0,
                "summary": ""}
    lead_col = _first_existing_column(df, ["bussiness_lead", "business_lead", "Business Lead"]) or "bussiness_lead"
    for col, value in defaults.items():
        if col == "bussiness_lead":
            col = lead_col
        if col not in df.columns:
            df[col] = value
    df["target_end_date"] = pd.to_datetime(df["target_end_date"], errors="coerce").dt.date
    df["days_left"] = df["target_end_date"].apply(lambda d: (d - today_date).days if pd.notnull(d) else None)
    df["updated"] = pd.to_datetime(df["updated"], errors="coerce").dt.date
    df["days_old"] = pd.to_numeric(df["days_old"], errors="coerce").fillna(0)
    df["size_group"] = df["size"]

    table = df[["key", "priority_name", "size_group", "issuetype", lead_col, "creator_name", "assignee_name",
                "updated", "target_end_date", "days_old", "days_left", "summary"]].copy()
    table.columns = ["Ticket", "Priority", "Size", "Issue Type", "Business Lead", "Creator", "Assognee Name",
                     "Last Updated", "Target End Date", "Days Old", "Days Left", "Summary"]
    table["Ticket"] = JIRA_BROWSE_BASE_URL + table["Ticket"].astype(str)
    table["Summary"] = table["Summary"].fillna("").astype(str).str[:160]
    return table.sort_values("Days Old", ascending=False)


def prepare_tickets(df_issues: pd.DataFrame) -> pd.DataFrame:
    """Tickets in scope for the forecasts: no Features/Initiatives, no excluded assignees, with
    normalised assignee, priority bucket and size."""
    tickets = _exclude_container_items(df_issues).copy()
    tickets["assignee_name"] = _normalize_assignee(tickets["assignee_name"])
    tickets = tickets[~tickets["assignee_name"].str.casefold().isin(EXCLUDED_ASSIGNEES)].copy()
    tickets["priority_bucket"] = _normalize_priority(tickets.get("priority_name", pd.Series(index=tickets.index)))
    tickets["size"] = _normalize_size(tickets.get("estimated_size_name", pd.Series(index=tickets.index)))
    if "planned_start_date" not in tickets.columns:
        tickets["planned_start_date"] = pd.NaT
    return tickets


def prepare_in_progress(tickets: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray) -> pd.DataFrame:
    """In Progress tickets with their clock start, elapsed business days and Target End day."""
    in_progress = tickets[tickets["status"].astype(str).str.strip().str.casefold().eq("in progress")].copy()
    # Clock start: Target start date; created date when it is missing; never later than today,
    # since the ticket is already being worked.
    start = _to_day(in_progress["planned_start_date"], timezone.utc)
    start = start.fillna(_to_day(in_progress["created"], LOCAL_TZ))
    in_progress["start_day"] = start.where(start <= today, today)
    in_progress["elapsed_bd"] = _busdays_between(in_progress["start_day"],
                                                 pd.Series(today, index=in_progress.index), hol).fillna(0)
    in_progress["target_end_day"] = _to_day(
        in_progress.get("target_end_date", pd.Series(pd.NaT, index=in_progress.index)), timezone.utc)
    return in_progress


def today_local() -> pd.Timestamp:
    return pd.Timestamp.now(tz=LOCAL_TZ).tz_localize(None).normalize()


def build_in_progress_visuals(df_issues: pd.DataFrame, risk_basis: str = "SLA") -> dict:
    """SLA-aware completion forecast for In Progress tickets (Features/Initiatives excluded).

    `risk_basis` (one of RISK_BASES) picks the deadline the headline risk, KPIs and charts use.
    """
    if df_issues is None or df_issues.empty:
        return _empty_payload()
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload()

    tickets = prepare_tickets(df_issues)
    today = today_local()
    hol = _calendar_holidays(today)

    in_progress = prepare_in_progress(tickets, today, hol)
    if in_progress.empty:
        return _empty_payload()

    history = _build_history(tickets, today, hol)
    _, effects = _assignee_effects(history)
    wip = _wip_factors(_typical_wip(history, in_progress, today, hol), in_progress)

    forecasts = pd.DataFrame([_forecast_ticket(row, history, effects, wip) for _, row in in_progress.iterrows()],
                             index=in_progress.index)
    fc = in_progress.join(forecasts)

    today_series = pd.Series(today, index=fc.index)
    fc["forecast_p50"] = _add_busdays(today_series, fc["remaining_p50_bd"], hol)
    fc["forecast_p85"] = _add_busdays(today_series, fc["remaining_p85_bd"], hol)
    fc["sla_size"] = fc["size"].where(fc["size"] != "Unestimated", ASSUMED_SIZE)
    fc["size_label"] = np.where(fc["size"] == "Unestimated", f"{ASSUMED_SIZE}* (assumed)", fc["size"])
    fc["sla_bd"] = [SLA_BUSINESS_DAYS[p][s] for p, s in zip(fc["priority_bucket"], fc["sla_size"])]
    fc["sla_due"] = _add_busdays(fc["start_day"], fc["sla_bd"].astype(float), hol)
    fc["sla_used_pct"] = (fc["elapsed_bd"] / fc["sla_bd"] * 100).round(0)

    def risk_against(deadline: pd.Series) -> list[str]:
        return [_risk(today, d, p50, p85) for d, p50, p85 in zip(deadline, fc["forecast_p50"], fc["forecast_p85"])]

    risk_rank = {r: i for i, r in enumerate(RISK_ORDER)}
    fc["risk_sla"] = risk_against(fc["sla_due"])
    fc["risk_target"] = risk_against(fc["target_end_day"])
    fc["risk_both"] = [min(a, b, key=risk_rank.get) for a, b in zip(fc["risk_sla"], fc["risk_target"])]
    fc["risk"] = {"SLA": fc["risk_sla"], "Target End Date": fc["risk_target"]}.get(risk_basis, fc["risk_both"])
    fc["risk_label"] = fc["risk"].map(RISK_LABELS)
    fc["wip_factor"] = fc["assignee_name"].map(wip).fillna(1.0)

    fc = fc.sort_values(["risk", "forecast_p85"], key=lambda s: s.map(risk_rank) if s.name == "risk" else s)

    forecast_df = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + fc["key"].astype(str),
        "Risk": fc["risk_label"],
        "SLA Status": fc["risk_sla"].map(RISK_LABELS),
        "Target End Status": fc["risk_target"].map(RISK_LABELS),
        "Assignee": fc["assignee_name"],
        "Priority": fc["priority_bucket"],
        "Size": fc["size_label"],
        "Target Start": fc["start_day"].dt.date,
        "Elapsed (bd)": fc["elapsed_bd"].astype(int),
        "SLA (bd)": fc["sla_bd"],
        "SLA Used %": fc["sla_used_pct"],
        "SLA Due": fc["sla_due"].dt.date,
        "Target End": fc["target_end_day"].dt.date,
        "Forecast P50": fc["forecast_p50"].dt.date,
        "Forecast P85": fc["forecast_p85"].dt.date,
        "Load Factor": fc["wip_factor"].round(2),
        "Confidence": fc["confidence"],
        "Based On": fc["basis"],
    })

    current_assignees = fc.groupby("assignee_name").size().sort_values(ascending=False).index.tolist()
    return {
        "total_in_progress": int(len(fc)),
        "on_track": int((fc["risk"] == "On Track").sum()),
        "at_risk": int(fc["risk"].isin(["At Risk", "Likely Late"]).sum()),
        "breached": int((fc["risk"] == "Breached").sum()),
        "all_done_p85": fc["forecast_p85"].max().date(),
        "unsized": int((fc["size"] == "Unestimated").sum()),
        "timeline_fig": _timeline_figure(fc, today),
        "velocity_fig": _velocity_heatmap(history, effects, current_assignees),
        "assignee_risk_fig": _assignee_risk_figure(fc),
        "sla_grid_fig": _sla_grid_figure(fc),
        "forecast_df": forecast_df,
        "tickets_df": _all_tickets_table(fc, today.date()),
    }
