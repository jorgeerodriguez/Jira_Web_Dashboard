from __future__ import annotations

from datetime import date, timedelta


def load_metrics(report_date: date, lookback_days: int) -> dict:
    """Return mock metrics for dashboard scaffolding.

    Replace this function with Jira API calls later. Keeping this function in a
    dedicated module lets notebooks import and test it directly.
    """
    trend = []
    start = report_date - timedelta(days=lookback_days - 1)
    for i in range(lookback_days):
        day = start + timedelta(days=i)
        created = 8 + (i % 4)
        resolved = 6 + ((i + 1) % 4)
        trend.append(
            {
                "day": day.isoformat(),
                "created": created,
                "resolved": resolved,
            }
        )

    return {
        "open_issues": 73,
        "created_24h": 11,
        "resolved_24h": 9,
        "sla_breaches": 2,
        "priority": {
            "critical": 4,
            "high": 21,
            "medium": 35,
            "low": 13,
        },
        "trend": trend,
    }
