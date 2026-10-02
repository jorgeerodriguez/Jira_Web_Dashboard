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
- **In Progress completion forecast**: when each in-progress ticket will finish (P50 likely / P85 safe dates), checked against its **business-day SLA** (Priority × Size) and its Target End Date — see [How the In Progress forecast works](#how-the-in-progress-forecast-works)
- **Backlog forecast**: when each backlog ticket will *start* and finish, queued behind each person's In Progress work in ATC order, with SLA risk, capacity runway and backlog readiness — see [How the Backlog forecast works](#how-the-backlog-forecast-works)
- **Teams Conversations** from human ticket comments: **comment coverage** to track monthly, the friction themes that cost the most time (with suggested process changes), conversation health, and the phrase of the month — see [How Teams Conversations works](#how-teams-conversations-works)
- **Executive Summary** for leadership: generated headlines, health tiles compared with the previous period, flow of work in vs out, SLA compliance trend, where open work sits and how much is at risk, a short "needs attention" list, and aging — see [How the Executive Summary works](#how-the-executive-summary-works)
- Consistent ticket scope: Features and Initiatives are excluded from ticket metrics — see [What counts as a ticket](#what-counts-as-a-ticket-and-as-completed)
- **Distribution of Ticket's Age**: open-work age against SLA, where work has gone quiet (no human comment), and whether open work is getting older — see [How Distribution of Ticket's Age works](#how-distribution-of-tickets-age-works)
- **Capacity**: weekly delivery vs demand, a Monte Carlo delivery forecast with a "how long for N more tickets?" calculator, where capacity goes (planned vs reactive, priority), and load balance across the team — see [How Capacity works](#how-capacity-works)
- **Distribution per Business Leader**: a service scorecard per requesting business lead (requested, delivered, wait, SLA met, open past SLA, top friction) with demand, SLA and priority charts — see [How Distribution per Business Leader works](#how-distribution-per-business-leader-works)
- **Distribution of Ticket by Estimated Size** report, including a Priority × Size risk heatmap
- **Tickets Older Than 90 Days** split into Epics vs. Tickets
- **Trend**: an improvement scorecard and 12-month small multiples for delivery, lead and cycle time, predictability, SLA met, urgent and reactive work, comment coverage and people delivering, plus team contribution over time — see [How Trend works](#how-trend-works)

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
│   ├── fetch_all_tickets_for_devops.py
│   └── metrics.py
├── reports/
│   ├── __init__.py
│   ├── atc_sequence.py          # ATC ordering shared by Personal Dashboard + Backlog
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
- **Apple Silicon Macs** install the regular `xgboost` package; Linux (CI, Docker) installs
  `xgboost-cpu`. `requirements.txt` picks the right one per platform.
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
- **Forecast** report trains on tickets only, with completed meaning status `Done`
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
  Business Leader, Capacity, Trend and Teams Conversations reports. The Executive
  Summary, In Progress, Backlog and Blocked & On Hold pages also leave out the people in
  `EXCLUDED_ASSIGNEES`, so their counts agree. The Size Distribution report excludes Features
  only, and Tickets Older Than 90 Days shows Features and Initiatives in a separate Epics table.
- **PE SLA scope**: the SLA (Priority × Size, business days) applies to Platform Engineering tickets.
  Release Management **"Change and Release" (CAR)** tickets follow the release process and have no PE
  SLA: they are never judged against it and are not counted in SLA compliance. The rule is
  `sla_applies()` in `reports/in_progress_report.py` (`SLA_EXEMPT_PROJECTS`, `SLA_EXEMPT_ISSUE_TYPES`).
- **Completed / Resolved**: status `Done`. The Capacity report is the exception and also counts
  Release Management's `Released Successfully to Production`.
- **When it finished**: most Done tickets have no Jira resolution date. The Forecast uses `updated`
  (the resolution date when Jira has one). The In Progress, Backlog, Executive Summary and Teams
  Conversations reports use `status_category_changed` (Jira's `statuscategorychangedate`), which later
  comments or edits don't move.

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

### Limits

The duration model is the one back-tested on the In Progress page. The projected *start* dates
can't be back-tested yet: the dataframe has each ticket's Target start but not the date it actually
moved to In Progress. Darkstar already stores status transitions, so that's the data to use if
start-date accuracy needs checking.

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
- **How Much Can We Deliver?**: a Monte Carlo (10,000 runs) that resamples the last 12 weeks of
  delivery. For the next 4, 8 and 12 weeks it shows the likely total (P50) and the total reached in
  85% of runs. The **"How long for N more tickets?"** calculator gives likely (P50) and safe (P85)
  weeks. Both use the whole team's pace, which is shared with incoming demand, so a new project only
  gets the spare capacity unless something else is deprioritised.
- **Where Capacity Goes**: monthly share of delivered tickets by work type (Planned = Story, Task,
  Sub-task; Reactive = Bug, Hotfix, Incident, Support, Security) and by priority, last 6 months, with
  the share of sized tickets.
- **Load Balance**: tickets in progress per person (with the team's typical level) and each person's
  share of delivery over the last 8 weeks (with an even-split line). It's meant for balancing work,
  not for judging individuals. The charts show the core team (work in progress, or 4+ delivered in
  8 weeks); everyone is in the table, along with weekly detail.

It replaced a "Total Tickets Worked per Year" chart (any ticket *updated* in a year, with only 24
months of data) and monthly created vs completed bars that counted Features and CAR tickets and dated
completions by last update. `build_capacity_data`, which feeds the Forecast page, is unchanged.

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
- **Team Contribution Over Time**: delivered tickets per person per month for people with 10+
  delivered in the window. It's meant for spotting ramp-ups, gaps and load, not for judging
  individuals.
- **Monthly detail**: every measure per month, with the SLA sample size.

It replaced a dual-axis flow chart, a "cycle time" that was really created → last update, a
status-mix chart that grouped *current* statuses by last-update month, and a 16-line per-engineer
chart.

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

### 2026-10-02
- Rebuilt the **Executive Summary** for leadership: generated headlines, seven health tiles compared with the previous Lookback period, weekly flow (created vs closed, all outcomes), monthly SLA compliance trend with target, open work by stage and SLA risk, a top-10 "needs attention" list with reasons, aging by band and priority, oldest work by business lead, and ways-of-working signals (see [How the Executive Summary works](#how-the-executive-summary-works))
- Release Management "Change and Release" (CAR) tickets are no longer judged against the PE SLA: shown as Not assessed, left out of SLA compliance and the Needs Attention list (`sla_applies()`); the Backlog forecast applies the same rule
- **Blocked & On Hold** is now tickets-only (no Features or Initiatives, no `EXCLUDED_ASSIGNEES`), so it agrees with the Executive Summary
- Rebuilt **Trend** as "are we getting better?": an improvement scorecard (last full month vs the 3 before), 12-month small multiples with targets, and a team contribution heatmap; PE tickets only, business days, lead time and cycle time measured correctly (see [How Trend works](#how-trend-works))
- Rebuilt **Capacity**: weekly delivery vs demand KPIs, demand vs capacity trend, Monte Carlo delivery forecast with a "how long for N more tickets?" calculator, planned vs reactive and priority mix, and core-team load balance; PE tickets only, full weeks (see [How Capacity works](#how-capacity-works))
- Rebuilt **Distribution per Business Leader** as a service scorecard per requesting business lead (requested, delivered, won't do, wait, SLA met, open past SLA, oldest open, top friction) with demand, SLA, wait, priority and open-risk charts; PE tickets only, PE internal work as one row and out of the charts by default, and the hard-coded reassignment of CAR tickets' business lead removed (see [How Distribution per Business Leader works](#how-distribution-per-business-leader-works))
- Fixed a Backlog crash when the data has no `days_old` column
- Rebuilt **Distribution of Ticket's Age**: age against SLA by stage, silence since the last human comment, weekly age trend (median and 75th percentile), by-status summary and a past-SLA-first ticket table; business days and tickets-only, replacing the duplicate box and violin charts (see [How Distribution of Ticket's Age works](#how-distribution-of-tickets-age-works))
- Fixed the age-by-business-lead chart, which showed the youngest groups instead of the oldest
- The sidebar **Lookback** now drives the Executive Summary's period comparisons
- Added `tests/test_executive_summary.py`, `tests/test_ticket_age.py`, `tests/test_business_leader.py`, `tests/test_capacity.py` and `tests/test_trend.py`; made a Teams Conversations test independent of the day of the month

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
Streamlit app's packages (the In Progress, Backlog, Teams Conversations, Blocked & On Hold, Executive Summary, ticket age, business leader, capacity and trend tests need Plotly)
skip themselves there. Run them in the app environment:

```bash
.venv/bin/python -m pytest tests/test_in_progress_forecast.py tests/test_backlog_forecast.py tests/test_word_of_the_month.py tests/test_blocked_on_hold.py tests/test_executive_summary.py tests/test_ticket_age.py tests/test_business_leader.py tests/test_capacity.py tests/test_trend.py -q
```

---

## Notes

- `app.py` is the Streamlit entrypoint.
- `data/build_dataframe_new.py` builds the canonical Jira issues dataframe used across reports.
- `reports/velocity_report.py` contains `PE_TEAM_MEMBERS`, reused by multiple views.
