"""The velocity payload splits each member's completions by Estimated Size, and states how far into
the month the forecast's blend is.

The page's "By ticket size" panel draws each member's tickets closed per month by size, and its All
sizes bar is meant to be the same velocity the By engineer chart shows, only split. That holds only
if every completion lands in exactly one size bucket: an option Jira adds later has to count as
unsized (the rule intake's weight_of applies to WIP), not vanish, and a reopened ticket still counts
once.

The forecast description quotes the business days elapsed this month, because that fraction is the
weight the forecast gives this month's pace. It has to be the business-timezone month the forecast
blends, or the page would describe one month while forecasting another.
"""
from datetime import datetime

import duckdb

from darkstar import store, velocity

_NOW = datetime(2026, 9, 23, 18, 0, 0)
_OMAR = "712020:58e4121c-dadd-4c34-99a9-92dc31ee039b"
_ADAM = "600ece193b1af000697f339d"


def _issue(key: str, account_id: str, size: str | None) -> store.IssueRow:
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype="Story", status="Done",
        status_category="done", priority="Medium", summary=key, assignee=None,
        assignee_account_id=account_id, reporter=None, business_lead=None, parent_key=None,
        created=datetime(2026, 1, 5), updated=_NOW, resolutiondate=None, planned_start=None,
        target_end=None, labels=[], mr_field_url=None, dev_has_pr=False, dev_has_commits=False,
        estimated_size=size, fetched_at=_NOW)


def _report(completions: list[tuple[str, str, str | None, list[datetime]]], now: datetime) -> dict:
    """The velocity payload for issues (key, assignee, size) completed at each of their Done times."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, [_issue(key, account_id, size) for key, account_id, size, _ in completions])
    for key, _account_id, _size, done_times in completions:
        store.replace_transitions(conn, [key], [
            store.TransitionRow(key=key, to_status="Done", changed_at=done_at, seq=seq)
            for seq, done_at in enumerate(done_times)])
    return velocity.velocity_report(conn, now)


def test_each_completion_lands_in_exactly_one_size_so_the_split_matches_velocity():
    report = _report([
        ("DEVOPS-1", _OMAR, "Small", [datetime(2026, 8, 4, 18)]),
        ("DEVOPS-2", _OMAR, "Small", [datetime(2026, 8, 12, 18)]),
        ("DEVOPS-3", _OMAR, "Medium", [datetime(2026, 8, 20, 18)]),
        ("DEVOPS-4", _OMAR, None, [datetime(2026, 8, 21, 18)]),
        ("DEVOPS-5", _OMAR, "Huge", [datetime(2026, 8, 24, 18)]),
        # Reopened in August after first reaching Done in July: it counts once, in July.
        ("DEVOPS-6", _OMAR, "Large", [datetime(2026, 7, 9, 18), datetime(2026, 8, 3, 18)]),
        ("DEVOPS-7", _ADAM, "XL", [datetime(2026, 6, 15, 18)]),
    ], _NOW)
    assert report["months"] == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    assert report["sizes"] == ["Small", "Medium", "Large", "XL"], "the page draws its ramp in this order"
    members = {member["name"]: member for member in report["members"]}
    assert members["Omar"]["sizes"] == {
        "Small": [0, 0, 0, 0, 0, 2],
        "Medium": [0, 0, 0, 0, 0, 1],
        "Large": [0, 0, 0, 0, 1, 0],
        "XL": [0, 0, 0, 0, 0, 0],
        "Unsized": [0, 0, 0, 0, 0, 2],
    }, "no size and an option Jira added later both count as unsized"
    for member in report["members"]:
        split = [sum(month) for month in zip(*member["sizes"].values())]
        assert split == member["counts"], f"{member['name']}'s size split must sum to their velocity"


def test_month_progress_counts_business_days_in_the_month_the_forecast_blends():
    """September 2026 has 22 weekdays; the 23rd is the 17th of them.

    At 05:00 UTC on October 1 it is still September 30 in Pacific, the business timezone, so the
    forecast is still blending September -- counting UTC's date would report October's first day.
    """
    assert _report([], _NOW)["month_progress"] == {"elapsed": 17, "total": 22}
    assert _report([], datetime(2026, 10, 1, 5, 0, 0))["month_progress"] == {"elapsed": 22, "total": 22}
