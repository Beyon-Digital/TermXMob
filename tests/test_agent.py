from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from termx.agent import providers as agent_providers
from termx.agent.computer import ComputerController
from termx.agent.context import workspace_manifest
from termx.agent.manager import AgentManager
from termx.agent.policy import evaluate_computer, evaluate_shell, redact
from termx.agent.providers import OpenAIResponsesAdapter, ProviderCall, ProviderError, ProviderTurn, _parse_plan
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.app import AppState, create_app
from termx.desktop import broker, input as desktop_input


class FakeComputer:
    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.released = 0
        self.actions: list[list[dict[str, Any]]] = []
        self._frames = frames or [b"jpeg"]
        self._frame_index = 0

    async def execute(
        self,
        actions: list[dict[str, Any]],
        *,
        cancel: asyncio.Event | None = None,
    ) -> bytes:
        self.actions.append(actions)
        frame = self._frames[min(self._frame_index, len(self._frames) - 1)]
        self._frame_index += 1
        return frame

    async def release_all(self) -> None:
        self.released += 1


def test_parse_plan_rejects_tool_markup_and_honors_requested_step_count() -> None:
    prompt = "Use the computer to inspect the desktop in exactly two safe steps."
    plan = _parse_plan(
        "<tool_call>computer\n<arg_key>action</arg_key>\n<arg_value>screenshot</arg_value>\n</tool_call>",
        prompt,
    )

    assert plan["summary"] == prompt
    assert plan["steps"] == [
        "Inspect the current desktop state",
        "Verify and report the requested result",
    ]
    assert plan["tools"] == ["computer"]


def test_parse_plan_rejects_nested_json_and_invalid_step_values() -> None:
    prompt = "Inspect the desktop in exactly two safe steps."
    plan = _parse_plan(
        json.dumps(
            {
                "summary": json.dumps({"summary": "Inspect", "steps": ["One", "Two"]}),
                "steps": [{"raw": "one"}, "[\"two\"]"],
                "tools": ["computer"],
                "risks": [{"raw": "none"}],
            }
        ),
        prompt,
    )

    assert plan == {
        "summary": prompt,
        "steps": [
            "Inspect the current desktop state",
            "Verify and report the requested result",
        ],
        "tools": ["computer"],
        "risks": [],
    }


def test_parse_plan_fallback_preserves_explicit_screenshot_and_wait_actions() -> None:
    prompt = "Take a screenshot of the current desktop, then wait for 15 seconds in exactly two safe steps."
    plan = _parse_plan("<tool_call>computer</tool_call>", prompt)

    assert plan["steps"] == [
        "Capture a screenshot of the current desktop",
        "Wait for 15 seconds while keeping the task interruptible",
    ]
    assert plan["tools"] == ["computer"]


class FakeAdapter:
    def __init__(self, command: str = "printf agent-ok") -> None:
        self.command = command
        self.turns = 0
        self.inputs: list[list[dict[str, Any]] | None] = []
        self.read_only_flags: list[bool] = []

    async def test(self) -> str:
        return "OK"

    async def plan(self, prompt: str, cwd: str, manifest: dict[str, Any]):
        return (
            {
                "summary": prompt,
                "steps": ["Inspect", "Change", "Verify"],
                "tools": ["shell"],
                "risks": [],
            },
            "plan-response",
        )

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        self.inputs.append(input_items)
        self.read_only_flags.append(read_only)
        self.turns += 1
        if self.turns == 1:
            call = ProviderCall(
                type="function",
                call_id="call-1",
                name="run_shell",
                arguments={"command": self.command, "purpose": "Verify execution"},
            )
            return ProviderTurn(
                response_id="response-1",
                text="I will verify the project.",
                calls=[call],
                usage={"input_tokens": 10},
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "run_shell",
                        "arguments": json.dumps(call.arguments),
                    }
                ],
            )
        assert input_items is not None
        assert any(item.get("type") == "function_call_output" for item in input_items)
        return ProviderTurn(
            response_id="response-2",
            text="Verified successfully.",
            calls=[],
            usage={"output_tokens": 4},
            output_items=[{"type": "message", "content": [{"type": "output_text", "text": "Verified successfully."}]}],
        )


class ComputerAdapter(FakeAdapter):
    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        self.inputs.append(input_items)
        self.turns += 1
        if self.turns == 1:
            call = ProviderCall(
                type="computer",
                call_id="computer-1",
                actions=[{"type": "click", "x": 24, "y": 32}],
            )
            return ProviderTurn(
                response_id="computer-response-1",
                text="I will inspect the visible result.",
                calls=[call],
                usage={},
                output_items=[{"type": "computer_call", "call_id": call.call_id, "action": call.actions[0]}],
            )
        assert allow_computer is True
        assert input_items is not None
        assert any(item.get("type") == "computer_call_output" for item in input_items)
        return ProviderTurn(
            response_id="computer-response-2",
            text="The visible result is verified.",
            calls=[],
            usage={},
            output_items=[{"type": "message", "content": [{"type": "output_text", "text": "The visible result is verified."}]}],
        )


class ComputerFunctionAdapter(FakeAdapter):
    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        self.inputs.append(input_items)
        self.turns += 1
        if self.turns == 1:
            call = ProviderCall(
                type="computer",
                call_id="computer-function-1",
                name="use_computer",
                actions=[{"type": "screenshot"}],
            )
            return ProviderTurn(
                response_id="computer-function-response-1",
                text="I will inspect the current screen.",
                calls=[call],
                usage={},
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": json.dumps({"actions": call.actions}),
                    }
                ],
            )
        assert allow_computer is True
        assert input_items is not None
        output = next(item for item in input_items if item.get("type") == "function_call_output")
        assert output["output"] == "Computer action completed."
        screenshot = next(item for item in input_items if item.get("role") == "user")
        assert screenshot["content"][1]["type"] == "input_image"
        assert screenshot["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
        return ProviderTurn(
            response_id="computer-function-response-2",
            text="The current screen is visible.",
            calls=[],
            usage={},
            output_items=[
                {"type": "message", "content": [{"type": "output_text", "text": "The current screen is visible."}]}
            ],
        )


class SecretComputerAdapter(ComputerAdapter):
    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        if self.turns == 0:
            self.turns += 1
            call = ProviderCall(
                type="computer",
                call_id="computer-secret",
                actions=[{"type": "type", "text": "token=super-secret-value"}],
            )
            return ProviderTurn(
                response_id="computer-secret-response-1",
                text="I will enter the approved value.",
                calls=[call],
                usage={},
                output_items=[
                    {"type": "computer_call", "call_id": call.call_id, "action": call.actions[0]}
                ],
            )
        return await super().turn(
            prompt=prompt,
            cwd=cwd,
            manifest=manifest,
            previous_response_id=previous_response_id,
            input_items=input_items,
            allow_computer=allow_computer,
            read_only=read_only,
        )


class BlockingProviderAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        self.started.set()
        await asyncio.sleep(30)
        raise ProviderError("Provider request failed (404): The provider returned an error.")


async def wait_for_status(store: AgentStore, task_id: str, status: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        task = store.get_task(task_id, include_events=True)
        if task and task["status"] == status:
            return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}")


async def wait_for_event(
    store: AgentStore, task_id: str, event_type: str, timeout: float = 3.0
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        matched = [e for e in store.events(task_id) if e["type"] == event_type]
        if matched:
            return matched
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} never emitted {event_type}")


async def wait_for_pending_approval(store: AgentStore, task_id: str, kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        task = store.get_task(task_id, include_events=True)
        if task:
            approval = next(
                (item for item in task["approvals"] if item["status"] == "pending" and item["kind"] == kind),
                None,
            )
            if approval is not None:
                return task, approval
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} did not request {kind} approval")


def build_manager(tmp_path: Path, adapter: FakeAdapter) -> tuple[AgentManager, AgentStore]:
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    credentials = CredentialStore(memory={})
    manager = AgentManager(store, credentials, desktop=None, adapter_factory=lambda _provider, _key: adapter)
    manager._computer = FakeComputer()  # type: ignore[assignment]
    manager.save_provider(
        provider_id="fake",
        kind="openai-compatible",
        name="Fake",
        base_url="http://127.0.0.1:9999/v1",
        model="fake-model",
        capabilities=["shell", "computer"],
        api_key="test-key",
    )
    return manager, store


def test_store_replay_artifacts_and_secret_metadata(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    provider = store.put_provider(
        "openai",
        kind="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        model="model",
        capabilities=["shell"],
        secret_configured=True,
    )
    assert provider["secret_configured"] is True
    assert "api_key" not in provider
    task = store.create_task(prompt="test", cwd=str(tmp_path), provider_id="openai", model="model", limits={})
    first = store.append_event(task["id"], "one", {"value": 1})
    second = store.append_event(task["id"], "two", {"value": 2})
    assert [item["sequence"] for item in store.events(task["id"])] == [1, 2]
    assert store.events(task["id"], after=first["sequence"]) == [second]
    artifact = store.save_artifact(task["id"], "screenshot", "image/jpeg", b"same")
    again = store.save_artifact(task["id"], "screenshot", "image/jpeg", b"same")
    assert artifact["digest"] == again["digest"]
    assert len(list((tmp_path / "artifacts").iterdir())) == 1
    assert store.retention_policy() == {"retention_days": 30, "max_bytes": 500_000_000}
    store.set_retention_policy(retention_days=7, max_bytes=25_000_000)
    assert store.storage_status()["retention_days"] == 7
    assert store.storage_status()["max_bytes"] == 25_000_000
    store.close()


def test_manager_plan_approval_shell_and_completion(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = FakeAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Check the project", cwd=str(tmp_path), provider_id="fake")
        assert task["status"] == "awaiting_approval"
        approval = task["approvals"][0]
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        completed = await wait_for_status(store, task["id"], "completed")
        assert completed["result"] == "Verified successfully."
        events = completed["events"]
        finished = next(item for item in events if item["type"] == "tool.finished")
        assert "agent-ok" in finished["payload"]["result"]["output"]
        assert adapter.turns == 2
        await manager.close()
        store.close()

    asyncio.run(run())


def test_consequential_tool_pauses_and_denial_cancels(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter("git push origin main"))
        task = await manager.create_task(prompt="Publish", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        paused, pending = await wait_for_pending_approval(store, task["id"], "tool")
        assert paused["status"] == "awaiting_approval"
        assert pending["payload"]["title"] == "External publication"
        await manager.resolve_approval(task["id"], pending["id"], "denied")
        cancelled = await wait_for_status(store, task["id"], "cancelled")
        assert cancelled["error"] == "Approval denied"
        assert task["id"] not in manager._context_engines
        assert task["id"] not in manager._metrics
        assert task["id"] not in manager._steering
        metrics_events = [e for e in store.events(task["id"]) if e["type"] == "task.metrics"]
        assert metrics_events, "terminal transitions must publish task.metrics"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_approved_tool_executes_private_call_without_persisting_secrets(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, SecretComputerAdapter())
        task = await manager.create_task(prompt="Enter the value", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")

        assert "super-secret-value" not in json.dumps(pending["payload"])
        await manager.resolve_approval(task["id"], pending["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        assert manager._computer.actions == [
            [{"type": "type", "text": "token=super-secret-value"}]
        ]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_approved_tool_without_private_call_is_an_approval_conflict(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, SecretComputerAdapter())
        task = await manager.create_task(prompt="Enter the value", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        manager._pending_approval_calls.pop(pending["id"])

        with pytest.raises(ValueError, match="approved tool request is no longer available"):
            await manager.resolve_approval(task["id"], pending["id"], "approved")

        assert store.get_task(task["id"])["status"] == "awaiting_approval"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_ask_mode_runs_read_only_without_approval(tmp_path: Path) -> None:
    async def run() -> None:
        # Ask mode issues a mutating command; the manager must refuse it and the
        # adapter must be told the turn is read-only.
        adapter = FakeAdapter(command="rm -rf build")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(
            prompt="What does this project do?",
            cwd=str(tmp_path),
            provider_id="fake",
            mode="ask",
        )
        # No plan/approval gate in Ask mode: it starts immediately.
        assert task["mode"] == "ask"
        assert not task.get("approvals")
        completed = await wait_for_status(store, task["id"], "completed")
        finished = next(item for item in completed["events"] if item["type"] == "tool.finished")
        assert finished["payload"]["result"]["refused"] is True
        assert adapter.read_only_flags == [True, True]
        await manager.close()
        store.close()

    asyncio.run(run())


class ScriptedAdapter(FakeAdapter):
    """Returns one scripted list of calls per turn, then a final text turn."""

    def __init__(self, script: list[list[ProviderCall]], plan_delay: float = 0.0) -> None:
        super().__init__()
        self.script = script
        self.plan_delay = plan_delay
        self.plan_calls = 0

    async def plan(self, prompt: str, cwd: str, manifest: dict[str, Any]):
        self.plan_calls += 1
        if self.plan_delay:
            await asyncio.sleep(self.plan_delay)
        return await super().plan(prompt, cwd, manifest)

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        self.inputs.append(input_items)
        self.read_only_flags.append(read_only)
        self.turns += 1
        if self.script:
            calls = self.script.pop(0)
            return ProviderTurn(
                response_id=f"response-{self.turns}",
                text="",
                calls=calls,
                usage={},
                output_items=[
                    {"type": "function_call", "call_id": c.call_id, "name": c.name, "arguments": json.dumps(c.arguments)}
                    for c in calls
                ],
            )
        return ProviderTurn(
            response_id=f"response-{self.turns}",
            text="Done.",
            calls=[],
            usage={},
            output_items=[{"type": "message", "content": [{"type": "output_text", "text": "Done."}]}],
        )


def _fn(call_id: str, name: str, **arguments: Any) -> ProviderCall:
    return ProviderCall(type="function", call_id=call_id, name=name, arguments=arguments)


def test_ask_mode_shares_files_inline_and_refuses_sensitive_paths(tmp_path: Path) -> None:
    (tmp_path / "report.txt").write_text("hello")
    (tmp_path / ".env").write_text("SECRET=1")

    async def run() -> None:
        adapter = ScriptedAdapter([[_fn("s1", "share_file", path="report.txt"), _fn("s2", "share_file", path=".env")]])
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Show me the report", cwd=str(tmp_path), provider_id="fake", mode="ask")
        completed = await wait_for_status(store, task["id"], "completed")
        assert not completed["approvals"]
        media = [e for e in completed["events"] if e["type"] == "agent.media"]
        assert [m["payload"]["name"] for m in media] == ["report.txt"]
        finished = [e for e in completed["events"] if e["type"] == "tool.finished"]
        assert finished[0]["payload"]["result"]["ok"] is True
        assert finished[1]["payload"]["result"]["refused"] is True
        assert len(completed["artifacts"]) == 1
        await manager.close()
        store.close()

    asyncio.run(run())


def test_subagent_escalates_consequential_actions_to_parent(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = ScriptedAdapter(
            [
                [_fn("p1", "spawn_subagent", task="Publish it", agent="publisher")],
                [_fn("c1", "run_shell", command="git push origin main")],
            ]
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(prompt="Delegate", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        _, delegate = await wait_for_pending_approval(store, parent["id"], "tool")
        assert delegate["payload"]["title"] == "Delegate to a sub-agent"
        await manager.resolve_approval(parent["id"], delegate["id"], "approved")

        _, escalated = await wait_for_pending_approval(store, parent["id"], "tool")
        # Async spawn lets the parent finish while a child escalation is still
        # open; the escalation stays resolvable either way.
        assert store.get_task(parent["id"])["status"] in {"awaiting_approval", "completed"}
        assert escalated["payload"]["title"] == "publisher: External publication"
        child_id = escalated["payload"]["child_id"]
        child = store.get_task(child_id, include_events=True)
        assert child["status"] == "awaiting_approval"
        assert all(a["status"] != "pending" or a["kind"] == "tool" for a in child["approvals"])

        await manager.resolve_approval(parent["id"], escalated["id"], "denied")
        completed = await wait_for_status(store, parent["id"], "completed")
        await wait_for_status(store, child_id, "cancelled")
        events = await wait_for_event(store, parent["id"], "subagent.finished")
        finished = events[-1]
        assert finished["payload"]["status"] == "cancelled"
        all_events = store.events(parent["id"])
        assert any(e["type"] == "subagent.event" and e["payload"]["type"] == "task.status" for e in all_events)
        await manager.close()
        store.close()

    asyncio.run(run())


def test_parent_cancel_interrupts_child_planning(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = ScriptedAdapter([[_fn("p1", "spawn_subagent", task="Slow child")]], plan_delay=30.0)
        manager, store = build_manager(tmp_path, adapter)
        adapter.plan_delay = 0.0
        parent = await manager.create_task(prompt="Delegate", cwd=str(tmp_path), provider_id="fake")
        adapter.plan_delay = 30.0
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        _, delegate = await wait_for_pending_approval(store, parent["id"], "tool")
        await manager.resolve_approval(parent["id"], delegate["id"], "approved")
        while adapter.plan_calls < 2:
            await asyncio.sleep(0.02)
        manager.cancel(parent["id"])
        cancelled = await wait_for_status(store, parent["id"], "cancelled")
        assert cancelled["error"] == "Cancelled by user"
        children = [t for t in store.list_tasks() if t["id"] != parent["id"]]
        assert len(children) == 1 and children[0]["status"] == "cancelled"
        await manager.close()
        store.close()

    asyncio.run(run())


class _FanOutAdapter(FakeAdapter):
    """Routes scripted turns per-task by a substring of the task prompt.

    routes maps a prompt substring to (script, per-turn delay). Parent and
    children consume independent scripts, and a nonzero delay keeps a task
    busy so fan-out/cancel paths are exercised deterministically.
    """

    def __init__(self, routes: dict[str, tuple[list[list[ProviderCall]], float]]) -> None:
        super().__init__()
        self.routes = {key: (list(script), delay) for key, (script, delay) in routes.items()}

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        self.turns += 1
        for key, (script, delay) in self.routes.items():
            if key not in prompt:
                continue
            if delay:
                await asyncio.sleep(delay)
            if script:
                calls = script.pop(0)
                return ProviderTurn(
                    response_id=f"response-{self.turns}",
                    text="",
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
            return ProviderTurn(
                response_id=f"response-{self.turns}",
                text=f"{key} done.",
                calls=[],
                usage={},
                output_items=[{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
            )
        raise AssertionError(f"no route scripted for prompt {prompt!r}")


async def _approve_pending(manager: AgentManager, store: AgentStore, task_id: str) -> dict[str, Any]:
    _, approval = await wait_for_pending_approval(store, task_id, "tool")
    return await manager.resolve_approval(task_id, approval["id"], "approved")


def test_spawn_subagent_returns_async_handle(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter(
            {
                "Coordinate": ([[_fn("s1", "spawn_subagent", task="child-a")]], 0.0),
                "child-a": ([], 0.0),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])

        spawn_events = await wait_for_event(store, parent["id"], "tool.finished")
        spawn_result = spawn_events[0]["payload"]["result"]
        assert spawn_result["ok"] is True and spawn_result["status"] == "running"
        child_id = spawn_result["task_id"]
        assert store.get_task(child_id)["parent_id"] == parent["id"]

        await wait_for_status(store, parent["id"], "completed")
        await wait_for_status(store, child_id, "completed")
        finished = (await wait_for_event(store, parent["id"], "subagent.finished"))[-1]
        assert finished["payload"]["status"] == "completed"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_subagent_fanout_and_await_collects_results(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter(
            {
                "Coordinate": (
                    [
                        [
                            _fn("s1", "spawn_subagent", task="child-a"),
                            _fn("s2", "spawn_subagent", task="child-b"),
                        ],
                        [_fn("w1", "await_subagents")],
                    ],
                    0.0,
                ),
                "child-a": ([], 0.1),
                "child-b": ([], 0.1),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])
        await _approve_pending(manager, store, parent["id"])

        await wait_for_status(store, parent["id"], "completed")
        children = [t for t in store.list_tasks() if t.get("parent_id") == parent["id"]]
        assert len(children) == 2
        for child in children:
            assert child["status"] == "completed"

        events = store.events(parent["id"])
        started = [e for e in events if e["type"] == "subagent.started"]
        finished = [e for e in events if e["type"] == "subagent.finished"]
        assert len(started) == 2 and len(finished) == 2
        # true fan-out: both children launched before either finished
        first_finished_index = events.index(finished[0])
        assert all(events.index(entry) < first_finished_index for entry in started)

        awaited = next(
            e for e in events
            if e["type"] == "tool.finished" and e["payload"].get("call_id") == "w1"
        )
        result = awaited["payload"]["result"]
        assert result["ok"] is True and result["timed_out"] is False
        assert set(result["children"].keys()) == {c["id"] for c in children}
        assert all(entry["status"] == "completed" for entry in result["children"].values())
        assert any(e["type"] == "subagent.awaited" for e in events)
        await manager.close()
        store.close()

    asyncio.run(run())


def test_subagent_status_and_cancel(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter(
            {
                "Coordinate": (
                    [
                        [_fn("s1", "spawn_subagent", task="child-slow")],
                        [_fn("q1", "subagent_status")],
                        [_fn("x1", "cancel_subagent")],
                    ],
                    0.0,
                ),
                "child-slow": ([], 30.0),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])

        await wait_for_status(store, parent["id"], "completed")
        events = store.events(parent["id"])
        status_call = next(e for e in events if e["type"] == "tool.finished" and e["payload"]["call_id"] == "q1")
        status_children = status_call["payload"]["result"]["children"]
        child_id = next(iter(status_children))
        assert status_children[child_id]["running"] is True

        cancel_call = next(e for e in events if e["type"] == "tool.finished" and e["payload"]["call_id"] == "x1")
        assert cancel_call["payload"]["result"]["cancelled"] == [child_id]
        assert any(e["type"] == "subagent.cancelled" for e in events)

        await wait_for_status(store, child_id, "cancelled")
        finished = (await wait_for_event(store, parent["id"], "subagent.finished"))[-1]
        assert finished["payload"]["status"] == "cancelled"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_subagent_parallel_limit_blocks_spawn(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter(
            {
                "Coordinate": (
                    [
                        [_fn("s1", "spawn_subagent", task="child-slow")],
                        [_fn("s2", "spawn_subagent", task="child-extra")],
                    ],
                    0.0,
                ),
                "child-slow": ([], 30.0),
                "child-extra": ([], 30.0),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(
            prompt="Coordinate",
            cwd=str(tmp_path),
            provider_id="fake",
            limits={"max_parallel_subagents": 1},
        )
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])
        await _approve_pending(manager, store, parent["id"])

        await wait_for_status(store, parent["id"], "completed")
        events = store.events(parent["id"])
        second = next(e for e in events if e["type"] == "tool.finished" and e["payload"]["call_id"] == "s2")
        assert second["payload"]["result"]["ok"] is False
        assert "parallel limit" in second["payload"]["result"]["error"]
        children = [t for t in store.list_tasks() if t.get("parent_id") == parent["id"]]
        assert len(children) == 1
        manager.cancel(children[0]["id"])
        await manager.close()
        store.close()

    asyncio.run(run())


def test_subagent_total_limit_blocks_spawn(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter(
            {
                "Coordinate": (
                    [
                        [_fn("s1", "spawn_subagent", task="child-a")],
                        [_fn("s2", "spawn_subagent", task="child-b")],
                    ],
                    0.0,
                ),
                "child-a": ([], 0.0),
                "child-b": ([], 0.0),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(
            prompt="Coordinate",
            cwd=str(tmp_path),
            provider_id="fake",
            limits={"max_subagents_total": 1},
        )
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])
        await _approve_pending(manager, store, parent["id"])

        await wait_for_status(store, parent["id"], "completed")
        events = store.events(parent["id"])
        second = next(e for e in events if e["type"] == "tool.finished" and e["payload"]["call_id"] == "s2")
        assert second["payload"]["result"]["ok"] is False
        assert "total limit" in second["payload"]["result"]["error"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_await_subagents_timeout_reports_partial(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter(
            {
                "Coordinate": (
                    [
                        [_fn("s1", "spawn_subagent", task="child-slow")],
                        [_fn("w1", "await_subagents", timeout_s=0)],
                    ],
                    0.0,
                ),
                "child-slow": ([], 30.0),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])

        await wait_for_status(store, parent["id"], "completed")
        events = store.events(parent["id"])
        awaited = next(e for e in events if e["type"] == "tool.finished" and e["payload"]["call_id"] == "w1")
        result = awaited["payload"]["result"]
        assert result["timed_out"] is True and result["ok"] is False
        entry = next(iter(result["children"].values()))
        assert entry["running"] is True and entry["status"] == "running"
        children = [t for t in store.list_tasks() if t.get("parent_id") == parent["id"]]
        manager.cancel(children[0]["id"])
        await manager.close()
        store.close()

    asyncio.run(run())


def test_subagent_handles_rebuilt_from_store(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    parent = store.create_task(
        prompt="parent", cwd=str(tmp_path), provider_id="fake", model="m", limits={}
    )
    child = store.create_task(
        prompt="child", cwd=str(tmp_path), provider_id="fake", model="m",
        limits={}, parent_id=parent["id"],
    )
    store.update_task(child["id"], status="completed", result="all done")
    manager = AgentManager(store, CredentialStore(memory={}), desktop=None, adapter_factory=lambda *_: FakeAdapter())
    handles = manager._subagent_handles(parent["id"])
    assert child["id"] in handles
    handle = handles[child["id"]]
    assert handle.done.is_set()
    assert handle.status == "completed" and handle.result == "all done"
    store.close()


def test_computer_actions_are_replayed_and_takeover_releases_input(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, ComputerAdapter())
        computer = manager._computer
        task = await manager.create_task(prompt="Inspect the desktop", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        completed = await wait_for_status(store, task["id"], "completed")
        assert any(event["type"] == "computer.screenshot" for event in completed["events"])
        assert any(artifact["kind"] == "screenshot" for artifact in completed["artifacts"])

        paused = await manager.create_task(prompt="Pause before acting", cwd=str(tmp_path), provider_id="fake")
        taken_over = await manager.takeover(paused["id"])
        assert taken_over["status"] == "cancelled"
        assert computer.released == 1
        await manager.takeover(paused["id"])
        events = store.events(paused["id"])
        assert len([event for event in events if event["type"] == "control.takeover"]) == 1
        assert len([event for event in events if event["type"] == "task.cancelled"]) == 1
        await manager.close()
        store.close()

    asyncio.run(run())


def test_takeover_preserves_terminal_task_status(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await manager.create_task(prompt="Finish normally", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        completed = await wait_for_status(store, task["id"], "completed")

        taken_over = await manager.takeover(task["id"])

        assert taken_over["status"] == "completed"
        assert taken_over["result"] == completed["result"]
        assert manager._computer.released == 0
        assert not any(event["type"] == "control.takeover" for event in store.events(task["id"]))
        await manager.close()
        store.close()

    asyncio.run(run())


def test_cancel_interrupts_provider_wait_without_overwriting_cancelled_state(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = BlockingProviderAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Wait for the provider", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        await asyncio.wait_for(adapter.started.wait(), timeout=1)
        manager.cancel(task["id"])

        cancelled = await wait_for_status(store, task["id"], "cancelled")
        assert cancelled["error"] == "Cancelled by user"
        assert not any(event["type"] == "task.failed" for event in cancelled["events"])
        await manager.close()
        store.close()

    asyncio.run(run())


def test_computer_function_returns_screenshot_as_follow_up_input(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = ComputerFunctionAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Inspect the desktop", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        completed = await wait_for_status(store, task["id"], "completed")
        assert completed["result"] == "The current screen is visible."
        await manager.close()
        store.close()

    asyncio.run(run())


def test_policy_and_manifest_boundaries(tmp_path: Path) -> None:
    (tmp_path / "visible.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("x", encoding="utf-8")
    manifest = workspace_manifest(str(tmp_path))
    assert "visible.py" in manifest["files"]
    assert ".env" not in manifest["files"]
    assert not any(str(item).startswith("node_modules") for item in manifest["files"])
    assert evaluate_shell("git status", str(tmp_path)).approval_required is False
    assert evaluate_shell("rm -rf /tmp/example", str(tmp_path)).approval_required is True
    assert evaluate_shell("cat ../outside.txt", str(tmp_path)).approval_required is True
    assert evaluate_shell("cat .env", str(tmp_path)).approval_required is True
    assert evaluate_computer([{"type": "type", "text": "password=secret"}]).approval_required is True
    assert "secret-value" not in redact("api_key=secret-value")


def test_provider_call_public_redacts_sensitive_action_text() -> None:
    call = ProviderCall(
        type="computer",
        call_id="computer-secret",
        arguments={"purpose": "Use token=super-secret-value"},
        actions=[{"type": "type", "text": "password=super-secret-value"}],
        safety_checks=[{"message": "Submit api_key=super-secret-value"}],
    )

    public = call.public()

    assert "super-secret-value" not in json.dumps(public)
    assert public["actions"][0]["text"] == "[redacted]"


def test_computer_controller_interrupts_and_executes_key_chords(monkeypatch) -> None:
    async def run() -> None:
        events: list[dict[str, Any]] = []
        controller = ComputerController()

        monkeypatch.setattr(
            "termx.agent.computer.apply_event",
            lambda event, target=None: events.append(event),
        )

        async def screenshot() -> bytes:
            return b"jpeg"

        monkeypatch.setattr(controller, "screenshot", screenshot)
        result = await controller.execute([{"type": "keypress", "keys": ["CTRL", "L"]}])
        assert result == b"jpeg"
        assert events == [
            {
                "type": "key",
                "key": "l",
                "action": "tap",
                "modifiers": ["control"],
            },
        ]

        events.clear()
        cancel = asyncio.Event()
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await controller.execute([{"type": "wait", "seconds": 5}], cancel=cancel)
        assert events == [{"type": "release_all"}]

    asyncio.run(run())


def test_computer_controller_releases_drag_on_cancellation(monkeypatch) -> None:
    async def run() -> None:
        events: list[dict[str, Any]] = []
        cancel = asyncio.Event()
        controller = ComputerController()

        def apply(event: dict[str, Any], target: str | None = None) -> None:
            events.append(event)
            if event.get("action") == "down":
                cancel.set()

        monkeypatch.setattr("termx.agent.computer.apply_event", apply)
        monkeypatch.setattr(
            controller,
            "_display",
            lambda: (None, 100.0, 100.0),
        )

        with pytest.raises(asyncio.CancelledError):
            await controller.execute(
                [{"type": "drag", "path": [{"x": 10, "y": 10}, {"x": 20, "y": 20}]}],
                cancel=cancel,
            )

        assert events[-1] == {"type": "release_all"}

    asyncio.run(run())


def test_computer_controller_stops_typing_before_takeover_release(monkeypatch) -> None:
    async def run() -> None:
        first_character = threading.Event()
        finish_character = threading.Event()
        events: list[dict[str, Any]] = []
        cancel = asyncio.Event()
        controller = ComputerController()

        def apply(event: dict[str, Any], target: str | None = None) -> None:
            events.append(event)
            if event.get("type") == "text":
                first_character.set()
                assert finish_character.wait(timeout=1)

        monkeypatch.setattr("termx.agent.computer.apply_event", apply)
        typing = asyncio.create_task(
            controller.execute([{"type": "type", "text": "long input"}], cancel=cancel)
        )
        assert await asyncio.to_thread(first_character.wait, 1)

        cancel.set()
        release = asyncio.create_task(controller.release_all())
        finish_character.set()

        with pytest.raises(asyncio.CancelledError):
            await typing
        await release

        assert [event for event in events if event.get("type") == "text"] == [
            {"type": "text", "data": "l"}
        ]
        assert events[-1] == {"type": "release_all"}

    asyncio.run(run())


def test_desktop_input_release_all_releases_xtest_buttons_and_modifiers(monkeypatch) -> None:
    class FakeXTest:
        def __init__(self) -> None:
            self.buttons: list[tuple[int, bool]] = []
            self.keys: list[tuple[int, bool]] = []

        def button(self, button: int, pressed: bool) -> None:
            self.buttons.append((button, pressed))

        def keycode(self, name: str) -> int:
            return {"Shift_L": 1, "Control_L": 2, "Alt_L": 3, "Super_L": 4}[name]

        def key(self, code: int, pressed: bool) -> None:
            self.keys.append((code, pressed))

    adapter = FakeXTest()
    monkeypatch.setattr(desktop_input.sys, "platform", "linux")
    monkeypatch.setattr(
        desktop_input,
        "probe_desktop",
        lambda: SimpleNamespace(input_backend="xtest"),
    )
    monkeypatch.setattr(desktop_input, "_xtest", lambda: adapter)
    desktop_input.apply_event({"type": "release_all"})

    assert adapter.buttons == [(1, False), (2, False), (3, False)]
    assert adapter.keys == [(1, False), (2, False), (3, False), (4, False)]


def test_desktop_input_serializes_events_across_threads(monkeypatch) -> None:
    first_started = threading.Event()
    finish_first = threading.Event()
    active = 0
    max_active = 0
    guard = threading.Lock()

    def apply(event: dict[str, Any], target: str | None = None) -> None:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        if event["id"] == 1:
            first_started.set()
            assert finish_first.wait(timeout=1)
        with guard:
            active -= 1

    monkeypatch.setattr(desktop_input, "_apply_event", apply)
    first = threading.Thread(target=desktop_input.apply_event, args=({"id": 1},))
    second = threading.Thread(target=desktop_input.apply_event, args=({"id": 2},))

    first.start()
    assert first_started.wait(timeout=1)
    second.start()
    assert second.is_alive()
    finish_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert max_active == 1


def test_desktop_input_xtest_is_unavailable_without_display(monkeypatch) -> None:
    monkeypatch.setattr(desktop_input.sys, "platform", "linux")
    monkeypatch.setattr(desktop_input, "_xtest_singleton", None)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    assert desktop_input._xtest() is None


def test_desktop_input_release_all_uses_xdotool_fallback(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        desktop_input,
        "probe_desktop",
        lambda: SimpleNamespace(input_backend="xdotool"),
    )
    monkeypatch.setattr(desktop_input, "_xtest", lambda: None)
    monkeypatch.setattr(
        desktop_input.subprocess,
        "run",
        lambda command, check=False: commands.append(command),
    )

    desktop_input.apply_event({"type": "release_all"})

    assert commands == [
        ["xdotool", "mouseup", "1"],
        ["xdotool", "mouseup", "2"],
        ["xdotool", "mouseup", "3"],
        ["xdotool", "keyup", "Shift_L"],
        ["xdotool", "keyup", "Control_L"],
        ["xdotool", "keyup", "Alt_L"],
        ["xdotool", "keyup", "Super_L"],
    ]


def test_desktop_input_release_all_uses_sendinput_on_windows(monkeypatch) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(desktop_input.sys, "platform", "win32")
    monkeypatch.setattr(
        desktop_input,
        "probe_desktop",
        lambda: SimpleNamespace(input_backend="sendinput"),
    )
    monkeypatch.setattr(desktop_input, "_send_inputs", lambda inputs: sent.extend(inputs))

    desktop_input.apply_event({"type": "release_all"})

    assert [item.mi.dwFlags for item in sent[:3]] == [
        desktop_input.MOUSEEVENTF_LEFTUP,
        desktop_input.MOUSEEVENTF_MIDDLEUP,
        desktop_input.MOUSEEVENTF_RIGHTUP,
    ]
    assert [item.ki.wVk for item in sent[3:]] == [
        desktop_input._WIN_VK["shift"],
        desktop_input._WIN_VK["control"],
        desktop_input._WIN_VK["alt"],
        desktop_input._WIN_VK["meta"],
    ]
    assert all(item.ki.dwFlags == desktop_input.KEYEVENTF_KEYUP for item in sent[3:])


def test_desktop_input_releases_character_key_chords(monkeypatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        broker,
        "send_input",
        lambda event: events.append(event) or True,
    )

    assert desktop_input._broker_event(
        {
            "type": "key",
            "key": "l",
            "action": "tap",
            "modifiers": ["meta"],
        },
        None,
    )
    assert events == [
        {
            "kind": "key",
            "key": "l",
            "down": True,
            "modifiers": ["meta"],
        },
        {"kind": "key", "key": "l", "down": False},
    ]


def test_openai_adapter_cancellation_stops_http_request(monkeypatch) -> None:
    async def run() -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()
        client_options: dict[str, object] = {}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, url: str, *, json: dict[str, Any]):
                started.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    stopped.set()

        def fake_client(**kwargs: object) -> FakeClient:
            client_options.update(kwargs)
            return FakeClient()

        monkeypatch.setattr(agent_providers.httpx, "AsyncClient", fake_client)
        adapter = OpenAIResponsesAdapter(
            base_url="https://example.com/v1",
            model="model",
            api_key="secret",
            capabilities=["shell"],
        )

        request = asyncio.create_task(adapter._post({"model": "model"}))
        await asyncio.wait_for(started.wait(), timeout=1)
        request.cancel()

        with pytest.raises(asyncio.CancelledError):
            await request
        await asyncio.wait_for(stopped.wait(), timeout=1)
        assert client_options["follow_redirects"] is True

    asyncio.run(run())


def test_openai_adapter_strips_authorization_on_cross_origin_redirect(monkeypatch) -> None:
    async def run() -> None:
        requests: list[tuple[str, str | None]] = []

        def handler(request: agent_providers.httpx.Request) -> agent_providers.httpx.Response:
            requests.append((str(request.url), request.headers.get("authorization")))
            if request.url.host == "provider.example":
                return agent_providers.httpx.Response(
                    307,
                    headers={"location": "https://attacker.example/v1/responses"},
                )
            return agent_providers.httpx.Response(200, json={"id": "response"})

        original_client = agent_providers.httpx.AsyncClient

        def fake_client(**kwargs: object) -> agent_providers.httpx.AsyncClient:
            return original_client(
                transport=agent_providers.httpx.MockTransport(handler),
                **kwargs,
            )

        monkeypatch.setattr(agent_providers.httpx, "AsyncClient", fake_client)
        adapter = OpenAIResponsesAdapter(
            base_url="https://provider.example/v1",
            model="model",
            api_key="secret",
            capabilities=["shell"],
        )

        assert await adapter._post({"model": "model"}) == {"id": "response"}
        assert requests == [
            ("https://provider.example/v1/responses", "Bearer secret"),
            ("https://attacker.example/v1/responses", None),
        ]

    asyncio.run(run())


def test_openai_adapter_normalizes_text_and_calls(monkeypatch) -> None:
    adapter = OpenAIResponsesAdapter(
        base_url="https://example.com/v1",
        model="model",
        api_key="secret",
        capabilities=["shell", "computer"],
    )

    async def fake_post(_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": "resp",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Working"}]},
                {"type": "function_call", "call_id": "fn", "name": "run_shell", "arguments": '{"command":"pwd","purpose":"inspect"}'},
                {
                    "type": "computer_call",
                    "call_id": "pc",
                    "action": {"type": "click", "x": 1, "y": 2},
                    "pending_safety_checks": [{"id": "check-1", "code": "external_side_effect", "message": "Submits a form"}],
                },
            ],
            "usage": {"input_tokens": 3},
        }

    monkeypatch.setattr(adapter, "_post", fake_post)
    turn = asyncio.run(adapter.turn(prompt="do it", cwd="/tmp", manifest={"files": []}, allow_computer=True))
    assert turn.text == "Working"
    assert [(call.type, call.call_id) for call in turn.calls] == [("function", "fn"), ("computer", "pc")]
    assert turn.calls[0].arguments["command"] == "pwd"
    assert turn.calls[1].actions[0]["type"] == "click"
    assert turn.calls[1].safety_checks[0]["id"] == "check-1"


def test_compatible_adapter_uses_function_computer_tool(monkeypatch) -> None:
    adapter = OpenAIResponsesAdapter(
        base_url="https://example.com/v1",
        model="model",
        api_key="secret",
        capabilities=["shell", "computer"],
        native_computer=False,
    )
    payloads: list[dict[str, Any]] = []

    async def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return {
            "id": "resp",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "pc",
                    "name": "use_computer",
                    "arguments": '{"actions":[{"type":"screenshot"}]}',
                }
            ],
        }

    monkeypatch.setattr(adapter, "_post", fake_post)
    turn = asyncio.run(adapter.turn(prompt="look", cwd="/tmp", manifest={}, allow_computer=True))
    computer_tool = next(tool for tool in payloads[0]["tools"] if tool.get("name") == "use_computer")
    assert computer_tool["type"] == "function"
    assert computer_tool["parameters"]["properties"]["actions"]["maxItems"] == 12
    assert turn.calls == [
        ProviderCall(
            type="computer",
            call_id="pc",
            name="use_computer",
            actions=[{"type": "screenshot"}],
        )
    ]


def test_agent_api_scopes_and_write_only_provider(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    store = AgentStore(tmp_path / "api.sqlite3", tmp_path / "api-artifacts")
    credentials = CredentialStore(memory={})
    state = AppState(
        passcode="secret",
        agent_store=store,
        credentials=credentials,
        adapter_factory=lambda _provider, _key: adapter,
    )
    state.agent._computer = FakeComputer()  # type: ignore[assignment]
    viewer = state.tokens.issue(["agent-view"])
    with TestClient(create_app(state, web_dir=None)) as client:
        assert client.get("/api/agent/providers").status_code == 401
        assert client.get("/api/agent/providers", headers={"Authorization": f"Bearer {viewer}"}).status_code == 200
        denied = client.put(
            "/api/agent/providers/fake",
            headers={"Authorization": f"Bearer {viewer}"},
            json={"id": "fake", "name": "Fake", "model": "fake", "api_key": "do-not-return"},
        )
        assert denied.status_code == 403
        saved = client.put(
            "/api/agent/providers/fake",
            headers={"X-Termx-Passcode": "secret"},
            json={
                "id": "fake",
                "kind": "openai-compatible",
                "name": "Fake",
                "base_url": "http://127.0.0.1:9999/v1",
                "model": "fake",
                "capabilities": ["shell"],
                "api_key": "do-not-return",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["secret_configured"] is True
        assert "do-not-return" not in saved.text
        created = client.post(
            "/api/agent/tasks",
            headers={"X-Termx-Passcode": "secret"},
            json={"prompt": "Check", "cwd": str(tmp_path), "provider_id": "fake"},
        )
        assert created.status_code == 200
        task = created.json()
        assert task["status"] == "awaiting_approval"
        assert task["events"][0]["sequence"] == 1
        replay = client.get(
            f"/api/agent/tasks/{task['id']}",
            headers={"Authorization": f"Bearer {viewer}"},
        )
        assert replay.status_code == 200
        assert [item["sequence"] for item in replay.json()["events"]] == sorted(
            item["sequence"] for item in replay.json()["events"]
        )


def test_selected_model_and_image_attachment(tmp_path: Path) -> None:
    async def run() -> None:
        seen: dict[str, str] = {}

        def factory(provider: dict[str, Any], _key: str) -> FakeAdapter:
            seen["model"] = provider["model"]
            return FakeAdapter()

        store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
        manager = AgentManager(store, CredentialStore(memory={}), desktop=None, adapter_factory=factory)
        manager._computer = FakeComputer()  # type: ignore[assignment]
        manager.save_provider(
            provider_id="fake",
            kind="openai-compatible",
            name="Fake",
            base_url="http://127.0.0.1:9999/v1",
            model="fake-model, other-model",
            capabilities=["shell"],
            api_key="test-key",
        )
        image = base64.b64encode(b"png-bytes").decode("ascii")
        task = await manager.create_task(
            prompt="Look at this",
            cwd=str(tmp_path),
            provider_id="fake",
            mode="ask",
            model="other-model",
            attachments=[{"name": "shot.png", "mime": "image/png", "data": image}],
        )
        assert task["model"] == "other-model"
        assert seen["model"] == "other-model"
        assert any(item["type"] == "user.media" for item in task["events"])
        loaded = store.get_provider("fake")
        assert loaded is not None
        assert loaded["models"] == ["fake-model", "other-model"]
        await manager.close()
        store.close()

    asyncio.run(run())


def _write_call(path: str, content: str = "v1") -> dict[str, Any]:
    return {
        "type": "function",
        "call_id": "call-write-1",
        "name": "write_file",
        "arguments": {"path": path, "content": content, "expected_revision": None},
        "actions": [],
        "safety_checks": [],
    }


def _execution_checkpoint(store: AgentStore, task_id: str, cwd: str, *, state: str,
                          result: dict[str, Any] | None = None,
                          history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    call = _write_call("recovered.txt")
    payload = {
        "task_id": task_id,
        "call": call,
        "remaining_calls": [],
        "history": history if history is not None else [{"role": "user", "content": "resume me"}],
        "step": 0,
        "started_at": 0.0,
    }
    return store.create_checkpoint(
        task_id,
        kind="execution",
        history_cursor=len(payload["history"]),
        plan_step=0,
        pending_call_id=call["call_id"],
        side_effect_state=state,
        payload=payload,
        result=result,
        provider_turn_id="resp-1",
    )


def _crashed_task(store: AgentStore, tmp_path: Path) -> dict[str, Any]:
    task = store.create_task(
        prompt="resume me", cwd=str(tmp_path), provider_id="fake", model="fake-model",
        limits={"max_steps": 24, "max_seconds": 900, "shell_timeout_s": 120}, mode="agent",
    )
    store.update_task(
        task["id"], status="running",
        runtime={"history": [{"role": "user", "content": "resume me"}], "step": 0, "started_at": 0.0},
    )
    return task


def test_recovery_prepared_reexecutes_once(tmp_path: Path) -> None:
    async def run() -> None:
        store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
        credentials = CredentialStore(memory={})
        store.put_provider("fake", kind="openai-compatible", name="Fake",
                           base_url="http://x/v1", model="m", capabilities=["shell"],
                           secret_configured=True)
        task = _crashed_task(store, tmp_path)
        _execution_checkpoint(store, task["id"], str(tmp_path), state="prepared")

        writes: list[str] = []
        adapter = FakeAdapter()
        manager = AgentManager(store, credentials, desktop=None,
                               adapter_factory=lambda _p, _k: adapter)
        spec = manager._tools.get("write_file")
        assert spec is not None
        original = spec.execute
        async def counting(call, ctx):
            writes.append(str(call.arguments.get("path")))
            return await original(call, ctx)
        import dataclasses
        manager._tools.register(dataclasses.replace(spec, execute=counting))
        try:
            assert manager._task(task["id"])["status"] == "recovering"
            assert manager.pending_recoveries() == [task["id"]]
            await manager.recover_task(task["id"])
            await wait_for_status(store, task["id"], "completed", timeout=10.0)
            assert writes == ["recovered.txt"]
            assert (tmp_path / "recovered.txt").read_text() == "v1"
            types = [e["type"] for e in store.events(task["id"])]
            assert "task.recovery.started" in types
            assert "task.recovery.resumed" in types
            assert "execution.checkpoint" in types
            cp = store.get_checkpoint(_execution_checkpoint  # noqa: keep reference
                                      and store.checkpoints(task["id"], kind="execution")[-1]["id"])
            assert cp["side_effect_state"] in {"committed", "completed_uncommitted", "running"}
        finally:
            manager._tools.register(spec)
            await manager.close()
            store.close()

    asyncio.run(run())


def test_recovery_running_requires_confirmation(tmp_path: Path) -> None:
    async def run() -> None:
        store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
        credentials = CredentialStore(memory={})
        store.put_provider("fake", kind="openai-compatible", name="Fake",
                           base_url="http://x/v1", model="m", capabilities=["shell"],
                           secret_configured=True)
        task = _crashed_task(store, tmp_path)
        _execution_checkpoint(store, task["id"], str(tmp_path), state="running")

        adapter = FakeAdapter()
        manager = AgentManager(store, credentials, desktop=None,
                               adapter_factory=lambda _p, _k: adapter)
        writes: list[str] = []
        spec = manager._tools.get("write_file")
        assert spec is not None
        original = spec.execute
        async def counting(call, ctx):
            writes.append(str(call.arguments.get("path")))
            return await original(call, ctx)
        import dataclasses
        manager._tools.register(dataclasses.replace(spec, execute=counting))
        try:
            current = manager._task(task["id"])
            assert current["status"] == "recovery_confirmation_required"
            blocked = await manager.recover_task(task["id"])
            assert blocked["status"] == "recovery_confirmation_required"
            assert writes == []  # nothing replayed without confirmation
            types = [e["type"] for e in store.events(task["id"])]
            assert "task.recovery.blocked" in types
            # Explicit confirmation resumes and re-executes (documented risk).
            await manager.recover_task(task["id"], confirm=True)
            await wait_for_status(store, task["id"], "completed", timeout=10.0)
            assert writes == ["recovered.txt"]
        finally:
            manager._tools.register(spec)
            await manager.close()
            store.close()

    asyncio.run(run())


def test_recovery_completed_uncommitted_reuses_result(tmp_path: Path) -> None:
    async def run() -> None:
        store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
        credentials = CredentialStore(memory={})
        store.put_provider("fake", kind="openai-compatible", name="Fake",
                           base_url="http://x/v1", model="m", capabilities=["shell"],
                           secret_configured=True)
        task = _crashed_task(store, tmp_path)
        stored = {
            "finished": {"call": {"call_id": "call-write-1", "name": "write_file"},
                         "result": {"ok": True, "path": "recovered.txt"}},
            "output_items": [
                {"type": "function_call_output", "call_id": "call-write-1",
                 "output": json.dumps({"ok": True, "path": "recovered.txt"})}
            ],
        }
        _execution_checkpoint(store, task["id"], str(tmp_path),
                              state="completed_uncommitted", result=stored)

        adapter = FakeAdapter()
        manager = AgentManager(store, credentials, desktop=None,
                               adapter_factory=lambda _p, _k: adapter)
        writes: list[str] = []
        spec = manager._tools.get("write_file")
        assert spec is not None
        original = spec.execute
        async def counting(call, ctx):
            writes.append(str(call.arguments.get("path")))
            return await original(call, ctx)
        import dataclasses
        manager._tools.register(dataclasses.replace(spec, execute=counting))
        try:
            await manager.recover_task(task["id"])
            await wait_for_status(store, task["id"], "completed", timeout=10.0)
            assert writes == []  # stored result reused — no re-execution
            assert not (tmp_path / "recovered.txt").exists()
            cp = store.checkpoints(task["id"], kind="execution")[-1]
            assert cp["side_effect_state"] == "committed"
        finally:
            manager._tools.register(spec)
            await manager.close()
            store.close()

    asyncio.run(run())


def test_recovery_committed_continues_without_replay(tmp_path: Path) -> None:
    async def run() -> None:
        store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
        credentials = CredentialStore(memory={})
        store.put_provider("fake", kind="openai-compatible", name="Fake",
                           base_url="http://x/v1", model="m", capabilities=["shell"],
                           secret_configured=True)
        task = _crashed_task(store, tmp_path)
        history = [
            {"role": "user", "content": "resume me"},
            {"type": "function_call", "call_id": "call-write-1", "name": "write_file",
             "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call-write-1",
             "output": json.dumps({"ok": True})},
        ]
        _execution_checkpoint(store, task["id"], str(tmp_path), state="committed",
                              history=history)

        adapter = FakeAdapter()
        manager = AgentManager(store, credentials, desktop=None,
                               adapter_factory=lambda _p, _k: adapter)
        writes: list[str] = []
        spec = manager._tools.get("write_file")
        assert spec is not None
        original = spec.execute
        async def counting(call, ctx):
            writes.append(str(call.arguments.get("path")))
            return await original(call, ctx)
        import dataclasses
        manager._tools.register(dataclasses.replace(spec, execute=counting))
        try:
            await manager.recover_task(task["id"])
            await wait_for_status(store, task["id"], "completed", timeout=10.0)
            assert writes == []  # committed side effects are never replayed
        finally:
            manager._tools.register(spec)
            await manager.close()
            store.close()

    asyncio.run(run())


def test_restart_fails_task_without_checkpoint(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    credentials = CredentialStore(memory={})
    store.put_provider("fake", kind="openai-compatible", name="Fake",
                       base_url="http://x/v1", model="m", capabilities=["shell"],
                       secret_configured=True)
    task = _crashed_task(store, tmp_path)
    adapter = FakeAdapter()
    manager = AgentManager(store, credentials, desktop=None,
                           adapter_factory=lambda _p, _k: adapter)
    current = manager._task(task["id"])
    assert current["status"] == "failed"
    assert current["error"] == "The Termx host stopped before this task completed."


def test_execution_checkpoint_events_during_normal_run(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await manager.create_task(prompt="Check the project", cwd=str(tmp_path),
                                         provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        checkpoints = store.checkpoints(task["id"], kind="execution")
        assert len(checkpoints) == 1  # one side-effecting run_shell call
        assert checkpoints[0]["side_effect_state"] == "committed"
        assert checkpoints[0]["provider_turn_id"] == "response-1"
        # Events fire only at the prepared/committed boundaries; the row
        # still records every transition for crash classification.
        states = [
            e["payload"]["side_effect_state"]
            for e in store.events(task["id"])
            if e["type"] == "execution.checkpoint"
        ]
        assert states == ["prepared", "committed"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_recovery_completed_uncommitted_dedupes_finished_event(tmp_path: Path) -> None:
    async def run() -> None:
        store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
        credentials = CredentialStore(memory={})
        store.put_provider("fake", kind="openai-compatible", name="Fake",
                           base_url="http://x/v1", model="m", capabilities=["shell"],
                           secret_configured=True)
        task = _crashed_task(store, tmp_path)
        stored = {
            "finished": {"call_id": "call-write-1",
                         "result": {"ok": True, "path": "recovered.txt"}},
            "output_items": [
                {"type": "function_call_output", "call_id": "call-write-1",
                 "output": json.dumps({"ok": True, "path": "recovered.txt"})}
            ],
        }
        _execution_checkpoint(store, task["id"], str(tmp_path),
                              state="completed_uncommitted", result=stored)
        # Crash happened after tool.finished was recorded but before the
        # checkpoint reached committed — resume must not re-emit it.
        store.append_event(task["id"], "tool.finished", stored["finished"])

        adapter = FakeAdapter()
        manager = AgentManager(store, credentials, desktop=None,
                               adapter_factory=lambda _p, _k: adapter)
        await manager.recover_task(task["id"])
        await wait_for_status(store, task["id"], "completed", timeout=10.0)
        finished = [
            e for e in store.events(task["id"])
            if e["type"] == "tool.finished"
            and e["payload"].get("call_id") == "call-write-1"
        ]
        assert len(finished) == 1
        cp = store.checkpoints(task["id"], kind="execution")[-1]
        assert cp["side_effect_state"] == "committed"
        await manager.close()
        store.close()

    asyncio.run(run())


class _FlakyStreamAdapter:
    """Streams a delta then dies — retrying would duplicate visible text."""

    def __init__(self) -> None:
        self.supports_streaming = True
        self.calls = 0

    async def test(self) -> str:
        return "OK"

    async def plan(self, prompt: str, cwd: str, manifest: dict[str, Any]):
        return (
            {"summary": prompt, "steps": ["Inspect", "Change", "Verify"],
             "tools": ["shell"], "risks": []},
            "plan-response",
        )

    async def stream_turn(self, **kwargs):
        self.calls += 1
        on_delta = kwargs.get("on_delta")
        if on_delta:
            on_delta("partial visible text ")
        raise ProviderError("connection reset", network=True)

    async def turn(self, **kwargs):  # pragma: no cover - streaming path only
        raise AssertionError("turn() should not be called for streaming adapters")


def test_stream_retry_suppressed_after_visible_delta(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FlakyStreamAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Stream something", cwd=str(tmp_path),
                                       provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        failed = await wait_for_status(store, task["id"], "failed", timeout=10.0)
        assert "connection reset" in (failed.get("error") or "")
        assert adapter.calls == 1  # no silent replay of a visible stream
        events = store.events(task["id"])
        types = [e["type"] for e in events]
        assert "provider.stream.started" in types
        assert "provider.stream.aborted" in types
        assert "provider.retry" not in types
        aborted = next(e for e in events if e["type"] == "provider.stream.aborted")
        assert aborted["payload"]["stream_id"]
        assert aborted["payload"]["attempt"] == 1
        assert aborted["payload"]["reason_class"] == "retryable"
        delta = next(e for e in events if e["type"] == "assistant.delta")
        assert delta["payload"]["text"] == "partial visible text "
        assert delta["payload"]["stream_id"] == aborted["payload"]["stream_id"]
        assert delta["payload"]["attempt"] == 1
        failed_event = next(e for e in events if e["type"] == "provider.failed")
        assert failed_event["payload"].get("retry_suppressed") is True
        await manager.close()
        store.close()

    asyncio.run(run())


def test_stream_retry_allowed_before_first_delta(tmp_path: Path) -> None:
    async def run() -> None:
        class EarlyFail(_FlakyStreamAdapter):
            async def stream_turn(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise ProviderError("connection reset", network=True)
                on_delta = kwargs.get("on_delta")
                if on_delta:
                    on_delta("hello")
                return ProviderTurn(response_id="resp-x", text="hello", calls=[], usage={},
                                    output_items=[{"type": "message",
                                                   "content": [{"type": "output_text",
                                                                "text": "hello"}]}])

        adapter = EarlyFail()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Stream something", cwd=str(tmp_path),
                                       provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        await wait_for_status(store, task["id"], "completed", timeout=10.0)
        assert adapter.calls == 2  # pre-delta failure retried once, then succeeded
        events = store.events(task["id"])
        types = [e["type"] for e in events]
        assert "provider.retry" in types
        assert "provider.stream.aborted" in types
        deltas = [e["payload"] for e in events if e["type"] == "assistant.delta"]
        assert len(deltas) == 1 and deltas[0]["attempt"] == 2
        started = [e["payload"] for e in events if e["type"] == "provider.stream.started"]
        assert [s["attempt"] for s in started] == [1, 2]
        await manager.close()
        store.close()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Phase 4 — Observation v2 + richer computer actions (AG2-015 / AG2-016)


class ComputerDedupAdapter(FakeAdapter):
    """Two use_computer calls then a text turn; records the final input."""

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
    ) -> ProviderTurn:
        self.inputs.append(input_items)
        self.turns += 1
        if self.turns <= 2:
            call = ProviderCall(
                type="computer",
                call_id=f"computer-{self.turns}",
                name="use_computer",
                actions=[{"type": "screenshot"}],
            )
            return ProviderTurn(
                response_id=f"dedup-response-{self.turns}",
                text="",
                calls=[call],
                usage={},
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": "use_computer",
                        "arguments": json.dumps({"actions": call.actions}),
                    }
                ],
            )
        self.final_items = input_items
        return ProviderTurn(
            response_id="dedup-response-3",
            text="Done.",
            calls=[],
            usage={},
            output_items=[
                {"type": "message", "content": [{"type": "output_text", "text": "Done."}]}
            ],
        )


def test_jpeg_size_parses_sof_marker() -> None:
    from termx.agent.observation import jpeg_size

    frame = (
        b"\xff\xd8"  # SOI
        + b"\xff\xe0" + (16).to_bytes(2, "big") + b"\x00" * 14  # APP0
        + b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08"
        + (480).to_bytes(2, "big") + (640).to_bytes(2, "big")
        + b"\x03" + b"\x00" * 6
    )
    assert jpeg_size(frame) == (640, 480)
    assert jpeg_size(b"jpeg") is None
    assert jpeg_size(b"") is None


def test_frame_identity_dedupes_identical_frames() -> None:
    from termx.agent.observation import ObservationTracker, frame_identity

    kind, digest = frame_identity(b"jpeg")
    assert kind == "sha256" and len(digest) == 16
    tracker = ObservationTracker()
    first = tracker.record(
        frame=b"jpeg", display_id="1", width=10, height=10, dpr=1.0, backend="fake"
    )
    second = tracker.record(
        frame=b"jpeg", display_id="1", width=10, height=10, dpr=1.0, backend="fake"
    )
    third = tracker.record(
        frame=b"jpeg2", display_id="1", width=10, height=10, dpr=1.0, backend="fake"
    )
    assert first.changed is True
    assert second.changed is False
    assert third.changed is True
    payload = second.payload()
    assert payload["previous_id"] == first.id
    assert payload["age_ms"] >= 0


def test_screenshot_region_parsed_from_actions() -> None:
    from termx.agent.manager import _screenshot_region

    assert _screenshot_region([{"type": "click"}, {"type": "screenshot"}]) is None
    assert _screenshot_region(
        [{"type": "screenshot", "region": {"x": 1.7, "y": 2, "width": 100, "height": 50}}]
    ) == {"x": 1, "y": 2, "width": 100, "height": 50}
    assert (
        _screenshot_region(
            [{"type": "screenshot", "region": {"x": -5, "y": "bad", "width": 0, "height": 20}}]
        )
        is None
    )
    assert _screenshot_region(
        [{"type": "screenshot", "region": {"x": -5, "y": 0, "width": 10, "height": 10}}]
    ) == {"x": 0, "y": 0, "width": 10, "height": 10}


def test_computer_observation_schema_and_batch_events(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, ComputerAdapter())
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        events = store.events(task["id"])
        started = next(e for e in events if e["type"] == "computer.action.started")
        finished = next(e for e in events if e["type"] == "computer.action.finished")
        assert started["payload"]["batch_id"] == finished["payload"]["batch_id"]
        assert started["payload"]["actions"] == ["click"]
        assert finished["payload"]["ok"] is True
        obs_event = next(e for e in events if e["type"] == "computer.observation")
        assert obs_event["payload"]["batch_id"] == started["payload"]["batch_id"]
        observation = obs_event["payload"]["observation"]
        for field in (
            "id",
            "display_id",
            "width",
            "height",
            "dpr",
            "captured_at",
            "age_ms",
            "artifact_id",
            "frame_hash",
            "changed",
            "control_owner",
            "capture_backend",
        ):
            assert field in observation, field
        assert observation["changed"] is True
        assert observation["control_owner"] == "agent"
        artifact = store.get_artifact(task["id"], observation["artifact_id"])
        assert artifact is not None and artifact["kind"] == "screenshot"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_computer_no_change_suppresses_duplicate_frame(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = ComputerDedupAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Look twice", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        events = store.events(task["id"])
        observations = [e for e in events if e["type"] == "computer.observation"]
        assert len(observations) == 2
        assert observations[0]["payload"]["observation"]["changed"] is True
        assert observations[1]["payload"]["observation"]["changed"] is False
        no_change = [e for e in events if e["type"] == "computer.no_change"]
        assert len(no_change) == 1
        # The second frame produced a text-only follow-up — no input_image item.
        final_items = getattr(adapter, "final_items", None) or []
        second_output = next(
            index
            for index, item in enumerate(final_items)
            if item.get("type") == "function_call_output"
            and item.get("call_id") == "computer-2"
        )
        trailing = final_items[second_output + 1 :]
        assert not any(
            content.get("type") == "input_image"
            for item in trailing
            if item.get("role") == "user"
            for content in item.get("content", [])
        )
        assert any(
            "unchanged" in str(content.get("text") or "").lower()
            for item in final_items
            if item.get("role") == "user"
            for content in item.get("content", [])
        )
        # Both frames are still persisted as artifacts for replay.
        artifacts = [a for a in store.artifacts(task["id"]) if a["kind"] == "screenshot"]
        assert len(artifacts) == 2
        await manager.close()
        store.close()

    asyncio.run(run())


def test_computer_control_changed_on_takeover(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, ComputerAdapter())
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake")
        # Takeover while the plan approval is pending — an active status.
        await manager.takeover(task["id"])
        changed = [
            e for e in store.events(task["id"]) if e["type"] == "computer.control.changed"
        ]
        assert changed[-1]["payload"]["owner"] == "user"
        # The next agent batch on a fresh task reasserts agent control.
        task2 = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task2["id"], task2["approvals"][0]["id"], "approved")
        await wait_for_status(store, task2["id"], "completed")
        back = [
            e for e in store.events(task2["id"]) if e["type"] == "computer.control.changed"
        ]
        assert back[-1]["payload"]["owner"] == "agent"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_computer_region_screenshot_records_region(tmp_path: Path) -> None:
    class RegionAdapter(FakeAdapter):
        async def turn(self, **kwargs: Any) -> ProviderTurn:
            self.turns += 1
            self.inputs.append(kwargs.get("input_items"))
            if self.turns == 1:
                call = ProviderCall(
                    type="computer",
                    call_id="region-1",
                    name="use_computer",
                    actions=[
                        {
                            "type": "screenshot",
                            "region": {"x": 0, "y": 0, "width": 50, "height": 50},
                        }
                    ],
                )
                return ProviderTurn(
                    response_id="region-response-1",
                    text="",
                    calls=[call],
                    usage={},
                    output_items=[
                        {
                            "type": "function_call",
                            "call_id": call.call_id,
                            "name": "use_computer",
                            "arguments": json.dumps({"actions": call.actions}),
                        }
                    ],
                )
            return ProviderTurn(
                response_id="region-response-2",
                text="Done.",
                calls=[],
                usage={},
                output_items=[
                    {"type": "message", "content": [{"type": "output_text", "text": "Done."}]}
                ],
            )

    async def run() -> None:
        manager, store = build_manager(tmp_path, RegionAdapter())
        task = await manager.create_task(prompt="Region", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        obs_event = next(
            e for e in store.events(task["id"]) if e["type"] == "computer.observation"
        )
        observation = obs_event["payload"]["observation"]
        assert observation["region"] == {"x": 0, "y": 0, "width": 50, "height": 50}
        # The fake frame is undecodable, so hosts serve the full frame honestly.
        assert observation["region_cropped"] is False
        await manager.close()
        store.close()

    asyncio.run(run())


def test_machine_snapshot_reports_observation_v2(tmp_path: Path) -> None:
    from termx.config import ConfigStore
    from termx.machine import machine_snapshot

    snapshot = machine_snapshot(ConfigStore(tmp_path / "config.json"))
    assert snapshot["capabilities"]["computer_observation_v2"] is True
    assert snapshot["capabilities"]["agent_subagents"] is True


def test_paste_text_uses_clipboard_chord(monkeypatch: pytest.MonkeyPatch) -> None:
    from termx.agent import computer as computer_module

    events: list[dict[str, Any]] = []
    clipboard: dict[str, str] = {}
    monkeypatch.setattr(
        computer_module,
        "list_displays",
        lambda: [{"id": "1", "main": True, "width": 100, "height": 100}],
    )
    monkeypatch.setattr(computer_module, "pointer_target", lambda _d: None)
    monkeypatch.setattr(computer_module, "apply_event", lambda e, *_a, **_k: events.append(e))
    monkeypatch.setattr(
        computer_module, "clipboard_set", lambda text: clipboard.__setitem__("v", text)
    )
    monkeypatch.setattr(computer_module, "clipboard_get", lambda: clipboard.get("v"))

    controller = ComputerController()
    asyncio.run(controller._action({"type": "paste_text", "text": "long text here"}))
    assert clipboard["v"] == "long text here"
    assert events == [
        {
            "type": "key",
            "key": "v",
            "action": "tap",
            "modifiers": ["control"] if sys.platform != "darwin" else ["meta"],
        }
    ]


def test_paste_text_falls_back_to_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    from termx.agent import computer as computer_module

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        computer_module,
        "list_displays",
        lambda: [{"id": "1", "main": True, "width": 100, "height": 100}],
    )
    monkeypatch.setattr(computer_module, "pointer_target", lambda _d: None)
    monkeypatch.setattr(computer_module, "apply_event", lambda e, *_a, **_k: events.append(e))
    monkeypatch.setattr(computer_module, "clipboard_set", lambda _t: None)
    monkeypatch.setattr(computer_module, "clipboard_get", lambda: None)

    controller = ComputerController()
    asyncio.run(controller._action({"type": "paste_text", "text": "ab"}))
    assert events == [
        {"type": "text", "data": "a"},
        {"type": "text", "data": "b"},
    ]


def test_mouse_and_key_hold_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    from termx.agent import computer as computer_module

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        computer_module,
        "list_displays",
        lambda: [{"id": "1", "main": True, "width": 100, "height": 100}],
    )
    monkeypatch.setattr(computer_module, "pointer_target", lambda _d: None)
    monkeypatch.setattr(computer_module, "apply_event", lambda e, *_a, **_k: events.append(e))

    controller = ComputerController()

    async def run() -> None:
        await controller._action({"type": "mouse_down", "x": 10, "y": 20, "button": "left"})
        await controller._action({"type": "mouse_up", "x": 10, "y": 20})
        await controller._action({"type": "key_down", "keys": ["control"]})
        await controller._action({"type": "key_up", "keys": ["a"]})
        await controller._action({"type": "release_all"})
        await controller._action({"type": "set_display", "display_id": "7"})

    asyncio.run(run())
    assert events[0] == {
        "type": "pointer",
        "action": "down",
        "x": 0.1,
        "y": 0.2,
        "button": 1,
    }
    assert events[1]["action"] == "up"
    assert events[2] == {"type": "key", "key": "control", "action": "down", "modifiers": ["control"]}
    assert events[3]["action"] == "up"
    assert events[4] == {"type": "release_all"}
    assert controller.display_id == "7"


def test_paste_text_secret_scanned_like_type() -> None:
    decision = evaluate_computer(
        [{"type": "paste_text", "text": "password=super-secret-123"}]
    )
    assert decision.approval_required is True
