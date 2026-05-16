# Python version 3.15.5

from datetime import date, timedelta, datetime
import random

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from metrics import load_metrics

try:
    from Load_Configuration import load_config_details
except ModuleNotFoundError:
    from jira_morning_report_site.Load_Configuration import load_config_details

try:
    from validate_and_connect_to_jira import validate_jira_connection
except ModuleNotFoundError:
    from jira_morning_report_site.validate_and_connect_to_jira import validate_jira_connection
try:
    from fetch_all_tickets_for_devops import fetch_all_tickets_for_project
except ModuleNotFoundError:
    from jira_morning_report_site.fetch_all_tickets_for_devops import fetch_all_tickets_for_project
try:
    from tickets_distribution import plot_ticket_distribution
except ModuleNotFoundError:
    from jira_morning_report_site.tickets_distribution import plot_ticket_distribution
try:
    from build_dataframe_new import build_issues_dataframe
except ModuleNotFoundError:
    from jira_morning_report_site.build_dataframe_new import build_issues_dataframe
try:
    from tickets_older_than_90_days import build_tickets_older_than_90_days_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.tickets_older_than_90_days import build_tickets_older_than_90_days_visuals
try:
    from executive_summary import render_executive_summary
except ModuleNotFoundError:
    from jira_morning_report_site.executive_summary import render_executive_summary
try:
    from capacity_report import build_capacity_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.capacity_report import build_capacity_visuals
try:
    from velocity_report import build_velocity_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.velocity_report import build_velocity_visuals
try:
    from trend_report import build_trend_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.trend_report import build_trend_visuals
try:
    from in_progress_report import build_in_progress_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.in_progress_report import build_in_progress_visuals
try:
    from validating_report import build_validating_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.validating_report import build_validating_visuals
try:
    from backlog_report import build_backlog_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.backlog_report import build_backlog_visuals
try:
    from blocked_report import build_blocked_visuals
except ModuleNotFoundError:
    from jira_morning_report_site.blocked_report import build_blocked_visuals
try:
    from forcast_report import build_forecast_visuals
except ImportError:
    build_forecast_visuals = None
try:
    from distribution_of_tickets_report import build_distribution_visuals
except ImportError:
    build_distribution_visuals = None
try:
    from distribution_by_business_leader import build_business_leader_visuals
except ImportError:
    build_business_leader_visuals = None
try:
    from word_of_the_month_report import build_word_of_the_month_visuals
except ImportError:
    build_word_of_the_month_visuals = None
try:
    from service_level_agreement_report import build_sla_visuals, PRIORITY_SLA_DAYS
except ImportError:
    build_sla_visuals = None
    PRIORITY_SLA_DAYS = {}

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Platform Engineering Morning Report v1.0",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ───────────────────────────────────────────────────────────────
_defaults = {
    "jira_validation_code": None,
    "jira_validation_message": "",
    "jira_connector": None,
    "jira_fetch_code": None,
    "jira_fetch_message": "",
    "jira_fetch_count": 0,
    "jira_status_counts": {},
    "jira_df_issues": pd.DataFrame(),
    "selected_menu": "🏠  Overview",  # Default to Overview page
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 PE Morning Report")
    st.caption("Platform Engineering · Jira Dashboard")
    st.divider()

    cfg_details = load_config_details()
    cfg_source = cfg_details.get("source")
    source_label = {
        "streamlit_secrets": "Streamlit secrets",
        "environment": ".env / environment variables",
        "config_json": "config.json (legacy fallback)",
    }

    if cfg_source:
        if cfg_source == "config_json":
            st.warning("⚠️ Credentials loaded from config.json. For publishing, move them to Streamlit secrets or .env.")
        else:
            st.caption(f"🔐 Jira credentials source: **{source_label.get(cfg_source, cfg_source)}**")
    else:
        st.error("❌ Jira credentials not found. Configure Streamlit secrets or .env before publishing.")

    with st.expander("Credential setup (publish-ready)"):
        st.markdown(
            """
            **Recommended order**
            1. `.streamlit/secrets.toml`
            2. `.env` (or environment variables)
            3. `config.json` (local fallback only)

            Required keys:
            - `jira_server`
            - `jira_email`
            - `jira_api_token`
            """
        )

    # Jira connection controls
    validate_color = (
        "#16a34a" if st.session_state["jira_validation_code"] == 0
        else "#dc2626" if st.session_state["jira_validation_code"] == 1
        else "#64748b"
    )
    fetch_color = (
        "#16a34a" if st.session_state["jira_fetch_code"] == 0
        else "#dc2626" if st.session_state["jira_fetch_code"] == 1
        else "#64748b"
    )

    st.markdown(
        f"""
        <style>
        div[data-testid="stButton"]:nth-of-type(1) > button {{
            background-color: {validate_color}; color: white; width: 100%;
        }}
        div[data-testid="stButton"]:nth-of-type(2) > button {{
            background-color: {fetch_color}; color: white; width: 100%;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    if st.button("🔌 Test & Validate Jira"):
        code, message, jira = validate_jira_connection()
        st.session_state["jira_validation_code"] = code
        st.session_state["jira_validation_message"] = message
        st.session_state["jira_connector"] = jira
        st.rerun()

    if st.session_state["jira_validation_code"] == 0:
        st.caption(f"✅ {st.session_state['jira_validation_message']}")
    elif st.session_state["jira_validation_code"] == 1:
        st.caption(f"❌ {st.session_state['jira_validation_message']}")

    st.markdown("")

    if st.button("📥 Fetch All Jira Tickets"):
        jira_connector = st.session_state["jira_connector"]
        if jira_connector is None:
            code, message, jira_connector = validate_jira_connection()
            st.session_state.update({
                "jira_validation_code": code,
                "jira_validation_message": message,
                "jira_connector": jira_connector,
            })
        if jira_connector is None:
            st.session_state.update({
                "jira_fetch_code": 1,
                "jira_fetch_message": "Connection failed. Could not fetch tickets.",
                "jira_fetch_count": 0,
                "jira_status_counts": {},
                "jira_df_issues": pd.DataFrame(),
            })
        else:
            code, message, total_count, _sc = fetch_all_tickets_for_project(
                jira_connector=jira_connector, project_key="DEVOPS"
            )
            # Build dataframe if fetch succeeded
            df_issues = pd.DataFrame()
            if code == 0:
                df_issues = build_issues_dataframe(jira_connector, projects=("DEVOPS", "CAR"))
            
            st.session_state.update({
                "jira_fetch_code": code,
                "jira_fetch_message": message,
                "jira_fetch_count": total_count if code == 0 else 0,
                "jira_status_counts": _sc if code == 0 else {},
                "jira_df_issues": df_issues,
            })
        st.rerun()

    if st.session_state["jira_fetch_code"] == 0:
        st.caption(f"✅ {st.session_state['jira_fetch_count']:,} tickets fetched")
        if isinstance(st.session_state.get("jira_df_issues"), pd.DataFrame):
            st.caption(f"📄 Dataframe rows: {len(st.session_state['jira_df_issues']):,}")
    elif st.session_state["jira_fetch_code"] == 1:
        st.caption(f"❌ 0 records — {st.session_state['jira_fetch_message']}")

    st.divider()

    # Navigation menu
    MENU_ITEMS = [
        "📋  Executive Summary",
        "🏠  Overview",
        "📅  Tickets Older Than 90 Days",
        "📈  Capacity",
        "📉  Trend",
        "⚡  Velocity",
        "🔄  In Progress",
        "✅  Validating",
        "🚧  Blocked",
        "🗂️  Backlog",
        "🔮  Forecast",
        "📊  Distribution of Ticket's Age",
        "👤  Distribution per Business Leader",
        "💬  Word of the Month",
        "🛡️  SLA (Service Level Agreements)",
    ]

    selected = st.radio(
        "Navigate to",
        MENU_ITEMS,
        index=MENU_ITEMS.index(st.session_state.get("selected_menu", "🏠  Overview")),
        label_visibility="collapsed",
    )
    if selected != st.session_state.get("selected_menu"):
        st.session_state["selected_menu"] = selected

    st.divider()
    report_date = st.date_input("Report date", value=date.today())
    lookback_days = st.slider("Lookback days", min_value=1, max_value=30, value=7)


# ── Helper: placeholder notice ──────────────────────────────────────────────────
def _placeholder(section: str):
    st.info(
        f"**{section}** — visualization coming soon. "
        "Wire this section to live Jira data once tickets are fetched."
    )


# ── Mock data helpers ───────────────────────────────────────────────────────────
metrics = load_metrics(report_date=report_date, lookback_days=lookback_days)
start_date = report_date - timedelta(days=lookback_days - 1)


# ══════════════════════════════════════════════════════════════════════════════
# VIEWS
# ══════════════════════════════════════════════════════════════════════════════

# ── Executive Summary ────────────────────────────────────────────────────────
if selected == "📋  Executive Summary":
    render_executive_summary(
        st.session_state.get("jira_df_issues", pd.DataFrame()),
        report_date,
        lookback_days,
    )


# ── Overview ───────────────────────────────────────────────────────────────────
elif selected == "🏠  Overview":
    st.title("Platform Engineering Morning Report")
    st.caption(f"Showing **{start_date}** → **{report_date}**")

    status_counts = st.session_state.get("jira_status_counts", {})
    total_fetched = st.session_state.get("jira_fetch_count", 0)
    fetch_code = st.session_state.get("jira_fetch_code")

    if fetch_code is None:
        st.info("📥 Fetch Jira tickets from the sidebar to display the Overview")
    else:
        # ── KPI row ─────────────────────────────────────────────────────────
        k1, k2, k3, k4 = st.columns(4)
        if status_counts:
            total_open = (
                status_counts.get("Triage", 0)
                + status_counts.get("Tech Discovery Required", 0)
                + status_counts.get("To Do", 0)
                + status_counts.get("Blocked", 0)
                + status_counts.get("On Hold", 0)
                + status_counts.get("In Progress", 0)
                + status_counts.get("Validating", 0)
            )
            in_progress = status_counts.get("In Progress", 0)
            blocked     = status_counts.get("Blocked", 0)
            validating  = status_counts.get("Validating", 0)
            k1.metric("Total Open", f"{total_open:,}")
            k2.metric("In Progress", f"{in_progress:,}")
            k3.metric("Blocked", f"{blocked:,}")
            k4.metric("Validating", f"{validating:,}")
        else:
            k1.metric("Open Issues", int(metrics["open_issues"]))
            k2.metric("Created (24h)", int(metrics["created_24h"]))
            k3.metric("Resolved (24h)", int(metrics["resolved_24h"]))
            k4.metric("SLA Breaches", int(metrics["sla_breaches"]))

        st.divider()

        # ── Ticket distribution chart ───────────────────────────────────────
        if status_counts:
            st.subheader("Ticket Distribution by Kanban Status")
            dist_fig = plot_ticket_distribution(status_counts, project_key="DEVOPS")
            st.plotly_chart(dist_fig, use_container_width=True)
        else:
            st.info("📥 Fetch Jira tickets from the sidebar to see live ticket distribution.")
            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Issues by Priority (mock)")
                priority_df = pd.DataFrame({
                    "priority": ["Critical", "High", "Medium", "Low"],
                    "count": [
                        metrics["priority"]["critical"],
                        metrics["priority"]["high"],
                        metrics["priority"]["medium"],
                        metrics["priority"]["low"],
                    ],
                })
                fig = px.bar(priority_df, x="priority", y="count", text="count",
                             color="priority",
                             color_discrete_map={"Critical": "#dc2626", "High": "#f97316",
                                                 "Medium": "#facc15", "Low": "#4ade80"})
                fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

            with col_b:
                st.subheader("Daily Throughput (mock)")
                trend_df = pd.DataFrame(metrics["trend"])
                trend_df["day"] = pd.to_datetime(trend_df["day"])
                trend_fig = px.line(trend_df, x="day", y=["created", "resolved"], markers=True)
                trend_fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(trend_fig, use_container_width=True)


# ── Tickets Older Than 90 Days ──────────────────────────────────────────────────
elif selected == "📅  Tickets Older Than 90 Days":
    st.title("📅 Tickets Older Than 90 Days")
    st.caption("Tickets that have been open for more than 90 days without resolution.")
    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    visuals = build_tickets_older_than_90_days_visuals(df_issues)

    if visuals["total_old"] == 0:
        if isinstance(df_issues, pd.DataFrame) and not df_issues.empty:
            st.info("No open tickets older than 90 days were found in the current dataframe.")
        else:
            st.info("📥 Fetch Jira tickets from the sidebar to see tickets older than 90 days.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Total Stale Tickets", visuals["total_old"])
        c2.metric("Average Age (days)", visuals["avg_age"])

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(visuals["hist_fig"], use_container_width=True)

        with col2:
            st.plotly_chart(visuals["pie_fig"], use_container_width=True)

        st.subheader("Top 25 Oldest Open Tickets")
        st.plotly_chart(visuals["top25_fig"], use_container_width=True)

        st.subheader("Stale Ticket Details")
        st.dataframe(
            visuals["details_df"],
            use_container_width=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )


# ── Capacity ───────────────────────────────────────────────────────────────────
elif selected == "📈  Capacity":
    st.title("📈 Capacity")
    st.caption("Team incoming and completed work capacity.")
    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    cap = build_capacity_visuals(df_issues)

    if cap["capacity_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see capacity visuals.")
    else:
        if cap.get("yearly_total_fig") is not None:
            st.plotly_chart(cap["yearly_total_fig"], use_container_width=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Avg Created / month", f"{cap['avg_created']:.1f}")
        c2.metric("Avg Completed / month", f"{cap['avg_completed']:.1f}")
        c3.metric("Latest Created", f"{cap['latest_created']:,}")
        c4.metric("Latest Completed", f"{cap['latest_completed']:,}")

        st.divider()
        st.plotly_chart(cap["capacity_fig"], use_container_width=True)
        st.subheader("Capacity Monthly Detail")
        st.dataframe(cap["capacity_table"], use_container_width=True)


# ── Trend ──────────────────────────────────────────────────────────────────────
elif selected == "📉  Trend":
    st.title("📉 Trend")
    st.caption("Leadership trend view for the last 6-9 months from live Jira data.")

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    tr = build_trend_visuals(df_issues, months=9)

    if tr["trend_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see trend visuals.")
    else:
        kpis = tr.get("kpis", {})
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Created (period)", f"{int(kpis.get('created_total', 0)):,}")
        c2.metric("Completed (period)", f"{int(kpis.get('completed_total', 0)):,}")
        c3.metric("Completion Rate", f"{kpis.get('completion_rate', 0.0):.1f}%")
        c4.metric("Median Cycle Time", f"{kpis.get('median_cycle', 0.0):.1f} days")
        c5.metric("P75 Cycle Time", f"{kpis.get('p75_cycle', 0.0):.1f} days")

        st.divider()
        if tr.get("flow_fig") is not None:
            st.plotly_chart(tr["flow_fig"], use_container_width=True)
        if tr.get("cycle_fig") is not None:
            st.plotly_chart(tr["cycle_fig"], use_container_width=True)
        if tr["status_mix_fig"] is not None:
            st.plotly_chart(tr["status_mix_fig"], use_container_width=True)
        st.subheader("Trend Monthly Detail")
        st.dataframe(tr["table_df"], use_container_width=True)


# ── Velocity ───────────────────────────────────────────────────────────────────
elif selected == "⚡  Velocity":
    st.title("⚡ Velocity")
    st.caption("Execution and backlog velocity from live Jira dataframe.")
    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    vel = build_velocity_visuals(df_issues, time_period_days=90)

    if vel["box_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see velocity visuals.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Completed Tickets (90d)", f"{vel['ticket_count']:,}")
        c2.metric("Avg Execution Velocity", f"{vel['avg_execution']:.1f} days")
        c3.metric("Avg Backlog Velocity", f"{vel['avg_backlog']:.1f} days")

        st.divider()
        st.plotly_chart(vel["box_fig"], use_container_width=True)

        h1, h2 = st.columns(2)
        with h1:
            if vel["heat_exec_fig"] is not None:
                st.plotly_chart(vel["heat_exec_fig"], use_container_width=True)
        with h2:
            if vel["heat_backlog_fig"] is not None:
                st.plotly_chart(vel["heat_backlog_fig"], use_container_width=True)

        st.plotly_chart(vel["compare_fig"], use_container_width=True)


# ── In Progress ─────────────────────────────────────────────────────────────────
elif selected == "🔄  In Progress":
    st.title("🔄 In Progress")
    st.caption("Leadership view of current in-progress workload and estimated completion effort.")

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    ip = build_in_progress_visuals(df_issues)

    if ip["load_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see In Progress visuals.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total In Progress", f"{ip['total_in_progress']:,}")
        c2.metric("Estimated Total Days", f"{ip['total_estimated_days']:.0f}")
        c3.metric("Avg Days / Ticket", f"{ip['avg_velocity']:.1f}")
        c4.metric("Critical Assignees", f"{ip['critical_assignees']}")
        c5.metric("Missing Target End Date", f"{ip['missing_target_end_dates']}")

        st.divider()
        st.plotly_chart(ip["load_fig"], use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(ip["scatter_fig"], use_container_width=True)
        with col2:
            st.plotly_chart(ip["distribution_fig"], use_container_width=True)

        if ip.get("target_timeline_fig") is not None:
            st.subheader("Target End Date Timeline")
            st.plotly_chart(ip["target_timeline_fig"], use_container_width=True)

        st.subheader("In Progress Workload Detail")
        st.dataframe(ip["detail_df"], use_container_width=True)

        st.subheader("All In Progress Tickets")
        st.dataframe(
            ip["tickets_df"],
            use_container_width=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )


# ── Validating ──────────────────────────────────────────────────────────────────
elif selected == "✅  Validating":
    st.title("✅ Validating")
    st.caption("Tickets in QA / validation stage waiting for sign-off.")

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    val = build_validating_visuals(df_issues)

    if val["oldest_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see Validating visuals.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total in Validating", f"{val['total_validating']:,}")
        c2.metric("Overdue", f"{val['overdue_tickets']}")
        c3.metric("Due in 7 Days", f"{val['due_soon_tickets']}")
        c4.metric("Urgent Priority", f"{val['urgent_tickets']}")

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(val["oldest_fig"], use_container_width=True)
        with col2:
            st.plotly_chart(val["risk_fig"], use_container_width=True)

        st.plotly_chart(val["assignee_fig"], use_container_width=True)
        st.subheader("Validating Ticket Detail")
        st.dataframe(
            val["detail_df"],
            use_container_width=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )


# ── Blocked ──────────────────────────────────────────────────────────────────────
elif selected == "🚧  Blocked":
    st.title("🚧 Blocked")
    st.caption("Tickets that are blocked and require immediate attention.")

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    blocked = build_blocked_visuals(df_issues)

    if blocked["blocked_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see Blocked visuals.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Blocked", f"{blocked['total_blocked']:,}")
        c2.metric("Overdue", f"{blocked['overdue_tickets']}")
        c3.metric("Due in 7 Days", f"{blocked['due_soon_tickets']}")
        c4.metric("High Priority", f"{blocked['high_priority_tickets']}")

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(blocked["blocked_fig"], use_container_width=True)
        with col2:
            st.plotly_chart(blocked["risk_fig"], use_container_width=True)

        st.subheader("Blocked Ticket Detail")
        st.dataframe(
            blocked["detail_df"],
            use_container_width=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )


# ── Backlog ──────────────────────────────────────────────────────────────────────
elif selected == "🗂️  Backlog":
    st.title("🗂️ Backlog")
    st.caption("Tickets in To Do and Tech Discovery Required stages, with live backlog analysis.")

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    backlog = build_backlog_visuals(df_issues)

    if backlog["load_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see Backlog visuals.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Backlog", f"{backlog['total_in_progress']:,}")
        c2.metric("Estimated Total Days", f"{backlog['total_estimated_days']:.0f}")
        c3.metric("Avg Complexity Days", f"{backlog['avg_velocity']:.1f}")
        c4.metric("Critical Assignees", f"{backlog['critical_assignees']}")

        st.divider()
        st.plotly_chart(backlog["load_fig"], use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(backlog["scatter_fig"], use_container_width=True)
        with col2:
            st.plotly_chart(backlog["distribution_fig"], use_container_width=True)

        st.subheader("Backlog Workload Detail")
        st.dataframe(backlog["detail_df"], use_container_width=True)

        st.subheader("All Backlog Tickets")
        st.dataframe(
            backlog["tickets_df"],
            use_container_width=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )


# ── Forecast ─────────────────────────────────────────────────────────────────────
elif selected == "🔮  Forecast":
    st.title("🔮 Forecast")
    st.caption("XGBoost model trained on historical ticket completion data to project future throughput.")

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    if df_issues is None or (isinstance(df_issues, pd.DataFrame) and df_issues.empty):
        st.info("📥 Fetch Jira tickets from the sidebar to display the Forecast.")
    else:
        # ── PERIODS selector ──────────────────────────────────────────────────
        periods = st.slider(
            "📅 Forecast horizon (months)",
            min_value=1,
            max_value=12,
            value=4,
            step=1,
            help="Select how many months into the future to forecast. Changing this value re-runs the XGBoost model.",
        )

        with st.spinner(f"Training XGBoost model and forecasting {periods} month(s)…"):
            if build_forecast_visuals is None:
                st.error("⚠️ Forecast module could not be loaded.")
                st.stop()
            fc = build_forecast_visuals(df_issues, periods=periods)

        if fc["error_message"]:
            st.warning(f"⚠️ {fc['error_message']}")
        else:
            # ── KPI row ───────────────────────────────────────────────────────
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Forecast Periods", f"{periods} mo")
            c2.metric("MAE", f"{fc['mae']:.1f} tickets")
            c3.metric("MSE", f"{fc['mse']:.1f}")
            c4.metric(
                f"Forecast Low ({periods} mo total)",
                f"{fc['future_low']:,.0f}",
            )
            c5.metric(
                f"Forecast High ({periods} mo total)",
                f"{fc['future_high']:,.0f}",
            )

            st.divider()
            st.plotly_chart(fc["forecast_fig"], use_container_width=True)
            st.caption(
                "🟦 Actual · 🟢 Model on test data (dotted) · 🟠 Future forecast · "
                "Shaded band = ±MAE confidence interval"
            )


# ── Distribution of Ticket's Age ─────────────────────────────────────────────────
elif selected == "📊  Distribution of Ticket's Age":
    st.title("📊 Distribution of Ticket's Age")
    st.caption("How old are the currently open tickets?")

    if build_distribution_visuals is None:
        st.error("distribution_of_tickets_report module could not be loaded.")
        st.stop()

    df_issues = st.session_state.get("jira_df_issues")
    if df_issues is None or df_issues.empty:
        st.warning("⚠️ No Jira data loaded yet. Please fetch tickets from the Overview page first.")
        st.stop()

    with st.spinner("Building distribution charts…"):
        dist = build_distribution_visuals(df_issues)

    if dist["error_message"] and dist["box_fig"] is None:
        st.error(f"❌ {dist['error_message']}")
        st.stop()

    # KPI row
    k1, k2, k3 = st.columns(3)
    k1.metric("Open Tickets Analysed", dist["open_count"])
    k2.metric("Median Age (days)", dist["median_age"])
    k3.metric("75th Percentile (days)", dist["p75_age"])

    st.plotly_chart(dist["box_fig"], use_container_width=True)

    if dist["violin_fig"] is not None:
        st.plotly_chart(dist["violin_fig"], use_container_width=True)
    elif dist["error_message"]:
        st.warning(f"Violin plot skipped: {dist['error_message']}")


# ── Distribution per Business Leader ─────────────────────────────────────────────
elif selected == "👤  Distribution per Business Leader":
    st.title("👤 Distribution of Tickets per Business Leader")
    st.caption("Ticket volume broken down by the requesting business leader / owner.")

    if build_business_leader_visuals is None:
        st.error("distribution_by_business_leader module could not be loaded.")
        st.stop()

    df_issues = st.session_state.get("jira_df_issues")
    if df_issues is None or df_issues.empty:
        st.warning("⚠️ No Jira data loaded yet. Please fetch tickets from the Overview page first.")
        st.stop()

    seed = build_business_leader_visuals(df_issues)
    if seed["error_message"] and not seed["available_months"]:
        st.error(f"❌ {seed['error_message']}")
        st.stop()

    available_months = seed["available_months"]
    default_start = seed["start_month"]
    default_end = seed["end_month"]

    c1, c2 = st.columns(2)
    with c1:
        start_month = st.selectbox(
            "Start month",
            options=available_months,
            index=available_months.index(default_start) if default_start in available_months else 0,
        )
    with c2:
        end_month = st.selectbox(
            "End month",
            options=available_months,
            index=available_months.index(default_end) if default_end in available_months else len(available_months) - 1,
        )

    with st.spinner("Building business leader distribution visuals…"):
        biz = build_business_leader_visuals(df_issues, start_month=start_month, end_month=end_month)

    if biz["error_message"] and biz["stacked_fig"] is None:
        st.error(f"❌ {biz['error_message']}")
        st.stop()

    st.caption(f"Showing tickets from **{biz['start_month']}** to **{biz['end_month']}**")

    col1, col2 = st.columns([2, 2])
    with col1:
        st.plotly_chart(biz["leader_pie_fig"], use_container_width=True)
    with col2:
        st.plotly_chart(biz["priority_pie_fig"], use_container_width=True)

    st.plotly_chart(biz["stacked_fig"], use_container_width=True)

    with st.expander("View summary table"):
        st.dataframe(biz["summary_df"], use_container_width=True)


# ── Word of the Month ─────────────────────────────────────────────────────────────
elif selected == "💬  Word of the Month":
    st.title("💬 Word of the Month")
    st.caption("Trending terms from ticket summaries and sentiment signals from ticket comments for the selected month range.")

    if build_word_of_the_month_visuals is None:
        st.error("word_of_the_month_report module could not be loaded.")
        st.stop()

    df_issues = st.session_state.get("jira_df_issues")
    if df_issues is None or df_issues.empty:
        st.warning("⚠️ No Jira data loaded yet. Please fetch tickets from the Overview page first.")
        st.stop()

    seed = build_word_of_the_month_visuals(df_issues)
    if seed["error_message"] and not seed["available_months"]:
        st.error(f"❌ {seed['error_message']}")
        st.stop()

    available_months = seed["available_months"]
    default_start = seed["start_month"]
    default_end = seed["end_month"]

    c1, c2 = st.columns(2)
    with c1:
        start_month = st.selectbox(
            "Start month",
            options=available_months,
            index=available_months.index(default_start) if default_start in available_months else 0,
        )
    with c2:
        end_month = st.selectbox(
            "End month",
            options=available_months,
            index=available_months.index(default_end) if default_end in available_months else len(available_months) - 1,
        )

    with st.spinner("Building word of the month visuals…"):
        words = build_word_of_the_month_visuals(df_issues, start_month=start_month, end_month=end_month)

    if words["error_message"] and words["bar_fig"] is None:
        st.error(f"❌ {words['error_message']}")
        st.stop()

    st.caption(f"Showing words from **{words['start_month']}** to **{words['end_month']}**")

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(words["bar_fig"], use_container_width=True)
    with col2:
        st.plotly_chart(words["treemap_fig"], use_container_width=True)

    if words.get("sentiment_fig") is not None:
        st.plotly_chart(words["sentiment_fig"], use_container_width=True)

    if words.get("wordcloud_fig") is not None:
        st.pyplot(words["wordcloud_fig"], clear_figure=True, use_container_width=True)

    st.subheader(f"🏆 Word of the Month: **{words['top_word'].upper()}**")
    st.caption(f"Appeared {words['top_frequency']} times across ticket summaries in the selected range.")

    with st.expander("View summary table"):
        st.dataframe(words["summary_df"], use_container_width=True)


# ── SLA ─────────────────────────────────────────────────────────────────────────
elif selected == "🛡️  SLA (Service Level Agreements)":
    st.title("🛡️ SLA (Service Level Agreements)")
    st.caption("Priority-based SLA performance for the last 90 days.")

    if PRIORITY_SLA_DAYS:
        sla_box_html = """
        <div style="
            border: 1px solid rgba(148, 163, 184, 0.35);
            border-radius: 14px;
            padding: 0.85rem 1rem;
            background: linear-gradient(180deg, rgba(248,250,252,0.95), rgba(241,245,249,0.95));
            margin: 0.25rem 0 1rem 0;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
        ">
            <div style="font-size: 0.92rem; font-weight: 700; color: #0f172a; margin-bottom: 0.35rem;">
                SLA reference thresholds by priority
            </div>
            <div style="font-size: 0.84rem; color: #334155; line-height: 1.55;">
                All priorities currently use <strong>90 days</strong> as the baseline reference point.<br/>
                <span style="color:#475569;">
                    Blocker: 90 · Highest: 90 · Critical: 90 · Urgent: 90 · High: 90 · Medium: 90 · Low: 90 · Lowest: 90
                </span>
            </div>
        </div>
        """
        st.markdown(sla_box_html, unsafe_allow_html=True)

    if build_sla_visuals is None:
        st.error("service_level_agreement_report module could not be loaded.")
        st.stop()

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    sla = build_sla_visuals(df_issues, time_period_days=90)

    if sla["status_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see SLA visuals.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tickets in SLA Window", f"{sla['total_tickets']:,}")
        c2.metric("Breach Rate", f"{sla['breach_rate']:.1f}%")
        c3.metric("At Risk", f"{sla['at_risk']:,}")
        c4.metric("Median Elapsed Days", f"{sla['median_elapsed_days']:.1f}")

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(sla["status_fig"], use_container_width=True)
        with col2:
            st.plotly_chart(sla["priority_fig"], use_container_width=True)

        st.plotly_chart(sla["box_fig"], use_container_width=True)

        col3, col4 = st.columns(2)
        with col3:
            st.plotly_chart(sla["heatmap_fig"], use_container_width=True)
        with col4:
            st.plotly_chart(sla["trend_fig"], use_container_width=True)

        st.subheader("SLA Detail")
        st.dataframe(
            sla["detail_df"],
            use_container_width=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )

        if isinstance(sla.get("breached_df"), pd.DataFrame) and not sla["breached_df"].empty:
            st.subheader("Breached Tickets")
            st.dataframe(
                sla["breached_df"],
                use_container_width=True,
                column_config={
                    "Ticket": st.column_config.LinkColumn(
                        "Ticket",
                        help="Open Jira ticket",
                        display_text=r".*/([^/]+)$",
                    )
                },
            )

        if sla.get("scatter_fig") is not None:
            st.subheader("Breached Ticket Distribution by Assignee and Business Lead")
            st.plotly_chart(sla["scatter_fig"], use_container_width=True)
