"""Platform Engineering roster: Jira accountId -> display name.

Velocity and capacity views count only these 14 people (completions by anyone else —
e.g. non-team assignees — are excluded). Ported from the audacy-jira-reports pipeline.
"""
from __future__ import annotations

ROSTER: dict[str, str] = {
    "600ece193b1af000697f339d": "Adam",
    "5aa3365d29118e2c1375d5ea": "Randall",
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

# GitLab username -> Jira accountId, for attributing merged MRs to roster members.
# Contributors not in this map (other teams, bots) are ignored by the GitLab ingest.
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

# GitLab contributors OUTSIDE the PE roster whose merge-request flow is still tracked.
# Deliberately absent from ROSTER: velocity, capacity and the SME matrix count roster members
# only, so adding them there would silently change those dashboards. This map exists purely so
# the GitLab ingest stops discarding their MRs and the MR-turnaround view can name them.
TRACKED_MR_AUTHORS: dict[str, str] = {
    "audacy-ben.bonora":      "557058:db636519-8345-40f2-a3d6-ac816608b5e8",
    "audacy-jeremy.williams": "61d4662c7aa7ac007029587f",
}

_TRACKED_MR_AUTHOR_NAMES: dict[str, str] = {
    "557058:db636519-8345-40f2-a3d6-ac816608b5e8": "Ben Bonora",
    "61d4662c7aa7ac007029587f":                    "Jeremy Williams",
}

# GitLab username -> Jira accountId for every author the MR ingest attributes (roster + tracked).
MR_AUTHORS: dict[str, str] = {**GITLAB_USERNAMES, **TRACKED_MR_AUTHORS}

# Jira accountId -> display name for every attributed MR author.
MR_AUTHOR_NAMES: dict[str, str] = {**ROSTER, **_TRACKED_MR_AUTHOR_NAMES}
