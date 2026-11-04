"""Executive Report: one month of Platform Engineering in a self-contained HTML document for leadership.

Built on demand, never committed (the GitHub repo is public): by scripts/executive_report.py, or by the
Executive Summary page's download button, which only appears on the machine running the app
(report_permission).

The reporting period is one full calendar month compared with the month before (default: the last full
month). Delivery, SLA and lead-time numbers are recomputed for that window with the dashboard's own rules
(executive_summary._facts, service_level_agreement_report._judged / _rates), so they match the app pages.
Sections that describe the present (open work, risk, forecast, dependencies) are as of the data fetch and say so.

Names: ticket keys and business leads appear; engineers do not. People and knowledge risk are reported at
team level (counts and shares, no names).

Sections: summary (tiles, headlines, decisions) · delivery and capacity · SLA · open work and flow ·
requesters · people and knowledge risk · data quality · recommendations · appendix (definitions, tickets).
Optional sections that fail to build are left out with a note instead of breaking the report.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import change_history as chg
from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports import service_level_agreement_report as sla

TREND_MONTHS = 12
SLA_MET_TARGET = 1 - sla.BREACH_GOAL               # 90% met = the SLA page's 10% breach goal
AFTER_FACT_ALERT = 0.20                            # share of completed tickets with the clock set after the work
SERIES = ("#2a78d6", "#eb6834")                    # categorical slots 1 and 2 (as on the app pages)
TONE_COLORS = {"good": "#1f8a4c", "warn": "#b7791f", "bad": "#d03b3b", "info": "#475569"}
TONE_ICONS = {"good": "▲", "warn": "●", "bad": "▼", "info": "■"}
INK, MUTED, GRID = "#1e293b", "#64748b", "rgba(148,163,184,0.3)"
FIG_WIDTH = 700
TICKET_ROWS = 12
ENABLE_FLAG = "EXECUTIVE_REPORT_ENABLED"


# ── Who may build it ────────────────────────────────────────────────────────────

def report_permission(host: str | None) -> tuple[bool, str]:
    """(allowed, reason): only on the machine running the app, and only when the .env flag is on."""
    from reports import jira_dates
    enabled, _ = jira_dates.env_flag(ENABLE_FLAG)
    if not enabled:
        return False, f"Set {ENABLE_FLAG}=true in the project .env file to build the report on this machine."
    if not jira_dates.is_local_host(host):
        return False, "The Executive Report can only be built on the machine running the app."
    return True, ""


# ── Period ──────────────────────────────────────────────────────────────────────

def last_full_month(today: pd.Timestamp | None = None) -> str:
    today = today if today is not None else ipr.today_local()
    return (today.to_period("M") - 1).strftime("%Y-%m")


def period(month: str) -> dict:
    """Window [start, end) for `month` ("2026-10") and the month before it."""
    p = pd.Period(month, "M")
    prev = p - 1
    return {"month": str(p), "start": p.start_time, "end": (p + 1).start_time, "label": p.strftime("%B %Y"),
            "short": p.strftime("%b %Y"), "prev_start": prev.start_time, "prev_label": prev.strftime("%B %Y"),
            "prev_short": prev.strftime("%b %Y")}


def available_months(df_issues: pd.DataFrame, today: pd.Timestamp | None = None, limit: int = 12) -> list[str]:
    """Full months with tickets in the data, newest first (the current, unfinished month is left out)."""
    if df_issues is None or df_issues.empty or "created" not in df_issues.columns:
        return []
    today = today if today is not None else ipr.today_local()
    first = ipr._to_day(df_issues["created"], ipr.LOCAL_TZ).min()
    if pd.isna(first):
        return []
    months = pd.period_range(first.to_period("M"), today.to_period("M") - 1, freq="M")
    return [str(m) for m in months[::-1][:limit]]


# ── Numbers ─────────────────────────────────────────────────────────────────────

def _judged(t: pd.DataFrame, today: pd.Timestamp, hol: np.ndarray, history, original_starts) -> pd.DataFrame:
    """PE tickets judged on their SLA, as on the SLA page (original Target start, clock-after-the-fact flag)."""
    pe = t[t["sla_applies"]]
    if original_starts is None:
        try:
            from reports.jira_dates import original_target_starts
            original_starts = original_target_starts()
        except Exception:
            original_starts = {}
    pe, _ = sla._original_starts(pe, original_starts, hol)
    j = sla._judged(pe, today)
    if chg.has_history(history):
        dates = chg.ticket_date_summary(chg.date_changes(history, hol), pe)
        j["clock_after_fact"] = j["key"].map(dates["clock_after_fact"]).fillna(False).astype(bool)
    else:
        j["clock_after_fact"] = False
    return j


def _pct(values: pd.Series, q: float) -> float | None:
    values = values.dropna()
    return float(values.quantile(q)) if len(values) else None


def _window_numbers(t: pd.DataFrame, j: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                    hol: np.ndarray) -> dict:
    closed_in = (t["closed_day"] >= start) & (t["closed_day"] < end)
    created_in = (t["created_day"] >= start) & (t["created_day"] < end)
    done = t[closed_in & t["outcome"].eq("Done")]
    lead = ipr._busdays_between(done["created_day"], done["closed_day"], hol)
    work = ipr._busdays_between(done["start_day"].where(done["start_day"] <= done["closed_day"]), done["closed_day"], hol)
    rates = sla._rates(j, start, end)
    honest = sla._rates(j[~j["clock_after_fact"]], start, end)
    done_j = j[j["outcome"].eq("Done") & (j["closed_day"] >= start) & (j["closed_day"] < end)]
    after = int(done_j["clock_after_fact"].sum())

    def met(r):
        return None if r["done_rate"] is None else 1 - r["done_rate"]

    return {
        "delivered": int(len(done)), "created": int(created_in.sum()), "closed": int(closed_in.sum()),
        "net_flow": int(closed_in.sum() - created_in.sum()),
        "open_end": es._open_at(t, end - pd.Timedelta(days=1)),
        "lead_p50": _pct(lead, 0.5), "lead_p85": _pct(lead, 0.85), "work_p50": _pct(work, 0.5),
        "sla_met": met(rates), "sla_n": rates["done_n"], "sla_late": rates["done_late"],
        "due_breach": rates["due_rate"], "due_n": rates["due_n"],
        "sla_met_honest": met(honest), "sla_n_honest": honest["done_n"],
        "after_fact": after, "after_fact_share": after / len(done_j) if len(done_j) else None,
        "urgent_high": int(done["priority_bucket"].isin(["Urgent", "High"]).sum()),
    }


def _monthly(t: pd.DataFrame, j: pd.DataFrame, last_month: str, hol: np.ndarray) -> pd.DataFrame:
    end = pd.Period(last_month, "M")
    rows = []
    for p in pd.period_range(end - (TREND_MONTHS - 1), end, freq="M"):
        n = _window_numbers(t, j, p.start_time, (p + 1).start_time, hol)
        rows.append({"month": str(p), "label": p.strftime("%b %y"), **n})
    return pd.DataFrame(rows)


# ── Figures ─────────────────────────────────────────────────────────────────────

def _style(fig: go.Figure, height: int = 300, pct_axis: bool = False, left: int = 60) -> go.Figure:
    # Fixed margins: Plotly's automatic ones are computed after first paint, too late for a printed PDF.
    fig.update_layout(width=FIG_WIDTH, height=height, margin=dict(l=left, r=20, t=40, b=40, autoexpand=False),
                      paper_bgcolor="white", plot_bgcolor="white",
                      font=dict(family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif", size=12, color=INK),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
                      hovermode="x unified")
    fig.update_xaxes(showgrid=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, rangemode="tozero")
    if pct_axis:
        fig.update_yaxes(tickformat=".0%", range=[0, 1.08])
    return fig


def _flow_figure(m: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col, name, color in (("created", "Created", SERIES[1]), ("delivered", "Delivered (Done)", SERIES[0])):
        fig.add_bar(x=m["label"], y=m[col], name=name, marker_color=color, marker_line=dict(color="white", width=2),
                    hovertemplate="%{y:,}<extra>" + name + "</extra>")
    fig.update_layout(barmode="group", bargap=0.25)
    return _style(fig)


def _sla_figure(m: pd.DataFrame, history: bool) -> go.Figure:
    fig = go.Figure()
    series = [("sla_met", "All completed tickets", SERIES[0])]
    if history:
        series.append(("sla_met_honest", "Clock set before the work finished", SERIES[1]))
    for col, name, color in series:
        fig.add_scatter(x=m["label"], y=m[col], name=name, mode="lines+markers", line=dict(color=color, width=2),
                        marker=dict(size=8, line=dict(color="white", width=2)),
                        hovertemplate="%{y:.0%}<extra>" + name + "</extra>")
    fig.add_hline(y=SLA_MET_TARGET, line=dict(color=MUTED, dash="dash", width=1),
                  annotation_text=f"Target {SLA_MET_TARGET:.0%}", annotation_position="bottom right",
                  annotation_font_color=MUTED)
    fig.update_yaxes(title_text="SLA met")
    fig = _style(fig, height=280)
    low = pd.concat([m[c] for c, _, _ in series]).min()
    fig.update_yaxes(tickformat=".0%", range=[min(0.5, (low if pd.notna(low) else 0.5) - 0.05), 1.0])
    return fig


def _after_fact_figure(m: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(x=m["label"], y=m["after_fact_share"], marker_color=SERIES[1],
                           text=[f"{v:.0%}" if pd.notna(v) else "" for v in m["after_fact_share"]],
                           textposition="outside", cliponaxis=False,
                           hovertemplate="%{y:.0%} of completed tickets<extra></extra>"))
    fig.update_yaxes(title_text="Share of completed tickets")
    return _style(fig, height=240, pct_axis=True)


def _lead_figure(m: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col, name, color in (("lead_p50", "Typical (median)", SERIES[0]), ("lead_p85", "85% done within", SERIES[1])):
        fig.add_scatter(x=m["label"], y=m[col], name=name, mode="lines+markers", line=dict(color=color, width=2),
                        marker=dict(size=8, line=dict(color="white", width=2)),
                        hovertemplate="%{y:.1f} business days<extra>" + name + "</extra>")
    fig.update_yaxes(title_text="Business days")
    return _style(fig, height=260)


# ── Text helpers ────────────────────────────────────────────────────────────────

def _e(value) -> str:
    return html.escape("" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value))


def _md(text: str) -> str:
    """Escape, then **bold**."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _e(text))


def _p(v, digits: int = 0) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:.{digits}%}"


def _n(v, digits: int = 0) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:,.{digits}f}"


def _ticket(key: str) -> str:
    return f'<a href="{_e(ipr.JIRA_BROWSE_BASE_URL + str(key))}">{_e(key)}</a>'


def _tile(label: str, now, prev, kind: str, better: str, prev_label: str, note: str = "") -> dict:
    """kind: count / pct / days; better: up / down / none."""
    show = {"count": lambda v: _n(v), "pct": lambda v: _p(v), "days": lambda v: "—" if v is None else f"{v:.1f} bd"}[kind]
    delta, tone = "", "info"
    if now is not None and prev is not None and not pd.isna(now) and not pd.isna(prev):
        d = now - prev
        if kind == "pct":
            delta = f"{d * 100:+.0f} pts vs {prev_label}"
        elif kind == "days":
            delta = f"{d:+.1f} bd vs {prev_label}"
        else:
            delta = f"{d:+,.0f} vs {prev_label}"
        eps = 0.005 if kind == "pct" else 0.05
        if better != "none" and abs(d) > eps:
            tone = "good" if (d > 0) == (better == "up") else "warn"
    return {"label": label, "value": show(now), "delta": delta, "tone": tone, "note": note}


def _safe(build, *args, **kwargs):
    try:
        out = build(*args, **kwargs)
    except Exception as exc:  # an optional section must not break the report
        return None, f"{type(exc).__name__}: {exc}"
    if isinstance(out, dict) and out.get("error_message"):
        return None, out["error_message"]
    return out, None


# ── Build ───────────────────────────────────────────────────────────────────────

def build_executive_report(df_issues: pd.DataFrame, history: pd.DataFrame | None = None, month: str | None = None,
                           today: pd.Timestamp | None = None, original_starts: dict | None = None,
                           fetched_at: datetime | None = None) -> dict:
    if df_issues is None or df_issues.empty:
        return {"error_message": "No ticket data loaded."}
    today = today if today is not None else ipr.today_local()
    month = month or last_full_month(today)
    per = period(month)
    if per["end"] > today + pd.Timedelta(days=1):
        per["partial"] = True
    hol = ipr._calendar_holidays(today)
    t = es._facts(df_issues, today, hol)
    j = _judged(t, today, hol, history, original_starts)
    has_history = chg.has_history(history)

    now = _window_numbers(t, j, per["start"], per["end"], hol)
    prev = _window_numbers(t, j, per["prev_start"], per["start"], hol)
    monthly = _monthly(t, j, month, hol)
    notes: list[str] = []

    # Open work today (forecast-based risk, as on the Executive Summary).
    from reports.backlog_report import build_backlog_visuals
    ip, err = _safe(ipr.build_in_progress_visuals, df_issues, risk_basis="SLA")
    bl, err2 = _safe(build_backlog_visuals, df_issues, risk_basis="SLA")
    open_t = es._stage_risk(t, today, hol, ip or {}, bl or {})
    open_pe = open_t[open_t["sla_applies"]]
    status_norm = open_t["status"].astype(str).str.strip().str.casefold()
    open_now = {
        "total": int(len(open_t)), "breached": int(open_pe["risk"].eq("Breached").sum()),
        "likely_late": int(open_pe["risk"].eq("Likely Late").sum()),
        "at_risk": int(open_pe["risk"].eq("At Risk").sum()),
        "blocked": int(status_norm.eq("blocked").sum()), "on_hold": int(status_norm.eq("on hold").sum()),
        "old": int((open_t["age_days"] > 90).sum()), "no_clock": int(open_pe["sla_due"].isna().sum()),
        "stage_fig": _style(es._stage_figure(open_t), height=280, left=150) if len(open_t) else None,
    }

    # Context from the detail pages; each one optional.
    from reports import capacity_report, forecast_report
    from reports import estimated_size_distribution_report as size_report
    from reports import validating_report
    cap, _ = _safe(capacity_report.build_capacity_visuals, df_issues)
    fc, _ = _safe(forecast_report.build_forecast_visuals, df_issues)
    size, _ = _safe(size_report.build_estimated_size_distribution_visuals, df_issues)
    val, _ = _safe(validating_report.build_validating_visuals, df_issues, history)
    try:
        from reports import distribution_by_business_leader as leaders
        lead, lead_err = _safe(leaders.build_business_leader_visuals, df_issues, month, month)
    except ImportError as exc:
        lead, lead_err = None, str(exc)
    try:
        from reports import word_of_the_month_report as wom
        conv, conv_err = _safe(wom.build_word_of_the_month_visuals, df_issues, month, month)
    except ImportError as exc:
        conv, conv_err = None, str(exc)
    try:
        from reports import dependency_graph as dg
        from reports import knowledge_graph as kg
        deps, _ = _safe(lambda d: dg.analyse(dg.build_graph(d)), df_issues)
        know, _ = _safe(kg.build_knowledge_graph, df_issues, 6)
    except ImportError:
        deps, know = None, None
        notes.append("Dependencies and knowledge risk need networkx; those parts are left out.")
    for name, missing in (("business leads", lead_err if lead is None else None),
                          ("ticket conversations", conv_err if conv is None else None)):
        if missing:
            notes.append(f"The {name} section could not be built ({missing}).")

    report = {
        "error_message": None, "period": per, "today": today, "fetched_at": fetched_at, "history": has_history,
        "now": now, "prev": prev, "monthly": monthly, "open": open_now, "notes": notes,
        "capacity": (cap or {}).get("kpis"), "forecast": fc, "size": (size or {}).get("kpis"),
        "validating": val, "leaders": lead, "conversations": conv, "dependencies": deps, "knowledge": know,
        "figures": {"flow": _flow_figure(monthly), "sla": _sla_figure(monthly, has_history),
                    "after_fact": _after_fact_figure(monthly) if has_history else None,
                    "lead": _lead_figure(monthly)},
        "late_df": _late_tickets(j, per),
        "after_fact_df": _after_fact_tickets(j, per) if has_history else pd.DataFrame(),
        "attention_df": _attention(open_pe),
        "unknown_lead_share": _unknown_lead_share(t, per),
    }
    report["tiles"] = _tiles(report)
    report["headlines"] = _headlines(report)
    report["recommendations"] = _recommendations(report)
    return report


def _late_tickets(j: pd.DataFrame, per: dict) -> pd.DataFrame:
    late = j[j["outcome"].eq("Done") & (j["closed_day"] >= per["start"]) & (j["closed_day"] < per["end"])
             & j["breached"].astype(bool)]
    return late.sort_values("closed_day")[["key", "priority_bucket", "size", "lead", "closed_day", "sla_due", "summary"]]


def _after_fact_tickets(j: pd.DataFrame, per: dict) -> pd.DataFrame:
    a = j[j["outcome"].eq("Done") & (j["closed_day"] >= per["start"]) & (j["closed_day"] < per["end"])
          & j["clock_after_fact"]]
    return a[["key", "lead"]]


def _attention(open_pe: pd.DataFrame) -> pd.DataFrame:
    flagged = open_pe[open_pe["risk"].isin(["Breached", "Likely Late"])].copy()
    rank = {r: i for i, r in enumerate(es.RISK_ORDER)}
    prio = {p: i for i, p in enumerate(ipr.PRIORITY_ORDER)}
    flagged["_r"], flagged["_p"] = flagged["risk"].map(rank), flagged["priority_bucket"].map(prio)
    flagged = flagged.sort_values(["_r", "_p", "days_past_sla", "age_days"], ascending=[True, True, False, False])
    return flagged[["key", "risk", "stage", "priority_bucket", "lead", "days_past_sla", "age_days", "summary"]]


def _unknown_lead_share(t: pd.DataFrame, per: dict) -> float | None:
    created = t[(t["created_day"] >= per["start"]) & (t["created_day"] < per["end"])]
    return float(created["lead"].eq("Unknown").mean()) if len(created) else None


# ── Words ───────────────────────────────────────────────────────────────────────

def _tiles(r: dict) -> list[dict]:
    now, prev, pl = r["now"], r["prev"], r["period"]["prev_short"]
    tiles = [
        _tile("Delivered", now["delivered"], prev["delivered"], "count", "up", pl, "tickets moved to Done"),
        _tile("Created", now["created"], prev["created"], "count", "none", pl, "new requests"),
        _tile("Net flow", now["net_flow"], prev["net_flow"], "count", "up", pl, "closed minus created"),
        _tile("Open at month end", now["open_end"], prev["open_end"], "count", "down", pl, "all open tickets"),
        _tile("SLA met", now["sla_met"], prev["sla_met"], "pct", "up", pl,
              f"of {now['sla_n']} completed PE tickets"),
    ]
    if r["history"]:
        tiles.append(_tile("SLA met, clock set in time", now["sla_met_honest"], prev["sla_met_honest"], "pct", "up", pl,
                           f"of {now['sla_n_honest']}; leaves out clocks set after the work"))
    tiles += [
        _tile("Typical lead time", now["lead_p50"], prev["lead_p50"], "days", "down", pl, "request to Done, median"),
        _tile("85% done within", now["lead_p85"], prev["lead_p85"], "days", "down", pl, "request to Done"),
    ]
    return tiles


def _headlines(r: dict) -> list[tuple[str, str]]:
    now, prev, per, o = r["now"], r["prev"], r["period"], r["open"]
    lines: list[tuple[str, str]] = []
    change = (now["delivered"] - prev["delivered"]) / prev["delivered"] if prev["delivered"] else None
    if change is not None:
        lines.append(("good" if change >= 0 else "warn",
                      f"**{now['delivered']}** tickets delivered in {per['label']}, "
                      f"{'up' if change >= 0 else 'down'} {abs(change):.0%} on {per['prev_label']}."))
    lines.append(("good" if now["net_flow"] >= 0 else "warn",
                  f"{now['closed']} closed vs {now['created']} created: open work "
                  f"{'shrank' if now['net_flow'] >= 0 else 'grew'} by {abs(now['net_flow'])} during the month."))
    if now["sla_met"] is not None:
        tone = "good" if now["sla_met"] >= SLA_MET_TARGET else "bad"
        lines.append((tone, f"SLA met on **{now['sla_met']:.0%}** of completed PE tickets "
                            f"(target {SLA_MET_TARGET:.0%}; {per['prev_short']}: {_p(prev['sla_met'])})."))
    if r["history"] and now["after_fact_share"] is not None and now["after_fact_share"] >= AFTER_FACT_ALERT:
        lines.append(("bad", f"**{now['after_fact_share']:.0%}** of completed tickets had their SLA clock (Target start) "
                             "set on or after the day the work was done, so they could only count as met. "
                             f"Counting only clocks set in time, SLA met is {_p(now['sla_met_honest'])}."))
    if o["breached"] or o["likely_late"]:
        lines.append(("bad" if o["breached"] else "warn",
                      f"Today **{o['breached']}** open PE tickets are past their SLA and **{o['likely_late']}** more "
                      "are forecast to miss it."))
    cap = r["capacity"] or {}
    if cap.get("demand_ratio"):
        ratio = cap["demand_ratio"]
        lines.append(("warn" if ratio > 1.1 else "good",
                      f"Demand is running at **{ratio:.2f}×** what the team delivers (last 12 weeks)."
                      if ratio > 1 else f"The team delivers more than it receives (demand {ratio:.2f}× delivery)."))
    return lines


def _recommendations(r: dict) -> list[dict]:
    """Ranked actions as {title, why, ask, decision}; decision = needs a leadership call."""
    out: list[dict] = []
    now = r["now"]
    if r["history"] and now["after_fact_share"] is not None and now["after_fact_share"] >= AFTER_FACT_ALERT:
        out.append({"decision": True, "title": "Set the SLA clock before work starts",
                    "why": f"{now['after_fact']} of {now['sla_n']} completed tickets in {r['period']['label']} "
                           f"({now['after_fact_share']:.0%}) had the Target start set after the work was done.",
                    "ask": "Agree that a ticket needs a Target start before it moves to In Progress, and report the "
                           "SLA on tickets whose clock was set in time."})
    cap = r["capacity"] or {}
    if cap.get("demand_ratio", 0) > 1.1:
        out.append({"decision": True, "title": "Match demand and capacity",
                    "why": f"Requests arrive at {cap['demand_ratio']:.2f}× the delivery rate; the queue is about "
                           f"{cap.get('weeks_queued', 0):.1f} weeks of work.",
                    "ask": "Choose: add capacity, defer low-priority work, or agree which requesters' work waits."})
    deps = r["dependencies"] or {}
    k = deps.get("kpis") or {}
    if k.get("can_move"):
        out.append({"decision": False, "title": "Unblock tickets that are only waiting on finished work",
                    "why": f"{k['can_move']} open tickets are linked as blocked, but every ticket blocking them is done.",
                    "ask": "Review these tickets this week and move them forward or remove the stale links."})
    know = r["knowledge"] or {}
    single = know.get("single_person_df")
    if single is not None and len(single):
        left = int(single["Main Person Left PE"].eq("yes").sum())
        out.append({"decision": True, "title": "Spread knowledge in single-person areas",
                    "why": f"{len(single)} technical areas rest on one person"
                           + (f", {left} of them on someone who has left PE" if left else "") + ".",
                    "ask": "Pair a second engineer on the next tickets in: " + ", ".join(single["Domain"].head(6)) + "."})
    conv = r["conversations"] or {}
    if conv.get("recommendations"):
        top = conv["recommendations"][0]
        out.append({"decision": False, "title": f"Reduce '{top['theme']}' friction",
                    "why": f"{top['tickets']} tickets mention it, about {top['extra_per_ticket']:.1f} extra business "
                           f"days each (~{top['total_extra']:.0f} days in the month).",
                    "ask": top["action"]})
    size = r["size"] or {}
    if size.get("coverage_open") is not None and size["coverage_open"] < size.get("target", 0.9):
        out.append({"decision": False, "title": "Size every ticket",
                    "why": f"Only {size['coverage_open']:.0%} of open tickets have an Estimated Size "
                           f"(target {size.get('target', 0.9):.0%}); unsized tickets get a default SLA and weaker forecasts.",
                    "ask": "Make Estimated Size required when a ticket leaves Reviewing."})
    o = r["open"]
    if o["no_clock"]:
        out.append({"decision": False, "title": "Give every open ticket an SLA clock",
                    "why": f"{o['no_clock']} open PE tickets have no Target start, so their SLA cannot be judged.",
                    "ask": "Set a Target start during intake (the Backlog page can suggest dates)."})
    return out


# ── HTML ────────────────────────────────────────────────────────────────────────

CSS = """
:root { --ink:#1e293b; --muted:#64748b; --line:#e2e8f0; --soft:#f8fafc; --accent:#2a78d6; }
* { box-sizing:border-box; }
body { margin:0; background:#eef2f7; color:var(--ink);
       font:14px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width:780px; margin:24px auto; }
section { background:white; padding:32px 40px; margin-bottom:18px; border-radius:6px; box-shadow:0 1px 3px rgba(0,0,0,.08); }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:19px; margin:0 0 12px; border-bottom:2px solid var(--accent); padding-bottom:6px; }
h3 { font-size:15px; margin:18px 0 6px; }
.sub { color:var(--muted); margin:0 0 18px; } .note { color:var(--muted); font-size:12px; }
.tiles { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin:14px 0 18px; }
.tile { border:1px solid var(--line); border-radius:6px; padding:10px 12px; background:var(--soft); }
.tile .label { font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
.tile .value { font-size:22px; font-weight:650; margin:2px 0; }
.tile .delta { font-size:12px; } .tile .why { font-size:11px; color:var(--muted); }
ul.lines { list-style:none; padding:0; margin:0; } ul.lines li { padding:4px 0 4px 22px; position:relative; }
ul.lines li span.dot { position:absolute; left:0; top:4px; font-size:12px; }
table { border-collapse:collapse; width:100%; font-size:12px; margin:8px 0; }
th, td { text-align:left; padding:5px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
th { background:var(--soft); font-weight:600; } td.num, th.num { text-align:right; white-space:nowrap; }
td a { white-space:nowrap; }
a { color:var(--accent); text-decoration:none; }
.kv { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px 18px; margin:10px 0; }
.kv div b { display:block; font-size:18px; color:var(--ink); } .kv div { font-size:12px; color:var(--muted); }
.rec { border-left:4px solid var(--accent); padding:6px 12px; margin:10px 0; background:var(--soft); }
.rec.decision { border-color:#eb6834; } .tag { font-size:10px; font-weight:700; text-transform:uppercase;
  color:white; background:#eb6834; border-radius:3px; padding:1px 5px; margin-left:6px; vertical-align:middle; }
.fig { margin:6px 0 4px; overflow-x:auto; } .wrap { overflow-x:auto; }
@media screen and (max-width:760px) {
  main { margin:0; } section { padding:20px 16px; border-radius:0; }
  .tiles { grid-template-columns:repeat(2, 1fr); } .kv { grid-template-columns:repeat(2, 1fr); }
}
@page { size:Letter; margin:0.5in; }
@media print {
  body { background:white; } main { margin:0; max-width:none; }
  section { box-shadow:none; border-radius:0; padding:0; margin:0 0 28px; }
  section.summary { break-after:page; } section.appendix { break-before:page; }
  h2, h3 { break-after:avoid; } table.keep, .tile, .rec, .fig, .kv, tr { break-inside:avoid; }
  a { color:var(--ink); }
}
"""


class _Figures:
    """Embeds Plotly figures; plotly.js goes inline once so the file works offline and when emailed."""

    def __init__(self):
        self.first = True

    def __call__(self, fig: go.Figure | None, caption: str = "") -> str:
        if fig is None:
            return ""
        out = fig.to_html(full_html=False, include_plotlyjs=self.first,
                          config={"displayModeBar": False, "responsive": False})
        self.first = False
        cap = f'<div class="note">{_md(caption)}</div>' if caption else ""
        return f'<div class="fig">{out}{cap}</div>'


def _table(frame: pd.DataFrame, numeric: set[str] = frozenset()) -> str:
    if frame is None or frame.empty:
        return '<p class="note">None.</p>'
    head = "".join(f'<th class="{"num" if c in numeric else ""}">{_e(c)}</th>' for c in frame.columns)
    body = []
    for _, row in frame.iterrows():
        cells = []
        for c in frame.columns:
            v = row[c]
            text = v if c == "Ticket" else _e(v)
            cells.append(f'<td class="{"num" if c in numeric else ""}">{text}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    keep = ' class="keep"' if len(frame) <= 8 else ""            # short tables never split across pages
    return f'<div class="wrap"><table{keep}><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _lines(items: list[tuple[str, str]]) -> str:
    li = "".join(f'<li><span class="dot" style="color:{TONE_COLORS[tone]}">{TONE_ICONS[tone]}</span>{_md(text)}</li>'
                 for tone, text in items)
    return f'<ul class="lines">{li}</ul>'


def _kv(pairs: list[tuple[str, str]]) -> str:
    return '<div class="kv">' + "".join(f"<div><b>{_e(v)}</b>{_e(k)}</div>" for k, v in pairs) + "</div>"


def _short(text, n: int = 80) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _sec_summary(r: dict) -> str:
    per = r["period"]
    tiles = "".join(
        f'<div class="tile"><div class="label">{_e(t["label"])}</div><div class="value">{_e(t["value"])}</div>'
        f'<div class="delta" style="color:{TONE_COLORS[t["tone"]]}">{_e(t["delta"])}</div>'
        f'<div class="why">{_e(t["note"])}</div></div>' for t in r["tiles"])
    decisions = [x for x in r["recommendations"] if x["decision"]][:3]
    dec = "".join(f'<li><span class="dot" style="color:#eb6834">◆</span><strong>{_e(d["title"])}.</strong> '
                  f'{_e(d["ask"])}</li>' for d in decisions)
    fetched = r["fetched_at"].strftime("%b %d, %Y %H:%M") if r["fetched_at"] else r["today"].strftime("%b %d, %Y")
    partial = ' <strong>(month not finished yet)</strong>' if per.get("partial") else ""
    return (f'<section class="summary"><h1>Platform Engineering · Executive Report</h1>'
            f'<p class="sub">{_e(per["label"])}{partial} compared with {_e(per["prev_label"])} · '
            f'data as of {_e(fetched)}</p><div class="tiles">{tiles}</div>'
            f'<h3>Headlines</h3>{_lines(r["headlines"])}'
            + (f'<h3>Decisions needed</h3><ul class="lines">{dec}</ul>' if dec else "") + "</section>")


def _sec_delivery(r: dict, fig) -> str:
    cap, fc, per = r["capacity"] or {}, r["forecast"] or {}, r["period"]
    parts = [f'<section><h2>1 · Delivery and capacity</h2>',
             fig(r["figures"]["flow"], f"Tickets created and delivered per month, {TREND_MONTHS} months to "
                                       f"{per['label']}."),
             fig(r["figures"]["lead"], "Lead time: business days from request to Done, for tickets delivered "
                                       "each month.")]
    if cap:
        parts.append("<h3>Capacity (last 12 weeks)</h3>" + _kv([
            ("delivered per week", _n(cap.get("throughput"), 1)),
            ("demand vs delivery", f"{cap.get('demand_ratio', 0):.2f}×"),
            ("weeks of work queued", _n(cap.get("weeks_queued"), 1)),
            ("engineers delivering", _n(cap.get("engineers"))),
            ("per engineer per week", _n(cap.get("per_engineer"), 1)),
            ("delivered by the top 3 people", _p(cap.get("top3_share")))]))
    if fc and fc.get("cards"):
        rows = pd.DataFrame([{"Horizon": f"Next {c['weeks']} weeks (to {c['by']:%b %d})", "Likely": c["likely"],
                              "Range": f"{c['low']:,} – {c['high']:,}"} for c in fc["cards"]])
        parts.append("<h3>Forecast: tickets the team is likely to deliver</h3>" + _table(rows, {"Likely"})
                     + f'<p class="note">{_md(fc.get("headline", ""))}</p>')
    return "".join(parts) + "</section>"


def _sec_sla(r: dict, fig) -> str:
    now, per = r["now"], r["period"]
    parts = ['<section><h2>2 · Service levels (SLA)</h2>',
             _kv([("SLA met (completed in month)", _p(now["sla_met"])),
                  ("completed late", f'{now["sla_late"]} of {now["sla_n"]}'),
                  ("missed, of SLAs due in month", _p(now["due_breach"]))]),
             fig(r["figures"]["sla"], f"Share of completed PE tickets finished within their SLA (target "
                                      f"{SLA_MET_TARGET:.0%}). Release Management (CAR) tickets have no PE SLA.")]
    if r["history"]:
        parts.append("<h3>SLA clocks set after the work was done</h3>"
                     f'<p>A ticket\'s SLA clock starts at its Target start. In {_e(per["label"])}, '
                     f'<strong>{now["after_fact"]}</strong> completed tickets ({_p(now["after_fact_share"])}) had the '
                     "Target start set on or after the day they were finished, which can only produce “met”. "
                     f'Counting only clocks set in time, SLA met was <strong>{_p(now["sla_met_honest"])}</strong> '
                     f'(of {now["sla_n_honest"]}).</p>')
        parts.append(fig(r["figures"]["after_fact"], "Share of completed PE tickets whose clock was set after the work."))
    else:
        parts.append('<p class="note">Change history was not loaded, so clocks set after the fact are not checked.</p>')
    late = r["late_df"]
    rows = pd.DataFrame({"Ticket": [_ticket(k) for k in late["key"]], "Priority": late["priority_bucket"].values,
                         "Size": late["size"].values, "Business Lead": late["lead"].values,
                         "Done": late["closed_day"].dt.strftime("%b %d").values,
                         "SLA Due": late["sla_due"].dt.strftime("%b %d").values,
                         "Summary": [_short(s, 60) for s in late["summary"]]})
    parts.append(f"<h3>Completed late in {_e(per['label'])} ({len(rows)})</h3>" + _table(rows.head(TICKET_ROWS)))
    if len(rows) > TICKET_ROWS:
        parts.append(f'<p class="note">{len(rows) - TICKET_ROWS} more in the appendix.</p>')
    return "".join(parts) + "</section>"


def _sec_open(r: dict, fig) -> str:
    o, today = r["open"], r["today"]
    parts = [f'<section><h2>3 · Open work and flow (today, {today:%b %d})</h2>',
             _kv([("open tickets", _n(o["total"])), ("past SLA", _n(o["breached"])),
                  ("forecast to miss SLA", _n(o["likely_late"])), ("blocked / on hold", f'{o["blocked"]} / {o["on_hold"]}'),
                  ("open more than 90 days", _n(o["old"])), ("no SLA clock yet", _n(o["no_clock"]))]),
             fig(o["stage_fig"], "Open tickets by stage and SLA risk.")]
    val = r["validating"] or {}
    vk = val.get("kpis") or {}
    if vk:
        parts.append("<h3>Validating (waiting for the requester to confirm)</h3>" + _kv([
            ("in Validating now", _n(vk.get("in_validating"))),
            ("median wait now (bd)", _n(vk.get("waiting_median"), 1)),
            (f"waiting {vk.get('nudge_bd', 5)}+ business days", _n(vk.get("nudge"))),
            ("sent back to be reworked", _p(vk.get("rework"))),
            ("validated the same day", _p(vk.get("same_day"))),
            ("closed by the requester", _p(vk.get("closed_by_requester")))]))
    k = (r["dependencies"] or {}).get("kpis") or {}
    if k:
        parts.append("<h3>Dependencies (Jira issue links)</h3>" + _kv([
            ("open PE tickets waiting on others", _n(k.get("waiting"))),
            ("blocked only by finished work", _n(k.get("can_move"))),
            ("waiting on other teams", _n(k.get("other_teams"))),
            ("other teams waiting on PE", _n(k.get("others_waiting_on_pe"))),
            ("longest chain (tickets)", _n(k.get("longest_chain"))),
            ("circular dependencies", _n(k.get("cycles")))]))
    att = r["attention_df"]
    rows = pd.DataFrame({"Ticket": [_ticket(k) for k in att["key"]], "Risk": att["risk"].values,
                         "Stage": att["stage"].values, "Priority": att["priority_bucket"].values,
                         "Business Lead": att["lead"].values, "Days Past SLA": att["days_past_sla"].astype(int).values,
                         "Summary": [_short(s, 55) for s in att["summary"]]})
    parts.append(f"<h3>Needs attention ({len(rows)} past or forecast to miss SLA)</h3>"
                 + _table(rows.head(TICKET_ROWS), {"Days Past SLA"}))
    return "".join(parts) + "</section>"


def _sec_requesters(r: dict) -> str:
    per = r["period"]
    parts = [f'<section><h2>4 · Requesters and business leads ({_e(per["label"])})</h2>']
    lead = r["leaders"]
    if lead:
        k = lead["kpis"]
        parts.append(_kv([("tickets requested", _n(k.get("requested"))),
                          ("from business leads", _n(k.get("requested_by_leads"))),
                          ("requesting leads", _n(k.get("requesting_leads"))),
                          ("internal PE work", _p(k.get("internal_share"))),
                          ("SLA met for leads", _p(k.get("sla_met_leads"))),
                          ("no business lead set", _p(k.get("unknown_share")))]))
        card = lead["scorecard_df"].head(10)
        cols = [c for c in ["Business Lead", "Requested", "Delivered", "Median Wait (bd)", "SLA Met %", "Open Now",
                            "Open Past SLA", "Top Friction in Comments"] if c in card.columns]
        parts.append("<h3>Business-lead scorecard (top 10 by requests)</h3>"
                     + _table(card[cols].fillna("—"), {"Requested", "Delivered", "Median Wait (bd)", "SLA Met %",
                                                      "Open Now", "Open Past SLA"}))
    conv = r["conversations"]
    if conv:
        parts.append("<h3>What slows tickets down (ticket comments)</h3>" + _kv([
            ("tickets with a conversation", _p(conv.get("coverage"))),
            ("closed with a closing note", _p(conv.get("closing_note"))),
            ("typical first reply (business h)", _n(conv.get("first_reply_hours"), 1))]))
        recs = conv.get("recommendations") or []
        if recs:
            rows = pd.DataFrame([{"Theme": x["theme"], "Tickets": x["tickets"],
                                  "Extra Days Each": f"{x['extra_per_ticket']:.1f}",
                                  "Suggested Change": x["action"]} for x in recs[:4]])
            parts.append(_table(rows, {"Tickets", "Extra Days Each"}))
    return "".join(parts) + "</section>"


def _sec_people(r: dict) -> str:
    cap, know = r["capacity"] or {}, r["knowledge"] or {}
    parts = ['<section><h2>5 · People and knowledge risk (team level)</h2>']
    if cap:
        parts.append(f'<p>{_n(cap.get("engineers"))} engineers delivered work in the last 12 weeks '
                     f'({_n(cap.get("core_team"))} regularly). The three busiest people delivered '
                     f'<strong>{_p(cap.get("top3_share"))}</strong> of it, so the team\'s output depends heavily on a few '
                     "people.</p>")
    ask = know.get("ask_stats") or {}
    if ask:
        parts.append(f'<p>Over the last {know.get("months", 6)} months, {_p(ask.get("self_share"))} of tickets were '
                     f'created by PE engineers themselves; the rest came from {_n(ask.get("requesters"))} requesters.</p>')
    single = know.get("single_person_df")
    if single is not None and len(single):
        rows = pd.DataFrame({"Area": single["Domain"], "Group": single["Group"], "Tickets": single["Tickets"],
                             "Share Done by One Person": single["Main Person Share %"].astype(str) + "%",
                             "That Person Left PE": single["Main Person Left PE"].replace("", "no"),
                             "Others With Some Experience": single["Others With Some Experience"]})
        parts.append("<h3>Areas that rest on one person</h3>"
                     "<p class=\"note\">Only one engineer has handled 3 or more tickets in these areas (last 6 months).</p>"
                     + _table(rows, {"Tickets", "Others With Some Experience"}))
    elif know:
        parts.append("<p>No technical area rests on a single person.</p>")
    return "".join(parts) + "</section>"


def _sec_quality(r: dict) -> str:
    size, now, o = r["size"] or {}, r["now"], r["open"]
    rows = [("Open tickets with an Estimated Size", _p(size.get("coverage_open")), f"target {_p(size.get('target', .9))}"),
            ("Estimates that matched the actual work", _p(size.get("accuracy")), "completed, last 12 months"),
            ("Open PE tickets without a Target start", _n(o["no_clock"]), "no SLA clock, cannot be judged"),
            ("New tickets without a Business Lead", _p(r["unknown_lead_share"]), r["period"]["label"])]
    if r["history"]:
        rows.append(("Completed tickets with the clock set after the work", _p(now["after_fact_share"]),
                     r["period"]["label"]))
    frame = pd.DataFrame(rows, columns=["Measure", "Value", "Context"])
    return ('<section><h2>6 · Data quality</h2><p>These numbers decide how far the rest of the report can be '
            'trusted.</p>' + _table(frame, {"Value"}) + "</section>")


def _sec_recommendations(r: dict) -> str:
    recs = r["recommendations"]
    if not recs:
        return '<section><h2>7 · Recommendations</h2><p>Nothing stands out this month.</p></section>'
    body = "".join(f'<div class="rec{" decision" if x["decision"] else ""}"><strong>{i}. {_e(x["title"])}</strong>'
                   + ('<span class="tag">Decision</span>' if x["decision"] else "")
                   + f'<br><span class="note">Why:</span> {_e(x["why"])}<br><span class="note">Ask:</span> {_e(x["ask"])}</div>'
                   for i, x in enumerate(recs, 1))
    return f'<section><h2>7 · Recommendations</h2>{body}</section>'


def _sec_appendix(r: dict) -> str:
    per = r["period"]
    defs = [
        ("Delivered", "Tickets moved to Done in the month (tickets only: no Features or Initiatives)."),
        ("SLA", "Business days allowed by Priority × Estimated Size, counted from the Target start; unsized tickets "
                "use Medium. PE tickets only; Release Management (CAR) tickets have no PE SLA."),
        ("SLA met", "Of PE tickets completed in the month, the share finished on or before their SLA due date. "
                    "Re-planned tickets are judged on their original Target start."),
        ("Clock set after the work", "The Target start was set or moved on or after the day the ticket moved to Done "
                                     "(from Jira's change history)."),
        ("Lead time", "Business days from creation to Done (weekends and US holidays excluded)."),
        ("Risk (open work)", "Breached = past its SLA due date; Likely Late = forecast (P85) to finish after it."),
        ("Business days (bd)", "Monday to Friday, excluding company holidays."),
    ]
    parts = ['<section class="appendix"><h2>Appendix</h2><h3>Definitions</h3>',
             _table(pd.DataFrame(defs, columns=["Term", "Meaning"]))]
    late = r["late_df"]
    if len(late) > TICKET_ROWS:
        rows = pd.DataFrame({"Ticket": [_ticket(k) for k in late["key"]], "Priority": late["priority_bucket"].values,
                             "Business Lead": late["lead"].values, "Summary": [_short(s, 70) for s in late["summary"]]})
        parts.append(f"<h3>All tickets completed late in {_e(per['label'])}</h3>" + _table(rows))
    att = r["attention_df"]
    if len(att) > TICKET_ROWS:
        rows = pd.DataFrame({"Ticket": [_ticket(k) for k in att["key"]], "Risk": att["risk"].values,
                             "Business Lead": att["lead"].values, "Summary": [_short(s, 70) for s in att["summary"]]})
        parts.append("<h3>All open tickets past or forecast to miss SLA</h3>" + _table(rows))
    if r["notes"]:
        parts.append("<h3>Notes</h3>" + "".join(f'<p class="note">{_e(n)}</p>' for n in r["notes"]))
    parts.append(f'<p class="note">Generated {datetime.now():%b %d, %Y %H:%M} by the Platform Engineering dashboard. '
                 "Internal: contains ticket and business-lead names.</p>")
    return "".join(parts) + "</section>"


def render_html(r: dict) -> str:
    if r.get("error_message"):
        raise ValueError(r["error_message"])
    fig = _Figures()
    body = "".join([_sec_summary(r), _sec_delivery(r, fig), _sec_sla(r, fig), _sec_open(r, fig), _sec_requesters(r),
                    _sec_people(r), _sec_quality(r), _sec_recommendations(r), _sec_appendix(r)])
    title = f"PE Executive Report · {r['period']['label']}"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(title)}</title>'
            f"<style>{CSS}</style></head><body><main>{body}</main></body></html>")


def filename(r: dict, ext: str = "html") -> str:
    return f"PE_Executive_Report_{r['period']['month']}.{ext}"


# ── PDF ─────────────────────────────────────────────────────────────────────────

CHROME_PATHS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium")


def chrome_path() -> str | None:
    found = next((c for c in CHROME_PATHS if Path(c).exists()), None)
    return found or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")


def html_to_pdf(html_text: str) -> bytes | None:
    """Print the report with local headless Chrome; None when Chrome is not installed or printing fails."""
    chrome = chrome_path()
    if chrome is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "report.html", Path(tmp) / "report.pdf"
        src.write_text(html_text, encoding="utf-8")
        try:
            subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            "--virtual-time-budget=10000", f"--print-to-pdf={out}", src.as_uri()],
                           check=True, capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            return None
        return out.read_bytes() if out.exists() else None
