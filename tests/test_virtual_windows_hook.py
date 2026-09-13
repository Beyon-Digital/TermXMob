from __future__ import annotations

import subprocess

import pytest

import termx.desktop.virtual as vmod
from termx.desktop.virtual import VirtualDisplayError, WindowsVirtualAdapter


def test_windows_adapter_requires_platform_and_command(monkeypatch) -> None:
    adapter = WindowsVirtualAdapter()
    monkeypatch.delenv(adapter.create_env, raising=False)
    monkeypatch.setattr(vmod.sys, "platform", "linux")
    assert adapter.available() is False
    monkeypatch.setattr(vmod.sys, "platform", "win32")
    assert adapter.available() is False
    monkeypatch.setenv(adapter.create_env, "driver.exe create")
    assert adapter.can_create() is True
    assert "command hook" in adapter.reason()


def test_windows_adapter_formats_command(monkeypatch) -> None:
    adapter = WindowsVirtualAdapter()
    monkeypatch.setattr(vmod.sys, "platform", "win32")
    monkeypatch.setenv(
        adapter.create_env,
        "driver-cli add --width {width} --height {height} --refresh {refresh} --tag {id}",
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(vmod.subprocess, "run", fake_run)
    name = adapter.create("abcd1234", 1280, 720, 2.0, 60)
    assert name == "termx-abcd1234"
    assert calls[0][0] == "driver-cli"
    assert "1280" in calls[0] and "720" in calls[0] and "abcd1234" in calls[0]


def test_windows_adapter_destroy_requires_hook(monkeypatch) -> None:
    adapter = WindowsVirtualAdapter()
    monkeypatch.setattr(vmod.sys, "platform", "win32")
    monkeypatch.setenv(adapter.create_env, "driver.exe create")
    monkeypatch.delenv(adapter.destroy_env, raising=False)
    with pytest.raises(VirtualDisplayError):
        adapter.destroy("termx-abcd")


def test_windows_adapter_rejects_unknown_placeholder(monkeypatch) -> None:
    adapter = WindowsVirtualAdapter()
    monkeypatch.setattr(vmod.sys, "platform", "win32")
    monkeypatch.setenv(adapter.create_env, "driver.exe create --missing {nope}")
    with pytest.raises(VirtualDisplayError):
        adapter.create("abcd", 800, 600, 1.0, 60)


def test_active_adapter_uses_windows_hook_when_configured(monkeypatch) -> None:
    adapter = WindowsVirtualAdapter()
    monkeypatch.setattr(vmod.sys, "platform", "win32")
    monkeypatch.setenv(adapter.create_env, "driver.exe create")
    monkeypatch.setattr(vmod, "_ADAPTERS", [adapter])
    assert vmod.active_adapter() is adapter
