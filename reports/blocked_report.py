import pandas as pd
import plotly.express as px

from reports import in_progress_report as ipr


TIME_PERIOD_DAYS = 90
JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"
# Statuses on the page, keyed by lowercase status; the value is the State shown.
HELD_STATUSES = {"blocked": "Blocked", "on hold": "On Hold"}
STATE_ORDER = ["Blocked", "On Hold"]
STATE_COLORS = {"Blocked": "#2a78d6", "On Hold": "#eb6834"}  # categorical slots 1 and 2
RISK_ORDER = ["Overdue", "Due in 7 Days", "On Track", "No Target Date"]


def _empty_payload() -> dict:
    return {
        "total_blocked": 0,
        "total_on_hold": 0,
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
    """Executive dashboard for Blocked and On Hold tickets (no Features or Initiatives), side by side."""
    if df_issues is None or df_issues.empty:
        return _empty_payload()

    status_col = _first_existing_column(df_issues, ["status", "Status"])
    if status_col is None:
        return _empty_payload()

    # Tickets only, with the same rules as the Executive Summary, In Progress and Backlog pages:
    # no Features or Initiatives, and no tickets assigned to EXCLUDED_ASSIGNEES.
    tickets = ipr._exclude_container_items(df_issues)
    if "assignee_name" in tickets.columns:
        assignee = ipr._normalize_assignee(tickets["assignee_name"]).str.casefold()
        tickets = tickets[~assignee.isin(ipr.EXCLUDED_ASSIGNEES)]
    status_norm = tickets[status_col].astype(str).str.strip().str.lower()
    df = tickets[status_norm.isin(HELD_STATUSES)].copy()
    if df.empty:
        return _empty_payload()
    df["state"] = status_norm[df.index].map(HELD_STATUSES)

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

    total_blocked = int((df["state"] == "Blocked").sum())
    total_on_hold = int((df["state"] == "On Hold").sum())
    overdue_tickets = int((df["risk_bucket"] == "Overdue").sum())
    due_soon_tickets = int((df["risk_bucket"] == "Due in 7 Days").sum())
    high_priority_tickets = int(df[priority_col].astype(str).isin(["High", "Critical", "Urgent"]).sum())

    def stacked_by_state(category_col: str, category_order: list[str], title: str, axis_title: str):
        counts = df.groupby([category_col, "state"]).size().reset_index(name="Tickets")
        fig = px.bar(
            counts, x="Tickets", y=category_col, color="state", orientation="h", text="Tickets", title=title,
            color_discrete_map=STATE_COLORS,
            category_orders={"state": STATE_ORDER, category_col: category_order},
        )
        fig.update_traces(marker_line_color="rgba(255,255,255,0.9)", marker_line_width=2,
                          hovertemplate="%{y} · %{fullData.name}: %{x} ticket(s)<extra></extra>")
        fig.update_layout(barmode="stack", height=380, xaxis_title="Tickets", yaxis_title=axis_title,
                          legend_title_text="", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        return fig

    lead_order = df[lead_col].fillna("Unknown").value_counts().index.tolist()  # most tickets on top
    df[lead_col] = df[lead_col].fillna("Unknown")
    blocked_fig = stacked_by_state(lead_col, lead_order, "Blocked & On Hold by Business Lead", "")
    risk_fig = stacked_by_state("risk_bucket", RISK_ORDER, "Risk Mix: Blocked vs On Hold", "")

    if key_col is None:
        df["key"] = df.index.astype(str)
        key_col = "key"

    df["_state_rank"] = df["state"].map({state: i for i, state in enumerate(STATE_ORDER)})
    df["_days_left_sort"] = pd.to_numeric(df["days_left"], errors="coerce")
    df = df.sort_values(["_state_rank", "_days_left_sort"], na_position="last")
    detail_df = df[[key_col, "state", lead_col, assignee_col, priority_col, days_old_col, "days_left", "risk_bucket"]].copy()
    detail_df[key_col] = detail_df[key_col].astype(str).apply(lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}")
    detail_df["days_left"] = pd.to_numeric(detail_df["days_left"], errors="coerce").round().astype("Int64")
    detail_df.columns = [
        "Ticket",
        "State",
        "Business Lead",
        "Assignee",
        "Priority",
        "Days Old",
        "Days Left",
        "Risk",
    ]

    return {
        "total_blocked": total_blocked,
        "total_on_hold": total_on_hold,
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
