"""The PE roster is hand-maintained, so something has to notice when it stops being true.

`roster.ROSTER` is a hardcoded list ported from the audacy-jira-reports pipeline. Nothing derives or
refreshes it, and it gates far more than one panel: velocity, capacity, intake and the SME matrix all
count roster members only, and /slas uses it to decide PE vs non-PE authorship.

The failure is silent and biased. A new PE hire is in no list, so their merge requests are attributed
to "outside PE" and the self-service adoption headline goes UP — a number moving in the flattering
direction for a reason that has nothing to do with adoption. Nobody investigates a metric that
improves, so this test is the only thing that would catch it.

The live checks skip without a GITLAB_TOKEN, so the ordinary suite stays offline; the two structural
checks at the bottom need no network and always run. Locally:

    SSL_CERT_FILE=$(python -m certifi 2>/dev/null || echo /etc/ssl/cert.pem) \
    GITLAB_TOKEN=$(glab config get token --host gitlab.com) pytest tests/test_roster_membership.py

On a machine behind TLS inspection, SSL_CERT_FILE must include the corporate CA or every call fails
certificate verification — glab works there because Go reads the macOS trust store and Python does not.
"""
import json
import os
import urllib.request

import pytest

from darkstar.roster import (
    GITLAB_USERNAMES,
    NON_HUMAN_GROUP_MEMBERS,
    PE_GROUP_ID,
    ROSTER,
)

_TIMEOUT_SECONDS = 30


def _group_members(group_id: int, token: str) -> dict[str, str]:
    """username -> display name for every direct member of the group."""
    request = urllib.request.Request(
        f"https://gitlab.com/api/v4/groups/{group_id}/members?per_page=100",
        headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return {m["username"]: m["name"] for m in json.load(response)}


@pytest.fixture(name="members")
def _members() -> dict[str, str]:
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        pytest.skip("GITLAB_TOKEN unset; roster drift can only be checked against live GitLab")
    return _group_members(PE_GROUP_ID, token)


def test_every_human_group_member_is_on_the_roster(members):
    """A PE member missing from ROSTER is counted as *outside* PE, inflating the adoption share."""
    humans = set(members) - NON_HUMAN_GROUP_MEMBERS
    missing = humans - set(GITLAB_USERNAMES)
    assert not missing, (
        "these audacy-inc/devops members are on no list — add them to roster.GITLAB_USERNAMES and "
        "roster.ROSTER if they are PE, or to roster.NON_HUMAN_GROUP_MEMBERS if they are service "
        f"accounts: {sorted(f'{u} ({members[u]})' for u in missing)}")


def test_nobody_on_the_roster_has_left_the_group(members):
    """A departed member keeps absorbing merge requests into the PE side of every comparison."""
    departed = set(GITLAB_USERNAMES) - set(members)
    assert not departed, (
        "these roster entries are no longer direct members of audacy-inc/devops; remove them from "
        f"roster.GITLAB_USERNAMES and roster.ROSTER: {sorted(departed)}")


def test_the_exclusion_list_still_describes_real_members(members):
    """A stale exclusion is how a real person gets silently written off as a bot.

    If an excluded username is reused, or a service account is removed and a human later takes that
    name, the exclusion would quietly drop them from PE with no other symptom.
    """
    stale = NON_HUMAN_GROUP_MEMBERS - set(members)
    assert not stale, (
        f"roster.NON_HUMAN_GROUP_MEMBERS names accounts that have left the group: {sorted(stale)}")


def test_no_account_is_both_excluded_and_rostered():
    """Offline: the two lists must not overlap, or membership depends on which is checked first."""
    assert not (NON_HUMAN_GROUP_MEMBERS & set(GITLAB_USERNAMES))


def test_the_two_roster_maps_describe_the_same_people():
    """Offline: GITLAB_USERNAMES maps to accountIds that ROSTER must be able to name.

    A username added without its ROSTER entry attributes merge requests to an accountId no view can
    resolve, so the author silently vanishes from every panel rather than erroring.
    """
    unnameable = {u: a for u, a in GITLAB_USERNAMES.items() if a not in ROSTER}
    assert not unnameable, f"GITLAB_USERNAMES entries with no ROSTER name: {unnameable}"
    assert len(set(GITLAB_USERNAMES.values())) == len(GITLAB_USERNAMES), "an accountId is mapped twice"
