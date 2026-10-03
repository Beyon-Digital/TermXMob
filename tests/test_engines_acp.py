"""ACP engine tests — shared client via fake agent, Devin/Grok descriptors."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from termx.engines.acp import AcpEngine
from termx.engines.devin import DevinEngine
from termx.engines.grok import GrokEngine
from termx.engines.types import EffectiveRunConfiguration

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


def test_devin_descriptor_and_grok_descriptor():
    d = DevinEngine()
    assert d.id == "devin" and d.acp_args == ["acp"]
    g = GrokEngine()
    assert "agent" in g.acp_args and "stdio" in g.acp_args
    desc = d.descriptor()
    assert desc.transport == "acp-stdio"
