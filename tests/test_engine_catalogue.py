"""Runner registration, explicit discovery and ACP option interoperability."""

import asyncio
import json
import sys

import pytest

from termx.agent.store import AgentStore
from termx.config import ConfigStore, _agent_prefs
from termx.engines.acp_registry import AcpRegistry, ENGINE_IDS, RegistryAcpEngine
from termx.engines.custom_acp import CustomAcpEngine
from termx.engines.gateway import EngineGateway
from termx.engines.types import EffectiveRunConfiguration
from test_engines_acp import FIXTURE, _engine


def test_catalogue_reads_never_spawn_and_refresh_is_single_flight(tmp_path):
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        log = tmp_path / "rpc.jsonl"
        engine = _engine([], [], {"FAKE_ACP_LOG": str(log)})
        gateway.register(engine)
        try:
            assert await gateway.models(engine.id) == []
            assert (await gateway.configuration(engine.id))["stale"]
            await gateway.describe_all()
            await gateway.probe(engine.id)
            assert not log.exists()
            first, second = await asyncio.gather(
                gateway.catalogue.refresh(engine.id, str(tmp_path)),
                gateway.catalogue.refresh(engine.id, str(tmp_path)))
            assert first == second
            requests = [json.loads(line) for line in log.read_text().splitlines()]
            assert sum(r.get("method") == "initialize" for r in requests) == 2  # probe + discovery
            assert sum(r.get("method") == "session/new" for r in requests) == 1
            assert not any(r.get("method") == "session/prompt" for r in requests)
            before = log.read_text()
            for _ in range(4):
                assert await gateway.models(engine.id) == ["slow", "fast"]
                configuration = await gateway.configuration(engine.id, "/ignored/read-only/cache-key")
                await gateway.describe_all()
                await gateway.probe(engine.id)
            assert log.read_text() == before
            variants = configuration["model_configurations"]
            slow = next(o for o in variants["slow"]["config_options"] if o["category"] == "thought_level")
            fast = next(o for o in variants["fast"]["config_options"] if o["category"] == "thought_level")
            assert [o["value"] for o in slow["options"]] == ["low", "high"]
            assert [o["value"] for o in fast["options"]] == ["low"]
            assert not engine._sessions and not engine._bindings
            # Return values are copies; a client cannot mutate the cache.
            configuration["models"].clear()
            assert await gateway.models(engine.id) == ["slow", "fast"]
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_empty_and_failed_catalogues_are_cached_until_manual_refresh(tmp_path):
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        engine = _engine([], [], {"FAKE_ACP_LEGACY": "1", "FAKE_ACP_NO_MODE": "1"})
        gateway.register(engine)
        try:
            await gateway.catalogue.refresh(engine.id, str(tmp_path))
            assert await gateway.models(engine.id) == []
            previous = (await gateway.configuration(engine.id))["refreshed_at"]
            async def fail():
                raise RuntimeError("connection failed")
            engine.probe = fail
            for _ in range(3):
                assert (await gateway.configuration(engine.id))["refreshed_at"] == previous
                assert await gateway.models(engine.id) == []
            refreshed = await gateway.catalogue.refresh(engine.id)
            assert refreshed[engine.id]["refresh_error"] == "connection failed"
            assert (await gateway.configuration(engine.id))["refresh_error"] == "connection failed"
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_failed_refresh_keeps_previous_models_and_marks_stale(tmp_path):
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        engine = _engine([], [])
        gateway.register(engine)
        try:
            await gateway.catalogue.refresh(engine.id, str(tmp_path))
            async def fail():
                raise RuntimeError("offline")
            engine.probe = fail
            await gateway.catalogue.refresh(engine.id)
            cfg = await gateway.configuration(engine.id)
            assert cfg["models"] == ["slow", "fast"] and cfg["stale"]
            assert cfg["refresh_error"] == "offline"
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_startup_refresh_runs_once_and_is_closed_at_shutdown(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from _gql import data
    from termx.app import AppState, create_app
    monkeypatch.setenv("TERMX_ENGINE_STARTUP_REFRESH", "1")
    state = AppState(passcode="test-only")
    # Isolate startup from real installed/authenticated vendor binaries.
    state.engines._adapters.clear()
    state.engines.acp_registry._fetch_index = lambda: {"version": "1.0.0", "agents": [{
        "id": "test-registry-agent", "name": "Test ACP", "version": "1.0.0",
        "distribution": {"npx": {"package": "test-acp@1.0.0"}},
    }]}
    log = tmp_path / "rpc.jsonl"
    engine = _engine([], [], {"FAKE_ACP_LOG": str(log)})
    state.engines.register(engine)
    headers = {"X-Termx-Passcode": "test-only"}
    with TestClient(create_app(state)) as client:
        for _ in range(3):
            engines = data(client, "{ engines { id } }", headers=headers)["engines"]
            assert {e["id"] for e in engines} == {"internal", "fake-acp"}
            assert data(client, '{ engine_models(engine_id: "fake-acp") }', headers=headers)["engine_models"] == ["slow", "fast"]
        requests = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(r.get("method") == "session/new" for r in requests) == 1
    assert not engine._sessions and not state.engines.catalogue.jobs


def test_custom_runner_graphql_registration_refresh_task_and_removal(tmp_path):
    from fastapi.testclient import TestClient
    from _gql import data, err_status
    from termx.app import AppState, create_app
    state = AppState(passcode="test-only")
    headers = {"X-Termx-Passcode": "test-only"}
    runner = {"label": "Example ACP", "executable": sys.executable, "args": ["-u", str(FIXTURE)], "env_names": []}
    with TestClient(create_app(state)) as client:
        mutation = 'mutation($input: JSON!) { update_agent_configuration(input: $input) }'
        assert err_status(client, mutation, {"input": {"acp_runners": {"example-acp": runner}}}) == 401
        config = data(client, mutation, variables={"input": {"acp_runners": {"example-acp": runner}}}, headers=headers)["update_agent_configuration"]
        assert config["acp_runners"]["example-acp"]["args"] == ["-u", str(FIXTURE)]
        adapter = state.engines.adapter("example-acp")
        assert isinstance(adapter, CustomAcpEngine)
        assert adapter.version_args == []  # no nonstandard version command required
        refreshed = data(client, 'mutation($cwd: String!) { refresh_engine_catalogue(engine_id: "example-acp", cwd: $cwd) }',
            variables={"cwd": str(tmp_path)}, headers=headers)["refresh_engine_catalogue"]
        assert refreshed["example-acp"]["descriptor"]["version"] == "0.1"
        assert refreshed["example-acp"]["configuration"]["models"] == ["slow", "fast"]
        task = data(client, 'mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id engine model } }',
            variables={"input": {"cwd": str(tmp_path), "prompt": "Hello", "engine": "example-acp", "model": "fast"}}, headers=headers)["create_agent_task"]
        assert task["engine"] == "example-acp" and task["model"] == "fast"
        # Ensure the short fake turn has completed before removing its adapter.
        for _ in range(100):
            import time
            if state.agent_store.get_task(task["id"])["status"] == "completed":
                break
            time.sleep(.01)
        data(client, mutation, variables={"input": {"acp_runners": {}}}, headers=headers)
        assert "example-acp" not in state.engines.engines()
        assert not adapter._sessions


def test_runner_configuration_round_trips_and_custom_agent_id_is_portable(tmp_path):
    from termx.agents.files import parse_agent_file, serialize_agent
    store = ConfigStore(tmp_path / "config.json")
    runner = {"label": "Custom", "executable": "my-agent", "args": ["--acp"], "enabled": False}
    store.update_agent({"acp_runners": {"my-runner": runner}, "engines": {"my-runner": {"model": "native-model"}}})
    assert ConfigStore(tmp_path / "config.json").get().agent.acp_runners["my-runner"] == runner
    parsed = parse_agent_file('---\nname: Test\nx-termx:\n  engine: my-runner\n---\nInstructions')
    assert parsed.engine == "my-runner" and not parsed.errors
    assert parse_agent_file(serialize_agent(parsed)).engine == "my-runner"


@pytest.mark.parametrize("runners", [
    {"grok": {"executable": "x"}}, {"bad id": {"executable": "x"}},
    {"custom": {}}, {"custom": {"executable": "x", "transport": "http"}},
    {"custom": {"executable": "x", "args": "acp"}},
    {"custom": {"executable": "x", "env_names": ["API_KEY=value"]}},
    {"custom": {"executable": "x", "enabled": "yes"}},
])
def test_invalid_runner_definitions_are_rejected(runners):
    with pytest.raises(ValueError):
        _agent_prefs({"acp_runners": runners})


def test_existing_acp_engine_ids_map_to_official_registry_entries():
    assert ENGINE_IDS == {"grok-build": "grok", "antigravity-acp": "antigravity"}


def test_registry_index_is_cached_and_detected_agents_use_the_generic_acp_adapter(tmp_path, monkeypatch):
    import termx.engines.acp_registry as registry_module
    store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
    gateway = EngineGateway(store, store.append_event)
    manager = AcpRegistry(gateway, root=tmp_path / "acp")
    manager._load_index({"version": "1.0.0", "agents": [{
        "id": "known-acp", "name": "Known ACP", "version": "1.2.3",
        "distribution": {"npx": {"package": "@example/known-acp@1.2.3", "args": ["--acp"]}},
    }]})
    monkeypatch.setattr(registry_module, "resolve_executable", lambda _name: sys.executable)
    manager._auto_register_detected()
    entry = manager.as_dict()["agents"][0]
    assert entry["installed"] and not entry["registered"]
    assert isinstance(gateway.adapter("known-acp"), RegistryAcpEngine)
    assert gateway.adapter("known-acp")._launch_config["args"] == ["--acp"]
    store.close()


def test_registry_install_registers_with_one_admin_mutation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from _gql import data, err_status
    from termx.app import AppState, create_app
    state = AppState(passcode="test-only")
    registry = state.engines.acp_registry
    registry._fetch_index = lambda: {"version": "1.0.0", "agents": [{
        "id": "fixture-acp", "name": "Fixture ACP", "version": "1.0.0",
        "distribution": {"npx": {"package": "fixture-acp@1.0.0"}},
    }]}
    registry._install_sync = lambda _id, _entry: (sys.executable, ["-u", str(FIXTURE)])
    headers = {"X-Termx-Passcode": "test-only"}
    mutation = 'mutation($id: String!) { install_acp_runner(registry_id: $id) }'
    with TestClient(create_app(state)) as client:
        assert err_status(client, mutation, variables={"id": "fixture-acp"}) == 401
        result = data(client, mutation, variables={"id": "fixture-acp"}, headers=headers)["install_acp_runner"]
        assert result["installed"] and result["registered"]
        assert result["engine_id"] == "fixture-acp"
        assert isinstance(state.engines.adapter("fixture-acp"), RegistryAcpEngine)
        config = state.store.get().agent.acp_runners["fixture-acp"]
        assert config["registry_id"] == "fixture-acp" and config["registry_version"] == "1.0.0"
        listed = data(client, "{ acp_registry }", headers=headers)["acp_registry"]
        assert listed["agents"][0]["registered"]


def test_refresh_does_not_change_an_active_session_or_its_event_owner(tmp_path):
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        engine = _engine([], [], {"FAKE_ACP_HANG": "1"})
        engine._event_sink = gateway.on_engine_event
        gateway.register(engine)
        try:
            task = await gateway.create_task(prompt="Wait", cwd=str(tmp_path), engine=engine.id,
                                             model="fast", mode="code")
            binding = gateway._binding_for_task(task["id"])
            await engine._sessions[binding.binding_id]["prompt_started"].wait()
            before = engine.session_configuration(binding)
            await gateway.catalogue.refresh(engine.id, str(tmp_path))
            assert engine.session_configuration(binding) == before
            assert gateway._binding_task[binding.binding_id] == task["id"]
            assert binding.status == "active"
            await gateway.cancel(task["id"])
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_active_custom_runner_cannot_be_removed_or_disabled(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from _gql import data, err_status
    from termx.app import AppState, create_app
    monkeypatch.setenv("FAKE_ACP_HANG", "1")
    state = AppState(passcode="test-only")
    headers = {"X-Termx-Passcode": "test-only"}
    # Pass only an environment name; never serialize a value into runner settings.
    runner = {"executable": sys.executable, "args": ["-u", str(FIXTURE)], "env_names": ["FAKE_ACP_HANG"]}
    mutation = 'mutation($input: JSON!) { update_agent_configuration(input: $input) }'
    with TestClient(create_app(state)) as client:
        data(client, mutation, variables={"input": {"acp_runners": {"custom": runner}}}, headers=headers)
        task = data(client, 'mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id } }',
            variables={"input": {"cwd": str(tmp_path), "prompt": "Wait", "engine": "custom"}}, headers=headers)["create_agent_task"]
        for replacement in ({}, {"custom": {**runner, "enabled": False}}):
            assert err_status(client, mutation, {"input": {"acp_runners": replacement}}, headers) == 400
            assert state.store.get().agent.acp_runners["custom"] == runner
        data(client, 'mutation($id: String!) { cancel_agent_task(task_id: $id) }',
             variables={"id": task["id"]}, headers=headers)


def test_launch_change_marks_cache_stale_without_rediscovery(tmp_path):
    async def run():
        store = AgentStore(tmp_path / "agent.db", tmp_path / "artifacts")
        gateway = EngineGateway(store, store.append_event)
        runner = {"executable": sys.executable, "args": ["-u", str(FIXTURE)]}
        gateway.register_configured_runners(_agent_prefs({"acp_runners": {"custom": runner}}))
        try:
            await gateway.catalogue.refresh("custom", str(tmp_path))
            previous = (await gateway.configuration("custom"))["refreshed_at"]
            prefs = _agent_prefs({"acp_runners": {"custom": runner}, "engines": {"custom": {"model": "fast"}}})
            await gateway.configure_runners(prefs)
            cfg = await gateway.configuration("custom")
            assert cfg["stale"] and cfg["refreshed_at"] == previous
            await gateway.catalogue.refresh("custom", str(tmp_path))
            cfg = await gateway.configuration("custom")
            assert not cfg["stale"]
            assert next(o["currentValue"] for o in cfg["config_options"] if o["category"] == "model") == "fast"
        finally:
            await gateway.shutdown()
            store.close()
    asyncio.run(run())


def test_generic_authentication_uses_only_advertised_method_ids():
    async def run():
        engine = _engine([], [], {"FAKE_ACP_AUTH_REQUIRED": "1"})
        try:
            assert await engine.authenticate("oauth") == {"ok": True, "method": "oauth"}
            with pytest.raises(ValueError, match="did not advertise"):
                await engine.authenticate("invented-method")
        finally:
            await engine.shutdown()
    asyncio.run(run())
