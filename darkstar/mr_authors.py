"""Shared, editable MR-author roster persisted as JSON on the PVC.

Which GitLab authors appear in the MR-turnaround table. That table is opt-in and starts empty, so
this file IS its contents: the lead adds whoever they want to compare, and hides rows they want out
of the way, without a deploy -- the same way overrides.py handles the SME matrix. darkstar's second
write path.

It no longer decides what gets crawled. The ingest keeps every author it finds, so adding a name
here fetches nothing; it only decides who this one table lists.

Shape: {"added": {gitlab_username: display_name}, "hidden": [...], "version": int}
  - added:  GitLab usernames to list in the MR-turnaround table. Non-roster authors are keyed in the
            store by their *username*, not a Jira accountId, so they cannot leak into the
            roster-gated views (velocity/capacity/SME all look up ROSTER by accountId and miss).
  - hidden: display names dropped from the table only. The team totals describe every self-service
            author in the window regardless, because a figure that moves when you tidy a table
            cannot be quoted.

Adding an author is served entirely from the store: their merge requests are already there, so no
crawl is triggered and the watermark is left alone.
"""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()
# `version` is a monotonic counter bumped by every add. The GitLab crawl records the version it
# read when it STARTED, so an author added while a crawl is running leaves version != recorded and
# the next crawl is a full one. Without it, a crawl finishing after a second add would stamp its
# watermark and the second author's history would never be fetched.
_EMPTY: dict = {"added": {}, "hidden": [], "version": 0}


def read(path: str) -> dict:
    """Return the persisted author roster, seeding an empty file on first use."""
    with _LOCK:
        return _read_locked(path)


def _read_locked(path: str) -> dict:
    if not os.path.exists(path):
        _write_locked(path, _EMPTY)
        return json.loads(json.dumps(_EMPTY))
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("added", {})
    data.setdefault("hidden", [])
    data.setdefault("version", 0)
    return data


def _write_locked(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, path)


def apply(path: str, op: str, username: str, display_name: str) -> tuple[dict, bool]:
    """Apply one roster edit; returns (updated roster, whether a full re-crawl is needed).

    ops:
      add     — attribute `username`'s merged MRs from the next crawl and show them as
                `display_name`. Needs a re-crawl: an incremental pull only returns MRs updated
                since the watermark, so their history would otherwise never arrive.
      remove  — drop the username from `added` again. No re-crawl; the rows simply stop resolving.
      hide    — hide `display_name`'s row and exclude it from the team totals. No re-crawl.
      show    — unhide `display_name`. No re-crawl.
    """
    if op not in ("add", "remove", "hide", "show"):
        raise ValueError(f"unknown op: {op!r}")
    with _LOCK:
        data = _read_locked(path)
        added, hidden = data["added"], data["hidden"]
        needs_recrawl = False
        if op == "add":
            needs_recrawl = username not in added
            added[username] = display_name or username
            data["hidden"] = [name for name in hidden if name != added[username]]
            if needs_recrawl:
                # Bump before the crawl starts, so a crawl already in flight cannot record it.
                data["version"] = int(data.get("version", 0)) + 1
        elif op == "remove":
            added.pop(username, None)
        elif op == "hide":
            if display_name not in hidden:
                hidden.append(display_name)
        else:
            data["hidden"] = [name for name in hidden if name != display_name]
        _write_locked(path, data)
        return data, needs_recrawl


def authors_path(db_path: str) -> str:
    """Path to the roster JSON, alongside the store on the PVC (mirrors overrides.py)."""
    return os.path.join(os.path.dirname(db_path) or ".", "darkstar_mr_authors.json")
