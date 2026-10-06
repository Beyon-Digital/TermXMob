from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from _gql import data, err_status
from test_agent import FakeAdapter, build_manager, wait_for_status
from termx.agent.limits import resolve_limits
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.app import AppState, create_app
from termx.config import ConfigStore


class ManyReads(FakeAdapter):
    def __init__(self, count=30):
        super().__init__()
        self.count = count
        self.seen = []

    async def turn(self, **kwargs):
        history = kwargs.get("input_items") or []
        self.seen.append(history)
        self.turns += 1
        if self.turns > self.count:
            return ProviderTurn("done", "verified", [], {})
        call = ProviderCall(type="function", call_id=f"read-{self.turns}",
                            name="read_file", arguments={"path": "sample.txt"})
        return ProviderTurn(f"response-{self.turns}", "", [call], {}, [
            {"type": "function_call", "call_id": call.call_id,
             "name": "read_file", "arguments": '{"path":"sample.txt"}'}])


def test_internal_task_survives_more_than_old_default_requests(tmp_path):
    (tmp_path / "sample.txt").write_text("sample")
    async def run():
        adapter = ManyReads()
        manager, store = build_manager(tmp_path, adapter)
        try:
            task = await manager.create_task(prompt="Inspect", cwd=str(tmp_path),
                                             provider_id="fake", mode="ask")
            done = await wait_for_status(store, task["id"], "completed", timeout=10)
            assert done["result"] == "verified"
            assert adapter.turns == 31
            assert len([e for e in store.events(task["id"]) if e["type"] == "tool.finished"]) == 30
            assert not any(e["type"] == "task.failed" for e in store.events(task["id"]))
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_budget_approval_resumes_without_replaying_tools_after_restart(tmp_path):
    (tmp_path / "sample.txt").write_text("sample")
    async def run():
        adapter = ManyReads(2)
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Inspect", cwd=str(tmp_path),
                                         provider_id="fake", mode="ask", limits={"max_steps": 1})
        paused = await wait_for_status(store, task["id"], "awaiting_approval")
        assert paused["runtime"]["step"] == 1
        approval = store.approvals(task["id"])[-1]
        assert approval["kind"] == "budget"
        await manager.close()
        store.close()
        manager, store = build_manager(tmp_path, adapter)
        try:
            assert store.get_task(task["id"])["status"] == "awaiting_approval"
            with pytest.raises(ValueError, match="must exceed"):
                await manager.resolve_approval(task["id"], approval["id"], "approved",
                                               limits={"max_steps": 1})
            assert store.get_approval(approval["id"])["status"] == "pending"
            await manager.resolve_approval(task["id"], approval["id"], "approved", limits={"max_steps": 10})
            await wait_for_status(store, task["id"], "completed")
            finished = [e["payload"]["call_id"] for e in store.events(task["id"])
                        if e["type"] == "tool.finished"]
            assert finished == ["read-1", "read-2"]
            assert any(i.get("call_id") == "read-1" for i in adapter.seen[-1])
            with pytest.raises(ValueError):
                await manager.resolve_approval(task["id"], approval["id"], "approved")
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_time_budget_pauses_and_approval_renews_clock(tmp_path):
    async def run():
        manager, store = build_manager(tmp_path, ManyReads(0))
        try:
            task = store.create_task(prompt="Inspect", cwd=str(tmp_path), provider_id="fake",
                                     model="fake-model", limits=resolve_limits({"max_seconds": 1}), mode="ask")
            await manager._drive(task["id"], started_at=monotonic() - 2)
            approval = store.approvals(task["id"])[-1]
            assert approval["payload"]["reason"] == "max_seconds"
            await manager.resolve_approval(task["id"], approval["id"], "approved")
            await wait_for_status(store, task["id"], "completed")
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_host_configuration_persists_and_does_not_silently_clamp(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    prefs = store.update_agent({"limits": {"max_steps": 2000, "max_seconds": 86400},
        "provider_timeout_s": 600, "engines": {"devin": {
            "args": ["acp"], "env_names": ["DEVIN_API_KEY"], "mode": "code",
            "model": "fast", "config_options": {"safe": True}}}})
    assert ConfigStore(store.path).get().agent == prefs
    assert resolve_limits(prefs.limits)["max_steps"] == 2000
    for patch in ({"limits": {"max_steps": 0}}, {"provider_timeout_s": True},
                  {"engines": {"devin": {"env": {"SECRET": "value"}}}}):
        with pytest.raises(ValueError):
            store.update_agent(patch)
    assert store.get().agent == prefs


@pytest.mark.parametrize("limits", [{"max_steps": 1.2}, {"max_steps": "100"},
                                   {"max_runs": 50}, {"max_seconds": None}, {"max_steps": True}])
def test_invalid_run_limits_are_actionable(limits):
    with pytest.raises(ValueError):
        resolve_limits(limits)


def test_graphql_host_agent_configuration_is_applied_and_reloaded(tmp_path):
    state = AppState(passcode="test-only")
    with TestClient(create_app(state)) as client:
        headers = {"X-Termx-Passcode": "test-only"}
        out = data(client, 'mutation($input: JSON!) { update_agent_configuration(input: $input) }',
                   variables={"input": {"limits": {"max_steps": 1500, "max_seconds": 14400,
                        "shell_timeout_s": 120, "max_parallel_subagents": 3,
                        "max_subagents_total": 8}, "provider_timeout_s": 500}}, headers=headers)
        assert out["update_agent_configuration"]["limits"]["max_steps"] == 1500
        assert state.agent._limits(None)["max_steps"] == 1500
        out = data(client, "{ agent_configuration }", headers=headers)
        assert out["agent_configuration"]["provider_timeout_s"] == 500
        assert err_status(client, '{ agent_configuration }') == 401
    assert ConfigStore(state.store.path).get().agent.limits["max_steps"] == 1500
