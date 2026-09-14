from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from termx.desktop.capabilities import capture_helper_path, probe_desktop
from termx.desktop.displays import PRIMARY_ID, find_display, list_physical_displays


class CaptureError(RuntimeError):
    pass


_LOCK = threading.Lock()
_HELPER: subprocess.Popen[bytes] | None = None
_HELPER_BIN: str | None = None
_HELPER_DISPLAY: str | None = None


def list_displays() -> list[dict[str, object]]:
    from termx.desktop.virtual import list_virtual_displays

    virtuals = list_virtual_displays()
    virtual_names = {str(item.get("name")) for item in virtuals}
    displays: list[dict[str, object]] = []
    for index, item in enumerate(list_physical_displays()):
        if str(item.get("id")) in virtual_names:
            # CGActiveDisplayList also reports virtual displays; the dock shows
            # the dedicated virtual entry instead of a duplicate "physical" one.
            continue
        displays.append(
            {
                "id": item["id"],
                "name": item["name"],
                "kind": item.get("kind", "physical"),
                "width": item.get("width", 0),
                "height": item.get("height", 0),
                "x": item.get("x", 0),
                "y": item.get("y", 0),
                "main": item.get("main", False),
                "backend": item.get("backend"),
                "output": item.get("output"),
                "index": index,
                "selected": bool(item.get("main")),
            }
        )
    for item in virtuals:
        displays.append(
            {
                "id": item["id"],
                "name": item["name"],
                "kind": "virtual",
                "width": item["width"],
                "height": item["height"],
                "x": item.get("x", 0),
                "y": item.get("y", 0),
                "main": False,
                "backend": item.get("adapter"),
                "output": item.get("name"),
                "selected": False,
            }
        )
    return displays


def _virtual_target(display_id: str | None) -> dict[str, Any] | None:
    if not display_id:
        return None
    from termx.desktop.virtual import list_virtual_displays

    for item in list_virtual_displays():
        if item["id"] == display_id:
            record = dict(item)
            record["kind"] = "virtual"
            record["output"] = item.get("name")
            record["id"] = display_id
            return record
    return None


def _target(display_id: str | None) -> dict[str, Any] | None:
    virtual = _virtual_target(display_id)
    if virtual is not None:
        return virtual
    physical = find_display(display_id)
    if physical is None:
        return None
    index = 0
    for position, item in enumerate(list_physical_displays()):
        if item["id"] == physical["id"]:
            index = position
            break
    physical = dict(physical)
    physical["index"] = index
    physical["output"] = physical.get("output")
    return physical


def pointer_target(display_id: str | None) -> str | None:
    """Return the numeric CoreGraphics display id used for pointer mapping."""
    return _helper_display_id(_target(display_id))


def screen_recording_denied() -> bool:
    if sys.platform != "darwin":
        return False
    from termx.desktop.permissions import permission_snapshot

    return permission_snapshot().get("screen_recording") == "denied"


def close_capture() -> None:
    with _LOCK:
        _stop_helper_locked()
    from termx.desktop import broker

    if broker.available():
        broker.stop_capture()


def grab_jpeg(display_id: str | None = None) -> bytes:
    from termx.desktop import broker

    if broker.available():
        target = _target(display_id)
        numeric = _helper_display_id(target)
        for attempt in range(2):
            try:
                return broker.capture_frame(numeric, quality=55, timeout_ms=700)
            except broker.BrokerError as exc:
                if exc.kind == "waiting" and attempt == 0:
                    continue
                raise CaptureError(str(exc)) from exc
    if screen_recording_denied():
        raise CaptureError(
            "Screen Recording permission is required to mirror this Mac. "
            "Grant it to Termx in System Settings → Privacy & Security → Screen Recording."
        )
    target = _target(display_id)
    helper = capture_helper_path()
    if helper and (sys.platform == "darwin" or os.environ.get("TERMX_CAPTURE_BIN")):
        helper_display = _helper_display_id(target)
        try:
            return _grab_helper(helper, helper_display)
        except (CaptureError, OSError):
            close_capture()
    if sys.platform == "win32":
        try:
            return _windows_gdi_jpeg(target)
        except CaptureError:
            pass
    if shutil.which("ffmpeg"):
        try:
            return _ffmpeg_jpeg(target)
        except CaptureError:
            pass
    if sys.platform == "darwin" and shutil.which("screencapture"):
        try:
            return _screencapture(target)
        except CaptureError:
            pass
    if shutil.which("grim"):
        try:
            return _grim_jpeg(target)
        except CaptureError:
            pass
    if shutil.which("maim"):
        try:
            return _maim_jpeg(target)
        except CaptureError:
            pass
    if shutil.which("scrot"):
        try:
            return _tempfile_capture(["scrot", "-o", "-q", "55"])
        except CaptureError:
            pass
    if shutil.which("spectacle"):
        try:
            return _tempfile_capture(["spectacle", "-b", "-n", "-f", "-o"], "png")
        except CaptureError:
            pass
    if shutil.which("gnome-screenshot"):
        try:
            return _tempfile_capture(["gnome-screenshot", "-f"], "png")
        except CaptureError:
            pass
    if shutil.which("import"):
        try:
            return _import_jpeg(target)
        except CaptureError:
            pass
    if shutil.which("xwd") and shutil.which("convert"):
        try:
            raw = _run_stdout(["xwd", "-root"])
            converted = subprocess.run(
                ["convert", "xwd:-", "-quality", "55", "jpg:-"],
                input=raw,
                capture_output=True,
                check=True,
            )
            if not converted.stdout:
                raise CaptureError("capture returned an empty frame")
            return converted.stdout
        except (CaptureError, OSError, subprocess.CalledProcessError):
            pass
    probe = probe_desktop()
    raise CaptureError(probe.reason or "screen capture is not available")


def _helper_display_id(target: dict[str, Any] | None) -> str | None:
    if target is None or target.get("kind") == "virtual":
        name = str(target.get("output") or target.get("name") or "") if target else ""
        return name if name.isdigit() else None
    return str(target["id"]) if str(target["id"]).isdigit() else None


def _grab_helper(bin_path: str, display_id: str | None) -> bytes:
    with _LOCK:
        last: Exception | None = None
        for _ in range(2):
            try:
                proc = _start_helper_locked(bin_path, display_id)
                return _read_frame(proc)
            except (CaptureError, OSError, struct.error) as exc:
                last = exc
                _stop_helper_locked()
        raise CaptureError(f"termx-capture failed: {last}")


def _start_helper_locked(bin_path: str, display_id: str | None) -> subprocess.Popen[bytes]:
    global _HELPER, _HELPER_BIN, _HELPER_DISPLAY
    if (
        _HELPER is not None
        and _HELPER.poll() is None
        and _HELPER_BIN == bin_path
        and _HELPER_DISPLAY == display_id
    ):
        return _HELPER
    _stop_helper_locked()
    argv = [bin_path]
    if display_id:
        argv.extend(["--display", display_id])
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    if proc.stdout is None:
        raise CaptureError("termx-capture stdout is not available")
    _HELPER = proc
    _HELPER_BIN = bin_path
    _HELPER_DISPLAY = display_id
    return proc


def _stop_helper_locked() -> None:
    global _HELPER, _HELPER_BIN, _HELPER_DISPLAY
    proc = _HELPER
    _HELPER = None
    _HELPER_BIN = None
    _HELPER_DISPLAY = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _read_exact(fp: object, n: int) -> bytes:
    read = getattr(fp, "read")
    buf = bytearray()
    while len(buf) < n:
        chunk = read(n - len(buf))
        if not chunk:
            raise CaptureError("termx-capture ended")
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(proc: subprocess.Popen[bytes]) -> bytes:
    stdout = proc.stdout
    if stdout is None:
        raise CaptureError("termx-capture stdout is not available")
    header = _read_exact(stdout, 4)
    (length,) = struct.unpack(">I", header)
    if length == 0 or length > 32 * 1024 * 1024:
        raise CaptureError("termx-capture sent an invalid frame")
    return _read_exact(stdout, length)


def _geometry(target: dict[str, Any] | None) -> tuple[int, int, int, int] | None:
    if target is None:
        return None
    width = int(target.get("width") or 0)
    height = int(target.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    return width, height, int(target.get("x") or 0), int(target.get("y") or 0)


def _ffmpeg_jpeg(target: dict[str, Any] | None) -> bytes:
    attempts: list[list[str]] = []
    if sys.platform == "darwin":
        index = int(target.get("index") or 0) + 1 if target else 1
        attempts.append(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-f",
                "avfoundation",
                "-capture_cursor",
                "1",
                "-i",
                f"{index}:none",
                "-frames:v",
                "1",
                "-q:v",
                "5",
                "-f",
                "image2",
                "pipe:1",
            ]
        )
    elif sys.platform == "win32":
        # Tiers by capability, newest first: ddagrab wraps the DXGI Desktop
        # Duplication API (Windows 8+) and captures GPU-composited windows that
        # gdigrab misses; gdigrab (GDI BitBlt) stays as the universal fallback
        # for ffmpeg builds without ddagrab.
        attempts.append(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-filter_complex",
                "ddagrab=0,hwdownload,format=bgra",
                "-frames:v",
                "1",
                "-q:v",
                "5",
                "-f",
                "image2",
                "pipe:1",
            ]
        )
        geometry = _geometry(target)
        argv = ["ffmpeg", "-y", "-nostdin", "-f", "gdigrab"]
        if geometry:
            width, height, x, y = geometry
            argv.extend(["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{width}x{height}"])
        attempts.append(
            argv + ["-i", "desktop", "-frames:v", "1", "-q:v", "5", "-f", "image2", "pipe:1"]
        )
    elif sys.platform.startswith("linux"):
        display = os.environ.get("DISPLAY")
        if display:
            geometry = _geometry(target)
            argv = ["ffmpeg", "-y", "-nostdin", "-f", "x11grab"]
            if geometry:
                width, height, x, y = geometry
                argv.extend(["-video_size", f"{width}x{height}"])
                display = f"{display}+{x},{y}"
            attempts.append(
                argv + ["-i", display, "-frames:v", "1", "-q:v", "5", "-f", "mjpeg", "pipe:1"]
            )
        attempts.append(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-f",
                "pipewire",
                "-i",
                "default",
                "-frames:v",
                "1",
                "-q:v",
                "5",
                "-f",
                "image2",
                "pipe:1",
            ]
        )
    else:
        raise CaptureError("ffmpeg capture is not supported on this platform")
    last: CaptureError | None = None
    for argv in attempts:
        try:
            return _run_stdout(argv, timeout=8)
        except CaptureError as exc:
            last = exc
    raise last or CaptureError("ffmpeg capture failed")


def _screencapture(target: dict[str, Any] | None) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="termx-")
    os.close(fd)
    destination = Path(path)
    argv = ["screencapture", "-x", "-t", "jpg", "-C"]
    if target and "index" in target:
        argv.extend(["-D", str(int(target["index"]) + 1)])
    argv.append(str(destination))
    try:
        subprocess.run(argv, check=True, capture_output=True)
        data = destination.read_bytes()
        if not data:
            raise CaptureError("screencapture returned an empty frame")
        return data
    finally:
        try:
            destination.unlink()
        except OSError:
            pass


def _grim_jpeg(target: dict[str, Any] | None) -> bytes:
    argv = ["grim", "-t", "jpeg", "-q", "55"]
    output = target.get("output") if target else None
    if output:
        argv.extend(["-o", str(output)])
        try:
            return _run_stdout(argv + ["-"])
        except CaptureError:
            pass
    return _run_stdout(["grim", "-t", "jpeg", "-q", "55", "-"])


def _maim_jpeg(target: dict[str, Any] | None) -> bytes:
    argv = ["maim", "-f", "jpg", "-q", "55"]
    geometry = _geometry(target)
    if geometry:
        width, height, x, y = geometry
        argv.extend(["-g", f"{width}x{height}+{x}+{y}"])
    return _run_stdout(argv)


def _import_jpeg(target: dict[str, Any] | None) -> bytes:
    argv = ["import", "-window", "root"]
    geometry = _geometry(target)
    if geometry:
        width, height, x, y = geometry
        argv.extend(["-crop", f"{width}x{height}+{x}+{y}", "+repage"])
    argv.extend(["-quality", "55", "jpg:-"])
    return _run_stdout(argv)


def _tempfile_capture(argv: list[str], suffix: str) -> bytes:
    fd, path = tempfile.mkstemp(suffix=f".{suffix}", prefix="termx-")
    os.close(fd)
    destination = Path(path)
    try:
        subprocess.run(argv + [str(destination)], check=True, capture_output=True)
        data = destination.read_bytes()
        if not data:
            raise CaptureError("capture returned an empty frame")
        return _encode_jpeg(data)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CaptureError(f"capture failed: {exc}") from exc
    finally:
        try:
            destination.unlink()
        except OSError:
            pass


def _encode_jpeg(data: bytes) -> bytes:
    """Normalize a captured frame to JPEG for the client stream."""
    if data[:3] == b"\xff\xd8\xff":
        return data
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(data)).convert("RGB")
            output = io.BytesIO()
            image.save(output, "JPEG", quality=55)
            return output.getvalue()
        except ImportError:
            pass
        if shutil.which("convert"):
            proc = subprocess.run(
                ["convert", "-", "-quality", "55", "jpg:-"],
                input=data,
                capture_output=True,
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout
    raise CaptureError("capture did not produce a JPEG frame")


def _windows_gdi_jpeg(target: dict[str, Any] | None) -> bytes:
    import ctypes
    import ctypes.wintypes as wintypes

    try:
        from PIL import Image
    except ImportError as exc:
        raise CaptureError("Pillow is required for Windows screen capture") from exc

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    geometry = _geometry(target) or _virtual_screen_geometry(user32)
    width, height, x, y = geometry
    screen_dc = user32.GetDC(0)
    if not screen_dc:
        raise CaptureError("GetDC failed")
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    try:
        gdi32.SelectObject(memory_dc, bitmap)
        SRCCOPY = 0x00CC0020
        CAPTUREBLT = 0x40000000
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, x, y, SRCCOPY | CAPTUREBLT):
            raise CaptureError("BitBlt failed")
        _draw_windows_cursor(user32, gdi32, memory_dc, x, y)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        if rows == 0:
            raise CaptureError("GetDIBits failed")
        image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")
        import io

        output = io.BytesIO()
        image.save(output, "JPEG", quality=55)
        return output.getvalue()
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(0, screen_dc)


def _virtual_screen_geometry(user32: Any) -> tuple[int, int, int, int]:
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)) or 1920
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)) or 1080
    return width, height, x, y


def _draw_windows_cursor(user32: Any, gdi32: Any, memory_dc: int, origin_x: int, origin_y: int) -> None:
    import ctypes
    import ctypes.wintypes as wintypes

    class CURSORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hCursor", wintypes.HANDLE),
            ("ptScreenPos", wintypes.POINT),
        ]

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    try:
        info = CURSORINFO()
        info.cbSize = ctypes.sizeof(CURSORINFO)
        if not user32.GetCursorInfo(ctypes.byref(info)) or not info.flags:
            return
        icon_info = ICONINFO()
        if not user32.GetIconInfo(info.hCursor, ctypes.byref(icon_info)):
            return
        try:
            x = int(info.ptScreenPos.x) - origin_x - int(icon_info.xHotspot or 0)
            y = int(info.ptScreenPos.y) - origin_y - int(icon_info.yHotspot or 0)
            gdi32.DrawIconEx(memory_dc, x, y, info.hCursor, 0, 0, 0, None, 3)
        finally:
            if icon_info.hbmMask:
                gdi32.DeleteObject(icon_info.hbmMask)
            if icon_info.hbmColor:
                gdi32.DeleteObject(icon_info.hbmColor)
    except Exception:
        return


def _run_stdout(argv: list[str], timeout: float | None = None) -> bytes:
    try:
        proc = subprocess.run(argv, capture_output=True, check=True, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise CaptureError(f"capture failed: {exc}") from exc
    if not proc.stdout:
        raise CaptureError("capture returned an empty frame")
    return proc.stdout


def virtual_display_reason() -> str:
    from termx.desktop.virtual import active_adapter, unsupported_reason

    adapter = active_adapter()
    if adapter is not None:
        return adapter.reason()
    return unsupported_reason()
