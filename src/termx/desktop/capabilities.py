from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

from termx.desktop.paths import resolve_macos_helper
from termx.desktop.virtual import active_adapter


@dataclass
class DesktopProbe:
    remote_screen: bool = False
    virtual_display: bool = False
    capture_backend: str | None = None
    input_backend: str | None = None
    virtual_backend: str | None = None
    permissions: dict[str, Any] = field(default_factory=dict)
    helper: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


def capture_helper_path() -> str | None:
    return resolve_macos_helper("termx-capture", "TERMX_CAPTURE_BIN")


def probe_desktop() -> DesktopProbe:
    helper = _helper_dict()
    adapter = active_adapter()
    virtual_display = bool(adapter and adapter.can_create())
    virtual_backend = adapter.id if adapter else None
    capture = _select_capture_backend()
    if sys.platform == "darwin":
        return DesktopProbe(
            remote_screen=capture is not None,
            virtual_display=virtual_display,
            capture_backend=capture,
            input_backend="cgevent",
            virtual_backend=virtual_backend,
            permissions={
                "screen_recording": "unknown",
                "accessibility": "unknown",
            },
            helper=helper,
            reason=None if capture else "No capture tool (termx-capture, ffmpeg, screencapture)",
        )
    if sys.platform.startswith("linux"):
        wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        x11 = bool(os.environ.get("DISPLAY"))
        input_backend = None
        if shutil.which("xdotool") and x11:
            input_backend = "xdotool"
        elif shutil.which("ydotool"):
            input_backend = "ydotool"
        reason = None
        if not capture:
            reason = "No capture tool (termx-capture, ffmpeg, grim, maim, import)"
        elif not input_backend:
            reason = "No input tool (xdotool or ydotool)"
        return DesktopProbe(
            remote_screen=capture is not None and input_backend is not None,
            virtual_display=virtual_display,
            capture_backend=capture,
            input_backend=input_backend,
            virtual_backend=virtual_backend,
            permissions={
                "portal": "unknown" if wayland else "n/a",
                "display": "wayland" if wayland else "x11" if x11 else "none",
            },
            helper=helper,
            reason=reason,
        )
    return DesktopProbe(
        virtual_display=virtual_display,
        virtual_backend=virtual_backend,
        reason=f"unsupported platform {sys.platform}",
        helper=helper,
    )


def _helper_dict() -> dict[str, Any]:
    from termx.desktop.paths import host_arch

    helper: dict[str, Any] = {"name": "termx-host", "version": "0.1.0", "installed": True, "arch": host_arch()}
    path = capture_helper_path()
    if path:
        helper["capture_helper"] = path
    return helper


def _select_capture_backend() -> str | None:
    if capture_helper_path():
        return "termx-capture"
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    if shutil.which("screencapture"):
        return "screencapture"
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    x11 = bool(os.environ.get("DISPLAY"))
    if wayland and shutil.which("grim"):
        return "grim"
    if x11 and shutil.which("maim"):
        return "maim"
    if x11 and shutil.which("import"):
        return "imagemagick"
    if x11 and shutil.which("xwd"):
        return "xwd"
    return None
