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
from darkstar.roster import GITLAB_USERNAMES, NON_HUMAN_GROUP_MEMBERS, ROSTER

_ADAM = "600ece193b1af000697f339d"
_OMAR = "712020:58e4121c-dadd-4c34-99a9-92dc31ee039b"
# Two ordinary non-PE contributors. Nothing special about them any more: with the ingest keeping
# every author, an outside contributor is just a GitLab username the store has never been told about.
_BEN = "audacy-ben.bonora"
_JEREMY = "audacy-jeremy.williams"
_NOW = datetime(2026, 8, 19, 12, 0, 0)
_SINCE = datetime(2026, 8, 3, 7, 0)


def _mr(id, account_id, opened, merged, self_service=True, author_name=None):
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id=account_id,
        title=f"MR {id}", opened_at=opened, merged_at=merged,
        labels=["pe:iac-request"] if self_service else [],
        web_url="u", merged_by="", fetched_at=_NOW, events_fetched_at=_NOW, description="",
        source_branch="", pipelines_fetched_at=_NOW, author_name=author_name)


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
    # And the test is structural, not a list of known outsiders: every PE username maps to an
    # accountId in ROSTER, and a bare GitLab username can never be one.
    for account_id in GITLAB_USERNAMES.values():
        assert account_id in ROSTER


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


def test_figures_the_store_cannot_support_are_named_not_approximated():
    """Merge rate and approver identity both need an ingest change; neither may be guessed at.

    The scorecard this panel came from carried both. Rendering a plausible substitute in their place
    is how a dashboard ends up quietly disagreeing with the audit it was built to reproduce.
    """
    report = _report(_seed([]))
    assert set(report["unmeasurable"]) == {"merge_rate", "approver_identity"}
    assert "state=merged" in report["unmeasurable"]["merge_rate"]
    assert "not who" in report["unmeasurable"]["approver_identity"]


def test_an_author_the_roster_has_never_heard_of_is_counted_and_named():
    """The census case. Before, an unrostered author was set aside as unattributable; now they are
    simply a non-PE author, named from the display name GitLab reported on their merge request.

    This is the whole point of the change: adoption cannot be read off a list of people somebody
    remembered to add, because the people worth discovering are exactly the ones not on it.
    """
    conn = _seed([_mr(1, "audacy-marc.polidor", datetime(2026, 8, 10, 16, 0),
                      datetime(2026, 8, 10, 17, 0), author_name="Marc Polidor")])
    report = _report(conn)
    assert report["total"] == 1
    assert report["non_pe"]["mrs"] == 1
    assert report["by_author"] == [{"name": "Marc Polidor", "mrs": 1, "pe": False}]


def test_two_authors_sharing_a_display_name_stay_separate():
    """Keyed on the account, not the name. Merging them was harmless only while the population was
    a curated list of sixteen; over an open population it silently fuses two people into one bar.
    """
    conn = _seed([
        _mr(1, "user-a", datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0), author_name="Alex Kim"),
        _mr(2, "user-a", datetime(2026, 8, 11, 16, 0), datetime(2026, 8, 11, 17, 0), author_name="Alex Kim"),
        _mr(3, "user-b", datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0), author_name="Alex Kim"),
    ])
    report = _report(conn)
    assert len(report["by_author"]) == 2, "one bar per person, not one per name"
    assert sorted(a["mrs"] for a in report["by_author"]) == [1, 2]


def test_service_accounts_are_excluded_and_counted():
    """Bots carry agent footers by their nature, so they enter the self-service population the
    moment the ingest stops filtering authors -- and `DevOps-agent` topping an adoption chart is
    both wrong and embarrassing. Excluded at report time so the call stays reversible, and reported
    rather than silently dropped.
    """
    bot = sorted(NON_HUMAN_GROUP_MEMBERS)[0]
    conn = _seed([
        _mr(1, bot, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0), author_name="a bot"),
        _mr(2, _BEN, datetime(2026, 8, 10, 16, 0), datetime(2026, 8, 10, 17, 0),
            author_name="Ben Bonora"),
    ])
    report = _report(conn)
    assert report["total"] == 1
    assert report["robots"] == 1
    assert [a["name"] for a in report["by_author"]] == ["Ben Bonora"]


def test_an_author_with_no_display_name_anywhere_is_labelled_by_username():
    """Better a username on the chart than a blank bar or a crash.

    Some GitLab accounts have no display name set — two PE members are like this today — so the
    fallback is a real case, not defensive padding.
    """
    conn = _seed([_mr(1, "audacy-zack.amadi", datetime(2026, 8, 10, 16, 0),
                      datetime(2026, 8, 10, 17, 0), author_name=None)])
    assert [a["name"] for a in _report(conn)["by_author"]] == ["audacy-zack.amadi"]
