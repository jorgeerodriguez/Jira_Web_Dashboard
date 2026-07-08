"""Delivery-forecast aggregation: forecast inputs for the open Initiative(s) + Features.

Per open Initiative/Feature: child-scope counts (done/remaining/todo/prog/blk/hold), recent
throughput (children that reached Done in the last 12 weeks, from the changelog), the child
drill-down, and cycle days for done children. The Monte Carlo burn-down runs client-side; this
supplies its inputs (items, rollup, children, cycle). Reproduces df_build's methodology.

`scd` in the child drill-down is the issue's updated timestamp (the store does not capture
statuscategorychangedate); the forecast's `recent` uses exact changelog Done-dates.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import duckdb

from darkstar.leadtime import _lead_and_cycle  # reuse the verified active-spell cycle compute

_RECENT_WEEKS: int = 12
_TODO_STATUSES: tuple[str, ...] = ("To Do", "Tech Discovery Required", "Triage")
_PROG_STATUSES: tuple[str, ...] = ("In Progress", "Validating")
_TERMINAL: tuple[str, ...] = ("Done", "Will Not Do")


def _counts(children: list[dict], recent_keys: set[str]) -> dict:
    """Child-scope buckets for one item (mirrors df_build's counts())."""
    open_children = [c for c in children if c["st"] not in _TERMINAL]
    return {
        "done": sum(1 for c in children if c["st"] == "Done"),
        "remaining": len(open_children),
        "todo": sum(1 for c in open_children if c["st"] in _TODO_STATUSES),
        "prog": sum(1 for c in open_children if c["st"] in _PROG_STATUSES),
        "blk": sum(1 for c in open_children if c["st"] == "Blocked"),
        "hold": sum(1 for c in open_children if c["st"] == "On Hold"),
        "recent": sum(1 for c in children if c["k"] in recent_keys),
    }


def _proxy_created(children: list[dict], fallback: datetime | None) -> str:
    """Earliest child created date (ISO), or the item's own created date, as a start proxy."""
    dates = sorted(c["created"] for c in children if c["created"] is not None)
    if dates:
        return dates[0].date().isoformat()
    return fallback.date().isoformat() if fallback is not None else "2026-01-01"


def delivery_report(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict:
    """Forecast items (initiatives + features), rollup, child drill-down, and cycle days."""
    initiatives = connection.execute(
        "SELECT key, issuetype, status, summary, parent_key, created "
        "FROM issues WHERE issuetype = 'Initiative' AND status_category <> 'done'"
    ).fetchall()
    open_initiative_keys = [row[0] for row in initiatives]

    # Open Features, plus any Feature (open OR done) that rolls up to an open Initiative — so a
    # completed Feature still counts toward its Initiative's scope and progress.
    if open_initiative_keys:
        init_placeholders = ", ".join(["?"] * len(open_initiative_keys))
        features = connection.execute(
            f"SELECT key, issuetype, status, summary, parent_key, created FROM issues "
            f"WHERE issuetype = 'Feature' AND (status_category <> 'done' OR parent_key IN ({init_placeholders}))",
            open_initiative_keys,
        ).fetchall()
    else:
        features = connection.execute(
            "SELECT key, issuetype, status, summary, parent_key, created "
            "FROM issues WHERE issuetype = 'Feature' AND status_category <> 'done'"
        ).fetchall()

    epics = initiatives + features
    if not epics:
        return {"items": [], "rollup": {}, "children": {}, "cycle": {}}

    epic_keys = [row[0] for row in epics]
    epic_placeholders = ", ".join(["?"] * len(epic_keys))
    children_by_parent: dict[str, list[dict]] = {}
    child_keys: list[str] = []
    for key, parent, summary, status, category, assignee, updated, target_end, created in connection.execute(
        f"SELECT key, parent_key, summary, status, status_category, assignee, updated, target_end, created "
        f"FROM issues WHERE parent_key IN ({epic_placeholders})",
        epic_keys,
    ).fetchall():
        children_by_parent.setdefault(parent, []).append({
            "k": key, "s": summary or "", "st": status, "c": category, "a": assignee,
            "scd": updated.date().isoformat() if updated is not None else None,
            "te": target_end.isoformat() if target_end is not None else None,
            "created": created,
        })
        child_keys.append(key)

    transitions_by_key: dict[str, list[tuple[str, datetime]]] = {}
    if child_keys:
        child_placeholders = ", ".join(["?"] * len(child_keys))
        for key, to_status, changed_at in connection.execute(
            f"SELECT key, to_status, changed_at FROM transitions WHERE key IN ({child_placeholders}) ORDER BY key, seq",
            child_keys,
        ).fetchall():
            transitions_by_key.setdefault(key, []).append((to_status, changed_at))

    cutoff = now - timedelta(weeks=_RECENT_WEEKS)
    created_by_key = {c["k"]: c["created"] for kids in children_by_parent.values() for c in kids}
    recent_keys: set[str] = set()
    cycle: dict[str, int] = {}
    for key, transitions in transitions_by_key.items():
        done_dates = [changed_at for (to_status, changed_at) in transitions if to_status == "Done"]
        if not done_dates:
            continue
        if max(done_dates) >= cutoff:
            recent_keys.add(key)
        _lead, cycle_days, _done = _lead_and_cycle(created_by_key[key], transitions)
        cycle[key] = cycle_days

    initiative_keys = {row[0] for row in epics if row[1] == "Initiative"}
    rollup = {row[0]: row[4] for row in epics if row[1] == "Feature" and row[4] in initiative_keys}

    feature_items = []
    for key, _type, status, summary, _parent, created in epics:
        if _type != "Feature":
            continue
        kids = children_by_parent.get(key, [])
        feature_items.append({
            "type": "feature", "key": key, "name": summary, "status": status,
            "created": _proxy_created(kids, created), **_counts(kids, recent_keys),
        })

    initiative_items = []
    for key, _type, status, summary, _parent, created in epics:
        if _type != "Initiative":
            continue
        rolled = [row[0] for row in epics if rollup.get(row[0]) == key]
        rolled_kids = [c for feature_key in rolled for c in children_by_parent.get(feature_key, [])]
        initiative_items.append({
            "type": "initiative", "key": key, "name": summary, "status": status,
            "created": created.date().isoformat() if created is not None else _proxy_created(rolled_kids, None),
            **_counts(rolled_kids, recent_keys),
            "note": f"Rolls up {len(rolled)} Features; several may have no stories yet, so this "
                    f"date is a floor for the remaining work and will extend as they're broken down.",
        })

    children_out = {
        parent: [{field: child[field] for field in ("k", "s", "st", "c", "a", "scd", "te")} for child in kids]
        for parent, kids in children_by_parent.items()
    }
    return {"items": initiative_items + feature_items, "rollup": rollup, "children": children_out, "cycle": cycle}
