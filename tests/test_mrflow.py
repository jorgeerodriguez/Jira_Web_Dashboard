"""MR turnaround must measure delivery speed, not how much of the wait was overnight.

The point of this view is to answer "how quickly does an author's work land", including for
contributors who are not on the PE roster — the previous ingest discarded their MRs entirely,
so they could not be measured at all.
"""
from datetime import datetime

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
        web_url="u", fetched_at=_NOW, description="")


def _report(conn, roster=None):
    """mr_turnaround_report with the view's default window and, by default, an empty roster."""
    return mrflow.mr_turnaround_report(conn, mrflow.default_window_start(_NOW), roster or {})


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
    """Opened 16:00 Tue, merged 09:00 Wed: 1 business hour, though 17 hours elapsed on the clock."""
    conn = _seed([_mr(1, _BEN, datetime(2026, 8, 18, 22, 0), datetime(2026, 8, 19, 15, 0))])
    ben = next(a for a in _report(conn)["authors"] if a["name"] == "Ben Bonora")
    assert ben["merged"] == 1
    assert ben["biz_hours_median"] == 1.0
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


def test_eet_work_scores_near_zero_on_the_denver_clock():
    """A known and accepted property of standardising on one North American business day.

    An MR a Ukraine-based engineer opens and merges inside their own workday falls entirely
    outside Denver 09:00-17:00 and scores 0.0. Recorded so the behaviour is deliberate rather
    than a surprise when a row reads as instantaneous.
    """
    # Opened 09:00 and merged 14:00 Kyiv (EEST, UTC+3) = 06:00-11:00 UTC = 00:00-05:00 Denver.
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
