"""Client for the privileged broker served by the Termx.app shell process.

macOS applies Screen Recording and Accessibility permissions to the process
that performs the operation. The Python backend is a child process with its own
code identity, so it can neither read the app's real permission state nor
capture frames or post events on its behalf. The desktop shell (the process the
user actually grants) serves these operations over a Unix socket; this module
talks to it.

When no broker is available (running `termx` from a terminal, or on Windows and
Linux) every helper here reports unavailable and the callers fall back to their
platform-native implementations.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from typing import Any

DEFAULT_TIMEOUT = 15.0


class BrokerError(RuntimeError):
    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


class _Client:
    def __init__(self, path: str) -> None:
        self.path = path
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()

    def _connect(self) -> socket.socket:
        if self.sock is None:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(DEFAULT_TIMEOUT)
            connection.connect(self.path)
            self.sock = connection
        return self.sock

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _read_exact(self, sock: socket.socket, count: int) -> bytes:
        buffer = bytearray()
        while len(buffer) < count:
            chunk = sock.recv(count - len(buffer))
            if not chunk:
                raise BrokerError("broker closed the connection")
            buffer.extend(chunk)
        return bytes(buffer)

    def call(self, payload: dict[str, Any]) -> dict[str, Any] | bytes:
        raw = json.dumps(payload).encode("utf-8") + b"\n"
        with self.lock:
            last: Exception | None = None
            for attempt in range(2):
                try:
                    sock = self._connect()
                    sock.sendall(raw)
                    first = self._read_exact(sock, 1)
                    if first == b"F":
                        length = int.from_bytes(self._read_exact(sock, 4), "big")
                        if length <= 0 or length > 64 * 1024 * 1024:
                            raise BrokerError("broker sent an invalid frame")
                        return self._read_exact(sock, length)
                    line = bytearray(first)
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            raise BrokerError("broker closed the connection")
                        newline = chunk.find(b"\n")
                        if newline >= 0:
                            line.extend(chunk[:newline])
                            break
                        line.extend(chunk)
                    return json.loads(bytes(line).decode("utf-8"))
                except (OSError, ValueError, BrokerError) as exc:
                    last = exc
                    self.close()
            raise BrokerError(str(last) if last else "broker unavailable")


_CLIENT: _Client | None = None


def configure(path: str | None) -> None:
    """Point the client at the shell's socket (or disable it with None)."""
    global _CLIENT
    if _CLIENT is not None:
        _CLIENT.close()
    _CLIENT = _Client(path) if path else None


def available() -> bool:
    """True when a shell socket has been handed to this process.

    The client itself is platform-neutral: only the desktop shell serves a
    broker today, so anything else simply never calls configure().
    """
    return _CLIENT is not None


def _call(payload: dict[str, Any]) -> dict[str, Any] | bytes:
    client = _CLIENT
    if client is None:
        raise BrokerError("no privileged broker is connected")
    return client.call(payload)


def status() -> dict[str, Any] | None:
    """Permission state as the Termx.app process sees it."""
    if not available():
        return None
    try:
        result = _call({"op": "status"})
    except BrokerError:
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    return result


def request_permission(which: str) -> dict[str, Any] | None:
    """Trigger the system prompt from the shell process and report the result."""
    if not available():
        return None
    try:
        result = _call({"op": "request", "which": which})
    except BrokerError:
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    return result


def capture_frame(
    display: str | None,
    width: int = 0,
    height: int = 0,
    fps: int = 12,
    quality: int = 55,
    timeout_ms: int = 700,
) -> bytes:
    """Return one JPEG frame captured by the shell process."""
    payload: dict[str, Any] = {
        "op": "frame",
        "quality": quality,
        "fps": fps,
        "timeout_ms": timeout_ms,
        "width": width,
        "height": height,
    }
    if display:
        text = str(display)
        payload["display"] = int(text) if text.isdigit() else display
    result = _call(payload)
    if isinstance(result, bytes):
        return result
    raise BrokerError(str(result.get("error") or "capture failed"), str(result.get("kind") or "error"))


def stop_capture() -> None:
    if not available():
        return
    try:
        _call({"op": "stop"})
    except BrokerError:
        return


def send_input(payload: dict[str, Any]) -> bool:
    """Ask the shell to post one input event; returns True when it succeeded."""
    if not available():
        return False
    try:
        result = _call({"op": "input", **payload})
    except BrokerError:
        return False
    return bool(isinstance(result, dict) and result.get("ok"))


def send_relative_move(dx: float, dy: float) -> bool:
    """Trackpad-style cursor movement by a delta (macOS shell only)."""
    return send_input({"kind": "mouse", "event": "move", "dx": dx, "dy": dy, "relative": True})
