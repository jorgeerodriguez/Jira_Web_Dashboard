"""Distribution of Ticket by Estimated Size: sizing as a practice -- coverage, accuracy, outcomes, load.

PE tickets only (no Features, Initiatives or Release Management CAR tickets). Times are business days
of work: Target start -> Done (from creation when Target start is earlier).

- Coverage: share of tickets with an Estimated Size, for open tickets and for tickets created in the
  last 30 days, against COVERAGE_TARGET; monthly adoption; where sizes are missing (business lead,
  assignee, issue type).
- Accuracy: share of completed sized tickets whose work landed inside the sizing guide for their size
  (undersized = took longer, oversized = took less). The guide is derived from the team's own work:
  the boundary between two sizes sits at the geometric midpoint of their typical (median) work, so it
  reflects how sizes are actually used. Sizes with fewer than MIN_GUIDE_SAMPLE completed tickets fall
  back to DEFAULT_GUIDE_BD.
- Outcomes: SLA met and lead time per size (measured, replacing the old assumed "risk" cells).
- Load: open In Progress / Backlog by size and priority, and the business days of work that represents.
- Action lists: open tickets that need a size, and in-progress tickets already running past their
  size's range (likely undersized).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from reports import executive_summary as es
from reports import in_progress_report as ipr


JIRA_BROWSE_BASE_URL = ipr.JIRA_BROWSE_BASE_URL
COVERAGE_TARGET = 0.90
RECENT_DAYS = 30
OUTCOME_DAYS = 180          # completed work used for accuracy and outcomes
BREAKDOWN_DAYS = 90         # created work used for "where sizes are missing"
MIN_GROUP = 5
TREND_MONTHS = 12
SIZES = ["Small", "Medium", "Large", "XL"]
UNSIZED = "Unestimated"
SIZE_LABELS = {"Small": "Small", "Medium": "Medium", "Large": "Large", "XL": "XL", UNSIZED: "Unsized"}
# Fallback sizing guide, in business days of work (Target start -> Done), used when there is too little
# completed work to derive one. Edit to the team's written agreement.
DEFAULT_GUIDE_BD = {"Small": (0, 2), "Medium": (3, 5), "Large": (6, 12), "XL": (13, None)}
MIN_GUIDE_SAMPLE = 10
SERIES = "#2a78d6"
WARN = "#eb6834"
GUIDE_FILL = "rgba(42,120,214,0.10)"
INK = ipr.INK
GRID = es.GRID


def _empty_payload(message: str | None = None) -> dict:
    return {"error_message": message, "kpis": {}, "adoption_fig": None, "coverage_figs": {}, "accuracy_fig": None,
            "guide_df": pd.DataFrame(), "outcomes_df": pd.DataFrame(), "outcome_fig": None, "load_fig": None,
            "needs_size_df": pd.DataFrame(), "undersized_df": pd.DataFrame(), "detail_df": pd.DataFrame(),
            "recommendations": [], "guide": {}, "guide_from_data": False}


def derive_guide(done: pd.DataFrame) -> tuple[dict, bool]:
    """(guide, from_data). Boundaries at the geometric midpoint of neighbouring sizes' median work."""
    medians = done[done["size"].isin(SIZES)].groupby("size")["work_bd"].agg(["median", "count"]).reindex(SIZES)
    if (medians["count"].fillna(0) < MIN_GUIDE_SAMPLE).any() or medians["median"].isna().any():
        uppers = [DEFAULT_GUIDE_BD[s][1] for s in SIZES[:-1]]
        from_data = False
        # use data where it exists, defaults elsewhere
        for i, (a, b) in enumerate(zip(SIZES, SIZES[1:])):
            if medians.loc[[a, b], "count"].fillna(0).min() >= MIN_GUIDE_SAMPLE:
                uppers[i] = int(round(np.sqrt(max(medians.loc[a, "median"], 0.5) * max(medians.loc[b, "median"], 0.5))))
                from_data = True
    else:
        uppers = [int(round(np.sqrt(max(medians.loc[a, "median"], 0.5) * max(medians.loc[b, "median"], 0.5))))
                  for a, b in zip(SIZES, SIZES[1:])]
        from_data = True
    uppers = [max(u, (uppers[i - 1] + 1) if i else 0) for i, u in enumerate(uppers)]   # keep bands increasing
    lows = [0] + [u + 1 for u in uppers]
    return {s: (lows[i], uppers[i] if i < len(uppers) else None) for i, s in enumerate(SIZES)}, from_data


def guide_text(size: str, guide: dict) -> str:
    lo, hi = guide[size]
    return f"{lo}–{hi} bd" if hi is not None else f"{lo}+ bd"


def size_fit(size: str, work_bd: float, guide: dict) -> str | None:
    """'Right size', 'Undersized' (took longer than the guide) or 'Oversized' (took less)."""
    if size not in guide or pd.isna(work_bd):
        return None
    lo, hi = guide[size]
    if work_bd < lo:
        return "Oversized"
    if hi is not None and work_bd > hi:
        return "Undersized"
    return "Right size"


def _size_for(work_bd: float, guide: dict) -> str:
    return next((s for s in SIZES if guide[s][1] is None or work_bd <= guide[s][1]), "XL")


def _work_bd(frame: pd.DataFrame, end: pd.Series, hol) -> pd.Series:
    begin = frame["start_day"].where(frame["start_day"] >= frame["created_day"], frame["created_day"])
    begin = begin.where(frame["start_day"].notna() & (frame["start_day"] <= end))
    return ipr._busdays_between(begin, end, hol)


# ── Figures ─────────────────────────────────────────────────────────────────────

def _adoption_figure(pe: pd.DataFrame, today: pd.Timestamp) -> go.Figure:
    current = today.to_period("M")
    months = pd.period_range(current - (TREND_MONTHS - 1), current, freq="M")
    created = pe[pe["created_day"].dt.to_period("M").isin(months)]
    trend = created.groupby(created["created_day"].dt.to_period("M")).agg(
        sized=("sized", "mean"), tickets=("key", "size")).reindex(months)
    labels = [m.strftime("%b %y") for m in months]
    fig = go.Figure(go.Scatter(
        x=labels, y=trend["sized"], mode="lines+markers+text", line=dict(color=SERIES, width=2),
        marker=dict(size=8, color=np.where(months == current, "white", SERIES), line=dict(color=SERIES, width=2)),
        text=[f"{v:.0%}" if pd.notna(v) else "" for v in trend["sized"]], textposition="top center",
        customdata=trend[["tickets"]], hovertemplate="%{x}: %{y:.0%} of %{customdata[0]} new tickets sized<extra></extra>",
    ))
    fig.add_hline(y=COVERAGE_TARGET, line_dash="dash", line_color=INK, line_width=1.5,
                  annotation_text=f"Target {COVERAGE_TARGET:.0%}", annotation_position="bottom right",
                  annotation_font_color=INK)
    fig.update_yaxes(tickformat=".0%", range=[0, 1.08], title="New tickets with a size", gridcolor=GRID)
    fig.update_layout(height=320, showlegend=False, margin=dict(l=10, r=10, t=20, b=10))
    return fig


def _coverage_bar(frame: pd.DataFrame, col: str) -> go.Figure | None:
    g = frame.groupby(col).agg(sized=("sized", "mean"), tickets=("key", "size"))
    g = g[g["tickets"] >= MIN_GROUP].sort_values("sized")
    if g.empty:
        return None
    colors = np.where(g["sized"] >= COVERAGE_TARGET, SERIES, WARN)
    fig = go.Figure(go.Bar(
        y=g.index, x=g["sized"], orientation="h", marker_color=colors,
        text=[f"{v:.0%} of {n}" for v, n in zip(g["sized"], g["tickets"])], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:.0%} sized<extra></extra>",
    ))
    fig.add_vline(x=COVERAGE_TARGET, line_dash="dash", line_color=INK)
    fig.update_xaxes(tickformat=".0%", range=[0, 1.3], title=f"Tickets sized (created in the last {BREAKDOWN_DAYS} days)",
                     gridcolor=GRID)
    fig.update_layout(height=max(260, 26 * len(g) + 90), margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _accuracy_figure(done: pd.DataFrame, guide: dict) -> go.Figure | None:
    sized = done[done["size"].isin(SIZES) & done["work_bd"].notna()]
    if sized.empty:
        return None
    order = [s for s in SIZES if (sized["size"] == s).any()]
    cap = max(float(sized["work_bd"].quantile(0.97)), 25.0)
    fig = go.Figure()
    for i, size in enumerate(order):
        lo, hi = guide[size]
        fig.add_shape(type="rect", x0=lo, x1=hi if hi is not None else cap, y0=i - 0.42, y1=i + 0.42,
                      xref="x", yref="y", fillcolor=GUIDE_FILL, line=dict(color="rgba(42,120,214,0.45)", width=1, dash="dot"),
                      layer="below")
        rows = sized[sized["size"] == size]
        fig.add_trace(go.Box(
            x=rows["work_bd"], y=[f"{size} ({len(rows)})"] * len(rows), orientation="h", name=size, boxpoints="outliers",
            marker=dict(color=SERIES, size=5), line=dict(color=SERIES, width=2), fillcolor="rgba(42,120,214,0.15)",
            hovertemplate=f"{size}: %{{x:.0f}} business days of work<extra></extra>", showlegend=False,
        ))
    fig.update_yaxes(categoryorder="array", categoryarray=[f"{s} ({int((sized['size'] == s).sum())})" for s in order],
                     title=None)
    fig.update_xaxes(range=[0, cap], title="Business days of work, Target start → Done (shaded = sizing guide)",
                     gridcolor=GRID)
    fig.update_layout(height=max(260, 70 * len(order) + 90), margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _outcome_figure(outcomes: pd.DataFrame) -> go.Figure | None:
    rows = outcomes[outcomes["SLA Sample"] > 0]
    if rows.empty:
        return None
    small = rows["SLA Sample"] < MIN_GROUP
    fig = go.Figure(go.Bar(
        x=rows["Size"], y=rows["SLA Met %"] / 100, marker_color=np.where(small, "rgba(42,120,214,0.35)", SERIES),
        text=[f"{v:.0f}%{'*' if s else ''} · n={n}" for v, s, n in zip(rows["SLA Met %"], small, rows["SLA Sample"])],
        textposition="outside", cliponaxis=False, hovertemplate="%{x}: %{y:.0%} met their SLA<extra></extra>",
    ))
    fig.add_hline(y=1 - 0.10, line_dash="dash", line_color=INK, annotation_text="90% (breach goal under 10%)",
                  annotation_position="bottom right", annotation_font_color=INK)
    fig.update_yaxes(tickformat=".0%", range=[0, 1.12], title="Completed within SLA", gridcolor=GRID)
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _load_figure(open_t: pd.DataFrame) -> go.Figure | None:
    rows = open_t[open_t["stage"].isin(["In Progress", "Backlog"])]
    if rows.empty:
        return None
    order = SIZES + [UNSIZED]
    fig = go.Figure()
    for priority in ipr.PRIORITY_ORDER:
        part = rows[rows["priority_bucket"] == priority]
        if part.empty:
            continue
        counts = part.groupby(["stage", "size"]).size()
        x = [[s for st_ in ["In Progress", "Backlog"] for s in [st_] * len(order)],
             [SIZE_LABELS[s] for _ in ["In Progress", "Backlog"] for s in order]]
        y = [int(counts.get((st_, s), 0)) for st_ in ["In Progress", "Backlog"] for s in order]
        fig.add_trace(go.Bar(x=x, y=y, name=priority,
                             marker=dict(color=es.PRIORITY_SHADES[priority], line=dict(color="rgba(255,255,255,0.9)", width=2)),
                             hovertemplate="%{x}: %{y} " + priority + "<extra></extra>"))
    fig.update_yaxes(title="Open tickets", gridcolor=GRID)
    fig.update_layout(barmode="stack", height=340, legend_title_text="Priority",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _recommendations(kpis: dict, guide: dict, guide_df: pd.DataFrame, outcomes: pd.DataFrame, window: pd.DataFrame) -> list[str]:
    """Plain-English, data-backed changes to how the team sizes work."""
    recs = []
    cov = kpis.get("coverage_new")
    if cov is not None and cov < COVERAGE_TARGET:
        gaps = []
        for col, label in (("assignee_name", "unassigned tickets"), ("lead", "tickets with no business lead")):
            key = "Unassigned" if col == "assignee_name" else "Unknown"
            part = window[window[col] == key]
            if len(part) >= MIN_GROUP:
                gaps.append(f"{label} ({part['sized'].mean():.0%} sized)")
        recs.append(f"**Size at intake.** Only {cov:.0%} of new tickets are sized (target {COVERAGE_TARGET:.0%}). "
                    "Make Estimated Size required at triage" + (f", starting with {' and '.join(gaps)}." if gaps else "."))
    for _, row in guide_df.iterrows():
        if row["Completed"] < MIN_GUIDE_SAMPLE:
            continue
        if row["Oversized %"] >= 40:
            smaller = SIZES[SIZES.index(row["Size"]) - 1] if row["Size"] != "Small" else None
            recs.append(f"**{row['Size']} is often smaller work.** {row['Oversized %']:.0f}% of {row['Size']} tickets "
                        f"finished in under {guide[row['Size']][0]} business days" + (f", which is {smaller} effort." if smaller else "."))
        if row["Undersized %"] >= 30:
            recs.append(f"**{row['Size']} is often bigger than it looks.** {row['Undersized %']:.0f}% of {row['Size']} "
                        f"tickets took longer than {guide_text(row['Size'], guide)}. Re-size when work runs past the range "
                        "(see Likely Undersized below).")
    unsized = outcomes[outcomes["Size"] == "Unsized"]
    if not unsized.empty and pd.notna(unsized["Work P50 (bd)"].iloc[0]):
        work = float(unsized["Work P50 (bd)"].iloc[0])
        closest = _size_for(work, guide)
        if closest != ipr.ASSUMED_SIZE:
            recs.append(f"**Revisit the SLA for unsized tickets.** They're judged as {ipr.ASSUMED_SIZE}, but typically take "
                        f"{work:.0f} business days of work, like {closest}. Either treat them as {closest} or start the SLA "
                        "clock only once a ticket is sized.")
    xl = outcomes[outcomes["Size"] == "XL"]
    if not xl.empty and xl["Completed"].iloc[0] and xl["SLA Met %"].iloc[0] is not None:
        others = outcomes[outcomes["Size"].isin(["Small", "Medium"])]["SLA Met %"].dropna()
        if len(others) and xl["SLA Met %"].iloc[0] < others.min():
            recs.append(f"**Split XL work.** Only {int(xl['Completed'].iloc[0])} XL tickets were completed in "
                        f"{OUTCOME_DAYS} days and {xl['SLA Met %'].iloc[0]:.0f}% met their SLA. Break XL into smaller "
                        "tickets that can each be delivered and tracked.")
    recs.append("**Write the sizing guide down.** Share the guide above with the team (business days of work per size) "
                "and use it in refinement, so a size means the same thing to everyone.")
    return recs


# ── Entry point ─────────────────────────────────────────────────────────────────

def build_estimated_size_distribution_visuals(df_issues: pd.DataFrame) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    pe = facts[facts["sla_applies"]].copy()
    if pe.empty:
        return _empty_payload("No Platform Engineering tickets found.")
    pe["sized"] = pe["size"].ne(UNSIZED)

    open_t = pe[~pe["is_closed"]].copy()
    recent = pe[pe["created_day"] > today - pd.Timedelta(days=RECENT_DAYS)]
    previous = pe[(pe["created_day"] > today - pd.Timedelta(days=2 * RECENT_DAYS))
                  & (pe["created_day"] <= today - pd.Timedelta(days=RECENT_DAYS))]

    done = pe[pe["outcome"].eq("Done") & (pe["closed_day"] > today - pd.Timedelta(days=OUTCOME_DAYS))].copy()
    done["work_bd"] = _work_bd(done, done["closed_day"], hol)
    done["lead_bd"] = ipr._busdays_between(done["created_day"], done["closed_day"], hol)
    done["met"] = (done["closed_day"] <= done["sla_due"]).where(done["sla_due"].notna())
    guide, guide_from_data = derive_guide(done)
    done["fit"] = [size_fit(s, w, guide) for s, w in zip(done["size"], done["work_bd"])]
    judged = done[done["fit"].notna()]

    # Typical work per size (unsized: its own typical), for "work queued".
    typical = done.groupby("size")["work_bd"].median()
    load = open_t[open_t["stage"].isin(["In Progress", "Backlog"])]
    queued_bd = float(load["size"].map(typical).fillna(typical.get(UNSIZED, 2)).sum())

    def share(frame):
        return float(frame["sized"].mean()) if len(frame) else None

    payload = _empty_payload()
    payload["kpis"] = {
        "coverage_open": share(open_t), "open_total": int(len(open_t)), "open_unsized": int((~open_t["sized"]).sum()),
        "coverage_new": share(recent), "coverage_new_delta": (share(recent) - share(previous))
        if share(recent) is not None and share(previous) is not None else None, "new_total": int(len(recent)),
        "accuracy": float((judged["fit"] == "Right size").mean()) if len(judged) else None,
        "undersized": float((judged["fit"] == "Undersized").mean()) if len(judged) else None,
        "oversized": float((judged["fit"] == "Oversized").mean()) if len(judged) else None,
        "accuracy_n": int(len(judged)), "queued_bd": queued_bd, "target": COVERAGE_TARGET,
    }
    payload["guide"] = guide
    payload["guide_from_data"] = guide_from_data

    payload["adoption_fig"] = _adoption_figure(pe, today)
    window = pe[pe["created_day"] > today - pd.Timedelta(days=BREAKDOWN_DAYS)]
    payload["coverage_figs"] = {label: fig for label, fig in (
        ("Business Lead", _coverage_bar(window, "lead")), ("Assignee", _coverage_bar(window, "assignee_name")),
        ("Issue Type", _coverage_bar(window, "issuetype"))) if fig is not None}
    payload["accuracy_fig"] = _accuracy_figure(done, guide)

    guide_rows, outcome_rows = [], []
    for size in SIZES + [UNSIZED]:
        rows = done[done["size"] == size]
        fits = rows["fit"].value_counts(normalize=True)
        if size in SIZES:
            guide_rows.append({
                "Size": size, "Guide": guide_text(size, guide), "Completed": int(len(rows)),
                "Typical (median, bd)": rows["work_bd"].median(), "85% within (bd)": rows["work_bd"].quantile(0.85),
                "Right Size %": round(fits.get("Right size", 0) * 100), "Undersized %": round(fits.get("Undersized", 0) * 100),
                "Oversized %": round(fits.get("Oversized", 0) * 100),
            })
        outcome_rows.append({
            "Size": SIZE_LABELS[size], "Completed": int(len(rows)),
            "SLA Met %": round(float(rows["met"].mean()) * 100) if rows["met"].notna().any() else None,
            "SLA Sample": int(rows["met"].notna().sum()),
            "Lead Time P50 (bd)": rows["lead_bd"].median(), "Work P50 (bd)": rows["work_bd"].median(),
        })
    payload["guide_df"] = pd.DataFrame(guide_rows).round(1)
    payload["outcomes_df"] = pd.DataFrame(outcome_rows).round(1)
    payload["outcome_fig"] = _outcome_figure(payload["outcomes_df"])
    payload["recommendations"] = _recommendations(payload["kpis"], guide, payload["guide_df"], payload["outcomes_df"], window)
    payload["load_fig"] = _load_figure(open_t)

    needs = open_t[~open_t["sized"]].sort_values("age_days", ascending=False)
    summary = lambda f: f["summary"].fillna("").astype(str).str[:100] if "summary" in f.columns else ""  # noqa: E731
    payload["needs_size_df"] = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + needs["key"].astype(str), "Stage": needs["stage"], "Jira Status": needs["status"],
        "Priority": needs["priority_bucket"], "Assignee": needs["assignee_name"], "Business Lead": needs["lead"],
        "Days Open": needs["age_days"].astype(int), "Summary": summary(needs),
    })

    wip = open_t[open_t["stage"].eq("In Progress") & open_t["sized"]].copy()
    wip["elapsed_bd"] = _work_bd(wip, pd.Series(today, index=wip.index), hol)
    running_long = pd.Series([size_fit(s, w, guide) == "Undersized" for s, w in zip(wip["size"], wip["elapsed_bd"])],
                             index=wip.index, dtype=bool)
    wip = wip[running_long]
    wip = wip.sort_values("elapsed_bd", ascending=False)
    payload["undersized_df"] = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + wip["key"].astype(str), "Size": wip["size"],
        "Guide": wip["size"].map(lambda s: guide_text(s, guide)), "In Progress (bd)": wip["elapsed_bd"].astype("Int64"),
        "Suggested Size": [_size_for(w, guide) for w in wip["elapsed_bd"]], "Priority": wip["priority_bucket"],
        "Assignee": wip["assignee_name"], "Business Lead": wip["lead"], "Summary": summary(wip),
    })

    detail = open_t[open_t["stage"].isin(["In Progress", "Backlog"])].sort_values(["stage", "size"])
    payload["detail_df"] = pd.DataFrame({
        "Ticket": JIRA_BROWSE_BASE_URL + detail["key"].astype(str), "Stage": detail["stage"],
        "Size": detail["size"].map(SIZE_LABELS), "Priority": detail["priority_bucket"],
        "Assignee": detail["assignee_name"], "Business Lead": detail["lead"], "Days Open": detail["age_days"].astype(int),
        "Summary": summary(detail),
    })
    return payload
