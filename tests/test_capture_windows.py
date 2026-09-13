from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only capture path")


def test_windows_display_enumeration() -> None:
    from termx.desktop.displays import list_physical_displays

    displays = list_physical_displays()
    assert displays
    assert any(item.get("main") for item in displays)
    for item in displays:
        assert item["width"] > 0
        assert item["height"] > 0


def test_windows_gdi_capture_returns_jpeg() -> None:
    from termx.desktop.capture import close_capture, grab_jpeg

    try:
        frame = grab_jpeg()
    finally:
        close_capture()
    assert frame[:3] == b"\xff\xd8\xff"
    assert len(frame) > 512


def test_windows_input_backend() -> None:
    from termx.desktop.capabilities import probe_desktop

    probe = probe_desktop()
    assert probe.capture_backend in {"gdi", "ffmpeg"}
    assert probe.input_backend == "sendinput"
    assert probe.remote_screen is True


def test_windows_clipboard_roundtrip() -> None:
    from termx.desktop.input import clipboard_get, clipboard_set

    token = "termx-clipboard-roundtrip"
    clipboard_set(token)
    assert clipboard_get().strip() == token
