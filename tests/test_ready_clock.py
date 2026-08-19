"""The turnaround clock must run only while an MR is asking for review.

Measured from `opened_at`, the numbers were dominated by draft time: across the 20 slowest MRs,
67% of all attributed hours were spent in draft, and one 612-hour MR was marked ready 15 minutes
before it merged. Draft state comes from GitLab's own system notes, which — unlike commit
timestamps — a rebase cannot rewrite.
"""
from datetime import datetime

from darkstar.gitlab_ingest import _is_bot, _mr_events
from darkstar.mrflow import ready_hours

# August, so Pacific is UTC-7; the business day is 08:00-17:00 local.
_MON_0800 = datetime(2026, 8, 17, 15, 0)
_MON_1200 = datetime(2026, 8, 17, 19, 0)
_MON_1600 = datetime(2026, 8, 17, 23, 0)
_TUE_0800 = datetime(2026, 8, 18, 15, 0)
_TUE_1200 = datetime(2026, 8, 18, 19, 0)


def test_no_events_measures_the_whole_span():
    """An MR that was never a draft and drew no comments is ready from the moment it opened."""
    assert ready_hours(_MON_0800, _MON_1200, []) == 4.0


def test_draft_time_is_not_charged():
    """Ready 08:00, draft at 12:00, merged at 16:00 → only the first four hours count."""
    assert ready_hours(_MON_0800, _MON_1600, [("draft", _MON_1200)]) == 4.0


def test_an_mr_opened_as_a_draft_starts_the_clock_at_ready():
    """First event "ready" means it was a draft before that, so the earlier hours are the author's.

    This is the 612-hour case: open for weeks as a draft, marked ready, merged minutes later.
    """
    assert ready_hours(_MON_0800, _TUE_1200, [("ready", _TUE_0800)]) == 4.0


def test_toggling_back_and_forth_accrues_only_the_ready_spells():
    """Ready 08:00-12:00, draft until Tue 08:00, ready again, merged Tue 12:00 → 4h + 4h."""
    events = [("draft", _MON_1200), ("ready", _TUE_0800)]
    assert ready_hours(_MON_0800, _TUE_1200, events) == 8.0


def test_a_still_draft_mr_at_merge_stops_accruing():
    """Merged straight out of draft: the draft hours are still not charged."""
    assert ready_hours(_MON_0800, _TUE_1200, [("draft", _MON_1200)]) == 4.0


def test_events_outside_the_span_are_clamped():
    """A stray event before open or after merge must not push the clock outside the MR's life."""
    before, after = datetime(2026, 8, 10, 15, 0), datetime(2026, 9, 1, 15, 0)
    assert ready_hours(_MON_0800, _MON_1200, [("ready", before)]) == 4.0
    assert ready_hours(_MON_0800, _MON_1200, [("draft", after)]) == 4.0


# -- what counts as review activity ------------------------------------------------------------

def _note(username, body, when, system=False):
    return {"author": {"username": username}, "body": body, "created_at": when, "system": system}


def test_bot_comments_are_not_review_activity():
    """GitLab Duo answers on every MR; counting it would make every MR look reviewed instantly."""
    assert _is_bot("GitLabDuo") is True
    assert _is_bot("security-bot") is True
    assert _is_bot("audacy-adam.shero") is False


def test_first_review_ignores_bots_and_the_author():
    """A review is a human other than the author looking at it."""
    notes = [
        _note("GitLabDuo", "Can't access the merge request.", "2026-08-17T15:10:00Z"),
        _note("audacy-adam.shero", "self note", "2026-08-17T16:00:00Z"),
        _note("pavlo.myshok", "looks good", "2026-08-17T17:00:00Z"),
    ]
    events = _mr_events(1, "audacy-adam.shero", notes)
    review = [e for e in events if e.kind == "review"]
    assert len(review) == 1
    assert review[0].happened_at == datetime(2026, 8, 17, 17, 0)


def test_draft_and_ready_system_notes_become_events():
    notes = [
        _note("a", "marked this merge request as **draft**", "2026-08-17T16:00:00Z", system=True),
        _note("b", "marked this merge request as **ready**", "2026-08-18T15:00:00Z", system=True),
    ]
    events = _mr_events(1, "someone", notes)
    assert [(e.kind, e.seq) for e in events] == [("draft", 0), ("ready", 1)]


def test_an_mr_with_nothing_to_record_yields_no_events():
    """Which is why completeness is tracked by events_fetched_at, not by having any events."""
    assert _mr_events(1, "a", [_note("GitLabDuo", "hi", "2026-08-17T15:00:00Z")]) == []
