"""Suggested updates to the domain taxonomy (reports/domains.py), learned from ticket text. Advisory only.

Shown at the bottom of Suggested Assignments for a person to review. Nothing here changes the taxonomy: it is
shared with darkstar's Intake page (a test fails if the two drift apart), so accepted suggestions are applied by
hand in both places, from the snippets this module writes.

Tickets: DEVOPS and Release Management (CAR) tickets created in the last MONTHS months. Terms are words and
two-word phrases from the title and description (comments are chatty; they still count when a ticket is tagged).
Stop words, template phrases and people's names are left out, and a term needs MIN_TICKETS tickets.

1. Keyword additions. A term that is not matched by any domain's patterns, but whose tickets mostly sit in one
   domain already (at least MIN_PRECISION of them, and LIFT times that domain's share of all tickets), and that
   would tag at least MIN_GAIN more tickets with that domain. Each domain's additions are back-tested
   (assignment_report.domain_accuracy): "Domain Effect" is how many recent tickets gain (+) or lose (-) the person
   who did them from the top 3. Only domains that help go into the snippet and the what-if.
2. New-domain candidates. Terms no domain covers and that no single domain owns (under NEW_MAX_PRECISION), that
   are common among untagged tickets or rising (most of their tickets recent). Terms that appear on the same
   tickets are grouped with Louvain communities (NetworkX), and each group is one candidate with its evidence.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import pandas as pd

from reports import domains
from reports import executive_summary as es
from reports import in_progress_report as ipr

MONTHS = 12
RECENT_DAYS = 90
MIN_TICKETS = 6
MAX_SHARE = 0.10                 # terms on more than 10% of tickets are too generic to define a domain
MIN_PRECISION, LIFT, MIN_INSIDE, MIN_GAIN = 0.70, 3.0, 5, 2
PER_DOMAIN = 4
NEW_MAX_PRECISION, NEW_MIN_TICKETS, NEW_MIN_UNTAGGED, NEW_MIN_RECENT = 0.5, 8, 3, 0.6
CLUSTER_SIMILARITY = 0.35        # Jaccard overlap of two terms' tickets to group them
EXAMPLES = 3

STOP_WORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both but by
can could did do does doing done down during each few for from further get gets got had has have having he her here hers
him his how i if in into is it its itself just me more most my no nor not now of off on once only or other our out over own
same she should so some such than that the their them then there these they this those through to too under until up us
use used using very via was we were what when where which while who whom why will with would you your yours etc eg ie
ticket tickets request requests requested need needs needed new add added adding update updated updates updating create
created creating make set setup change changes changed required require requires ensure team please thanks hi hello issue
issues task tasks work prod nonprod non-prod production dev stage staging test testing env environment environments one
two three per background scope steps step description summary details detail notes note context goal goals acceptance
criteria h1 h2 h3 h4 h5 h6 mr merge apply applied plan existing current currently able like see following item items part
based within across service services resource resources account accounts project projects access support value values
name names file files config configuration configure configured run running runs well first next last time day days week
weeks today yes jira link links continued
""".split())
# Markup from pasted images and tables, and the Release Management template's boilerplate. Extend as needed.
TEMPLATE_WORDS = frozenset("""
width height alt png jpg jpeg gif screenshot image thumbnail color colour bug bugs epics features fixes upcoming release
releases code configuration-only blast radius partially complete many times significant debt tech noformat
accountid quote panel
""".split())
_WORD = re.compile(r"[a-z][a-z0-9]+(?:[-_.][a-z0-9]+)*")
KEYWORD_COLUMNS = ["Domain", "Add Keyword", "Pattern", "Would Add", "Already In Domain", "Precision %", "Lift", "Examples"]
NEW_COLUMNS = ["Candidate", "Keywords", "Tickets", "Untagged", "DEVOPS", "CAR", "Recent %", "People", "Examples"]


def _empty(message: str | None = None) -> dict:
    return {"error_message": message, "keywords_df": pd.DataFrame(columns=KEYWORD_COLUMNS),
            "new_df": pd.DataFrame(columns=NEW_COLUMNS), "keywords_snippet": "", "new_snippet": "", "what_if": None,
            "tickets": 0}


def _people_words(df: pd.DataFrame) -> set[str]:
    """Name parts of everyone in the data, so 'humza' or 'naumenko' never becomes a keyword."""
    names = set()
    for col in ("assignee_name", "reporter_name", "creator_name"):
        if col in df.columns:
            names |= set(df[col].dropna().astype(str))
    if "comments" in df.columns:
        names |= {c.get("author") for cs in df["comments"] if isinstance(cs, list) for c in cs if isinstance(c, dict)}
    return {w for n in names if isinstance(n, str) for w in re.split(r"[\s._-]+", n.lower()) if len(w) > 2}


def terms(text: str, skip: frozenset | set = frozenset()) -> set[str]:
    words = [w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and not w.isdigit()]
    bad = STOP_WORDS | TEMPLATE_WORDS | skip
    single = {w for w in words if w not in bad}
    pairs = {f"{a} {b}" for a, b in zip(words, words[1:]) if a != b and a not in bad and b not in bad}
    return single | pairs


def pattern(term: str) -> str:
    """The regular expression to paste: the term as a whole word or phrase."""
    return r"\b" + re.escape(term).replace(r"\ ", r"\s") + r"\b"


def _covered(term: str, compiled) -> bool:
    return any(r.search(term) for _, regexes in compiled for r in regexes)


def _example(t: pd.DataFrame, ids) -> str:
    return " · ".join(t.loc[sorted(ids, key=lambda i: t.at[i, "created_day"], reverse=True)[:EXAMPLES], "key"])


def _prepare(df_issues: pd.DataFrame, today: pd.Timestamp) -> dict | None:
    """Tickets of the last MONTHS months with their terms and domains, and which tickets carry each term/domain."""
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    t = facts[facts["created_day"] > today - pd.DateOffset(months=MONTHS)].copy().reset_index(drop=True)
    if t.empty:
        return None
    description = t["description_text"] if "description_text" in t.columns else pd.Series("", index=t.index)
    skip = _people_words(df_issues)
    t["terms"] = [terms(f"{s or ''} {d if isinstance(d, str) else ''}", skip) for s, d in zip(t["summary"], description)]
    t["text"] = [domains.ticket_text(r) for _, r in t.iterrows()]
    t["domains"] = t["text"].map(domains.tag)
    n = len(t)
    docs: dict[str, set[int]] = defaultdict(set)
    for i, ts in t["terms"].items():
        for term in ts:
            docs[term].add(i)
    compiled = domains._compiled()
    docs = {k: v for k, v in docs.items() if MIN_TICKETS <= len(v) <= MAX_SHARE * n and not _covered(k, compiled)}
    in_domain: dict[str, set[int]] = defaultdict(set)
    for i, ds in t["domains"].items():
        for d in ds:
            in_domain[d].add(i)
    return {"t": t, "n": n, "docs": docs, "in_domain": in_domain, "skip": skip,
            "recent": set(t.index[t["created_day"] > today - pd.Timedelta(days=RECENT_DAYS)])}


def _keyword_additions(ctx: dict) -> pd.DataFrame:
    t, n = ctx["t"], ctx["n"]
    rows = []
    for term, ids in ctx["docs"].items():
        for d, members in ctx["in_domain"].items():
            inside = len(ids & members)
            precision = inside / len(ids)
            lift = precision / (len(members) / n)
            gain = ids - members
            if precision >= MIN_PRECISION and lift >= LIFT and inside >= MIN_INSIDE and len(gain) >= MIN_GAIN:
                rows.append({"Domain": d, "Add Keyword": term, "Pattern": pattern(term), "Would Add": len(gain),
                             "Already In Domain": inside, "Precision %": round(precision * 100), "Lift": round(lift, 1),
                             "Examples": _example(t, gain)})
    kw = pd.DataFrame(rows, columns=KEYWORD_COLUMNS)
    if len(kw):
        kw = (kw.sort_values(["Domain", "Would Add", "Precision %"], ascending=[True, False, False])
              .groupby("Domain").head(PER_DOMAIN).sort_values(["Would Add", "Precision %"], ascending=False)
              .reset_index(drop=True))
    return kw


def _new_candidates(ctx: dict) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Candidate new domains, and the ticket keys behind each one."""
    t, recent, in_domain = ctx["t"], ctx["recent"], ctx["in_domain"]
    candidates = {}
    for term, ids in ctx["docs"].items():
        owned = max((len(ids & m) / len(ids) for m in in_domain.values()), default=0.0)
        untagged = sum(1 for i in ids if not t.at[i, "domains"])
        if (len(ids) >= NEW_MIN_TICKETS and owned < NEW_MAX_PRECISION
                and (untagged >= NEW_MIN_UNTAGGED or len(ids & recent) / len(ids) >= NEW_MIN_RECENT)):
            candidates[term] = ids
    out, keys = [], {}
    for group in _group_terms(candidates):
        ids = set().union(*(candidates[x] for x in group))
        if len(ids) < NEW_MIN_TICKETS:
            continue
        keywords = sorted(group, key=lambda x: (-len(candidates[x]), len(x), x))   # deterministic order
        rows_t = t.loc[sorted(ids)]
        car = rows_t["project_name"].astype(str).str.casefold().eq("release management")
        who = Counter(p for p in rows_t["assignee_name"] if isinstance(p, str) and p != "Unassigned")
        name = _title(keywords)
        keys[name] = list(rows_t["key"])
        out.append({"Candidate": name, "Keywords": ", ".join(keywords[:6]), "Tickets": len(ids),
                    "Untagged": int(sum(1 for i in ids if not t.at[i, "domains"])), "DEVOPS": int((~car).sum()),
                    "CAR": int(car.sum()), "Recent %": round(len(ids & recent) / len(ids) * 100),
                    "People": ", ".join(f"{p} ({c})" for p, c in who.most_common(3)), "Examples": _example(t, ids)})
    new = pd.DataFrame(out, columns=NEW_COLUMNS)
    return (new.sort_values("Tickets", ascending=False).reset_index(drop=True) if len(new) else new), keys


def candidate_domains(df_issues: pd.DataFrame, today: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict]:
    """Only the candidate new domains (fast: no back-test), for the application map."""
    if df_issues is None or df_issues.empty or "summary" not in df_issues.columns:
        return pd.DataFrame(columns=NEW_COLUMNS), {}
    ctx = _prepare(df_issues, today if today is not None else ipr.today_local())
    return _new_candidates(ctx) if ctx else (pd.DataFrame(columns=NEW_COLUMNS), {})


def build_domain_suggestions(df_issues: pd.DataFrame, people: list[str] | None = None,
                             today: pd.Timestamp | None = None) -> dict:
    if df_issues is None or df_issues.empty or "summary" not in df_issues.columns:
        return _empty("No ticket data loaded.")
    today = today if today is not None else ipr.today_local()
    ctx = _prepare(df_issues, today)
    if ctx is None:
        return _empty("No tickets in the last year.")
    kw = _keyword_additions(ctx)
    new, _ = _new_candidates(ctx)
    test = what_if(df_issues, kw, people, today) if people and len(kw) else None
    if test is not None:
        kw.insert(1, "Domain Effect", kw["Domain"].map(test["effect"]).fillna(0).astype(int))
        kw = kw.sort_values(["Domain Effect", "Would Add"], ascending=False).reset_index(drop=True)
    helpful = kw[kw["Domain Effect"] > 0] if test is not None else kw
    return {"error_message": None, "tickets": ctx["n"], "keywords_df": kw, "new_df": new,
            "keywords_snippet": keyword_snippet(helpful), "new_snippet": new_domain_snippet(new), "what_if": test}


def _group_terms(candidates: dict[str, set[int]]) -> list[set[str]]:
    """Terms that share tickets, grouped (Louvain). A term with no partner is dropped: on its own it is usually a
    generic word, while real themes come as a name plus a phrase (vader / adtech vader)."""
    import networkx as nx
    G = nx.Graph()
    names = sorted(candidates)
    G.add_nodes_from(names)                       # sorted: same groups on every run
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            overlap = len(candidates[a] & candidates[b]) / len(candidates[a] | candidates[b])
            if overlap >= CLUSTER_SIMILARITY:
                G.add_edge(a, b, weight=overlap)
    G.remove_nodes_from([x for x in list(G) if G.degree(x) == 0])   # a theme needs two terms that go together
    if G.number_of_nodes() == 0:
        return []
    return [set(c) for c in sorted(nx.community.louvain_communities(G, weight="weight", seed=1), key=min)]


def _title(keywords: list[str]) -> str:
    """A readable name: the shortest of the leading keywords, title-cased."""
    lead = min(keywords[:3], key=lambda x: (len(x), x))
    return lead if any(c.isupper() for c in lead) else lead.title()


# ── Snippets to paste ───────────────────────────────────────────────────────────

def keyword_snippet(kw: pd.DataFrame) -> str:
    if kw is None or kw.empty:
        return ""
    lines = ["# Add to DOMAIN_PATTERNS in reports/domains.py AND darkstar/dashboards/intake.html (keep them in sync)."]
    for d, g in kw.groupby("Domain", sort=True):
        lines.append(f"# {d}: append to its list")
        lines.append("    " + ", ".join(repr(p) for p in g["Pattern"]) + ",")
    return "\n".join(lines)


def new_domain_snippet(new: pd.DataFrame) -> str:
    if new is None or new.empty:
        return ""
    lines = ["# New domains for DOMAIN_PATTERNS in reports/domains.py AND darkstar/dashboards/intake.html.",
             "# Also add each to DOMAIN_PRIORITY (where it ranks) and DOMAIN_GROUP (AWS, GCP, Security or Other)."]
    for _, r in new.iterrows():
        pats = [pattern(k.strip()) for k in r["Keywords"].split(",")]
        lines.append(f"{r['Candidate']!r}: [{', '.join(repr(p) for p in pats)}],")
    return "\n".join(lines)


# ── Does it help? Back-test each domain's additions ─────────────────────────────

def what_if(df_issues: pd.DataFrame, kw: pd.DataFrame, people: list[str], today: pd.Timestamp) -> dict | None:
    """Effect of each domain's keyword additions on the top-3 hit rate (assignment_report.domain_accuracy), and of
    accepting only the ones that help.

    Adding a keyword can hurt: it pulls tickets into a broad domain, which changes their primary domain and dilutes
    who looks like the expert. A change can only affect tickets whose primary domain is that domain before or after,
    so each domain is re-scored on those tickets alone.
    """
    from reports.assignment_report import domain_accuracy
    hol = ipr._calendar_holidays(today)
    t = es._facts(df_issues, today, hol)
    t = t[t["sla_applies"]].copy()
    texts = [domains.ticket_text(row) for _, row in t.iterrows()]
    before = [domains.tag(x) for x in texts]
    extra = {d: [re.compile(p, re.I) for p in g["Pattern"]] for d, g in kw.groupby("Domain")}

    def with_domains(accepted) -> pd.DataFrame:
        tags = [b + [d for d in accepted if d not in b and any(r.search(x) for r in extra[d])]
                for b, x in zip(before, texts)]
        out = t.assign(domains=tags)
        out["domain"] = out["domains"].map(domains.primary).astype(object).where(lambda v: v.notna(), None)
        return out

    base = with_domains([])
    effect = {}
    for d in extra:
        changed = with_domains([d])
        keys = set(base.loc[base["domain"].eq(d), "key"]) | set(changed.loc[changed["domain"].eq(d), "key"])
        if not keys:
            effect[d] = 0
            continue
        effect[d] = domain_accuracy(changed, today, people, keys=keys)["hits"] - \
            domain_accuracy(base, today, people, keys=keys)["hits"]
    helpful = [d for d, e in effect.items() if e > 0]
    now = domain_accuracy(base, today, people)
    after = domain_accuracy(with_domains(helpful), today, people) if helpful else now
    return {"before": now["hit"], "after": after["hit"], "n": now["n"], "effect": effect, "helpful": helpful}
