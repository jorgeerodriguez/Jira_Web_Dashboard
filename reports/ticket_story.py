"""Status of My Ticket: the whole status and history of one ticket, in one place.

Scope: PE tickets, plus Release Management (CAR) tickets that are linked to PE work. Everything reuses the rest of
the dashboard, so the numbers agree with the other pages:
- facts, SLA, stages and the business calendar (executive_summary._facts, in_progress_report);
- the In Progress / Backlog forecasts for open tickets;
- the change history (status, Target dates, assignee) for the timeline, date history and time in each status;
- human comments for the conversation log (friction themes, @mentions, reply gaps in business hours);
- issue links for what the ticket waits on and holds up (dependency_graph);
- GitLab merge requests whose links were pasted in the description or comments (data/merge_request_links), dated
  when the link was posted (Jira's GitLab integration isn't connected, so open / merge times aren't known);
- forecasts posted to the ticket (jira_forecast tags) against what actually happened.

`build_ticket_story(df, history, key)` returns a payload of plain values, tables and figures; the page and the
printable summary are drawn from it.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from data.dashboard_comments import parse_forecast
from data.merge_request_links import merge_request_table
from reports import change_history as chg
from reports import domains
from reports import executive_summary as es
from reports import in_progress_report as ipr
from reports.word_of_the_month_report import THEMES, business_hours_between, clean_comment

JIRA = ipr.JIRA_BROWSE_BASE_URL
CAR_PROJECT = "release management"
# Rework = finished work sent back. "Reviewing" is intake review in this workflow, not validation.
REWORK_FROM = {"validating", "test and validate", "done"}
REWORK_TO = {"in progress", "to do", "triage", "tech discovery required"}
MIN_TYPICAL = 8
TYPICAL_DAYS = 365
_MENTION = re.compile(r"\[~accountid:([^\]]+)\]")
STAGE_COLORS = {"Backlog": "#8891a7", "In Progress": "#2a78d6", "Validating": "#1baf7a", "Blocked & On Hold": "#e34948",
                "Release": "#4a3aa7", "Closed": "#008300", "Other": "#b0b8c6"}
MR_COLOR = "#6b4fbb"
ROLE_COLORS = {"Requester": "#eb6834", "Assignee": "#2a78d6", "PE": "#1baf7a", "Other": "#8891a7", "Dashboard": "#b0b8c6"}
INK = ipr.INK


def _stage(status: str) -> str:
    s = str(status).strip().casefold()
    if s in es.CLOSED_STATUSES:
        return "Closed"
    return es._stage(status)


def _local(ts) -> pd.Timestamp:
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    return t.tz_convert(ipr.LOCAL_TZ).tz_localize(None) if pd.notna(t) else pd.NaT


def _bd(a, b, hol) -> int | None:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    return int(np.busday_count(pd.Timestamp(a).date(), pd.Timestamp(b).date(), holidays=hol))


def _days(n) -> str:
    return "1 business day" if n == 1 else f"{n if n is not None else '?'} business days"


def _fmt(d) -> str:
    return pd.Timestamp(d).strftime("%b %d, %Y") if d is not None and pd.notna(d) else "—"


# ── Which tickets can be looked up ──────────────────────────────────────────────

def searchable(df_issues: pd.DataFrame) -> pd.DataFrame:
    """PE tickets, plus CAR tickets linked to PE work (either side of the link). Columns: key, label."""
    if df_issues is None or df_issues.empty:
        return pd.DataFrame(columns=["key", "label"])
    today = ipr.today_local()
    facts = es._facts(df_issues, today, ipr._calendar_holidays(today))
    pe = set(facts.loc[facts["sla_applies"], "key"])
    is_car = df_issues["project_name"].astype(str).str.strip().str.casefold().eq(CAR_PROJECT)
    linked = set()
    if "links" in df_issues.columns:
        for key, links, car in zip(df_issues["key"], df_issues["links"], is_car):
            for link in links if isinstance(links, list) else []:
                other = str(link.get("key") or "")
                if key in pe and other.startswith("CAR-"):
                    linked.add(other)
                if car and other in pe:
                    linked.add(key)
    keep = df_issues[df_issues["key"].isin(pe | linked)].copy()
    keep["_n"] = keep["key"].str.extract(r"-(\d+)$")[0].astype(float)
    keep = keep.sort_values(["_n"], ascending=False)
    labels = keep["key"] + " · " + keep["summary"].fillna("").astype(str).str[:90]
    return pd.DataFrame({"key": keep["key"].values, "label": labels.values})


# ── History spans ───────────────────────────────────────────────────────────────

def _spans(history: pd.DataFrame, key: str, field: str, created: pd.Timestamp, current, end: pd.Timestamp) -> list[dict]:
    """[{value, start, end}] for a field over the ticket's life, from creation (local time) to `end`."""
    if not chg.has_history(history):
        return [{"value": current, "start": created, "end": end}]
    h = history[(history["key"] == key) & (history["field"] == field)].sort_values("at")
    if h.empty:
        return [{"value": current, "start": created, "end": end}]
    first = h.iloc[0]["frm"]
    spans, value, start = [], first if first not in (None, "") else ("Unassigned" if field == "assignee" else current), created
    for _, row in h.iterrows():
        at = _local(row["at"])
        spans.append({"value": value, "start": start, "end": at})
        value = row["to"] if row["to"] not in (None, "") else ("Unassigned" if field == "assignee" else row["to"])
        start = at
    spans.append({"value": value, "start": start, "end": end})
    return [s for s in spans if s["end"] >= s["start"]]


def status_time(spans: list[dict], hol) -> pd.DataFrame:
    rows = {}
    for s in spans:
        rows[s["value"]] = rows.get(s["value"], 0) + (_bd(s["start"], s["end"], hol) or 0)
    return pd.DataFrame({"status": list(rows), "bd": list(rows.values())})


def typical_status_time(df_issues: pd.DataFrame, history: pd.DataFrame, facts: pd.DataFrame, row: pd.Series, hol) -> tuple[pd.DataFrame, str]:
    """Median business days per status for similar completed tickets (same priority x size, falling back)."""
    if not chg.has_history(history):
        return pd.DataFrame(columns=["status", "typical_bd", "share"]), ""
    today = ipr.today_local()
    done = facts[facts["sla_applies"] & facts["outcome"].eq("Done")
                 & (facts["closed_day"] >= today - pd.Timedelta(days=TYPICAL_DAYS))]
    for label, ref in ((f"{row['priority_bucket']} × {row['size']}",
                        done[(done["priority_bucket"] == row["priority_bucket"]) & (done["size"] == row["size"])]),
                       (f"{row['priority_bucket']} (any size)", done[done["priority_bucket"] == row["priority_bucket"]]),
                       ("all PE tickets", done)):
        if len(ref) >= MIN_TYPICAL:
            break
    keys = set(ref["key"])
    hs = history[history["key"].isin(keys) & history["field"].eq("status")]
    per = []
    for k, r in ref.set_index("key").iterrows():
        created = _local(df_issues.loc[df_issues["key"] == k, "created"].iloc[0]) if k in set(df_issues["key"]) else pd.NaT
        if pd.isna(created):
            continue
        spans = _spans(hs, k, "status", created, r["status"], pd.Timestamp(r["closed_day"]))
        t = status_time([s for s in spans if _stage(s["value"]) != "Closed"], hol)
        t["key"] = k
        per.append(t)
    if not per:
        return pd.DataFrame(columns=["status", "typical_bd", "share"]), label
    allt = pd.concat(per)
    n = allt["key"].nunique()
    g = allt.groupby("status").agg(typical_bd=("bd", "median"), tickets=("key", "nunique"))
    g["share"] = g["tickets"] / n
    return g.reset_index()[["status", "typical_bd", "share"]], f"{label}, {n} completed tickets"


# ── Conversation ────────────────────────────────────────────────────────────────

def conversation(row: pd.Series, assignees: set[str], pe_people: set[str], names: dict[str, str]) -> pd.DataFrame:
    human = row.get("comments") if isinstance(row.get("comments"), list) else []
    bot = row.get("dashboard_comments") if isinstance(row.get("dashboard_comments"), list) else []
    items = [(c, False) for c in human] + [(c, True) for c in bot]
    items = sorted((x for x in items if pd.notna(x[0].get("created"))), key=lambda x: x[0]["created"])
    requester = row.get("reporter_name")
    rows, last_other = [], None
    for c, dashboard in items:
        author = c.get("author", "Unknown")
        role = ("Dashboard" if dashboard else "Requester" if author == requester else
                "Assignee" if author in assignees else "PE" if author in pe_people else "Other")
        when = _local(c["created"])
        gap = None
        if not dashboard and last_other is not None and last_other[0] != author:
            gap = round(business_hours_between(last_other[1].to_pydatetime(), when.to_pydatetime()), 1)
        body = c.get("body") or ""
        text = " ".join(clean_comment(body).split())
        themes = [t for t, rx in THEMES.items() if re.search(rx, text, flags=re.I)] if not dashboard else []
        mentioned = sorted({names[a] for a in _MENTION.findall(body) if a in names})
        rows.append({"When": when, "Author": author, "Role": role, "Reply After (business h)": gap,
                     "Themes": ", ".join(themes), "Mentions": ", ".join(mentioned),
                     "Comment": text[:400] + ("…" if len(text) > 400 else "")})
        if not dashboard:
            last_other = (author, when)
    return pd.DataFrame(rows, columns=["When", "Author", "Role", "Reply After (business h)", "Themes", "Mentions", "Comment"])


# ── Timeline figure ─────────────────────────────────────────────────────────────

def timeline_figure(status_spans, assignee_spans, markers: list[dict], comments: pd.DataFrame,
                    forecast: dict | None, today: pd.Timestamp, mrs: pd.DataFrame | None = None) -> go.Figure:
    has_mrs = mrs is not None and not mrs.empty
    lanes = ["Status", "Assignee", "Dates", "Comments"] + (["Merge requests"] if has_mrs else []) + ["Forecast"]
    fig = go.Figure()
    ms = lambda a, b: (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() * 1000  # noqa: E731
    shown = set()
    for s in status_spans:
        stage = _stage(s["value"])
        fig.add_trace(go.Bar(
            y=["Status"], x=[max(ms(s["start"], s["end"]), 3.6e6)], base=[s["start"]], orientation="h",
            marker=dict(color=STAGE_COLORS.get(stage, "#b0b8c6"), line=dict(color="white", width=1.5)),
            name=stage, legendgroup=stage, showlegend=stage not in shown, text=[s["value"]], textposition="inside",
            insidetextanchor="middle", textfont=dict(color="white", size=10),
            hovertemplate=f"<b>{html_lib.escape(str(s['value']))}</b><br>{_fmt(s['start'])} → {_fmt(s['end'])}<extra></extra>"))
        shown.add(stage)
    palette = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]
    people = {}
    for s in assignee_spans:
        color = "#d5dbe5" if s["value"] == "Unassigned" else palette[people.setdefault(s["value"], len(people)) % len(palette)]
        fig.add_trace(go.Bar(
            y=["Assignee"], x=[max(ms(s["start"], s["end"]), 3.6e6)], base=[s["start"]], orientation="h", showlegend=False,
            marker=dict(color=color, opacity=0.75, line=dict(color="white", width=1.5)), text=[s["value"]],
            textposition="inside", insidetextanchor="middle", textfont=dict(size=10),
            hovertemplate=f"<b>{html_lib.escape(str(s['value']))}</b><br>{_fmt(s['start'])} → {_fmt(s['end'])}<extra></extra>"))
    if forecast:
        a, b = forecast.get("from"), forecast.get("to")
        if a is not None and b is not None and pd.notna(a) and pd.notna(b):
            fig.add_trace(go.Bar(y=["Forecast"], x=[max(ms(a, b), 3.6e6)], base=[a], orientation="h", showlegend=False,
                                 marker=dict(color="rgba(42,120,214,0.25)", line=dict(color="#2a78d6", width=1)),
                                 hovertemplate=f"{forecast['label']}: {_fmt(a)} → {_fmt(b)}<extra></extra>"))
        for m in forecast.get("points", []):
            if m["at"] is not None and pd.notna(m["at"]):
                fig.add_trace(go.Scatter(x=[m["at"]], y=["Forecast"], mode="markers+text", text=[m["label"]],
                                         textposition="top center", textfont=dict(size=9), showlegend=False,
                                         marker=dict(size=10, symbol=m.get("symbol", "circle"), color="#2a78d6"),
                                         hovertemplate=f"{m['label']}: {_fmt(m['at'])}<extra></extra>"))
    for m in markers:
        if m["at"] is None or pd.isna(m["at"]):
            continue
        fig.add_trace(go.Scatter(x=[m["at"]], y=["Dates"], mode="markers+text", text=[m["short"]], textposition="top center",
                                 textfont=dict(size=9, color=INK), showlegend=False,
                                 marker=dict(size=11, symbol=m["symbol"], color=m["color"], line=dict(width=1.5, color=m["color"])),
                                 hovertemplate=f"{html_lib.escape(m['label'])}: {_fmt(m['at'])}<extra></extra>"))
    if not comments.empty:
        for role, g in comments.groupby("Role"):
            fig.add_trace(go.Scatter(
                x=g["When"], y=["Comments"] * len(g), mode="markers", name=f"Comment: {role}",
                marker=dict(size=10, color=ROLE_COLORS.get(role, "#8891a7"), line=dict(color="white", width=1)),
                customdata=np.stack([g["Author"], g["Comment"].str[:140]], axis=1),
                hovertemplate="<b>%{customdata[0]}</b> · %{x|%b %d %H:%M}<br>%{customdata[1]}<extra></extra>"))
    if has_mrs:
        # Links posted together (one comment) share a marker, so their labels don't pile up.
        groups = [(when, g) for when, g in mrs.groupby("When", sort=True)]
        labels = [g.iloc[0]["MR #"] if len(g) == 1 else f"{len(g)} MRs" for _, g in groups]
        hover = ["<br>".join(html_lib.escape(f"{m['Project']} {m['MR #']}") for _, m in g.iterrows())
                 + f"<br>Link posted %{{x|%b %d %H:%M}} by {html_lib.escape(str(g.iloc[0]['By']))} "
                   f"({html_lib.escape(str(g.iloc[0]['Where']))})<extra></extra>" for _, g in groups]
        for (when, _), label, tip in zip(groups, labels, hover):
            fig.add_trace(go.Scatter(
                x=[when], y=["Merge requests"], mode="markers+text", name="Merge request (link posted)",
                legendgroup="mr", showlegend=label == labels[0] and when == groups[0][0],
                text=[label], textposition="top center", textfont=dict(size=9, color=INK),
                marker=dict(size=11, symbol="square", color=MR_COLOR, line=dict(color="white", width=1)),
                hovertemplate=tip))
    fig.add_vline(x=today, line_dash="dash", line_color=INK, line_width=1)
    fig.add_annotation(x=today, y=1.0, yref="paper", text="Today", showarrow=False, xanchor="left", yanchor="bottom",
                       font=dict(color=INK, size=10))
    fig.update_layout(barmode="overlay", height=380, margin=dict(l=10, r=10, t=30, b=10), bargap=0.35,
                      legend=dict(orientation="h", y=-0.15, x=0, title_text=""), plot_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(categoryorder="array", categoryarray=lanes[::-1], title=None)
    fig.update_xaxes(type="date", gridcolor="rgba(148,163,184,0.25)")
    return fig


def time_figure(time_df: pd.DataFrame) -> go.Figure | None:
    if time_df is None or time_df.empty:
        return None
    t = time_df.iloc[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=t["status"], x=t["bd"], orientation="h", name="This ticket",
                         marker=dict(color=[STAGE_COLORS.get(s, "#8891a7") for s in t["stage"]]),
                         text=[f"{v:.0f}" for v in t["bd"]], textposition="outside",
                         hovertemplate="%{y}: %{x} business days<extra>This ticket</extra>"))
    if "typical_bd" in t and t["typical_bd"].notna().any():
        fig.add_trace(go.Scatter(y=t["status"], x=t["typical_bd"], mode="markers", name="Typical (median)",
                                 marker=dict(symbol="line-ns-open", size=18, color=INK, line=dict(width=3, color=INK)),
                                 hovertemplate="%{y}: typical %{x:.0f} business days<extra></extra>"))
    fig.update_layout(height=max(200, 34 * len(t) + 90), margin=dict(l=10, r=30, t=10, b=10),
                      legend=dict(orientation="h", y=1.08, x=0, title_text=""), plot_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(title="Business days", gridcolor="rgba(148,163,184,0.25)", rangemode="tozero")
    return fig


# ── Verdict ─────────────────────────────────────────────────────────────────────

def _verdict(r: pd.Series, k: dict, forecast: dict | None, blockers: list[tuple[str, str, bool]], hol) -> list[tuple[str, str]]:
    lines = []
    if r["is_closed"]:
        tone = "good" if r["outcome"] == "Done" else "info"
        lines.append((tone, f"**{r['outcome']}** on {_fmt(r['closed_day'])}, {_days(k['age_bd'])} after it was requested."))
        if r["sla_applies"] and pd.notna(r["sla_due"]) and r["outcome"] == "Done":
            late = _bd(r["sla_due"], r["closed_day"], hol)
            lines.append(("good" if not late or late <= 0 else "bad",
                          "SLA **met**." if not late or late <= 0 else f"SLA **missed** by {_days(late)}."))
            if k.get("clock_after_fact"):
                lines.append(("warn", "Its SLA clock (Target start) was set on or after the day the work was done, so "
                                      "the SLA result isn't a real measurement."))
        return lines
    since = k.get("in_status_bd")
    lines.append(("info", f"**{r['status']}** for {_days(since)}; open {_days(k['age_bd'])} in total."))
    if forecast and forecast.get("sentence"):
        lines.append((forecast.get("tone", "info"), forecast["sentence"]))
    if not r["sla_applies"]:
        lines.append(("info", "Release Management ticket: no PE SLA applies."))
    elif pd.isna(r["sla_due"]):
        lines.append(("warn", "No Target start, so the SLA clock hasn't started. Set a Target start in Jira."))
    else:
        left = k.get("sla_left_bd")
        lines.append(("bad" if left < 0 else "warn" if left <= 3 else "good",
                      f"SLA due **{_fmt(r['sla_due'])}** "
                      + (f"(overdue by {_days(-left)})." if left < 0 else f"({_days(left)} left).")))
    open_b = [b for b in blockers if b[2]]
    if open_b:
        lines.append(("warn", "Waiting on " + ", ".join(f"**{b[0]}** ({b[1]})" for b in open_b) + "."))
    elif blockers:
        lines.append(("warn", "Marked as blocked only by finished work (" + ", ".join(b[0] for b in blockers)
                      + "): check the link and unblock it."))
    if str(r["status"]).casefold() == "validating":
        lines.append(("info", "Finished and waiting for the requester to confirm."))
    if r["assignee_name"] == "Unassigned":
        lines.append(("warn", "No assignee yet: see Suggested Assignments."))
    if k.get("date_moves"):
        lines.append(("info", f"Target dates moved {k['date_moves']} times"
                              + (f", {k['late_moves']} after the date had passed." if k.get("late_moves") else ".")))
    if k.get("rework"):
        lines.append(("warn", f"Sent back to be worked on {k['rework']} time(s) after validation."))
    return lines


# ── Entry point ─────────────────────────────────────────────────────────────────

def build_ticket_story(df_issues: pd.DataFrame, history: pd.DataFrame | None, key: str,
                       forecasts: dict | None = None, graph=None, names: dict | None = None) -> dict:
    """`forecasts` = {"in_progress": forecast_df, "backlog": planning_df} (optional, computed by the caller once);
    `graph` = dependency graph (optional); `names` = account id -> display name for @mentions."""
    if df_issues is None or df_issues.empty or key not in set(df_issues["key"]):
        return {"error_message": f"Ticket {key} isn't in the fetched data."}
    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    raw = df_issues[df_issues["key"] == key].iloc[0]
    if key not in set(facts["key"]):
        return {"error_message": f"{key} is outside the dashboard's scope (Features, Initiatives and some excluded "
                                 "assignees aren't covered)."}
    r = facts[facts["key"] == key].iloc[0]
    created = _local(raw["created"])
    end = pd.Timestamp(r["closed_day"]) + pd.Timedelta(hours=12) if r["is_closed"] else pd.Timestamp.now(tz=ipr.LOCAL_TZ).tz_localize(None)

    status_spans = _spans(history, key, "status", created, r["status"], end)
    assignee_spans = _spans(history, key, "assignee", created, r["assignee_name"], end)
    assignees = {s["value"] for s in assignee_spans if isinstance(s["value"], str) and s["value"] not in ("Unassigned", "None")}
    pe_people = set(facts.loc[facts["sla_applies"] & facts["assignee_name"].ne("Unassigned"), "assignee_name"])

    # ── Date changes ──
    changes = chg.date_changes(history, hol) if chg.has_history(history) else pd.DataFrame()
    mine = changes[changes["key"] == key] if not changes.empty else changes
    summary = chg.ticket_date_summary(changes if not changes.empty else pd.DataFrame(), facts[facts["key"] == key])
    k_dates = summary.loc[key] if key in summary.index else None
    date_df = pd.DataFrame()
    if not mine.empty:
        hist_author = history[(history["key"] == key) & history["field"].isin(chg.DATE_FIELDS)].sort_values("at")
        mine = mine.assign(author=hist_author["author"].values[:len(mine)] if len(hist_author) == len(mine) else mine["author"])
        date_df = pd.DataFrame({
            "When": mine["day"].dt.date, "Field": mine["field"].map(chg.FIELD_LABELS), "Change": mine["kind"].str.title(),
            "From": mine["old"].dt.date, "To": mine["new"].dt.date,
            "Shift (bd)": mine["shift_bd"].astype("Int64"),
            "After Date Passed": np.where(mine["after_passed"], "yes", ""), "By": mine["author"],
        }).sort_values("When").reset_index(drop=True)

    # ── Comments ──
    comments = conversation(raw, assignees, pe_people, names or {})
    human = comments[comments["Role"] != "Dashboard"]
    reply = None
    if not human.empty:
        other = human[human["Author"] != raw.get("reporter_name")]
        if not other.empty:
            reply = round(business_hours_between(created.to_pydatetime(), other.iloc[0]["When"].to_pydatetime()), 1)

    # ── Merge requests (links pasted on the ticket) ──
    mr_df = merge_request_table(raw.get("merge_requests"))
    if not mr_df.empty:
        mr_df["When"] = mr_df["First Mentioned"].map(_local)

    # ── Rework and time in status ──
    seq = [str(s["value"]).casefold() for s in status_spans]
    rework = sum(1 for a, b in zip(seq, seq[1:]) if a in REWORK_FROM and b in REWORK_TO)
    time_df = status_time(status_spans, hol)
    typical, basis = typical_status_time(df_issues, history, facts, r, hol)
    if not time_df.empty:
        time_df = time_df.merge(typical, on="status", how="left")
        time_df["stage"] = time_df["status"].map(_stage)
        time_df = time_df[time_df["stage"] != "Closed"].sort_values("bd", ascending=False).reset_index(drop=True)

    # ── Forecast (open tickets) ──
    forecast = None
    forecasts = forecasts or {}
    stage = _stage(r["status"])
    if not r["is_closed"] and stage == "In Progress":
        fdf = forecasts.get("in_progress", pd.DataFrame())
        hit = fdf[fdf["Ticket"].astype(str).str.endswith("/" + key)] if not fdf.empty else fdf
        if not hit.empty:
            p50, p85 = pd.Timestamp(hit.iloc[0]["Forecast P50"]), pd.Timestamp(hit.iloc[0]["Forecast P85"])
            risk = hit.iloc[0].get("SLA Status", "")
            forecast = {"label": "Finish (likely → safe)", "from": p50, "to": p85,
                        "points": [{"at": p50, "label": "likely"}, {"at": p85, "label": "safe", "symbol": "diamond"}],
                        "sentence": f"Likely done **{_fmt(p50)}**; safe to promise **{_fmt(p85)}**"
                                    + (f" · {risk}." if risk else "."),
                        "tone": "bad" if "Breached" in str(risk) or "Likely" in str(risk) else "warn" if "Risk" in str(risk) else "good"}
    elif not r["is_closed"] and stage == "Backlog":
        pdf = forecasts.get("backlog", pd.DataFrame())
        hit = pdf[pdf["key"] == key] if not pdf.empty else pdf
        if not hit.empty and pd.notna(hit.iloc[0].get("start_p50")):
            h = hit.iloc[0]
            forecast = {"label": "Start (likely) → finish (safe)", "from": h["start_p50"], "to": h["finish_p85"],
                        "points": [{"at": h["start_p50"], "label": "likely start"},
                                   {"at": h.get("start_safe"), "label": "safe start", "symbol": "triangle-right"},
                                   {"at": h["finish_p85"], "label": "safe finish", "symbol": "diamond"}],
                        "sentence": f"Queue #{int(h['queue_position']) if pd.notna(h.get('queue_position')) else '?'} for "
                                    f"{r['assignee_name']}: likely starts **{_fmt(h['start_p50'])}** (safe "
                                    f"{_fmt(h.get('start_safe'))}), safe finish **{_fmt(h['finish_p85'])}**.",
                        "tone": "info"}

    # ── Dependencies and links ──
    blockers, deps_fig, link_rows = [], None, []
    if graph is not None and key in graph:
        blockers = [(b, graph.nodes[b]["status"], graph.nodes[b]["open"]) for b in graph.predecessors(key)]
        from reports import dependency_graph as dg
        deps_fig = dg.figure(graph, focus=key) if graph.degree(key) else None
    for link in raw.get("links") if isinstance(raw.get("links"), list) else []:
        link_rows.append({"Relation": link.get("relation"), "Ticket": JIRA + str(link.get("key")),
                          "Status": link.get("status"), "Summary": str(link.get("summary") or "")[:90]})
    if pd.notna(raw.get("parent")) and raw.get("parent"):
        link_rows.insert(0, {"Relation": "child of (Epic / Feature)", "Ticket": JIRA + str(raw.get("parent")),
                             "Status": "", "Summary": ""})
    links_df = pd.DataFrame(link_rows, columns=["Relation", "Ticket", "Status", "Summary"])

    # ── Posted forecasts vs reality ──
    posted = []
    actual_start = next((s["start"] for s in status_spans if _stage(s["value"]) not in ("Backlog",)), None)
    for c in raw.get("dashboard_comments") if isinstance(raw.get("dashboard_comments"), list) else []:
        f = parse_forecast(c.get("body"))
        if f:
            posted.append({"Posted": f.get("as_of"), "Likely Start": f.get("start_p50"), "Safe Start": f.get("safe_start"),
                           "Actual Start": actual_start.date() if actual_start is not None and stage != "Backlog" else None,
                           "Safe Finish (P85)": f.get("finish_p85"),
                           "Actual Finish": r["closed_day"].date() if r["is_closed"] and pd.notna(r["closed_day"]) else None})
    posted_df = pd.DataFrame(posted)

    # ── People ──
    people = {}
    def add(name, role):
        if isinstance(name, str) and name.strip() and name not in ("Unassigned", "Unknown", "None"):
            people.setdefault(name, set()).add(role)
    add(raw.get("reporter_name"), "requester")
    for a in assignees:
        add(a, "assignee")
    for _, c in human.iterrows():
        add(c["Author"], "commented")
    for m in human["Mentions"]:
        for n in [x.strip() for x in str(m).split(",") if x.strip()]:
            add(n, "mentioned")
    counts = human["Author"].value_counts()
    people_df = pd.DataFrame([{"Person": p, "Role": ", ".join(sorted(rs)), "Comments": int(counts.get(p, 0))}
                              for p, rs in people.items()]).sort_values("Comments", ascending=False) if people else pd.DataFrame()

    # ── Similar tickets ──
    tags = domains.tag(domains.ticket_text(raw))
    domain = domains.primary(tags)
    similar = {}
    done = facts[facts["sla_applies"] & facts["outcome"].eq("Done") & facts["key"].ne(key)
                 & (facts["closed_day"] >= today - pd.Timedelta(days=TYPICAL_DAYS))]
    pool = done[done["priority_bucket"] == r["priority_bucket"]]
    if domain:
        dkeys = {k2 for k2, s2 in zip(df_issues["key"], df_issues["summary"].fillna("")) if domain in domains.tag(s2)}
        same = pool[pool["key"].isin(dkeys)]
        if len(same) >= 5:
            pool, similar["basis"] = same, f"{domain} · {r['priority_bucket']} priority"
    similar.setdefault("basis", f"{r['priority_bucket']} priority")
    if len(pool):
        lead = pool.apply(lambda x: _bd(x["created_day"], x["closed_day"], hol), axis=1)
        met = (pool["closed_day"] <= pool["sla_due"]).where(pool["sla_due"].notna())
        similar.update({"n": int(len(pool)), "lead_p50": float(lead.median()), "lead_p85": float(lead.quantile(0.85)),
                        "sla_met": float(met.mean()) if met.notna().any() else None})
        recent = pool.sort_values("closed_day", ascending=False).head(5)
        similar["recent_df"] = pd.DataFrame({"Ticket": JIRA + recent["key"], "Completed": recent["closed_day"].dt.date,
                                             "Lead (bd)": lead.loc[recent.index].astype("Int64").values,
                                             "Summary": recent["summary"].fillna("").astype(str).str[:80]})

    # ── KPIs and verdict ──
    age_end = r["closed_day"] if r["is_closed"] else today
    in_status = _bd(status_spans[-1]["start"], today, hol) if status_spans and not r["is_closed"] else None
    started = r["start_day"] if pd.notna(r["start_day"]) and r["start_day"] <= age_end else None
    kpis = {
        "age_bd": _bd(r["created_day"], age_end, hol), "in_status_bd": in_status,
        "waiting_bd": _bd(r["created_day"], started, hol) if started is not None else None,
        "working_bd": _bd(started, age_end, hol) if started is not None else None,
        "sla_bd": int(r["sla_bd"]) if r["sla_applies"] else None,
        "sla_used": (_bd(r["start_day"], age_end, hol) / r["sla_bd"])
        if r["sla_applies"] and pd.notna(r["start_day"]) and r["start_day"] <= age_end else None,
        "sla_left_bd": (_bd(today, r["sla_due"], hol) if r["sla_due"] >= today else -_bd(r["sla_due"], today, hol))
        if pd.notna(r["sla_due"]) and not r["is_closed"] else None,
        "date_moves": int(k_dates["moves"]) if k_dates is not None else 0,
        "late_moves": int(k_dates["late_moves"]) if k_dates is not None else 0,
        "clock_after_fact": bool(k_dates["clock_after_fact"]) if k_dates is not None else False,
        "rework": rework, "comments": int(len(human)), "people": len(people), "first_reply_h": reply,
        "status_changes": max(len(status_spans) - 1, 0), "mrs": int(len(mr_df)),
    }

    markers = [
        {"at": created, "label": "Created", "short": "created", "symbol": "circle", "color": INK},
        {"at": r["start_day"], "label": "Target start", "short": "start", "symbol": "triangle-right", "color": "#2a78d6"},
        {"at": r["target_end_day"], "label": "Target end", "short": "target end", "symbol": "x", "color": "#eb6834"},
        {"at": r["sla_due"], "label": "SLA due", "short": "SLA due", "symbol": "diamond", "color": "#e34948"},
        {"at": r["closed_day"] if r["is_closed"] else None, "label": r.get("outcome") or "Closed", "short": "done",
         "symbol": "star", "color": "#008300"},
    ]
    if not mine.empty:
        for _, c in mine[mine["kind"].eq("moved") & mine["field"].eq(chg.START)].iterrows():
            markers.append({"at": c["old"], "label": f"Earlier Target start (moved {c['day']:%b %d})", "short": "",
                            "symbol": "triangle-right-open", "color": "#8891a7"})
    fig = timeline_figure(status_spans, assignee_spans, markers, comments, forecast,
                          pd.Timestamp.now(tz=ipr.LOCAL_TZ).tz_localize(None), mr_df)

    header = {"key": key, "url": JIRA + key, "summary": raw.get("summary") or "", "status": r["status"], "stage": stage,
              "priority": r["priority_bucket"], "size": r["size"], "assignee": r["assignee_name"],
              "requester": raw.get("reporter_name") or "Unknown", "lead": r["lead"], "issuetype": r["issuetype"],
              "project": raw.get("project_name"), "domains": tags, "created": created,
              "outcome": r["outcome"] if r["is_closed"] else None, "sla_applies": bool(r["sla_applies"]),
              "sla_due": r["sla_due"], "target_start": r["start_day"], "target_end": r["target_end_day"]}
    return {"error_message": None, "header": header, "kpis": kpis,
            "verdict": _verdict(r, kpis, forecast, blockers, hol), "timeline_fig": fig, "time_df": time_df,
            "typical_basis": basis, "date_df": date_df, "conversation_df": comments, "deps_fig": deps_fig,
            "links_df": links_df, "mr_df": mr_df, "posted_df": posted_df, "people_df": people_df, "similar": similar,
            "history": chg.has_history(history)}


# ── Printable summary ───────────────────────────────────────────────────────────

def summary_html(story: dict) -> str:
    """A self-contained one-page HTML summary (print to PDF from the browser)."""
    h, k = story["header"], story["kpis"]
    md = lambda t: re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html_lib.escape(t))  # noqa: E731
    verdict = "".join(f"<li>{md(t)}</li>" for _, t in story["verdict"])
    def row(label, value):
        return f"<tr><td>{label}</td><td><b>{html_lib.escape(str(value))}</b></td></tr>"
    facts = "".join([
        row("Status", h["status"]), row("Priority / size", f"{h['priority']} / {h['size']}"),
        row("Assignee", h["assignee"]), row("Requester", h["requester"]), row("Business lead", h["lead"]),
        row("Created", _fmt(h["created"])), row("Target start / end", f"{_fmt(h['target_start'])} / {_fmt(h['target_end'])}"),
        row("SLA due", _fmt(h["sla_due"]) if h["sla_applies"] else "no PE SLA"),
        row("Age (business days)", k["age_bd"]), row("Comments / people", f"{k['comments']} / {k['people']}"),
        row("Target date moves", k["date_moves"]), row("Rework", k["rework"]),
    ])
    conv = story["conversation_df"]
    recent = conv[conv["Role"] != "Dashboard"].tail(6)
    comments = "".join(f"<li><b>{html_lib.escape(str(c['Author']))}</b> · {c['When']:%b %d}: "
                       f"{html_lib.escape(str(c['Comment'])[:220])}</li>" for _, c in recent.iterrows())
    dates = story["date_df"]
    date_rows = "".join(f"<tr><td>{d['When']}</td><td>{d['Field']}</td><td>{d['Change']}</td><td>{d['From'] or '—'} → "
                        f"{d['To'] or '—'}</td></tr>" for _, d in dates.tail(8).iterrows()) if not dates.empty else ""
    mrs = story.get("mr_df", pd.DataFrame())
    mr_rows = "".join(f"<tr><td><a href=\"{html_lib.escape(m['MR'])}\">{html_lib.escape(m['Project'])} {m['MR #']}</a></td>"
                      f"<td>link posted {m['When']:%b %d} by {html_lib.escape(str(m['By']))}</td></tr>"
                      for _, m in mrs.head(10).iterrows()) if not mrs.empty else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{h['key']} status</title>
<style>body{{font-family:Inter,Helvetica,Arial,sans-serif;color:#1f2937;max-width:7.2in;margin:0.5in auto;font-size:10pt}}
h1{{font-size:16pt;margin:0}} h2{{font-size:11pt;border-bottom:1.5px solid #2a78d6;padding-bottom:3px;margin-top:16px}}
table{{border-collapse:collapse;width:100%}} td{{border-bottom:1px solid #e2e8f0;padding:3px 6px;vertical-align:top}}
.muted{{color:#64748b;font-size:8.5pt}}</style></head><body>
<p class="muted">Status of My Ticket · PE Delivery Dashboard · as of {pd.Timestamp.now():%b %d, %Y}</p>
<h1>{h['key']}: {html_lib.escape(h['summary'])}</h1><p><a href="{h['url']}">{h['url']}</a></p>
<h2>Where it stands</h2><ul>{verdict}</ul>
<h2>Key facts</h2><table>{facts}</table>
{'<h2>Target date changes</h2><table>' + date_rows + '</table>' if date_rows else ''}
{'<h2>Merge requests</h2><table>' + mr_rows + '</table>' if mr_rows else ''}
<h2>Latest conversation</h2><ul>{comments or '<li>No human comments.</li>'}</ul>
<p class="muted">Business days skip weekends and company holidays. Generated from Jira data by the PE Delivery Dashboard.</p>
</body></html>"""
