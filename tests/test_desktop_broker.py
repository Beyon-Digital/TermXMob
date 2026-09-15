"""Tests for the privileged broker client and its consumers."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from termx.desktop import broker
from termx.desktop.input import _virtual_desktop_point


class FakeBroker:
    """Minimal stand-in for the shell's privileged socket server."""

    def __init__(self, tmp_path: Path, responses: dict[str, object] | None = None) -> None:
        # Unix socket paths are capped near 104 bytes, and pytest temp dirs are
        # long on macOS, so the fake broker lives in its own short directory.
        self.dir = Path(tempfile.mkdtemp(prefix="tb-", dir="/tmp"))
        self.path = str(self.dir / "b.sock")
        self.responses = responses or {}
        self.seen: list[dict] = []
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(4)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        with connection:
            buffer = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    request = json.loads(line)
                    self.seen.append(request)
                    reply = self.responses.get(request.get("op"))
                    if callable(reply):
                        reply = reply(request)
                    if isinstance(reply, bytes):
                        connection.sendall(b"F" + len(reply).to_bytes(4, "big") + reply)
                    elif isinstance(reply, str):
                        connection.sendall(reply.encode() + b"\n")
                    else:
                        payload = json.dumps(reply if reply is not None else {"ok": True})
                        connection.sendall(payload.encode() + b"\n")

    def close(self) -> None:
        self.listener.close()
        shutil.rmtree(self.dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_client():
    yield
    broker.configure(None)


def test_status_reads_screen_and_accessibility(tmp_path: Path) -> None:
    fake = FakeBroker(
        tmp_path,
        {"status": {"ok": True, "screen_recording": True, "accessibility": False}},
    )
    broker.configure(fake.path)
    state = broker.status()
    assert state is not None
    assert state["screen_recording"] is True
    assert state["accessibility"] is False
    fake.close()


def test_capture_frame_reads_binary_reply(tmp_path: Path) -> None:
    payload = b"\xff\xd8\xff\xe0jpeg-bytes"
    fake = FakeBroker(tmp_path, {"frame": payload})
    broker.configure(fake.path)
    frame = broker.capture_frame("1", quality=55)
    assert frame == payload
    assert fake.seen[-1]["op"] == "frame"
    # The broker parses the display id as a number; a string id would silently
    # fall back to the main display.
    assert fake.seen[-1]["display"] == 1
    fake.close()


def test_capture_frame_surfaces_denied(tmp_path: Path) -> None:
    fake = FakeBroker(
        tmp_path,
        {"frame": {"ok": False, "kind": "denied", "error": "screen recording denied"}},
    )
    broker.configure(fake.path)
    with pytest.raises(broker.BrokerError) as error:
        broker.capture_frame("1")
    assert error.value.kind == "denied"
    fake.close()


def test_send_input_reports_success(tmp_path: Path) -> None:
    fake = FakeBroker(tmp_path, {"input": {"ok": True}})
    broker.configure(fake.path)
    assert broker.send_input({"kind": "mouse", "event": "move", "x": 1.0, "y": 2.0}) is True
    assert fake.seen[-1]["op"] == "input"
    assert fake.seen[-1]["kind"] == "mouse"
    fake.close()


def test_unreachable_broker_disables_helpers(tmp_path: Path) -> None:
    broker.configure(str(tmp_path / "missing.sock"))
    assert broker.status() is None
    assert broker.send_input({"kind": "release_all"}) is False
    if sys.platform == "darwin":
        from termx.desktop.permissions import permission_snapshot

        state = permission_snapshot()
        assert set(state) == {"screen_recording", "accessibility"}
    fake = FakeBroker(tmp_path, {})
    fake.close()


def test_virtual_desktop_point_maps_within_range() -> None:
    bounds = (0, 0, 1920, 1080)
    assert _virtual_desktop_point(0, 0, bounds) == (0, 0)
    assert _virtual_desktop_point(1919, 1079, bounds) == (65535, 65535)
    x, y = _virtual_desktop_point(960, 540, bounds)
    assert 32000 < x < 33500
    assert 32000 < y < 33500


def test_virtual_desktop_point_offset_monitor() -> None:
    # second monitor to the left: origin (-1920, 0) spanning 3840px
    bounds = (-1920, 0, 3840, 1080)
    assert _virtual_desktop_point(-1920, 0, bounds) == (0, 0)
    assert _virtual_desktop_point(0, 0, bounds) == (32776, 0)
