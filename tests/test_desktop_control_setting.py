"""The owner's explicit path to an agent-desktop program: validation, storage, precedence, and startup refresh."""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from agent_desktop_release_server import client_for, fake_cli_source, good_members, make_archive, pins_for
from termx.desktop import agent_desktop as ad
from termx.desktop import agent_desktop_setting as setting
from termx.private_files import private_path_permissions

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


def make_program(directory, *, version=ad.PINNED_VERSION, helper=True, mode=0o700):
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "agent-desktop"
    binary.write_bytes(fake_cli_source(version=version))
    binary.chmod(mode)
    if helper:
        (directory / "agent-desktop-macos-helper").write_bytes(b"h")
    if os.name != "nt":
        directory.chmod(0o700)
    return binary


@pytest.fixture
def client(tmp_path):
    archive = make_archive(good_members())
    return client_for(tmp_path / "state", pins_for(archive))


def validate(client, path):
    return asyncio.run(client.validate_setting(str(path)))


def test_a_program_that_passes_the_version_and_helper_checks_is_accepted(client, tmp_path):
    assert validate(client, make_program(tmp_path / "mine")) == []


def test_the_live_client_is_untouched_by_validation(client, tmp_path):
    validate(client, make_program(tmp_path / "mine"))
    assert client.status_cached() is None and client._candidate is None


@pytest.mark.parametrize("path", ["agent-desktop", "./agent-desktop", "~/agent-desktop", ""])
def test_a_path_must_be_absolute(client, path):
    issues = asyncio.run(client.validate_setting(path))
    assert [item["code"] for item in issues] == ["configured_invalid"]


def test_a_missing_file_is_rejected(client, tmp_path):
    assert [item["code"] for item in validate(client, tmp_path / "nope" / "agent-desktop")] == ["configured_invalid"]


def test_a_directory_is_rejected(client, tmp_path):
    (tmp_path / "dir").mkdir()
    assert [item["code"] for item in validate(client, tmp_path / "dir")] == ["configured_invalid"]


def test_another_version_is_rejected(client, tmp_path):
    assert [item["code"] for item in validate(client, make_program(tmp_path / "old", version="1.1.0"))] == ["version_mismatch"]


def test_the_macos_helper_must_sit_beside_the_program(client, tmp_path):
    assert [item["code"] for item in validate(client, make_program(tmp_path / "nohelper", helper=False))] == ["helper_missing"]


@posix_only
def test_a_group_writable_program_is_rejected(client, tmp_path):
    assert [item["code"] for item in validate(client, make_program(tmp_path / "loose", mode=0o770))] == ["configured_invalid"]


@posix_only
def test_a_program_in_a_group_writable_directory_is_rejected(client, tmp_path):
    binary = make_program(tmp_path / "loosedir")
    binary.parent.chmod(0o770)
    assert [item["code"] for item in validate(client, binary)] == ["configured_invalid"]


def test_a_program_that_does_not_answer_like_the_cli_is_rejected(client, tmp_path):
    binary = make_program(tmp_path / "liar")
    binary.write_text("print('not json')")
    binary.chmod(0o700)
    assert validate(client, binary)  # any issue; it is never accepted


def test_the_saved_path_round_trips_and_is_private(tmp_path):
    root = tmp_path / "desktop-control"
    path = str(tmp_path / "program" / ad.detect_platform().binary_name)
    assert setting.load_setting(root) is None
    setting.save_setting(root, path)
    assert setting.load_setting(root) == path
    assert private_path_permissions(root)
    assert private_path_permissions(root / "setting.json")
    if os.name != "nt":
        assert (root / "setting.json").stat().st_mode & 0o777 == 0o600
    setting.save_setting(root, None)
    assert setting.load_setting(root) is None and not (root / "setting.json").exists()


@pytest.mark.parametrize("content", ["not json", "[]", '{"path": 5}', '{"path": "relative/agent-desktop"}', '{"path": "/a\\u0000b"}', '{"path": ""}'])
def test_a_damaged_or_unsafe_saved_setting_is_ignored(tmp_path, content):
    root = tmp_path / "desktop-control"
    root.mkdir()
    (root / "setting.json").write_text(content)
    assert setting.load_setting(root) is None


def test_save_refuses_paths_that_cannot_be_a_program(tmp_path):
    for bad in ("relative", "", "x" * 5000, "/a\x00b"):
        with pytest.raises(ValueError):
            setting.save_setting(tmp_path, bad)


def test_get_client_prefers_the_environment_then_the_saved_path(tmp_path, monkeypatch):
    root = tmp_path / "termx-config" / "desktop-control"
    saved_path = str(tmp_path / "saved" / ad.detect_platform().binary_name)
    env_path = str(tmp_path / "env" / ad.detect_platform().binary_name)
    setting.save_setting(root, saved_path)
    ad.set_client(None)
    monkeypatch.delenv(ad.SETTING_ENV, raising=False)
    try:
        assert ad.get_client().setting == saved_path
        ad.set_client(None)
        monkeypatch.setenv(ad.SETTING_ENV, env_path)
        assert ad.get_client().setting == env_path
    finally:
        ad.set_client(None)


def test_the_environment_locks_the_setting(monkeypatch):
    monkeypatch.delenv(ad.SETTING_ENV, raising=False)
    assert setting.env_locked() is False
    monkeypatch.setenv(ad.SETTING_ENV, "/env/agent-desktop")
    assert setting.env_locked() is True


def test_startup_refresh_makes_the_machine_capability_accurate(fake_agent_desktop, monkeypatch, tmp_path):
    """The lifespan schedules a status refresh, so a restart re-hashes the files and the capability does not wait for a UI visit."""
    from starlette.testclient import TestClient
    from termx.app import AppState, create_app
    from termx.config import ConfigStore
    from termx.machine import machine_snapshot

    desk = fake_agent_desktop()
    desk.client.forget()
    assert desk.client.status_cached() is None
    monkeypatch.setenv("TERMX_AGENT_DESKTOP_STARTUP_REFRESH", "1")
    with TestClient(create_app(AppState())):
        for _ in range(100):
            if desk.client.status_cached() is not None:
                break
            time.sleep(0.05)
    capabilities = machine_snapshot(ConfigStore(tmp_path / "config.json"))["capabilities"]
    assert capabilities["agent_desktop"] == {"ready": True, "version": "1.2.0", "issue": None}


def test_startup_refresh_is_off_by_default_in_tests(fake_agent_desktop):
    from starlette.testclient import TestClient
    from termx.app import AppState, create_app

    desk = fake_agent_desktop()
    desk.client.forget()
    with TestClient(create_app(AppState())):
        pass
    assert desk.client.status_cached() is None


@posix_only
def test_the_saved_file_is_never_world_readable_even_for_a_moment(tmp_path, monkeypatch):
    """It is created 0600 with a loose umask; nothing narrows it afterwards."""
    from termx import private_files

    def must_not_be_needed(*args, **kwargs):
        raise AssertionError("the file must be created private, not narrowed later")

    monkeypatch.setattr(setting, "protect_private_path", must_not_be_needed, raising=False)
    monkeypatch.setattr(private_files, "protect_private_path", must_not_be_needed)
    previous = os.umask(0)
    try:
        setting.save_setting(tmp_path / "desktop-control", "/opt/ad/agent-desktop")
    finally:
        os.umask(previous)
    assert (tmp_path / "desktop-control" / "setting.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "desktop-control").stat().st_mode & 0o077 == 0


def test_valid_path_is_one_rule_for_the_api_and_the_file(tmp_path):
    path = str(tmp_path / "program" / ad.detect_platform().binary_name)
    assert setting.valid_path(path) == path
    for bad in ("relative", "", "x" * 5000, "/a\x00b", "/a\nb", 5, None):
        assert setting.valid_path(bad) is None


def test_json_shape_of_the_saved_file(tmp_path):
    path = str(tmp_path / "program" / ad.detect_platform().binary_name)
    setting.save_setting(tmp_path, path)
    assert json.loads((tmp_path / "setting.json").read_text()) == {"path": path}


@pytest.mark.parametrize("path", [
    "/Users/me/.config/termx/desktop-control/setting.json",
    "cat ~/.config/termx/desktop-control/bin/1.2.0/agent-desktop",
    "C:\\Users\\me\\AppData\\termx\\desktop-control\\home",
    "/Users/me/.config/termx/desktop-control",
    "cd desktop-control; cat setting.json",
    "ls ~/.config/termx/desktop-control && echo",
])
def test_the_desktop_control_directory_is_a_sensitive_path(path):
    from termx.agent.policy import is_sensitive_path

    assert is_sensitive_path(path)


@pytest.mark.parametrize("path", ["docs/desktop-control.md", "src/termx/desktop/agent_desktop.py", "desktop-controls/x"])
def test_ordinary_project_files_are_not_made_sensitive(path):
    from termx.agent.policy import is_sensitive_path

    assert not is_sensitive_path(path)
