"""Learning about skills from ticket history (phase 3 of the Suggested Assignments learning section). Advisory only.

1. Domains to split (split_candidates). Inside each busy domain, tickets whose titles share distinctive words are
   linked (weighted by how rare the shared words are) and grouped with Louvain communities, so each ticket is in at
   most one group. A sizable group is a sub-theme: a candidate sub-domain. Each one is back-tested with
   assignment_report.domain_accuracy: tickets matching the sub-theme's keywords get it as their primary domain, and
   "Effect" is how many recent tickets gain (+) or lose (-) the person who did them from the top 3.
2. Hidden expertise (hidden_expertise). Evidence of skill beyond being the assignee: commenting on a domain's tickets
   assigned to someone else (not as the requester), and posting MR links on them. A what-if back-test counts that
   help at HELP_WEIGHT of an assigned ticket and reports the top-3 hit rate.
3. Who knows which application (application_map). The taxonomy's Applications domains, plus candidate new domains
   not covered yet (domain_suggestions.candidate_domains), against everyone who worked on or discussed them.

DEVOPS and CAR tickets from the last MONTHS months; the back-tests score PE tickets, as the suggestions do.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

import pandas as pd

from reports import domains
from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports.domain_suggestions import MONTHS, _people_words, pattern, terms

SPLIT_MIN_DOMAIN = 60            # only domains with this many tickets in the window
SPLIT_MIN_GROUP, SPLIT_MIN_SHARE = 8, 0.10
LINK_MIN = 0.6                   # how much (rarity-weighted) title vocabulary two tickets must share to be linked
LABEL_TERMS, LABEL_MIN_SHARE = 3, 0.25
LABEL_SKIP = frozenset("fix remove enable deploy delete investigate migrate migration cleanup review verify".split())
HELP_WEIGHT = 0.3
HIDDEN_MIN = 3
SPLIT_COLUMNS = ["Domain", "Sub-Theme", "Effect", "Tickets", "Share of Domain %", "Main People", "Domain's Main Person",
                 "Different Expert", "Keywords", "Examples"]
HIDDEN_COLUMNS = ["Person", "Domain", "Assigned", "Helped On", "MRs On Others' Tickets", "Evidence"]


def _tickets(df_issues: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    hol = ipr._calendar_holidays(today)
    t = es._facts(df_issues, today, hol)
    t = t[t["created_day"] > today - pd.DateOffset(months=MONTHS)].copy().reset_index(drop=True)
    t["text"] = [domains.ticket_text(r) for _, r in t.iterrows()]
    t["domains"] = t["text"].map(domains.tag)
    t["domain"] = t["domains"].map(domains.primary).astype(object).where(lambda v: v.notna(), None)
    return t


def _pe_tags(df_issues: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """All PE tickets (any age: earlier work is experience) with their text and domains, for the back-tests."""
    hol = ipr._calendar_holidays(today)
    t = es._facts(df_issues, today, hol)
    t = t[t["sla_applies"]].copy()
    t["text"] = [domains.ticket_text(r) for _, r in t.iterrows()]
    t["domains"] = t["text"].map(domains.tag)
    t["domain"] = t["domains"].map(domains.primary).astype(object).where(lambda v: v.notna(), None)
    return t


# ── 1. Domains to split ─────────────────────────────────────────────────────────

def _groups(g: pd.DataFrame, skip: set[str]) -> tuple[list[set[int]], dict[int, set[str]], Counter]:
    import networkx as nx
    words = {i: {x for x in terms(s or "", skip) if " " not in x} for i, s in g["summary"].items()}
    freq = Counter(x for ws in words.values() for x in ws)
    keep = {x for x, c in freq.items() if 3 <= c <= 0.3 * len(g)}
    words = {i: ws & keep for i, ws in words.items()}
    index = defaultdict(set)
    for i, ws in words.items():
        for x in ws:
            index[x].add(i)
    G = nx.Graph()
    G.add_nodes_from(words)
    for i, ws in words.items():
        for j in sorted(set().union(*(index[x] for x in ws))) if ws else ():    # sorted: same groups every run
            if j > i:
                w = sum(1 / math.log(1 + freq[x]) for x in ws & words[j])
                if w >= LINK_MIN:
                    G.add_edge(i, j, weight=w)
    return [set(c) for c in sorted(nx.community.louvain_communities(G, weight="weight", seed=1), key=min)], words, freq


def _label(group: set[int], words: dict[int, set[str]], freq: Counter, size: int) -> list[str]:
    inside = Counter(x for i in group for x in words[i])
    ranked = sorted((x for x, c in inside.items() if c >= 3 and c / len(group) >= LABEL_MIN_SHARE and x not in LABEL_SKIP),
                    key=lambda x: (-(inside[x] / len(group)) / (freq[x] / size) * min(inside[x], 6), x))
    return ranked[:LABEL_TERMS]


def _top_people(names, n: int = 2) -> list[tuple[str, int]]:
    return Counter(p for p in names if isinstance(p, str) and p not in ("Unassigned", "Unknown")).most_common(n)


def split_candidates(df_issues: pd.DataFrame, people: list[str] | None = None,
                     today: pd.Timestamp | None = None) -> pd.DataFrame:
    if df_issues is None or df_issues.empty or "summary" not in df_issues.columns:
        return pd.DataFrame(columns=SPLIT_COLUMNS)
    today = today if today is not None else ipr.today_local()
    t = _tickets(df_issues, today)
    skip = _people_words(df_issues)
    rows = []
    for d, g in t[t["domain"].notna()].groupby("domain"):
        if len(g) < SPLIT_MIN_DOMAIN:
            continue
        groups, words, freq = _groups(g, skip)
        main = _top_people(g["assignee_name"], 1)
        for c in groups:
            if len(c) < max(SPLIT_MIN_GROUP, SPLIT_MIN_SHARE * len(g)):
                continue
            label = _label(c, words, freq, len(g))
            if len(label) < 2:
                continue
            sub = g.loc[sorted(c)]
            top = _top_people(sub["assignee_name"])
            rows.append({"Domain": d, "Sub-Theme": " · ".join(label), "Tickets": len(c),
                         "Share of Domain %": round(len(c) / len(g) * 100),
                         "Main People": ", ".join(f"{p} ({k})" for p, k in top),
                         "Domain's Main Person": main[0][0] if main else "",
                         "Different Expert": "yes" if top and main and top[0][0] != main[0][0] else "",
                         "Keywords": ", ".join(label),
                         "Examples": " · ".join(sub.sort_values("created_day", ascending=False)["key"].head(3))})
    out = pd.DataFrame(rows, columns=[c for c in SPLIT_COLUMNS if c != "Effect"])
    if out.empty:
        return pd.DataFrame(columns=SPLIT_COLUMNS)
    effects = _split_effects(df_issues, out, people, today) if people else {}
    out.insert(2, "Effect", [effects.get(i) for i in out.index])
    return out.sort_values(["Effect", "Tickets"], ascending=False, na_position="last").reset_index(drop=True)


def _split_effects(df_issues: pd.DataFrame, splits: pd.DataFrame, people: list[str], today: pd.Timestamp) -> dict:
    """Back-test each sub-domain: matching tickets of the domain get it as their primary domain."""
    from reports.assignment_report import domain_accuracy
    pe = _pe_tags(df_issues, today)
    effects = {}
    for i, r in splits.iterrows():
        d, name = r["Domain"], f"{r['Domain']} › {r['Sub-Theme']}"
        regexes = [re.compile(pattern(k.strip()), re.I) for k in r["Keywords"].split(",")]
        keys = set(pe.loc[pe["domain"].eq(d), "key"])
        if not keys:
            effects[i] = 0
            continue
        hit = pe["domains"].map(lambda ds: d in ds) & pe["text"].map(lambda x: any(rx.search(x) for rx in regexes))
        changed = pe.copy()
        changed.loc[hit, "domains"] = changed.loc[hit, "domains"].map(lambda ds: ds + [name])
        changed.loc[hit & changed["domain"].eq(d), "domain"] = name
        effects[i] = domain_accuracy(changed, today, people, keys=keys)["hits"] - \
            domain_accuracy(pe, today, people, keys=keys)["hits"]
    return effects


def split_snippet(splits: pd.DataFrame) -> str:
    good = splits[splits["Effect"].fillna(0) > 0] if "Effect" in splits else splits
    if good.empty:
        return ""
    lines = ["# Sub-domains for DOMAIN_PATTERNS in reports/domains.py AND darkstar/dashboards/intake.html.",
             "# Put each one just BEFORE its parent domain in DOMAIN_PRIORITY, and give it the parent's DOMAIN_GROUP."]
    for _, r in good.iterrows():
        pats = [pattern(k.strip()) for k in r["Keywords"].split(",")]
        name = r["Domain"] + ": " + r["Keywords"].split(",")[0].strip().title()
        lines.append(f"{name!r}: [{', '.join(repr(p) for p in pats)}],")
    return "\n".join(lines)


# ── 2. Hidden expertise ─────────────────────────────────────────────────────────

def _help_rows(t: pd.DataFrame, people: set[str]) -> pd.DataFrame:
    """One row per piece of help: a comment or an MR link on a ticket assigned to someone else."""
    rows = []
    for _, r in t.iterrows():
        owner, requester = r.get("assignee_name"), r.get("reporter_name")
        for c in r.get("comments") if isinstance(r.get("comments"), list) else []:
            who = c.get("author")
            if who in people and who not in (owner, requester):
                rows.append({"assignee_name": who, "key": r["key"], "kind": "comment",
                             "closed_day": ipr._to_day(pd.Series([c.get("created")]), ipr.LOCAL_TZ).iloc[0],
                             "domains": r["domains"], "domain": r["domain"]})
        for m in r.get("merge_requests") if isinstance(r.get("merge_requests"), list) else []:
            who = m.get("by")
            if who in people and who != owner:
                rows.append({"assignee_name": who, "key": r["key"], "kind": "mr",
                             "closed_day": ipr._to_day(pd.Series([m.get("at")]), ipr.LOCAL_TZ).iloc[0],
                             "domains": r["domains"], "domain": r["domain"]})
    return pd.DataFrame(rows, columns=["assignee_name", "key", "kind", "closed_day", "domains", "domain"])


def hidden_expertise(df_issues: pd.DataFrame, people: list[str], today: pd.Timestamp | None = None) -> dict:
    empty = {"hidden_df": pd.DataFrame(columns=HIDDEN_COLUMNS), "what_if": None}
    if df_issues is None or df_issues.empty or not people:
        return empty
    today = today if today is not None else ipr.today_local()
    team = set(people)
    t = _tickets(df_issues, today)
    help_ = _help_rows(t[t["domain"].notna()], team)
    assigned = t[t["domain"].notna() & t["assignee_name"].isin(team)].groupby(["assignee_name", "domain"]).size()
    rows = []
    if len(help_):
        helped = help_[help_["kind"].eq("comment")].groupby(["assignee_name", "domain"])["key"].nunique()
        mrs = help_[help_["kind"].eq("mr")].groupby(["assignee_name", "domain"])["key"].nunique()
        for (person, domain) in set(helped.index) | set(mrs.index):
            h, m, a = int(helped.get((person, domain), 0)), int(mrs.get((person, domain), 0)), int(assigned.get((person, domain), 0))
            if h + m >= HIDDEN_MIN and h + m >= a:
                rows.append({"Person": person, "Domain": domain, "Assigned": a, "Helped On": h,
                             "MRs On Others' Tickets": m,
                             "Evidence": "helps more than they're assigned" if a else "helps, never assigned"})
    hidden = (pd.DataFrame(rows, columns=HIDDEN_COLUMNS).sort_values(["Helped On", "MRs On Others' Tickets"], ascending=False)
              .reset_index(drop=True))

    # What if help counted toward expertise? Back-test on PE tickets, help from all PE tickets (any age).
    from reports.assignment_report import domain_accuracy
    pe = _pe_tags(df_issues, today)
    extra = _help_rows(pe, team).assign(weight=HELP_WEIGHT)
    now = domain_accuracy(pe, today, people)
    with_help = domain_accuracy(pe, today, people, extra=extra) if len(extra) else now
    return {"hidden_df": hidden, "what_if": {"before": now["hit"], "after": with_help["hit"], "weight": HELP_WEIGHT,
                                             "help_rows": int(len(extra))}}


# ── 3. Who knows which application ──────────────────────────────────────────────

APP_GROUP = "Applications"


def _applications(df_issues: pd.DataFrame, today: pd.Timestamp) -> list[dict]:
    """The Applications domains of the taxonomy, then candidate themes not covered yet: name, ticket keys, keywords."""
    from reports.domain_suggestions import candidate_domains
    t = _tickets(df_issues, today)
    apps = []
    for d in domains.DOMAIN_PRIORITY:
        if domains.group_of(d) == APP_GROUP:
            keys = list(t.loc[t["domains"].map(lambda ds: d in ds), "key"])
            if keys:
                apps.append({"name": d, "keys": keys, "keywords": ", ".join(domains.DOMAIN_PATTERNS[d]),
                             "source": "Domain"})
    new, candidate_keys = candidate_domains(df_issues, today)
    for _, r in new.iterrows():
        apps.append({"name": r["Candidate"], "keys": candidate_keys.get(r["Candidate"], []), "keywords": r["Keywords"],
                     "source": "Candidate"})
    return apps


def application_map(df_issues: pd.DataFrame, today: pd.Timestamp | None = None, top_people: int = 15) -> dict:
    """Applications x people: tickets each person worked on (assignee) or discussed (commented).

    Applications are the taxonomy's Applications group (domains.py), plus candidate themes the taxonomy doesn't cover
    yet (domain_suggestions.candidate_domains), marked by Source.
    """
    empty = {"apps_df": pd.DataFrame(), "matrix": pd.DataFrame()}
    if df_issues is None or df_issues.empty or "summary" not in df_issues.columns:
        return empty
    today = today if today is not None else ipr.today_local()
    apps = _applications(df_issues, today)
    if not apps:
        return empty
    by_key = df_issues.drop_duplicates("key").set_index("key")
    rows = []
    for app in apps:
        for k in app["keys"]:
            if k not in by_key.index:
                continue
            r = by_key.loc[k]
            owner = r.get("assignee_name")
            if isinstance(owner, str) and owner not in ("Unassigned", "Unknown"):
                rows.append({"app": app["name"], "person": owner, "key": k, "role": "worked"})
            for c in r.get("comments") if isinstance(r.get("comments"), list) else []:
                if c.get("author") and c.get("author") != owner:
                    rows.append({"app": app["name"], "person": c["author"], "key": k, "role": "discussed"})
    if not rows:
        return empty
    ev = pd.DataFrame(rows).drop_duplicates(["app", "person", "key", "role"])
    worked = ev[ev["role"].eq("worked")].groupby(["app", "person"])["key"].nunique()
    discussed = ev[ev["role"].eq("discussed")].groupby(["app", "person"])["key"].nunique()
    score = worked.add(0.5 * discussed, fill_value=0).rename("score").reset_index()
    matrix = score.pivot_table(index="app", columns="person", values="score", fill_value=0.0)
    order = [a["name"] for a in apps if a["name"] in matrix.index]
    matrix = matrix.loc[order]
    busiest = matrix.sum().sort_values(ascending=False).index[:top_people]
    info = {a["name"]: a for a in apps}
    out = []
    for name, col in matrix.iterrows():
        ranked = col[col > 0].sort_values(ascending=False)
        total = float(ranked.sum())
        out.append({"Application": name, "Source": info[name]["source"], "Tickets": len(set(info[name]["keys"])),
                    "People Involved": int(len(ranked)), "Main Person": ranked.index[0] if len(ranked) else "",
                    "Main Share %": round(ranked.iloc[0] / total * 100) if total else 0,
                    "Second": ranked.index[1] if len(ranked) > 1 else "—",
                    "Worked On (Main)": int(worked.get((name, ranked.index[0]), 0)) if len(ranked) else 0,
                    "Keywords": info[name]["keywords"]})
    return {"apps_df": pd.DataFrame(out), "matrix": matrix[busiest]}


def application_figure(matrix: pd.DataFrame):
    import plotly.graph_objects as go
    if matrix is None or matrix.empty:
        return None
    fig = go.Figure(go.Heatmap(
        z=matrix.values, x=list(matrix.columns), y=list(matrix.index), colorscale="Blues", zmin=0, xgap=2, ygap=2,
        text=[[f"{v:.0f}" if v >= 1 else "" for v in row] for row in matrix.values], texttemplate="%{text}",
        colorbar=dict(title="Tickets"),
        hovertemplate="%{y} · %{x}: %{z:.1f} (worked = 1, discussed = 0.5 per ticket)<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=max(300, 30 * len(matrix) + 140), margin=dict(l=10, r=10, t=10, b=10))
    return fig
