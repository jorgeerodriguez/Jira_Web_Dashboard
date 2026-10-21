"""Dependencies (Knowledge Graph page): from Jira issue links to what waits on what.

Pinned: the loader keeps each link's type, wording, direction and the other ticket's status; "blocks",
"is blocked by" and "depends on" become blocker -> blocked edges (both sides of a link agree), other link types
are ignored and containers are left out; and the lists are right: waiting on open work, can likely move (all
blockers done), waiting on other teams, other teams waiting on PE, top blockers counted down the chain, the
longest chain, and circular dependencies.
"""
import pandas as pd
import pytest

from data.build_dataframe_new import _issue_links


def test_loader_reads_link_type_direction_and_other_status():
    fields = {"issuelinks": [
        {"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
         "outwardIssue": {"key": "ST-1", "fields": {"status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                                                    "summary": "s"}}},
        {"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
         "inwardIssue": {"key": "DEVOPS-2", "fields": {"status": {"name": "Done", "statusCategory": {"key": "done"}}}}},
        {"type": {"name": "Relates"}},                                                 # no other issue: skipped
    ]}
    links = _issue_links(fields)
    assert [(l["relation"], l["direction"], l["key"], l["status_category"]) for l in links] == [
        ("blocks", "outward", "ST-1", "indeterminate"), ("is blocked by", "inward", "DEVOPS-2", "done")]
    assert _issue_links({}) == []


@pytest.fixture
def dg():
    pytest.importorskip("networkx")
    pytest.importorskip("plotly")
    pytest.importorskip("holidays")
    from reports import dependency_graph
    return dependency_graph


def _link(kind, relation, key, status="To Do", category=None):
    return {"type": kind, "relation": relation, "key": key, "status": status,
            "status_category": category or ("done" if status == "Done" else "new"), "summary": key}


def _issue(key, status="To Do", links=(), issuetype="Story"):
    return {"key": key, "status": status, "summary": key, "assignee_name": "Ana", "priority_name": "Medium",
            "issuetype": issuetype, "links": list(links)}


def _df():
    return pd.DataFrame([
        # Chain: A blocks B, B blocks C (written from both sides, which must not duplicate edges).
        _issue("DEVOPS-A", links=[_link("Blocks", "blocks", "DEVOPS-B")]),
        _issue("DEVOPS-B", "In Progress", [_link("Blocks", "is blocked by", "DEVOPS-A"), _link("Blocks", "blocks", "DEVOPS-C")]),
        _issue("DEVOPS-C", links=[_link("Dependent", "depends on", "DEVOPS-B", "In Progress")]),
        # Blocked only by finished work.
        _issue("DEVOPS-D", links=[_link("Blocks", "is blocked by", "DEVOPS-X", "Done")]),
        _issue("DEVOPS-X", "Done"),
        # Waiting on another team; and another team waiting on PE.
        _issue("DEVOPS-E", links=[_link("Blocks", "is blocked by", "ST-9", "In Progress", "indeterminate"),
                                  _link("Blocks", "blocks", "APP-3", "To Do")]),
        # Not dependencies, and a container.
        _issue("DEVOPS-F", links=[_link("Relates", "relates to", "DEVOPS-A"), _link("Cloners", "clones", "DEVOPS-B")]),
        _issue("DEVOPS-G", issuetype="Feature", links=[_link("Blocks", "blocks", "DEVOPS-A")]),
    ])


def test_edges_follow_blocker_to_blocked(dg):
    G = dg.build_graph(_df())
    assert set(G.edges) == {("DEVOPS-A", "DEVOPS-B"), ("DEVOPS-B", "DEVOPS-C"), ("DEVOPS-X", "DEVOPS-D"),
                            ("ST-9", "DEVOPS-E"), ("DEVOPS-E", "APP-3")}
    assert not G.nodes["ST-9"]["pe"] and G.nodes["ST-9"]["open"] and G.nodes["ST-9"]["project"] == "ST"
    assert not G.nodes["DEVOPS-X"]["open"] and "DEVOPS-F" not in G and "DEVOPS-G" not in G


def test_takeaways(dg):
    a = dg.analyse(dg.build_graph(_df()))
    k = a["kpis"]
    assert (k["waiting"], k["can_move"], k["other_teams"], k["others_waiting_on_pe"]) == (3, 1, 1, 1)
    assert a["longest_chain"] == ["DEVOPS-A", "DEVOPS-B", "DEVOPS-C"] and k["cycles"] == 0
    assert a["can_move_df"]["Ticket"].str.endswith("DEVOPS-D").all()
    assert a["other_teams_df"].iloc[0]["Other Teams"] == "ST"
    assert a["others_waiting_df"].iloc[0]["Team"] == "APP"
    top = a["top_blockers_df"].set_index(a["top_blockers_df"]["Blocker"].str.split("/").str[-1])
    assert top.loc["DEVOPS-A", "Holds Up In Total"] == 2 and top.loc["DEVOPS-A", "Blocks Directly"] == 1


def test_cycles_are_reported_and_figures_focus(dg):
    df = pd.DataFrame([_issue("DEVOPS-1", links=[_link("Blocks", "blocks", "DEVOPS-2")]),
                       _issue("DEVOPS-2", links=[_link("Blocks", "blocks", "DEVOPS-1")])])
    a = dg.analyse(dg.build_graph(df))
    assert a["kpis"]["cycles"] == 1 and a["kpis"]["longest_chain"] >= 1
    G = dg.build_graph(_df())
    full = dg.figure(G)
    names = {n for tr in full.data for n in tr.text}
    assert {"DEVOPS-A", "DEVOPS-C", "ST-9", "APP-3", "DEVOPS-X"} <= names
    assert "DEVOPS-X" not in {n for tr in dg.figure(G, include_done=False).data for n in tr.text}
    focus = {n for tr in dg.figure(G, focus="DEVOPS-B").data for n in tr.text}
    assert focus == {"DEVOPS-A", "DEVOPS-B", "DEVOPS-C"}
    assert dg.build_graph(pd.DataFrame([{"key": "K"}])).number_of_edges() == 0           # no links column
