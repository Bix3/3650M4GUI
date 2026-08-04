"""
IMM2 KVM Client Configuration Persistence

Stores connection settings (host, user, password) as JSON in `config.json` in
the current working directory. Host defaults to empty; user/password fall back
to the IMM2 factory defaults (USERID/PASSW0RD) when not configured.

NOTE: the password is stored in plaintext by design — this tool targets a lab
IMM2 with factory credentials.
"""

import json
from pathlib import Path

DEFAULT_USER = "USERID"
DEFAULT_PASSWORD = "PASSW0RD"

CONFIG_FILENAME = "config.json"


def config_path() -> Path:
    return Path.cwd() / CONFIG_FILENAME
def load() -> dict:
    """Return saved string values; defaults applied by the caller."""
    try:
        data = json.loads(config_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def save(host: str, user: str, password: str) -> None:
    """Persist connection settings, creating the config directory as needed."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"host": host, "user": user, "password": password}, indent=2
        )
        + "\n"
    )
