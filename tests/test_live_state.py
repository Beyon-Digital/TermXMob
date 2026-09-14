from __future__ import annotations

import asyncio
import json
import sys

from fastapi.testclient import TestClient

from termx.app import AppState, create_app
from termx.sessions import SessionManager, parse_osc7

PROMOTING_ARGV = [
    sys.executable,
    "-u",
    "-c",
    "import sys, time\n"
    "sys.stdout.write('\\x1b]7;file://host/tmp/termx-live\\x07READY\\n')\n"
    "sys.stdout.flush()\n"
    "for _ in range(5):\n"
    "    sys.stdout.write('tick\\n')\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.15)\n"
    "time.sleep(3)\n",
]


class _Collector:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.control: list[str] = []

    async def send_bytes(self, data: bytes) -> None:
        self.chunks.append(data)

    async def send_text(self, data: str) -> None:
        self.control.append(data)


def test_parse_osc7() -> None:
    cwd, rest = parse_osc7(b"\x1b]7;file://host/Users/jane/My%20Code\x07tail")
    assert cwd == "/Users/jane/My Code"
    assert rest == b"tail"
    assert parse_osc7(b"plain output")[0] is None
    assert parse_osc7(b"\x1b]7;file://host/tmp\x1b\\")[0] == "/tmp"
    assert parse_osc7(b"\x1b]7;file:///root\x07")[0] == "/root"


def test_session_reports_cwd_and_activity(monkeypatch) -> None:
    monkeypatch.setenv("TERMX_ACTIVITY_IDLE_S", "0.3")
    monkeypatch.setenv("TERMX_ACTIVITY_NOTIFY_S", "0.2")

    async def inner() -> None:
        manager = SessionManager()
        session = manager.create(argv=PROMOTING_ARGV)
        sink = _Collector()
        session.subscribe(sink)
        payloads: list[dict] = []
        for _ in range(120):
            await asyncio.sleep(0.05)
            for text in sink.control:
                try:
                    payloads.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
            sink.control.clear()
            if any(item.get("type") == "cwd" for item in payloads) and any(
                item.get("type") == "activity" and item.get("state") == "idle" for item in payloads
            ):
                break
        manager.kill(session.id)
        assert any(item.get("type") == "cwd" and item.get("cwd") == "/tmp/termx-live" for item in payloads)
        idle = [item for item in payloads if item.get("type") == "activity" and item.get("state") == "idle"]
        assert idle and idle[0]["duration_s"] >= 1
        assert any(item.get("type") == "activity" and item.get("state") == "busy" for item in payloads)

    asyncio.run(inner())


def test_devices_list_and_revoke() -> None:
    client = TestClient(create_app(AppState(passcode="secret"), web_dir=None))
    issued = client.post("/api/pair", headers={"X-Termx-Passcode": "secret"}).json()["token"]
    devices = client.get("/api/devices", headers={"X-Termx-Passcode": "secret"}).json()["devices"]
    assert len(devices) == 1
    device_id = devices[0]["id"]
    assert client.delete(f"/api/devices/{device_id}").status_code == 401
    assert client.delete(f"/api/devices/{device_id}", headers={"X-Termx-Passcode": "secret"}).status_code == 200
    assert client.get("/api/devices", headers={"X-Termx-Passcode": "secret"}).json()["devices"] == []
    assert client.get("/api/sessions", headers={"Authorization": f"Bearer {issued}"}).status_code == 401


def test_shell_integration_zsh_sets_zdotdir(tmp_path):
    from termx.sessions import shell_integration_dir, with_shell_integration

    env = {"ZDOTDIR": str(tmp_path / "orig")}
    argv = with_shell_integration(["/bin/zsh", "-l"], env)
    assert argv == ["/bin/zsh", "-l"]
    assert env["TERMX_ZDOTDIR"] == str(tmp_path / "orig")
    root = env["ZDOTDIR"]
    assert root != env["TERMX_ZDOTDIR"]
    assert "add-zsh-hook precmd _termx_osc7" in (shell_integration_dir() / ".zshrc").read_text()
    assert ']7;file://' in (shell_integration_dir() / ".zshrc").read_text()


def test_shell_integration_bash_adds_rcfile():
    from termx.sessions import with_shell_integration

    env: dict[str, str] = {}
    argv = with_shell_integration(["/bin/bash", "-l"], env)
    assert argv[0] == "/bin/bash"
    assert argv[1] == "--rcfile"
    assert argv[2].endswith("bashrc")
    assert "-l" in argv[3:]


def test_shell_integration_ignores_other_shells():
    from termx.sessions import with_shell_integration

    env: dict[str, str] = {}
    argv = with_shell_integration(["C:\\Windows\\System32\\cmd.exe"], env)
    assert argv == ["C:\\Windows\\System32\\cmd.exe"]
    assert env == {}
