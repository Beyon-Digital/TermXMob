from __future__ import annotations

import asyncio
import base64
import json
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
    def __init__(self) -> None:
        self.released = 0
        self.actions: list[list[dict[str, Any]]] = []

    async def execute(
        self,
        actions: list[dict[str, Any]],
        *,
        cancel: asyncio.Event | None = None,
    ) -> bytes:
        self.actions.append(actions)
        return b"jpeg"

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

        paused, escalated = await wait_for_pending_approval(store, parent["id"], "tool")
        assert paused["status"] == "awaiting_approval"
        assert escalated["payload"]["title"] == "publisher: External publication"
        child_id = escalated["payload"]["child_id"]
        child = store.get_task(child_id, include_events=True)
        assert child["status"] == "awaiting_approval"
        assert all(a["status"] != "pending" or a["kind"] == "tool" for a in child["approvals"])

        await manager.resolve_approval(parent["id"], escalated["id"], "denied")
        completed = await wait_for_status(store, parent["id"], "completed")
        assert store.get_task(child_id)["status"] == "cancelled"
        finished = next(e for e in completed["events"] if e["type"] == "subagent.finished")
        assert finished["payload"]["status"] == "cancelled"
        assert any(e["type"] == "subagent.event" and e["payload"]["type"] == "task.status" for e in completed["events"])
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
