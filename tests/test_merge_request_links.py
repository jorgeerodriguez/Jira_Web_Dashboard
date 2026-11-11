"""GitLab merge requests on a ticket, from links pasted in its description or comments.

Pinned: links to an MR's notes, diffs or commits count as that MR; wiki markup and Atlassian document format both
work; duplicates collapse; automation comments count (marked as such); description links are dated at creation;
the table keeps one row per MR with its first mention; and Status of My Ticket shows them (lane, table, KPI,
printable summary), also for data fetched before MRs were loaded.
"""
import pandas as pd
import pytest

from data.merge_request_links import find_merge_requests, issue_merge_requests, merge_request_table

BASE = "https://gitlab.com/audacy-inc/devops/terraform-aws"


def test_finds_and_normalises_mr_links():
    text = (f"See [the MR|{BASE}/-/merge_requests/12] and {BASE}/-/merge_requests/12/diffs#abc, "
            f"also {BASE}/-/merge_requests/12#note_99. Then https://GitLab.com/audacy-inc/infra/-/merge_requests/7.")
    found = find_merge_requests(text)
    assert [(m["project"], m["iid"]) for m in found] == [("audacy-inc/devops/terraform-aws", 12), ("audacy-inc/infra", 7)]
    assert found[0]["url"] == f"{BASE}/-/merge_requests/12"
    adf = {"type": "doc", "content": [{"type": "inlineCard", "attrs": {"url": f"{BASE}/-/merge_requests/3"}}]}
    assert find_merge_requests(adf)[0]["iid"] == 3
    assert find_merge_requests(None) == [] and find_merge_requests("gitlab.com/x/-/issues/4") == []


def test_mentions_and_table():
    fields = {
        "created": "2026-10-01T09:00:00.000-0600", "reporter": {"displayName": "Rae"},
        "description": f"Plan: {BASE}/-/merge_requests/12",
        "comment": {"comments": [
            {"author": {"displayName": "Ana"}, "created": "2026-10-03T10:00:00.000-0600",
             "body": f"Ready: {BASE}/-/merge_requests/12 and {BASE}/-/merge_requests/15"},
            {"author": {"displayName": "GitLab", "accountType": "app"}, "created": "2026-10-04T10:00:00.000-0600",
             "body": f"Merged {BASE}/-/merge_requests/15"}]},
    }
    mentions = issue_merge_requests(fields)
    assert [(m["iid"], m["where"]) for m in mentions] == [
        (12, "description"), (12, "comment"), (15, "comment"), (15, "comment (automation)")]
    table = merge_request_table(mentions)
    assert list(table["MR #"]) == ["!12", "!15"] and list(table["Mentions"]) == [2, 2]
    assert table.iloc[0]["By"] == "Rae" and table.iloc[0]["Where"] == "description"
    assert table.iloc[0]["First Mentioned"] == pd.Timestamp("2026-10-01T15:00", tz="UTC")
    assert merge_request_table(None).empty and merge_request_table([]).empty


def test_ticket_story_shows_merge_requests():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    pytest.importorskip("networkx")
    from reports import in_progress_report as ipr
    from reports import ticket_story as ts
    from tests.test_ticket_story import _bd, _c, _issue

    d = lambda n: _bd(ipr, n)  # noqa: E731
    issue = _issue("DEVOPS-7", "In Progress", d(10), start=d(8), comments=[_c("Ana", d(5), f"MR: {BASE}/-/merge_requests/12")])
    issue["merge_requests"] = issue_merge_requests({
        "created": str(issue["created"]), "comment": {"comments": [
            {"author": {"displayName": "Ana"}, "created": str(issue["comments"][0]["created"]),
             "body": issue["comments"][0]["body"]}]}})
    s = ts.build_ticket_story(pd.DataFrame([issue]), None, "DEVOPS-7")
    assert s["kpis"]["mrs"] == 1 and s["mr_df"].iloc[0]["MR #"] == "!12" and s["mr_df"].iloc[0]["When"].tzinfo is None
    assert "Merge requests" in list(s["timeline_fig"].layout.yaxis.categoryarray)
    assert "terraform-aws !12" in ts.summary_html(s)

    old = _issue("DEVOPS-8", "To Do", d(3))                                       # fetched before MRs were loaded
    s = ts.build_ticket_story(pd.DataFrame([old]), None, "DEVOPS-8")
    assert s["kpis"]["mrs"] == 0 and s["mr_df"].empty
    assert "Merge requests" not in list(s["timeline_fig"].layout.yaxis.categoryarray)
