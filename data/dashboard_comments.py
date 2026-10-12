"""Comments the dashboard itself posts to Jira, and the machine-readable forecast tag.

The dashboard writes Jira comments when it updates Target dates, assigns tickets, undoes either, or posts a
backlog forecast. They are posted with a person's token, so Jira reports them as human comments. The loader
(data/build_dataframe_new.py) uses `is_dashboard_comment` to keep them out of the human-comment metrics
(comment coverage, first reply, friction themes, phrases) and stores them separately, where the Backlog
page reads the posted forecasts back to measure them against what actually happened.

Detection matches the exact opening line of each kind of dashboard comment (and the forecast tag), not
just the words "PE dashboard", so a person writing "see the PE dashboard" stays a human comment.

Forecast tag, one line at the end of a forecast comment (version 1):

    [pe-forecast v1 as_of=2026-10-12 start_p50=2026-10-20 ... batch=20261012T150000Z-ab12cd]

Fields are space-separated key=value pairs with no spaces inside a value; dates are ISO; unknown keys are
kept, so later versions can add fields without breaking older readers.
"""
from __future__ import annotations

import re

FORECAST_VERSION = "v1"
_TAG = re.compile(r"\[pe-forecast (v\d+)((?:\s+[a-z_0-9]+=[^\s\]]*)*)\s*\]")
_PAIR = re.compile(r"([a-z_0-9]+)=([^\s\]]*)")
_DASHBOARD = re.compile(
    r"^(?:Target dates updated from the PE dashboard"
    r"|Assigned to .+? from the PE dashboard"
    r"|Backlog forecast from the PE dashboard"
    r"|Undo of (?:PE )?dashboard (?:assignment )?batch)"
    r"|\[pe-forecast v\d+ ")


def is_dashboard_comment(body: str | None) -> bool:
    return bool(body) and bool(_DASHBOARD.search(body))


def format_forecast_tag(fields: dict) -> str:
    """The tag line for `fields` (values must not contain spaces or ']'; None becomes an empty value)."""
    parts = []
    for key, value in fields.items():
        text = "" if value is None else str(value)
        if re.search(r"[\s\]]", text):
            raise ValueError(f"forecast tag value for {key!r} contains a space or ']': {text!r}")
        parts.append(f"{key}={text}")
    return f"[pe-forecast {FORECAST_VERSION} " + " ".join(parts) + "]"


def parse_forecast(body: str | None) -> dict | None:
    """The fields of the forecast tag in `body` (with "version"), or None when there is no tag."""
    match = _TAG.search(body or "")
    if not match:
        return None
    fields = {key: (value or None) for key, value in _PAIR.findall(match.group(2))}
    fields["version"] = match.group(1)
    return fields
