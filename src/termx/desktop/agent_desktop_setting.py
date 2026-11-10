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


def valid_path(value: object) -> str | None:
    """``value`` when it can be a program path (absolute, printable, at most MAX_PATH, no NUL), otherwise None."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_PATH or "\x00" in value or not value.isprintable():
        return None
    return value if os.path.isabs(value) else None


def load_setting(home_root: Path) -> str | None:
    try:
        body = json.loads((Path(home_root) / FILE_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return valid_path(body.get("path")) if isinstance(body, dict) else None


def save_setting(home_root: Path, path: str | None) -> None:
    """Save an absolute path, or clear the setting with ``None``."""
    target = Path(home_root) / FILE_NAME
    if path is None:
        target.unlink(missing_ok=True)
        return
    if valid_path(path) is None:
        raise ValueError("The path must be an absolute path to the program.")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        protect_private_path(target.parent, directory=True)
    temp = target.with_name(FILE_NAME + ".new")
    temp.unlink(missing_ok=True)
    # Created private (not narrowed afterwards) so the file is never readable by others, not even briefly.
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(json.dumps({"path": path}))
    if os.name == "nt":
        protect_private_path(temp)
    os.replace(temp, target)
