"""WIP weighted by Estimated Size, and the normalization that makes it safe to ship half-covered.

The bug being fixed: `spare = max(0, vel - done) - wip` counted WIP as tickets, so an engineer
holding two XLs read as exactly as free as one holding two Smalls. That was the largest source of
bad routing suggestions on the intake page.

The risk in fixing it is a units mismatch. `vel` and `done` are ticket COUNTS; if `wip` becomes a
weighted sum in some other unit, `spare` silently subtracts apples from oranges. The normalization
is what prevents that, and most of these tests exist to pin it rather than the weighting itself.
"""
from datetime import datetime

import duckdb

from darkstar import intake, store

_NOW = datetime(2026, 9, 18, 12, 0, 0)
_ADAM = "600ece193b1af000697f339d"
_VLAD = "712020:ff85e042-9fc6-4019-9fda-590317ad40a1"


def _issue(key, account_id, size, status="In Progress"):
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype="Story",
        status=status, status_category="indeterminate", priority="Medium", summary=key,
        assignee=None, assignee_account_id=account_id, reporter=None, business_lead=None,
        parent_key=None, created=_NOW, updated=_NOW, resolutiondate=None,
        planned_start=None, target_end=None, labels=[], mr_field_url=None,
        dev_has_pr=None, dev_has_commits=None, estimated_size=size, fetched_at=_NOW)


def _store(issues):
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, issues)
    return conn


def _wip(conn, key):
    return intake.intake_report(conn, _NOW)["roster"][key]["wip"]


# --- the normalization, which is the whole safety argument ---------------------------------------

def test_the_reference_mix_averages_exactly_one_average_ticket():
    """The property every other guarantee here rests on.

    Weighted WIP has to stay in the same units as the count-based velocity that `spare` subtracts it
    from. Scaling the ratios so the observed size mix averages 1.0 is what makes that true — and it
    is also what lets an unsized ticket weigh 1.0 without inventing a second rule.
    """
    mix = intake._REFERENCE_MIX
    average = sum(intake.SIZE_WEIGHTS[size] * n for size, n in mix.items()) / sum(mix.values())
    assert abs(average - 1.0) < 1e-12


def test_the_weights_are_computed_from_the_ratios_and_never_hardcoded():
    """A pinned decimal would silently stop matching the moment a ratio or the mix is edited."""
    for size, ratio in intake._SIZE_RATIOS.items():
        assert abs(intake.SIZE_WEIGHTS[size] - ratio * intake._NORMALISATION) < 1e-12


def test_the_weights_increase_with_size():
    """Monotonicity is the only property the convention actually has to satisfy.

    DEVOPS-10567 established these ratios cannot be a measurement (1 of 7 Large/XL issues was sized
    before work started). Ranking engineers against each other at one instant needs a monotone
    scale, not a calibrated one — so this, and not any particular value, is the contract.
    """
    weights = [intake.SIZE_WEIGHTS[s] for s in ("Small", "Medium", "Large", "XL")]
    assert weights == sorted(weights) and len(set(weights)) == 4


# --- weight_of ------------------------------------------------------------------------------------

def test_an_unsized_ticket_weighs_one_average_ticket():
    """~41% of current WIP is unsized, so this is the common path, not an error case."""
    assert intake.weight_of(None) == 1.0


def test_an_unrecognised_size_degrades_instead_of_raising():
    """The option set lives in Jira, not here. Adding one there must not take the panel down."""
    assert intake.weight_of("Enormous") == intake.UNSIZED_WEIGHT


def test_each_known_size_maps_to_its_own_weight():
    assert {s: intake.weight_of(s) for s in intake._SIZE_RATIOS} == intake.SIZE_WEIGHTS


# --- the behaviour the story is for --------------------------------------------------------------

def test_an_engineer_holding_an_xl_is_busier_than_one_holding_a_small():
    """The actual bug: one ticket each, and the old count said these two were equally free."""
    conn = _store([_issue("DEVOPS-1", _ADAM, "XL"), _issue("DEVOPS-2", _VLAD, "Small")])
    assert _wip(conn, "adam") > _wip(conn, "vlad")


def test_fewer_large_tickets_can_outweigh_more_small_ones():
    """The routing case that motivated the story: 3 tickets can be more load than 5."""
    conn = _store(
        [_issue("DEVOPS-1", _ADAM, "Large"), _issue("DEVOPS-2", _ADAM, "Large"),
         _issue("DEVOPS-3", _ADAM, "Large")]
        + [_issue(f"DEVOPS-1{i}", _VLAD, "Small") for i in range(5)])
    assert _wip(conn, "adam") > _wip(conn, "vlad")


def test_an_all_unsized_roster_reproduces_the_ticket_count_exactly():
    """The regression that makes this shippable at 59% coverage.

    With nothing sized, every ticket weighs 1.0 and the panel must produce byte-identical numbers to
    the count-based version it replaces. If this fails, the change is not backward compatible and
    needs a coverage gate — which is precisely what the normalization exists to avoid.
    """
    conn = _store([_issue(f"DEVOPS-{i}", _ADAM, None) for i in range(1, 5)])
    assert _wip(conn, "adam") == 4.0


def test_a_partially_sized_engineer_counts_both_halves():
    """Coverage is incremental, so mixed rows are the normal state, not a transitional one."""
    conn = _store([_issue("DEVOPS-1", _ADAM, None), _issue("DEVOPS-2", _ADAM, "Large")])
    expected = round(intake.UNSIZED_WEIGHT + intake.SIZE_WEIGHTS["Large"], 1)
    assert _wip(conn, "adam") == expected


def test_only_wip_statuses_are_weighed():
    """A Done ticket carries a size too; counting it would charge an engineer for finished work.

    This is also why retroactive sizing cannot corrupt this number: a size set after a ticket closes
    never enters the sum at all.
    """
    conn = _store([_issue("DEVOPS-1", _ADAM, "XL", status="Done"),
                   _issue("DEVOPS-2", _ADAM, "Small")])
    assert _wip(conn, "adam") == round(intake.SIZE_WEIGHTS["Small"], 1)


def test_an_engineer_with_no_wip_is_zero_not_missing():
    """The client indexes the roster by key and would render undefined for a missing entry."""
    conn = _store([_issue("DEVOPS-1", _ADAM, "Small")])
    assert _wip(conn, "vlad") == 0


# --- unsized-WIP visibility (DEVOPS-10569) -------------------------------------------------------
# The weighting above is only as good as its coverage, and an unsized ticket weighs 1.0 silently.
# These pin the counts the page needs to say so out loud.

def _row(conn, key):
    return intake.intake_report(conn, _NOW)["roster"][key]


def test_unsized_wip_is_counted_per_engineer_not_just_team_wide():
    """A team-level coverage figure hides the row that is actually wrong."""
    conn = _store([_issue("DEVOPS-1", _ADAM, None), _issue("DEVOPS-2", _ADAM, None),
                   _issue("DEVOPS-3", _VLAD, "Small")])
    assert _row(conn, "adam")["unsized"] == 2
    assert _row(conn, "vlad")["unsized"] == 0


def test_the_ticket_count_survives_alongside_the_weight():
    """`wip` is a float once weighted, so the raw count has to be carried separately or the page
    cannot say "3 of 5" — and 3-of-5 is the whole message."""
    conn = _store([_issue("DEVOPS-1", _ADAM, None), _issue("DEVOPS-2", _ADAM, "Large"),
                   _issue("DEVOPS-3", _ADAM, "XL")])
    row = _row(conn, "adam")
    assert row["tickets"] == 3 and row["unsized"] == 1


def test_a_fully_sized_engineer_reports_zero_unsized():
    """Zero is what makes the callout disappear; a missing key would render undefined."""
    conn = _store([_issue("DEVOPS-1", _ADAM, "Medium")])
    assert _row(conn, "adam")["unsized"] == 0


def test_an_unrecognised_size_counts_as_unsized():
    """It is priced at the fallback weight, so the page must not claim that row is informed.

    Consistent with weight_of: a size added in Jira that darkstar does not know degrades to the
    average-ticket weight, and this is the disclosure that goes with it.
    """
    conn = _store([_issue("DEVOPS-1", _ADAM, "Enormous")])
    row = _row(conn, "adam")
    assert row["unsized"] == 1 and row["tickets"] == 1
    assert row["wip"] == round(intake.UNSIZED_WEIGHT, 1)


def test_done_tickets_are_excluded_from_the_coverage_figures_too():
    """Coverage describes current load. Closed work is not load and not this row's problem."""
    conn = _store([_issue("DEVOPS-1", _ADAM, None, status="Done"),
                   _issue("DEVOPS-2", _ADAM, "Small")])
    row = _row(conn, "adam")
    assert row["tickets"] == 1 and row["unsized"] == 0


def test_an_engineer_with_no_wip_reports_zero_rather_than_missing():
    conn = _store([_issue("DEVOPS-1", _ADAM, "Small")])
    assert _row(conn, "vlad")["unsized"] == 0 and _row(conn, "vlad")["tickets"] == 0


# --- per-engineer size mix (DEVOPS-10570) --------------------------------------------------------
# The weighted total says a row moved; the mix says why. These pin the shape the gauge reads.

def test_the_mix_breaks_wip_down_by_size():
    """Without this the page can show that a row is heavy but not what is making it heavy."""
    conn = _store([_issue("DEVOPS-1", _ADAM, "XL"), _issue("DEVOPS-2", _ADAM, "Medium"),
                   _issue("DEVOPS-3", _ADAM, "Medium")])
    assert _row(conn, "adam")["mix"] == {"XL": 1, "Medium": 2}


def test_unsized_is_its_own_bucket_never_folded_into_a_size():
    """"No size recorded" is a different statement from "small".

    Folding them would let a coverage gap read as a light workload — the precise misreading the
    unsized callout exists to prevent, so the mix must not reintroduce it.
    """
    conn = _store([_issue("DEVOPS-1", _ADAM, None), _issue("DEVOPS-2", _ADAM, "Small")])
    assert _row(conn, "adam")["mix"] == {intake.UNSIZED_LABEL: 1, "Small": 1}


def test_the_mix_counts_reconcile_with_the_weighted_total():
    """The gauge sizes each segment from these counts, so a drift here silently mis-draws the bar."""
    conn = _store([_issue("DEVOPS-1", _ADAM, "Large"), _issue("DEVOPS-2", _ADAM, "Large"),
                   _issue("DEVOPS-3", _ADAM, None)])
    row = _row(conn, "adam")
    rebuilt = sum(n * intake.weight_of(None if s == intake.UNSIZED_LABEL else s)
                  for s, n in row["mix"].items())
    assert round(rebuilt, 1) == row["wip"]
    assert sum(row["mix"].values()) == row["tickets"]


def test_an_engineer_with_no_wip_has_an_empty_mix_not_a_missing_key():
    """The client iterates it directly; undefined would throw rather than render nothing."""
    conn = _store([_issue("DEVOPS-1", _ADAM, "Small")])
    assert _row(conn, "vlad")["mix"] == {}


def test_the_weight_table_is_published_so_the_client_need_not_restate_it():
    """The gauge sizes segments by weight. A second copy in the dashboard would drift the moment
    the convention is retuned, so the payload carries the one table."""
    conn = _store([_issue("DEVOPS-1", _ADAM, "Small")])
    report = intake.intake_report(conn, _NOW)
    assert report["weights"] == intake.SIZE_WEIGHTS
    assert report["unsized_label"] == intake.UNSIZED_LABEL
