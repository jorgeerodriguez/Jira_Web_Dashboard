"""Knowledge Graph: the relationships it draws and the takeaways it lists.

Pinned: self-created tickets are counted but not drawn; a requester sending most of their work to one
engineer is flagged (automation requesters are marked); reply edges come from consecutive comments by
different people; two separate conversation groups are found as two communities and the person linking
them has the top bridge score; domains held by one person are listed, with a flag when that person has
left; skill pairs are ranked by lift; and filters and focus narrow the drawings.
"""
from datetime import timedelta, timezone

import pandas as pd
import pytest

pytest.importorskip("networkx")
pytest.importorskip("plotly")
pytest.importorskip("holidays")

from reports import knowledge_graph as kg  # noqa: E402

_LOCAL = timezone(timedelta(hours=-6))


def _t(rows):
    return pd.DataFrame(rows, columns=["key", "reporter_name", "assignee_name", "summary", "comments"])


def _c(author, minute, body=""):
    return {"author": author, "created": pd.Timestamp("2026-10-01", tz="UTC") + pd.Timedelta(minutes=minute), "body": body}


def test_who_asks_whom_counts_self_created_and_flags_dependency():
    rows = [(f"D-{i}", "Dee", "Ana" if i < 9 else "Ben", "", []) for i in range(12)]          # 75% to Ana
    rows += [(f"S-{i}", "Ana", "Ana", "", []) for i in range(4)]                               # self-created
    rows += [(f"B-{i}", "Agentcore PE", "Ben", "", []) for i in range(10)]                     # automation, 100% Ben
    rows += [("U-1", "Eve", "Unassigned", "", [])]
    edges, stats = kg.requester_edges(_t(rows))
    assert stats["self_created"] == 4 and stats["tickets"] == 26
    assert set(edges["requester"]) == {"Dee", "Agentcore PE"}
    dep = kg.requester_dependency(edges).set_index("Requester")
    assert dep.loc["Dee", "Depends on One Engineer"] == "yes" and dep.loc["Dee", "Top Engineer"] == "Ana"
    assert dep.loc["Agentcore PE (automation)", "Top Engineer Share %"] == 100

    fig = kg.requester_graph_figure(edges, min_tickets=4)
    links = sum(tr.x.count(None) for tr in fig.data if tr.mode == "lines")
    assert links == 2                                                                # Dee->Ben (3 tickets) hidden
    assert kg.requester_graph_figure(edges, min_tickets=1) is not None
    focus = kg.requester_graph_figure(edges, min_tickets=1, focus="Ben")
    assert {n for tr in focus.data if tr.mode == "markers+text" for n in tr.text} == {"Dee", "Agentcore PE", "Ben"}
    assert kg.requester_sankey(edges) is not None


def test_conversation_groups_and_bridges():
    left = [_c(a, i) for i, a in enumerate(["Ana", "Ben", "Ana", "Cy", "Ben", "Cy"] * 2)]
    right = [_c(a, i) for i, a in enumerate(["Xi", "Yo", "Xi", "Zed", "Yo", "Zed"] * 2)]
    link = [_c("Cy", 0), _c("Xi", 1)]                                                        # Cy and Xi link the groups
    G = kg.weighted(kg.conversation_graph(_t([("L", "", "", "", left), ("R", "", "", "", right), ("K", "", "", "", link)])))
    assert G["Ana"]["Ben"]["weight"] == 4 and G.has_edge("Cy", "Xi")
    fig, kpis, bridges = kg.conversation_outputs(G, pe_people={"Ana", "Ben", "Cy"}, min_replies=1)
    assert kpis["communities"] == 2 and kpis["modularity"] > 0.3
    assert set(bridges["Person"].head(2)) == {"Cy", "Xi"}
    assert bridges.set_index("Person").loc["Xi", "PE Engineer"] == ""
    fig, _, _ = kg.conversation_outputs(G, set(), min_replies=1, focus="Ana")
    assert {n for tr in fig.data if tr.mode == "markers+text" for n in tr.text} == {"Ana", "Ben", "Cy"}


def test_skills_single_person_domains_and_pairs(monkeypatch):
    monkeypatch.setattr(kg, "DEPARTED_MEMBERS", {"old timer"})
    rows = [(f"L-{i}", "R", "Old Timer", "Looker dashboard model", []) for i in range(4)]
    rows += [(f"L2", "R", "Ana", "Looker access", [])]
    rows += [(f"A-{i}", "R", p, "Airflow DAG on Composer", []) for i, p in enumerate(["Ana", "Ben", "Ana", "Ben"] * 2)]
    edges, tags = kg.skill_data(_t(rows))
    single = kg.single_person_domains(edges).set_index("Domain")
    assert "Looker" in single.index and single.loc["Looker", "Main Person"] == "Old Timer"
    assert single.loc["Looker", "Main Person Left PE"] == "yes"
    assert "Airflow" not in single.index                                            # Ana and Ben both hold it
    pairs = kg.domain_pairs(tags)
    top = pairs.iloc[0]
    assert {top["Domain A"], top["Domain B"]} == {"Airflow", "Composer"} and top["Lift"] > 1
    assert kg.skills_figure(edges, group="Security") is None
    assert kg.skills_figure(edges, focus="Looker") is not None


def test_build_handles_empty_and_real_shaped_data():
    assert kg.build_knowledge_graph(pd.DataFrame())["error_message"]
    now = pd.Timestamp.now(tz=_LOCAL).tz_localize(None).normalize()
    issues = pd.DataFrame([{
        "key": f"PE-{i}", "id": str(i), "status": "Done", "issuetype": "Story", "project_name": "DevOps",
        "assignee_name": "Ana", "reporter_name": "Dee", "priority_name": "Medium", "estimated_size_name": "Small",
        "business_lead": "Lead", "summary": "GKE upgrade", "created": (now - pd.Timedelta(days=20)).tz_localize(_LOCAL),
        "updated": now.tz_localize(_LOCAL), "status_category_changed": pd.NaT, "planned_start_date": pd.NaT,
        "target_end_date": pd.NaT, "comments": [_c("Dee", 0), _c("Ana", 1)]} for i in range(3)])
    out = kg.build_knowledge_graph(issues, months=3)
    assert out["error_message"] is None and out["tickets"] == 3
    assert out["ask_stats"]["pairs"] == 1 and out["conversation_graph"].has_edge("Dee", "Ana")


def test_mentions_signals_and_strongest_pairs():
    names = {"id-ben": "Ben", "id-cy": "Cy"}
    rows = [("T1", "", "", "", [_c("Ana", 0, "[~accountid:id-ben] can you check?"), _c("Ben", 1, "done"),
                                _c("Ana", 2, "thanks [~accountid:id-ben] and [~accountid:id-cy]")]),
            ("T2", "", "", "", [_c("Ana", 0, "[~accountid:id-ben] again"), _c("Dan", 1, "[~accountid:unknown] hi")])]
    G = kg.conversation_graph(_t(rows), names)
    assert (G["Ana"]["Ben"]["replies"], G["Ana"]["Ben"]["mentions"]) == (2, 3)    # mentions counted once per comment
    assert G["Ana"]["Ben"]["tickets"] == {"T1", "T2"} and G["Ana"]["Cy"]["mentions"] == 1
    assert kg.weighted(G, "Replies")["Ana"]["Ben"]["weight"] == 2
    assert not kg.weighted(G, "Replies").has_edge("Ana", "Cy")                       # mention-only pair dropped
    both = kg.weighted(G)
    assert both["Ana"]["Ben"]["weight"] == 5
    top = kg.strongest_pairs(both, 1)
    assert set(top.iloc[0][["Person", "Works With"]]) == {"Ana", "Ben"} and top.iloc[0]["Shared Tickets"] == 2
    assert set(kg.strongest_pairs(both, 5, focus="Cy").iloc[0][["Person", "Works With"]]) == {"Ana", "Cy"}
    fig, kpis, _ = kg.conversation_outputs(both, {"Ana"}, top_n=1)
    links = sum(tr.x.count(None) for tr in fig.data if tr.mode == "lines")
    assert links == 1 and kpis["pairs"] == both.number_of_edges()                    # KPIs use every link
    assert {n for tr in fig.data if tr.mode == "markers+text" for n in tr.text} == {"Ana", "Ben"}   # all labelled


def test_account_names_come_from_authors_assignees_and_reporters():
    df = pd.DataFrame({"assignee_account_id": ["a1", None], "assignee_name": ["Ana", "Unassigned"],
                       "reporter_account_id": ["r1", "a1"], "reporter_name": ["Rae", "Ana"],
                       "comments": [[{"author": "Cy", "author_id": "c1"}], []]})
    assert kg.account_names(df) == {"a1": "Ana", "r1": "Rae", "c1": "Cy"}


def test_leaving_out_a_hub_reveals_how_everyone_else_connects():
    # Hub talks to everyone; without the hub, two separate pairs remain.
    rows = [(f"T{i}", "", "", "", [_c("Hub", 0), _c(p, 1), _c("Hub", 2)]) for i, p in enumerate(["Ana", "Ben", "Cy", "Dee"])]
    rows += [("P1", "", "", "", [_c("Ana", 0), _c("Ben", 1)]), ("P2", "", "", "", [_c("Cy", 0), _c("Dee", 1)])]
    G = kg.weighted(kg.conversation_graph(_t(rows)))
    _, _, bridges = kg.conversation_outputs(G, set(), 1)
    assert bridges.iloc[0]["Person"] == "Hub"
    rest = kg.without(G, ["Hub", "Nobody"])
    assert set(rest.nodes) == {"Ana", "Ben", "Cy", "Dee"}
    _, kpis, _ = kg.conversation_outputs(rest, set(), 1)
    assert kpis["communities"] == 2 and kpis["pairs"] == 2
    assert set(kg.without(G, ["Hub", "Ana", "Ben"]).nodes) == {"Cy", "Dee"}       # people left with no links drop
    assert G.has_node("Hub")                                                          # the original is untouched
