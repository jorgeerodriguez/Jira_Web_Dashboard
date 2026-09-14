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
- **Distribution of Ticket by Estimated Size** report, including a Priority × Size risk heatmap
- **Tickets Older Than 90 Days** split into Epics vs. Tickets
- Trend report includes completed-ticket trend per PE team member

---

## Current project structure

```text
Jira_Web_Dashboard/
├── app.py
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
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
├── tests/
│   ├── __init__.py
│   └── test_smoke.py
└── backup/
```

---

## Run locally

From project root:

```bash
.venv/bin/python -m streamlit run app.py
```

Open: http://localhost:8501

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
- Updated Streamlit layout API usage (`width="stretch"` / `width="content"`)
- Fixed Jira fetch JQL lookback syntax (`created >= -730d`)
- Improved config fallback path resolution so root `config.json` is detected

---

## How ticket sequencing works (Apparent Tardiness Cost)

The **Personal Dashboard**'s suggested sequence and calendar are built with this method.

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

Install dev dependencies:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

Run tests:

```bash
.venv/bin/python -m pytest -q
```

---

## Notes

- `app.py` is the Streamlit entrypoint.
- `data/build_dataframe_new.py` builds the canonical Jira issues dataframe used across reports.
- `reports/velocity_report.py` contains `PE_TEAM_MEMBERS`, reused by multiple views.
