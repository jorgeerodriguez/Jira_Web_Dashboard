"""Shared analysis of Jira change history (data/fetch_change_history.py).

Used by the Trend page (Target date changes), the SLA page (SLA clock set after the fact) and the
Validating page (time waiting in Validating, rework). Every function accepts an empty history and returns
empty results, so the pages work (with a note) when the history could not be loaded.

Definitions
- A Target date change is *set* (empty -> date), *moved* (date -> another date) or *cleared* (date -> empty).
  Jira does not log the values a ticket was created with, so a date filled in on the create screen has no
  history and is never counted as a change.
- A move is *after the date passed* when it is made after the old date (re-planning once the date was
  already missed, rather than ahead of time).
- *SLA clock set after the fact*: the ticket's Target start was set or moved on or after the day the
  ticket moved to Done. The SLA clock starts at Target start, so such a ticket's SLA result was decided
  after the work was finished and is not a real measurement.
- A *status episode* is one stay in a status: from entering it to the next status change (open when the
  ticket is still there).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from reports import in_progress_report as ipr

START, END = "target_start", "target_end"
DATE_FIELDS = (START, END)
FIELD_LABELS = {START: "Target start", END: "Target end"}


def _local_day(at: pd.Series) -> pd.Series:
    return pd.to_datetime(at, utc=True, errors="coerce").dt.tz_convert(ipr.LOCAL_TZ).dt.tz_localize(None).dt.normalize()


def _local_time(at: pd.Series) -> pd.Series:
    return pd.to_datetime(at, utc=True, errors="coerce").dt.tz_convert(ipr.LOCAL_TZ).dt.tz_localize(None)


def has_history(history) -> bool:
    return isinstance(history, pd.DataFrame) and not history.empty


# ── Target date changes ─────────────────────────────────────────────────────────

def date_changes(history: pd.DataFrame, hol: np.ndarray) -> pd.DataFrame:
    """One row per Target start / Target end change: kind, old/new dates, shift and timing."""
    cols = ["key", "field", "day", "author", "old", "new", "kind", "shift_bd", "after_passed"]
    if not has_history(history):
        return pd.DataFrame(columns=cols)
    d = history[history["field"].isin(DATE_FIELDS)].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)
    d["day"] = _local_day(d["at"])
    d["old"] = pd.to_datetime(d["frm"], format="%Y-%m-%d", errors="coerce")
    d["new"] = pd.to_datetime(d["to"], format="%Y-%m-%d", errors="coerce")
    d["kind"] = np.select([d["old"].isna() & d["new"].notna(), d["old"].notna() & d["new"].isna(),
                           d["old"].notna() & d["new"].notna()], ["set", "cleared", "moved"], "other")
    d = d[d["kind"] != "other"]
    moved = d["kind"].eq("moved")
    d["shift_bd"] = np.nan
    if moved.any():
        d.loc[moved, "shift_bd"] = np.busday_count(d.loc[moved, "old"].values.astype("datetime64[D]"),
                                                   d.loc[moved, "new"].values.astype("datetime64[D]"), holidays=hol)
    d["after_passed"] = moved & (d["day"] > d["old"])
    return d[cols].reset_index(drop=True)


def ticket_date_summary(changes: pd.DataFrame, facts: pd.DataFrame) -> pd.DataFrame:
    """Per ticket (index = key): moves of each date, moves after the date passed, and clock-after-the-fact."""
    keys = facts["key"]
    out = pd.DataFrame(index=pd.Index(keys, name="key"))
    if changes.empty:
        for col in ("moves", "start_moves", "end_moves", "late_moves", "changes"):
            out[col] = 0
        out["clock_after_fact"] = False
        out["clock_changed_on"] = pd.NaT
        return out
    moved = changes[changes["kind"].eq("moved")]
    out["moves"] = moved.groupby("key").size().reindex(out.index, fill_value=0)
    out["start_moves"] = moved[moved["field"].eq(START)].groupby("key").size().reindex(out.index, fill_value=0)
    out["end_moves"] = moved[moved["field"].eq(END)].groupby("key").size().reindex(out.index, fill_value=0)
    out["late_moves"] = moved[moved["after_passed"]].groupby("key").size().reindex(out.index, fill_value=0)
    out["changes"] = changes.groupby("key").size().reindex(out.index, fill_value=0)

    done_day = facts.set_index("key")["closed_day"].where(facts.set_index("key")["outcome"].eq("Done"))
    start_set = changes[changes["field"].eq(START) & changes["kind"].isin(["set", "moved"]) & changes["new"].notna()]
    start_set = start_set.assign(done_day=start_set["key"].map(done_day))
    after = start_set[start_set["done_day"].notna() & (start_set["day"] >= start_set["done_day"])]
    first_after = after.groupby("key")["day"].min()
    out["clock_after_fact"] = out.index.isin(first_after.index)
    out["clock_changed_on"] = first_after.reindex(out.index)
    return out


# ── Status episodes ─────────────────────────────────────────────────────────────

def status_episodes(history: pd.DataFrame, status: str, today: pd.Timestamp, hol: np.ndarray) -> pd.DataFrame:
    """Every stay in `status`: when it started and ended, where it came from and went, and its length."""
    cols = ["key", "entered", "left", "came_from", "exit_to", "exit_by", "open", "bd"]
    if not has_history(history):
        return pd.DataFrame(columns=cols)
    s = history[history["field"].eq("status")].sort_values(["key", "at"]).copy()
    if s.empty:
        return pd.DataFrame(columns=cols)
    s["when"] = _local_time(s["at"])
    s["next_when"] = s.groupby("key")["when"].shift(-1)
    s["next_to"] = s.groupby("key")["to"].shift(-1)
    s["next_by"] = s.groupby("key")["author"].shift(-1)
    target = str(status).strip().casefold()
    ep = s[s["to"].astype(str).str.strip().str.casefold().eq(target)].copy()
    if ep.empty:
        return pd.DataFrame(columns=cols)
    ep["open"] = ep["next_when"].isna()
    end = ep["next_when"].fillna(today)
    ep["bd"] = np.maximum(np.busday_count(ep["when"].values.astype("datetime64[D]"),
                                          end.values.astype("datetime64[D]"), holidays=hol), 0)
    out = pd.DataFrame({"key": ep["key"], "entered": ep["when"], "left": ep["next_when"], "came_from": ep["frm"],
                        "exit_to": ep["next_to"], "exit_by": ep["next_by"], "open": ep["open"], "bd": ep["bd"]})
    return out.reset_index(drop=True)
