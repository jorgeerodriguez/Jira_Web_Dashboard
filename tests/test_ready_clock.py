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
    assert ready_hours(_MON_0800, _MON_1200, [], []) == 4.0


def test_draft_time_is_not_charged():
    """Ready 08:00, draft at 12:00, merged at 16:00 → only the first four hours count."""
    assert ready_hours(_MON_0800, _MON_1600, [("draft", _MON_1200)], []) == 4.0


def test_an_mr_opened_as_a_draft_starts_the_clock_at_ready():
    """First event "ready" means it was a draft before that, so the earlier hours are the author's.

    This is the 612-hour case: open for weeks as a draft, marked ready, merged minutes later.
    """
    assert ready_hours(_MON_0800, _TUE_1200, [("ready", _TUE_0800)], []) == 4.0


def test_toggling_back_and_forth_accrues_only_the_ready_spells():
    """Ready 08:00-12:00, draft until Tue 08:00, ready again, merged Tue 12:00 → 4h + 4h."""
    events = [("draft", _MON_1200), ("ready", _TUE_0800)]
    assert ready_hours(_MON_0800, _TUE_1200, events, []) == 8.0


def test_a_still_draft_mr_at_merge_stops_accruing():
    """Merged straight out of draft: the draft hours are still not charged."""
    assert ready_hours(_MON_0800, _TUE_1200, [("draft", _MON_1200)], []) == 4.0


def test_events_outside_the_span_are_clamped():
    """A stray event before open or after merge must not push the clock outside the MR's life."""
    before, after = datetime(2026, 8, 10, 15, 0), datetime(2026, 9, 1, 15, 0)
    assert ready_hours(_MON_0800, _MON_1200, [("ready", before)], []) == 4.0
    assert ready_hours(_MON_0800, _MON_1200, [("draft", after)], []) == 4.0


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


# -- what counts as review -----------------------------------------------------------------------

def test_an_approval_is_review_even_with_no_comment():
    """PE merges its own work once a colleague approves, so the approval IS the review.

    Counting comments alone measured conversation, not review: on the crawled sample 111 of 124
    MRs carried an approval and only 23 drew a comment.
    """
    notes = [_note("pavlo.myshok", "approved this merge request",
                   "2026-08-17T17:00:00Z", system=True)]
    events = _mr_events(1, "audacy-adam.shero", notes)
    assert [(e.kind, e.happened_at) for e in events] == [("approval", datetime(2026, 8, 17, 17, 0))]


def test_a_self_approval_is_not_review():
    notes = [_note("audacy-adam.shero", "approved this merge request",
                   "2026-08-17T17:00:00Z", system=True)]
    assert _mr_events(1, "audacy-adam.shero", notes) == []


def test_first_review_takes_the_earliest_of_the_three_signals():
    from darkstar.mrflow import first_review_at
    merged = datetime(2026, 8, 17, 23, 0)
    comment = ("review", datetime(2026, 8, 17, 20, 0))
    approval = ("approval", datetime(2026, 8, 17, 18, 0))

    # approval precedes the comment
    assert first_review_at([comment, approval], merged, author_is_merger=True) == approval[1]
    # no events, but someone else merged it — that merge is the review
    assert first_review_at([], merged, author_is_merger=False) == merged
    # a colleague's approval still beats the later merge
    assert first_review_at([approval], merged, author_is_merger=False) == approval[1]


def test_a_self_merge_with_no_approval_or_comment_is_not_review():
    """A self-merge is normal for PE and says nothing on its own about whether anyone looked."""
    from darkstar.mrflow import first_review_at
    assert first_review_at([], datetime(2026, 8, 17, 23, 0), author_is_merger=True) is None


def test_red_pipeline_time_is_not_charged_to_review():
    """A broken pipeline is not PE being slow: the MR cannot merge whoever reviews it.

    Adam's framing, and the reason this is universal rather than author-conditional: whoever wrote it,
    the ball is with whoever pushes the fix. Measured over the slowest MRs, 32% of open hours on
    PE-authored ones and 4% on externally-authored ones were spent red.
    """
    from darkstar.metrics import ready_hours, red_spans
    opened = datetime(2026, 8, 3, 15, 0, 0)          # Mon 08:00 Pacific
    merged = datetime(2026, 8, 4, 17, 0, 0)          # Tue 10:00 Pacific -> 11 business hours
    assert ready_hours(opened, merged, [], []) == 11.0

    # failed at Mon 10:00, green again Tue 09:00: 7h Mon + 1h Tue = 8 business hours red
    reds = red_spans([("failed", datetime(2026, 8, 3, 17, 0, 0)),
                      ("success", datetime(2026, 8, 4, 16, 0, 0))], opened, merged)
    assert ready_hours(opened, merged, [], reds) == 3.0, "11h ready less 8h red"


def test_a_run_still_going_is_not_a_failure():
    """Only "failed" stops the clock — a pipeline in flight is the normal state of a live MR."""
    from darkstar.metrics import ready_hours, red_spans
    opened, merged = datetime(2026, 8, 3, 15, 0, 0), datetime(2026, 8, 3, 20, 0, 0)
    reds = red_spans([("running", datetime(2026, 8, 3, 16, 0, 0)),
                      ("success", datetime(2026, 8, 3, 17, 0, 0))], opened, merged)
    assert reds == []
    assert ready_hours(opened, merged, [], reds) == 5.0


def test_red_time_while_the_mr_is_in_draft_is_not_deducted_twice():
    """Draft time is already excluded; deducting the overlap again would go past zero.

    This is why the clock subtracts intervals rather than subtracting red hours from a total.
    """
    from darkstar.metrics import ready_hours, red_spans
    opened = datetime(2026, 8, 3, 15, 0, 0)          # Mon 08:00 Pacific, opens ready
    merged = datetime(2026, 8, 4, 17, 0, 0)          # Tue 10:00 Pacific
    # draft from Mon 09:00, ready again Tue 08:00 -> ready spells are 1h Mon + 2h Tue
    events = [("draft", datetime(2026, 8, 3, 16, 0, 0)),
              ("ready", datetime(2026, 8, 4, 15, 0, 0))]
    assert ready_hours(opened, merged, events, []) == 3.0
    # a failure spanning the whole draft window and nothing else
    reds = red_spans([("failed", datetime(2026, 8, 3, 17, 0, 0)),
                      ("success", datetime(2026, 8, 4, 14, 0, 0))], opened, merged)
    assert ready_hours(opened, merged, events, reds) == 3.0, "red inside draft costs nothing extra"


def test_a_pipeline_left_red_at_merge_runs_to_the_merge():
    """Merged with a red pipeline (an admin override, or a later green outside the window)."""
    from darkstar.metrics import ready_hours, red_spans
    opened, merged = datetime(2026, 8, 3, 15, 0, 0), datetime(2026, 8, 3, 22, 0, 0)
    reds = red_spans([("failed", datetime(2026, 8, 3, 17, 0, 0))], opened, merged)
    assert ready_hours(opened, merged, [], reds) == 2.0, "only the 2h before it broke"


def test_subtract_spans_handles_overlapping_and_unordered_holes():
    """Pipelines report out of order and their red windows can overlap; the arithmetic must not care."""
    from darkstar.metrics import subtract_spans
    span = [(datetime(2026, 8, 3, 0, 0), datetime(2026, 8, 3, 10, 0))]
    holes = [(datetime(2026, 8, 3, 6, 0), datetime(2026, 8, 3, 8, 0)),
             (datetime(2026, 8, 3, 2, 0), datetime(2026, 8, 3, 5, 0)),
             (datetime(2026, 8, 3, 4, 0), datetime(2026, 8, 3, 7, 0))]
    assert subtract_spans(span, holes) == [
        (datetime(2026, 8, 3, 0, 0), datetime(2026, 8, 3, 2, 0)),
        (datetime(2026, 8, 3, 8, 0), datetime(2026, 8, 3, 10, 0)),
    ]
    assert subtract_spans(span, []) == span
