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
        "load_fig": None,
        "scatter_fig": None,
        "distribution_fig": None,
        "detail_df": pd.DataFrame(),
        "tickets_df": pd.DataFrame(),
    }


def _harmonic_estimate(avg_days: float, tickets: int) -> float:
    if tickets <= 0 or avg_days <= 0:
        return 0.0
    return sum(avg_days / i for i in range(1, tickets + 1))


def _normalize_assignee(series: pd.Series) -> pd.Series:
    return series.fillna("Unassigned").astype(str).str.strip()


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
            done_df = done_df[done_df[date_col].isna() | (done_df[date_col] >= cutoff)].copy()
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

    required = {"status", "assignee_name", "velocity_days", "velocity_backlog_days"}
    if not required.issubset(df_issues.columns):
        return _empty_payload()

    df = df_issues.copy()
    df["assignee_name"] = _normalize_assignee(df["assignee_name"])
    excluded_lower = {x.lower() for x in EXCLUDED_ASSIGNEES}

    in_progress_df = df[df["status"].astype(str).str.lower().eq("in progress")].copy()
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
    done_df["velocity_days"] = pd.to_numeric(done_df["velocity_days"], errors="coerce")
    done_df["velocity_backlog_days"] = pd.to_numeric(done_df["velocity_backlog_days"], errors="coerce")

    priority_col = "priority"
    if priority_col not in done_df.columns:
        done_df[priority_col] = "Unknown"
    done_df[priority_col] = done_df[priority_col].fillna("Unknown").astype(str)

    backlog_type_df = done_df[done_df["velocity_backlog_days"] > 0][
        ["assignee_name", priority_col, "velocity_backlog_days"]
    ].copy()
    backlog_type_df["velocity_type"] = "* Backlog Velocity"
    backlog_type_df.rename(columns={"velocity_backlog_days": "velocity_value"}, inplace=True)

    execution_type_df = done_df[done_df["velocity_days"] > 0][
        ["assignee_name", priority_col, "velocity_days"]
    ].copy()
    execution_type_df["velocity_type"] = "Execution Velocity"
    execution_type_df.rename(columns={"velocity_days": "velocity_value"}, inplace=True)

    combined_velocity = pd.concat([backlog_type_df, execution_type_df], ignore_index=True)
    combined_velocity = combined_velocity[
        ~combined_velocity["assignee_name"].str.lower().isin(excluded_lower)
    ].copy()

    if combined_velocity.empty:
        global_exec_avg = 0.0
        global_backlog_avg = 0.0
        velocity_comparison_df = pd.DataFrame(columns=[
            "assignee_name",
            "backlog_velocity_value",
            "execution_velocity_value",
            "complexity_days",
        ])
    else:
        assignees = sorted(combined_velocity["assignee_name"].dropna().unique().tolist())
        velocity_types = ["* Backlog Velocity", "Execution Velocity"]
        priorities = sorted(combined_velocity[priority_col].dropna().unique().tolist())

        full_index = pd.MultiIndex.from_product(
            [assignees, velocity_types, priorities],
            names=["assignee_name", "velocity_type", priority_col],
        )

        avg_by_combo = (
            combined_velocity.groupby(["assignee_name", "velocity_type", priority_col], as_index=False)[
                "velocity_value"
            ].mean()
        )
        avg_by_combo = avg_by_combo.set_index(["assignee_name", "velocity_type", priority_col]).reindex(full_index).reset_index()

        overall_avg = float(combined_velocity["velocity_value"].mean()) if not combined_velocity.empty else 0.0
        avg_by_combo["velocity_value"] = avg_by_combo["velocity_value"].fillna(overall_avg)

        summary_by_assignee = (
            avg_by_combo.groupby(["assignee_name", "velocity_type"], as_index=False)["velocity_value"]
            .mean()
        )

        backlog_df = summary_by_assignee[
            summary_by_assignee["velocity_type"] == "* Backlog Velocity"
        ][["assignee_name", "velocity_value"]].rename(columns={"velocity_value": "backlog_velocity_value"})

        execution_df = summary_by_assignee[
            summary_by_assignee["velocity_type"] == "Execution Velocity"
        ][["assignee_name", "velocity_value"]].rename(columns={"velocity_value": "execution_velocity_value"})

        velocity_comparison_df = pd.merge(backlog_df, execution_df, on="assignee_name", how="outer")
        velocity_comparison_df["complexity_days"] = (
            velocity_comparison_df["execution_velocity_value"] - velocity_comparison_df["backlog_velocity_value"]
        )

        global_exec_avg = float(velocity_comparison_df["execution_velocity_value"].mean())
        global_backlog_avg = float(velocity_comparison_df["backlog_velocity_value"].mean())

    velocity_comparison_df = backlog_counts.merge(velocity_comparison_df, on="assignee_name", how="left")
    velocity_comparison_df = velocity_comparison_df.rename(
        columns={"total_tickets_in_progress": "total_tickets_in_backlog"}
    )
    velocity_comparison_df["execution_velocity_value"] = velocity_comparison_df[
        "execution_velocity_value"
    ].fillna(global_exec_avg)
    velocity_comparison_df["backlog_velocity_value"] = velocity_comparison_df[
        "backlog_velocity_value"
    ].fillna(global_backlog_avg)
    velocity_comparison_df["complexity_days"] = velocity_comparison_df["complexity_days"].fillna(
        velocity_comparison_df["execution_velocity_value"] - velocity_comparison_df["backlog_velocity_value"]
    )
    velocity_comparison_df["complexity_days"] = velocity_comparison_df["complexity_days"].abs()

    # Iterate through each row
    for idx, row in velocity_comparison_df.iterrows():
        if row["total_tickets_in_backlog"] > 0:
            TOT = 0
            for x in range(1, int(row["total_tickets_in_backlog"]) + 1):
                TOT = TOT + row["execution_velocity_value"] / x
            velocity_comparison_df.at[idx, "estimated_total_velocity_execution_days"] = TOT
        else:
            velocity_comparison_df.at[idx, "estimated_total_velocity_execution_days"] = 0

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

    summary["load_bucket"] = summary["complexity_days"].apply(load_bucket)
    summary["estimated_total_days_detail"] = (
        summary["total_tickets_in_progress"] * summary["complexity_days"]
    )
    summary = summary.sort_values("complexity_days", ascending=False)

    load_fig = px.bar(
        summary,
        y="assignee_name",
        x="complexity_days",
        color="load_bucket",
        orientation="h",
        title="Complexity Days by Assignee",
        hover_data=["total_tickets_in_progress", "execution_velocity_value", "average_total_velocity_backlog_days"],
        color_discrete_map={
            "Critical": "#dc2626",
            "High": "#f97316",
            "Medium": "#facc15",
            "Low": "#16a34a",
            "None": "#94a3b8",
        },
    )
    load_fig.update_layout(height=460, xaxis_title="Complexity Days", yaxis_title="Assignee")

    scatter_fig = px.scatter(
        summary,
        x="total_tickets_in_progress",
        y="complexity_days",
        size="average_total_velocity_backlog_days",
        color="average_total_velocity_backlog_days",
        color_continuous_scale="RdYlGn_r",
        hover_name="assignee_name",
        hover_data=["execution_velocity_value", "backlog_velocity_value", "average_total_velocity_backlog_days"],
        title="In Progress Size vs Complexity Days",
    )
    scatter_fig.update_layout(height=380, xaxis_title="In Progress Tickets", yaxis_title="Complexity Days")

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

    detail_df = summary[
        [
            "assignee_name",
            "total_tickets_in_progress",
            "complexity_days",
            "load_bucket",
        ]
    ].copy()
    detail_df.columns = [
        "Assignee",
        "In Progress",
        "Complexity Days",
        "Load",
    ]

    return {
        "total_in_progress": int(summary["total_tickets_in_progress"].sum()),
        "total_estimated_days": float(summary["average_total_velocity_backlog_days"].sum()),
        "avg_velocity": float(summary["complexity_days"].mean()),
        "critical_assignees": int((summary["average_total_velocity_backlog_days"] > 90).sum()),
        "load_fig": load_fig,
        "scatter_fig": scatter_fig,
        "distribution_fig": distribution_fig,
        "detail_df": detail_df,
        "tickets_df": tickets_df,
    }
