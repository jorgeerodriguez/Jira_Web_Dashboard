import pandas as pd
import plotly.express as px


TIME_PERIOD_DAYS = 90
JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"


def _empty_payload() -> dict:
    return {
        "total_blocked": 0,
        "overdue_tickets": 0,
        "due_soon_tickets": 0,
        "high_priority_tickets": 0,
        "blocked_fig": None,
        "risk_fig": None,
        "detail_df": pd.DataFrame(),
    }


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def build_blocked_visuals(df_issues: pd.DataFrame) -> dict:
    """Build an executive dashboard for Blocked tickets from Jira dataframe."""
    if df_issues is None or df_issues.empty:
        return _empty_payload()

    status_col = _first_existing_column(df_issues, ["status", "Status"])
    if status_col is None:
        return _empty_payload()

    df = df_issues[df_issues[status_col].astype(str).str.lower().eq("blocked")].copy()
    if df.empty:
        return _empty_payload()

    assignee_col = _first_existing_column(df, ["assignee_name", "Assignee"])
    lead_col = _first_existing_column(df, ["bussiness_lead", "business_lead", "Business Lead"])
    priority_col = _first_existing_column(df, ["priority_name", "priority", "Priority"])
    days_old_col = _first_existing_column(df, ["days_old", "Days Old"])
    key_col = _first_existing_column(df, ["key", "Key", "ticket", "Ticket"])
    target_end_col = _first_existing_column(df, ["target_end_date", "project_due_date", "Target End Date"])
    updated_col = _first_existing_column(df, ["updated", "Updated"])

    if assignee_col is None:
        df["assignee_name"] = "Unassigned"
        assignee_col = "assignee_name"
    if lead_col is None:
        df["bussiness_lead"] = "Unknown"
        lead_col = "bussiness_lead"
    if priority_col is None:
        df["priority_name"] = "Unknown"
        priority_col = "priority_name"
    if days_old_col is None:
        df["days_old"] = 0
        days_old_col = "days_old"
    if key_col is None:
        df["key"] = df.index.astype(str)
        key_col = "key"

    today = pd.Timestamp.now(tz="UTC")
    today_ts = today.normalize()
    today_date_only = today_ts.date()

    if target_end_col is not None:
        df[target_end_col] = pd.to_datetime(df[target_end_col], errors="coerce").dt.date
        df["days_left"] = df[target_end_col].apply(
            lambda d: (d - today_date_only).days if pd.notnull(d) else None
        )
    else:
        df["days_left"] = None

    if updated_col is not None:
        df[updated_col] = pd.to_datetime(df[updated_col], errors="coerce").dt.date

    if days_old_col in df.columns:
        df[days_old_col] = pd.to_numeric(df[days_old_col], errors="coerce").fillna(0)
    else:
        df[days_old_col] = 0

    if "days_left" in df.columns:
        days_left_num = pd.to_numeric(df["days_left"], errors="coerce")
    else:
        days_left_num = pd.Series([pd.NA] * len(df), index=df.index)

    df["risk_bucket"] = "On Track"
    df.loc[days_left_num.isna(), "risk_bucket"] = "No Target Date"
    df.loc[days_left_num < 0, "risk_bucket"] = "Overdue"
    df.loc[days_left_num.between(0, 7, inclusive="both"), "risk_bucket"] = "Due in 7 Days"

    risk_colors = {
        "Overdue": "#d62728",
        "Due in 7 Days": "#ff7f0e",
        "On Track": "#2ca02c",
        "No Target Date": "#7f7f7f",
    }

    total_blocked = len(df)
    overdue_tickets = int((df["risk_bucket"] == "Overdue").sum())
    due_soon_tickets = int((df["risk_bucket"] == "Due in 7 Days").sum())
    high_priority_tickets = int(df[priority_col].astype(str).isin(["High", "Critical", "Urgent"]).sum())

    blocked_by_lead = df[lead_col].value_counts().sort_values().reset_index()
    blocked_by_lead.columns = ["Business Lead", "Count"]

    risk_counts = df["risk_bucket"].value_counts().reindex(
        ["Overdue", "Due in 7 Days", "On Track", "No Target Date"],
        fill_value=0,
    )

    blocked_fig = px.bar(
        blocked_by_lead,
        x="Count",
        y="Business Lead",
        orientation="h",
        title="Blocked Tickets by Business Lead",
        text="Count",
        color="Count",
        color_continuous_scale="Blues",
    )
    blocked_fig.update_layout(height=380, xaxis_title="Count", yaxis_title="")

    risk_df = risk_counts.reset_index()
    risk_df.columns = ["Risk", "Count"]
    risk_fig = px.pie(
        risk_df,
        names="Risk",
        values="Count",
        title="Blocked Risk Mix",
        color="Risk",
        color_discrete_map=risk_colors,
    )
    risk_fig.update_layout(height=340)

    if key_col is None:
        df["key"] = df.index.astype(str)
        key_col = "key"

    detail_df = df[[key_col, lead_col, assignee_col, priority_col, days_old_col, "days_left", "risk_bucket"]].copy()
    detail_df[key_col] = detail_df[key_col].astype(str).apply(lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}")
    detail_df.columns = [
        "Ticket",
        "Business Lead",
        "Assignee",
        "Priority",
        "Days Old",
        "Days Left",
        "Risk",
    ]

    return {
        "total_blocked": total_blocked,
        "overdue_tickets": overdue_tickets,
        "due_soon_tickets": due_soon_tickets,
        "high_priority_tickets": high_priority_tickets,
        "blocked_fig": blocked_fig,
        "risk_fig": risk_fig,
        "detail_df": detail_df,
    }


def build_in_progress_visuals(df_issues: pd.DataFrame) -> dict:
    """Backward-compatible alias for existing callers."""
    return build_blocked_visuals(df_issues)
