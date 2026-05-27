# --- Configuration Handling (Streamlit secrets, env vars, and config.json fallback) ---
import json
import os

CONFIG_FILENAME = "config.json"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REQUIRED_KEYS = ["jira_server", "jira_email", "jira_api_token"]

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)


def _first_non_empty(values):
    for value in values:
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _normalize_config(raw: dict | None) -> dict:
    if not raw:
        return {}

    normalized = {
        "jira_server": _first_non_empty([
            raw.get("jira_server"),
            raw.get("JIRA_SERVER"),
            raw.get("server"),
        ]),
        "jira_email": _first_non_empty([
            raw.get("jira_email"),
            raw.get("JIRA_EMAIL"),
            raw.get("email"),
        ]),
        "jira_api_token": _first_non_empty([
            raw.get("jira_api_token"),
            raw.get("JIRA_API_TOKEN"),
            raw.get("jira_token"),
            raw.get("token"),
        ]),
        "jira_username": _first_non_empty([
            raw.get("jira_username"),
            raw.get("JIRA_USERNAME"),
            raw.get("username"),
            raw.get("jira_email"),
            raw.get("JIRA_EMAIL"),
        ]),
        "jira_password": _first_non_empty([
            raw.get("jira_password"),
            raw.get("JIRA_PASSWORD"),
            raw.get("password"),
            raw.get("jira_api_token"),
            raw.get("JIRA_API_TOKEN"),
        ]),
        "auth_type": _first_non_empty([
            raw.get("auth_type"),
            raw.get("JIRA_AUTH_TYPE"),
            "pat",
        ]),
    }

    # Remove keys with None to keep payload clean.
    return {k: v for k, v in normalized.items() if v is not None}


def _missing_required(config: dict) -> list[str]:
    return [key for key in REQUIRED_KEYS if not config.get(key)]


def _load_from_streamlit_secrets() -> dict:
    try:
        import streamlit as st
    except ModuleNotFoundError:
        return {}

    try:
        secret_root = st.secrets
        jira_section = secret_root.get("jira", {}) if hasattr(secret_root, "get") else {}
        raw = {
            "jira_server": _first_non_empty([
                jira_section.get("jira_server") if hasattr(jira_section, "get") else None,
                jira_section.get("server") if hasattr(jira_section, "get") else None,
                secret_root.get("jira_server") if hasattr(secret_root, "get") else None,
                secret_root.get("JIRA_SERVER") if hasattr(secret_root, "get") else None,
            ]),
            "jira_email": _first_non_empty([
                jira_section.get("jira_email") if hasattr(jira_section, "get") else None,
                jira_section.get("email") if hasattr(jira_section, "get") else None,
                secret_root.get("jira_email") if hasattr(secret_root, "get") else None,
                secret_root.get("JIRA_EMAIL") if hasattr(secret_root, "get") else None,
            ]),
            "jira_api_token": _first_non_empty([
                jira_section.get("jira_api_token") if hasattr(jira_section, "get") else None,
                jira_section.get("api_token") if hasattr(jira_section, "get") else None,
                secret_root.get("jira_api_token") if hasattr(secret_root, "get") else None,
                secret_root.get("JIRA_API_TOKEN") if hasattr(secret_root, "get") else None,
            ]),
            "jira_username": _first_non_empty([
                jira_section.get("jira_username") if hasattr(jira_section, "get") else None,
                jira_section.get("username") if hasattr(jira_section, "get") else None,
                secret_root.get("jira_username") if hasattr(secret_root, "get") else None,
                secret_root.get("JIRA_USERNAME") if hasattr(secret_root, "get") else None,
            ]),
            "jira_password": _first_non_empty([
                jira_section.get("jira_password") if hasattr(jira_section, "get") else None,
                jira_section.get("password") if hasattr(jira_section, "get") else None,
                secret_root.get("jira_password") if hasattr(secret_root, "get") else None,
                secret_root.get("JIRA_PASSWORD") if hasattr(secret_root, "get") else None,
            ]),
            "auth_type": _first_non_empty([
                jira_section.get("auth_type") if hasattr(jira_section, "get") else None,
                secret_root.get("auth_type") if hasattr(secret_root, "get") else None,
                secret_root.get("JIRA_AUTH_TYPE") if hasattr(secret_root, "get") else None,
            ]),
        }
        return _normalize_config(raw)
    except Exception:
        # Includes StreamlitSecretNotFoundError when secrets.toml is absent.
        return {}


def _load_from_environment() -> dict:
    raw = {
        "jira_server": os.getenv("JIRA_SERVER") or os.getenv("jira_server"),
        "jira_email": os.getenv("JIRA_EMAIL") or os.getenv("jira_email"),
        "jira_api_token": os.getenv("JIRA_API_TOKEN") or os.getenv("jira_api_token"),
        "jira_username": os.getenv("JIRA_USERNAME") or os.getenv("jira_username"),
        "jira_password": os.getenv("JIRA_PASSWORD") or os.getenv("jira_password"),
        "auth_type": os.getenv("JIRA_AUTH_TYPE") or os.getenv("auth_type"),
    }
    return _normalize_config(raw)


def _load_from_config_json(filepath=CONFIG_FILENAME) -> dict:
    candidate_paths = []
    if os.path.isabs(filepath):
        candidate_paths.append(filepath)
    else:
        candidate_paths.extend([
            os.path.join(BASE_DIR, filepath),
            os.path.join(os.path.dirname(BASE_DIR), filepath),
        ])

    for config_path in candidate_paths:
        if not os.path.exists(config_path):
            continue

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return _normalize_config(json.load(f))
        except Exception:
            return {}

    return {}


def load_config_details(filepath=CONFIG_FILENAME) -> dict:
    """Load Jira credentials using provider precedence:
    1) Streamlit secrets, 2) environment/.env, 3) config.json fallback.
    """
    providers = [
        ("streamlit_secrets", _load_from_streamlit_secrets()),
        ("environment", _load_from_environment()),
        ("config_json", _load_from_config_json(filepath=filepath)),
    ]

    for source, cfg in providers:
        if not cfg:
            continue
        missing = _missing_required(cfg)
        if not missing:
            return {
                "config": cfg,
                "source": source,
                "missing": [],
            }

    return {
        "config": None,
        "source": None,
        "missing": REQUIRED_KEYS,
    }


def load_config(filepath=CONFIG_FILENAME):
    """Backward compatible config loader used by existing modules."""
    details = load_config_details(filepath=filepath)
    return details["config"]
