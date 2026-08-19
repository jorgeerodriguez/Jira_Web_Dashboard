"""Adding an author while a crawl is running must not lose that author's history.

The failure this guards against is silent and permanent. Add A, a full crawl starts. Add B while
it runs. The crawl finishes and stamps its watermark. The next crawl sees a watermark and goes
incremental, which only returns merge requests updated since — so B's existing merge requests are
never fetched, and B stays missing from the table forever with no error anywhere.

The fix is that a crawl records the roster version it READ AT THE START, not the current one. B's
add bumps the version past that, so the next crawl is full.
"""
from datetime import datetime, timedelta

import duckdb

from darkstar import gitlab_ingest, mr_authors, store

_NOW = datetime(2026, 8, 18, 12, 0, 0)
_WINDOW_DAYS = 183


def _conn():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    return conn


def _crawl(conn, monkeypatch, roster_path, during=None):
    """Run a sync with the network stubbed, returning the cutoff it decided on.

    `during` runs while the crawl is in flight - the concurrency this test exists for.
    """
    seen = {}

    def fake_sync_scopes(connection, cutoff, group_ids, project_ids, roster):
        seen["cutoff"] = cutoff
        seen["added"] = dict(roster.get("added") or {})
        if during is not None:
            during()
        return (0, 0)

    monkeypatch.setattr(gitlab_ingest, "_sync_scopes", fake_sync_scopes)
    monkeypatch.setattr(gitlab_ingest.config, "db_path", lambda: roster_path)
    monkeypatch.setattr(mr_authors, "authors_path", lambda _db: roster_path)
    gitlab_ingest.run_gitlab_sync(conn, _NOW, _WINDOW_DAYS)
    return seen


def _is_full(cutoff):
    """A full crawl reaches back the whole window; an incremental one starts near the watermark."""
    return cutoff <= _NOW - timedelta(days=_WINDOW_DAYS - 1)


def test_an_author_added_mid_crawl_is_picked_up_by_the_next_crawl(monkeypatch, tmp_path):
    path = str(tmp_path / "authors.json")
    conn = _conn()

    # A settled store: one crawl has run, so the next would normally be incremental.
    _crawl(conn, monkeypatch, path)
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False

    mr_authors.apply(path, "add", "audacy-a", "A")

    # The crawl for A runs full - and B is added while it is still in flight.
    first = _crawl(conn, monkeypatch, path,
                   during=lambda: mr_authors.apply(path, "add", "audacy-b", "B"))
    assert _is_full(first["cutoff"]) is True
    assert set(first["added"]) == {"audacy-a"}      # B arrived too late for this crawl

    # The next crawl must therefore be full as well, and must see B.
    second = _crawl(conn, monkeypatch, path)
    assert _is_full(second["cutoff"]) is True, "B's history would be lost"
    assert set(second["added"]) == {"audacy-a", "audacy-b"}

    # And once B has been crawled, it settles back to incremental.
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False


def test_a_crawl_records_the_version_it_read_not_the_current_one(monkeypatch, tmp_path):
    """Recording the current version at the end is the bug; it stamps a version never crawled."""
    path = str(tmp_path / "authors.json")
    conn = _conn()
    mr_authors.apply(path, "add", "audacy-a", "A")

    _crawl(conn, monkeypatch, path,
           during=lambda: mr_authors.apply(path, "add", "audacy-b", "B"))
    assert store.get_roster_version(conn) == 1        # the version at the start, not 2
    assert mr_authors.read(path)["version"] == 2


def test_a_settled_roster_does_not_force_repeated_full_crawls(monkeypatch, tmp_path):
    """The forcing must terminate, or every crawl re-reads six months forever."""
    path = str(tmp_path / "authors.json")
    conn = _conn()
    mr_authors.apply(path, "add", "audacy-a", "A")
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is True
    for _ in range(3):
        assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False


def test_removing_an_author_does_not_force_a_full_crawl(monkeypatch, tmp_path):
    """Only adds need history fetched; a removal is free."""
    path = str(tmp_path / "authors.json")
    conn = _conn()
    mr_authors.apply(path, "add", "audacy-a", "A")
    _crawl(conn, monkeypatch, path)
    mr_authors.apply(path, "remove", "audacy-a", "")
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False
