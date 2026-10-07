"""ACP engine tests — shared client and registry-managed engine descriptors."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from termx.engines.acp import AcpEngine
from termx.engines.acp_registry import ENGINE_IDS, RegistryAcpEngine
from termx.engines.types import EffectiveRunConfiguration
from termx.engines.gateway import EngineGateway
from termx.agent.store import AgentStore

FIXTURE = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


class FakeAcpEngine(AcpEngine):
    id = "fake-acp"
    label = "Fake ACP"
    executable_name = "python3"
    acp_args = ["-u", str(FIXTURE)]
    version_args = ["--version"]

    def __init__(self, *a, env_extra=None, **kw):
        env = dict(os.environ)
        env.update(env_extra or {})
        super().__init__(spawn_env=env, *a, **kw)


def _engine(events, approvals, env_extra=None):
    async def sink(binding_id, token, method, params, kind):
        approvals.append((token, method, params, kind))
        return f"appr_{token}"
    e = FakeAcpEngine(
        event_sink=lambda bid, ev: events.append((bid, ev)),
        approval_sink=sink,
        env_extra=env_extra,
    )
    e._executable = sys.executable
    return e


def test_acp_probe_and_turn(tmp_path):
    events: list = []
    approvals: list = []

    async def run():
        engine = _engine(events, approvals)
        desc = await engine.probe()
        assert desc.installed
        assert desc.protocol["agent_info"]["name"] == "fake-acp"
        assert desc.protocol["auth_methods"] == []

        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="fake-acp", cwd=str(tmp_path)))
        assert binding.native_session_id.startswith("acp_")
        await engine.send(binding, "hello there")
        deadline = time.monotonic() + 15
        while not any(ev.type == "engine.turn.completed" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await engine.shutdown()

    asyncio.run(run())
    types = [ev.type for _, ev in events]
    assert "engine.turn.started" in types
    assert "engine.message.delta" in types
    done = [ev for _, ev in events if ev.type == "engine.turn.completed"][0]
    assert done.payload["status"] == "completed"
    assert "usage" in done.payload


def test_acp_permission_flow(tmp_path):
    events: list = []
    approvals: list = []

    async def run():
        engine = _engine(events, approvals, {"FAKE_ACP_APPROVE": "please"})
        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="fake-acp", cwd=str(tmp_path)))
        await engine.send(binding, "please do it")
        deadline = time.monotonic() + 15
        while not approvals:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        token, method, params, kind = approvals[0]
        assert method == "session/request_permission"
        assert params["options"][0]["kind"] == "allow_once"
        await engine.respond_approval(binding, token, "approve")
        deadline = time.monotonic() + 15
        while not any(ev.type == "engine.turn.completed" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await engine.shutdown()

    asyncio.run(run())
    done = [ev for _, ev in events if ev.type == "engine.turn.completed"][0]
    assert done.payload["status"] == "completed"


def test_acp_cancel(tmp_path):
    events: list = []
    approvals: list = []

    async def run():
        engine = _engine(events, approvals, {"FAKE_ACP_HANG": "1"})
        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="fake-acp", cwd=str(tmp_path)))
        await engine.send(binding, "hang forever")
        await asyncio.sleep(0.3)
        await engine.cancel(binding)
        deadline = time.monotonic() + 15
        while not any(ev.type == "engine.turn.completed" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await engine.shutdown()

    asyncio.run(run())
    done = [ev for _, ev in events if ev.type == "engine.turn.completed"][0]
    assert done.payload["status"] == "interrupted"


def test_acp_load_session(tmp_path):
    events: list = []
    state_file = tmp_path / "acp-state.json"

    async def run():
        engine = _engine(events, [],
                         {"FAKE_ACP_STATE_FILE": str(state_file)})
        binding = await engine.create_session(
            EffectiveRunConfiguration(engine="fake-acp", cwd=str(tmp_path)))
        sid = binding.native_session_id
        await engine.send(binding, "first")
        deadline = time.monotonic() + 15
        while not any(ev.type == "engine.turn.completed" for _, ev in events):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await engine.close(binding)
        # reattach
        lost = type(binding).new("fake-acp", sid, cwd=str(tmp_path), status="lost")
        re = await engine.attach(lost)
        assert re.native_session_id == sid
        await engine.shutdown()

    asyncio.run(run())


def test_acp_auth_methods_reported(tmp_path):
    async def run():
        engine = _engine([], [], {"FAKE_ACP_AUTH_REQUIRED": "1"})
        desc = await engine.probe()
        assert desc.protocol["auth_methods"][0]["id"] == "oauth"
    asyncio.run(run())


def test_registry_agents_share_the_generic_acp_adapter():
    engine = RegistryAcpEngine("devin", "Devin", sys.executable, ["acp"])
    assert engine.id == "devin" and engine._launch_config["args"] == ["acp"]
    assert ENGINE_IDS["grok-build"] == "grok"
    desc = engine.descriptor()
    assert desc.transport == "acp-stdio"


async def _wait_turn(engine, binding):
    await asyncio.wait_for(engine._sessions[binding.binding_id]["turn_task"], 5)


def test_acp_selects_and_changes_model_mode_and_boolean_options(tmp_path):
    async def run():
        engine = _engine([], [])
        try:
            catalogue = await engine.discover_configuration(str(tmp_path))
            assert catalogue["models"] == ["slow", "fast"]
            binding = await engine.create_session(EffectiveRunConfiguration(
                cwd=str(tmp_path), model="fast", mode="code", config_options={"safe": False}))
            cfg = engine.session_configuration(binding)
            values = {o["id"]: o["currentValue"] for o in cfg["config_options"]}
            assert values["model"] == "fast" and values["mode"] == "code"
            assert values["safe"] is False
            await engine.send(binding, "first")
            await _wait_turn(engine, binding)
            await engine.configure_session(binding, EffectiveRunConfiguration(
                model="slow", mode="ask", config_options={"reasoning": "high"}))
            await engine.send(binding, "second")
            await _wait_turn(engine, binding)
            values = {o["id"]: o["currentValue"] for o in engine.session_configuration(binding)["config_options"]}
            assert values["model"] == "slow" and values["reasoning"] == "high"
            assert len(engine._sessions) == 1
        finally:
            await engine.shutdown()
    asyncio.run(run())


def test_acp_invalid_config_cleans_up_and_checks_dependent_values(tmp_path):
    async def run():
        engine = _engine([], [])
        with pytest.raises(ValueError, match="invalid value"):
            await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), model="imaginary"))
        assert not engine._sessions
        with pytest.raises(ValueError, match="reasoning"):
            await engine.create_session(EffectiveRunConfiguration(
                cwd=str(tmp_path), model="fast", config_options={"reasoning": "high"}))
        assert not engine._sessions
        with pytest.raises(ValueError, match="unknown ACP config option"):
            await engine.create_session(EffectiveRunConfiguration(
                cwd=str(tmp_path), config_options={"unknown": True}))
        assert not engine._sessions
    asyncio.run(run())


def test_acp_legacy_mode_fallback_and_missing_model_is_explicit(tmp_path):
    async def run():
        engine = _engine([], [], {"FAKE_ACP_LEGACY": "1"})
        binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), mode="code"))
        assert engine.session_configuration(binding)["modes"]["currentModeId"] == "code"
        with pytest.raises(ValueError, match="model selector"):
            await engine.configure_session(binding, EffectiveRunConfiguration(model="fast"))
        await engine.shutdown()
    asyncio.run(run())


def test_acp_rejects_protocol_version_and_concurrent_turn(tmp_path):
    async def run():
        bad = _engine([], [], {"FAKE_ACP_VERSION": "2"})
        with pytest.raises(ValueError, match="unsupported ACP protocol"):
            await bad.create_session(EffectiveRunConfiguration(cwd=str(tmp_path)))
        engine = _engine([], [], {"FAKE_ACP_HANG": "1"})
        try:
            binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path)))
            await engine.send(binding, "first")
            with pytest.raises(ValueError, match="active turn"):
                await engine.send(binding, "second")
            with pytest.raises(ValueError, match="active turn"):
                await engine.configure_session(binding, EffectiveRunConfiguration(mode="code"))
            await engine.cancel(binding)
            assert engine._sessions[binding.binding_id]["turn_task"].done()
        finally:
            await engine.shutdown()
    asyncio.run(run())


def test_acp_live_callbacks_use_same_client_and_correct_terminal_schema(tmp_path):
    log = tmp_path / "wire.jsonl"
    async def run():
        engine = _engine([], [], {"FAKE_ACP_CALLBACKS": "1", "FAKE_ACP_LOG": str(log)})
        try:
            binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path)))
            client = engine._sessions[binding.binding_id]["client"]
            assert client._session_id == binding.native_session_id
            await engine.send(binding, "callbacks")
            await _wait_turn(engine, binding)
            assert client._terminals == {}
        finally:
            await engine.shutdown()
    asyncio.run(run())
    responses = [m["result"] for m in map(json.loads, log.read_text().splitlines()) if "result" in m]
    assert any(r.get("exitCode") == 0 for r in responses)
    assert any(r.get("output", "").splitlines() == ["callback output"] for r in responses)
    initialize = next(m for m in map(json.loads, log.read_text().splitlines()) if m.get("method") == "initialize")
    assert initialize["params"]["clientCapabilities"]["session"]["configOptions"]["boolean"] == {}


def test_acp_close_cancels_pending_permissions(tmp_path):
    async def run():
        approvals = []
        engine = _engine([], approvals, {"FAKE_ACP_APPROVE": "please"})
        binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path)))
        await engine.send(binding, "please")
        async with asyncio.timeout(5):
            while not approvals:
                await asyncio.sleep(.01)
        await engine.close(binding)
        await asyncio.sleep(.05)
        assert not engine._pending_decisions
        assert not engine._sessions
    asyncio.run(run())


def test_acp_cancel_during_permission_does_not_report_success(tmp_path):
    async def run():
        events, approvals = [], []
        engine = _engine(events, approvals, {"FAKE_ACP_APPROVE": "please"})
        try:
            binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path)))
            await engine.send(binding, "please")
            async with asyncio.timeout(5):
                while not approvals:
                    await asyncio.sleep(.01)
            await engine.cancel(binding)
            done = [e for _, e in events if e.type == "engine.turn.completed"]
            assert len(done) == 1 and done[0].payload["status"] == "interrupted"
            assert not engine._pending_decisions
        finally:
            await engine.shutdown()
    asyncio.run(run())


def test_gateway_native_session_model_change_and_restart_preserve_identity(tmp_path):
    state_file = tmp_path / "native.json"
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        engine = _engine([], [], {"FAKE_ACP_STATE_FILE": str(state_file)})
        engine._event_sink = gateway.on_engine_event
        gateway.register(engine)
        conversation = store.create_conversation(cwd=str(tmp_path))
        kw = {"cwd": str(tmp_path), "engine": "fake-acp", "conversation_id": conversation["id"]}
        task = await gateway.create_task(prompt="first", model="fast", mode="code", **kw)
        binding = gateway._binding_for_task(task["id"])
        await _wait_turn(engine, binding)
        assert store.get_task(task["id"])["model"] == "fast"
        task2 = await gateway.create_task(prompt="second", model="slow", config_options={"reasoning": "high"}, **kw)
        await _wait_turn(engine, binding)
        assert task2["engine_session_id"] == task["engine_session_id"]
        assert task2["model"] == "slow"
        assert "first" in store.get_task(task["id"])["result"]
        assert "second" in store.get_task(task2["id"])["result"]
        await gateway.shutdown()
        gateway = EngineGateway(store, store.append_event)
        gateway.recover()
        engine = _engine([], [], {"FAKE_ACP_STATE_FILE": str(state_file)})
        engine._event_sink = gateway.on_engine_event
        gateway.register(engine)
        try:
            task3 = await gateway.create_task(prompt="third", **kw)
            loaded = gateway._binding_for_task(task3["id"])
            await _wait_turn(engine, loaded)
            assert loaded.binding_id == binding.binding_id
            assert loaded.native_session_id == binding.native_session_id
            assert task3["model"] == "slow"
            assert len(store.list_engine_sessions()) == 1
            assert store.get_task(task3["id"])["status"] == "completed"
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_gateway_rejects_duplicate_turn_before_rebinding_task(tmp_path):
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        engine = _engine([], [], {"FAKE_ACP_HANG": "1"})
        engine._event_sink = gateway.on_engine_event
        gateway.register(engine)
        conv = store.create_conversation(cwd=str(tmp_path))
        kw = {"cwd": str(tmp_path), "engine": "fake-acp", "conversation_id": conv["id"]}
        try:
            task = await gateway.create_task(prompt="first", **kw)
            with pytest.raises(ValueError, match="active engine turn"):
                await gateway.create_task(prompt="second", **kw)
            assert len(store.list_tasks()) == 1
            await gateway.cancel(task["id"])
            assert store.get_task(task["id"])["status"] == "cancelled"
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_graphql_acp_config_selection_and_metadata(tmp_path):
    from fastapi.testclient import TestClient
    from _gql import data, err_status
    from termx.app import AppState, create_app
    state = AppState(passcode="test-only")
    engine = _engine([], [])
    engine._event_sink = state.engines.on_engine_event
    state.engines.register(engine)
    headers = {"X-Termx-Passcode": "test-only"}
    with TestClient(create_app(state)) as client:
        data(client, 'mutation($cwd: String!) { refresh_engine_catalogue(engine_id: "fake-acp", cwd: $cwd) }',
             variables={"cwd": str(tmp_path)}, headers=headers)
        catalog = data(client, 'query($cwd: String!) { engine_configuration(engine_id: "fake-acp", cwd: $cwd) }',
                       variables={"cwd": str(tmp_path)}, headers=headers)["engine_configuration"]
        assert catalog["models"] == ["slow", "fast"]
        task = data(client, 'mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id model } }',
                    variables={"input": {"prompt": "Hello", "cwd": str(tmp_path), "engine": "fake-acp",
                        "model": "fast", "engine_mode": "code", "config_options": {"safe": False}}},
                    headers=headers)["create_agent_task"]
        assert task["model"] == "fast"
        cfg = data(client, 'query($id: String!) { engine_task_configuration(task_id: $id) }',
                   variables={"id": task["id"]}, headers=headers)["engine_task_configuration"]
        assert {o["id"]: o["currentValue"] for o in cfg["config_options"]}["mode"] == "code"
        assert err_status(client, '{ engine_configuration(engine_id: "fake-acp") }') == 401


@pytest.mark.parametrize("env_extra", [
    {"FAKE_ACP_NO_MODE": "1"},
    {"FAKE_ACP_NO_MODE": "1", "FAKE_ACP_LEGACY": "1"},
    {"FAKE_ACP_DEFAULT_MODE": "code"},
])
def test_graphql_ask_does_not_invent_or_override_native_mode(tmp_path, env_extra):
    from fastapi.testclient import TestClient
    from _gql import data
    from termx.app import AppState, create_app

    log = tmp_path / "rpc.jsonl"
    state = AppState(passcode="test-only")
    engine = _engine([], [], {**env_extra, "FAKE_ACP_LOG": str(log)})
    engine.id = "grok"
    engine._event_sink = state.engines.on_engine_event
    state.engines.register(engine)
    headers = {"X-Termx-Passcode": "test-only"}
    with TestClient(create_app(state)) as client:
        task = data(client, 'mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id engine } }',
            variables={"input": {"prompt": "Investigate", "cwd": str(tmp_path),
                "engine": "grok", "mode": "ask"}}, headers=headers)["create_agent_task"]
        assert task["engine"] == "grok"
        cfg = data(client, 'query($id: String!) { engine_task_configuration(task_id: $id) }',
            variables={"id": task["id"]}, headers=headers)["engine_task_configuration"]
        if env_extra.get("FAKE_ACP_NO_MODE"):
            assert not cfg["modes"]
            assert not any(o.get("category") == "mode" for o in cfg["config_options"])
        else:
            assert cfg["modes"]["currentModeId"] == "code"
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(r.get("method") == "session/set_mode" or
        (r.get("method") == "session/set_config_option" and r["params"]["configId"] == "mode")
        for r in requests)


def test_graphql_ask_preserves_saved_and_explicit_native_modes(tmp_path):
    from fastapi.testclient import TestClient
    from _gql import data
    from termx.app import AppState, create_app

    state = AppState(passcode="test-only")
    state.store.update_agent({"engines": {"grok": {"mode": "code"}}})
    engine = _engine([], [])
    engine.id = "grok"
    engine._event_sink = state.engines.on_engine_event
    state.engines.register(engine)
    headers = {"X-Termx-Passcode": "test-only"}
    with TestClient(create_app(state)) as client:
        for selection, expected in (({}, "code"), ({"engine_mode": "ask"}, "ask")):
            task = data(client, 'mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id mode } }',
                variables={"input": {"prompt": "Investigate", "cwd": str(tmp_path),
                    "engine": "grok", "mode": "ask", **selection}}, headers=headers)["create_agent_task"]
            assert task["mode"] == expected


def test_graphql_explicit_native_mode_requires_advertised_selector(tmp_path):
    from fastapi.testclient import TestClient
    from _gql import gql
    from termx.app import AppState, create_app

    state = AppState(passcode="test-only")
    engine = _engine([], [], {"FAKE_ACP_NO_MODE": "1"})
    engine.id = "grok"
    engine._event_sink = state.engines.on_engine_event
    state.engines.register(engine)
    with TestClient(create_app(state)) as client:
        response = gql(client, 'mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id } }',
            variables={"input": {"prompt": "Investigate", "cwd": str(tmp_path),
                "engine": "grok", "mode": "ask", "engine_mode": "ask"}},
            headers={"X-Termx-Passcode": "test-only"})
        errors = response.json()["errors"]
        assert "grok did not advertise a mode selector" in errors[0]["message"]
        assert errors[0]["extensions"]["http_status"] == 400


def test_custom_agent_native_selection_round_trips_and_is_used(tmp_path):
    from fastapi.testclient import TestClient
    from _gql import data
    from termx.app import AppState, create_app
    from termx.agents.files import parse_agent_file, serialize_agent
    state = AppState(passcode="test-only")
    engine = _engine([], [])
    engine.id = "devin"
    engine._event_sink = state.engines.on_engine_event
    state.engines.register(engine)
    headers = {"X-Termx-Passcode": "test-only"}
    with TestClient(create_app(state)) as client:
        profile = data(client, 'mutation($input: CustomAgentInput!) { create_custom_agent(input: $input) { id engine_mode config_options } }',
            variables={"input": {"name": "ACP Builder", "engine": "devin", "model": "fast",
                "engine_mode": "code", "config_options": {"safe": False}}}, headers=headers)["create_custom_agent"]
        assert profile["engine_mode"] == "code"
        assert profile["config_options"] == {"safe": False}
        saved = state.agent_registry.load("acp-builder")
        assert parse_agent_file(serialize_agent(saved)).config_options == {"safe": False}
        task = data(client, 'mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id engine model mode } }',
            variables={"input": {"prompt": "Build", "cwd": str(tmp_path), "custom_agent_id": profile["id"]}},
            headers=headers)["create_agent_task"]
        assert task["engine"] == "devin"
        assert task["model"] == "fast"
        assert task["mode"] == "code"


def test_acp_process_death_fails_turn_and_resumes_native_session(tmp_path):
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        engine = _engine([], [], {"FAKE_ACP_HANG": "1", "FAKE_ACP_STATE_FILE": str(tmp_path / "native.json")})
        engine._event_sink = gateway.on_engine_event
        gateway.register(engine)
        conv = store.create_conversation(cwd=str(tmp_path))
        kw = {"cwd": str(tmp_path), "engine": "fake-acp", "conversation_id": conv["id"]}
        try:
            task = await gateway.create_task(prompt="first", **kw)
            binding = gateway._binding_for_task(task["id"])
            state = engine._sessions[binding.binding_id]
            await state["prompt_started"].wait()
            state["proc"].kill()
            await asyncio.wait_for(state["process_watch"], 5)
            assert binding.status == "lost"
            assert store.get_task(task["id"])["status"] == "failed"
            assert store.get_engine_session(binding.binding_id)["status"] == "lost"
            engine._spawn_env["FAKE_ACP_HANG"] = "0"
            next_task = await gateway.create_task(prompt="continue", **kw)
            resumed = gateway._binding_for_task(next_task["id"])
            await _wait_turn(engine, resumed)
            assert resumed.native_session_id == binding.native_session_id
            assert store.get_task(next_task["id"])["status"] == "completed"
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_acp_cancel_timeout_closes_unresponsive_agent(tmp_path):
    async def run():
        engine = _engine([], [], {"FAKE_ACP_HANG": "1", "FAKE_ACP_IGNORE_CANCEL": "1"})
        engine.configure_launch({"cancel_timeout_s": .05})
        binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path)))
        proc = engine._sessions[binding.binding_id]["proc"]
        await engine.send(binding, "hang")
        await asyncio.wait_for(engine.cancel(binding), 5)
        assert binding.status == "closed"
        assert proc.returncode is not None
        assert not engine._sessions
    asyncio.run(run())


def test_acp_relative_file_boundary_and_terminal_output_tail(tmp_path):
    (tmp_path / "sample.txt").write_bytes(b"one\ntwo\n")
    async def run():
        engine = _engine([], [])
        binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path)))
        client = engine._sessions[binding.binding_id]["client"]
        try:
            assert (await client.read_text_file(binding.native_session_id, "sample.txt", line=2, limit=1)).content == "two\n"
            with pytest.raises(PermissionError):
                await client.read_text_file(binding.native_session_id, "../outside.txt")
            terminal = await client.create_terminal(binding.native_session_id, sys.executable,
                args=["-c", "print('abcdefghij', end='')"], output_byte_limit=4)
            exited = await client.wait_for_terminal_exit(binding.native_session_id, terminal.terminal_id)
            assert exited.exit_code == 0
            output = await client.terminal_output(binding.native_session_id, terminal.terminal_id)
            assert output.output == "ghij" and output.truncated
            with pytest.raises(PermissionError):
                await client.terminal_output("another-session", terminal.terminal_id)
        finally:
            await engine.shutdown()
    asyncio.run(run())


def test_acp_mcp_server_schema_and_resume_configuration(tmp_path):
    engine = _engine([], [])
    engine._agent_capabilities = {"mcpCapabilities": {"http": True, "sse": True}}
    cfg = EffectiveRunConfiguration(mcp_bindings=[
        {"connection_id": "http", "transport": "http", "url": "http://localhost/mcp"},
        {"connection_id": "sse", "transport": "sse", "url": "http://localhost/sse"}])
    servers = engine._mcp_servers(cfg)
    assert [(s.type, s.headers) for s in servers] == [("http", []), ("sse", [])]
    engine._agent_capabilities = {}
    with pytest.raises(ValueError, match="HTTP MCP support"):
        engine._mcp_servers(cfg)
    log = tmp_path / "wire.jsonl"
    async def run():
        engine = _engine([], [], {"FAKE_ACP_LOG": str(log), "FAKE_ACP_STATE_FILE": str(tmp_path / "native.json")})
        cfg = EffectiveRunConfiguration(cwd=str(tmp_path), mcp_bindings=[{
            "connection_id": "stdio", "command": ["test-server", "--stdio"], "env": {"TEST_KEY": "fixture"}}])
        binding = await engine.create_session(cfg)
        await engine.close(binding)
        await engine.attach(binding, cfg=cfg)
        await engine.shutdown()
    asyncio.run(run())
    requests = [m for m in map(json.loads, log.read_text().splitlines())
                if m.get("method") in ("session/new", "session/load")]
    assert requests[0]["params"]["mcpServers"] == requests[1]["params"]["mcpServers"]
    assert requests[0]["params"]["mcpServers"][0]["command"] == "test-server"


def test_acp_connection_scoped_native_ids_do_not_cross_sessions(tmp_path):
    async def run():
        events = []
        engine = _engine(events, [], {"FAKE_ACP_HANG": "1"})
        try:
            first = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), mode="code"))
            engine._spawn_env["FAKE_ACP_HANG"] = "0"
            second = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), mode="ask"))
            assert first.native_session_id == second.native_session_id
            assert first.binding_id != second.binding_id
            await engine.send(first, "hang")
            await engine.send(second, "complete")
            await _wait_turn(engine, second)
            assert not engine._sessions[first.binding_id]["turn_task"].done()
            deltas = [(bid, ev.payload["delta"]) for bid, ev in events if ev.type == "engine.message.delta"]
            assert deltas and all(bid == second.binding_id for bid, _ in deltas)
            assert len(engine._sessions) == 2
            await engine.cancel(first)
        finally:
            await engine.shutdown()
        assert not engine._sessions
    asyncio.run(run())


def test_gateway_restart_does_not_leave_native_turn_running(tmp_path):
    store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
    try:
        task = store.create_task(prompt="work", cwd=str(tmp_path), provider_id="engine:devin",
                                 model="fast", limits={}, engine="devin", status="running")
        store.save_engine_session("binding", task_id=task["id"], engine="devin",
            native_session_id="native", cwd=str(tmp_path), conversation_id="conversation", status="active")
        gateway = EngineGateway(store, store.append_event)
        assert gateway.recover() == 1
        assert store.get_task(task["id"])["status"] == "failed"
        assert store.engine_session_for_conversation("conversation")["status"] == "lost"
    finally:
        store.close()


def test_acp_discovery_reflects_host_defaults_without_a_prompt_or_live_binding(tmp_path):
    async def run():
        events = []
        engine = _engine(events, [])
        engine.configure_launch({"model": "fast", "mode": "code", "config_options": {"safe": False}})
        try:
            configuration = await engine.discover_configuration(str(tmp_path))
            values = {option["id"]: option["currentValue"] for option in configuration["config_options"]}
            assert values["model"] == "fast"
            assert values["mode"] == "code"
            assert values["safe"] is False
            assert not engine._sessions and not engine._bindings
            assert not any(event.type == "engine.message.delta" for _, event in events)
        finally:
            await engine.shutdown()
    asyncio.run(run())
