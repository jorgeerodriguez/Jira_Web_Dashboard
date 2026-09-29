"""Teams Conversations (formerly Word of the Month): what ticket comments say about how we work, and where time gets lost.

Built from human comments (bots such as "Automation for Jira" are dropped when the data loads) on
tickets only (no Features or Initiatives). Four views:

- Comment coverage -- a metric to track: the share of tickets completed each month that carry at
  least one human comment, and the share where the assignee left one (a closing note).
- Friction themes -- transparent keyword rules (THEMES, editable) tag tickets whose comments talk
  about waiting, access, approvals, clarification, chasing, rework or incidents. Each theme's cost
  is its tickets' extra business days versus similar tickets (same Priority x Size) without it.
  Long tickets collect more comments, so this is association, not proof of cause.
- Conversation health -- time to first human reply (business hours), back-and-forth between
  people, and how often the requester had to chase.
- Phrases -- the most used two-word phrases in comments, and the ones rising versus the previous
  months ("Phrase of the Month").
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import in_progress_report as ipr

try:
    from darkstar.metrics import business_hours_between
except ImportError:  # `holidays` missing: fall back to calendar hours
    def business_hours_between(start, end) -> float:
        return max((end - start).total_seconds() / 3600.0, 0.0)


JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
DEFAULT_COVERAGE_TARGET = 0.80
BUSINESS_HOURS_PER_DAY = 9.0  # 08:00-17:00, as in darkstar.metrics
TREND_MONTHS = 12
EMERGING_BASELINE_MONTHS = 3
MIN_EMERGING_COUNT = 4
MIN_SLICE_TICKETS = 10
INK = ipr.INK
SERIES_COLORS = ("#2a78d6", "#eb6834")  # categorical slots 1 and 2

# Friction themes: a ticket has a theme when any human comment matches. Edit freely.
THEMES: dict[str, str] = {
    "Waiting / blocked": r"\b(?:waiting (?:on|for)|blocked|blocker|dependenc(?:y|ies)|depends on|on hold)\b",
    "Access / permissions": r"\b(?:access|permissions?|iam|grant(?:ed)?|credentials?|vpn|sso|okta)\b",
    "Approval": r"\b(?:approv\w*|sign[- ]?off)\b",
    "Clarification": r"\b(?:clarif\w*|what do you mean|more (?:info|information|details)|can you confirm|requirements?)\b",
    "Chasing / follow-up": r"\b(?:any updates?|following up|follow[- ]?up|bump(?:ing)?|checking in)\b",
    "Rework / rollback": r"\b(?:roll(?:ed)? ?back|revert\w*|reopen\w*|still (?:failing|broken|not working)|not working|doesn'?t work)\b",
    "Incident / outage": r"\b(?:outage|incident|sev ?[0-4]|downtime|went down|is down)\b",
}
# Symptoms of slow tickets rather than causes: shown, but not ranked as opportunities.
SYMPTOM_THEMES = {"Chasing / follow-up"}
# Only the requester's own comments count for these themes (an engineer writing "I'll follow up"
# is not the requester chasing).
REQUESTER_ONLY_THEMES = {"Chasing / follow-up"}

# Suggested process change per theme, used in the recommendations panel.
THEME_ACTIONS: dict[str, str] = {
    "Waiting / blocked": "Surface dependencies at intake and use the Blocked status so waits are visible and owned.",
    "Access / permissions": "Offer self-service or pre-approved access roles for the most common requests.",
    "Approval": "Name the approver on the intake form and pre-approve routine changes.",
    "Clarification": "Tighten the intake template with required fields for what engineers keep asking.",
    "Chasing / follow-up": "Give requesters proactive status updates or a visible ETA so they don't have to chase.",
    "Rework / rollback": "Add a validation or test step before release for the affected work types.",
    "Incident / outage": "Make sure incident follow-ups get a post-incident review and a preventive ticket.",
}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could", "do", "does", "for",
    "from", "has", "have", "had", "he", "her", "him", "his", "i", "if", "in", "into", "is", "it", "its",
    "just", "let", "me", "my", "no", "not", "now", "of", "on", "or", "our", "out", "she", "should", "so",
    "some", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "up",
    "was", "we", "were", "what", "when", "which", "who", "will", "with", "would", "you", "your", "all",
    "also", "any", "get", "got", "here", "how", "know", "like", "one", "see", "thanks", "thank", "please",
    "hi", "hello", "hey", "ok", "okay", "yes", "yeah", "sure", "need", "needs", "able", "going", "still",
    "audacy", "com", "www", "http", "https", "net", "org",
    "i'm", "it's", "we're", "i'll", "let's", "don't", "can't", "there's", "that's", "you're", "we'll",
}
_MARKUP = [
    (re.compile(r"\{(code|noformat|quote)[^}]*\}.*?\{\1\}", re.S), " "),   # code/noformat/quote blocks
    (re.compile(r"\[~accountid:[^\]]+\]"), " "),                              # @mentions
    (re.compile(r"\[[^\]|]*\|[^\]]*\]"), " "),                                # [text|link]
    (re.compile(r"https?://\S+"), " "),
    (re.compile(r"![^!\s]+!"), " "),                                          # !image.png!
    (re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b"), " "),                             # ticket keys
]


def _empty_payload() -> dict:
    return {
        "available_months": [], "start_month": None, "end_month": None, "error_message": None,
        "coverage": None, "coverage_delta": None, "closing_note": None, "closing_note_delta": None,
        "first_reply_hours": None, "chased_share": None, "tickets_in_range": 0,
        "coverage_fig": None, "coverage_by_lead_df": pd.DataFrame(), "coverage_by_assignee_df": pd.DataFrame(),
        "themes_df": pd.DataFrame(), "pareto_fig": None, "theme_trend_fig": None, "slice_figs": {},
        "recommendations": [], "handoff_fig": None, "people_df": pd.DataFrame(),
        "phrases_fig": None, "emerging_df": pd.DataFrame(), "phrase_of_the_month": None,
        "tickets_df": pd.DataFrame(),
    }


# ── Text helpers ────────────────────────────────────────────────────────────────

def clean_comment(text: str) -> str:
    out = str(text or "")
    for pattern, repl in _MARKUP:
        out = pattern.sub(repl, out)
    return out


def _bigrams(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z][a-z'\-]+", clean_comment(text).lower())
             if len(w) > 2 and w not in _STOPWORDS]
    return [f"{a} {b}" for a, b in zip(words, words[1:]) if a != b]


def tag_themes(bodies: list[str]) -> dict[str, str | None]:
    """First matching excerpt per theme (None when the theme is absent)."""
    found: dict[str, str | None] = {theme: None for theme in THEMES}
    for body in bodies:
        text = clean_comment(body)
        for theme, pattern in THEMES.items():
            if found[theme] is None:
                match = re.search(pattern, text, flags=re.I)
                if match:
                    lo = max(match.start() - 60, 0)
                    found[theme] = ("…" if lo else "") + " ".join(text[lo:lo + 160].split()) + "…"
    return found


# ── Per-ticket facts ────────────────────────────────────────────────────────────

def _ticket_facts(tickets: pd.DataFrame, hol: np.ndarray) -> pd.DataFrame:
    df = tickets.copy()
    df["comments"] = df["comments"].apply(lambda c: c if isinstance(c, list) else [])
    df["done"] = df["status"].astype(str).str.strip().str.casefold().eq("done")
    finished = df.get("status_category_changed", pd.Series(pd.NaT, index=df.index))
    df["done_day"] = ipr._to_day(finished, ipr.LOCAL_TZ).fillna(ipr._to_day(df["updated"], ipr.LOCAL_TZ))
    df["done_month"] = df["done_day"].dt.to_period("M").astype(str).where(df["done"])
    df["start_day"] = ipr._to_day(df["planned_start_date"], timezone.utc)
    df["cycle_bd"] = np.where(df["done"] & df["start_day"].notna() & (df["done_day"] >= df["start_day"]),
                              ipr._busdays_between(df["start_day"], df["done_day"], hol), np.nan)
    df["created_utc"] = pd.to_datetime(df["created"], utc=True, errors="coerce")
    df["created_month"] = df["created_utc"].dt.tz_convert(ipr.LOCAL_TZ).dt.tz_localize(None).dt.to_period("M").astype(str)
    df["reporter_name"] = df.get("reporter_name", pd.Series("Unknown", index=df.index)).fillna("Unknown")
    df["lead"] = (df.get("business_lead", pd.Series(index=df.index)).fillna("Unknown").astype(str)
                  .replace({"": "Unknown", "None": "Unknown", "nan": "Unknown"}))

    def per_ticket(row) -> pd.Series:
        comments = sorted(row["comments"], key=lambda c: c["created"] if pd.notna(c["created"]) else pd.Timestamp.max.tz_localize("UTC"))
        authors = [c["author"] for c in comments]
        reply = next((c for c in comments if c["author"] != row["reporter_name"]), None)
        reply_hours = np.nan
        if reply is not None and pd.notna(reply["created"]) and pd.notna(row["created_utc"]):
            reply_hours = business_hours_between(row["created_utc"].tz_convert("UTC").tz_localize(None).to_pydatetime(),
                                                 reply["created"].tz_convert("UTC").tz_localize(None).to_pydatetime())
        themes = tag_themes([c["body"] for c in comments])
        requester_themes = tag_themes([c["body"] for c in comments
                                       if c["author"] == row["reporter_name"] and c["author"] != row["assignee_name"]])
        for theme in REQUESTER_ONLY_THEMES:
            themes[theme] = requester_themes[theme]
        chased = themes["Chasing / follow-up"] is not None
        return pd.Series({
            "human_comments": len(comments),
            "has_comment": bool(comments),
            "assignee_commented": row["assignee_name"] in authors,
            "handoffs": max(sum(1 for a, b in zip(authors, authors[1:]) if a != b), 0),
            "first_reply_hours": reply_hours,
            "chased": chased,
            **{f"theme::{t}": excerpt for t, excerpt in themes.items()},
        })

    return df.join(df.apply(per_ticket, axis=1))


def _excess_days(done: pd.DataFrame) -> pd.DataFrame:
    """Per theme: tickets, share and extra business days versus similar tickets without the theme."""
    rows = []
    timed = done[done["cycle_bd"].notna()]
    for theme in THEMES:
        flag = done[f"theme::{theme}"].notna()
        with_theme = timed[timed[f"theme::{theme}"].notna()]
        without = timed[timed[f"theme::{theme}"].isna()]
        extras = []
        for _, row in with_theme.iterrows():
            ref = without[(without["priority_bucket"] == row["priority_bucket"]) & (without["size"] == row["size"])]
            if len(ref) < 8:
                ref = without[without["priority_bucket"] == row["priority_bucket"]]
            if len(ref) < 8:
                ref = without
            extras.append(row["cycle_bd"] - ref["cycle_bd"].median() if len(ref) else np.nan)
        extras = pd.Series(extras, dtype=float).dropna()
        median_extra = float(extras.median()) if len(extras) else 0.0
        rows.append({
            "Theme": theme,
            "Tickets": int(flag.sum()),
            "Share": float(flag.mean()) if len(done) else 0.0,
            "Median cycle (bd)": float(with_theme["cycle_bd"].median()) if len(with_theme) else np.nan,
            "Similar tickets without (bd)": float(without["cycle_bd"].median()) if len(without) else np.nan,
            "Extra days per ticket": median_extra,
            "Total extra days": max(median_extra, 0.0) * len(extras),
            "Kind": "Symptom" if theme in SYMPTOM_THEMES else "Cause",
            "Suggested change": THEME_ACTIONS[theme],
        })
    return pd.DataFrame(rows).sort_values("Total extra days", ascending=False).reset_index(drop=True)


# ── Figures ─────────────────────────────────────────────────────────────────────

def _coverage_figure(trend: pd.DataFrame, target: float) -> go.Figure:
    fig = go.Figure()
    for (col, name), color in zip([("coverage", "Tickets with a human comment"),
                                   ("closing_note", "Assignee left a comment")], SERIES_COLORS):
        fig.add_trace(go.Scatter(
            x=trend["month"], y=trend[col], mode="lines+markers", name=name,
            line=dict(color=color, width=2), marker=dict(size=8),
            customdata=trend[["tickets"]],
            hovertemplate="%{x}: %{y:.0%} of %{customdata[0]} completed tickets<extra>" + name + "</extra>",
        ))
    fig.add_hline(y=target, line_dash="dash", line_color=INK, line_width=1.5,
                  annotation_text=f"Target {target:.0%}", annotation_position="top left",
                  annotation_font_color=INK)
    fig.update_yaxes(tickformat=".0%", range=[0, 1.05], title=None, gridcolor="rgba(148,163,184,0.25)")
    fig.update_xaxes(title=None)
    fig.update_layout(height=340, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _pareto_figure(themes: pd.DataFrame) -> go.Figure:
    plot = themes.sort_values("Total extra days", ascending=True).copy()
    plot["label"] = np.where(plot["Kind"] == "Symptom", plot["Theme"] + " (symptom)", plot["Theme"])
    fig = go.Figure(go.Bar(
        y=plot["label"], x=plot["Total extra days"], orientation="h", marker_color=SERIES_COLORS[0],
        text=[f"{d:.0f} bd · {n} tickets" for d, n in zip(plot["Total extra days"], plot["Tickets"])],
        textposition="outside", cliponaxis=False,
        customdata=plot[["Extra days per ticket", "Share"]],
        hovertemplate="<b>%{y}</b><br>%{x:.0f} extra business days in total"
                      "<br>+%{customdata[0]:.1f} bd per ticket vs similar tickets"
                      "<br>%{customdata[1]:.0%} of completed tickets<extra></extra>",
    ))
    fig.update_xaxes(title="Extra business days vs similar tickets", range=[0, max(plot["Total extra days"].max(), 1) * 1.35],
                     gridcolor="rgba(148,163,184,0.25)")
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=None)
    return fig


def _share_heatmap(frame: pd.DataFrame, row_col: str, row_order: list[str], counts: pd.Series,
                   colorbar: str = "Share of<br>tickets") -> go.Figure:
    themes = list(THEMES)
    z = [[float(frame.loc[frame[row_col] == r, f"theme::{t}"].notna().mean()) for t in themes] for r in row_order]
    text = [[f"{v:.0%}" for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=themes, y=[f"{r} ({counts[r]})" for r in row_order], text=text, texttemplate="%{text}",
        colorscale="Blues", zmin=0, xgap=2, ygap=2, colorbar=dict(title=colorbar, tickformat=".0%"),
        hovertemplate="%{y} · %{x}: %{z:.0%} of tickets<extra></extra>",
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=max(300, 30 * len(row_order) + 120), margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _handoff_figure(done: pd.DataFrame) -> go.Figure:
    timed = done[done["cycle_bd"].notna()].copy()
    timed["Back-and-forth"] = pd.cut(timed["handoffs"], [-1, 0, 2, 5, 10_000], labels=["None", "1–2", "3–5", "6+"])
    stats = timed.groupby("Back-and-forth", observed=False)["cycle_bd"].agg(["size", "median"]).reset_index()
    fig = go.Figure(go.Bar(
        x=stats["Back-and-forth"].astype(str), y=stats["median"], marker_color=SERIES_COLORS[0],
        text=[f"{m:.0f} bd" if pd.notna(m) else "" for m in stats["median"]], textposition="outside", cliponaxis=False,
        customdata=stats[["size"]],
        hovertemplate="%{x} changes of speaker: median %{y:.1f} business days (%{customdata[0]} tickets)<extra></extra>",
    ))
    fig.update_yaxes(title="Median cycle (business days)", gridcolor="rgba(148,163,184,0.25)",
                     range=[0, max(stats["median"].max() if stats["median"].notna().any() else 1, 1) * 1.25])
    fig.update_xaxes(title="Changes of speaker in the comments")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _phrases_figure(counts: Counter, top_n: int) -> go.Figure | None:
    top = counts.most_common(top_n)
    if not top:
        return None
    data = pd.DataFrame(top, columns=["Phrase", "Mentions"]).sort_values("Mentions")
    fig = go.Figure(go.Bar(y=data["Phrase"], x=data["Mentions"], orientation="h", marker_color=SERIES_COLORS[0],
                           text=data["Mentions"], textposition="outside", cliponaxis=False,
                           hovertemplate="%{y}: %{x} mentions<extra></extra>"))
    fig.update_layout(height=max(320, 24 * len(data) + 80), margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title="Mentions in human comments", yaxis_title=None)
    return fig


# ── Entry point ─────────────────────────────────────────────────────────────────

def _month_range(months: list[str]) -> tuple[str | None, str | None]:
    if not months:
        return None, None
    return months[max(0, len(months) - 3)], months[-1]


def build_word_of_the_month_visuals(df_issues: pd.DataFrame, start_month: str | None = None,
                                    end_month: str | None = None, coverage_target: float = DEFAULT_COVERAGE_TARGET,
                                    top_n: int = 15) -> dict:
    payload = _empty_payload()
    if df_issues is None or df_issues.empty:
        payload["error_message"] = "No ticket data available."
        return payload
    if "comments" not in df_issues.columns:
        payload["error_message"] = "Comments are not loaded. Fetch Jira tickets again to load them."
        return payload

    tickets = ipr.prepare_tickets(df_issues)
    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = _ticket_facts(tickets, hol)
    done = facts[facts["done"] & facts["done_month"].notna()]

    months = sorted(m for m in done["done_month"].unique() if m <= today.strftime("%Y-%m"))
    payload["available_months"] = months
    default_start, default_end = _month_range(months)
    start, end = start_month or default_start, end_month or default_end
    if start is None:
        payload["error_message"] = "No completed tickets found."
        return payload
    start, end = min(start, end), max(start, end)
    payload["start_month"], payload["end_month"] = start, end

    in_range = done[(done["done_month"] >= start) & (done["done_month"] <= end)]
    payload["tickets_in_range"] = int(len(in_range))
    if in_range.empty:
        payload["error_message"] = f"No tickets completed between {start} and {end}."
        return payload

    # ── Coverage metric ──
    period = pd.period_range(start, end, freq="M")
    prev = pd.period_range(period[0] - len(period), period[0] - 1, freq="M").astype(str)
    previous = done[done["done_month"].isin(prev)]
    payload["coverage"] = float(in_range["has_comment"].mean())
    payload["closing_note"] = float(in_range["assignee_commented"].mean())
    if len(previous):
        payload["coverage_delta"] = payload["coverage"] - float(previous["has_comment"].mean())
        payload["closing_note_delta"] = payload["closing_note"] - float(previous["assignee_commented"].mean())
    trend = (done[done["done_month"].isin(months[-TREND_MONTHS:])].groupby("done_month")
             .agg(tickets=("key", "size"), coverage=("has_comment", "mean"), closing_note=("assignee_commented", "mean"))
             .reset_index().rename(columns={"done_month": "month"}))
    payload["coverage_fig"] = _coverage_figure(trend, coverage_target)

    def coverage_table(col: str, label: str) -> pd.DataFrame:
        g = in_range.groupby(col).agg(tickets=("key", "size"), coverage=("has_comment", "mean"),
                                      closing=("assignee_commented", "mean"))
        g = g[g["tickets"] >= 3].sort_values("coverage")
        return pd.DataFrame({label: g.index, "Completed Tickets": g["tickets"].values,
                             "With Human Comment": (g["coverage"] * 100).round(0).values,
                             "Assignee Commented": (g["closing"] * 100).round(0).values})

    payload["coverage_by_lead_df"] = coverage_table("lead", "Business Lead")
    payload["coverage_by_assignee_df"] = coverage_table("assignee_name", "Assignee")

    # ── Friction themes ──
    themes = _excess_days(in_range)
    payload["themes_df"] = themes
    payload["pareto_fig"] = _pareto_figure(themes)
    trend_frame = done[done["done_month"].isin(months[-TREND_MONTHS:])]
    month_order = sorted(trend_frame["done_month"].unique())
    z = [[float(trend_frame.loc[trend_frame["done_month"] == m, f"theme::{t}"].notna().mean()) for m in month_order]
         for t in THEMES]
    payload["theme_trend_fig"] = go.Figure(go.Heatmap(
        z=z, x=month_order, y=list(THEMES), colorscale="Blues", zmin=0, xgap=2, ygap=2,
        text=[[f"{v:.0%}" for v in row] for row in z], texttemplate="%{text}",
        colorbar=dict(title="Share of<br>tickets", tickformat=".0%"),
        hovertemplate="%{x} · %{y}: %{z:.0%} of completed tickets<extra></extra>",
    )).update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10)).update_yaxes(autorange="reversed")

    slice_cols = {"Business Lead": "lead", "Issue Type": "issuetype", "Priority": "priority_bucket"}
    for label, col in slice_cols.items():
        counts = in_range[col].value_counts()
        rows = counts[counts >= MIN_SLICE_TICKETS].index.tolist()[:12]
        if col == "priority_bucket":
            rows = [p for p in ipr.PRIORITY_ORDER if p in rows]
        if rows:
            payload["slice_figs"][label] = _share_heatmap(in_range, col, rows, counts)

    payload["recommendations"] = [
        {"theme": r["Theme"], "tickets": int(r["Tickets"]), "extra_per_ticket": float(r["Extra days per ticket"]),
         "total_extra": float(r["Total extra days"]), "action": r["Suggested change"]}
        for _, r in themes[(themes["Total extra days"] > 0) & (themes["Kind"] == "Cause")].head(3).iterrows()
    ]

    # ── Conversation health ──
    created_in_range = facts[(facts["created_month"] >= start) & (facts["created_month"] <= end)]
    replies = created_in_range["first_reply_hours"].dropna()
    payload["first_reply_hours"] = float(replies.median()) if len(replies) else None
    payload["chased_share"] = float(in_range["chased"].mean())
    payload["handoff_fig"] = _handoff_figure(in_range)
    people = created_in_range.groupby("assignee_name").agg(
        tickets=("key", "size"), first_reply=("first_reply_hours", "median"),
        replied_1bd=("first_reply_hours", lambda s: float((s.dropna() <= BUSINESS_HOURS_PER_DAY).mean()) if s.notna().any() else np.nan))
    back_forth = in_range.groupby("assignee_name")["handoffs"].median()
    people = people.join(back_forth.rename("handoffs"), how="outer")
    people = people[people["tickets"].fillna(0) >= 3].drop(index="Unassigned", errors="ignore")
    payload["people_df"] = pd.DataFrame({
        "Assignee": people.index,
        "Tickets Created in Range": people["tickets"].fillna(0).astype(int).values,
        "Median First Reply (business h)": people["first_reply"].round(1).values,
        "Replied Within 1 Business Day %": (people["replied_1bd"] * 100).round(0).values,
        "Median Back-and-forth": people["handoffs"].round(1).values,
    }).sort_values("Median First Reply (business h)", na_position="last")

    # ── Phrases ──
    # People's names are not phrases: drop any phrase containing a name part seen in the data.
    names = set(facts["assignee_name"]) | set(facts["reporter_name"]) | {
        c["author"] for comments in facts["comments"] for c in comments}
    name_parts = {part for name in names for part in re.split(r"[^a-z]+", str(name).lower()) if len(part) > 2}

    def phrase_counts(frame: pd.DataFrame) -> Counter:
        counts: Counter = Counter()
        for comments in frame["comments"]:
            for c in comments:
                # once per comment, so one long comment can't dominate
                counts.update({b for b in _bigrams(c["body"]) if not name_parts.intersection(b.split())})
        return counts

    now_counts = phrase_counts(in_range)
    base_months = pd.period_range(period[0] - EMERGING_BASELINE_MONTHS, period[0] - 1, freq="M").astype(str)
    base = done[done["done_month"].isin(base_months)]
    base_counts = phrase_counts(base)
    # Scale by phrase volume, not ticket count: comments have grown longer over time.
    scale = max(sum(now_counts.values()), 1) / max(sum(base_counts.values()), 1)
    emerging = []
    for phrase, n in now_counts.items():
        if n >= MIN_EMERGING_COUNT:
            expected = base_counts.get(phrase, 0) * scale
            emerging.append({"Phrase": phrase, "Mentions": n, "Previous (scaled)": round(expected, 1),
                             "Lift": round((n + 1) / (expected + 1), 1)})
    emerging_df = pd.DataFrame(emerging, columns=["Phrase", "Mentions", "Previous (scaled)", "Lift"])
    emerging_df = emerging_df.sort_values(["Lift", "Mentions"], ascending=False).head(top_n).reset_index(drop=True)
    payload["phrases_fig"] = _phrases_figure(now_counts, top_n)
    payload["emerging_df"] = emerging_df
    payload["phrase_of_the_month"] = emerging_df.iloc[0]["Phrase"] if not emerging_df.empty else (
        now_counts.most_common(1)[0][0] if now_counts else None)

    # ── Drill-down ──
    theme_cols = [f"theme::{t}" for t in THEMES]
    tagged = in_range[in_range[theme_cols].notna().any(axis=1)].copy()
    tagged["Themes"] = tagged[theme_cols].apply(lambda r: ", ".join(t for t, v in zip(THEMES, r) if v is not None and pd.notna(v)), axis=1)
    tagged["Example"] = tagged[theme_cols].apply(lambda r: next((v for v in r if v is not None and pd.notna(v)), ""), axis=1)
    payload["tickets_df"] = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + tagged["key"].astype(str),
        "Themes": tagged["Themes"],
        "Business Lead": tagged["lead"],
        "Assignee": tagged["assignee_name"],
        "Priority": tagged["priority_bucket"],
        "Size": tagged["size"],
        "Cycle (bd)": tagged["cycle_bd"].round(0).astype("Int64"),
        "Human Comments": tagged["human_comments"].astype(int),
        "Back-and-forth": tagged["handoffs"].astype(int),
        "Example": tagged["Example"],
    }).sort_values("Cycle (bd)", ascending=False, na_position="last")
    return payload
