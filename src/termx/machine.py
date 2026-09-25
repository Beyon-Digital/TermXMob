from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from typing import Any

from termx.config import ConfigStore, available_shells
from termx.desktop.capabilities import probe_desktop


def hostname() -> str:
    return socket.gethostname()


def os_name() -> str:
    return sys.platform


def machine_snapshot(
    store: ConfigStore,
    tunnel_status: dict[str, Any] | None = None,
    *,
    webrtc: bool = False,
) -> dict[str, Any]:
    desktop = probe_desktop()
    prefs = store.get().terminal
    providers = {
        "cloudflare": shutil.which("cloudflared") is not None,
        "ngrok": shutil.which("ngrok") is not None,
        "tailscale": shutil.which("tailscale") is not None,
    }
    return {
        "hostname": hostname(),
        "os": os_name(),
        "arch": platform.machine(),
        "shells": available_shells(),
        "terminal": {"shell": prefs.shell, "cwd": prefs.cwd},
        "permissions": desktop.permissions,
        "helper": desktop.helper,
        "capabilities": {
            "saved_commands": True,
            "saved_directories": True,
            "session_defaults": True,
            "dynamic_tunnels": True,
            "remote_screen": desktop.remote_screen,
            "virtual_display": desktop.virtual_display,
            "webrtc": webrtc,
            "agent": True,
            "agent_shell": True,
            "agent_computer": desktop.remote_screen,
            "editor": True,
            "editor_git": shutil.which("git") is not None,
            "editor_lsp": True,
            "editor_previews": True,
            "providers": providers,
        },
        "tunnel": tunnel_status,
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
    }
