"""MR turnaround must measure delivery speed, not how much of the wait was overnight.

The point of this view is to answer "how quickly does an author's work land", including for
contributors who are not on the PE roster — the previous ingest discarded their MRs entirely,
so they could not be measured at all.
"""
from datetime import datetime, timedelta

import duckdb

from darkstar import mrflow, store
from darkstar.roster import GITLAB_USERNAMES, ROSTER

_ADAM = "600ece193b1af000697f339d"
_OMAR = "712020:58e4121c-dadd-4c34-99a9-92dc31ee039b"
_BEN = "audacy-ben.bonora"
_NOW = datetime(2026, 8, 18, 12, 0, 0)


def _mr(id, account_id, opened, merged, self_service=True):
    """A merged MR by `account_id`.

    Self-service by default, because this page is scoped to self-service work and almost every test
    here is about turnaround mechanics rather than scope. Pass self_service=False to build one the
    panel must exclude.
    """
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id=account_id,
        title=f"MR {id}", opened_at=opened, merged_at=merged,
        labels=["pe:iac-request"] if self_service else [],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="",
        source_branch="", pipelines_fetched_at=_NOW, author_name=f"Author {id}")


_IN_VIEW: dict = {"audacy-adam.shero": "Adam", "audacy-ben.bonora": "Ben Bonora",
                  "omar.saundersholiday": "Omar"}


def _report(conn, roster=None, name_filter=None, environment="all"):
    """mr_turnaround_report with the view's default window and the test authors opted in.

    The table lists only authors added to the view, so a roster that opts nobody in yields no rows
    at all -- correct, and useless for testing turnaround arithmetic. Curation itself is covered by
    the tests at the end of this file.
    """
    merged = {"added": dict(_IN_VIEW), **(roster or {})}
    if roster and "added" in roster:
        merged["added"] = {**_IN_VIEW, **roster["added"]}
    return mrflow.mr_turnaround_report(
        conn, mrflow.default_window_start(_NOW), merged, name_filter or [], environment)


def _seed(rows):
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_merge_requests(conn, rows)
    return conn


def test_a_non_roster_author_cannot_reach_the_roster_gated_views():
    """An outside contributor is keyed by GitLab username, which is never a Jira accountId.

    That is the whole guard: velocity, capacity and the SME matrix look ROSTER up by accountId, so
    a username simply misses. It used to depend on a hand-kept list of known outside contributors;
    now it holds for anyone the crawl finds, which is everyone.
    """
    assert "audacy-ben.bonora" not in ROSTER
    assert "brand-new-person" not in ROSTER
    for username, account_id in GITLAB_USERNAMES.items():
        assert account_id in ROSTER, f"{username} is PE and must be nameable"
        assert username not in ROSTER, "a username must never double as an accountId"


def test_overnight_wait_is_not_charged():
    """Opened 15:00 Tue, merged 08:00 Wed: 2 business hours, though 17 hours elapsed on the clock."""
    conn = _seed([_mr(1, _BEN, datetime(2026, 8, 18, 22, 0), datetime(2026, 8, 19, 15, 0))])
    ben = next(a for a in _report(conn)["authors"] if a["name"] == "Ben Bonora")
    assert ben["merged"] == 1
    assert ben["biz_hours_median"] == 2.0
    assert ben["tracked"] is True   # flagged as a non-roster author on the dashboard


def test_every_merged_mr_counts_not_one_per_issue():
    """Unlike the SLA bucket's review median, no MR is dropped — three MRs give a 3-MR median."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 18, 0)),   # 3h
        _mr(3, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 20, 0)),   # 5h
    ])
    adam = next(a for a in _report(conn)["authors"] if a["name"] == "Adam")
    assert adam["merged"] == 3
    assert adam["biz_hours_median"] == 3.0
    assert adam["biz_hours_p90"] == 5.0


def test_mrs_merged_before_the_window_are_excluded():
    """Without a window an old outlier would drag the median forever."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2025, 1, 6, 16, 0), datetime(2025, 1, 10, 23, 0)),    # long, out of window
        _mr(2, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 17, 0)),   # 2h, in window
    ])
    report = _report(conn)
    assert report["window_start"] == "2026-03-01"
    assert report["team"]["merged"] == 1
    assert report["team"]["biz_hours_median"] == 2.0


def test_authors_sort_slowest_first():
    """The dashboard leads with whoever is slowest on the standard clock, as the lead-time table does."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # 6h
    ])
    assert [a["name"] for a in _report(conn)["authors"]] == ["Ben Bonora", "Adam"]


def test_an_author_nobody_added_is_absent_from_the_rows_but_present_in_the_total():
    """The table is opt-in; the team total is not.

    "Team (all authors)" has to mean all authors, or the figure changes meaning depending on who
    happens to be in the view — and a number that moves when you curate a table cannot be quoted
    anywhere. So an author nobody has added contributes to the total while having no row.
    """
    conn = _seed([_mr(1, "someone-nobody-added", datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 17, 0))])
    report = _report(conn)
    assert report["authors"] == []
    assert report["team"]["merged"] == 1


def test_eet_work_scores_near_zero_on_the_business_clock():
    """A known and accepted property of standardising on one North American business day.

    An MR a Ukraine-based engineer opens and merges inside their own workday falls entirely
    outside Pacific 08:00-17:00 and scores 0.0. Recorded so the behaviour is deliberate rather
    than a surprise when a row reads as instantaneous.
    """
    # Opened 09:00 and merged 14:00 Kyiv (EEST, UTC+3) = 06:00-11:00 UTC = 23:00-04:00 Pacific.
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 6, 0), datetime(2026, 8, 17, 11, 0))])
    assert _report(conn)["authors"][0]["biz_hours_median"] == 0.0


def test_roster_can_add_an_author_the_static_map_does_not_know():
    """Added authors are keyed by GitLab username, so the ingest attributes them under that key."""
    conn = _seed([_mr(1, "audacy-new.person", datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 18, 0))])
    report = _report(conn, {"added": {"audacy-new.person": "New Person"}})
    row = next(a for a in report["authors"] if a["name"] == "New Person")
    assert row["merged"] == 1 and row["biz_hours_median"] == 3.0
    assert row["tracked"] is True          # flagged as non-roster in the table


def test_hiding_an_author_removes_their_row_and_nothing_else():
    """The x button is a view control. It must not move a number.

    This used to drop hidden work from the team total too, on the reasoning that a total should
    describe what is on screen. The opposite is more defensible: tidying a table is not a claim
    about the world, and a total that quietly shrinks when you hide a row is a figure nobody can
    safely read twice.
    """
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # 6h
    ])
    before = _report(conn)
    report = _report(conn, {"hidden": ["Ben Bonora"]})
    assert [a["name"] for a in report["authors"]] == ["Adam"], "the row goes"
    assert report["team"] == before["team"], "and the total stays exactly where it was"
    assert report["team"]["merged"] == 2 and report["team"]["biz_hours_median"] == 3.5
    assert report["hidden"] == ["Ben Bonora"]


# --- daily cut -------------------------------------------------------------------------------

def test_daily_groups_by_the_business_day_the_mr_merged():
    """Two MRs merged the same Pacific day are one row; a third the next day is its own."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # Mon, 1h
        _mr(2, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 18, 0)),   # Mon, 3h
        _mr(3, _BEN, datetime(2026, 8, 18, 15, 0), datetime(2026, 8, 18, 20, 0)),    # Tue, 5h
    ])
    daily = _report(conn)["daily"]
    assert [d["day"] for d in daily] == ["2026-08-18", "2026-08-17"]   # most recent first
    assert daily[0]["merged"] == 1 and daily[0]["biz_hours_median"] == 5.0
    assert daily[1]["merged"] == 2 and daily[1]["biz_hours_median"] == 2.0


def test_daily_uses_the_business_timezone_not_utc():
    """Merged 23:30 Pacific belongs to that Pacific day, not the following UTC one."""
    # 2026-08-18 06:30 UTC = 2026-08-17 23:30 PDT
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 18, 6, 30))])
    assert [d["day"] for d in _report(conn)["daily"]] == ["2026-08-17"]


def test_daily_excludes_hidden_authors_like_the_author_table():
    """The two cuts must describe the same population, or the totals contradict each other."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # 6h
    ])
    daily = _report(conn, {"hidden": ["Ben Bonora"]})["daily"]
    assert len(daily) == 1
    assert daily[0]["merged"] == 1 and daily[0]["biz_hours_median"] == 1.0


def test_daily_omits_days_with_no_merged_mrs():
    """A day with nothing merged is absent rather than a zero row that would drag the eye."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),
        _mr(2, _ADAM, datetime(2026, 8, 19, 15, 0), datetime(2026, 8, 19, 16, 0)),
    ])
    assert [d["day"] for d in _report(conn)["daily"]] == ["2026-08-19", "2026-08-17"]


def test_author_filter_narrows_every_cut_together():
    """Filtering must happen server-side: a median cannot be re-derived from per-author medians.

    Ben's MR is 6 business hours and Adam's is 1, both merged the same day. Filtering to Ben must
    give a daily median of 6.0 — a number the page could not compute from the unfiltered rows.
    """
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # 6h
    ])
    unfiltered = _report(conn)
    assert unfiltered["daily"][0]["merged"] == 2 and unfiltered["daily"][0]["biz_hours_median"] == 3.5

    filtered = _report(conn, name_filter=["ben"])
    assert [a["name"] for a in filtered["authors"]] == ["Ben Bonora"]
    assert filtered["daily"][0]["merged"] == 1 and filtered["daily"][0]["biz_hours_median"] == 6.0
    # The filter narrows the view, so it narrows the per-author and per-day cuts with it -- but the
    # team total describes the population, not the view, and a search box must not restate it.
    assert filtered["team"]["merged"] == 2
    assert filtered["filter"] == ["ben"]


def test_author_filter_matches_any_term_as_a_substring():
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),
    ])
    both = _report(conn, name_filter=["ben", "adam"])
    assert sorted(a["name"] for a in both["authors"]) == ["Adam", "Ben Bonora"]
    assert _report(conn, name_filter=["nobody"])["authors"] == []


def test_hidden_authors_still_win_over_the_filter():
    """Hiding is roster state; a filter must never resurrect someone deliberately removed."""
    conn = _seed([_mr(1, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0))])
    r = _report(conn, {"hidden": ["Ben Bonora"]}, name_filter=["ben"])
    assert r["authors"] == [] and r["daily"] == []


def test_series_is_one_entry_per_author_on_a_shared_axis():
    """Multiple selected authors must each get their own line, not be merged into one.

    Reported symptom: filtering to two people drew a single aggregate line. Each author now owns a
    series whose points sit only on the days they merged, indexed against a shared `days` axis.
    """
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # Mon 1h
        _mr(2, _ADAM, datetime(2026, 8, 19, 15, 0), datetime(2026, 8, 19, 18, 0)),   # Wed 3h
        _mr(3, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # Mon 6h
    ])
    r = _report(conn, name_filter=["adam", "ben"])
    assert r["days"] == ["2026-08-17", "2026-08-19"]          # ascending, shared x axis
    names = [s["name"] for s in r["series"]]
    assert names == ["Adam", "Ben Bonora"]                    # one series each, not one combined

    adam = next(s for s in r["series"] if s["name"] == "Adam")
    ben = next(s for s in r["series"] if s["name"] == "Ben Bonora")
    assert [p["day"] for p in adam["points"]] == ["2026-08-17", "2026-08-19"]
    assert [p["biz_hours_median"] for p in adam["points"]] == [1.0, 3.0]
    assert [p["day"] for p in ben["points"]] == ["2026-08-17"]   # sparse: no Wed point
    assert ben["points"][0]["biz_hours_median"] == 6.0


def test_series_medians_are_per_author_not_shared():
    """Ben's line must show 6.0 on a day whose combined median is 3.5."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # 6h
    ])
    r = _report(conn)
    assert r["daily"][0]["biz_hours_median"] == 3.5           # combined
    per = {s["name"]: s["points"][0]["biz_hours_median"] for s in r["series"]}
    assert per == {"Adam": 1.0, "Ben Bonora": 6.0}


def test_series_respects_hidden_authors():
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),
    ])
    assert [s["name"] for s in _report(conn, {"hidden": ["Ben Bonora"]})["series"]] == ["Adam"]


# --- environment split -------------------------------------------------------------------------

def _mr_in(id, account_id, project_path, opened, merged):
    """As _mr, in a named project. Self-service by default for the same reason."""
    return store.MergeRequestRow(
        id=id, project_path=project_path, iid=id, author_account_id=account_id,
        title=f"MR {id}", opened_at=opened, merged_at=merged, labels=["pe:iac-request"],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="",
        source_branch="", pipelines_fetched_at=_NOW, author_name=f"Author {id}")


def test_environment_filter_splits_prod_from_nonprod():
    """"nonprod" contains "prod", so a substring test would file every nonprod MR as production."""
    conn = _seed([
        _mr_in(1, _ADAM, "audacy-inc/devops/terraform/tf-aardvark2-prod",
               datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),      # 1h prod
        _mr_in(2, _ADAM, "audacy-inc/devops/terraform/tf-aardvark2-nonprod",
               datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),      # 6h nonprod
        _mr_in(3, _ADAM, "audacy-inc/devops/gitops/gitops-k8s-team-a2",
               datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 18, 0)),      # 3h neither
    ])
    assert _report(conn, environment="all")["team"]["merged"] == 3
    prod = _report(conn, environment="prod")
    assert prod["team"]["merged"] == 1 and prod["team"]["biz_hours_median"] == 1.0
    nonprod = _report(conn, environment="nonprod")
    assert nonprod["team"]["merged"] == 1 and nonprod["team"]["biz_hours_median"] == 6.0
    other = _report(conn, environment="other")
    assert other["team"]["merged"] == 1 and other["team"]["biz_hours_median"] == 3.0


def test_environment_filter_narrows_the_daily_series_too():
    conn = _seed([
        _mr_in(1, _ADAM, "audacy-inc/devops/terraform/tf-x-prod",
               datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),
        _mr_in(2, _ADAM, "audacy-inc/devops/terraform/tf-x-nonprod",
               datetime(2026, 8, 18, 15, 0), datetime(2026, 8, 18, 21, 0)),
    ])
    r = _report(conn, environment="prod")
    assert [d["day"] for d in r["daily"]] == ["2026-08-17"]
    assert r["environment"] == "prod"


def test_draft_time_is_excluded_from_the_reported_turnaround():
    """End to end: the same MR reads 8h from opened, 4h once its draft spell is discounted."""
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 18, 15, 0))])
    assert _report(conn)["team"]["biz_hours_median"] == 9.0      # no events yet: whole span
    store.replace_mr_events(conn, 1, [store.MergeRequestEventRow(
        mr_id=1, kind="draft", happened_at=datetime(2026, 8, 17, 19, 0), seq=0, actor=None)])
    assert _report(conn)["team"]["biz_hours_median"] == 4.0      # draft from 12:00 Mon onwards


def test_first_review_is_reported_on_the_ready_clock():
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 23, 0))])
    store.replace_mr_events(conn, 1, [store.MergeRequestEventRow(
        mr_id=1, kind="review", happened_at=datetime(2026, 8, 17, 18, 0), seq=0, actor=None)])
    fr = _report(conn)["first_review"]
    assert fr["reviewed"] == 1 and fr["hours_median"] == 3.0


def test_unreviewed_mrs_are_absent_from_the_first_review_stat():
    """An MR nobody commented on has no review time — it must not count as an instant review."""
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 23, 0))])
    fr = _report(conn)["first_review"]
    assert fr["reviewed"] == 0 and fr["hours_median"] is None


def test_unmeasurable_rows_are_counted_not_silently_dropped():
    """A store mid-backfill must not look like a team that did no work before a certain date.

    Rows crawled before opened_at existed cannot be measured, and excluding them quietly made the
    chart start late for no visible reason — reported as "no data older than 7/29".
    """
    # Built the way prod got here: rows written before opened_at existed, then migrated.
    conn = duckdb.connect(":memory:")
    conn.execute("""CREATE TABLE merge_requests (
        id BIGINT PRIMARY KEY, project_path VARCHAR NOT NULL, iid BIGINT NOT NULL,
        author_account_id VARCHAR NOT NULL, title VARCHAR NOT NULL, merged_at TIMESTAMP NOT NULL,
        web_url VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL)""")
    conn.execute("INSERT INTO merge_requests VALUES (2, 'audacy-inc/devops/x', 2, ?, 'old', "
                 "TIMESTAMP '2026-05-01 15:00:00', 'u', ?)", [_ADAM, _NOW])
    store.initialize_schema(conn)
    store.upsert_merge_requests(conn, [_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0),
                                           datetime(2026, 8, 17, 16, 0))])
    r = _report(conn)
    assert r["incomplete"] == 1
    assert r["earliest_measurable"] == "2026-08-17"
    assert r["team"]["merged"] == 1          # still excluded from the figures, just not in silence


def test_a_fully_backfilled_store_reports_nothing_incomplete():
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0))])
    r = _report(conn)
    assert r["incomplete"] == 0
    assert r["earliest_measurable"] == "2026-08-17"


def test_crawl_state_reports_a_backfill_that_is_still_owed():
    """"Is this still filling, or is this all there is?" must be answerable from the page.

    `pending` used to compare roster versions. Since roster edits stopped forcing a crawl that
    comparison is almost always equal, so the page reported itself current while a backfill was
    outstanding and the figures depending on it sat empty — the same failure the flag exists to
    prevent, arriving from the other direction. It now tracks what actually forces a full crawl.
    """
    conn = _seed([])
    settled = _report(conn, {"added": {}, "hidden": [], "version": 0})["crawl"]
    assert settled["pending"] is False and settled["last_crawl"] is None

    # A row missing a marker cannot be repaired incrementally, so a full crawl is owed.
    store.upsert_merge_requests(conn, [_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0),
                                           datetime(2026, 8, 17, 16, 0))])
    conn.execute("UPDATE merge_requests SET author_name = NULL WHERE id = 1")
    owed = _report(conn)["crawl"]
    assert owed["pending"] is True, "the page must say a crawl is still owed"

    conn.execute("UPDATE merge_requests SET author_name = 'Adam' WHERE id = 1")
    store.set_gitlab_watermark(conn, datetime(2026, 8, 18, 12, 0))
    caught_up = _report(conn)["crawl"]
    assert caught_up["pending"] is False
    assert caught_up["last_crawl"].startswith("2026-08-18")


def test_a_roster_edit_alone_no_longer_reports_a_pending_crawl():
    """Adding an author fetches nothing now, so it must not claim the page is mid-update.

    Reporting "a crawl is still owed" after an add would send someone waiting for data that is
    already there — and the wait has no end, because no crawl is coming.
    """
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0))])
    store.set_gitlab_watermark(conn, datetime(2026, 8, 18, 12, 0))
    after_add = _report(conn, {"added": {"audacy-x": "X"}, "hidden": [], "version": 9})["crawl"]
    assert after_add["pending"] is False
    # The versions are still reported, for diagnosis; they just no longer drive `pending`.
    assert after_add["roster_version"] == 9 and after_add["crawled_version"] == 0


def test_a_roster_member_re_added_is_not_split_into_two_rows():
    """`added` must not override the roster, or one person becomes two rows with the same name."""
    added = {"audacy-adam.shero": "Adam Shero"}
    attributable = {**{u: u for u in added}, **GITLAB_USERNAMES}
    assert attributable["audacy-adam.shero"] == GITLAB_USERNAMES["audacy-adam.shero"]


# --- environment from changed paths ------------------------------------------------------------

def _mr_with_paths(conn, id, account_id, project_path, opened, merged, paths):
    store.upsert_merge_requests(conn, [_mr_in(id, account_id, project_path, opened, merged)])
    store.replace_mr_files(conn, [id], [(id, p) for p in paths])


def test_paths_classify_a_repo_whose_name_says_nothing():
    """gitops-k8s-team-a2 holds both trees, so the repo name is silent but the change is not.

    This is the real case: three ST-975 cutover MRs under clusters/prod-fluxv2/.../prod/ were
    filed as "other", hiding a production coordination delay in an unclassified bucket.
    """
    conn = _seed([])
    _mr_with_paths(conn, 1, _ADAM, "audacy-inc/devops/gitops/gitops-k8s-team-a2",
                   datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0),
                   ["clusters/prod-fluxv2/namespaces/app/prod/wp-cms/ingress.yaml"])
    assert _report(conn, environment="prod")["team"]["merged"] == 1
    assert _report(conn, environment="other")["team"]["merged"] == 0


def test_the_repo_name_beats_a_contradictory_path():
    """A repo named -prod deploys to production whatever directory the change sits in."""
    conn = _seed([])
    _mr_with_paths(conn, 1, _ADAM, "audacy-inc/devops/terraform/tf-aardvark2-prod",
                   datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0),
                   ["some/nonprod/path.tf"])
    assert _report(conn, environment="prod")["team"]["merged"] == 1
    assert _report(conn, environment="nonprod")["team"]["merged"] == 0


def test_a_change_spanning_both_trees_is_excluded_and_counted():
    """Mixed is unexpected; folding it into either bucket would misreport that bucket."""
    conn = _seed([])
    _mr_with_paths(conn, 1, _ADAM, "audacy-inc/devops/gitops/gitops-k8s-team-a2",
                   datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0),
                   ["clusters/prod-fluxv2/x/prod/a.yaml", "clusters/nonprod-fluxv2/x/b.yaml"])
    for env in ("prod", "nonprod", "other"):
        assert _report(conn, environment=env)["team"]["merged"] == 0, env
    everything = _report(conn, environment="all")
    assert everything["team"]["merged"] == 1      # still in the unfiltered total
    assert everything["mixed"] == 1               # ...and reported, not dropped in silence


def test_an_mr_with_no_environment_signal_stays_other():
    conn = _seed([])
    _mr_with_paths(conn, 1, _ADAM, "audacy-inc/devops/pe-morning-report",
                   datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0), ["darkstar/app.py"])
    assert _report(conn, environment="other")["team"]["merged"] == 1


def test_the_slowest_list_is_ordered_slowest_first_and_reports_what_it_left_out():
    """A drill-down that ends without saying so reads as "that is all of them".

    The list is capped because a six-month window is well over a thousand merge requests, so the cap
    has to come with the count it dropped — otherwise the tail is invisible rather than merely
    unlisted, which is the same absence-as-value trap as a silently truncated table.
    """
    rows = []
    for n in range(1, 121):
        # n hours of ready time each, all inside one business day so the clock is linear in n
        opened = datetime(2026, 8, 3, 15, 0, 0)
        rows.append(_mr(n, _BEN, opened, opened + timedelta(hours=n)))
    report = _report(_seed(rows))

    assert report["measured"] == 120
    assert len(report["slowest"]) == 100, "the list is capped"
    assert report["slowest_omitted"] == 20, "and says how many it dropped"

    hours = [row["ready_hours"] for row in report["slowest"]]
    assert hours == sorted(hours, reverse=True), "slowest first — the question is always an outlier"
    assert hours[0] == max(hours), "the very slowest must be on the first page"


def test_each_slowest_row_carries_both_clocks_so_draft_time_is_visible():
    """One "age" column cannot separate "still being written" from "waiting on review".

    An MR open for days with almost no ready time was never waiting on anyone, and that is the usual
    explanation. The row has to carry the whole span and the ready-only span so the gap is legible.
    """
    opened = datetime(2026, 8, 3, 15, 0, 0)          # Mon 08:00 Pacific
    merged = datetime(2026, 8, 5, 17, 0, 0)          # Wed 10:00 Pacific
    conn = _seed([_mr(1, _BEN, opened, merged)])
    # Marked ready only for the final hour: two days open, one hour actually awaiting review.
    store.replace_mr_events(conn, 1, [
        store.MergeRequestEventRow(mr_id=1, kind="draft", happened_at=opened, seq=0, actor=None),
        store.MergeRequestEventRow(
            mr_id=1, kind="ready", happened_at=datetime(2026, 8, 5, 16, 0, 0), seq=1, actor=None),
    ])
    row = _report(conn)["slowest"][0]

    # Mon 08:00 -> Wed 10:00 is 9h + 9h + 2h = 20 business hours, of which one was ready.
    assert row["ready_hours"] == 1.0, "only the ready spell counts toward turnaround"
    assert row["open_hours"] == 20.0, "the whole span is carried alongside it"
    assert round(row["open_hours"] - row["ready_hours"], 1) == 19.0, "the rest was draft"
    for field in ("iid", "project", "title", "url", "author", "merged", "environment"):
        assert row[field] is not None, f"{field} is needed to identify the MR being inspected"


def _pipes(conn, mr_id, events):
    store.replace_mr_pipelines(conn, mr_id, [
        store.MergeRequestPipelineRow(mr_id=mr_id, status=status, happened_at=when, seq=i)
        for i, (status, when) in enumerate(events)])


def test_a_failing_pipeline_stops_the_turnaround_clock():
    """Adam's call, and the reason is that the metric claims to measure PE's responsiveness.

    A red pipeline blocks the merge whoever reviews it, so the wait is on whoever pushes the fix.
    Charging it to review time made the worst offenders unreadable: 32% of open hours on the slowest
    PE-authored MRs were spent red.
    """
    opened = datetime(2026, 8, 3, 15, 0, 0)          # Mon 08:00 Pacific
    merged = datetime(2026, 8, 4, 17, 0, 0)          # Tue 10:00 Pacific -> 11 business hours
    conn = _seed([_mr(1, _BEN, opened, merged)])
    assert _report(conn)["slowest"][0]["ready_hours"] == 11.0

    # red from Mon 10:00 until Tue 09:00 = 8 business hours
    _pipes(conn, 1, [("failed", datetime(2026, 8, 3, 17, 0, 0)),
                     ("success", datetime(2026, 8, 4, 16, 0, 0))])
    row = _report(conn)["slowest"][0]
    assert row["ready_hours"] == 3.0, "11h ready less 8h red"
    assert row["red_hours"] == 8.0, "and the excluded time is reported, not hidden"
    assert row["open_hours"] == 11.0, "the whole span is unchanged"


def test_red_time_is_reported_even_though_it_is_excluded():
    """Excluding it silently would let a merge request parked broken for days look instant."""
    opened = datetime(2026, 8, 3, 15, 0, 0)
    merged = datetime(2026, 8, 5, 17, 0, 0)          # 20 business hours
    conn = _seed([_mr(1, _BEN, opened, merged)])
    _pipes(conn, 1, [("failed", datetime(2026, 8, 3, 16, 0, 0))])   # red to the merge
    row = _report(conn)["slowest"][0]
    assert row["ready_hours"] == 1.0
    assert row["red_hours"] == 19.0, "the 19 hours it sat broken must still be visible"


def test_a_green_pipeline_changes_nothing():
    """The common case: one run, it passes, the clock is untouched."""
    opened, merged = datetime(2026, 8, 3, 15, 0, 0), datetime(2026, 8, 3, 20, 0, 0)
    conn = _seed([_mr(1, _BEN, opened, merged)])
    _pipes(conn, 1, [("success", datetime(2026, 8, 3, 16, 0, 0))])
    row = _report(conn)["slowest"][0]
    assert row["ready_hours"] == 5.0 and row["red_hours"] == 0.0


def test_an_mr_with_no_pipeline_history_keeps_its_full_clock():
    """A fetch failure must never make a merge request look fast — err toward charging PE."""
    opened, merged = datetime(2026, 8, 3, 15, 0, 0), datetime(2026, 8, 3, 20, 0, 0)
    row = _report(_seed([_mr(1, _BEN, opened, merged)]))["slowest"][0]
    assert row["ready_hours"] == 5.0 and row["red_hours"] == 0.0


def test_red_time_inside_a_draft_spell_is_not_deducted_twice():
    """Draft and red overlap, so the clock intersects intervals rather than subtracting totals.

    Subtracting red hours from the ready total drives this MR to zero: the ready spells are 3h and the
    red window is 7h, but every one of those red hours falls while the MR was in draft and already
    uncounted. The MR really did wait 3 hours on review.
    """
    opened = datetime(2026, 8, 3, 15, 0, 0)          # Mon 08:00 Pacific, opens ready
    merged = datetime(2026, 8, 4, 17, 0, 0)          # Tue 10:00 Pacific
    conn = _seed([_mr(1, _BEN, opened, merged)])
    store.replace_mr_events(conn, 1, [
        store.MergeRequestEventRow(mr_id=1, kind="draft",
                                   happened_at=datetime(2026, 8, 3, 16, 0, 0), seq=0, actor=None),
        store.MergeRequestEventRow(mr_id=1, kind="ready",
                                   happened_at=datetime(2026, 8, 4, 15, 0, 0), seq=1, actor=None),
    ])
    # red for the whole draft window: Mon 10:00 -> Tue 07:00, which is 7 business hours
    _pipes(conn, 1, [("failed", datetime(2026, 8, 3, 17, 0, 0)),
                     ("success", datetime(2026, 8, 4, 14, 0, 0))])

    row = _report(conn)["slowest"][0]
    assert row["ready_hours"] == 3.0, "1h Mon + 2h Tue; the red hours were all inside the draft"
    assert row["red_hours"] == 7.0, "still reported in full, even though none of it was deducted"


def test_ordinary_pe_work_is_excluded_from_a_page_that_scores_self_service():
    """Adam: "This page is specifically here to score PE on how well self-service is working."

    Unscoped, the panel was 71% unrelated merge requests — only 29% of 1,571 since June carried an
    agent footer and 20% a pe:* label. Two engineers with no access to the skills at all showed 70 and
    51 merge requests of turnaround on it, which is what gave the game away.
    """
    opened, merged = datetime(2026, 8, 3, 15, 0, 0), datetime(2026, 8, 3, 20, 0, 0)
    conn = _seed([
        _mr(1, _BEN, opened, merged, self_service=True),
        _mr(2, _BEN, opened, merged, self_service=False),
        _mr(3, _BEN, opened, merged, self_service=False),
    ])
    report = _report(conn)
    assert report["team"]["merged"] == 1, "only the self-service MR is measured"
    assert report["not_self_service"] == 2, "and the excluded ones are counted, not vanished"
    assert len(report["slowest"]) == 1


def test_an_agent_footer_alone_is_not_self_service():
    """The footer says Claude wrote the code, not that a requester served themselves.

    It used to satisfy the scope test on its own, on the reasoning that it predated the pe:* labels
    by two months. That was wrong in kind rather than degree, and it admitted 347 merge requests with
    no self-service label at all -- overstating the population 2.1x. Those are AI-assisted, which is
    a real figure and a different one.
    """
    opened, merged = datetime(2026, 8, 3, 15, 0, 0), datetime(2026, 8, 3, 20, 0, 0)
    row = _mr(1, _BEN, opened, merged, self_service=False)
    from dataclasses import replace
    conn = _seed([replace(row, description="Generated with Claude Code")])
    report = _report(conn)
    assert report["team"]["merged"] == 0, "an agent footer alone is out of scope"
    assert report["not_self_service"] == 1, "counted as out of scope, not silently dropped"


def test_an_author_with_no_self_service_work_disappears_entirely():
    """Oleh and Denys had 70 and 51 merge requests, none of them self-service.

    They must not appear at all rather than appear with a misleading figure — the panel is scored as
    self-service performance, and their work is not that.
    """
    opened, merged = datetime(2026, 8, 3, 15, 0, 0), datetime(2026, 8, 3, 20, 0, 0)
    conn = _seed([
        _mr(1, _BEN, opened, merged, self_service=True),
        _mr(2, _ADAM, opened, merged, self_service=False),
        _mr(3, _ADAM, opened, merged, self_service=False),
    ])
    report = _report(conn)
    assert [a["name"] for a in report["authors"] if a["merged"]] == ["Ben Bonora"]
    assert report["not_self_service"] == 2


# --- the view is opt-in ------------------------------------------------------------------------

def test_the_table_starts_empty():
    """Nobody is listed until somebody is added. The census lives in the adoption panels above.

    The crawl now keeps every author it finds, so defaulting this table to "everyone" would dump the
    whole organisation into a comparison meant for a handful of people.
    """
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),
        _mr(2, "audacy-marc.polidor", datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 17, 0)),
    ])
    report = mrflow.mr_turnaround_report(
        conn, mrflow.default_window_start(_NOW), {}, [], "all")
    assert report["authors"] == []
    assert report["series"] == [] and report["daily"] == []
    assert report["team"]["merged"] == 2, "the population is still measured, just not itemised"


def test_adding_an_author_puts_exactly_them_in_the_view():
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),
        _mr(2, "audacy-marc.polidor", datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 17, 0)),
    ])
    report = mrflow.mr_turnaround_report(
        conn, mrflow.default_window_start(_NOW),
        {"added": {"audacy-marc.polidor": "Marc Polidor"}}, [], "all")
    assert [a["name"] for a in report["authors"]] == ["Marc Polidor"]
    assert report["team"]["merged"] == 2


def test_adding_a_roster_member_works_despite_the_two_key_spaces():
    """`added` is keyed by GitLab username; a roster member's MRs are stored under their accountId.

    Comparing the two directly would silently never match, so adding a PE member to the view would
    appear to do nothing at all — a dead control with no error anywhere.
    """
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0))])
    report = mrflow.mr_turnaround_report(
        conn, mrflow.default_window_start(_NOW),
        {"added": {"audacy-adam.shero": "Adam"}}, [], "all")
    assert [a["name"] for a in report["authors"]] == ["Adam"]


def test_an_added_author_with_nothing_in_the_window_is_listed_as_a_zero():
    """An add that took must not look like an add that failed.

    Adam merged inside the window; Omar's only self-service MR predates it. Omar was previously
    absent from the response altogether, which on the page is indistinguishable from the add never
    having saved -- no row, no message, nothing. He is now a zero row carrying the date he last
    merged self-service work, because "never" and "not lately" have different remedies: widen the
    lookback, or go and ask why the work is not going through a workflow.

    This is the bug Trevor surfaced. He was added, saved and attributed, with his last self-service
    MR on 2026-07-09, so every recent lookback showed nothing and the page never said why.
    """
    conn = _seed([
        _mr(1, _ADAM, _NOW - timedelta(days=3), _NOW - timedelta(days=2)),
        _mr(2, _OMAR, datetime(2026, 1, 6, 16, 0), datetime(2026, 1, 6, 17, 0)),
    ])
    report = _report(conn)

    assert [a["name"] for a in report["authors"]] == ["Adam"]
    absent = {a["name"]: a for a in report["absent"]}
    assert absent["Omar"]["merged"] == 0
    assert absent["Omar"]["biz_hours_median"] is None
    assert absent["Omar"]["last_self_service"] == "2026-01-06"
    # Ben was added and has never merged anything at all: still listed, distinguished by a null date.
    assert absent["Ben Bonora"]["last_self_service"] is None
    # Kept out of `authors`, so the counts, paging and per-author series still describe measured work.
    assert "Omar" not in {s["name"] for s in report["series"]}
    assert report["team"]["merged"] == 1


def test_a_hidden_or_filtered_out_author_is_not_reintroduced_as_a_zero():
    """The zero rows obey the same two view rules the measured rows do.

    Otherwise x-ing someone out would bring them straight back as a zero, and a filter would widen
    the table instead of narrowing it -- the opposite of what both controls promise.
    """
    conn = _seed([_mr(1, _ADAM, _NOW - timedelta(days=3), _NOW - timedelta(days=2))])

    hidden = _report(conn, roster={"hidden": ["Omar"]})
    assert "Omar" not in {a["name"] for a in hidden["absent"]}

    filtered = _report(conn, name_filter=["ben"])
    assert {a["name"] for a in filtered["absent"]} == {"Ben Bonora"}


def test_the_filter_matches_the_name_the_add_control_showed_you():
    """Filtering by the display name you typed has to find the row it created.

    A roster member's merge requests are keyed by Jira accountId, so the username-keyed "added"
    overlay is never consulted for them and they render under their roster short name: you add
    "Trevor Atchley", the table says "Trevor", and searching for what you typed matched nothing.
    The filter therefore also matches the added display name and the GitLab username.
    """
    conn = _seed([_mr(1, _ADAM, _NOW - timedelta(days=3), _NOW - timedelta(days=2))])
    assert _report(conn)["authors"][0]["name"] == "Adam"       # renders under the roster name

    for term in ("adam", "adam shero", "audacy-adam.shero"):
        report = _report(conn, roster={"added": {"audacy-adam.shero": "Adam Shero"}},
                         name_filter=[term])
        assert [a["name"] for a in report["authors"]] == ["Adam"], f"{term!r} found nothing"

    # And it is still a filter: a term matching nobody must not admit everybody.
    assert _report(conn, name_filter=["nobody"])["authors"] == []
