# Jira Morning Report (Python)

This dashboard now uses **Python + Streamlit** instead of static HTML/JS.

## Run locally

From project root:

```bash
.venv/bin/python -m streamlit run app.py
```

Then open: http://localhost:8501

## Jira credentials (publish-ready)

Use one of these options (in priority order):

1. **Streamlit secrets**: `.streamlit/secrets.toml`
2. **Environment variables / `.env`**
3. **`config.json` fallback** (legacy, local only)

Required values:

- `jira_server`
- `jira_email`
- `jira_api_token`

Templates are included:

- `.streamlit/secrets.toml.example`
- `.env.example`

## Where to add Jira logic

- Edit `jira_morning_report_site/metrics.py`
- Replace `load_metrics()` mock data with Jira API calls

## Reuse from Jupyter notebooks

In a notebook cell:

```python
from jira_morning_report_site.metrics import load_metrics
from datetime import date

load_metrics(report_date=date.today(), lookback_days=7)
```

This keeps notebook exploration and dashboard logic aligned.
