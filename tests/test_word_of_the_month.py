"""Word of the Month: comment coverage, friction themes and phrases from human ticket comments.

The page is meant to drive process changes, so the rules behind each number are pinned here: bots
never count as comments, coverage is measured on completed tickets, only the requester can "chase",
chasing is a symptom and never a recommendation, and people's names are never a phrase.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

# The CI test job installs darkstar's runtime, not the Streamlit app's (no plotly): skip there.
pytest.importorskip("plotly")
pytest.importorskip("holidays")

from data.build_dataframe_new import _human_comments  # noqa: E402
from reports import word_of_the_month_report as wotm  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))
_TODAY = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()


def _comment(author, body, days_after_created=1):
    return {"author": author, "body": body, "days": days_after_created}


def _issue(key, comments=(), assignee="Ana", reporter="Rita", status="Done", done_days_ago=0, cycle_days=4,
           priority="Medium", size="Medium", lead="Lead A"):
    done = _TODAY - pd.Timedelta(days=done_days_ago)
    start = done - pd.Timedelta(days=cycle_days)
    created = (start - pd.Timedelta(days=1)).tz_localize(_LOCAL)
    return {
        "key": key, "status": status, "issuetype": "Story", "assignee_name": assignee, "reporter_name": reporter,
        "priority_name": priority, "estimated_size_name": size, "business_lead": lead,
        "created": created, "updated": done.tz_localize(_LOCAL),
        "status_category_changed": done.tz_localize(_LOCAL).tz_convert("UTC"),
        "planned_start_date": pd.Timestamp(start.date(), tz="UTC"),
        "comments": [{"author": c["author"], "body": c["body"],
                      "created": (created + pd.Timedelta(days=c["days"])).tz_convert("UTC")} for c in comments],
    }


def _build(rows, **kwargs):
    month = _TODAY.strftime("%Y-%m")
    return wotm.build_word_of_the_month_visuals(pd.DataFrame(rows), start_month=month, end_month=month, **kwargs)


def test_loader_keeps_human_comments_and_counts_bots():
    fields = {"comment": {"total": 3, "comments": [
        {"author": {"displayName": "Automation for Jira", "accountType": "app"}, "body": "Moved to Done",
         "created": "2026-09-01T10:00:00.000-0600"},
        {"author": {"displayName": "Ana", "accountType": "atlassian"}, "body": "Deployed",
         "created": "2026-09-01T11:00:00.000-0600"},
        {"author": {"displayName": "Rita", "accountType": "atlassian"}, "body": "Thanks",
         "created": "2026-09-01T12:00:00.000-0600"},
    ]}}
    comments, total, bots = _human_comments(fields)
    assert [c["author"] for c in comments] == ["Ana", "Rita"]
    assert total == 3 and bots == 1


def test_clean_comment_strips_markup_mentions_links_and_code():
    text = "Hi [~accountid:123abc] see https://x.io/y and [the doc|https://d] {code}secret stuff{code} DEVOPS-42 done"
    cleaned = wotm.clean_comment(text)
    for gone in ["accountid", "https", "the doc", "secret", "DEVOPS-42"]:
        assert gone not in cleaned


def test_themes_are_tagged_with_an_excerpt():
    found = wotm.tag_themes(["We are waiting on the network team", "Granted IAM access to the bucket"])
    assert "waiting on" in found["Waiting / blocked"]
    assert found["Access / permissions"] is not None
    assert found["Approval"] is None


def test_comment_coverage_counts_human_and_assignee_comments_on_completed_tickets():
    rows = [
        _issue("T-1", [_comment("Ana", "Deployed to prod")]),
        _issue("T-2", [_comment("Rita", "Looks good")]),
        _issue("T-3"),
        _issue("T-4"),
        _issue("T-5", [_comment("Ana", "Done")], status="In Progress"),  # not completed: not counted
    ]
    out = _build(rows)
    assert out["tickets_in_range"] == 4
    assert out["coverage"] == pytest.approx(0.5)
    assert out["closing_note"] == pytest.approx(0.25)


def test_only_the_requester_can_chase_and_chasing_is_never_recommended():
    rows = [
        _issue("T-1", [_comment("Ana", "I'll follow up tomorrow")]),                  # engineer, not chasing
        _issue("T-2", [_comment("Rita", "Any update on this?", 3)], cycle_days=12),   # requester chasing
    ] + [_issue(f"T-{i}", [_comment("Ana", "Deployed")]) for i in range(3, 15)]
    out = _build(rows)
    detail = out["tickets_df"].assign(key=lambda d: d["Ticket"].str.split("/").str[-1]).set_index("key")
    assert "Chasing / follow-up" in detail.loc["T-2", "Themes"]
    assert "T-1" not in detail.index or "Chasing" not in detail.loc["T-1", "Themes"]
    assert out["chased_share"] == pytest.approx(1 / 14)
    assert all(rec["theme"] not in wotm.SYMPTOM_THEMES for rec in out["recommendations"])


def test_first_reply_ignores_the_requesters_own_comments():
    rows = [_issue("T-1", [_comment("Rita", "Adding details", 0), _comment("Ana", "On it", 2)])]
    out = _build(rows)
    # Reply is Ana's comment two calendar days after creation: at least one full business day.
    assert out["first_reply_hours"] >= wotm.BUSINESS_HOURS_PER_DAY


def test_people_names_are_never_phrases():
    body = "Ana Silva deployed the runtime image to the shared vpc"
    rows = [_issue(f"T-{i}", [_comment("Ana Silva", body)], assignee="Ana Silva") for i in range(6)]
    out = _build(rows)
    phrases = set(out["emerging_df"]["Phrase"])
    assert "runtime image" in phrases
    assert not any("ana" in p.split() or "silva" in p.split() for p in phrases)


def test_missing_comments_column_asks_for_a_refetch():
    df = pd.DataFrame([_issue("T-1")]).drop(columns="comments")
    out = wotm.build_word_of_the_month_visuals(df)
    assert "Fetch Jira tickets again" in out["error_message"]
