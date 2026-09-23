"""darkstar Jira poller.

One sync cycle:
  1. plan_sync() decides, from the stored watermark and the clock, whether this is a
     full crawl (first run only) or an incremental `updated >=` slice.
  2. fetch_issues() pulls the matching DEVOPS issues (paginated).
  3. fetch_transitions() pulls each fetched issue's changelog and extracts its status
     transitions (the basis for changelog-derived completion, cycle, and lead time).
  4. the store is updated (issues upserted, transitions replaced for the fetched keys).
  5. reconcile_departed_issues() deletes stored open issues that were moved out of DEVOPS
     or deleted -- the one change an `updated >=` slice can never see -- and the
     watermark is advanced.

Blocking Jira calls run under asyncio.to_thread when driven by poll_loop(); all Jira
timestamps are normalized to naive UTC to match the store.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
from jira import JIRA
from jira.exceptions import JIRAError
from jira.resources import Issue

from darkstar import store
from darkstar.config import Config, load_config
from darkstar.metrics import SELF_SERVICE_EPOCH

logger = logging.getLogger("darkstar.ingest")

# Overlap the incremental window slightly: Jira's `updated` has minute resolution and its clock is
# not ours, so a watermark used as an exact floor can miss an issue updated in the same minute the
# previous sync finished.
_WATERMARK_MARGIN: timedelta = timedelta(minutes=2)

# Splits a JQL statement from its trailing ORDER BY, which cannot appear inside
# parentheses when the statement is wrapped as a sub-clause.
_ORDER_BY_RE: re.Pattern[str] = re.compile(r"\s+ORDER\s+BY\s+", re.IGNORECASE)

_FULL_JQL: str = "project = DEVOPS ORDER BY updated ASC"
# Jira's side of the store's "open" (issues.status_category <> 'done'): what reconcile_departed_issues
# compares the stored open rows against.
_OPEN_JQL: str = "project = DEVOPS AND statusCategory != Done"
_PAGE_SIZE: int = 100
_RETRY_ATTEMPTS: int = 3
_RETRY_BACKOFF_SECONDS: float = 2.0

# Only the fields the dashboards need (keeps the payload small).
_ISSUE_FIELDS: str = (
    "summary,status,issuetype,priority,assignee,reporter,created,updated,resolutiondate,labels,"
    "parent,project,customfield_11751,customfield_10946,customfield_10947,"
    # customfield_11534 = "Merge Request", a free-text field holding a GitLab MR URL. The Development
    # field (customfield_10400) is deliberately NOT fetched: it serves a stale cache that omitted a
    # merged pull request on DEVOPS-10117 and disagreed with its own panel on build count. The dev
    # panel is read through JQL instead -- see fetch_dev_panel_keys.
    # customfield_10968 = "Estimated Size" (Small/Medium/Large/XL); duedate = Jira's built-in "Due
    # date". Bump _FIELDS_VERSION whenever this list gains a column, or existing rows keep a NULL
    # there forever.
    "customfield_11534,customfield_10968,duedate"
)

# Version of _ISSUE_FIELDS above. A store stamped below this has rows that predate a column, so the
# next cycle runs one field-only backfill and stamps the new value.
#
# Why a version and not a NULL count: `issues_missing_link_fields` can key on `dev_has_pr IS NULL`
# only because `apply_dev_panel_flags` writes False for every issue it queries, making NULL mean
# "never asked". Most of the fields added since have no such sentinel -- an issue with no Estimated
# Size set has a legitimately NULL `estimated_size`, true of ~71% of DEVOPS -- so a NULL count never
# reaches zero and the backfill re-crawls the store every cycle, forever. The version is the one
# marker that is unambiguous regardless of what the field itself holds, and it costs one integer for
# every column added after this one.
_FIELDS_VERSION: int = 2   # 2: duedate

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
    departed_issues: int
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
        mr_field_url=(fields.get("customfield_11534") or None),
        # A select field: Jira sends {"value": "Small", ...} or null. "Unestimated" is deliberately
        # NOT substituted here -- that label is a presentation choice (it matches
        # reports/backlog_report.py's SIZE_ORDER) and the store records what Jira holds, which is
        # nothing.
        estimated_size=((fields.get("customfield_10968") or {}).get("value") or None),
        due_date=_parse_date(fields.get("duedate")),
        # Filled by fetch_dev_panel_keys after the batch is mapped; unknown until then.
        dev_has_pr=None,
        dev_has_commits=None,
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


def fetch_dev_panel_keys(jira: JIRA, scope_jql: str, predicate: str) -> set[str]:
    """Keys whose development panel satisfies `predicate`, e.g. "development[pullrequests].all > 0".

    One JQL query for the whole batch rather than a field on each issue, because the Development
    summary field is a CACHE and lies: on DEVOPS-10117 it reported five builds where the panel showed
    two, omitted the issue's merged pull request altogether, and flagged itself "isStale". The JQL
    index agreed with the panel, and one query is cheaper than a field nobody can trust.

    Jira rejects two development[] clauses OR'd together, so each predicate is asked separately.

    The scope's ORDER BY is stripped before wrapping. Every JQL this module builds ends with one, and
    `(... ORDER BY updated ASC) AND development[...]` is a 400 -- an ORDER BY cannot sit inside
    parentheses. Ordering is meaningless here anyway; only the set of keys is wanted.
    """
    scope = _ORDER_BY_RE.split(scope_jql)[0].strip()
    return fetch_keys(jira, f"({scope}) AND {predicate}", f"enhanced_search_issues({predicate})")


def fetch_keys(jira: JIRA, jql: str, description: str) -> set[str]:
    """Keys of every issue matching jql (token-paginated, no fields beyond the key)."""
    keys: set[str] = set()
    next_token: str | None = None
    while True:
        kwargs = {"jql_str": jql, "maxResults": _PAGE_SIZE, "fields": "key"}
        if next_token:
            kwargs["nextPageToken"] = next_token
        issues = _retry(lambda: jira.enhanced_search_issues(**kwargs), description)
        if not issues:
            break
        keys.update(issue.key for issue in issues)
        next_token = getattr(issues, "nextPageToken", None)
        if not next_token:
            break
    return keys


def fetch_current_key(jira: JIRA, key: str) -> str | None:
    """The key Jira files this issue under now, or None when Jira has no such issue for us.

    Jira follows an old key to wherever the issue went: after its move, DEVOPS-9888 answers as
    DAT-5323. A 404 means deleted, or no longer visible to the API account -- Jira does not tell the
    two apart, and darkstar can display neither. The 404 is the only failure that is an answer; any
    other is retried and then raised.
    """
    def _get() -> str | None:
        try:
            return jira.issue(key, fields="project").key
        except JIRAError as error:
            if error.status_code == 404:
                return None
            raise
    return _retry(_get, f"issue {key}")


def reconcile_departed_issues(jira: JIRA, connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Delete stored open issues that were moved out of DEVOPS or deleted; return their keys.

    The incremental slice cannot see these. It asks for `project = DEVOPS AND updated >= ...`, and an
    issue that has been moved to another project or deleted never matches that again, so its row kept
    the status it last had in DEVOPS, forever. On 2026-09-23 that was 10 of the 17 tickets in the
    intake queue: eight moved to DAT/ADTECH/AWQA/SECOPS/ST, two deleted.

    Only rows the store holds as open are checked. Those are the rows that put ghosts on screen (the
    queue, WIP, open Features), and there are few of them: ~150, two key-only search pages, against
    ~9,800 stored rows. A done row that later leaves keeps counting in the history it was part of.

    A key missing from Jira's open set is a CANDIDATE, never a verdict. It may simply have closed or
    been created between this cycle's slice and this search, and a search that came back short would
    make every open row a candidate at once. Deleting on absence would lose rows for good, because
    the slice never revisits an issue nobody edits. So each candidate is fetched by key, and removed
    only when Jira positively says it is gone (404) or now files it under another key -- which is
    every move, including one out of DEVOPS and back, where the slice has already stored the new
    key. A candidate still under its own key is left to the slice, which re-reads anything updated
    since the watermark.
    """
    stored_open = {key for (key,) in connection.execute(
        "SELECT key FROM issues WHERE status_category <> 'done'").fetchall()}
    candidates = sorted(stored_open - fetch_keys(jira, _OPEN_JQL, "enhanced_search_issues(open)"))
    departed = [key for key in candidates if fetch_current_key(jira, key) != key]
    store.delete_issues(connection, departed)
    if departed:
        logger.info("jira reconcile: removed %d issues that left DEVOPS: %s",
                    len(departed), ", ".join(departed))
    return departed


def apply_dev_panel_flags(rows: list[store.IssueRow], with_pr: set[str],
                          with_commits: set[str]) -> list[store.IssueRow]:
    """Set dev_has_pr / dev_has_commits on the batch from the two key sets.

    False, not None, for a row that was queried and did not come back: Jira positively reported no
    linked code, which IS evidence. None survives only where the query never ran.
    """
    return [replace(row, dev_has_pr=row.key in with_pr, dev_has_commits=row.key in with_commits)
            for row in rows]


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


def issues_missing_link_fields(connection: duckdb.DuckDBPyConnection, floor: datetime) -> int:
    """In-window issues that predate the Jira link columns and will never be revisited otherwise.

    The incremental plan only fetches issues *updated* since the watermark, so an issue that has not
    changed since a column was added keeps NULL there forever. GitLab's side self-heals through
    `_needs_backfill`; this is the Jira equivalent, and without it the columns stayed empty on all
    9,811 stored issues after the deploy that introduced them.

    The marker is `dev_has_pr`, not `mr_field_url`. An issue with no Merge Request field set has a
    legitimately NULL url, so keying on that would re-fetch the same issues every cycle forever;
    `apply_dev_panel_flags` writes False for every issue it queries, so NULL there means only
    "never synced".
    """
    return connection.execute(
        "SELECT count(*) FROM issues WHERE created >= ? AND dev_has_pr IS NULL", [floor]
    ).fetchone()[0]


def backfill_link_fields(jira: JIRA, connection: duckdb.DuckDBPyConnection,
                         floor: datetime) -> int:
    """Re-read issue FIELDS (never changelogs) for the window, filling the Jira link columns.

    Changelogs are the expensive half of a sync -- `fetch_transitions` costs one request per issue, so
    a full re-sync of the store is ~9,800 requests -- and they have not changed. Skipping them turns
    this into roughly 15 requests for the ~1,300 issues since the self-service epoch: 13 pages of
    fields plus the two development[] queries.

    Scoped to the epoch because nothing older is displayed by any panel.
    """
    jql = f'project = DEVOPS AND created >= "{floor:%Y-%m-%d}" ORDER BY created ASC'
    issues = fetch_issues(jira, jql)
    issues = apply_dev_panel_flags(
        issues,
        fetch_dev_panel_keys(jira, jql, "development[pullrequests].all > 0"),
        fetch_dev_panel_keys(jira, jql, "development[commits].all > 0"),
    )
    store.upsert_issues(connection, issues)
    logger.info("jira backfill: refreshed link fields on %d issues since %s (no changelogs)",
                len(issues), floor.date())
    return len(issues)


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
    # Two extra queries for the whole batch, scoped by the same JQL, replacing a per-issue field that
    # cannot be trusted. Jira will not accept both development[] clauses in one query.
    issues = apply_dev_panel_flags(
        issues,
        fetch_dev_panel_keys(jira, jql, "development[pullrequests].all > 0"),
        fetch_dev_panel_keys(jira, jql, "development[commits].all > 0"),
    )
    keys = [issue.key for issue in issues]
    transitions = fetch_transitions(jira, keys)

    store.upsert_issues(connection, issues)
    store.replace_transitions(connection, keys, transitions)
    # After the slice is written, so an issue it has just closed is not taken for a departure.
    departed = reconcile_departed_issues(jira, connection)

    # Self-heal the columns an incremental plan can never reach: it only fetches issues *updated*
    # since the watermark, so a row untouched since a column was added keeps NULL there forever.
    # Two independent triggers, both self-terminating, and the floor is SELF_SERVICE_EPOCH for the
    # same reason the link-field backfill already used it -- no panel displays anything older.
    backfilled = 0
    stale_fields = store.get_fields_version(connection) < _FIELDS_VERSION
    if stale_fields or issues_missing_link_fields(connection, SELF_SERVICE_EPOCH):
        backfilled = backfill_link_fields(jira, connection, SELF_SERVICE_EPOCH)
        store.set_fields_version(connection, _FIELDS_VERSION)

    total_issues = connection.execute("SELECT count(*) FROM issues").fetchone()[0]
    total_transitions = connection.execute("SELECT count(*) FROM transitions").fetchone()[0]
    store.set_sync_meta(connection, now, plan.last_full_sync, total_issues, total_transitions, now)

    return SyncResult(
        full=plan.watermark is None,
        fetched_issues=len(issues) + backfilled,
        fetched_transitions=len(transitions),
        departed_issues=len(departed),
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
                    "departed_issues": result.departed_issues,
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
        f"fetched_transitions={result.fetched_transitions} departed_issues={result.departed_issues} "
        f"total_issues={result.total_issues} "
        f"total_transitions={result.total_transitions}"
    )


if __name__ == "__main__":
    main()
