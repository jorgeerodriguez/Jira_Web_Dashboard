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
from data.fetch_change_history import fetch_change_history
from reports.tickets_older_than_90_days import build_tickets_older_than_90_days_visuals
from reports.executive_summary import render_executive_summary
from reports.atc_sequence import build_atc_sequence as _build_atc_sequence
from reports.capacity_report import build_capacity_visuals
from reports.forecast_report import weeks_to_deliver as forecast_weeks_to_deliver
from reports.velocity_report import MIN_CELL as VELOCITY_MIN_CELL, PE_TEAM_MEMBERS, build_velocity_visuals
from reports.trend_report import CORE_MIN_DELIVERED as TREND_CORE_MIN, build_trend_visuals
from reports.in_progress_report import RISK_BASES as IN_PROGRESS_RISK_BASES, build_in_progress_visuals
from reports.validating_report import build_validating_visuals
from reports.assignment_report import build_assignment_visuals
from reports.welcome import WELCOME, render_welcome
from reports.backlog_report import (
    START_TYPICAL_MISS_BD as BACKLOG_START_MISS,
    RISK_BASES as BACKLOG_RISK_BASES,
    SIMULATIONS as BACKLOG_SIMULATIONS,
    build_backlog_visuals,
)
from reports.blocked_report import build_blocked_visuals
from reports.estimated_size_distribution_report import (
    MIN_GROUP as SIZE_MIN_GROUP,
    OUTCOME_DAYS as SIZE_OUTCOME_DAYS,
    RECENT_DAYS as SIZE_RECENT_DAYS,
    build_estimated_size_distribution_visuals,
)
try:
    from reports.forecast_report import ACCURACY_HORIZON as FORECAST_ACCURACY_HORIZON, build_forecast_visuals
except ImportError:
    build_forecast_visuals = None
try:
    from reports.distribution_of_tickets_report import (
        SILENT_THRESHOLD_BD as DIST_SILENT_THRESHOLD_BD,
        build_distribution_visuals,
    )
except ImportError:
    build_distribution_visuals = None
try:
    from reports.distribution_by_business_leader import (
        SMALL_SAMPLE as BIZ_SMALL_SAMPLE,
        build_business_leader_visuals,
    )
except ImportError:
    build_business_leader_visuals = None
try:
    from reports.word_of_the_month_report import build_word_of_the_month_visuals
except (ImportError, OSError):
    build_word_of_the_month_visuals = None
try:
    from reports.service_level_agreement_report import (
        DEFAULT_WINDOW as SLA_DEFAULT_WINDOW,
        DUE_SOON_BD as SLA_DUE_SOON_BD,
        HEATMAP_DAYS as SLA_HEATMAP_DAYS,
        MIN_CELL as SLA_MIN_CELL,
        WINDOWS as SLA_WINDOWS,
        build_sla_visuals,
    )
except ImportError:
    build_sla_visuals = None
try:
    from reports.probability_completion_report import (
        build_completion_on_time_model,
        predict_completion_probability,
        build_probability_curve,
        build_probability_training_detail_table,
        build_probability_training_distribution_figures,
        build_probability_trend_figure,
    )
except ImportError:
    build_completion_on_time_model = None
    predict_completion_probability = None
    build_probability_curve = None
    build_probability_training_detail_table = None
    build_probability_training_distribution_figures = None
    build_probability_trend_figure = None

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
    "selected_menu": WELCOME,  # Default to the Welcome page
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
                "jira_change_history": pd.DataFrame(),
            })
        else:
            code, message, total_count, _sc = fetch_all_tickets_for_project(
                jira_connector=jira_connector, project_key="DEVOPS"
            )
            # Build dataframe if fetch succeeded
            df_issues = pd.DataFrame()
            history = pd.DataFrame()
            if code == 0:
                df_issues = build_issues_dataframe(jira_connector, projects=("DEVOPS", "CAR"))
                # Status and Target date history (Trend, SLA, Validating); empty if it cannot be loaded.
                with st.spinner("Loading change history…"):
                    history = fetch_change_history(jira_connector, df_issues)

            st.session_state.update({
                "jira_change_history": history,
                "jira_fetched_at": datetime.now() if code == 0 else None,
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
        _hist = st.session_state.get("jira_change_history")
        if isinstance(_hist, pd.DataFrame) and not _hist.empty:
            st.caption(f"🕘 Change history: {len(_hist):,} status and Target date changes")
        else:
            st.caption("🕘 Change history not loaded: history-based views are hidden")
    elif st.session_state["jira_fetch_code"] == 1:
        st.caption(f"❌ 0 records — {st.session_state['jira_fetch_message']}")

    st.divider()

    # Navigation menu
    MENU_ITEMS = [
        WELCOME,
        "📋  Executive Summary",
        "🏠  Overview",
        "📅  Tickets Older Than 90 Days",
        "📈  Capacity",
        "📉  Trend",
        "⚡  Velocity",
        "🔄  In Progress",
        "✅  Validating",
        "🚧  Blocked & On Hold",
        "🗂️  Backlog",
        "🧭  Suggested Assignments",
        "🔮  Forecast",
        "📊  Distribution of Ticket's Age",
        "👤  Distribution per Business Leader",
        "💬  Teams Conversations",
        "🛡️  SLA (Service Level Agreements)",
        "🎯  Probability of completion on time",
        "🧑‍💼  Personal Dashboard",
        "📏  Distribution of Ticket by Estimated Size",
    ]

    selected = st.radio(
        "Navigate to",
        MENU_ITEMS,
        index=MENU_ITEMS.index(st.session_state.get("selected_menu"))
        if st.session_state.get("selected_menu") in MENU_ITEMS else MENU_ITEMS.index(WELCOME),
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


def _normalize_ticket_size(series: pd.Series) -> pd.Series:
    valid_sizes = ["Small", "Medium", "Large", "XL"]
    norm = series.fillna("Unestimated").astype(str).str.strip()
    norm = norm.replace("", "Unestimated")
    return norm.where(norm.isin(valid_sizes), "Unestimated")


ATC_CAL_CODE_COLORS = {
    0: "#eef1f5",  # weekend
    1: "#fcfcfb",  # working day, nothing scheduled
    2: "#94a3b8",  # no priority
    3: "#16a34a",  # low
    4: "#facc15",  # medium
    5: "#f97316",  # high
    6: "#dc2626",  # urgent / critical
}
ATC_CAL_PRIORITY_CODE = {
    "no priority": 2,
    "low": 3,
    "medium": 4,
    "high": 5,
    "urgent": 6,
    "critical": 6,
}
ATC_CAL_LEGEND_LABELS = {2: "No Priority", 3: "Low", 4: "Medium", 5: "High", 6: "Urgent / Critical"}
ATC_CAL_MAX_WORKING_DAYS = 90  # ~18 weeks; keeps the grid readable for a large backlog


def _build_atc_calendar_fig(atc_df: pd.DataFrame, max_working_days: int = ATC_CAL_MAX_WORKING_DAYS):
    """Working-day calendar for the ATC sequence: 'Projected Start (Day)' = today (offset 0),
    each later offset the next business day. A ticket fills every working day between its
    projected start and finish, skipping weekends."""
    if atc_df is None or atc_df.empty:
        return None, False

    rows = atc_df.dropna(subset=["Projected Start (Day)", "Projected Finish (Day)"]).copy()
    if rows.empty:
        return None, False

    priority_norm = rows["Priority"].astype(str).map(_normalize_text)
    rows["_code"] = priority_norm.map(ATC_CAL_PRIORITY_CODE).fillna(2).astype(int)
    rows["_key"] = rows["Ticket"].astype(str).str.rstrip("/").str.rsplit("/", n=1).str[-1]

    slots = []
    truncated = False
    for _, r in rows.iterrows():
        start = int(round(r["Projected Start (Day)"]))
        finish = int(round(r["Projected Finish (Day)"]))
        for offset in range(start, max(finish, start + 1)):
            if offset >= max_working_days:
                truncated = True
                break
            slots.append({"offset": offset, **r.to_dict()})

    if not slots:
        return None, truncated

    offsets = sorted({s["offset"] for s in slots})
    today = np.datetime64(pd.Timestamp.today().normalize().date())
    dates = np.busday_offset(today, offsets, roll="forward")
    offset_to_date = {o: pd.Timestamp(d) for o, d in zip(offsets, dates)}
    for s in slots:
        s["date"] = offset_to_date[s["offset"]]
    slot_by_date = {s["date"].normalize(): s for s in slots}

    min_date = min(s["date"] for s in slots)
    max_date = max(s["date"] for s in slots)
    grid_start = min_date - pd.Timedelta(days=int(min_date.dayofweek))
    grid_end = max_date + pd.Timedelta(days=int(6 - max_date.dayofweek))
    all_days = pd.date_range(grid_start, grid_end, freq="D")
    num_weeks = len(all_days) // 7

    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    week_labels = [all_days[w * 7].strftime("Week of %b %d") for w in range(num_weeks)]

    z = [[0] * 7 for _ in range(num_weeks)]
    text = [[""] * 7 for _ in range(num_weeks)]
    customdata = [[""] * 7 for _ in range(num_weeks)]

    for i, day in enumerate(all_days):
        week_idx, weekday_idx = divmod(i, 7)
        is_weekend = weekday_idx >= 5
        info = slot_by_date.get(day.normalize())
        if info is not None:
            z[week_idx][weekday_idx] = info["_code"]
            text[week_idx][weekday_idx] = f"{day.day}<br><b>{info['_key']}</b>"
            customdata[week_idx][weekday_idx] = (
                f"{info['_key']} — {day.strftime('%a %b %d, %Y')}<br>"
                f"Priority: {info['Priority']} · Size: {info['Size']} · Tier: {info['Tier']}<br>"
                f"Seq #{info['Seq']} · Days Left (due): {info['Days Left']}<br>"
                f"Projected Tardiness: {info['Projected Tardiness (Days)']} days"
            )
        else:
            z[week_idx][weekday_idx] = 0 if is_weekend else 1
            text[week_idx][weekday_idx] = str(day.day)
            customdata[week_idx][weekday_idx] = day.strftime("%A, %b %d, %Y") + (
                " — weekend" if is_weekend else " — nothing scheduled"
            )

    n_codes = len(ATC_CAL_CODE_COLORS)
    colorscale = []
    for code, color in ATC_CAL_CODE_COLORS.items():
        colorscale.append([code / n_codes, color])
        colorscale.append([(code + 1) / n_codes, color])

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=weekday_labels,
            y=week_labels,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 11},
            customdata=customdata,
            hovertemplate="%{customdata}<extra></extra>",
            colorscale=colorscale,
            zmin=0,
            zmax=n_codes,
            showscale=False,
            xgap=3,
            ygap=3,
        )
    )

    present_codes = sorted({v for row in z for v in row} & set(ATC_CAL_LEGEND_LABELS))
    for code in present_codes:
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                marker={"size": 12, "color": ATC_CAL_CODE_COLORS[code], "symbol": "square"},
                name=ATC_CAL_LEGEND_LABELS[code],
                showlegend=True,
                hoverinfo="skip",
            )
        )

    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(side="top", showgrid=False)
    fig.update_layout(
        title="Suggested Working-Day Calendar",
        height=110 * num_weeks + 160,
        margin=dict(l=10, r=10, t=60, b=10),
        plot_bgcolor="#ffffff",
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="left", x=0),
    )
    return fig, truncated


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
        "atc_df": pd.DataFrame(),
        "atc_calendar_fig": None,
        "atc_calendar_truncated": False,
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
    size_col = _pick_col(df_issues, ["estimated_size_name", "Estimated Size"])

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
    work["Size"] = (
        _normalize_ticket_size(work[size_col]) if size_col is not None else "Unestimated"
    )
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

    attention_pool_df = work[open_mask & ~feature_mask].copy()
    attention_pool_df = attention_pool_df.sort_values(
        by=["_attention_rank", "Days Left", "_priority_rank", "Days Old"],
        ascending=[True, True, True, False],
    )
    focus_cols = ["Ticket", "Status", "Priority", "Size", "Attention", "Days Left", "Days Old", "Business Lead", "Summary"]
    focus_df = attention_pool_df[focus_cols].head(15).copy()

    summary_df = work[feature_mask].copy()
    summary_df = summary_df.sort_values(
        by=["_attention_rank", "Days Left", "_priority_rank", "Days Old"],
        ascending=[True, True, True, False],
    )[focus_cols].copy()

    atc_df = _build_atc_sequence(attention_pool_df[["Ticket", "Priority", "Size", "Days Left", "Days Old", "Status"]])
    atc_calendar_fig, atc_calendar_truncated = _build_atc_calendar_fig(atc_df)

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
        "atc_df": atc_df,
        "atc_calendar_fig": atc_calendar_fig,
        "atc_calendar_truncated": atc_calendar_truncated,
    }


# ── Target date updates (Backlog, safe mode) ─────────────────────────────────────
@st.dialog("✏️ Propose Target date updates", width="large")
def _target_date_dialog(backlog_payload: dict) -> None:
    from reports import jira_dates

    host = st.context.headers.get("Host", "") if hasattr(st, "context") else ""
    allowed, reason = jira_dates.write_permission(host)
    st.caption(
        "Proposals come from the Backlog forecast. Nothing is selected and nothing is written until you tick rows, "
        "review the changes and confirm. Each updated ticket gets a Jira comment, and the last batch can be undone."
    )
    if not allowed:
        st.error(f"Jira updates are not available: {reason}")
        return
    st.success(f"🔓 {reason} Each update still needs your selection and typed confirmation.")

    rule = st.radio("Propose Target start from", ["likely", "safe"], horizontal=True,
                    format_func=lambda r: "Likely start (P50)" if r == "likely" else "Safe start (85% by then)",
                    key="tdu_rule")
    proposals = jira_dates.build_proposals(backlog_payload["planning_df"], backlog_payload["today"],
                                           backlog_payload["holidays"], rule)
    if proposals.empty:
        st.success("No Target dates need updating right now.")
        return

    st.markdown(f"**{len(proposals)} tickets** have Target dates that look out of date. "
                "Tick the ones to update; you can edit the new dates.")
    edited = st.data_editor(
        proposals, hide_index=True, width="stretch", key=f"tdu_editor_{rule}",
        disabled=[c for c in proposals.columns if c not in ("Apply", "New Target Start", "New Target End")],
        column_config={
            "Apply": st.column_config.CheckboxColumn("Apply", help="Tick to include this ticket"),
            "New Target Start": st.column_config.DateColumn("New Target Start", format="YYYY-MM-DD"),
            "New Target End": st.column_config.DateColumn("New Target End", format="YYYY-MM-DD"),
            "Why": st.column_config.TextColumn("Why", width="medium"),
        },
    )
    selected = edited[edited["Apply"]]
    errors, warnings = jira_dates.validate(selected, backlog_payload["today"], backlog_payload["holidays"])
    if not selected.empty:
        st.markdown("**Dry run: these changes would be sent to Jira**")
        st.dataframe(selected[["Ticket", "Current Target Start", "New Target Start", "Current Target End",
                               "New Target End"]], hide_index=True, width="stretch")
    for message in errors:
        st.error(message)
    for message in warnings:
        st.warning(message)

    phrase = f"UPDATE {len(selected)}"
    typed = st.text_input(f"Type **{phrase}** to confirm", key="tdu_confirm", disabled=not allowed or selected.empty)
    ready = allowed and not selected.empty and not errors and typed.strip() == phrase
    if st.button(f"Update {len(selected)} ticket(s) in Jira", type="primary", disabled=not ready, key="tdu_apply"):
        jira = st.session_state.get("jira_connector")
        if jira is None:
            _, _, jira = validate_jira_connection()
            st.session_state["jira_connector"] = jira
        if jira is None:
            st.error("Could not connect to Jira.")
            return
        try:
            actor = jira.myself().get("displayName", "")
        except Exception:
            actor = ""
        with st.spinner("Updating Jira…"):
            results = jira_dates.apply_updates(jira, selected, backlog_payload["today"].date(), actor)
        st.dataframe(results, hide_index=True, width="stretch")
        updated = int((results["Result"] == "updated").sum())
        st.success(f"{updated} ticket(s) updated. Fetch Jira tickets again to see the new dates on the dashboard.")

    batch = jira_dates.last_batch()
    if not batch.empty:
        with st.expander(f"Undo last batch ({len(batch)} ticket(s), {batch['at'].iloc[0][:16].replace('T', ' ')} UTC)"):
            st.dataframe(batch[["key", "old_start", "new_start", "old_end", "new_end"]].rename(columns={
                "key": "Ticket", "old_start": "Restore Start", "new_start": "Current Start",
                "old_end": "Restore End", "new_end": "Current End"}), hide_index=True, width="stretch")
            undo_typed = st.text_input("Type **UNDO** to restore these dates", key="tdu_undo_confirm",
                                       disabled=not allowed)
            if st.button("Undo last batch", disabled=not allowed or undo_typed.strip() != "UNDO", key="tdu_undo"):
                jira = st.session_state.get("jira_connector") or validate_jira_connection()[2]
                with st.spinner("Restoring dates…"):
                    st.dataframe(jira_dates.undo_batch(jira, batch), hide_index=True, width="stretch")


# ── Assign in Jira (Suggested Assignments, safe mode) ────────────────────────────
@st.dialog("✏️ Assign tickets in Jira", width="large")
def _assign_dialog(asg_payload: dict) -> None:
    from reports import jira_assign, jira_dates

    host = st.context.headers.get("Host", "") if hasattr(st, "context") else ""
    allowed, reason = jira_dates.write_permission(host)
    st.caption(
        "Rows come from the Assignment Plan. Nothing is selected and nothing is written until you tick rows, review "
        "them and confirm. Each assigned ticket gets a Jira comment with the reason, and the last batch can be undone."
    )
    if not allowed:
        st.error(f"Jira updates are not available: {reason}")
        return
    st.success(f"🔓 {reason} Each assignment still needs your selection and typed confirmation.")

    accounts = asg_payload["account_ids"]
    rows = jira_assign.build_rows(asg_payload["plan_df"], asg_payload["current_accounts"])
    if not accounts:
        st.warning("No Jira account ids are loaded yet. Fetch Jira tickets again from the sidebar, then reopen this.")
        return
    edited = st.data_editor(
        rows.drop(columns=["Current Account"]), hide_index=True, width="stretch", key="asg_editor",
        disabled=[c for c in rows.columns if c not in ("Apply", "Assign To")],
        column_config={
            "Apply": st.column_config.CheckboxColumn("Apply", help="Tick to include this ticket"),
            "Assign To": st.column_config.SelectboxColumn("Assign To", options=sorted(accounts), required=True,
                                                          help="Defaults to the suggestion; pick someone else if needed"),
            "Why": st.column_config.TextColumn("Why", width="medium"),
        },
    )
    edited["Current Account"] = rows["Current Account"].values
    selected = edited[edited["Apply"]]
    errors, warnings = jira_assign.validate(selected, accounts)
    if not selected.empty:
        st.markdown("**Dry run: these assignments would be sent to Jira**")
        st.dataframe(selected[["Ticket", "Current Owner", "Assign To", "SLA Fit"]], hide_index=True, width="stretch")
    for message in errors:
        st.error(message)
    for message in warnings:
        st.warning(message)

    phrase = f"ASSIGN {len(selected)}"
    typed = st.text_input(f"Type **{phrase}** to confirm", key="asg_confirm", disabled=selected.empty)
    ready = not selected.empty and not errors and typed.strip() == phrase
    if st.button(f"Assign {len(selected)} ticket(s) in Jira", type="primary", disabled=not ready, key="asg_apply"):
        jira = st.session_state.get("jira_connector")
        if jira is None:
            _, _, jira = validate_jira_connection()
            st.session_state["jira_connector"] = jira
        if jira is None:
            st.error("Could not connect to Jira.")
            return
        try:
            actor = jira.myself().get("displayName", "")
        except Exception:
            actor = ""
        with st.spinner("Assigning in Jira…"):
            results = jira_assign.apply_assignments(jira, selected, accounts, date.today(), actor)
        st.dataframe(results, hide_index=True, width="stretch")
        assigned = int((results["Result"] == "updated").sum())
        st.success(f"{assigned} ticket(s) assigned. Fetch Jira tickets again to refresh the plan.")

    batch = jira_assign.last_batch()
    if not batch.empty:
        with st.expander(f"Undo last batch ({len(batch)} ticket(s), {batch['at'].iloc[0][:16].replace('T', ' ')} UTC)"):
            st.dataframe(batch[["key", "old_owner", "new_owner"]].rename(columns={
                "key": "Ticket", "old_owner": "Restore Owner", "new_owner": "Current Owner"}),
                hide_index=True, width="stretch")
            undo_typed = st.text_input("Type **UNDO** to restore the previous owners", key="asg_undo_confirm")
            if st.button("Undo last batch", disabled=undo_typed.strip() != "UNDO", key="asg_undo"):
                jira = st.session_state.get("jira_connector") or validate_jira_connection()[2]
                with st.spinner("Restoring owners…"):
                    st.dataframe(jira_assign.undo_batch(jira, batch), hide_index=True, width="stretch")


# ── Mock data helpers ───────────────────────────────────────────────────────────
metrics = load_metrics(report_date=report_date, lookback_days=lookback_days)
start_date = report_date - timedelta(days=lookback_days - 1)


# ══════════════════════════════════════════════════════════════════════════════
# VIEWS
# ══════════════════════════════════════════════════════════════════════════════

# ── Executive Summary ────────────────────────────────────────────────────────
def _go_to(page: str) -> None:
    st.session_state["selected_menu"] = page


if selected == WELCOME:
    render_welcome(st, _go_to, st.session_state.get("jira_df_issues"), st.session_state.get("jira_change_history"),
                   st.session_state.get("jira_fetched_at"))

elif selected == "📋  Executive Summary":
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
    st.caption("Open work older than 90 days, split into Epics (Feature) and Tickets.")
    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    visuals = build_tickets_older_than_90_days_visuals(df_issues)

    total_old = visuals["epics_count"] + visuals["tickets_count"]
    if total_old == 0:
        if isinstance(df_issues, pd.DataFrame) and not df_issues.empty:
            st.info("No open epics or tickets older than 90 days were found in the current dataframe.")
        else:
            st.info("📥 Fetch Jira tickets from the sidebar to see epics and tickets older than 90 days.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Epics", visuals["epics_count"])
        c2.metric("Tickets", visuals["tickets_count"])

        st.divider()
        st.subheader("Epics Older Than 90 Days")
        if visuals["epics_df"].empty:
            st.info("No epics older than 90 days.")
        else:
            st.dataframe(
                visuals["epics_df"],
                width="stretch",
                column_config={
                    "Issue": st.column_config.LinkColumn(
                        "Issue",
                        help="Open Jira issue",
                        display_text=r".*/([^/]+)$",
                    )
                },
            )

        st.subheader("Tickets Older Than 90 Days")
        if visuals["tickets_df"].empty:
            st.info("No tickets older than 90 days.")
        else:
            st.dataframe(
                visuals["tickets_df"],
                width="stretch",
                column_config={
                    "Issue": st.column_config.LinkColumn(
                        "Issue",
                        help="Open Jira issue",
                        display_text=r".*/([^/]+)$",
                    )
                },
            )


# ── Capacity ───────────────────────────────────────────────────────────────────
elif selected == "📈  Capacity":
    st.title("📈 Capacity")
    st.caption(
        "How much Platform Engineering delivers, whether it keeps up with demand, how much more it can take on, "
        "where the time goes, and how evenly the load is spread. PE tickets only; full weeks (Mon–Sun)."
    )
    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    cap = build_capacity_visuals(df_issues)

    if cap["error_message"] or cap["flow_fig"] is None:
        st.info(cap["error_message"] or "📥 Fetch Jira tickets from the sidebar to see capacity visuals.")
    else:
        kp = cap["kpis"]
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Delivered / Week", f"{kp['throughput']:.0f}",
                  f"{kp['throughput_change']:+.0%}" if kp["throughput_change"] is not None else None,
                  help="Average PE tickets moved to Done per week, last 4 full weeks vs the 4 before.")
        k2.metric("Engineers Delivering", f"{kp['engineers']:.1f}",
                  help="Average number of people who delivered at least one ticket per week, last 4 weeks.")
        k3.metric("Per Engineer / Week", f"{kp['per_engineer']:.1f}" if kp["per_engineer"] else "—")
        k4.metric("Weeks of Work Queued", f"{kp['weeks_queued']:.1f}" if kp["weeks_queued"] is not None else "—",
                  help=f"{kp['queue']} tickets in Backlog or In Progress ÷ weekly delivery.")
        k5.metric("Demand / Capacity", f"{kp['demand_ratio']:.2f}" if kp["demand_ratio"] else "—",
                  f"{kp['spare_per_week']:+.1f} tickets/week spare", delta_color="normal",
                  help="Requested ÷ delivered over the last 4 weeks. Above 1.0, demand is outrunning delivery.")

        st.subheader("Demand vs Capacity")
        st.caption("PE tickets requested vs delivered each week, last 26 full weeks. Thick lines are 4-week averages.")
        st.plotly_chart(cap["flow_fig"], width="stretch")

        st.subheader("How Much Can We Deliver?")
        st.caption("From the Delivery Forecast (same numbers as the Forecast page): the whole team's pace, shared "
                   "with everything else that keeps coming in. Likely = central forecast; at least = low end of the range.")
        fc_cols = st.columns(len(cap["forecast"]) + 1)
        for col, (weeks, fc) in zip(fc_cols, cap["forecast"].items()):
            col.metric(f"Next {weeks} weeks", f"~{fc['likely']:,} tickets",
                       help=f"Likely (P50). At least {fc['at_least']:,} in 85% of simulations.")
            col.caption(f"At least **{fc['at_least']:,}** (85% confidence)")
        with fc_cols[-1]:
            extra = st.number_input("How long for N more tickets?", min_value=1, max_value=5000, value=100, step=10,
                                    help="For example a new project's ticket count, on top of current work.")
            eta = forecast_weeks_to_deliver(int(extra), cap["forecast_table"])
            if eta:
                likely = f"{eta['likely']} weeks" if eta["likely"] else "over a year"
                safe = f"{eta['safe']} weeks" if eta["safe"] else "over a year"
                st.caption(f"With the whole team: likely **{likely}**, safely **{safe}**. Only spare capacity is "
                           "truly free, so expect longer while demand stays this high (see the Forecast page).")

        st.divider()
        m1, m2 = st.columns(2)
        with m1:
            st.subheader("Where Capacity Goes: Work Type")
            reactive = cap.get("reactive_share")
            st.caption("Planned = Story, Task, Sub-task. Reactive = Bug, Hotfix, Incident, Support, Security. "
                       + (f"Reactive work: {reactive:.0%} of the last 6 months." if reactive is not None else ""))
            st.plotly_chart(cap["type_mix_fig"], width="stretch")
        with m2:
            st.subheader("Where Capacity Goes: Priority")
            urgent = cap.get("urgent_share")
            st.caption("Share of delivered tickets by priority, last 6 months. "
                       + (f"Urgent: {urgent:.0%}." if urgent is not None else "")
                       + (f" Sized tickets (last 4 weeks): {kp['sized_share']:.0%}." if kp.get("sized_share") is not None else ""))
            st.plotly_chart(cap["priority_mix_fig"], width="stretch")

        st.divider()
        st.subheader("Load Balance")
        st.caption("For balancing work across the team, not for judging individuals. "
                   + (f"The top 3 people delivered {kp['top3_share']:.0%} of tickets in the last 8 weeks. "
                      if kp.get("top3_share") is not None else "")
                   + f"Charts show the core team ({kp.get('core_team', 0)} people); "
                   f"{kp.get('occasional', 0)} occasional contributors are in the table.")
        l1, l2 = st.columns(2)
        with l1:
            if cap["wip_fig"] is not None:
                st.plotly_chart(cap["wip_fig"], width="stretch")
        with l2:
            if cap["share_fig"] is not None:
                st.plotly_chart(cap["share_fig"], width="stretch")
        with st.expander("Per engineer"):
            st.dataframe(cap["people_df"], width="stretch", hide_index=True)
        with st.expander("Weekly detail"):
            st.dataframe(cap["weekly_df"], width="stretch", hide_index=True)


# ── Trend ──────────────────────────────────────────────────────────────────────
elif selected == "📉  Trend":
    st.title("📉 Trend")
    st.caption(
        "Are we getting better, month over month? PE tickets only, by the month they moved to Done; times in "
        "business days. Weekly flow and the delivery forecast are on the Capacity page."
    )

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    tr = build_trend_visuals(df_issues, history=st.session_state.get("jira_change_history"))

    if tr["error_message"] or tr["multiples_fig"] is None:
        st.info(tr["error_message"] or "📥 Fetch Jira tickets from the sidebar to see trend visuals.")
    else:
        st.subheader(f"Improvement Scorecard · {tr['last_full_month']}")
        st.caption("Last full month vs the average of the 3 months before. Green = moving the right way.")
        cards = tr["scorecard"]
        for row_start in range(0, len(cards), 5):
            cols = st.columns(5)
            for col, card in zip(cols, cards[row_start:row_start + 5]):
                col.metric(card["label"], card["value"], card["delta"], delta_color=card["delta_color"],
                           help=card["help"])

        st.divider()
        st.subheader("12-Month Trends")
        st.caption("Hollow marker = current month so far. Dashed lines are targets.")
        st.plotly_chart(tr["multiples_fig"], width="stretch")

        if tr["team_fig"] is not None:
            st.subheader("Team Contribution Over Time")
            st.caption(f"Delivered tickets per person per month (people with {TREND_CORE_MIN}+ delivered in the window). "
                       "For spotting ramp-ups, gaps and load, not for judging individuals.")
            st.plotly_chart(tr["team_fig"], width="stretch")

        dc = tr.get("date_changes")
        st.divider()
        st.subheader("Target Date Changes")
        if dc is None:
            st.info("Change history was not loaded with the last fetch, so Target date changes can't be shown. "
                    "Fetch Jira tickets again.")
        else:
            k = dc["kpis"]
            pct = lambda v: "—" if v is None else f"{v:.0%}"  # noqa: E731
            st.caption(f"How often Target start and Target end are moved on tickets delivered in the last {dc['days']} days "
                       f"({k['delivered']:,} tickets). A move changes one date to another; setting a date the first time "
                       "is not a move. Dates filled in when a ticket is created have no history.")
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.metric("Moves per Ticket", "—" if k["moves_per_ticket"] is None else f"{k['moves_per_ticket']:.2f}",
                      help="Target start + Target end moves, per delivered ticket.")
            d2.metric("Never Moved", pct(k["stable"]), help="Delivered tickets whose Target dates were never moved.")
            d3.metric("Moves Pushing Later", pct(k["later"]),
                      help=f"Moves that made the date later. Typical move: {k['median_shift_bd'] or 0:.0f} business days.")
            d4.metric("Moved After Date Passed", pct(k["after_passed"]),
                      help="Moves made after the old date had already passed: re-planning after the fact.")
            d5.metric("SLA Clock Set After the Fact", pct(k["clock_after_fact"]),
                      help="Delivered tickets whose Target start (the SLA clock) was set or moved on or after the day "
                           "they moved to Done. Flagged on the SLA page.")
            o1, o2 = st.columns([3, 2])
            with o1:
                st.markdown("**Outcomes by How Often Dates Moved**")
                st.caption("Association, not cause: late tickets also get re-planned more.")
                if dc["outcome_fig"] is not None:
                    st.plotly_chart(dc["outcome_fig"], width="stretch")
            with o2:
                st.markdown("**Moves per Ticket by Priority and Size**")
                st.caption("Delivered tickets; bigger work is re-planned more.")
                if dc["heatmap_fig"] is not None:
                    st.plotly_chart(dc["heatmap_fig"], width="stretch")
            st.markdown("**Most Re-planned Open Tickets**")
            if dc["replanned_df"].empty:
                st.success("No open ticket has had its dates moved twice or more.")
            else:
                st.caption("Open tickets whose Target dates moved 2+ times: often a sign the work needs splitting, "
                           "unblocking or a realistic plan.")
                st.dataframe(dc["replanned_df"], width="stretch", hide_index=True, column_config={
                    "Ticket": st.column_config.LinkColumn("Ticket", help="Open in Jira", display_text=r".*/([^/]+)$")})

        with st.expander("Monthly detail"):
            st.dataframe(tr["monthly_df"], width="stretch", hide_index=True)


# ── Velocity ───────────────────────────────────────────────────────────────────
elif selected == "⚡  Velocity":
    st.title("⚡ Velocity")
    st.caption(
        "How fast work flows from request to done, and what slows it. PE tickets completed in the last 90 days "
        "(picked by completion date, so slow tickets count too); times in business days. "
        "Speed per person and priority is on the In Progress page."
    )
    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    vel = build_velocity_visuals(df_issues, time_period_days=90)

    if vel["error_message"] or vel["promise_fig"] is None:
        st.info(vel["error_message"] or "📥 Fetch Jira tickets from the sidebar to see velocity visuals.")
    else:
        kp = vel["kpis"]
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Delivered (90d)", f"{kp['delivered']:,}")
        k2.metric("Lead Time · 50%", f"≤ {kp['lead_p50']:.0f} bd", help="Half of requests were done within this.")
        k3.metric("Lead Time · 85%", f"≤ {kp['lead_p85']:.0f} bd",
                  help=f"85% of requests were done within this; 95% within {kp['lead_p95']:.0f} business days.")
        k4.metric("In Progress (median)", f"{kp['work_median']:.0f} bd" if kp["work_median"] is not None else "—",
                  help="Target start → Done.")
        k5.metric("Flow Efficiency", f"{kp['flow_efficiency']:.0%}" if kp["flow_efficiency"] is not None else "—",
                  help="Share of total lead time spent after Target start; the rest is waiting to start.")

        st.subheader("What We Can Promise a Requester")
        st.caption(f"Lead time (created → Done) of every ticket delivered in the last 90 days: half within "
                   f"{kp['lead_p50']:.0f}, 85% within {kp['lead_p85']:.0f} and 95% within {kp['lead_p95']:.0f} business days.")
        st.plotly_chart(vel["promise_fig"], width="stretch")

        v1, v2 = st.columns(2)
        with v1:
            st.subheader("Waiting vs In Progress, by Priority")
            st.caption("Share of total lead time spent waiting to start vs after Target start.")
            st.plotly_chart(vel["wait_work_fig"], width="stretch")
        with v2:
            st.subheader("Do Sizes Predict Effort?")
            st.caption(f"Time in progress by estimated size. {kp['sized_share']:.0%} of delivered tickets were sized.")
            st.plotly_chart(vel["size_fig"], width="stretch")

        st.subheader("SLA Reality Check")
        st.caption("For each Priority × Size: how long 85% of tickets took in progress, as a share of that cell's SLA. "
                   "Orange (over 100%) = the SLA is tighter than what usually happens; blue (well under) = the SLA is "
                   f"loose. * = fewer than {VELOCITY_MIN_CELL} tickets, read with care.")
        st.plotly_chart(vel["sla_reality_fig"], width="stretch")

        d1, d2 = st.columns([3, 2])
        with d1:
            st.subheader("What We Delivered")
            st.caption("Tickets delivered in the last 90 days by priority and size.")
            st.plotly_chart(vel["delivered_fig"], width="stretch")
        with d2:
            st.subheader("By Priority")
            st.dataframe(vel["priority_df"], width="stretch", hide_index=True)
            with st.expander("SLA reality check, as a table"):
                st.dataframe(vel["sla_reality_df"], width="stretch", hide_index=True)


# ── In Progress ─────────────────────────────────────────────────────────────────
elif selected == "🔄  In Progress":
    st.title("🔄 In Progress")
    st.caption(
        "When each in-progress ticket (Features and Initiatives excluded) is forecast to finish, "
        "against its SLA and Target End Date. SLAs are in business days."
    )

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    risk_basis = st.radio(
        "Judge risk against",
        IN_PROGRESS_RISK_BASES,
        horizontal=True,
        help="The KPIs and charts use this deadline. The detail table always shows both the SLA and Target End status.",
    )
    ip = build_in_progress_visuals(df_issues, risk_basis=risk_basis)

    if ip["timeline_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see In Progress visuals.")
    else:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total In Progress", f"{ip['total_in_progress']:,}")
        c2.metric("✓ On Track", f"{ip['on_track']}")
        c3.metric("! At Risk / Likely Late", f"{ip['at_risk']}")
        c4.metric("✖ Breached", f"{ip['breached']}")
        c5.metric("All Done by (P85)", ip["all_done_p85"].strftime("%b %d") if ip["all_done_p85"] else "—")
        c6.metric("Unsized (Medium assumed)", f"{ip['unsized']}")

        with st.expander("How the forecast works"):
            st.markdown(
                "- **SLA clock** starts at the Target start date and counts business days "
                "(weekends and company holidays excluded), using the Priority × Size SLA matrix. "
                "Unsized tickets use the Medium SLA.\n"
                "- **Execution velocity** is learned from the last year of Done tickets (Target start → Done), "
                "weighting recent work more. Each assignee's speed per priority is blended with the team "
                "baseline, so people with little history lean on the team.\n"
                "- **Time already spent** is taken into account: only comparable tickets that ran at least as "
                "long as this one inform how much is left.\n"
                "- **Current load**: carrying more tickets than usual stretches the forecast (Load Factor).\n"
                "- **P50** is the likely finish date; **P85** is the safe date to commit to.\n"
                "- **Risk** compares the forecast with the deadline chosen above (SLA due date, Target End Date, "
                "or whichever is earlier): ✖ Breached (deadline passed) · ▲ Likely Late (P50 after deadline) · "
                "! At Risk (P85 after deadline) · ✓ On Track.\n"
                "- Back-tested on past tickets: about 6 in 10 finished by P50 and 9 in 10 by P85."
            )

        st.divider()
        st.subheader("Completion Forecast")
        st.caption("Bar: Target start → P85 date, coloured by risk. ● P50 · ◇ SLA due · ✕ Target End Date.")
        st.plotly_chart(ip["timeline_fig"], width="stretch")

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Risk by Assignee")
            st.plotly_chart(ip["assignee_risk_fig"], width="stretch")
        with col2:
            st.subheader("In Progress by SLA Cell")
            st.caption("Tickets per Priority × Size, with each cell's SLA.")
            st.plotly_chart(ip["sla_grid_fig"], width="stretch")

        if ip.get("velocity_fig") is not None:
            st.subheader("Execution Velocity by Priority per Assignee")
            st.caption("Typical business days from Target start to Done, learned from the last year of Done tickets.")
            st.plotly_chart(ip["velocity_fig"], width="stretch")

        st.subheader("In Progress Forecast Detail")
        st.dataframe(
            ip["forecast_df"],
            width="stretch",
            hide_index=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                ),
                "SLA Used %": st.column_config.ProgressColumn(
                    "SLA Used %", format="%d%%", min_value=0, max_value=100,
                ),
            },
        )

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
    st.caption(
        "Work that is finished and waiting for the requester to confirm it. PE tickets only; times in business days. "
        "The SLA clock keeps running while a ticket waits here."
    )

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    val = build_validating_visuals(df_issues, history=st.session_state.get("jira_change_history"))

    if val["error_message"]:
        st.info(val["error_message"])
    else:
        kv = val["kpis"]

        def _bd(v):
            return "—" if v is None else f"{v:.1f} bd"

        def _delta_bd(now, before):
            return None if now is None or before is None else f"{now - before:+.1f} bd vs previous {kv['window_days']}d"

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("In Validating Now", f"{kv['in_validating']}",
                  help="PE tickets in Validating today.")
        c2.metric("Waiting Now (median)", _bd(kv.get("waiting_median")),
                  help="Business days since each ticket entered Validating (not since it was created).")
        c3.metric(f"Waiting {kv['nudge_bd']}+ bd", f"{kv['nudge']}",
                  help="Tickets to nudge: the requester hasn't confirmed for a week or more.")
        if val["history"]:
            c4.metric(f"Validation Time (P85, {kv['window_days']}d)", _bd(kv.get("p85")),
                      _delta_bd(kv.get("p85"), kv.get("p85_prev")), delta_color="inverse",
                      help=f"85% of validations finished within this many business days (median {_bd(kv.get('p50'))}; "
                           f"{(kv.get('same_day') or 0):.0%} the same day). {kv.get('validations', 0)} validations ended "
                           f"in the last {kv['window_days']} days.")
            rw, rwp = kv.get("rework"), kv.get("rework_prev")
            c5.metric(f"Sent Back ({kv['window_days']}d)", "—" if rw is None else f"{rw:.0%}",
                      None if rw is None or rwp is None else f"{(rw - rwp) * 100:+.0f} pts vs previous {kv['window_days']}d",
                      delta_color="inverse",
                      help="Validations that went back to In Progress / To Do / Triage instead of being closed: "
                           f"the work wasn't accepted ({kv.get('rework_n', 0)} tickets).")
        else:
            c4.metric("Past SLA", f"{kv['past_sla']}", help="Tickets in Validating already past their SLA due date.")
            st.info("Change history wasn't loaded with the last fetch, so validation times, rework and SLA impact are "
                    "hidden. Fetch Jira tickets again.")

        st.subheader("Waiting for Confirmation")
        st.caption(f"Longest wait first. Round = how many times the ticket has been in Validating. Nudge requesters "
                   f"waiting {kv['nudge_bd']}+ business days, especially when SLA Left is near or below zero.")
        if val["waiting_df"].empty:
            st.success("Nothing is waiting in Validating.")
        else:
            st.dataframe(val["waiting_df"], width="stretch", hide_index=True, column_config={
                "Ticket": st.column_config.LinkColumn("Ticket", help="Open in Jira", display_text=r".*/([^/]+)$"),
                "Latest Comment": st.column_config.TextColumn("Latest Comment", width="large"),
            })

        if val["history"]:
            st.divider()
            v1, v2 = st.columns(2)
            with v1:
                st.subheader("Validation Time by Month")
                st.caption("Business days from entering Validating to leaving it, by the month it ended. "
                           "Hollow marker = current month so far.")
                if val["trend_fig"] is not None:
                    st.plotly_chart(val["trend_fig"], width="stretch")
            with v2:
                st.subheader("Sent Back to Be Worked On")
                st.caption("Share of validations each month that didn't pass: a quality signal.")
                if val["rework_fig"] is not None:
                    st.plotly_chart(val["rework_fig"], width="stretch")

            v3, v4 = st.columns(2)
            with v3:
                st.subheader("How Long Validations Take")
                st.caption(f"Validations that ended in the last {kv['outcome_days']} days.")
                if val["dist_fig"] is not None:
                    st.plotly_chart(val["dist_fig"], width="stretch")
            with v4:
                st.subheader("By Requesting Business Lead")
                st.caption(f"Median time to confirm, last {kv['outcome_days']} days (leads with 5+ validations).")
                if val["lead_fig"] is not None:
                    st.plotly_chart(val["lead_fig"], width="stretch")
                    with st.expander("Table"):
                        st.dataframe(val["lead_df"], width="stretch", hide_index=True)

            st.divider()
            st.subheader("SLA Impact and Policy What-ifs")
            st.caption(f"Done tickets with an SLA clock, last {kv['outcome_days']} days ({kv['done_judged']:,} tickets; "
                       f"{(kv.get('through_validating') or 0):.0%} went through Validating). Views only: nothing changes.")
            w1, w2 = st.columns(2)
            with w1:
                st.markdown("**If the SLA clock paused during Validating**")
                if kv.get("breach_now") is not None:
                    st.metric("Completed late", f"{kv['breach_paused']:.1%}",
                              f"{(kv['breach_paused'] - kv['breach_now']) * 100:+.1f} pts vs today's rule ({kv['breach_now']:.1%})",
                              delta_color="inverse")
                    st.caption(f"{kv['late_only_validating']} of {kv['late']} late tickets were late only because of "
                               "time spent waiting for confirmation.")
            with w2:
                st.markdown("**If tickets closed after N business days without a reply**")
                n = st.radio("Close after", list(val["what_if"]), format_func=lambda d: f"{d} bd", horizontal=True,
                             key="val_autoclose", index=1)
                wi = val["what_if"][n]
                st.metric("Validations affected", f"{wi['episodes']}", f"{wi['share']:.1%} of {val['recent_n']}",
                          delta_color="off")
                st.caption(f"Would have saved about {wi['days_saved']:.0f} business days of waiting in total.")
            if kv.get("closed_by_requester") is not None:
                st.caption(f"Who closes validations: the requester {kv['closed_by_requester']:.0%} of the time, the "
                           f"assignee {kv['closed_by_assignee']:.0%} (they overlap when the requester is the assignee).")


# ── Blocked & On Hold ────────────────────────────────────────────────────────────
elif selected == "🚧  Blocked & On Hold":
    st.title("🚧 Blocked & On Hold")
    st.caption("Tickets that are Blocked or On Hold, shown side by side, and which need attention to get moving again.")

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    blocked = build_blocked_visuals(df_issues)

    if blocked["blocked_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see Blocked & On Hold visuals.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Blocked", f"{blocked['total_blocked']:,}")
        c2.metric("On Hold", f"{blocked['total_on_hold']:,}")
        c3.metric("Overdue", f"{blocked['overdue_tickets']}", help="Blocked and On Hold tickets past their Target End Date.")
        c4.metric("Due in 7 Days", f"{blocked['due_soon_tickets']}", help="Blocked and On Hold tickets due within 7 days.")
        c5.metric("High Priority", f"{blocked['high_priority_tickets']}", help="Blocked and On Hold tickets with High, Critical or Urgent priority.")

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(blocked["blocked_fig"], width="stretch")
        with col2:
            st.plotly_chart(blocked["risk_fig"], width="stretch")

        st.subheader("Blocked & On Hold Ticket Detail")
        st.dataframe(
            blocked["detail_df"],
            width="stretch",
            hide_index=True,
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
    st.caption(
        "To Do and Tech Discovery Required tickets (Features and Initiatives excluded): when each is "
        "projected to start and finish, and whether it will make its SLA. SLAs are in business days."
    )

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    backlog_risk_basis = st.radio(
        "Judge risk against",
        BACKLOG_RISK_BASES,
        horizontal=True,
        key="backlog_risk_basis",
        help="The KPIs and charts use this deadline. The detail table always shows both the SLA and Target End status.",
    )
    backlog = build_backlog_visuals(df_issues, risk_basis=backlog_risk_basis)

    if backlog["readiness_fig"] is None:
        st.info("📥 Fetch Jira tickets from the sidebar to see Backlog visuals.")
    else:
        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        c1.metric("Backlog Tickets", f"{backlog['total_backlog']:,}")
        c2.metric("Ready to Start", f"{backlog['ready']}")
        c3.metric("Should Have Started", f"{backlog['should_have_started']}")
        c4.metric("! At Risk / Likely Late", f"{backlog['at_risk']}")
        c5.metric("✖ Breached", f"{backlog['breached']}")
        c6.metric("Unassigned", f"{backlog['unassigned']}")
        c7.metric("Waiting > SLA", f"{backlog['waiting_past_sla']}")

        with st.expander("How the backlog forecast works"):
            st.markdown(
                "- **Queue order**: each person's backlog is ordered with the Apparent Tardiness Cost rule, "
                "the same order the Personal Dashboard suggests (Queue #).\n"
                "- **Queue simulation**: each person works on as many tickets at once as they usually do. "
                "Their In Progress tickets finish first (using the In Progress forecast); each backlog ticket "
                "starts when a slot frees up, but not before its Target start. This is simulated "
                f"{BACKLOG_SIMULATIONS:,} times with durations drawn from comparable Done tickets.\n"
                "- **Projected Start** and **Finish P50** are the likely dates; **Finish P85** is the safe date to commit to.\n"
                "- **SLA** runs from Target start (Priority × Size, business days). Tickets with no Target start "
                "or no assignee are ○ Not assessed. The **Missing** column says what to fill in.\n"
                "- **Waiting > SLA**: tickets that have already sat in the backlog longer than their whole SLA."
            )

        st.divider()
        col1, col2 = st.columns([3, 2])
        with col1:
            st.subheader("Capacity Runway by Assignee")
            st.caption("Business days until each person is free: In Progress work first, then their backlog queue.")
            if backlog["runway_fig"] is not None:
                st.plotly_chart(backlog["runway_fig"], width="stretch")
        with col2:
            st.subheader("Backlog Readiness")
            st.caption("A ticket needs all four to be scheduled and judged against its SLA.")
            st.plotly_chart(backlog["readiness_fig"], width="stretch")

        if backlog["timeline_fig"] is not None:
            st.subheader("Projected Start and Finish")
            st.caption("Bar: projected start → P85 finish, coloured by risk. ● P50 finish · ▷ Target start · ◇ SLA due · ✕ Target End Date.")
            st.plotly_chart(backlog["timeline_fig"], width="stretch")
        if backlog["all_done_p85"]:
            st.caption(f"Whole assigned backlog projected done by **{backlog['all_done_p85']:%b %d, %Y}** (P85).")

        col3, col4 = st.columns(2)
        with col3:
            st.subheader("Backlog by SLA Cell")
            st.caption("Tickets per Priority × Size, with each cell's SLA. Unsized tickets count as Medium.")
            st.plotly_chart(backlog["sla_grid_fig"], width="stretch")
        with col4:
            st.subheader("Waiting Longer Than Their SLA")
            st.caption("Business days since creation already exceed the ticket's whole SLA.")
            if backlog["waiting_df"].empty:
                st.success("No backlog ticket has waited longer than its SLA.")
            else:
                st.dataframe(
                    backlog["waiting_df"],
                    width="stretch",
                    hide_index=True,
                    height=320,
                    column_config={"Ticket": st.column_config.LinkColumn(
                        "Ticket", help="Open Jira ticket", display_text=r".*/([^/]+)$")},
                )

        h1, h2 = st.columns([4, 1])
        with h1:
            st.subheader("Backlog Forecast Detail")
        with h2:
            st.write("")
            from reports.jira_dates import write_permission
            host = st.context.headers.get("Host", "") if hasattr(st, "context") else ""
            updates_allowed, updates_reason = write_permission(host)
            if st.button("✏️ Update Target dates", key="tdu_open", width="stretch", disabled=not updates_allowed,
                         help=("Propose new Target start / end dates from this forecast, review them and update Jira "
                               "safely." if updates_allowed else f"Disabled. {updates_reason}")):
                _target_date_dialog(backlog)
        st.caption(
            f"Projected Start is the likely start (P50); Safe Start is the date 85% of tickets started by in back-tests. "
            f"Start Confidence by queue position: High (#1, typically within ±{BACKLOG_START_MISS['High']} bd), "
            f"Medium (#2–3, ±{BACKLOG_START_MISS['Medium']} bd), Low (#4+, ±{BACKLOG_START_MISS['Low']} bd)."
        )
        st.dataframe(
            backlog["forecast_df"],
            width="stretch",
            hide_index=True,
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )

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


# ── Suggested Assignments ────────────────────────────────────────────────────────
elif selected == "🧭  Suggested Assignments":
    st.title("🧭 Suggested Assignments")
    st.caption(
        "Who should take each unassigned or triage ticket, weighing domain experience, availability, the SLA and team "
        "load. Nothing changes in Jira unless you use Assign in Jira (safe mode). PE tickets only; Jira data only."
    )
    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    if df_issues is None or df_issues.empty:
        st.info("📥 Fetch Jira tickets from the sidebar to see suggested assignments.")
    else:
        with st.spinner("Trying each ticket in each person's queue…"):
            asg = build_assignment_visuals(df_issues)
        if asg["error_message"]:
            st.warning(asg["error_message"])
        else:
            kp = asg["kpis"]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Tickets to Assign", f"{kp['to_assign']}", help="Open PE tickets that are unassigned or in Triage/Reviewing.")
            k2.metric("With a Known Domain", f"{kp['with_domain']}",
                      help="Domain recognised from the title or comments; the rest are suggested on availability alone.")
            k3.metric("Plan Fits SLA", f"{kp['fit']} of {kp['to_assign']}" if kp["to_assign"] else "—",
                      help="Suggested owner likely finishes within the SLA (P85).")
            k4.metric("People in the Plan", f"{kp['people']} of {kp['candidates']}" if kp["to_assign"] else "—",
                      help=f"Core team members who would receive work. Most to one person: {kp.get('max_per_person', 0)}.")

            with st.expander("How suggestions are made"):
                st.markdown(
                    "- **Tickets:** open PE tickets that are unassigned or in Triage / Reviewing, taken in **ATC order** "
                    "(most urgent first).\n"
                    "- **Candidates:** people who completed at least 5 PE tickets in the last 90 days, excluding people "
                    "who have left the team.\n"
                    "- **Domain experience:** recent tickets in the same domain (from titles and comments, using the same "
                    "domain list as darkstar's Intake page); recent work counts more.\n"
                    "- **Availability and SLA:** each ticket is tried in each person's queue (their current work first, "
                    "then ATC order, at their own speed) using the Backlog forecast. Owners who would finish within the "
                    "SLA come first.\n"
                    "- **Balance:** people already above their usual load, or already given tickets in this plan, are "
                    "ranked a little lower, so work spreads across the team.\n"
                    "- **Backup** is the next best option; **Stretch** is someone with some experience in the domain who "
                    "could grow into it."
                )

            a1, a2 = st.columns([4, 1])
            with a1:
                st.subheader("Assignment Plan")
            with a2:
                st.write("")
                from reports.jira_dates import write_permission
                host = st.context.headers.get("Host", "") if hasattr(st, "context") else ""
                assign_allowed, assign_reason = write_permission(host)
                if st.button("✏️ Assign in Jira", key="asg_open", width="stretch",
                             disabled=not assign_allowed or asg["plan_df"].empty,
                             help=("Pick tickets from this plan, review them and assign them in Jira safely."
                                   if assign_allowed else f"Disabled. {assign_reason}")):
                    _assign_dialog(asg)
            if asg["plan_df"].empty:
                st.success("No unassigned or triage tickets right now.")
            else:
                st.download_button("⬇️ Download plan (CSV)", asg["plan_df"].to_csv(index=False).encode("utf-8"),
                                   file_name="suggested_assignments.csv", mime="text/csv", key="asg_csv")
                st.dataframe(
                    asg["plan_df"], width="stretch", hide_index=True,
                    column_config={
                        "Ticket": st.column_config.LinkColumn("Ticket", help="Open in Jira", display_text=r".*/([^/]+)$"),
                        "Why": st.column_config.TextColumn("Why", width="large"),
                    },
                )

                st.subheader("Compare Options for a Ticket")
                key = st.selectbox("Ticket", list(asg["options"]), key="asg_ticket")
                st.caption("Every candidate for this ticket, best first: SLA fit, then score.")
                st.dataframe(asg["options"][key], width="stretch", hide_index=True)

            c1, c2 = st.columns([3, 2])
            with c1:
                st.subheader("Who Knows What")
                st.caption("Recent tickets per domain and person (recent work counts more). Top domains by volume.")
                if asg["matrix_fig"] is not None:
                    st.plotly_chart(asg["matrix_fig"], width="stretch")
            with c2:
                st.subheader("Domains Leaning on One Person")
                st.caption("Share of each domain's recent work done by its top person: candidates for cross-training.")
                st.dataframe(asg["concentration_df"], width="stretch", hide_index=True, height=360,
                             column_config={"Top Share %": st.column_config.ProgressColumn(
                                 "Top Share %", format="%d%%", min_value=0, max_value=100)})
            with st.expander("Team load used for the suggestions"):
                st.dataframe(asg["load_df"], width="stretch", hide_index=True)


# ── Forecast ─────────────────────────────────────────────────────────────────────
elif selected == "🔮  Forecast":
    st.title("🔮 Delivery Forecast")
    st.caption(
        "How many tickets Platform Engineering will likely deliver in the coming weeks, with an honest range. "
        "PE tickets only (no Features, Initiatives or CAR); full weeks; built from the team's own delivery history."
    )

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    if df_issues is None or (isinstance(df_issues, pd.DataFrame) and df_issues.empty):
        st.info("📥 Fetch Jira tickets from the sidebar to display the Forecast.")
    elif build_forecast_visuals is None:
        st.error("⚠️ Forecast module could not be loaded.")
    else:
        with st.spinner("Building the delivery forecast…"):
            fc = build_forecast_visuals(df_issues)

        if fc["error_message"]:
            st.warning(f"⚠️ {fc['error_message']}")
        else:
            st.info(fc["headline"])
            if not fc["calibrated"]:
                st.warning("Not enough history yet to calibrate the range from past forecast errors; the range is a "
                           "rough estimate from recent weeks.")

            cols = st.columns(len(fc["cards"]))
            for col, card in zip(cols, fc["cards"]):
                col.metric(f"Next {card['weeks']} weeks · by {card['by']:%b %d}", f"~{card['likely']:,} tickets",
                           help="Likely (central forecast). The range below covers about 7 in 10 outcomes.")
                col.caption(f"Range **{card['low']:,} – {card['high']:,}**")
            acc = fc["accuracy"]
            if acc:
                st.caption(
                    f"**Track record:** over the last {acc['forecasts']} weeks, the {FORECAST_ACCURACY_HORIZON}-week forecast "
                    f"was within ±{acc['typical_error']:.0%} of what actually happened on average, and the actual landed "
                    f"inside the range {acc['inside']:.0%} of the time. Recent pace: {fc['recent_rate']:.0f} tickets/week "
                    f"(last 4 full weeks, through the week of {fc['last_full_week']:%b %d})."
                )

            st.divider()
            st.subheader("Weekly Delivery: History and Forecast")
            st.caption("Solid line: tickets delivered each full week. Dashed line and shaded area: the forecast and its likely range.")
            st.plotly_chart(fc["weekly_fig"], width="stretch")

            c1, c2 = st.columns([3, 2])
            with c1:
                st.subheader("By When? Cumulative Delivery")
                st.caption("Total tickets delivered from today. Read across a date to see how many will likely be done by then.")
                st.plotly_chart(fc["cumulative_fig"], width="stretch")
            with c2:
                st.subheader("Can We Commit to a Project?")
                st.caption("Uses the same forecast, but only the share of team capacity the project can have; "
                           "the rest keeps serving incoming requests.")
                project = st.number_input("Project size (tickets)", min_value=1, max_value=10000, value=150, step=10)
                share = st.slider("Share of team capacity for the project", 10, 100, 30, 5, format="%d%%")
                eta = forecast_weeks_to_deliver(int(project), fc["forecast"], share / 100)
                if eta:
                    last = fc["last_full_week"]

                    def _when(weeks):
                        if weeks is None:
                            return "more than a year"
                        return f"{weeks} weeks (≈ {(pd.Timestamp(last) + pd.Timedelta(days=7 * weeks + 6)):%b %d})"

                    st.metric("Likely done in", _when(eta["likely"]))
                    st.metric("Safe to commit", _when(eta["safe"]),
                              help="Uses the low end of the forecast range: done by then in roughly 9 of 10 outcomes.")
                    st.caption("Commit to the safe date; the likely date is a coin flip.")

            st.subheader("Demand vs Delivery Outlook")
            demand_err = fc.get("demand_typical_error")
            st.caption(
                f"Requested vs delivered per week, with forecasts. Over the next 12 weeks: about "
                f"**{fc['requested_12w']:,.0f} requested** vs **{fc['delivered_12w']:,.0f} delivered**. "
                + (f"Requests are harder to forecast (typically within ±{demand_err:.0%}), so treat the requested line as a guide."
                   if demand_err is not None else "")
            )
            st.plotly_chart(fc["demand_fig"], width="stretch")

            if fc["accuracy_fig"] is not None:
                st.subheader("How Accurate Has This Forecast Been?")
                st.caption(f"Each point: the {FORECAST_ACCURACY_HORIZON}-week forecast made that week (dashed, with its range at "
                           "the time) vs what actually happened (solid). Ranges were built only from information "
                           "available at the time.")
                st.plotly_chart(fc["accuracy_fig"], width="stretch")

            with st.expander("How this forecast works"):
                st.markdown(
                    "- **What is counted:** PE tickets moved to Done, in full Monday–Sunday weeks. The current week is never "
                    "used, so a few days of data can't drag the forecast down.\n"
                    "- **Central forecast:** the trend of the last 12 weeks, projected forward with the growth **slowing down** "
                    "each week (a damped trend), rather than assuming it continues forever.\n"
                    "- **Range:** set from this method's own past mistakes. The forecast was re-run at every past week using only "
                    "what was known then, and compared with what actually happened; the range covers about 7 in 10 of those outcomes. "
                    "Because the team has recently kept beating its forecasts, the range leans upward.\n"
                    "- **Why not a machine-learning model?** With about two years of weekly data, the earlier XGBoost model "
                    "overfitted and its ranges could go negative. Back-tested on this team's history, this simpler method "
                    "was more accurate and honest about uncertainty.\n"
                    "- **Longer horizons** are less certain: at 12 weeks the actual landed inside the range about 6 in 10 times, "
                    "and when it missed it was usually above, because delivery has been growing quickly."
                )


# ── Distribution of Ticket's Age ─────────────────────────────────────────────────
elif selected == "📊  Distribution of Ticket's Age":
    st.title("📊 Distribution of Ticket's Age")
    st.caption(
        "How old open work is against its SLA, and where it has gone quiet. Open tickets only (no Features or "
        "Initiatives), ages in business days, grouped by stage as on the Executive Summary."
    )

    if build_distribution_visuals is None:
        st.error("distribution_of_tickets_report module could not be loaded.")
        st.stop()

    df_issues = st.session_state.get("jira_df_issues")
    if df_issues is None or df_issues.empty:
        st.warning("⚠️ No Jira data loaded yet. Please fetch tickets from the Overview page first.")
        st.stop()

    with st.spinner("Measuring ticket age…"):
        dist = build_distribution_visuals(df_issues)

    if dist["error_message"]:
        st.error(f"❌ {dist['error_message']}")
        st.stop()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Open Tickets", f"{dist['open_count']:,}")
    k2.metric("Median Age", f"{dist['median_age_bd']:.0f} bd",
              help=f"Business days since creation. 75th percentile: {dist['p75_age_bd']:.0f} bd.")
    k3.metric("Past SLA", f"{dist['past_sla']}",
              help="Open tickets that have used more than their whole SLA since Target start. "
                   "Release Management (CAR) tickets have no PE SLA and are not counted.")
    k4.metric(f"Silent {DIST_SILENT_THRESHOLD_BD}+ bd", f"{dist['silent_count']}",
              help=f"No human comment in {DIST_SILENT_THRESHOLD_BD} or more business days (or since creation).")
    k5.metric("Oldest Ticket", f"{dist['oldest']['age_bd']} bd",
              help=f"{dist['oldest']['key']} ({dist['oldest']['stage']})")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Age Against SLA")
        st.caption("Share of each ticket's SLA already used, by stage. Right of the dashed line = past its SLA. "
                   "Tickets without a Target start and CAR tickets are not shown.")
        if dist["sla_fig"] is not None:
            st.plotly_chart(dist["sla_fig"], width="stretch")
        else:
            st.info("No open tickets with a Target start and a PE SLA.")
    with c2:
        st.subheader("Where Work Has Gone Quiet")
        st.caption("Business days since the last human comment (or since creation if nobody has commented).")
        st.plotly_chart(dist["silence_fig"], width="stretch")

    st.subheader("Is Open Work Getting Older?")
    st.caption("Age of the tickets that were open at the end of each week, last 12 weeks.")
    st.plotly_chart(dist["trend_fig"], width="stretch")

    with st.expander("By status"):
        st.dataframe(dist["status_df"], width="stretch", hide_index=True)

    st.subheader("Open Tickets: Past SLA and Quietest First")
    st.dataframe(
        dist["tickets_df"],
        width="stretch",
        hide_index=True,
        column_config={
            "Ticket": st.column_config.LinkColumn("Ticket", help="Open Jira ticket", display_text=r".*/([^/]+)$"),
            "SLA Used %": st.column_config.ProgressColumn("SLA Used %", format="%d%%", min_value=0, max_value=100),
        },
    )


# ── Distribution per Business Leader ─────────────────────────────────────────────
elif selected == "👤  Distribution per Business Leader":
    st.title("👤 Distribution of Tickets per Business Leader")
    st.caption(
        "The service each requesting business lead gets from Platform Engineering: what they asked for, what was "
        "delivered, how long they waited, and whether it met the SLA. PE tickets only (no Features, Initiatives "
        "or Release Management CAR tickets)."
    )

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
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        start_month = st.selectbox("Start month", available_months,
                                   index=available_months.index(seed["start_month"]))
    with c2:
        end_month = st.selectbox("End month", available_months,
                                 index=available_months.index(seed["end_month"]))
    with c3:
        st.write("")
        include_internal = st.toggle(
            "Include Platform Engineering (internal) in charts", value=False,
            help="PE's own work is most of the volume and would hide the requesting business leads. "
                 "The scorecard always shows it as its own row.",
        )

    with st.spinner("Building the business leader view…"):
        biz = build_business_leader_visuals(df_issues, start_month=start_month, end_month=end_month,
                                            include_internal=include_internal)
    if biz["error_message"]:
        st.error(f"❌ {biz['error_message']}")
        st.stop()

    kp = biz["kpis"]
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Requesting Leads", f"{kp['requesting_leads']}", help="Business leads who requested tickets in the period.")
    k2.metric("Requested by Leads", f"{kp['requested_by_leads']:,}",
              help=f"Out of {kp['requested']:,} PE tickets requested in the period.")
    k3.metric("Delivered (Done)", f"{kp['delivered']:,}", help="All PE tickets completed in the period.")
    k4.metric("SLA Met (Leads)", f"{kp['sla_met_leads']:.0%}" if kp["sla_met_leads"] is not None else "—",
              help="Requesting business leads' delivered tickets that met their SLA.")
    k5.metric("PE Internal Share", f"{kp['internal_share']:.0%}", help="Requested tickets that are PE's own work.")
    k6.metric("No Business Lead", f"{kp['unknown_share']:.0%}",
              help="Requested tickets with no business lead in Jira: a data gap to fix at intake.")

    st.subheader("Service Scorecard")
    st.caption(f"{biz['start_month']} to {biz['end_month']}. Requested = created in the period; Delivered = moved to Done "
               "in the period; Wait = business days from request to Done. Open columns are as of today.")
    st.dataframe(
        biz["scorecard_df"], width="stretch", hide_index=True,
        column_config={
            "SLA Met %": st.column_config.ProgressColumn("SLA Met %", format="%d%%", min_value=0, max_value=100),
            "Top Friction in Comments": st.column_config.TextColumn("Top Friction in Comments", width="medium"),
        },
    )

    st.divider()
    b1, b2 = st.columns(2)
    with b1:
        st.subheader("Requested vs Delivered")
        if biz["demand_fig"] is not None:
            st.plotly_chart(biz["demand_fig"], width="stretch")
    with b2:
        st.subheader("Monthly Demand")
        st.caption("Tickets requested per month, last 12 months: top 5 leads and Other.")
        st.plotly_chart(biz["trend_fig"], width="stretch")

    b3, b4 = st.columns(2)
    with b3:
        st.subheader("SLA Met by Business Lead")
        st.caption(f"Hollow markers: fewer than {BIZ_SMALL_SAMPLE} delivered tickets, so read with care.")
        if biz["sla_fig"] is not None:
            st.plotly_chart(biz["sla_fig"], width="stretch")
    with b4:
        st.subheader("How Long Requests Wait")
        if biz["wait_fig"] is not None:
            st.plotly_chart(biz["wait_fig"], width="stretch")

    b5, b6 = st.columns(2)
    with b5:
        st.subheader("Priority Mix of Requests")
        if biz["priority_fig"] is not None:
            st.plotly_chart(biz["priority_fig"], width="stretch")
    with b6:
        st.subheader("Open Work by SLA Risk")
        if biz["open_fig"] is not None:
            st.plotly_chart(biz["open_fig"], width="stretch")
        else:
            st.info("No open tickets for these business leads.")


# ── Teams Conversations ───────────────────────────────────────────────────────────
elif selected == "💬  Teams Conversations":
    st.title("💬 Teams Conversations")
    st.caption(
        "What ticket comments say about how we work: comment coverage to track over time, the friction "
        "themes that cost the most time, conversation health, and the phrases of the month. Human comments "
        "only (bots excluded), on completed tickets (no Features or Initiatives)."
    )

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
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        start_month = st.selectbox("Start month (completed)", available_months,
                                   index=available_months.index(seed["start_month"]))
    with c2:
        end_month = st.selectbox("End month (completed)", available_months,
                                 index=available_months.index(seed["end_month"]))
    with c3:
        coverage_target = st.slider("Comment coverage target", 0.5, 1.0, 0.8, 0.05, format="%.2f",
                                    help="Drawn on the coverage trend as the goal to work toward.")

    with st.spinner("Reading ticket comments…"):
        words = build_word_of_the_month_visuals(df_issues, start_month=start_month, end_month=end_month,
                                                coverage_target=coverage_target)
    if words["error_message"]:
        st.error(f"❌ {words['error_message']}")
        st.stop()

    def _pp(delta):
        return None if delta is None else f"{delta * 100:+.0f} pts vs previous period"

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Comment Coverage", f"{words['coverage']:.0%}", _pp(words["coverage_delta"]),
              help="Completed tickets with at least one human comment.")
    k2.metric("Assignee Commented", f"{words['closing_note']:.0%}", _pp(words["closing_note_delta"]),
              help="Completed tickets where the assignee left at least one comment.")
    k3.metric("Median First Reply",
              f"{words['first_reply_hours']:.1f} bh" if words["first_reply_hours"] is not None else "—",
              help="Business hours from ticket creation to the first human comment by someone other than the requester.")
    k4.metric("Requester Had to Chase", f"{words['chased_share']:.0%}",
              help="Completed tickets where the requester posted a follow-up ping.")
    k5.metric("Completed Tickets Read", f"{words['tickets_in_range']:,}")

    with st.expander("How this works"):
        st.markdown(
            "- **Comments** are human comments only. Bot accounts such as Automation for Jira are dropped when data "
            "loads. Jira returns up to 20 comments per ticket.\n"
            "- **Comment coverage** is measured on tickets completed in each month. **Assignee Commented** means the "
            "person who did the work left a note.\n"
            "- **Friction themes** are keyword rules (editable in `reports/word_of_the_month_report.py`). A ticket has "
            "a theme when any human comment matches. **Extra days** compares its cycle time (Target start → Done, "
            "business days) with similar tickets (same Priority × Size) without that theme.\n"
            "- Long tickets collect more comments, so themes are **associated** with delay, not proven to cause it. "
            "*Chasing* is a symptom of slow tickets, so it is shown but not ranked as an opportunity.\n"
            "- **Emerging phrases** are two-word phrases used much more than in the previous three months, "
            "adjusted for how much more people comment now."
        )

    st.divider()
    st.subheader("📈 Comment Coverage")
    st.caption("Share of completed tickets with a human comment, and with a comment from the assignee. Track this monthly.")
    st.plotly_chart(words["coverage_fig"], width="stretch")
    cc1, cc2 = st.columns(2)
    with cc1:
        st.caption("By business lead (lowest coverage first)")
        st.dataframe(words["coverage_by_lead_df"], width="stretch", hide_index=True, height=280,
                     column_config={c: st.column_config.ProgressColumn(c, format="%d%%", min_value=0, max_value=100)
                                    for c in ["With Human Comment", "Assignee Commented"]})
    with cc2:
        st.caption("By assignee (documentation habit, not performance)")
        st.dataframe(words["coverage_by_assignee_df"], width="stretch", hide_index=True, height=280,
                     column_config={c: st.column_config.ProgressColumn(c, format="%d%%", min_value=0, max_value=100)
                                    for c in ["With Human Comment", "Assignee Commented"]})

    st.divider()
    st.subheader("🧭 Where Time Gets Lost")
    if words["recommendations"]:
        st.caption("Top opportunities, ranked by total extra business days in the selected months.")
        rec_cols = st.columns(len(words["recommendations"]))
        for col, rec in zip(rec_cols, words["recommendations"]):
            col.info(
                f"**{rec['theme']}**\n\n{rec['tickets']} tickets · +{rec['extra_per_ticket']:.1f} business days each "
                f"· ~{rec['total_extra']:.0f} days in total\n\n{rec['action']}"
            )
    wt1, wt2 = st.columns(2)
    with wt1:
        st.caption("Total extra business days by theme")
        st.plotly_chart(words["pareto_fig"], width="stretch")
    with wt2:
        st.caption("Share of completed tickets with each theme, by month")
        st.plotly_chart(words["theme_trend_fig"], width="stretch")

    if words["slice_figs"]:
        slice_by = st.radio("Break themes down by", list(words["slice_figs"]), horizontal=True, key="wotm_slice")
        st.plotly_chart(words["slice_figs"][slice_by], width="stretch")
    with st.expander("Theme details"):
        st.dataframe(words["themes_df"], width="stretch", hide_index=True)

    st.divider()
    st.subheader("🗣️ Conversation Health")
    ch1, ch2 = st.columns(2)
    with ch1:
        st.caption("More back-and-forth between people goes with longer tickets")
        st.plotly_chart(words["handoff_fig"], width="stretch")
    with ch2:
        st.caption("Response and back-and-forth by assignee (tickets created in the selected months)")
        st.dataframe(words["people_df"], width="stretch", hide_index=True, height=320)

    st.divider()
    st.subheader(f"🏆 Phrase of the Month: **{(words['phrase_of_the_month'] or '—').upper()}**")
    ph1, ph2 = st.columns(2)
    with ph1:
        st.caption("Most used phrases in human comments")
        if words["phrases_fig"] is not None:
            st.plotly_chart(words["phrases_fig"], width="stretch")
    with ph2:
        st.caption("Emerging: used far more than in the previous three months")
        st.dataframe(words["emerging_df"], width="stretch", hide_index=True, height=420)

    st.divider()
    st.subheader("🔎 Tickets With Friction Themes")
    st.dataframe(
        words["tickets_df"],
        width="stretch",
        hide_index=True,
        column_config={
            "Ticket": st.column_config.LinkColumn("Ticket", help="Open Jira ticket", display_text=r".*/([^/]+)$"),
            "Example": st.column_config.TextColumn("Example", width="large"),
        },
    )


# ── SLA ─────────────────────────────────────────────────────────────────────────
elif selected == "🛡️  SLA (Service Level Agreements)":
    st.title("🛡️ SLA (Service Level Agreements)")
    st.caption(
        "Where work is breached, late or at risk, and the breach rate against the goal. SLAs are the Priority × Size "
        "table in business days from Target start. PE tickets only (no Features, Initiatives or CAR tickets)."
    )

    if build_sla_visuals is None:
        st.error("service_level_agreement_report module could not be loaded.")
        st.stop()

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    if df_issues is None or df_issues.empty:
        st.info("📥 Fetch Jira tickets from the sidebar to see SLA visuals.")
        st.stop()

    options = build_sla_visuals(df_issues)["filter_options"]
    w1, f1, f2, f3, f4 = st.columns([2, 3, 3, 2, 2])
    with w1:
        window = st.radio("Breach rate window", SLA_WINDOWS, index=SLA_WINDOWS.index(SLA_DEFAULT_WINDOW),
                          format_func=lambda d: f"{d} days", horizontal=True, key="sla_window")
    with f1:
        sel_assignees = st.multiselect("Assignee", options.get("assignees", []), key="sla_assignees")
    with f2:
        sel_leads = st.multiselect("Business Lead", options.get("leads", []), key="sla_leads")
    with f3:
        sel_priorities = st.multiselect("Priority", options.get("priorities", []), key="sla_priorities")
    with f4:
        sel_stages = st.multiselect("Stage (open work)", options.get("stages", []), key="sla_stages")
    filters = {"assignees": sel_assignees, "leads": sel_leads, "priorities": sel_priorities, "stages": sel_stages}

    t1, t2, t3, t4 = st.columns(4)
    with t1:
        include_on_track = st.toggle("SLA Detail: also show on-track and not-assessed open tickets", value=False,
                                     key="sla_include_on_track")
    with t2:
        include_completed = st.toggle(f"Breached Tickets: include tickets completed late in the last {window} days",
                                      value=True, key="sla_include_completed")
    with t3:
        judge_original = st.toggle("Judge re-planned tickets on their original Target start", value=True,
                                   key="sla_judge_original",
                                   help="Tickets whose Target dates were moved from the Backlog page keep their original "
                                        "SLA clock here, so re-planning can't hide a breach.")
    with t4:
        exclude_after_fact = st.toggle("Leave out tickets whose SLA clock was set after the fact", value=False,
                                       key="sla_exclude_after_fact",
                                       help="Completed tickets whose Target start was set or moved on or after the day "
                                            "they were done. Their SLA result isn't a real measurement.")

    sla = build_sla_visuals(df_issues, time_period_days=window, filters=filters,
                            include_on_track=include_on_track, include_completed_late=include_completed,
                            judge_original_start=judge_original,
                            history=st.session_state.get("jira_change_history"),
                            exclude_after_fact=exclude_after_fact)
    if sla["error_message"]:
        st.error(f"❌ {sla['error_message']}")
        st.stop()

    kp = sla["kpis"]
    goal = kp["goal"]

    def _rate_tile(col, label, rate, delta, breached, n, help_text):
        if rate is None:
            col.metric(label, "—", help=help_text)
            col.caption("No tickets in this window.")
            return
        col.metric(label, f"{rate:.1%}", f"{delta * 100:+.1f} pts vs previous {window}d" if delta is not None else None,
                   delta_color="inverse", help=help_text)
        verdict = "✓ under goal" if rate < goal else "✖ above goal"
        col.caption(f"**{verdict}** (goal < {goal:.0%}) · {breached} of {n} tickets")

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    _rate_tile(k1, "Breach Rate · SLA came due", kp["due_rate"], kp["due_rate_delta"], kp["due_breached"], kp["due_n"],
               "Of tickets whose SLA due date fell in the window, the share that missed it, including tickets still "
               "open past their due date.")
    _rate_tile(k2, "Breach Rate · completed late", kp["done_rate"], kp["done_rate_delta"], kp["done_late"], kp["done_n"],
               "Of tickets moved to Done in the window, the share finished after their SLA due date "
               "(as on the Trend and Executive Summary pages).")
    k3.metric("✖ Open & Breached", f"{kp['open_breached']}", help="Open tickets already past their SLA due date.")
    k4.metric("! At Risk / Likely Late", f"{kp['open_at_risk']}", help="Open tickets forecast to miss, or close to, their SLA.")
    k5.metric(f"Due in Next {SLA_DUE_SOON_BD} bd", f"{kp['due_soon']}", help="Open tickets whose SLA comes due soon.")
    if kp.get("replanned"):
        st.caption(f"🔁 {kp['replanned']} ticket(s) re-planned from the dashboard are judged on their original Target start.")
    k6.metric("No SLA Clock", f"{kp['no_clock']}", help="Open tickets without a Target start: they can't be judged. "
              "Set a Target start in Jira.")
    if kp.get("history") and kp.get("after_fact_n"):
        share = kp["after_fact"] / kp["after_fact_n"]
        note = ("left out of the breach rates above" if kp["excluding_after_fact"]
                else "still counted in the breach rates above (toggle to leave them out)")
        st.warning(f"⚠ **SLA clock set after the fact:** {kp['after_fact']} of {kp['after_fact_n']} tickets completed in the "
                   f"last {window} days ({share:.0%}) had their Target start set or moved on or after the day they were done; "
                   f"{kp['after_fact_met']} of them show as met. Their SLA result isn't a real measurement and is {note}. "
                   "See the list below.")
    elif not kp.get("history"):
        st.caption("Change history wasn't loaded with the last fetch, so tickets with an SLA clock set after the fact "
                   "can't be flagged. Fetch Jira tickets again.")

    st.divider()
    g1, g2 = st.columns([3, 2])
    with g1:
        st.subheader("Breach Rate Trend")
        st.caption("Monthly, both definitions, against the goal. Hollow marker = current month so far.")
        st.plotly_chart(sla["trend_fig"], width="stretch")
    with g2:
        st.subheader("Where Breaches Come From")
        st.caption(f"SLA came due in the last {SLA_HEATMAP_DAYS} days: share missed by Priority × Size "
                   f"(missed / due). * = fewer than {SLA_MIN_CELL} tickets.")
        if sla["heatmap_fig"] is not None:
            st.plotly_chart(sla["heatmap_fig"], width="stretch")

    g3, g4 = st.columns(2)
    with g3:
        st.subheader("Coming Due")
        st.caption(f"Open tickets overdue or with their SLA due in the next {SLA_DUE_SOON_BD} business days, by forecast risk.")
        if sla["due_soon_fig"] is not None:
            st.plotly_chart(sla["due_soon_fig"], width="stretch")
        else:
            st.success("Nothing overdue or coming due soon.")
    with g4:
        st.subheader("Open Work by Stage")
        st.caption("Open tickets by stage and SLA risk (same rules as the Executive Summary).")
        if sla["stage_fig"] is not None:
            st.plotly_chart(sla["stage_fig"], width="stretch")

    def _searchable_table(title, caption, frame, key, file_name, extra_config=None):
        st.subheader(title)
        st.caption(caption)
        if frame.empty:
            st.success("No tickets to show with these filters.")
            return
        s1, s2 = st.columns([4, 1])
        with s1:
            query = st.text_input("Search", key=f"{key}_search", placeholder="Ticket, assignee, summary, comment…",
                                  label_visibility="collapsed")
        shown = frame
        if query:
            text = frame.astype(str).apply(lambda col: col.str.contains(query, case=False, regex=False))
            shown = frame[text.any(axis=1)]
        with s2:
            st.download_button("⬇️ CSV", shown.to_csv(index=False).encode("utf-8"), file_name=file_name,
                               mime="text/csv", key=f"{key}_csv", width="stretch")
        config = {
            "Ticket": st.column_config.LinkColumn("Ticket", help="Open in Jira to comment or review",
                                                  display_text=r".*/([^/]+)$"),
            "Latest Comment": st.column_config.TextColumn("Latest Comment", width="large"),
            "Summary": st.column_config.TextColumn("Summary", width="medium"),
        }
        config.update(extra_config or {})
        st.dataframe(shown, width="stretch", hide_index=True, column_config=config, height=min(560, 38 + 35 * len(shown)))
        st.caption(f"{len(shown)} of {len(frame)} tickets")

    st.divider()
    _searchable_table(
        "SLA Detail",
        "Open tickets that are breached, likely late or at risk, most urgent first. Days to SLA: negative = business days "
        "overdue. Forecast Finish is the safe (P85) date from the In Progress / Backlog forecasts.",
        sla["detail_df"], "sla_detail", "sla_detail.csv",
        {"SLA Used %": st.column_config.ProgressColumn("SLA Used %", format="%d%%", min_value=0, max_value=100)},
    )
    _searchable_table(
        "Breached Tickets",
        "Still-open breached tickets first (most overdue first), then tickets completed late in the window.",
        sla["breached_df"], "sla_breached", "sla_breached.csv",
    )

    if not sla["after_fact_df"].empty:
        _searchable_table(
            "SLA Clock Set After the Fact",
            f"Tickets completed in the last {window} days whose Target start (the SLA clock) was set or moved on or after "
            "the day they moved to Done. Set Target start when work is planned, not when it is finished.",
            sla["after_fact_df"], "sla_after_fact", "sla_clock_after_fact.csv",
        )

    with st.expander("SLA table and definitions"):
        st.dataframe(sla["sla_table_df"], width="stretch", hide_index=True)
        st.markdown(
            "- The SLA clock starts at **Target start** and counts **business days** (weekends and company holidays "
            "excluded). Unsized tickets use the Medium column.\n"
            "- **Release Management (CAR)** tickets follow the release process and have no PE SLA.\n"
            "- Tickets closed as **Will Not Do** or **Rolled Back** are never judged.\n"
            f"- **Goal:** both breach rates under {goal:.0%}."
        )


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

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Training rows (last 90d)", f"{prob_payload['training_rows']:,}")
    k2.metric("Historical on-time rate", f"{prob_payload['on_time_rate'] * 100:.1f}%")
    k3.metric("Training accuracy", f"{prob_payload['training_accuracy'] * 100:.1f}%")
    if prob_payload.get("validation_accuracy") is not None:
        k4.metric("Validation accuracy", f"{prob_payload['validation_accuracy'] * 100:.1f}%")
        k5.metric("Average validation time", f"{prob_payload['average_validation_days']:.1f} days")
        st.caption(
            f"Validation set uses the most recent {prob_payload.get('validation_rows', 0):,} Done tickets (time-based holdout)."
        )
    else:
        k4.metric("Validation accuracy", "N/A")
        k5.metric("Average validation time", f"{prob_payload['average_validation_days']:.1f} days")

    if prob_payload.get("model_name"):
        st.caption(f"Selected model: {prob_payload['model_name']}")
    if prob_payload.get("probability_calibrated"):
        st.caption(f"Probability calibration: {prob_payload.get('calibration_method', 'enabled')}")

    if prob_payload.get("accuracy_target_met"):
        if prob_payload.get("validation_accuracy") is not None:
            st.success("Validation accuracy target met (≥ 90%) on the time-based holdout set.")
        else:
            st.success("Training accuracy target met (≥ 90%).")
    else:
        if prob_payload.get("validation_accuracy") is not None:
            st.info("Validation accuracy target of 90% was not reached; the app is using the best available fitted model.")
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

        if build_probability_trend_figure is not None:
            trend_fig = build_probability_trend_figure(detail_df)
            if trend_fig is not None:
                st.subheader("Past Due Days & On-Time Rate Trend")
                st.caption(
                    "Bars show raw Past Due Days per ticket (green ≤ 0, amber ≤ 3, red > 3). "
                    "Blue line shows the rolling on-time rate across the same tickets."
                )
                st.plotly_chart(trend_fig, width="stretch")


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

    st.subheader("Apparent Tardiness Cost / Suggested Sequence")
    st.caption(
        "Suggested work order from the Apparent Tardiness Cost rule (Vepsalainen & Morton, 1987): "
        "Urgent/Critical tickets are scheduled first, soonest due date first; everything else is ranked by "
        "Score = (Weight / Effort) × exp(−slack / (K × avg. remaining effort)), K = 2. "
        "Effort is days from Size (Small=1, Medium=3, Large=5, XL=10, Unestimated=2); "
        "Weight doubles per priority tier and again every 30 days of ticket age; tickets with no due date default to 30 days out. "
        "**Validating** tickets are excluded (waiting on the end user); **On Hold** and **Blocked** tickets are excluded "
        "from scoring and appended at the end, On Hold first, then Blocked."
    )
    if personal["atc_df"].empty:
        st.success("No active tickets need sequencing for this assignee.")
    else:
        st.dataframe(
            personal["atc_df"],
            width="stretch",
            column_config={
                "Ticket": st.column_config.LinkColumn(
                    "Ticket",
                    help="Open Jira ticket",
                    display_text=r".*/([^/]+)$",
                )
            },
        )

        st.subheader("Suggested Working-Day Calendar")
        st.caption(
            "Same sequence, laid out on the calendar. 'Projected Start (Day)' 0 is today; each ticket then "
            "fills its Effort in consecutive **working days** (weekends skipped) up to 'Projected Finish (Day)'. "
            "Hover a day for the ticket, priority, size, and projected tardiness."
        )
        if personal["atc_calendar_fig"] is None:
            st.info("Not enough scheduled data to draw a calendar.")
        else:
            st.plotly_chart(personal["atc_calendar_fig"], width="stretch")
            if personal["atc_calendar_truncated"]:
                st.caption(
                    f"⚠️ Sequence extends beyond {ATC_CAL_MAX_WORKING_DAYS} working days — "
                    "the calendar shows only that horizon."
                )


# ── Distribution of Ticket by Estimated Size ────────────────────────────────────
elif selected == "📏  Distribution of Ticket by Estimated Size":
    st.title("📏 Distribution of Ticket by Estimated Size")
    st.caption(
        "Sizing as a practice: how much work is sized, whether sizes match the real effort, how each size performs "
        "against its SLA, and what to size next. PE tickets only; work measured in business days (Target start → Done)."
    )

    df_issues = st.session_state.get("jira_df_issues", pd.DataFrame())
    size_visuals = build_estimated_size_distribution_visuals(df_issues)

    if size_visuals["error_message"]:
        st.info(size_visuals["error_message"] if df_issues is not None and not df_issues.empty
                else "📥 Fetch Jira tickets from the sidebar to see the estimated size distribution.")
    else:
        kp = size_visuals["kpis"]
        target = kp["target"]
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Open Tickets Sized", f"{kp['coverage_open']:.0%}" if kp["coverage_open"] is not None else "—",
                  help=f"Open PE tickets with an Estimated Size. Target {target:.0%}.")
        k1.caption(("✓ at target" if (kp["coverage_open"] or 0) >= target else f"✖ below {target:.0%}")
                   + f" · {kp['open_unsized']} of {kp['open_total']} unsized")
        k2.metric(f"New Tickets Sized ({SIZE_RECENT_DAYS}d)", f"{kp['coverage_new']:.0%}" if kp["coverage_new"] is not None else "—",
                  f"{kp['coverage_new_delta'] * 100:+.0f} pts vs previous {SIZE_RECENT_DAYS}d"
                  if kp["coverage_new_delta"] is not None else None,
                  help=f"Of {kp['new_total']} PE tickets created in the last {SIZE_RECENT_DAYS} days.")
        k3.metric("Size Accuracy", f"{kp['accuracy']:.0%}" if kp["accuracy"] is not None else "—",
                  help=f"Completed sized tickets (last {SIZE_OUTCOME_DAYS} days) whose work landed in their size's range "
                       "in the sizing guide below.")
        if kp["accuracy"] is not None:
            k3.caption(f"{kp['undersized']:.0%} took longer · {kp['oversized']:.0%} took less")
        k4.metric("Open & Unsized", f"{kp['open_unsized']}", help="Open tickets to size (list below).")
        k5.metric("Work Queued", f"≈ {kp['queued_bd']:,.0f} bd",
                  help="Open In Progress + Backlog tickets × the typical work of their size.")

        if size_visuals["recommendations"]:
            with st.container(border=True):
                st.markdown("**What the data suggests changing**")
                st.markdown("\n".join(f"- {r}" for r in size_visuals["recommendations"]))

        st.divider()
        a1, a2 = st.columns(2)
        with a1:
            st.subheader("Sizing Adoption")
            st.caption("Share of new PE tickets with an Estimated Size, by month created.")
            st.plotly_chart(size_visuals["adoption_fig"], width="stretch")
        with a2:
            st.subheader("Where Sizes Are Missing")
            if size_visuals["coverage_figs"]:
                by = st.radio("By", list(size_visuals["coverage_figs"]), horizontal=True, key="size_cov_by",
                              label_visibility="collapsed")
                st.caption(f"Orange = below the {target:.0%} target. Groups with at least {SIZE_MIN_GROUP} tickets.")
                st.plotly_chart(size_visuals["coverage_figs"][by], width="stretch")

        st.subheader("Do Our Sizes Mean What We Think?")
        guide_note = ("derived from the team's own completed work" if size_visuals["guide_from_data"]
                      else "the default guide (not enough completed work to derive one)")
        st.caption(f"Actual business days of work per size (last {SIZE_OUTCOME_DAYS} days). Shaded ranges = sizing guide, "
                   f"{guide_note}.")
        g1, g2 = st.columns([3, 2])
        with g1:
            if size_visuals["accuracy_fig"] is not None:
                st.plotly_chart(size_visuals["accuracy_fig"], width="stretch")
        with g2:
            st.markdown("**Sizing guide and accuracy**")
            st.dataframe(size_visuals["guide_df"], width="stretch", hide_index=True)

        o1, o2 = st.columns(2)
        with o1:
            st.subheader("SLA Met by Size")
            st.caption("Completed tickets that finished within their SLA. * = fewer than 5 tickets.")
            if size_visuals["outcome_fig"] is not None:
                st.plotly_chart(size_visuals["outcome_fig"], width="stretch")
            st.dataframe(size_visuals["outcomes_df"], width="stretch", hide_index=True)
        with o2:
            st.subheader("Open Work by Size")
            st.caption("In Progress and Backlog tickets by size and priority.")
            if size_visuals["load_fig"] is not None:
                st.plotly_chart(size_visuals["load_fig"], width="stretch")

        link = {"Ticket": st.column_config.LinkColumn("Ticket", help="Open in Jira to set or change the size",
                                                      display_text=r".*/([^/]+)$")}
        st.divider()
        st.subheader("Needs a Size")
        st.caption("Open tickets without an Estimated Size, oldest first.")
        if size_visuals["needs_size_df"].empty:
            st.success("Every open ticket has a size.")
        else:
            st.dataframe(size_visuals["needs_size_df"], width="stretch", hide_index=True, column_config=link)

        st.subheader("Likely Undersized")
        st.caption("In-progress tickets already running longer than their size's range in the guide. Consider re-sizing.")
        if size_visuals["undersized_df"].empty:
            st.success("No in-progress ticket is running past its size's range.")
        else:
            st.dataframe(size_visuals["undersized_df"], width="stretch", hide_index=True, column_config=link)

        with st.expander("All open In Progress and Backlog tickets by size"):
            st.dataframe(size_visuals["detail_df"], width="stretch", hide_index=True, column_config=link)


