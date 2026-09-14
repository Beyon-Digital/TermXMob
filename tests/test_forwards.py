from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termx.app import AppState, create_app
from termx.config import ConfigStore
from termx.forwards import ForwardManager, ssh_args


@pytest.fixture()
def fake_ssh(tmp_path: Path, monkeypatch) -> str:
    if os.name == "nt":
        script = tmp_path / "fakessh.bat"
        # ping is the classic no-input sleep; timeout would steal stdin
        script.write_bytes(b"@echo off\r\nping -n 31 127.0.0.1 >nul\r\n")
    else:
        script = tmp_path / "fakessh"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("TERMX_SSH_BIN", str(script))
    return str(script)


def test_ssh_args_local_remote_dynamic() -> None:
    from termx.config import ForwardRule

    local = ssh_args(
        ForwardRule(id="1", name="web", kind="local", listen_port=8080, target_port=3000, ssh_host="me@box")
    )
    assert Path(local[0]).stem == "ssh" or "fakessh" in local[0]
    assert "-N" in local
    assert "-L127.0.0.1:8080:127.0.0.1:3000" in local
    assert local[-1] == "me@box"

    remote = ssh_args(
        ForwardRule(id="2", name="db", kind="remote", listen_port=5432, target_port=5432, ssh_host="me@box")
    )
    assert "-R127.0.0.1:5432:127.0.0.1:5432" in remote

    dynamic = ssh_args(ForwardRule(id="3", name="socks", kind="dynamic", listen_port=1080, ssh_host="me@box"))
    assert "-D127.0.0.1:1080" in dynamic


def test_rule_validation() -> None:
    store = ConfigStore()
    with pytest.raises(ValueError):
        store.validate_rule("tunnel", 8080, 3000, "me@box")
    with pytest.raises(ValueError):
        store.validate_rule("local", 0, 3000, "me@box")
    with pytest.raises(ValueError):
        store.validate_rule("local", 8080, 0, "me@box")
    with pytest.raises(ValueError):
        store.validate_rule("local", 8080, 3000, "")
    with pytest.raises(ValueError):
        store.validate_rule("local", 8080, 3000, "-oProxyCommand=boom")
    kind, listen, target, dest, _, _ = store.validate_rule("dynamic", 1080, 0, "me@box")
    assert (kind, listen, target, dest) == ("dynamic", 1080, 0, "me@box")


def test_config_roundtrip_rules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    store = ConfigStore()
    rule = store.add_rule(
        name="web",
        kind="local",
        listen_port=8080,
        target_port=3000,
        ssh_host="me@box",
        auto_start=True,
    )
    reloaded = ConfigStore()
    rules = reloaded.list_rules()
    assert [item.id for item in rules] == [rule.id]
    assert rules[0].auto_start is True
    assert rules[0].ssh_host == "me@box"
    assert reloaded.patch_rule(rule.id, auto_start=False).auto_start is False
    assert reloaded.delete_rule(rule.id) is True
    assert reloaded.list_rules() == []


def test_manager_lifecycle_with_fake_ssh(tmp_path: Path, monkeypatch, fake_ssh: str) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    store = ConfigStore()
    rule = store.add_rule(name="web", kind="local", listen_port=18080, target_port=13000, ssh_host="me@box")

    manager = ForwardManager(store)
    status = manager.start(rule)
    assert status.state == "running"
    assert status.started_at is not None
    time.sleep(0.2)
    running = manager.status_for(rule.id)
    assert running.state == "running"
    uptime = running.public()["uptime_s"]
    assert uptime is not None and uptime >= 0
    assert manager.stop(rule.id) is True
    after = manager.status_for(rule.id)
    assert after.state == "stopped"
    assert after.started_at is None
    # stopping again is a no-op
    assert manager.stop(rule.id) is False


def test_manager_reports_error_for_missing_binary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TERMX_SSH_BIN", str(tmp_path / "does-not-exist"))
    store = ConfigStore()
    rule = store.add_rule(name="web", kind="local", listen_port=18081, target_port=13001, ssh_host="me@box")

    manager = ForwardManager(store)
    status = manager.start(rule)
    assert status.state == "error"
    assert status.detail and "cannot run ssh" in status.detail


def test_forwards_api(tmp_path: Path, monkeypatch, fake_ssh: str) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))

    created = client.post(
        "/api/forwards",
        params={"k": "secret"},
        json={
            "name": "dev server",
            "kind": "local",
            "listen_port": 18082,
            "target_host": "127.0.0.1",
            "target_port": 13002,
            "ssh_host": "me@box",
            "auto_start": True,
        },
    )
    assert created.status_code == 200
    rule = created.json()
    assert rule["auto_start"] is True

    listed = client.get("/api/forwards", params={"k": "secret"}).json()
    assert [item["id"] for item in listed["rules"]] == [rule["id"]]

    started = client.post(f"/api/forwards/{rule['id']}/start", params={"k": "secret"}).json()
    assert started["state"] == "running"
    time.sleep(0.1)
    stopped = client.post(f"/api/forwards/{rule['id']}/stop", params={"k": "secret"}).json()
    assert stopped["state"] == "stopped"

    assert (
        client.post(
            "/api/forwards",
            params={"k": "secret"},
            json={
                "name": "bad",
                "kind": "local",
                "listen_port": 70000,
                "target_port": 1,
                "ssh_host": "me@box",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/forwards",
            params={"k": "secret"},
            json={
                "name": "bad",
                "kind": "nope",
                "listen_port": 8080,
                "target_port": 1,
                "ssh_host": "me@box",
            },
        ).status_code
        == 400
    )
    patched = client.patch(
        f"/api/forwards/{rule['id']}", params={"k": "secret"}, json={"auto_start": False}
    ).json()
    assert patched["auto_start"] is False
    assert client.delete(f"/api/forwards/{rule['id']}", params={"k": "secret"}).json() == {"ok": True}
    assert client.get("/api/forwards", params={"k": "secret"}).json()["rules"] == []
