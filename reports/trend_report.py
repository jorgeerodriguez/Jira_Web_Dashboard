import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from reports.velocity_report import PE_TEAM_MEMBERS


DONE_STATUSES = {"Done", "Released Successfully to Production", "Closed", "Resolved"}


def _empty_payload() -> dict:
    return {
        "kpis": {},
        "flow_fig": None,
        "cycle_fig": None,
        "status_mix_fig": None,
        "trend_fig": None,
        "pe_completion_trend_fig": None,
        "table_df": pd.DataFrame(),
    }


def build_trend_visuals(df_issues: pd.DataFrame, months: int = 9) -> dict:
    """Build leadership-level trend visuals for the last 6-9 months."""
    if df_issues is None or df_issues.empty:
        return _empty_payload()

    required = {"created", "updated", "status", "velocity_days"}
    if not required.issubset(df_issues.columns):
        return _empty_payload()

    df = df_issues.copy()
    df["created"] = pd.to_datetime(df["created"], errors="coerce")
    df["updated"] = pd.to_datetime(df["updated"], errors="coerce")
    df = df[df["updated"].notna() & df["created"].notna()].copy()
    if df.empty:
        return _empty_payload()

    months = max(6, min(9, int(months)))

    df["month_created"] = df["created"].dt.to_period("M").dt.to_timestamp()
    df["month_updated"] = df["updated"].dt.to_period("M").dt.to_timestamp()

    last_month = df["month_updated"].max()
    first_month = (last_month - pd.DateOffset(months=months - 1)).to_period("M").to_timestamp()
    idx = pd.date_range(start=first_month, end=last_month, freq="MS")

    created = (
        df[df["month_created"] >= first_month]
        .groupby("month_created")
        .size()
        .rename("Created")
    )
    completed = (
        df[(df["status"].isin(DONE_STATUSES)) & (df["month_updated"] >= first_month)]
        .groupby("month_updated")
        .size()
        .rename("Completed")
    )

    flow_df = pd.DataFrame(index=idx).join([created, completed], how="left").fillna(0)
    flow_df["Created"] = flow_df["Created"].astype(int)
    flow_df["Completed"] = flow_df["Completed"].astype(int)
    flow_df["Net Flow"] = flow_df["Completed"] - flow_df["Created"]
    flow_df["Cum Net Flow"] = flow_df["Net Flow"].cumsum()
    flow_df = flow_df.reset_index().rename(columns={"index": "month_start"})
    flow_df["Month"] = flow_df["month_start"].dt.strftime("%b %Y")

    flow_fig = go.Figure()
    flow_fig.add_bar(x=flow_df["Month"], y=flow_df["Created"], name="Created", marker_color="#94a3b8")
    flow_fig.add_bar(x=flow_df["Month"], y=flow_df["Completed"], name="Completed", marker_color="#16a34a")
    flow_fig.add_scatter(
        x=flow_df["Month"],
        y=flow_df["Net Flow"],
        name="Net Flow",
        mode="lines+markers",
        yaxis="y2",
        line=dict(color="#f97316", width=3),
    )
    flow_fig.update_layout(
        title="Monthly Delivery Flow (Created vs Completed)",
        height=420,
        barmode="group",
        template="simple_white",
        xaxis_title="Month",
        yaxis=dict(title="Ticket Count"),
        yaxis2=dict(title="Net Flow", overlaying="y", side="right", showgrid=False),
    )

    done = df[(df["status"].isin(DONE_STATUSES)) & (df["month_updated"] >= first_month)].copy()
    cycle = (
        done.groupby("month_updated")["velocity_days"]
        .agg(median_days="median", p75_days=lambda s: s.quantile(0.75))
    )
    cycle_df = (
        pd.DataFrame(index=idx)
        .join(cycle, how="left")
        .fillna(0)
        .reset_index()
        .rename(columns={"index": "month_start"})
    )
    cycle_df["Month"] = cycle_df["month_start"].dt.strftime("%b %Y")

    cycle_fig = go.Figure()
    cycle_fig.add_scatter(
        x=cycle_df["Month"],
        y=cycle_df["median_days"],
        name="Median Cycle Time",
        mode="lines+markers",
        line=dict(color="#2563eb", width=3),
    )
    cycle_fig.add_scatter(
        x=cycle_df["Month"],
        y=cycle_df["p75_days"],
        name="P75 Cycle Time",
        mode="lines+markers",
        line=dict(color="#dc2626", width=3, dash="dash"),
    )
    cycle_fig.update_layout(
        title="Cycle Time Trend for Completed Work",
        height=360,
        template="simple_white",
        xaxis_title="Month",
        yaxis_title="Days",
    )

    status_month = (
        df[df["month_updated"] >= first_month]
        .groupby(["month_updated", "status"]).size().reset_index(name="count")
    )
    status_month["Month"] = status_month["month_updated"].dt.strftime("%b %Y")
    top_statuses = df["status"].value_counts().head(6).index.tolist()
    status_month = status_month[status_month["status"].isin(top_statuses)].copy()
    mix_fig = px.area(
        status_month,
        x="Month",
        y="count",
        color="status",
        title="Monthly Status Mix (Top 6)",
    )
    mix_fig.update_layout(height=360, xaxis_title="Month", yaxis_title="Tickets", template="simple_white")

    pe_completion_trend_fig = None
    if "assignee_name" in df.columns and "issuetype" in df.columns:
        pe_done = df[
            df["status"].isin(DONE_STATUSES)
            & (df["month_updated"] >= first_month)
            & df["assignee_name"].isin(PE_TEAM_MEMBERS)
            & ~df["issuetype"].astype(str).str.strip().str.casefold().eq("feature")
        ].copy()
        if not pe_done.empty:
            pe_month = (
                pe_done.groupby(["month_updated", "assignee_name"])
                .size()
                .reset_index(name="Completed")
            )
            pe_month["Month"] = pe_month["month_updated"].dt.strftime("%b %Y")
            pe_completion_trend_fig = px.line(
                pe_month.sort_values("month_updated"),
                x="Month",
                y="Completed",
                color="assignee_name",
                markers=True,
                category_orders={"Month": flow_df["Month"].tolist()},
                title="Completion Trend by Platform Engineer (excl. Features)",
            )
            pe_completion_trend_fig.update_layout(
                height=420,
                template="simple_white",
                xaxis_title="Month",
                yaxis_title="Completed Tickets",
                legend_title="Assignee",
            )

    created_total = int(flow_df["Created"].sum())
    completed_total = int(flow_df["Completed"].sum())
    completion_rate = (completed_total / created_total * 100) if created_total > 0 else 0.0

    nonzero_median = cycle_df["median_days"].replace(0, pd.NA).dropna()
    nonzero_p75 = cycle_df["p75_days"].replace(0, pd.NA).dropna()

    kpis = {
        "created_total": created_total,
        "completed_total": completed_total,
        "completion_rate": float(completion_rate),
        "median_cycle": float(nonzero_median.mean()) if not nonzero_median.empty else 0.0,
        "p75_cycle": float(nonzero_p75.mean()) if not nonzero_p75.empty else 0.0,
    }

    table_df = flow_df[["Month", "Created", "Completed", "Net Flow", "Cum Net Flow"]].copy()
    return {
        "kpis": kpis,
        "flow_fig": flow_fig,
        "cycle_fig": cycle_fig,
        "status_mix_fig": mix_fig,
        "trend_fig": flow_fig,
        "pe_completion_trend_fig": pe_completion_trend_fig,
        "table_df": table_df,
    }

