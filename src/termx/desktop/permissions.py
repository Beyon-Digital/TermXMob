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


def _request_screen_recording() -> str:
    cg = _core_graphics()
    if cg is None or not hasattr(cg, "CGRequestScreenCaptureAccess"):
        return "unknown"
    cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
    return _bool_status(lambda: cg.CGRequestScreenCaptureAccess())


def _accessibility_status() -> str:
    svc = _application_services()
    if svc is None or not hasattr(svc, "AXIsProcessTrusted"):
        return "unknown"
    svc.AXIsProcessTrusted.restype = ctypes.c_bool
    return _bool_status(lambda: svc.AXIsProcessTrusted())


def _request_accessibility() -> str:
    svc = _application_services()
    cf = None
    try:
        path = ctypes.util.find_library("CoreFoundation") or (
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        cf = ctypes.CDLL(path)
    except OSError:
        cf = None
    if svc is None or cf is None or not hasattr(svc, "AXIsProcessTrustedWithOptions"):
        return _accessibility_status()
    try:
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        key = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", 0x08000100)
        value = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        keys = (ctypes.c_void_p * 1)(key)
        values = (ctypes.c_void_p * 1)(value)
        options = cf.CFDictionaryCreate(None, keys, values, 1, None, None)
        svc.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        svc.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        return _bool_status(lambda: svc.AXIsProcessTrustedWithOptions(options))
    except Exception:
        return _accessibility_status()


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
    """Trigger native permission prompts where supported, then return the snapshot."""
    if sys.platform != "darwin":
        return permission_snapshot()
    wanted = set(which or [_SCREEN_RECORDING, _ACCESSIBILITY])
    if _SCREEN_RECORDING in wanted:
        _request_screen_recording()
    if _ACCESSIBILITY in wanted:
        _request_accessibility()
    return permission_snapshot()
