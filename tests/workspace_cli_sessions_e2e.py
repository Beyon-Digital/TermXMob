"""CLI sessions end to end over real sockets: a real uvicorn host, a fake CLI, no browser, no network.

The fake CLI behaves like a hook-capable CLI: it reads the ``--settings`` file the host generated and runs
the very hook command it names (the real forwarder script) for each lifecycle event, so the whole path
CLI -> forwarder -> loopback capability URL -> status machine -> REST / GraphQL subscription / pty websocket
is exercised for real. Run: ``python tests/workspace_cli_sessions_e2e.py [report-dir]`` (POSIX only).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep

import httpx
import uvicorn
import websockets

HOOKY_CLI = r'''
import json, os, subprocess, sys

args = sys.argv[1:]
settings = json.load(open(args[args.index("--settings") + 1])) if "--settings" in args else {"hooks": {}}


def fire(event, **extra):
    entries = settings["hooks"].get(event)
    if not entries:
        return
    command = entries[0]["hooks"][0]["command"]
    payload = json.dumps({"hook_event_name": event, "session_id": "native", **extra}).encode()
    subprocess.run(command, shell=True, input=payload, env=os.environ, capture_output=True, timeout=20)


fire("SessionStart")
print("HOOKY READY", flush=True)
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if line == "quit":
        fire("SessionEnd")
        sys.exit(0)
    if line == "turn":
        fire("UserPromptSubmit")
        print("TURN RUNNING", flush=True)
        fire("Stop")
    elif line == "ask":
        fire("PermissionRequest", tool_name="Bash")
        print("ASKING", flush=True)
    else:
        print("ECHO:" + line, flush=True)
'''


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def eventually(check, timeout=15.0, label="condition"):
    deadline = monotonic() + timeout
    last = None
    while monotonic() < deadline:
        last = check()
        if last:
            return last
        sleep(0.1)
    raise AssertionError(f"{label} not reached (last: {last})")


async def pty_text(port, token, pty_id, needle):
    async with websockets.connect(f"ws://127.0.0.1:{port}/api/sessions/{pty_id}/pty", additional_headers={"Authorization": f"Bearer {token}"}) as socket_:
        seen = b""
        while needle.encode() not in seen:
            seen += await asyncio.wait_for(socket_.recv(), 10)
        return seen.decode("utf-8", "replace")


async def next_events(port, token, until):
    """Subscribe and collect cli_session_events until ``until(events)`` is true."""
    events = []
    async with websockets.connect(f"ws://127.0.0.1:{port}/graphql", subprotocols=["graphql-transport-ws"]) as socket_:
        await socket_.send(json.dumps({"type": "connection_init", "payload": {"authorization": f"Bearer {token}"}}))
        assert json.loads(await socket_.recv())["type"] == "connection_ack"
        query = "subscription { cli_session_events { type session { id status status_source } } }"
        await socket_.send(json.dumps({"id": "1", "type": "subscribe", "payload": {"query": query}}))
        while not until(events):
            message = json.loads(await asyncio.wait_for(socket_.recv(), 15))
            if message["type"] == "next":
                events.append(message["payload"]["data"]["cli_session_events"])
    return events


def run(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = {"proof": "cli-sessions-over-real-sockets", "steps": []}
    with tempfile.TemporaryDirectory(prefix="termx-cli-e2e-") as temporary:
        root = Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root / "config"), TERMX_AGENTS_DIR=str(root / "agents"), TERMX_ENGINE_STARTUP_REFRESH="0",
                          TERMX_PROVIDER_STARTUP_REFRESH="0", TERMX_AGENT_DESKTOP_STARTUP_REFRESH="0")
        from termx.app import AppState, create_app

        port = free_port()
        state = AppState(passcode=None, port=port)
        state.identity.setup_owner("cli-owner", "cli-e2e-fixture-password-123")
        cli = root / "bin" / "claude"
        cli.parent.mkdir()
        cli.write_text(f"#!{sys.executable}\n{HOOKY_CLI}", encoding="utf-8")
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
        project = root / "project"
        project.mkdir()
        project_id = state.projects.register(str(project))["id"]
        app = create_app(state)
        service = state.cli_sessions
        service._executables = {"claude": str(cli)}
        service.tick, service.thresholds, service.grace = 0.1, {"quiet_after": 0.5, "idle_after": 4.0, "sustain": 0.6}, (0.6, 0.6)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        eventually(lambda: server.started, 25, "host start")
        base = f"http://127.0.0.1:{port}"
        try:
            login = httpx.post(f"{base}/auth/login", json={"method": "local-password", "username": "cli-owner", "password": "cli-e2e-fixture-password-123"})
            token = (login.json().get("access_token") or login.json().get("credentials", {}).get("access_token")) if login.status_code == 200 else None
            if not token:
                import asyncio as _asyncio

                token = _asyncio.run(state.identity.login("local-password", {"username": "cli-owner", "password": "cli-e2e-fixture-password-123"}, peer="local")).access_token
            headers = {"Authorization": f"Bearer {token}"}
            client = httpx.Client(base_url=base, headers=headers, timeout=20)

            made = client.post("/api/cli-sessions", json={"cli": "claude", "project_id": project_id, "prompt": "hello"})
            assert made.status_code == 201, made.text
            row = made.json()
            sid, pty = row["id"], row["pty_session_id"]
            report["steps"].append({"started": {"status": row["status"], "cli": row["cli"]}})

            screen = asyncio.run(pty_text(port, token, pty, "HOOKY READY"))
            assert "HOOKY READY" in screen
            report["steps"].append({"tui_streamed_on_existing_pty_socket": True})

            def status(expected, source=None):
                got = client.get(f"/api/cli-sessions/{sid}").json()
                return got if got["status"] == expected and (source is None or got["status_source"] == source) else None

            eventually(lambda: status("waiting_input"), label="first quiet prompt")
            assert client.post(f"/api/cli-sessions/{sid}/input", json={"text": "ask"}).status_code == 200
            asking = eventually(lambda: status("needs_approval", "hook"), label="PermissionRequest hook")
            report["steps"].append({"hook_permission_request": {"status": asking["status"], "source": asking["status_source"], "attention": asking["attention"]}})
            assert client.post(f"/api/cli-sessions/{sid}/input", json={"text": "turn"}).status_code == 200
            done_turn = eventually(lambda: status("waiting_input", "hook"), label="Stop hook")
            report["steps"].append({"hook_stop": {"status": done_turn["status"], "source": done_turn["status_source"]}})

            events = asyncio.run(next_events(port, token, lambda seen: any(e["type"] == "cli.session.created" for e in seen)))
            assert events[0]["session"]["id"] == sid
            report["steps"].append({"subscription_snapshot": events[0]["type"]})

            stopped = client.post(f"/api/cli-sessions/{sid}/stop")
            assert stopped.status_code == 200 and stopped.json()["status"] == "done", stopped.text
            assert client.post(f"/api/cli-sessions/{sid}/input", json={"text": "late"}).status_code == 409
            final = asyncio.run(next_events(port, token, lambda seen: any(e["type"] == "cli.session.exited" for e in seen)))
            assert [e["session"]["status"] for e in final if e["type"] == "cli.session.exited"] == ["done"]
            report["steps"].append({"stopped": True, "exited_event": True, "pty_reaped": state.sessions.get(pty) is None})
            assert state.sessions.list() == []
            report["status"] = "passed"
        finally:
            server.should_exit = True
            worker.join(timeout=20)
    (destination / "cli-sessions-e2e.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    if os.name == "nt":
        raise SystemExit("CLI-session e2e is POSIX-only")
    print(json.dumps(run(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="termx-cli-e2e-report-")), indent=2))
