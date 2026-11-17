"""Suggested Assignments, learning section (phase 1): descriptions for tagging and the domain back-test.

Pinned: the loader keeps a cleaned description (links removed, ADF flattened, trimmed); every page tags from title +
description + comments; the back-test only uses work finished before each ticket was created, counts untagged tickets
as misses, and compares against the busiest-people baseline.
"""
import pandas as pd
import pytest

from data.build_dataframe_new import DESCRIPTION_CHARS, _description_text
from reports import domains


def test_description_text_is_clean_and_short():
    text = _description_text("Set up *BigQuery* reservation, see https://gitlab.com/a/b/-/merge_requests/3 and\n\nmore")
    assert text == "Set up *BigQuery* reservation, see and more" and "gitlab" not in text
    adf = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Fastly"},
                                                                        {"type": "text", "text": "purge"}]}]}
    assert _description_text(adf) == "Fastly purge"
    assert _description_text(None) == "" and len(_description_text("x " * 5000)) == DESCRIPTION_CHARS


def test_ticket_text_includes_description():
    row = {"summary": "Help please", "description_text": "Grafana alert is noisy", "comments": [{"body": "ok"}]}
    assert "Grafana" in domains.tag(domains.ticket_text(row))
    assert domains.tag(domains.ticket_text({"summary": "Help please", "comments": None})) == []


def _done(key, person, domain, created, closed):
    return {"key": key, "assignee_name": person, "outcome": "Done", "domains": [domain] if domain else [],
            "domain": domain, "created_day": pd.Timestamp(created), "closed_day": pd.Timestamp(closed)}


def test_back_test_uses_only_earlier_work():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports.assignment_report import domain_accuracy
    today = pd.Timestamp("2026-11-17")
    history = [_done(f"H{i}", "Ana", "Fastly", "2026-01-05", "2026-01-10") for i in range(3)]
    history += [_done(f"B{i}", p, "GKE", "2026-01-05", "2026-01-12") for i, p in enumerate(["Ben", "Cy", "Dee", "Eve"] * 3)]
    recent = [
        _done("R1", "Ana", "Fastly", "2026-11-01", "2026-11-05"),   # Ana is the Fastly expert: hit
        _done("R2", "Eve", "Fastly", "2026-11-01", "2026-11-06"),   # nobody but Ana knew Fastly before: miss
        _done("R3", "Ben", None, "2026-11-01", "2026-11-07"),       # no domain: a miss
        # R5: nobody had Looker work before it was created, so a miss. It was done before R4 was created, so it
        # makes Fay the Looker expert for R4: a hit.
        _done("R5", "Fay", "Looker", "2026-10-01", "2026-10-20"),
        _done("R4", "Fay", "Looker", "2026-11-02", "2026-11-08"),
    ]
    t = pd.DataFrame(history + recent)
    acc = domain_accuracy(t, today, ["Ana", "Ben", "Cy", "Dee", "Eve", "Fay"], days=90)
    assert acc["n"] == 5 and acc["tagged"] == 4
    assert acc["hit"] == pytest.approx(2 / 5)                 # R1 and R4 (R1's own Fastly work came after R2)
    by = acc["by_domain_df"].set_index("Domain")
    assert by.loc["No domain", "Top-3 Hit %"] == 0 and by.loc["Fastly", "Tickets"] == 2
    assert 0 <= acc["baseline"] <= 1 and acc["first"] <= acc["hit"]
    assert domain_accuracy(t.iloc[0:0], today, ["Ana"])["n"] == 0
    # The baseline counts tickets, not ticket-domain pairs: extra domains on old work must not change it.
    more = t.copy()
    more["domains"] = [d + ["Grafana", "IAM/RBAC"] if k.startswith("B") else d for k, d in zip(more["key"], more["domains"])]
    assert domain_accuracy(more, today, ["Ana", "Ben", "Cy", "Dee", "Eve", "Fay"], days=90)["baseline"] == acc["baseline"]
