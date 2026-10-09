"""Change history (status, Target start, Target end) for the tickets the history-based reports use.

Read-only. Uses Jira Cloud's bulk changelog endpoint (POST /rest/api/3/changelog/bulkfetch), which returns
the history of up to 1,000 issues per request filtered to the fields asked for, so a fetch takes about
30-40 seconds instead of one request per ticket. The jira library has no wrapper for it, so the request goes
through the connector's authenticated session.

Scope: DevOps-project issues (Release Management CAR tickets have no PE SLA) updated in the last
HISTORY_DAYS days, which covers every ticket delivered or still open in the reports' 12-month windows.

Returned columns: key, at (UTC), author, field ("status", "target_start", "target_end"), frm, to.
For status the values are status names; for the Target dates they are ISO dates ("2026-09-11"), never the
display strings ("11/Sep/26"), which are ambiguous to parse.
"""
from __future__ import annotations

import pandas as pd

HISTORY_DAYS = 400
BATCH = 1000
FIELDS = {"status": "status", "customfield_10946": "target_start", "customfield_10947": "target_end"}
COLUMNS = ["key", "at", "author", "field", "frm", "to"]
EXCLUDED_PROJECTS = {"release management"}


def empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def _scope(df_issues: pd.DataFrame) -> pd.DataFrame:
    if df_issues is None or df_issues.empty or "id" not in df_issues.columns:
        return pd.DataFrame(columns=["id", "key"])
    keep = ~df_issues.get("project_name", pd.Series("", index=df_issues.index)).astype(str).str.strip() \
        .str.casefold().isin(EXCLUDED_PROJECTS)
    updated = pd.to_datetime(df_issues["updated"], utc=True, errors="coerce")
    keep &= updated >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=HISTORY_DAYS)
    return df_issues.loc[keep, ["id", "key"]].astype(str)


def _parse_items(change_logs: list[dict], key_by_id: dict[str, str]) -> list[dict]:
    rows = []
    for log in change_logs:
        key = key_by_id.get(str(log.get("issueId")))
        if key is None:
            continue
        for history in log.get("changeHistories", []):
            created = history.get("created")
            at = (pd.to_datetime(int(created), unit="ms", utc=True) if str(created).isdigit()
                  else pd.to_datetime(created, utc=True, errors="coerce"))
            author = (history.get("author") or {}).get("displayName")
            for item in history.get("items", []):
                field = FIELDS.get(item.get("fieldId"))
                if field is None:
                    continue
                if field == "status":
                    frm, to = item.get("fromString"), item.get("toString")
                else:
                    frm, to = item.get("from"), item.get("to")
                rows.append({"key": key, "at": at, "author": author, "field": field, "frm": frm or None, "to": to or None})
    return rows


def fetch_change_history(jira_connector, df_issues: pd.DataFrame) -> pd.DataFrame:
    """History rows for the in-scope issues; an empty frame (never an exception) when anything fails."""
    scope = _scope(df_issues)
    if scope.empty or jira_connector is None:
        return empty_history()
    key_by_id = dict(zip(scope["id"], scope["key"]))
    url = jira_connector._options["server"].rstrip("/") + "/rest/api/3/changelog/bulkfetch"
    ids = scope["id"].tolist()
    rows: list[dict] = []
    try:
        for start in range(0, len(ids), BATCH):
            body = {"issueIdsOrKeys": ids[start:start + BATCH], "fieldIds": list(FIELDS), "maxResults": BATCH}
            while True:
                response = jira_connector._session.post(url, json=body)
                response.raise_for_status()
                data = response.json()
                rows.extend(_parse_items(data.get("issueChangeLogs", []), key_by_id))
                token = data.get("nextPageToken")
                if not token:
                    break
                body["nextPageToken"] = token
    except Exception:
        return empty_history()
    if not rows:
        return empty_history()
    return pd.DataFrame(rows, columns=COLUMNS).sort_values(["key", "at"]).reset_index(drop=True)
