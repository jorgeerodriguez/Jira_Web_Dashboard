import pandas as pd
import plotly.express as px


TIME_PERIOD_DAYS = 90
JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"
EXCLUDED_ASSIGNEES = {
    "satish kumar marana",
    "denys loboda",
    "cullen philippson",
    "emmanuel adjei",
}


def _empty_payload() -> dict:
    return {
        "total_in_progress": 0,
        "total_estimated_days": 0.0,
        "avg_velocity": 0.0,
        "critical_assignees": 0,
        "missing_target_end_dates": 0,
        "load_fig": None,
        "scatter_fig": None,
        "distribution_fig": None,
        "target_timeline_fig": None,
        "detail_df": pd.DataFrame(),
        "tickets_df": pd.DataFrame(),
    }


def _harmonic_estimate(avg_days: float, tickets: int) -> float:
    try:
        avg = float(avg_days)
    except (TypeError, ValueError):
        return 0.0

    try:
        total_tickets = int(tickets)
    except (TypeError, ValueError):
        return 0.0

    if total_tickets <= 0 or avg <= 0:
        return 0.0

    tot = 0.0
    for x in range(1, total_tickets + 1):
        tot += avg / x
    return tot


def _normalize_assignee(series: pd.Series) -> pd.Series:
    return series.fillna("Unassigned").astype(str).str.strip()


def _safe_weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce")
    weights = pd.to_numeric(weights, errors="coerce")
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return 0.0

    values = values[mask]
    weights = weights[mask]
    weight_sum = float(weights.sum())
    if weight_sum <= 0:
        return 0.0
    return float((values * weights).sum() / weight_sum)


def _project_execution_days(group: pd.DataFrame) -> pd.Series:
    values = pd.to_numeric(group["execution_days"], errors="coerce").dropna()
    if values.empty:
        return pd.Series(
            {
                "execution_velocity_value": 0.0,
                "execution_velocity_weighted": 0.0,
                "execution_velocity_median": 0.0,
                "done_ticket_count_90d": 0,
            }
        )

    recency_days = pd.to_numeric(group.loc[values.index, "recency_days"], errors="coerce").fillna(0)
    weights = 1 / (1 + (recency_days / 30.0))
    weighted_mean = _safe_weighted_mean(values, weights)
    median_value = float(values.median())

    return pd.Series(
        {
            "execution_velocity_value": float(max(median_value, 0.0)),
            "execution_velocity_weighted": float(max(weighted_mean, 0.0)),
            "execution_velocity_median": float(max(median_value, 0.0)),
            "done_ticket_count_90d": int(len(values)),
        }
    )


def _get_done_window(df: pd.DataFrame) -> pd.DataFrame:
    done_df = df[df["status"].astype(str).str.lower().eq("done")].copy()
    if done_df.empty:
        return done_df

    for date_col in ["resolved_date", "completed_date", "done_date", "updated"]:
        if date_col in done_df.columns:
            done_df[date_col] = pd.to_datetime(done_df[date_col], errors="coerce")
            date_tz = done_df[date_col].dt.tz
            if date_tz is not None:
                cutoff = pd.Timestamp.now(tz=date_tz).normalize() - pd.Timedelta(days=TIME_PERIOD_DAYS)
            else:
                cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=TIME_PERIOD_DAYS)
            done_df = done_df[done_df[date_col] >= cutoff].copy()
            break
    return done_df


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def build_in_progress_visuals(df_issues: pd.DataFrame) -> dict:
    """Leadership view of in-progress workload based on backlog/execution velocity."""
    if df_issues is None or df_issues.empty:
        return _empty_payload()

    required = {"status", "assignee_name", "velocity_days", "velocity_backlog_days", "issuetype"}
    if not required.issubset(df_issues.columns):
        return _empty_payload()

    df = df_issues.copy()
    df["assignee_name"] = _normalize_assignee(df["assignee_name"])
    excluded_lower = {x.lower() for x in EXCLUDED_ASSIGNEES}

    in_progress_df = df[df["status"].astype(str).str.lower().eq("in progress") & df['issuetype'].astype(str).str.lower().ne('feature')].copy()
    in_progress_df = in_progress_df[~in_progress_df["assignee_name"].str.lower().isin(excluded_lower)].copy()

    key_col = _first_existing_column(in_progress_df, ["key", "Key", "ticket", "Ticket"])
    priority_col_src = _first_existing_column(in_progress_df, ["priority_name", "priority", "Priority"])
    lead_col = _first_existing_column(in_progress_df, ["bussiness_lead", "business_lead", "Business Lead"])
    creator_col = _first_existing_column(in_progress_df, ["creator_name", "creator", "Creator"])
    assignee_col = _first_existing_column(in_progress_df, ["assignee_name", "Assignee"])
    updated_col = _first_existing_column(in_progress_df, ["updated", "Updated", "last_updated"])
    target_end_col = _first_existing_column(in_progress_df, ["target_end_date", "project_due_date", "Target End Date"])
    days_old_col = _first_existing_column(in_progress_df, ["days_old", "Days Old"])
    summary_col = _first_existing_column(in_progress_df, ["summary", "Summary"])

    if key_col is None:
        in_progress_df["key"] = in_progress_df.index.astype(str)
        key_col = "key"
    if priority_col_src is None:
        in_progress_df["priority_name"] = "Unknown"
        priority_col_src = "priority_name"
    if lead_col is None:
        in_progress_df["bussiness_lead"] = "Unknown"
        lead_col = "bussiness_lead"
    if creator_col is None:
        in_progress_df["creator_name"] = "Unknown"
        creator_col = "creator_name"
    if assignee_col is None:
        in_progress_df["assignee_name"] = "Unassigned"
        assignee_col = "assignee_name"
    if days_old_col is None:
        in_progress_df["days_old"] = 0
        days_old_col = "days_old"
    if summary_col is None:
        in_progress_df["summary"] = ""
        summary_col = "summary"

    today = pd.Timestamp.now(tz="UTC").normalize()
    today_date_only = today.date()
    if target_end_col is not None:
        in_progress_df[target_end_col] = pd.to_datetime(in_progress_df[target_end_col], errors="coerce").dt.date
        in_progress_df["days_left"] = in_progress_df[target_end_col].apply(
            lambda d: (d - today_date_only).days if pd.notnull(d) else None
        )
    else:
        in_progress_df["days_left"] = None

    if updated_col is not None:
        in_progress_df[updated_col] = pd.to_datetime(in_progress_df[updated_col], errors="coerce").dt.date
    else:
        in_progress_df["updated"] = pd.NaT
        updated_col = "updated"

    in_progress_df[days_old_col] = pd.to_numeric(in_progress_df[days_old_col], errors="coerce").fillna(0)

    target_end_select_col = target_end_col
    if target_end_select_col is None:
        target_end_select_col = "__target_end_date__"
        in_progress_df[target_end_select_col] = pd.NaT

    tickets_df = in_progress_df[
        [
            key_col,
            priority_col_src,
            lead_col,
            creator_col,
            assignee_col,
            updated_col,
            target_end_select_col,
            days_old_col,
            "days_left",
            summary_col,
        ]
    ].copy()

    tickets_df.columns = [
        "Ticket",
        "Priority",
        "Business Lead",
        "Creator",
        "Assognee Name",
        "Last Updated",
        "Target End Date",
        "Days Old",
        "Days Left",
        "Summary",
    ]
    tickets_df["Ticket"] = tickets_df["Ticket"].astype(str).apply(
        lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}"
    )
    tickets_df["Summary"] = tickets_df["Summary"].fillna("").astype(str).str[:160]
    tickets_df = tickets_df.sort_values("Days Old", ascending=False)

    backlog_counts = (
        in_progress_df.groupby("assignee_name", as_index=False)
        .size()
        .rename(columns={"size": "total_tickets_in_progress"})
    )

    if backlog_counts.empty:
        return _empty_payload()

    done_df = _get_done_window(df)
    done_df = done_df[~done_df["assignee_name"].str.lower().isin(excluded_lower)].copy()

    expected_start_col = _first_existing_column(
        done_df,
        [
            "planned_start_date",
            "target_start_date",
            "expected_start_date",
            "expected_start",
            "Expected Start Date",
            "project_start_date",
            "start_date",
            "created",
        ],
    )
    done_updated_col = _first_existing_column(done_df, ["updated", "Updated", "last_updated"])

    if expected_start_col is not None and done_updated_col is not None:
        start_dt = pd.to_datetime(done_df[expected_start_col], errors="coerce", utc=True)
        end_dt = pd.to_datetime(done_df[done_updated_col], errors="coerce", utc=True)
        done_df["execution_days"] = (end_dt - start_dt).dt.total_seconds() / 86400.0
        done_df["execution_days"] = pd.to_numeric(done_df["execution_days"], errors="coerce")
        done_df = done_df[done_df["execution_days"] >= 0].copy()
    else:
        # Fallback for datasets that don't expose expected-start/updated fields.
        done_df["execution_days"] = pd.to_numeric(done_df["velocity_days"], errors="coerce")

    done_df = done_df[done_df["execution_days"] > 0].copy()
    done_df["recency_days"] = (
        pd.Timestamp.now(tz="UTC") - pd.to_datetime(done_df[done_updated_col], errors="coerce", utc=True)
    ).dt.total_seconds() / 86400.0
    done_df["recency_days"] = pd.to_numeric(done_df["recency_days"], errors="coerce").fillna(0).clip(lower=0)

    if done_df.empty:
        global_exec_avg = 0.0
        execution_avg_df = pd.DataFrame(columns=[
            "assignee_name",
            "execution_velocity_value",
            "execution_velocity_weighted",
            "execution_velocity_median",
            "done_ticket_count_90d",
        ])
    else:
        global_stats = _project_execution_days(done_df)
        global_exec_avg = float(global_stats["execution_velocity_value"])
        execution_avg_df = done_df.groupby("assignee_name").apply(_project_execution_days).reset_index()

    velocity_comparison_df = backlog_counts.merge(execution_avg_df, on="assignee_name", how="left")
    velocity_comparison_df = velocity_comparison_df.rename(
        columns={"total_tickets_in_progress": "total_tickets_in_backlog"}
    )
    velocity_comparison_df["execution_velocity_value"] = velocity_comparison_df[
        "execution_velocity_value"
    ].fillna(global_exec_avg)
    velocity_comparison_df["execution_velocity_weighted"] = velocity_comparison_df[
        "execution_velocity_weighted"
    ].fillna(global_exec_avg)
    velocity_comparison_df["execution_velocity_median"] = velocity_comparison_df[
        "execution_velocity_median"
    ].fillna(global_exec_avg)
    velocity_comparison_df["done_ticket_count_90d"] = (
        velocity_comparison_df["done_ticket_count_90d"].fillna(0).astype(int)
    )
    velocity_comparison_df["complexity_days"] = velocity_comparison_df["execution_velocity_value"]

    # Predicted completion days = in-progress count * individual median execution days from Done tickets.
    velocity_comparison_df["estimated_total_velocity_execution_days"] = (
        velocity_comparison_df["total_tickets_in_backlog"] * velocity_comparison_df["execution_velocity_value"]
    )

    velocity_comparison_df["average_total_velocity_backlog_days"] = velocity_comparison_df[
        "estimated_total_velocity_execution_days"
    ]

    summary = velocity_comparison_df.rename(columns={"total_tickets_in_backlog": "total_tickets_in_progress"})

    def load_bucket(days: float) -> str:
        if days > 90:
            return "Critical"
        if days > 60:
            return "High"
        if days > 30:
            return "Medium"
        if days > 0:
            return "Low"
        return "None"

    summary["load_bucket"] = summary["average_total_velocity_backlog_days"].apply(load_bucket)
    summary["estimated_total_days_detail"] = summary["average_total_velocity_backlog_days"]
    summary = summary.sort_values("average_total_velocity_backlog_days", ascending=False)

    load_fig = px.bar(
        summary,
        y="assignee_name",
        x="average_total_velocity_backlog_days",
        color="load_bucket",
        orientation="h",
        title="Estimated Completion Days by Assignee",
        hover_data=[
            "total_tickets_in_progress",
            "execution_velocity_value",
            "execution_velocity_weighted",
            "execution_velocity_median",
            "done_ticket_count_90d",
            "average_total_velocity_backlog_days",
        ],
        color_discrete_map={
            "Critical": "#dc2626",
            "High": "#f97316",
            "Medium": "#facc15",
            "Low": "#16a34a",
            "None": "#94a3b8",
        },
    )
    load_fig.update_layout(height=460, xaxis_title="Estimated Total Days", yaxis_title="Assignee")

    scatter_fig = px.scatter(
        summary,
        x="total_tickets_in_progress",
        y="average_total_velocity_backlog_days",
        size="average_total_velocity_backlog_days",
        color="average_total_velocity_backlog_days",
        color_continuous_scale="RdYlGn_r",
        hover_name="assignee_name",
        hover_data=[
            "execution_velocity_value",
            "execution_velocity_weighted",
            "execution_velocity_median",
            "done_ticket_count_90d",
            "average_total_velocity_backlog_days",
        ],
        title="In Progress Size vs Estimated Completion Days",
    )
    scatter_fig.update_layout(height=380, xaxis_title="In Progress Tickets", yaxis_title="Estimated Total Days")

    dist = (
        summary["load_bucket"]
        .value_counts()
        .reindex(["None", "Low", "Medium", "High", "Critical"], fill_value=0)
        .reset_index()
    )
    dist.columns = ["Load", "Count"]
    distribution_fig = px.pie(
        dist,
        names="Load",
        values="Count",
        title="Workload Distribution by Load",
        color="Load",
        color_discrete_map={
            "Critical": "#dc2626",
            "High": "#f97316",
            "Medium": "#facc15",
            "Low": "#16a34a",
            "None": "#94a3b8",
        },
    )
    distribution_fig.update_layout(height=380)

    target_timeline_df = in_progress_df[[target_end_select_col, key_col]].copy()
    target_timeline_df["target_end_dt"] = pd.to_datetime(target_timeline_df[target_end_select_col], errors="coerce")

    missing_target_end_dates = int(target_timeline_df["target_end_dt"].isna().sum())
    target_timeline_fig = None

    valid_timeline_df = target_timeline_df[target_timeline_df["target_end_dt"].notna()].copy()
    if not valid_timeline_df.empty:
        valid_timeline_df["days_to_target"] = (
            valid_timeline_df["target_end_dt"].dt.date.apply(lambda d: (d - today_date_only).days)
        )

        def due_bucket(days_to_target: int) -> str:
            if days_to_target < 0:
                return "Overdue"
            if days_to_target <= 14:
                return "Due in 14 Days"
            if days_to_target <= 30:
                return "Due in 30 Days"
            return "Due Later"

        valid_timeline_df["Due Bucket"] = valid_timeline_df["days_to_target"].apply(due_bucket)

        target_timeline_fig = px.histogram(
            valid_timeline_df,
            x="target_end_dt",
            color="Due Bucket",
            nbins=max(8, min(36, int(valid_timeline_df["target_end_dt"].nunique()))),
            title="In Progress Ticket Distribution Over Time (Target End Date)",
            labels={"target_end_dt": "Target End Date", "count": "Tickets"},
            color_discrete_map={
                "Overdue": "#dc2626",
                "Due in 14 Days": "#f97316",
                "Due in 30 Days": "#facc15",
                "Due Later": "#16a34a",
            },
        )
        today_x = pd.Timestamp(today_date_only).strftime("%Y-%m-%d")
        target_timeline_fig.add_vline(
            x=today_x,
            line_width=2,
            line_dash="dash",
            line_color="#334155",
        )
        target_timeline_fig.add_annotation(
            x=today_x,
            y=1,
            xref="x",
            yref="paper",
            text="Today",
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font={"color": "#334155"},
        )
        target_timeline_fig.update_layout(
            barmode="stack",
            height=420,
            xaxis_title="Target End Date",
            yaxis_title="Ticket Count",
            legend_title="Due Bucket",
        )
        target_timeline_fig.update_xaxes(rangeslider_visible=True)

    detail_df = summary[
        [
            "assignee_name",
            "total_tickets_in_progress",
            "execution_velocity_weighted",
            "execution_velocity_median",
            "execution_velocity_value",
            "average_total_velocity_backlog_days",
            "load_bucket",
        ]
    ].copy()
    detail_df.columns = [
        "Assignee",
        "In Progress",
        "Weighted Avg Execution Days",
        "Median Execution Days",
        "Avg Execution Days (90d)",
        "Estimated Total Days",
        "Load",
    ]

    return {
        "total_in_progress": int(summary["total_tickets_in_progress"].sum()),
        "total_estimated_days": float(summary["average_total_velocity_backlog_days"].sum()),
        "avg_velocity": float(summary["execution_velocity_value"].mean()),
        "critical_assignees": int((summary["average_total_velocity_backlog_days"] > 90).sum()),
        "missing_target_end_dates": missing_target_end_dates,
        "load_fig": load_fig,
        "scatter_fig": scatter_fig,
        "distribution_fig": distribution_fig,
        "target_timeline_fig": target_timeline_fig,
        "detail_df": detail_df,
        "tickets_df": tickets_df,
    }
