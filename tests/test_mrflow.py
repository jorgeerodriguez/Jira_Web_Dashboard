"""MR turnaround must measure delivery speed, not how much of the wait was overnight.

The point of this view is to answer "how quickly does an author's work land", including for
contributors who are not on the PE roster — the previous ingest discarded their MRs entirely,
so they could not be measured at all.
"""
from datetime import datetime, timedelta

import duckdb

from darkstar import mrflow, store
from darkstar.roster import MR_AUTHORS, ROSTER, TRACKED_MR_AUTHORS

_ADAM = "600ece193b1af000697f339d"
_BEN = TRACKED_MR_AUTHORS["audacy-ben.bonora"]
_NOW = datetime(2026, 8, 18, 12, 0, 0)


def _mr(id, account_id, opened, merged):
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id=account_id,
        title=f"MR {id}", opened_at=opened, merged_at=merged, labels=[],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="")


def _report(conn, roster=None, name_filter=None, environment="all"):
    """mr_turnaround_report with the view's default window, empty roster, no filters."""
    return mrflow.mr_turnaround_report(
        conn, mrflow.default_window_start(_NOW), roster or {}, name_filter or [], environment)


def _seed(rows):
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_merge_requests(conn, rows)
    return conn


def test_tracked_authors_are_attributed_but_stay_off_the_roster():
    """Ben and Jeremy must be ingestible without silently entering velocity/capacity/SME counts."""
    for username, account_id in TRACKED_MR_AUTHORS.items():
        assert MR_AUTHORS[username] == account_id   # the ingest will now attribute their MRs
        assert account_id not in ROSTER             # ...but roster-gated views are unchanged


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


def test_unmapped_author_is_dropped_from_rows_and_from_the_team_total():
    """The team row must describe the rows on screen, or the two disagree and neither is trusted."""
    conn = _seed([_mr(1, "someone-not-in-any-map", datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 17, 0))])
    report = _report(conn)
    assert report["authors"] == []
    assert report["team"]["merged"] == 0


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


def test_hiding_an_author_removes_them_from_the_rows_and_the_total():
    """Hiding is not just a visual filter — the team total must not describe hidden work."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # 6h
    ])
    report = _report(conn, {"hidden": ["Ben Bonora"]})
    assert [a["name"] for a in report["authors"]] == ["Adam"]
    assert report["team"]["merged"] == 1 and report["team"]["biz_hours_median"] == 1.0
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
    assert filtered["team"]["merged"] == 1
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
    return store.MergeRequestRow(
        id=id, project_path=project_path, iid=id, author_account_id=account_id,
        title=f"MR {id}", opened_at=opened, merged_at=merged, labels=[],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="")


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
        mr_id=1, kind="draft", happened_at=datetime(2026, 8, 17, 19, 0), seq=0)])
    assert _report(conn)["team"]["biz_hours_median"] == 4.0      # draft from 12:00 Mon onwards


def test_first_review_is_reported_on_the_ready_clock():
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 23, 0))])
    store.replace_mr_events(conn, 1, [store.MergeRequestEventRow(
        mr_id=1, kind="review", happened_at=datetime(2026, 8, 17, 18, 0), seq=0)])
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


def test_crawl_state_says_when_an_added_author_is_still_owed_a_crawl():
    """"I added someone and nothing happened" must be answerable from the page.

    Until a crawl fetches their merge requests the author cannot appear, and with no signal that
    is indistinguishable from the add having failed.
    """
    conn = _seed([])
    fresh = _report(conn, {"added": {}, "hidden": [], "version": 0})["crawl"]
    assert fresh["pending"] is False and fresh["last_crawl"] is None

    after_add = _report(conn, {"added": {"audacy-x": "X"}, "hidden": [], "version": 1})["crawl"]
    assert after_add["pending"] is True          # roster moved ahead of what was crawled
    assert after_add["roster_version"] == 1 and after_add["crawled_version"] == 0

    store.set_roster_version(conn, 1)
    store.set_gitlab_watermark(conn, datetime(2026, 8, 18, 12, 0))
    caught_up = _report(conn, {"added": {"audacy-x": "X"}, "hidden": [], "version": 1})["crawl"]
    assert caught_up["pending"] is False
    assert caught_up["last_crawl"].startswith("2026-08-18")


def test_a_roster_member_re_added_is_not_split_into_two_rows():
    """`added` must not override MR_AUTHORS, or one person becomes two rows with the same name."""
    from darkstar.roster import MR_AUTHORS
    added = {"audacy-adam.shero": "Adam Shero"}
    attributable = {**{u: u for u in added}, **MR_AUTHORS}
    assert attributable["audacy-adam.shero"] == MR_AUTHORS["audacy-adam.shero"]  # accountId wins


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
        store.MergeRequestEventRow(mr_id=1, kind="draft", happened_at=opened, seq=0),
        store.MergeRequestEventRow(
            mr_id=1, kind="ready", happened_at=datetime(2026, 8, 5, 16, 0, 0), seq=1),
    ])
    row = _report(conn)["slowest"][0]

    # Mon 08:00 -> Wed 10:00 is 9h + 9h + 2h = 20 business hours, of which one was ready.
    assert row["ready_hours"] == 1.0, "only the ready spell counts toward turnaround"
    assert row["open_hours"] == 20.0, "the whole span is carried alongside it"
    assert round(row["open_hours"] - row["ready_hours"], 1) == 19.0, "the rest was draft"
    for field in ("iid", "project", "title", "url", "author", "merged", "environment"):
        assert row[field] is not None, f"{field} is needed to identify the MR being inspected"
