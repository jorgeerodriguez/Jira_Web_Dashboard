import pandas as pd
import plotly.express as px


JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"


def _empty_payload() -> dict:
    return {
        "total_validating": 0,
        "overdue_tickets": 0,
        "due_soon_tickets": 0,
        "urgent_tickets": 0,
        "oldest_fig": None,
        "risk_fig": None,
        "assignee_fig": None,
        "detail_df": pd.DataFrame(),
    }


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def build_validating_visuals(df_issues: pd.DataFrame) -> dict:
    """Build Validating leadership visuals from Jira dataframe."""
    if df_issues is None or df_issues.empty:
        return _empty_payload()

    status_col = _first_existing_column(df_issues, ["status", "Status"])
    if status_col is None:
        return _empty_payload()

    df1 = df_issues[df_issues[status_col].astype(str).str.lower().eq("validating")].copy()
    if df1.empty:
        return _empty_payload()

    assignee_col = _first_existing_column(df1, ["assignee_name", "Assignee"])
    lead_col = _first_existing_column(df1, ["bussiness_lead", "business_lead", "Business Lead"])
    priority_col = _first_existing_column(df1, ["priority_name", "priority", "Priority"])
    days_old_col = _first_existing_column(df1, ["days_old", "Days Old"])
    summary_col = _first_existing_column(df1, ["summary", "Summary"])
    key_col = _first_existing_column(df1, ["key", "Key", "ticket", "Ticket"])
    target_end_col = _first_existing_column(df1, ["target_end_date", "project_due_date", "Target End Date"])
    updated_col = _first_existing_column(df1, ["updated", "Updated"])

    if assignee_col is None:
        df1["assignee_name"] = "Unassigned"
        assignee_col = "assignee_name"
    if lead_col is None:
        df1["bussiness_lead"] = "Unknown"
        lead_col = "bussiness_lead"
    if priority_col is None:
        df1["priority_name"] = "Unknown"
        priority_col = "priority_name"
    if days_old_col is None:
        df1["days_old"] = 0
        days_old_col = "days_old"
    if summary_col is None:
        df1["summary"] = ""
        summary_col = "summary"
    if key_col is None:
        df1["key"] = df1.index.astype(str)
        key_col = "key"

    today = pd.to_datetime("today", utc=True, errors="coerce").tz_convert("UTC-06:00")
    if isinstance(today, pd.Timestamp):
        today_ts = today.normalize()
    else:
        today_ts = pd.to_datetime(today).normalize()
    today_date_only = today_ts.date()

    if target_end_col is not None:
        df1[target_end_col] = pd.to_datetime(df1[target_end_col], errors="coerce").dt.date
        df1["days_left"] = df1[target_end_col].apply(
            lambda d: (d - today_date_only).days if pd.notnull(d) else None
        )
    else:
        df1["days_left"] = None

    if updated_col is not None:
        df1[updated_col] = pd.to_datetime(df1[updated_col], errors="coerce").dt.date

    days_left_num = pd.to_numeric(df1["days_left"], errors="coerce")
    df1["risk_bucket"] = "On Track"
    df1.loc[days_left_num.isna(), "risk_bucket"] = "No Target Date"
    df1.loc[days_left_num < 0, "risk_bucket"] = "Overdue"
    df1.loc[days_left_num.between(0, 7, inclusive="both"), "risk_bucket"] = "Due in 7 Days"

    risk_colors = {
        "Overdue": "#d62728",
        "Due in 7 Days": "#ff7f0e",
        "On Track": "#2ca02c",
        "No Target Date": "#7f7f7f",
    }

    total_tickets = len(df1)
    overdue_tickets = int((df1["risk_bucket"] == "Overdue").sum())
    due_soon_tickets = int((df1["risk_bucket"] == "Due in 7 Days").sum())
    urgent_tickets = int(df1[priority_col].astype(str).eq("Urgent").sum())

    top_oldest = df1.sort_values([days_old_col, "days_left"], ascending=[False, True]).copy()

    business_lead_counts = df1[lead_col].value_counts().sort_values().reset_index()
    business_lead_counts.columns = ["Business Lead", "Count"]

    assignee_counts = df1[assignee_col].value_counts().sort_values().reset_index()
    assignee_counts.columns = ["Assignee", "Count"]

    risk_counts = df1["risk_bucket"].value_counts().reindex(
        ["Overdue", "Due in 7 Days", "On Track", "No Target Date"],
        fill_value=0,
    )

    oldest_fig = px.bar(
        top_oldest,
        x=days_old_col,
        y=key_col,
        orientation="h",
        color="risk_bucket",
        color_discrete_map=risk_colors,
        title="Oldest Validating Tickets",
        hover_data={
            key_col: True,
            assignee_col: True,
            lead_col: True,
            days_old_col: True,
            "days_left": True,
        },
    )
    oldest_fig.update_layout(height=420, xaxis_title="Days Old", yaxis_title="Ticket")

    risk_df = risk_counts.reset_index()
    risk_df.columns = ["Risk", "Count"]
    risk_fig = px.pie(
        risk_df,
        names="Risk",
        values="Count",
        title="Risk Mix",
        color="Risk",
        color_discrete_map=risk_colors,
    )
    risk_fig.update_layout(height=340)

    assignee_fig = px.bar(
        assignee_counts,
        x="Count",
        y="Assignee",
        orientation="h",
        title="Tickets by Assignee",
        text="Count",
        color_discrete_sequence=["#2ca02c"],
    )
    assignee_fig.update_layout(height=340, xaxis_title="Count", yaxis_title="")

    detail_df = df1[[key_col, lead_col, assignee_col, priority_col, days_old_col, "risk_bucket"]].copy()
    detail_df.columns = ["Ticket", "Business Lead", "Assignee", "Priority", "Days Old", "Risk"]
    detail_df["Ticket"] = detail_df["Ticket"].astype(str).apply(
        lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}"
    )

    return {
        "total_validating": total_tickets,
        "overdue_tickets": overdue_tickets,
        "due_soon_tickets": due_soon_tickets,
        "urgent_tickets": urgent_tickets,
        "oldest_fig": oldest_fig,
        "risk_fig": risk_fig,
        "assignee_fig": assignee_fig,
        "detail_df": detail_df,
    }


def build_in_progress_visuals(df_issues: pd.DataFrame) -> dict:
    """Backward-compatible alias for existing callers."""
    return build_validating_visuals(df_issues)
