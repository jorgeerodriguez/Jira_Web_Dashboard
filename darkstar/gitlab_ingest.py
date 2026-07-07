"""GitLab merge-request ingest: merged MRs by roster members, with changed file paths.

Parallel to the Jira poller. Pulls merged MRs from the PE groups (audacy-inc/devops and
audacy-inc/gcp) over a trailing window, attributes each to a roster member via
GITLAB_USERNAMES, and stores the MR plus its changed file paths. The SME matrix is then
tagged from real authorship (see gitlab_domains), which fills the gaps sparse Jira titles leave.

Transport mirrors the Jira poller: a token from the environment (GITLAB_TOKEN), so the same
code runs locally and in-cluster.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import duckdb
import requests

from darkstar import store
from darkstar.roster import GITLAB_USERNAMES

logger = logging.getLogger("darkstar.gitlab_ingest")

_API: str = "https://gitlab.com/api/v4"
_PE_GROUP_IDS: tuple[int, ...] = (115211004, 116139818)  # audacy-inc/devops, audacy-inc/gcp
_MAX_FILES_PER_MR: int = 60
_PER_PAGE: int = 100
_TIMEOUT_SECONDS: int = 30


def _token() -> str:
    """The GitLab API token; raises loudly if unset (no silent no-op crawl)."""
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        raise RuntimeError("missing required environment variable: GITLAB_TOKEN")
    return token


def _to_naive_utc(value: str) -> datetime:
    """Parse a GitLab ISO-8601 timestamp ('...Z' or offset) to a naive-UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _merged_mrs(session: requests.Session, group_id: int, updated_after_iso: str) -> list[dict]:
    """Every merged MR in a group updated since the cutoff, following pagination."""
    results: list[dict] = []
    page = 1
    while True:
        response = session.get(
            f"{_API}/groups/{group_id}/merge_requests",
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


def _project_path(mr: dict) -> str:
    """The repo's full namespace path, e.g. 'audacy-inc/devops/pe-morning-report'."""
    full_reference = (mr.get("references") or {}).get("full", "")
    return full_reference.split("!")[0] or str(mr["project_id"])


def run_gitlab_sync(connection: duckdb.DuckDBPyConnection, now: datetime, window_days: int) -> tuple[int, int]:
    """Crawl merged MRs by roster members over the trailing window; store MRs + file paths.

    `now` is a naive-UTC datetime (consistent with the rest of the store). Returns
    (merge requests written, file-path rows written).
    """
    cutoff = now - timedelta(days=window_days)
    updated_after_iso = cutoff.replace(tzinfo=timezone.utc).isoformat()
    fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)

    session = requests.Session()
    session.headers["PRIVATE-TOKEN"] = _token()

    mr_rows: list[store.MergeRequestRow] = []
    file_rows: list[tuple[int, str]] = []
    seen_ids: set[int] = set()

    for group_id in _PE_GROUP_IDS:
        for mr in _merged_mrs(session, group_id, updated_after_iso):
            account_id = GITLAB_USERNAMES.get((mr.get("author") or {}).get("username", ""))
            if account_id is None:
                continue
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
            mr_rows.append(store.MergeRequestRow(
                id=mr_id,
                project_path=_project_path(mr),
                iid=mr["iid"],
                author_account_id=account_id,
                title=mr.get("title") or "",
                merged_at=merged_naive,
                web_url=mr.get("web_url") or "",
                fetched_at=fetched_at,
            ))
            file_rows.extend((mr_id, path) for path in paths)

    store.upsert_merge_requests(connection, mr_rows)
    store.replace_mr_files(connection, [row.id for row in mr_rows], file_rows)
    logger.info("gitlab sync: %d merge requests, %d file rows", len(mr_rows), len(file_rows))
    return len(mr_rows), len(file_rows)
