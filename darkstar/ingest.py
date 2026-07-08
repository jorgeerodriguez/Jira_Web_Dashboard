"""darkstar Jira poller.

One sync cycle:
  1. plan_sync() decides, from the stored watermark and the clock, whether this is a
     full crawl (first run or a due reconcile) or an incremental `updated >=` slice.
  2. fetch_issues() pulls the matching DEVOPS issues (paginated).
  3. fetch_transitions() pulls each fetched issue's changelog and extracts its status
     transitions (the basis for changelog-derived completion, cycle, and lead time).
  4. the store is updated (issues upserted, transitions replaced for the fetched keys)
     and the watermark advanced.

Blocking Jira calls run under asyncio.to_thread when driven by poll_loop(); all Jira
timestamps are normalized to naive UTC to match the store.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
from jira import JIRA
from jira.resources import Issue

from darkstar import store
from darkstar.config import Config, load_config

logger = logging.getLogger("darkstar.ingest")

# Only the fields the dashboards need (keeps the payload small).
_ISSUE_FIELDS: str = (
    "summary,status,issuetype,priority,assignee,reporter,created,updated,resolutiondate,labels,"
    "parent,project,customfield_11751,customfield_10946,customfield_10947"
)
_FULL_JQL: str = "project = DEVOPS ORDER BY updated ASC"
_PAGE_SIZE: int = 100
_WATERMARK_MARGIN: timedelta = timedelta(minutes=2)
_RETRY_ATTEMPTS: int = 3
_RETRY_BACKOFF_SECONDS: float = 2.0


@dataclass(frozen=True)
class SyncPlan:
    """What one sync cycle should do. watermark is None for a full crawl."""

    watermark: datetime | None
    last_full_sync: datetime


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one sync cycle."""

    full: bool
    fetched_issues: int
    fetched_transitions: int
    total_issues: int
    total_transitions: int


def _utcnow() -> datetime:
    """Current time as a naive UTC datetime (the store's timestamp convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_utc(value: str | None) -> datetime | None:
    """Parse a Jira ISO timestamp into a naive UTC datetime, or None."""
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime().replace(tzinfo=None)


def _parse_date(value: str | None) -> date | None:
    """Parse a Jira date custom field into a date, or None."""
    if not value:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _retry(operation, description: str):
    """Call operation(), retrying with warnings on failure, then raising the last error."""
    last_error: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return operation()
        except Exception as error:  # retried below, re-raised after the last attempt
            last_error = error
            logger.warning(
                "jira call failed",
                extra={"op": description, "attempt": attempt, "attempts": _RETRY_ATTEMPTS, "error": str(error)},
            )
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise last_error


def connect_jira(config: Config) -> JIRA:
    """Open a Jira client using basic auth (email + API token)."""
    return JIRA(server=config.jira_server, basic_auth=(config.jira_email, config.jira_api_token))


def get_jira_timezone(jira: JIRA) -> ZoneInfo:
    """Return the API account's timezone (JQL interprets datetimes in it); UTC if unknown."""
    myself = _retry(lambda: jira.myself(), "myself")
    tz_name = myself.get("timeZone") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        logger.warning("unknown Jira timezone, using UTC", extra={"tz": tz_name})
        return ZoneInfo("UTC")


def build_incremental_jql(watermark: datetime, jira_tz: ZoneInfo) -> str:
    """JQL for issues updated since the watermark (converted to the account's tz for JQL)."""
    local = watermark.replace(tzinfo=timezone.utc).astimezone(jira_tz) - _WATERMARK_MARGIN
    stamp = local.strftime("%Y-%m-%d %H:%M")
    return f'project = DEVOPS AND updated >= "{stamp}" ORDER BY updated ASC'


def _map_issue(issue: Issue, fetched_at: datetime) -> store.IssueRow:
    """Map a Jira issue into a store.IssueRow."""
    raw = issue.raw
    fields = raw.get("fields", {})
    status = fields.get("status") or {}
    priority = fields.get("priority") or {}
    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or {}
    lead = fields.get("customfield_11751") or {}
    parent = fields.get("parent") or {}
    return store.IssueRow(
        key=raw["key"],
        id=int(raw["id"]),
        project=(fields.get("project") or {}).get("key", ""),
        issuetype=(fields.get("issuetype") or {}).get("name", ""),
        status=status.get("name", ""),
        status_category=(status.get("statusCategory") or {}).get("key", ""),
        priority=priority.get("name", "No Priority"),
        summary=fields.get("summary") or "",
        assignee=assignee.get("displayName"),
        assignee_account_id=assignee.get("accountId"),
        reporter=reporter.get("displayName"),
        business_lead=lead.get("displayName"),
        parent_key=parent.get("key"),
        created=_parse_utc(fields.get("created")),
        updated=_parse_utc(fields.get("updated")),
        resolutiondate=_parse_utc(fields.get("resolutiondate")),
        planned_start=_parse_date(fields.get("customfield_10946")),
        target_end=_parse_date(fields.get("customfield_10947")),
        labels=list(fields.get("labels") or []),
        fetched_at=fetched_at,
    )


def fetch_issues(jira: JIRA, jql: str) -> list[store.IssueRow]:
    """Fetch all issues matching jql (token-paginated) and map them to IssueRows."""
    fetched_at = _utcnow()
    rows: list[store.IssueRow] = []
    next_token: str | None = None
    while True:
        kwargs = {"jql_str": jql, "maxResults": _PAGE_SIZE, "fields": _ISSUE_FIELDS}
        if next_token:
            kwargs["nextPageToken"] = next_token
        issues = _retry(lambda: jira.enhanced_search_issues(**kwargs), "enhanced_search_issues")
        if not issues:
            break
        rows.extend(_map_issue(issue, fetched_at) for issue in issues)
        next_token = getattr(issues, "nextPageToken", None)
        if not next_token:
            break
    return rows


def _extract_transitions(key: str, histories: list[dict]) -> list[store.TransitionRow]:
    """Extract status-change events from an issue's changelog histories, ordered in time."""
    events: list[tuple[datetime, str]] = []
    for history in histories:
        changed_at = _parse_utc(history.get("created"))
        if changed_at is None:
            continue
        for item in history.get("items", []):
            if item.get("field") == "status":
                events.append((changed_at, item.get("toString") or ""))
    events.sort(key=lambda event: event[0])
    return [
        store.TransitionRow(key=key, to_status=to_status, changed_at=changed_at, seq=seq)
        for seq, (changed_at, to_status) in enumerate(events)
    ]


def fetch_transitions(jira: JIRA, keys: list[str]) -> list[store.TransitionRow]:
    """Fetch each issue's changelog and extract its status transitions.

    One request per key; the incremental plan keeps this list to just-changed issues.
    (expand=changelog returns up to the last 100 history entries, ample for these issues.)
    """
    transitions: list[store.TransitionRow] = []
    for key in keys:
        issue = _retry(lambda: jira.issue(key, expand="changelog"), f"issue changelog {key}")
        histories = issue.raw.get("changelog", {}).get("histories", [])
        transitions.extend(_extract_transitions(key, histories))
    return transitions


def plan_sync(meta: store.SyncMeta | None, now: datetime) -> SyncPlan:
    """Decide whether this cycle is a full crawl or an incremental slice.

    A full crawl happens only once — when the store has no watermark yet (first run). Every cycle
    after is an incremental `updated >=` slice; there is no periodic full reconcile.
    """
    if meta is None or meta.last_incremental_sync is None or meta.last_full_sync is None:
        return SyncPlan(watermark=None, last_full_sync=now)
    return SyncPlan(watermark=meta.last_incremental_sync, last_full_sync=meta.last_full_sync)


def run_sync(
    connection: duckdb.DuckDBPyConnection,
    jira: JIRA,
    plan: SyncPlan,
    now: datetime,
    jira_tz: ZoneInfo,
) -> SyncResult:
    """Execute one planned sync: fetch, write issues + transitions, advance the watermark."""
    jql = _FULL_JQL if plan.watermark is None else build_incremental_jql(plan.watermark, jira_tz)
    issues = fetch_issues(jira, jql)
    keys = [issue.key for issue in issues]
    transitions = fetch_transitions(jira, keys)

    store.upsert_issues(connection, issues)
    store.replace_transitions(connection, keys, transitions)

    total_issues = connection.execute("SELECT count(*) FROM issues").fetchone()[0]
    total_transitions = connection.execute("SELECT count(*) FROM transitions").fetchone()[0]
    store.set_sync_meta(connection, now, plan.last_full_sync, total_issues, total_transitions, now)

    return SyncResult(
        full=plan.watermark is None,
        fetched_issues=len(issues),
        fetched_transitions=len(transitions),
        total_issues=total_issues,
        total_transitions=total_transitions,
    )


def sync_cycle(
    connection: duckdb.DuckDBPyConnection,
    jira: JIRA,
    now: datetime,
    jira_tz: ZoneInfo,
) -> SyncResult:
    """Plan and run one sync cycle against the current store state."""
    meta = store.get_sync_meta(connection)
    plan = plan_sync(meta, now)
    return run_sync(connection, jira, plan, now, jira_tz)


async def poll_loop(config: Config) -> None:
    """Run sync_cycle forever on the configured interval; a failed cycle is logged, not fatal."""
    connection = store.connect(config.db_path)
    store.initialize_schema(connection)
    jira = connect_jira(config)
    jira_tz = get_jira_timezone(jira)
    logger.info("poller started", extra={"db": config.db_path, "interval_s": config.poll_interval_seconds})
    while True:
        now = _utcnow()
        try:
            result = await asyncio.to_thread(sync_cycle, connection, jira, now, jira_tz)
            logger.info(
                "sync ok",
                extra={
                    "full": result.full,
                    "fetched_issues": result.fetched_issues,
                    "fetched_transitions": result.fetched_transitions,
                    "total_issues": result.total_issues,
                },
            )
        except Exception:
            logger.exception("sync failed")
        await asyncio.sleep(config.poll_interval_seconds)


def main() -> None:
    """Run a single sync cycle (standalone / one-shot). Uses config from the environment."""
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    connection = store.connect(config.db_path)
    store.initialize_schema(connection)
    jira = connect_jira(config)
    jira_tz = get_jira_timezone(jira)
    result = sync_cycle(connection, jira, _utcnow(), jira_tz)
    connection.close()
    print(
        f"sync complete: full={result.full} fetched_issues={result.fetched_issues} "
        f"fetched_transitions={result.fetched_transitions} total_issues={result.total_issues} "
        f"total_transitions={result.total_transitions}"
    )


if __name__ == "__main__":
    main()
