"""Nobody off the current roster can be suggested as an SME or an alternate.

Randall left PE and was still being offered as an alternate for GCP Core and Composer. Two separate
causes, and fixing either alone leaves the bug:

  1. He was still in `ROSTER`, so the derived ranking counted his ticket and MR history.
  2. The manual overrides name him by key, and those live in JSON on the PVC. Removing him from
     `_SEED` in overrides.py does nothing to a file seeded months ago — the seed only ever applies
     on first read.

These drive the dashboard's own `smeList` over a DOM stub, for the reason set out in
test_capacity_gauge_js.py: checking a helper with arguments invented by the test cannot catch a
mismatch between two pieces of production code.

Like that file, these SKIP without node, which the CI image does not have.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from darkstar import roster

_DASHBOARD = Path(__file__).resolve().parents[1] / "darkstar" / "dashboards" / "intake.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed (CI image is python:3.12-slim)")

_HARNESS = """
globalThis.document = { getElementById: () => ({ set innerHTML(v){}, addEventListener(){},
                          querySelector: () => null, querySelectorAll: () => [] }),
                        querySelector: () => ({ insertAdjacentHTML(){}, querySelectorAll: () => [] }),
                        querySelectorAll: () => [], addEventListener(){} };
globalThis.window = globalThis;
globalThis.fetch = () => Promise.reject(new Error("no network in tests"));
"""

_DRIVE = """
const __in = JSON.parse(process.env.DARKSTAR_TEST_INPUT);
ROSTER = __in.roster; SME = __in.sme; SME_OVERRIDES = __in.overrides; SME_ADD = __in.add;
console.log(JSON.stringify(smeList(__in.domain).map(e => e[0])));
"""


def _sme_list(domain, roster_keys, sme=None, overrides=None, add=None):
    """Run the page's own smeList and return the ordered keys it would route to."""
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>",
                                   _DASHBOARD.read_text(encoding="utf-8"), re.S))
    payload = {"domain": domain,
               "roster": {k: {"name": k.title()} for k in roster_keys},
               "sme": sme or {}, "overrides": overrides or {}, "add": add or {}}
    result = subprocess.run(["node", "-e", _HARNESS + scripts + _DRIVE],
                            capture_output=True, text=True, timeout=30,
                            env={**os.environ, "DARKSTAR_TEST_INPUT": json.dumps(payload)})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- the roster split -----------------------------------------------------------------------------

def test_a_departed_member_is_out_of_the_roster_but_still_pe():
    """The two answers to "is Randall PE" point opposite ways, so they are two maps.

    Out of ROSTER, nothing forward-looking counts on him. Still in PE_EVER, so the work he did is
    not reclassified as outside PE — which would move the self-service adoption headline up for a
    reason that has nothing to do with adoption.
    """
    assert "Randall" not in roster.ROSTER.values()
    assert "Randall" in roster.ALUMNI.values()
    assert "Randall" in roster.PE_EVER.values()


def test_alumni_keep_their_gitlab_mapping():
    """Dropping it would re-key their already-crawled merge requests to a bare username, moving
    historical authorship and approvals off the PE side of every ratio."""
    assert roster.GITLAB_USERNAMES["randall.puterbaugh"] in roster.ALUMNI


def test_the_seed_names_nobody_who_has_left():
    """A store created after this ships must not reintroduce the entry the filter exists to hide."""
    from darkstar import overrides
    named = {k for ov in overrides._SEED["overrides"].values() for k in ov["order"]}
    named |= {k for keys in overrides._SEED["add"].values() for k in keys}
    departed = {n.lower() for n in roster.ALUMNI.values()}
    assert not (named & departed), f"seed still names departed members: {named & departed}"


# --- the filter, which is what protects an already-seeded store ------------------------------------

def test_a_departed_member_is_dropped_from_a_manual_override():
    """The live case: the PVC's JSON still lists randall for GCP Core, seeded before he left."""
    keys = _sme_list("GCP Core", ["trevor", "omar"],
                     overrides={"GCP Core": {"order": ["trevor", "omar", "randall"],
                                             "why": "x", "set": "2026-07"}})
    assert keys == ["trevor", "omar"]


def test_a_departed_member_is_dropped_from_the_derived_ranking_too():
    """Belt and braces: the corpus is roster-keyed, but a stale cached payload is not."""
    keys = _sme_list("Grafana", ["vlad"], sme={"Grafana": [["randall", 9], ["vlad", 3]]})
    assert keys == ["vlad"]


def test_a_departed_member_is_dropped_from_the_add_list():
    """SME_ADD appends runners-up without displacing the derived SME, and was unfiltered."""
    keys = _sme_list("GitLab", ["adam"], sme={"GitLab": [["adam", 5]]},
                     add={"GitLab": ["randall"]})
    assert keys == ["adam"]


def test_a_departed_member_cannot_be_the_top_suggestion():
    """order[0] is the Preferred SME. Routing new work to someone who left is the actual harm."""
    keys = _sme_list("Composer", ["trevor"],
                     overrides={"Composer": {"order": ["randall", "trevor"],
                                             "why": "x", "set": "2026-07"}})
    assert keys[0] == "trevor"


def test_an_override_naming_only_departed_people_falls_back_to_the_derived_ranking():
    """Not an instruction to show nobody — it is a stale override, so the domain should read the
    way it would have without one. Returning [] would also crash renderSME on arr[0]."""
    keys = _sme_list("Composer", ["trevor"],
                     sme={"Composer": [["trevor", 4]]},
                     overrides={"Composer": {"order": ["randall"], "why": "x", "set": "2026-07"}})
    assert keys == ["trevor"]


def test_a_current_member_is_untouched_by_the_filter():
    """The guard must not quietly drop the people it is supposed to keep."""
    keys = _sme_list("IAM/RBAC", ["simon", "tom"],
                     overrides={"IAM/RBAC": {"order": ["simon", "tom"], "why": "x", "set": "2026-07"}})
    assert keys == ["simon", "tom"]
