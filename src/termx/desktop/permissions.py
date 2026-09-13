from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from typing import Any

_SCREEN_RECORDING = "screen_recording"
_ACCESSIBILITY = "accessibility"


def _bool_status(callback: Any) -> str:
    try:
        return "granted" if callback() else "denied"
    except Exception:
        return "unknown"


def _core_graphics() -> Any | None:
    try:
        path = ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        return ctypes.CDLL(path)
    except OSError:
        return None


def _application_services() -> Any | None:
    try:
        path = (
            ctypes.util.find_library("ApplicationServices")
            or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        return ctypes.CDLL(path)
    except OSError:
        return None


def _screen_recording_status() -> str:
    cg = _core_graphics()
    if cg is None or not hasattr(cg, "CGPreflightScreenCaptureAccess"):
        return "unknown"
    cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    return _bool_status(lambda: cg.CGPreflightScreenCaptureAccess())


def _accessibility_status() -> str:
    svc = _application_services()
    if svc is None or not hasattr(svc, "AXIsProcessTrusted"):
        return "unknown"
    svc.AXIsProcessTrusted.restype = ctypes.c_bool
    return _bool_status(lambda: svc.AXIsProcessTrusted())


def permission_snapshot() -> dict[str, Any]:
    """Report permission state without prompting. Safe to call on any platform."""
    if sys.platform == "darwin":
        return {
            _SCREEN_RECORDING: _screen_recording_status(),
            _ACCESSIBILITY: _accessibility_status(),
        }
    if sys.platform.startswith("linux"):
        if os.environ.get("WAYLAND_DISPLAY"):
            return {"screen_capture": "portal", "input": "portal"}
        if os.environ.get("DISPLAY"):
            return {"screen_capture": "x11", "input": "x11"}
        return {"screen_capture": "unknown", "input": "unknown"}
    return {}


def request_permissions(which: list[str] | None = None) -> dict[str, Any]:
    """Return the current state only.

    Prompting used to run here, but TCC APIs invoked from the Python sidecar were
    killed by macOS and were attributed to the wrong process. The desktop shell
    now owns prompting from the Termx.app process; this stays read-only.
    """
    return permission_snapshot()
