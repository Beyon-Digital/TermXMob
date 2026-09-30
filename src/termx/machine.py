from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from typing import Any

from termx.config import ConfigStore, available_shells
from termx.desktop.capabilities import probe_desktop


def _execution_sandbox() -> dict[str, Any]:
    """Report what each profile's selected backend actually enforces.

    Never claims isolation the runner does not provide — a host fallback
    reports strength "none"; linux-ns reports "kernel" with its real controls.
    """
    from termx.sandbox import runner_for

    profiles: dict[str, Any] = {}
    try:
        for profile in ("host", "workspace", "agent"):
            caps = runner_for(profile).capabilities().public()
            caps["profile"] = profile
            profiles[profile] = caps
        restricted = profiles.get("agent") or {}
        return {
            "backend": restricted.get("backend", "host"),
            "strength": restricted.get("strength", "none"),
            "profiles": profiles,
            "network_control": bool(restricted.get("network_control")),
            "filesystem_isolation": bool(restricted.get("filesystem_isolation")),
            "identity_isolation": bool(restricted.get("identity_isolation")),
            "resource_limits": bool(restricted.get("resource_limits")),
        }
    except Exception:
        return {
            "backend": "unknown",
            "strength": "none",
            "profiles": {},
            "network_control": False,
            "filesystem_isolation": False,
            "identity_isolation": False,
            "resource_limits": False,
        }


def _process_discovery_supported() -> bool:
    from termx.processes import supported

    try:
        return supported()
    except Exception:
        return False


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
            "agent_tools_v2": True,
            "agent_context_v2": True,
            "agent_parallel_tools": True,
            "agent_streaming": True,
            "agent_recovery": True,
            "agent_subagents": True,
            "computer_observation_v2": True,
            "conversations": True,
            "custom_agents": True,
            "agent_worktrees": True,
            "activity": True,
            "process_discovery": _process_discovery_supported(),
            "runbooks": True,
            "device_scopes_v2": True,
            "remembered_approvals": True,
            "policy_engine_v2": True,
            "editor": True,
            "editor_git": shutil.which("git") is not None,
            "editor_lsp": True,
            "editor_previews": True,
            "providers": providers,
            # Truthful per-profile sandbox report — whatever the runner
            # actually selected advertises, nothing more.
            "execution_sandbox": _execution_sandbox(),
        },
        "tunnel": tunnel_status,
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
    }
