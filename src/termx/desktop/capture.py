from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from termx.desktop.capabilities import capture_helper_path, probe_desktop


class CaptureError(RuntimeError):
    pass


_LOCK = threading.Lock()
_HELPER: subprocess.Popen[bytes] | None = None
_HELPER_BIN: str | None = None


def list_displays() -> list[dict[str, object]]:
    from termx.desktop.virtual import list_virtual_displays

    displays: list[dict[str, object]] = [
        {
            "id": "primary",
            "name": "Primary display",
            "kind": "physical",
            "width": 0,
            "height": 0,
            "selected": True,
        }
    ]
    for item in list_virtual_displays():
        displays.append(
            {
                "id": item["id"],
                "name": item["name"],
                "kind": "virtual",
                "width": item["width"],
                "height": item["height"],
                "selected": False,
            }
        )
    return displays


def close_capture() -> None:
    with _LOCK:
        _stop_helper_locked()


def grab_jpeg() -> bytes:
    helper = capture_helper_path()
    if helper:
        try:
            return _grab_helper(helper)
        except (CaptureError, OSError):
            close_capture()
    if shutil.which("ffmpeg"):
        try:
            return _ffmpeg_jpeg()
        except CaptureError:
            pass
    if shutil.which("screencapture"):
        try:
            return _screencapture()
        except CaptureError:
            pass
    if shutil.which("grim"):
        try:
            return _run_stdout(["grim", "-t", "jpeg", "-q", "55", "-"])
        except CaptureError:
            pass
    if shutil.which("maim"):
        try:
            return _run_stdout(["maim", "-f", "jpg", "-q", "55"])
        except CaptureError:
            pass
    if shutil.which("import"):
        try:
            return _run_stdout(["import", "-window", "root", "-quality", "55", "jpg:-"])
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


def _grab_helper(bin_path: str) -> bytes:
    with _LOCK:
        last: Exception | None = None
        for _ in range(2):
            try:
                proc = _start_helper_locked(bin_path)
                return _read_frame(proc)
            except (CaptureError, OSError, struct.error) as exc:
                last = exc
                _stop_helper_locked()
        raise CaptureError(f"termx-capture failed: {last}")


def _start_helper_locked(bin_path: str) -> subprocess.Popen[bytes]:
    global _HELPER, _HELPER_BIN
    if _HELPER is not None and _HELPER.poll() is None and _HELPER_BIN == bin_path:
        return _HELPER
    _stop_helper_locked()
    proc = subprocess.Popen(
        [bin_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    if proc.stdout is None:
        raise CaptureError("termx-capture stdout is not available")
    _HELPER = proc
    _HELPER_BIN = bin_path
    return proc


def _stop_helper_locked() -> None:
    global _HELPER, _HELPER_BIN
    proc = _HELPER
    _HELPER = None
    _HELPER_BIN = None
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


def _ffmpeg_jpeg() -> bytes:
    if sys.platform == "darwin":
        attempts = [
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-f",
                "avfoundation",
                "-capture_cursor",
                "1",
                "-i",
                "1:none",
                "-frames:v",
                "1",
                "-q:v",
                "5",
                "-f",
                "image2",
                "pipe:1",
            ]
        ]
    elif sys.platform.startswith("linux"):
        attempts = [
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
        ]
        display = os.environ.get("DISPLAY")
        if display:
            attempts.append(
                [
                    "ffmpeg",
                    "-y",
                    "-nostdin",
                    "-f",
                    "x11grab",
                    "-i",
                    display,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "5",
                    "-f",
                    "mjpeg",
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


def _screencapture() -> bytes:
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="termx-")
    os.close(fd)
    target = Path(path)
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "jpg", "-C", str(target)],
            check=True,
            capture_output=True,
        )
        data = target.read_bytes()
        if not data:
            raise CaptureError("screencapture returned an empty frame")
        return data
    finally:
        try:
            target.unlink()
        except OSError:
            pass


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
