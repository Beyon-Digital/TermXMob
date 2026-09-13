from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

PRIMARY_ID = "primary"


def _entry(
    display_id: str,
    name: str,
    *,
    width: int = 0,
    height: int = 0,
    x: int = 0,
    y: int = 0,
    main: bool = False,
    output: str | None = None,
    backend: str | None = None,
    detail: str | None = None,
    kind: str = "physical",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": display_id,
        "name": name,
        "kind": kind,
        "width": width,
        "height": height,
        "x": x,
        "y": y,
        "main": main,
        "selected": main,
    }
    if output:
        item["output"] = output
    if backend:
        item["backend"] = backend
    if detail:
        item["detail"] = detail
    return item


def list_physical_displays() -> list[dict[str, Any]]:
    """Enumerate physical/logical displays on this machine. Never raises."""
    try:
        if sys.platform == "darwin":
            displays = _darwin_displays()
        elif sys.platform == "win32":
            displays = _windows_displays()
        elif sys.platform.startswith("linux"):
            displays = _linux_displays()
        else:
            displays = []
    except Exception:
        displays = []
    if not displays:
        displays = [_entry(PRIMARY_ID, "Primary display", main=True, backend="fallback")]
    if not any(item.get("main") for item in displays):
        displays[0]["main"] = True
    return displays


def find_display(display_id: str | None) -> dict[str, Any] | None:
    displays = list_physical_displays()
    if not display_id or display_id == PRIMARY_ID:
        for item in displays:
            if item.get("main"):
                return item
        return displays[0] if displays else None
    for item in displays:
        if item["id"] == display_id:
            return item
    return None


# --- macOS -------------------------------------------------------------------


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


def _core_graphics() -> Any | None:
    try:
        path = (
            ctypes.util.find_library("CoreGraphics")
            or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        return ctypes.CDLL(path)
    except OSError:
        return None


def _darwin_displays() -> list[dict[str, Any]]:
    cg = _core_graphics()
    if cg is None or not hasattr(cg, "CGGetActiveDisplayList"):
        return []
    cg.CGGetActiveDisplayList.restype = ctypes.c_int
    cg.CGGetActiveDisplayList.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    cg.CGDisplayBounds.restype = _CGRect
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
    cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
    cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
    cg.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
    cg.CGMainDisplayID.restype = ctypes.c_uint32
    cg.CGMainDisplayID.argtypes = []
    main = int(cg.CGMainDisplayID())
    builtin = getattr(cg, "CGDisplayIsBuiltin", None)
    if builtin is not None:
        builtin.restype = ctypes.c_int
        builtin.argtypes = [ctypes.c_uint32]

    limit = 16
    ids = (ctypes.c_uint32 * limit)()
    count = ctypes.c_uint32(0)
    if cg.CGGetActiveDisplayList(limit, ids, ctypes.byref(count)) != 0:
        return []
    out: list[dict[str, Any]] = []
    for index in range(int(count.value)):
        display_id = int(ids[index])
        rect = cg.CGDisplayBounds(display_id)
        is_builtin = bool(builtin(display_id)) if builtin is not None else False
        label = "Built-in display" if is_builtin else f"Display {index + 1}"
        if display_id == main and not is_builtin:
            label = f"{label} (main)"
        out.append(
            _entry(
                str(display_id),
                label,
                width=int(cg.CGDisplayPixelsWide(display_id)),
                height=int(cg.CGDisplayPixelsHigh(display_id)),
                x=int(rect.origin.x),
                y=int(rect.origin.y),
                main=display_id == main,
                backend="coregraphics",
            )
        )
    return out


# --- Windows -----------------------------------------------------------------


def _windows_displays() -> list[dict[str, Any]]:
    import ctypes.wintypes as wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    user32 = ctypes.windll.user32
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(RECT),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    monitors: list[dict[str, Any]] = []

    monitor_enum = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        ctypes.c_void_p,
    )

    def callback(hmonitor: int, _hdc: int, _rect: Any, _data: Any) -> int:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            monitors.append(
                {
                    "device": str(info.szDevice),
                    "primary": bool(info.dwFlags & 1),
                    "x": int(info.rcMonitor.left),
                    "y": int(info.rcMonitor.top),
                    "width": int(info.rcMonitor.right - info.rcMonitor.left),
                    "height": int(info.rcMonitor.bottom - info.rcMonitor.top),
                    "driver": _windows_monitor_driver(str(info.szDevice)),
                }
            )
        return 1

    user32.EnumDisplayMonitors(None, None, monitor_enum(callback), 0)
    out: list[dict[str, Any]] = []
    for index, monitor in enumerate(monitors):
        label = f"Display {index + 1}"
        if monitor["primary"]:
            label = f"{label} (main)"
        driver = monitor.get("driver") or ""
        if driver and "generic" not in driver.lower():
            label = f"{label} · {driver}"
        out.append(
            _entry(
                monitor["device"],
                label,
                width=monitor["width"],
                height=monitor["height"],
                x=monitor["x"],
                y=monitor["y"],
                main=monitor["primary"],
                output=monitor["device"],
                backend="gdi",
                detail=driver or None,
            )
        )
    return out


def _windows_monitor_driver(device: str) -> str:
    try:
        import ctypes.wintypes as wintypes

        class DISPLAY_DEVICEW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128),
            ]

        user32 = ctypes.windll.user32
        adapter = DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(device, 0, ctypes.byref(adapter), 0):
            return ""
        return str(adapter.DeviceString)
    except Exception:
        return ""


# --- Linux -------------------------------------------------------------------


def _run(argv: list[str], timeout: float = 10) -> str | None:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 and not proc.stdout:
        return None
    return proc.stdout or ""


def _linux_displays() -> list[dict[str, Any]]:
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    x11 = bool(os.environ.get("DISPLAY"))
    if wayland:
        displays = _hyprland_displays() or _sway_displays() or _wlr_randr_displays()
        if displays:
            return displays
    if x11:
        displays = _xrandr_displays()
        if displays:
            return displays
    return []


def _hyprland_displays() -> list[dict[str, Any]]:
    if not shutil.which("hyprctl"):
        return []
    text = _run(["hyprctl", "monitors", "-j"])
    if not text:
        return []
    try:
        monitors = json.loads(text)
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for index, monitor in enumerate(monitors if isinstance(monitors, list) else []):
        if not isinstance(monitor, dict):
            continue
        name = str(monitor.get("name") or f"output-{index}")
        out.append(
            _entry(
                name,
                name,
                width=int(monitor.get("width") or 0),
                height=int(monitor.get("height") or 0),
                x=int(monitor.get("x") or 0),
                y=int(monitor.get("y") or 0),
                main=index == 0 or bool(monitor.get("focused")),
                output=name,
                backend="hyprctl",
            )
        )
    return out


def _sway_displays() -> list[dict[str, Any]]:
    if not shutil.which("swaymsg"):
        return []
    text = _run(["swaymsg", "-t", "get_outputs", "--raw"])
    if not text:
        return []
    try:
        outputs = json.loads(text)
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for index, output in enumerate(outputs if isinstance(outputs, list) else []):
        if not isinstance(output, dict) or not output.get("active"):
            continue
        rect = output.get("rect") if isinstance(output.get("rect"), dict) else {}
        name = str(output.get("name") or f"output-{index}")
        out.append(
            _entry(
                name,
                name,
                width=int(rect.get("width") or 0),
                height=int(rect.get("height") or 0),
                x=int(rect.get("x") or 0),
                y=int(rect.get("y") or 0),
                main=index == 0 or bool(output.get("focused")),
                output=name,
                backend="swaymsg",
            )
        )
    return out


def _wlr_randr_displays() -> list[dict[str, Any]]:
    binary = shutil.which("wlr-randr")
    if not binary:
        return []
    text = _run([binary, "--json"])
    if not text:
        return []
    try:
        outputs = json.loads(text)
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for index, output in enumerate(outputs if isinstance(outputs, list) else []):
        if not isinstance(output, dict) or not output.get("enabled", True):
            continue
        position = output.get("position") if isinstance(output.get("position"), dict) else {}
        current = {}
        for mode in output.get("modes") or []:
            if isinstance(mode, dict) and mode.get("current"):
                current = mode
                break
        name = str(output.get("name") or f"output-{index}")
        out.append(
            _entry(
                name,
                name,
                width=int(current.get("width") or 0),
                height=int(current.get("height") or 0),
                x=int(position.get("x") or 0),
                y=int(position.get("y") or 0),
                main=index == 0,
                output=name,
                backend="wlr-randr",
            )
        )
    return out


XRANDR_MONITOR_RE = re.compile(
    r"^\s*(\d+):\s+([+*]*)(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)"
)


def parse_xrandr_monitors(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines()[1:]:
        match = XRANDR_MONITOR_RE.match(line)
        if not match:
            continue
        _index, flags, name, width, height, x, y = match.groups()
        out.append(
            _entry(
                name,
                name,
                width=int(width),
                height=int(height),
                x=int(x),
                y=int(y),
                main="*" in flags,
                output=name,
                backend="xrandr",
            )
        )
    return out


def _xrandr_displays() -> list[dict[str, Any]]:
    binary = shutil.which("xrandr")
    if not binary:
        return []
    text = _run([binary, "--listmonitors"])
    if not text:
        return []
    return parse_xrandr_monitors(text)
