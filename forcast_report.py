import pandas as pd
import numpy as np
import plotly.graph_objects as go
from xgboost import XGBRegressor


def mean_absolute_error(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred)))


def mean_squared_error(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean((y_true - y_pred) ** 2))

try:
    from capacity_report import build_capacity_data
except ImportError:
    import importlib

    build_capacity_data = importlib.import_module("jira_morning_report_site.capacity_report").build_capacity_data


# ── Feature helpers ───────────────────────────────────────────────────────────

def _create_lagged_features(data: pd.DataFrame, lags: int = 3) -> pd.DataFrame:
    df = data.copy()
    for lag in range(1, lags + 1):
        df[f"lag_{lag}"] = df["count"].shift(lag)
    df.dropna(inplace=True)
    return df


def _add_date_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df["month"] = df.index.month
    df["day_of_week"] = df.index.dayofweek
    df["is_weekend"] = df["day_of_week"].apply(lambda x: 1 if x >= 5 else 0)
    df["3_month_max"] = df["count"].rolling(window=3).max()
    df["3_month_min"] = df["count"].rolling(window=3).min()
    return df


def _add_rolling_features(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    df["rolling_mean"] = df["count"].rolling(window=window).mean().shift(1)
    df["rolling_std"] = df["count"].rolling(window=window).std().shift(1)
    df.dropna(inplace=True)
    return df


def _forecast_future(model, initial_data, steps: int) -> list:
    future_values = []
    input_data = initial_data[-1:].copy()
    for _ in range(steps):
        prediction = model.predict(input_data)
        future_values.append(float(prediction[0]))
        input_data = np.roll(input_data, -1)
        input_data[0, -1] = prediction[0]
    return future_values


# ── Main entry point ──────────────────────────────────────────────────────────

def build_forecast_visuals(df_issues: pd.DataFrame, periods: int = 4) -> dict:
    """
    Train an XGBoost model on historical completed-ticket counts and return
    a Plotly forecast figure plus key metrics.

    Parameters
    ----------
    df_issues : pd.DataFrame   Raw Jira issues dataframe from session state.
    periods   : int            Number of future months to forecast.

    Returns
    -------
    dict with keys:
        forecast_fig, mse, mae,
        future_low, future_mid, future_high,
        error_message (str | None)
    """
    empty = {
        "forecast_fig": None,
        "mse": None,
        "mae": None,
        "future_low": None,
        "future_mid": None,
        "future_high": None,
        "error_message": None,
    }

    # ── 1. Build capacity data ────────────────────────────────────────────────
    all_data = build_capacity_data(df_issues)
    if all_data is None or all_data.empty:
        empty["error_message"] = "Not enough capacity data to build a forecast."
        return empty

    all_data = all_data.set_index("date").sort_index()

    if len(all_data) < 8:
        empty["error_message"] = (
            f"Need at least 8 months of data to train the model "
            f"(currently have {len(all_data)})."
        )
        return empty

    # ── 2. Prepare completed & created series ─────────────────────────────────
    lagged_completed = _add_rolling_features(
        _add_date_features(
            _create_lagged_features(
                all_data[["completed"]].rename(columns={"completed": "count"}), lags=3
            )
        ),
        window=3,
    )

    lagged_created = _add_rolling_features(
        _add_date_features(
            _create_lagged_features(
                all_data[["created"]].rename(columns={"created": "count"}), lags=3
            )
        ),
        window=3,
    )

    # Add created count as an extra feature
    lagged_completed["created"] = lagged_created["count"].reindex(lagged_completed.index)
    lagged_completed.dropna(inplace=True)

    if len(lagged_completed) < 6:
        empty["error_message"] = "Not enough data after feature engineering to train the model."
        return empty

    # ── 3. Train / test split ─────────────────────────────────────────────────
    train_size = int(len(lagged_completed) * 0.8)
    train_data = lagged_completed.iloc[:train_size]
    test_data = lagged_completed.iloc[train_size:]

    feature_cols = [c for c in lagged_completed.select_dtypes(
        include=["int", "float", "bool"]).columns if c != "count"]

    X_train = train_data[feature_cols]
    y_train = train_data["count"]
    X_test = test_data[feature_cols]
    y_test = test_data["count"]

    # ── 4. Fit XGBoost ────────────────────────────────────────────────────────
    model = XGBRegressor(
        objective="reg:squarederror",
        n_estimators=5000,
        max_depth=3,
        learning_rate=0.01,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mse = float(mean_squared_error(y_test, y_pred))
    mae = float(mean_absolute_error(y_test, y_pred))

    # ── 5. Future forecast ────────────────────────────────────────────────────
    future_forecast = _forecast_future(model, X_test.tail(1).values, steps=periods)

    future_dates = pd.date_range(
        start=X_test.index[-1] + pd.DateOffset(months=1), periods=periods, freq="MS"
    )
    future_df = pd.DataFrame(
        {
            "Predicted_Value": future_forecast,
            "yhat_lower": [v - mae for v in future_forecast],
            "yhat_upper": [v + mae for v in future_forecast],
        },
        index=future_dates,
    )

    past_dates = pd.date_range(start=X_test.index[0], periods=len(y_pred), freq="MS")
    past_df = pd.DataFrame(
        {
            "Predicted_Value": y_pred,
            "yhat_lower": y_pred - mae,
            "yhat_upper": y_pred + mae,
        },
        index=past_dates,
    )

    all_dates_df = pd.concat([past_df, future_df])

    future_low = future_df["yhat_lower"].sum()
    future_mid = future_df["Predicted_Value"].sum()
    future_high = future_df["yhat_upper"].sum()

    # ── 6. Build Plotly figure ────────────────────────────────────────────────
    fig = go.Figure()

    # Confidence band
    fig.add_trace(
        go.Scatter(
            x=list(all_dates_df.index) + list(all_dates_df.index[::-1]),
            y=list(all_dates_df["yhat_upper"]) + list(all_dates_df["yhat_lower"][::-1]),
            fill="toself",
            fillcolor="rgba(34,197,94,0.15)",
            line=dict(color="rgba(255,255,255,0)"),
            hoverinfo="skip",
            showlegend=False,
        )
    )

    # Actual (test period)
    fig.add_trace(
        go.Scatter(
            x=X_test.index,
            y=y_test,
            mode="lines+markers",
            name="Actual",
            line=dict(color="#3b82f6", width=2),
            marker=dict(size=6),
        )
    )

    # Model on test period
    fig.add_trace(
        go.Scatter(
            x=past_df.index,
            y=past_df["Predicted_Value"],
            mode="lines",
            name="Model (test)",
            line=dict(color="#22c55e", width=2, dash="dot"),
        )
    )

    # Future forecast
    fig.add_trace(
        go.Scatter(
            x=future_df.index,
            y=future_df["Predicted_Value"],
            mode="lines+markers",
            name=f"Forecast ({periods} mo)",
            line=dict(color="#f97316", width=2.5),
            marker=dict(size=8, symbol="diamond"),
        )
    )

    # Separator line (use shape + annotation to avoid Plotly add_vline
    # annotation bug with datetime/category x axes)
    split_x = future_df.index[0]
    fig.add_shape(
        type="line",
        x0=split_x,
        x1=split_x,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        line=dict(dash="dash", color="rgba(120,120,120,0.5)"),
    )
    fig.add_annotation(
        x=split_x,
        y=1,
        xref="x",
        yref="paper",
        text="Forecast →",
        showarrow=False,
        xanchor="left",
        yanchor="bottom",
        font=dict(color="rgba(90,90,90,1)"),
    )

    fig.update_layout(
        title=(
            f"Completed Tickets — Historical vs XGBoost Forecast  "
            f"[Low: {future_low:,.0f} | Mid: {future_mid:,.0f} | High: {future_high:,.0f}]"
        ),
        xaxis_title="Month",
        yaxis_title="Tickets Completed",
        height=460,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return {
        "forecast_fig": fig,
        "mse": mse,
        "mae": mae,
        "future_low": future_low,
        "future_mid": future_mid,
        "future_high": future_high,
        "error_message": None,
    }




