import pandas as pd
import plotly.express as px
import streamlit as st


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


def _lead_column(df_issues: pd.DataFrame) -> str:
    if "bussiness_lead" in df_issues.columns:
        return "bussiness_lead"
    if "business_lead" in df_issues.columns:
        return "business_lead"
    return ""


def build_executive_summary_data(df_issues: pd.DataFrame) -> dict:
    if df_issues is None or df_issues.empty:
        return {
            "total_tickets": 0,
            "avg_days_old": 0.0,
            "over_90_days": 0,
            "overdue_tickets": 0,
            "lead_age_df": pd.DataFrame(),
            "status_priority_df": pd.DataFrame(),
            "assignee_age_df": pd.DataFrame(),
            "top_oldest_df": pd.DataFrame(),
        }

    df = df_issues.copy()
    if "status" in df.columns:
        df = df[~df["status"].isin(EXCLUDED_STATUSES)].copy()

    if "days_old" in df.columns:
        df["days_old"] = pd.to_numeric(df["days_old"], errors="coerce")
    else:
        df["days_old"] = 0

    if "target_end_date" in df.columns:
        df["target_end_date"] = pd.to_datetime(df["target_end_date"], errors="coerce").dt.date
    else:
        df["target_end_date"] = pd.NaT

    today_date = pd.to_datetime("today", utc=True, errors="coerce").tz_convert("UTC-06:00").date()
    df["days_left"] = (df["target_end_date"] - today_date).apply(lambda x: x.days if pd.notnull(x) else None)

    lead_col = _lead_column(df)
    if lead_col:
        df[lead_col] = df[lead_col].fillna("Unknown")
    else:
        df["lead_label"] = "Unknown"
        lead_col = "lead_label"

    if "assignee_name" in df.columns:
        df["assignee_name"] = df["assignee_name"].fillna("Unassigned")
    else:
        df["assignee_name"] = "Unassigned"

    if "priority_name" in df.columns:
        df["priority_name"] = df["priority_name"].fillna("No Priority")
    else:
        df["priority_name"] = "No Priority"

    total_tickets = int(len(df))
    avg_days_old = float(df["days_old"].mean()) if total_tickets else 0.0
    over_90_days = int((df["days_old"] >= 90).sum())
    overdue_tickets = int((df["days_left"] < 0).sum())

    lead_age_df = (
        df.groupby(lead_col, as_index=False)["days_old"]
        .mean()
        .sort_values("days_old", ascending=True)
        .head(12)
        .rename(columns={lead_col: "Business Lead", "days_old": "Avg Days Old"})
    )

    if {"status", "priority_name"}.issubset(df.columns):
        status_priority_df = pd.crosstab(df["status"], df["priority_name"]).reset_index().melt(
            id_vars="status", var_name="Priority", value_name="Count"
        )
        status_priority_df.rename(columns={"status": "Status"}, inplace=True)
    else:
        status_priority_df = pd.DataFrame(columns=["Status", "Priority", "Count"])

    assignee_age_df = (
        df.groupby("assignee_name", as_index=False)["days_old"]
        .mean()
        .sort_values("days_old", ascending=True)
        .head(15)
        .rename(columns={"assignee_name": "Assignee", "days_old": "Avg Days Old"})
    )

    top_oldest_df = df.sort_values("days_old", ascending=False).head(30).copy()
    keep_cols = [c for c in ["key", "status", "assignee_name", "priority_name", lead_col, "days_old", "summary"] if c in top_oldest_df.columns]
    top_oldest_df = top_oldest_df[keep_cols].copy()
    if lead_col in top_oldest_df.columns:
        top_oldest_df.rename(columns={lead_col: "Business Lead"}, inplace=True)
    if "assignee_name" in top_oldest_df.columns:
        top_oldest_df.rename(columns={"assignee_name": "Assignee"}, inplace=True)
    if "priority_name" in top_oldest_df.columns:
        top_oldest_df.rename(columns={"priority_name": "Priority"}, inplace=True)
    if "days_old" in top_oldest_df.columns:
        top_oldest_df.rename(columns={"days_old": "Days Old"}, inplace=True)

    return {
        "total_tickets": total_tickets,
        "avg_days_old": avg_days_old,
        "over_90_days": over_90_days,
        "overdue_tickets": overdue_tickets,
        "lead_age_df": lead_age_df,
        "status_priority_df": status_priority_df,
        "assignee_age_df": assignee_age_df,
        "top_oldest_df": top_oldest_df,
    }


def render_executive_summary(df_issues: pd.DataFrame, report_date, lookback_days: int) -> None:
    st.title("📋 Executive Summary")
    st.caption(f"Platform Engineering · {report_date.strftime('%B %d, %Y')} · Lookback: {lookback_days} days")

    data = build_executive_summary_data(df_issues)
    if data["total_tickets"] == 0:
        st.info("📥 Fetch Jira tickets from the sidebar to display the Executive Summary.")
        return

    st.divider()

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Open Issues", data["total_tickets"])
    k2.metric("Avg Days Old", f"{data['avg_days_old']:.1f}")
    k3.metric("Created (24h)", "—")
    k4.metric("Resolved (24h)", "—")
    k5.metric("90+ Days", data["over_90_days"])
    k6.metric("Overdue", data["overdue_tickets"])

    st.divider()

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Status × Priority Heatmap")
        if not data["status_priority_df"].empty:
            fig_status = px.density_heatmap(
                data["status_priority_df"],
                x="Priority",
                y="Status",
                z="Count",
                histfunc="sum",
                color_continuous_scale="Blues",
                text_auto=True,
            )
            fig_status.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_status, use_container_width=True)
        else:
            st.info("No status data available.")

    with col_right:
        st.subheader("Top Oldest Tickets (by Assignee, Lead, Priority)")
        if not data["top_oldest_df"].empty:
            plot_df = data["top_oldest_df"].copy()
            color_col = "Business Lead" if "Business Lead" in plot_df.columns else None
            if color_col is None:
                plot_df["Business Lead"] = "Unknown"
                color_col = "Business Lead"
            if "Priority" not in plot_df.columns:
                plot_df["Priority"] = "No Priority"
            if "Assignee" not in plot_df.columns:
                plot_df["Assignee"] = "Unassigned"
            if "Days Old" not in plot_df.columns:
                plot_df["Days Old"] = 0

            fig_pri = px.scatter(
                plot_df,
                x="Days Old",
                y="Assignee",
                color=color_col,
                symbol="Priority",
                size="Days Old",
                size_max=28,
                hover_data=[c for c in ["key", "status", "summary", "Business Lead", "Priority"] if c in plot_df.columns],
                template="simple_white",
            )
            fig_pri.update_traces(
                marker=dict(
                    opacity=0.85,
                    line=dict(width=0.6, color="rgba(60,60,60,0.55)"),
                )
            )
            fig_pri.update_layout(
                height=420,
                margin=dict(l=10, r=10, t=10, b=10),
                legend_title_text="Business Lead / Priority",
                xaxis_title="Days Old",
                yaxis_title="Assignee",
                legend=dict(
                    orientation="v",
                    yanchor="top",
                    y=1,
                    xanchor="left",
                    x=1.02,
                ),
            )
            st.plotly_chart(fig_pri, use_container_width=True)
        else:
            st.info("No oldest ticket data available.")

    st.subheader("Average Ticket Age by Business Lead")
    if not data["lead_age_df"].empty:
        fig_lead = px.bar(data["lead_age_df"], y="Business Lead", x="Avg Days Old", orientation="h")
        fig_lead.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_lead, use_container_width=True)
    else:
        st.info("No business lead data available.")

    st.subheader("Average Ticket Age by Assignee")
    if not data["assignee_age_df"].empty:
        fig_assignee = px.bar(data["assignee_age_df"], y="Assignee", x="Avg Days Old", orientation="h")
        fig_assignee.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_assignee, use_container_width=True)
    else:
        st.info("No assignee data available.")

    st.subheader("Top Oldest Tickets")
    if not data["top_oldest_df"].empty:
        display_df = data["top_oldest_df"].copy()
        if "summary" in display_df.columns:
            display_df["summary"] = display_df["summary"].fillna("").astype(str).str[:120]
        column_config = {}
        if "key" in display_df.columns:
            display_df["key"] = display_df["key"].astype(str).apply(lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}")
            column_config["key"] = st.column_config.LinkColumn(
                "key",
                help="Open Jira ticket",
                display_text=r".*/([^/]+)$",
            )
        st.dataframe(display_df, use_container_width=True, column_config=column_config)
    else:
        st.info("No oldest tickets available.")
