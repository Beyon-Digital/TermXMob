"""Plan mode: read-only exploration, model-authored plan, revise/approve loop."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from test_agent import FakeAdapter, build_manager, wait_for_event, wait_for_pending_approval, wait_for_status
from termx.agent.providers import ProviderCall, ProviderTurn


def _call(name: str, call_id: str, **arguments: Any) -> ProviderCall:
    return ProviderCall(type="function", call_id=call_id, name=name, arguments=arguments)


class PlanAdapter(FakeAdapter):
    """Plays one scripted turn per request; records the flags it was called with."""

    def __init__(self, script: list[tuple[str, list[ProviderCall]]]) -> None:
        super().__init__()
        self.script = list(script)
        self.plan_mode_flags: list[bool] = []

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        read_only: bool = False,
        allow_subagents: bool = True,
        plan_mode: bool = False,
    ) -> ProviderTurn:
        self.inputs.append(input_items)
        self.read_only_flags.append(read_only)
        self.plan_mode_flags.append(plan_mode)
        self.turns += 1
        text, calls = self.script.pop(0) if self.script else ("All done.", [])
        return ProviderTurn(
            response_id=f"response-{self.turns}",
            text=text,
            calls=calls,
            usage={},
            output_items=[
                {
                    "type": "function_call",
                    "call_id": c.call_id,
                    "name": c.name,
                    "arguments": json.dumps(c.arguments),
                }
                for c in calls
            ],
        )


async def _start(tmp_path: Path, adapter: PlanAdapter):
    manager, store = build_manager(tmp_path, adapter)
    task = await manager.create_task(
        prompt="Add a --verbose flag", cwd=str(tmp_path), provider_id="fake", mode="plan"
    )
    return manager, store, task


def test_plan_mode_is_kept_and_starts_read_only_without_planning_call(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter([("", [_call("exit_plan", "c1", plan_markdown="1. Edit cli.py")])])
        manager, store, task = await _start(tmp_path, adapter)
        assert task["mode"] == "plan"
        await wait_for_status(store, task["id"], "awaiting_approval")
        assert adapter.read_only_flags[0] is True
        assert adapter.plan_mode_flags[0] is True
        await manager.close()
        store.close()

    asyncio.run(run())


def test_exit_plan_requests_plan_approval(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter([("", [_call("exit_plan", "c1", plan_markdown="1. Edit cli.py\n2. Add test")])])
        manager, store, task = await _start(tmp_path, adapter)
        task, approval = await wait_for_pending_approval(store, task["id"], "plan")
        assert task["status"] == "awaiting_approval"
        assert "Edit cli.py" in task["plan"]["markdown"]
        assert approval["payload"]["plan"]["markdown"] == task["plan"]["markdown"]
        await wait_for_event(store, task["id"], "plan.ready")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_plan_text_without_exit_plan_becomes_the_plan(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter([("Here is my plan: edit cli.py.", [])])
        manager, store, task = await _start(tmp_path, adapter)
        task, _approval = await wait_for_pending_approval(store, task["id"], "plan")
        assert "edit cli.py" in task["plan"]["markdown"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_plan_mode_refuses_mutating_shell(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter(
            [
                ("", [_call("run_shell", "c1", command="rm -rf build", purpose="clean")]),
                ("", [_call("exit_plan", "c2", plan_markdown="plan")]),
            ]
        )
        manager, store, task = await _start(tmp_path, adapter)
        await wait_for_status(store, task["id"], "awaiting_approval")
        finished = next(e for e in store.events(task["id"]) if e["type"] == "tool.finished")
        assert finished["payload"]["result"]["refused"] is True
        await manager.close()
        store.close()

    asyncio.run(run())


def test_revise_feeds_feedback_back_and_replans(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter(
            [
                ("", [_call("exit_plan", "c1", plan_markdown="v1")]),
                ("", [_call("exit_plan", "c2", plan_markdown="v2 with tests")]),
            ]
        )
        manager, store, task = await _start(tmp_path, adapter)
        _task, first = await wait_for_pending_approval(store, task["id"], "plan")
        resolved = await manager.resolve_approval(
            task["id"], first["id"], "revise", feedback="Also cover the --quiet flag"
        )
        assert resolved["status"] == "revised"
        await wait_for_event(store, task["id"], "plan.revised")
        task, second = await wait_for_pending_approval(store, task["id"], "plan")
        assert second["id"] != first["id"]
        assert task["plan"]["markdown"] == "v2 with tests"
        assert task["mode"] == "plan"
        assert "--quiet" in json.dumps(adapter.inputs[-1])
        await manager.close()
        store.close()

    asyncio.run(run())


def test_approve_switches_to_agent_mode_and_keeps_exploration_history(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter(
            [
                ("", [_call("exit_plan", "c1", plan_markdown="v1")]),
                ("Implemented.", []),
            ]
        )
        manager, store, task = await _start(tmp_path, adapter)
        _task, approval = await wait_for_pending_approval(store, task["id"], "plan")
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        done = await wait_for_status(store, task["id"], "completed")
        assert done["mode"] == "agent"
        assert adapter.read_only_flags[-1] is False
        assert adapter.plan_mode_flags[-1] is False
        assert "v1" in json.dumps(adapter.inputs[-1])
        await manager.close()
        store.close()

    asyncio.run(run())


def test_denying_a_plan_still_cancels(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter([("", [_call("exit_plan", "c1", plan_markdown="v1")])])
        manager, store, task = await _start(tmp_path, adapter)
        _task, approval = await wait_for_pending_approval(store, task["id"], "plan")
        await manager.resolve_approval(task["id"], approval["id"], "denied")
        await wait_for_status(store, task["id"], "cancelled")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_revise_is_only_valid_for_plan_approvals(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = FakeAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="agent")
        _task, approval = await wait_for_pending_approval(store, task["id"], "plan")
        with pytest.raises(ValueError, match="plan mode"):
            await manager.resolve_approval(task["id"], approval["id"], "revise", feedback="no")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_ask_mode_still_rejects_worktrees(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = PlanAdapter([])
        manager, store = build_manager(tmp_path, adapter)
        with pytest.raises(ValueError, match="read-only"):
            await manager.create_task(
                prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask", execution_mode="worktree"
            )
        await manager.close()
        store.close()

    asyncio.run(run())


def test_exit_plan_is_offered_only_in_plan_mode() -> None:
    from termx.agent.tools import default_registry

    def names(**kwargs: Any) -> set[str]:
        return {tool["name"] for tool in default_registry().provider_tools(**kwargs)}

    assert "exit_plan" in names(read_only=True, plan_mode=True)
    assert "exit_plan" not in names(read_only=True)
    assert "exit_plan" not in names(read_only=False)
