"""darkstar configuration, loaded from environment variables.

Jira credentials are required (fail loud if absent). Operational settings have
documented env-driven defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_DB_PATH: str = "/data/darkstar.duckdb"


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the poller and the app."""

    jira_server: str
    jira_email: str
    jira_api_token: str
    db_path: str
    poll_interval_seconds: int


def _require(name: str) -> str:
    """Return a required environment variable, raising if it is unset or empty."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def load_config() -> Config:
    """Build Config from the environment (used by the poller; requires Jira secrets)."""
    return Config(
        jira_server=_require("JIRA_SERVER"),
        jira_email=_require("JIRA_EMAIL"),
        jira_api_token=_require("JIRA_API_TOKEN"),
        db_path=os.environ.get("DARKSTAR_DB_PATH", _DEFAULT_DB_PATH),
        poll_interval_seconds=int(os.environ.get("DARKSTAR_POLL_INTERVAL_SECONDS", "900")),
    )


def db_path() -> str:
    """Store path for read-only consumers (the app); no Jira secrets required."""
    return os.environ.get("DARKSTAR_DB_PATH", _DEFAULT_DB_PATH)


def overrides_path() -> str:
    """Path to the shared SME-overrides JSON, alongside the store on the PVC by default."""
    explicit = os.environ.get("DARKSTAR_OVERRIDES_PATH")
    if explicit:
        return explicit
    return os.path.join(os.path.dirname(db_path()) or ".", "darkstar_overrides.json")
