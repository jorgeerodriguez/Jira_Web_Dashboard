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


def test_overnight_wait_separates_business_from_calendar():
    """Opened 16:00 Tue, merged 09:00 Wed: 1 business hour against 17 calendar hours."""
    conn = _seed([_mr(1, _BEN, datetime(2026, 8, 18, 22, 0), datetime(2026, 8, 19, 15, 0))])
    ben = next(a for a in mrflow.mr_turnaround_report(conn, _NOW)["authors"] if a["name"] == "Ben Bonora")
    assert ben["merged"] == 1
    assert ben["biz_hours_median"] == 1.0
    assert ben["cal_hours_median"] == 17.0
    assert ben["tracked"] is True   # flagged as a non-roster author on the dashboard


def test_every_merged_mr_counts_not_one_per_issue():
    """Unlike the SLA bucket's review median, no MR is dropped — three MRs give a 3-MR median."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 18, 0)),   # 3h
        _mr(3, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 20, 0)),   # 5h
    ])
    adam = next(a for a in mrflow.mr_turnaround_report(conn, _NOW)["authors"] if a["name"] == "Adam")
    assert adam["merged"] == 3
    assert adam["biz_hours_median"] == 3.0
    assert adam["biz_hours_p90"] == 5.0
    assert adam["cal_hours_median"] == 3.0   # fixture sits inside the Denver workday, so they agree


def test_mrs_merged_before_the_window_are_excluded():
    """Without a window an old outlier would drag the median forever."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2025, 1, 6, 16, 0), datetime(2025, 1, 10, 23, 0)),    # long, out of window
        _mr(2, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 17, 0)),   # 2h, in window
    ])
    report = mrflow.mr_turnaround_report(conn, _NOW)
    assert report["window_start"] == "2026-03-01"
    assert report["team"]["merged"] == 1
    assert report["team"]["biz_hours_median"] == 2.0


def test_authors_sort_slowest_first():
    """The dashboard leads with whoever is slowest on the standard clock, as the lead-time table does."""
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 16, 0)),   # 1h
        _mr(2, _BEN, datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 21, 0)),    # 6h
    ])
    assert [a["name"] for a in mrflow.mr_turnaround_report(conn, _NOW)["authors"]] == ["Ben Bonora", "Adam"]


def test_unmapped_author_is_dropped_rather_than_shown_unnamed():
    conn = _seed([_mr(1, "someone-not-in-any-map", datetime(2026, 8, 17, 15, 0), datetime(2026, 8, 17, 17, 0))])
    report = mrflow.mr_turnaround_report(conn, _NOW)
    assert report["authors"] == []
    assert report["team"]["merged"] == 1   # still counted in the team total


def test_calendar_column_exposes_the_eet_understatement():
    """The standard Denver clock understates EET work, so the calendar column must stay visible.

    Audacy's users are North American, so one Denver business clock is the agreed standard. But an
    MR a Ukraine-based engineer opens and merges inside their own workday falls entirely outside
    Denver 09:00-17:00 and scores 0.0, which would read as instantaneous. The calendar median is
    reported beside it precisely so that case is visible rather than silently flattering.
    """
    # Opened 09:00 and merged 14:00 Kyiv (EEST, UTC+3) = 06:00-11:00 UTC = 00:00-05:00 Denver.
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 17, 6, 0), datetime(2026, 8, 17, 11, 0))])
    author = mrflow.mr_turnaround_report(conn, _NOW)["authors"][0]
    assert author["cal_hours_median"] == 5.0   # five real hours the MR was open
    assert author["biz_hours_median"] == 0.0   # none of it inside the Denver workday
