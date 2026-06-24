from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.compose import ColumnTransformer
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.base import clone

from .velocity_report import PE_TEAM_MEMBERS


_DONE_STATUSES = {"Done", "Closed", "Resolved"}
JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"


def _empty_payload() -> dict:
    return {
        "model_bundle": None,
        "priority_options": [],
        "assignee_options": [],
        "training_rows": 0,
        "training_accuracy": None,
        "validation_rows": 0,
        "validation_accuracy": None,
        "accuracy_target_met": False,
        "model_name": None,
        "probability_calibrated": False,
        "calibration_method": None,
        "on_time_rate": None,
        "average_validation_days": 0.0,
        "error_message": None,
    }


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col

    # Fuzzy fallback for variations in case, spacing, underscores, and dashes.
    def _norm(v: str) -> str:
        return str(v).strip().casefold().replace("_", " ").replace("-", " ")

    normalized_columns = {_norm(c): c for c in df.columns}
    for col in candidates:
        found = normalized_columns.get(_norm(col))
        if found is not None:
            return found

    return None


def _trimmed_mean(series: pd.Series, lower_q: float = 0.05, upper_q: float = 0.95) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return 0.0
    if len(s) < 5:
        return float(s.median())

    low = float(s.quantile(lower_q))
    high = float(s.quantile(upper_q))
    trimmed = s[(s >= low) & (s <= high)]
    if trimmed.empty:
        return float(s.median())
    return float(trimmed.mean())


def _build_smoothed_validation_maps(
    df: pd.DataFrame,
    *,
    value_col: str,
    assignee_col: str,
    priority_col: str | None,
    prior_weight: float = 8.0,
) -> tuple[float, dict[str, float], dict[str, float]]:
    global_days = _trimmed_mean(df[value_col])

    assignee_stats = (
        df.groupby(assignee_col)[value_col]
        .agg([("group_mean", "mean"), ("group_count", "size")])
        .reset_index()
    )
    assignee_map = {
        str(row[assignee_col]): float(
            (row["group_mean"] * row["group_count"] + global_days * prior_weight)
            / (row["group_count"] + prior_weight)
        )
        for _, row in assignee_stats.iterrows()
    }

    priority_map: dict[str, float] = {}
    if priority_col is not None:
        priority_stats = (
            df.groupby(priority_col)[value_col]
            .agg([("group_mean", "mean"), ("group_count", "size")])
            .reset_index()
        )
        priority_map = {
            str(row[priority_col]): float(
                (row["group_mean"] * row["group_count"] + global_days * prior_weight)
                / (row["group_count"] + prior_weight)
            )
            for _, row in priority_stats.iterrows()
        }

    return global_days, assignee_map, priority_map


def _build_smoothed_on_time_rate_maps(
    df: pd.DataFrame,
    *,
    on_time_col: str,
    assignee_col: str,
    priority_col: str | None,
    prior_weight: float = 8.0,
) -> tuple[float, dict[str, float], dict[str, float]]:
    """Build Bayesian-smoothed on-time rate maps to stabilize small groups."""
    global_rate = float(df[on_time_col].mean())

    assignee_stats = (
        df.groupby(assignee_col)[on_time_col]
        .agg(["sum", "size"])
        .reset_index()
        .rename(columns={on_time_col + "sum": "on_time_count", on_time_col + "size": "group_size"})
    )
    # Fix column names after agg
    assignee_stats.columns = [assignee_col, "on_time_count", "group_size"]
    assignee_map = {
        str(row[assignee_col]): float(
            (row["on_time_count"] + global_rate * prior_weight) / (row["group_size"] + prior_weight)
        )
        for _, row in assignee_stats.iterrows()
    }

    priority_map: dict[str, float] = {}
    if priority_col is not None:
        priority_stats = (
            df.groupby(priority_col)[on_time_col]
            .agg(["sum", "size"])
            .reset_index()
        )
        priority_stats.columns = [priority_col, "on_time_count", "group_size"]
        priority_map = {
            str(row[priority_col]): float(
                (row["on_time_count"] + global_rate * prior_weight) / (row["group_size"] + prior_weight)
            )
            for _, row in priority_stats.iterrows()
        }

    return global_rate, assignee_map, priority_map


def _compute_schedule_adherence(on_time_series: pd.Series, days_late_series: pd.Series, tolerance_days: float = 7.0) -> pd.Series:
    """Compute continuous schedule adherence score (0 to 1) penalizing based on days late.
    
    - On-time tickets (on_time=1) get score 1.0
    - Late tickets (on_time=0) get score = max(0, 1.0 - (days_late / tolerance_days))
    - This captures "how late" as a continuous feature for the model.
    """
    adherence = pd.Series(1.0, index=on_time_series.index)
    late_mask = on_time_series == 0
    adherence.loc[late_mask] = np.maximum(0.0, 1.0 - (days_late_series.loc[late_mask] / tolerance_days))
    return adherence


def _apply_leave_one_out_history_features(
    feature_df: pd.DataFrame,
    base_df: pd.DataFrame,
    *,
    assignee_col: str,
    priority_col: str,
    prior_weight: float = 8.0,
) -> pd.DataFrame:
    """Replace history features with leave-one-out variants to reduce target leakage."""
    out = feature_df.copy()
    work = base_df.copy()

    global_on_time = float(work["on_time"].mean())
    global_schedule = float(work["schedule_adherence"].mean())

    assignee_count = work.groupby(assignee_col)["on_time"].size().to_dict()
    assignee_on_time_sum = work.groupby(assignee_col)["on_time"].sum().to_dict()
    assignee_schedule_sum = work.groupby(assignee_col)["schedule_adherence"].sum().to_dict()

    priority_count = work.groupby(priority_col)["on_time"].size().to_dict()
    priority_on_time_sum = work.groupby(priority_col)["on_time"].sum().to_dict()
    priority_schedule_sum = work.groupby(priority_col)["schedule_adherence"].sum().to_dict()

    a_count = work[assignee_col].map(assignee_count).astype(float)
    p_count = work[priority_col].map(priority_count).astype(float)

    out["assignee_done_90"] = np.maximum(a_count - 1.0, 0.0)

    out["assignee_on_time_rate_90"] = (
        (work[assignee_col].map(assignee_on_time_sum).astype(float) - work["on_time"].astype(float) + global_on_time * prior_weight)
        / (np.maximum(a_count - 1.0, 0.0) + prior_weight)
    )
    out["priority_on_time_rate_90"] = (
        (work[priority_col].map(priority_on_time_sum).astype(float) - work["on_time"].astype(float) + global_on_time * prior_weight)
        / (np.maximum(p_count - 1.0, 0.0) + prior_weight)
    )

    out["assignee_schedule_adherence_90"] = (
        (
            work[assignee_col].map(assignee_schedule_sum).astype(float)
            - work["schedule_adherence"].astype(float)
            + global_schedule * prior_weight
        )
        / (np.maximum(a_count - 1.0, 0.0) + prior_weight)
    )
    out["priority_schedule_adherence_90"] = (
        (
            work[priority_col].map(priority_schedule_sum).astype(float)
            - work["schedule_adherence"].astype(float)
            + global_schedule * prior_weight
        )
        / (np.maximum(p_count - 1.0, 0.0) + prior_weight)
    )

    return out


def build_probability_training_detail_table(
    df_issues: pd.DataFrame,
    lookback_days: int = 90,
    assignee_filter: str | None = None,
    priority_filter: str | None = None,
) -> pd.DataFrame:
    if df_issues is None or df_issues.empty:
        return pd.DataFrame()

    df = df_issues.copy()

    key_col = _pick_col(df, ["key", "Key", "ticket", "Ticket"])
    assignee_col = _pick_col(df, ["assignee_name", "assignee"])
    lead_col = _pick_col(df, ["bussiness_lead", "business_lead", "Business Lead"])
    status_col = _pick_col(df, ["status"])
    created_col = _pick_col(df, ["created", "Created"])
    priority_col = _pick_col(df, ["priority_name", "priority", "Priority"])
    end_col = _pick_col(df, ["target_end_date", "project_due_date", "duedate", "Target End Date"])
    updated_col = _pick_col(df, ["updated", "Updated"])

    required = [key_col, assignee_col, status_col, created_col, end_col, updated_col]
    if any(col is None for col in required):
        return pd.DataFrame()

    today = pd.Timestamp.today(tz="UTC").normalize()
    cutoff = today - pd.Timedelta(days=lookback_days)

    df[assignee_col] = df[assignee_col].fillna("Unassigned").astype(str)
    df[status_col] = df[status_col].fillna("Unknown").astype(str)
    df["_status_norm"] = df[status_col].str.strip().str.casefold()

    df = df[df[assignee_col].isin(PE_TEAM_MEMBERS)].copy()
    if df.empty:
        return pd.DataFrame()

    df[created_col] = pd.to_datetime(df[created_col], errors="coerce", utc=True)
    df[end_col] = pd.to_datetime(df[end_col], errors="coerce", utc=True)
    df[updated_col] = pd.to_datetime(df[updated_col], errors="coerce", utc=True)

    # Requested rule alignment: completed date uses updated timestamp.
    df["_completed_dt"] = df[updated_col]

    detail_df = df[
        (df["_status_norm"] == "done")
        & (df["_completed_dt"].notna())
        & (df[created_col].notna())
        & (df[end_col].notna())
        & (df["_completed_dt"] >= cutoff)
    ].copy()
    if detail_df.empty:
        return pd.DataFrame()

    if assignee_filter:
        detail_df = detail_df[detail_df[assignee_col].astype(str) == str(assignee_filter)].copy()
        if detail_df.empty:
            return pd.DataFrame()

    detail_df["validation_days"] = (
        (detail_df["_completed_dt"] - detail_df[end_col]).dt.total_seconds() / 86400.0
    )
    detail_df["validation_delay_days"] = detail_df["validation_days"].clip(lower=0)
    average_validation_days, assignee_validation_days, priority_validation_days = _build_smoothed_validation_maps(
        detail_df,
        value_col="validation_delay_days",
        assignee_col=assignee_col,
        priority_col=priority_col,
    )
    selected_validation_days = float(
        assignee_validation_days.get(
            str(assignee_filter),
            priority_validation_days.get(str(priority_filter), average_validation_days),
        )
    )
    detail_df["_adjusted_completed_dt"] = detail_df["_completed_dt"] - pd.Timedelta(days=selected_validation_days)

    detail_df["On Time"] = np.where(
        detail_df["_adjusted_completed_dt"].dt.normalize() <= detail_df[end_col].dt.normalize(),
        "Yes",
        "No",
    )
    detail_df["past_due_days"] = (
        detail_df["_completed_dt"].dt.normalize() - detail_df[end_col].dt.normalize()
    ).dt.days

    def _fmt_date(series: pd.Series) -> pd.Series:
        return series.dt.strftime("%Y-%m-%d").fillna("")

    out = pd.DataFrame(
        {
            "Ticket No": detail_df[key_col].astype(str).apply(lambda t: f"{JIRA_BROWSE_BASE_URL}{t}"),
            "Assignee": detail_df[assignee_col].astype(str),
            "Business Lead": detail_df[lead_col].astype(str) if lead_col is not None else "",
            "Priority": detail_df[priority_col].astype(str) if priority_col is not None else "",
            "Created Date": _fmt_date(detail_df[created_col]),
            "End Date": _fmt_date(detail_df[end_col]),
            "Completed Date": _fmt_date(detail_df["_completed_dt"]),
            "Past Due Days": detail_df["past_due_days"].astype(int),
            "Avg Validation Time (days)": round(selected_validation_days, 1),
            "On Time": detail_df["On Time"],
        }
    )

    out = out.sort_values(by="Completed Date", ascending=False).reset_index(drop=True)
    return out


def build_probability_training_distribution_figures(detail_df: pd.DataFrame) -> dict:
    if detail_df is None or detail_df.empty:
        return {"on_time_fig": None, "past_due_fig": None}

    work = detail_df.copy()

    on_time_counts = (
        work["On Time"]
        .fillna("Unknown")
        .astype(str)
        .value_counts()
        .reindex(["Yes", "No"], fill_value=0)
    )
    on_time_fig = go.Figure(
        go.Pie(
            labels=on_time_counts.index.tolist(),
            values=on_time_counts.values.tolist(),
            hole=0.45,
            textinfo="label+percent",
            marker=dict(colors=["#16a34a", "#dc2626"]),
        )
    )
    on_time_fig.update_layout(
        title="On Time Distribution with Validation Adjustment",
        height=320,
        margin=dict(l=20, r=20, t=50, b=20),
        legend_title_text="On Time",
    )

    work["Past Due Days"] = pd.to_numeric(work["Past Due Days"], errors="coerce").fillna(0)
    past_due_bucket = np.where(work["Past Due Days"] <= 3, "On Time", "Late") # the 3 is for a 3-day tolerance window for validation delays
    past_due_counts = (
        pd.Series(past_due_bucket)
        .value_counts()
        .reindex(["On Time", "Late"], fill_value=0)
    )
    past_due_fig = go.Figure(
        go.Pie(
            labels=past_due_counts.index.tolist(),
            values=past_due_counts.values.tolist(),
            hole=0.45,
            textinfo="label+percent",
            marker=dict(colors=["#0ea5e9", "#f59e0b"]),
        )
    )
    past_due_fig.update_layout(
        title="Completion of Work Distribution + 3 Day Tolerance Window",
        height=320,
        margin=dict(l=20, r=20, t=50, b=20),
        legend_title_text="Completion Status",
    )

    return {"on_time_fig": on_time_fig, "past_due_fig": past_due_fig}


def _build_feature_frame(
    df: pd.DataFrame,
    *,
    priority_col: str,
    assignee_col: str,
    assignee_velocity: dict,
    priority_velocity: dict,
    assignee_done_count: dict,
    assignee_on_time_rate: dict,
    priority_on_time_rate: dict,
    assignee_backlog: dict,
    assignee_priority_backlog: dict,
    assignee_schedule_adherence: dict,
    priority_schedule_adherence: dict,
    global_assignee_velocity: float,
    global_priority_velocity: float,
    global_assignee_on_time_rate: float,
    global_priority_on_time_rate: float,
    global_schedule_adherence: float,
) -> pd.DataFrame:
    feature_df = df.copy()
    feature_df["assignee_velocity_90"] = (
        feature_df[assignee_col].map(assignee_velocity).fillna(global_assignee_velocity).astype(float)
    )
    feature_df["priority_velocity_90"] = (
        feature_df[priority_col].map(priority_velocity).fillna(global_priority_velocity).astype(float)
    )
    feature_df["assignee_done_90"] = feature_df[assignee_col].map(assignee_done_count).fillna(1).astype(float)
    feature_df["assignee_on_time_rate_90"] = (
        feature_df[assignee_col].map(assignee_on_time_rate).fillna(global_assignee_on_time_rate).astype(float)
    )
    feature_df["priority_on_time_rate_90"] = (
        feature_df[priority_col].map(priority_on_time_rate).fillna(global_priority_on_time_rate).astype(float)
    )
    feature_df["assignee_backlog_open"] = feature_df[assignee_col].map(assignee_backlog).fillna(0).astype(float)
    feature_df["assignee_priority_backlog"] = [
        float(assignee_priority_backlog.get((a, p), 0))
        for a, p in zip(feature_df[assignee_col], feature_df[priority_col])
    ]
    feature_df["velocity_gap_assignee"] = (
        feature_df["budget_days"] - feature_df["assignee_velocity_90"]
    ).astype(float)
    feature_df["velocity_gap_priority"] = (
        feature_df["budget_days"] - feature_df["priority_velocity_90"]
    ).astype(float)
    feature_df["velocity_ratio_assignee"] = (
        feature_df["budget_days"] / np.maximum(feature_df["assignee_velocity_90"], 1.0)
    ).astype(float)
    feature_df["velocity_ratio_priority"] = (
        feature_df["budget_days"] / np.maximum(feature_df["priority_velocity_90"], 1.0)
    ).astype(float)
    feature_df["assignee_schedule_adherence_90"] = (
        feature_df[assignee_col].map(assignee_schedule_adherence).fillna(global_schedule_adherence).astype(float)
    )
    feature_df["priority_schedule_adherence_90"] = (
        feature_df[priority_col].map(priority_schedule_adherence).fillna(global_schedule_adherence).astype(float)
    )
    return feature_df


def build_completion_on_time_model(df_issues: pd.DataFrame, lookback_days: int = 90) -> dict:
    payload = _empty_payload()
    if df_issues is None or df_issues.empty:
        payload["error_message"] = "No Jira data available."
        return payload

    df = df_issues.copy()

    priority_col = _pick_col(df, ["priority_name", "priority"])
    assignee_col = _pick_col(df, ["assignee_name", "assignee"])
    status_col = _pick_col(df, ["status"])
    created_col = _pick_col(df, ["created"])
    updated_col = _pick_col(df, ["updated"])
    deadline_col = _pick_col(df, ["target_end_date", "project_due_date", "duedate"])

    required = {
        "priority": priority_col,
        "assignee": assignee_col,
        "status": status_col,
        "created": created_col,
        "updated": updated_col,
        "deadline": deadline_col,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        payload["error_message"] = f"Missing required columns for ML model: {', '.join(missing)}"
        return payload

    today = pd.Timestamp.today(tz="UTC").normalize()
    cutoff = today - pd.Timedelta(days=lookback_days)

    df[priority_col] = df[priority_col].fillna("No Priority").astype(str)
    df[assignee_col] = df[assignee_col].fillna("Unassigned").astype(str)
    df[status_col] = df[status_col].fillna("Unknown").astype(str)
    df["_status_norm"] = df[status_col].astype(str).str.strip().str.casefold()

    # Only keep PE team assignees for this model/report.
    df = df[df[assignee_col].isin(PE_TEAM_MEMBERS)].copy()
    if df.empty:
        payload["error_message"] = "No PE team tickets found for the selected dataset."
        return payload

    datetime_cols = [created_col, updated_col, deadline_col]

    for col in datetime_cols:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    # Requested rule: completion timestamp is the last update timestamp.
    df["_completion_ts"] = df[updated_col]
    df["_updated_ts"] = df[updated_col]
    df["_effective_done_ts"] = df["_completion_ts"]

    # Training set: Done tickets with explicit deadline in the last N days,
    # where completion timestamp is the ticket's updated timestamp.
    train_df = df[
        (df["_status_norm"] == "done")
        & (df["_effective_done_ts"].notna())
        & (df[created_col].notna())
        & (df[deadline_col].notna())
        & (df["_effective_done_ts"] >= cutoff)
    ].copy()

    if train_df.empty:
        payload["error_message"] = (
            f"No Done tickets with deadlines in the last {lookback_days} days by updated timestamp."
        )
        return payload

    train_df["cycle_days"] = (train_df["_effective_done_ts"] - train_df[created_col]).dt.days
    train_df["budget_days"] = (train_df[deadline_col] - train_df[created_col]).dt.days
    train_df = train_df[(train_df["cycle_days"] >= 0) & (train_df["budget_days"] >= 1)].copy()
    if len(train_df) < 20:
        payload["error_message"] = f"Not enough training rows after cleanup ({len(train_df)}). Need at least 20."
        return payload

    # Calculate average validation time (difference between updated date and target end date)
    train_df["validation_days"] = (
        (train_df["_effective_done_ts"] - train_df[deadline_col]).dt.total_seconds() / 86400.0
    )
    train_df["validation_delay_days"] = train_df["validation_days"].clip(lower=0)
    average_validation_days, assignee_validation_days, priority_validation_days = _build_smoothed_validation_maps(
        train_df,
        value_col="validation_delay_days",
        assignee_col=assignee_col,
        priority_col=priority_col,
    )

    # Adjust the completed date by subtracting the average validation time
    # This accounts for typical review/validation delays
    train_df["_effective_validation_days"] = [
        float(
            assignee_validation_days.get(
                str(a),
                priority_validation_days.get(str(p), average_validation_days),
            )
        )
        for a, p in zip(train_df[assignee_col], train_df[priority_col])
    ]
    train_df["_adjusted_done_ts"] = train_df["_effective_done_ts"] - pd.to_timedelta(
        train_df["_effective_validation_days"], unit="D"
    )

    # Calculate on_time using the adjusted completion date
    train_df["on_time"] = (
        train_df["_adjusted_done_ts"].dt.normalize() <= train_df[deadline_col].dt.normalize()
    ).astype(int)
    if train_df["on_time"].nunique() < 2:
        payload["error_message"] = "Training data has only one target class. Need both on-time and late outcomes."
        return payload

    # Compute schedule adherence score (continuous 0-1 metric penalizing lateness)
    # Days late = (adjusted_done_ts - deadline) in days
    train_df["days_late"] = (
        (train_df["_adjusted_done_ts"] - train_df[deadline_col]).dt.total_seconds() / 86400.0
    )
    train_df["schedule_adherence"] = _compute_schedule_adherence(
        train_df["on_time"], train_df["days_late"], tolerance_days=7.0
    )

    # Backlog features from open items
    open_df = df[~df[status_col].isin(_DONE_STATUSES)].copy()
    assignee_backlog = open_df.groupby(assignee_col)[status_col].size().to_dict() if not open_df.empty else {}
    assignee_priority_backlog = (
        open_df.groupby([assignee_col, priority_col])[status_col].size().to_dict() if not open_df.empty else {}
    )

    def _build_history_maps(window_df: pd.DataFrame) -> dict:
        assignee_velocity_local = window_df.groupby(assignee_col)["cycle_days"].median().to_dict()
        priority_velocity_local = window_df.groupby(priority_col)["cycle_days"].median().to_dict()
        assignee_done_count_local = window_df.groupby(assignee_col)["on_time"].size().to_dict()

        (
            global_assignee_on_time_rate_local,
            assignee_on_time_rate_local,
            priority_on_time_rate_local,
        ) = _build_smoothed_on_time_rate_maps(
            window_df,
            on_time_col="on_time",
            assignee_col=assignee_col,
            priority_col=priority_col,
        )
        global_priority_on_time_rate_local = global_assignee_on_time_rate_local

        (
            global_schedule_adherence_local,
            assignee_schedule_adherence_local,
            priority_schedule_adherence_local,
        ) = _build_smoothed_on_time_rate_maps(
            window_df,
            on_time_col="schedule_adherence",
            assignee_col=assignee_col,
            priority_col=priority_col,
        )

        global_assignee_velocity_local = float(window_df["cycle_days"].median())
        global_priority_velocity_local = float(window_df["cycle_days"].median())

        return {
            "assignee_velocity": assignee_velocity_local,
            "priority_velocity": priority_velocity_local,
            "assignee_done_count": assignee_done_count_local,
            "assignee_on_time_rate": assignee_on_time_rate_local,
            "priority_on_time_rate": priority_on_time_rate_local,
            "assignee_schedule_adherence": assignee_schedule_adherence_local,
            "priority_schedule_adherence": priority_schedule_adherence_local,
            "global_assignee_velocity": global_assignee_velocity_local,
            "global_priority_velocity": global_priority_velocity_local,
            "global_assignee_on_time_rate": global_assignee_on_time_rate_local,
            "global_priority_on_time_rate": global_priority_on_time_rate_local,
            "global_schedule_adherence": global_schedule_adherence_local,
        }

    # Time-based holdout: keep the most recent tickets as validation set.
    sorted_df = train_df.sort_values(by="_effective_done_ts").reset_index(drop=True)
    holdout_size = min(max(int(len(sorted_df) * 0.2), 1), max(len(sorted_df) - 10, 1))
    fit_window = sorted_df.iloc[:-holdout_size].copy()
    holdout_window = sorted_df.iloc[-holdout_size:].copy()
    use_holdout = bool(
        len(fit_window) >= 10
        and not holdout_window.empty
        and fit_window["on_time"].nunique() >= 2
        and holdout_window["on_time"].nunique() >= 2
    )

    eval_window = fit_window if use_holdout else sorted_df
    eval_holdout = holdout_window if use_holdout else pd.DataFrame()

    eval_maps = _build_history_maps(eval_window)
    fit_features = _build_feature_frame(
        eval_window,
        priority_col=priority_col,
        assignee_col=assignee_col,
        assignee_velocity=eval_maps["assignee_velocity"],
        priority_velocity=eval_maps["priority_velocity"],
        assignee_done_count=eval_maps["assignee_done_count"],
        assignee_on_time_rate=eval_maps["assignee_on_time_rate"],
        priority_on_time_rate=eval_maps["priority_on_time_rate"],
        assignee_backlog=assignee_backlog,
        assignee_priority_backlog=assignee_priority_backlog,
        assignee_schedule_adherence=eval_maps["assignee_schedule_adherence"],
        priority_schedule_adherence=eval_maps["priority_schedule_adherence"],
        global_assignee_velocity=eval_maps["global_assignee_velocity"],
        global_priority_velocity=eval_maps["global_priority_velocity"],
        global_assignee_on_time_rate=eval_maps["global_assignee_on_time_rate"],
        global_priority_on_time_rate=eval_maps["global_priority_on_time_rate"],
        global_schedule_adherence=eval_maps["global_schedule_adherence"],
    )
    fit_features = _apply_leave_one_out_history_features(
        fit_features,
        eval_window,
        assignee_col=assignee_col,
        priority_col=priority_col,
    )

    holdout_features = pd.DataFrame()
    if use_holdout:
        holdout_features = _build_feature_frame(
            eval_holdout,
            priority_col=priority_col,
            assignee_col=assignee_col,
            assignee_velocity=eval_maps["assignee_velocity"],
            priority_velocity=eval_maps["priority_velocity"],
            assignee_done_count=eval_maps["assignee_done_count"],
            assignee_on_time_rate=eval_maps["assignee_on_time_rate"],
            priority_on_time_rate=eval_maps["priority_on_time_rate"],
            assignee_backlog=assignee_backlog,
            assignee_priority_backlog=assignee_priority_backlog,
            assignee_schedule_adherence=eval_maps["assignee_schedule_adherence"],
            priority_schedule_adherence=eval_maps["priority_schedule_adherence"],
            global_assignee_velocity=eval_maps["global_assignee_velocity"],
            global_priority_velocity=eval_maps["global_priority_velocity"],
            global_assignee_on_time_rate=eval_maps["global_assignee_on_time_rate"],
            global_priority_on_time_rate=eval_maps["global_priority_on_time_rate"],
            global_schedule_adherence=eval_maps["global_schedule_adherence"],
        )

    feature_cols = [
        priority_col,
        assignee_col,
        "budget_days",
        "assignee_velocity_90",
        "priority_velocity_90",
        "assignee_done_90",
        "assignee_on_time_rate_90",
        "priority_on_time_rate_90",
        "assignee_schedule_adherence_90",
        "priority_schedule_adherence_90",
        "assignee_backlog_open",
        "assignee_priority_backlog",
        "velocity_gap_assignee",
        "velocity_gap_priority",
        "velocity_ratio_assignee",
        "velocity_ratio_priority",
    ]

    X_fit = fit_features[feature_cols].copy()
    y_fit = eval_window["on_time"].astype(int)
    X_holdout = holdout_features[feature_cols].copy() if use_holdout else pd.DataFrame()
    y_holdout = eval_holdout["on_time"].astype(int) if use_holdout else pd.Series(dtype=int)

    preprocess = ColumnTransformer(
        transformers=[
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore"),
                [priority_col, assignee_col],
            ),
            (
                "num",
                "passthrough",
                [
                    "budget_days",
                    "assignee_velocity_90",
                    "priority_velocity_90",
                    "assignee_done_90",
                    "assignee_on_time_rate_90",
                    "priority_on_time_rate_90",
                    "assignee_schedule_adherence_90",
                    "priority_schedule_adherence_90",
                    "assignee_backlog_open",
                    "assignee_priority_backlog",
                    "velocity_gap_assignee",
                    "velocity_gap_priority",
                    "velocity_ratio_assignee",
                    "velocity_ratio_priority",
                ],
            ),
        ]
    )

    candidate_models = [
        (
            "Random Forest",
            RandomForestClassifier(
                n_estimators=1500,
                max_depth=None,
                min_samples_leaf=1,
                min_samples_split=2,
                class_weight="balanced_subsample",
                random_state=42,
                n_jobs=-1,
            ),
        ),
        (
            "Extra Trees",
            ExtraTreesClassifier(
                n_estimators=1500,
                max_depth=None,
                min_samples_leaf=1,
                min_samples_split=2,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1,
            ),
        ),
    ]

    best_model = None
    best_model_name = None
    best_eval_accuracy = -1.0

    for model_name, estimator in candidate_models:
        pipeline = Pipeline(
            steps=[
                ("prep", preprocess),
                ("clf", clone(estimator)),
            ]
        )
        pipeline.fit(X_fit, y_fit)
        eval_accuracy = (
            float(accuracy_score(y_holdout, pipeline.predict(X_holdout)))
            if use_holdout
            else float(accuracy_score(y_fit, pipeline.predict(X_fit)))
        )
        if eval_accuracy > best_eval_accuracy:
            best_eval_accuracy = eval_accuracy
            best_model = pipeline
            best_model_name = model_name

    # Refit selected model on all available training rows using leave-one-out historical features.
    full_maps = _build_history_maps(train_df)
    full_features = _build_feature_frame(
        train_df,
        priority_col=priority_col,
        assignee_col=assignee_col,
        assignee_velocity=full_maps["assignee_velocity"],
        priority_velocity=full_maps["priority_velocity"],
        assignee_done_count=full_maps["assignee_done_count"],
        assignee_on_time_rate=full_maps["assignee_on_time_rate"],
        priority_on_time_rate=full_maps["priority_on_time_rate"],
        assignee_backlog=assignee_backlog,
        assignee_priority_backlog=assignee_priority_backlog,
        assignee_schedule_adherence=full_maps["assignee_schedule_adherence"],
        priority_schedule_adherence=full_maps["priority_schedule_adherence"],
        global_assignee_velocity=full_maps["global_assignee_velocity"],
        global_priority_velocity=full_maps["global_priority_velocity"],
        global_assignee_on_time_rate=full_maps["global_assignee_on_time_rate"],
        global_priority_on_time_rate=full_maps["global_priority_on_time_rate"],
        global_schedule_adherence=full_maps["global_schedule_adherence"],
    )
    full_features = _apply_leave_one_out_history_features(
        full_features,
        train_df,
        assignee_col=assignee_col,
        priority_col=priority_col,
    )
    X_full = full_features[feature_cols].copy()
    y_full = train_df["on_time"].astype(int)

    estimator_lookup = {name: est for name, est in candidate_models}
    final_model = Pipeline(
        steps=[
            ("prep", preprocess),
            ("clf", clone(estimator_lookup[best_model_name])),
        ]
    )
    final_model.fit(X_full, y_full)
    deployed_model = final_model
    probability_calibrated = False
    calibration_method = None
    # Calibrate probability outputs so displayed completion probabilities are better aligned with observed outcomes.
    if len(X_full) >= 30 and y_full.nunique() >= 2:
        try:
            calibrated_model = CalibratedClassifierCV(
                estimator=final_model,
                method="sigmoid",
                cv=3,
            )
            calibrated_model.fit(X_full, y_full)
            deployed_model = calibrated_model
            probability_calibrated = True
            calibration_method = "sigmoid"
        except Exception:
            # Fall back to the uncalibrated model if calibration cannot be fit.
            deployed_model = final_model

    train_accuracy = float(accuracy_score(y_full, deployed_model.predict(X_full)))

    payload["training_rows"] = int(len(train_df))
    payload["training_accuracy"] = train_accuracy
    payload["validation_rows"] = int(len(eval_holdout)) if use_holdout else 0
    payload["validation_accuracy"] = best_eval_accuracy if use_holdout else None
    payload["accuracy_target_met"] = bool((best_eval_accuracy if use_holdout else train_accuracy) >= 0.90)
    payload["model_name"] = best_model_name
    payload["probability_calibrated"] = probability_calibrated
    payload["calibration_method"] = calibration_method
    payload["on_time_rate"] = float(train_df["on_time"].mean())
    payload["average_validation_days"] = average_validation_days

    payload["priority_options"] = sorted(df[priority_col].dropna().astype(str).unique().tolist())
    present_assignees = set(df[assignee_col].dropna().astype(str).unique().tolist())
    payload["assignee_options"] = [member for member in PE_TEAM_MEMBERS if member in present_assignees]

    payload["model_bundle"] = {
        "model": deployed_model,
        "priority_col": priority_col,
        "assignee_col": assignee_col,
        "average_validation_days": average_validation_days,
        "assignee_validation_days": assignee_validation_days,
        "priority_validation_days": priority_validation_days,
        "assignee_velocity": full_maps["assignee_velocity"],
        "priority_velocity": full_maps["priority_velocity"],
        "assignee_done_count": full_maps["assignee_done_count"],
        "assignee_on_time_rate": full_maps["assignee_on_time_rate"],
        "priority_on_time_rate": full_maps["priority_on_time_rate"],
        "assignee_schedule_adherence": full_maps["assignee_schedule_adherence"],
        "priority_schedule_adherence": full_maps["priority_schedule_adherence"],
        "assignee_backlog": assignee_backlog,
        "assignee_priority_backlog": assignee_priority_backlog,
        "global_assignee_velocity": full_maps["global_assignee_velocity"],
        "global_priority_velocity": full_maps["global_priority_velocity"],
        "global_assignee_on_time_rate": full_maps["global_assignee_on_time_rate"],
        "global_priority_on_time_rate": full_maps["global_priority_on_time_rate"],
        "global_schedule_adherence": full_maps["global_schedule_adherence"],
    }

    return payload


def predict_completion_probability(
    model_bundle: dict,
    priority_value: str,
    assignee_value: str,
    expected_completion_date: date,
) -> dict:
    today = pd.Timestamp.today().normalize()
    target_date = pd.Timestamp(expected_completion_date)
    raw_budget_days = int((target_date - today).days)

    # Resolve validation time specific to the selected assignee → priority → global fallback.
    # This mirrors how on_time was labeled during training for each group.
    global_validation_days = float(model_bundle.get("average_validation_days", 0.0))
    av_days = model_bundle.get("assignee_validation_days", {})
    pv_days = model_bundle.get("priority_validation_days", {})
    effective_validation_days = av_days.get(
        assignee_value,
        pv_days.get(priority_value, global_validation_days),
    )
    budget_days = int(raw_budget_days - effective_validation_days)

    av = model_bundle["assignee_velocity"]
    pv = model_bundle["priority_velocity"]
    dc = model_bundle["assignee_done_count"]
    ator = model_bundle["assignee_on_time_rate"]
    ptor = model_bundle["priority_on_time_rate"]
    asar = model_bundle.get("assignee_schedule_adherence", {})
    psar = model_bundle.get("priority_schedule_adherence", {})
    ab = model_bundle["assignee_backlog"]
    apb = model_bundle["assignee_priority_backlog"]

    assignee_velocity = float(av.get(assignee_value, model_bundle["global_assignee_velocity"]))
    priority_velocity = float(pv.get(priority_value, model_bundle["global_priority_velocity"]))
    assignee_done = float(dc.get(assignee_value, 1))
    assignee_on_time_rate = float(ator.get(assignee_value, model_bundle["global_assignee_on_time_rate"]))
    priority_on_time_rate = float(ptor.get(priority_value, model_bundle["global_priority_on_time_rate"]))
    assignee_schedule_adherence = float(asar.get(assignee_value, model_bundle.get("global_schedule_adherence", 0.5)))
    priority_schedule_adherence = float(psar.get(priority_value, model_bundle.get("global_schedule_adherence", 0.5)))
    assignee_backlog = float(ab.get(assignee_value, 0))
    assignee_priority_backlog = float(apb.get((assignee_value, priority_value), 0))
    velocity_gap_assignee = float(budget_days - assignee_velocity)
    velocity_gap_priority = float(budget_days - priority_velocity)
    velocity_ratio_assignee = float(budget_days / max(assignee_velocity, 1.0))
    velocity_ratio_priority = float(budget_days / max(priority_velocity, 1.0))
    priority_col = model_bundle["priority_col"]
    assignee_col = model_bundle["assignee_col"]

    row = pd.DataFrame(
        [
            {
                priority_col: priority_value,
                assignee_col: assignee_value,
                "budget_days": budget_days,
                "assignee_velocity_90": assignee_velocity,
                "priority_velocity_90": priority_velocity,
                "assignee_done_90": assignee_done,
                "assignee_on_time_rate_90": assignee_on_time_rate,
                "priority_on_time_rate_90": priority_on_time_rate,
                "assignee_schedule_adherence_90": assignee_schedule_adherence,
                "priority_schedule_adherence_90": priority_schedule_adherence,
                "assignee_backlog_open": assignee_backlog,
                "assignee_priority_backlog": assignee_priority_backlog,
                "velocity_gap_assignee": velocity_gap_assignee,
                "velocity_gap_priority": velocity_gap_priority,
                "velocity_ratio_assignee": velocity_ratio_assignee,
                "velocity_ratio_priority": velocity_ratio_priority,
            }
        ]
    )

    p_on_time = float(model_bundle["model"].predict_proba(row)[0, 1])
    if p_on_time >= 0.75:
        risk_band = "High confidence"
        risk_color = "#16a34a"
    elif p_on_time >= 0.5:
        risk_band = "Moderate confidence"
        risk_color = "#f59e0b"
    else:
        risk_band = "Low confidence"
        risk_color = "#dc2626"

    gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=round(p_on_time * 100, 1),
            number={"suffix": "%"},
            title={"text": "Probability of Completion On Time"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": risk_color},
                "steps": [
                    {"range": [0, 50], "color": "#fee2e2"},
                    {"range": [50, 75], "color": "#fef3c7"},
                    {"range": [75, 100], "color": "#dcfce7"},
                ],
            },
        )
    )
    gauge.update_layout(height=280, margin=dict(l=20, r=20, t=40, b=10))

    return {
        "probability": p_on_time,
        "risk_band": risk_band,
        "budget_days": raw_budget_days,
        "assignee_velocity_90": assignee_velocity,
        "priority_velocity_90": priority_velocity,
        "assignee_done_90": assignee_done,
        "assignee_on_time_rate_90": assignee_on_time_rate,
        "priority_on_time_rate_90": priority_on_time_rate,
        "assignee_schedule_adherence_90": assignee_schedule_adherence,
        "priority_schedule_adherence_90": priority_schedule_adherence,
        "assignee_backlog_open": assignee_backlog,
        "assignee_priority_backlog": assignee_priority_backlog,
        "velocity_gap_assignee": velocity_gap_assignee,
        "velocity_gap_priority": velocity_gap_priority,
        "velocity_ratio_assignee": velocity_ratio_assignee,
        "velocity_ratio_priority": velocity_ratio_priority,
        "gauge_fig": gauge,
    }


def build_probability_curve(
    model_bundle: dict,
    priority_value: str,
    assignee_value: str,
    start_date: date,
    horizon_days: int = 120,
) -> go.Figure:
    start_ts = pd.Timestamp(start_date)
    days = np.arange(0, horizon_days + 1, 7)

    probs = []
    x_dates = []
    for d in days:
        target = (start_ts + pd.Timedelta(days=int(d))).date()
        pred = predict_completion_probability(model_bundle, priority_value, assignee_value, target)
        probs.append(pred["probability"] * 100)
        x_dates.append(target)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_dates,
            y=probs,
            mode="lines+markers",
            line=dict(color="#0ea5e9", width=3),
            marker=dict(size=6),
            name="On-time probability",
        )
    )
    fig.update_layout(
        title="Probability Curve by Target Date",
        xaxis_title="Target completion date",
        yaxis_title="Probability (%)",
        yaxis=dict(range=[0, 100]),
        height=330,
        hovermode="x unified",
    )
    return fig
