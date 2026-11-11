"""Task quotas are absent while explicit command control stays available."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from test_agent import FakeAdapter, build_manager, wait_for_status
from termx.agent.limits import DEFAULT_LIMITS, resolve_limits
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.tools.shell import _shell_timeout


@pytest.mark.parametrize("legacy", [None, {"max_steps": 1, "max_seconds": 1,
    "shell_timeout_s": 1, "max_parallel_subagents": 1, "max_subagents_total": 1},
    {key: None for key in DEFAULT_LIMITS}])
def test_task_limits_have_stable_unlimited_public_keys(legacy):
    assert resolve_limits(legacy) == {key: None for key in DEFAULT_LIMITS}


def test_shell_has_no_implicit_deadline_but_retains_explicit_command_timeout():
    ctx = SimpleNamespace(task={"limits": {"shell_timeout_s": 120}})
    assert _shell_timeout(ProviderCall("function", "c", "run_shell", {}), ctx) is None
    assert _shell_timeout(ProviderCall("function", "c", "run_check", {}), ctx, 300.0) is None
    assert _shell_timeout(ProviderCall("function", "c", "run_shell", {"timeout_s": 18_000}), ctx) == 18_000
    for value in (0, -1, True, "invalid", float("inf"), float("nan")):
        with pytest.raises(ValueError, match="positive number"):
            _shell_timeout(ProviderCall("function", "c", "run_shell", {"timeout_s": value}), ctx)


def test_more_than_eight_parallel_children_run_and_parent_cancellation_still_stops_them(tmp_path):
    class Adapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.started = set()
            self.cancelled = set()
            self.all_started = asyncio.Event()

        async def turn(self, **kwargs):
            prompt = kwargs["prompt"]
            self.started.add(prompt)
            if len(self.started) == 10:
                self.all_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.add(prompt)
                raise
            return ProviderTurn("unused", "", [], {})

    async def run():
        adapter = Adapter()
        manager, store = build_manager(tmp_path, adapter)
        try:
            parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake",
                limits={"max_parallel_subagents": 1, "max_subagents_total": 1})
            cancel = asyncio.Event()
            children = []
            for index in range(10):
                call = ProviderCall("function", f"spawn-{index}", "spawn_subagent",
                    {"task": f"Child {index}", "mode": "ask"})
                result = await manager._spawn_subagent(parent["id"], manager._task(parent["id"]), call, cancel)
                assert result["ok"] is True
                children.append(result["task_id"])
            await asyncio.wait_for(adapter.all_started.wait(), 5)
            assert len(store.children(parent["id"])) == 10
            assert all(store.get_task(child)["status"] == "running" for child in children)
            assert all(all(value is None for value in store.get_task(child)["limits"].values()) for child in children)
            manager.cancel(parent["id"])
            for child in children:
                await wait_for_status(store, child, "cancelled", timeout=5)
            assert len(adapter.cancelled) == 10
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_immediate_close_cancels_a_driver_before_its_first_turn(tmp_path):
    async def run():
        manager, store = build_manager(tmp_path, FakeAdapter())
        # A fresh adapter would keep requesting its first tool forever if
        # shutdown missed the cancellation event before driver startup.
        manager._adapter_factory = lambda *_: FakeAdapter()
        try:
            task = await manager.create_task(prompt="Inspect", cwd=str(tmp_path), provider_id="fake", mode="ask")
            await asyncio.wait_for(manager.close(), timeout=2)
            assert store.get_task(task["id"])["status"] == "cancelled"
        finally:
            for worker in manager._workers.values():
                worker.cancel()
            store.close()
    asyncio.run(run())
