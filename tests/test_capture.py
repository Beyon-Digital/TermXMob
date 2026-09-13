from __future__ import annotations

import sys

from termx.desktop.capabilities import probe_desktop
from termx.desktop.capture import close_capture, grab_jpeg

TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb0043000806060706050807070709"
    "09080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720221c1c28372c3031"
    "3434341f27393d32383c3432ffc0000b080001000101011100ffc4001400010000000000"
    "0000000000000000000008ffc40014100100000000000000000000000000000000ffda00"
    "08010100003f0054bfffd9"
)


def test_probe_does_not_crash() -> None:
    probe = probe_desktop()
    assert probe.helper["installed"] is True
    assert probe.capture_backend in {
        None,
        "termx-capture",
        "ffmpeg",
        "screencapture",
        "grim",
        "maim",
        "imagemagick",
        "xwd",
        "gdi",
        "scrot",
        "spectacle",
        "gnome-screenshot",
    }


def test_close_capture_safe_without_helper() -> None:
    close_capture()
    close_capture()


def test_grab_jpeg_from_env_helper(tmp_path, monkeypatch) -> None:
    script = tmp_path / "fake-capture"
    script.write_text(
        f"#!{sys.executable}\n"
        "import struct, sys\n"
        f"jpeg = {TINY_JPEG!r}\n"
        "sys.stdout.buffer.write(struct.pack('>I', len(jpeg)) + jpeg)\n"
        "sys.stdout.buffer.flush()\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("TERMX_CAPTURE_BIN", str(script))
    close_capture()
    try:
        assert grab_jpeg() == TINY_JPEG
    finally:
        close_capture()
