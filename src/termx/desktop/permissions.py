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
        from termx.desktop import broker

        # The shell process owns the app's TCC identity; its answer is the only
        # one that matches what macOS will actually enforce.
        state = broker.status()
        if state is not None:
            return {
                _SCREEN_RECORDING: "granted" if state.get("screen_recording") else "denied",
                _ACCESSIBILITY: "granted" if state.get("accessibility") else "denied",
            }
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
    """Trigger the system prompts and return the resulting state.

    Prompting happens in the Termx.app process through the privileged broker:
    TCC records the grant against the process that asks, and only the app's own
    identity is the one the user manages in System Settings.
    """
    if sys.platform == "darwin":
        from termx.desktop import broker

        if broker.available():
            for item in which or [_SCREEN_RECORDING, _ACCESSIBILITY]:
                broker.request_permission(item)
    return permission_snapshot()
