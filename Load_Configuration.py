# --- Configuration File Handling ---
import os
import json

CONFIG_FILENAME = "config.json"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config(filepath=CONFIG_FILENAME):
    """
    Loads configuration data from a JSON file.

    Args:
        filepath (str): The path to the configuration file.
                        Defaults to CONFIG_FILENAME in the script's directory.

    Returns:
        dict: A dictionary containing the configuration data.
            Returns None if the file cannot be read or parsed.
    """
    if os.path.isabs(filepath):
        config_path = filepath
    else:
        config_path = os.path.join(BASE_DIR, filepath)  # Look next to this file

    if not os.path.exists(config_path):
        print(f"Error: Configuration file not found at '{config_path}'")
        print("Please create the file with your Jira server, email, and API token.")
        return None

    try:
        with open(config_path, 'r') as f:
            config_data = json.load(f)
            # Basic validation (check if essential keys exist)
            required_keys = ["jira_server", "jira_email", "jira_api_token"]
            if not all(key in config_data for key in required_keys):
                missing = [key for key in required_keys if key not in config_data]
                print(f"Error: Configuration file '{config_path}' is missing required keys: {missing}")
                return None
            # Optional: Add more specific validation (e.g., check if token is not empty)
            if not config_data.get("jira_api_token"):
                print(f"Error: 'jira_api_token' found in '{config_path}' but it is empty.")
                return None

            print(f"Successfully loaded configuration from '{config_path}'")
            return config_data

    except json.JSONDecodeError as e:
        print(f"Error: Could not parse JSON in configuration file '{config_path}'.")
        print(f"Details: {e}")
        return None
    except Exception as e:
        print(f"Error: An unexpected error occurred while reading '{config_path}'.")
        print(f"Details: {e}")
        return None
