import pandas as pd
import plotly.express as px


def _empty_payload() -> dict:
	return {
		"stacked_fig": None,
		"priority_pie_fig": None,
		"leader_pie_fig": None,
		"summary_df": pd.DataFrame(),
		"start_month": None,
		"end_month": None,
		"available_months": [],
		"error_message": None,
	}


def _ensure_month_column(df: pd.DataFrame) -> pd.DataFrame:
	out = df.copy()

	if "month" in out.columns:
		out["month"] = out["month"].astype(str)
		return out

	if {"year_created", "month_created"}.issubset(out.columns):
		out["month"] = (
			out["year_created"].astype("Int64").astype(str)
			+ "-"
			+ out["month_created"].astype("Int64").astype(str).str.zfill(2)
		)
		return out

	if "created" in out.columns:
		created = pd.to_datetime(out["created"], errors="coerce")
		out["month"] = created.dt.to_period("M").astype(str)
		return out

	out["month"] = pd.NA
	return out


def _month_bounds(available_months: list[str]) -> tuple[str | None, str | None]:
	if not available_months:
		return None, None

	current_month = pd.Timestamp.today().to_period("M")
	default_start = (current_month - 2).strftime("%Y-%m")
	default_end = current_month.strftime("%Y-%m")

	if default_end < available_months[0]:
		return available_months[0], available_months[min(2, len(available_months) - 1)]

	start_month = max(default_start, available_months[0])
	end_month = min(default_end, available_months[-1])

	if start_month > end_month:
		start_month = available_months[max(0, len(available_months) - 3)]
		end_month = available_months[-1]

	return start_month, end_month


def build_business_leader_visuals(
	df_issues: pd.DataFrame,
	start_month: str | None = None,
	end_month: str | None = None,
) -> dict:
	payload = _empty_payload()

	if df_issues is None or df_issues.empty:
		payload["error_message"] = "No ticket data available."
		return payload

	df = _ensure_month_column(df_issues)

	lead_col = "bussiness_lead" if "bussiness_lead" in df.columns else "business_lead"
	if lead_col not in df.columns:
		payload["error_message"] = "Business lead column not found in data."
		return payload

	if "priority_name" not in df.columns:
		payload["error_message"] = "Priority column not found in data."
		return payload

	df[lead_col] = df[lead_col].fillna("Unassigned").astype(str).str.strip()
	df["priority_name"] = df["priority_name"].fillna("Unknown").astype(str)
	df["month"] = df["month"].fillna("").astype(str)
	df = df[df["month"].str.match(r"^\d{4}-\d{2}$", na=False)].copy()

	if df.empty:
		payload["error_message"] = "No valid month data available."
		return payload

	available_months = sorted(df["month"].dropna().unique().tolist())
	payload["available_months"] = available_months

	default_start, default_end = _month_bounds(available_months)
	start = start_month or default_start
	end = end_month or default_end

	if start is None or end is None:
		payload["error_message"] = "Could not determine month range."
		return payload

	if start > end:
		start, end = end, start

	payload["start_month"] = start
	payload["end_month"] = end

	filtered = df[(df["month"] >= start) & (df["month"] <= end)].copy()
	if filtered.empty:
		payload["error_message"] = f"No tickets found between {start} and {end}."
		return payload

	if "key" in filtered.columns:
		filtered.loc[
			filtered["key"].astype(str).str.contains("CAR-", na=False),
			lead_col,
		] = "Jorge Rodriguez"

	grouped = (
		filtered.groupby([lead_col, "month", "priority_name"], as_index=False)
		.size()
		.rename(columns={"size": "ticket_count", lead_col: "business_lead"})
	)

	stacked_fig = px.bar(
		grouped,
		x="business_lead",
		y="ticket_count",
		color="priority_name",
		facet_col="month",
		facet_col_wrap=3,
		title=f"Tickets by Business Lead and Priority ({start} to {end})",
		labels={
			"business_lead": "Business Lead",
			"ticket_count": "Number of Tickets",
			"priority_name": "Priority",
			"month": "Month",
		},
		barmode="stack",
	)
	stacked_fig.update_layout(height=650, legend_title_text="Priority")
	stacked_fig.update_xaxes(tickangle=-25)

	priority_totals = (
		filtered.groupby("priority_name", as_index=False)
		.size()
		.rename(columns={"size": "ticket_count"})
		.sort_values("ticket_count", ascending=False)
	)
	priority_pie_fig = px.pie(
		priority_totals,
		names="priority_name",
		values="ticket_count",
		title=f"Ticket Distribution by Priority ({start} to {end})",
		hole=0.35,
	)

	lead_totals = (
		filtered.groupby(lead_col, as_index=False)
		.size()
		.rename(columns={"size": "ticket_count", lead_col: "business_lead"})
		.sort_values("ticket_count", ascending=False)
	)
	leader_pie_fig = px.pie(
		lead_totals,
		names="business_lead",
		values="ticket_count",
		title=f"Ticket Distribution by Business Lead ({start} to {end})",
		hole=0.35,
	)

	summary_df = grouped.pivot_table(
		index=["business_lead", "month"],
		columns="priority_name",
		values="ticket_count",
		aggfunc="sum",
		fill_value=0,
	).reset_index()

	payload["stacked_fig"] = stacked_fig
	payload["priority_pie_fig"] = priority_pie_fig
	payload["leader_pie_fig"] = leader_pie_fig
	payload["summary_df"] = summary_df

	return payload
