"""Suggested Assignments: who should take each unassigned or triage ticket, and why.

Advisory: this module writes nothing (assigning in Jira is a separate, safe-mode step in jira_assign.py).
Jira data only. PE tickets (no Features, Initiatives or
Release Management CAR tickets).

Tickets: open PE tickets that are unassigned, or in Triage / Reviewing.
Candidates: the core team -- people who completed at least MIN_DELIVERED PE tickets in the last
CANDIDATE_DAYS days, minus DEPARTED_MEMBERS (excluded assignees are already left out by prepare_tickets).

Each candidate is scored on what the other pages already measure:
- Expertise: recency-weighted completed (1.0) and active (0.5) tickets in the ticket's primary domain,
  tagged from title + human comments with the shared taxonomy (reports/domains.py).
- Availability and SLA fit: the Backlog queue simulation with the ticket added to the candidate's
  queue in ATC order (their In Progress work first), using their own speed at that priority. Gives a
  likely start and a P85 finish, and whether that finish beats the SLA due date.
- Load: a penalty when someone already carries more work in progress than usual, and a smaller one for
  each ticket the plan has already suggested to them, so work spreads across the team.

The plan takes tickets in ATC order and adds each suggestion to that person's queue before scoring
the next ticket, so one expert is not handed everything.
"""
from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import backlog_report as br
from reports import domains
from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports.atc_sequence import build_atc_sequence
from reports.jira_assign import account_ids


JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
TRIAGE_STATUSES = {"triage", "reviewing"}
# People who have left PE: never suggested for new work. Their past tickets still count everywhere else
# (keep this separate from EXCLUDED_ASSIGNEES, which removes people from all history). Lower-case names.
DEPARTED_MEMBERS = {"randall puterbaugh", "andriy petryshyn"}
CANDIDATE_DAYS = 90
MIN_DELIVERED = 5
EXPERTISE_HALF_LIFE_DAYS = 120
ACTIVE_WEIGHT = 0.5
WEIGHT_EXPERTISE, WEIGHT_AVAILABILITY = 0.6, 0.4
LOAD_PENALTY, LOAD_PENALTY_FROM = 0.15, 1.3      # penalise when current WIP is 1.3x+ the usual
PLAN_BALANCE_PENALTY = 0.08                      # per ticket already suggested to the same person in this plan
STRETCH_MAX_SHARE = 0.5                          # stretch = some exposure, but at most half the top expert's
INK = ipr.INK


def _empty_payload(message: str | None = None) -> dict:
    return {"error_message": message, "kpis": {}, "plan_df": pd.DataFrame(), "options": {}, "matrix_fig": None, "matrix_df": pd.DataFrame(),
            "concentration_df": pd.DataFrame(), "load_df": pd.DataFrame(), "account_ids": {}, "current_accounts": {}}


def _text(row) -> str:
    comments = row.get("comments")
    bodies = " ".join(c.get("body", "") for c in comments) if isinstance(comments, list) else ""
    return f"{row.get('summary') or ''} {bodies}"


def expertise_matrix(tickets: pd.DataFrame, today: pd.Timestamp, people: list[str]) -> pd.DataFrame:
    """Recency-weighted domain experience: rows = domain, columns = person."""
    work = tickets[tickets["assignee_name"].isin(people)]
    done = work["outcome"].eq("Done")
    active = ~work["is_closed"] & work["stage"].isin(["In Progress", "Validating", "Blocked & On Hold", "Release"])
    work = work[done | active]
    when = work["closed_day"].where(work["outcome"].eq("Done"), today)
    age = (today - when).dt.days.clip(lower=0)
    weight = (0.5 ** (age / EXPERTISE_HALF_LIFE_DAYS)) * np.where(work["outcome"].eq("Done"), 1.0, ACTIVE_WEIGHT)
    rows = []
    for (_, row), w in zip(work.iterrows(), weight):
        for d in row["domains"]:
            rows.append((d, row["assignee_name"], w))
    if not rows:
        return pd.DataFrame(columns=people)
    return (pd.DataFrame(rows, columns=["domain", "person", "w"]).pivot_table(
        index="domain", columns="person", values="w", aggfunc="sum", fill_value=0.0).reindex(columns=people, fill_value=0.0))


class _Queues:
    """Each candidate's current work and backlog queue, so a ticket can be 'tried' in anyone's queue."""

    def __init__(self, tickets, in_progress, history, effects, wip, typical, today, hol, people):
        self.today, self.hol, self.history, self.effects = today, hol, history, effects
        self.slots = {p: br._slots(typical.get(p, 1.0)) for p in people}
        rng = np.random.default_rng(br.RNG_SEED)
        self.ip_samples = {
            p: [ipr.sample_remaining(ipr._remaining_distribution(row, history, effects, wip), rng, br.SIMULATIONS)
                for _, row in in_progress[in_progress["assignee_name"] == p].iterrows()]
            for p in people}
        backlog = tickets[tickets["status"].astype(str).str.strip().str.casefold().isin(br.BACKLOG_STATUSES)
                          & tickets["assignee_name"].isin(people)]
        self.queue = {p: [row for _, row in backlog[backlog["assignee_name"] == p].iterrows()] for p in people}
        self.rng = rng

    def try_ticket(self, person: str, ticket: pd.Series) -> dict:
        """Likely start / P85 finish (business days from today) of `ticket` if added to `person`'s queue."""
        candidate = ticket.copy()
        candidate["assignee_name"] = person
        rows = self.queue[person] + [candidate]
        atc = build_atc_sequence(pd.DataFrame({
            "Ticket": [r["key"] for r in rows],
            "Priority": [r.get("priority_name") or "None" for r in rows],
            "Size": [r["size"] for r in rows],
            "Days Left": [((r["target_end_day"] - self.today).days if pd.notna(r["target_end_day"]) else None) for r in rows],
            "Days Old": [r.get("age_days", 0) or 0 for r in rows],
            "Status": "To Do",
        }))
        order = {k: i for i, k in enumerate(atc["Ticket"])}
        rows = sorted(rows, key=lambda r: order.get(r["key"], 0))
        samples, earliest = [], []
        for r in rows:
            r = r.copy()
            r["elapsed_bd"] = 0.0
            samples.append(ipr.sample_remaining(ipr._remaining_distribution(r, self.history, self.effects, {}),
                                                self.rng, br.SIMULATIONS))
            start = r["start_day"]
            earliest.append(float(np.busday_count(self.today.date(), start.date(), holidays=self.hol))
                            if pd.notna(start) and start > self.today else 0.0)
        starts, finishes, _ = br._simulate_queue(self.ip_samples[person], samples, earliest, self.slots[person])
        i = [r["key"] for r in rows].index(ticket["key"])
        return {"start_p50": float(np.quantile(starts[i], 0.5)), "finish_p85": float(np.quantile(finishes[i], 0.85)),
                "work_p85": float(np.quantile(finishes[i] - starts[i], 0.85))}

    def add(self, person: str, ticket: pd.Series) -> None:
        added = ticket.copy()
        added["assignee_name"] = person
        self.queue[person].append(added)


def _sla_fit(ticket: pd.Series, trial: dict, today, hol) -> bool:
    """Would the ticket finish within its SLA with this person (P85)?"""
    if not ticket["sla_applies"]:
        return True
    if pd.notna(ticket["start_day"]):
        due = ipr._add_busdays(pd.Series([ticket["start_day"]]), pd.Series([float(ticket["sla_bd"])]), hol).iloc[0]
        finish = ipr._add_busdays(pd.Series([today]), pd.Series([trial["finish_p85"]]), hol).iloc[0]
        return finish <= due
    # No Target start yet: the SLA clock would start when work starts, so the work itself must fit.
    return trial["work_p85"] <= ticket["sla_bd"]


def build_assignment_visuals(df_issues: pd.DataFrame) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    t = facts[facts["sla_applies"]].copy()
    t["domains"] = [domains.tag(_text(row)) for _, row in t.iterrows()]
    t["domain"] = t["domains"].map(domains.primary).astype(object).where(lambda s: s.notna(), None)

    recent_done = t[t["outcome"].eq("Done") & (t["closed_day"] > today - pd.Timedelta(days=CANDIDATE_DAYS))
                    & t["assignee_name"].ne("Unassigned")]
    delivered = recent_done["assignee_name"].value_counts()
    people = sorted(p for p in delivered[delivered >= MIN_DELIVERED].index
                    if str(p).strip().casefold() not in DEPARTED_MEMBERS)
    if not people:
        return _empty_payload("No core team members found (nobody completed enough tickets recently).")

    status_norm = t["status"].astype(str).str.strip().str.casefold()
    queue = t[~t["is_closed"] & (t["assignee_name"].eq("Unassigned") | status_norm.isin(TRIAGE_STATUSES))].copy()
    payload = _empty_payload()

    # Forecast inputs shared with the In Progress and Backlog pages.
    prepared = ipr.prepare_tickets(df_issues)
    in_progress = ipr.prepare_in_progress(prepared, today, hol)
    history = ipr._build_history(prepared, today, hol)
    _, effects = ipr._assignee_effects(history)
    typical = ipr._typical_wip(history, in_progress, today, hol)
    wip_factor = ipr._wip_factors(typical, in_progress)
    queues = _Queues(t, in_progress, history, effects, wip_factor, typical, today, hol, people)

    matrix = expertise_matrix(t, today, people)
    wip_now = in_progress.groupby("assignee_name").size()
    load = pd.DataFrame({"Person": people,
                         "In Progress Now": [int(wip_now.get(p, 0)) for p in people],
                         "Usual WIP": [round(float(typical.get(p, 0.0)), 1) for p in people],
                         "Delivered (90d)": [int(delivered.get(p, 0)) for p in people]})
    overloaded = {p for p in people if wip_now.get(p, 0) >= LOAD_PENALTY_FROM * max(float(typical.get(p, 0.0)), 1.0)}

    if queue.empty:
        payload["kpis"] = {"to_assign": 0, "with_domain": 0, "fit": 0, "people": 0, "candidates": len(people)}
        payload["matrix_fig"], payload["concentration_df"] = _matrix_outputs(matrix)
        payload["matrix_df"] = matrix
        payload["load_df"] = load
        return payload

    # Plan order: the ATC rule across the whole queue, so the most urgent tickets pick first.
    atc = build_atc_sequence(pd.DataFrame({
        "Ticket": queue["key"], "Priority": queue.get("priority_name", "None").fillna("None"), "Size": queue["size"],
        "Days Left": (queue["target_end_day"] - today).dt.days, "Days Old": queue["age_days"], "Status": "To Do"}))
    order = {k: i for i, k in enumerate(atc["Ticket"])}
    queue = queue.assign(_order=queue["key"].map(order)).sort_values("_order")

    plan, options = [], {}
    given: dict[str, int] = {}
    for _, ticket in queue.iterrows():
        domain = ticket["domain"] if isinstance(ticket["domain"], str) else None
        column = matrix.loc[domain] if domain is not None and domain in matrix.index else pd.Series(0.0, index=people)
        top = float(column.max()) if len(column) else 0.0
        trials = {p: queues.try_ticket(p, ticket) for p in people}
        latest = max(v["finish_p85"] for v in trials.values()) or 1.0
        scored = []
        for p in people:
            expertise = float(column.get(p, 0.0)) / top if top > 0 else 0.0
            availability = 1 - trials[p]["finish_p85"] / latest if latest > 0 else 1.0
            score = (WEIGHT_EXPERTISE * expertise + WEIGHT_AVAILABILITY * availability
                     - (LOAD_PENALTY if p in overloaded else 0) - PLAN_BALANCE_PENALTY * given.get(p, 0))
            scored.append({"person": p, "fits_sla": _sla_fit(ticket, trials[p], today, hol), "score": score,
                           "expertise": expertise, "domain_weight": float(column.get(p, 0.0)), **trials[p]})
        ranked = sorted(scored, key=lambda s: (not s["fits_sla"], -s["score"]))
        best = ranked[0]
        backup = next((s for s in ranked[1:]), None)
        stretch = next((s for s in sorted(scored, key=lambda s: (not s["fits_sla"], s["finish_p85"]))
                        if s["person"] not in (best["person"], backup["person"] if backup else None)
                        and 0 < s["expertise"] <= STRETCH_MAX_SHARE), None) if domain else None
        queues.add(best["person"], ticket)
        given[best["person"]] = given.get(best["person"], 0) + 1

        def when(bd):
            return ipr._add_busdays(pd.Series([today]), pd.Series([bd]), hol).iloc[0].date()

        why = []
        if domain:
            why.append(f"≈{best['domain_weight']:.0f} recent {domain} tickets" if best["domain_weight"] >= 0.5
                       else f"little {domain} history (chosen for availability)")
        else:
            why.append("no domain recognised (chosen for availability)")
        why.append(f"likely start {when(best['start_p50']):%b %d}, done by {when(best['finish_p85']):%b %d}")
        if not best["fits_sla"]:
            why.append("no one fits the SLA: earliest finish shown")
        if best["person"] in overloaded:
            why.append("already above usual load")
        plan.append({
            "Ticket": JIRA_BROWSE_BASE_URL + ticket["key"], "Priority": ticket["priority_bucket"], "Size": ticket["size"],
            "Domain": domain or "—", "Jira Status": ticket["status"],
            "Current Owner": ticket["assignee_name"] if ticket["assignee_name"] != "Unassigned" else "",
            "Suggested": best["person"], "Why": "; ".join(why),
            "Likely Start": when(best["start_p50"]), "Done By (P85)": when(best["finish_p85"]),
            "SLA Fit": "✓" if best["fits_sla"] else "✖",
            "Backup": backup["person"] if backup else "", "Stretch": stretch["person"] if stretch else "",
            "Waiting (days)": int(ticket["age_days"]),
            "Summary": str(ticket.get("summary") or "")[:100],
        })
        options[ticket["key"]] = pd.DataFrame([{
            "Person": s["person"], "Fits SLA": "✓" if s["fits_sla"] else "✖", "Score": round(s["score"], 2),
            "Domain Experience": round(s["domain_weight"], 1), "Likely Start": when(s["start_p50"]),
            "Done By (P85)": when(s["finish_p85"]), "Above Usual Load": "yes" if s["person"] in overloaded else "",
        } for s in ranked])

    plan_df = pd.DataFrame(plan)
    payload["plan_df"] = plan_df
    # For the safe-mode "Assign in Jira" button: assign by account id (never by name), and know what each ticket
    # held when the plan was made so a change made in Jira since then is never overwritten.
    known = account_ids(df_issues)
    payload["account_ids"] = {p: known[p] for p in people if p in known}
    if "assignee_account_id" in df_issues.columns:
        held = df_issues.set_index("key")["assignee_account_id"]
        payload["current_accounts"] = {k: (v if isinstance(v, str) else None) for k, v in held.items()
                                       if k in set(queue["key"])}
    payload["options"] = options
    payload["kpis"] = {
        "to_assign": int(len(plan_df)), "with_domain": int((plan_df["Domain"] != "—").sum()),
        "fit": int((plan_df["SLA Fit"] == "✓").sum()), "people": int(plan_df["Suggested"].nunique()),
        "candidates": len(people), "max_per_person": int(plan_df["Suggested"].value_counts().max()),
    }
    payload["matrix_fig"], payload["concentration_df"] = _matrix_outputs(matrix)
    payload["matrix_df"] = matrix
    payload["load_df"] = load
    return payload


def matrix_figure(matrix: pd.DataFrame, group: str | None = None) -> go.Figure | None:
    """Who knows what: every domain with recent work (optionally one group's), busiest first, x people."""
    if matrix is None or matrix.empty:
        return None
    m = matrix[matrix.sum(axis=1) > 0]
    if group:
        m = m[[domains.group_of(d) == group for d in m.index]]
    if m.empty:
        return None
    m = m.loc[m.sum(axis=1).sort_values(ascending=False).index]
    m = m.loc[:, m.sum(axis=0) > 0]
    fig = go.Figure(go.Heatmap(
        z=m.values, x=list(m.columns), y=list(m.index), colorscale="Blues", zmin=0, xgap=2, ygap=2,
        text=[[f"{v:.1f}" if v >= 0.5 else "" for v in row] for row in m.values], texttemplate="%{text}",
        colorbar=dict(title="Weighted<br>tickets"),
        hovertemplate="%{y} · %{x}: %{z:.1f} recency-weighted tickets<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=max(320, 26 * len(m) + 120), margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _matrix_outputs(matrix: pd.DataFrame) -> tuple[go.Figure | None, pd.DataFrame]:
    """Who knows what (all domains x people) and the domains that depend on one person."""
    if matrix.empty:
        return None, pd.DataFrame()
    fig = matrix_figure(matrix)
    rows = []
    for domain, col in matrix.iterrows():
        total = float(col.sum())
        if total <= 0:
            continue
        ranked = col.sort_values(ascending=False)
        rows.append({"Domain": domain, "Top Person": ranked.index[0], "Top Share %": round(ranked.iloc[0] / total * 100),
                     "Second": ranked.index[1] if ranked.iloc[1] > 0 else "—",
                     "People With Experience": int((col > 0.5).sum()), "Weighted Tickets": round(total, 1)})
    conc = pd.DataFrame(rows).sort_values(["Top Share %", "Weighted Tickets"], ascending=[False, False])
    return fig, conc[conc["Weighted Tickets"] >= 3].reset_index(drop=True)
