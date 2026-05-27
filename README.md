# Jira Web Dashboard (Streamlit)

Platform Engineering Jira reporting dashboard built with Python + Streamlit.

It supports:
- live Jira connection validation
- full ticket fetch + dataframe build (DEVOPS + CAR)
- multiple analytics reports (capacity, trend, velocity, SLA, forecast, etc.)
- ML-based **Probability of completion on time**
- **Personal Dashboard** with prioritized attention and Epic-only view

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
- Improved probability model workflow:
	- assignee/priority-aware validation-time offsets
	- training detail table aligned with selected filters
- Updated Streamlit layout API usage (`width="stretch"` / `width="content"`)
- Fixed Jira fetch JQL lookback syntax (`created >= -730d`)
- Improved config fallback path resolution so root `config.json` is detected

---

## Release notes

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
