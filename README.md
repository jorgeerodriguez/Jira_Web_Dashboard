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
- **Executive Summary** with live **Created (24h)** and **Resolved (24h)** counts
- Consistent ticket scope: Features and Initiatives are excluded from ticket metrics — see [What counts as a ticket](#what-counts-as-a-ticket-and-as-completed)
- **Distribution of Ticket by Estimated Size** report, including a Priority × Size risk heatmap
- **Tickets Older Than 90 Days** split into Epics vs. Tickets
- Trend report includes completed-ticket trend per PE team member

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
- Rebuilt the **Backlog** report as a queue-aware start/finish forecast with SLA risk, capacity runway and readiness
- Rebuilt the **In Progress** report as an SLA-aware completion forecast (P50/P85 dates, risk vs SLA and Target End Date, execution velocity by priority per assignee)
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
  Summary, In Progress and Backlog reports. The Trend and Size Distribution reports exclude Features only,
  and Tickets Older Than 90 Days shows Features and Initiatives in a separate Epics table.
- **Completed / Resolved**: status `Done`. The Capacity report is the exception and also counts
  Release Management's `Released Successfully to Production`.
- **When it finished**: most Done tickets have no Jira resolution date. The Forecast and Executive
  Summary use `updated` (the resolution date when Jira has one). The In Progress forecast uses
  `status_category_changed` (Jira's `statuscategorychangedate`), which later comments or edits
  don't move.

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
Streamlit app's packages (the In Progress and Backlog forecast tests need Plotly)
skip themselves there. Run them in the app environment:

```bash
.venv/bin/python -m pytest tests/test_in_progress_forecast.py tests/test_backlog_forecast.py -q
```

---

## Notes

- `app.py` is the Streamlit entrypoint.
- `data/build_dataframe_new.py` builds the canonical Jira issues dataframe used across reports.
- `reports/velocity_report.py` contains `PE_TEAM_MEMBERS`, reused by multiple views.
