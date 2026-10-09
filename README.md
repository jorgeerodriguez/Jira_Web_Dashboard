# Jira Web Dashboard (Streamlit)

Platform Engineering Jira reporting dashboard built with Python + Streamlit.

It supports:
- live Jira connection validation
- full ticket fetch + dataframe build (DEVOPS + CAR)
- multiple analytics reports (capacity, trend, velocity, SLA, forecast, etc.)
- ML-based **Probability of completion on time** with robust validation-delay math
	- fractional-day validation delays with outlier trimming
	- Bayesian-smoothed assignee/priority on-time rates
	- continuous schedule-adherence feature to capture how late a ticket is
	- target-date-aware predictions, on-time/past-due pie breakdowns, and a rolling trend chart
- **Personal Dashboard** with prioritized attention and Epic-only view
	- **Apparent Tardiness Cost (ATC)** ticket sequencing: suggests a work order that minimizes weighted tardiness across a person's open tickets
	- a suggested working-day calendar visualizing that sequence, color-coded by priority
- **Suggested Assignments**: who should take each unassigned or triage ticket — domain experience, availability, SLA fit and team load combined into an assignment plan with reasons, backups and cross-training suggestions, plus a safe-mode **✏️ Assign in Jira** button — see [How Suggested Assignments works](#how-suggested-assignments-works)
- **SLA**: the daily view of breached, late and at-risk work. Two breach rates against the **under 10%** goal (SLA came due / completed late), a trend, where breaches come from, what's coming due, and two searchable, filterable action tables (**SLA Detail** and **Breached Tickets**) with the latest human comment and a Jira link — see [How the SLA page works](#how-the-sla-page-works)
- **Delivery Forecast**: how many tickets PE will likely deliver in the next 4, 8 and 12 weeks with a calibrated range, a "can we commit to a project?" calculator, a demand vs delivery outlook, and the forecast's own track record — see [How the Delivery Forecast works](#how-the-delivery-forecast-works)
- **In Progress completion forecast**: when each in-progress ticket will finish (P50 likely / P85 safe dates), checked against its **business-day SLA** (Priority × Size) and its Target End Date — see [How the In Progress forecast works](#how-the-in-progress-forecast-works)
- **Backlog forecast**: when each backlog ticket will *start* and finish, queued behind each person's In Progress work in ATC order, with SLA risk, capacity runway and backlog readiness, plus **safe-mode updates of Jira Target dates** from the forecast — see [How the Backlog forecast works](#how-the-backlog-forecast-works)
- **Teams Conversations** from human ticket comments: **comment coverage** to track monthly, the friction themes that cost the most time (with suggested process changes), conversation health, and the phrase of the month — see [How Teams Conversations works](#how-teams-conversations-works)
- **Executive Summary** for leadership: generated headlines, health tiles compared with the previous period, flow of work in vs out, SLA compliance trend, where open work sits and how much is at risk, a short "needs attention" list, and aging — see [How the Executive Summary works](#how-the-executive-summary-works)
- Consistent ticket scope: Features and Initiatives are excluded from ticket metrics — see [What counts as a ticket](#what-counts-as-a-ticket-and-as-completed)
- **Distribution of Ticket's Age**: open-work age against SLA, where work has gone quiet (no human comment), and whether open work is getting older — see [How Distribution of Ticket's Age works](#how-distribution-of-tickets-age-works)
- **Capacity**: weekly delivery vs demand, a Monte Carlo delivery forecast with a "how long for N more tickets?" calculator, where capacity goes (planned vs reactive, priority), and load balance across the team — see [How Capacity works](#how-capacity-works)
- **Distribution per Business Leader**: a service scorecard per requesting business lead (requested, delivered, wait, SLA met, open past SLA, top friction) with demand, SLA and priority charts — see [How Distribution per Business Leader works](#how-distribution-per-business-leader-works)
- **Distribution of Ticket by Estimated Size**: sizing as a practice — coverage against a 90% target, a sizing guide derived from the team's own work, size accuracy, SLA met by size, open work by size, data-backed recommendations, and "Needs a Size" / "Likely Undersized" action lists — see [How Distribution by Estimated Size works](#how-distribution-by-estimated-size-works)
- **Tickets Older Than 90 Days** split into Epics vs. Tickets
- **Velocity**: what lead time we can promise a requester (50/85/95%), waiting vs in-progress time by priority, whether sizes predict effort, an SLA reality check per Priority × Size, and what was delivered — see [How Velocity works](#how-velocity-works)
- **Trend**: an improvement scorecard and 12-month small multiples for delivery, lead and cycle time, predictability, SLA met, urgent and reactive work, comment coverage and people delivering, Target date changes (moves, re-planning after the date passed, SLA clocks set after the fact), plus team contribution over time — see [How Trend works](#how-trend-works)
- **Validating**: work waiting for the requester to confirm — time waiting since entering Validating, a nudge list, validation time and rework trends, by business lead, SLA impact and policy what-ifs — see [How Validating works](#how-validating-works)
- **Change history**: status and Target date history loaded with every fetch (Jira bulk changelog) — see [Change history](#change-history)

---

## Current project structure

```text
Jira_Web_Dashboard/
├── app.py
├── Dockerfile
├── requirements.txt             # Streamlit app
├── requirements-dev.txt         # pytest, httpx
├── README.md
├── config.json                  # local fallback only (do not commit secrets)
├── .env.example
├── .streamlit/
│   └── secrets.toml.example
├── config/
│   ├── __init__.py
│   ├── load_configuration.py
│   └── validate_and_connect_to_jira.py
├── data/
│   ├── __init__.py
│   ├── build_dataframe_new.py
│   ├── fetch_change_history.py  # status / Target date history (bulk changelog)
│   ├── fetch_all_tickets_for_devops.py
│   └── metrics.py
├── reports/
│   ├── __init__.py
│   ├── assignment_report.py     # Suggested Assignments
│   ├── atc_sequence.py          # ATC ordering shared by Personal Dashboard + Backlog
│   ├── change_history.py        # date changes, SLA clock set after the fact, status episodes
│   ├── domains.py               # domain (skill-area) taxonomy, kept identical to darkstar's Intake page
│   ├── jira_dates.py            # safe-mode Target date updates (Backlog page)
│   ├── jira_assign.py           # safe-mode Assign in Jira (Suggested Assignments page)
│   ├── executive_summary.py
│   ├── capacity_report.py
│   ├── trend_report.py
│   ├── velocity_report.py
│   ├── in_progress_report.py
│   ├── validating_report.py
│   ├── backlog_report.py
│   ├── blocked_report.py
│   ├── tickets_distribution.py
│   ├── tickets_older_than_90_days.py
│   ├── distribution_of_tickets_report.py
│   ├── distribution_by_business_leader.py
│   ├── estimated_size_distribution_report.py
│   ├── word_of_the_month_report.py
│   ├── service_level_agreement_report.py
│   ├── forecast_report.py
│   └── probability_completion_report.py
├── darkstar/                    # FastAPI v2 dashboards (own requirements.txt, own venv)
├── tests/                       # pytest suite (darkstar + reports)
│   ├── test_in_progress_forecast.py
│   ├── test_backlog_forecast.py
│   ├── test_word_of_the_month.py
│   ├── test_blocked_on_hold.py
│   ├── test_executive_summary.py
│   ├── test_ticket_age.py
│   ├── test_business_leader.py
│   ├── test_capacity.py
│   ├── test_trend.py
│   ├── test_velocity_flow.py
│   ├── test_forecast.py
│   ├── test_sla.py
│   ├── test_jira_dates.py
│   ├── test_size_distribution.py
│   ├── test_assignments.py
│   ├── test_jira_assign.py
│   ├── test_change_history.py
│   └── ...
└── backup/
```

---

## Run locally

The Streamlit app and darkstar need **separate virtual environments**: darkstar pins
`fastapi==0.115.0`, whose `starlette` is older than Streamlit requires. The Docker image does the
same split.

First-time setup, from project root:

```bash
# Streamlit dashboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# darkstar + the full test suite
python3 -m venv .venv-darkstar
.venv-darkstar/bin/pip install -r darkstar/requirements.txt -r requirements-dev.txt
```

Run the dashboard:

```bash
.venv/bin/python -m streamlit run app.py
```

Open: http://localhost:8501

Notes:
- The project folder lives in Google Drive. Don't let Drive sync `.venv` between machines: a
  virtualenv built on an Intel Mac will not load on Apple Silicon. If imports fail with an
  "incompatible architecture" error, delete `.venv` and recreate it.

---

## Run with Docker

Build image:

```bash
docker build -t jira-web-dashboard:local .
```

Run container:

```bash
docker run --rm -p 8501:8080 --env-file .env jira-web-dashboard:local
```

Open: http://localhost:8501

Notes:
- Container listens on `8080` internally.
- Keep credentials out of the image.

---

## Jira credentials and config precedence

Configuration is resolved in this order:

1. Streamlit secrets (`.streamlit/secrets.toml`)
2. Environment variables (`JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, etc.)
3. `config.json` fallback (project root)

Required keys:
- `jira_server`
- `jira_email`
- `jira_api_token`

Templates provided:
- `.streamlit/secrets.toml.example`
- `.env.example`

---

## Recent updates

- Refactored codebase into package folders: `config/`, `data/`, and `reports/`
- Added/updated report modules under `reports/` and imports in `app.py`
- Added **Personal Dashboard** menu with PE assignee filtering
- Personal Dashboard now:
	- restricts to active statuses (Triage, To Do, In Progress, On Hold, Validating, Tech Discovery Required, Blocked, Staged CAR)
	- excludes `Feature` tickets from **Tickets Requiring Attention**
	- shows only `Feature` tickets in **Epic Ticket Only** table
	- sequences a person's open tickets with the **Apparent Tardiness Cost (ATC)** rule and shows projected start/finish/tardiness per ticket — see [How ticket sequencing works](#how-ticket-sequencing-works-apparent-tardiness-cost)
	- visualizes that sequence as a **Suggested Working-Day Calendar** heatmap, color-coded by priority tier
- Added **Distribution of Ticket by Estimated Size** menu: In Progress vs. Backlog counts/pies by size, plus a Priority × Size risk heatmap
- Added estimated ticket **Size** column to the Backlog and In Progress reports
- Split **Tickets Older Than 90 Days** into separate Epics and Tickets tables (was a single mixed list)
- Added a per-PE-team-member completed-ticket trend chart to the Trend report
- Improved probability model workflow:
	- assignee/priority-aware validation-time offsets
	- training detail table aligned with selected filters
	- smoothed historical on-time rates for assignee and priority
	- continuous schedule-adherence feature for lateness severity
	- added target-date awareness, on-time/past-due pie visualizations, and a rolling completion trend chart
	- normalized heatmap ordering to a consistent priority sequence across reports
- Renamed pages: **Word of the Month** → **Teams Conversations**, **Blocked** → **Blocked & On Hold**
- **Blocked & On Hold** now includes On Hold tickets, with Blocked and On Hold shown separately in the KPIs, both charts and the ticket table
- Rebuilt **Teams Conversations** (formerly Word of the Month) on human ticket comments (comment coverage metric, friction themes, conversation health, phrases); Jira fetch now loads comments and reporter
- Rebuilt the **Backlog** report as a queue-aware start/finish forecast with SLA risk, capacity runway and readiness
- Rebuilt the **In Progress** report as an SLA-aware completion forecast (P50/P85 dates, risk vs SLA and Target End Date, execution velocity by priority per assignee)
- Rebuilt the **Executive Summary** as a leadership view built on the SLA, forecast and conversation reports (headlines, trend tiles, flow, SLA trend, stage risk, needs-attention list, aging)
- **Executive Summary** now counts tickets only (no Features/Initiatives) across every KPI, chart and table, and calculates **Created (24h)** / **Resolved (24h)** from Jira data
- Rebuilt the **Forecast** as a calibrated Delivery Forecast (damped trend + back-tested ranges) shared with Capacity; XGBoost removed
- Jira fetch now loads `statuscategorychangedate` (when a ticket moved to Done)
- Updated Streamlit layout API usage (`width="stretch"` / `width="content"`)
- Fixed Jira fetch JQL lookback syntax (`created >= -730d`)
- Improved config fallback path resolution so root `config.json` is detected

---

## What counts as a ticket and as completed

These rules are shared across reports so the numbers agree:

- **Ticket**: any issue type except **Feature** and **Initiative**. Those are containers tracked
  through their child tickets. A row is excluded when either its `issuetype` or its `status` is
  `Feature`, `Initiative` (or the `Iniciative` misspelling). Applied in the Forecast, Executive
  Summary, In Progress, Backlog, Blocked & On Hold, Distribution of Ticket's Age, Distribution per
  Business Leader, Capacity, Trend, Velocity, SLA, Estimated Size and Teams Conversations reports. The Executive
  Summary, In Progress, Backlog and Blocked & On Hold pages also leave out the people in
  `EXCLUDED_ASSIGNEES`, so their counts agree. The Size Distribution report excludes Features
  Tickets Older Than 90 Days shows Features and Initiatives in a separate Epics table.
- **PE SLA scope**: the SLA (Priority × Size, business days) applies to Platform Engineering tickets.
  Release Management **"Change and Release" (CAR)** tickets follow the release process and have no PE
  SLA: they are never judged against it and are not counted in SLA compliance. The rule is
  `sla_applies()` in `reports/in_progress_report.py` (`SLA_EXEMPT_PROJECTS`, `SLA_EXEMPT_ISSUE_TYPES`).
- **Completed / Resolved**: status `Done`.
- **When it finished**: most Done tickets have no Jira resolution date, so every report uses
  `status_category_changed` (Jira's `statuscategorychangedate`, when the ticket moved to Done), which
  later comments or edits don't move.

---

## Change history

Every **Fetch Jira tickets** also loads the history of three fields, status, Target start and Target end,
for DevOps-project tickets updated in the last 400 days (`data/fetch_change_history.py`). It uses Jira Cloud's
bulk changelog endpoint (`POST /rest/api/3/changelog/bulkfetch`, up to 1,000 issues per request), which adds
about 25–40 seconds to a fetch; it is read-only. Target dates are read as ISO values (`2026-09-11`), not the
display strings (`11/Sep/26`). If the history can't be loaded the fetch still succeeds, the sidebar says so,
and history-based views are hidden.

Shared rules (`reports/change_history.py`):

- A Target date change is **set** (empty → date), **moved** (date → another date) or **cleared**. Dates entered
  on the create screen have no history and are never counted.
- A move is **after the date passed** when it's made after the old date.
- **SLA clock set after the fact**: a completed ticket whose Target start was set or moved on or after the day
  it moved to Done. The SLA clock starts at Target start, so its SLA result was decided after the work
  finished. As of 2026-10-09: 27% of tickets delivered in the last 180 days, rising from 5–6% (Nov–Dec 2025)
  to 30–33% (Jul–Sep 2026); most were set the same day the ticket was closed.
- A **status episode** is one stay in a status, from entering it to the next status change.

---

## How the Executive Summary works

The **Executive Summary** is the leadership view: the state of the work on one page, read top to
bottom. Code: `reports/executive_summary.py`. Risk and forecasts come from the In Progress and Backlog
reports and the conversation signals from Teams Conversations, so its numbers match those pages.
Tickets only (no Features or Initiatives).

### Headlines

Three to seven plain-English bullets generated from the data, marked ✓ good, ! watch, ✖ problem or
• for information. They cover the SLA compliance trend (last *full* month against six months earlier),
throughput against the previous period, whether open work is growing or shrinking, tickets that have
breached or are forecast to miss their SLA, in-progress tickets on track, Blocked/On Hold, and the
biggest friction theme from ticket comments.

### Health tiles

Each tile compares the last *N* days with the *N* days before, where *N* is the sidebar **Lookback**
(1–30 days).

| Tile | Meaning |
| --- | --- |
| Open Tickets | Open now; change since *N* days ago |
| Completed (*N*d) | Moved to `Done`; % change vs previous period |
| Net Flow (*N*d) | Closed minus created. Closed counts every outcome (Done, Released, Will Not Do, Rolled Back). Positive = open work shrinking |
| SLA Compliance | Tickets completed in the period that finished within their SLA (those with a Target start) |
| In Progress On Track | In-progress tickets forecast to finish within SLA (In Progress page) |
| Blocked / On Hold | Current counts |
| Comment Coverage | Completed tickets with a human comment, last 3 months (Teams Conversations page) |

### Sections

- **Flow: Created vs Closed**: weekly, last 12 full weeks; hover shows the closing outcomes. Also
  the last-24h created and resolved counts.
- **SLA Compliance Trend**: monthly share of Done tickets that met their SLA (CAR tickets excluded), against a target line
  (`SLA_TARGET`, default 90%). The current month is marked "so far".
- **Where the Work Is**: open tickets by stage (Backlog, In Progress, Validating, Blocked & On Hold,
  Release), coloured by SLA risk. In Progress and Backlog use their forecasts. Other stages are
  Breached past their SLA due date and At Risk once 80% of the SLA is used. Tickets without a Target
  start, and Release Management (CAR) tickets, are Not assessed.
- **Needs Attention**: the top 10 open tickets that have breached their SLA, are forecast to miss it,
  are Blocked/On Hold past their Target End Date, or have waited in the backlog longer than their
  whole SLA. Most serious first (risk, then priority, then how far past due), each with the reason.
- **Age of Open Work**: open tickets by age band and priority, plus average age by business lead,
  oldest first.
- **Ways of Working**: the biggest friction theme and comment coverage from Teams Conversations.

The old per-assignee age chart was removed from this leadership view. People-level detail is on the
In Progress and Backlog pages. The old business-lead and assignee age charts also showed the
*youngest* groups because of a sort bug; the new chart shows the oldest first.

---

## How the In Progress forecast works

The **In Progress** page answers: *when will each in-progress ticket finish, and will it make its
SLA?* It covers In Progress tickets only (no Features or Initiatives). Code:
`reports/in_progress_report.py`.

### SLA (business days)

The SLA clock starts at the ticket's **Target start** date and counts business days: weekends and
company holidays are skipped, using the holiday calendar in `darkstar/metrics.py`.

| Priority / Effort | Small | Medium | Large | XLarge |
| --- | --- | --- | --- | --- |
| None / Low | 30 | 33 | 37 | 45 |
| Medium | 15 | 18 | 22 | 30 |
| High | 7 | 10 | 17 | 22 |
| Urgent | 1 | 4 | 8 | 16 |

Unsized tickets use the **Medium** column and are shown as `Medium* (assumed)`.

### Forecasting the finish date

1. **Execution velocity.** Learned from the last 365 days of Done tickets, measured in business
   days from Target start to Done. Recent tickets count more (120-day half-life). The baseline comes
   from the most specific group with at least 8 tickets: Priority × Size, then Size, then Priority,
   then team-wide.
2. **Assignee × priority speed.** Each assignee's speed at each priority is estimated relative to
   that baseline and **shrunk toward the team** (empirical Bayes, pseudo-count 5). One slow ticket
   can't mark someone as slow.
3. **Time already spent.** Only comparable past tickets that ran at least as long as this one
   inform how much is left. A ticket older than nearly all comparable history uses a fallback:
   P50 = 0.5 × time spent, P85 = 2 × time spent.
4. **Current load.** A dampened Little's-law factor (between 0.85 and 1.5) stretches the forecast
   when an assignee carries more tickets than their usual concurrent load. Shown as **Load Factor**.
5. **Output.** **P50** is the likely finish date; **P85** is the safe date to commit to. Each
   ticket also gets a **Confidence** level based on how much comparable history it had.

### Risk status

A switch on the page chooses the deadline: **SLA** (default), **Target End Date**, or **Both
(earliest deadline)**. The detail table always shows both statuses.

| Status | Meaning |
| --- | --- |
| ✖ Breached | The deadline has already passed |
| ▲ Likely Late | P50 is after the deadline |
| ! At Risk | P85 is after the deadline |
| ✓ On Track | P85 is on or before the deadline |

SLA is the default because Target End dates are usually set very tight. Over the last 180 days
(as of 2026-09-25), 91% of Done tickets met their SLA but only 60% met their Target End Date, and the
typical planned window was one business day.

### How accurate it is

Back-tested on 243 past tickets, each forecast at a date when it was still in progress using only
history available then: **61% finished by P50 and 93% by P85** (targets 50% / 85%), with a median
error of 1 business day. The forecast leans slightly cautious. The back-test only includes tickets
that eventually finished, so be careful with very long-running tickets.

### What the page shows

- KPIs: total in progress, On Track, At Risk / Likely Late, Breached, the P85 date for everything
  in progress, and how many tickets are unsized
- **Completion Forecast** timeline: one bar per ticket from Target start to P85, coloured by risk,
  with markers for P50, SLA due and Target End Date
- **Risk by Assignee** and **In Progress by SLA Cell** (Priority × Size with each cell's SLA)
- **Execution Velocity by Priority per Assignee** heatmap (typical business days, with sample sizes)
- **In Progress Forecast Detail** table, plus the unchanged **All In Progress Tickets** table

---

## How the Backlog forecast works

The **Backlog** page answers: *when will each backlog ticket start and finish, and will it make its
SLA?* Backlog means `To Do` and `Tech Discovery Required` tickets (no Features or Initiatives). Code:
`reports/backlog_report.py`, reusing the In Progress model.

### Queue simulation

1. **Queue order.** Each person's backlog is ordered with the Apparent Tardiness Cost rule
   (`reports/atc_sequence.py`), so it matches the Personal Dashboard's suggested order. Shown as
   **Queue #**.
2. **Slots.** Each person works on as many tickets at once as they typically do: their average
   work in progress over the last 180 days, rounded, and at least 1.
3. **In Progress first.** Their current In Progress tickets hold those slots until they finish,
   using the remaining-time forecast from the In Progress page.
4. **Backlog next.** Each backlog ticket, in queue order, starts when a slot frees up, but not before
   its Target start. How long it takes is drawn from comparable Done tickets (same execution-velocity
   model as In Progress; no load factor, because the queue itself models the load).
5. **Monte Carlo.** The whole queue is simulated 1,000 times (fixed seed, so a rerun gives the same
   dates). **Projected Start** and **Finish P50** are the medians; **Finish P85** is the safe date.

### SLA and risk

- The SLA clock starts at **Target start** (same Priority × Size table, business days), so
  **SLA Due = Target start + SLA**. A Target start that has already passed while the ticket is still
  in the backlog uses up SLA time.
- Risk uses the same statuses and switch as In Progress (SLA by default).
- Tickets with no assignee or no Target start are **○ Not assessed**: there's no queue to put them
  in, or no SLA clock to judge them against. The **Missing** column lists what to fill in.
- **Start Slip** is how many business days after its Target start a ticket is projected to start.
- **Waiting > SLA** counts tickets that have already sat in the backlog (business days since
  creation) longer than their whole SLA.

### What the page shows

- KPIs: backlog tickets, Ready to Start (assigned, sized, Target start and Target end all set),
  Should Have Started, At Risk / Likely Late, Breached, Unassigned, Waiting > SLA
- **Capacity Runway by Assignee**: business days until each person is free, split into In Progress
  and backlog work, with a P85 "free by" marker
- **Backlog Readiness**: how many tickets are assigned, sized, and have Target start and end dates
- **Projected Start and Finish** timeline with Target start, SLA due and Target End markers
- **Backlog by SLA Cell**, **Waiting Longer Than Their SLA**, the **Backlog Forecast Detail**
  table, and the unchanged **All Backlog Tickets** table

### How reliable is Projected Start?

Back-tested in October 2026 with Jira change history: at five past dates (Jul 20 – Sep 14) the backlog
was rebuilt as it was then (statuses, assignees and Target starts at the time), the forecast was run
as of that day, and each Projected Start was compared with when the ticket actually left the backlog
(266 predictions, 157 tickets).

| Prediction | Typical miss | Within 5 bd | Started by that date | Bias |
| --- | --- | --- | --- | --- |
| Projected Start (P50) | 8 bd | 39% | 52% | none |
| Old P85 start | 11.5 bd | 30% | 65% (should be 85%) | early |
| Target start in Jira | 11 bd | 33% | 12% | starts ~11 bd later |

- **Projected Start** is an honest median but a ±3-week estimate. It is clearly better than the
  Target start in Jira, which tickets usually start well after; Target dates were edited 686 (start)
  and 1,082 (end) times across these tickets.
- **Safe Start** replaces the old P85: Projected Start + 16 business days (queue #1–3) or + 9 (#4+),
  the margin that made 85% of tickets start by it (84% out of sample). `SAFE_START_MARGIN_BD`.
- **Start Confidence** by queue position: High (#1, typical miss about 4 bd), Medium (#2–3, about
  8 bd), Low (#4+, about 9 bd).
- **Target Start Slipped** marks backlog tickets whose Target start has already passed.

### Updating Target dates (safe mode)

The **✏️ Update Target dates** button next to *Backlog Forecast Detail* opens a review dialog. Code:
`reports/jira_dates.py`.

1. **Proposals**: tickets whose Target start has passed, is missing, or is more than 2 business days
   before the projected start, or whose forecast finish is after the Target end. Proposed Target
   start = Projected Start (or Safe Start); proposed Target end = forecast finish (P85).
2. **Review**: nothing is selected. Tick rows; the new dates are editable. A dry run shows exactly what
   would be sent.
3. **Validation** blocks the batch on any error (missing dates, end before start, start in the past,
   more than 25 tickets) and warns about non-business days and moves of more than 20 business days.
4. **Confirmation**: type `UPDATE n` to enable the button.
5. **No overwriting**: each ticket is re-read just before writing and skipped if its dates changed in
   Jira since the data was loaded.
6. **Audit**: every updated ticket gets a Jira comment (old → new dates, forecast date, batch id), and
   each batch is appended to `.dashboard_audit/jira_date_changes.jsonl` (git-ignored; override with
   `JIRA_AUDIT_LOG`).
7. **Undo last batch** (type `UNDO`) restores the old dates, but only where Jira still holds the dates
   the batch wrote.

**Turning it on.** Writes are **off by default**: the **✏️ Update Target dates** button is **disabled** (its
tooltip says why), and the dialog refuses to show proposals without permission. To enable them on your
own machine, set this line in the project's `.env` file (git- and Docker-ignored, so it never leaves your
computer):

```bash
JIRA_WRITE_ENABLED=true
```

The file is read fresh on every page refresh, so changing the line to `false` (or deleting it) disables
the button without restarting. The button is also disabled for anyone not opening the app from the
machine running it (localhost), even when the switch is on. `true`, `1`, `yes` or `on` enable it; an empty value, any other
value, a missing line or a missing file means off. If `.env` has no value for it, an environment variable
of the same name is used instead, e.g. for one run:

```bash
JIRA_WRITE_ENABLED=true .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1
```

`--server.address 127.0.0.1` keeps the app reachable only from your own computer while writes are on.

Writes are also limited to the machine running the app (`localhost`). The shared deployment has no
login, so anyone with its URL would otherwise update Jira as the account in the configured token.
`JIRA_WRITE_ALLOW_REMOTE=true` lifts the localhost limit; don't set it on a shared deployment.

**SLA guard.** The SLA clock starts at Target start, so moving a Target start later also moves the
SLA due date. By default the SLA page judges re-planned tickets on their **original** Target start (from
the audit log) in both breach rates and the Breached Tickets table, so re-planning can't lower the
breach rate. A toggle shows the re-planned view.

## How Teams Conversations works

The **Teams Conversations** page (formerly *Word of the Month*) reads ticket comments to show how we work and where time gets lost.
Code: `reports/word_of_the_month_report.py`.

### Comments

- The Jira fetch loads each ticket's comments into a `comments` column (author, time, text). Bot
  accounts such as *Automation for Jira* are dropped at load time. They were about 30% of all
  comments. `bot_comment_count` and `comment_total` keep the counts.
- Jira's search returns up to 20 comments per ticket. About 1% of tickets have more.
- Loading comments adds about 20 seconds to a full fetch.

### Comment coverage (the metric to track)

Measured on tickets **completed** in each month:

- **Comment Coverage**: share with at least one human comment.
- **Assignee Commented**: share where the assignee left a comment (a closing note).

The page shows both as a 12-month trend against an adjustable target (default 80%), the change
against the previous period of the same length, and breakdowns by business lead and assignee.
The per-assignee view is a documentation habit, not a performance score. As of 2026-09-28,
coverage for Jul–Sep was 65% and Assignee Commented was 52%.

### Friction themes

Keyword rules in `THEMES` (edit them freely) tag a ticket when any human comment matches:

| Theme | Example words | Suggested change |
| --- | --- | --- |
| Waiting / blocked | waiting on, blocked, dependency, on hold | Surface dependencies at intake; use the Blocked status |
| Access / permissions | access, permission, IAM, VPN, SSO | Self-service or pre-approved access roles |
| Approval | approve, sign-off | Name the approver at intake; pre-approve routine changes |
| Clarification | clarify, more details, requirements | Required fields in the intake template |
| Rework / rollback | rollback, revert, reopen, not working | Validation step before release |
| Incident / outage | outage, incident, sev, downtime | Post-incident review and preventive ticket |
| Chasing / follow-up | any update, following up, bump | *(symptom, not ranked)* |

- **Extra days**: each tagged ticket's cycle time (Target start → Done, business days) minus the
  median for similar tickets (same Priority × Size, falling back to priority, then all) without
  the theme. **Total extra days** ranks the themes, and the top three *causes* become the page's
  recommendations.
- **Chasing** counts only when the **requester** posts the ping. It is a symptom of slow tickets,
  so it is shown but never recommended.
- Long tickets collect more comments, so a theme is **associated** with delay, not proven to cause it.

Themes can be broken down by business lead, issue type or priority, and shown month by month.

### Conversation health

- **Median First Reply**: business hours (08:00–17:00, company holidays excluded, via
  `darkstar/metrics.py`) from ticket creation to the first human comment by someone other than the
  requester.
- **Back-and-forth**: how many times the speaker changes in a ticket's comments, compared with
  cycle time.
- A per-assignee table shows reply time and back-and-forth.

### Phrases

Two-word phrases from human comments, after removing markup, links, code blocks, @mentions,
ticket keys and people's names. **Emerging** phrases are used much more than in the previous three
months, adjusted for overall comment volume (comments have grown longer). The top emerging phrase is
the **Phrase of the Month**.

The old version's VADER sentiment and word cloud were removed. They ran on ticket titles, because
comments weren't loaded, and general-purpose sentiment misreads normal DevOps words such as
"kill", "fail" and "block".

---

## How Distribution of Ticket's Age works

The **Distribution of Ticket's Age** page shows how old open work is *against its SLA* and where it
has gone quiet. Code: `reports/distribution_of_tickets_report.py`. Open tickets only, tickets-only,
ages in business days, grouped into the same stages as the Executive Summary (so its open and
past-SLA counts match).

- **KPIs**: open tickets, median age (with the 75th percentile in the tooltip), tickets past their
  SLA, tickets silent for `SILENT_THRESHOLD_BD` (10) or more business days, and the oldest ticket.
- **Age Against SLA**: per stage, each ticket's share of its SLA already used (business days since
  Target start ÷ Priority × Size SLA), with a dashed line at 100%. Tickets without a Target start,
  and Release Management (CAR) tickets, are not shown.
- **Where Work Has Gone Quiet**: per stage, business days since the last human comment (or since
  creation when nobody has commented), in bands: under 5, 5–9, 10–19, 20+.
- **Is Open Work Getting Older?**: median and 75th-percentile age of the tickets that were open at
  the end of each of the last 12 weeks, reconstructed from created and closed dates.
- **By status** (expander) and a table of every open ticket, past-SLA first, then by SLA used and
  silence.

It replaced the earlier box plot and violin chart, which showed calendar days since creation by
status, twice, with fixed 30/60/90-day bands that ignored each ticket's SLA. The violin chart also
used a hard-coded black background.

---

## How Distribution per Business Leader works

The **Distribution per Business Leader** page shows the service each requesting business lead gets
from Platform Engineering. Code: `reports/distribution_by_business_leader.py`.

- **Scope**: PE tickets only. Features, Initiatives and Release Management (CAR) tickets are left out.
  The page used to reassign every CAR ticket's business lead to one person; that override is gone.
- **PE internal work**: business leads in `INTERNAL_LEADS` (currently Jorge Rodriguez) are shown as
  one **Platform Engineering (internal)** row. It is about two-thirds of the volume, so the charts
  leave it out unless **Include Platform Engineering (internal) in charts** is on. Tickets with no
  business lead are shown as **Unknown**, a data gap to fix at intake.
- **Period**: the selected months. *Requested* = created in the period; *Delivered* = moved to Done
  in the period. Open columns are as of today.

| Scorecard column | Meaning |
| --- | --- |
| Requested / Delivered / Won't Do | Tickets created / completed (Done) / closed as Will Not Do in the period |
| Median Wait (bd) | Business days from request (created) to Done, for delivered tickets |
| SLA Met % (SLA Sample) | Delivered tickets with a Target start that finished within their SLA |
| Open Now / Open Past SLA | Open tickets today, and those already past their SLA |
| Oldest Open (days) | Age of the lead's oldest open ticket |
| Top Friction in Comments | Most common friction theme (causes only) in delivered tickets' comments, with its share |

Charts: requested vs delivered per lead, monthly demand (last 12 months, top 5 leads and Other),
SLA met by lead against the 90% target (hollow markers for fewer than 5 delivered tickets), median
wait by lead, priority mix of requests, and open work by SLA risk. The KPIs include the share of
requests that are PE internal and the share with no business lead.

It replaced two pie charts (one slice per business lead) and a per-month grid of stacked bars,
which only counted tickets created.

---

## How Suggested Assignments works

The **🧭 Suggested Assignments** page proposes an owner for each open PE ticket that is unassigned or in
Triage / Reviewing. It uses Jira data only (no darkstar data), and nothing is written to Jira unless you
use **✏️ Assign in Jira** (below). Code: `reports/assignment_report.py`.

**Candidates** are people who completed at least 5 PE tickets in the last 90 days, minus
`DEPARTED_MEMBERS` (people who have left PE; their history still counts everywhere else).

Each candidate is scored for each ticket:

| Signal | How |
| --- | --- |
| Domain experience | Recent completed (1.0) and active (0.5) tickets in the ticket's primary domain, halving every 120 days. Domains are tagged from the title **and human comments** (79% of PE tickets get a domain, against 67% from titles alone) |
| Availability and SLA fit | The Backlog queue simulation with the ticket added to the candidate's queue in ATC order, after their In Progress work, at their own speed for that priority. Gives a likely start and a P85 finish, and whether that finish beats the SLA due date (or, with no Target start yet, whether the work fits within the SLA) |
| Load | A penalty when someone already holds 1.3× their usual WIP, and a smaller one for each ticket the plan has already given them |

Owners who fit the SLA rank first, then by score (60% experience, 40% availability, minus the
penalties). Tickets are planned in **ATC order**, and each suggestion is added to that person's queue
before the next ticket is scored, so the plan spreads work. Each ticket shows a **Suggested** owner with
the reason, a **Backup**, and a **Stretch** (someone with some experience in the domain, for
cross-training). **Compare Options** lists every candidate for a ticket.

Also on the page: **Who Knows What** (recent tickets per domain and person) and **Domains Leaning on
One Person** (the top person's share of each domain, for cross-training).

**Taxonomy.** `reports/domains.py` is a copy of darkstar's Intake taxonomy (33 domains);
`tests/test_assignments.py` fails if the two drift apart, so edit both together.

**Limits.** A back-test (671 tickets, October 2026) found that today's assignee was the top domain expert
26% of the time and in the top 3 49% of the time, so work isn't routed mainly by expertise today. The
page balances expertise against availability and the SLA rather than copying past assignments. Whether
following it improves SLA results can only be measured after using it.

### Assigning in Jira (safe mode)

The **✏️ Assign in Jira** button next to *Assignment Plan* opens a review dialog. Code:
`reports/jira_assign.py`. It uses the same switch and safeguards as
[Updating Target dates](#updating-target-dates-safe-mode): **off by default**, disabled unless
`JIRA_WRITE_ENABLED=true` and the app is opened from localhost.

1. **Review**: one row per planned ticket, nothing selected. **Assign To** defaults to the suggestion and
   can be changed to any core team member. A dry run shows exactly what would be sent.
2. **By account id, never by name**: people are assigned by the Jira account id read from their own
   tickets (`assignee_account_id`). Someone with no known id can't be chosen; fetch Jira tickets again.
3. **Validation** blocks the batch on any error (no one chosen, no account id, more than 25 tickets) and
   warns when a ticket is reassigned from its current owner or someone other than the suggestion is chosen.
4. **Confirmation**: type `ASSIGN n` to enable the button.
5. **No overwriting**: each ticket's assignee is re-read just before writing; the ticket is skipped if it
   changed in Jira since the data was loaded.
6. **Audit**: every assigned ticket gets a Jira comment (who, why, previous owner, batch id), and each row
   is appended to the same audit log as Target date changes (action `assign`).
7. **Undo last batch** (type `UNDO`) restores the previous owner (or unassigns), but only where Jira
   still holds the person the batch assigned.

---

## How the SLA page works

The **SLA** page is the daily view of where work is breached, late or at risk, and of the breach rate
against the team's goal of **under 10%**. Code: `reports/service_level_agreement_report.py`. SLAs are the
Priority × Size table (`SLA_BUSINESS_DAYS`) in business days from **Target start**, unsized = Medium.
PE tickets only; CAR tickets have no PE SLA. Tickets closed as Will Not Do or Rolled Back are never
judged: closing stale work is hygiene, not a late delivery.

### Two breach rates, both against the goal

| Breach rate | Of which tickets | Breached when | Why |
| --- | --- | --- | --- |
| **SLA came due** | SLA due date in the window | Done after the due date, **or still open past it** | Strict: a breach can't hide until the ticket is finished |
| **Completed late** | Moved to Done in the window | Done after the due date | Same as the Trend and Executive Summary pages |

The window is 7, 30 (default) or 90 days. Each tile shows the change against the previous window,
✓/✖ against the goal, and "x of n tickets". As of 2026-10-02: last 30 days 10.3% (above goal) and 5.8%;
last 90 days 9.4% and 6.1%.

Other KPIs: open tickets already breached, at risk or likely late, coming due in the next 10 business
days, and **No SLA Clock** (open tickets without a Target start, a data gap to fix in Jira).

### Filters, charts and tables

- **Filters** (assignee, business lead, priority, stage of open work) narrow every number, chart and table.
- **Breach Rate Trend**: monthly, both definitions, with the 10% goal line.
- **Where Breaches Come From**: share of tickets that missed their SLA by Priority × Size (last 90 days).
- **Coming Due**: open tickets overdue or due in the next 10 business days, by forecast risk.
- **Open Work by Stage**: open tickets by stage and SLA risk, as on the Executive Summary.
- **SLA Detail**: open tickets that are breached, likely late or at risk (optionally all open tickets),
  most urgent first. Columns include business days to SLA (negative = overdue), SLA due, forecast finish
  (P85), SLA used, priority, size, stage, Jira status, assignee, business lead, Target End, last comment
  date, business days silent and the latest human comment (author and excerpt).
- **Breached Tickets**: still-open breached tickets first (most overdue first), then, optionally, tickets
  completed late in the window, with days overdue, SLA due, completion date and the latest comment.
- Both tables have a **search box**, a **CSV download** and a Jira link on every ticket.

### SLA clock set after the fact

With change history loaded, completed tickets whose Target start was set or moved on or after the day they
were done are flagged: a warning under the KPIs (how many of the window's completed tickets, and how many show
as met), an **SLA Clock** column in **Breached Tickets**, and a searchable **SLA Clock Set After the Fact** table.
The toggle **Leave out tickets whose SLA clock was set after the fact** removes them from both breach rates
(off by default, so the numbers match the other pages). As of 2026-10-09, last 30 days: 110 of 329 completed
tickets were flagged, all showing as met; leaving them out moved the breach rates from 7.5% to 12.1% (SLA came
due) and from 6.6% to 10.0% (completed late).

It replaced a report that used a flat 90-calendar-day SLA (or Target End − created) from creation, only
looked at currently open tickets, and used red-green charts.

---

## How Validating works

The **Validating** page shows work that is finished and waiting for the requester to confirm it. Code:
`reports/validating_report.py`. PE tickets only; business days; built on the [change history](#change-history),
where each stay in Validating is an episode.

| KPI | Meaning |
| --- | --- |
| In Validating Now | PE tickets in Validating today |
| Waiting Now (median) | Business days since each ticket entered Validating (not since it was created) |
| Waiting 5+ bd | Tickets to nudge (`NUDGE_BD`) |
| Validation Time (P85, 90d) | 85% of validations that ended in the last 90 days took at most this; vs the 90 days before |
| Sent Back (90d) | Validations that went back to In Progress / To Do / Triage instead of being closed (rework) |

- **Waiting for Confirmation**: tickets in Validating, longest wait first, with the round (times in
  Validating), SLA due and business days left, requester, business lead and latest human comment.
- **Validation Time by Month** (median and P85) and **Sent Back to Be Worked On** (monthly rework rate).
- **How Long Validations Take** (distribution) and **By Requesting Business Lead** (median time to confirm).
- **SLA Impact and Policy What-ifs** (views only): the completed-late rate if the SLA clock paused during
  Validating, how many late tickets were late only because of validation time, and how many validations a
  "close after 5 / 10 / 15 business days without a reply" rule would have affected.

As of 2026-10-09: 37% of PE tickets go through Validating; median 1 business day, P85 3, about half the same
day; 8% are sent back; 15 of 97 late tickets in the last 180 days were late only because of validation time.
Without change history the page shows only the list of tickets in Validating.

It replaced counts by assignee and business lead, an "oldest" chart based on days since creation, and a risk
pie based on Target end (the work is already done at this stage), which also counted Features and CAR tickets.

---

## How the Delivery Forecast works

The **Forecast** page ("Delivery Forecast") answers *how many tickets will we likely deliver, and can
we commit to a project?* Code: `reports/forecast_report.py`, which is also the engine behind the
Capacity page's forecast. PE tickets only, Done dated when it moved, full Monday–Sunday weeks (the
current week is never used).

### Method, chosen by back-testing

- **Central forecast: a damped trend.** A line is fitted to the log of weekly delivery over the last
  12 weeks (`TREND_WEEKS`) and projected forward with the slope shrinking 20% each week (`DAMPING` =
  0.8), so growth is assumed to slow rather than continue forever.
- **Range: calibrated from past errors (conformal intervals).** The forecast is re-run at every past
  week using only data known at the time (`backtest`), compared with what actually happened, and the
  10th–90th percentiles of the last 52 errors per horizon set the range. Errors on neighbouring weeks
  overlap, so this nominal 80% band covers about **7 in 10** outcomes out of sample. With fewer than 12
  past errors, the range falls back to resampling recent weeks, and the page says so.
- **The central estimate is not shifted by past errors.** Back-tested, doing that was no better
  calibrated and less accurate, and it assumed recent growth would continue.

Back-test on this team's history (as of 2026-10-02, 40 forecasts each):

| Horizon | Actual inside the range | Typical error of the central forecast |
| --- | --- | --- |
| 4 weeks | 80% | ±23% |
| 8 weeks | 70% | ±25% |
| 12 weeks | 62% | ±27% |

When the actual fell outside the range it was usually *above* it, because delivery grew quickly
during 2026. The earlier XGBoost model was removed: it was trained on about 12 monthly rows, its
multi-step forecast shifted every feature (not just the lags), it used day-of-week features on
month-start dates, and its ±MAE band could go negative. `xgboost` is no longer a dependency.

### What the page shows

- **Headline** in plain English, and cards for the next 4, 8 and 12 weeks (likely value, range, end date).
- **Weekly Delivery: History and Forecast**: 26 weeks of actuals, then 12 weeks of forecast with its range.
- **By When? Cumulative Delivery**: total tickets delivered from today, with the range.
- **Can We Commit to a Project?**: project size and the share of team capacity it can use give a
  *likely* date and a *safe to commit* date (the low end of the range).
- **Demand vs Delivery Outlook**: requested vs delivered per week with forecasts. Requests are harder to
  forecast (typical error about ±33%), so the page treats that line as a guide.
- **How Accurate Has This Forecast Been?**: each past 4-week forecast and its range at the time
  against what actually happened, with the track record in the caption.

---

## How Capacity works

The **Capacity** page answers *how much do we deliver, does it keep up with demand, how much more can
we take on, where does the time go, and is the load balanced?* Code: `reports/capacity_report.py`.
PE tickets only (no Features, Initiatives or CAR tickets). *Delivered* = moved to Done, dated when it
moved. Weeks run Monday to Sunday, and only full weeks are used.

| KPI | Meaning |
| --- | --- |
| Delivered / Week | Average over the last 4 full weeks, % change vs the 4 before |
| Engineers Delivering | Average number of people who delivered at least one ticket per week |
| Per Engineer / Week | Delivered per week ÷ engineers delivering |
| Weeks of Work Queued | Tickets in Backlog or In Progress ÷ delivered per week |
| Demand / Capacity | Requested ÷ delivered, last 4 weeks; the delta shows spare tickets per week (negative = demand outrunning delivery) |

- **Demand vs Capacity**: weekly requested vs delivered for the last 26 weeks, with 4-week averages.
- **How Much Can We Deliver?**: the next 4, 8 and 12 weeks from the
  [Delivery Forecast](#how-the-delivery-forecast-works) engine (likely total and the low end of its
  range), so Capacity and Forecast always show the same numbers. The **"How long for N more
  tickets?"** calculator gives likely and safe weeks. Both use the whole team's pace, which is shared
  with incoming demand, so a new project only gets the spare capacity unless something else is
  deprioritised.
- **Where Capacity Goes**: monthly share of delivered tickets by work type (Planned = Story, Task,
  Sub-task; Reactive = Bug, Hotfix, Incident, Support, Security) and by priority, last 6 months, with
  the share of sized tickets.
- **Load Balance**: tickets in progress per person (with the team's typical level) and each person's
  share of delivery over the last 8 weeks (with an even-split line). It's meant for balancing work,
  not for judging individuals. The charts show the core team (work in progress, or 4+ delivered in
  8 weeks); everyone is in the table, along with weekly detail.

It replaced a "Total Tickets Worked per Year" chart (any ticket *updated* in a year, with only 24
months of data) and monthly created vs completed bars that counted Features and CAR tickets and dated
completions by last update.

---

## How Trend works

The **Trend** page answers *are we getting better, month over month?* Code: `reports/trend_report.py`.
PE tickets only (no Features, Initiatives or CAR tickets), each counted in the month it moved to Done.
Times are in business days. Weekly flow and the delivery forecast are on the Capacity page.

| Measure | Meaning | Better when |
| --- | --- | --- |
| Delivered | Tickets moved to Done | up |
| Lead Time (median) | Created → Done | down |
| Lead Time (85th pct) | 85% of tickets finish within this; how predictable delivery is | down |
| Cycle Time (median) | Target start → Done (tickets with a Target start) | down |
| SLA Met | Completed within their SLA (target 90%) | up |
| Urgent Share | Delivered tickets that were Urgent | down |
| Reactive Share | Bugs, Hotfixes, Incidents, Support, Security | down |
| Comment Coverage | Completed tickets with a human comment (target 80%) | up |
| People Delivering | People who delivered at least one ticket | neutral |

- **Improvement Scorecard**: each measure's last *full* month against the average of the three months
  before it. The delta is green when the measure moved the right way.
- **12-Month Trends**: one small chart per measure (12 full months plus the current month, marked
  "so far" with a hollow marker), with target lines.
- **Target date measures** (when change history is loaded), per month of delivery: **Target Date Moves per
  Ticket**, **Delivered Without Date Moves**, **Moves After the Date Passed** and **SLA Clock Set After the
  Fact**. They join the scorecard and the small multiples.
- **Target Date Changes** section (tickets delivered in the last 180 days): KPIs (moves per ticket, never
  moved, moves pushing later, moved after the date passed, SLA clock set after the fact), **Outcomes by How
  Often Dates Moved** (SLA met and Target end met by 0 / 1 / 2–3 / 4+ moves), moves per ticket by priority and
  size, and **Most Re-planned Open Tickets** (2+ moves). As of 2026-10-09, 90% of moves pushed the date later
  and 67% came after the date had passed; SLA met was 97% with no moves and 62% with 4+ (association: late
  tickets also get re-planned).
- **Team Contribution Over Time**: delivered tickets per person per month for people with 10+
  delivered in the window. It's meant for spotting ramp-ups, gaps and load, not for judging
  individuals.
- **Monthly detail**: every measure per month, with the SLA sample size.

It replaced a dual-axis flow chart, a "cycle time" that was really created → last update, a
status-mix chart that grouped *current* statuses by last-update month, and a 16-line per-engineer
chart.

---

## How Velocity works

The **Velocity** page answers *how fast does work flow from request to done, and what slows it?*
Code: `reports/velocity_report.py`. PE tickets only, **completed** in the last 90 days, picked by the
date they moved to Done, so slow tickets are counted too. Times are in business days.

| Measure | Meaning |
| --- | --- |
| Lead time | Created → Done: what the requester experiences |
| Waiting | Created → Target start |
| In progress | Target start → Done (from creation when Target start is earlier) |
| Flow efficiency | In-progress time ÷ lead time, summed over tickets with a Target start |

- **What We Can Promise a Requester**: the lead-time distribution with the 50%, 85% and 95% lines.
  For example, "85% of requests are done within 14 business days" (as of 2026-10-02).
- **Waiting vs In Progress, by Priority**: the share of lead time spent waiting to start vs in
  progress, with ticket counts and median lead time per priority.
- **Do Sizes Predict Effort?**: time in progress by estimated size, with the unsized share.
- **SLA Reality Check**: for each Priority × Size, the 85th-percentile time in progress as a share of
  that cell's SLA. Orange (over 100%) means the SLA is tighter than what usually happens; blue (well
  under) means it is loose. Cells with fewer than 5 tickets are marked `*`. As of 2026-10-02, Urgent
  and High SLAs were tighter than reality and Low/None SLAs very loose, which is useful input for
  tuning `SLA_BUSINESS_DAYS`.
- **What We Delivered**: tickets delivered by priority and size, plus a per-priority table.

Per-person speed by priority is on the In Progress page. The page replaced an "execution velocity"
that was created → last update and a selection of tickets *created* in the last 90 days (which hid
slow work), shown as averages in per-person box plots and red-green heatmaps.

---

## How Distribution by Estimated Size works

The **Distribution of Ticket by Estimated Size** page treats sizing as a practice to improve: how much
work is sized, whether sizes match the real effort, and how each size performs. Code:
`reports/estimated_size_distribution_report.py`. PE tickets only; *work* = business days from Target
start to Done (from creation when Target start is earlier).

### KPIs

| KPI | Meaning |
| --- | --- |
| Open Tickets Sized | Open PE tickets with an Estimated Size, against the 90% target (`COVERAGE_TARGET`) |
| New Tickets Sized (30d) | Tickets created in the last 30 days that are sized, vs the 30 days before |
| Size Accuracy | Completed sized tickets (last 180 days) whose work landed in their size's range in the sizing guide, with the share that took longer (undersized) or less (oversized) |
| Open & Unsized | Open tickets to size |
| Work Queued | Open In Progress + Backlog tickets × the typical work of their size |

### Sizing guide

The guide comes from the team's own completed work. The boundary between two sizes sits at the
geometric midpoint of their typical (median) work. As of 2026-10-07: **Small 0–2, Medium 3–5, Large
6–12, XL 13+ business days**, from typical work of 1, 3, 7 and 20 days. Sizes with fewer than 10
completed tickets fall back to `DEFAULT_GUIDE_BD`. Accuracy against it was 57%; Medium was the least
reliable size, and 47% of Medium tickets finished in Small time.

### Sections

- **What the data suggests changing**: recommendations generated from the numbers, for example size at
  intake (naming the least-sized groups), sizes that are often bigger or smaller than they look, the
  SLA treatment of unsized tickets (judged as Medium, though they typically take Small-sized work),
  splitting XL work, and writing the guide down.
- **Sizing Adoption**: monthly share of new tickets sized, against the target (it went from about 4% in
  spring 2026 to about 60% in Sep–Oct).
- **Where Sizes Are Missing**: coverage by business lead, assignee or issue type (groups below the
  target in orange).
- **Do Our Sizes Mean What We Think?**: actual work per size against the shaded guide ranges, plus the
  guide table with right-sized, undersized and oversized shares.
- **SLA Met by Size** and **Open Work by Size** (In Progress and Backlog by size and priority).
- **Needs a Size** (open unsized tickets, oldest first) and **Likely Undersized** (in-progress tickets
  already past their size's range, with a suggested size), each with Jira links.

It replaced counts and pie charts by size and a Priority × Size heatmap whose "risk" cells were an
assumption (Urgent/High with Large/XL) rather than a measurement.

---

## How ticket sequencing works (Apparent Tardiness Cost)

The **Personal Dashboard**'s suggested sequence and calendar are built with this method, and the
**Backlog** report uses it to order each person's queue. Code: `reports/atc_sequence.py`.

### How to frame it

Treat each person as one worker doing one ticket at a time. Every ticket has three numbers:

- **Effort (p)**: days needed, taken from Size. We use the upper limit of each range so plans come out conservative: Small = 1, Medium = 3, Large = 5, Extra Large = 10, no size = 2.
- **Due (d)**: Days Left.
- **Weight (w)**: how much it matters, from Priority, bumped up for older tickets.

The goal is an order that keeps the most important tickets from finishing late. Formally that's "minimize total weighted tardiness" (tardiness is how many days a ticket finishes past its due date). That problem has no fast exact solution once you have more than a few tickets. The standard practical answer is a rule called **Apparent Tardiness Cost (ATC)** (Vepsalainen & Morton, 1987).

### The ATC rule

Start at day `t = 0`. Repeat until every ticket is placed:

1. For each remaining ticket, compute:

   `Score = (w / p) × exp( −max(d − p − t, 0) / (K · p̄) )`

2. Put the ticket with the highest score next in the list, then move the clock forward: `t = t + p`.

In plain terms:

- `w / p` favors high value per day of effort. Ordering by this ratio alone is the classic rule for finishing important work early.
- `d − p − t` is the ticket's slack: how many days you could wait and still finish it on time. The exponential term stays near zero while there's plenty of slack and rises to 1 as slack runs out. Overdue tickets get the full 1.
- `p̄` is the average effort of the remaining tickets.
- `K` sets how far ahead the rule looks. Values of 1.5–3 are typical; we start at 2.

### Inputs used

| Input | Starting value |
| --- | --- |
| Priority weight | None 1, Low 2, Medium 4, High 8. Each level counts double the one below. |
| Urgent | A separate first tier: always at the top, soonest due date first. Otherwise a big Urgent ticket could lose to a small Medium one on value per day. |
| Age | Effective weight = w × (1 + DaysOld / 30), so weight doubles every 30 days. This keeps old, low-priority tickets from waiting forever. |
| No target date | Given a default due date of 30 days out, so aging alone decides when it comes up. |

### Worked example

Six tickets run through the rule, compared with the obvious approach of sorting by priority, then due date:

| Ticket | Priority / Size | Due in (days) | ATC finishes on day | Simple sort finishes on day |
| --- | --- | --- | --- | --- |
| T6 | Urgent / S | 1 | 1 ✅ | 1 ✅ |
| T2 | Medium / S | 2 | 2 ✅ | 17 ❌ (15 days late) |
| T5 | High / L | 7 | 7 ✅ | 6 ✅ |
| T3 | Low / M (40 days old) | 6 | 10 ❌ (4 days late) | 20 ❌ (14 days late) |
| T1 | High / XL | 20 | 20 ✅ | 16 ✅ |
| T4 | None / no size (60 days old) | 30 | 22 ✅ | 22 ✅ |

This workload can't all be done on time: 10 days of work are due within 7 days. ATC still limits the damage to one Low ticket, 4 days late. The simple sort lets a Medium ticket due in 2 days sit behind a 10-day ticket that isn't due for 20.

### Two extras that make it useful

- **Projected dates.** Each ticket in the list gets a projected start and finish, so people can see which ones will run late before it happens.
- **Overload alert.** Sort a person's tickets by due date. If the running total of effort ever exceeds a ticket's Days Left, no order can meet every date. That's the signal for a manager to move dates or reassign work.

### Assumptions to confirm

- Days Left is counted in working days. If it's calendar days, this will need to be converted.
- One ticket at a time, and a ticket isn't split once started.

---

## How the probability model works

The on-time completion model uses historical Jira tickets to estimate whether a ticket will finish by a target date.

- **Validation delay** is treated as the gap between completion and the target end date.
	- It uses fractional days instead of integer days.
	- Negative gaps are clipped to `0` so early completions do not reduce the delay estimate.
	- Global delay estimates are trimmed to reduce outlier impact.
- **Group-level delay estimates** are smoothed by assignee and priority.
	- This prevents small sample sizes from producing extreme values.
	- Training and prediction use the same assignee -> priority -> global fallback logic.
- **Historical on-time rate** is also smoothed.
	- Raw `mean(on_time)` was replaced with a Bayesian-smoothed rate.
	- This makes the feature more stable for assignees or priorities with few tickets.
- **Schedule adherence** is a continuous score from `0` to `1`.
	- On-time tickets score `1.0`.
	- Late tickets are penalized based on how many days late they finished.
	- The model can learn the difference between slightly late and very late tickets.

In practical terms, this means the model now uses both binary history and lateness severity instead of relying only on simple averages.

## Release notes

### 2026-10-09
- Every fetch now also loads **change history** (status, Target start, Target end) from Jira's bulk changelog endpoint, read-only, about 25–40 seconds (see [Change history](#change-history))
- **Trend**: Target date measures in the scorecard and small multiples, and a **Target Date Changes** section (outcomes by number of moves, moves by priority and size, most re-planned open tickets)
- **SLA**: tickets whose **SLA clock was set after the fact** are flagged (warning, Breached Tickets column, own table) and can be left out of the breach rates
- Rebuilt **Validating** around time waiting since entering Validating, a nudge list, validation time and rework trends, by business lead, SLA impact and policy what-ifs (see [How Validating works](#how-validating-works))
- Added `tests/test_change_history.py`

### 2026-10-02
- Rebuilt the **Executive Summary** for leadership: generated headlines, seven health tiles compared with the previous Lookback period, weekly flow (created vs closed, all outcomes), monthly SLA compliance trend with target, open work by stage and SLA risk, a top-10 "needs attention" list with reasons, aging by band and priority, oldest work by business lead, and ways-of-working signals (see [How the Executive Summary works](#how-the-executive-summary-works))
- Release Management "Change and Release" (CAR) tickets are no longer judged against the PE SLA: shown as Not assessed, left out of SLA compliance and the Needs Attention list (`sla_applies()`); the Backlog forecast applies the same rule
- **Blocked & On Hold** is now tickets-only (no Features or Initiatives, no `EXCLUDED_ASSIGNEES`), so it agrees with the Executive Summary
- New **Suggested Assignments** page: an advisory assignment plan for unassigned and triage tickets combining domain experience (titles and comments), availability and SLA fit from the Backlog queue simulation, and load balance, with backups, stretch suggestions, a who-knows-what matrix and single-person domains; shared domain taxonomy in `reports/domains.py` (see [How Suggested Assignments works](#how-suggested-assignments-works))
- **Suggested Assignments**: **✏️ Assign in Jira** button for safe-mode assignment (review, assign by account id, validation, typed confirmation, re-read before write, Jira comment, audit log, undo; off by default and localhost-only) (see [Assigning in Jira (safe mode)](#assigning-in-jira-safe-mode)); the data loader now keeps each assignee's Jira account id
- Rebuilt **Distribution of Ticket by Estimated Size** around sizing as a practice: coverage against a 90% target, a data-derived sizing guide, size accuracy, SLA met by size, open work by size, data-backed recommendations and "Needs a Size" / "Likely Undersized" action lists (see [How Distribution by Estimated Size works](#how-distribution-by-estimated-size-works))
- **Backlog**: Projected Start back-tested against actual starts (honest median, ±3 weeks); new **Safe Start**, **Start Confidence** and **Target Start Slipped** columns; and a **✏️ Update Target dates** button for safe-mode Jira updates (proposals, review, validation, typed confirmation, re-read before write, Jira comment, audit log, undo; off by default and localhost-only) (see [Updating Target dates (safe mode)](#updating-target-dates-safe-mode))
- **SLA**: re-planned tickets are judged on their original Target start by default, so moving dates can't lower the breach rate
- Rebuilt the **SLA** page around the real SLA table: two breach rates (SLA came due / completed late) against the under-10% goal with 7/30/90-day windows, trend, Priority × Size breach map, coming-due chart, open work by stage, filters, and searchable, downloadable **SLA Detail** and **Breached Tickets** tables with the latest human comment (see [How the SLA page works](#how-the-sla-page-works))
- Rebuilt the **Forecast** page as a Delivery Forecast: damped-trend central estimate with ranges calibrated from back-tested past errors (about 7 in 10 outcomes), next 4/8/12-week cards, weekly and cumulative charts, a "can we commit to a project?" calculator, demand vs delivery outlook and the forecast's own track record (see [How the Delivery Forecast works](#how-the-delivery-forecast-works))
- Capacity's "How much can we deliver?" now uses the same engine (its flat Monte Carlo back-tested 17–26% low); removed `build_capacity_data` and the `xgboost` dependency
- Rebuilt **Velocity** around flow: lead-time promise (50/85/95%), waiting vs in progress by priority, size vs effort, an SLA reality check per Priority × Size and what was delivered; tickets picked by completion date, business days (see [How Velocity works](#how-velocity-works))
- Date-based tests now use the company holiday calendar, so they pass in weeks with a holiday
- Rebuilt **Trend** as "are we getting better?": an improvement scorecard (last full month vs the 3 before), 12-month small multiples with targets, and a team contribution heatmap; PE tickets only, business days, lead time and cycle time measured correctly (see [How Trend works](#how-trend-works))
- Rebuilt **Capacity**: weekly delivery vs demand KPIs, demand vs capacity trend, Monte Carlo delivery forecast with a "how long for N more tickets?" calculator, planned vs reactive and priority mix, and core-team load balance; PE tickets only, full weeks (see [How Capacity works](#how-capacity-works))
- Rebuilt **Distribution per Business Leader** as a service scorecard per requesting business lead (requested, delivered, won't do, wait, SLA met, open past SLA, oldest open, top friction) with demand, SLA, wait, priority and open-risk charts; PE tickets only, PE internal work as one row and out of the charts by default, and the hard-coded reassignment of CAR tickets' business lead removed (see [How Distribution per Business Leader works](#how-distribution-per-business-leader-works))
- Fixed a Backlog crash when the data has no `days_old` column
- Rebuilt **Distribution of Ticket's Age**: age against SLA by stage, silence since the last human comment, weekly age trend (median and 75th percentile), by-status summary and a past-SLA-first ticket table; business days and tickets-only, replacing the duplicate box and violin charts (see [How Distribution of Ticket's Age works](#how-distribution-of-tickets-age-works))
- Fixed the age-by-business-lead chart, which showed the youngest groups instead of the oldest
- The sidebar **Lookback** now drives the Executive Summary's period comparisons
- Added `tests/test_executive_summary.py`, `tests/test_ticket_age.py`, `tests/test_business_leader.py`, `tests/test_capacity.py`, `tests/test_trend.py`, `tests/test_velocity_flow.py`, `tests/test_forecast.py`, `tests/test_sla.py`, `tests/test_jira_dates.py`, `tests/test_size_distribution.py`, `tests/test_assignments.py` and `tests/test_jira_assign.py`; made a Teams Conversations test independent of the day of the month

### 2026-09-29
- Renamed the **Word of the Month** page to **Teams Conversations** and the **Blocked** page to **Blocked & On Hold** (menu, page titles and messages; module names unchanged)
- The sidebar menu falls back to Overview if a previously selected page name no longer exists
- **Blocked & On Hold** now includes `On Hold` tickets as well as `Blocked`: separate Blocked and On Hold KPIs, business-lead and risk-mix charts stacked by state (the risk pie became a stacked bar), and a State column in the ticket table (Blocked first, most overdue first)
- Added `tests/test_blocked_on_hold.py`

### 2026-09-28
- Rebuilt **Word of the Month** (now **Teams Conversations**) on human ticket comments: comment coverage and assignee-comment metrics with a monthly trend and target, friction themes ranked by extra business days with suggested process changes, breakdowns by business lead / issue type / priority, conversation health (first reply, back-and-forth, requester chasing), and emerging phrases (see [How Teams Conversations works](#how-teams-conversations-works))
- Jira fetch loads human comments (`comments`, `comment_total`, `bot_comment_count`) and `reporter_name`
- Removed `wordcloud`, `vaderSentiment` and `matplotlib` from `requirements.txt` (no longer used)
- Added `tests/test_word_of_the_month.py`

### 2026-09-25
- Rebuilt the **Backlog** report as a queue-aware forecast: ATC-ordered queue per assignee, Monte Carlo start/finish dates behind current In Progress work, SLA risk from Target start, capacity runway, backlog readiness and "waiting longer than SLA" (see [How the Backlog forecast works](#how-the-backlog-forecast-works)). Fixes the old report's priority grouping, which read a column that didn't exist, and replaces "Complexity Days". The **All Backlog Tickets** table keeps its columns and now excludes Features and Initiatives
- Moved ATC sequencing from `app.py` to `reports/atc_sequence.py` so the Personal Dashboard and Backlog share it (output verified identical)
- Added `tests/test_backlog_forecast.py`
- Rebuilt the **In Progress** report as an SLA-aware completion forecast: business-day SLAs by Priority × Size from Target start, P50/P85 finish dates from execution velocity by priority per assignee, current-load adjustment, and risk against SLA and/or Target End Date (see [How the In Progress forecast works](#how-the-in-progress-forecast-works))
- The **All In Progress Tickets** table now also excludes Initiatives (it already excluded Features)
- **Executive Summary**: Features and Initiatives excluded from every KPI, chart and table; **Created (24h)** and **Resolved (24h)** are now calculated from Jira data (they were hard-coded placeholders)
- Jira fetch adds `statuscategorychangedate` as `status_category_changed`
- Added `holidays` to `requirements.txt` for the business-day calendar
- Added `tests/test_in_progress_forecast.py`

### 2026-09-24
- **Forecast** report: Features and Initiatives excluded from training data and metrics; completed now means status `Done` only. `build_capacity_data` gained a `completed_statuses` option; the Capacity report is unchanged
- Made the dev environment install on Apple Silicon Macs (`xgboost` on macOS, `xgboost-cpu` on Linux)

### 2026-09-11
- Added **Apparent Tardiness Cost (ATC)** ticket sequencing to the Personal Dashboard — suggested work order, ATC score, and projected start/finish/tardiness per ticket (see [How ticket sequencing works](#how-ticket-sequencing-works-apparent-tardiness-cost))
- Added a **Suggested Working-Day Calendar** heatmap visualizing the ATC sequence, color-coded by priority tier, with weekend skipping and an overload warning when the sequence exceeds the visible horizon

### 2026-09-04
- Added estimated ticket **Size** column to the Backlog report

### 2026-09-03
- Split **Tickets Older Than 90 Days** into separate Epics and Tickets tables instead of one mixed list

### 2026-07-17
- Fixed Backlog report columns to display Target Start Date instead of Target End Date
- Fixed typos in the Backlog report

### 2026-07-16
- Added **Distribution of Ticket by Estimated Size** menu: In Progress vs. Backlog counts and pie charts by size
- Added a Priority × Size risk heatmap to flag ticket combinations most likely to miss resolution targets

### 2026-07-06
- Added a completed-ticket trend chart per PE team member to the Trend report

### 2026-06-25
- Added a rolling completion trend chart (past-due days and on-time rate over time) to the Probability of Completion report
- Normalized heatmap ordering to a consistent priority sequence across reports

### 2026-06-24
- Added due-date awareness and pie-chart breakdowns to the Probability of Completion on time report
- Adjusted the completion-of-work pie chart and refined several probability calculations for a better-fitting model

### 2026-05-27
- Added Personal Dashboard table split:
	- **Tickets Requiring Attention** excludes `issuetype = Feature`
	- **Epic Ticket Only** shows only `issuetype = Feature`
- Fixed Jira fetch reliability:
	- corrected JQL lookback syntax to `created >= -730d`
	- fixed configuration fallback lookup so root `config.json` is discovered
- Updated project documentation to match current package structure

### 2026-05-26
- Pulled and aligned latest GitLab merge with package refactor (`config/`, `data/`, `reports/`, `tests/`)
- Added CI/testing support files (`requirements-dev.txt`, smoke test scaffold)

### 2026-05-22
- Enhanced on-time completion probability model with assignee/priority-specific validation offsets
- Updated training detail table to reflect selected assignee/priority context
- Added Personal Dashboard view with Jira-linked ticket tables and risk-focused metrics
- Migrated Streamlit sizing API usage to `width="stretch"` / `width="content"`

### 2026-06-23
- Improved probability model math:
	- validation delay now uses fractional days instead of integer truncation
	- validation delay is clipped to late-only values before aggregation
	- assignee/priority validation delays use smoothed estimates instead of raw means
- Improved historical on-time rate calculations:
	- Bayesian smoothing stabilizes small assignee/priority groups
	- added continuous schedule-adherence scoring to capture how late a ticket finished
- Aligned training and prediction feature logic so the model uses the same historical assumptions end to end

---

## Development and tests

The full suite runs in the darkstar environment, the same as CI:

```bash
.venv-darkstar/bin/python -m pytest -q
```

CI installs only `requirements-dev.txt` and `darkstar/requirements.txt`, so tests that need the
Streamlit app's packages (the In Progress, Backlog, Teams Conversations, Blocked & On Hold, Executive Summary, ticket age, business leader, capacity, trend, velocity, forecast and SLA tests need Plotly)
skip themselves there. Run them in the app environment:

```bash
.venv/bin/python -m pytest tests/test_in_progress_forecast.py tests/test_backlog_forecast.py tests/test_word_of_the_month.py tests/test_blocked_on_hold.py tests/test_executive_summary.py tests/test_ticket_age.py tests/test_business_leader.py tests/test_capacity.py tests/test_trend.py tests/test_velocity_flow.py tests/test_forecast.py tests/test_sla.py tests/test_jira_dates.py tests/test_size_distribution.py tests/test_assignments.py tests/test_jira_assign.py tests/test_change_history.py -q
```

---

## Notes

- `app.py` is the Streamlit entrypoint.
- `data/build_dataframe_new.py` builds the canonical Jira issues dataframe used across reports.
- `reports/velocity_report.py` contains `PE_TEAM_MEMBERS`, reused by multiple views.
