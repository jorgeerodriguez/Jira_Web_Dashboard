from datetime import timedelta

import pandas as pd
import plotly.express as px


PE_TEAM_MEMBERS = [
    "Adam Shero", "Bolanle", "Denys Loboda", "Denys Naumenko", "Jeff Stuewe", "Oleh Kuzo",
    "Omar Saunders", "Owen Tregoning", "Pavlo Myshok", "Randall Puterbaugh", "simon.davison",
    "Taras Protsiv", "Tom Terry", "Trevor Atchley", "vladyslav.zhyhulin", "Zack.Amadi", "Unassigned",
]

PRIORITY_ORDER = ["Urgent", "High", "Medium", "Low", "None"]


def _normalize_priority(series: pd.Series) -> pd.Series:
    norm = series.fillna("None").astype(str).str.strip().str.casefold()
    mapped = norm.map(
        {
            "urgent": "Urgent",
            "highest": "Urgent",
            "critical": "Urgent",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
            "none": "None",
            "no priority": "None",
            "": "None",
        }
    )
    return mapped.fillna("None")


def build_velocity_visuals(df_issues: pd.DataFrame, time_period_days: int = 90) -> dict:
    """Build Velocity menu visuals from Jira dataframe."""
    if df_issues is None or df_issues.empty:
        return {
            "ticket_count": 0,
            "avg_execution": 0.0,
            "avg_backlog": 0.0,
            "box_fig": None,
            "heat_exec_fig": None,
            "heat_backlog_fig": None,
            "compare_fig": None,
        }

    required = {"created", "updated", "status", "assignee_name", "priority_name", "velocity_days", "velocity_backlog_days"}
    if not required.issubset(df_issues.columns):
        return {
            "ticket_count": 0,
            "avg_execution": 0.0,
            "avg_backlog": 0.0,
            "box_fig": None,
            "heat_exec_fig": None,
            "heat_backlog_fig": None,
            "compare_fig": None,
        }

    work = df_issues.copy()
    work["created"] = pd.to_datetime(work["created"], utc=True, errors="coerce")
    work["updated"] = pd.to_datetime(work["updated"], utc=True, errors="coerce")
    comparison_date = pd.to_datetime("today", utc=True, errors="coerce") - timedelta(days=time_period_days)

    backlog_data = work[
        (work["velocity_backlog_days"] > 0)
        & (work["created"] >= comparison_date)
        & (work["status"] == "Done")
        & (work["assignee_name"].isin(PE_TEAM_MEMBERS))
    ].copy()
    backlog_data["velocity_type"] = "Backlog Velocity"
    backlog_data["velocity_value"] = pd.to_numeric(backlog_data["velocity_backlog_days"], errors="coerce").fillna(0)

    regular_data = work[
        (work["velocity_days"] > 0)
        & (work["created"] >= comparison_date)
        & (work["status"] == "Done")
        & (work["assignee_name"].isin(PE_TEAM_MEMBERS))
    ].copy()
    regular_data["velocity_type"] = "Execution Velocity"
    regular_data["velocity_value"] = (regular_data["updated"] - regular_data["created"]).dt.days.fillna(0)

    combined_data = pd.concat(
        [
            backlog_data[["assignee_name", "velocity_type", "velocity_value", "priority_name"]],
            regular_data[["assignee_name", "velocity_type", "velocity_value", "priority_name"]],
        ],
        ignore_index=True,
    )
    combined_data["priority_name"] = _normalize_priority(combined_data["priority_name"])
    combined_data = combined_data[combined_data["priority_name"].notna()].copy()

    if combined_data.empty:
        return {
            "ticket_count": 0,
            "avg_execution": 0.0,
            "avg_backlog": 0.0,
            "box_fig": None,
            "heat_exec_fig": None,
            "heat_backlog_fig": None,
            "compare_fig": None,
        }

    ticket_count = int(len(regular_data))
    avg_execution = float(regular_data["velocity_value"].mean()) if not regular_data.empty else 0.0
    avg_backlog = float(backlog_data["velocity_value"].mean()) if not backlog_data.empty else 0.0

    ticket_counts = regular_data["assignee_name"].value_counts().to_dict()
    combined_data["assignee_label"] = combined_data["assignee_name"].apply(lambda a: f"{a} ({ticket_counts.get(a, 0)})")

    box_fig = px.box(
        combined_data,
        x="assignee_label",
        y="velocity_value",
        color="velocity_type",
        points="outliers",
        title=f"SLA Velocity Comparison (last {time_period_days} days)",
    )
    box_fig.update_layout(height=420, xaxis_title="Assignee (Total Tickets)", yaxis_title="Velocity (Days)")
    box_fig.update_xaxes(tickangle=45)

    summary = (
        combined_data.groupby(["assignee_name", "velocity_type", "priority_name"], as_index=False)["velocity_value"]
        .mean()
        .rename(columns={"velocity_value": "avg_velocity"})
    )

    top_assignees = combined_data["assignee_name"].value_counts().head(25).index.tolist()
    summary_top = summary[summary["assignee_name"].isin(top_assignees)].copy()

    assignee_order = sorted(summary_top["assignee_name"].dropna().unique().tolist())
    exec_df = summary_top[summary_top["velocity_type"] == "Execution Velocity"].sort_values("assignee_name", ascending=True)
    back_df = summary_top[summary_top["velocity_type"] == "Backlog Velocity"].sort_values("assignee_name", ascending=True)

    heat_exec_fig = px.density_heatmap(
        exec_df,
        x="priority_name",
        y="assignee_name",
        z="avg_velocity",
        histfunc="avg",
        text_auto=True,
        category_orders={"assignee_name": assignee_order, "priority_name": PRIORITY_ORDER},
        color_continuous_scale="RdYlGn_r",
        title=f"Execution Velocity by Priority (last {time_period_days} days)",
    ) if not exec_df.empty else None
    if heat_exec_fig:
        heat_exec_fig.update_xaxes(categoryorder="array", categoryarray=PRIORITY_ORDER)
        heat_exec_fig.update_yaxes(categoryorder="array", categoryarray=assignee_order)

    heat_backlog_fig = px.density_heatmap(
        back_df,
        x="priority_name",
        y="assignee_name",
        z="avg_velocity",
        histfunc="avg",
        text_auto=True,
        category_orders={"assignee_name": assignee_order, "priority_name": PRIORITY_ORDER},
        color_continuous_scale="RdYlGn_r",
        title=f"Backlog Velocity by Priority (last {time_period_days} days)",
    ) if not back_df.empty else None
    if heat_backlog_fig:
        heat_backlog_fig.update_xaxes(categoryorder="array", categoryarray=PRIORITY_ORDER)
        heat_backlog_fig.update_yaxes(categoryorder="array", categoryarray=assignee_order)

    compare = (
        combined_data[combined_data["assignee_name"].isin(top_assignees)]
        .groupby(["assignee_name", "velocity_type"], as_index=False)["velocity_value"]
        .mean()
    )
    compare_fig = px.bar(
        compare,
        x="assignee_name",
        y="velocity_value",
        color="velocity_type",
        barmode="group",
        title=f"Average Velocity: Execution vs Backlog by Assignee (last {time_period_days} days)",
    )
    compare_fig.update_layout(height=420, xaxis_title="Assignee", yaxis_title="Average Velocity (Days)")
    compare_fig.update_xaxes(tickangle=45)

    return {
        "ticket_count": ticket_count,
        "avg_execution": avg_execution,
        "avg_backlog": avg_backlog,
        "box_fig": box_fig,
        "heat_exec_fig": heat_exec_fig,
        "heat_backlog_fig": heat_backlog_fig,
        "compare_fig": compare_fig,
    }



