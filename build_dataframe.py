import pandas as pd
from collections import defaultdict, Counter

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


fields_to_fetch = "assignee, summary, key, status, created, updated, issuetype, creator, project, duedate, priority, customfield_10947, customfield_11751,customfield_11445,customfield_11312,customfield_10300," \
"parentProject, parent, customfield_10946" # Fields to fetch

# Start with a base JQL query
jql_clauses = []
if PROJECT_KEY:
    jql_clauses.append(f'project = "{PROJECT_KEY}" OR project = "{PROJECT_KEY_2}" OR project = "{PROJECT_KEY_3}"')  # Handle case sensitivity
    print(f"Fetching issues for project '{PROJECT_KEY}', {PROJECT_KEY_2}, {PROJECT_KEY_3}...")
else:
    # Warning: Fetching all issues without a project filter can be very slow
    # and return a huge number of results depending on your Jira instance size
    # and permissions. Consider adding other filters (e.g., status, updatedDate).
    print("Warning: Fetching issues across all accessible projects. This might take a while.")
    # Example: Add a filter for only assigned issues if searching all projects
    # jql_clauses.append("assignee is not EMPTY")

# Combine clauses and add ordering
jql_query = " AND ".join(jql_clauses) if jql_clauses else "" # Handle case with no clauses
jql_query += " ORDER BY assignee ASC, updated DESC"
jql_query = jql_query.strip() # Remove leading/trailing whitespace if needed

print(f"Using JQL: {jql_query if jql_query else 'Searching all issues (no specific JQL)'}")


# --- Fetch and Group Issues by Assignee ---
# Use defaultdict to easily group issues; value is a list of issue details
issues_by_assignee = defaultdict(list)

try:
    #search_params = {'jql': jql_query, 'maxResults': False, 'fields': fields_to_fetch}
    #issues_iterator = jira.search_issues(jql_str=search_params['jql'],
    #                                     maxResults=search_params['maxResults'],
    #                                     fields=search_params['fields'],
    #                                     expand=None,
    #                                     json_result=False) # Get Issue objects

    search_params = {
        "jql": jql_query,
        "maxResults": 5000,
        "fields": fields_to_fetch
    }

    # Correct API endpoint
    url = f"{jira_server}/rest/api/3/search/jql"
    next_token = None
    total_issues_processed = 0
    issues_iterator_all = []
    while True:
        if next_token:
            search_params["nextPageToken"] = next_token
        response = jira._session.get(url,params=search_params)

        issues_iterator = response.json()
        issues_iterator_all = issues_iterator_all + issues_iterator['issues']

        #print(f"Total issues found: {len(issues_iterator.get('issues', []))}")
        
        for issue in issues_iterator.get("issues", []):
            #print(issue["fields"]['status']['name'])
            #print(issue["fields"]['summary'])
            #print(issue["fields"]['assignee'])
            #print(issue["fields"]['assignee']['displayName'] if issue["fields"]['assignee'] else "Unassigned")
            assignee_name = issue["fields"]['assignee']['displayName'] if issue["fields"]['assignee'] else "Unassigned"
            # Store relevant issue info (e.g., key and summary)
            issue_info = {
                "key": issue['key'],
                "summary": issue['fields']['summary']
            }
            #status_name = issue["fields"]["status"]["name"]
            issues_by_assignee[assignee_name].append(issue_info)
            total_issues_processed += 1
            # Optional: Print progress
            if total_issues_processed % 2000 == 0:
                print(f"Processed {total_issues_processed} issues...")
                
        next_token = issues_iterator.get("nextPageToken")
        if not next_token:
            break

    print(f"Finished processing. Found {total_issues_processed} issues in total.")

    if not issues_by_assignee:
        print("No issues found matching the criteria.")
        exit()
    

except Exception as e:
    print(f"An error occurred while fetching issues: {e}")
    if "401" in str(e):
        print("Hint: Check JIRA_EMAIL/API Token.")
    elif "400" in str(e) and "JQL" in str(e):
        print(f"Hint: Check if the JQL query is valid: {jql_query}")
    elif PROJECT_KEY and ("404" in str(e) or "project" in str(e).lower()):
        print(f"Hint: Check Project Key '{PROJECT_KEY}'.")
    exit()


# --- Define JQL Query ---

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


print(f"Start fetching: {PROJECT_KEY}, {PROJECT_KEY_2}, {PROJECT_KEY_3} issues...")
assignee_counts = Counter()
issue_creator_counts = Counter()
status_counts = Counter()
issue_type_counts = Counter()
topic_issues_counts = Counter()
topic_issues_counts_days = 0
processed_data = []
today = pd.to_datetime('today', utc=True, errors='coerce').tz_convert('UTC-06:00')
issues_iterator =issues_iterator_all

for issue in issues_iterator:
    fields = issue['fields']
    #print(f'created {fields["created"]}')
    if fields.get('creator'):
        creator_name = fields['creator']['displayName']
        #creator_email = fields['creator']['emailAddress']
    else:
        creator_name = 'Unknown'
        #creator_email = 'Unknown'

    if fields.get('assignee'):
        assignee_name = fields['assignee']['displayName']
        #assignee_email = fields['assignee']['emailAddress']
    else:
        assignee_name = 'Unassigned'
        #assignee_email = 'Unassigned'

    if fields.get('priority'):
        priority_name = fields['priority']['name']
        priority_id = fields['priority']['id']
    else:
        priority_name = 'No Priority'
        priority_id = 'No Priority'

    if fields.get('duedate'):
        due_date = fields['duedate']
    else:
        due_date = '2000-01-01T00:00:00.000+0000'  # Default date if not set
    try:
        if fields.get(CF_BUSINESS_LEAD):
            bussiness_lead = str(fields[CF_BUSINESS_LEAD]['displayName']).replace('.', ' ')
        else:
            bussiness_lead = 'Unknown'
    except Exception as e:
        bussiness_lead = 'Unknown'
    try:
        if fields.get(CF_PLANNING_RANK):
            planning_rank = fields[CF_PLANNING_RANK]
        else:
            planning_rank = 'Unknown'
    except Exception as e:
        planning_rank = 'Unknown'
    
    try:
        if fields.get(CF_PORTFOLIO_RANK):
            portfolio_rank = fields[CF_PORTFOLIO_RANK]
        else:
            portfolio_rank = pd.NaT  # Use NaT for missing dates
    except Exception as e:
        portfolio_rank = pd.NaT  # Use NaT for missing dates

    try:
        if fields.get(CF_PARENT_LINK):
            legend = fields[CF_PARENT_LINK]
        else:
            legend = pd.NA  # Use NaT for missing dates
    except Exception as e:
        legend = pd.NA  # Use NaT for missing dates

    try:
        if fields.get('parentProject'):
            parent_project = fields['parentProject']
        else:
            parent_project = pd.NA  # Use NaT for missing dates
    except Exception as e:
        parent_project = pd.NA  # Use NaT for missing dates

    try:
        if fields.get('parent'):
            parent = fields['parent']['key']
        else:
            parent = pd.NA  # Use NaT for missing dates
    except Exception as e:
        parent = pd.NA  # Use NaT for missing dates

    issue_data = {
        'key': issue['key'],
        'id': issue['id'],
        'summary': fields.get('summary', ''),
        'status': fields.get('status', {}).get('name', ''),
        'issuetype': fields.get('issuetype', {}).get('name', ''),
        'creator_name': creator_name,
        #'creator_email': creator_email,
        #'reporter_name': fields['reporter']['displayName'] if fields.get('reporter') else 'Unknown',
        #'reporter_email': fields['reporter']['emailAddress'] if fields.get('reporter') else 'Unknown',
        'assignee_name': assignee_name,
        #'assignee_email': assignee_email,
        'created': pd.to_datetime(fields.get('created', None), errors='coerce'),
        'updated': pd.to_datetime(fields.get('updated', None), errors='coerce'),
        'project_name': fields.get('project', {}).get('name', ''),
        'project_id': fields.get('project', {}).get('id', ''),
        'priority_name': priority_name,
        'priority_id': priority_id,
        'project_due_date' : pd.to_datetime(due_date, utc=True, errors='coerce').tz_convert('UTC-06:00'),
        'issue_url' : issue.get('permalink', lambda: None)(),
        'today' : pd.to_datetime('today', utc=True, errors='coerce').tz_convert('UTC-06:00'),
        'days_old' : (pd.to_datetime('today', utc=True, errors='coerce').tz_convert('UTC-06:00') - pd.to_datetime(fields.get('created', None), errors='coerce')).days,
        'year_updated' : pd.to_datetime(fields.get('updated', None), errors='coerce').year,
        'month_updated' : pd.to_datetime(fields.get('updated', None), errors='coerce').month,
        'year_created' : pd.to_datetime(fields.get('created', None), errors='coerce').year,
        'month_created' : pd.to_datetime(fields.get('created', None), errors='coerce').month,
        'last_updated_days' : (pd.to_datetime('today', utc=True, errors='coerce').tz_convert('UTC-06:00') - pd.to_datetime(fields.get('updated', None), errors='coerce')).days,
        'project_due_date' : pd.to_datetime(due_date, utc=True, errors='coerce').tz_convert('UTC-06:00'),
        'project_due_date_days' : (pd.to_datetime(due_date, utc=True, errors='coerce').tz_convert('UTC-06:00') - pd.to_datetime(fields.get('updated', None), errors='coerce')).days if pd.to_datetime(due_date, utc=True, errors='coerce') is not pd.NaT and pd.to_datetime(fields.get('updated', None), errors='coerce') is not pd.NaT and pd.to_datetime(due_date, utc=True, errors='coerce') is not None and due_date != '2000-01-01T00:00:00.000+0000' else None,
        'velocity' : (pd.to_datetime(fields.get('updated', None), errors='coerce') - pd.to_datetime(fields.get('created', None), errors='coerce')).days,
        'velocity_hours' : (pd.to_datetime(fields.get('updated', None), errors='coerce') - pd.to_datetime(fields.get('created', None), errors='coerce')).total_seconds() / 3600,
        'velocity_days' : (pd.to_datetime(fields.get('updated', None), errors='coerce') - pd.to_datetime(fields.get('created', None), errors='coerce')).days,
        'target_end_date' : pd.to_datetime(fields.get('customfield_10947', None), utc=True, errors='coerce'),

        #'velocity_backlog' : (pd.to_datetime(fields.get('customfield_10946', pd.NaT), errors='coerce') - pd.to_datetime(fields.get('created', None), errors='coerce')).days if pd.to_datetime(fields.get('customfield_10946', pd.NaT), errors='coerce') is not pd.NaT and pd.to_datetime(fields.get('created', None), errors='coerce') is not pd.NaT else None,
        #'velocity_backlog_hours' : (pd.to_datetime(fields.get('customfield_10946', pd.NaT), errors='coerce') - pd.to_datetime(fields.get('created', None), errors='coerce')).total_seconds() / 3600 if pd.to_datetime(fields.get('customfield_10946', None), errors='coerce') is not pd.NaT and pd.to_datetime(fields.get('created', None), errors='coerce') is not pd.NaT else None,
        #'velocity_backlog_days' : (pd.to_datetime(fields.get('customfield_10946', pd.NaT), errors='coerce') - pd.to_datetime(fields.get('created', None), errors='coerce')).days if pd.to_datetime(fields.get('customfield_10946', None), errors='coerce') is not pd.NaT and pd.to_datetime(fields.get('created', None), errors='coerce') is not pd.NaT else None,

        'planned_start_date' : pd.to_datetime(fields.get('customfield_10946', pd.NaT), utc=True, errors='coerce'),
        'bussiness_lead' : bussiness_lead,
        'planning_rank' : planning_rank,
        'portfolio_rank' : portfolio_rank,
        'legend' : legend,
        'parent_project' : parent_project,
        'parent' : parent,
        #'target_end_days' : (pd.to_datetime(fields.get('customfield_10947', None), utc=True, errors='coerce').tz_convert('UTC-06:00') - pd.to_datetime(fields.get('updated', None), errors='coerce')).days,

        # Add more fields as needed, including custom fields
        # 'customfield_xxxxx': fields.get('customfield_xxxxx')
    }
    processed_data.append(issue_data)

    # Use fields['assignee'] instead of issue['assignee']
    if fields.get('assignee'):
        assignee_counts[fields['assignee']['displayName']] += 1
    else:
        assignee_counts['Unassigned'] += 1

    if fields.get('issuetype'):
        issue_type_counts[fields['issuetype']['name']] += 1
    if fields.get('status'):
        status_counts[fields['status']['name']] += 1
    if fields.get('creator'):
        issue_creator_counts[fields['creator']['displayName']] += 1
    

df_issues = pd.DataFrame(processed_data)

# Ensure datetime columns are properly converted
df_issues['planned_start_date'] = pd.to_datetime(df_issues['planned_start_date'], errors='coerce')
df_issues['planned_start_date'] = df_issues['planned_start_date'].fillna('2000-12-31 00:00:00')
df_issues['planned_start_date'] = df_issues['planned_start_date'].dt.tz_convert('UTC-06:00')
# Keep NaT values instead of filling with string

#df_issues['created'] = pd.to_datetime(df_issues['created'], errors='coerce')

# Calculate velocity_backlog with proper handling of NaT values
df_issues['velocity_backlog'] = df_issues['planned_start_date'] - df_issues['created']
# Only calculate hours for valid timedelta values
#df_issues['velocity_backlog_hours'] = df_issues['velocity_backlog'].dt.total_seconds() / 3600
# Fix: Extract days from timedelta, handling mixed types
df_issues['velocity_backlog_days'] = df_issues['velocity_backlog'].apply(
	lambda x: x.days if pd.notnull(x) and hasattr(x, 'days') else None
)
df_issues['velocity_backlog_hours'] = df_issues['velocity_backlog_days'] / 24 if 'velocity_backlog_days' in df_issues else None
# fixign negaive days with 0 and hours wiht 0
df_issues.loc[(df_issues['velocity_backlog_days'] < 0, 'velocity_backlog_days')] = 0
df_issues.loc[(df_issues['velocity_backlog_hours'] < 0, 'velocity_backlog_hours')] = 0


# Calculate days until due date
df_issues.loc[(df_issues['last_updated_days'] < 0, 'last_updated_days')] = 0

return df_issues


