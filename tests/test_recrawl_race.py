"""Adding an author must be cheap, and must not depend on a crawl to take effect.

This file used to guard a race: a full crawl was started on every add, and an author added while one
was in flight could be stamped as "crawled" without ever being fetched — losing their history
permanently and silently. The fix was for a crawl to record the roster version it read at its START.

That race no longer exists, because the thing it protected no longer happens. The ingest keeps every
author it finds rather than a curated list, so an added author's merge requests are already in the
store before anyone names them. An add is a labelling change; it fetches nothing, so there is nothing
for a concurrent crawl to miss.

What is worth guarding now is the opposite property: that adding an author does NOT quietly cost a
six-month re-read of the GitLab API. That regression would be invisible from the page — the data
would look right, and only the API bill and the crawl log would show it.
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


def _crawl(conn, monkeypatch, roster_path):
    """Run a sync with the network stubbed, returning the cutoff it decided on."""
    seen = {}

    def fake_sync_scopes(connection, cutoff, group_ids, project_ids, roster):
        seen["cutoff"] = cutoff
        return (0, 0)

    monkeypatch.setattr(gitlab_ingest, "_sync_scopes", fake_sync_scopes)
    monkeypatch.setattr(gitlab_ingest.config, "db_path", lambda: roster_path)
    monkeypatch.setattr(mr_authors, "authors_path", lambda _db: roster_path)
    gitlab_ingest.run_gitlab_sync(conn, _NOW, _WINDOW_DAYS)
    return seen


def _is_full(cutoff):
    """A full crawl reaches back the whole window; an incremental one starts near the watermark."""
    return cutoff <= _NOW - timedelta(days=_WINDOW_DAYS - 1)


def _settle(conn, monkeypatch, path):
    """Get the store past its first-run full crawl and any owed backfill."""
    _crawl(conn, monkeypatch, path)
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False, "store did not settle"


def test_adding_an_author_does_not_force_a_full_crawl(monkeypatch, tmp_path):
    """The cost this file now exists to prevent.

    A roster edit cannot leave the store incomplete, because the crawl was never filtering on the
    roster in the first place — so forcing a six-month re-read on every add buys nothing at all.
    """
    path = str(tmp_path / "authors.json")
    conn = _conn()
    _settle(conn, monkeypatch, path)

    mr_authors.apply(path, "add", "audacy-marc.polidor", "Marc Polidor")
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False


def test_removing_or_hiding_an_author_does_not_force_a_full_crawl(monkeypatch, tmp_path):
    path = str(tmp_path / "authors.json")
    conn = _conn()
    mr_authors.apply(path, "add", "audacy-marc.polidor", "Marc Polidor")
    _settle(conn, monkeypatch, path)

    mr_authors.apply(path, "remove", "audacy-marc.polidor", "")
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False
    mr_authors.apply(path, "hide", "", "Marc Polidor")
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is False


def test_an_incomplete_store_still_forces_a_full_crawl(monkeypatch, tmp_path):
    """Dropping the roster trigger must not disarm the backfill trigger beside it.

    Rows missing a marker cannot be repaired incrementally, and that is now the ONLY thing that
    forces a full crawl — so if this stopped working, nothing would.
    """
    path = str(tmp_path / "authors.json")
    conn = _conn()
    _settle(conn, monkeypatch, path)

    store.upsert_merge_requests(conn, [store.MergeRequestRow(
        id=1, project_path="p", iid=1, author_account_id="a", title="t",
        opened_at=_NOW - timedelta(days=3), merged_at=_NOW - timedelta(days=2), labels=[],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="",
        source_branch="b", pipelines_fetched_at=_NOW, author_name=None)])
    assert _is_full(_crawl(conn, monkeypatch, path)["cutoff"]) is True
