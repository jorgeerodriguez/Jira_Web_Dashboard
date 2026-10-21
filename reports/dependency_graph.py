"""Dependencies: which tickets wait on which, from Jira issue links (Knowledge Graph page, Dependencies tab).

Built with NetworkX as a directed graph, blocker -> blocked, from two link types:
- "Blocks": "A blocks B" / "B is blocked by A".
- "Dependent": "B depends on A" / "A is depended on by B" (the same meaning, other wording).
Other link types (relates to, clones, causes ...) are not dependencies and are ignored.

Links come from every fetched issue (DevOps and Release Management), and a blocker can be in any project, so work
waiting on other teams shows up. A ticket counts as open unless Jira puts its status in the Done category (or it is
closed as Done / Will Not Do / Released / Rolled Back).

What the tab reports about *open PE work*:
- Waiting on open work: open PE tickets with at least one open blocker.
- Can likely move: open PE tickets whose blockers are all done (the link may be stale).
- Waiting on other teams: open PE tickets blocked by an open ticket outside the DevOps project.
- Other teams waiting on PE: open tickets outside DevOps blocked by an open PE ticket.
- Top blockers: open tickets that hold up the most open work, directly and down the chain.
- Longest chain: the longest path of open tickets waiting on each other (the critical path).
- Circular dependencies: cycles, which are always a data error.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import in_progress_report as ipr

PE_PREFIX = "DEVOPS-"
WAITS_ON = {"is blocked by", "depends on"}         # the other ticket is the blocker
HOLDS_UP = {"blocks", "is depended on by"}         # this ticket is the blocker
DEPENDENCY_TYPES = {"Blocks", "Dependent"}
CLOSED_STATUSES = {"done", "will not do", "released successfully to production", "❌ rolled back", "closed",
                   "resolved", "cancelled", "canceled"}
EXCLUDED = {"feature", "initiative", "iniciative"}
COLORS = {"pe": "#2a78d6", "external": "#eb6834", "done": "#b0b8c6"}
STALLED, STALLED_STATUSES = "#e34948", {"blocked", "on hold"}      # red outline: Jira status Blocked / On Hold
INK = ipr.INK


def _is_open(status: str | None, category: str | None = None) -> bool:
    if category is not None:
        return category != "done"
    return str(status or "").strip().casefold() not in CLOSED_STATUSES


def build_graph(df_issues: pd.DataFrame) -> nx.DiGraph:
    """Directed graph blocker -> blocked with node attributes: status, open, pe, project, summary, assignee."""
    G = nx.DiGraph()
    if df_issues is None or df_issues.empty or "links" not in df_issues.columns:
        return G
    known = df_issues.set_index("key")

    def node(key, status=None, category=None, summary=None):
        if key in G:
            return
        if key in known.index:
            row = known.loc[key]
            G.add_node(key, status=row["status"], open=_is_open(row["status"]), summary=str(row.get("summary") or ""),
                       assignee=row.get("assignee_name", ""), priority=row.get("priority_name", ""),
                       issuetype=row.get("issuetype", ""))
        else:
            G.add_node(key, status=status or "?", open=_is_open(status, category), summary=str(summary or ""),
                       assignee="", priority="", issuetype="")
        G.nodes[key]["pe"] = key.startswith(PE_PREFIX)
        G.nodes[key]["project"] = key.split("-")[0]

    for key, links in zip(df_issues["key"], df_issues["links"]):
        if not isinstance(links, list):
            continue
        for link in links:
            if link.get("type") not in DEPENDENCY_TYPES or not link.get("key"):
                continue
            relation = str(link.get("relation") or "").strip()
            if relation in WAITS_ON:
                blocker, blocked = link["key"], key
            elif relation in HOLDS_UP:
                blocker, blocked = key, link["key"]
            else:
                continue
            node(key)
            node(link["key"], link.get("status"), link.get("status_category"), link.get("summary"))
            if blocker != blocked:
                G.add_edge(blocker, blocked)
    # Containers (Features, Initiatives) are tracked through their tickets: leave them out like everywhere else.
    G.remove_nodes_from([n for n, d in G.nodes(data=True) if str(d.get("issuetype", "")).casefold() in EXCLUDED])
    return G


def _open_pe(G: nx.DiGraph) -> list[str]:
    return [n for n, d in G.nodes(data=True) if d["pe"] and d["open"]]


def analyse(G: nx.DiGraph) -> dict:
    """KPIs and the action lists for open PE work."""
    open_nodes = {n for n, d in G.nodes(data=True) if d["open"]}
    O = G.subgraph(open_nodes).copy()                    # open work waiting on open work
    waiting, can_move, other_teams = [], [], []
    for n in _open_pe(G):
        blockers = list(G.predecessors(n))
        if not blockers:
            continue
        open_b = [b for b in blockers if G.nodes[b]["open"]]
        if open_b:
            waiting.append(n)
            ext = [b for b in open_b if not G.nodes[b]["pe"]]
            if ext:
                other_teams.append((n, ext))
        else:
            can_move.append((n, blockers))
    waiting_on_pe = []
    for n, d in G.nodes(data=True):
        if d["open"] and not d["pe"]:
            pe_blockers = [b for b in G.predecessors(n) if G.nodes[b]["open"] and G.nodes[b]["pe"]]
            if pe_blockers:
                waiting_on_pe.append((n, pe_blockers))
    cycles = [c for c in nx.simple_cycles(O)] if O.number_of_edges() else []
    longest = []
    if O.number_of_edges():
        D = nx.condensation(O) if cycles else O
        path = nx.dag_longest_path(D)
        longest = ([sorted(D.nodes[c]["members"])[0] for c in path] if cycles else path)
    kpis = {"waiting": len(waiting), "can_move": len(can_move), "other_teams": len(other_teams),
            "longest_chain": len(longest), "cycles": len(cycles), "links": G.number_of_edges(),
            "others_waiting_on_pe": len(waiting_on_pe),
            "open_pe": len(_open_pe(G))}

    def describe(keys):
        return ", ".join(f"{k} ({G.nodes[k]['status']})" for k in keys)

    def row(n, extra):
        d = G.nodes[n]
        return {"Ticket": ipr.JIRA_BROWSE_BASE_URL + n, "Status": d["status"], "Assignee": d["assignee"],
                "Priority": d["priority"], **extra, "Summary": d["summary"][:100]}

    base = ["Ticket", "Status", "Assignee", "Priority"]
    can_move_df = pd.DataFrame([row(n, {"Blocked By (all done)": describe(bs)}) for n, bs in can_move],
                               columns=base + ["Blocked By (all done)", "Summary"])
    other_df = pd.DataFrame([row(n, {"Waiting On": describe(bs),
                                     "Other Teams": ", ".join(sorted({G.nodes[b]['project'] for b in bs}))})
                             for n, bs in other_teams], columns=base + ["Waiting On", "Other Teams", "Summary"])
    on_pe_df = pd.DataFrame([row(n, {"Team": G.nodes[n]["project"], "Waiting On PE Ticket": describe(bs)})
                             for n, bs in waiting_on_pe], columns=base + ["Team", "Waiting On PE Ticket", "Summary"])
    tops = []
    for n in O.nodes:
        direct = sum(1 for _ in O.successors(n))
        if not direct:
            continue
        down = nx.descendants(O, n)
        tops.append({"Blocker": ipr.JIRA_BROWSE_BASE_URL + n,
                     "Project": G.nodes[n]["project"], "Status": G.nodes[n]["status"],
                     "Assignee": G.nodes[n]["assignee"], "Blocks Directly": direct,
                     "Holds Up In Total": len(down),
                     "PE Tickets Held Up": sum(1 for k in down if G.nodes[k]["pe"]),
                     "Summary": G.nodes[n]["summary"][:100]})
    top_df = pd.DataFrame(tops, columns=["Blocker", "Project", "Status", "Assignee", "Blocks Directly",
                                         "Holds Up In Total", "PE Tickets Held Up", "Summary"])
    if not top_df.empty:
        top_df = top_df.sort_values(["Holds Up In Total", "Blocks Directly"], ascending=False).reset_index(drop=True)
    return {"kpis": kpis, "can_move_df": can_move_df, "other_teams_df": other_df, "top_blockers_df": top_df,
            "others_waiting_df": on_pe_df,
            "longest_chain": longest, "cycles": cycles[:10]}


# ── Drawing ─────────────────────────────────────────────────────────────────────

def _layered(G: nx.DiGraph) -> dict:
    """Left-to-right layers (blockers left of what they block), one band per connected group, stacked."""
    pos, y0 = {}, 0.0
    comps = sorted(nx.weakly_connected_components(G), key=len, reverse=True)
    for comp in comps:
        H = G.subgraph(comp)
        C = nx.condensation(H)
        layers = list(nx.topological_generations(C))
        height = max(sum(len(C.nodes[c]["members"]) for c in layer) for layer in layers)
        for x, layer in enumerate(layers):
            members = sorted(m for c in layer for m in C.nodes[c]["members"])
            for i, m in enumerate(members):
                pos[m] = (float(x), y0 - (i + (height - len(members)) / 2.0))
        y0 -= height + 1.0
    return pos


def figure(G: nx.DiGraph, focus: str | None = None, include_done: bool = True) -> go.Figure | None:
    """Open PE work and everything it waits on or holds up; or the whole chain around `focus`."""
    if G.number_of_nodes() == 0:
        return None
    if focus and focus in G:
        keep = {focus} | nx.ancestors(G, focus) | nx.descendants(G, focus)
    else:
        keep = set()
        for n in _open_pe(G):
            if G.in_degree(n) or any(G.nodes[s]["open"] for s in G.successors(n)):
                keep |= {n} | set(G.predecessors(n)) | {s for s in G.successors(n) if G.nodes[s]["open"]}
    if not include_done and not focus:
        keep = {n for n in keep if G.nodes[n]["open"]}
    H = G.subgraph(keep).copy()
    H.remove_nodes_from(list(nx.isolates(H)))
    if H.number_of_nodes() == 0:
        return None
    pos = _layered(H)
    fig = go.Figure()
    for u, v in H.edges:
        fig.add_annotation(x=pos[v][0] - 0.06, y=pos[v][1], ax=pos[u][0] + 0.06, ay=pos[u][1], xref="x", yref="y",
                           axref="x", ayref="y", showarrow=True, arrowhead=3, arrowsize=1.2, arrowwidth=1.4,
                           arrowcolor="rgba(100,116,139,0.6)", text="")
    groups = [("Open PE ticket", lambda d: d["open"] and d["pe"], COLORS["pe"], "circle"),
              ("Open, other team", lambda d: d["open"] and not d["pe"], COLORS["external"], "square"),
              ("Done", lambda d: not d["open"], COLORS["done"], "circle-open")]
    for name, test, color, symbol in groups:
        nodes = [n for n in H if test(H.nodes[n])]
        if not nodes:
            continue
        fig.add_trace(go.Scatter(
            x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes], mode="markers+text", text=nodes,
            textposition="top center", textfont=dict(size=10),
            marker=dict(size=[18 if n == focus else 13 for n in nodes], color=color, symbol=symbol,
                        line=dict(color=[STALLED if str(H.nodes[n]["status"]).casefold() in STALLED_STATUSES
                                         else ("white" if symbol != "circle-open" else color) for n in nodes], width=2.5)),
            name=name, customdata=np.array([[H.nodes[n]["status"], H.nodes[n]["assignee"] or "—",
                                             H.nodes[n]["summary"][:80]] for n in nodes], dtype=object),
            hovertemplate="<b>%{text}</b> · %{customdata[0]}<br>%{customdata[1]}<br>%{customdata[2]}<extra></extra>"))
    span_y = min(p[1] for p in pos.values())
    fig.update_layout(height=max(360, int(-span_y * 44) + 160), margin=dict(l=20, r=20, t=30, b=20),
                      legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom", title_text=""),
                      plot_bgcolor="rgba(0,0,0,0)", hovermode="closest")
    fig.update_xaxes(visible=False, range=[-0.6, max(p[0] for p in pos.values()) + 0.6])
    fig.update_yaxes(visible=False, range=[span_y - 0.8, 0.8])
    return fig
