"""
distribution_of_tickets_report.py
──────────────────────────────────
Builds Plotly visualisations for the Distribution of Ticket's Age page.

Public API
----------
build_distribution_visuals(df_issues: pd.DataFrame) -> dict
    Returns a dict with keys:
        box_fig       – Plotly Figure: box plot of days_old by status
        violin_fig    – Plotly Figure: violin plot of velocity_days by status
        median_age    – int: median ticket age (days)
        p75_age       – int: 75th-percentile ticket age (days)
        open_count    – int: number of open tickets analysed
        error_message – str | None
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Statuses to exclude from both charts
_EXCLUDE_STATUSES = {
    "Done",
    "Will Not Do",
    "Released Successfully to Production",
    "❌ Rolled Back",
    "Prepare Release",
    "Plan Release",
    "Post Implementation Review",
    "Deploy Release",
    "Test and Validate",
    "Review Release Plan",
}

# Additional statuses excluded only from the violin chart
_VIOLIN_EXTRA_EXCLUDE = {
    "Resource Constrained",
    "Blocked For Development",
    "Plan Release",
    "Technical Debt",
}


def _filter_open(df: pd.DataFrame, extra_exclude: set = None) -> pd.DataFrame:
    """Return rows whose status is not in the excluded sets."""
    exclude = _EXCLUDE_STATUSES | (extra_exclude or set())
    return df[~df["status"].isin(exclude)].copy()


def _risk_band(age_days: float) -> str:
    """Classify ticket age into executive-friendly risk bands."""
    if age_days >= 90:
        return "Critical"
    if age_days >= 60:
        return "Aging"
    if age_days >= 30:
        return "Watch"
    return "Healthy"


def build_distribution_visuals(df_issues: pd.DataFrame) -> dict:
    """
    Build ticket-age distribution charts from *df_issues*.

    Parameters
    ----------
    df_issues : pd.DataFrame
        The master issues DataFrame produced by build_issues_dataframe().

    Returns
    -------
    dict with keys: box_fig, violin_fig, median_age, p75_age,
                    open_count, error_message
    """
    result = {
        "box_fig": None,
        "violin_fig": None,
        "median_age": None,
        "p75_age": None,
        "open_count": 0,
        "error_message": None,
    }

    if df_issues is None or df_issues.empty:
        result["error_message"] = "No ticket data available."
        return result

    # ── Box plot: days_old by status ─────────────────────────────────────────
    try:
        df_box = _filter_open(df_issues)

        if "days_old" not in df_box.columns:
            result["error_message"] = "Column 'days_old' not found in data."
            return result

        df_box = df_box.dropna(subset=["days_old", "status"])
        df_box["days_old"] = pd.to_numeric(df_box["days_old"], errors="coerce")
        df_box = df_box.dropna(subset=["days_old"])

        # Sort statuses by median age descending so the most aged appear first
        order = (
            df_box.groupby("status")["days_old"]
            .median()
            .sort_values(ascending=False)
            .index.tolist()
        )

        box_fig = px.box(
            df_box,
            x="status",
            y="days_old",
            color="status",
            category_orders={"status": order},
            title="Distribution of Ticket Age by Status",
            labels={"status": "Status", "days_old": "Ticket Age (days)"},
            color_discrete_sequence=px.colors.qualitative.Vivid,
        )
        box_fig.update_layout(
            xaxis_tickangle=-45,
            showlegend=False,
            height=450,
            yaxis=dict(rangemode="tozero"),
        )

        result["box_fig"] = box_fig
        result["open_count"] = len(df_box)
        result["median_age"] = int(df_box["days_old"].median())
        result["p75_age"] = int(df_box["days_old"].quantile(0.75))

    except Exception as exc:
        result["error_message"] = f"Box plot error: {exc}"
        return result

    # ── Executive violin view: ticket age risk by status ────────────────────
    try:
        df_violin = _filter_open(df_issues, extra_exclude=_VIOLIN_EXTRA_EXCLUDE)

        age_column = "days_old" if "days_old" in df_violin.columns else "velocity_days"
        if age_column not in df_violin.columns:
            return result

        df_violin = df_violin.dropna(subset=[age_column, "status"])
        df_violin[age_column] = pd.to_numeric(df_violin[age_column], errors="coerce")
        df_violin = df_violin.dropna(subset=[age_column])

        summary = (
            df_violin.groupby("status")[age_column]
            .agg(ticket_count="size", median_age="median", mean_age="mean", max_age="max")
            .reset_index()
        )
        p75_series = df_violin.groupby("status")[age_column].quantile(0.75)
        summary["p75_age"] = summary["status"].map(p75_series)
        summary["risk_band"] = summary["median_age"].apply(_risk_band)

        violin_order = (
            summary.sort_values(["median_age", "ticket_count"], ascending=[False, False])["status"]
            .tolist()
        )
        summary = summary.set_index("status").loc[violin_order].reset_index()

        color_map = {
            "Critical": "#7f1d1d",
            "Aging": "#9a3412",
            "Watch": "#1d4ed8",
            "Healthy": "#0f766e",
        }

        x_upper = max(120, float(df_violin[age_column].max()) * 1.15)
        violin_fig = go.Figure()

        for _, row in summary.iterrows():
            status = row["status"]
            status_df = df_violin[df_violin["status"] == status]
            customdata = [
                [
                    int(row["ticket_count"]),
                    float(row["median_age"]),
                    float(row["p75_age"]),
                    float(row["mean_age"]),
                    float(row["max_age"]),
                    row["risk_band"],
                ]
            ] * len(status_df)

            violin_fig.add_trace(
                go.Violin(
                    x=status_df[age_column],
                    y=[status] * len(status_df),
                    name=status,
                    orientation="h",
                    legendgroup=row["risk_band"],
                    scalegroup=status,
                    spanmode="hard",
                    box_visible=True,
                    meanline_visible=True,
                    line_color=color_map[row["risk_band"]],
                    fillcolor=color_map[row["risk_band"]],
                    opacity=0.45,
                    points=False,
                    customdata=customdata,
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "Ticket age: %{x:.0f} days<br>"
                        "Open tickets: %{customdata[0]}<br>"
                        "Median age: %{customdata[1]:.0f} days<br>"
                        "75th percentile: %{customdata[2]:.0f} days<br>"
                        "Average age: %{customdata[3]:.0f} days<br>"
                        "Oldest ticket: %{customdata[4]:.0f} days<br>"
                        "Risk band: %{customdata[5]}<extra></extra>"
                    ),
                )
            )

        violin_fig.add_vrect(x0=0, x1=30, fillcolor="#dbeafe", opacity=0.22, line_width=0)
        violin_fig.add_vrect(x0=30, x1=60, fillcolor="#e0f2fe", opacity=0.20, line_width=0)
        violin_fig.add_vrect(x0=60, x1=90, fillcolor="#ffedd5", opacity=0.22, line_width=0)
        violin_fig.add_vrect(x0=90, x1=x_upper, fillcolor="#fee2e2", opacity=0.24, line_width=0)

        violin_fig.add_vline(x=30, line_width=1, line_dash="dot", line_color="#2563eb")
        violin_fig.add_vline(x=60, line_width=1, line_dash="dot", line_color="#f59e0b")
        violin_fig.add_vline(x=90, line_width=1.5, line_dash="dash", line_color="#b91c1c")

        violin_fig.add_trace(
            go.Scatter(
                x=summary["median_age"],
                y=summary["status"],
                mode="markers+text",
                text=[
                    f"Median {median:.0f}d · P75 {p75:.0f}d · n={count}"
                    for median, p75, count in zip(
                        summary["median_age"], summary["p75_age"], summary["ticket_count"]
                    )
                ],
                textposition="middle right",
                marker=dict(size=10, color="#0f172a", symbol="diamond"),
                name="Median summary",
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Median age: %{x:.0f} days<extra></extra>"
                ),
                showlegend=False,
            )
        )

        violin_fig.update_layout(
            title="Executive View: Ticket Age Risk by Status",
            xaxis_title="Ticket Age (days)",
            yaxis_title=None,
            xaxis=dict(range=[0, x_upper]),
            yaxis=dict(categoryorder="array", categoryarray=violin_order),
            height=max(460, 110 + 65 * len(violin_order)),
            showlegend=False,
            margin=dict(l=20, r=180, t=70, b=20),
            paper_bgcolor="#000000",
            plot_bgcolor="#000000",
            font=dict(color="#f8fafc"),
        )
        violin_fig.add_annotation(
            x=x_upper,
            y=1.08,
            xref="x",
            yref="paper",
            text="Healthy <30d · Watch 30-59d · Aging 60-89d · Critical 90d+",
            showarrow=False,
            xanchor="right",
            font=dict(size=11, color="#e2e8f0"),
        )

        violin_fig.update_xaxes(showgrid=True, gridcolor="#334155", zeroline=False)
        violin_fig.update_yaxes(showgrid=False)

        result["violin_fig"] = violin_fig

    except Exception as exc:
        result["error_message"] = f"Violin plot error: {exc}"

    return result
