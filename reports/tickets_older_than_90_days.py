import pandas as pd


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


EPIC_ISSUE_TYPES = {"feature", "iniciative", "initiative"}


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


def _build_details_table(df_subset: pd.DataFrame) -> pd.DataFrame:
    """Build a link-friendly details table for stale issues."""
    if df_subset.empty:
        return pd.DataFrame()

    detail_cols = [
        c
        for c in ["key", "issuetype", "status", "assignee_name", "priority_name", "days_old", "summary"]
        if c in df_subset.columns
    ]
    details_df = df_subset[detail_cols].copy()
    rename_map = {
        "key": "Issue",
        "issuetype": "Issue Type",
        "status": "Status",
        "assignee_name": "Assignee",
        "priority_name": "Priority",
        "days_old": "Days Open",
        "summary": "Summary",
    }
    details_df.rename(columns=rename_map, inplace=True)
    if "Issue" in details_df.columns:
        details_df["Issue"] = details_df["Issue"].astype(str).apply(
            lambda issue_key: f"{JIRA_BROWSE_BASE_URL}{issue_key}"
        )
    return details_df


def build_tickets_older_than_90_days_visuals(df_issues: pd.DataFrame) -> dict:
    """Build KPI values and split details for stale epics vs tickets."""
    df_old = get_tickets_older_than_days(df_issues, min_days=90)

    if df_old.empty:
        return {
            "epics_count": 0,
            "tickets_count": 0,
            "epics_df": pd.DataFrame(),
            "tickets_df": pd.DataFrame(),
        }

    issuetype_series = (
        df_old["issuetype"].fillna("").astype(str).str.strip().str.casefold()
        if "issuetype" in df_old.columns
        else pd.Series("", index=df_old.index)
    )

    epic_mask = issuetype_series.isin(EPIC_ISSUE_TYPES)
    df_epics = df_old[epic_mask].copy()
    df_tickets = df_old[~epic_mask].copy()

    epics_df = _build_details_table(df_epics)
    tickets_df = _build_details_table(df_tickets)

    return {
        "epics_count": int(len(df_epics)),
        "tickets_count": int(len(df_tickets)),
        "epics_df": epics_df,
        "tickets_df": tickets_df,
    }
