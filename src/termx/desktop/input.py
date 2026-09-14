from __future__ import annotations

import ctypes
import ctypes.util
import shutil
import subprocess
import sys
from typing import Any

from termx.desktop.capabilities import probe_desktop


class InputError(RuntimeError):
    pass


def clipboard_get() -> str:
    if sys.platform == "win32":
        return _win_clipboard_get()
    cmd = _clipboard_read_cmd()
    if not cmd:
        return ""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.stdout or ""
    except Exception:
        return ""


def clipboard_set(text: str) -> None:
    if sys.platform == "win32":
        _win_clipboard_set(text)
        return
    cmd = _clipboard_write_cmd()
    if not cmd:
        return
    try:
        subprocess.run(cmd, input=text or "", capture_output=True, text=True, check=False)
    except Exception:
        return


def _clipboard_read_cmd() -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("pbpaste"):
        return ["pbpaste"]
    if shutil.which("wl-paste"):
        return ["wl-paste", "-n"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-o"]
    return None


def _clipboard_write_cmd() -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        return ["pbcopy"]
    if shutil.which("wl-copy"):
        return ["wl-copy"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-i"]
    return None


def apply_event(event: dict[str, Any], target: str | None = None) -> None:
    kind = event.get("type")
    if kind == "release_all":
        return
    if kind == "pointer":
        _pointer(event, target)
        return
    if kind == "key":
        _key(event)
        return
    if kind == "text":
        _text(str(event.get("data") or ""))


def _pointer(event: dict[str, Any], target: str | None = None) -> None:
    x = float(event.get("x") or 0)
    y = float(event.get("y") or 0)
    action = str(event.get("action") or "move")
    button = int(event.get("button") or 1)
    probe = probe_desktop()
    if probe.input_backend == "sendinput" and sys.platform == "win32":
        _win_pointer(event, x, y, action, button)
        return
    if probe.input_backend == "cgevent" and sys.platform == "darwin":
        _mac_pointer(x, y, action, button, event, target)
        return
    if probe.input_backend == "xdotool":
        px = int(x) if x > 1 else None
        py = int(y) if y > 1 else None
        if px is None or py is None:
            geom = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True, check=False)
            parts = (geom.stdout or "1920 1080").split()
            width = int(parts[0]) if parts else 1920
            height = int(parts[1]) if len(parts) > 1 else 1080
            px = int(max(0.0, min(1.0, x)) * (width - 1))
            py = int(max(0.0, min(1.0, y)) * (height - 1))
        if action == "move":
            subprocess.run(["xdotool", "mousemove", str(px), str(py)], check=False)
        elif action == "down":
            subprocess.run(["xdotool", "mousemove", str(px), str(py), "mousedown", str(button)], check=False)
        elif action == "up":
            subprocess.run(["xdotool", "mouseup", str(button)], check=False)
        elif action == "click":
            subprocess.run(["xdotool", "mousemove", str(px), str(py), "click", str(button)], check=False)
        elif action == "wheel":
            dy = int(event.get("dy") or 0)
            key = "4" if dy < 0 else "5"
            subprocess.run(["xdotool", "click", key], check=False)
        return
    raise InputError("pointer input is not available")


def _key(event: dict[str, Any]) -> None:
    key = str(event.get("key") or "")
    action = str(event.get("action") or "down")
    if not key:
        return
    probe = probe_desktop()
    if probe.input_backend == "sendinput" and sys.platform == "win32":
        _win_key(key, action)
        return
    if probe.input_backend == "xdotool":
        cmd = "keydown" if action == "down" else "keyup"
        if action == "tap":
            subprocess.run(["xdotool", "key", key], check=False)
            return
        subprocess.run(["xdotool", cmd, key], check=False)
        return
    if probe.input_backend == "cgevent" and action in {"tap", "down"}:
        _mac_key(key)
        return


def _text(data: str) -> None:
    if not data:
        return
    probe = probe_desktop()
    if probe.input_backend == "sendinput" and sys.platform == "win32":
        _win_text(data)
        return
    if probe.input_backend == "xdotool":
        subprocess.run(["xdotool", "type", "--", data], check=False)
        return
    if probe.input_backend == "cgevent":
        for char in data:
            _mac_key(char)


# --- Windows SendInput -------------------------------------------------------


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WHEEL_DELTA = 120

_WIN_VK = {
    "return": 0x0D,
    "enter": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "tab": 0x09,
    "backspace": 0x08,
    " ": 0x20,
    "space": 0x20,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "up": 0x26,
    "arrowup": 0x26,
    "down": 0x28,
    "arrowdown": 0x28,
    "left": 0x25,
    "arrowleft": 0x25,
    "right": 0x27,
    "arrowright": 0x27,
    "clear": 0x0C,
}


def _win_input_structs() -> tuple[Any, Any, Any]:
    import ctypes

    # Fixed-width Win32 types: c_long is 8 bytes on POSIX, so do not use
    # ctypes.wintypes here if the structures must have identical layouts everywhere.
    LONG = ctypes.c_int32
    DWORD = ctypes.c_uint32
    WORD = ctypes.c_uint16
    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", LONG),
            ("dy", LONG),
            ("mouseData", DWORD),
            ("dwFlags", DWORD),
            ("time", DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", WORD),
            ("wScan", WORD),
            ("dwFlags", DWORD),
            ("time", DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", DWORD),
            ("wParamL", WORD),
            ("wParamH", WORD),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", DWORD), ("u", INPUTUNION)]

    return INPUT, MOUSEINPUT, KEYBDINPUT


def _send_inputs(inputs: list[Any]) -> None:
    import ctypes

    if not inputs:
        return
    user32 = ctypes.windll.user32
    array = (type(inputs[0]) * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(inputs[0]))


def _mouse_input(mouse_class: Any, flags: int, data: int = 0) -> Any:
    value = mouse_class()
    value.dwFlags = flags
    value.mouseData = data
    return value


def _win_pointer(event: dict[str, Any], x: float, y: float, action: str, button: int) -> None:
    import ctypes

    INPUT, MOUSEINPUT, _KEYBDINPUT = _win_input_structs()
    user32 = ctypes.windll.user32
    if x <= 1 and y <= 1:
        width = int(user32.GetSystemMetrics(0)) or 1920
        height = int(user32.GetSystemMetrics(1)) or 1080
        px = int(max(0.0, min(1.0, x)) * max(width - 1, 1))
        py = int(max(0.0, min(1.0, y)) * max(height - 1, 1))
    else:
        px, py = int(x), int(y)
    user32.SetCursorPos(px, py)
    down = {1: MOUSEEVENTF_LEFTDOWN, 2: MOUSEEVENTF_RIGHTDOWN, 3: MOUSEEVENTF_MIDDLEDOWN}.get(button, MOUSEEVENTF_LEFTDOWN)
    up = {1: MOUSEEVENTF_LEFTUP, 2: MOUSEEVENTF_RIGHTUP, 3: MOUSEEVENTF_MIDDLEUP}.get(button, MOUSEEVENTF_LEFTUP)
    if action == "move":
        return
    if action == "down":
        _send_inputs([INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, down))])
        return
    if action == "up":
        _send_inputs([INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, up))])
        return
    if action == "click":
        _send_inputs(
            [
                INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, down)),
                INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, up)),
            ]
        )
        return
    if action == "wheel":
        delta = int(event.get("dy") or 0)
        direction = -WHEEL_DELTA if delta < 0 else WHEEL_DELTA
        _send_inputs([INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, MOUSEEVENTF_WHEEL, direction))])


def _win_key(key: str, action: str) -> None:
    INPUT, _MOUSEINPUT, KEYBDINPUT = _win_input_structs()
    code = _WIN_VK.get(key.lower())
    if code is None and len(key) == 1:
        _win_text(key)
        return
    if code is None:
        return
    events = []
    if action in {"down", "tap"}:
        events.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=code)))
    if action in {"up", "tap"}:
        events.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=code, dwFlags=KEYEVENTF_KEYUP)))
    _send_inputs(events)


def _win_text(data: str) -> None:
    INPUT, _MOUSEINPUT, KEYBDINPUT = _win_input_structs()
    units = data.encode("utf-16-le")
    events = []
    for index in range(0, len(units), 2):
        scan = int.from_bytes(units[index : index + 2], "little")
        if scan == 0:
            continue
        events.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=KEYEVENTF_UNICODE)))
        events.append(
            INPUT(
                type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
            )
        )
    _send_inputs(events)


def _configure_clipboard_types(user32: Any, kernel32: Any) -> None:
    import ctypes
    import ctypes.wintypes as wintypes

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL


def _win_clipboard_get() -> str:
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _configure_clipboard_types(user32, kernel32)
    CF_UNICODETEXT = 13
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _win_clipboard_set(text: str) -> None:
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _configure_clipboard_types(user32, kernel32)
    GMEM_MOVEABLE = 0x0002
    CF_UNICODETEXT = 13
    value = text or ""
    size = (len(value) + 1) * ctypes.sizeof(ctypes.c_wchar)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        return
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        return
    try:
        ctypes.memmove(pointer, ctypes.create_unicode_buffer(value), size)
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, handle)
    finally:
        user32.CloseClipboard()


# --- macOS CoreGraphics ------------------------------------------------------


def map_normalized(x: float, y: float, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    """Map a normalized (0..1) point onto a display's bounds (origin + size in points)."""
    width, height, origin_x, origin_y = bounds
    px = origin_x + max(0.0, min(1.0, x)) * max(width - 1.0, 1.0)
    py = origin_y + max(0.0, min(1.0, y)) * max(height - 1.0, 1.0)
    return px, py


def _mac_pointer(
    x: float,
    y: float,
    action: str,
    button: int,
    event: dict[str, Any] | None = None,
    target: str | None = None,
) -> None:
    if action == "wheel":
        _cg_scroll(event or {})
        return
    dragging = bool((event or {}).get("down"))
    _cg_pointer(x, y, action, button, dragging=dragging, target=target)


def _cg_display_bounds(cg: Any, display_id: int | None) -> tuple[float, float, float, float]:
    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]

    cg.CGDisplayBounds.restype = CGRect
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGMainDisplayID.restype = ctypes.c_uint32
    identifier = display_id if display_id else int(cg.CGMainDisplayID())
    rect = cg.CGDisplayBounds(ctypes.c_uint32(identifier))
    width = float(rect.size.width)
    height = float(rect.size.height)
    if width <= 0 or height <= 0:
        return (1920.0, 1080.0, 0.0, 0.0)
    return (width, height, float(rect.origin.x), float(rect.origin.y))


def _cg_pointer(
    x: float,
    y: float,
    action: str,
    button: int,
    dragging: bool = False,
    target: str | None = None,
) -> None:
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        cg = ctypes.CDLL(path)
        display_id = int(target) if target and str(target).isdigit() else None
        bounds = _cg_display_bounds(cg, display_id)
        px, py = map_normalized(x, y, bounds) if x <= 1 and y <= 1 else (x, y)

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        point = CGPoint(px, py)
        left_down, left_up, right_down, right_up = 1, 2, 3, 4
        moved, left_drag, right_drag = 5, 6, 7
        if button == 2:
            down, up, drag = right_down, right_up, right_drag
        else:
            down, up, drag = left_down, left_up, left_drag
        event_type = {
            "move": drag if dragging else moved,
            "drag": drag,
            "down": down,
            "up": up,
            "click": down,
            "right_click": right_down,
        }.get(action, moved)
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_int]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        event = cg.CGEventCreateMouseEvent(None, event_type, point, max(0, button - 1))
        if event:
            cg.CGEventPost(0, event)
            if action in {"click", "right_click"}:
                up_type = right_up if action == "right_click" else up
                up_event = cg.CGEventCreateMouseEvent(None, up_type, point, max(0, button - 1))
                if up_event:
                    cg.CGEventPost(0, up_event)
    except Exception as exc:
        raise InputError(f"macOS pointer event failed: {exc}") from exc


def _cg_scroll(event: dict[str, Any]) -> None:
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        cg = ctypes.CDLL(path)
        dy = float(event.get("dy") or 0)
        dx = float(event.get("dx") or 0)
        if dy == 0 and dx == 0:
            return
        lines_v = int(-dy) if abs(dy) > 0 else 0
        lines_h = int(dx)
        cg.CGEventCreateScrollWheelEvent.restype = ctypes.c_void_p
        cg.CGEventCreateScrollWheelEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        scroll = cg.CGEventCreateScrollWheelEvent(None, 1, 2, lines_v, lines_h)
        if scroll:
            cg.CGEventPost(0, scroll)
    except Exception as exc:
        raise InputError(f"macOS scroll event failed: {exc}") from exc


def _mac_key(key: str) -> None:
    if shutil.which("osascript") and len(key) == 1 and key.isalnum():
        subprocess.run(
            ["osascript", "-e", f'tell application "System Events" to keystroke "{key}"'],
            check=False,
            capture_output=True,
        )
        return
    mapping = {
        "Return": 36,
        "Enter": 36,
        "Escape": 53,
        "Tab": 48,
        "Backspace": 51,
        " ": 49,
    }
    code = mapping.get(key)
    if code is None and len(key) == 1:
        return
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        cg = ctypes.CDLL(path)
        cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        down = cg.CGEventCreateKeyboardEvent(None, code or 0, True)
        up = cg.CGEventCreateKeyboardEvent(None, code or 0, False)
        if down:
            cg.CGEventPost(0, down)
        if up:
            cg.CGEventPost(0, up)
    except Exception:
        pass
