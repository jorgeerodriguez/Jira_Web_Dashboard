"""How much experience each person has for a ticket (used by Suggested Assignments and its back-test).

A person's experience for a ticket is the sum of two kinds of evidence:
- Domains: their recency-weighted work in every domain on the ticket (the primary domain at full weight, the other
  domains at SECONDARY_WEIGHT). Hub domains (tagged on more than HUB_SHARE of tickets, like GitLab or GCP Core) only
  count when they are the primary domain: they sit on so many tickets that, as a secondary tag, they say little.
- Similar tickets: SIMILAR_WEIGHT x the credit from the SIMILAR_K most similar finished tickets (TF-IDF cosine on
  title and description), each counted by its similarity and how recent it is. Tickets from the *same requester* are
  left out: a requester's tickets read alike and tend to go to the same person, so counting them would copy routing
  habit instead of finding skill (a requester alone "predicts" 75% of top-3s in the back-test, and would also lock in
  the requesters who already lean on one engineer).

Chosen with reports/assignment_experiments.py (docs/assignment_experiments/run.py): on the last 90 days of completed
PE tickets it raised the top-3 hit rate from 55.1% to 58.0% (better on 39 tickets, worse on 13, p < 0.001).
The weights are the ones tried there, not tuned values.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations

import pandas as pd

EXPERTISE_HALF_LIFE_DAYS = 120
SECONDARY_WEIGHT = 0.5
MIN_LIFT, MIN_TOGETHER = 3.0, 3
HUB_SHARE = 0.10
SIMILAR_K = 10
SIMILAR_WEIGHT = 3.0
MIN_SIMILARITY = 0.1


class Relations:
    """Which domains are hubs, and domain-to-domain affinity (Jaccard overlap) from tickets that carry both.

    The hubs are cheap; the affinities (pair counts) are only worked out when asked for, since the app never needs them."""

    def __init__(self, tagged: pd.Series | list[list[str]]):
        self._lists = [sorted(set(d)) for d in tagged]
        self._n = max(len(self._lists), 1)
        self._single = Counter(d for ds in self._lists for d in ds)
        self.hubs = {d for d, c in self._single.items() if c / self._n > HUB_SHARE}
        self._affinity: dict[str, dict[str, float]] | None = None

    @property
    def affinity(self) -> dict[str, dict[str, float]]:
        if self._affinity is None:
            single, n = self._single, self._n
            pair = Counter(p for ds in self._lists for p in combinations(ds, 2))
            self._affinity = {}
            for (a, b), together in pair.items():
                if a in self.hubs or b in self.hubs:
                    continue
                lift = (together / n) / ((single[a] / n) * (single[b] / n))
                if together >= MIN_TOGETHER and lift >= MIN_LIFT:
                    jaccard = together / (single[a] + single[b] - together)
                    self._affinity.setdefault(a, {})[b] = jaccard
                    self._affinity.setdefault(b, {})[a] = jaccard
        return self._affinity

    def of(self, domain: str, k: int | None = None) -> dict[str, float]:
        """The domain's related domains, strongest first (the closest `k` when given)."""
        near = sorted(self.affinity.get(domain, {}).items(), key=lambda kv: -kv[1])
        return dict(near[:k] if k else near)


def early_text(row) -> str:
    """What is known when a ticket is created: title and description (no comments, which come after the work starts)."""
    description = row.get("description_text")
    return f"{row.get('summary') or ''} {description if isinstance(description, str) else ''}"


def domain_weights(domains: list[str], primary: str | None, hubs: set[str]) -> dict[str, float]:
    """How much each of a ticket's domains counts: the primary in full, the others at half, hubs only as the primary."""
    return {d: (1.0 if d == primary else SECONDARY_WEIGHT) for d in domains if d == primary or d not in hubs}


class SimilarTickets:
    """TF-IDF similarity between tickets. The vocabulary weights (idf) are fitted on all tickets' text, never on who did them."""

    def __init__(self, tickets: pd.DataFrame, texts: pd.Series, people: list[str]):
        from sklearn.feature_extraction.text import TfidfVectorizer
        keys = list(tickets["key"])
        vec = TfidfVectorizer(sublinear_tf=True, stop_words="english", min_df=2, max_df=0.5, ngram_range=(1, 2))
        self.x = vec.fit_transform(texts.reindex(keys).fillna(""))
        self.row = {k: i for i, k in enumerate(keys)}
        done = tickets[tickets["outcome"].eq("Done") & tickets["assignee_name"].isin(people) & tickets["closed_day"].notna()]
        self.done_rows = [self.row[k] for k in done["key"]]
        self.x_done = self.x[self.done_rows]
        self.done = done.reset_index(drop=True)

    def neighbours(self, t: pd.Series, k: int = SIMILAR_K, other_requesters: bool = True,
                   as_of: pd.Timestamp | None = None) -> pd.DataFrame:
        """The `k` most similar tickets finished before `as_of` (default: when `t` was created): key, assignee, weight.

        weight = similarity x recency (halving every EXPERTISE_HALF_LIFE_DAYS). With `other_requesters`, tickets from
        `t`'s own requester are skipped."""
        as_of = as_of if as_of is not None else t["created_day"]
        sims = (self.x_done @ self.x[self.row[t["key"]]].T).toarray().ravel()
        usable = (self.done["closed_day"] < as_of).to_numpy() & (self.done["key"] != t["key"]).to_numpy() \
            & (sims >= MIN_SIMILARITY)
        if other_requesters and "reporter_name" in self.done.columns and pd.notna(t.get("reporter_name")):
            usable &= (self.done["reporter_name"] != t["reporter_name"]).to_numpy()
        idx = [i for i in sims.argsort()[::-1] if usable[i]][:k]
        if not idx:
            return pd.DataFrame(columns=["key", "assignee_name", "weight"])
        age = (as_of - self.done.loc[idx, "closed_day"]).dt.days.clip(lower=0)
        weight = sims[idx] * 0.5 ** (age.to_numpy() / float(EXPERTISE_HALF_LIFE_DAYS))
        return pd.DataFrame({"key": self.done.loc[idx, "key"].to_numpy(),
                             "assignee_name": self.done.loc[idx, "assignee_name"].to_numpy(), "weight": weight})

    def credit(self, t: pd.Series, k: int = SIMILAR_K, other_requesters: bool = False,
               as_of: pd.Timestamp | None = None) -> pd.Series:
        """Each person's credit from the similar tickets they finished."""
        n = self.neighbours(t, k, other_requesters, as_of)
        return n.groupby("assignee_name")["weight"].sum() if len(n) else pd.Series(dtype=float)


def build_similar(tickets: pd.DataFrame, people: list[str]) -> SimilarTickets | None:
    """The similar-ticket model for these tickets, or None when scikit-learn isn't installed (domains alone are used)."""
    try:
        texts = pd.Series([early_text(r) for _, r in tickets.iterrows()], index=tickets["key"].to_numpy())
        return SimilarTickets(tickets, texts[~texts.index.duplicated()], people)
    except ImportError:
        return None


def _experience(prior: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Recency-weighted experience per person from rows (assignee_name, domains, w), each domain at its own weight."""
    if not weights or prior.empty:
        return pd.Series(dtype=float)
    return (prior["domains"].map(weights).fillna(0.0) * prior["w"]).groupby(prior["assignee_name"]).sum()


def expertise_scorer(model: SimilarTickets | None):
    """The scorer used by the back-test: scorer(ticket, prior rows, Relations) -> experience per person.

    `prior` is the work finished before the ticket was created, one row per ticket and domain, with its recency weight in `w`."""
    def score(t: pd.Series, prior: pd.DataFrame, rel: Relations) -> pd.Series:
        domain = t["domain"] if isinstance(t["domain"], str) else None
        out = _experience(prior, domain_weights(list(t["domains"]), domain, rel.hubs))
        if model is not None:
            out = out.add(SIMILAR_WEIGHT * model.credit(t, SIMILAR_K, other_requesters=True), fill_value=0.0)
        return out
    return score
