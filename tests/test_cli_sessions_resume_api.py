"""POST /api/cli-sessions/{id}/resume: scopes, ownership, linking and the bypass re-confirmation (fake CLI)."""
from __future__ import annotations

from cli_fake import posix_only
from test_cli_sessions_api import boot, create, user, wait_status
from termx.cli_sessions import status as S

pytestmark = posix_only


def test_resume_over_rest_starts_a_new_session_and_is_scoped_like_every_other_post(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    _, viewer = user(identity, state, "viewer", "viewer", trusted=False)
    bob, bob_headers = user(identity, state, "bob", "operator")
    with client:
        made = create(client, owner, pid)
        sid = made.json()["id"]
        wait_status(client, owner, sid, {S.WAITING_INPUT})
        # a live session is not resumable
        live = client.post(f"/api/cli-sessions/{sid}/resume", headers=owner)
        assert live.status_code == 409 and "still running" in live.json()["detail"]
        assert client.post(f"/api/cli-sessions/{sid}/stop", headers=owner).json()["status"] == S.DONE
        # credentials and scopes
        assert client.post(f"/api/cli-sessions/{sid}/resume").status_code == 401
        assert client.post(f"/api/cli-sessions/{sid}/resume", headers=viewer).status_code in (403, 404)
        assert client.post(f"/api/cli-sessions/{sid}/resume", headers=bob_headers).status_code in (403, 404)
        assert client.post("/api/cli-sessions/nope/resume", headers=owner).status_code == 404
        assert client.post(f"/api/cli-sessions/{sid}/resume", headers=owner, json={"argv": ["rm"]}).status_code == 422
        resumed = client.post(f"/api/cli-sessions/{sid}/resume", headers=owner, json={})
        assert resumed.status_code == 201, resumed.text
        row = resumed.json()
        assert row["id"] != sid and row["resumed_from"] == sid and row["project_id"] == pid and row["pty_session_id"]
        assert "argv" not in str(row) and "owner" not in row
        assert client.get(f"/api/cli-sessions/{sid}", headers=owner).json()["resumed_to"] == row["id"]
        assert client.post(f"/api/cli-sessions/{sid}/resume", headers=owner).status_code == 409  # once
        # the new terminal is the owner's: it streams for them only
        with client.websocket_connect(f"/api/sessions/{row['pty_session_id']}/pty", headers=owner) as socket:
            assert b"FAKE READY" in socket.receive_bytes()
        assert wait_status(client, owner, row["id"], {S.WAITING_INPUT, S.WORKING})["resumed_from"] == sid
        assert client.post(f"/api/cli-sessions/{row['id']}/stop", headers=owner).status_code == 200


def test_resume_of_a_bypass_session_over_rest_needs_confirm_bypass_again(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        sid = create(client, owner, pid).json()["id"]
        wait_status(client, owner, sid, {S.WAITING_INPUT})
        client.post(f"/api/cli-sessions/{sid}/stop", headers=owner)
        record = state.cli_sessions.store.get("cli_session", sid)
        body = {k: v for k, v in record.items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}
        body.update(mode="bypass", sandbox_profile="workspace", resumed_to=None, status="lost", ended_reason="lost")
        state.cli_sessions.store.update("cli_session", sid, body)
        assert client.post(f"/api/cli-sessions/{sid}/resume", headers=owner).status_code == 403
        assert client.post(f"/api/cli-sessions/{sid}/resume", headers=owner, json={"confirm_bypass": False}).status_code == 403
        seen = {}

        def create_terminal(**kwargs):
            from termx.terminals import TerminalError

            seen.update(kwargs)
            raise TerminalError("no sandbox backend here")

        state.sessions.create = create_terminal
        done = client.post(f"/api/cli-sessions/{sid}/resume", headers=owner, json={"confirm_bypass": True})
        assert done.status_code == 503 and "--dangerously-skip-permissions" in seen["argv"] and "--resume" in seen["argv"]


def test_the_graphql_event_shape_carries_the_resume_links(tmp_path):
    identity, state, client, owner, pid = boot(tmp_path)
    with client:
        sid = create(client, owner, pid).json()["id"]
        wait_status(client, owner, sid, {S.WAITING_INPUT})
        client.post(f"/api/cli-sessions/{sid}/stop", headers=owner)
        new = client.post(f"/api/cli-sessions/{sid}/resume", headers=owner).json()
        token = owner["Authorization"].split()[1]
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
            socket.send_json({"type": "connection_init", "payload": {"authorization": f"Bearer {token}"}})
            assert socket.receive_json()["type"] == "connection_ack"
            query = "subscription { cli_session_events { type session { id resumed_from resumed_to resumable } } }"
            socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
            seen = {}
            for _ in range(6):
                message = socket.receive_json()
                session = message["payload"]["data"]["cli_session_events"]["session"]
                seen[session["id"]] = session
                if len(seen) == 2:
                    break
            socket.send_json({"id": "1", "type": "complete"})
        assert seen[sid]["resumed_to"] == new["id"] and seen[new["id"]]["resumed_from"] == sid and seen[sid]["resumable"] is True
        client.post(f"/api/cli-sessions/{new['id']}/stop", headers=owner)
