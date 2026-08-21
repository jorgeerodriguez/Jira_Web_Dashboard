"""Adoption asks whether self-service authorship has actually left PE, not how fast PE is.

The whole point of the panel is a comparison between two populations, so every test here is about
the boundary between them: who counts as PE, which merge requests are in scope, and — the part that
is easy to get wrong — what the panel must refuse to claim when the data cannot support it.

The floor discipline matters most. The GitLab crawl attributes a fixed list of authors, so non-PE
authorship is systematically undercounted; a panel that presented its share as a measurement rather
than a lower bound would contradict a hand-audit of the same window and be believed over it.
"""
from datetime import datetime

import duckdb

from darkstar import mrflow, store
from darkstar.roster import ROSTER, TRACKED_MR_AUTHORS

_ADAM = "600ece193b1af000697f339d"
_OMAR = "712020:58e4121c-dadd-4c34-99a9-92dc31ee039b"
_BEN = TRACKED_MR_AUTHORS["audacy-ben.bonora"]
_JEREMY = TRACKED_MR_AUTHORS["audacy-jeremy.williams"]
_NOW = datetime(2026, 8, 19, 12, 0, 0)
_SINCE = datetime(2026, 8, 3, 7, 0)


def _mr(id, account_id, opened, merged, self_service=True):
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id=account_id,
        title=f"MR {id}", opened_at=opened, merged_at=merged,
        labels=["pe:iac-request"] if self_service else [],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="",
        source_branch="", pipelines_fetched_at=_NOW)


def _seed(rows, events=None):
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_merge_requests(conn, rows)
    for mr_id, rows in (events or {}).items():
        store.replace_mr_events(conn, mr_id, rows)
    return conn


def _report(conn, roster=None, grain="week", until=None):
    return mrflow.adoption_report(conn, _SINCE, until, _NOW, grain, roster or {})


def test_the_pe_test_is_roster_membership_not_a_username_spelling():
    """Ben and Jeremy are outside PE; the roster accountId is what decides, not the `audacy-` prefix.

    Adam confirmed both are non-PE. Their GitLab usernames look exactly like roster members' — the
    `audacy-` prefix is an account-provisioning artefact, not an affiliation — so any classifier
    keying on the username would put them on the wrong side of the only comparison this panel makes.
    """
    assert mrflow.is_pe_author(_ADAM) is True
    assert mrflow.is_pe_author(_BEN) is False
    assert mrflow.is_pe_author(_JEREMY) is False
    for account_id in TRACKED_MR_AUTHORS.values():
        assert account_id not in ROSTER


def test_a_runtime_added_author_counts_as_non_pe():
    """Authors added through the dashboard are keyed by username, which is never a roster accountId.

    Defaulting them to PE would let a lead dilute the adoption number by adding contributors.
    """
    conn = _seed([_mr(1, "audacy-marc.polidor", datetime(2026, 8, 10, 16, 0),
                      datetime(2026, 8, 10, 17, 0))])
    report = _report(conn, roster={"added": {"audacy-marc.polidor": "Marc Polidor"}})
    assert report["non_pe"]["mrs"] == 1
    assert report["pe"]["mrs"] == 0
    assert report["by_author"] == [{"name": "Marc Polidor", "mrs": 1, "pe": False}]


def test_the_split_counts_both_sides_of_the_same_population():
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
        _mr(2, _OMAR, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
        _mr(3, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
    ])
    report = _report(conn)
    assert report["pe"]["mrs"] == 2
    assert report["non_pe"]["mrs"] == 1
    assert report["total"] == 3
    assert report["share"] == round(1 / 3, 4)


def test_work_that_is_not_self_service_is_out_of_scope_entirely():
    """This scores the self-service offering; ordinary PE work would swamp the PE side of the split.

    Unscoped, the same population ran 71% unrelated merge requests, which would drive the non-PE
    share toward zero and read as "nobody has adopted this" when the truth is "we counted the wrong
    merge requests".
    """
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0), self_service=False),
        _mr(2, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
    ])
    report = _report(conn)
    assert report["pe"]["mrs"] == 0
    assert report["non_pe"]["mrs"] == 1
    assert report["share"] == 1.0


def test_a_hidden_author_still_counts_as_adoption():
    """`hidden` curates the table below; hiding a name does not un-adopt the tooling.

    The turnaround table drops hidden authors from its rows AND its totals so the totals describe
    what is on screen. Inheriting that here would let a lead change the adoption score by tidying a
    table, and would have deployed near-empty: the pod's roster currently hides all 14 PE members.
    """
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
        _mr(2, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
    ])
    report = _report(conn, roster={"hidden": ["Adam", "Ben Bonora"]})
    assert report["pe"]["mrs"] == 1
    assert report["non_pe"]["mrs"] == 1


def test_an_empty_period_has_no_share_rather_than_a_zero_one():
    """0 of 0 is not 0%. A share of zero draws a floor line the data does not support."""
    conn = _seed([_mr(1, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0))])
    report = _report(conn)
    assert report["periods"][0]["share"] == 1.0
    empty = _report(_seed([]))
    assert empty["share"] is None
    assert empty["periods"] == []


def test_periods_are_flagged_partial_on_both_window_edges():
    """A half-week at either end is a shorter bar, and unflagged it reads as a real drop."""
    conn = _seed([
        _mr(1, _BEN, datetime(2026, 8, 6, 16, 0), datetime(2026, 8, 6, 17, 0)),    # opening week
        _mr(2, _BEN, datetime(2026, 8, 12, 16, 0), datetime(2026, 8, 12, 17, 0)),  # whole week
        _mr(3, _BEN, datetime(2026, 8, 18, 16, 0), datetime(2026, 8, 18, 17, 0)),  # week in progress
    ])
    # Wed 2026-08-05 00:00 PDT: the opening week is already three days gone when the window starts.
    midweek = mrflow.adoption_report(conn, datetime(2026, 8, 5, 7, 0), None, _NOW, "week", {})
    periods = {p["period"]: p["partial"] for p in midweek["periods"]}
    assert periods["2026-08-03"] is True     # window opened mid-week
    assert periods["2026-08-10"] is False    # a whole week, both edges inside the window
    assert periods["2026-08-17"] is True     # still running at _NOW


def test_a_window_opening_exactly_on_a_period_boundary_is_not_partial():
    """Partial must mean truncated, not merely first — else every lookback flags its own opening row."""
    conn = _seed([_mr(1, _BEN, datetime(2026, 8, 4, 16, 0), datetime(2026, 8, 4, 17, 0))])
    # _SINCE is Mon 2026-08-03 00:00 PDT, the exact start of the week bucket.
    assert _report(conn)["periods"][0]["partial"] is False


def test_an_independent_approval_is_counted_per_side():
    """mr_events records an approval only when it came from someone other than the author."""
    conn = _seed(
        [_mr(1, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
         _mr(2, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
         _mr(3, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0))],
        events={1: [store.MergeRequestEventRow(
            mr_id=1, kind="approval", happened_at=datetime(2026, 8, 10, 16, 30), seq=0)],
                2: [store.MergeRequestEventRow(
            mr_id=2, kind="review", happened_at=datetime(2026, 8, 10, 16, 30), seq=0)]})
    report = _report(conn)
    assert report["non_pe"]["mrs"] == 3
    # One approval, not two: a review comment is not an approval, and the other MR drew neither.
    assert report["non_pe"]["independent_approvals"] == 1


def test_the_non_pe_figure_is_published_as_a_floor():
    """Only attributed authors are visible, so the panel must be able to state its own bound.

    A hand audit of the same window found 10 non-PE authors where the ingest attributes 2. Without
    this number the panel cannot say which it is, and a reader compares 15% against a known 26% and
    concludes the dashboard is broken rather than narrow.
    """
    conn = _seed([_mr(1, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0))])
    report = _report(conn, roster={"added": {"audacy-marc.polidor": "Marc Polidor"}})
    assert report["tracked_non_pe_authors"] == len(TRACKED_MR_AUTHORS) + 1


def test_figures_the_store_cannot_support_are_named_not_approximated():
    """Merge rate and approver identity both need an ingest change; neither may be guessed at.

    The scorecard this panel came from carried both. Rendering a plausible substitute in their place
    is how a dashboard ends up quietly disagreeing with the audit it was built to reproduce.
    """
    report = _report(_seed([]))
    assert set(report["unmeasurable"]) == {"merge_rate", "approver_identity"}
    assert "state=merged" in report["unmeasurable"]["merge_rate"]
    assert "not who" in report["unmeasurable"]["approver_identity"]


def test_an_unattributable_author_joins_neither_side():
    """An author the roster cannot name has no affiliation, and inventing one moves the headline."""
    conn = _seed([_mr(1, "unknown-account-id", datetime(2026, 8, 10, 16, 0),
                      datetime(2026, 8, 10, 17, 0))])
    report = _report(conn)
    assert report["total"] == 0
    assert report["unattributed"] == 1
    assert report["share"] is None


def test_the_share_is_computed_over_the_selected_window_only():
    """The headline percentage must answer "in the window you picked", not "since records began".

    A share that silently spans a fixed window while the chart beside it obeys the lookback is the
    worst of both: the number looks responsive because the periods below it move, and it is not.
    Here the two halves of the store have opposite authorship, so any window that leaked would show.
    """
    conn = _seed(
        [_mr(i, _ADAM, datetime(2026, 8, 4, 16, 0), datetime(2026, 8, 4, 17, 0)) for i in range(1, 10)]
        + [_mr(i, _BEN, datetime(2026, 8, 18, 16, 0), datetime(2026, 8, 18, 17, 0))
           for i in range(10, 19)])
    whole = mrflow.adoption_report(conn, datetime(2026, 8, 1, 7, 0), None, _NOW, None, {})
    assert whole["total"] == 18 and whole["share"] == 0.5

    # The same store, read through a lookback that starts after the PE-authored half.
    recent = mrflow.adoption_report(conn, datetime(2026, 8, 17, 7, 0), None, _NOW, None, {})
    assert recent["total"] == 9 and recent["share"] == 1.0
    # A three-day lookback also coarsens differently: the grain follows the window, as everywhere
    # else on the page, so the row is the merge day rather than the week containing it.
    assert recent["grain"] == "day"
    assert [p["period"] for p in recent["periods"]] == ["2026-08-18"]
    assert whole["grain"] == "week"


def test_an_mr_merged_exactly_at_the_upper_bound_is_excluded():
    """`until` is exclusive everywhere else on the page, so two adjacent lookbacks cannot both claim
    the same merge request. Tested ON the boundary: an MR merged a day later is excluded either way.
    """
    boundary = datetime(2026, 8, 17, 7, 0)
    conn = _seed(
        [_mr(1, _ADAM, datetime(2026, 8, 4, 16, 0), datetime(2026, 8, 4, 17, 0)),
         _mr(2, _BEN, datetime(2026, 8, 17, 6, 0), boundary)])
    bounded = mrflow.adoption_report(conn, datetime(2026, 8, 1, 7, 0), boundary, _NOW, None, {})
    assert bounded["total"] == 1, "the MR merged at the bound belongs to the next window, not this one"
    assert bounded["share"] == 0.0, "the only MR in range is PE's, so the non-PE share is a real zero"

    # ...and the adjacent window does claim it, so no merge request falls between the two.
    following = mrflow.adoption_report(conn, boundary, None, _NOW, None, {})
    assert following["total"] == 1 and following["share"] == 1.0


def test_the_author_breakdown_covers_everyone_flagged_by_side():
    """"Who is self-serving" is not "who outside PE is self-serving" — PE's own use is most of it.

    Restricting the breakdown to non-PE authors answered a narrower question than the panel asks,
    and hid the comparison that makes the non-PE bars legible: how much of this tooling PE runs
    itself. The flag is what lets one chart carry both without merging them into a single total.
    """
    conn = _seed([
        _mr(1, _ADAM, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
        _mr(2, _ADAM, datetime(2026, 8, 11, 16, 0), datetime(2026, 8, 11, 17, 0)),
        _mr(3, _ADAM, datetime(2026, 8, 12, 16, 0), datetime(2026, 8, 12, 17, 0)),
        _mr(4, _OMAR, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
        _mr(5, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
        _mr(6, _BEN, datetime(2026, 8, 11, 16, 0), datetime(2026, 8, 11, 17, 0)),
    ])
    report = _report(conn)
    assert report["by_author"] == [
        {"name": "Adam", "mrs": 3, "pe": True},
        {"name": "Ben Bonora", "mrs": 2, "pe": False},
        {"name": "Omar", "mrs": 1, "pe": True},
    ], "ordered by volume, and each row says which side it is on"
    # The scorecard's distinct-author count still means non-PE only, or it would stop matching
    # the non-PE headline it sits beside.
    assert report["non_pe"]["authors"] == 1


def test_authors_are_ordered_by_volume_then_name():
    """Tallest bar first, ties broken by name so the chart does not reshuffle between refreshes.

    Omar sorts last alphabetically and first by volume, so this ordering holds under exactly one of
    the two rules.
    """
    conn = _seed(
        [_mr(i, _OMAR, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)) for i in (1, 2, 3)]
        + [_mr(4, _ADAM, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
           _mr(5, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0))])
    ordered = _report(conn)["by_author"]
    assert [a["name"] for a in ordered] == ["Omar", "Adam", "Ben Bonora"]
    assert [a["mrs"] for a in ordered] == [3, 1, 1]


def test_the_floor_count_does_not_double_count_a_re_added_author():
    """An author can sit in TRACKED_MR_AUTHORS and also be re-added through the dashboard.

    Summing the two lists claims a wider net than is actually cast, which overstates the very bound
    the panel exists to be honest about. Seen live: the roster carried `audacy-jeremy.williams` in
    `added` while he was already tracked statically, and the panel reported 4 tracked non-PE authors
    when only 3 distinct people were attributable.
    """
    roster = {"added": {"audacy-jeremy.williams": "Jeremy Williams",   # already in TRACKED
                        "audacy-marc.polidor": "Marc Polidor"}}        # genuinely new
    report = _report(_seed([]), roster=roster)
    assert report["tracked_non_pe_authors"] == 3


def test_the_author_chart_is_capped_and_says_who_fell_off():
    """Past twenty columns the chart stops having a readable shape, so it truncates — and says so.

    Silently showing the top twenty is the failure mode: a chart captioned nothing reads as the whole
    population, and "nobody else is self-serving" is the opposite of what a truncated chart means.
    """
    added = {f"user-{i:02d}": f"User {i:02d}" for i in range(1, 31)}
    rows, mr_id = [], 0
    for author in range(1, 31):
        for _ in range(author):
            mr_id += 1
            rows.append(_mr(mr_id, f"user-{author:02d}", datetime(2026, 8, 10, 16, 0),
                            datetime(2026, 8, 10, 17, 0)))
    report = _report(_seed(rows), roster={"added": added})
    assert len(report["by_author"]) == 20
    assert report["authors_total"] == 30
    assert report["authors_omitted"] == 10
    # The twenty kept are the twenty biggest: authors 30 down to 11, never 1-20 by name.
    assert [a["name"] for a in report["by_author"]] == [f"User {i:02d}" for i in range(30, 10, -1)]
    assert min(a["mrs"] for a in report["by_author"]) == 11


def test_nothing_is_reported_as_omitted_when_everyone_fits():
    """A count of zero omissions must be a real zero, or the caption cries wolf on every small window."""
    conn = _seed([_mr(1, _ADAM, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0)),
                  _mr(2, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0))])
    report = _report(conn)
    assert report["authors_omitted"] == 0
    assert report["authors_total"] == len(report["by_author"]) == 2
