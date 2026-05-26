import sys

from .load_configuration import load_config, load_config_details


def validate_jira_connection():
    """Validate Jira configuration and connection.

    Returns:
        tuple: (0, success_message, jira_connector) on success
        tuple: (1, error_message, None) on failure
    """
    try:
        from jira import JIRA
    except ModuleNotFoundError:
        return 1, "Missing dependency: jira. Install with: pip install jira", None

    config_details = load_config_details()
    config = config_details.get("config")
    source = config_details.get("source")
    if config is None:
        return (
            1,
            "Configuration load failed. Set Jira credentials in Streamlit secrets or .env (config.json is fallback only).",
            None,
        )

    jira_server = config.get("jira_server")
    jira_email = config.get("jira_email")
    jira_token = config.get("jira_api_token")
    jira_username = config.get("jira_username", jira_email)
    jira_password = config.get("jira_password", jira_token)
    auth_type = config.get("auth_type", "pat")

    if not jira_server:
        return 1, "Missing required setting: jira_server", None

    jira_options = {"server": jira_server}

    try:
        if auth_type == "pat":
            jira = JIRA(options=jira_options, basic_auth=(jira_email, jira_token))
            return 0, f"Successfully connected to Jira at {jira_server} using Personal Access Token ({source}).", jira

        if auth_type == "basic":
            jira = JIRA(options=jira_options, basic_auth=(jira_username, jira_password))
            return 0, f"Successfully connected to Jira at {jira_server} using Basic Authentication ({source}).", jira
        
        return 1, f"Invalid authentication type selected: {auth_type}", None
    except Exception as err:
        return 1, f"Error connecting to Jira: {err}", None


def main() -> int:
    code, message, jira = validate_jira_connection()
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
