"""Where the owner's explicit path to an agent-desktop program is kept.

The environment variable ``TERMX_AGENT_DESKTOP`` wins. Otherwise the path saved here by the host-admin API is used.
There is deliberately no ordinary config key: a program path is a code-execution setting, and the general config file
is something file tools can edit. This file lives under ``desktop-control/``, which the agent's file policy treats as
a sensitive location. That is a guard against accidents, not a boundary against a process that runs as the owner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from termx.desktop.agent_desktop import SETTING_ENV
from termx.private_files import protect_private_path

FILE_NAME = "setting.json"
MAX_PATH = 1024


def env_locked() -> bool:
    return bool(os.environ.get(SETTING_ENV))


def _valid(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_PATH or "\x00" in value or not value.isprintable():
        return None
    return value if os.path.isabs(value) else None


def load_setting(home_root: Path) -> str | None:
    try:
        body = json.loads((Path(home_root) / FILE_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return _valid(body.get("path")) if isinstance(body, dict) else None


def save_setting(home_root: Path, path: str | None) -> None:
    """Save an absolute path, or clear the setting with ``None``."""
    target = Path(home_root) / FILE_NAME
    if path is None:
        target.unlink(missing_ok=True)
        return
    if _valid(path) is None:
        raise ValueError("The path must be an absolute path to the program.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(FILE_NAME + ".new")
    temp.write_text(json.dumps({"path": path}), "utf-8")
    try:
        protect_private_path(temp)
    except OSError:
        pass
    os.replace(temp, target)
