from __future__ import annotations

import os
import sys

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
