"""Delivery Forecast: how many tickets Platform Engineering will likely deliver, with honest ranges.

PE tickets only (no Features, Initiatives or Release Management CAR tickets); delivered = moved to Done,
dated when it moved; full Monday-Sunday weeks only (the current week is never used).

Method (chosen by back-testing on the team's own history):
- Central forecast: a damped trend on the last TREND_WEEKS weeks of delivery. A straight line is fitted
  to log weekly throughput and projected forward with the slope shrinking each week (DAMPING), so
  growth is assumed to slow down rather than continue forever.
- Range: calibrated from the method's own past errors ("conformal" intervals). For each horizon, the
  forecast is re-run at every past week using only data known then and compared with what actually
  happened; the 10th and 90th percentiles of those errors set the range around the trend, which
  back-tests to covering about 7 in 10 outcomes. The central
  estimate stays on the trend: back-tested, shifting it by the median past error was no better
  calibrated and less accurate. When there is not enough history to calibrate, the range falls back to
  resampling recent weeks.

The same engine drives the Capacity page's "How much can we deliver?" cards and calculator.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go


TREND_WEEKS = 12          # weeks the trend is fitted on
DAMPING = 0.8             # each future week keeps 80% of the previous week's trend
HISTORY_WEEKS = 104
CHART_HISTORY_WEEKS = 26
FORECAST_WEEKS = 12       # shown on the charts; the calculator looks further ahead
MAX_WEEKS = 52
HORIZONS = (4, 8, 12)
CALIBRATION_ERRORS = 52   # most recent past errors used per horizon (about a year)
MIN_CALIBRATION = 12      # fewer than this: fall back to resampling recent weeks
FIRST_ORIGIN = 20         # back-tests start once there are 20 weeks of history
ACCURACY_HORIZON = 4
ACCURACY_ORIGINS = 40
# Range = 10th-90th percentile of past errors. Errors on neighbouring weeks overlap, so a nominal 80%
# band covers about 70% of outcomes when back-tested (out of sample) -- "about 7 in 10".
LOW_Q, HIGH_Q = 0.10, 0.90
RNG_SEED = 7

ACTUAL_COLOR, FORECAST_COLOR = "#2a78d6", "#eb6834"   # categorical slots 1 and 2
BAND = "rgba(235,104,52,0.18)"
INK = "#334155"
GRID = "rgba(148,163,184,0.25)"


# ── Engine ──────────────────────────────────────────────────────────────────────

def damped_trend(history: np.ndarray, weeks: int, window: int = TREND_WEEKS, damping: float = DAMPING) -> np.ndarray:
    """Weekly point forecasts for the next `weeks` weeks."""
    hist = np.asarray(history, dtype=float)
    if hist.size == 0:
        return np.zeros(weeks)
    if hist.size < 4:
        return np.full(weeks, float(hist.mean()))
    y = np.log1p(hist[-window:])
    x = np.arange(y.size)
    slope, intercept = np.polyfit(x, y, 1)
    level = intercept + slope * x[-1]
    steps = np.cumsum(damping ** np.arange(1, weeks + 1))
    return np.clip(np.expm1(level + slope * steps), 0, None)


def backtest(series: np.ndarray, max_weeks: int = FORECAST_WEEKS, first_origin: int = FIRST_ORIGIN) -> pd.DataFrame:
    """Re-run the forecast at every past week; one row per (origin, horizon) that can be scored."""
    series = np.asarray(series, dtype=float)
    rows = []
    for origin in range(first_origin, series.size):
        point = damped_trend(series[:origin], max_weeks)
        for h in range(1, max_weeks + 1):
            if origin + h > series.size:
                break
            predicted = float(point[:h].sum())
            actual = float(series[origin:origin + h].sum())
            rows.append({"origin": origin, "h": h, "predicted": predicted, "actual": actual,
                         "log_error": np.log(max(actual, 1.0) / max(predicted, 1.0))})
    return pd.DataFrame(rows, columns=["origin", "h", "predicted", "actual", "log_error"])


def _error_quantiles(errors: pd.Series) -> tuple[float, float] | None:
    if len(errors) < MIN_CALIBRATION:
        return None
    recent = errors.tail(CALIBRATION_ERRORS)
    return float(recent.quantile(LOW_Q)), float(recent.quantile(HIGH_Q))


def _bootstrap_range(history: np.ndarray, h: int, sims: int = 4000) -> tuple[float, float]:
    sample = np.asarray(history[-TREND_WEEKS:], dtype=float)
    if sample.size == 0:
        return 0.0, 0.0
    totals = np.random.default_rng(RNG_SEED).choice(sample, size=(sims, h)).sum(axis=1)
    return float(np.percentile(totals, LOW_Q * 100)), float(np.percentile(totals, HIGH_Q * 100))


def delivery_forecast(series: np.ndarray, max_weeks: int = MAX_WEEKS, records: pd.DataFrame | None = None) -> pd.DataFrame:
    """Cumulative forecast for weeks 1..max_weeks from now: likely (P50) and range (P15-P85).

    Horizons beyond the calibrated ones reuse the longest calibrated horizon's error spread.
    """
    series = np.asarray(series, dtype=float)
    records = backtest(series) if records is None else records
    point = damped_trend(series, max_weeks)
    cumulative = np.cumsum(point)
    rows = []
    for h in range(1, max_weeks + 1):
        cal_h = min(h, FORECAST_WEEKS)
        q = _error_quantiles(records.loc[records["h"] == cal_h, "log_error"]) if not records.empty else None
        mid = float(cumulative[h - 1])
        if q is not None:
            lo, hi = mid * np.exp(q[0]), mid * np.exp(q[1])
            calibrated = True
        else:
            lo, hi = _bootstrap_range(series, h)
            calibrated = False
        rows.append({"h": h, "likely": mid, "low": min(lo, mid), "high": max(hi, mid), "calibrated": calibrated})
    out = pd.DataFrame(rows)
    out["weekly_likely"] = out["likely"].diff().fillna(out["likely"])
    return out


def weeks_to_deliver(tickets: int, forecast: pd.DataFrame, share: float = 1.0) -> dict | None:
    """Weeks until `tickets` are delivered using `share` of team capacity: likely, and safe (the low end
    of the range, so done by then in roughly 9 of 10 outcomes)."""
    if tickets <= 0 or forecast.empty or share <= 0:
        return None
    tolerance = 1e-6   # float noise: 3 weeks of exactly 10 must count as 30
    likely = forecast.loc[forecast["likely"] * share >= tickets - tolerance, "h"]
    safe = forecast.loc[forecast["low"] * share >= tickets - tolerance, "h"]
    return {"likely": int(likely.iloc[0]) if len(likely) else None,
            "safe": int(safe.iloc[0]) if len(safe) else None}


def accuracy(records: pd.DataFrame, h: int = ACCURACY_HORIZON,
             last: int = ACCURACY_ORIGINS) -> pd.DataFrame:
    """Out-of-sample check: each past forecast scored with a range built only from earlier errors."""
    rows = []
    at_h = records[records["h"] == h].sort_values("origin")
    for _, rec in at_h.tail(last).iterrows():
        known = at_h[at_h["origin"] + h <= rec["origin"]]["log_error"]
        q = _error_quantiles(known)
        if q is None:
            continue
        mid = rec["predicted"]
        lo, hi = min(mid * np.exp(q[0]), mid), max(mid * np.exp(q[1]), mid)
        rows.append({"origin": int(rec["origin"]), "likely": mid, "low": lo, "high": hi, "actual": rec["actual"],
                     "inside": lo <= rec["actual"] <= hi, "abs_pct_error": abs(mid - rec["actual"]) / max(rec["actual"], 1)})
    return pd.DataFrame(rows, columns=["origin", "likely", "low", "high", "actual", "inside", "abs_pct_error"])


# ── Data ────────────────────────────────────────────────────────────────────────

def weekly_series(df_issues: pd.DataFrame) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, pd.Timestamp]:
    """Full weeks (oldest first), PE tickets delivered and requested per week, and today."""
    from reports import executive_summary as es   # local import keeps this module light for Capacity
    from reports import in_progress_report as ipr

    today = ipr.today_local()
    hol = ipr._calendar_holidays(today)
    facts = es._facts(df_issues, today, hol)
    t = facts[facts["sla_applies"]]
    week_of = lambda s: s.dt.to_period("W-SUN").dt.start_time  # noqa: E731
    this_week = today.to_period("W-SUN").start_time
    weeks = pd.date_range(end=this_week - pd.Timedelta(days=7), periods=HISTORY_WEEKS, freq="W-MON")
    done = t[t["outcome"].eq("Done") & t["closed_day"].notna()]
    delivered = done.groupby(week_of(done["closed_day"])).size().reindex(weeks, fill_value=0).to_numpy(float)
    requested = t.groupby(week_of(t["created_day"])).size().reindex(weeks, fill_value=0).to_numpy(float)
    active = (delivered + requested) > 0
    first = int(np.argmax(active)) if active.any() else len(weeks)
    return weeks[first:], delivered[first:], requested[first:], today


# ── Figures ─────────────────────────────────────────────────────────────────────

def _band(fig: go.Figure, x, low, high, name: str) -> None:
    fig.add_trace(go.Scatter(x=list(x) + list(x)[::-1], y=list(high) + list(low)[::-1], fill="toself",
                             fillcolor=BAND, line=dict(color="rgba(0,0,0,0)"), hoverinfo="skip", name=name))


def _weekly_figure(weeks, delivered, forecast: pd.DataFrame) -> go.Figure:
    hist_x, hist_y = weeks[-CHART_HISTORY_WEEKS:], delivered[-CHART_HISTORY_WEEKS:]
    fc = forecast[forecast["h"] <= FORECAST_WEEKS]
    future = pd.date_range(weeks[-1] + pd.Timedelta(days=7), periods=len(fc), freq="W-MON")
    weekly_low = fc["low"].diff().fillna(fc["low"]).clip(lower=0)
    weekly_high = fc["high"].diff().fillna(fc["high"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_x, y=hist_y, name="Delivered", mode="lines+markers",
                             line=dict(color=ACTUAL_COLOR, width=2), marker=dict(size=6),
                             hovertemplate="Week of %{x|%b %d}: %{y:.0f} delivered<extra></extra>"))
    _band(fig, future, np.minimum(weekly_low, fc["weekly_likely"]), np.maximum(weekly_high, fc["weekly_likely"]),
          "Likely range")
    fig.add_trace(go.Scatter(x=future, y=fc["weekly_likely"], name="Forecast (likely)", mode="lines+markers",
                             line=dict(color=FORECAST_COLOR, width=2, dash="dash"), marker=dict(size=6),
                             hovertemplate="Week of %{x|%b %d}: ~%{y:.0f} forecast<extra></extra>"))
    split = future[0] - pd.Timedelta(days=3.5)
    fig.add_vline(x=split, line_dash="dot", line_color=INK)
    fig.add_annotation(x=split, y=1, xref="x", yref="paper", text="Forecast →", showarrow=False,
                       xanchor="left", yanchor="bottom", font=dict(color=INK))
    fig.update_xaxes(title=None, tickformat="%b %d", gridcolor=GRID)
    fig.update_yaxes(title="PE tickets delivered per week", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=380, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _cumulative_figure(weeks, forecast: pd.DataFrame) -> go.Figure:
    fc = forecast[forecast["h"] <= FORECAST_WEEKS].reset_index(drop=True)
    ends = weeks[-1] + pd.to_timedelta(fc["h"] * 7 + 6, unit="D")   # Sunday ending each future week
    fig = go.Figure()
    _band(fig, ends, fc["low"], fc["high"], "Likely range (about 7 in 10 outcomes)")
    fig.add_trace(go.Scatter(
        x=ends, y=fc["likely"], name="Likely total", mode="lines+markers", line=dict(color=FORECAST_COLOR, width=3),
        marker=dict(size=7), customdata=np.stack([fc["low"], fc["high"]], axis=-1),
        hovertemplate="By %{x|%b %d}: ~%{y:,.0f} delivered (%{customdata[0]:,.0f}–%{customdata[1]:,.0f})<extra></extra>",
    ))
    for h in HORIZONS:
        idx = fc.index[fc["h"] == h]
        if len(idx):
            i = idx[0]
            fig.add_annotation(x=ends[i], y=float(fc.loc[i, "likely"]), text=f"{h} wks: ~{fc.loc[i, 'likely']:,.0f}",
                               showarrow=True, arrowhead=0, ax=-40, ay=-30, font=dict(color=INK))
    fig.update_xaxes(title=None, tickformat="%b %d", gridcolor=GRID)
    fig.update_yaxes(title="PE tickets delivered from today (cumulative)", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=380, legend_title_text="", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _demand_figure(weeks, delivered, requested, delivery_fc: pd.DataFrame, demand_fc: pd.DataFrame) -> go.Figure:
    hist_x = weeks[-CHART_HISTORY_WEEKS:]
    n = min(FORECAST_WEEKS, len(delivery_fc))
    future = pd.date_range(weeks[-1] + pd.Timedelta(days=7), periods=n, freq="W-MON")
    fig = go.Figure()
    for name, history, fc, color in [("Requested", requested, demand_fc, ACTUAL_COLOR),
                                     ("Delivered", delivered, delivery_fc, FORECAST_COLOR)]:
        fig.add_trace(go.Scatter(x=hist_x, y=history[-CHART_HISTORY_WEEKS:], name=name, mode="lines",
                                 line=dict(color=color, width=2), legendgroup=name,
                                 hovertemplate=f"{name}: %{{y:.0f}}<extra></extra>"))
        fig.add_trace(go.Scatter(x=future, y=fc["weekly_likely"].head(n), name=f"{name} (forecast)", mode="lines",
                                 line=dict(color=color, width=2, dash="dash"), legendgroup=name,
                                 hovertemplate=f"{name}, forecast: ~%{{y:.0f}}<extra></extra>"))
    fig.update_xaxes(title=None, tickformat="%b %d", gridcolor=GRID)
    fig.update_yaxes(title="PE tickets per week", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=340, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _accuracy_figure(weeks, acc: pd.DataFrame) -> go.Figure:
    x = [weeks[o] for o in acc["origin"]]
    fig = go.Figure()
    _band(fig, x, acc["low"], acc["high"], "Forecast range at the time")
    fig.add_trace(go.Scatter(x=x, y=acc["likely"], name="Forecast at the time (likely)", mode="lines",
                             line=dict(color=FORECAST_COLOR, width=2, dash="dash"),
                             hovertemplate="From %{x|%b %d}: forecast ~%{y:.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=acc["actual"], name="What actually happened", mode="lines+markers",
                             line=dict(color=ACTUAL_COLOR, width=2), marker=dict(size=6),
                             hovertemplate="From %{x|%b %d}: actually %{y:.0f}<extra></extra>"))
    fig.update_xaxes(title=f"Forecast made in the week of… (for the next {ACCURACY_HORIZON} weeks)", tickformat="%b %d",
                     gridcolor=GRID)
    fig.update_yaxes(title=f"Tickets over {ACCURACY_HORIZON} weeks", rangemode="tozero", gridcolor=GRID)
    fig.update_layout(height=340, hovermode="x unified", legend_title_text="",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), margin=dict(l=10, r=10, t=40, b=10))
    return fig


# ── Entry point ─────────────────────────────────────────────────────────────────

def _empty_payload(message: str | None = None) -> dict:
    return {"error_message": message, "headline": None, "cards": [], "weekly_fig": None, "cumulative_fig": None,
            "demand_fig": None, "accuracy_fig": None, "accuracy": {}, "forecast": pd.DataFrame(), "calibrated": False}


def build_forecast_visuals(df_issues: pd.DataFrame) -> dict:
    if df_issues is None or df_issues.empty:
        return _empty_payload("No ticket data available.")
    required = {"key", "status", "assignee_name", "issuetype", "created", "updated"}
    if not required.issubset(df_issues.columns):
        return _empty_payload("Ticket data is missing required columns.")

    weeks, delivered, requested, today = weekly_series(df_issues)
    if len(weeks) < 8:
        return _empty_payload(f"Need at least 8 full weeks of delivery history (have {len(weeks)}).")

    records = backtest(delivered)
    forecast = delivery_forecast(delivered, records=records)
    demand = delivery_forecast(requested)
    acc = accuracy(records) if not records.empty else pd.DataFrame()

    cards = []
    for h in HORIZONS:
        row = forecast[forecast["h"] == h].iloc[0]
        cards.append({"weeks": h, "by": (weeks[-1] + pd.Timedelta(days=7 * h + 6)).date(),
                      "likely": int(round(row["likely"])), "low": int(round(row["low"])), "high": int(round(row["high"]))})
    longest = cards[-1]
    req_12 = float(demand.loc[demand["h"] == FORECAST_WEEKS, "likely"].iloc[0])
    del_12 = float(forecast.loc[forecast["h"] == FORECAST_WEEKS, "likely"].iloc[0])
    demand_acc = accuracy(backtest(requested))
    payload = _empty_payload()
    payload.update({
        "as_of": today.date(),
        "last_full_week": weeks[-1].date(),
        "headline": (f"If the current trend continues, Platform Engineering will likely deliver about "
                     f"**{longest['likely']:,} tickets in the next {longest['weeks']} weeks** (by {longest['by']:%b %d}), "
                     f"most likely between **{longest['low']:,} and {longest['high']:,}**."),
        "cards": cards,
        "recent_rate": float(delivered[-4:].mean()),
        "weekly_fig": _weekly_figure(weeks, delivered, forecast),
        "cumulative_fig": _cumulative_figure(weeks, forecast),
        "demand_fig": _demand_figure(weeks, delivered, requested, forecast, demand),
        "requested_12w": req_12,
        "delivered_12w": del_12,
        "demand_typical_error": float(demand_acc["abs_pct_error"].mean()) if not demand_acc.empty else None,
        "forecast": forecast,
        "calibrated": bool(forecast.loc[forecast["h"] <= FORECAST_WEEKS, "calibrated"].all()),
    })
    if not acc.empty:
        payload["accuracy_fig"] = _accuracy_figure(weeks, acc)
        payload["accuracy"] = {"forecasts": int(len(acc)), "inside": float(acc["inside"].mean()),
                               "typical_error": float(acc["abs_pct_error"].mean()),
                               "bias": float(((acc["likely"] - acc["actual"]) / acc["actual"].clip(lower=1)).mean())}
    return payload
