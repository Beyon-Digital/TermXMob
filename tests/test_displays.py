from __future__ import annotations

import sys

import pytest

from termx.desktop.displays import PRIMARY_ID, find_display, list_physical_displays, parse_xrandr_monitors

XRANDR_SAMPLE = """Monitors: 2
 0: +*eDP-1 1920/344x1080/193+0+0  eDP-1
 1: +HDMI-1 2560/598x1440/336+1920+-120  HDMI-1
"""


def test_parse_xrandr_monitors() -> None:
    monitors = parse_xrandr_monitors(XRANDR_SAMPLE)
    assert [item["id"] for item in monitors] == ["eDP-1", "HDMI-1"]
    assert monitors[0]["main"] is True
    assert monitors[0]["width"] == 1920
    assert monitors[0]["height"] == 1080
    assert monitors[1]["x"] == 1920
    assert monitors[1]["y"] == -120
    assert monitors[1]["backend"] == "xrandr"


def test_listing_never_empty_and_has_main() -> None:
    displays = list_physical_displays()
    assert displays
    assert any(item.get("main") for item in displays)
    for item in displays:
        assert item["id"]
        assert item["width"] >= 0
        assert item["height"] >= 0


def test_find_display_defaults_to_main() -> None:
    displays = list_physical_displays()
    main = next(item for item in displays if item.get("main"))
    assert find_display(None)["id"] == main["id"]
    assert find_display(PRIMARY_ID)["id"] == main["id"]
    assert find_display("does-not-exist") is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS display ids are numeric")
def test_darwin_displays_are_numeric() -> None:
    displays = list_physical_displays()
    if any(item["id"] == PRIMARY_ID for item in displays):
        pytest.skip("no display server available")
    assert all(str(item["id"]).isdigit() for item in displays)
    assert any(item["width"] > 0 and item["height"] > 0 for item in displays)
