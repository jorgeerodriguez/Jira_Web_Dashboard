from datetime import date
import plotly.graph_objects as go

KANBAN_COLUMN_ORDER = [
    "Triage", "Tech Discovery Required", "Technical Debt", "Resource Constrained", "To Do", "Blocked", "On Hold", "Plan Release", "Staged CAR",
    "In Progress", "Build Release", "Deploy Replease", "Validating", "Post Implementation Review", "Done", "Will Not Do",
    "Closed", "Resolved", "Cancelled", "Released Successfully to Production", "❌ Rolled Back"
]


def plot_ticket_distribution(status_counts: dict, project_key: str = "DEVOPS") -> go.Figure:
    """Return a Plotly bar chart of ticket counts by Kanban status.

    Args:
        status_counts: dict mapping status name → count
        project_key: Jira project key shown in the chart title

    Returns:
        A plotly.graph_objects.Figure ready to pass to st.plotly_chart()
    """
    ordered_statuses = [s for s in KANBAN_COLUMN_ORDER if s in status_counts]
    # Include any statuses not in the canonical order at the end
    extra = [s for s in status_counts if s not in KANBAN_COLUMN_ORDER]
    ordered_statuses += extra

    counts = [status_counts[s] for s in ordered_statuses]

    fig = go.Figure(
        go.Bar(
            x=ordered_statuses,
            y=counts,
            text=counts,
            textposition="outside",
            marker_color="#f97316",
            marker_line_color="#000",
            marker_line_width=0.5,
        )
    )
    fig.update_layout(
        title=f"Ticket Distribution by Status — {project_key} ({date.today()})",
        xaxis_title="Issue Status (Stage)",
        yaxis_title="Number of Tickets",
        xaxis_tickangle=-45,
        height=480,
        margin=dict(l=10, r=10, t=50, b=160),
    )
    return fig