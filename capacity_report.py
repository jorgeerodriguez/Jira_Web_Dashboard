import pandas as pd
import plotly.express as px


def build_capacity_data(df_issues: pd.DataFrame) -> pd.DataFrame:
    """Build monthly created/completed capacity dataframe from Jira issues."""
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

    completed_scope = scope[scope["status"].isin(["Done", "Released Successfully to Production"])].copy()
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


def build_capacity_visuals(df_issues: pd.DataFrame) -> dict:
    """Return capacity KPIs and chart for Streamlit Capacity menu."""
    capacity_df = build_capacity_data(df_issues)
    yearly_fig = None

    if isinstance(df_issues, pd.DataFrame) and not df_issues.empty and "year_updated" in df_issues.columns:
        start_year = 2020
        yearly_df = (
            df_issues[df_issues["year_updated"] >= start_year]
            .groupby("year_updated")
            .size()
            .reset_index(name="Total")
            .sort_values("year_updated")
        )
        if not yearly_df.empty:
            yearly_df["year_updated"] = yearly_df["year_updated"].astype(str)
            yearly_fig = px.bar(
                yearly_df,
                x="year_updated",
                y="Total",
                color="year_updated",
                text="Total",
                title="Total Tickets Worked per Year",
                color_discrete_sequence=px.colors.qualitative.Bold,
            )
            yearly_fig.update_layout(
                height=360,
                xaxis_title="Calendar Year",
                yaxis_title="No. of Tickets",
                showlegend=True,
                legend_title_text="Year",
            )

    if capacity_df.empty:
        return {
            "avg_created": 0.0,
            "avg_completed": 0.0,
            "latest_created": 0,
            "latest_completed": 0,
            "yearly_total_fig": yearly_fig,
            "capacity_fig": None,
            "capacity_table": pd.DataFrame(),
        }

    avg_created = float(capacity_df["created"].mean())
    avg_completed = float(capacity_df["completed"].mean())
    latest_created = int(capacity_df.iloc[-1]["created"])
    latest_completed = int(capacity_df.iloc[-1]["completed"])

    plot_df = capacity_df.copy()
    plot_df["month"] = plot_df["date"].dt.strftime("%Y-%m")
    melted = plot_df.melt(
        id_vars=["date", "month"],
        value_vars=["created", "completed"],
        var_name="type",
        value_name="count",
    )

    fig = px.bar(
        melted,
        x="month",
        y="count",
        color="type",
        barmode="group",
        title="Created vs Completed Tickets and CARs per Month",
        color_discrete_map={"created": "#3b82f6", "completed": "#f97316"},
    )
    fig.update_layout(height=420, xaxis_title="Month", yaxis_title="Tickets")

    table_df = plot_df[["month", "created", "completed"]].copy()
    table_df.rename(columns={"month": "Month", "created": "Created", "completed": "Completed"}, inplace=True)

    return {
        "avg_created": avg_created,
        "avg_completed": avg_completed,
        "latest_created": latest_created,
        "latest_completed": latest_completed,
        "yearly_total_fig": yearly_fig,
        "capacity_fig": fig,
        "capacity_table": table_df,
    }


