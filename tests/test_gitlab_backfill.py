"""A store carrying incomplete MR rows must re-crawl in full, exactly once.

Incremental crawls re-fetch only MRs *updated* since the watermark, so rows written before
opened_at existed would never be
repaired, leaving the MR-turnaround view permanently near-empty on a long-lived store.
"""
from datetime import datetime, timedelta

import duckdb

from darkstar import gitlab_ingest, store

_NOW = datetime(2026, 8, 18, 12, 0, 0)
_WINDOW_DAYS = 183


# The pre-SLA merge_requests shape: no opened_at, no labels. initialize_schema migrates it by
# adding those columns as NULLABLE, which is the only way an in-window row can lack opened_at --
# on a store created fresh the column is NOT NULL, so the backfill can never fire there.
_LEGACY_DDL: str = """
CREATE TABLE merge_requests (
    id                BIGINT PRIMARY KEY,
    project_path      VARCHAR NOT NULL,
    iid               BIGINT NOT NULL,
    author_account_id VARCHAR NOT NULL,
    title             VARCHAR NOT NULL,
    merged_at         TIMESTAMP NOT NULL,
    web_url           VARCHAR NOT NULL,
    fetched_at        TIMESTAMP NOT NULL
);
"""


def _conn():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    return conn


def _migrated_conn_with_legacy_row(merged_at):
    """A store as it exists in prod: legacy rows migrated, so opened_at is NULL on them."""
    conn = duckdb.connect(":memory:")
    conn.execute(_LEGACY_DDL)
    conn.execute(
        "INSERT INTO merge_requests VALUES (1, 'audacy-inc/devops/x', 1, 'a', 'MR 1', ?, 'u', ?)",
        [merged_at, _NOW],
    )
    store.initialize_schema(conn)   # adds opened_at/labels as nullable; row 1 keeps NULL
    assert conn.execute("SELECT opened_at FROM merge_requests").fetchone()[0] is None
    return conn


def _mr_row(id, opened, merged):
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id="a",
        title=f"MR {id}", opened_at=opened, merged_at=merged, labels=[],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="",
        source_branch="", pipelines_fetched_at=_NOW, author_name="A")


def test_in_window_row_missing_opened_at_forces_a_full_crawl():
    conn = _migrated_conn_with_legacy_row(_NOW - timedelta(days=9))
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is True


def test_complete_in_window_rows_stay_incremental():
    """The backfill must terminate — once repaired, crawls go back to cheap incremental pulls."""
    conn = _conn()
    store.upsert_merge_requests(conn, [_mr_row(1, _NOW - timedelta(days=10), _NOW - timedelta(days=9))])
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is False


def test_incomplete_row_older_than_the_window_is_ignored():
    """Ancient rows are never re-crawled, so they must not pin the sync to full crawls forever."""
    conn = _migrated_conn_with_legacy_row(_NOW - timedelta(days=399))
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is False


def test_empty_store_needs_no_backfill():
    """A first run already crawls the full window via the absent watermark, not via backfill."""
    assert gitlab_ingest._needs_backfill(_conn(), _NOW - timedelta(days=_WINDOW_DAYS)) is False


def test_rows_missing_a_source_branch_force_one_full_recrawl(tmp_path):
    """source_branch was added after the store existed, so old rows carry NULL.

    An incremental crawl only re-fetches MRs updated since the watermark, so without this the branch
    would stay NULL forever on every historical row — and branch names are what link a request whose
    title mangles its key. Silent, permanent, and invisible in any panel.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    conn.execute(
        "INSERT INTO merge_requests (id, project_path, iid, author_account_id, title, opened_at, "
        "merged_at, labels, web_url, merged_by, fetched_at, events_fetched_at, description) "
        "VALUES (1, 'p', 1, 'a', 'DEVOPS-1 x', '2026-08-01 16:00:00', '2026-08-02 17:00:00', "
        "[], 'u', '', '2026-08-03 00:00:00', '2026-08-03 00:00:00', '')")
    assert conn.execute(
        "SELECT count(*) FROM merge_requests WHERE source_branch IS NULL").fetchone()[0] == 1
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is True

    conn.execute("UPDATE merge_requests SET source_branch = 'DEVOPS-1-thing'")
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is True, \
        "still owed: pipelines_fetched_at is its own marker and remains NULL"
    conn.execute("UPDATE merge_requests SET pipelines_fetched_at = '2026-08-03 00:00:00'")
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is True, \
        "still owed: author_name is its own marker and remains NULL"
    conn.execute("UPDATE merge_requests SET author_name = 'A'")
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is False, \
        "and the forcing has to stop once every marker is filled, or every crawl is a full one"


def test_the_ingest_stores_the_branch_gitlab_reports(monkeypatch):
    """The branch has to survive the round trip, or the linking change is decoration.

    Nothing else in the suite reaches _sync_scopes, so without this the ingest could quietly write ""
    for every branch and every panel would still look right — the links would just be missing.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    author = "audacy-ben.bonora"
    api_mr = {
        "id": 501, "iid": 7, "title": "Devops 9426",
        "source_branch": "DEVOPS-9426-rotate-certs",
        "description": "", "labels": ["pe:iac-request"], "web_url": "u",
        "created_at": "2026-08-03T15:00:00.000Z", "merged_at": "2026-08-04T17:00:00.000Z",
        "author": {"username": author}, "merged_by": {"username": "someone-else"},
        "project_id": 99, "references": {"full": "audacy-inc/devops/x!7"},
        "target_project_id": 99,
    }
    monkeypatch.setattr(gitlab_ingest, "_token", lambda: "t")
    monkeypatch.setattr(gitlab_ingest, "_merged_mrs", lambda session, scope, iso: [api_mr])
    monkeypatch.setattr(gitlab_ingest, "_changed_paths", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_mr_notes", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_project_path", lambda mr: "audacy-inc/devops/x")

    gitlab_ingest._sync_scopes(conn, datetime(2026, 7, 1), (1,), (), {})

    stored = conn.execute("SELECT title, source_branch FROM merge_requests WHERE id = 501").fetchone()
    assert stored is not None, "the MR should have been ingested at all"
    assert stored[1] == "DEVOPS-9426-rotate-certs", "the branch GitLab reported must be stored"


def test_the_ingest_stores_pipeline_results(monkeypatch):
    """Without them no red time is ever excluded, and every panel still looks plausible.

    Nothing else in the suite reaches _sync_scopes, so the pipeline fetch could be dropped entirely
    and the only symptom would be turnaround figures quietly inflated by time nobody was waiting on.
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    api_mr = {
        "id": 601, "iid": 4, "title": "DEVOPS-1 thing", "source_branch": "DEVOPS-1",
        "description": "", "labels": [], "web_url": "u",
        "created_at": "2026-08-03T15:00:00.000Z", "merged_at": "2026-08-04T17:00:00.000Z",
        "author": {"username": "audacy-ben.bonora"}, "merged_by": {"username": "x"},
        "project_id": 99, "references": {"full": "audacy-inc/devops/x!4"},
    }
    pipelines = [
        {"status": "failed", "updated_at": "2026-08-03T17:00:00.000Z"},
        {"status": "success", "updated_at": "2026-08-04T16:00:00.000Z"},
    ]
    monkeypatch.setattr(gitlab_ingest, "_token", lambda: "t")
    monkeypatch.setattr(gitlab_ingest, "_merged_mrs", lambda session, scope, iso: [api_mr])
    monkeypatch.setattr(gitlab_ingest, "_changed_paths", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_mr_notes", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_project_path", lambda mr: "audacy-inc/devops/x")

    class _Response:
        def raise_for_status(self): pass
        def json(self): return pipelines

    monkeypatch.setattr(gitlab_ingest.requests.Session, "get",
                        lambda self, url, **kw: _Response())
    gitlab_ingest._sync_scopes(conn, datetime(2026, 7, 1), (1,), (), {})

    stored = conn.execute(
        "SELECT status FROM mr_pipelines WHERE mr_id = 601 ORDER BY seq").fetchall()
    assert [row[0] for row in stored] == ["failed", "success"], "both results must be stored"
    # And the marker must be stamped, or _needs_backfill forces a full crawl on every cycle forever.
    marker = conn.execute(
        "SELECT pipelines_fetched_at FROM merge_requests WHERE id = 601").fetchone()[0]
    assert marker is not None, "reading pipelines must record that they were read"
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is False, \
        "a crawled MR must not still look owed"


def test_rows_with_no_pipeline_history_force_one_full_recrawl():
    """The Red CI clock is dead data without this, and nothing would ever say so.

    !17 added mr_pipelines but not a marker, so `_needs_backfill` stayed False once the other five
    columns were filled and the crawl remained incremental forever. The 2,649 merge requests already
    stored would never have been read for pipelines, so red time would have been excluded for nothing
    and the Red CI column would have sat empty permanently.

    The marker is a column, not "has rows": an MR can legitimately have zero pipelines — two of a
    20-MR sample did — so absence of rows cannot mean "not yet crawled".
    """
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    conn.execute(
        "INSERT INTO merge_requests (id, project_path, iid, author_account_id, title, opened_at, "
        "merged_at, labels, web_url, merged_by, fetched_at, events_fetched_at, description, "
        "source_branch) VALUES (1, 'p', 1, 'a', 'DEVOPS-1 x', '2026-08-01 16:00:00', "
        "'2026-08-02 17:00:00', [], 'u', '', '2026-08-03 00:00:00', '2026-08-03 00:00:00', '', 'b')")
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is True

    # marked as read, with no pipeline rows at all — a legitimate outcome
    conn.execute("UPDATE merge_requests SET pipelines_fetched_at = '2026-08-03 00:00:00'")
    conn.execute("UPDATE merge_requests SET author_name = 'A'")   # the other marker on this row
    assert gitlab_ingest._needs_backfill(conn, datetime(2026, 7, 1)) is False, \
        "an MR with genuinely no pipelines must not re-trigger the crawl forever"


def test_rows_without_an_author_name_force_one_full_recrawl():
    """This marker is the only thing that makes the census release take effect.

    Dropping the ingest-time author filter changes nothing by itself: no roster edit fires when the
    new image rolls, so the pod would crawl incrementally and every author the old filter discarded
    would stay discarded — the feature would ship looking exactly like the bug it fixes. Every
    pre-existing row has a NULL author_name, so the first crawl after deploy is a full one.

    Same failure this file already guards for source_branch and pipelines: a new column with no
    backfill marker never fills, and nothing anywhere reports that it is empty.
    """
    conn = _conn()
    store.upsert_merge_requests(conn, [_mr_row(1, _NOW - timedelta(days=2), _NOW - timedelta(days=1))])
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is False

    conn.execute("UPDATE merge_requests SET author_name = NULL WHERE id = 1")
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is True, \
        "a row with no author_name predates the census crawl and cannot be repaired incrementally"

    # ...and it terminates: once the full crawl has named the author, no further crawl is forced.
    conn.execute("UPDATE merge_requests SET author_name = 'Marc Polidor' WHERE id = 1")
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is False


def test_an_out_of_window_row_without_an_author_name_is_left_alone():
    """The marker must not pin a long-lived store into re-crawling forever over ancient rows."""
    conn = _conn()
    store.upsert_merge_requests(conn, [_mr_row(1, _NOW - timedelta(days=400), _NOW - timedelta(days=399))])
    conn.execute("UPDATE merge_requests SET author_name = NULL WHERE id = 1")
    assert gitlab_ingest._needs_backfill(conn, _NOW - timedelta(days=_WINDOW_DAYS)) is False


def _api_mr(mr_id, username, name, **over):
    mr = {
        "id": mr_id, "iid": mr_id, "title": "Do a thing", "source_branch": "feat/x",
        "description": "", "labels": ["pe:iac-request"], "web_url": "u",
        "created_at": "2026-08-03T15:00:00.000Z", "merged_at": "2026-08-04T17:00:00.000Z",
        "author": {"username": username, "name": name}, "merged_by": {"username": "someone-else"},
        "project_id": 99, "references": {"full": "audacy-inc/devops/x!%d" % mr_id},
        "target_project_id": 99,
    }
    mr.update(over)
    return mr


def _run_sync(monkeypatch, conn, api_mrs, roster=None):
    """Drive the real _sync_scopes with only the HTTP layer stubbed.

    Stubbing _sync_scopes itself is what let four defects reach production: every test passed while
    the function they all depended on was never executed. The seam belongs at the network edge.
    """
    monkeypatch.setattr(gitlab_ingest, "_token", lambda: "t")
    monkeypatch.setattr(gitlab_ingest, "_merged_mrs",
                        lambda session, scope, iso: api_mrs if scope.endswith("115211004") else [])
    monkeypatch.setattr(gitlab_ingest, "_changed_paths", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_mr_notes", lambda session, pid, iid: [])
    monkeypatch.setattr(gitlab_ingest, "_mr_pipelines", lambda session, pid, iid, mr_id: [])
    return gitlab_ingest._sync_scopes(
        conn, datetime(2026, 7, 1), gitlab_ingest._PE_GROUP_IDS, gitlab_ingest._PE_PROJECT_IDS,
        roster or {})


def test_the_ingest_keeps_authors_nobody_has_listed(monkeypatch):
    """The census property, asserted where it is actually implemented.

    The ingest used to drop any author outside a curated list, so a contributor nobody had added was
    discarded at crawl time and could never appear on any panel at any lookback. That made the store
    a function of who somebody had remembered, which is not something a dashboard can be honest about.
    """
    conn = _conn()
    _run_sync(monkeypatch, conn, [
        _api_mr(1, "audacy-adam.shero", "Adam Shero"),      # roster member
        _api_mr(2, "audacy-marc.polidor", "Marc Polidor"),  # on no list anywhere
        _api_mr(3, "brand-new-person", "Brand New"),        # never seen before
    ])
    stored = dict(conn.execute(
        "SELECT author_account_id, author_name FROM merge_requests ORDER BY id").fetchall())
    assert len(stored) == 3, "an unlisted author must not be discarded at crawl time"
    # Roster members keep their Jira accountId so their history stays one series...
    assert "600ece193b1af000697f339d" in stored
    # ...everyone else is keyed by GitLab username, which no roster-gated view can match.
    assert stored["audacy-marc.polidor"] == "Marc Polidor"
    assert stored["brand-new-person"] == "Brand New"


def test_a_roster_member_is_never_split_across_two_keys(monkeypatch):
    """The roster accountId must win over the username, or one person becomes two rows."""
    conn = _conn()
    _run_sync(monkeypatch, conn, [_api_mr(1, "audacy-adam.shero", "Adam Shero")],
              roster={"added": {"audacy-adam.shero": "Adam Shero"}})
    keys = [r[0] for r in conn.execute("SELECT author_account_id FROM merge_requests").fetchall()]
    assert keys == ["600ece193b1af000697f339d"], "the accountId must win over the username"


def test_an_mr_with_no_author_is_skipped_rather_than_stored_blank(monkeypatch):
    """GitLab can report a null author on a deleted account; a blank key would collide with itself."""
    conn = _conn()
    _run_sync(monkeypatch, conn, [_api_mr(1, "", "", author={}),
                                  _api_mr(2, "audacy-marc.polidor", "Marc Polidor")])
    keys = [r[0] for r in conn.execute("SELECT author_account_id FROM merge_requests").fetchall()]
    assert keys == ["audacy-marc.polidor"]


def test_the_author_display_name_falls_back_to_the_username(monkeypatch):
    """Some accounts have no display name set in GitLab; a chart still needs a label."""
    conn = _conn()
    _run_sync(monkeypatch, conn, [_api_mr(1, "audacy-zack.amadi", "")])
    assert conn.execute("SELECT author_name FROM merge_requests").fetchone()[0] == "audacy-zack.amadi"
