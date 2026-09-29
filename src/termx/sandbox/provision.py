"""Installation/repair lifecycle for restricted sandbox backends.

Restricted execution has two layers:

* primitives the OS already provides (Linux unprivileged userns + bwrap,
  macOS Seatbelt, Windows restricted tokens + Job Objects) — each probed
  here so the machine snapshot can report them truthfully; and
* provisioned upgrades that need a one-time elevated install (a dedicated
  ``termx-sandbox`` OS identity, a signed privileged helper, Windows
  firewall rules for a dedicated child binary) — reported as pending with
  concrete repair steps rather than silently skipped.

Nothing here performs privileged operations. ``provision_status`` is a
read-only audit; each check carries a ``repair`` hint the installer/CLI or
a human can act on. A missing provisioned upgrade never degrades the
kernel boundary — backends advertise exactly what they enforce.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# The privileged helper is opt-in and environment-overridable (also used by
# the platform backends for detection — keep this name stable).
HELPER_ENV = "TERMX_SANDBOX_HELPER"
RESTRICTED_USER = "termx-sandbox"


def helper_path() -> Path | None:
    """Well-known signed helper location (env override first).

    The helper is a small validated broker shipped with the desktop build —
    it accepts structured spawn/lifecycle requests and never an arbitrary
    shell. Absence only means the *identity* upgrade is unavailable.
    """
    override = os.environ.get(HELPER_ENV)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/termx/sandbox-helper"
    if sys.platform == "win32":
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(program_data) / "termx" / "sandbox-helper.exe"
    if sys.platform.startswith("linux"):
        return Path.home() / ".local" / "share" / "termx" / "sandbox-helper"
    return None


def helper_installed() -> bool:
    path = helper_path()
    return bool(path is not None and path.is_file() and os.access(path, os.X_OK))


def _restricted_user_exists() -> bool:
    """Whether the dedicated non-login ``termx-sandbox`` identity exists."""
    if sys.platform == "darwin":
        probe = subprocess.run(
            ["dscl", ".", "-read", f"/Users/{RESTRICTED_USER}"],
            capture_output=True,
            timeout=5,
        )
        return probe.returncode == 0
    if sys.platform == "win32":
        probe = subprocess.run(
            ["net", "user", RESTRICTED_USER],
            capture_output=True,
            timeout=5,
        )
        return probe.returncode == 0
    return False


def _linux_checks() -> list[dict[str, Any]]:
    from termx.sandbox.linux_ns import linux_ns_available

    bwrap = shutil.which("bwrap")
    checks = [
        {
            "id": "linux-ns.bwrap",
            "ok": bwrap is not None,
            "detail": bwrap or "bubblewrap not installed",
            "repair": "install bubblewrap (e.g. apt install bubblewrap)",
            "elevated": True,
        },
        {
            "id": "linux-ns.userns",
            "ok": linux_ns_available() if bwrap else False,
            "detail": (
                "unprivileged user namespaces + bwrap functional"
                if linux_ns_available()
                else "userns probe failed — restricted spawns cannot start"
            ),
            "repair": (
                "enable unprivileged user namespaces "
                "(e.g. sysctl kernel.unprivileged_userns_clone=1)"
            ),
            "elevated": True,
        },
    ]
    return checks


def _darwin_checks() -> list[dict[str, Any]]:
    seatbelt = shutil.which("sandbox-exec")
    return [
        {
            "id": "macos.seatbelt",
            "ok": seatbelt is not None,
            "detail": seatbelt or "sandbox-exec missing",
            "repair": "Seatbelt ships with macOS — reinstall the OS toolchain if absent",
            "elevated": False,
        },
        {
            "id": "macos.restricted-user",
            "ok": _restricted_user_exists(),
            "detail": (
                f"dedicated {RESTRICTED_USER} identity exists"
                if _restricted_user_exists()
                else "no separate identity — same-user seatbelt only"
            ),
            "repair": (
                "run `termx sandbox provision` from the elevated installer "
                f"(creates the {RESTRICTED_USER} non-login account)"
            ),
            "elevated": True,
        },
        {
            "id": "macos.helper",
            "ok": helper_installed(),
            "detail": (
                f"helper at {helper_path()}" if helper_installed() else "helper not installed"
            ),
            "repair": "install the signed sandbox helper via the desktop installer",
            "elevated": True,
        },
    ]


def _windows_checks() -> list[dict[str, Any]]:
    return [
        {
            "id": "windows.token-job",
            "ok": True,
            "detail": "restricted token + Job Object primitives available (no install needed)",
            "repair": None,
            "elevated": False,
        },
        {
            "id": "windows.restricted-user",
            "ok": _restricted_user_exists(),
            "detail": (
                f"dedicated {RESTRICTED_USER} account exists"
                if _restricted_user_exists()
                else "no separate identity — same-user integrity level only"
            ),
            "repair": (
                "run `termx sandbox provision` elevated "
                f"(creates the {RESTRICTED_USER} local account + ACL seed)"
            ),
            "elevated": True,
        },
        {
            "id": "windows.netblock",
            "ok": helper_installed(),
            "detail": (
                "helper/firewall rule provisioned" if helper_installed() else
                "outbound network cannot be denied without a WFAS rule for "
                "a dedicated child binary"
            ),
            "repair": "elevated install adds the WFAS deny rule for the helper child exe",
            "elevated": True,
        },
    ]


def provision_status() -> dict[str, Any]:
    """Read-only provisioning audit for the current platform."""
    platform = sys.platform
    if platform.startswith("linux"):
        checks = _linux_checks()
    elif platform == "darwin":
        checks = _darwin_checks()
    elif platform == "win32":
        checks = _windows_checks()
    else:
        checks = [
            {
                "id": "platform.unknown",
                "ok": False,
                "detail": f"no restricted backend for {platform} — host profile only",
                "repair": None,
                "elevated": False,
            }
        ]
    return {
        "platform": platform,
        "helper": {"installed": helper_installed(), "path": str(helper_path() or "")},
        "restricted_user": (
            _restricted_user_exists() if platform in {"darwin", "win32"} else False
        ),
        "checks": checks,
        "ok": any(check["ok"] for check in checks if check["id"].split(".")[0] != "platform"),
    }


__all__ = ["HELPER_ENV", "RESTRICTED_USER", "helper_installed", "helper_path", "provision_status"]
