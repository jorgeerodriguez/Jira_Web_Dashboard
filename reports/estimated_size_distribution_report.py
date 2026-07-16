import pandas as pd
import plotly.express as px


JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"

SIZE_ORDER = ["Small", "Medium", "Large", "XL", "Unestimated"]
SIZE_COLORS = {
    "Small": "#16a34a",
    "Medium": "#f59e0b",
    "Large": "#f97316",
    "XL": "#dc2626",
    "Unestimated": "#94a3b8",
}
BACKLOG_STATUSES = {"to do", "tech discovery required"}


def _empty_payload() -> dict:
    return {
        "in_progress_count": 0,
        "backlog_count": 0,
        "bar_fig": None,
        "pie_in_progress_fig": None,
        "pie_backlog_fig": None,
        "table_df": pd.DataFrame(),
        "detail_df": pd.DataFrame(),
    }


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _normalize_size(series: pd.Series) -> pd.Series:
    norm = series.fillna("Unestimated").astype(str).str.strip()
    norm = norm.replace("", "Unestimated")
    return norm.where(norm.isin(SIZE_ORDER[:-1]), "Unestimated")


def _pie(sub_df: pd.DataFrame, title: str):
    if sub_df.empty:
        return None
    sizes = sub_df["size_group"].value_counts().reindex(SIZE_ORDER).dropna()
    fig = px.pie(
        names=sizes.index,
        values=sizes.values,
        title=title,
        color=sizes.index,
        color_discrete_map=SIZE_COLORS,
        hole=0.45,
    )
    fig.update_traces(textinfo="label+percent+value")
    fig.update_layout(height=380)
    return fig


def build_estimated_size_distribution_visuals(df_issues: pd.DataFrame) -> dict:
    """Distribution of tickets by estimated_size_name, split into In Progress vs Backlog.

    In Progress: status == 'In Progress'.
    Backlog: status in {'To Do', 'Tech Discovery Required'}.
    Features (issuetype == 'Feature') are excluded from both groups.
    """
    if df_issues is None or df_issues.empty:
        return _empty_payload()

    required = {"status", "estimated_size_name"}
    if not required.issubset(df_issues.columns):
        return _empty_payload()

    df = df_issues.copy()
    status_norm = df["status"].fillna("").astype(str).str.strip().str.casefold()

    if "issuetype" in df.columns:
        issuetype_norm = df["issuetype"].fillna("").astype(str).str.strip().str.casefold()
        keep_mask = ~issuetype_norm.eq("feature")
        df = df[keep_mask].copy()
        status_norm = status_norm[keep_mask]

    df["size_group"] = _normalize_size(df["estimated_size_name"])

    in_progress_df = df[status_norm.eq("in progress")].copy()
    backlog_df = df[status_norm.isin(BACKLOG_STATUSES)].copy()

    if in_progress_df.empty and backlog_df.empty:
        return _empty_payload()

    in_progress_df["Group"] = "In Progress"
    backlog_df["Group"] = "Backlog"
    combined = pd.concat([in_progress_df, backlog_df], ignore_index=True)

    counts = combined.groupby(["Group", "size_group"]).size().reset_index(name="Tickets")

    bar_fig = px.bar(
        counts,
        x="size_group",
        y="Tickets",
        color="Group",
        barmode="group",
        text="Tickets",
        category_orders={"size_group": SIZE_ORDER, "Group": ["In Progress", "Backlog"]},
        color_discrete_map={"In Progress": "#2563eb", "Backlog": "#94a3b8"},
        title="Ticket Count by Estimated Size — In Progress vs Backlog",
    )
    bar_fig.update_traces(textposition="outside")
    bar_fig.update_layout(height=440, xaxis_title="Estimated Size", yaxis_title="Tickets", template="simple_white")

    pie_in_progress_fig = _pie(in_progress_df, f"In Progress by Size (n={len(in_progress_df)})")
    pie_backlog_fig = _pie(backlog_df, f"Backlog by Size (n={len(backlog_df)})")

    table_df = (
        counts.pivot(index="size_group", columns="Group", values="Tickets")
        .reindex(SIZE_ORDER)
        .fillna(0)
        .astype(int)
    )
    for col in ["In Progress", "Backlog"]:
        if col not in table_df.columns:
            table_df[col] = 0
    table_df = table_df[["In Progress", "Backlog"]].reset_index().rename(columns={"size_group": "Estimated Size"})

    key_col = _first_existing_column(combined, ["key", "Key", "ticket", "Ticket"])
    priority_col = _first_existing_column(combined, ["priority_name", "priority", "Priority"])
    lead_col = _first_existing_column(combined, ["bussiness_lead", "business_lead", "Business Lead"])
    creator_col = _first_existing_column(combined, ["creator_name", "creator", "Creator"])
    assignee_col = _first_existing_column(combined, ["assignee_name", "Assignee"])
    summary_col = _first_existing_column(combined, ["summary", "Summary"])

    detail_df = pd.DataFrame()
    if key_col is not None:
        detail_df = combined[["Group", "size_group", key_col]].copy()
        detail_df["Priority"] = combined[priority_col].astype(str) if priority_col is not None else "Unknown"
        detail_df["Business Lead"] = combined[lead_col].astype(str) if lead_col is not None else "Unknown"
        detail_df["Creator"] = combined[creator_col].astype(str) if creator_col is not None else "Unknown"
        detail_df["Assignee Name"] = combined[assignee_col].astype(str) if assignee_col is not None else "Unassigned"
        detail_df["Summary"] = combined[summary_col].astype(str) if summary_col is not None else ""
        detail_df = detail_df.rename(columns={key_col: "Ticket", "size_group": "Size"})
        detail_df["Ticket"] = detail_df["Ticket"].astype(str).apply(lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}")
        detail_df["_group_order"] = detail_df["Group"].map({"In Progress": 0, "Backlog": 1})
        detail_df["_size_order"] = detail_df["Size"].map({size: i for i, size in enumerate(SIZE_ORDER)})
        detail_df = detail_df.sort_values(["_group_order", "_size_order", "Assignee Name"]).drop(
            columns=["_group_order", "_size_order"]
        )
        detail_df = detail_df[
            ["Group", "Ticket", "Priority", "Size", "Business Lead", "Creator", "Assignee Name", "Summary"]
        ].reset_index(drop=True)

    return {
        "in_progress_count": int(len(in_progress_df)),
        "backlog_count": int(len(backlog_df)),
        "bar_fig": bar_fig,
        "pie_in_progress_fig": pie_in_progress_fig,
        "pie_backlog_fig": pie_backlog_fig,
        "table_df": table_df,
        "detail_df": detail_df,
    }
