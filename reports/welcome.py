"""Welcome page: what the dashboard is, how to read it, and where to go.

Written for the PE team and leadership. The page content lives in the plain lists below, so it is easy to
edit; tests/test_welcome.py fails if a menu page is missing from PAGE_GROUPS or a description points to a
page that no longer exists. No calculations run here, so the page opens instantly.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

WELCOME = "👋  Welcome"
README_URL = "https://gitlab.com/audacy-inc/devops/pe-morning-report/-/blob/main/README.md"

# "Got 2 minutes?" — (page, short name, who it's for, what you get).
QUICK_START = [
    ("📋  Executive Summary", "Executive Summary", "For leadership",
     "The state of the work: headlines, SLA trend, what needs attention."),
    ("🛡️  SLA (Service Level Agreements)", "SLA", "For everyone",
     "What is late or about to be, against the under-10% goal, and what to act on today."),
    ("🧑‍💼  Personal Dashboard", "Personal Dashboard", "For PE engineers",
     "Your open tickets in a suggested working order, with projected dates."),
]

# Every menu page, grouped by the question it answers: (question, [(page, one line)]).
PAGE_GROUPS = [
    ("How are we doing?", [
        ("📋  Executive Summary", "Leadership view: headlines, health tiles, SLA trend, needs-attention list."),
        ("🛡️  SLA (Service Level Agreements)", "Breach rates, what's coming due, searchable action tables."),
        ("📉  Trend", "Are we getting better month over month? Scorecard, 12-month trends, Target date changes."),
        ("🏠  Overview", "The original morning report: quick counts by status (some charts are still placeholders)."),
    ]),
    ("When will it be done?", [
        ("🔄  In Progress", "Likely and safe finish dates for work in progress, checked against the SLA."),
        ("🗂️  Backlog", "When backlog work will start and finish, queued behind each person's current work."),
        ("🔮  Forecast", "Tickets we'll likely deliver in 4, 8 and 12 weeks, and a \"can we commit?\" calculator."),
        ("📈  Capacity", "Delivery vs demand, room for more work, where time goes, load balance."),
    ]),
    ("Where is work stuck?", [
        ("✅  Validating", "Finished work waiting for the requester to confirm, longest wait first."),
        ("🚧  Blocked & On Hold", "Blocked and On Hold tickets, how long, and for whom."),
        ("📊  Distribution of Ticket's Age", "How old open work is against its SLA, and where it has gone quiet."),
        ("📅  Tickets Older Than 90 Days", "Long-running open work, Epics and tickets separately."),
    ]),
    ("Who does what, and how do we work?", [
        ("🧭  Suggested Assignments", "A suggested owner for each new ticket, with a backup and the reason."),
        ("🧑‍💼  Personal Dashboard", "One person's queue in suggested order, with a working-day calendar."),
        ("👤  Distribution per Business Leader", "Service per requesting business lead: requests, delivery, wait, SLA."),
        ("💬  Teams Conversations", "What ticket comments say: friction that costs time, response times, comment coverage."),
        ("⚡  Velocity", "Lead time we can promise requesters, waiting vs working time, whether sizes predict effort."),
        ("📏  Distribution of Ticket by Estimated Size", "Sizing coverage, a sizing guide from our own work, size accuracy."),
        ("🎯  Probability of completion on time", "Legacy machine-learning estimate of on-time completion; treat as indicative."),
    ]),
]

PRINCIPLES = [
    ("One set of definitions", "Every page counts tickets, completions and SLA dates the same way, so numbers agree "
                               "from page to page. Features, Initiatives and Release Management (CAR) tickets are "
                               "left out of PE ticket metrics."),
    ("Business days", "Like our SLAs, times skip weekends and company holidays."),
    ("Likely vs safe", "\"Likely\" (P50) is a coin flip; \"safe\" (P85) is the date to commit to."),
    ("Tested against reality", "Forecasts were re-run at past dates and compared with what happened; the pages say "
                               "how accurate they are."),
    ("Recommends, doesn't act", "Nothing changes in Jira unless an engineer turns writing on, on their own computer, "
                                "reviews the change and confirms it. Every change is commented and can be undone."),
]

GLOSSARY = [
    ("SLA", "Business days allowed by Priority × Size (unsized tickets count as Medium). Goal: under 10% breached."),
    ("SLA clock / Target start", "The SLA clock starts at a ticket's Target start in Jira. No Target start, no clock: "
                                 "the ticket can't be judged."),
    ("SLA clock set after the fact", "Target start was set or moved on or after the day the ticket was done, so its "
                                     "SLA result isn't a real measurement. Flagged on the SLA and Trend pages."),
    ("Breached / Likely late / At risk", "Past the SLA due date / likely finish after it / safe finish after it."),
    ("P50 / P85", "Half of similar tickets finish by P50; 85% by P85."),
    ("Lead time / cycle time", "Created → Done (what the requester experiences) / Target start → Done."),
    ("Rework (Validating)", "Validation that went back to In Progress instead of being closed."),
    ("PE ticket", "Platform Engineering work in the DevOps project; not Features, Initiatives or CAR tickets."),
]


def menu_pages() -> list[str]:
    """Every page the Welcome page describes (for the drift test)."""
    return [page for _, pages in PAGE_GROUPS for page, _ in pages]


def _label(page: str) -> str:
    return " ".join(page.split())


def data_status(df_issues, history, fetched_at) -> dict:
    loaded = isinstance(df_issues, pd.DataFrame) and not df_issues.empty
    return {
        "loaded": loaded,
        "tickets": int(len(df_issues)) if loaded else 0,
        "history": int(len(history)) if isinstance(history, pd.DataFrame) else 0,
        "fetched_at": fetched_at.strftime("%b %d, %Y %H:%M") if isinstance(fetched_at, datetime) else None,
    }


def render_welcome(st, go_to, df_issues=None, history=None, fetched_at=None) -> None:
    """Draw the page. `go_to(page)` switches the sidebar menu to `page`."""
    st.title("👋 Welcome to the PE Delivery Dashboard")
    st.markdown("Platform Engineering's view of our work, built on Jira: **are we meeting our commitments, when will "
                "work finish, how much can we take on, and who should do what.**")

    status = data_status(df_issues, history, fetched_at)
    if not status["loaded"]:
        st.info("**Start here:** click **📥 Fetch All Jira Tickets** in the sidebar. It loads about two years of "
                "tickets, comments and change history (about 1–2 minutes). Every page uses that data.")
    else:
        when = f" · fetched {status['fetched_at']}" if status["fetched_at"] else ""
        history_note = (f"{status['history']:,} status and date changes" if status["history"]
                        else "change history not loaded (history-based views hidden)")
        st.success(f"Data loaded: **{status['tickets']:,} Jira issues** · {history_note}{when}. "
                   "Fetch again any time for the latest.")

    st.subheader("Got 2 minutes?")
    cols = st.columns(len(QUICK_START))
    for col, (page, short, audience, text) in zip(cols, QUICK_START):
        with col.container(border=True):
            st.markdown(f"**{_label(page)}**")
            st.caption(audience)
            st.write(text)
            st.button(f"Open {short}", key=f"welcome_quick_{page}", on_click=go_to, args=(page,), width="stretch",
                      type="primary" if page == QUICK_START[0][0] else "secondary")

    st.subheader("Find the right page")
    st.caption("Every page in the menu, grouped by the question it answers.")
    left, right = st.columns(2)
    for i, (question, pages) in enumerate(PAGE_GROUPS):
        with (left if i % 2 == 0 else right).container(border=True):
            st.markdown(f"**{question}**")
            for page, text in pages:
                a, b = st.columns([5, 2], vertical_alignment="center")
                a.markdown(f"{_label(page)}  \n<span style='opacity:0.7;font-size:0.9em'>{text}</span>",
                           unsafe_allow_html=True)
                b.button("Open", key=f"welcome_open_{page}", on_click=go_to, args=(page,), width="stretch")

    st.subheader("How to read the numbers")
    cols = st.columns(len(PRINCIPLES))
    for col, (title, text) in zip(cols, PRINCIPLES):
        with col:
            st.markdown(f"**{title}**")
            st.caption(text)

    with st.expander("Key terms"):
        st.markdown("\n".join(f"- **{term}**: {text}" for term, text in GLOSSARY))

    with st.expander("Where the data comes from"):
        st.markdown(
            "- **Jira**: the DevOps and Release Management (CAR) projects, tickets created in the last 24 months, "
            "with their human comments (automation comments are left out).\n"
            "- **Change history**: status, Target start and Target end changes, loaded with every fetch.\n"
            "- **Company calendar**: weekends and company holidays are skipped in every business-day count.\n"
            "- **Refresh**: data is as fresh as the last **Fetch All Jira Tickets**; nothing updates on its own.\n"
            f"- **More detail**: the [README]({README_URL}) explains every page and metric. A technical white paper "
            "and a two-page leadership brief are available from Platform Engineering."
        )
