"""Engine extension tests — transport, env, codex adapter, gateway, API."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.app import AppState, create_app
from termx.engines.codex import CodexEngine
from termx.engines.env import engine_search_path, resolve_executable
from termx.engines.gateway import EngineGateway
from termx.engines.jsonrpc import JsonlProcess, TransportClosed
from termx.engines.types import EffectiveRunConfiguration, EngineEvent

FIXTURE = Path(__file__).parent / "fixtures" / "fake_codex.py"


def _fake_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update({k: v for k, v in extra.items()})
    return env


def _store(tmp_path: Path) -> AgentStore:
    return AgentStore(tmp_path / "agent.db", artifact_dir=tmp_path / "artifacts")


# --------------------------------------------------------------------- env


def test_resolve_finds_engine_in_user_dir(monkeypatch, tmp_path):
    """The GUI-PATH gap: stripped PATH must not hide ~/.local/bin engines."""
    fake = tmp_path / ".local" / "bin" / "devin"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # stripped GUI env
    resolved = resolve_executable("devin")
    assert resolved == str(fake.resolve())


def test_resolve_override_and_missing(tmp_path):
    missing = resolve_executable("definitely-not-a-real-binary-xyz")
    assert missing is None
    override = resolve_executable(
        "whatever", override=str(FIXTURE), env={})
    # fixture is not executable-bit; expect None unless we chmod
    assert override is None


def test_search_path_includes_user_dirs(monkeypatch, tmp_path):
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    entries = engine_search_path({"PATH": "/usr/bin"})
    assert str(tmp_path / ".local" / "bin") in entries
    assert entries[0] == "/usr/bin"  # existing PATH preserved first


# ----------------------------------------------------------------- jsonrpc


def test_jsonrpc_request_response():
    async def run():
        proc = JsonlProcess([sys.executable, "-u", str(FIXTURE)], env=_fake_env())
        await proc.start()
        try:
            result = await proc.request("initialize", {"clientInfo": {"name": "t"}})
            assert result["userAgent"] == "fake-codex/1.0"
            result = await proc.request("model/list", {})
            assert result["data"][0]["id"] == "gpt-fake-1"
            with pytest.raises(Exception):
                await proc.request("no/such/method", {})
        finally:
            await proc.close()
        assert not proc.running
    asyncio.run(run())


def test_jsonrpc_notifications_and_server_requests():
    notes: list[tuple[str, dict]] = []
    server_reqs: list[tuple[str, dict]] = []

    async def run():
        async def on_req(method, params):
            server_reqs.append((method, params))
            return {"decision": "accept"}

        proc = JsonlProcess(
            [sys.executable, "-u", str(FIXTURE)],
            env=_fake_env(FAKE_CODEX_APPROVE_WHEN="please"),
            on_notification=lambda m, p: notes.append((m, p)),
            on_server_request=on_req,
        )
        await proc.start()
        try:
            await proc.request("initialize", {})
            await proc.notify("initialized", {})
            thread = await proc.request("thread/start", {"cwd": "/tmp"})
            tid = thread["thread"]["id"]
            await proc.request(
                "turn/start",
                {"threadId": tid,
                 "input": [{"type": "text", "text": "please run it"}]},
            )
            deadline = time.monotonic() + 10
            while not any(m == "turn/completed" for m, _ in notes):
                assert time.monotonic() < deadline, "turn never completed"
                await asyncio.sleep(0.05)
        finally:
            await proc.close()
    asyncio.run(run())

    methods = [m for m, _ in notes]
    assert "item/commandExecution/requestApproval" in [m for m, _ in server_reqs]
    assert "turn/started" in methods
    assert "serverRequest/resolved" in methods
    assert "item/completed" in methods
    assert "turn/completed" in methods


def test_jsonrpc_survives_process_exit():
    async def run():
        proc = JsonlProcess(
            [sys.executable, "-u", str(FIXTURE)],
            env=_fake_env(FAKE_CODEX_DIE_AFTER_INIT="1"),
        )
        await proc.start()
        await proc.request("initialize", {})
        await proc.notify("initialized", {})
        deadline = time.monotonic() + 10
        while proc.running:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        with pytest.raises(TransportClosed):
            await proc.request("account/read", {})
    asyncio.run(run())


def test_jsonrpc_malformed_lines_skipped():
    """A peer that emits garbage lines must not wedge the reader."""
    async def run():
        script = (
            "import sys\n"
            "sys.stdout.write('not json\\n')\n"
            "sys.stdout.write('{\\\"id\\\":1,\\\"result\\\":{\\\"ok\\\":true}}\\n')\n"
            "sys.stdout.flush()\n"
            "import time; time.sleep(30)\n"
        )
        proc = JsonlProcess([sys.executable, "-u", "-c", script])
        await proc.start()
        try:
            result = await proc.request("anything", {})
            assert result == {"ok": True}
        finally:
            await proc.close()
    asyncio.run(run())


# -------------------------------------------------------------------- codex


def _engine(tmp_path: Path, events: list, approvals: list | None = None,
            env_extra: dict[str, str] | None = None):
    async def sink(binding_id, token, method, params, kind):
        if approvals is not None:
            approvals.append((binding_id, token, method, params, kind))
        return f"appr_{token}"

    engine = CodexEngine(
        executable_override=sys.executable,
        event_sink=lambda bid, ev: events.append((bid, ev)),
        approval_sink=sink,
        spawn_env=_fake_env(**(env_extra or {})),
    )
    engine._executable = sys.executable  # resolved; argv patched below
    return engine


class _CodexProc(CodexEngine):
    """CodexEngine whose 'codex' binary is the python fixture script."""

    async def _ensure_conn(self) -> None:
        async with self._conn_lock:
            if self._conn and self._conn.running and self._initialized:
                return
            if self._conn:
                await self._conn.close()
            self._initialized = False
            argv = [sys.executable, "-u", str(FIXTURE)]
            self._conn = JsonlProcess(
                argv,
                env=self._spawn_env,
                on_notification=self._on_notification,
                on_server_request=self._on_server_request,
                on_close=self._on_conn_lost,
            )
            await self._conn.start()
            await self._conn.request(
                "initialize",
                {"clientInfo": {"name": "termx-test", "title": "T", "version": "0"}},
                timeout=20,
            )
            await self._conn.notify("initialized", {})
            self._initialized = True


def _codex(tmp_path: Path, events: list, approvals: list | None = None,
           env_extra: dict[str, str] | None = None):
    async def sink(binding_id, token, method, params, kind):
        if approvals is not None:
            approvals.append((binding_id, token, method, params, kind))
        return f"appr_{token}"

    return _CodexProc(
        executable_override=sys.executable,
        event_sink=lambda bid, ev: events.append((bid, ev)),
        approval_sink=sink,
        spawn_env=_fake_env(**(env_extra or {})),
    )


def test_codex_probe_and_session_roundtrip(tmp_path):
    events: list = []

    async def run():
        engine = _codex(tmp_path, events)
        desc = await engine.probe()
        assert desc.auth_state == "authenticated"
        assert desc.auth_detail == "apikey/pro"
        assert "gpt-fake-1" in engine.capabilities().models

        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="codex", cwd=str(tmp_path)))
        assert binding.native_session_id.startswith("thr_")

        await engine.send(binding, "hello world")
        deadline = time.monotonic() + 10
        while not any(
            ev.type == "engine.turn.completed" for _, ev in events
        ):
            assert time.monotonic() < deadline, "no turn completion"
            await asyncio.sleep(0.05)
        await engine.shutdown()

    asyncio.run(run())
    types = [ev.type for _, ev in events]
    assert "engine.turn.started" in types
    assert "engine.message.delta" in types
    assert "engine.turn.completed" in types
    completed = [ev for _, ev in events if ev.type == "engine.turn.completed"][0]
    assert completed.payload["status"] == "completed"
    assert completed.native["session_id"].startswith("thr_")


def test_codex_approval_roundtrip(tmp_path):
    events: list = []
    approvals: list = []

    async def run():
        engine = _codex(tmp_path, events, approvals,
                        {"FAKE_CODEX_APPROVE_WHEN": "please"})
        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="codex", cwd=str(tmp_path)))
        await engine.send(binding, "please run it")
        # wait for the approval to land
        deadline = time.monotonic() + 10
        while not approvals:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        _bid, token, method, params, kind = approvals[0]
        assert method == "item/commandExecution/requestApproval"
        assert kind == "tool"
        await engine.respond_approval(binding, token, "approve", remember=True)
        deadline = time.monotonic() + 10
        while not any(ev.type == "engine.turn.completed" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await engine.shutdown()

    asyncio.run(run())
    completed = [ev for _, ev in events if ev.type == "engine.turn.completed"][0]
    assert completed.payload["status"] == "completed"
    assert any(ev.type == "engine.approval.resolved" for _, ev in events)


def test_codex_interrupt(tmp_path):
    events: list = []

    async def run():
        engine = _codex(tmp_path, events, env_extra={"FAKE_CODEX_HANG": "1"})
        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="codex", cwd=str(tmp_path)))
        await engine.send(binding, "never finishes")
        deadline = time.monotonic() + 10
        while not any(ev.type == "engine.turn.started" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await engine.cancel(binding)
        deadline = time.monotonic() + 10
        while not any(ev.type == "engine.turn.completed" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await engine.shutdown()

    asyncio.run(run())
    completed = [ev for _, ev in events if ev.type == "engine.turn.completed"][0]
    assert completed.payload["status"] == "interrupted"


def test_codex_resume_and_steer(tmp_path):
    events: list = []

    async def run():
        engine = _codex(tmp_path, events)
        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="codex", cwd=str(tmp_path)))
        await engine.send(binding, "first")
        deadline = time.monotonic() + 10
        while not any(ev.type == "engine.turn.completed" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        # reattach to the persisted native thread
        detached = EngineSessionBindingPlaceholder(binding)
        reattached = await engine.attach(detached)
        assert reattached.native_session_id == binding.native_session_id
        sessions = await engine.list_sessions()
        assert any(s["id"] == binding.native_session_id for s in sessions)
        await engine.shutdown()

    asyncio.run(run())


def EngineSessionBindingPlaceholder(binding):
    from termx.engines.types import EngineSessionBinding
    return EngineSessionBinding.new(
        binding.engine, binding.native_session_id,
        cwd=binding.cwd, status="lost",
    )


def test_codex_process_loss_marks_sessions(tmp_path):
    events: list = []

    async def run():
        engine = _codex(tmp_path, events)
        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="codex", cwd=str(tmp_path)))
        assert engine._conn and engine._conn._proc
        engine._conn._proc.terminate()
        deadline = time.monotonic() + 10
        while not any(ev.type == "engine.session.lost" for _, ev in events):
            assert time.monotonic() < deadline, "session never marked lost"
            await asyncio.sleep(0.05)
        assert binding.status == "lost"
        await engine.shutdown()

    asyncio.run(run())


# ------------------------------------------------------------------ gateway


def test_gateway_create_complete_and_approve(tmp_path):
    store = _store(tmp_path)
    emitted: list = []

    def emit(task_id, etype, payload):
        event = store.append_event(task_id, etype, payload)
        emitted.append((task_id, etype))
        return event

    async def run():
        gateway = EngineGateway(store, emit)
        engine = _codex(tmp_path, [], None)
        # route engine events through the gateway
        engine._event_sink = gateway.on_engine_event
        engine._approval_sink = gateway.approval_sink
        gateway.register(engine)

        task = await gateway.create_task(
            prompt="do the thing",
            cwd=str(tmp_path),
            engine="codex",
        )
        assert task["engine"] == "codex"
        assert task["engine_session_id"]
        deadline = time.monotonic() + 10
        while True:
            t = store.get_task(task["id"])
            if t["status"] == "completed":
                break
            assert time.monotonic() < deadline, t["status"]
            await asyncio.sleep(0.05)
        t = store.get_task(task["id"])
        assert t["result"] and "fake says" in t["result"]
        session = store.engine_session_for_task(task["id"])
        assert session and session["native_session_id"].startswith("thr_")
        await gateway.shutdown()

    asyncio.run(run())
    types = [t for _, t in emitted]
    assert "engine.session.bound" in types
    assert "task.completed" in types


def test_gateway_marks_sessions_lost_on_recover(tmp_path):
    store = _store(tmp_path)
    store.create_task(prompt="x", cwd="/tmp", provider_id="engine:codex",
                      model="", limits={}, engine="codex", status="running")
    store.save_engine_session(
        "b1", task_id=None, engine="codex", native_session_id="thr_x",
        status="active")
    gateway = EngineGateway(store, lambda *a: {})
    assert gateway.recover() == 1
    assert store.get_engine_session("b1")["status"] == "lost"


# ------------------------------------------------------------------- store


def test_store_engine_columns_and_sessions(tmp_path):
    store = _store(tmp_path)
    task = store.create_task(
        prompt="x", cwd="/tmp", provider_id="engine:codex", model="",
        limits={}, engine="codex")
    assert task["engine"] == "codex"
    store.update_task(task["id"], engine_session_id="b1",
                      engine_native_id="thr_1")
    store.save_engine_session(
        "b1", task_id=task["id"], engine="codex",
        native_session_id="thr_1", conversation_id="conv1", cwd="/tmp")
    found = store.engine_session_for_conversation("conv1")
    assert found and found["binding_id"] == "b1"
    store.update_engine_session("b1", status="idle")
    assert store.engine_session_for_task(task["id"])["status"] == "idle"


def test_store_migration_preserves_existing_db(tmp_path):
    """Open a DB written by the pre-engine schema; new columns appear."""
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, prompt TEXT NOT NULL, cwd TEXT NOT NULL,
            provider_id TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
            limits TEXT NOT NULL, parent_id TEXT, mode TEXT NOT NULL DEFAULT 'agent',
            plan TEXT, result TEXT, error TEXT, previous_response_id TEXT,
            runtime TEXT, metrics TEXT, next_sequence INTEGER NOT NULL DEFAULT 1,
            custom_agent_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        INSERT INTO tasks (id, prompt, cwd, provider_id, model, status, limits,
            created_at, updated_at)
        VALUES ('t1', 'p', '/tmp', 'prov', 'm', 'completed', '{}', 1.0, 1.0);
        """
    )
    conn.commit()
    conn.close()
    store = AgentStore(db, artifact_dir=tmp_path / "art")
    task = store.get_task("t1")
    assert task["engine"] == "internal"  # migrated default
    store.save_engine_session("b9", task_id="t1", engine="codex",
                              native_session_id="thr_9")
    assert store.engine_session_for_task("t1")["binding_id"] == "b9"


# --------------------------------------------------------------------- api


def test_engines_api_lists_internal_and_codex(tmp_path):
    state = AppState(
        passcode=None,
        agent_store=_store(tmp_path),
        credentials=CredentialStore(str(tmp_path / "creds")),
    )
    client = TestClient(create_app(state, web_dir=None))
    resp = client.get("/api/engines")
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json()["engines"]]
    assert "internal" in ids and "codex" in ids
    codex = next(e for e in ids if e == "codex" and ids)
    diag = client.get("/api/engines/diagnostics")
    assert diag.status_code == 200
    assert "resolutions" in diag.json()


def test_engine_task_requires_valid_engine(tmp_path):
    state = AppState(
        passcode=None,
        agent_store=_store(tmp_path),
        credentials=CredentialStore(str(tmp_path / "creds")),
    )
    client = TestClient(create_app(state, web_dir=None))
    resp = client.post("/api/agent/tasks", json={
        "prompt": "hi", "cwd": str(tmp_path), "engine": "bogus"})
    assert resp.status_code == 422  # pattern rejects unknown engines
    state.engines._adapters.pop("devin")  # unregister → valid name, no adapter
    resp = client.post("/api/agent/tasks", json={
        "prompt": "hi", "cwd": str(tmp_path), "engine": "devin"})
    assert resp.status_code == 404  # registered engines only
