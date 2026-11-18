"""Authenticated host health: query, change stream, scope gating, settings, heartbeat file, keep-awake policy."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from _gql import data, err_status, gql
from termx import __version__
from termx import host_status as HS
from termx.app import AppState, create_app
from termx.keep_awake import Backend, KeepAwake

QUERY = """{ host_status {
  version pid started_at uptime_s
  service { backend supported installed managed state }
  holder { mode requested reason running pid version protocol sessions exited_sessions draining direct_terminals }
  recovery { recovering_tasks running_tasks lost_cli_sessions resumable_cli_sessions live_cli_sessions lost_engine_sessions resumable_engine_sessions }
  keep_awake { enabled active supported backend reasons count error }
  heartbeat { at age_s interval_s file error }
} }"""


class FakeBackend(Backend):
    name = "fake"
    supported = True

    def __init__(self) -> None:
        self.running = False
        self.starts = 0

    def start(self, why: str) -> None:
        self.running = True
        self.starts += 1

    def stop(self) -> None:
        self.running = False

    def alive(self) -> bool:
        return self.running


def boot(passcode="host-secret"):
    state = AppState(passcode=passcode)
    state.host_status.keep_awake = KeepAwake(FakeBackend())
    app = create_app(state)
    return state, app


def headers(token):
    return {"x-termx-passcode": token}


def test_the_status_query_needs_authentication_and_the_machine_view_scope():
    state, app = boot()
    viewer = state.tokens.issue(["machine-view"], device_name="watch")
    blind = state.tokens.issue(["terminal-view", "agent-view"], device_name="phone")
    with TestClient(app, base_url="http://localhost") as client:
        assert err_status(client, QUERY) == 401
        assert err_status(client, QUERY, headers=headers("wrong")) == 401
        assert err_status(client, QUERY, headers=headers(blind)) == 403
        status = data(client, QUERY, "host_status", headers=headers(viewer))
        assert status["version"] == __version__ and status["pid"] == os.getpid()
        # the unauthenticated health query is unchanged and says nothing about any of this
        public = data(client, "{ health { app ok version passcode_required } }", "health")
        assert public == {"app": "termx", "ok": True, "version": __version__, "passcode_required": True}


def test_the_payload_reports_uptime_service_holder_recovery_keep_awake_and_heartbeat():
    state, app = boot()
    with TestClient(app, base_url="http://localhost") as client:
        status = data(client, QUERY, "host_status", headers=headers("host-secret"))
    assert status["uptime_s"] >= 0 and status["started_at"] > 1_600_000_000
    assert status["service"]["state"] == "not_installed" and status["service"]["installed"] is False and status["service"]["managed"] is False
    # the suite runs direct on purpose (TERMX_PTY_HOLDER=direct): the status says so and why
    assert status["holder"]["mode"] == "direct" and status["holder"]["requested"] == "direct" and "turned off" in status["holder"]["reason"]
    assert status["holder"]["running"] is False and status["holder"]["sessions"] == 0 and status["holder"]["direct_terminals"] == 0
    assert status["recovery"] == {"recovering_tasks": 0, "running_tasks": 0, "lost_cli_sessions": 0, "resumable_cli_sessions": 0, "live_cli_sessions": 0,
                                  "lost_engine_sessions": 0, "resumable_engine_sessions": 0}
    assert status["keep_awake"]["enabled"] is False and status["keep_awake"]["active"] is False and status["keep_awake"]["count"] == 0
    assert status["heartbeat"]["file"] == "heartbeat.json" and status["heartbeat"]["interval_s"] == 10.0
    assert status["heartbeat"]["at"] is not None and status["heartbeat"]["error"] is None, "the lifespan wrote the first heartbeat"


def test_direct_terminals_are_counted_so_the_app_can_warn(tmp_path):
    state, app = boot()
    with TestClient(app, base_url="http://localhost") as client:
        session = state.sessions.create(argv=["/bin/sh", "-c", "sleep 30"], cwd=str(tmp_path))
        status = data(client, QUERY, "host_status", headers=headers("host-secret"))
        assert status["holder"]["direct_terminals"] == 1
        state.sessions.kill(session.id)


def test_a_failed_holder_is_explained_in_the_status_and_announced_once(monkeypatch):
    monkeypatch.setenv("TERMX_PTY_HOLDER", "auto")
    state, app = boot()
    state.sessions.holder.holder_command = lambda: ["/bin/sh", "-c", "echo cannot start >&2; exit 5"]
    state.sessions.holder.acquire(create=True)  # the first terminal tried to start a holder and could not
    published = []
    state.notifications.publish = lambda *args, **kwargs: published.append((args, kwargs))
    with TestClient(app, base_url="http://localhost") as client:
        status = data(client, QUERY, "host_status", headers=headers("host-secret"))
    assert status["holder"]["mode"] == "direct" and "will not survive a restart" in status["holder"]["reason"]
    assert "cannot start" in status["holder"]["reason"]
    assert len(published) == 1 and "restarts" in published[0][0][0] and published[0][1]["kind"] == "warning"


def test_changing_settings_needs_host_admin_and_applies_immediately():
    state, app = boot()
    viewer = state.tokens.issue(["machine-view"], device_name="watch")
    mutation = "mutation($i: HostSettingsInput!) { update_host_settings(input: $i) { keep_awake { enabled active reasons count backend } holder { requested } } }"
    with TestClient(app, base_url="http://localhost") as client:
        assert err_status(client, mutation, {"i": {"keep_awake": True}}) == 401
        assert err_status(client, mutation, {"i": {"keep_awake": True}}, headers=headers(viewer)) == 403
        assert state.store.get().host.keep_awake is False
        out = data(client, mutation, "update_host_settings", {"i": {"keep_awake": True}}, headers=headers("host-secret"))
        assert out["keep_awake"] == {"enabled": True, "active": True, "reasons": [HS.REASON_SETTING], "count": 1, "backend": "fake"}
        assert state.store.get().host.keep_awake is True
        again = data(client, mutation, "update_host_settings", {"i": {"keep_awake": False, "pty_holder": "held"}}, headers=headers("host-secret"))
        assert again["keep_awake"]["active"] is False and again["keep_awake"]["count"] == 0
        assert state.store.get().host.pty_holder == "held"
        assert err_status(client, mutation, {"i": {"pty_holder": "sometimes"}}, headers=headers("host-secret")) == 400
        assert state.store.get().host.pty_holder == "held"


def test_settings_survive_a_restart_of_the_config_store(tmp_path):
    from termx.config import ConfigStore

    store = ConfigStore(tmp_path / "c.json")
    store.update_host(keep_awake=True, pty_holder="direct")
    reloaded = ConfigStore(tmp_path / "c.json").get().host
    assert reloaded.keep_awake is True and reloaded.pty_holder == "direct"
    (tmp_path / "c.json").write_text(json.dumps({"host": {"keep_awake": "yes", "pty_holder": "bogus"}}))
    odd = ConfigStore(tmp_path / "c.json").get().host
    assert odd.keep_awake is True and odd.pty_holder == "auto", "an unknown mode falls back to auto, never to something weaker by accident"
    with pytest.raises(ValueError):
        store.update_host(pty_holder="bogus")


def test_the_saved_terminal_setting_is_reported_apart_from_what_is_in_force(monkeypatch):
    state, app = boot()
    query = "{ host_status { holder { requested setting env_override } } }"
    with TestClient(app, base_url="http://localhost") as client:
        monkeypatch.delenv("TERMX_PTY_HOLDER", raising=False)
        assert data(client, query, "host_status", headers=headers("host-secret"))["holder"] == {"requested": "auto", "setting": "auto", "env_override": False}
        state.store.update_host(pty_holder="held")
        assert data(client, query, "host_status", headers=headers("host-secret"))["holder"] == {"requested": "held", "setting": "held", "env_override": False}
        monkeypatch.setenv("TERMX_PTY_HOLDER", "direct")
        assert data(client, query, "host_status", headers=headers("host-secret"))["holder"] == {"requested": "direct", "setting": "held", "env_override": True}


def test_keep_awake_follows_running_agent_tasks_and_working_cli_sessions():
    from termx.cli_sessions import status as S

    store_tasks = {"running": False}
    cli = SimpleNamespace(_live={})
    state = SimpleNamespace(
        store=SimpleNamespace(get=lambda: SimpleNamespace(host=SimpleNamespace(keep_awake=False))),
        agent_store=SimpleNamespace(task_page=lambda limit=100, statuses=(): [{"id": "t"}] if store_tasks["running"] and "running" in statuses else []),
        cli_sessions=cli, sessions=None, port=1,
    )
    backend = FakeBackend()
    service = HS.HostStatusService(state, keep_awake=KeepAwake(backend))
    service.evaluate()
    assert not backend.running
    store_tasks["running"] = True
    service.evaluate()
    assert backend.running and service.keep_awake.reasons() == [HS.REASON_TASKS]
    cli._live["a"] = SimpleNamespace(tracker=SimpleNamespace(status=S.WORKING))
    service.evaluate()
    assert service.keep_awake.reasons() == [HS.REASON_CLI, HS.REASON_TASKS] and backend.starts == 1
    store_tasks["running"] = False
    service.evaluate()
    assert backend.running and service.keep_awake.reasons() == [HS.REASON_CLI]
    cli._live["a"].tracker.status = S.WAITING_INPUT  # waiting on a human is not work
    service.evaluate()
    assert not backend.running and service.keep_awake.count == 0
    service.keep_awake.close()


# -- heartbeat ---------------------------------------------------------------------------


def make_service(**kwargs):
    state = SimpleNamespace(port=8787, store=None, agent_store=None, cli_sessions=None, sessions=None)
    return HS.HostStatusService(state, keep_awake=KeepAwake(FakeBackend()), **kwargs)


def test_heartbeat_file_is_private_json_with_only_liveness_facts():
    service = make_service()
    service.write_heartbeat()
    path = service.heartbeat_path
    assert path.name == "heartbeat.json" and stat.S_IMODE(path.stat().st_mode) == 0o600
    body = json.loads(path.read_text())
    assert body["pid"] == os.getpid() and body["version"] == __version__ and body["state"] == "running" and body["port"] == 8787
    assert abs(body["at"] - time.time()) < 5 and set(body) == {"schema", "state", "pid", "version", "port", "at", "started_at", "interval_s"}
    assert list(path.parent.glob(".termx-*")) == [], "no temporary file is left behind"


def test_heartbeat_replacement_is_atomic_for_a_concurrent_watchdog():
    service = make_service()
    service.write_heartbeat()
    stop = threading.Event()
    seen: list[float] = []
    torn: list[str] = []

    def watchdog():
        while not stop.is_set():
            try:
                text = service.heartbeat_path.read_text()
            except FileNotFoundError:
                torn.append("missing")
                continue
            try:
                seen.append(json.loads(text)["at"])
            except ValueError:
                torn.append(text[:40])

    readers = [threading.Thread(target=watchdog) for _ in range(3)]
    for reader in readers:
        reader.start()
    for _ in range(150):
        service.write_heartbeat()
    stop.set()
    for reader in readers:
        reader.join()
    assert torn == [], f"a reader saw a torn or missing heartbeat: {torn[:3]}"
    assert len(seen) > 20


def test_the_heartbeat_loop_rewrites_it_and_shutdown_marks_it_stopping():
    async def run():
        service = make_service(heartbeat_s=0.1, poll_s=0.1)
        await service.start()
        first = json.loads(service.heartbeat_path.read_text())["at"]
        await asyncio.sleep(0.45)
        assert json.loads(service.heartbeat_path.read_text())["at"] > first
        await service.close()
        assert json.loads(service.heartbeat_path.read_text())["state"] == "stopping"

    asyncio.run(run())


def test_an_unwritable_heartbeat_is_reported_in_the_status_not_raised(monkeypatch):
    service = make_service()
    monkeypatch.setattr(HS, "atomic_write", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        service.write_heartbeat()
    assert service.snapshot()["heartbeat"]["error"] == "disk full"


# -- the change stream ----------------------------------------------------------------------


def test_host_status_events_stream_changes_and_gate_by_scope(monkeypatch):
    import termx.graphql.subscriptions as subs

    monkeypatch.setattr(subs, "HOST_STATUS_POLL_SECONDS", 0.1)
    state, app = boot()
    viewer = state.tokens.issue(["machine-view"], device_name="watch")
    blind = state.tokens.issue(["agent-view"], device_name="phone")
    query = "subscription { host_status_events { version uptime_s keep_awake { enabled active count } holder { mode } } }"
    with TestClient(app, base_url="http://localhost") as client:
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
            socket.send_json({"type": "connection_init", "payload": {"k": blind}})
            assert socket.receive_json()["type"] == "connection_ack"
            socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
            message = socket.receive_json()
            assert "machine-view" in str(message["payload"]) and not (message["payload"].get("data") or {}).get("host_status_events")
        with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as socket:
            socket.send_json({"type": "connection_init", "payload": {"k": viewer}})
            assert socket.receive_json()["type"] == "connection_ack"
            socket.send_json({"id": "1", "type": "subscribe", "payload": {"query": query}})
            first = socket.receive_json()["payload"]["data"]["host_status_events"]
            assert first["version"] == __version__ and first["keep_awake"]["active"] is False
            state.host_status.update_settings(keep_awake=True)  # a change elsewhere is pushed without polling by the client
            update = socket.receive_json()["payload"]["data"]["host_status_events"]
            assert update["keep_awake"] == {"enabled": True, "active": True, "count": 1}
            socket.send_json({"id": "1", "type": "complete"})


def test_stable_view_ignores_per_second_noise():
    base = {"uptime_s": 1.0, "heartbeat": {"at": 1.0, "age_s": 0.2, "interval_s": 10}, "holder": {"mode": "held"}}
    later = {"uptime_s": 9.0, "heartbeat": {"at": 9.0, "age_s": 0.1, "interval_s": 10}, "holder": {"mode": "held"}}
    assert HS.stable_view(base) == HS.stable_view(later)
    assert HS.stable_view(base) != HS.stable_view({**later, "holder": {"mode": "direct"}})
