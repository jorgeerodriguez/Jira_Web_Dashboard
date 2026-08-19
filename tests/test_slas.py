from datetime import datetime
import duckdb
from darkstar import store, slas
from darkstar.metrics import SELF_SERVICE_EPOCH


def _report(conn, now, grain="week"):
    """slas_report with the view's own default window — the page can override it, tests need not.

    Grain is pinned to weekly here so a test's assertions do not move when the default window's width
    changes; the grain-selection rules are covered in test_business_hours.py.
    """
    return slas.slas_report(conn, now, slas.default_window_start(now), None, grain)


def _issue(key, labels, created, status="Done", issuetype="Story"):
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype=issuetype,
        status=status, status_category=("done" if status in ("Done", "Will Not Do") else "indeterminate"),
        priority="Medium", summary=key, assignee=None, assignee_account_id=None,
        reporter=None, business_lead=None, parent_key=None,
        created=created, updated=created, resolutiondate=None,
        planned_start=None, target_end=None, labels=labels,
        fetched_at=datetime(2026, 7, 28, 0, 0, 0))


def _mr(id, key, labels, opened, merged, description=""):
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id="a",
        title=f"{key} do a thing", opened_at=opened, merged_at=merged, labels=labels,
        web_url="u", merged_by="", fetched_at=datetime(2026, 7, 28, 0, 0, 0), events_fetched_at=datetime(2026, 7, 28, 0, 0, 0), description=description)


def _seed():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    # AI tf-module (MR label pe:tf-module). Created Mon 02:00 and done Tue 02:00 Pacific: 24 calendar
    # hours, but only Monday's 08:00-17:00 overlaps the business day, so 9 business hours. Same for
    # its MR's open→merge.
    store.upsert_issues(conn, [_issue("DEVOPS-1", ["DevOps", "pe-iac-request", "pe-tf-module"],
                                       datetime(2026, 7, 20, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-1"],
        [store.TransitionRow(key="DEVOPS-1", to_status="Done", changed_at=datetime(2026, 7, 21, 9, 0, 0), seq=0)])
    # AI k8s-request: Jira carries only pe-iac-request, but the MR label pe:k8s-request separates it.
    store.upsert_issues(conn, [_issue("DEVOPS-4", ["DevOps", "pe-iac-request"],
                                       datetime(2026, 7, 20, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-4"],
        [store.TransitionRow(key="DEVOPS-4", to_status="Done", changed_at=datetime(2026, 7, 20, 21, 0, 0), seq=0)])
    # AI troubleshoot, abandoned. DEVOPS spells this "Will Not Do", NOT "Won't Do" — the real
    # status name, so this fixture actually exercises the abandon path. NO MR → Jira label fallback.
    store.upsert_issues(conn, [_issue("DEVOPS-2", ["DevOps", "pe-troubleshoot"],
                                       datetime(2026, 7, 20, 9, 0, 0), status="Will Not Do")])
    store.replace_transitions(conn, ["DEVOPS-2"],
        [store.TransitionRow(key="DEVOPS-2", to_status="Will Not Do", changed_at=datetime(2026, 7, 20, 12, 0, 0), seq=0)])
    # Human issue (no pe-* watermark) → excluded from v1 counts
    store.upsert_issues(conn, [_issue("DEVOPS-3", ["DevOps"],
                                       datetime(2026, 7, 20, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-3"],
        [store.TransitionRow(key="DEVOPS-3", to_status="Done", changed_at=datetime(2026, 7, 22, 9, 0, 0), seq=0)])
    store.upsert_merge_requests(conn, [
        _mr(1, "DEVOPS-1", ["pe:tf-module"], datetime(2026, 7, 20, 9, 0, 0), datetime(2026, 7, 21, 9, 0, 0)),
        _mr(4, "DEVOPS-4", ["pe:k8s-request"], datetime(2026, 7, 20, 9, 0, 0), datetime(2026, 7, 20, 21, 0, 0)),
    ])
    return conn


def test_bucketing_by_mr_label_separates_k8s_from_iac():
    r = _report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    by = {(b["audience"], b["type"]): b for b in r["buckets"]}
    assert by[("ai", "tf-module")]["volume"] == 1
    assert by[("ai", "k8s-request")]["volume"] == 1     # separated via the MR label (Option B win)
    assert by[("ai", "troubleshoot")]["volume"] == 1    # no MR → Jira fallback
    assert all(b["audience"] == "ai" for b in r["buckets"])   # DEVOPS-3 (no pe-*) excluded


def test_sla_compliance_uses_business_hour_delivery_turnaround():
    """Turnaround is charged in business hours, so the 16 overnight hours are not held against it.

    Billing the request for the night nobody was working is what made these medians read roughly
    3x too high; an SLA the team is measured on must only count hours it could have acted in.
    """
    r = _report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    tm = next(b for b in r["buckets"] if b["type"] == "tf-module")
    assert tm["turnaround_hours_median"] == 9.0    # 24 calendar hours, 9 of them in the business day


def test_each_sla_tier_is_scored_independently():
    """A bimodal distribution needs two verdicts: the fast path and the tail fail separately.

    tf-module's targets are p50 2h / p90 8h. This request takes 5 business hours (09:00→14:00
    Pacific, one working day), so it blows the p50 target while comfortably meeting the p90 — a
    split a single blended "% under one target" score could not express.
    """
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-50", ["DevOps", "pe-iac-request", "pe-tf-module"],
                                      datetime(2026, 7, 20, 16, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-50"], [store.TransitionRow(
        key="DEVOPS-50", to_status="Done", changed_at=datetime(2026, 7, 20, 21, 0, 0), seq=0)])
    tm = next(b for b in _report(conn, datetime(2026, 7, 28, 12, 0, 0))["buckets"]
              if b["type"] == "tf-module")
    assert (tm["target_p50_hours"], tm["target_p90_hours"]) == (2, 8)
    assert tm["turnaround_hours_median"] == 5.0 and tm["meets_p50"] is False
    assert tm["turnaround_hours_p90"] == 5.0 and tm["meets_p90"] is True
    assert tm["within_target_pct"] == 0.0          # 0 of 1 request inside the 2h fast path


def test_tiers_are_none_rather_than_false_when_nothing_closed():
    """An open-only bucket must not read as an SLA breach — no data is not a failure."""
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-30", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 7, 20, 9, 0, 0), status="In Progress")])
    store.replace_transitions(conn, ["DEVOPS-30"], [])
    bucket = _report(conn, datetime(2026, 7, 28, 12, 0, 0))["buckets"][0]
    assert bucket["volume"] == 1 and bucket["closed"] == 0
    assert bucket["meets_p50"] is None and bucket["meets_p90"] is None
    assert bucket["within_target_pct"] is None


def test_sla_window_is_three_months_not_six():
    """Six months pools in the Apr/May learning period, when p50 was 100-370 business hours."""
    assert slas._WINDOW_MONTHS == 3
    # Aug 2026 less three months opens 2026-06-01, so a May request is out of scope.
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-31", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 5, 20, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-31"], [store.TransitionRow(
        key="DEVOPS-31", to_status="Done", changed_at=datetime(2026, 5, 21, 15, 0, 0), seq=0)])
    assert _report(conn, datetime(2026, 8, 18, 12, 0, 0))["buckets"] == []


def test_agent_success_rate_counts_abandoned_as_failure():
    """DEVOPS names this status "Will Not Do". Matching only "Won't Do" pinned the rate at 100%."""
    r = _report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    # 3 AI terminal issues: DEVOPS-1 Done, DEVOPS-4 Done, DEVOPS-2 Will Not Do → 2/3 = 66.7%
    assert r["agent_success"]["terminal"] == 3
    assert r["agent_success"]["succeeded"] == 2
    assert r["agent_success"]["rate_pct"] == 66.7


def test_review_turnaround_from_linked_mr():
    """Review turnaround uses the same business-hour clock as delivery, so the two are comparable."""
    r = _report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    tm = next(b for b in r["buckets"] if b["type"] == "tf-module")
    assert tm["review_hours_median"] == 9.0


def test_multiple_mrs_per_key_pick_is_deterministic():
    """Two MRs on one issue key → the lowest-id MR wins deterministically (ORDER BY id),
    regardless of insertion order, so the reported review turnaround is stable across runs."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, [_issue("DEVOPS-1", ["DevOps", "pe-iac-request", "pe-tf-module"],
                                       datetime(2026, 7, 20, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-1"],
        [store.TransitionRow(key="DEVOPS-1", to_status="Done", changed_at=datetime(2026, 7, 21, 9, 0, 0), seq=0)])
    # insert in reverse-id order; id=5 has 6 business hours, id=1 has 9. Lowest id (1) must win.
    store.upsert_merge_requests(conn, [
        _mr(5, "DEVOPS-1", ["pe:tf-module"], datetime(2026, 7, 20, 9, 0, 0), datetime(2026, 7, 20, 21, 0, 0)),
        _mr(1, "DEVOPS-1", ["pe:tf-module"], datetime(2026, 7, 20, 9, 0, 0), datetime(2026, 7, 21, 9, 0, 0)),
    ])
    tm = next(b for b in _report(conn, datetime(2026, 7, 28, 12, 0, 0))["buckets"]
              if b["type"] == "tf-module")
    assert tm["review_hours_median"] == 9.0  # id=1's 9h, not id=5's 6h


def test_reopened_issue_not_counted_as_terminal_success():
    """An issue with a Done transition in history but currently In Progress (reopened) is not
    terminal, so it must not inflate the agent-success counts."""
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    store.upsert_issues(conn, [_issue("DEVOPS-7", ["DevOps", "pe-troubleshoot"],
                                       datetime(2026, 7, 20, 9, 0, 0), status="In Progress")])
    store.replace_transitions(conn, ["DEVOPS-7"], [
        store.TransitionRow(key="DEVOPS-7", to_status="Done", changed_at=datetime(2026, 7, 20, 12, 0, 0), seq=0),
        store.TransitionRow(key="DEVOPS-7", to_status="In Progress", changed_at=datetime(2026, 7, 21, 12, 0, 0), seq=1),
    ])
    a = _report(conn, datetime(2026, 7, 28, 12, 0, 0))["agent_success"]
    assert a["terminal"] == 0
    assert a["succeeded"] == 0
    assert a["rate_pct"] is None


# --- multi-signal AI detection -------------------------------------------------------------
# The pe-* Jira labels only began 2026-05-27, two months after the first agent-footered MR
# (2026-03-18). Detecting on labels alone therefore misses the entire March-May era, so the
# MR description footer and the ai-generated label have to count on their own.

_FOOTER = "🤖 Generated with Claude Code via /audacy-platform-engineering:iac-request"


def _done_issue(conn, key, labels, status="Done"):
    store.upsert_issues(conn, [_issue(key, labels, datetime(2026, 7, 20, 9, 0, 0), status=status)])
    store.replace_transitions(conn, [key], [store.TransitionRow(
        key=key, to_status=status, changed_at=datetime(2026, 7, 20, 15, 0, 0), seq=0)])


def _fresh():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    return conn


def test_a_footer_alone_is_ai_generated_but_not_self_service():
    """The correction that matters: a footer means the *code* was agent-written, nothing more.

    96 issues created since the labels existed are footer-only, and they are ordinary human-filed
    tickets PE delivered with the agent. Counting them as self-service put the share card at 100%
    when the true figure was around 42%.
    """
    conn = _fresh()
    _done_issue(conn, "DEVOPS-20", ["DevOps"])            # human-filed: no label anywhere
    store.upsert_merge_requests(conn, [_mr(20, "DEVOPS-20", [], datetime(2026, 7, 20, 9, 0, 0),
                                           datetime(2026, 7, 20, 15, 0, 0), description=_FOOTER)])
    report = _report(conn, datetime(2026, 7, 28, 12, 0, 0))
    assert report["buckets"] == []                        # not a self-service request
    head = report["headline"]
    assert head["share_delivered"] == 0                   # ...so it is not in the self-service share
    assert head["ai_delivered"] == 1                      # ...but it IS AI-generated
    assert head["share_total"] == 1                       # and it counts in the delivery denominator


def test_an_mr_skill_label_does_qualify_as_self_service():
    """pe:<skill> is stamped by the skill itself, so it means the request came through one."""
    conn = _fresh()
    _done_issue(conn, "DEVOPS-26", ["DevOps"])
    store.upsert_merge_requests(conn, [_mr(26, "DEVOPS-26", ["pe:iac-request"],
                                           datetime(2026, 7, 20, 9, 0, 0),
                                           datetime(2026, 7, 20, 15, 0, 0))])
    report = _report(conn, datetime(2026, 7, 28, 12, 0, 0))
    assert report["buckets"][0]["type"] == "iac-request"
    assert report["headline"]["share_delivered"] == 1


def test_ai_generated_jira_label_alone_is_enough():
    """`ai-generated` is stamped on 56 of the 135 self-service issues; it must count by itself."""
    conn = _fresh()
    _done_issue(conn, "DEVOPS-21", ["DevOps", "ai-generated"])
    report = _report(conn, datetime(2026, 7, 28, 12, 0, 0))
    assert sum(b["volume"] for b in report["buckets"]) == 1
    assert report["buckets"][0]["type"] == "other"        # no skill signal anywhere, so unbucketed


def test_footer_skill_name_is_normalised_to_the_bucket():
    """The skill is `tf-module-request` but the bucket and label are `tf-module`.

    The footer still *buckets* a self-service request — it names the skill reliably — it just no
    longer *qualifies* one, so this issue carries a Jira label to get into the population.
    """
    conn = _fresh()
    _done_issue(conn, "DEVOPS-22", ["DevOps", "ai-generated"])
    store.upsert_merge_requests(conn, [_mr(
        22, "DEVOPS-22", [], datetime(2026, 7, 20, 9, 0, 0), datetime(2026, 7, 20, 15, 0, 0),
        description=":robot: Generated with Claude Code via /tf-module-request")])
    report = _report(conn, datetime(2026, 7, 28, 12, 0, 0))
    assert report["buckets"][0]["type"] == "tf-module"


def test_malformed_json_array_label_still_buckets():
    """15 crawled MRs carry the label as literal `["pe:iac-request"]` text — a producer quoting bug."""
    conn = _fresh()
    _done_issue(conn, "DEVOPS-23", ["DevOps"])
    store.upsert_merge_requests(conn, [_mr(23, "DEVOPS-23", ['["pe:iac-request"]'],
                                           datetime(2026, 7, 20, 9, 0, 0),
                                           datetime(2026, 7, 20, 15, 0, 0))])
    report = _report(conn, datetime(2026, 7, 28, 12, 0, 0))
    assert report["buckets"][0]["type"] == "iac-request"


def test_genuinely_human_work_is_still_excluded():
    """Broadening detection must not sweep in ordinary hand-written tickets and MRs."""
    conn = _fresh()
    _done_issue(conn, "DEVOPS-24", ["DevOps"])
    store.upsert_merge_requests(conn, [_mr(
        24, "DEVOPS-24", [], datetime(2026, 7, 20, 9, 0, 0), datetime(2026, 7, 20, 15, 0, 0),
        description="Bumps the chart version. See CLAUDE.md for the commit convention.")])
    report = _report(conn, datetime(2026, 7, 28, 12, 0, 0))
    assert report["buckets"] == []          # a bare "CLAUDE.md" mention is not an agent footer
    assert report["agent_success"]["terminal"] == 0


def test_requests_before_the_self_service_epoch_are_excluded():
    """Nothing before 2026-03 can be a self-service request, so it must not dilute the rates."""
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-25", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 1, 5, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-25"], [store.TransitionRow(
        key="DEVOPS-25", to_status="Done", changed_at=datetime(2026, 1, 6, 15, 0, 0), seq=0)])
    assert _report(conn, datetime(2026, 7, 28, 12, 0, 0))["buckets"] == []


def test_container_issue_types_are_excluded_from_turnaround():
    """A Feature is a container for requests, not a request; its lifetime wrecks the median.

    leadtime/velocity/intake all scope to metrics.DELIVERY_TYPES for this reason. On the live data
    the unscoped population pulled the iac-request p50 from 6.6h up to 10.6h.
    """
    conn = _fresh()
    # Story: 10:00 -> 16:00 Denver on a Monday, i.e. 6 hours fully inside the business day.
    store.upsert_issues(conn, [_issue("DEVOPS-40", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 7, 20, 16, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-40"], [store.TransitionRow(
        key="DEVOPS-40", to_status="Done", changed_at=datetime(2026, 7, 20, 22, 0, 0), seq=0)])
    # Feature spanning three weeks — ~120 business hours if it were wrongly counted as a request.
    store.upsert_issues(conn, [_issue("DEVOPS-41", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 7, 1, 16, 0, 0), issuetype="Feature")])
    store.replace_transitions(conn, ["DEVOPS-41"], [store.TransitionRow(
        key="DEVOPS-41", to_status="Done", changed_at=datetime(2026, 7, 24, 22, 0, 0), seq=0)])
    bucket = _report(conn, datetime(2026, 7, 28, 12, 0, 0))["buckets"][0]
    assert bucket["volume"] == 1                      # the Feature is not a request
    assert bucket["turnaround_hours_median"] == 6.0   # not dragged toward the Feature's ~120h


def test_last_month_is_not_reported_as_zero_when_it_is_outside_the_window():
    """"108 this month, up from 0" was a fact about the lookback, not about the team.

    created_by_month only counts issues inside the window, so a lookback starting on the 1st of
    this month leaves last month empty by construction - and the card rendered that as growth.
    """
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-60", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 8, 5, 16, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-60"], [store.TransitionRow(
        key="DEVOPS-60", to_status="Done", changed_at=datetime(2026, 8, 5, 21, 0, 0), seq=0)])
    now = datetime(2026, 8, 18, 12, 0, 0)

    # Window opens 2026-08-01: July is not in scope, so there is nothing to compare against.
    cut = slas.window_start(now, 1, SELF_SERVICE_EPOCH)
    assert slas.slas_report(conn, now, cut)["headline"]["requests_prev_month"] is None

    # Window opens 2026-06-01: July is fully covered, so a real (here zero) count is honest.
    cut = slas.window_start(now, 3, SELF_SERVICE_EPOCH)
    assert slas.slas_report(conn, now, cut)["headline"]["requests_prev_month"] == 0


def test_this_month_is_still_reported_either_way():
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-61", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 8, 5, 16, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-61"], [])
    now = datetime(2026, 8, 18, 12, 0, 0)
    head = slas.slas_report(conn, now, slas.window_start(now, 1, SELF_SERVICE_EPOCH))["headline"]
    assert head["requests_this_month"] == 1


def test_turnaround_groups_by_the_period_a_request_arrived():
    """A blended figure over the window libels current performance while the team improves fast.

    Weekly rather than monthly because a month is too coarse to see a change land: four weeks inside
    one month routinely diverge while the team is still improving.
    """
    conn = _fresh()
    # July: one slow request (created 09:00, done 16:00 next working day => 9h + 7h)
    store.upsert_issues(conn, [_issue("DEVOPS-70", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 7, 20, 16, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-70"], [store.TransitionRow(
        key="DEVOPS-70", to_status="Done", changed_at=datetime(2026, 7, 21, 23, 0, 0), seq=0)])
    # August: two fast ones
    for key, day in (("DEVOPS-71", 5), ("DEVOPS-72", 6)):
        store.upsert_issues(conn, [_issue(key, ["DevOps", "pe-iac-request"],
                                          datetime(2026, 8, day, 16, 0, 0))])
        store.replace_transitions(conn, [key], [store.TransitionRow(
            key=key, to_status="Done", changed_at=datetime(2026, 8, day, 18, 0, 0), seq=0)])

    rows = _report(conn, datetime(2026, 8, 18, 12, 0, 0))["turnaround"]
    # Mon 2026-07-20 and Mon 2026-08-03, labelled by the Monday commencing each week.
    assert [r["period"] for r in rows] == ["2026-07-20", "2026-08-03"]   # ascending
    assert rows[0]["delivered"] == 1 and rows[0]["within_day_pct"] == 0
    assert rows[1]["delivered"] == 2 and rows[1]["p50_hours"] == 2.0
    assert rows[1]["within_day_pct"] == 100        # both inside a 9h working day
    # Both cohorts closed, so nothing is still settling.
    assert [r["created"] for r in rows] == [1, 2]


def test_turnaround_is_empty_when_nothing_delivered():
    assert _report(_fresh(), datetime(2026, 8, 18, 12, 0, 0))["turnaround"] == []


def test_turnaround_no_longer_reports_a_rest_of_pe_ratio():
    """The comparison was removed because the data does not support it.

    Head to head on live June data, abandoned excluded from both arms, self-service ran 15.9h p50
    against 37.9h but 174.8h p90 against 144.0h -- better at the median, worse in the tail, and
    Mann-Whitney z=+1.44 at n=26, which is not significant. Printing a monthly multiple would read
    as a finding the sample cannot carry, so these keys must stay gone.
    """
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-80", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 8, 5, 16, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-80"], [store.TransitionRow(
        key="DEVOPS-80", to_status="Done", changed_at=datetime(2026, 8, 5, 18, 0, 0), seq=0)])
    store.upsert_issues(conn, [_issue("DEVOPS-81", ["DevOps"], datetime(2026, 8, 6, 16, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-81"], [store.TransitionRow(
        key="DEVOPS-81", to_status="Done", changed_at=datetime(2026, 8, 6, 22, 0, 0), seq=0)])

    row = _report(conn, datetime(2026, 8, 18, 12, 0, 0))["turnaround"][0]
    assert row["delivered"] == 1 and row["p50_hours"] == 2.0
    for gone in ("other_delivered", "other_p50_hours", "faster_by"):
        assert gone not in row


def test_origin_counts_one_request_once_however_many_merge_requests_it_took():
    """The unit is the request, not the branch.

    One request routinely spawns several merge requests -- July ran 643 MRs against 117 distinct
    tickets, one of them spread across 19. Counting MRs answers a question about branches while
    looking like a question about demand, and it put a 645 on the page next to a ~200 ticket count.
    """
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-80", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 8, 5, 16, 0, 0))])
    store.upsert_merge_requests(conn, [
        _mr(80, "DEVOPS-80", [], datetime(2026, 8, 5, 16, 0, 0), datetime(2026, 8, 5, 17, 0, 0)),
        _mr(81, "DEVOPS-80", [], datetime(2026, 8, 5, 17, 0, 0), datetime(2026, 8, 5, 18, 0, 0)),
        _mr(82, "DEVOPS-80", [], datetime(2026, 8, 6, 16, 0, 0), datetime(2026, 8, 6, 17, 0, 0)),
    ])
    row = _report(conn, datetime(2026, 8, 18, 12, 0, 0))["origin"][0]
    assert row["agent"] == 1 and row["total"] == 1, "three MRs, one request"


def test_origin_splits_by_how_the_request_was_created():
    """Skill-filed against human-filed, which is the whole question the panel answers."""
    conn = _fresh()
    store.upsert_issues(conn, [
        _issue("DEVOPS-83", ["DevOps", "pe-iac-request"], datetime(2026, 8, 5, 16, 0, 0)),
        _issue("DEVOPS-84", ["DevOps"], datetime(2026, 8, 5, 17, 0, 0)),
        _issue("DEVOPS-85", ["DevOps"], datetime(2026, 8, 6, 16, 0, 0)),
    ])
    row = _report(conn, datetime(2026, 8, 18, 12, 0, 0))["origin"][0]
    assert (row["agent"], row["human"], row["total"]) == (1, 2, 3)
    assert row["agent_share_pct"] == 33


def test_a_merge_request_label_still_identifies_a_skill_filed_request():
    """Reading an MR for the signal is not the same as counting it.

    Some skill-filed tickets never got the Jira watermark; the pe:* label on the merge request the
    skill opened is the only evidence. Dropping merge requests from the COUNT must not drop them as
    a classification source, or those requests silently move to the human column.
    """
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-86", ["DevOps"], datetime(2026, 8, 5, 16, 0, 0))])
    store.upsert_merge_requests(conn, [
        _mr(86, "DEVOPS-86", ["pe:iac-request"],
            datetime(2026, 8, 5, 16, 0, 0), datetime(2026, 8, 5, 17, 0, 0))])
    row = _report(conn, datetime(2026, 8, 18, 12, 0, 0))["origin"][0]
    assert row["agent"] == 1 and row["human"] == 0


def test_a_bounded_window_still_reads_merge_requests_that_landed_after_it():
    """`until` must bound what is COUNTED, not what is READ, or classification degrades silently.

    A request created inside a "last month" window whose merge request landed after the window
    closed would lose the pe:* signal and be counted as human-filed -- the panel would understate
    self-service exactly for the most recent work.
    """
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-87", ["DevOps"], datetime(2026, 7, 28, 16, 0, 0))])
    store.upsert_merge_requests(conn, [
        _mr(87, "DEVOPS-87", ["pe:iac-request"],
            datetime(2026, 8, 3, 16, 0, 0), datetime(2026, 8, 3, 17, 0, 0))])
    report = slas.slas_report(conn, datetime(2026, 8, 19, 12, 0, 0),
                              datetime(2026, 7, 1, 7, 0, 0), datetime(2026, 8, 1, 7, 0, 0))
    assert report["origin"][0]["agent"] == 1, "the August MR still classifies the July request"


def test_origin_excludes_a_request_created_on_the_upper_bound():
    """`until` is exclusive, so two adjacent ranges cannot double count a request."""
    conn = _fresh()
    store.upsert_issues(conn, [
        _issue("DEVOPS-88", ["DevOps", "pe-iac-request"], datetime(2026, 7, 31, 16, 0, 0)),
        # created exactly 2026-08-01 00:00 Pacific, the bound itself
        _issue("DEVOPS-89", ["DevOps", "pe-iac-request"], datetime(2026, 8, 1, 7, 0, 0)),
    ])
    report = slas.slas_report(conn, datetime(2026, 8, 19, 12, 0, 0),
                              datetime(2026, 7, 1, 7, 0, 0), datetime(2026, 8, 1, 7, 0, 0))
    assert sum(r["total"] for r in report["origin"]) == 1


def test_both_edges_of_a_window_are_flagged_partial_not_just_the_current_week():
    """A lookback starting mid-week holds a part-week at each end; unflagged, both read as dips.

    "Last 30 days" almost never starts on a Monday, and a bounded window rarely ends on a Sunday.
    """
    conn = _fresh()
    store.upsert_issues(conn, [
        # week commencing Mon 2026-07-06, but the window opens Wed the 8th
        _issue("DEVOPS-90", ["DevOps", "pe-iac-request"], datetime(2026, 7, 9, 16, 0, 0)),
        # a whole week inside the window
        _issue("DEVOPS-91", ["DevOps", "pe-iac-request"], datetime(2026, 7, 15, 16, 0, 0)),
        # week commencing Mon 2026-07-20, but the window closes Wed the 22nd
        _issue("DEVOPS-92", ["DevOps", "pe-iac-request"], datetime(2026, 7, 21, 16, 0, 0)),
    ])
    rows = {r["period"]: r for r in slas.slas_report(
        conn, datetime(2026, 8, 19, 12, 0, 0),
        datetime(2026, 7, 8, 7, 0, 0), datetime(2026, 7, 22, 7, 0, 0), "week")["origin"]}
    assert rows["2026-07-06"]["partial"] is True, "window opened mid-week"
    assert rows["2026-07-13"]["partial"] is False, "a complete week inside the window"
    assert rows["2026-07-20"]["partial"] is True, "window closed mid-week"


def test_origin_is_empty_rather_than_a_row_of_zeroes_with_no_requests():
    """A synthesised 0-of-0 row reads as a week nobody filed anything, not as nothing crawled."""
    assert _report(_fresh(), datetime(2026, 8, 18, 12, 0, 0))["origin"] == []


def test_a_long_window_groups_by_month_instead_of_growing_the_table():
    """A 90-day lookback in weekly buckets is 14 rows — a list to scroll, not a trend to read."""
    conn = _fresh()
    for day in (10, 20, 30):                      # June, spread across three weeks
        store.upsert_issues(conn, [_issue(f"DEVOPS-6{day}", ["DevOps", "pe-iac-request"],
                                          datetime(2026, 6, day, 16, 0, 0))])
    for day in (5, 15):                           # July, two more weeks
        store.upsert_issues(conn, [_issue(f"DEVOPS-7{day}", ["DevOps", "pe-iac-request"],
                                          datetime(2026, 7, day, 16, 0, 0))])
    report = slas.slas_report(conn, datetime(2026, 8, 19, 12, 0, 0), datetime(2026, 5, 21, 7, 0, 0))
    assert report["grain"] == "month"
    assert [r["period"] for r in report["origin"]] == ["2026-06-01", "2026-07-01"]
    assert [r["total"] for r in report["origin"]] == [3, 2], "five weeks fold into two months"


def test_a_short_window_groups_by_day_instead_of_one_meaningless_row():
    """A week-long lookback in weekly buckets is a single row that says nothing about the week."""
    conn = _fresh()
    for day in (11, 12):
        store.upsert_issues(conn, [_issue(f"DEVOPS-8{day}", ["DevOps", "pe-iac-request"],
                                          datetime(2026, 8, day, 16, 0, 0))])
    report = slas.slas_report(conn, datetime(2026, 8, 19, 12, 0, 0),
                              datetime(2026, 8, 10, 7, 0, 0), datetime(2026, 8, 17, 7, 0, 0))
    assert report["grain"] == "day"
    assert [r["period"] for r in report["origin"]] == ["2026-08-11", "2026-08-12"]


def test_an_explicit_grain_overrides_what_the_window_would_have_chosen():
    """Auto is a default, not a cage — a 90-day window still has to be readable week by week."""
    conn = _fresh()
    store.upsert_issues(conn, [_issue("DEVOPS-95", ["DevOps", "pe-iac-request"],
                                      datetime(2026, 6, 10, 16, 0, 0))])
    args = (conn, datetime(2026, 8, 19, 12, 0, 0), datetime(2026, 5, 21, 7, 0, 0), None)
    assert slas.slas_report(*args)["grain"] == "month"
    assert slas.slas_report(*args, "week")["origin"][0]["period"] == "2026-06-08"
    assert slas.slas_report(*args, "day")["origin"][0]["period"] == "2026-06-10"


def test_a_monthly_row_is_partial_when_the_window_starts_mid_month():
    """Monthly windows almost never start on the 1st, so the first month is a part-month.

    Unflagged it reads as a real drop in demand, which is the same trap the weekly edges had.
    """
    conn = _fresh()
    store.upsert_issues(conn, [
        _issue("DEVOPS-96", ["DevOps", "pe-iac-request"], datetime(2026, 5, 25, 16, 0, 0)),
        _issue("DEVOPS-97", ["DevOps", "pe-iac-request"], datetime(2026, 6, 15, 16, 0, 0)),
    ])
    rows = {r["period"]: r for r in slas.slas_report(
        conn, datetime(2026, 8, 19, 12, 0, 0), datetime(2026, 5, 21, 7, 0, 0),
        datetime(2026, 7, 1, 7, 0, 0))["origin"]}
    assert rows["2026-05-01"]["partial"] is True, "window opened on the 21st"
    assert rows["2026-06-01"]["partial"] is False, "a whole month inside the window"
