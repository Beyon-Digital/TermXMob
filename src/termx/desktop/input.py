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
    if sys.platform == "darwin":
        from termx.desktop import broker

        # The shell owns the app's Accessibility grant; events posted from the
        # backend process are judged against the backend's own identity.
        if broker.available():
            if kind == "release_all":
                broker.send_input({"kind": "release_all"})
                return
            if not _broker_event(event, target):
                raise InputError(
                    "the Termx app could not post this input — grant Accessibility permission to Termx"
                )
            return
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


def _pointer_points(x: float, y: float, display_id: int | None) -> tuple[float, float]:
    if x > 1 or y > 1:
        return x, y
    try:
        import ctypes
        import ctypes.util

        path = (
            ctypes.util.find_library("CoreGraphics")
            or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg = ctypes.CDLL(path)
        return map_normalized(x, y, _cg_display_bounds(cg, display_id))
    except Exception:
        return x, y


def _broker_event(event: dict[str, Any], target: str | None) -> bool:
    from termx.desktop import broker

    kind = event.get("type")
    if kind == "pointer":
        action = str(event.get("action") or "move")
        if event.get("relative"):
            return broker.send_relative_move(float(event.get("dx") or 0), float(event.get("dy") or 0))
        if action == "wheel":
            return broker.send_input(
                {
                    "kind": "scroll",
                    "dy": float(event.get("dy") or 0),
                    "dx": float(event.get("dx") or 0),
                }
            )
        display_id = int(target) if target and str(target).isdigit() else None
        px, py = _pointer_points(float(event.get("x") or 0), float(event.get("y") or 0), display_id)
        return broker.send_input(
            {
                "kind": "mouse",
                "event": action,
                "x": px,
                "y": py,
                "button": int(event.get("button") or 1),
                "dragging": bool(event.get("down")),
            }
        )
    if kind == "key":
        name = str(event.get("key") or "")
        action = str(event.get("action") or "down")
        modifiers = [str(item) for item in (event.get("modifiers") or []) if isinstance(item, str)]
        if not name:
            return True
        if len(name) == 1:
            if modifiers:
                # Chords (⌘C, ⇧A) need real key events; the shell maps characters
                # to virtual key codes.
                sent = broker.send_input(
                    {"kind": "key", "key": name, "down": action != "up", "modifiers": modifiers}
                )
                if action == "tap":
                    sent = broker.send_input({"kind": "key", "key": name, "down": False}) and sent
                return sent
            return broker.send_input({"kind": "text", "text": name})
        payload = {"kind": "key", "key": name.lower(), "down": action != "up"}
        if modifiers:
            payload["modifiers"] = modifiers
        sent = broker.send_input(payload)
        if action == "tap":
            release = {"kind": "key", "key": name.lower(), "down": False}
            sent = broker.send_input(release) and sent
        return sent
    if kind == "text":
        data = str(event.get("data") or "")
        modifiers = [str(item) for item in (event.get("modifiers") or []) if isinstance(item, str)]
        if modifiers:
            # Typing while a modifier is held: post each character as a key
            # event so the modifier applies.
            ok = True
            for character in data:
                ok = (
                    broker.send_input(
                        {"kind": "key", "key": character, "down": True, "modifiers": modifiers}
                    )
                    and ok
                )
            return ok
        return broker.send_input({"kind": "text", "text": data})
    return False


def _pointer(event: dict[str, Any], target: str | None = None) -> None:
    x = float(event.get("x") or 0)
    y = float(event.get("y") or 0)
    action = str(event.get("action") or "move")
    button = int(event.get("button") or 1)
    if event.get("relative"):
        if _relative_pointer(event):
            return
    probe = probe_desktop()
    if probe.input_backend == "sendinput" and sys.platform == "win32":
        _win_pointer(event, x, y, action, button)
        return
    if probe.input_backend == "cgevent" and sys.platform == "darwin":
        _mac_pointer(x, y, action, button, event, target)
        return
    if _xtest_pointer(x, y, action, button, event):
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


def _relative_pointer(event: dict[str, Any]) -> bool:
    """Trackpad-style relative movement, per platform API.

    Windows: SendInput with MOUSEEVENTF_MOVE and no ABSOLUTE flag is a relative
    move by definition. X11: XTestFakeRelativeMotionEvent. macOS: applied by the
    privileged shell through the broker.
    """
    dx = float(event.get("dx") or 0)
    dy = float(event.get("dy") or 0)
    if dx == 0 and dy == 0:
        return True
    if sys.platform == "win32":
        INPUT, MOUSEINPUT, _KEYBDINPUT = _win_input_structs()
        _ensure_windows_dpi_awareness()
        return _send_inputs(
            [
                INPUT(
                    type=INPUT_MOUSE,
                    mi=MOUSEINPUT(dx=int(round(dx)), dy=int(round(dy)), mouseData=0, dwFlags=MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0),
                )
            ]
        )
    if sys.platform.startswith("linux"):
        adapter = _xtest()
        if adapter is None:
            return False
        adapter.relative_motion(dx, dy)
        return True
    if sys.platform == "darwin":
        from termx.desktop import broker

        return broker.send_relative_move(dx, dy)
    return False


def _key(event: dict[str, Any]) -> None:
    key = str(event.get("key") or "")
    action = str(event.get("action") or "down")
    modifiers = [
        str(item)
        for item in (event.get("modifiers") or [])
        if isinstance(item, str)
    ]
    if not key:
        return
    if modifiers:
        if action in {"down", "tap"}:
            for modifier in modifiers:
                _key({"key": modifier, "action": "down"})
        _key({"key": key, "action": action})
        if action in {"up", "tap"}:
            for modifier in reversed(modifiers):
                _key({"key": modifier, "action": "up"})
        return
    probe = probe_desktop()
    if probe.input_backend == "sendinput" and sys.platform == "win32":
        _win_key(key, action)
        return
    if _xtest_key(key, action):
        return
    if probe.input_backend == "xdotool":
        if action == "tap":
            subprocess.run(["xdotool", "key", key], check=False)
            return
        cmd = "keydown" if action == "down" else "keyup"
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
    if _xtest_text(data):
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
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WHEEL_DELTA = 120

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


def _ensure_windows_dpi_awareness() -> None:
    """Opt into per-monitor DPI awareness before any coordinate math.

    Without this, GetSystemMetrics/BitBlt report DPI-virtualized sizes on
    scaled displays (documented behaviour: the APIs are "virtualized" for
    non-DPI-aware processes), which corrupts both pointer mapping and capture
    geometry. Tiers follow Microsoft's documented order: Windows 10 1703+
    per-monitor v2, Windows 8.1+ per-monitor, else system aware (Vista+).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            ):
                return
        try:
            shcore = ctypes.windll.shcore
            if shcore.SetProcessDpiAwareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
                return
        except OSError:
            pass
        user32.SetProcessDPIAware()
    except Exception:
        return


def _windows_virtual_desktop() -> tuple[int, int, int, int]:
    """Virtual desktop bounds in physical pixels (SM_XVIRTUALSCREEN ...)."""
    import ctypes

    user32 = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)) or 1
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)) or 1
    return left, top, width, height


def _virtual_desktop_point(px: int, py: int, bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    """Map physical pixels onto the 0..65535 absolute range for SendInput.

    Microsoft documents absolute mouse coordinates as a 0..65535 range; with
    MOUSEEVENTF_VIRTUALDESK the range spans the whole virtual desktop
    (left/top/width/height from SM_*VIRTUALSCREEN).
    """
    left, top, width, height = bounds
    dx = int(round((px - left) * 65535 / max(width - 1, 1)))
    dy = int(round((py - top) * 65535 / max(height - 1, 1)))
    return max(0, min(65535, dx)), max(0, min(65535, dy))


def _windows_absolute_point(px: int, py: int) -> tuple[int, int]:
    return _virtual_desktop_point(px, py, _windows_virtual_desktop())

_WIN_VK = {
    "shift": 0x10,
    "control": 0x11,
    "ctrl": 0x11,
    "alt": 0x12,
    "meta": 0x5B,
    "super": 0x5B,
    "command": 0x5B,
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


def _send_inputs(inputs: list[Any]) -> bool:
    """Post input events; False means the OS blocked them (documented UIPI case)."""
    import ctypes

    if not inputs:
        return True
    user32 = ctypes.windll.user32
    array = (type(inputs[0]) * len(inputs))(*inputs)
    inserted = user32.SendInput(len(inputs), array, ctypes.sizeof(inputs[0]))
    return bool(inserted)


def _mouse_input(mouse_class: Any, flags: int, data: int = 0, *, dx: int = 0, dy: int = 0) -> Any:
    value = mouse_class()
    value.dx = dx
    value.dy = dy
    value.dwFlags = flags
    value.mouseData = data
    return value


def _win_pointer(event: dict[str, Any], x: float, y: float, action: str, button: int) -> None:
    import ctypes

    _ensure_windows_dpi_awareness()
    INPUT, MOUSEINPUT, _KEYBDINPUT = _win_input_structs()
    user32 = ctypes.windll.user32
    if x <= 1 and y <= 1:
        left, top, width, height = _windows_virtual_desktop()
        px = int(left + max(0.0, min(1.0, x)) * max(width - 1, 1))
        py = int(top + max(0.0, min(1.0, y)) * max(height - 1, 1))
    else:
        px, py = int(x), int(y)
    absolute = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    dx, dy = _windows_absolute_point(px, py)
    moved = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=absolute, time=0, dwExtraInfo=0))
    down = {1: MOUSEEVENTF_LEFTDOWN, 2: MOUSEEVENTF_RIGHTDOWN, 3: MOUSEEVENTF_MIDDLEDOWN}.get(button, MOUSEEVENTF_LEFTDOWN)
    up = {1: MOUSEEVENTF_LEFTUP, 2: MOUSEEVENTF_RIGHTUP, 3: MOUSEEVENTF_MIDDLEUP}.get(button, MOUSEEVENTF_LEFTUP)
    if action == "move":
        if not _send_inputs([moved]):
            user32.SetCursorPos(px, py)
        return
    if action == "down":
        _send_inputs([moved, INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, down, flags=absolute, dx=dx, dy=dy))])
        return
    if action == "up":
        _send_inputs([INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, up, flags=absolute, dx=dx, dy=dy))])
        return
    if action == "click":
        sent = _send_inputs(
            [
                INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, down, flags=absolute, dx=dx, dy=dy)),
                INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, up, flags=absolute, dx=dx, dy=dy)),
            ]
        )
        if not sent:
            raise InputError(
                "Windows blocked the input (UIPI): run Termx at the same integrity level as the target window"
            )
        return
    if action == "wheel":
        delta = int(event.get("dy") or 0)
        direction = -WHEEL_DELTA if delta < 0 else WHEEL_DELTA
        _send_inputs(
            [
                INPUT(type=INPUT_MOUSE, mi=_mouse_input(MOUSEINPUT, MOUSEEVENTF_WHEEL, direction, flags=absolute, dx=dx, dy=dy))
            ]
        )


def _win_key(key: str, action: str) -> None:
    INPUT, _MOUSEINPUT, KEYBDINPUT = _win_input_structs()
    code = _WIN_VK.get(key.lower())
    if code is None and len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key.upper())
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


# --- X11 XTest ---------------------------------------------------------------

_XTST_KEYSYMS = {
    "return": "Return",
    "enter": "Return",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "space": "space",
    " ": "space",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pgup": "Page_Up",
    "pagedown": "Page_Down",
    "pgdn": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "arrowup": "Up",
    "arrowdown": "Down",
    "arrowleft": "Left",
    "arrowright": "Right",
    "shift": "Shift_L",
    "control": "Control_L",
    "ctrl": "Control_L",
    "alt": "Alt_L",
    "meta": "Super_L",
    "super": "Super_L",
}

_xtest_singleton: Any = None


class _XTest:
    """Minimal XTest client: the X11 input-injection extension (libXtst)."""

    def __init__(self, x11: Any, xtst: Any) -> None:
        self.x11 = x11
        self.xtst = xtst
        self.display = x11.XOpenDisplay(None)
        if not self.display:
            raise OSError("cannot open the X display")
        self.screen = x11.XDefaultScreen(self.display)
        self.width = int(x11.XDisplayWidth(self.display, 0))
        self.height = int(x11.XDisplayHeight(self.display, 0))

    def _flush(self) -> None:
        self.x11.XFlush(self.display)

    def motion(self, x: float, y: float) -> None:
        self.xtst.XTestFakeMotionEvent(self.display, self.screen, int(x), int(y), 0)
        self._flush()

    def relative_motion(self, dx: float, dy: float) -> None:
        self.xtst.XTestFakeRelativeMotionEvent(self.display, int(dx), int(dy), 0)
        self._flush()

    def button(self, button: int, pressed: bool) -> None:
        self.xtst.XTestFakeButtonEvent(self.display, int(button), bool(pressed), 0)
        self._flush()

    def scroll(self, dy: float, dx: float) -> None:
        # X11 buttons 4/5 are vertical wheel, 6/7 horizontal.
        if dy:
            button = 4 if dy > 0 else 5
            for _ in range(max(1, int(abs(dy)))):
                self.button(button, True)
                self.button(button, False)
        if dx:
            button = 6 if dx > 0 else 7
            for _ in range(max(1, int(abs(dx)))):
                self.button(button, True)
                self.button(button, False)

    def keycode(self, keysym_name: str) -> int | None:
        keysym = self.x11.XStringToKeysym(keysym_name.encode("utf-8"))
        if not keysym:
            return None
        code = self.x11.XKeysymToKeycode(self.display, keysym)
        return int(code) or None

    def needs_shift(self, code: int) -> bool:
        """True when the keysym sits on the shifted level of this keycode."""
        unshifted = self.x11.XkbKeycodeToKeysym(self.display, code, 0, 0)
        char = self.x11.XkbKeycodeToKeysym(self.display, code, 0, 1)
        return False if not unshifted else bool(char and char != unshifted)

    def press(self, code: int, shift: bool = False) -> None:
        shift_code = self.keycode("Shift_L") if shift else None
        if shift_code:
            self.xtst.XTestFakeKeyEvent(self.display, shift_code, True, 0)
        self.xtst.XTestFakeKeyEvent(self.display, code, True, 0)
        self.xtst.XTestFakeKeyEvent(self.display, code, False, 0)
        if shift_code:
            self.xtst.XTestFakeKeyEvent(self.display, shift_code, False, 0)
        self._flush()

    def key(self, code: int, pressed: bool) -> None:
        self.xtst.XTestFakeKeyEvent(self.display, code, pressed, 0)
        self._flush()


def _xtest() -> _XTest | None:
    """Return a cached XTest client for X11 sessions, or None."""
    global _xtest_singleton
    if _xtest_singleton is not None:
        return _xtest_singleton
    if sys.platform != "linux" or os.environ.get("WAYLAND_DISPLAY"):
        return None
    if not os.environ.get("DISPLAY"):
        return None
    try:
        import ctypes
        import ctypes.util

        xtst_path = ctypes.util.find_library("Xtst")
        x11_path = ctypes.util.find_library("X11")
        if not xtst_path or not x11_path:
            return None
        x11 = ctypes.CDLL(x11_path)
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultScreen.restype = ctypes.c_int
        x11.XDisplayWidth.restype = ctypes.c_int
        x11.XDisplayHeight.restype = ctypes.c_int
        x11.XStringToKeysym.restype = ctypes.c_ulong
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XkbKeycodeToKeysym.restype = ctypes.c_ulong
        xtst = ctypes.CDLL(xtst_path)
        _xtest_singleton = _XTest(x11, xtst)
    except Exception:
        return None
    return _xtest_singleton


def _xtest_pointer(x: float, y: float, action: str, button: int, event: dict[str, Any]) -> bool:
    adapter = _xtest()
    if adapter is None:
        return False
    if x <= 1 and y <= 1:
        px = max(0.0, min(1.0, x)) * max(adapter.width - 1, 1)
        py = max(0.0, min(1.0, y)) * max(adapter.height - 1, 1)
    else:
        px, py = x, y
    if action == "wheel":
        adapter.scroll(float(event.get("dy") or 0), float(event.get("dx") or 0))
        return True
    adapter.motion(px, py)
    if action in {"down", "click"}:
        adapter.button(button, True)
    if action in {"up", "click"}:
        adapter.button(button, False)
    return True


def _xtest_key(key: str, action: str) -> bool:
    adapter = _xtest()
    if adapter is None:
        return False
    name = _XTST_KEYSYMS.get(key.lower())
    if name is None and len(key) == 1:
        name = key
    if name is None:
        return False
    code = adapter.keycode(name)
    if code is None:
        return False
    if action == "down":
        adapter.key(code, True)
        return True
    if action == "up":
        adapter.key(code, False)
        return True
    adapter.press(code, shift=len(key) == 1 and adapter.needs_shift(code))
    return True


def _xtest_text(data: str) -> bool:
    adapter = _xtest()
    if adapter is None:
        return False
    for char in data:
        code = adapter.keycode(char)
        if code is None:
            return False
        adapter.press(code, shift=adapter.needs_shift(code))
    return True


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
