"""GitLab merge-request ingest: merged MRs by tracked authors, with changed file paths.

Parallel to the Jira poller. Pulls merged MRs from the PE groups (audacy-inc/devops and
audacy-inc/gcp) over a trailing window, attributes each to a tracked author via
every author it finds; roster.GITLAB_USERNAMES only decides which key their merge requests are
mr_authors.py), and
stores the MR plus its changed file paths. The SME matrix is then tagged from real authorship
(see gitlab_domains), which fills the gaps sparse Jira titles leave; it keys on ROSTER, so
tracked non-roster authors feed the MR-turnaround view without entering the SME matrix.

Transport mirrors the Jira poller: a token from the environment (GITLAB_TOKEN), so the same
code runs locally and in-cluster.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import duckdb
import requests

from darkstar import config, mr_authors, store
from darkstar.roster import GITLAB_USERNAMES

logger = logging.getLogger("darkstar.gitlab_ingest")

_API: str = "https://gitlab.com/api/v4"
_PE_GROUP_IDS: tuple[int, ...] = (115211004, 116139818)  # audacy-inc/devops, audacy-inc/gcp
# individual repos outside the PE groups worth tracking (they live in other groups).
_PE_PROJECT_IDS: tuple[int, ...] = (74581778, 83803878)  # audacy-inc/secops/aws-identity-center/tf-org{,-v2}
_MAX_FILES_PER_MR: int = 60
_PER_PAGE: int = 100
_TIMEOUT_SECONDS: int = 30
# Re-fetch a small overlap before the watermark on incremental crawls so a boundary MR is never
# missed (upserts are idempotent, so the overlap is harmless).
_WATERMARK_MARGIN: timedelta = timedelta(minutes=5)


def _token() -> str:
    """The GitLab API token; raises loudly if unset (no silent no-op crawl)."""
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        raise RuntimeError("missing required environment variable: GITLAB_TOKEN")
    return token


def search_members(query: str, limit: int) -> list[dict]:
    """GitLab users in the PE groups matching `query`, for the dashboard's author picker.

    Without this the author field is a bare text box: you have to already know the exact GitLab
    username, and anything else — an email address, a display name — is accepted and silently
    matches no merge requests at all. Searching the groups keeps the picker to people who could
    plausibly have MRs in the store.
    """
    session = requests.Session()
    session.headers["PRIVATE-TOKEN"] = _token()
    seen: dict[str, dict] = {}
    for group_id in _PE_GROUP_IDS:
        response = session.get(
            f"{_API}/groups/{group_id}/members/all",
            params={"query": query, "per_page": limit}, timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        for member in response.json():
            username = member.get("username")
            if username and username not in seen:
                seen[username] = {"username": username, "name": member.get("name") or username}
    return sorted(seen.values(), key=lambda m: m["username"])[:limit]


def _to_naive_utc(value: str) -> datetime:
    """Parse a GitLab ISO-8601 timestamp ('...Z' or offset) to a naive-UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _merged_mrs(session: requests.Session, scope: str, updated_after_iso: str) -> list[dict]:
    """Every merged MR in a scope ('groups/{id}' or 'projects/{id}') since the cutoff, paginated."""
    results: list[dict] = []
    page = 1
    while True:
        response = session.get(
            f"{_API}/{scope}/merge_requests",
            params={"state": "merged", "updated_after": updated_after_iso,
                    "per_page": _PER_PAGE, "page": page},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        batch = response.json()
        results.extend(batch)
        if len(batch) < _PER_PAGE:
            return results
        page += 1


def _changed_paths(session: requests.Session, project_id: int, iid: int) -> list[str]:
    """The changed file paths for one MR (new path, falling back to old for deletions)."""
    response = session.get(
        f"{_API}/projects/{project_id}/merge_requests/{iid}/changes",
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    changes = response.json().get("changes", [])
    paths: list[str] = []
    for change in changes[:_MAX_FILES_PER_MR]:
        path = change.get("new_path") or change.get("old_path")
        if path:
            paths.append(path)
    return paths


# Automated commenters. Their notes are not review activity: GitLab Duo answers on every MR, and
# bot accounts post pipeline and security chatter. GitLab does not flag these reliably in the notes
# payload, so match the account names instead.
_BOT_USERNAMES: frozenset[str] = frozenset({"gitlabduo", "gitlab-duo", "ghost", "alert-bot"})
_BOT_SUFFIXES: tuple[str, ...] = ("_bot", "-bot", "bot")

_READY_NOTE: str = "as **ready**"
_DRAFT_NOTE: str = "as **draft**"
_APPROVAL_NOTE: str = "approved this merge request"


def _is_bot(username: str) -> bool:
    """True for automated commenters, whose notes must not count as human review activity."""
    name = (username or "").lower()
    return name in _BOT_USERNAMES or name.endswith(_BOT_SUFFIXES)


def _mr_notes(session: requests.Session, project_id: int, iid: int) -> list[dict]:
    """Every note on an MR, system and user alike (one page is ample; MRs rarely exceed 100)."""
    response = session.get(
        f"{_API}/projects/{project_id}/merge_requests/{iid}/notes",
        params={"per_page": _PER_PAGE}, timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _mr_pipelines(session: requests.Session, project_id: int, iid: int,
                  mr_id: int) -> list[store.MergeRequestPipelineRow]:
    """Pipeline results for one merge request, oldest first.

    Costs one API call per merge request, which roughly doubles a full crawl. Worth it because red
    time was skewing the turnaround badly: 32% of open hours on the slowest PE-authored MRs were spent
    with a failing pipeline, and the clock now excludes it.
    """
    response = session.get(
        f"{_API}/projects/{project_id}/merge_requests/{iid}/pipelines",
        params={"per_page": 100}, timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    raw = response.json() or []
    rows: list[store.MergeRequestPipelineRow] = []
    for seq, pipeline in enumerate(sorted(raw, key=lambda p: p.get("updated_at") or "")):
        when = pipeline.get("updated_at") or pipeline.get("created_at")
        status = pipeline.get("status")
        if not when or not status:
            continue
        rows.append(store.MergeRequestPipelineRow(
            mr_id=mr_id, status=status, happened_at=_to_naive_utc(when), seq=seq))
    return rows


def _mr_events(mr_id: int, author_username: str, notes: list[dict]) -> list[store.MergeRequestEventRow]:
    """Draft/ready transitions and the first human review comment, from an MR's notes.

    Draft state comes from GitLab's own system notes, which a rebase cannot rewrite -- unlike
    commit timestamps, which proved useless for this (a three-week-old MR can carry a commit dated
    minutes before the merge). "review" is the first note from a human who is not the MR's author:
    a self-comment is not review, and neither is Duo's automatic reply.
    """
    events: list[tuple[str, datetime]] = []
    review_at: datetime | None = None
    approval_at: datetime | None = None
    for note in notes:
        username = ((note.get("author") or {}).get("username")) or ""
        created = _to_naive_utc(note["created_at"])
        if note.get("system"):
            body = note.get("body") or ""
            if _READY_NOTE in body:
                events.append(("ready", created))
            elif _DRAFT_NOTE in body:
                events.append(("draft", created))
            elif (_APPROVAL_NOTE in body and username != author_username
                  and not _is_bot(username)):
                # An approval IS the review. PE merges its own work once a colleague approves, so
                # counting comments alone measured conversation, not review: on the crawled sample
                # 111 of 124 MRs carried an approval and only 23 drew a comment.
                if approval_at is None or created < approval_at:
                    approval_at = created
            continue
        if _is_bot(username) or username == author_username:
            continue
        if review_at is None or created < review_at:
            review_at = created
    if review_at is not None:
        events.append(("review", review_at))
    if approval_at is not None:
        events.append(("approval", approval_at))
    events.sort(key=lambda pair: pair[1])
    return [store.MergeRequestEventRow(mr_id=mr_id, kind=kind, happened_at=when, seq=i)
            for i, (kind, when) in enumerate(events)]


def _project_path(mr: dict) -> str:
    """The repo's full namespace path, e.g. 'audacy-inc/devops/pe-morning-report'."""
    full_reference = (mr.get("references") or {}).get("full", "")
    return full_reference.split("!")[0] or str(mr["project_id"])


def _needs_backfill(connection: duckdb.DuckDBPyConnection, cutoff: datetime) -> bool:
    """True if in-window MRs lack opened_at/description/events, which an incremental crawl cannot fix.

    Incremental crawls only re-fetch MRs *updated* since the watermark, so rows written before
    opened_at/labels existed would stay incomplete
    forever. One full re-crawl fixes both. Self-terminating: once every in-window row has an
    opened_at this is False again, and rows older than the window are never revisited.

    author_name is what makes the census release actually take effect. Dropping the ingest-time
    author filter changes nothing on its own: no roster edit fires on deploy, so the pod would crawl
    incrementally and every author the old filter discarded would stay discarded. Every pre-existing
    row has a NULL author_name, so this forces exactly one full re-crawl, which ingests everyone.
    """
    missing = connection.execute(
        "SELECT count(*) FROM merge_requests "
        "WHERE merged_at >= ? AND (opened_at IS NULL OR description IS NULL "
        "  OR events_fetched_at IS NULL OR merged_by IS NULL OR source_branch IS NULL "
        "  OR pipelines_fetched_at IS NULL OR author_name IS NULL)", [cutoff]
    ).fetchone()[0]
    if missing:
        logger.info("gitlab sync: %s in-window MRs incomplete, forcing a full re-crawl", missing)
    return bool(missing)


def run_gitlab_sync(connection: duckdb.DuckDBPyConnection, now: datetime, window_days: int) -> tuple[int, int]:
    """Crawl merged MRs by tracked authors: full `window_days` on the first run, incremental after.

    The first crawl (no stored watermark) pulls the whole trailing window; every crawl after pulls
    only MRs updated since the last successful sync (minus a small margin). The watermark advances
    only after a successful crawl, so a failed run just retries the same slice next time. A store
    holding incomplete in-window rows is re-crawled in full once (see _needs_backfill).

    The roster is read ONCE here and both used for the crawl and recorded against it. That pairing
    is what makes adding an author safe under concurrency: an author added while this crawl is
    running bumps the version past the one recorded at the end, so the next crawl is full and picks
    them up. Re-reading the roster later, or recording the version at the end, would let a crawl
    stamp a version it never actually crawled and lose that author's history for good.
    """
    roster = mr_authors.read(mr_authors.authors_path(config.db_path()))
    version = int(roster.get("version", 0))
    watermark = store.get_gitlab_watermark(connection)
    window_cutoff = now - timedelta(days=window_days)
    if watermark is None or _needs_backfill(connection, window_cutoff):
        cutoff = window_cutoff
    else:
        cutoff = watermark - _WATERMARK_MARGIN
    written = _sync_scopes(connection, cutoff, _PE_GROUP_IDS, _PE_PROJECT_IDS, roster)
    store.set_gitlab_watermark(connection, now)
    store.set_roster_version(connection, version)
    return written


def _sync_scopes(connection: duckdb.DuckDBPyConnection, cutoff: datetime,
                 group_ids: tuple[int, ...], project_ids: tuple[int, ...],
                 roster: dict) -> tuple[int, int]:
    """Crawl merged MRs by roster members from the given groups + projects; store MRs + file paths.

    `cutoff` is a naive-UTC floor: only MRs updated on GitLab and merged on or after it are
    (re)ingested. Returns (merge requests written, file-path rows written).
    """
    updated_after_iso = cutoff.replace(tzinfo=timezone.utc).isoformat()
    fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)

    session = requests.Session()
    session.headers["PRIVATE-TOKEN"] = _token()

    mr_rows: list[store.MergeRequestRow] = []
    file_rows: list[tuple[int, str]] = []
    event_rows: list[store.MergeRequestEventRow] = []
    pipeline_rows: list[store.MergeRequestPipelineRow] = []
    seen_ids: set[int] = set()

    # Every author is ingested. This used to filter to a known list, which made the store a function
    # of who somebody had remembered to add: a contributor nobody listed was discarded at crawl time
    # and could never appear on any panel, no matter the lookback. Adoption is a census question --
    # "who is using the self-service tooling" -- and a census cannot be answered from a curated list.
    #
    # Roster-gated views are unaffected, by the same mechanism that already protected them from
    # dashboard-added authors: an author outside the PE roster is keyed by GitLab username, and
    # velocity/capacity/SME look ROSTER up by Jira accountId, which a username never matches.

    scopes = [f"groups/{gid}" for gid in group_ids] + [f"projects/{pid}" for pid in project_ids]
    for scope in scopes:
        for mr in _merged_mrs(session, scope, updated_after_iso):
            author = mr.get("author") or {}
            username = author.get("username") or ""
            if not username:
                continue
            # Roster members keep their Jira accountId so their history stays one series; everyone
            # else is keyed by username. The roster wins on a collision, so re-adding a member
            # through the dashboard cannot split one person into two rows with the same name.
            account_id = GITLAB_USERNAMES.get(username, username)
            merged_at = mr.get("merged_at")
            if not merged_at:
                continue
            merged_naive = _to_naive_utc(merged_at)
            if merged_naive < cutoff:
                continue
            mr_id = mr["id"]
            if mr_id in seen_ids:
                continue
            seen_ids.add(mr_id)

            try:
                paths = _changed_paths(session, mr["project_id"], mr["iid"])
            except requests.RequestException as exc:
                logger.warning("MR %s!%s changes fetch failed, no paths: %s", mr["project_id"], mr["iid"], exc)
                paths = []
            try:
                notes = _mr_notes(session, mr["project_id"], mr["iid"])
            except requests.RequestException as exc:
                logger.warning("MR %s!%s notes fetch failed, no draft/review events: %s",
                               mr["project_id"], mr["iid"], exc)
                notes = []
            event_rows.extend(_mr_events(mr_id, username, notes))
            try:
                pipeline_rows.extend(_mr_pipelines(session, mr["project_id"], mr["iid"], mr_id))
            except requests.RequestException as exc:
                # No pipeline history means no red time is excluded, so the MR reports its full ready
                # clock. That errs toward charging PE for time it may not owe, which is the safe way
                # round: a fetch failure must not quietly make a merge request look fast.
                logger.warning("MR %s!%s pipelines fetch failed, red time not excluded: %s",
                               mr["project_id"], mr["iid"], exc)
            mr_rows.append(store.MergeRequestRow(
                id=mr_id,
                project_path=_project_path(mr),
                iid=mr["iid"],
                author_account_id=account_id,
                title=mr.get("title") or "",
                opened_at=_to_naive_utc(mr["created_at"]),
                merged_at=merged_naive,
                labels=list(mr.get("labels") or []),
                web_url=mr.get("web_url") or "",
                merged_by=((mr.get("merged_by") or {}).get("username")) or "",
                fetched_at=fetched_at,
                events_fetched_at=fetched_at,
                description=mr.get("description") or "",
                source_branch=mr.get("source_branch") or "",
                pipelines_fetched_at=fetched_at,
                author_name=author.get("name") or username,
            ))
            file_rows.extend((mr_id, path) for path in paths)

    store.upsert_merge_requests(connection, mr_rows)
    store.replace_mr_files(connection, [row.id for row in mr_rows], file_rows)
    by_mr: dict[int, list[store.MergeRequestEventRow]] = {row.id: [] for row in mr_rows}
    for event in event_rows:
        by_mr.setdefault(event.mr_id, []).append(event)
    for mr_id, events in by_mr.items():
        store.replace_mr_events(connection, mr_id, events)
    pipes_by_mr: dict[int, list[store.MergeRequestPipelineRow]] = {row.id: [] for row in mr_rows}
    for pipeline in pipeline_rows:
        pipes_by_mr.setdefault(pipeline.mr_id, []).append(pipeline)
    for mr_id, pipelines in pipes_by_mr.items():
        store.replace_mr_pipelines(connection, mr_id, pipelines)
    logger.info("gitlab sync: %d merge requests, %d file rows, %d draft/review events, "
                "%d pipeline results", len(mr_rows), len(file_rows), len(event_rows),
                len(pipeline_rows))
    return len(mr_rows), len(file_rows)
