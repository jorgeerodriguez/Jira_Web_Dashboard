"""Apparent Tardiness Cost (ATC) ticket sequencing (Vepsalainen & Morton, 1987).

Shared by the Personal Dashboard (suggested work order) and the Backlog report (projected start
dates per assignee queue), so both order a person's tickets the same way. See the README section
"How ticket sequencing works (Apparent Tardiness Cost)".
"""
import numpy as np
import pandas as pd


def _normalize_text(value: str) -> str:
    return str(value).strip().casefold().replace("_", " ").replace("-", " ")


# Apparent Tardiness Cost (Vepsalainen & Morton, 1987) sequencing inputs.
ATC_SIZE_EFFORT_DAYS = {"Small": 1, "Medium": 3, "Large": 5, "XL": 10}
ATC_DEFAULT_EFFORT_DAYS = 2  # Unestimated / no size
ATC_PRIORITY_BASE_WEIGHT = {"no priority": 1, "low": 2, "medium": 4, "high": 8, "urgent": 16, "critical": 16}
ATC_URGENT_PRIORITIES = {"urgent", "critical"}  # always-first tier, soonest due date first; weight shown for reference only
ATC_DEFAULT_DAYS_LEFT = 30  # ticket has no target date
ATC_K = 2.0  # look-ahead sensitivity (typical range 1.5-3)


ATC_HELD_BACK_STATUSES = {"on hold", "blocked"}  # excluded from scoring, appended at the end
ATC_HELD_BACK_TIER_ORDER = ["On Hold", "Blocked"]  # On Hold first, then Blocked


def build_atc_sequence(pool_df: pd.DataFrame) -> pd.DataFrame:
    """Order open tickets by the Apparent Tardiness Cost rule.

    "Validating" tickets are dropped entirely — they're waiting on the end user,
    not on the assignee, so they shouldn't consume a slot in the sequence.
    "On Hold" and "Blocked" tickets can't be actively worked right now, so they're
    held out of ATC scoring and appended at the end instead (On Hold first, then
    Blocked, each soonest due date first) rather than competing for a slot.

    Urgent/Critical priority tickets among the schedulable ones are a separate
    first tier (soonest due date first) so a big Urgent ticket can't lose to a
    small Medium one on value per day. Everything else is ordered by
    Score = (w / p) * exp(-slack / (K * p_bar)), recomputed after each pick since
    p_bar and the clock both move.
    Expects pool_df with "Ticket", "Priority", "Size", "Days Left", "Days Old",
    "Status" columns.
    """
    if pool_df is None or pool_df.empty:
        return pd.DataFrame()

    status_norm = pool_df["Status"].astype(str).map(_normalize_text)
    rows = pool_df[~status_norm.eq("validating")].copy()
    if rows.empty:
        return pd.DataFrame()

    status_norm = rows["Status"].astype(str).map(_normalize_text)
    priority_norm = rows["Priority"].astype(str).map(_normalize_text)

    rows["_effort"] = rows["Size"].astype(str).map(ATC_SIZE_EFFORT_DAYS).fillna(ATC_DEFAULT_EFFORT_DAYS)
    rows["_due"] = pd.to_numeric(rows["Days Left"], errors="coerce").fillna(ATC_DEFAULT_DAYS_LEFT)
    base_weight = priority_norm.map(ATC_PRIORITY_BASE_WEIGHT).fillna(ATC_PRIORITY_BASE_WEIGHT["no priority"])
    days_old = pd.to_numeric(rows["Days Old"], errors="coerce").fillna(0)
    rows["_weight"] = base_weight * (1 + days_old / 30.0)
    rows["_urgent_tier"] = priority_norm.isin(ATC_URGENT_PRIORITIES)
    rows["_held_back_tier"] = status_norm.map(
        {"on hold": "On Hold", "blocked": "Blocked"}
    )

    held_back = rows[rows["_held_back_tier"].notna()]
    schedulable = rows[rows["_held_back_tier"].isna()].copy()

    urgent = schedulable[schedulable["_urgent_tier"]].sort_values("_due", ascending=True)
    remaining = schedulable[~schedulable["_urgent_tier"]].copy()

    sequence_rows = []
    t = 0.0

    def _append(r: pd.Series, tier: str, score) -> None:
        nonlocal t
        p = float(r["_effort"])
        start, finish = t, t + p
        sequence_rows.append({
            "Seq": len(sequence_rows) + 1,
            "Ticket": r["Ticket"],
            "Priority": r["Priority"],
            "Size": r["Size"],
            "Effort (Days)": p,
            "Weight": round(float(r["_weight"]), 2),
            "Days Left": round(float(r["_due"]), 1),
            "ATC Score": round(score, 3) if score is not None else None,
            "Tier": tier,
            "Projected Start (Day)": round(start, 1),
            "Projected Finish (Day)": round(finish, 1),
            "Projected Tardiness (Days)": round(max(finish - float(r["_due"]), 0.0), 1),
        })
        t = finish

    for _, r in urgent.iterrows():
        _append(r, "Urgent", None)

    remaining_idx = list(remaining.index)
    while remaining_idx:
        p_bar = float(remaining.loc[remaining_idx, "_effort"].mean()) or 1.0

        best_idx, best_score = None, -np.inf
        for idx in remaining_idx:
            r = remaining.loc[idx]
            p, d, w = float(r["_effort"]), float(r["_due"]), float(r["_weight"])
            slack = max(d - p - t, 0.0)
            score = (w / p) * np.exp(-slack / (ATC_K * p_bar)) if p > 0 else 0.0
            if score > best_score:
                best_idx, best_score = idx, score

        _append(remaining.loc[best_idx], "ATC", best_score)
        remaining_idx.remove(best_idx)

    for tier in ATC_HELD_BACK_TIER_ORDER:
        tier_rows = held_back[held_back["_held_back_tier"].eq(tier)].sort_values("_due", ascending=True)
        for _, r in tier_rows.iterrows():
            _append(r, tier, None)

    return pd.DataFrame(sequence_rows)


# Calendar cell codes, low -> high severity (drives the discrete Heatmap colorscale).
