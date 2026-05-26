import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"


EXCLUDED_STATUSES = {
    "Technical Debt",
    "Done",
    "Backlog",
    "Plan Release",
    "Prepare Release",
    "Will Not Do",
    "Blocked For Development",
    "Resource Constrained",
    "Released Successfully to Production",
    "❌ Rolled Back",
}


def get_tickets_older_than_days(df_issues: pd.DataFrame, min_days: int = 90) -> pd.DataFrame:
    """Filter issues to open tickets older than `min_days`."""
    if df_issues is None or df_issues.empty:
        return pd.DataFrame()

    required = {"status", "days_old", "key", "summary", "assignee_name"}
    if not required.issubset(df_issues.columns):
        return pd.DataFrame()

    df = df_issues.copy()
    df["days_old"] = pd.to_numeric(df["days_old"], errors="coerce")

    status_list = [s for s in df["status"].dropna().unique() if s not in EXCLUDED_STATUSES]
    filtered = df[(df["status"].isin(status_list)) & (df["days_old"] >= min_days)].copy()

    if "target_end_date" in filtered.columns:
        filtered["target_end_date"] = pd.to_datetime(filtered["target_end_date"], errors="coerce").dt.date

    if "updated" in filtered.columns:
        filtered["updated"] = pd.to_datetime(filtered["updated"], errors="coerce").dt.date

    if "project_due_date" in filtered.columns:
        filtered["due_date_date"] = pd.to_datetime(filtered["project_due_date"], errors="coerce").dt.date

    return filtered.sort_values("days_old", ascending=False)


def build_tickets_older_than_90_days_visuals(df_issues: pd.DataFrame) -> dict:
    """Build KPI values, charts, and detail table for the stale tickets view."""
    df_old = get_tickets_older_than_days(df_issues, min_days=90)

    if df_old.empty:
        return {
            "total_old": 0,
            "avg_age": 0,
            "hist_fig": None,
            "pie_fig": None,
            "top25_fig": None,
            "details_df": pd.DataFrame(),
        }

    total_old = int(len(df_old))
    avg_age = int(df_old["days_old"].mean()) if total_old > 0 else 0

    hist_fig = px.histogram(
        df_old,
        x="days_old",
        nbins=20,
        title="Age Distribution (>90 days)",
        color_discrete_sequence=["#f97316"],
    )
    hist_fig.update_layout(height=350)

    if "priority_name" in df_old.columns and df_old["priority_name"].notna().any():
        pri_counts = df_old["priority_name"].fillna("Unknown").value_counts().reset_index()
        pri_counts.columns = ["Priority", "Count"]
    else:
        pri_counts = pd.DataFrame({"Priority": ["Unknown"], "Count": [len(df_old)]})

    pie_fig = px.pie(
        pri_counts,
        names="Priority",
        values="Count",
        title="By Priority",
        color_discrete_map={
            "Critical": "#dc2626",
            "High": "#f97316",
            "Medium": "#facc15",
            "Low": "#4ade80",
            "Unknown": "#94a3b8",
        },
    )
    pie_fig.update_layout(height=350)

    lead_col = "business_lead" if "business_lead" in df_old.columns else "bussiness_lead"
    if lead_col in df_old.columns:
        label_series = df_old["key"].astype(str) + " | " + df_old[lead_col].fillna("Unknown").astype(str).str[:16]
    else:
        label_series = df_old["key"].astype(str)

    top25 = df_old.head(25).copy()
    top25_labels = label_series.loc[top25.index]
    top25_fig = go.Figure(
        go.Bar(
            x=top25["days_old"],
            y=top25_labels,
            orientation="h",
            marker_color="#60a5fa",
            text=top25["days_old"],
            textposition="outside",
        )
    )
    top25_fig.update_layout(
        title="Top 25 Oldest Open Tickets",
        xaxis_title="Days Old",
        yaxis_title="Ticket",
        yaxis=dict(autorange="reversed"),
        height=700,
        margin=dict(l=10, r=10, t=60, b=20),
    )

    detail_cols = [c for c in ["key", "status", "assignee_name", "priority_name", "days_old", "summary"] if c in df_old.columns]
    details_df = df_old[detail_cols].copy()
    rename_map = {
        "key": "Ticket",
        "status": "Status",
        "assignee_name": "Assignee",
        "priority_name": "Priority",
        "days_old": "Days Open",
        "summary": "Summary",
    }
    details_df.rename(columns=rename_map, inplace=True)
    if "Ticket" in details_df.columns:
        details_df["Ticket"] = details_df["Ticket"].astype(str).apply(
            lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}"
        )

    return {
        "total_old": total_old,
        "avg_age": avg_age,
        "hist_fig": hist_fig,
        "pie_fig": pie_fig,
        "top25_fig": top25_fig,
        "details_df": details_df,
    }
