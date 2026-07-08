import pandas as pd

# Custom field definitions
CF_BUSINESS_LEAD    = "customfield_11751"
CF_PLANNING_RANK    = "customfield_11445"
CF_PORTFOLIO_RANK   = "customfield_11312"
CF_PARENT_LINK      = "customfield_10300"   # Legend link lives here
CF_EPIC_LINK        = "customfield_10009"   # Story  Epic
CF_EPIC_NAME        = "customfield_10008"   # Epic Name (text)
CF_REQUEST_TYPE     = "customfield_10977"
CF_TARGET_END       = "customfield_10947"
CF_TIME_IN_PROGRESS = "customfield_11261"
CF_PLANNED_START    = "customfield_10946"


def build_issues_dataframe(jira_connector, projects=("DEVOPS", "CAR")):
    """Build a pandas DataFrame of issues from Jira.

    Args:
        jira_connector: JIRA connector object from jira library
        projects: tuple of project keys to fetch (default: DEVOPS, CAR)

    Returns:
        pd.DataFrame with columns: key, id, summary, status, issuetype, creator_name,
            assignee_name, created, updated, project_name, priority_name, and many others
    """
    fields_to_fetch = (
        "assignee, summary, key, status, created, updated, issuetype, creator, project, "
        "duedate, priority, customfield_10947, customfield_11751, customfield_11445, "
        "customfield_11312, customfield_10300, parentProject, parent, customfield_10946, resolutiondate"
    )

    # Build JQL query — 24-month lookback
    jql_clauses = []
    if projects:
        project_filter = " OR ".join([f'project = "{p}"' for p in projects])
        jql_clauses.append(f"({project_filter})")
    jql_clauses.append('created >= -730d')

    jql_query = " AND ".join(jql_clauses) + " ORDER BY assignee ASC, updated DESC"

    # Fetch issues with pagination
    issues_iterator_all = []
    try:
        page_size = 5000
        next_token = None

        while True:
            query_kwargs = {
                "jql_str": jql_query,
                "maxResults": page_size,
                "fields": fields_to_fetch,
            }
            if next_token:
                query_kwargs["nextPageToken"] = next_token

            issues = jira_connector.enhanced_search_issues(**query_kwargs)
            if not issues:
                break

            issues_iterator_all.extend(issues)

            next_token = getattr(issues, "nextPageToken", None)
            if not next_token:
                break

    except Exception:
        return pd.DataFrame()  # Return empty dataframe on error

    # Process issues into dataframe
    processed_data = []

    for issue in issues_iterator_all:
        fields = getattr(issue, "raw", {}).get("fields", {})

        # Extract assignee
        if fields.get("assignee"):
            assignee_name = fields["assignee"].get("displayName", "Unassigned")
        else:
            assignee_name = "Unassigned"

        # Extract creator
        if fields.get("creator"):
            creator_name = fields["creator"].get("displayName", "Unknown")
        else:
            creator_name = "Unknown"

        # Extract priority
        if fields.get("priority"):
            priority_name = fields["priority"].get("name", "No Priority")
            priority_id = fields["priority"].get("id", "No Priority")
        else:
            priority_name = "No Priority"
            priority_id = "No Priority"

        # Extract due date
        due_date = fields.get("duedate", "2000-01-01T00:00:00.000+0000")

        # Extract custom fields
        try:
            if fields.get(CF_BUSINESS_LEAD):
                business_lead = str(fields[CF_BUSINESS_LEAD].get("displayName", "Unknown")).replace(".", " ")
            else:
                business_lead = "Unknown"
        except Exception:
            business_lead = "Unknown"

        try:
            planning_rank = fields.get(CF_PLANNING_RANK, "Unknown")
        except Exception:
            planning_rank = "Unknown"

        try:
            portfolio_rank = fields.get(CF_PORTFOLIO_RANK, pd.NaT)
        except Exception:
            portfolio_rank = pd.NaT

        try:
            legend = fields.get(CF_PARENT_LINK, pd.NA)
        except Exception:
            legend = pd.NA

        try:
            parent_project = fields.get("parentProject", pd.NA)
        except Exception:
            parent_project = pd.NA

        try:
            parent = fields.get("parent", {}).get("key", pd.NA) if fields.get("parent") else pd.NA
        except Exception:
            parent = pd.NA
            
        created_dt = pd.to_datetime(fields.get('created', None), utc=True, errors='coerce')
        updated_dt = pd.to_datetime(fields.get('updated', None), utc=True, errors='coerce')
        resolved_dt = pd.to_datetime(fields.get('resolutiondate', None), utc=True, errors='coerce')
        due_dt = pd.to_datetime(due_date, utc=True, errors='coerce')
        target_end_dt = pd.to_datetime(fields.get('customfield_10947', None), utc=True, errors='coerce')

        created_local = created_dt.tz_convert('UTC-06:00') if pd.notna(created_dt) else pd.NaT
        updated_local = updated_dt.tz_convert('UTC-06:00') if pd.notna(updated_dt) else pd.NaT
        resolved_local = resolved_dt.tz_convert('UTC-06:00') if pd.notna(resolved_dt) else pd.NaT
        due_local = due_dt.tz_convert('UTC-06:00') if pd.notna(due_dt) else pd.NaT
    
        if resolved_local is not pd.NaT:
            updated_local = resolved_local


        issue_data = {
            "key": issue.key,
            "id": issue.id,
            "summary": fields.get("summary", ""),
            "status": fields.get("status", {}).get("name", ""),
            "issuetype": fields.get("issuetype", {}).get("name", ""),
            "creator_name": creator_name,
            "assignee_name": assignee_name,
            "created": created_local,
            "updated": updated_local,
            "resolved": resolved_local,
            "project_name": fields.get("project", {}).get("name", ""),
            "project_id": fields.get("project", {}).get("id", ""),
            "priority_name": priority_name,
            "priority_id": priority_id,
            "project_due_date": pd.to_datetime(due_date, utc=True, errors="coerce"),
            "today": pd.to_datetime("today", utc=True, errors="coerce"),
            "days_old": (pd.to_datetime("today", utc=True, errors="coerce") - pd.to_datetime(fields.get("created", None), errors="coerce")).days,
            'year_created': int(created_local.year) if pd.notna(created_local) else None,
            'month_created': int(created_local.month) if pd.notna(created_local) else None,
            'year_updated': int(updated_local.year) if pd.notna(updated_local) else None,
            'month_updated': int(updated_local.month) if pd.notna(updated_local) else None,
            "year_resolved": int(resolved_local.year) if pd.notna(resolved_local) else None,
            "month_resolved": int(resolved_local.month) if pd.notna(resolved_local) else None,
            "last_updated_days": (pd.to_datetime("today", utc=True, errors="coerce") - pd.to_datetime(fields.get("updated", None), errors="coerce")).days,
            "project_due_date_days": (pd.to_datetime(due_date, utc=True, errors="coerce") - pd.to_datetime(fields.get("updated", None), errors="coerce")).days if due_date != "2000-01-01T00:00:00.000+0000" else None,
            "velocity": (pd.to_datetime(fields.get("updated", None), errors="coerce") - pd.to_datetime(fields.get("created", None), errors="coerce")).days,
            "velocity_hours": (pd.to_datetime(fields.get("updated", None), errors="coerce") - pd.to_datetime(fields.get("created", None), errors="coerce")).total_seconds() / 3600,
            "velocity_days": (pd.to_datetime(fields.get("updated", None), errors="coerce") - pd.to_datetime(fields.get("created", None), errors="coerce")).days,
            "target_end_date": pd.to_datetime(fields.get(CF_TARGET_END, None), utc=True, errors="coerce"),
            "planned_start_date": pd.to_datetime(fields.get(CF_PLANNED_START, pd.NaT), utc=True, errors="coerce"),
            "business_lead": business_lead,
            "planning_rank": planning_rank,
            "portfolio_rank": portfolio_rank,
            "legend": legend,
            "parent_project": parent_project,
            "parent": parent,
        }
        processed_data.append(issue_data)

    # Create DataFrame
    df_issues = pd.DataFrame(processed_data)

    # Post-processing
    if len(df_issues) > 0:
        # Fix negative days to 0
        df_issues.loc[df_issues["days_old"] < 0, "days_old"] = 0
        df_issues.loc[df_issues["last_updated_days"] < 0, "last_updated_days"] = 0

        # Calculate velocity_backlog
        df_issues["velocity_backlog"] = df_issues["planned_start_date"] - df_issues["created"]
        df_issues["velocity_backlog_days"] = df_issues["velocity_backlog"].apply(
            lambda x: x.days if pd.notnull(x) and hasattr(x, "days") else None
        )
        df_issues.loc[df_issues["velocity_backlog_days"] < 0, "velocity_backlog_days"] = 0

    return df_issues
