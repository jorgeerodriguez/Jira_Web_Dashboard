"""Knowledge Graph: relationships in PE work, analysed with NetworkX and drawn with Plotly.

PE tickets only (no Features, Initiatives or Release Management CAR tickets), created in the selected window.
Three views, each paired with a plain metric and a list so the picture has a takeaway:

- Who asks whom: requester (Jira reporter) -> engineer (assignee). Tickets an engineer created for themselves
  are counted but left out of the graph. Takeaway: requesters who depend on one engineer.
- Who talks to whom: two signals from human ticket comments (dashboard-posted comments are already removed by
  the loader). A *reply* links two people when one comments right after the other on a ticket; a *mention*
  links a comment's author to each person they @mention. Communities by Louvain modularity, bridges by
  betweenness centrality, and the strongest pairs. Takeaway: who connects the groups, whether work is siloed
  (modularity near 0 = well mixed), and who works together most.
- Skills map: engineers and the skill domains of their tickets (reports/domains.py taxonomy, tagged from
  titles and comments). Takeaways: domains that rest on one person, and domain pairs that go together more
  often than chance (lift).

Graphs are for balancing work and cross-training, not for rating people. Layouts use fixed seeds, so the
same data draws the same picture.
"""
from __future__ import annotations

import itertools
import re

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from networkx.algorithms import community as nx_community

from reports import domains
from reports.assignment_report import DEPARTED_MEMBERS
from reports import executive_summary as es
from reports import in_progress_report as ipr

WINDOW_OPTIONS = (3, 6, 12)
SEED = 7
DEPENDENCY_MIN_TICKETS = 10      # requesters with at least this many tickets are checked for dependency
DEPENDENCY_SHARE = 0.6           # ...and flagged when one engineer took this share or more
SKILL_MIN_TICKETS = 3            # a person "holds" a domain with this many tickets in it
PAIR_MIN_TICKETS = 5             # domain pairs need this many shared tickets to be listed
# Requesters that are automation, not people (shown, but marked).
AUTOMATION_REQUESTERS = {"agentcore pe", "aws-devops-agent", "automation for jira", "pe-code-review"}
# Categorical palette, fixed order (validated; same slots as the rest of the dashboard).
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER_GREY = "#8891a7"
GROUP_COLORS = {"GCP": CATEGORICAL[0], "AWS": CATEGORICAL[1], "Security": CATEGORICAL[6], "Other": OTHER_GREY}
EDGE = "rgba(100,116,139,{a})"
LABEL_TOP = 18                   # label only the busiest people; the rest show their name on hover
GROUP_LABELS = 3                 # conversation graph: label the busiest people of each group
SMALL_GRAPH = 25                 # up to this many people: plain layout and every name labelled
SIGNALS = ("Replies + mentions", "Replies", "Mentions")
_MENTION = re.compile(r"\[~accountid:([^\]]+)\]")
INK = ipr.INK


def _scope(df_issues: pd.DataFrame, months: int) -> pd.DataFrame:
    today = ipr.today_local()
    facts = es._facts(df_issues, today, ipr._calendar_holidays(today))
    t = facts[facts["sla_applies"]]
    return t[t["created_day"] >= today - pd.DateOffset(months=int(months))].copy()


def _text(row) -> str:
    comments = row.get("comments")
    bodies = " ".join(c.get("body", "") for c in comments) if isinstance(comments, list) else ""
    return f"{row.get('summary') or ''} {bodies}"


def _ego(G: nx.Graph, focus: str | None) -> nx.Graph:
    if not focus or focus not in G:
        return G
    return G.subgraph({focus, *G.neighbors(focus)} | (set(G.predecessors(focus)) if G.is_directed() else set())).copy()


# ── Drawing ─────────────────────────────────────────────────────────────────────

def _edge_traces(G: nx.Graph, pos: dict, label: str = "tickets") -> list[go.Scatter]:
    """Edges in three width bands (one trace each), heavier = darker and wider."""
    weights = [d.get("weight", 1) for _, _, d in G.edges(data=True)]
    if not weights:
        return []
    cuts = np.quantile(weights, [0.5, 0.85]) if len(weights) > 2 else [max(weights), max(weights)]
    bands = [(0.8, 0.25), (2.0, 0.45), (4.0, 0.7)]
    traces = []
    for band, (width, alpha) in enumerate(bands):
        xs, ys = [], []
        for u, v, d in G.edges(data=True):
            w = d.get("weight", 1)
            b = 0 if w <= cuts[0] else 1 if w <= cuts[1] else 2
            if b == band:
                xs += [pos[u][0], pos[v][0], None]
                ys += [pos[u][1], pos[v][1], None]
        if xs:
            traces.append(go.Scatter(x=xs, y=ys, mode="lines", line=dict(width=width, color=EDGE.format(a=alpha)),
                                     hoverinfo="skip", showlegend=False))
    return traces


def _layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=70, r=70, t=30, b=30), hovermode="closest",
                      legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom", title_text=""),
                      plot_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def _positions(G: nx.Graph) -> dict:
    """Kamada-Kawai spaces dense, hub-heavy graphs more evenly than a spring layout; deterministic."""
    if G.number_of_nodes() <= 2:
        return nx.spring_layout(G, seed=SEED)
    dist = {u: {v: 1.0 / d["weight"] ** 0.5 for v, d in nbrs.items()} for u, nbrs in G.adjacency()}
    H = nx.Graph()
    H.add_nodes_from(G)
    H.add_edges_from((u, v, {"d": dist[u][v]}) for u, v in G.edges())
    parts = [H.subgraph(c) for c in sorted(nx.connected_components(H), key=len, reverse=True)]
    pos = nx.kamada_kawai_layout(parts[0], weight="d")
    for i, part in enumerate(parts[1:], start=1):   # small islands around the edge
        angle = 2 * np.pi * i / max(len(parts) - 1, 1)
        sub = nx.kamada_kawai_layout(part, weight="d") if part.number_of_nodes() > 2 else nx.spring_layout(part, seed=SEED)
        for n, (x, y) in sub.items():
            pos[n] = (1.35 * np.cos(angle) + 0.15 * x, 1.35 * np.sin(angle) + 0.15 * y)
    return pos


def _community_positions(G: nx.Graph, member: dict) -> dict:
    """One cluster per community: clusters placed by how much they talk to each other, people within each."""
    groups = {}
    for n in G:
        groups.setdefault(member[n], []).append(n)
    if len(groups) == 1:
        return _positions(G)
    between = nx.Graph()
    between.add_nodes_from(groups)
    for u, v, d in G.edges(data=True):
        a, b = member[u], member[v]
        if a != b:
            between.add_edge(a, b, weight=between.get_edge_data(a, b, {"weight": 0})["weight"] + d["weight"])
    centres = nx.circular_layout(between) if len(groups) > 2 else {k: (i * 2.0 - 1.0, 0.0) for i, k in enumerate(groups)}
    total = sum(len(v) for v in groups.values())
    pos = {}
    for g, nodes in groups.items():
        sub = _positions(G.subgraph(nodes))
        scale = 0.45 + 0.95 * np.sqrt(len(nodes) / total)
        cx, cy = centres[g]
        for n, (x, y) in sub.items():
            pos[n] = (2.6 * cx + scale * x, 2.6 * cy + scale * y)
    return pos


def _ring_positions(G: nx.Graph, domain_order: list[str]) -> dict:
    """Domains evenly on a ring (in `domain_order`), each engineer at the weighted centre of their domains."""
    pos = {}
    ring = [d for d in domain_order if d in G]
    for i, d in enumerate(ring):
        angle = np.pi / 2 - 2 * np.pi * i / max(len(ring), 1)
        pos[d] = (np.cos(angle), np.sin(angle))
    for n in G:
        if n in pos:
            continue
        nbrs = [(v, d["weight"]) for v, d in G[n].items() if v in pos]
        w = np.array([x for _, x in nbrs], dtype=float)
        xy = np.array([pos[v] for v, _ in nbrs])
        centre = (w[:, None] * xy).sum(axis=0) / w.sum()
        pos[n] = tuple(0.82 * centre)              # pulled inside the ring
    return pos


def _outside(xy) -> str:
    """Label position pointing away from the ring's centre."""
    x, y = xy
    vertical = "top" if y > 0.35 else "bottom" if y < -0.35 else "middle"
    horizontal = "right" if x > 0.35 else "left" if x < -0.35 else "center"
    return f"{vertical} {horizontal}" if vertical != "middle" or horizontal != "center" else "top center"


def _labels(nodes: list, strength: dict, always: set = frozenset(), top: int = LABEL_TOP, strip: int = 0) -> list[str]:
    keep = set(sorted(strength, key=lambda n: -strength[n])[:top]) | set(always)
    return [str(n)[strip:] if n in keep else "" for n in nodes]


def _sizes(values, lo: float = 8, hi: float = 34) -> list[float]:
    v = np.sqrt(np.asarray(list(values), dtype=float))
    if len(v) == 0 or v.max() == v.min():
        return [float((lo + hi) / 2)] * len(v)
    return list(lo + (v - v.min()) / (v.max() - v.min()) * (hi - lo))


# ── 1. Who asks whom ────────────────────────────────────────────────────────────

def requester_edges(t: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Requester -> engineer ticket counts, and how much work engineers created for themselves."""
    assigned = t[t["assignee_name"].ne("Unassigned")]
    reporter = assigned.get("reporter_name", pd.Series("Unknown", index=assigned.index)).fillna("Unknown")
    own = reporter.eq(assigned["assignee_name"])
    asked = assigned[~own & reporter.ne("Unknown")].assign(requester=reporter[~own & reporter.ne("Unknown")])
    edges = (asked.groupby(["requester", "assignee_name"]).size().rename("tickets").reset_index()
             .rename(columns={"assignee_name": "engineer"}))
    stats = {"tickets": int(len(assigned)), "self_created": int(own.sum()),
             "self_share": float(own.mean()) if len(assigned) else None,
             "requesters": int(edges["requester"].nunique()), "engineers": int(edges["engineer"].nunique()),
             "pairs": int(len(edges))}
    return edges, stats


def requester_dependency(edges: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for requester, g in edges.groupby("requester"):
        total = int(g["tickets"].sum())
        top = g.sort_values("tickets", ascending=False).iloc[0]
        rows.append({"Requester": requester + (" (automation)" if requester.casefold() in AUTOMATION_REQUESTERS else ""),
                     "Tickets": total, "Engineers": int(len(g)),
                     "Top Engineer": top["engineer"], "Top Engineer Share %": round(top["tickets"] / total * 100),
                     "Depends on One Engineer": "yes" if total >= DEPENDENCY_MIN_TICKETS
                     and top["tickets"] / total >= DEPENDENCY_SHARE else ""})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["Depends on One Engineer", "Tickets"], ascending=[False, False]).reset_index(drop=True)


def requester_graph_figure(edges: pd.DataFrame, min_tickets: int = 2, focus: str | None = None) -> go.Figure | None:
    """Two columns, requesters left and engineers right, ordered to reduce crossings (barycentre)."""
    e = edges[edges["tickets"] >= min_tickets]
    if focus:
        e = e[(e["requester"] == focus) | (e["engineer"] == focus)]
    if e.empty:
        return None
    eng = e.groupby("engineer")["tickets"].sum().sort_values(ascending=False)
    req = e.groupby("requester")["tickets"].sum()
    eng_rank = {n: i for i, n in enumerate(eng.index)}
    for _ in range(3):  # alternate barycentre passes
        req_y = e.assign(r=e["engineer"].map(eng_rank)).groupby("requester").apply(
            lambda g: np.average(g["r"], weights=g["tickets"]), include_groups=False).sort_values()
        req_rank = {n: i for i, n in enumerate(req_y.index)}
        eng_y = e.assign(r=e["requester"].map(req_rank)).groupby("engineer").apply(
            lambda g: np.average(g["r"], weights=g["tickets"]), include_groups=False).sort_values()
        eng_rank = {n: i for i, n in enumerate(eng_y.index)}
    rows = max(len(req_rank), len(eng_rank))

    def spread(rank, n):
        return {k: -(v + 0.5) * rows / max(n, 1) for k, v in rank.items()}

    ry, ey = spread(req_rank, len(req_rank)), spread(eng_rank, len(eng_rank))
    G = nx.Graph()
    pos = {}
    for r in req_rank:
        pos["R:" + r] = (0.0, ry[r])
    for a in eng_rank:
        pos["A:" + a] = (1.0, ey[a])
    for _, row in e.iterrows():
        G.add_edge("R:" + row["requester"], "A:" + row["engineer"], weight=int(row["tickets"]))
    fig = go.Figure(_edge_traces(G, pos))
    for side, names, totals, color, anchor in (("R:", list(req_rank), req, CATEGORICAL[1], "middle left"),
                                               ("A:", list(eng_rank), eng, CATEGORICAL[0], "middle right")):
        fig.add_trace(go.Scatter(
            x=[pos[side + n][0] for n in names], y=[pos[side + n][1] for n in names], mode="markers+text",
            text=names, textposition=anchor, textfont=dict(size=11),
            marker=dict(size=_sizes([totals[n] for n in names], 7, 26), color=color, line=dict(color="white", width=1.5)),
            name="Requesters" if side == "R:" else "Engineers",
            customdata=[int(totals[n]) for n in names],
            hovertemplate="%{text}: %{customdata} tickets<extra></extra>"))
    fig.update_xaxes(range=[-0.75, 1.75])
    return _layout(fig, max(380, 22 * rows + 80))


def requester_sankey(edges: pd.DataFrame, top_requesters: int = 15) -> go.Figure | None:
    if edges.empty:
        return None
    top = edges.groupby("requester")["tickets"].sum().sort_values(ascending=False).head(top_requesters).index
    e = edges[edges["requester"].isin(top)]
    reqs = list(top)
    engs = list(e.groupby("engineer")["tickets"].sum().sort_values(ascending=False).index)
    labels = reqs + engs
    idx = {("R", r): i for i, r in enumerate(reqs)} | {("A", a): len(reqs) + i for i, a in enumerate(engs)}
    fig = go.Figure(go.Sankey(
        node=dict(label=labels, pad=8, thickness=12, line=dict(width=0),
                  color=[CATEGORICAL[1]] * len(reqs) + [CATEGORICAL[0]] * len(engs)),
        link=dict(source=[idx[("R", r)] for r in e["requester"]], target=[idx[("A", a)] for a in e["engineer"]],
                  value=e["tickets"].tolist(), color="rgba(100,116,139,0.25)",
                  hovertemplate="%{source.label} → %{target.label}: %{value} tickets<extra></extra>"),
    ))
    fig.update_layout(height=max(420, 26 * max(len(reqs), len(engs)) + 60), margin=dict(l=10, r=10, t=10, b=10))
    return fig


# ── 2. Who talks to whom ────────────────────────────────────────────────────────

def account_names(df_issues: pd.DataFrame) -> dict[str, str]:
    """Jira account id -> display name, from comment authors, assignees and reporters."""
    names: dict[str, str] = {}
    for col_id, col_name in (("assignee_account_id", "assignee_name"), ("reporter_account_id", "reporter_name")):
        if col_id in df_issues.columns:
            known = df_issues[df_issues[col_id].notna()]
            names.update(zip(known[col_id], known[col_name]))
    for col in ("comments", "dashboard_comments"):
        for comments in df_issues.get(col, pd.Series(dtype=object)):
            if isinstance(comments, list):
                names.update({c["author_id"]: c["author"] for c in comments if c.get("author_id")})
    return names


def conversation_graph(t: pd.DataFrame, names: dict[str, str] | None = None) -> nx.Graph:
    """People linked by replies and @mentions in ticket comments.

    Edge attributes: replies (author changes between consecutive comments), mentions (a comment's author
    @mentioning the other person, either way round) and tickets (shared ticket keys). Undirected.
    """
    names = names or {}
    G = nx.Graph()

    def bump(a, b, kind, key):
        if a == b or "Unknown" in (a, b):
            return
        if not G.has_edge(a, b):
            G.add_edge(a, b, replies=0, mentions=0, tickets=set())
        G[a][b][kind] += 1
        G[a][b]["tickets"].add(key)

    for key, comments in zip(t["key"], t.get("comments", pd.Series([[]] * len(t), index=t.index))):
        if not isinstance(comments, list):
            continue
        ordered = sorted((c for c in comments if pd.notna(c.get("created"))), key=lambda c: c["created"])
        authors = [c.get("author", "Unknown") for c in ordered]
        for a, b in zip(authors, authors[1:]):
            bump(a, b, "replies", key)
        for c in ordered:
            for account in set(_MENTION.findall(c.get("body") or "")):
                if account in names:
                    bump(c.get("author", "Unknown"), names[account], "mentions", key)
    return G


def weighted(G: nx.Graph, signal: str = SIGNALS[0]) -> nx.Graph:
    """A copy whose `weight` is the chosen signal; pairs with none of it are dropped."""
    H = nx.Graph()
    for u, v, d in G.edges(data=True):
        w = (d["replies"] if signal != "Mentions" else 0) + (d["mentions"] if signal != "Replies" else 0)
        if w > 0:
            H.add_edge(u, v, weight=w, replies=d["replies"], mentions=d["mentions"], tickets=len(d["tickets"]))
    return H


def without(G: nx.Graph, people) -> nx.Graph:
    """The graph with `people` left out, and anyone left with no links dropped.

    Used to see how everyone else connects (e.g. without a team lead who is in most conversations); groups,
    bridges and strongest pairs are then recalculated on what remains.
    """
    H = G.copy()
    H.remove_nodes_from([p for p in (people or []) if p in H])
    H.remove_nodes_from(list(nx.isolates(H)))
    return H


def strongest_pairs(G: nx.Graph, top_n: int = 10, focus: str | None = None) -> pd.DataFrame:
    """The `top_n` heaviest links of a weighted graph (or of one person's links)."""
    edges = G.edges(focus, data=True) if focus and focus in G else G.edges(data=True)
    ranked = sorted(edges, key=lambda e: (-e[2]["weight"], -e[2]["tickets"]))[:top_n]
    return pd.DataFrame([{"Person": u, "Works With": v, "Strength": d["weight"], "Replies": d["replies"],
                          "Mentions": d["mentions"], "Shared Tickets": d["tickets"]} for u, v, d in ranked])


def conversation_outputs(G: nx.Graph, pe_people: set[str], min_replies: int = 2, focus: str | None = None,
                         top_n: int | None = None) -> tuple[go.Figure | None, dict, pd.DataFrame]:
    """Communities and bridges on the whole (weighted) graph; the drawing is filtered.

    `min_replies` hides weak links; `top_n` keeps only the strongest links (of `focus` when one is chosen).
    """
    if G.number_of_nodes() < 2:
        return None, {"people": G.number_of_nodes()}, pd.DataFrame()
    comms = sorted(nx_community.louvain_communities(G, weight="weight", seed=SEED), key=len, reverse=True)
    member = {n: i for i, c in enumerate(comms) for n in c}
    between = nx.betweenness_centrality(G)
    strength = dict(G.degree(weight="weight"))
    kpis = {"people": G.number_of_nodes(), "pairs": G.number_of_edges(), "communities": len(comms),
            "modularity": float(nx_community.modularity(G, comms, weight="weight"))}

    if top_n:
        edges = G.edges(focus, data=True) if focus and focus in G else G.edges(data=True)
        keep = sorted(edges, key=lambda e: (-e[2]["weight"], -e[2].get("tickets", 0)))[:top_n]
        H = nx.Graph([(u, v, d) for u, v, d in keep])
    else:
        H = _ego(nx.Graph([(u, v, d) for u, v, d in G.edges(data=True) if d["weight"] >= min_replies]), focus)
    fig = None
    if H.number_of_nodes():
        small = H.number_of_nodes() <= SMALL_GRAPH
        pos = _positions(H) if small else _community_positions(H, member)
        fig = go.Figure(_edge_traces(H, pos))
        shown = {n: strength[n] for n in H}
        for i in sorted({member[n] for n in H}):
            nodes = [n for n in H if member[n] == i]
            color = CATEGORICAL[i] if i < len(CATEGORICAL) - 1 else OTHER_GREY
            fig.add_trace(go.Scatter(
                x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes], mode="markers+text",
                text=_labels(nodes, {n: shown[n] for n in nodes}, {focus} if focus else set(),
                             top=len(nodes) if small else LABEL_TOP if focus else GROUP_LABELS), hovertext=nodes,
                textposition="top center", textfont=dict(size=10),
                marker=dict(size=_sizes([strength[n] for n in nodes]), color=color,
                            symbol=["circle" if n in pe_people else "diamond" for n in nodes],
                            line=dict(color="white", width=1.5)),
                name=f"Group {i + 1}", customdata=np.stack([[strength[n] for n in nodes],
                                                            [round(between[n], 3) for n in nodes]], axis=1),
                hovertemplate="%{hovertext}<br>strength %{customdata[0]} · bridge score %{customdata[1]}<extra></extra>"))
        _layout(fig, 640)

    top = sorted(between.items(), key=lambda kv: -kv[1])[:15]
    bridges = pd.DataFrame([{"Person": n, "Bridge Score": round(b, 3), "Group": member[n] + 1,
                             "People Linked With": G.degree(n), "Strength": int(strength[n]),
                             "PE Engineer": "yes" if n in pe_people else ""} for n, b in top])
    return fig, kpis, bridges


# ── 3. Skills map ───────────────────────────────────────────────────────────────

def skill_data(t: pd.DataFrame) -> tuple[pd.DataFrame, list[list[str]]]:
    """(person, domain, tickets) for assigned tickets, and each ticket's domains (for pair analysis)."""
    tags = [domains.tag(_text(r)) for _, r in t.iterrows()]
    rows = [(a, d) for a, ts in zip(t["assignee_name"], tags) if a != "Unassigned" for d in ts]
    edges = (pd.DataFrame(rows, columns=["person", "domain"]).groupby(["person", "domain"]).size()
             .rename("tickets").reset_index()) if rows else pd.DataFrame(columns=["person", "domain", "tickets"])
    return edges, tags


def single_person_domains(edges: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d, g in edges.groupby("domain"):
        holders = g[g["tickets"] >= SKILL_MIN_TICKETS].sort_values("tickets", ascending=False)
        total = int(g["tickets"].sum())
        if len(holders) <= 1 and total >= SKILL_MIN_TICKETS:
            top = g.sort_values("tickets", ascending=False).iloc[0]
            rows.append({"Domain": d, "Group": domains.group_of(d), "Tickets": total,
                         "People With 3+ Tickets": int(len(holders)), "Main Person": top["person"],
                         "Main Person Share %": round(top["tickets"] / total * 100),
                         "Main Person Left PE": "yes" if str(top["person"]).strip().casefold() in DEPARTED_MEMBERS else "",
                         "Others With Some Experience": int((g["tickets"] < SKILL_MIN_TICKETS).sum())})
    out = pd.DataFrame(rows)
    return out.sort_values("Tickets", ascending=False).reset_index(drop=True) if len(out) else out


def domain_pairs(tags: list[list[str]], top_n: int = 15) -> pd.DataFrame:
    """Domain pairs on the same tickets more often than chance: lift = P(a,b) / (P(a) P(b))."""
    n = len(tags)
    if not n:
        return pd.DataFrame()
    single, pair = {}, {}
    for ts in tags:
        s = sorted(set(ts))
        for d in s:
            single[d] = single.get(d, 0) + 1
        for a, b in itertools.combinations(s, 2):
            pair[(a, b)] = pair.get((a, b), 0) + 1
    rows = [{"Domain A": a, "Domain B": b, "Shared Tickets": c, "Lift": round(c * n / (single[a] * single[b]), 1)}
            for (a, b), c in pair.items() if c >= PAIR_MIN_TICKETS]
    out = pd.DataFrame(rows)
    return out.sort_values(["Lift", "Shared Tickets"], ascending=False).head(top_n).reset_index(drop=True) if len(out) else out


def skills_figure(edges: pd.DataFrame, group: str | None = None, min_tickets: int = SKILL_MIN_TICKETS,
                  focus: str | None = None) -> go.Figure | None:
    e = edges[edges["tickets"] >= min_tickets]
    if group:
        e = e[[domains.group_of(d) == group for d in e["domain"]]]
    if e.empty:
        return None
    G = nx.Graph()
    for _, r in e.iterrows():
        G.add_edge("P:" + r["person"], "D:" + r["domain"], weight=int(r["tickets"]))
    G = _ego(G, ("P:" + focus) if focus and ("P:" + focus) in G else ("D:" + focus) if focus else None)
    strength = dict(G.degree(weight="weight"))
    order = sorted((n for n in G if n.startswith("D:")),
                   key=lambda n: (["GCP", "AWS", "Security", "Other"].index(domains.group_of(n[2:])), -strength[n]))
    pos = _ring_positions(G, order)
    fig = go.Figure(_edge_traces(G, pos))
    people = [n for n in G if n.startswith("P:")]
    fig.add_trace(go.Scatter(
        x=[pos[n][0] for n in people], y=[pos[n][1] for n in people], mode="markers+text",
        text=_labels(people, {n: strength[n] for n in people}, {"P:" + focus} if focus else set(), top=6, strip=2),
        hovertext=[n[2:] for n in people], textposition="bottom center", textfont=dict(size=10), name="Engineers",
        marker=dict(size=_sizes([strength[n] for n in people], 7, 22), color=INK, line=dict(color="white", width=1.5)),
        customdata=[strength[n] for n in people], hovertemplate="%{hovertext}: %{customdata} domain-tagged tickets<extra></extra>"))
    for g in ("GCP", "AWS", "Security", "Other"):
        nodes = [n for n in G if n.startswith("D:") and domains.group_of(n[2:]) == g]
        if not nodes:
            continue
        holders = [sum(1 for _, _, d in G.edges(n, data=True) if d["weight"] >= SKILL_MIN_TICKETS) for n in nodes]
        fig.add_trace(go.Scatter(
            x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes], mode="markers+text", text=[n[2:] for n in nodes],
            textposition=[_outside(pos[n]) for n in nodes], textfont=dict(size=11, color=GROUP_COLORS[g]),
            name=f"{g} domains",
            marker=dict(size=_sizes([strength[n] for n in nodes], 12, 36), color=GROUP_COLORS[g], symbol="square",
                        line=dict(color=["#e34948" if h <= 1 else "white" for h in holders], width=3)),
            customdata=np.stack([[strength[n] for n in nodes], holders], axis=1),
            hovertemplate="%{text}: %{customdata[0]} tickets · %{customdata[1]} people with 3+<extra></extra>"))
    _layout(fig, 720)
    fig.update_xaxes(range=[-1.55, 1.55])
    fig.update_yaxes(range=[-1.25, 1.25])
    return fig


# ── Entry point ─────────────────────────────────────────────────────────────────

def build_knowledge_graph(df_issues: pd.DataFrame, months: int = 12) -> dict:
    """Shared data for the three views; figures are drawn on demand with the page's filters."""
    if df_issues is None or df_issues.empty:
        return {"error_message": "📥 Fetch Jira tickets from the sidebar to see the Knowledge Graph."}
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return {"error_message": "Ticket data is missing required columns."}
    t = _scope(df_issues, months)
    if t.empty:
        return {"error_message": "No PE tickets created in this window."}
    edges, ask_stats = requester_edges(t)
    skills, tags = skill_data(t)
    return {
        "error_message": None, "months": int(months), "tickets": int(len(t)),
        "requester_edges": edges, "ask_stats": ask_stats, "dependency_df": requester_dependency(edges),
        "conversation_graph": conversation_graph(t, account_names(df_issues)), "pe_people": set(t.loc[t["assignee_name"].ne("Unassigned"), "assignee_name"]),
        "skill_edges": skills, "single_person_df": single_person_domains(skills), "pairs_df": domain_pairs(tags),
    }
