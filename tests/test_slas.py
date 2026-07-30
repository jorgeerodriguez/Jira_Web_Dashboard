from datetime import datetime
import duckdb
from darkstar import store, slas


def _issue(key, labels, created, status="Done", issuetype="Story"):
    return store.IssueRow(
        key=key, id=int(key.split("-")[1]), project="DEVOPS", issuetype=issuetype,
        status=status, status_category=("done" if status in ("Done", "Won't Do") else "indeterminate"),
        priority="Medium", summary=key, assignee=None, assignee_account_id=None,
        reporter=None, business_lead=None, parent_key=None,
        created=created, updated=created, resolutiondate=None,
        planned_start=None, target_end=None, labels=labels,
        fetched_at=datetime(2026, 7, 28, 0, 0, 0))


def _mr(id, key, labels, opened, merged):
    return store.MergeRequestRow(
        id=id, project_path="audacy-inc/devops/x", iid=id, author_account_id="a",
        title=f"{key} do a thing", opened_at=opened, merged_at=merged, labels=labels,
        web_url="u", fetched_at=datetime(2026, 7, 28, 0, 0, 0))


def _seed():
    conn = duckdb.connect(":memory:")
    store.initialize_schema(conn)
    # AI tf-module (MR label pe:tf-module), done in 24h, MR open→merge 24h. Meets 48h placeholder.
    store.upsert_issues(conn, [_issue("DEVOPS-1", ["DevOps", "pe-iac-request", "pe-tf-module"],
                                       datetime(2026, 7, 20, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-1"],
        [store.TransitionRow(key="DEVOPS-1", to_status="Done", changed_at=datetime(2026, 7, 21, 9, 0, 0), seq=0)])
    # AI k8s-request: Jira carries only pe-iac-request, but the MR label pe:k8s-request separates it.
    store.upsert_issues(conn, [_issue("DEVOPS-4", ["DevOps", "pe-iac-request"],
                                       datetime(2026, 7, 20, 9, 0, 0))])
    store.replace_transitions(conn, ["DEVOPS-4"],
        [store.TransitionRow(key="DEVOPS-4", to_status="Done", changed_at=datetime(2026, 7, 20, 21, 0, 0), seq=0)])
    # AI troubleshoot, abandoned (Won't Do), NO MR → falls back to the Jira pe-troubleshoot label.
    store.upsert_issues(conn, [_issue("DEVOPS-2", ["DevOps", "pe-troubleshoot"],
                                       datetime(2026, 7, 20, 9, 0, 0), status="Won't Do")])
    store.replace_transitions(conn, ["DEVOPS-2"],
        [store.TransitionRow(key="DEVOPS-2", to_status="Won't Do", changed_at=datetime(2026, 7, 20, 12, 0, 0), seq=0)])
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
    r = slas.slas_report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    by = {(b["audience"], b["type"]): b for b in r["buckets"]}
    assert by[("ai", "tf-module")]["volume"] == 1
    assert by[("ai", "k8s-request")]["volume"] == 1     # separated via the MR label (Option B win)
    assert by[("ai", "troubleshoot")]["volume"] == 1    # no MR → Jira fallback
    assert all(b["audience"] == "ai" for b in r["buckets"])   # DEVOPS-3 (no pe-*) excluded


def test_sla_compliance_uses_delivery_turnaround():
    r = slas.slas_report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    tm = next(b for b in r["buckets"] if b["type"] == "tf-module")
    assert tm["turnaround_hours_median"] == 24.0
    assert tm["sla_met_pct"] == 100.0  # 24h <= 48h placeholder


def test_agent_success_rate_counts_wont_do_as_failure():
    r = slas.slas_report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    # 3 AI terminal issues: DEVOPS-1 Done, DEVOPS-4 Done, DEVOPS-2 Won't Do → 2/3 = 66.7%
    assert r["agent_success"]["terminal"] == 3
    assert r["agent_success"]["succeeded"] == 2
    assert r["agent_success"]["rate_pct"] == 66.7


def test_review_turnaround_from_linked_mr():
    r = slas.slas_report(_seed(), datetime(2026, 7, 28, 12, 0, 0))
    tm = next(b for b in r["buckets"] if b["type"] == "tf-module")
    assert tm["review_hours_median"] == 24.0
