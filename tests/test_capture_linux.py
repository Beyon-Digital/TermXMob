from __future__ import annotations

import os
import sys
import threading

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only capture path"),
    pytest.mark.skipif(
        os.environ.get("TERMX_LINUX_DESKTOP_TESTS") != "1",
        reason="set TERMX_LINUX_DESKTOP_TESTS=1 inside a desktop session (CI uses xvfb-run)",
    ),
]


def test_linux_display_enumeration() -> None:
    from termx.desktop.displays import list_physical_displays

    displays = list_physical_displays()
    assert displays
    assert any(item.get("main") for item in displays)
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        assert any(item.get("backend") == "xrandr" for item in displays)


def test_linux_capture_returns_jpeg() -> None:
    from termx.desktop.capture import close_capture, grab_jpeg

    try:
        frame = grab_jpeg()
    finally:
        close_capture()
    assert frame[:3] == b"\xff\xd8\xff"
    assert len(frame) > 512


def test_linux_xtest_release_across_threads() -> None:
    from termx.desktop import input as desktop_input

    desktop_input._xtest_singleton = None
    if desktop_input._xtest() is None:
        pytest.skip("XTest is unavailable")

    errors: list[BaseException] = []

    def apply(event: dict[str, object]) -> None:
        try:
            desktop_input.apply_event(event)
        except BaseException as exc:
            errors.append(exc)

    press = threading.Thread(
        target=apply,
        args=({"type": "key", "key": "shift", "action": "down"},),
    )
    press.start()
    press.join(timeout=2)

    release = threading.Thread(target=apply, args=({"type": "release_all"},))
    release.start()
    release.join(timeout=2)

    assert not press.is_alive()
    assert not release.is_alive()
    assert not errors


def test_xrandr_virtual_display_roundtrip() -> None:
    from termx.desktop.virtual import (
        VirtualDisplayError,
        XrandrAdapter,
        create_virtual_display,
        destroy_virtual_display,
    )

    adapter = XrandrAdapter()
    if not adapter.can_create():
        pytest.skip("xrandr virtual monitors are unavailable")
    try:
        created = create_virtual_display(800, 600, force_adapter=adapter)
    except VirtualDisplayError as exc:
        pytest.skip(f"xrandr --setmonitor unsupported here: {exc}")
    try:
        assert created["kind"] == "virtual"
        assert created["adapter"] == "xrandr"
        assert created["width"] == 800
    finally:
        destroy_virtual_display(str(created["id"]))


class _FakeWS:
    def __init__(self) -> None:
        import asyncio

        self.sent_bytes = 0
        self.sent_text: list[str] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> None:
        return None

    async def send_text(self, data: str) -> None:
        self.sent_text.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes += len(data)

    async def receive(self) -> dict:
        return await self._queue.get()

    def send(self, payload: dict) -> None:
        import json

        self._queue.put_nowait({"type": "websocket.receive", "text": json.dumps(payload)})

    def disconnect(self) -> None:
        self._queue.put_nowait({"type": "websocket.disconnect"})


def test_linux_pause_stops_real_capture_and_resume_restarts() -> None:
    import asyncio

    from termx.desktop.capture import close_capture
    from termx.desktop.session import DesktopManager

    async def wait_until(predicate, timeout: float = 20.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.1)
        return predicate()

    async def inner() -> None:
        manager = DesktopManager()
        ws = _FakeWS()
        task = asyncio.create_task(manager.attach(ws))
        try:
            assert await wait_until(lambda: ws.sent_bytes > 0), "no frames while active"
            ws.send({"type": "pause"})
            assert await wait_until(lambda: any('"paused"' in text for text in ws.sent_text))
            frozen = ws.sent_bytes
            await asyncio.sleep(1.5)
            assert ws.sent_bytes == frozen, "capture continued while paused"
            ws.send({"type": "resume"})
            assert await wait_until(lambda: any('"resumed"' in text for text in ws.sent_text))
            assert await wait_until(lambda: ws.sent_bytes > frozen), "capture did not resume"
        finally:
            ws.disconnect()
            await asyncio.wait_for(task, timeout=10)

    try:
        asyncio.run(inner())
    finally:
        close_capture()
