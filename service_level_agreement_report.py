from __future__ import annotations

import math
from typing import Any

import pandas as pd
import plotly.express as px


TIME_PERIOD_DAYS = 90
JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"

# Practical PE-team SLA baselines when no ticket-specific deadline exists.
# All priorities currently use 90 days, but this structure is intentionally
# kept priority-specific so it can be tuned later without changing the report logic.
PRIORITY_SLA_DAYS = {
    "blocker": 90,
    "highest": 90,
    "critical": 90,
    "urgent": 90,
    "high": 90,
    "medium": 90,
    "low": 90,
    "lowest": 90,
}


def _empty_payload() -> dict[str, Any]:
    return {
        "total_tickets": 0,
        "on_track": 0,
        "at_risk": 0,
        "breached": 0,
        "unknown": 0,
        "breach_rate": 0.0,
        "at_risk_rate": 0.0,
        "median_elapsed_days": 0.0,
        "median_sla_target_days": 0.0,
        "status_fig": None,
        "priority_fig": None,
        "box_fig": None,
        "heatmap_fig": None,
        "trend_fig": None,
        "detail_df": pd.DataFrame(),
        "breached_df": pd.DataFrame(),
    }


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _normalize_text(value: Any, default: str = "Unknown") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    text = str(value).strip()
    return text if text else default


def _canonical_priority(value: Any) -> str:
    text = _normalize_text(value, "No Priority").lower().replace(" ", "")
    if text in {"p1", "prio1"}:
        return "Critical"
    if text in {"p2", "prio2"}:
        return "High"
    if text in {"p3", "prio3"}:
        return "Medium"
    if text in {"p4", "prio4"}:
        return "Low"
    mapping = {
        "highest": "Critical",
        "critical": "Critical",
        "urgent": "Critical",
        "blocker": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Lowest",
        "nopriority": "No Priority",
        "none": "No Priority",
    }
    return mapping.get(text, _normalize_text(value, "No Priority"))


def _priority_sla_days(priority: str) -> int:
    return PRIORITY_SLA_DAYS.get(priority.lower(), 90)


def _safe_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _sigmoid(x: float) -> float:
    # Smooth risk score between 0 and 1.
    return 1.0 / (1.0 + math.exp(-x))


def _build_risk_score(elapsed: float, target: float, is_closed: bool) -> float:
    if pd.isna(elapsed) or pd.isna(target) or target <= 0:
        return 0.0
    if is_closed:
        return 1.0 if elapsed > target else 0.0
    progress = elapsed / target
    # 0.8 => low risk, 1.0 => high risk; slope tuned for leadership use.
    return float(max(0.0, min(1.0, _sigmoid((progress - 0.82) * 10.0))))


def build_sla_visuals(df_issues: pd.DataFrame, time_period_days: int = TIME_PERIOD_DAYS) -> dict[str, Any]:
    """Build SLA visuals for the PE team using priority-based targets over the last N days.

    Best-practice approach used here:
    - Filter to tickets created or resolved in the last N days.
    - Derive SLA target days from ticket-specific target end date when available.
    - Fall back to conservative priority-based SLA targets.
    - Use robust stats (median, p75, breach rate, at-risk rate) and interactive Plotly charts.
    """
    if df_issues is None or df_issues.empty:
        return _empty_payload()

    required = {"status", "priority_name", "assignee_name", "created"}
    if not required.issubset(df_issues.columns):
        return _empty_payload()

    df = df_issues.copy()
    df["priority_label"] = df["priority_name"].apply(_canonical_priority)
    df["assignee_label"] = df["assignee_name"].apply(lambda x: _normalize_text(x, "Unassigned"))
    df["status_label"] = df["status"].apply(lambda x: _normalize_text(x, "Unknown"))

    allowed_statuses = {
        "in progress",
        "on hold",
        "blocked",
        "to do",
        "validating",
        "tech discovery required",
    }

    created_col = _first_existing_column(df, ["created", "Created"])
    updated_col = _first_existing_column(df, ["updated", "Updated", "last_updated"])
    resolved_col = _first_existing_column(df, ["resolved", "resolutiondate", "resolved_date", "completed_date", "done_date"])
    target_end_col = _first_existing_column(df, ["target_end_date", "project_due_date", "Target End Date"])
    key_col = _first_existing_column(df, ["key", "Key", "ticket", "Ticket"])
    summary_col = _first_existing_column(df, ["summary", "Summary"])

    # Exclude CAR project tickets from SLA analysis.
    car_mask = pd.Series(False, index=df.index)
    if "project_name" in df.columns:
        car_mask = car_mask | df["project_name"].astype(str).str.strip().str.upper().eq("CAR")
    if "project_key" in df.columns:
        car_mask = car_mask | df["project_key"].astype(str).str.strip().str.upper().eq("CAR")
    if key_col is not None:
        car_mask = car_mask | df[key_col].astype(str).str.strip().str.upper().str.startswith("CAR-")
    df = df[~car_mask].copy()
    if df.empty:
        return _empty_payload()

    df = df[df["status_label"].str.lower().isin(allowed_statuses)].copy()
    if df.empty:
        return _empty_payload()

    df["created_dt"] = _safe_datetime(df[created_col])
    if updated_col is not None:
        df["updated_dt"] = _safe_datetime(df[updated_col])
    else:
        df["updated_dt"] = pd.NaT
    if resolved_col is not None:
        df["resolved_dt"] = _safe_datetime(df[resolved_col])
    else:
        df["resolved_dt"] = pd.NaT
    if target_end_col is not None:
        df["target_end_dt"] = _safe_datetime(df[target_end_col])
    else:
        df["target_end_dt"] = pd.NaT

    now = pd.Timestamp.now(tz="UTC")
    window_start = now.normalize() - pd.Timedelta(days=time_period_days)

    # Keep tickets created or resolved in the SLA review window.
    df = df[(df["created_dt"] >= window_start) | (df["resolved_dt"] >= window_start)].copy()
    if df.empty:
        return _empty_payload()

    done_statuses = {"done", "closed", "resolved", "complete", "completed"}
    df["is_closed"] = df["status_label"].str.lower().isin(done_statuses) | df["resolved_dt"].notna()

    df["resolved_or_now_dt"] = df["resolved_dt"].where(df["resolved_dt"].notna(), now)
    # If this is still open but has been updated more recently than created, keep the latest reliable timestamp.
    df.loc[~df["is_closed"], "resolved_or_now_dt"] = now

    df["elapsed_days"] = (df["resolved_or_now_dt"] - df["created_dt"]).dt.total_seconds() / 86400.0
    df["elapsed_days"] = pd.to_numeric(df["elapsed_days"], errors="coerce")

    # SLA target days: prefer ticket-specific target end date if present; otherwise derive from priority.
    priority_target_days = df["priority_label"].apply(_priority_sla_days)
    if target_end_col is not None:
        ticket_target_days = (df["target_end_dt"] - df["created_dt"]).dt.total_seconds() / 86400.0
        ticket_target_days = pd.to_numeric(ticket_target_days, errors="coerce")
        df["sla_target_days"] = ticket_target_days.where(ticket_target_days > 0, priority_target_days)
    else:
        df["sla_target_days"] = priority_target_days

    df["remaining_days"] = df["sla_target_days"] - df["elapsed_days"]
    df["risk_score"] = [
        _build_risk_score(elapsed, target, closed)
        for elapsed, target, closed in zip(df["elapsed_days"], df["sla_target_days"], df["is_closed"])
    ]

    def _sla_status(row: pd.Series) -> str:
        elapsed = row["elapsed_days"]
        target = row["sla_target_days"]
        if pd.isna(elapsed) or pd.isna(target) or target <= 0:
            return "Unknown"
        if row["is_closed"]:
            return "Breached" if elapsed > target else "On Track"
        if elapsed > target:
            return "Breached"
        if elapsed >= (0.8 * target):
            return "At Risk"
        return "On Track"

    df["sla_status"] = df.apply(_sla_status, axis=1)
    status_order = ["On Track", "At Risk", "Breached", "Unknown"]
    priority_order = ["Critical", "High", "Medium", "Low", "Lowest", "No Priority"]
    df["priority_label"] = pd.Categorical(df["priority_label"], categories=priority_order, ordered=True)

    # Overall KPIs
    total_tickets = int(len(df))
    on_track = int((df["sla_status"] == "On Track").sum())
    at_risk = int((df["sla_status"] == "At Risk").sum())
    breached = int((df["sla_status"] == "Breached").sum())
    unknown = int((df["sla_status"] == "Unknown").sum())
    breach_rate = float(breached / total_tickets * 100.0) if total_tickets else 0.0
    at_risk_rate = float(at_risk / total_tickets * 100.0) if total_tickets else 0.0
    median_elapsed_days = float(df["elapsed_days"].median()) if not df["elapsed_days"].dropna().empty else 0.0
    median_target_days = float(df["sla_target_days"].median()) if not df["sla_target_days"].dropna().empty else 0.0

    # Counts and rates by priority.
    by_priority = (
        df.groupby(["priority_label", "sla_status"], observed=True)
        .size()
        .reset_index(name="count")
    )
    priority_totals = df.groupby("priority_label", observed=True).size().reset_index(name="total")
    priority_summary = (
        df.groupby("priority_label", observed=True)
        .agg(
            total_tickets=("sla_status", "size"),
            breached=("sla_status", lambda s: int((s == "Breached").sum())),
            at_risk=("sla_status", lambda s: int((s == "At Risk").sum())),
            on_track=("sla_status", lambda s: int((s == "On Track").sum())),
            median_elapsed_days=("elapsed_days", "median"),
            p75_elapsed_days=("elapsed_days", lambda s: float(s.quantile(0.75)) if not s.dropna().empty else 0.0),
            median_target_days=("sla_target_days", "median"),
        )
        .reset_index()
    )
    priority_summary["breach_rate_pct"] = priority_summary["breached"] / priority_summary["total_tickets"] * 100.0
    priority_summary["at_risk_rate_pct"] = priority_summary["at_risk"] / priority_summary["total_tickets"] * 100.0
    priority_summary["on_track_rate_pct"] = priority_summary["on_track"] / priority_summary["total_tickets"] * 100.0
    priority_summary["priority_label"] = priority_summary["priority_label"].astype(str)

    # Interactive visual 1: overall SLA status mix.
    status_fig = px.pie(
        df,
        names="sla_status",
        color="sla_status",
        category_orders={"sla_status": status_order},
        title="SLA Status Mix (last 90 days)",
        hole=0.48,
        color_discrete_map={
            "On Track": "#16a34a",
            "At Risk": "#f59e0b",
            "Breached": "#dc2626",
            "Unknown": "#94a3b8",
        },
    )
    status_fig.update_layout(height=390, legend_title_text="SLA Status")

    # Interactive visual 2: priority distribution of SLA status.
    priority_fig = px.bar(
        by_priority,
        x="priority_label",
        y="count",
        color="sla_status",
        barmode="stack",
        category_orders={"priority_label": priority_order, "sla_status": status_order},
        title="SLA Performance by Priority",
        labels={"priority_label": "Priority", "count": "Tickets"},
        color_discrete_map={
            "On Track": "#16a34a",
            "At Risk": "#f59e0b",
            "Breached": "#dc2626",
            "Unknown": "#94a3b8",
        },
        hover_data={"count": True},
    )
    priority_fig.update_layout(height=420, xaxis_title="Priority", yaxis_title="Ticket Count")

    # Interactive visual 3: elapsed days vs SLA target days, with a robust box layout.
    box_fig = px.box(
        df,
        x="priority_label",
        y="elapsed_days",
        color="sla_status",
        points="outliers",
        category_orders={"priority_label": priority_order, "sla_status": status_order},
        title="Elapsed Days vs SLA Target by Priority",
        labels={"priority_label": "Priority", "elapsed_days": "Elapsed Days"},
        color_discrete_map={
            "On Track": "#16a34a",
            "At Risk": "#f59e0b",
            "Breached": "#dc2626",
            "Unknown": "#94a3b8",
        },
        hover_data=["sla_target_days", "remaining_days", "risk_score"],
    )
    box_fig.update_layout(height=450, xaxis_title="Priority", yaxis_title="Elapsed Days")

    # Interactive visual 4: breach risk heatmap by assignee and priority.
    assignee_priority = (
        df.groupby(["assignee_label", "priority_label"], observed=True)
        .agg(
            total=("sla_status", "size"),
            breached=("sla_status", lambda s: int((s == "Breached").sum())),
            breach_rate=("sla_status", lambda s: float((s == "Breached").mean() * 100.0)),
        )
        .reset_index()
    )
    top_assignees = (
        df["assignee_label"].value_counts().head(15).index.tolist()
    )
    heatmap_df = assignee_priority[assignee_priority["assignee_label"].isin(top_assignees)].copy()

    heatmap_fig = px.density_heatmap(
        heatmap_df,
        x="priority_label",
        y="assignee_label",
        z="breach_rate",
        histfunc="avg",
        color_continuous_scale="RdYlGn_r",
        category_orders={"priority_label": priority_order},
        title="SLA Breach Rate Heatmap by Assignee (Top 15)",
        labels={"priority_label": "Priority", "assignee_label": "Assignee", "breach_rate": "Breach Rate %"},
        text_auto=True,
    )
    heatmap_fig.update_layout(height=520, xaxis_title="Priority", yaxis_title="Assignee")

    # Interactive visual 5: weekly trend of SLA health.
    weekly = df.copy()
    weekly["week"] = weekly["created_dt"].dt.to_period("W").dt.start_time
    trend_df = (
        weekly.groupby("week", as_index=False)
        .agg(
            created=("sla_status", "size"),
            breached=("sla_status", lambda s: int((s == "Breached").sum())),
            at_risk=("sla_status", lambda s: int((s == "At Risk").sum())),
            on_track=("sla_status", lambda s: int((s == "On Track").sum())),
        )
    )
    trend_df["breach_rate_pct"] = trend_df.apply(
        lambda r: (r["breached"] / r["created"] * 100.0) if r["created"] else 0.0,
        axis=1,
    )
    trend_fig = px.line(
        trend_df,
        x="week",
        y=["created", "breached", "at_risk"],
        markers=True,
        title="SLA Trend Over Time (Created Tickets by Week)",
        labels={"value": "Tickets", "week": "Week"},
    )
    trend_fig.update_layout(height=380, xaxis_title="Week", yaxis_title="Ticket Count", legend_title_text="Metric")

    # Detail table for leadership (focus on actionable tickets only).
    detail_source = df[df["sla_status"].isin(["At Risk", "Breached"])].copy()

    detail_df = detail_source[
        [
            c for c in [
                key_col,
                summary_col,
                "priority_label",
                "assignee_label",
                "status_label",
                "created_dt",
                "target_end_dt",
                "resolved_dt",
                "sla_target_days",
                "elapsed_days",
                "remaining_days",
                "risk_score",
                "sla_status",
            ]
            if c is not None and c in df.columns
        ]
    ].copy()
    detail_df.columns = [
        "Ticket" if c == key_col else
        "Summary" if c == summary_col else
        "Priority" if c == "priority_label" else
        "Assignee" if c == "assignee_label" else
        "Status" if c == "status_label" else
        "Created" if c == "created_dt" else
        "Target End" if c == "target_end_dt" else
        "Resolved" if c == "resolved_dt" else
        "SLA Target Days" if c == "sla_target_days" else
        "Elapsed Days" if c == "elapsed_days" else
        "Remaining Days" if c == "remaining_days" else
        "Risk Score" if c == "risk_score" else
        "SLA Status"
        for c in detail_df.columns
    ]
    if "Ticket" in detail_df.columns:
        detail_df["Ticket"] = detail_df["Ticket"].astype(str).apply(lambda t: f"{JIRA_BROWSE_BASE_URL}{t}")
    if "Summary" in detail_df.columns:
        detail_df["Summary"] = detail_df["Summary"].fillna("").astype(str).str[:180]

    # Keep urgent-first ordering for leadership review.
    if "SLA Status" in detail_df.columns:
        status_order_map = {"Breached": 0, "At Risk": 1}
        detail_df["__status_sort"] = detail_df["SLA Status"].map(status_order_map).fillna(99)
    else:
        detail_df["__status_sort"] = 99
    if "Risk Score" in detail_df.columns:
        detail_df = detail_df.sort_values(["__status_sort", "Risk Score"], ascending=[True, False])
    else:
        detail_df = detail_df.sort_values(["__status_sort"], ascending=[True])
    detail_df = detail_df.drop(columns=["__status_sort"])

    breached_df = df[df["sla_status"] == "Breached"].copy()
    breached_df = breached_df.sort_values(["risk_score", "elapsed_days"], ascending=[False, False])
    breached_df = breached_df[
        [
            c for c in [
                key_col,
                summary_col,
                "priority_label",
                "assignee_label",
                "elapsed_days",
                "sla_target_days",
                "remaining_days",
                "risk_score",
            ]
            if c is not None and c in breached_df.columns
        ]
    ].copy()
    breached_df.columns = [
        "Ticket" if c == key_col else
        "Summary" if c == summary_col else
        "Priority" if c == "priority_label" else
        "Assignee" if c == "assignee_label" else
        "Elapsed Days" if c == "elapsed_days" else
        "SLA Target Days" if c == "sla_target_days" else
        "Remaining Days" if c == "remaining_days" else
        "Risk Score"
        for c in breached_df.columns
    ]
    if "Ticket" in breached_df.columns:
        breached_df["Ticket"] = breached_df["Ticket"].astype(str).apply(lambda t: f"{JIRA_BROWSE_BASE_URL}{t}")
    if "Summary" in breached_df.columns:
        breached_df["Summary"] = breached_df["Summary"].fillna("").astype(str).str[:180]

    return {
        "total_tickets": total_tickets,
        "on_track": on_track,
        "at_risk": at_risk,
        "breached": breached,
        "unknown": unknown,
        "breach_rate": breach_rate,
        "at_risk_rate": at_risk_rate,
        "median_elapsed_days": median_elapsed_days,
        "median_sla_target_days": median_target_days,
        "status_fig": status_fig,
        "priority_fig": priority_fig,
        "box_fig": box_fig,
        "heatmap_fig": heatmap_fig,
        "trend_fig": trend_fig,
        "detail_df": detail_df,
        "breached_df": breached_df,
        "priority_summary_df": priority_summary,
    }
