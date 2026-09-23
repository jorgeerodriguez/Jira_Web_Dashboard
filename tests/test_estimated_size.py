"""Estimated Size (customfield_10968) and the version marker that gets it into an existing store.

The field itself is trivial — a Small/Medium/Large/XL select. Getting it onto the ~10,000 rows
already in the store is not, because the poller is watermark-driven: it fetches only issues Jira
says changed, so a row nobody edits again is never revisited and keeps NULL in any newly added
column forever.

The Jira side already had one self-healing backfill, keyed on `dev_has_pr IS NULL`. That marker
works only because `apply_dev_panel_flags` writes False for every issue it queries, so NULL there
means "never asked". Estimated Size has no such sentinel — ~71% of DEVOPS issues genuinely carry no
size — so the same trick would never reach zero and would re-crawl the store every 15 minutes
forever. These tests pin the version marker that replaces it, and the specific ways it can regress.
"""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import duckdb

from darkstar import ingest, store

_NOW = datetime(2026, 9, 18, 12, 0, 0)
_TZ = ZoneInfo("America/Denver")


def _issue(created=None, **fields):
    base = {
        "summary": "s", "status": {"name": "Done", "statusCategory": {"key": "done"}},
        "issuetype": {"name": "Task"}, "priority": {"name": "Low"}, "labels": [],
        "created": "2026-08-05T16:00:00.000-0600", "updated": "2026-08-05T17:00:00.000-0600",
        "project": {"key": "DEVOPS"},
    }
    if created is not None:
        base["created"] = created.strftime("%Y-%m-%dT%H:%M:%S.000-0000")
    base.update(fields)
    return SimpleNamespace(raw={"key": "DEVOPS-1", "id": "1", "fields": base})


def _size(value):
    """A Jira select value as the API actually sends it."""
    return {"self": "https://example/rest/api/3/customFieldOption/10311", "value": value,
            "id": "10311"}


# --- the field itself ---------------------------------------------------------------------------

def test_the_issue_sync_requests_the_estimated_size_field():
    """Dropping it from the field list empties the column silently — every row maps to None."""
    assert "customfield_10968" in ingest._ISSUE_FIELDS


def test_the_selected_option_reaches_the_row_as_its_label():
    """Jira sends a select as an object; the store holds the label a human chose."""
    assert ingest._map_issue(_issue(customfield_10968=_size("Medium")), _NOW).estimated_size == "Medium"


def test_an_unsized_issue_is_none_rather_than_a_placeholder():
    """None means "Jira holds no size". "Unestimated" is a presentation label, decided by whatever
    renders it (reports/backlog_report.py already spells it that way) — the store records absence."""
    assert ingest._map_issue(_issue(), _NOW).estimated_size is None
    assert ingest._map_issue(_issue(customfield_10968=None), _NOW).estimated_size is None


def test_the_size_survives_a_round_trip_through_the_store():
    """IssueRow is written positionally via astuple, so a column added out of order silently shifts
    every value after it into the wrong column."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    row = ingest._map_issue(_issue(customfield_10968=_size("XL")), _NOW)
    store.upsert_issues(conn, [row])
    stored = conn.execute(
        "SELECT estimated_size, key, summary, fetched_at FROM issues WHERE key = 'DEVOPS-1'"
    ).fetchone()
    assert stored[0] == "XL"
    assert stored[1] == "DEVOPS-1" and stored[2] == "s", "neighbouring columns must not shift"
    assert stored[3] == _NOW


# --- the version marker -------------------------------------------------------------------------

def test_an_unstamped_store_reports_version_zero():
    """Covers both a new store and one written before the column existed; both want a backfill."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    assert store.get_fields_version(conn) == 0


def test_the_version_survives_an_ordinary_sync_meta_write():
    """The trap this guards: set_sync_meta used INSERT OR REPLACE, which rewrites the whole row.

    The poller writes sync_meta every cycle and never passes fields_version, so under REPLACE the
    stamp would blank each pass, re-arming the backfill forever — precisely the failure the version
    exists to prevent, and invisible except as a permanently slow poll.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.set_fields_version(conn, 7)
    store.set_sync_meta(conn, _NOW, _NOW, 123, 456, _NOW)

    assert store.get_fields_version(conn) == 7, "an unrelated write must not clear the stamp"
    meta = store.get_sync_meta(conn)
    assert meta.issue_count == 123 and meta.transition_count == 456, "and must still write its own"


def _caught_up_store():
    """A store whose link-field window is full — so only the version can arm the backfill."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    row = ingest._map_issue(_issue(created=datetime(2026, 6, 1, 12, 0)), _NOW)
    store.upsert_issues(conn, [replace(row, key="DEVOPS-20", id=20)])
    conn.execute("UPDATE issues SET dev_has_pr = FALSE, dev_has_commits = FALSE")
    return conn


def _run(conn, monkeypatch, refill):
    """One incremental cycle; returns the JQL of every fetch_issues call it made."""
    calls: list[str] = []

    def _fetch(jira, jql):
        calls.append(jql)
        return [] if "updated >=" in jql else refill

    monkeypatch.setattr(ingest, "fetch_issues", _fetch)
    monkeypatch.setattr(ingest, "fetch_dev_panel_keys", lambda jira, jql, predicate: set())
    monkeypatch.setattr(ingest, "fetch_keys", lambda jira, jql, description: set())
    monkeypatch.setattr(ingest, "fetch_transitions", lambda jira, keys: [])
    plan = ingest.SyncPlan(watermark=datetime(2026, 9, 18, 0, 0), last_full_sync=_NOW)
    ingest.run_sync(conn, object(), plan, _NOW, _TZ)
    return calls


def test_a_store_behind_the_field_version_is_backfilled_even_though_nothing_looks_missing(monkeypatch):
    """The reason a NULL count is not enough.

    Every existing trigger is satisfied here — the link-field window is full — yet the size column
    is empty on a row the incremental slice will never return. Only the version knows.
    """
    conn = _caught_up_store()
    sized = ingest._map_issue(_issue(created=datetime(2026, 6, 1, 12, 0),
                                     customfield_10968=_size("Large")), _NOW)
    calls = _run(conn, monkeypatch, [replace(sized, key="DEVOPS-20", id=20)])

    assert len(calls) == 2, "the incremental slice plus one backfill pass"
    assert conn.execute(
        "SELECT estimated_size FROM issues WHERE key = 'DEVOPS-20'").fetchone()[0] == "Large"
    assert store.get_fields_version(conn) == ingest._FIELDS_VERSION, "and the store is stamped"


def test_the_version_backfill_runs_once_and_then_stops(monkeypatch):
    """Self-terminating, or the cheap incremental poll silently becomes a full crawl every cycle."""
    conn = _caught_up_store()
    sized = ingest._map_issue(_issue(created=datetime(2026, 6, 1, 12, 0),
                                     customfield_10968=_size("Small")), _NOW)
    refill = [replace(sized, key="DEVOPS-20", id=20)]

    assert len(_run(conn, monkeypatch, refill)) == 2, "first cycle backfills"
    assert len(_run(conn, monkeypatch, refill)) == 1, "second cycle is the slice alone"


def test_an_unsized_issue_does_not_re_arm_the_backfill(monkeypatch):
    """The failure mode that ruled out keying on `estimated_size IS NULL`.

    ~71% of DEVOPS issues have no size, so after a perfectly successful backfill those rows are
    still NULL. A NULL-count trigger would read that as "never asked" and re-crawl the window every
    cycle, forever. The stamp is written on the pass, not inferred from what the pass found.
    """
    conn = _caught_up_store()
    unsized = ingest._map_issue(_issue(created=datetime(2026, 6, 1, 12, 0)), _NOW)
    refill = [replace(unsized, key="DEVOPS-20", id=20)]

    assert len(_run(conn, monkeypatch, refill)) == 2
    assert conn.execute(
        "SELECT estimated_size FROM issues WHERE key = 'DEVOPS-20'").fetchone()[0] is None
    assert len(_run(conn, monkeypatch, refill)) == 1, \
        "a legitimately empty size must not look like a missing backfill"


def test_bumping_the_version_arms_the_backfill_again(monkeypatch):
    """What the next person adding a field gets for free: bump the constant, the store refills."""
    conn = _caught_up_store()
    row = ingest._map_issue(_issue(created=datetime(2026, 6, 1, 12, 0)), _NOW)
    refill = [replace(row, key="DEVOPS-20", id=20)]

    _run(conn, monkeypatch, refill)
    assert len(_run(conn, monkeypatch, refill)) == 1, "settled"

    monkeypatch.setattr(ingest, "_FIELDS_VERSION", ingest._FIELDS_VERSION + 1)
    assert len(_run(conn, monkeypatch, refill)) == 2, "a new column re-arms it"
