"""Experiments for Suggested Assignments: does a different way of scoring experience predict who does the work better?

Read-only and not used by the app. Every variant is scored with the same back-test as assignment_report.domain_accuracy:
for each PE ticket completed in the window by a current candidate, rank the candidates on experience built only from
work finished *before* the ticket was created, and check whether the person who did it is in the top ACCURACY_TOP.
The "primary" variant reproduces domain_accuracy exactly (the number shown on the Learning scorecard), so every other
variant is compared with today's behaviour.

Variants:
- primary: experience in the ticket's primary domain only (today).
- all_domains: every domain on the ticket counts, the primary one fully and the others at SECONDARY_WEIGHT.
- related(weight, k): primary experience, plus `weight` x the experience in the domain's `k` closest related domains.
  Two domains are related when they show up on the same tickets more than chance (lift >= MIN_LIFT, at least
  MIN_TOGETHER tickets together); the credit is their Jaccard overlap. Hub domains (tagged on more than HUB_SHARE of
  tickets, like GitLab or GCP Core) are never related to anything: they sit on so many tickets that they would link
  everything. The relations are built only from tickets created before each test ticket.

- similar(k, weight): credit whoever finished the `k` most similar earlier tickets (TF-IDF cosine on title + description
  only: comments are written after the work starts, so they would give the test ticket's answer away), each counted
  by its similarity and how recent it is; `weight` = 0 means similar tickets alone, otherwise it is added to the
  primary-domain experience. Needs no domain tags, so it also covers tickets with no domain. With
  `other_requesters` the neighbours from the test ticket's own requester are left out: a requester's tickets read
  alike and tend to go to the same person, so similar text alone mostly measures routing habit, not skill.

domain_families() groups domains by the same relations (Louvain communities) so the groups can be reviewed by hand.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Callable

import pandas as pd

from reports.assignment_report import ACCURACY_DAYS, ACCURACY_TOP
from reports.expertise_model import (EXPERTISE_HALF_LIFE_DAYS, Relations, SimilarTickets, _experience, domain_weights,  # noqa: F401
                                     build_similar, early_text, expertise_scorer)

Scorer = Callable[[pd.Series, pd.DataFrame, "Relations"], pd.Series]


def primary(t: pd.Series, prior: pd.DataFrame, rel: Relations) -> pd.Series:
    return _experience(prior, {t["domain"]: 1.0} if isinstance(t["domain"], str) else {})


def all_domains(t: pd.Series, prior: pd.DataFrame, rel: Relations) -> pd.Series:
    domain = t["domain"] if isinstance(t["domain"], str) else None
    return _experience(prior, domain_weights(list(t["domains"]), domain, rel.hubs))


def related(weight: float, k: int) -> Scorer:
    def score(t: pd.Series, prior: pd.DataFrame, rel: Relations) -> pd.Series:
        if not isinstance(t["domain"], str):
            return pd.Series(dtype=float)
        weights = {t["domain"]: 1.0}
        for other, jaccard in rel.of(t["domain"], k).items():
            weights[other] = max(weights.get(other, 0.0), weight * jaccard)
        return _experience(prior, weights)
    return score


def similar(model: SimilarTickets, k: int, weight: float = 0.0, other_requesters: bool = False,
            base: Scorer = primary) -> Scorer:
    def score(t: pd.Series, prior: pd.DataFrame, rel: Relations) -> pd.Series:
        credit = model.credit(t, k, other_requesters)
        if weight == 0:
            return credit
        return base(t, prior, rel).add(weight * credit, fill_value=0.0)
    return score


# Weights are first guesses to compare, not tuned values: judge them on both back-test windows.
BASE_VARIANTS: dict[str, Scorer] = {
    "primary": primary, "all_domains (no hubs)": all_domains,
    "related w=1 k=3": related(1.0, 3)}


def build_variants(tickets: pd.DataFrame, people: list[str], texts: pd.Series | None = None) -> dict[str, Scorer]:
    """The variants to compare; the similar-ticket ones too when the tickets' early text is given."""
    variants = dict(BASE_VARIANTS)
    variants["app today (domains + similar-other x3)"] = expertise_scorer(build_similar(tickets, people))
    if texts is not None:
        model = SimilarTickets(tickets, texts, people)
        variants.update({
            "similar k=10 (alone)": similar(model, 10),
            "similar k=10, other requesters (alone)": similar(model, 10, other_requesters=True),
            "domains + similar-other x1": similar(model, 10, 1.0, True, all_domains),
            "domains + similar-other x3": similar(model, 10, 3.0, True, all_domains),
            "domains + similar-other x6": similar(model, 10, 6.0, True, all_domains)})
    return variants


def backtest(tickets: pd.DataFrame, today: pd.Timestamp, people: list[str], scorer: Scorer,
             days: int = ACCURACY_DAYS, top: int = ACCURACY_TOP, relations: dict | None = None) -> dict:
    """Top-`top` hit rate of `scorer` on tickets completed in the last `days` days, by domain and overall.

    Same rules as domain_accuracy: only work finished before the ticket was created counts, and a ticket nobody has
    experience for (including a ticket with no domain) is a miss. Also reports how concentrated the first choices are.
    """
    done = tickets[tickets["outcome"].eq("Done") & tickets["assignee_name"].isin(people) & tickets["closed_day"].notna()]
    evaluate = done[done["closed_day"] > today - pd.Timedelta(days=days)]
    if evaluate.empty:
        return {"n": 0, "hit": None, "first": None, "top1_max_share": None, "top1_people": 0, "by_domain": pd.DataFrame()}
    work = done[["assignee_name", "closed_day", "domains"]].explode("domains").dropna(subset=["domains"])
    halflife = float(EXPERTISE_HALF_LIFE_DAYS)
    rows, firsts = [], []
    for _, t in evaluate.iterrows():
        prior = work[work["closed_day"] < t["created_day"]].copy()
        prior["w"] = 0.5 ** ((t["created_day"] - prior["closed_day"]).dt.days / halflife)
        if relations is None or t["created_day"] not in relations:
            rel = Relations(tickets.loc[tickets["created_day"] < t["created_day"], "domains"])
            if relations is not None:
                relations[t["created_day"]] = rel
        else:
            rel = relations[t["created_day"]]
        expertise = scorer(t, prior, rel)
        expertise = expertise.reindex(people).fillna(0.0)
        ranked = list(expertise[expertise > 0].sort_values(ascending=False).index)
        hit_list = ranked[:top]
        domain = t["domain"] if isinstance(t["domain"], str) else "No domain"
        rows.append({"key": t["key"], "domain": domain, "hit": t["assignee_name"] in hit_list,
                     "first": bool(hit_list) and hit_list[0] == t["assignee_name"]})
        if hit_list:
            firsts.append(hit_list[0])
    r = pd.DataFrame(rows)
    by = r.groupby("domain").agg(Tickets=("hit", "size"), Hit=("hit", "mean")).reset_index().rename(columns={"domain": "Domain"})
    by["Top-3 Hit %"] = (by.pop("Hit") * 100).round().astype(int)
    shares = pd.Series(firsts).value_counts(normalize=True) if firsts else pd.Series(dtype=float)
    return {"n": int(len(r)), "hit": float(r["hit"].mean()), "first": float(r["first"].mean()),
            "top1_max_share": float(shares.max()) if len(shares) else None, "top1_people": int(len(shares)),
            "by_domain": by, "per_ticket": r.set_index("key")["hit"]}


def paired(a: dict, b: dict) -> dict:
    """Is b really better than a? Same tickets, so compare them one by one: wins, losses and an exact sign-test p-value."""
    from math import comb
    both = pd.concat([a["per_ticket"].rename("a"), b["per_ticket"].rename("b")], axis=1).dropna()
    wins, losses = int((both["b"] & ~both["a"]).sum()), int((~both["b"] & both["a"]).sum())
    n = wins + losses
    p = min(1.0, 2 * sum(comb(n, i) for i in range(min(wins, losses) + 1)) / 2 ** n) if n else 1.0
    return {"b_only": wins, "a_only": losses, "p": p}


def compare(tickets: pd.DataFrame, today: pd.Timestamp, people: list[str], days: int = ACCURACY_DAYS,
            variants: dict[str, Scorer] | None = None) -> dict:
    """Run every variant and line them up: overall table and a per-domain table (hit % per variant)."""
    variants = variants or BASE_VARIANTS
    shared: dict = {}                                    # one set of relations per ticket, reused by every variant
    results = {name: backtest(tickets, today, people, fn, days, relations=shared) for name, fn in variants.items()}
    overall = pd.DataFrame([{"Variant": n, "Tickets": r["n"], "Top-3 Hit %": round(100 * r["hit"], 1) if r["hit"] is not None else None,
                             "First-Choice %": round(100 * r["first"], 1) if r["first"] is not None else None,
                             "Busiest First-Choice Share %": round(100 * r["top1_max_share"], 1) if r["top1_max_share"] is not None else None,
                             "People Picked First": r["top1_people"]} for n, r in results.items()])
    by_domain = None
    for n, r in results.items():
        if r["by_domain"].empty:
            continue
        part = r["by_domain"].rename(columns={"Top-3 Hit %": n})
        by_domain = part if by_domain is None else by_domain.merge(part[["Domain", n]], on="Domain", how="outer")
    if by_domain is not None:
        by_domain = by_domain.sort_values("Tickets", ascending=False).reset_index(drop=True)
    return {"overall": overall, "by_domain": by_domain if by_domain is not None else pd.DataFrame(), "results": results}


def domain_families(tickets: pd.DataFrame, min_size: int = 2, seed: int = 7) -> pd.DataFrame:
    """Groups of domains that travel together on tickets (Louvain communities on the same relations), to review by hand."""
    import networkx as nx
    rel = Relations(tickets["domains"])
    g = nx.Graph()
    for a, others in rel.affinity.items():
        for b, w in others.items():
            g.add_edge(a, b, weight=w)
    if g.number_of_edges() == 0:
        return pd.DataFrame(columns=["Family", "Domains", "Size", "Strongest Pair"])
    counts = Counter(d for ds in tickets["domains"] for d in set(ds))
    rows = []
    for community in nx.community.louvain_communities(g, weight="weight", seed=seed):
        if len(community) < min_size:
            continue
        members = sorted(community, key=lambda d: -counts[d])
        pairs = [(g[a][b]["weight"], a, b) for a, b in combinations(members, 2) if g.has_edge(a, b)]
        best = max(pairs) if pairs else None
        rows.append({"Family": members[0], "Domains": ", ".join(f"{d} ({counts[d]})" for d in members), "Size": len(members),
                     "Strongest Pair": f"{best[1]} + {best[2]} ({best[0]:.2f})" if best else ""})
    return pd.DataFrame(rows).sort_values("Size", ascending=False).reset_index(drop=True)
