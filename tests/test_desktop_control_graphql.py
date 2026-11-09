"""Desktop control status, managed install, removal and the explicit program path over GraphQL."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_desktop_release_server import (
    ASSET_NAME, ReleaseServer, client_for, fake_cli_source, good_members, make_archive, pins_for,
)
from test_authorization import client_fixture
from test_bots_graphql import denied, query, user
from termx.audit import read_events
from termx.desktop import agent_desktop as ad
from termx.desktop import agent_desktop_install as install

STATUS = """
installed source path version pinned_version verified helper_ok platform arch platform_supported platform_reason
envelope_version ready supported_surfaces configured_path setting_locked
permission { state guidance grant_to }
issues { code message action }
install { available asset size_bytes host note }
"""
QUERY = "query($r:Boolean){desktop_control_status(refresh:$r){%s}}" % STATUS
INSTALL = "mutation($i:DesktopControlInstallInput!){desktop_control_install(input:$i){%s}}" % STATUS
REMOVE = "mutation{desktop_control_remove{%s}}" % STATUS
SET_PATH = "mutation($p:String){desktop_control_set_path(path:$p){%s}}" % STATUS


def status_of(client, headers, refresh=False):
    result = query(client, headers, QUERY, {"r": refresh})
    assert not result.get("errors"), result
    return result["data"]["desktop_control_status"]


@pytest.fixture
def server():
    release = ReleaseServer()
    yield release
    release.close()


@pytest.fixture
def install_world(tmp_path, server, monkeypatch):
    """An empty managed install location served by the local release server, behind the installer seam."""
    members = good_members()
    archive = make_archive(members)
    desktop = client_for(tmp_path / "state", pins_for(archive, members))
    ad.set_client(desktop)
    server.serve_asset(archive)
    monkeypatch.setattr(
        install, "make_installer",
        lambda client: install.Installer(client, base_url=server.base_url, allowed_hosts=("127.0.0.1",), allow_http=True),
    )
    yield desktop, server, archive
    ad.set_client(None)


# ---------------------------------------------------------------- authorization


def test_every_desktop_control_operation_needs_host_admin(tmp_path, install_world):
    identity, state, client, owner = client_fixture(tmp_path)
    viewer = user(identity, state, "viewer", "viewer")
    server = install_world[1]
    for document, variables in (
        (QUERY, {"r": False}),
        (INSTALL, {"i": {"version": "1.2.0", "consent": True}}),
        (REMOVE, {}),
        (SET_PATH, {"p": "/opt/agent-desktop"}),
    ):
        denied(query(client, viewer, document, variables), 403)
        result = client.post("/graphql", json={"query": document, "variables": variables})
        assert result.status_code == 401 or result.json().get("errors"), "an unauthenticated call must fail"
    assert server.requests == [] and status_of(client, owner)["installed"] is False


# ---------------------------------------------------------------- status shapes


def test_status_when_nothing_is_installed_offers_the_install(tmp_path, install_world):
    identity, state, client, owner = client_fixture(tmp_path)
    current = status_of(client, owner)
    assert current["installed"] is False and current["ready"] is False and current["path"] is None
    assert current["pinned_version"] == "1.2.0" and current["platform"] == "macos" and current["platform_supported"] is True
    assert [item["code"] for item in current["issues"]] == ["not_installed"]
    assert current["install"]["available"] is True and current["install"]["asset"] == ASSET_NAME
    assert current["install"]["size_bytes"] > 0 and current["install"]["host"] == "github.com"
    assert "checksum built into this version of TermX" in current["install"]["note"]
    assert current["setting_locked"] is False and current["configured_path"] is None


def test_status_when_ready_reports_version_source_helper_and_surfaces(tmp_path, fake_agent_desktop):
    desk = fake_agent_desktop()
    identity, state, client, owner = client_fixture(tmp_path)
    current = status_of(client, owner)
    assert current["ready"] and current["installed"] and current["source"] == "managed" and current["verified"]
    assert current["version"] == "1.2.0" and current["helper_ok"] is True and current["envelope_version"] == "2.6"
    assert current["path"] == str(desk.binary)
    assert current["permission"]["state"] == "granted" and current["permission"]["guidance"] == ""
    assert current["supported_surfaces"] == ["window", "focused", "menu"] and current["issues"] == []


def test_status_with_accessibility_denied_explains_the_next_step(tmp_path, fake_agent_desktop):
    desk = fake_agent_desktop()
    desk.accessibility = "denied"
    desk.write()
    identity, state, client, owner = client_fixture(tmp_path)
    current = status_of(client, owner, refresh=True)
    assert current["ready"] is False and current["permission"]["state"] == "denied"
    assert "Accessibility" in current["permission"]["guidance"] and "Check again" in current["permission"]["guidance"]
    assert "permission_denied" in [item["code"] for item in current["issues"]]


def test_status_on_linux_without_an_accessibility_bus(tmp_path, fake_agent_desktop):
    desk = fake_agent_desktop("linux")
    desk.surfaces = []
    desk.write()
    identity, state, client, owner = client_fixture(tmp_path)
    current = status_of(client, owner, refresh=True)
    assert current["ready"] is False and current["permission"]["state"] == "unavailable"
    assert "at-spi2-core" in current["permission"]["guidance"]
    assert "no_accessibility_bus" in [item["code"] for item in current["issues"]]


def test_status_on_windows_needs_no_permission(tmp_path, fake_agent_desktop):
    fake_agent_desktop("windows")
    identity, state, client, owner = client_fixture(tmp_path)
    current = status_of(client, owner, refresh=True)
    assert current["permission"]["state"] == "not_required" and "Elevated" in current["permission"]["guidance"]


def test_status_reports_a_version_mismatch(tmp_path, fake_agent_desktop):
    desk = fake_agent_desktop()
    desk.version = "9.9.9"
    desk.write()
    identity, state, client, owner = client_fixture(tmp_path)
    current = status_of(client, owner, refresh=True)
    assert current["ready"] is False and "version_mismatch" in [item["code"] for item in current["issues"]]


def test_status_when_this_platform_has_no_complete_pin(tmp_path):
    desktop = ad.AgentDesktopClient(home_root=tmp_path / "state", platform=ad.HostPlatform("linux", "x86_64", True))
    ad.set_client(desktop)
    try:
        identity, state, client, owner = client_fixture(tmp_path)
        current = status_of(client, owner)
    finally:
        ad.set_client(None)
    assert current["platform_supported"] is False and current["platform_reason"] == "pin_incomplete"
    assert current["install"]["available"] is False and current["install"]["asset"] is None
    assert [item["code"] for item in current["issues"]] == ["pin_incomplete"]


def test_refresh_false_uses_the_cache_and_true_checks_again(tmp_path, fake_agent_desktop):
    desk = fake_agent_desktop()
    identity, state, client, owner = client_fixture(tmp_path)
    status_of(client, owner)
    before = len(desk.calls())
    status_of(client, owner, refresh=False)
    assert len(desk.calls()) == before
    status_of(client, owner, refresh=True)
    assert len(desk.calls()) > before


# ---------------------------------------------------------------- install


def test_install_needs_consent_and_the_pinned_version(tmp_path, install_world):
    desktop, server, _ = install_world
    identity, state, client, owner = client_fixture(tmp_path)
    denied(query(client, owner, INSTALL, {"i": {"version": "1.2.0", "consent": False}}), 400)
    denied(query(client, owner, INSTALL, {"i": {"version": "1.3.0", "consent": True}}), 409)
    assert server.requests == [] and not desktop.bin_dir.exists()


def test_install_downloads_verifies_and_returns_the_ready_status(tmp_path, install_world):
    desktop, server, _ = install_world
    identity, state, client, owner = client_fixture(tmp_path)
    result = query(client, owner, INSTALL, {"i": {"version": "1.2.0", "consent": True}})
    assert not result.get("errors"), result
    current = result["data"]["desktop_control_install"]
    assert current["ready"] and current["source"] == "managed" and current["verified"] and current["version"] == "1.2.0"
    assert len(server.requests) == 1
    events = [event for event in read_events(50) if event["kind"] == "desktop_control.install"]
    assert events and events[-1]["version"] == "1.2.0" and events[-1]["source"] == "managed"


def test_a_download_that_fails_verification_is_an_error_and_installs_nothing(tmp_path, install_world):
    desktop, server, archive = install_world
    tampered = bytearray(archive)
    tampered[-1] ^= 0xFF
    server.serve_asset(bytes(tampered))
    identity, state, client, owner = client_fixture(tmp_path)
    result = query(client, owner, INSTALL, {"i": {"version": "1.2.0", "consent": True}})
    assert result["errors"][0]["extensions"]["issue"] == "hash_mismatch"
    assert "checksum" in result["errors"][0]["message"] and "Nothing was installed" in result["errors"][0]["message"]
    assert not desktop.bin_dir.exists() and status_of(client, owner)["installed"] is False
    assert not [event for event in read_events(50) if event["kind"] == "desktop_control.install"]


def test_a_platform_without_pins_cannot_install(tmp_path):
    desktop = ad.AgentDesktopClient(home_root=tmp_path / "state", platform=ad.HostPlatform("linux", "x86_64", True))
    ad.set_client(desktop)
    try:
        identity, state, client, owner = client_fixture(tmp_path)
        result = query(client, owner, INSTALL, {"i": {"version": "1.2.0", "consent": True}})
    finally:
        ad.set_client(None)
    assert result["errors"][0]["extensions"]["issue"] == "pin_incomplete"


# ---------------------------------------------------------------- remove


def test_remove_deletes_the_managed_install_and_reports_not_installed(tmp_path, install_world):
    desktop, server, _ = install_world
    identity, state, client, owner = client_fixture(tmp_path)
    assert not query(client, owner, INSTALL, {"i": {"version": "1.2.0", "consent": True}}).get("errors")
    result = query(client, owner, REMOVE)
    assert not result.get("errors"), result
    current = result["data"]["desktop_control_remove"]
    assert current["installed"] is False and current["ready"] is False
    assert not desktop.bin_dir.exists()
    assert [event for event in read_events(50) if event["kind"] == "desktop_control.remove"]


def test_remove_stops_running_helpers(tmp_path, install_world, monkeypatch):
    desktop, _, _ = install_world
    killed = []
    monkeypatch.setattr(desktop, "kill_all", lambda: killed.append(True))
    identity, state, client, owner = client_fixture(tmp_path)
    query(client, owner, REMOVE)
    assert killed == [True]


# ---------------------------------------------------------------- explicit path


def make_program(directory: Path, **options) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "agent-desktop"
    binary.write_bytes(fake_cli_source(**options))
    binary.chmod(0o700)
    (directory / "agent-desktop-macos-helper").write_bytes(b"h")
    if os.name != "nt":
        directory.chmod(0o700)
    return binary


def test_setting_a_valid_path_uses_it_without_a_hash_and_saves_it(tmp_path, install_world):
    desktop, _, _ = install_world
    binary = make_program(tmp_path / "mine")
    identity, state, client, owner = client_fixture(tmp_path)
    result = query(client, owner, SET_PATH, {"p": str(binary)})
    assert not result.get("errors"), result
    current = result["data"]["desktop_control_set_path"]
    assert current["source"] == "configured" and current["verified"] is False and current["ready"] is True
    assert current["configured_path"] == str(binary) and current["path"] == str(binary)
    from termx.desktop.agent_desktop_setting import load_setting
    assert load_setting(desktop.home_root) == str(binary)
    assert desktop.setting == str(binary)
    assert [event for event in read_events(50) if event["kind"] == "desktop_control.path_set"]


@pytest.mark.parametrize("make", [
    lambda root: str(make_program(root / "old", version="1.1.0")),
    lambda root: "relative/agent-desktop",
    lambda root: str(root / "missing" / "agent-desktop"),
])
def test_a_path_that_fails_the_checks_is_refused_and_not_saved(tmp_path, install_world, make):
    desktop, _, _ = install_world
    identity, state, client, owner = client_fixture(tmp_path)
    result = query(client, owner, SET_PATH, {"p": make(tmp_path)})
    assert result["errors"][0]["extensions"]["http_status"] == 400 and result["errors"][0]["extensions"]["issue"]
    from termx.desktop.agent_desktop_setting import load_setting
    assert load_setting(desktop.home_root) is None and desktop.setting is None


def test_a_missing_helper_is_refused(tmp_path, install_world):
    binary = make_program(tmp_path / "nohelper")
    (binary.parent / "agent-desktop-macos-helper").unlink()
    identity, state, client, owner = client_fixture(tmp_path)
    result = query(client, owner, SET_PATH, {"p": str(binary)})
    assert result["errors"][0]["extensions"]["issue"] == "helper_missing"


def test_clearing_the_path_goes_back_to_discovery(tmp_path, install_world):
    desktop, _, _ = install_world
    binary = make_program(tmp_path / "mine")
    identity, state, client, owner = client_fixture(tmp_path)
    query(client, owner, SET_PATH, {"p": str(binary)})
    result = query(client, owner, SET_PATH, {"p": None})
    assert not result.get("errors"), result
    current = result["data"]["desktop_control_set_path"]
    assert current["configured_path"] is None and current["installed"] is False and desktop.setting is None


def test_the_environment_variable_locks_the_path_setting(tmp_path, install_world, monkeypatch):
    binary = make_program(tmp_path / "mine")
    monkeypatch.setenv("TERMX_AGENT_DESKTOP", str(binary))
    identity, state, client, owner = client_fixture(tmp_path)
    assert status_of(client, owner)["setting_locked"] is True
    denied(query(client, owner, SET_PATH, {"p": str(binary)}), 409)


# ---------------------------------------------------------------- schema file


def test_schema_graphql_is_the_regenerated_schema():
    from termx.graphql.schema import schema

    committed = (Path(__file__).resolve().parents[1] / "schema.graphql").read_text("utf-8")
    assert committed.rstrip() == str(schema).rstrip()
    assert "desktop_control_status" in committed
