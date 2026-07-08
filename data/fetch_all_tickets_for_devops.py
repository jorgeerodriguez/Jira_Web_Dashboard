from collections import Counter


def fetch_all_tickets_for_project(jira_connector, project_key: str = "DEVOPS") -> tuple[int, str, int, dict]:
    """Fetch all Jira tickets for a project.

    Returns:
        tuple: (0, message, total_count, status_counts) on success
        tuple: (1, error_message, 0, {}) on failure
    """
    if jira_connector is None:
        return 1, "Invalid Jira connector: None", 0, {}

    jql_query = f'project = "{project_key}" AND created >= -730d ORDER BY created DESC'
    status_counts = Counter()
    total_issues_processed = 0
    page_size = 100
    next_page_token = None

    try:
        while True:
            query_kwargs = {
                "jql_str": jql_query,
                "maxResults": page_size,
                "fields": "status,summary",
            }
            if next_page_token:
                query_kwargs["nextPageToken"] = next_page_token

            issues = jira_connector.enhanced_search_issues(**query_kwargs)

            if not issues:
                break

            for issue in issues:
                status_name = getattr(issue.fields.status, "name", "Unknown")
                status_counts[status_name] += 1
                total_issues_processed += 1

            next_page_token = getattr(issues, "nextPageToken", None)
            if not next_page_token:
                break

        status_counts.pop("Done", None)
        status_counts.pop("Will Not Do", None)

        return 0, f"Fetched {total_issues_processed} tickets from project '{project_key}'.", total_issues_processed, dict(status_counts)
    except Exception as err:
        return 1, f"Error fetching Jira tickets: {err}", 0, {}
