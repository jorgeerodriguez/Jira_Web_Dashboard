"""Suggested Assignments, learning section (phase 1): descriptions for tagging and the domain back-test.

Pinned: the loader keeps a cleaned description (links removed, ADF flattened, trimmed); every page tags from title +
description + comments; the back-test only uses work finished before each ticket was created, counts untagged tickets
as misses, and compares against the busiest-people baseline.
"""
import re

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


# ── Phase 2: suggested domain updates ───────────────────────────────────────────

def _ticket(key, summary, description="", assignee="Ana", project="DevOps", days_ago=20):
    from reports import in_progress_report as ipr
    from tests.test_ticket_story import _bd, _issue
    row = _issue(key, "To Do", _bd(ipr, days_ago), project=project, assignee=assignee, summary=summary)
    row["description_text"] = description
    return row


def _suggestion_data():
    rows = [_ticket(f"DEVOPS-{i}", f"Grafana panel zorbex tuning {i}") for i in range(6)]          # in Grafana
    rows += [_ticket(f"DEVOPS-{10 + i}", f"Zorbex rollout step {i}") for i in range(2)]           # would be added
    rows += [_ticket(f"CAR-{20 + i}", f"Quokka console {i}", project="Release Management") for i in range(9)]
    rows += [_ticket(f"DEVOPS-{40 + i}", f"Fumbleton asked again {i}", assignee="Zed Fumbleton") for i in range(8)]
    rows += [_ticket(f"DEVOPS-{60 + i}", f"wibblefish other{i}", "screenshot width alt") for i in range(8)]
    rows += [_ticket(f"DEVOPS-{100 + i}", f"filler{i} thing{i}") for i in range(90)]
    return pd.DataFrame(rows)


def test_suggestions_find_keywords_and_new_themes():
    pytest.importorskip("networkx")
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import domain_suggestions as ds
    s = ds.build_domain_suggestions(_suggestion_data())
    kw = s["keywords_df"]
    row = kw[(kw["Domain"] == "Grafana") & (kw["Add Keyword"] == "zorbex")].iloc[0]
    assert row["Would Add"] == 2 and row["Already In Domain"] == 6 and row["Precision %"] == 75
    assert row["Pattern"] == r"\bzorbex\b" and "DEVOPS-1" in row["Examples"]
    assert re.search(ds.pattern("adtech vader"), "AW - ADTECH - Vader: scale", re.I)       # punctuation between words
    assert ds.pattern("entercom-prod") == r"\bentercom-prod\b"
    new = s["new_df"]
    quokka = new[new["Keywords"].str.contains("quokka")].iloc[0]
    assert quokka["Tickets"] == 9 and quokka["CAR"] == 9 and quokka["Untagged"] == 9
    every = " ".join(kw["Add Keyword"]) + " " + " ".join(new["Keywords"])
    assert "fumbleton" not in every and "screenshot" not in every            # names and image markup are ignored
    assert "wibblefish" not in every                                          # a lone word is not a theme
    assert repr(ds.pattern("quokka")) in s["new_snippet"] and "DOMAIN_PRIORITY" in s["new_snippet"]
    assert ds.pattern("node pool") == r"\bnode\W+pool\b" and s["what_if"] is None    # no team given: no back-test


def test_back_test_can_score_a_subset():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports.assignment_report import domain_accuracy
    t = pd.DataFrame([_done("H1", "Ana", "Fastly", "2026-01-05", "2026-01-10"),
                      _done("R1", "Ana", "Fastly", "2026-11-01", "2026-11-05"),
                      _done("R2", "Ben", "GKE", "2026-11-01", "2026-11-05")])
    full = domain_accuracy(t, pd.Timestamp("2026-11-17"), ["Ana", "Ben"])
    part = domain_accuracy(t, pd.Timestamp("2026-11-17"), ["Ana", "Ben"], keys={"R1"})
    assert (full["n"], full["hits"]) == (2, 1) and (part["n"], part["hits"]) == (1, 1)


# ── Phase 3: splits, hidden expertise, applications ─────────────────────────────

def _comment(author, days_ago=15):
    from reports import in_progress_report as ipr
    from tests.test_ticket_story import _bd
    return {"author": author, "author_id": None, "body": "looked at this",
            "created": (_bd(ipr, days_ago) + pd.Timedelta(hours=10)).tz_localize("UTC")}


def test_split_finds_separate_kinds_of_work(monkeypatch):
    pytest.importorskip("networkx")
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import skill_learning as sl
    monkeypatch.setattr(sl, "SPLIT_MIN_DOMAIN", 20)
    rows = [_ticket(f"DEVOPS-{i}", f"Grafana zorbex quibble panel {i}", assignee="Ana") for i in range(12)]
    rows += [_ticket(f"DEVOPS-{20 + i}", f"Grafana wombat frizzle tuning {i}", assignee="Ben") for i in range(12)]
    rows += [_ticket(f"DEVOPS-{40 + i}", f"Grafana misc{i} other{i}", assignee="Ana") for i in range(30)]
    splits = sl.split_candidates(pd.DataFrame(rows))
    themes = [set(x.split(" · ")) for x in splits["Sub-Theme"]]
    assert len(themes) == 2 and any({"zorbex", "quibble"} <= x for x in themes) and any({"wombat", "frizzle"} <= x for x in themes)
    wombat = splits[splits["Sub-Theme"].str.contains("wombat")].iloc[0]
    assert wombat["Domain"] == "Grafana" and wombat["Tickets"] == 12 and wombat["Different Expert"] == "yes"
    assert splits["Effect"].isna().all()                                   # no team given: no back-test
    assert "Grafana: " in sl.split_snippet(splits.assign(Effect=1))


def test_hidden_expertise_counts_help_not_requests():
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import skill_learning as sl
    rows = []
    for i in range(4):
        row = _ticket(f"DEVOPS-{i}", f"Grafana alert {i}", assignee="Ana")
        row["reporter_name"] = "Rae"
        row["comments"] = [_comment("Ben"), _comment("Rae"), _comment("Ana")]
        rows.append(row)
    out = sl.hidden_expertise(pd.DataFrame(rows), ["Ana", "Ben", "Rae"])
    h = out["hidden_df"]
    assert list(h["Person"]) == ["Ben"] and h.iloc[0]["Helped On"] == 4 and h.iloc[0]["Assigned"] == 0
    assert out["what_if"]["help_rows"] == 4


def test_application_map_links_apps_to_people():
    pytest.importorskip("networkx")
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import skill_learning as sl
    df = _suggestion_data()
    df.loc[df["key"].str.startswith("CAR-"), "assignee_name"] = "Jo"
    df.loc[df["key"] == "CAR-20", "comments"] = pd.Series([[_comment("Kim")]], index=df.index[df["key"] == "CAR-20"])
    apps = sl.application_map(df)
    row = apps["apps_df"].set_index("Application").loc["Quokka"]
    assert row["Main Person"] == "Jo" and row["Worked On (Main)"] == 9 and row["People Involved"] == 2
    assert apps["matrix"].loc["Quokka", "Kim"] == 0.5 and sl.application_figure(apps["matrix"]) is not None


def test_every_domain_group_is_displayed_and_apps_come_first():
    assert set(domains.DOMAIN_GROUP.values()) <= set(domains.GROUP_ORDER)
    apps = [d for d in domains.DOMAIN_PRIORITY if domains.group_of(d) == "Applications"]
    assert apps and domains.DOMAIN_PRIORITY[:len(apps)] == apps          # a ticket naming its app is routed by it
    assert domains.tag("AW - ADTECH - Vader: scale log rotation") == ["Vader"]


def test_application_map_uses_the_applications_domains():
    pytest.importorskip("networkx")
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import skill_learning as sl
    rows = [_ticket(f"CAR-{i}", f"AW - ADTECH - Vader: change {i}", assignee="Jo", project="Release Management")
            for i in range(4)]
    rows += [_ticket(f"DEVOPS-{100 + i}", f"filler{i} thing{i}") for i in range(20)]
    apps = sl.application_map(pd.DataFrame(rows))["apps_df"].set_index("Application")
    assert apps.loc["Vader", "Source"] == "Domain" and apps.loc["Vader", "Main Person"] == "Jo"
    assert apps.loc["Vader", "Tickets"] == 4
