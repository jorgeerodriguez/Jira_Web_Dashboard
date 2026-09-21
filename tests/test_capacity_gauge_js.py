"""Executable coverage for the capacity gauge's client-side maths.

Why this file exists: `wipSegments` shipped calling `.toFixed()` on `donePct`, which
`renderCapacity` passes as a **string** (it came from `.toFixed(1)`). That threw
`left.toFixed is not a function` and took the whole intake page down with
"Failed to load intake data".

It got through because the only check on that function called it with a numeric literal
instead of what the real caller passes. A unit check that invents its own arguments cannot
catch a type mismatch between two pieces of production code — so these drive `renderCapacity`
itself, which is the thing the browser calls.

NOTE: the CI image is python:3.12-slim and has no node, so these SKIP in the pipeline and
only run locally. That is a real gap, not a solved problem — adding node to the test job
would make the repo's dashboard JS genuinely covered.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_DASHBOARD = Path(__file__).resolve().parents[1] / "darkstar" / "dashboards" / "intake.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed (CI image is python:3.12-slim)")

# Enough of a DOM for the module-scope code to load. The page's own init() runs on import and
# fetches, so fetch rejects and its error path is exercised too — which is how we learn the
# harness is honest about what the browser does.
_HARNESS = """
const __captured = {};
const __el = () => ({ set innerHTML(v){ __captured.html = v; }, get innerHTML(){ return ""; },
                      addEventListener(){}, insertAdjacentHTML(){}, appendChild(){},
                      querySelector: () => __el(), querySelectorAll: () => [],
                      closest: () => null, style:{}, dataset:{}, textContent:"",
                      classList:{add(){}, remove(){}, toggle(){}, contains(){ return false; }} });
globalThis.document = { getElementById: __el, querySelector: __el,
                        querySelectorAll: () => [], addEventListener(){},
                        createElement: __el, body: __el() };
globalThis.window = globalThis;
globalThis.fetch = () => Promise.reject(new Error("no network in tests"));
"""

_DRIVE = """
// Drive the real renderCapacity with a realistic payload, exactly as init() would.
SIZE_WEIGHTS = {Small: 0.6777, Medium: 1.5491, Large: 2.7109, XL: 4.3568};
ROSTER = JSON.parse(process.env.DARKSTAR_TEST_ROSTER);
renderCapacity();
console.log(JSON.stringify({ html: __captured.html }));
"""


def _render(roster: dict) -> str:
    """Run the page's own renderCapacity over `roster` and return the HTML it produced."""
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", _DASHBOARD.read_text(encoding="utf-8"), re.S))
    program = _HARNESS + scripts + _DRIVE
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30,
                            env={**os.environ, "DARKSTAR_TEST_ROSTER": json.dumps(roster)})
    assert result.returncode == 0, f"renderCapacity threw:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])["html"]


def _member(**over):
    base = {"name": "Adam", "color": "#ef6f9e", "vel": 20, "done": 4, "wip": 3.0,
            "unsized": 0, "tickets": 2, "mix": {"XL": 1, "Small": 1}, "lead": False}
    base.update(over)
    return base


def test_rendering_a_sized_roster_does_not_throw():
    """The exact production crash: renderCapacity hands wipSegments a string and it did maths on it.

    Asserting only "does not throw" is the point — the bug was a TypeError, not a wrong number.
    """
    html = _render({"adam": _member()})
    assert "capfill" in html


def test_every_segment_offset_is_a_finite_number():
    """A string `left` would have produced "4.0undefined%" style output rather than an offset.

    Catches the silent half of the same bug: string concatenation where arithmetic was meant.
    """
    html = _render({"adam": _member(mix={"XL": 1, "Medium": 2, "Small": 1})})
    offsets = [float(v) for v in re.findall(r"left:([0-9.]+)%", html)]
    assert offsets, "no positioned segments rendered"
    assert all(0 <= v <= 100 for v in offsets), offsets
    assert offsets == sorted(offsets), f"segments must march left to right: {offsets}"


def test_wip_is_one_block_in_the_engineer_s_own_colour():
    """WIP is a single block whose WIDTH carries the weighted load — no per-size segments.

    Splitting it by size was reverted: the mix is not an input to "who should take this ticket",
    which needs spare capacity and domain skill. It cost a page-down and an unreadable legend to
    restate numbers the spare figure already accounts for.
    """
    html = _render({"adam": _member()})
    assert html.count("capfill") == 1
    assert "#ef6f9e" in html, "the fill must carry the engineer's assigned colour"


def test_the_bar_never_recolours_for_over_capacity():
    """Over-utilisation is said once, by the red spare figure on the right.

    A second channel for the same fact cost a legend key and, once the bar was segmented, read as
    a size category rather than a state.
    """
    html = _render({"adam": _member(done=30, wip=12.0, vel=8)})   # well past velocity
    gauge = re.search(r'<div class="capgauge">(.*?)</div></div>', html, re.S).group(1)
    assert "--neg" not in gauge, f"the bar itself must not turn red: {gauge}"
    assert "#ef6f9e" in gauge, "and must still carry the engineer's colour"


def test_a_roster_with_no_wip_at_all_renders_cleanly():
    """scaleMax and the segment loop both divide; an all-zero roster must not produce NaN."""
    html = _render({"adam": _member(done=0, wip=0.0, tickets=0, mix={})})
    assert "NaN" not in html and "undefined" not in html
