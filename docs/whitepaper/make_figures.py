"""Figures for the technical white paper, computed with the dashboard's own code.

Usage: .venv/bin/python docs/whitepaper/make_figures.py <issues.pkl> [<backlog_start_backtest.pkl>]

Every chart is aggregated; no individual is named. Output: docs/whitepaper/figures/*.svg and
docs/whitepaper/figures/numbers.json (the figures quoted in the text).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from reports import backlog_report as br  # noqa: E402
from reports import domains  # noqa: E402
from reports import estimated_size_distribution_report as sz  # noqa: E402
from reports import executive_summary as es  # noqa: E402
from reports import forecast_report as fr  # noqa: E402
from reports import in_progress_report as ipr  # noqa: E402
from reports import service_level_agreement_report as sla  # noqa: E402
from reports import word_of_the_month_report as wom  # noqa: E402
from reports.atc_sequence import build_atc_sequence  # noqa: E402

OUT = Path(__file__).resolve().parent / "figures"
OUT.mkdir(parents=True, exist_ok=True)

C1, C2, C3 = "#2a78d6", "#eb6834", "#1baa8a"     # categorical slots (same as the dashboard)
INK, MUTED, GRID = "#1f2937", "#64748b", "#e2e8f0"
RISK = {"On Track": "#0ca30c", "At Risk": "#fab219", "Likely Late": "#ec835a", "Breached": "#d03b3b"}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold",
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "svg.fonttype": "none", "figure.dpi": 110,
})
NUM: dict = {}


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


df = pd.read_pickle(sys.argv[1])
today = ipr.today_local()
hol = ipr._calendar_holidays(today)
facts = es._facts(df, today, hol)
pe = facts[facts["sla_applies"]].copy()
NUM["as_of"] = str(today.date())
NUM["rows_fetched"] = int(len(df))
NUM["pe_tickets"] = int(len(pe))
NUM["open_pe"] = int((~pe["is_closed"]).sum())
NUM["human_comments"] = int(df["comments"].map(len).sum())
NUM["bot_comments"] = int(df["bot_comment_count"].sum())

# ── F1 Delivery forecast ───────────────────────────────────────────────────────
weeks, delivered, requested, _ = fr.weekly_series(df)
records = fr.backtest(delivered)
fc = fr.delivery_forecast(delivered, records=records)
fig, ax = plt.subplots(figsize=(7.2, 3.0))
hx, hy = weeks[-26:], delivered[-26:]
ax.plot(hx, hy, color=C1, lw=2, marker="o", ms=3.5, label="Delivered (actual)")
f12 = fc[fc["h"] <= 12]
future = pd.date_range(weeks[-1] + pd.Timedelta(days=7), periods=12, freq="W-MON")
wl = f12["low"].diff().fillna(f12["low"]).clip(lower=0)
wh = f12["high"].diff().fillna(f12["high"])
ax.fill_between(future, np.minimum(wl, f12["weekly_likely"]), np.maximum(wh, f12["weekly_likely"]),
                color=C2, alpha=0.18, lw=0, label="Calibrated range (P10–P90 of past errors)")
ax.plot(future, f12["weekly_likely"], color=C2, lw=2, ls="--", marker="o", ms=3.5, label="Damped-trend forecast")
ax.axvline(future[0] - pd.Timedelta(days=3.5), color=MUTED, ls=":", lw=1)
ax.set_ylabel("Tickets per week")
ax.legend(loc="upper left", fontsize=8)
ax.set_title("Weekly PE delivery: 26 weeks of history and a 12-week forecast")
save(fig, "f_forecast")
NUM["forecast"] = {int(h): {"likely": round(float(fc.loc[fc.h == h, "likely"].iloc[0])),
                            "low": round(float(fc.loc[fc.h == h, "low"].iloc[0])),
                            "high": round(float(fc.loc[fc.h == h, "high"].iloc[0]))} for h in (4, 8, 12)}

# ── F2 Back-test: coverage and error by horizon ─────────────────────────────────
cov, err = [], []
for h in range(1, 13):
    acc = fr.accuracy(records, h=h)
    cov.append(acc["inside"].mean() if len(acc) else np.nan)
    err.append(acc["abs_pct_error"].median() if len(acc) else np.nan)
NUM["backtest"] = {h: {"coverage": round(float(cov[h - 1]), 2), "median_ape": round(float(err[h - 1]), 2)} for h in (4, 8, 12)}
NUM["backtest_rows"] = int(len(records))
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))
hs = np.arange(1, 13)
axes[0].plot(hs, np.array(cov) * 100, color=C1, lw=2, marker="o", ms=3.5)
axes[0].axhline(80, color=MUTED, ls="--", lw=1)
axes[0].text(12, 81, "nominal 80%", ha="right", color=MUTED, fontsize=8)
axes[0].set_ylim(0, 100)
axes[0].set_xlabel("Horizon (weeks)")
axes[0].set_ylabel("Actual inside range (%)")
axes[0].set_title("Out-of-sample coverage")
axes[1].plot(hs, np.array(err) * 100, color=C2, lw=2, marker="o", ms=3.5)
axes[1].set_ylim(0, None)
axes[1].set_xlabel("Horizon (weeks)")
axes[1].set_ylabel("Median |error| (%)")
axes[1].set_title("Accuracy of the central forecast")
save(fig, "f_backtest")

# ── F3 Execution time by size (log scale) ───────────────────────────────────────
tickets = ipr.prepare_tickets(df)
history = ipr._build_history(tickets, today, hol)
NUM["history_rows"] = int(len(history))
fig, ax = plt.subplots(figsize=(7.2, 2.8))
order = ["Small", "Medium", "Large", "XL", "Unestimated"]
data = [history.loc[history["size"] == s, "duration_bd"].values + 1 for s in order]
bp = ax.boxplot(data, vert=False, widths=0.55, patch_artist=True, showfliers=False,
                medianprops=dict(color=INK, lw=2))
for patch in bp["boxes"]:
    patch.set(facecolor=C1, alpha=0.25, edgecolor=C1)
rng = np.random.default_rng(1)
for i, d in enumerate(data, start=1):
    ax.scatter(d, i + rng.uniform(-0.18, 0.18, len(d)), s=4, color=C1, alpha=0.35, lw=0)
ax.set_xscale("log")
ax.set_yticks(range(1, 6), [f"{s} (n={len(d)})" for s, d in zip(order, data)])
ax.set_xlabel("Business days from Target start to Done, + 1 (log scale)")
ax.set_title("Execution time is right-skewed: the model works on log(1 + days)")
save(fig, "f_duration")

# ── F4 Conditioning on time already spent ───────────────────────────────────────
ref, basis = ipr._reference_set(history, "Medium", "Small")
dur = np.expm1(ref["log_duration"].values)
w = ref["weight"].values
fig, ax = plt.subplots(figsize=(7.2, 2.7))
grid = np.arange(0, 41)
for elapsed, color, label in [(0, C1, "Not started (elapsed = 0)"), (5, C2, "Elapsed = 5 bd"), (12, C3, "Elapsed = 12 bd")]:
    m = dur > elapsed
    rem, ww = dur[m] - elapsed, w[m]
    cdf = [ww[rem <= g].sum() / ww.sum() for g in grid]
    ax.step(grid, np.array(cdf) * 100, where="post", color=color, lw=2, label=label)
    p50 = ipr._weighted_quantile(rem, ww, 0.5)
    p85 = ipr._weighted_quantile(rem, ww, 0.85)
    ax.plot([p50, p85], [50, 85], "o", color=color, ms=5)
ax.axhline(50, color=MUTED, lw=0.8, ls=":")
ax.axhline(85, color=MUTED, lw=0.8, ls=":")
ax.text(40, 51, "P50", ha="right", color=MUTED, fontsize=8)
ax.text(40, 86, "P85", ha="right", color=MUTED, fontsize=8)
ax.set_xlabel("Remaining business days")
ax.set_ylabel("Share finished (%)")
ax.set_title(f"Remaining-time distribution, conditioned on elapsed time ({basis}, n={len(dur)})")
ax.legend(loc="lower right", fontsize=8)
save(fig, "f_conditional")

# ── F5 Recency weight and shrinkage ─────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.5))
age = np.arange(0, 366)
axes[0].plot(age, 0.5 ** (age / 120), color=C1, lw=2)
axes[0].set_xlabel("Days since the ticket finished")
axes[0].set_ylabel("Weight")
axes[0].set_title("Recency weight, half-life 120 days")
n = np.linspace(0, 40, 200)
axes[1].plot(n, n / (n + 5), color=C2, lw=2)
axes[1].set_xlabel("Effective tickets for the assignee (Σw)")
axes[1].set_ylabel("Share of own effect kept")
axes[1].set_title("Empirical-Bayes shrinkage, k = 5")
axes[1].set_ylim(0, 1)
save(fig, "f_weights")

# ── F6 ATC worked example ───────────────────────────────────────────────────────
ex = pd.DataFrame({
    "Ticket": ["T1", "T2", "T3", "T4", "T5", "T6"],
    "Priority": ["High", "Medium", "Low", "None", "High", "Urgent"],
    "Size": ["XL", "Small", "Medium", "Unestimated", "Large", "Small"],
    "Days Left": [20, 2, 6, 30, 7, 1], "Days Old": [0, 0, 40, 60, 0, 0], "Status": "To Do"})
atc = build_atc_sequence(ex)
prank = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3, "None": 4}
simple = ex.assign(_p=ex["Priority"].map(prank)).sort_values(["_p", "Days Left"])
eff = {"Small": 1, "Medium": 3, "Large": 5, "XL": 10, "Unestimated": 2}
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6), sharex=True)
for ax, seq, title in [(axes[0], list(atc["Ticket"]), "ATC order"), (axes[1], list(simple["Ticket"]), "Priority, then due date")]:
    t, late_total = 0, 0
    for i, k in enumerate(seq):
        row = ex[ex["Ticket"] == k].iloc[0]
        p = eff[row["Size"]]
        late = max(t + p - row["Days Left"], 0)
        late_total += late * 1
        ax.barh(i, p, left=t, color=RISK["Breached"] if late else C1, alpha=0.85, edgecolor="white", lw=2)
        ax.plot(row["Days Left"], i, marker="|", color=INK, ms=12, mew=2)
        ax.text(t + p / 2, i, k, ha="center", va="center", color="white", fontsize=8, fontweight="bold")
        t += p
    ax.set_yticks([])
    ax.invert_yaxis()
    ax.set_xlabel("Working days")
    ax.set_title(f"{title}: {late_total:.0f} days late in total")
save(fig, "f_atc")

# ── F7 Monte Carlo queue (illustrative engineer, real duration model) ───────────
rng = np.random.default_rng(br.RNG_SEED)
_, effects = ipr._assignee_effects(history)


def dist(priority, size, elapsed=0.0):
    row = pd.Series({"elapsed_bd": elapsed, "priority_bucket": priority, "size": size, "assignee_name": "_"})
    return ipr._remaining_distribution(row, history, effects, {})


ip = [ipr.sample_remaining(dist("High", "Medium", 4), rng, br.SIMULATIONS),
      ipr.sample_remaining(dist("Medium", "Small", 2), rng, br.SIMULATIONS)]
queue_spec = [("High", "Small"), ("Medium", "Medium"), ("Medium", "Small"), ("Low/None", "Large"), ("Medium", "Small")]
qs = [ipr.sample_remaining(dist(p, s), rng, br.SIMULATIONS) for p, s in queue_spec]
starts, finishes, clear = br._simulate_queue(ip, qs, [0, 0, 3, 0, 0], slots=2)
fig, ax = plt.subplots(figsize=(7.2, 2.9))
for i in range(len(qs)):
    s50, s85 = np.quantile(starts[i], [0.5, 0.85])
    f50, f85 = np.quantile(finishes[i], [0.5, 0.85])
    ax.barh(i, f85 - s50, left=s50, color=C1, alpha=0.18, edgecolor=C1)
    ax.barh(i, f50 - s50, left=s50, color=C1, alpha=0.55)
    ax.plot(s50, i, "o", color=INK, ms=4)
    ax.plot(f85, i, "|", color=RISK["Breached"], ms=12, mew=2)
ax.set_yticks(range(len(qs)), [f"#{i+1} {p} · {s}" for i, (p, s) in enumerate(queue_spec)])
ax.invert_yaxis()
ax.set_xlabel("Business days from today")
ax.set_title("Queue simulation, 1,000 runs, 2 slots: start (●), likely finish (dark), P85 finish (|)")
save(fig, "f_queue")

# ── F8 Backlog start back-test ───────────────────────────────────────────────────
if len(sys.argv) > 2:
    bt = pd.read_pickle(sys.argv[2])
    bt = bt[bt["started"]].copy()
    for c in ("p50", "actual", "target"):
        bt[c] = pd.to_datetime(bt[c])
    ok = bt["p50"].notna() & bt["actual"].notna()
    e = np.busday_count(bt.loc[ok, "p50"].values.astype("datetime64[D]"), bt.loc[ok, "actual"].values.astype("datetime64[D]"), holidays=hol)
    okt = bt["target"].notna() & bt["actual"].notna()
    et = np.busday_count(bt.loc[okt, "target"].values.astype("datetime64[D]"), bt.loc[okt, "actual"].values.astype("datetime64[D]"), holidays=hol)
    NUM["start_bt"] = {"n": int(ok.sum()), "median_err": float(np.median(e)), "mae": float(np.median(np.abs(e))),
                       "target_median_err": float(np.median(et)), "n_target": int(okt.sum())}
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    bins = np.arange(-40, 41, 2)
    ax.hist(np.clip(e, -40, 40), bins=bins, color=C1, alpha=0.7, label=f"Projected Start (P50), n={ok.sum()}")
    ax.hist(np.clip(et, -40, 40), bins=bins, histtype="step", color=C2, lw=2, label=f"Target start in Jira, n={okt.sum()}")
    ax.axvline(0, color=INK, lw=1)
    ax.set_xlabel("Actual start − predicted start (business days; positive = started later)")
    ax.set_ylabel("Predictions")
    ax.set_title("Back-test of start dates (5 origins, Jul–Sep 2026)")
    ax.legend(fontsize=8)
    save(fig, "f_start_bt")

# ── F9 Sizing guide ───────────────────────────────────────────────────────────────
done = facts[facts["outcome"].eq("Done") & facts["sla_applies"]
             & (facts["closed_day"] >= today - pd.Timedelta(days=sz.OUTCOME_DAYS))].copy()
done["work_bd"] = sz._work_bd(done, done["closed_day"], hol)
done = done[done["work_bd"].notna()]
guide, from_data = sz.derive_guide(done)
NUM["guide"] = {k: list(v) for k, v in guide.items()}
fit = [sz.size_fit(s, w_, guide) for s, w_ in zip(done["size"], done["work_bd"])]
fit = pd.Series(fit).dropna()
NUM["size_accuracy"] = round(float((fit == "Right size").mean()), 2)
fig, ax = plt.subplots(figsize=(7.2, 2.7))
for i, s in enumerate(sz.SIZES):
    lo, hi = guide[s]
    ax.add_patch(plt.Rectangle((lo - 0.5, i - 0.4), ((hi if hi is not None else 60) - lo + 1), 0.8, color=C1, alpha=0.10, lw=0))
    vals = done.loc[done["size"] == s, "work_bd"].clip(upper=60)
    ax.scatter(vals, i + rng.uniform(-0.25, 0.25, len(vals)), s=6, color=C1, alpha=0.45, lw=0)
    if len(vals):
        ax.plot(vals.median(), i, "D", color=C2, ms=6)
ax.set_yticks(range(4), [f"{s} ({sz.guide_text(s, guide)})" for s in sz.SIZES])
ax.set_xlim(-1, 61)
ax.invert_yaxis()
ax.set_xlabel("Business days of work (clipped at 60)")
ax.set_title("Completed work vs the data-derived sizing guide (shaded); ◆ = median")
save(fig, "f_sizing")

# ── F10 Friction themes ───────────────────────────────────────────────────────────
conv = wom.build_word_of_the_month_visuals(df)
th = conv["themes_df"].sort_values("Total extra days")
NUM["themes_period"] = f"{conv['start_month']} to {conv['end_month']}"
NUM["coverage"] = conv.get("coverage")
NUM["first_reply_hours"] = conv.get("first_reply_hours")
fig, ax = plt.subplots(figsize=(7.2, 2.7))
colors = [MUTED if k == "Symptom" else C2 for k in th["Kind"]]
ax.barh(th["Theme"], th["Total extra days"], color=colors)
for y, (v, n_) in enumerate(zip(th["Total extra days"], th["Tickets"])):
    ax.text(v, y, f"  {v:.0f} bd · {n_} tickets", va="center", fontsize=8, color=INK)
ax.set_xlabel("Total extra business days vs similar tickets without the theme")
ax.set_title(f"Friction themes in human comments ({NUM['themes_period']}); grey = symptom")
ax.set_xlim(0, max(th["Total extra days"].max() * 1.35, 1))
save(fig, "f_themes")

# ── F11 SLA breach rates ─────────────────────────────────────────────────────────
j = sla._judged(facts, today)
months = pd.period_range(today.to_period("M") - 11, today.to_period("M") - 1, freq="M")
due_r, done_r = [], []
for m in months:
    a, b = m.start_time, (m + 1).start_time
    r = sla._rates(j, a, b)
    due_r.append(r["due_rate"])
    done_r.append(r["done_rate"])
fig, ax = plt.subplots(figsize=(7.2, 2.6))
x = [m.strftime("%b %y") for m in months]
ax.plot(x, np.array(due_r, dtype=float) * 100, color=C1, lw=2, marker="o", ms=3.5, label="SLA came due in month (strict)")
ax.plot(x, np.array(done_r, dtype=float) * 100, color=C2, lw=2, marker="o", ms=3.5, label="Completed late in month")
ax.axhline(10, color=MUTED, ls="--", lw=1)
ax.text(len(x) - 1, 10.5, "goal < 10%", ha="right", color=MUTED, fontsize=8)
ax.set_ylabel("Breach rate (%)")
ax.set_ylim(0, None)
ax.legend(fontsize=8)
ax.set_title("Monthly SLA breach rate, two definitions")
save(fig, "f_sla")

# ── F12 Lead time promise ─────────────────────────────────────────────────────────
recent = facts[facts["outcome"].eq("Done") & facts["sla_applies"] & (facts["closed_day"] >= today - pd.Timedelta(days=90))]
lead = ipr._busdays_between(recent["created_day"], recent["closed_day"], hol).dropna()
q = np.quantile(lead, [0.5, 0.85, 0.95])
NUM["lead"] = {"n": int(len(lead)), "p50": float(q[0]), "p85": float(q[1]), "p95": float(q[2])}
fig, ax = plt.subplots(figsize=(7.2, 2.5))
ax.hist(lead.clip(upper=80), bins=np.arange(0, 82, 2), color=C1, alpha=0.7)
for v, lab in zip(q, ["50%", "85%", "95%"]):
    ax.axvline(v, color=INK, ls="--", lw=1)
    ax.text(v, ax.get_ylim()[1] * 0.92, f" {lab}: {v:.0f} bd", fontsize=8, color=INK)
ax.set_xlabel("Lead time, created → Done (business days, clipped at 80)")
ax.set_ylabel("Tickets")
ax.set_title(f"Lead-time distribution, tickets completed in the last 90 days (n={len(lead)})")
save(fig, "f_lead")

# ── F13 Domain coverage ───────────────────────────────────────────────────────────
text = pe.apply(lambda r: f"{r.get('summary') or ''} " + " ".join(c.get("body", "") for c in (r["comments"] if isinstance(r["comments"], list) else [])), axis=1)
prim = text.map(lambda s: domains.primary(domains.tag(s)))
title_only = pe["summary"].fillna("").map(lambda s: domains.primary(domains.tag(s)))
NUM["domain_tagged"] = round(float(prim.notna().mean()), 2)
NUM["domain_tagged_title"] = round(float(title_only.notna().mean()), 2)
NUM["domains"] = len(domains.DOMAIN_PATTERNS)
top = prim.value_counts().head(14).sort_values()
fig, ax = plt.subplots(figsize=(7.2, 3.0))
ax.barh(top.index, top.values, color=[C1 if domains.group_of(d) == "GCP" else C2 if domains.group_of(d) == "AWS" else MUTED for d in top.index])
ax.set_xlabel("PE tickets (primary domain)")
ax.set_title("Primary domain of PE tickets (blue = GCP, orange = AWS, grey = other)")
save(fig, "f_domains")

# ── F14 Score decomposition (illustrative) ────────────────────────────────────────
people = ["Engineer A", "Engineer B", "Engineer C", "Engineer D", "Engineer E"]
expertise = np.array([1.0, 0.45, 0.10, 0.0, 0.30])
finish = np.array([24, 9, 6, 12, 14.0])
fits = finish <= 15
avail = 1 - finish / finish.max()
penalty = np.array([0.15, 0.0, 0.08, 0.0, 0.0])
score = 0.6 * expertise + 0.4 * avail - penalty
fig, ax = plt.subplots(figsize=(7.2, 2.6))
y = np.arange(len(people))
ax.barh(y, 0.6 * expertise, color=C1, label="0.6 × expertise")
ax.barh(y, 0.4 * avail, left=0.6 * expertise, color=C2, label="0.4 × availability")
ax.barh(y, -penalty, color=MUTED, label="− load / balance penalty")
for i in y:
    ax.text(max(0.6 * expertise[i] + 0.4 * avail[i], 0) + 0.02, i,
            f"score {score[i]:.2f} · P85 finish {finish[i]:.0f} bd · {'fits SLA' if fits[i] else 'misses SLA'}",
            va="center", fontsize=8, color=INK if fits[i] else RISK["Breached"])
ax.set_yticks(y, people)
ax.invert_yaxis()
ax.set_xlim(-0.25, 1.6)
ax.axvline(0, color=INK, lw=0.8)
ax.legend(fontsize=8, loc="lower right")
ax.set_title("Scoring one ticket (illustrative): SLA fit first, then score")
save(fig, "f_score")

(OUT / "numbers.json").write_text(json.dumps(NUM, indent=2, default=str))
print(json.dumps(NUM, indent=2, default=str))
