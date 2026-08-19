"""The two Jira fields that carry the most trustworthy link from a request to its code.

`customfield_11534` ("Merge Request") is a URL somebody entered deliberately; `customfield_10400`
("Development") is Jira's cached dev-panel summary, which names no merge request but does say whether
any code exists. Both arrive free with the issue sync — but only if the sync asks for them, which is
what these tests pin.
"""
from datetime import datetime
from types import SimpleNamespace

from darkstar import ingest

_NOW = datetime(2026, 8, 19, 12, 0, 0)


def test_the_issue_sync_requests_the_merge_request_field():
    """Dropping it from the field list costs the best link silently — the payload arrives thinner."""
    assert "customfield_11534" in ingest._ISSUE_FIELDS, "the Merge Request field must be fetched"


def _issue(**fields):
    base = {
        "summary": "s", "status": {"name": "Done", "statusCategory": {"key": "done"}},
        "issuetype": {"name": "Task"}, "priority": {"name": "Low"}, "labels": [],
        "created": "2026-08-05T16:00:00.000-0600", "updated": "2026-08-05T17:00:00.000-0600",
        "project": {"key": "DEVOPS"},
    }
    base.update(fields)
    return SimpleNamespace(raw={"key": "DEVOPS-1", "id": "1", "fields": base})


def test_the_merge_request_url_reaches_the_row_verbatim():
    """Parsing happens in slas.py; the ingest must not sanitise or interpret a free-text field."""
    url = "https://gitlab.com/audacy-inc/devops/terraform/tf-aardvark2-prod/-/merge_requests/480"
    row = ingest._map_issue(_issue(customfield_11534=url), _NOW)
    assert row.mr_field_url == url


def test_an_empty_merge_request_field_becomes_none_not_an_empty_string():
    """None means "nothing to link"; "" would look like a value that failed to parse."""
    assert ingest._map_issue(_issue(customfield_11534=""), _NOW).mr_field_url is None
    assert ingest._map_issue(_issue(), _NOW).mr_field_url is None


def test_the_development_summary_field_is_not_read_at_all():
    """It is a cache, and it lies. Reading it looked cheap and was wrong.

    On DEVOPS-10117 the panel showed 4 commits, 1 merged pull request and 2 builds. The same issue's
    customfield_10400 reported build count 5, repository count 5, **no pullrequest member at all**,
    and carried "isStale": true. A gap counter built on it silently reported "no pull request" for an
    issue with a merged one. JQL's development[pullrequests] index agreed with the panel, so the flags
    come from one query per batch instead.
    """
    assert "customfield_10400" not in ingest._ISSUE_FIELDS, "the cached summary must not be fetched"
    assert not hasattr(ingest, "_dev_counts"), "and its parser must be gone, not merely unused"


def test_the_batch_flags_come_from_the_key_sets_and_default_to_false():
    """False means Jira was asked and said no; None means nobody asked. Only the first is evidence."""
    rows = [ingest._map_issue(_issue(), _NOW)]
    assert rows[0].dev_has_pr is None, "unqueried until the flags are applied"

    flagged = ingest.apply_dev_panel_flags(rows, with_pr={"DEVOPS-1"}, with_commits=set())
    assert flagged[0].dev_has_pr is True
    assert flagged[0].dev_has_commits is False, "queried and absent is False, not None"

    neither = ingest.apply_dev_panel_flags(rows, with_pr=set(), with_commits=set())
    assert neither[0].dev_has_pr is False and neither[0].dev_has_commits is False


def test_applying_flags_leaves_every_other_field_untouched():
    """It rebuilds frozen rows, so a typo there would quietly blank a column."""
    url = "https://gitlab.com/audacy-inc/devops/x/-/merge_requests/1"
    row = ingest._map_issue(_issue(customfield_11534=url), _NOW)
    flagged = ingest.apply_dev_panel_flags([row], {"DEVOPS-1"}, {"DEVOPS-1"})[0]
    assert flagged.mr_field_url == url and flagged.key == row.key
    assert flagged.created == row.created and flagged.labels == row.labels
