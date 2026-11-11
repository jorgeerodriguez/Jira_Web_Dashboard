"""GitLab merge requests (MRs) mentioned on a Jira ticket: links pasted in its description or comments.

Jira's GitLab integration (the Development panel) is not connected for DEVOPS, so these pasted links are the only
MR information a ticket carries. A mention tells us which MR and when the link was posted, not when the MR was
opened or merged; the Status of My Ticket page says so.

Every comment is read, including those from automation accounts (an integration bot posting MR links is still
evidence), but only the MR links are kept, never the description or bot text.
"""
from __future__ import annotations

import json
import re

import pandas as pd

MR_URL = re.compile(r"https?://(?P<host>[\w.-]*gitlab[\w.-]*)/(?P<project>[\w.~%-]+(?:/[\w.~%-]+)*?)"
                    r"/-/merge_requests/(?P<iid>\d+)", re.IGNORECASE)
COLUMNS = ["MR", "Project", "MR #", "First Mentioned", "By", "Where", "Mentions"]


def _text(value) -> str:
    """Wiki-markup strings (API v2) as they are; Atlassian document format (API v3) as JSON text."""
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def find_merge_requests(text) -> list[dict]:
    """Distinct MRs in `text`, in order: {url, project, iid}. Links to notes, diffs or commits of an MR count as it."""
    seen, out = set(), []
    for m in MR_URL.finditer(_text(text)):
        url = f"https://{m['host'].lower()}/{m['project']}/-/merge_requests/{m['iid']}"
        if url not in seen:
            seen.add(url)
            out.append({"url": url, "project": m["project"], "iid": int(m["iid"])})
    return out


def issue_merge_requests(fields: dict) -> list[dict]:
    """Every MR mention on an issue: {url, project, iid, at (UTC), by, where}."""
    mentions = []
    reporter = (fields.get("reporter") or {}).get("displayName")
    created = pd.to_datetime(fields.get("created"), utc=True, errors="coerce")
    for mr in find_merge_requests(fields.get("description")):
        mentions.append({**mr, "at": created, "by": reporter, "where": "description"})
    for comment in (fields.get("comment") or {}).get("comments", []):
        author = comment.get("author") or {}
        where = "comment (automation)" if author.get("accountType") == "app" else "comment"
        at = pd.to_datetime(comment.get("created"), utc=True, errors="coerce")
        for mr in find_merge_requests(comment.get("body")):
            mentions.append({**mr, "at": at, "by": author.get("displayName"), "where": where})
    return mentions


def merge_request_table(mentions) -> pd.DataFrame:
    """One row per MR, first mention first (First Mentioned in UTC)."""
    if not isinstance(mentions, list) or not mentions:
        return pd.DataFrame(columns=COLUMNS)
    m = pd.DataFrame(mentions)
    m["at"] = pd.to_datetime(m["at"], utc=True, errors="coerce")
    m = m.sort_values("at", na_position="last")
    rows = []
    for url, g in m.groupby("url", sort=False):
        first = g.iloc[0]
        rows.append({"MR": url, "Project": first["project"], "MR #": f"!{first['iid']}", "First Mentioned": first["at"],
                     "By": first["by"] or "Unknown", "Where": first["where"], "Mentions": int(len(g))})
    return pd.DataFrame(rows, columns=COLUMNS)
