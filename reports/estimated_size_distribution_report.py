import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"

SIZE_ORDER = ["Small", "Medium", "Large", "XL", "Unestimated"]
HEATMAP_SIZE_ORDER = ["Small", "Medium", "Large", "XL"]
SIZE_COLORS = {
    "Small": "#16a34a",
    "Medium": "#f59e0b",
    "Large": "#f97316",
    "XL": "#dc2626",
    "Unestimated": "#94a3b8",
}
BACKLOG_STATUSES = {"to do", "tech discovery required"}

PRIORITY_ORDER = ["Urgent", "High", "Medium", "Low", "None"]
RISK_PRIORITIES = {"Urgent", "High"}
RISK_SIZES = {"Large", "XL"}


def _empty_payload() -> dict:
    return {
        "in_progress_count": 0,
        "backlog_count": 0,
        "bar_fig": None,
        "pie_in_progress_fig": None,
        "pie_backlog_fig": None,
        "priority_size_heatmap_fig": None,
        "table_df": pd.DataFrame(),
        "detail_df": pd.DataFrame(),
    }


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


def _first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _normalize_size(series: pd.Series) -> pd.Series:
    norm = series.fillna("Unestimated").astype(str).str.strip()
    norm = norm.replace("", "Unestimated")
    return norm.where(norm.isin(SIZE_ORDER[:-1]), "Unestimated")


def _safe_zmax(z_df: pd.DataFrame) -> int:
    vals = z_df.values.astype(float)
    if np.all(np.isnan(vals)):
        return 1
    return max(int(np.nanmax(vals)), 1)


def _text_color(value: float, vmax: float, threshold: float = 0.55) -> str:
    if pd.isna(value) or vmax <= 0:
        return "#0f172a"
    return "#ffffff" if (value / vmax) >= threshold else "#0f172a"


def _build_priority_size_heatmap(combined: pd.DataFrame, priority_col: str | None):
    if priority_col is None:
        return None

    heat_df = combined[combined["size_group"].isin(HEATMAP_SIZE_ORDER)].copy()
    if heat_df.empty:
        return None
    heat_df["priority_group"] = _normalize_priority(heat_df[priority_col])

    pivot = (
        heat_df.groupby(["priority_group", "size_group"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=PRIORITY_ORDER, columns=HEATMAP_SIZE_ORDER, fill_value=0)
    )

    risk_mask = pd.DataFrame(
        [[(p in RISK_PRIORITIES) and (s in RISK_SIZES) for s in HEATMAP_SIZE_ORDER] for p in PRIORITY_ORDER],
        index=PRIORITY_ORDER,
        columns=HEATMAP_SIZE_ORDER,
    )
    neutral_z = pivot.where(~risk_mask)
    risk_z = pivot.where(risk_mask)
    neutral_zmax = _safe_zmax(neutral_z)
    risk_zmax = _safe_zmax(risk_z)

    fig = go.Figure()
    fig.add_trace(
        go.Heatmap(
            z=neutral_z.values,
            x=HEATMAP_SIZE_ORDER,
            y=PRIORITY_ORDER,
            colorscale=[[0, "#f8fafc"], [1, "#2563eb"]],
            zmin=0,
            zmax=neutral_zmax,
            showscale=True,
            colorbar=dict(title="Tickets", x=1.03, len=0.85, thickness=16),
            hovertemplate="Priority: %{y}<br>Size: %{x}<br>Tickets: %{z}<extra></extra>",
            name="Ticket Count",
            xgap=6,
            ygap=6,
        )
    )
    fig.add_trace(
        go.Heatmap(
            z=risk_z.values,
            x=HEATMAP_SIZE_ORDER,
            y=PRIORITY_ORDER,
            colorscale=[[0, "#fff1f2"], [1, "#b91c1c"]],
            zmin=0,
            zmax=risk_zmax,
            showscale=True,
            colorbar=dict(title="At-Risk", x=1.24, len=0.85, thickness=16),
            hovertemplate="⚠️ At-Risk<br>Priority: %{y}<br>Size: %{x}<br>Tickets: %{z}<extra></extra>",
            name="At-Risk",
            xgap=6,
            ygap=6,
        )
    )

    for p in PRIORITY_ORDER:
        for s in HEATMAP_SIZE_ORDER:
            value = int(pivot.loc[p, s])
            vmax = risk_zmax if risk_mask.loc[p, s] else neutral_zmax
            fig.add_annotation(
                x=s,
                y=p,
                text=f"<b>{value}</b>",
                showarrow=False,
                font=dict(color=_text_color(value, vmax), size=16),
            )

    fig.update_layout(
        title="Priority × Size Heatmap (Urgent/High + Large/XL flagged as at-risk)",
        height=440,
        xaxis_title="Estimated Size",
        yaxis_title="Priority",
        template="simple_white",
        margin=dict(l=90, r=170, t=70, b=60),
        plot_bgcolor="#ffffff",
    )
    fig.update_xaxes(categoryorder="array", categoryarray=HEATMAP_SIZE_ORDER, showgrid=False, side="bottom")
    fig.update_yaxes(categoryorder="array", categoryarray=PRIORITY_ORDER, autorange="reversed", showgrid=False)
    return fig


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

    priority_size_heatmap_fig = _build_priority_size_heatmap(combined, priority_col)

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
        "priority_size_heatmap_fig": priority_size_heatmap_fig,
        "table_df": table_df,
        "detail_df": detail_df,
    }
