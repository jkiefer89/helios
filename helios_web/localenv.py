"""Repo-local .env loading, applied before the engine reads the environment."""
from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent


def _truthy_env(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _load_local_env_file(path: Path | None = None) -> dict:
    """Load local .env settings without overriding the process environment.

    This keeps secrets out of source control while making local server starts
    behave the way users expect after creating a repo-local .env file.
    """
    if not _truthy_env(os.environ.get("HELIOS_LOAD_DOTENV"), default=True):
        return {"loaded": False, "path": "", "count": 0}
    env_path = path or APP_ROOT / ".env"
    if not env_path.is_file():
        return {"loaded": False, "path": str(env_path), "count": 0}
    loaded = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not all(ch.isalnum() or ch == "_" for ch in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return {"loaded": True, "path": str(env_path), "count": loaded}
