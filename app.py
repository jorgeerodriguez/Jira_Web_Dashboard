# Python version 3.15.5

from datetime import date, timedelta, datetime
import random

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data.metrics import load_metrics

from config.validate_and_connect_to_jira import validate_jira_connection
from data.fetch_all_tickets_for_devops import fetch_all_tickets_for_project
from reports.tickets_distribution import plot_ticket_distribution
from data.build_dataframe_new import build_issues_dataframe
from reports.tickets_older_than_90_days import build_tickets_older_than_90_days_visuals
from reports.executive_summary import render_executive_summary
from reports.capacity_report import build_capacity_visuals
from reports.velocity_report import build_velocity_visuals, PE_TEAM_MEMBERS
from reports.trend_report import build_trend_visuals
from reports.in_progress_report import build_in_progress_visuals
from reports.validating_report import build_validating_visuals
from reports.backlog_report import build_backlog_visuals
from reports.blocked_report import build_blocked_visuals
try:
    from reports.forecast_report import build_forecast_visuals
except ImportError:
    build_forecast_visuals = None
try:
    from reports.distribution_of_tickets_report import build_distribution_visuals
except ImportError:
    build_distribution_visuals = None
try:
    from reports.distribution_by_business_leader import build_business_leader_visuals
except ImportError:
    build_business_leader_visuals = None
try:
    from reports.word_of_the_month_report import build_word_of_the_month_visuals
except (ImportError, OSError):
    build_word_of_the_month_visuals = None
try:
    from reports.service_level_agreement_report import build_sla_visuals, PRIORITY_SLA_DAYS
except ImportError:
    build_sla_visuals = None
    PRIORITY_SLA_DAYS = {}
try:
    from reports.probability_completion_report import (
        build_completion_on_time_model,
        predict_completion_probability,
        build_probability_curve,
        build_probability_training_detail_table,
        build_probability_training_distribution_figures,
    )
except ImportError:
    build_completion_on_time_model = None
    predict_completion_probability = None
    build_probability_curve = None
    build_probability_training_detail_table = None
    build_probability_training_distribution_figures = None

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
        st.caption("✅ Jira connection validated successfully.")
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
        "🎯  Probability of completion on time",
        "🧑‍💼  Personal Dashboard",
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


JIRA_BROWSE_BASE_URL = "https://entercomdigitalservices.atlassian.net/browse/"


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col

    def _norm(value: str) -> str:
        return str(value).strip().casefold().replace("_", " ").replace("-", " ")

    normalized_columns = {_norm(col): col for col in df.columns}
    for col in candidates:
        found = normalized_columns.get(_norm(col))
        if found is not None:
            return found
    return None


def _fmt_date(series: pd.Series) -> pd.Series:
    return series.dt.strftime("%Y-%m-%d").fillna("")


def _normalize_text(value: str) -> str:
    return str(value).strip().casefold().replace("_", " ").replace("-", " ")


def _build_personal_dashboard(df_issues: pd.DataFrame, assignee_value: str) -> dict:
    empty_payload = {
        "assigned_tickets": 0,
        "open_tickets": 0,
        "done_tickets": 0,
        "in_progress_tickets": 0,
        "on_hold_tickets": 0,
        "blocked_tickets": 0,
        "validating_tickets": 0,
        "overdue_tickets": 0,
        "due_soon_tickets": 0,
        "missing_target_tickets": 0,
        "high_priority_tickets": 0,
        "triage_tickets": 0,
        "tech_discovery_tickets": 0,
        "avg_days_old": 0.0,
        "oldest_days_old": 0.0,
        "status_fig": None,
        "priority_fig": None,
        "focus_df": pd.DataFrame(),
        "summary_df": pd.DataFrame(),
    }

    if df_issues is None or df_issues.empty:
        return empty_payload

    status_col = _pick_col(df_issues, ["status", "Status"])
    assignee_col = _pick_col(df_issues, ["assignee_name", "Assignee"])
    key_col = _pick_col(df_issues, ["key", "Key", "ticket", "Ticket"])
    issue_type_col = _pick_col(df_issues, ["issuetype", "issue_type", "Issue Type"])
    priority_col = _pick_col(df_issues, ["priority_name", "priority", "Priority"])
    lead_col = _pick_col(df_issues, ["bussiness_lead", "business_lead", "Business Lead"])
    summary_col = _pick_col(df_issues, ["summary", "Summary"])
    created_col = _pick_col(df_issues, ["created", "Created"])
    updated_col = _pick_col(df_issues, ["updated", "Updated"])
    target_end_col = _pick_col(df_issues, ["target_end_date", "project_due_date", "duedate", "Target End Date"])
    days_old_col = _pick_col(df_issues, ["days_old", "Days Old"])

    required = [status_col, assignee_col, key_col]
    if any(col is None for col in required):
        return empty_payload

    work = df_issues.copy()
    work[status_col] = work[status_col].fillna("Unknown").astype(str)
    work[assignee_col] = work[assignee_col].fillna("Unassigned").astype(str)
    if issue_type_col is not None:
        work[issue_type_col] = work[issue_type_col].fillna("Unknown").astype(str)

    selected_norm = str(assignee_value).strip().casefold()
    work = work[work[assignee_col].astype(str).str.strip().str.casefold().eq(selected_norm)].copy()
    if work.empty:
        return empty_payload

    for col in [created_col, updated_col, target_end_col]:
        if col is not None:
            work[col] = pd.to_datetime(work[col], errors="coerce", utc=True)

    today = pd.Timestamp.today(tz="UTC").normalize()
    if days_old_col is None:
        if created_col is not None:
            work["days_old"] = (today - work[created_col].dt.normalize()).dt.days
            days_old_col = "days_old"
        else:
            work["days_old"] = 0
            days_old_col = "days_old"
    else:
        work[days_old_col] = pd.to_numeric(work[days_old_col], errors="coerce").fillna(0)

    if target_end_col is not None:
        work["days_left"] = (work[target_end_col].dt.normalize() - today).dt.days
    else:
        work["days_left"] = pd.NA

    status_norm = work[status_col].astype(str).map(_normalize_text)
    allowed_statuses = {
        "triage",
        "to do",
        "in progress",
        "on hold",
        "validating",
        "tech discovery required",
        "blocked",
        "staged car",
        "stage car",
    }
    work = work[status_norm.isin(allowed_statuses)].copy()
    if work.empty:
        return empty_payload

    status_norm = work[status_col].astype(str).map(_normalize_text)
    priority_norm = (
        work[priority_col].astype(str).map(_normalize_text)
        if priority_col is not None
        else pd.Series("", index=work.index)
    )
    priority_high = {"critical", "urgent", "high"}
    open_mask = status_norm.ne("done")
    days_left_num = pd.to_numeric(work["days_left"], errors="coerce")
    overdue_mask = open_mask & days_left_num.lt(0)
    due_soon_mask = open_mask & days_left_num.between(0, 7, inclusive="both")
    missing_target_mask = open_mask & (work[target_end_col].isna() if target_end_col is not None else True)
    high_priority_mask = open_mask & priority_norm.isin(priority_high)

    conditions = [
        status_norm.eq("blocked"),
        status_norm.eq("on hold"),
        status_norm.eq("validating"),
        overdue_mask,
        due_soon_mask,
        missing_target_mask,
        high_priority_mask,
        status_norm.eq("in progress"),
        status_norm.eq("done"),
    ]
    choices = [
        "Blocked",
        "On Hold",
        "Validating",
        "Overdue",
        "Due Soon",
        "Missing Target",
        "High Priority",
        "In Progress",
        "Done",
    ]
    work["Attention"] = np.select(conditions, choices, default=work[status_col].astype(str))

    attention_rank = {
        "Blocked": 0,
        "On Hold": 1,
        "Validating": 2,
        "Overdue": 3,
        "Due Soon": 4,
        "Missing Target": 5,
        "High Priority": 6,
        "In Progress": 7,
        "Done": 9,
    }
    priority_rank = {
        "critical": 0,
        "urgent": 1,
        "high": 2,
        "medium": 3,
        "low": 4,
        "no priority": 5,
    }
    work["_attention_rank"] = work["Attention"].map(attention_rank).fillna(8)
    work["_priority_rank"] = priority_norm.map(priority_rank).fillna(6) if priority_col is not None else 6

    if key_col is None:
        work["key"] = work.index.astype(str)
        key_col = "key"
    if priority_col is None:
        work["priority_name"] = "Unknown"
        priority_col = "priority_name"
    if lead_col is None:
        work["bussiness_lead"] = "Unknown"
        lead_col = "bussiness_lead"
    if summary_col is None:
        work["summary"] = ""
        summary_col = "summary"

    work["Ticket"] = work[key_col].astype(str).apply(lambda ticket: f"{JIRA_BROWSE_BASE_URL}{ticket}")
    work["Status"] = work[status_col].astype(str)
    work["Priority"] = work[priority_col].astype(str)
    work["Business Lead"] = work[lead_col].astype(str)
    work["Summary"] = work[summary_col].astype(str)
    work["Days Old"] = pd.to_numeric(work[days_old_col], errors="coerce").fillna(0)
    work["Target End Date"] = _fmt_date(work[target_end_col]) if target_end_col is not None else ""
    work["Updated Date"] = _fmt_date(work[updated_col]) if updated_col is not None else ""
    work["Created Date"] = _fmt_date(work[created_col]) if created_col is not None else ""
    work["Days Left"] = pd.to_numeric(work["days_left"], errors="coerce").fillna(pd.NA)

    issue_type_norm = (
        work[issue_type_col].astype(str).map(_normalize_text)
        if issue_type_col is not None
        else pd.Series("", index=work.index)
    )
    feature_mask = issue_type_norm.eq("feature")

    total_assigned = int(len(work))
    done_tickets = int(status_norm.eq("done").sum())
    open_tickets = int(open_mask.sum())
    in_progress_tickets = int(status_norm.eq("in progress").sum())
    on_hold_tickets = int(status_norm.eq("on hold").sum())
    blocked_tickets = int(status_norm.eq("blocked").sum())
    validating_tickets = int(status_norm.eq("validating").sum())
    overdue_tickets = int(overdue_mask.sum())
    due_soon_tickets = int(due_soon_mask.sum())
    missing_target_tickets = int(missing_target_mask.sum())
    high_priority_tickets = int(high_priority_mask.sum())
    triage_tickets = int(status_norm.eq("triage").sum())
    tech_discovery_tickets = int(status_norm.eq("tech discovery required").sum())
    avg_days_old = float(pd.to_numeric(work["Days Old"], errors="coerce").mean()) if total_assigned else 0.0
    oldest_days_old = float(pd.to_numeric(work["Days Old"], errors="coerce").max()) if total_assigned else 0.0

    status_counts = work["Status"].value_counts(dropna=False).reset_index()
    status_counts.columns = ["Status", "Count"]
    priority_counts = work["Priority"].value_counts(dropna=False).reset_index()
    priority_counts.columns = ["Priority", "Count"]

    status_fig = px.bar(
        status_counts,
        x="Status",
        y="Count",
        text="Count",
        title=f"Status Distribution for {assignee_value}",
        color="Count",
        color_continuous_scale="Blues",
    )
    status_fig.update_layout(height=340, xaxis_title="Status", yaxis_title="Count")

    priority_fig = px.bar(
        priority_counts,
        x="Priority",
        y="Count",
        text="Count",
        title="Priority Mix",
        color="Count",
        color_continuous_scale="Viridis",
    )
    priority_fig.update_layout(height=340, xaxis_title="Priority", yaxis_title="Count")

    focus_df = work[open_mask & ~feature_mask].copy()
    focus_df = focus_df.sort_values(
        by=["_attention_rank", "Days Left", "_priority_rank", "Days Old"],
        ascending=[True, True, True, False],
    )
    focus_df = focus_df[["Ticket", "Status", "Priority", "Attention", "Days Left", "Days Old", "Business Lead", "Summary"]].head(15).copy()

    summary_df = work[feature_mask].copy()
    summary_df = summary_df.sort_values(
        by=["_attention_rank", "Days Left", "_priority_rank", "Days Old"],
        ascending=[True, True, True, False],
    )[["Ticket", "Status", "Priority", "Attention", "Days Left", "Days Old", "Business Lead", "Summary"]].copy()

    return {
        "assigned_tickets": total_assigned,
        "open_tickets": open_tickets,
        "done_tickets": done_tickets,
        "in_progress_tickets": in_progress_tickets,
        "on_hold_tickets": on_hold_tickets,
        "blocked_tickets": blocked_tickets,
        "validating_tickets": validating_tickets,
        "overdue_tickets": overdue_tickets,
        "due_soon_tickets": due_soon_tickets,
        "missing_target_tickets": missing_target_tickets,
        "high_priority_tickets": high_priority_tickets,
        "triage_tickets": triage_tickets,
        "tech_discovery_tickets": tech_discovery_tickets,
        "avg_days_old": avg_days_old,
        "oldest_days_old": oldest_days_old,
        "status_fig": status_fig,
        "priority_fig": priority_fig,
        "focus_df": focus_df,
        "summary_df": summary_df,
    }


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
            st.plotly_chart(dist_fig, width="stretch")
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
                st.plotly_chart(fig, width="stretch")

            with col_b:
                st.subheader("Daily Throughput (mock)")
                trend_df = pd.DataFrame(metrics["trend"])
                trend_df["day"] = pd.to_datetime(trend_df["day"])
                trend_fig = px.line(trend_df, x="day", y=["created", "resolved"], markers=True)
                trend_fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(trend_fig, width="stretch")


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
            st.plotly_chart(visuals["hist_fig"], width="stretch")

        with col2:
            st.plotly_chart(visuals["pie_fig"], width="stretch")

        st.subheader("Top 25 Oldest Open Tickets")
        st.plotly_chart(visuals["top25_fig"], width="stretch")

        st.subheader("Stale Ticket Details")
        st.dataframe(
            visuals["details_df"],
            width="stretch",
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
            st.plotly_chart(cap["yearly_total_fig"], width="stretch")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Avg Created / month", f"{cap['avg_created']:.1f}")
        c2.metric("Avg Completed / month", f"{cap['avg_completed']:.1f}")
        c3.metric("Latest Created", f"{cap['latest_created']:,}")
        c4.metric("Latest Completed", f"{cap['latest_completed']:,}")

        st.divider()
        st.plotly_chart(cap["capacity_fig"], width="stretch")
        st.subheader("Capacity Monthly Detail")
        st.dataframe(cap["capacity_table"], width="stretch")


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
            st.plotly_chart(tr["flow_fig"], width="stretch")
        if tr.get("cycle_fig") is not None:
            st.plotly_chart(tr["cycle_fig"], width="stretch")
        if tr["status_mix_fig"] is not None:
            st.plotly_chart(tr["status_mix_fig"], width="stretch")
        st.subheader("Trend Monthly Detail")
        st.dataframe(tr["table_df"], width="stretch")


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
        st.plotly_chart(vel["box_fig"], width="stretch")

        h1, h2 = st.columns(2)
        with h1:
            if vel["heat_exec_fig"] is not None:
                st.plotly_chart(vel["heat_exec_fig"], width="stretch")
        with h2:
            if vel["heat_backlog_fig"] is not None:
                st.plotly_chart(vel["heat_backlog_fig"], width="stretch")

        st.plotly_chart(vel["compare_fig"], width="stretch")


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
        st.plotly_chart(ip["load_fig"], width="stretch")

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(ip["scatter_fig"], width="stretch")
        with col2:
            st.plotly_chart(ip["distribution_fig"], width="stretch")

        if ip.get("target_timeline_fig") is not None:
            st.subheader("Target End Date Timeline")
            st.plotly_chart(ip["target_timeline_fig"], width="stretch")

        st.subheader("In Progress Workload Detail")
        st.dataframe(ip["detail_df"], width="stretch")

        st.subheader("All In Progress Tickets")
        st.dataframe(
            ip["tickets_df"],
            width="stretch",
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
            st.plotly_chart(val["oldest_fig"], width="stretch")
        with col2:
            st.plotly_chart(val["risk_fig"], width="stretch")

        st.plotly_chart(val["assignee_fig"], width="stretch")
        st.subheader("Validating Ticket Detail")
        st.dataframe(
            val["detail_df"],
            width="stretch",
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
            st.plotly_chart(blocked["blocked_fig"], width="stretch")
        with col2:
            st.plotly_chart(blocked["risk_fig"], width="stretch")

        st.subheader("Blocked Ticket Detail")
        st.dataframe(
            blocked["detail_df"],
            width="stretch",
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
        st.plotly_chart(backlog["load_fig"], width="stretch")

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(backlog["scatter_fig"], width="stretch")
        with col2:
            st.plotly_chart(backlog["distribution_fig"], width="stretch")

        st.subheader("Backlog Workload Detail")
        st.dataframe(backlog["detail_df"], width="stretch")

        st.subheader("All Backlog Tickets")
        st.dataframe(
            backlog["tickets_df"],
            width="stretch",
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
    st.caption("XGBoost baseline plus advanced lag+seasonality ML forecasting to project future throughput.")

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
            st.plotly_chart(fc["forecast_fig"], width="stretch")
            st.caption(
                "🟦 Actual · 🟢 Model on test data (dotted) · 🟠 Future forecast · "
                "Shaded band = ±MAE confidence interval"
            )

            st.divider()
            st.subheader("Advanced Forecasting (Model Comparison)")

            if fc.get("ml_error"):
                st.info(f"ℹ️ {fc['ml_error']}")
            else:
                a1, a2 = st.columns(2)
                a1.metric("Best Advanced Model", fc.get("ml_best_model", "N/A"))
                a2.metric("Best Advanced MAE", f"{fc.get('ml_best_mae', 0):.1f} tickets")

                if fc.get("ml_comparison_fig") is not None:
                    st.plotly_chart(fc["ml_comparison_fig"], width="stretch")

                if fc.get("ml_future_fig") is not None:
                    st.plotly_chart(fc["ml_future_fig"], width="stretch")


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

    st.plotly_chart(dist["box_fig"], width="stretch")

    if dist["violin_fig"] is not None:
        st.plotly_chart(dist["violin_fig"], width="stretch")
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
        st.plotly_chart(biz["leader_pie_fig"], width="stretch")
    with col2:
        st.plotly_chart(biz["priority_pie_fig"], width="stretch")

    st.plotly_chart(biz["stacked_fig"], width="stretch")

    with st.expander("View summary table"):
        st.dataframe(biz["summary_df"], width="stretch")


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
        st.plotly_chart(words["bar_fig"], width="stretch")
    with col2:
        st.plotly_chart(words["treemap_fig"], width="stretch")

    if words.get("sentiment_fig") is not None:
        st.plotly_chart(words["sentiment_fig"], width="stretch")

    if words.get("wordcloud_fig") is not None:
        st.pyplot(words["wordcloud_fig"], clear_figure=True, width="stretch")

    st.subheader(f"🏆 Word of the Month: **{words['top_word'].upper()}**")
    st.caption(f"Appeared {words['top_frequency']} times across ticket summaries in the selected range.")

    with st.expander("View summary table"):
        st.dataframe(words["summary_df"], width="stretch")


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
            st.plotly_chart(sla["status_fig"], width="stretch")
        with col2:
            st.plotly_chart(sla["priority_fig"], width="stretch")

        st.plotly_chart(sla["box_fig"], width="stretch")

        col3, col4 = st.columns(2)
        with col3:
            st.plotly_chart(sla["heatmap_fig"], width="stretch")
        with col4:
            st.plotly_chart(sla["trend_fig"], width="stretch")

        st.subheader("SLA Detail")
        st.dataframe(
            sla["detail_df"],
            width="stretch",
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
                width="stretch",
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
            st.plotly_chart(sla["scatter_fig"], width="stretch")


# ── Probability of completion on time ─────────────────────────────────────────
elif selected == "🎯  Probability of completion on time":
    st.title("🎯 Probability of completion on time")
    st.caption(
        "AI/ML prediction using a tree-based classifier trained on the last 90 days, "
        "assignee velocity by priority, and backlog pressure signals."
    )

    if (
        build_completion_on_time_model is None
        or predict_completion_probability is None
        or build_probability_curve is None
        or build_probability_training_detail_table is None
        or build_probability_training_distribution_figures is None
    ):
        st.error("probability_completion_report module could not be loaded.")
        st.stop()

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    if df_issues is None or (isinstance(df_issues, pd.DataFrame) and df_issues.empty):
        st.info("📥 Fetch Jira tickets from the sidebar to run on-time completion probability.")
        st.stop()

    with st.spinner("Training model using last 90 days of Done tickets and backlog signals…"):
        prob_payload = build_completion_on_time_model(df_issues, lookback_days=90)

    if prob_payload.get("error_message"):
        st.warning(f"⚠️ {prob_payload['error_message']}")
        st.stop()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Training rows (last 90d)", f"{prob_payload['training_rows']:,}")
    k2.metric("Historical on-time rate", f"{prob_payload['on_time_rate'] * 100:.1f}%")
    k3.metric("Training accuracy", f"{prob_payload['training_accuracy'] * 100:.1f}%")
    k4.metric("Average validation time", f"{prob_payload['average_validation_days']:.1f} days")

    if prob_payload.get("model_name"):
        st.caption(f"Selected model: {prob_payload['model_name']}")

    if prob_payload.get("accuracy_target_met"):
        st.success("Training accuracy target met (≥ 90%).")
    else:
        st.info("Training accuracy target of 90% was not reached; the app is using the best available fitted model.")

    pr_col, as_col, dt_col = st.columns(3)
    with pr_col:
        priority_value = st.selectbox(
            "Priority",
            options=prob_payload["priority_options"],
            index=0 if prob_payload["priority_options"] else None,
        )
    with as_col:
        assignee_value = st.selectbox(
            "Assignee",
            options=prob_payload["assignee_options"],
            index=0 if prob_payload["assignee_options"] else None,
        )
    with dt_col:
        expected_date = st.date_input(
            "Expected completion date",
            value=date.today() + timedelta(days=30),
            min_value=date.today(),
        )

    if not priority_value or not assignee_value:
        st.info("Select a priority and assignee to score completion probability.")
        st.stop()

    prediction = predict_completion_probability(
        prob_payload["model_bundle"],
        priority_value=priority_value,
        assignee_value=assignee_value,
        expected_completion_date=expected_date,
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("On-time Probability", f"{prediction['probability'] * 100:.1f}%")
    c2.metric("Confidence Band", prediction["risk_band"])
    c3.metric("Days Until Target", f"{prediction['budget_days']}")

    g1, g2 = st.columns([1, 2])
    with g1:
        st.plotly_chart(prediction["gauge_fig"], width="stretch")
    with g2:
        curve_fig = build_probability_curve(
            prob_payload["model_bundle"],
            priority_value=priority_value,
            assignee_value=assignee_value,
            start_date=date.today(),
            horizon_days=120,
        )
        st.plotly_chart(curve_fig, width="stretch")

    with st.expander("Model feature snapshot"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Priority": priority_value,
                        "Assignee": assignee_value,
                        "Expected Completion Date": expected_date,
                        "Assignee Velocity (90d)": round(prediction["assignee_velocity_90"], 2),
                        "Priority Velocity (90d)": round(prediction["priority_velocity_90"], 2),
                        "Assignee Historical On-Time Rate": f"{prediction['assignee_on_time_rate_90'] * 100:.1f}%",
                        "Priority Historical On-Time Rate": f"{prediction['priority_on_time_rate_90'] * 100:.1f}%",
                        "Assignee Open Backlog": int(prediction["assignee_backlog_open"]),
                        "Assignee Priority Backlog": int(prediction["assignee_priority_backlog"]),
                    }
                ]
            ),
            width="stretch",
        )

    st.subheader("Learning Opportunities from Recent Completed Tickets")
    st.caption(
        "Done tickets from the last 90 days for the selected assignee, using Updated date as Completed Date and the selected validation-time offset for the On Time flag."
    )

    detail_df = build_probability_training_detail_table(
        df_issues,
        lookback_days=90,
        assignee_filter=assignee_value,
        priority_filter=priority_value,
    )

    if detail_df.empty:
        st.info("No qualifying Done tickets found for the selected assignee in the last 90 days.")
    else:
        st.dataframe(
            detail_df,
            width="stretch",
            column_config={
                "Ticket No": st.column_config.LinkColumn(
                    "Ticket No",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )

        charts = build_probability_training_distribution_figures(detail_df)
        ch1, ch2 = st.columns(2)
        with ch1:
            st.plotly_chart(charts["on_time_fig"], width="stretch")
        with ch2:
            st.plotly_chart(charts["past_due_fig"], width="stretch")


# ── Personal Dashboard ─────────────────────────────────────────────────────────
elif selected == "🧑‍💼  Personal Dashboard":
    st.title("🧑‍💼 Personal Dashboard")
    st.caption(
        "A focused view for one PE assignee with active work, risk signals, and a Jira-linked summary table."
    )

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    if df_issues is None or (isinstance(df_issues, pd.DataFrame) and df_issues.empty):
        st.info("📥 Fetch Jira tickets from the sidebar to build the personal dashboard.")
        st.stop()

    assignee_options = []
    seen = set()
    for member in PE_TEAM_MEMBERS:
        normalized = str(member).strip().casefold()
        if normalized == "unassigned" or normalized in seen:
            continue
        seen.add(normalized)
        assignee_options.append(member)

    if not assignee_options:
        st.warning("No assignee options available.")
        st.stop()

    selected_assignee = st.selectbox(
        "Select assignee",
        options=assignee_options,
        index=0,
        key="personal_dashboard_assignee",
    )

    personal = _build_personal_dashboard(df_issues, selected_assignee)
    if personal["assigned_tickets"] == 0:
        st.info("No tickets were found for the selected assignee.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Assigned", f"{personal['assigned_tickets']:,}")
    c2.metric("Open", f"{personal['open_tickets']:,}")
    c3.metric("Overdue", f"{personal['overdue_tickets']:,}")
    c4.metric("Due Soon", f"{personal['due_soon_tickets']:,}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("In Progress", f"{personal['in_progress_tickets']:,}")
    c6.metric("On Hold", f"{personal['on_hold_tickets']:,}")
    c7.metric("Blocked", f"{personal['blocked_tickets']:,}")
    c8.metric("Validating", f"{personal['validating_tickets']:,}")

    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Triage", f"{personal['triage_tickets']:,}")
    c10.metric("Tech Discovery", f"{personal['tech_discovery_tickets']:,}")
    c11.metric("High Priority", f"{personal['high_priority_tickets']:,}")
    c12.metric("Avg Days Old", f"{personal['avg_days_old']:.1f}")

    st.caption(
        "Only tickets in Triage, To Do, In Progress, On Hold, Validating, Tech Discovery Required, Blocked, and Staged CAR are shown. "
        "Feature tickets are reserved for the Epic Ticket Only table. Prioritized by status risk, then target date, then ticket age."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        if personal["status_fig"] is not None:
            st.plotly_chart(personal["status_fig"], width="stretch")
    with col_b:
        if personal["priority_fig"] is not None:
            st.plotly_chart(personal["priority_fig"], width="stretch")

    st.subheader("Tickets Requiring Attention")
    if personal["focus_df"].empty:
        st.success("No active tickets need immediate attention for this assignee.")
    else:
        st.dataframe(
            personal["focus_df"],
            width="stretch",
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )

    st.subheader("Epic Ticket Only")
    st.dataframe(
        personal["summary_df"],
        width="stretch",
        column_config={
            "Ticket": st.column_config.LinkColumn(
                "Ticket",
                help="Open Jira ticket",
                display_text=r".*/([^/]+)$",
            )
        },
    )
