"""REST + GraphQL surface of CLI sessions: scopes, principal isolation, statuses, hooks, events (fake CLI)."""
from __future__ import annotations

import asyncio
import re
import time

import pytest
from fastapi.testclient import TestClient

from cli_fake import git_repo, posix_only, write_fake_cli
from test_authorization import PASSWORD, client_fixture
from termx.app import AppState, create_app
from termx.authorization import ROLES
from termx.cli_sessions import hooks as H
from termx.cli_sessions import status as S

pytestmark = posix_only
FAST = {"quiet_after": 0.5, "idle_after": 3.0, "sustain": 0.6}


def boot(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    fake = write_fake_cli(tmp_path / "bin")
    service = state.cli_sessions
    service._executables = {"claude": fake, "codex": fake, "aider": fake}
    service.tick, service.thresholds, service.grace = 0.1, dict(FAST), (0.6, 0.6)
    project = tmp_path / "proj"
    project.mkdir()
    pid = state.projects.register(str(project))["id"]
    return identity, state, client, owner, pid


def user(identity, state, name, role, *, trusted=True):
    principal = identity.create_local_user(name, PASSWORD, list(ROLES[role]))
    state.authorization.set_role(principal.id, role, trusted_execution=trusted)
    token = asyncio.run(identity.login("local-password", {"username": name, "password": PASSWORD}, peer="local")).access_token
    return principal, {"Authorization": f"Bearer {token}"}


def wait_status(client, headers, session_id, wanted, timeout=12.0):
    deadline = time.monotonic() + timeout
    row = {}
    while time.monotonic() < deadline:
        row = client.get(f"/api/cli-sessions/{session_id}", headers=headers).json()
        if row.get("status") in wanted:
            return row
        time.sleep(0.1)
    raise AssertionError(f"status never reached {wanted}: {row}")


def create(client, headers, project_id, **body):
    return client.post("/api/cli-sessions", headers=headers, json={"cli": "claude", "project_id": project_id, **body})


def read_text(state, owner_headers, client, session_id, needle, timeout=8.0):
    deadline = time.monotonic() + timeout
    owner = state.identity.resolve(owner_headers["Authorization"].split()[1]).principal.id
    while time.monotonic() < deadline:
        text = state.cli_sessions.read(owner, session_id)["text"]
        if needle in text:
            return text
        time.sleep(0.1)
    raise AssertionError(f"{needle!r} not seen")


def test_agents_endpoint_needs_a_scope_and_lists_every_known_cli(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        assert client.get("/api/cli-agents").status_code == 401
        body = client.get("/api/cli-agents", headers=owner).json()
    assert [agent["id"] for agent in body["agents"]] == ["claude", "codex", "gemini", "aider"]
    for agent in body["agents"]:
        assert {"id", "label", "installed", "version", "hooks", "modes", "bypass_flags", "resume", "verified"} <= set(agent)
        assert isinstance(agent["installed"], bool) and "path" not in agent


def test_scopes_viewer_can_list_but_not_start_or_steer(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    _, viewer = user(identity, state, "viewer", "viewer", trusted=False)
    with client:
        assert client.post("/api/cli-sessions", json={"cli": "claude", "project_id": pid}).status_code == 401
        assert client.get("/api/cli-sessions").status_code == 401
        assert create(client, viewer, pid).status_code == 403
        assert client.get("/api/cli-sessions", headers=viewer).json() == {"sessions": []}
        made = create(client, owner, pid)
        assert made.status_code == 201, made.text
        sid = made.json()["id"]
        assert client.get(f"/api/cli-sessions/{sid}", headers=viewer).status_code == 404  # not the viewer's
        for action in ("input", "interrupt", "stop"):
            assert client.post(f"/api/cli-sessions/{sid}/{action}", headers=viewer, json={"text": "x"} if action == "input" else None).status_code in (403, 404)
        assert client.post(f"/api/cli-sessions/{sid}/stop", headers=owner).status_code == 200


def test_device_token_scopes_gate_view_and_control(tmp_path):
    state = AppState(passcode="host-secret")
    app = create_app(state)
    fake = write_fake_cli(tmp_path / "bin")
    state.cli_sessions._executables = {"claude": fake}
    state.cli_sessions.tick, state.cli_sessions.thresholds = 0.1, dict(FAST)
    viewer = state.tokens.issue(["terminal-view"], device_name="phone")
    controller = state.tokens.issue(["terminal-view", "terminal-control", "agent-view"], device_name="tablet")
    root = tmp_path / "w"
    root.mkdir()
    body = {"cli": "claude", "cwd": str(root)}
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/cli-sessions", headers={"x-termx-passcode": "wrong"}).status_code == 401
        assert client.get("/api/cli-sessions", headers={"x-termx-passcode": viewer}).status_code == 200
        denied = client.post("/api/cli-sessions", headers={"x-termx-passcode": viewer}, json=body)
        assert denied.status_code == 403 and "terminal-control" in denied.json()["detail"]
        made = client.post("/api/cli-sessions", headers={"x-termx-passcode": controller}, json=body)
        assert made.status_code == 201, made.text
        sid = made.json()["id"]
        # a compatibility credential is the single host owner: the passcode sees the controller's session too
        assert [row["id"] for row in client.get("/api/cli-sessions", headers={"x-termx-passcode": "host-secret"}).json()["sessions"]] == [sid]
        assert client.post(f"/api/cli-sessions/{sid}/input", headers={"x-termx-passcode": viewer}, json={"text": "x"}).status_code == 403
        assert client.post(f"/api/cli-sessions/{sid}/stop", headers={"x-termx-passcode": controller}).json()["status"] == S.DONE


def test_lifecycle_over_rest_and_the_tui_streams_on_the_existing_pty_socket(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        made = create(client, owner, pid, prompt="Say hi", title="My claude")
        assert made.status_code == 201, made.text
        row = made.json()
        assert row["cli"] == "claude" and row["title"] == "My claude" and row["project_id"] == pid and row["isolation"] == "none"
        assert set(row) >= {"id", "status", "status_source", "pty_session_id", "started_at", "attention", "resumable", "sandbox_profile", "mode"}
        assert "owner" not in row and "argv" not in str(row) and "Say hi" not in str(row)
        sid, pty = row["id"], row["pty_session_id"]
        assert [r["id"] for r in client.get("/api/cli-sessions", headers=owner).json()["sessions"]] == [sid]
        assert wait_status(client, owner, sid, {S.WAITING_INPUT})["status_source"] == "heuristic"
        # the existing terminal websocket carries the TUI: no duplicate streaming route
        with client.websocket_connect(f"/api/sessions/{pty}/pty", headers=owner) as socket:
            assert b"FAKE READY" in socket.receive_bytes()
        sent = client.post(f"/api/cli-sessions/{sid}/input", headers=owner, json={"text": "ping"})
        assert sent.status_code == 200 and sent.json()["status"] == S.WORKING
        read_text(state, owner, client, sid, "ECHO:ping")
        assert client.post(f"/api/cli-sessions/{sid}/interrupt", headers=owner).status_code == 200
        stopped = client.post(f"/api/cli-sessions/{sid}/stop", headers=owner)
        assert stopped.status_code == 200 and stopped.json()["status"] == S.DONE and stopped.json()["ended_reason"] == "stopped"
        assert client.post(f"/api/cli-sessions/{sid}/input", headers=owner, json={"text": "again"}).status_code == 409
        assert client.get(f"/api/cli-sessions/{sid}", headers=owner).json()["status"] == S.DONE
        assert client.get("/api/cli-sessions/nope", headers=owner).status_code == 404


def test_request_validation(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        assert client.post("/api/cli-sessions", headers=owner, json={"cli": "claude", "argv": ["rm"]}).status_code == 422  # no raw argv
        assert client.post("/api/cli-sessions", headers=owner, json={"cli": "claude", "mode": "turbo"}).status_code == 422
        assert client.post("/api/cli-sessions", headers=owner, json={"cli": "bash", "project_id": pid}).status_code == 400
        assert client.post("/api/cli-sessions", headers=owner, json={"cli": "gemini", "project_id": pid}).status_code == 409  # not installed
        assert client.post("/api/cli-sessions", headers=owner, json={"cli": "claude", "project_id": "ghost"}).status_code == 404
        assert client.post("/api/cli-sessions", headers=owner, json={"cli": "claude", "project_id": pid, "cwd": "/"}).status_code == 400
        assert client.post("/api/cli-sessions", headers=owner, json={"cli": "claude", "project_id": pid, "isolation": "worktree"}).status_code == 400  # not a git repo
        assert state.sessions.list() == []


def test_bypass_needs_a_sandbox_profile_and_a_human_confirmation(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        for body in ({"mode": "bypass"}, {"mode": "bypass", "confirm_bypass": True}, {"mode": "bypass", "sandbox_profile": "workspace"}):
            assert create(client, owner, pid, **body).status_code == 403
        assert state.sessions.list() == []
        seen = {}

        def create_terminal(**kwargs):
            from termx.terminals import TerminalError

            seen.update(kwargs)
            raise TerminalError("no sandbox backend here")

        state.sessions.create = create_terminal
        done = create(client, owner, pid, mode="bypass", sandbox_profile="workspace", confirm_bypass=True)
        assert done.status_code == 503 and "--dangerously-skip-permissions" in seen["argv"]


def test_worktree_isolation_over_rest_and_it_needs_git_scopes(tmp_path):
    identity, state, client, owner, _ = boot(tmp_path)
    repo = git_repo(tmp_path / "repo")
    pid = state.projects.register(str(repo))["id"]
    bob, bob_headers = user(identity, state, "bob", "operator")
    state.authorization.grant_project(bob.id, pid, ["terminal-view", "terminal-control", "agent-view"])
    with client:
        denied = create(client, bob_headers, pid, isolation="worktree")
        assert denied.status_code == 403  # git-read/git-write are not in his grant
        made = create(client, owner, pid, isolation="worktree")
        assert made.status_code == 201, made.text
        row = made.json()
        assert row["isolation"] == "worktree" and row["worktree"]["branch"].startswith("termx/cli-") and row["cwd"] == row["worktree"]["path"]
        assert (repo / "README.md").read_text() == "hello\n"
        client.post(f"/api/cli-sessions/{row['id']}/stop", headers=owner)


def test_principals_cannot_see_or_steer_each_others_sessions(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    bob, bob_headers = user(identity, state, "bob", "operator")
    with client:
        mine = create(client, owner, pid).json()
        # bob has no grant on the project
        assert create(client, bob_headers, pid).status_code == 403
        state.authorization.grant_project(bob.id, pid, ["terminal-view", "terminal-control", "agent-view"])
        # a fresh login picks up the changed policy version
        token = asyncio.run(identity.login("local-password", {"username": "bob", "password": PASSWORD}, peer="local")).access_token
        bob_headers = {"Authorization": f"Bearer {token}"}
        theirs = create(client, bob_headers, pid)
        assert theirs.status_code == 201, theirs.text
        assert [r["id"] for r in client.get("/api/cli-sessions", headers=bob_headers).json()["sessions"]] == [theirs.json()["id"]]
        assert [r["id"] for r in client.get("/api/cli-sessions", headers=owner).json()["sessions"]] == [mine["id"]]
        for method, path in (("get", ""), ("post", "/input"), ("post", "/interrupt"), ("post", "/stop")):
            response = getattr(client, method)(f"/api/cli-sessions/{mine['id']}{path}", headers=bob_headers, **({"json": {"text": "x"}} if path == "/input" else {}))
            assert response.status_code == 404, (path, response.text)
        assert client.get(f"/api/cli-sessions/{mine['id']}", headers=owner).json()["status"] not in S.ENDED
        # the terminal behind a session is claimed too: bob cannot stream owner's TUI either
        assert state.authorization.can(bob_headers["Authorization"].split()[1], "terminal-control", resource_kind="terminal", resource_id=mine["pty_session_id"]) is False
        for sid, headers in ((mine["id"], owner), (theirs.json()["id"], bob_headers)):
            client.post(f"/api/cli-sessions/{sid}/stop", headers=headers)


def hook_path(state, client, owner, sid):
    """The capability URL the CLI was handed, read back out of the child's own environment."""
    assert client.post(f"/api/cli-sessions/{sid}/input", headers=owner, json={"text": "hookurl"}).status_code == 200
    text = read_text(state, owner, client, sid, "HOOKURL=http")
    return re.search(r"HOOKURL=http://127\.0\.0\.1:\d+(/api/cli-sessions/hook/clihook_[A-Za-z0-9_-]+)", text).group(1)


def test_hook_endpoint_updates_status_only_with_a_live_token(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        sid = create(client, owner, pid).json()["id"]
        wait_status(client, owner, sid, {S.WAITING_INPUT})
        path = hook_path(state, client, owner, sid)
        # no credentials at all: the token is the capability
        stop = client.post(path, json={"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "Claude needs permission"})
        assert stop.status_code == 200 and stop.json() == {"ok": True}
        row = client.get(f"/api/cli-sessions/{sid}", headers=owner).json()
        assert (row["status"], row["status_source"]) == (S.NEEDS_APPROVAL, "hook") and row["attention"] is True
        assert row["status_detail"].startswith("hook:Notification")
        assert client.post(path, json={"hook_event_name": "UserPromptSubmit"}).status_code == 200
        assert client.get(f"/api/cli-sessions/{sid}", headers=owner).json()["status"] == S.WORKING
        assert client.post(path, json={"hook_event_name": "Stop"}).status_code == 200
        assert client.get(f"/api/cli-sessions/{sid}", headers=owner).json()["status"] == S.WAITING_INPUT
        # untrusted input: unknown events, non-JSON, wrong shapes and oversize bodies change nothing
        assert client.post(path, json={"hook_event_name": "DropTables"}).status_code == 400
        assert client.post(path, content=b"not json").status_code == 400
        assert client.post(path, json=["Stop"]).status_code == 400
        assert client.post(path, content=b'{"hook_event_name":"Stop","pad":"' + b"x" * 40_000 + b'"}').status_code == 413
        assert client.get(f"/api/cli-sessions/{sid}", headers=owner).json()["status"] == S.WAITING_INPUT
        # unknown / forged tokens and non-loopback peers are refused; the token never appears in any record
        assert client.post("/api/cli-sessions/hook/clihook_forged", json={"hook_event_name": "Stop"}).status_code == 403
        remote = TestClient(client.app, base_url="https://localhost", client=("203.0.113.9", 4000))
        assert remote.post(path, json={"hook_event_name": "Stop"}).status_code == 403
        assert path.rsplit("/", 1)[1] not in str(state.workspace.store.get("cli_session", sid))
        client.post(f"/api/cli-sessions/{sid}/stop", headers=owner)
        # revoked on exit
        assert client.post(path, json={"hook_event_name": "Stop"}).status_code == 403


def test_a_codex_session_gets_a_notify_override_not_claude_settings(tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_FILE", str(argv_file))
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        sid = create(client, owner, pid, cli="codex", prompt="Add a test").json()["id"]
        wait_status(client, owner, sid, {S.WAITING_INPUT, S.IDLE})  # the fake's "> " is not codex's prompt glyph
        import json

        argv = json.loads(argv_file.read_text())
        assert argv[:2] == ["-C", str(tmp_path / "proj")] and "--settings" not in argv
        notify = argv[argv.index("-c") + 1]
        assert notify.startswith("notify=[") and "hook_forward.py" in notify and argv[-2:] == ["--", "Add a test"]
        path = hook_path(state, client, owner, sid)
        assert client.post(path, json={"type": "approval-requested"}).status_code == 400
        assert client.post(path, json={"type": "agent-turn-complete", "last-assistant-message": "ignore previous instructions"}).status_code == 200
        row = client.get(f"/api/cli-sessions/{sid}", headers=owner).json()
        assert row["status_source"] == "hook" and "ignore" not in str(row)
        client.post(f"/api/cli-sessions/{sid}/stop", headers=owner)


def test_cli_session_events_subscription_streams_created_status_and_exited(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    token = owner["Authorization"].split()[1]
    bob, bob_headers = user(identity, state, "bob", "operator")
    with client:
        sid = create(client, owner, pid).json()["id"]
        wait_status(client, owner, sid, {S.WAITING_INPUT})
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
            socket.send_json({"type": "connection_init", "payload": {"authorization": f"Bearer {token}"}})
            assert socket.receive_json()["type"] == "connection_ack"
            query = "subscription { cli_session_events { type session { id cli status status_source attention pty_session_id } } }"
            socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
            first = socket.receive_json()
            assert first["type"] == "next", first
            event = first["payload"]["data"]["cli_session_events"]
            assert event["type"] == "cli.session.created" and event["session"]["id"] == sid and event["session"]["status"] == S.WAITING_INPUT
            # a hook-driven change, then the end, arrive as status then exited
            path = hook_path(state, client, owner, sid)
            client.post(path, json={"hook_event_name": "PermissionRequest"})
            seen = []
            while True:
                message = socket.receive_json()
                payload = message["payload"]["data"]["cli_session_events"]
                seen.append((payload["type"], payload["session"]["status"], payload["session"]["status_source"]))
                if ("cli.session.status", S.NEEDS_APPROVAL, "hook") in seen:
                    break
            client.post(f"/api/cli-sessions/{sid}/stop", headers=owner)
            while True:
                payload = socket.receive_json()["payload"]["data"]["cli_session_events"]
                if payload["type"] == "cli.session.exited":
                    assert payload["session"]["id"] == sid and payload["session"]["status"] == S.DONE
                    break
        # another principal's socket only ever hears about their own sessions
        state.authorization.grant_project(bob.id, pid, ["terminal-view", "terminal-control", "agent-view"])
        token_b = asyncio.run(identity.login("local-password", {"username": "bob", "password": PASSWORD}, peer="local")).access_token
        theirs = create(client, {"Authorization": f"Bearer {token_b}"}, pid).json()["id"]
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
            socket.send_json({"type": "connection_init", "payload": {"authorization": f"Bearer {token_b}"}})
            socket.receive_json()
            socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": "subscription { cli_session_events { type session { id } } }"}})
            only = socket.receive_json()["payload"]["data"]["cli_session_events"]
            assert only["session"]["id"] == theirs
        client.post(f"/api/cli-sessions/{theirs}/stop", headers={"Authorization": f"Bearer {token_b}"})


def test_subscription_requires_authentication(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
                socket.send_json({"type": "connection_init", "payload": {}})
                socket.receive_json()
                socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": "subscription { cli_session_events { type } }"}})
                socket.receive_json()


def test_the_subscription_checks_the_terminal_view_scope(tmp_path):
    state = AppState(passcode="host-secret")
    app = create_app(state)
    blind = state.tokens.issue(["agent-view"], device_name="watch")
    sighted = state.tokens.issue(["terminal-view"], device_name="phone")
    query = "subscription { cli_session_events { type } }"
    with TestClient(app, base_url="http://localhost") as client:
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
            socket.send_json({"type": "connection_init", "payload": {"k": blind}})
            assert socket.receive_json()["type"] == "connection_ack"
            socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
            message = socket.receive_json()
            assert "terminal-view" in str(message["payload"]) and not (message["payload"].get("data") or {}).get("cli_session_events")
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
            socket.send_json({"type": "connection_init", "payload": {"k": sighted}})
            assert socket.receive_json()["type"] == "connection_ack"
            socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
            socket.send_json({"id": "1", "type": "complete"})


def test_the_schema_file_has_the_cli_session_contract():
    from pathlib import Path

    from termx.graphql.schema import schema

    committed = (Path(__file__).resolve().parents[1] / "schema.graphql").read_text("utf-8")
    assert committed.rstrip() == str(schema).rstrip()
    assert "cli_session_events: CliSessionEvent!" in committed and "type CliSession {" in committed
