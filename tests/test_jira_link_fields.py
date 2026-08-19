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


def test_the_issue_sync_requests_both_link_fields():
    """Dropping either from the field list costs the links silently — the payload just arrives thinner."""
    assert "customfield_11534" in ingest._ISSUE_FIELDS, "the Merge Request field must be fetched"
    assert "customfield_10400" in ingest._ISSUE_FIELDS, "the Development summary must be fetched"


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


def test_dev_panel_counts_are_read_from_jiras_summary_blob():
    """Jira serves this as a Java-style toString, not JSON, so the counts are pulled out by regex.

    This is the exact shape seen on DEVOPS-10117: four repositories carrying commits, and no
    `pullrequest` member at all even though JQL reports pull requests on that issue. Which is why
    either count is treated as evidence of code downstream.
    """
    blob = ("{build={count=5, dataType=build, failedBuildCount=1}, "
            "repository={count=4, dataType=repository}, json={\"cachedValue\":{},\"isStale\":true}}")
    row = ingest._map_issue(_issue(customfield_10400=blob), _NOW)
    assert row.dev_commit_count == 4
    assert row.dev_pr_count == 0, "absent from the blob, so Jira reported none"


def test_a_missing_development_field_is_none_rather_than_zero():
    """Zero is Jira saying "no code"; None is Jira saying nothing. Only the first is evidence."""
    row = ingest._map_issue(_issue(), _NOW)
    assert row.dev_pr_count is None and row.dev_commit_count is None
