from __future__ import annotations

import ctypes

import pytest

from termx.desktop.input import _WIN_VK, _win_input_structs


def test_sendinput_structure_layout_is_64bit_correct() -> None:
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("SendInput layout test targets 64-bit Windows")
    INPUT, MOUSEINPUT, KEYBDINPUT = _win_input_structs()
    assert ctypes.sizeof(MOUSEINPUT) == 32
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(INPUT) == 40


def test_virtual_key_map_covers_protocol_keys() -> None:
    for key in ("return", "escape", "tab", "backspace", " ", "delete", "home", "end"):
        assert key in _WIN_VK
    assert _WIN_VK["up"] == _WIN_VK["arrowup"] == 0x26
    assert _WIN_VK["left"] == _WIN_VK["arrowleft"] == 0x25


def test_encode_jpeg_passthrough_and_rejection() -> None:
    from termx.desktop.capture import CaptureError, _encode_jpeg

    jpeg = b"\xff\xd8\xff\xe0payload"
    assert _encode_jpeg(jpeg) == jpeg
    with pytest.raises(CaptureError):
        _encode_jpeg(b"definitely not an image")
