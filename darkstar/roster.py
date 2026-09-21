"""Platform Engineering roster: Jira accountId -> display name.

Two maps, because "who is PE" has two different answers. `ROSTER` is who is on the team *now*, and
gates every forward-looking view: velocity, capacity, intake and the SME matrix count only these
people (completions by anyone else — non-team assignees — are excluded). `ALUMNI` is who has left,
and still counts as PE wherever the question is historical rather than predictive. `PE_EVER` is the
union, for exactly those historical questions. Ported from the audacy-jira-reports pipeline.
"""
from __future__ import annotations

ROSTER: dict[str, str] = {
    "600ece193b1af000697f339d": "Adam",
    "712020:58e4121c-dadd-4c34-99a9-92dc31ee039b": "Omar",
    "5af1bbd7999f392c4882ea62": "Tom",
    "712020:ff85e042-9fc6-4019-9fda-590317ad40a1": "Vlad",
    "712020:7b17ea63-c2c5-430a-9077-01bc69e77d9e": "Oleh",
    "712020:ad62d468-1992-4732-ba89-6b97f6c30f04": "Taras",
    "712020:e4bf6796-fa1d-4b46-ab99-44efb469258d": "Andriy",
    "712020:848d5ad1-9ec9-47d2-9aa5-7b6da106ff14": "Pavlo",
    "712020:f7cf29d4-2183-4d11-a4d1-34da30c0cd69": "Trevor",
    "6228f44c932f0f00716aca72": "Bolanle",
    "62682169fff19d006926eb24": "Simon",
    "712020:f53bb9ec-b2b5-4a5a-8811-e3c0a197732b": "Denys",
    "712020:1afb2e28-5f9e-4e68-acf6-68f7e68f7e54": "Zack",
}

# Departed PE members. A departure is a move to this map, never a deletion, because the two things
# a leaver should do to the numbers point in opposite directions.
#
# Out of ROSTER, so nothing forward-looking counts on them: no capacity row offering spare capacity
# nobody has, no suggestion routing new work to them, no throughput of theirs in the team forecast.
#
# Still PE via PE_EVER, so nothing historical is rewritten: they were PE when they authored and
# approved that work, and deleting them reclassifies it as "outside PE", which moves the
# self-service adoption headline UP for a reason that has nothing to do with adoption. Randall's 96
# self-service merge requests alone take it from 21% to 37%.
ALUMNI: dict[str, str] = {
    "5aa3365d29118e2c1375d5ea": "Randall",
}

# Everyone who has ever been PE. Use this to attribute work that already happened (who authored it,
# who approved it); use ROSTER for anything about capacity or the month ahead.
PE_EVER: dict[str, str] = {**ROSTER, **ALUMNI}

# GitLab username -> Jira accountId, for keying merged MRs to a person. Covers ROSTER and ALUMNI:
# the ingest keeps every author it finds, and this map only decides whether they are keyed by
# accountId (PE, past or present) or by their bare username (everyone else). Alumni keep their entry
# so their already-crawled merge requests stay nameable and their approvals stay on the PE side.
GITLAB_USERNAMES: dict[str, str] = {
    "audacy-adam.shero":       "600ece193b1af000697f339d",
    "randall.puterbaugh":      "5aa3365d29118e2c1375d5ea",
    "omar.saundersholiday":    "712020:58e4121c-dadd-4c34-99a9-92dc31ee039b",
    "audacy-tom.terry":        "5af1bbd7999f392c4882ea62",
    "vladyslav.zhyhulin1":     "712020:ff85e042-9fc6-4019-9fda-590317ad40a1",
    "oleh.kuzo":               "712020:7b17ea63-c2c5-430a-9077-01bc69e77d9e",
    "taras.protsiv":           "712020:ad62d468-1992-4732-ba89-6b97f6c30f04",
    "audacy-andriy.petryshyn": "712020:e4bf6796-fa1d-4b46-ab99-44efb469258d",
    "pavlo.myshok":            "712020:848d5ad1-9ec9-47d2-9aa5-7b6da106ff14",
    "audacy-trevor.atchley":   "712020:f7cf29d4-2183-4d11-a4d1-34da30c0cd69",
    "audacy-bolanle.adeboye":  "6228f44c932f0f00716aca72",
    "audacy-zack.amadi":       "712020:1afb2e28-5f9e-4e68-acf6-68f7e68f7e54",
    "audacy-simon.davison":    "62682169fff19d006926eb24",
    "audacy-denys.naumenko":   "712020:f53bb9ec-b2b5-4a5a-8811-e3c0a197732b",
}

# Direct members of the audacy-inc/devops GitLab group that are NOT people. Maintained by hand and
# asserted against the live group by tests/test_roster_membership.py, so the roster cannot drift
# unnoticed: a human who joins PE appears in neither this list nor GITLAB_USERNAMES and fails the
# test, which is the point. An exclusion list rather than a name heuristic because "looks like a
# bot" silently reclassifies a person whose account happens to match.
#
# Drift matters beyond this page. ROSTER gates velocity, capacity, intake and the SME matrix, and on
# /slas PE_EVER decides PE vs non-PE authorship — where a missing member counts as OUTSIDE PE and
# inflates the adoption headline, the direction that flatters the metric. Group access usually
# outlives the departure, so alumni are exempted from the has-left check rather than being required
# to leave the group first.
NON_HUMAN_GROUP_MEMBERS: frozenset[str] = frozenset({
    "DevOps-agent",                                          # AWS-DevOps-agent
    "agentcore-pe",                                          # agentcore-pe
    "group_115211004_bot_6ce1b4bd899ad9f4f9b50d83b5273302",  # semantic-release
    "group_115211004_bot_bbc6b7e533b3a58af41a516bd3a2d55a",  # PlatformProvisionerBot
})

# The GitLab group ROSTER is expected to mirror: audacy-inc/devops.
PE_GROUP_ID: int = 115211004
