"""The MR-author roster is editable from the dashboard, so its edits must survive and be safe.

Adding an author is the one edit the store cannot serve on its own: the GitLab crawl is
incremental, so a newly tracked author's existing merged MRs are never fetched unless the
watermark is cleared. That coupling is what these encode.
"""
from datetime import datetime

import duckdb
import pytest

from darkstar import mr_authors, store


def test_first_read_seeds_an_empty_roster(tmp_path):
    path = str(tmp_path / "authors.json")
    assert mr_authors.read(path) == {"added": {}, "hidden": []}


def test_adding_an_author_requires_a_full_recrawl(tmp_path):
    """An incremental crawl only returns MRs updated since the watermark, so their history is lost."""
    path = str(tmp_path / "authors.json")
    roster, needs_recrawl = mr_authors.apply(path, "add", "audacy-new.person", "New Person")
    assert roster["added"] == {"audacy-new.person": "New Person"}
    assert needs_recrawl is True


def test_re_adding_the_same_author_does_not_recrawl_again(tmp_path):
    """A full crawl is expensive; only an actually-new username may trigger one."""
    path = str(tmp_path / "authors.json")
    mr_authors.apply(path, "add", "audacy-new.person", "New Person")
    _, needs_recrawl = mr_authors.apply(path, "add", "audacy-new.person", "New Person")
    assert needs_recrawl is False


def test_hide_and_show_never_recrawl(tmp_path):
    """Hiding is a presentation edit over data already in the store."""
    path = str(tmp_path / "authors.json")
    roster, recrawl = mr_authors.apply(path, "hide", "", "Adam")
    assert roster["hidden"] == ["Adam"] and recrawl is False
    roster, recrawl = mr_authors.apply(path, "show", "", "Adam")
    assert roster["hidden"] == [] and recrawl is False


def test_adding_an_author_unhides_them(tmp_path):
    """Otherwise adding someone previously hidden silently does nothing visible."""
    path = str(tmp_path / "authors.json")
    mr_authors.apply(path, "hide", "", "New Person")
    roster, _ = mr_authors.apply(path, "add", "audacy-new.person", "New Person")
    assert roster["hidden"] == []


def test_edits_persist_across_reads(tmp_path):
    path = str(tmp_path / "authors.json")
    mr_authors.apply(path, "add", "audacy-new.person", "New Person")
    mr_authors.apply(path, "hide", "", "Adam")
    assert mr_authors.read(path) == {"added": {"audacy-new.person": "New Person"}, "hidden": ["Adam"]}


def test_unknown_op_raises_rather_than_silently_doing_nothing(tmp_path):
    with pytest.raises(ValueError, match="unknown op"):
        mr_authors.apply(str(tmp_path / "authors.json"), "delete", "x", "X")


def test_clearing_the_watermark_forces_the_next_crawl_to_be_full(tmp_path):
    """The store-side half of the add path."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.set_gitlab_watermark(conn, datetime(2026, 8, 18, 12, 0))
    assert store.get_gitlab_watermark(conn) is not None
    store.clear_gitlab_watermark(conn)
    assert store.get_gitlab_watermark(conn) is None
