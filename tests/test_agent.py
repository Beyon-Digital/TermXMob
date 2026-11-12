from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from _gql import data, err_status, gql

from termx.agent import providers as agent_providers
from termx.agent.context import workspace_manifest
from termx.agent.manager import AgentManager
from termx.agent.policy import evaluate_shell, redact
from termx.agent.providers import OpenAIResponsesAdapter, ProviderCall, ProviderError, ProviderTurn, _parse_plan
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore
from termx.app import AppState, create_app
from termx.desktop import broker, input as desktop_input




def test_parse_plan_rejects_tool_markup_and_honors_requested_step_count() -> None:
    prompt = "Use the computer to inspect the desktop in exactly two safe steps."
    plan = _parse_plan(
        "<tool_call>computer\n<arg_key>action</arg_key>\n<arg_value>screenshot</arg_value>\n</tool_call>",
        prompt,
    )

    assert plan["summary"] == prompt
    assert plan["steps"] == [
        "Look at the application with desktop_snapshot",
        "Verify and report the requested result",
    ]
    assert plan["tools"] == ["shell"]


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
            "Look at the application with desktop_snapshot",
            "Verify and report the requested result",
        ],
        "tools": ["computer"],
        "risks": [],
    }


def test_parse_plan_fallback_points_screen_requests_at_the_desktop_tools_and_keeps_waits() -> None:
    prompt = "Take a screenshot of the current desktop, then wait for 15 seconds in exactly two safe steps."
    plan = _parse_plan("<tool_call>shell</tool_call>", prompt)

    assert plan["steps"] == [
        "Look at the application with desktop_snapshot",
        "Wait for 15 seconds while keeping the task interruptible",
    ]
    assert "screenshot" not in " ".join(plan["steps"]).lower()


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
        read_only: bool = False,
        allow_subagents: bool = True,
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
        read_only: bool = False,
        allow_subagents: bool = True,
    ) -> ProviderTurn:
        self.started.set()
        await asyncio.sleep(30)
        raise ProviderError("Provider request failed (404): The provider returned an error.")


async def wait_for_status(store: AgentStore, task_id: str, status: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        task = store.get_task(task_id, include_events=True)
        if task and task["status"] == status:
            return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}")


async def wait_for_event(
    store: AgentStore, task_id: str, event_type: str, timeout: float = 15.0
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        matched = [e for e in store.events(task_id) if e["type"] == event_type]
        if matched:
            return matched
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} never emitted {event_type}")


async def wait_for_pending_approval(store: AgentStore, task_id: str, kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + 15
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
    from termx.sandbox.host import HostSandboxRunner

    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    credentials = CredentialStore(memory={})
    # Pin the host backend: these tests assert policy semantics against the
    # unrestricted envelope and must not depend on the machine's namespace
    # support (linux-ns would change capability-ask behavior on Linux).
    manager = AgentManager(
        store,
        credentials,
        desktop=None,
        adapter_factory=lambda _provider, _key: adapter,
        runner_for=lambda profile, **kw: HostSandboxRunner(profile=profile),
    )
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


def test_approved_tool_keeps_secrets_out_of_the_pending_approval(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter("curl -d token=super-secret-value http://127.0.0.1:9/x"))
        task = await manager.create_task(prompt="Enter the value", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")

        assert "super-secret-value" not in json.dumps(pending["payload"])
        await manager.resolve_approval(task["id"], pending["id"], "denied")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_approved_tool_without_private_call_is_an_approval_conflict(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter("curl -d token=super-secret-value http://127.0.0.1:9/x"))
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
        read_only: bool = False,
        allow_subagents: bool = True,
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
    children consume independent scripts. Delays model slow children; tests
    that require overlapping execution must synchronize the child turns.
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
        read_only: bool = False,
        allow_subagents: bool = True,
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
        child_turns_started: set[str] = set()
        both_children_running = asyncio.Event()

        class ConcurrentAdapter(_FanOutAdapter):
            async def turn(self, **kwargs: Any) -> ProviderTurn:
                for child in ("child-a", "child-b"):
                    if child in kwargs["prompt"]:
                        child_turns_started.add(child)
                        if len(child_turns_started) == 2:
                            both_children_running.set()
                        # Separate spawn approvals can take arbitrarily longer
                        # than a fixture sleep on CI. Neither child may finish
                        # until both are executing; a serial spawn still fails.
                        await asyncio.wait_for(both_children_running.wait(), timeout=10)
                        break
                return await super().turn(**kwargs)

        adapter = ConcurrentAdapter(
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
                "child-a": ([], 0.0),
                "child-b": ([], 0.0),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])
        await _approve_pending(manager, store, parent["id"])

        await wait_for_status(store, parent["id"], "completed")
        children = [t for t in store.list_tasks() if t.get("parent_id") == parent["id"]]
        assert child_turns_started == {"child-a", "child-b"}
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


def test_legacy_subagent_parallel_quota_does_not_block_spawn(tmp_path: Path) -> None:
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
        assert second["payload"]["result"]["ok"] is True
        children = [t for t in store.list_tasks() if t.get("parent_id") == parent["id"]]
        assert len(children) == 2
        for child in children:
            manager.cancel(child["id"])
        await manager.close()
        store.close()

    asyncio.run(run())


def test_legacy_subagent_total_quota_does_not_block_spawn(tmp_path: Path) -> None:
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
        assert second["payload"]["result"]["ok"] is True
        assert len(store.children(parent["id"])) == 2
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




def test_takeover_preserves_terminal_task_status(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await manager.create_task(prompt="Finish normally", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        completed = await wait_for_status(store, task["id"], "completed")

        taken_over = await manager.takeover(task["id"])

        assert taken_over["status"] == "completed"
        assert taken_over["result"] == completed["result"]
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
    assert "secret-value" not in redact("api_key=secret-value")










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
                {"type": "computer_call", "call_id": "pc", "action": {"type": "click", "x": 1, "y": 2}},
            ],
            "usage": {"input_tokens": 3},
        }

    monkeypatch.setattr(adapter, "_post", fake_post)
    turn = asyncio.run(adapter.turn(prompt="do it", cwd="/tmp", manifest={"files": []}))
    assert turn.text == "Working"
    # A native computer_call from an old conversation is ignored: there is no screenshot loop to answer it.
    assert [(call.type, call.call_id) for call in turn.calls] == [("function", "fn")]
    assert turn.calls[0].arguments["command"] == "pwd"




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
    viewer = state.tokens.issue(["agent-view"])
    with TestClient(create_app(state, web_dir=None)) as client:
        assert err_status(client, "{ agent_providers { id } }") == 401
        data(client, "{ agent_providers { id } }", headers={"Authorization": f"Bearer {viewer}"})
        denied = gql(
            client,
            "mutation($input: AgentProviderInput!) { save_agent_provider(input: $input) { id } }",
            {"input": {"id": "fake", "name": "Fake", "model": "fake", "api_key": "do-not-return"}},
            {"Authorization": f"Bearer {viewer}"},
        )
        assert denied.json()["errors"][0]["extensions"]["http_status"] == 403
        saved = gql(
            client,
            "mutation($input: AgentProviderInput!) { save_agent_provider(input: $input) { secret_configured } }",
            {
                "input": {
                    "id": "fake",
                    "kind": "openai-compatible",
                    "name": "Fake",
                    "base_url": "http://127.0.0.1:9999/v1",
                    "model": "fake",
                    "capabilities": ["shell"],
                    "api_key": "do-not-return",
                }
            },
            {"X-Termx-Passcode": "secret"},
        )
        assert saved.json()["data"]["save_agent_provider"]["secret_configured"] is True
        assert "do-not-return" not in saved.text
        task = data(
            client,
            "mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id status events } }",
            "create_agent_task",
            {"input": {"prompt": "Check", "cwd": str(tmp_path), "provider_id": "fake"}},
            headers={"X-Termx-Passcode": "secret"},
        )
        assert task["status"] == "awaiting_approval"
        assert task["events"][0]["sequence"] == 1
        replay = data(
            client,
            "query($id: String!) { agent_task(task_id: $id) { events } }",
            "agent_task",
            {"id": task["id"]},
            headers={"Authorization": f"Bearer {viewer}"},
        )
        assert [item["sequence"] for item in replay["events"]] == sorted(
            item["sequence"] for item in replay["events"]
        )


def test_selected_model_and_image_attachment(tmp_path: Path) -> None:
    async def run() -> None:
        seen: dict[str, str] = {}

        def factory(provider: dict[str, Any], _key: str) -> FakeAdapter:
            seen["model"] = provider["model"]
            return FakeAdapter()

        store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
        manager = AgentManager(store, CredentialStore(memory={}), desktop=None, adapter_factory=factory)
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
# Phase 4 — machine capabilities




























# ---------------------------------------------------------------------------
# PROD-001 / PROD-002 — host-persisted conversations + custom agents


def _conversation_state(tmp_path: Path) -> AppState:
    adapter = FakeAdapter()
    store = AgentStore(tmp_path / "api.sqlite3", tmp_path / "api-artifacts")
    credentials = CredentialStore(memory={})
    state = AppState(
        passcode="secret",
        agent_store=store,
        credentials=credentials,
        adapter_factory=lambda _provider, _key: adapter,
    )
    return state


def test_conversations_crud_and_turns(tmp_path: Path) -> None:
    state = _conversation_state(tmp_path)
    with TestClient(create_app(state, web_dir=None)) as client:
        headers = {"x-termx-passcode": "secret"}
        assert err_status(client, "{ conversations { id } }") == 401
        created = data(
            client,
            "mutation($input: ConversationInput!) { create_conversation(input: $input) { id title pinned draft } }",
            "create_conversation",
            {"input": {"title": "Thread A", "cwd": "/tmp", "mode": "agent", "pinned": True}},
            headers=headers,
        )
        assert created["title"] == "Thread A"
        assert created["pinned"] is True and created["draft"] is False
        listed = data(client, "{ conversations { id } }", "conversations", headers=headers)
        assert [c["id"] for c in listed] == [created["id"]]
        turn = data(
            client,
            "mutation($id: String!, $input: ConversationTurnInput!) { add_conversation_turn(conversation_id: $id, input: $input) { sequence task_id context_refs attachment_refs } }",
            "add_conversation_turn",
            {"id": created["id"], "input": {
                "prompt": "fix the bug",
                "task_id": "task-1",
                "context_refs": [{"ref": "src/a.py", "start_line": 3}],
                "attachment_refs": [{"ref": "shot.png"}],
            }},
            headers=headers,
        )
        assert turn["sequence"] == 1 and turn["task_id"] == "task-1"
        assert turn["context_refs"][0]["meta"] == {"start_line": 3}
        assert turn["attachment_refs"][0]["ref"] == "shot.png"
        fetched = data(
            client,
            "query($id: String!) { conversation(conversation_id: $id) { turns } }",
            "conversation", {"id": created["id"]}, headers=headers,
        )
        assert fetched["turns"][0]["prompt"] == "fix the bug"
        patched = data(
            client,
            "mutation($id: String!, $input: ConversationPatchInput!) { patch_conversation(conversation_id: $id, input: $input) { archived title } }",
            "patch_conversation",
            {"id": created["id"], "input": {"archived": True, "title": "Renamed"}},
            headers=headers,
        )
        assert patched["archived"] is True and patched["title"] == "Renamed"
        assert data(client, "{ conversations { id } }", "conversations", headers=headers) == []
        assert data(
            client, '{ conversations(archived: "all") { id } }', "conversations", headers=headers
        )[0]["id"] == created["id"]
        data(
            client,
            "mutation($id: String!) { delete_conversation(conversation_id: $id) { deleted } }",
            "delete_conversation", {"id": created["id"]}, headers=headers,
        )
        assert (
            err_status(
                client,
                "query($id: String!) { conversation(conversation_id: $id) { id } }",
                {"id": created["id"]}, headers=headers,
            )
            == 404
        )


def test_conversation_turn_links_task_creation(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    store = AgentStore(tmp_path / "api.sqlite3", tmp_path / "api-artifacts")
    credentials = CredentialStore(memory={})
    state = AppState(
        passcode="secret",
        agent_store=store,
        credentials=credentials,
        adapter_factory=lambda _provider, _key: adapter,
    )
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        conv = data(
            client,
            "mutation($input: ConversationInput!) { create_conversation(input: $input) { id } }",
            "create_conversation", {"input": {"title": "T"}}, headers=headers,
        )
        missing = gql(
            client,
            "mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id } }",
            {"input": {
                "prompt": "do it",
                "cwd": str(tmp_path),
                "provider_id": "missing-conv-test",
                "conversation_id": "nope",
            }},
            headers,
        )
        assert missing.json()["errors"][0]["extensions"]["http_status"] == 404
        state.agent.save_provider(
            provider_id="fake",
            kind="openai-compatible",
            name="Fake",
            base_url="http://127.0.0.1:9/v1",
            model="m",
            capabilities=["shell"],
        )
        task = data(
            client,
            "mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id } }",
            "create_agent_task",
            {"input": {
                "prompt": "do it",
                "cwd": str(tmp_path),
                "provider_id": "fake",
                "conversation_id": conv["id"],
            }},
            headers=headers,
        )
        convo = data(
            client,
            "query($id: String!) { conversation(conversation_id: $id) { turns } }",
            "conversation", {"id": conv["id"]}, headers=headers,
        )
        assert convo["turns"][0]["task_id"] == task["id"]
        assert convo["turns"][0]["prompt"] == "do it"


def test_task_records_display_turn_fields(tmp_path: Path) -> None:
    """A composed agent prompt must not leak into the stored turn: the turn
    keeps the user's original text plus structured context refs."""
    adapter = FakeAdapter()
    store = AgentStore(tmp_path / "api.sqlite3", tmp_path / "api-artifacts")
    credentials = CredentialStore(memory={})
    state = AppState(
        passcode="secret",
        agent_store=store,
        credentials=credentials,
        adapter_factory=lambda _provider, _key: adapter,
    )
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        conv = data(
            client,
            "mutation($input: ConversationInput!) { create_conversation(input: $input) { id } }",
            "create_conversation", {"input": {"title": "T"}}, headers=headers,
        )
        state.agent.save_provider(
            provider_id="fake",
            kind="openai-compatible",
            name="Fake",
            base_url="http://127.0.0.1:9/v1",
            model="m",
            capabilities=["shell"],
        )
        task = data(
            client,
            "mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id } }",
            "create_agent_task",
            {"input": {
                "prompt": "instructions preface\n\nSummarize this\n\n--- a.py lines 1-2 ---\nx = 1",
                "cwd": str(tmp_path),
                "provider_id": "fake",
                "conversation_id": conv["id"],
                "turn_prompt": "Summarize this",
                "context_refs": [{"ref": "a.py", "meta": {"path": "a.py", "cwd": "/w", "start": 1, "end": 2}}],
            }},
            headers=headers,
        )
        turn = data(
            client,
            "query($id: String!) { conversation(conversation_id: $id) { turns } }",
            "conversation", {"id": conv["id"]}, headers=headers,
        )["turns"][0]
        assert turn["task_id"] == task["id"]
        assert turn["prompt"] == "Summarize this"
        assert turn["context_refs"][0]["ref"] == "a.py"
        # Structured refs round-trip: meta contents surface at the top level so
        # syncing clients read meta.start/meta.end/meta.cwd directly.
        ref_meta = turn["context_refs"][0]["meta"]
        assert ref_meta["start"] == 1
        assert ref_meta["end"] == 2
        assert ref_meta["cwd"] == "/w"


def test_custom_agents_crud(tmp_path: Path) -> None:
    state = _conversation_state(tmp_path)
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        assert err_status(client, "{ custom_agents { id } }") == 401
        agent = data(
            client,
            "mutation($input: CustomAgentInput!) { create_custom_agent(input: $input) { id name tools limits } }",
            "create_custom_agent",
            {"input": {
                "name": "Reviewer",
                "instructions": "review every diff",
                "tools": ["git_status", "git_diff"],
                "limits": {"max_steps": 10},
            }},
            headers=headers,
        )
        assert agent["name"] == "Reviewer"
        assert agent["tools"] == ["git_status", "git_diff"]
        assert agent["limits"] == {"max_steps": 10}
        patched = data(
            client,
            "mutation($id: String!, $input: CustomAgentPatchInput!) { patch_custom_agent(agent_id: $id, input: $input) { description } }",
            "patch_custom_agent",
            {"id": agent["id"], "input": {"description": "code reviewer"}},
            headers=headers,
        )
        assert patched["description"] == "code reviewer"
        listed = data(client, "{ custom_agents { id } }", "custom_agents", headers=headers)
        assert listed[0]["id"] == agent["id"]
        data(
            client,
            "mutation($id: String!) { delete_custom_agent(agent_id: $id) { deleted } }",
            "delete_custom_agent", {"id": agent["id"]}, headers=headers,
        )
        assert data(client, "{ custom_agents { id } }", "custom_agents", headers=headers) == []
        assert (
            err_status(
                client,
                'mutation { create_custom_agent(input: {name: "  "}) { id } }',
                headers=headers,
            )
            in {400, 422}
        )


# ---------------------------------------------------------------------------
# PROD-003 — worktree/checkpoint task execution


def _git_repo(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("base\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-qm", "init"], check=True, capture_output=True
    )


def _git(path: Path, *args: str) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


async def _complete_worktree_task(
    manager: AgentManager,
    store: AgentStore,
    repo: Path,
    *,
    execution_mode: str | None = "worktree",
) -> dict[str, Any]:
    task = await manager.create_task(
        prompt="change things",
        cwd=str(repo),
        provider_id="fake",
        execution_mode=execution_mode,
    )
    await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
    await wait_for_status(store, task["id"], "completed")
    return task


def test_worktree_task_runs_in_isolated_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        record = store.task_worktree(task["id"])
        assert record is not None
        assert record["mode"] == "worktree"
        assert record["branch"].startswith("termx/task-")
        # git reports the base path with forward slashes on Windows —
        # compare as paths, not raw strings.
        assert Path(record["base_repo"]) == repo
        worktree_path = Path(record["worktree_path"])
        assert worktree_path != repo
        assert Path(task["cwd"]) == worktree_path
        assert worktree_path.is_dir()
        # The user's checkout never sees the task branch's HEAD.
        assert (worktree_path / "README.md").exists()
        events = store.events(task["id"])
        assert any(e["type"] == "task.worktree.created" for e in events)
        assert any(e["type"] == "task.worktree.final" for e in events)
        assert record["head_sha"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_worktree_apply_merges_into_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        record = store.task_worktree(task["id"])
        worktree_path = Path(record["worktree_path"])
        # Simulate the agent's edit inside its isolated checkout.
        (worktree_path / "agent.txt").write_text("agent change\n")
        assert not (repo / "agent.txt").exists()
        view = manager.task_worktree(task["id"])
        assert view["dirty"]
        updated = manager.resolve_worktree(task["id"], "apply")
        assert updated["status"] == "applied"
        assert (repo / "agent.txt").read_text() == "agent change\n"
        assert not worktree_path.exists()
        assert "termx/task-" not in _git(repo, "branch", "--list")
        assert any(
            e["type"] == "task.worktree.applied" for e in store.events(task["id"])
        )
        await manager.close()
        store.close()

    asyncio.run(run())


def test_worktree_discard_requires_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from termx.agent.worktrees import WorktreeConfirmRequired

    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        record = store.task_worktree(task["id"])
        worktree_path = Path(record["worktree_path"])
        (worktree_path / "stray.txt").write_text("uncommitted\n")
        with pytest.raises(WorktreeConfirmRequired) as blocked:
            manager.resolve_worktree(task["id"], "discard")
        assert blocked.value.dirty
        updated = manager.resolve_worktree(task["id"], "discard", confirm=True)
        assert updated["status"] == "discarded"
        assert not worktree_path.exists()
        assert "termx/task-" not in _git(repo, "branch", "--list")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_worktree_keep_marks_without_merging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        updated = manager.resolve_worktree(task["id"], "keep")
        assert updated["status"] == "kept"
        # second resolution is a no-op read
        assert manager.resolve_worktree(task["id"], "keep")["status"] == "kept"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_worktree_mode_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        with pytest.raises(ValueError, match="Git"):
            await manager.create_task(
                prompt="x", cwd=str(plain), provider_id="fake", execution_mode="worktree"
            )
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_repo(repo)
        with pytest.raises(ValueError, match="Ask mode"):
            await manager.create_task(
                prompt="x",
                cwd=str(repo),
                provider_id="fake",
                mode="ask",
                execution_mode="worktree",
            )
        with pytest.raises(ValueError, match="execution_mode"):
            await manager.create_task(
                prompt="x", cwd=str(repo), provider_id="fake", execution_mode="branchless"
            )
        # direct-mode task reports mode=direct and no row
        task = await manager.create_task(prompt="x", cwd=str(repo), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        assert store.task_worktree(task["id"]) is None
        assert manager.task_worktree(task["id"])["mode"] == "direct"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_worktree_rest_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    adapter = FakeAdapter()
    store = AgentStore(tmp_path / "api.sqlite3", tmp_path / "api-artifacts")
    credentials = CredentialStore(memory={})
    state = AppState(
        passcode="secret",
        agent_store=store,
        credentials=credentials,
        adapter_factory=lambda _provider, _key: adapter,
    )
    state.agent.save_provider(
        provider_id="fake",
        kind="openai-compatible",
        name="Fake",
        base_url="http://127.0.0.1:9/v1",
        model="m",
        capabilities=["shell"],
    )
    headers = {"x-termx-passcode": "secret"}

    async def drive(task_id: str) -> None:
        await wait_for_pending_approval(store, task_id, "plan")
        task = store.get_task(task_id, include_events=True)
        await state.agent.resolve_approval(
            task_id, task["approvals"][0]["id"], "approved"
        )
        await wait_for_status(store, task_id, "completed")

    with TestClient(create_app(state, web_dir=None)) as client:
        assert (
            err_status(
                client,
                '{ agent_task_worktree(task_id: "nope") { task_id } }', headers=headers,
            )
            == 404
        )
        task = data(
            client,
            "mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id } }",
            "create_agent_task",
            {"input": {
                "prompt": "do it",
                "cwd": str(repo),
                "provider_id": "fake",
                "execution_mode": "worktree",
            }},
            headers=headers,
        )
        asyncio.run(drive(task["id"]))
        view = data(
            client,
            "query($id: String!) { agent_task_worktree(task_id: $id) { mode status worktree_path } }",
            "agent_task_worktree", {"id": task["id"]}, headers=headers,
        )
        assert view["mode"] == "worktree"
        assert view["status"] == "active"
        # Dirty the worktree, then verify the 409 confirm gate + confirm path.
        Path(view["worktree_path"], "extra.txt").write_text("x\n")
        blocked = gql(
            client,
            "mutation($id: String!, $input: WorktreeActionInput!) { resolve_task_worktree(task_id: $id, input: $input) { status } }",
            {"id": task["id"], "input": {"action": "discard"}},
            headers,
        )
        blocked_err = blocked.json()["errors"][0]["extensions"]
        assert blocked_err["http_status"] == 409
        assert blocked_err["requires_confirm"] is True
        confirmed = data(
            client,
            "mutation($id: String!, $input: WorktreeActionInput!) { resolve_task_worktree(task_id: $id, input: $input) { status } }",
            "resolve_task_worktree",
            {"id": task["id"], "input": {"action": "discard", "confirm": True}},
            headers=headers,
        )
        assert confirmed["status"] == "discarded"


# ---------------------------------------------------------------------------
# PROD-004 — Activity Center


def test_activity_endpoint_and_snapshot(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    store = AgentStore(tmp_path / "act.sqlite3", tmp_path / "act-artifacts")
    state = AppState(
        passcode="secret",
        agent_store=store,
        credentials=CredentialStore(memory={}),
        adapter_factory=lambda _provider, _key: adapter,
    )
    headers = {"x-termx-passcode": "secret"}

    with TestClient(create_app(state, web_dir=None)) as client:
        assert err_status(client, "{ activity { activity { id } } }") == 401
        project = state.projects.register(str(tmp_path), "demo")
        state.projects.add_preview(project["id"], "dev", "http://127.0.0.1:3000")
        session = state.sessions.create(title="build shell", cwd=str(tmp_path))
        state.agent.save_provider(
            provider_id="fake",
            kind="openai-compatible",
            name="Fake",
            base_url="http://127.0.0.1:9/v1",
            model="m",
            capabilities=["shell"],
        )
        task = data(
            client,
            "mutation($input: AgentTaskInput!) { create_agent_task(input: $input) { id } }",
            "create_agent_task",
            {"input": {"prompt": "check it", "cwd": str(tmp_path), "provider_id": "fake"}},
            headers=headers,
        )

        activity = data(
            client,
            "{ activity { activity { id kind project_id title state started_at updated_at actions extra } } }",
            "activity", headers=headers,
        )["activity"]
        by_kind = {}
        for item in activity:
            assert set(item) >= {
                "id", "kind", "project_id", "title", "state",
                "started_at", "updated_at", "actions",
            }
            by_kind.setdefault(item["kind"], []).append(item)
        agent_item = next(i for i in by_kind["agent"] if i["id"] == f"agent:{task['id']}")
        assert agent_item["project_id"] == project["id"]
        assert "stop" in agent_item["actions"]
        term_item = next(i for i in by_kind["terminal"] if i["id"] == f"terminal:{session.id}")
        assert term_item["state"] == "running"
        assert term_item["project_id"] == project["id"]
        prev_item = by_kind["preview"][0]
        assert prev_item["extra"]["url"] == "http://127.0.0.1:3000"
        state.sessions.kill(session.id)


def test_activity_ws_pushes_changes(tmp_path: Path) -> None:
    state = AppState(
        passcode="secret",
        agent_store=AgentStore(tmp_path / "ws.sqlite3", tmp_path / "ws-artifacts"),
        credentials=CredentialStore(memory={}),
        adapter_factory=lambda _p, _k: FakeAdapter(),
    )
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        with client.websocket_connect(
            "/graphql", subprotocols=["graphql-transport-ws"]
        ) as ws:
            ws.send_json({"type": "connection_init", "payload": {"k": "secret"}})
            assert ws.receive_json()["type"] == "connection_ack"
            ws.send_json({
                "id": "1",
                "type": "subscribe",
                "payload": {"query": "subscription { activity_events { type id activity { id } } }"},
            })
            project = state.projects.register(str(tmp_path), "demo")
            state.projects.add_preview(project["id"], "dev", "http://127.0.0.1:8000")
            deadline = time.time() + 8
            seen: dict[str, dict] = {}
            while time.time() < deadline:
                msg = ws.receive_json()
                if msg.get("type") != "next":
                    continue
                event = msg["payload"]["data"]["activity_events"]
                if event["type"] == "activity.upsert":
                    seen[event["activity"]["id"]] = event["activity"]
                if any(key.startswith("preview:") for key in seen):
                    break
            assert any(key.startswith("preview:") for key in seen), seen
        # stale terminal rows don't leak into a second snapshot
        data(client, "{ activity { activity { id } } }", "activity", headers=headers)


# ---------------------------------------------------------------------------
# PROD-005 — port + process discovery


def _http_listener() -> tuple[Any, int]:
    import http.server
    import socketserver
    import threading as _threading

    handler = http.server.SimpleHTTPRequestHandler
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_ports_and_processes_endpoints(tmp_path: Path) -> None:
    state = AppState(
        passcode="secret",
        agent_store=AgentStore(tmp_path / "p.sqlite3", tmp_path / "p-artifacts"),
        credentials=CredentialStore(memory={}),
        adapter_factory=lambda _p, _k: FakeAdapter(),
    )
    headers = {"x-termx-passcode": "secret"}
    server, port = _http_listener()
    try:
        with TestClient(create_app(state, web_dir=None)) as client:
            assert err_status(client, "{ ports { ports { port } } }") == 401
            assert err_status(client, "{ processes { processes { pid } } }") == 401
            ports = data(
                client,
                "{ ports { ports { port pid url is_http } } }",
                "ports", headers=headers,
            )["ports"]
            procs = data(
                client,
                "{ processes { processes { pid } } }",
                "processes", headers=headers,
            )["processes"]
            if sys.platform == "win32":
                # Discovery is truthfully gated off on Windows — endpoints
                # degrade to empty lists rather than pretending to work.
                assert ports == [] and procs == []
            else:
                entry = next((e for e in ports if e["port"] == port), None)
                assert entry is not None, ports
                assert entry["is_http"] is None  # discovery does not contact the server
                assert entry["url"] is None
                assert entry["pid"] == os.getpid()  # in-proc listener is discoverable
                pids = {p["pid"] for p in procs}
                assert os.getpid() in pids
                # never lists unrelated system processes
                assert all(p["pid"] > 0 for p in procs)
    finally:
        server.shutdown()
        server.server_close()


def test_preview_from_port(tmp_path: Path) -> None:
    state = AppState(
        passcode="secret",
        agent_store=AgentStore(tmp_path / "pf.sqlite3", tmp_path / "pf-artifacts"),
        credentials=CredentialStore(memory={}),
        adapter_factory=lambda _p, _k: FakeAdapter(),
    )
    headers = {"x-termx-passcode": "secret"}
    server, port = _http_listener()
    try:
        with TestClient(create_app(state, web_dir=None)) as client:
            project = state.projects.register(str(tmp_path), "demo")
            assert (
                err_status(
                    client,
                    "mutation($id: String!, $input: PreviewFromPortInput!) { preview_from_port(project_id: $id, input: $input) { preview } }",
                    {"id": project["id"], "input": {"port": port + 99}},
                    headers,
                )
                == 404
            )
            created_status = err_status(
                client,
                "mutation($id: String!, $input: PreviewFromPortInput!) { preview_from_port(project_id: $id, input: $input) { preview } }",
                {"id": project["id"], "input": {"port": port, "name": "dev server"}},
                headers,
            )
            if sys.platform == "win32":
                # Port discovery is gated off on Windows, so no port is ever
                # known and preview-from-port cannot resolve.
                assert created_status == 404
                return
            assert created_status == 200
            preview = data(
                client,
                "query($id: String!) { project_previews(project_id: $id) { name url } }",
                "project_previews", {"id": project["id"]}, headers=headers,
            )[-1]
            assert preview["name"] == "dev server"
            assert preview["url"] == f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# PROD-006 — runbooks + run history


def _runbook_state(tmp_path: Path) -> AppState:
    state = AppState(
        passcode="secret",
        agent_store=AgentStore(tmp_path / "rb.sqlite3", tmp_path / "rb-artifacts"),
        credentials=CredentialStore(memory={}),
        adapter_factory=lambda _p, _k: FakeAdapter(),
    )
    return state


async def _wait_run(store: AgentStore, run_id: str, status: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = store.get_runbook_run(run_id)
        if run and run["status"] == status:
            return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {status}: {run}")


_RUNBOOK = "id name project_id steps created_at updated_at"
_RUN = "id runbook_id status step_index step error steps created_at updated_at"


def _create_runbook(client, headers, name: str, steps: list) -> dict:
    return data(
        client,
        "mutation($input: RunbookInput!) { create_runbook(input: $input) { %s } }" % _RUNBOOK,
        "create_runbook",
        {"input": {"name": name, "steps": steps}},
        headers=headers,
    )


def _run_runbook(client, headers, runbook_id: str) -> dict:
    return data(
        client,
        "mutation($id: String!) { run_runbook(runbook_id: $id) { %s } }" % _RUN,
        "run_runbook", {"id": runbook_id}, headers=headers,
    )


def test_runbooks_crud(tmp_path: Path) -> None:
    state = _runbook_state(tmp_path)
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        assert err_status(client, "{ runbooks { id } }") == 401
        assert (
            err_status(
                client,
                'mutation { create_runbook(input: {name: "x", steps: []}) { id } }',
                headers=headers,
            )
            == 422
        )
        assert (
            err_status(
                client,
                'mutation { create_runbook(input: {name: "x", steps: [{kind: "shell", command: ""}]}) { id } }',
                headers=headers,
            )
            == 422
        )
        created = _create_runbook(
            client, headers, "dev stack",
            [{"kind": "shell", "command": "echo one"},
             {"kind": "shell", "command": "echo two"}],
        )
        assert created["name"] == "dev stack"
        assert len(created["steps"]) == 2
        detail = data(
            client,
            "query($id: String!) { runbook(runbook_id: $id) { runbook { id } } }",
            "runbook", {"id": created["id"]}, headers=headers,
        )
        assert detail["runbook"]["id"] == created["id"]
        patched = data(
            client,
            "mutation($id: String!, $input: RunbookPatchInput!) { update_runbook(runbook_id: $id, input: $input) { name } }",
            "update_runbook", {"id": created["id"], "input": {"name": "renamed"}},
            headers=headers,
        )
        assert patched["name"] == "renamed"
        data(
            client,
            "mutation($id: String!) { delete_runbook(runbook_id: $id) { ok } }",
            "delete_runbook", {"id": created["id"]}, headers=headers,
        )
        assert (
            err_status(
                client,
                "query($id: String!) { runbook(runbook_id: $id) { runbook { id } } }",
                {"id": created["id"]}, headers=headers,
            )
            == 404
        )


def test_runbook_sequential_run_and_history(tmp_path: Path) -> None:
    state = _runbook_state(tmp_path)
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        runbook = _create_runbook(
            client, headers, "seq",
            [{"kind": "shell", "command": "echo first"},
             {"kind": "shell", "command": "echo second"}],
        )
        run = _run_runbook(client, headers, runbook["id"])
        final = asyncio.run(_wait_run(state.agent_store, run["id"], "completed"))
        assert [r["status"] for r in final["step_results"]] == ["completed", "completed"]
        assert "first" in final["step_results"][0]["output"]
        history = data(
            client,
            "query($id: String) { runbook_runs(runbook_id: $id) { id } }",
            "runbook_runs", {"id": runbook["id"]}, headers=headers,
        )
        assert history and history[0]["id"] == run["id"]


def test_runbook_stop_on_failure(tmp_path: Path) -> None:
    state = _runbook_state(tmp_path)
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        runbook = _create_runbook(
            client, headers, "fails",
            [{"kind": "shell", "command": "exit 3"},
             {"kind": "shell", "command": "echo never"}],
        )
        run = _run_runbook(client, headers, runbook["id"])
        final = asyncio.run(_wait_run(state.agent_store, run["id"], "failed"))
        assert len(final["step_results"]) == 1  # step 2 never ran
        assert final["step_results"][0]["exit_code"] == 3
        assert final["error"]


def test_runbook_confirm_gate(tmp_path: Path) -> None:
    state = _runbook_state(tmp_path)
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        runbook = _create_runbook(
            client, headers, "gated",
            [{"kind": "shell", "command": "echo before"},
             {"kind": "shell", "command": "echo after", "confirm": True}],
        )
        run = _run_runbook(client, headers, runbook["id"])
        paused = asyncio.run(
            _wait_run(state.agent_store, run["id"], "awaiting_confirmation")
        )
        assert len(paused["step_results"]) == 1
        confirmed = data(
            client,
            "mutation($id: String!) { confirm_runbook_run(run_id: $id) { status } }",
            "confirm_runbook_run", {"id": run["id"]}, headers=headers,
        )
        assert confirmed["status"] == "running"
        final = asyncio.run(_wait_run(state.agent_store, run["id"], "completed"))
        assert len(final["step_results"]) == 2


def test_runbook_cancel_kills_process(tmp_path: Path) -> None:
    state = _runbook_state(tmp_path)
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        runbook = _create_runbook(
            client, headers, "long",
            [{"kind": "shell", "command": "sleep 30"}],
        )
        run = _run_runbook(client, headers, runbook["id"])
        asyncio.run(asyncio.sleep(0.3))  # let the step spawn
        cancelled = data(
            client,
            "mutation($id: String!) { cancel_runbook_run(run_id: $id) { status } }",
            "cancel_runbook_run", {"id": run["id"]}, headers=headers,
        )
        assert cancelled["status"] == "cancelled"


def test_runbook_parallel_steps(tmp_path: Path) -> None:
    state = _runbook_state(tmp_path)
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        runbook = _create_runbook(
            client, headers, "par",
            [{"kind": "shell", "command": "echo a", "parallel": True},
             {"kind": "shell", "command": "echo b", "parallel": True}],
        )
        run = _run_runbook(client, headers, runbook["id"])
        final = asyncio.run(_wait_run(state.agent_store, run["id"], "completed"))
        assert len(final["step_results"]) == 2


def test_agent_run_runbook_tool(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = ScriptedAdapter([[ _fn("rb-1", "run_runbook", runbook_id="pending") ]])
        manager, store = build_manager(tmp_path, adapter)
        runbook = store.create_runbook(
            name="agent called", steps=[{"kind": "shell", "command": "echo via-agent"}]
        )
        adapter.script = [[_fn("rb-1", "run_runbook", runbook_id=runbook["id"])]]
        task = await manager.create_task(prompt="run it", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        # tool call pauses for approval=always
        _, approval = await wait_for_pending_approval(store, task["id"], "tool")
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        events = store.events(task["id"])
        finished = next(e for e in reversed(events) if e["type"] == "tool.finished")
        assert finished["payload"]["result"]["status"] == "completed"
        runs = store.list_runbook_runs(runbook["id"])
        assert runs and runs[0]["status"] == "completed"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_old_db_migrates_to_v2_schema(tmp_path: Path) -> None:
    """A v1-era agent.sqlite3 (pre-mode/metrics/parent_id + none of the new
    tables) upgrades in place and stays functional."""
    import sqlite3

    db_path = tmp_path / "agent.sqlite3"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, prompt TEXT NOT NULL, cwd TEXT NOT NULL,
            provider_id TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
            limits TEXT NOT NULL, plan TEXT, result TEXT, error TEXT,
            previous_response_id TEXT, runtime TEXT,
            next_sequence INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL,
            UNIQUE(task_id, sequence)
        );
        CREATE TABLE approvals (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, payload TEXT NOT NULL,
            created_at REAL NOT NULL, resolved_at REAL
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO tasks (id, prompt, cwd, provider_id, model, status, limits,
                           created_at, updated_at)
        VALUES ('old-task', 'p', '/tmp', 'prov', 'm', 'completed', '{}', 1.0, 1.0);
        """
    )
    legacy.commit()
    legacy.close()

    store = AgentStore(db_path, tmp_path / "artifacts")
    try:
        cols = {r["name"] for r in store._db.execute("PRAGMA table_info(tasks)")}
        assert {"mode", "metrics", "parent_id"} <= cols
        for table in ("checkpoints", "conversations", "conversation_turns",
                      "conversation_context_refs", "custom_agents",
                      "task_worktrees", "runbooks", "runbook_runs", "artifacts",
                      "providers"):
            store._db.execute(f"SELECT * FROM {table} LIMIT 0")
        task = store.get_task("old-task")
        assert task["status"] == "completed" and task["mode"] == "agent"
        # New v2 surfaces work on the upgraded DB.
        conv = store.create_conversation(title="old-db")
        store.add_conversation_turn(conv["id"], prompt="hi")
        assert store.list_conversations()
        store.create_runbook(name="rb", steps=[{"kind": "shell", "command": "true"}])
        assert store.list_runbooks()
    finally:
        store.close()


def test_worktree_apply_refuses_branch_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply must land on the branch recorded at creation — not a drifted checkout."""
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    from termx.agent.worktrees import WorktreeApplyConflict

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        record = store.task_worktree(task["id"])
        assert record["base_branch"] == _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        worktree_path = Path(record["worktree_path"])
        (worktree_path / "agent.txt").write_text("agent change\n")
        # The user moved to another branch since the task started.
        _git(repo, "checkout", "-qb", "other")
        with pytest.raises(WorktreeApplyConflict, match="other"):
            manager.resolve_worktree(task["id"], "apply")
        # Nothing landed: file absent, worktree still active + present.
        assert not (repo / "agent.txt").exists()
        assert store.task_worktree(task["id"])["status"] == "active"
        assert worktree_path.exists()
        assert any(
            e["type"] == "task.worktree.conflict" for e in store.events(task["id"])
        )
        # Moving back to the recorded branch makes apply succeed.
        _git(repo, "checkout", "-q", record["base_branch"])
        updated = manager.resolve_worktree(task["id"], "apply")
        assert updated["status"] == "applied"
        assert (repo / "agent.txt").exists()
        await manager.close()
        store.close()

    asyncio.run(run())


def test_worktree_apply_conflict_rolls_back_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A conflicting merge is aborted transactionally — base is never left MERGING."""
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    from termx.agent.worktrees import WorktreeApplyConflict

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        record = store.task_worktree(task["id"])
        worktree_path = Path(record["worktree_path"])
        # Same file diverges on both sides -> real merge conflict.
        (worktree_path / "README.md").write_text("agent version\n")
        (repo / "README.md").write_text("user version\n")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-qm", "user change")
        with pytest.raises(WorktreeApplyConflict, match="rolled back"):
            manager.resolve_worktree(task["id"], "apply")
        # Transactional: no MERGE_HEAD, base content untouched, clean status.
        assert not (repo / ".git" / "MERGE_HEAD").exists()
        assert (repo / "README.md").read_text() == "user version\n"
        assert worktree_dirty_clean(repo)
        # Worktree survives for retry or discard; status stays active.
        assert store.task_worktree(task["id"])["status"] == "active"
        assert worktree_path.exists()
        await manager.close()
        store.close()

    asyncio.run(run())


def worktree_dirty_clean(repo: Path) -> bool:
    return not _git(repo, "status", "--porcelain").strip()


def test_subagent_cannot_spawn_deeper(tmp_path: Path) -> None:
    """Terminal-parent gate: a child task may not fan out (depth cap = 1)."""
    async def run() -> None:
        adapter = _FanOutAdapter(
            {
                "Parent": ([[_fn("s1", "spawn_subagent", task="child")]], 0.0),
                "child": ([[_fn("s2", "spawn_subagent", task="grandchild")]], 0.0),
            }
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(
            prompt="Parent", cwd=str(tmp_path), provider_id="fake"
        )
        await manager.resolve_approval(
            parent["id"], parent["approvals"][0]["id"], "approved"
        )
        await _approve_pending(manager, store, parent["id"])  # child's escalated plan
        await wait_for_status(store, parent["id"], "completed")
        children = [t for t in store.list_tasks() if t.get("parent_id") == parent["id"]]
        assert len(children) == 1
        child = children[0]
        grandchildren = [t for t in store.list_tasks() if t.get("parent_id") == child["id"]]
        assert grandchildren == []
        finished = [
            e for e in store.events(child["id"])
            if e["type"] == "tool.finished" and e["payload"].get("call_id") == "s2"
        ]
        assert finished
        assert finished[0]["payload"]["result"]["ok"] is False
        assert "depth" in finished[0]["payload"]["result"]["error"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_provider_tools_hide_subagents_for_children() -> None:
    from termx.agent.tools import default_registry

    registry = default_registry()
    all_names = {t["name"] for t in registry.provider_tools()}
    assert {"spawn_subagent", "await_subagents", "subagent_status", "cancel_subagent"} <= all_names
    child_names = {t["name"] for t in registry.provider_tools(allow_subagents=False)}
    assert "spawn_subagent" not in child_names
    assert "await_subagents" not in child_names
    assert "list_files" in child_names  # other tools unaffected




def test_task_uses_custom_agent_instructions_and_ignores_legacy_usage_limits(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    manager, store = build_manager(tmp_path, adapter)
    agent = store.create_custom_agent(
        name="Reviewer",
        instructions="Prefer read-only inspection.",
        provider_id="fake",
        model="fake-model",
        limits={"max_steps": 5},
    )

    async def _run() -> dict[str, Any]:
        task = await manager.create_task(
            prompt="check the readme",
            cwd=str(tmp_path),
            provider_id="fake",
            mode="ask",
            custom_agent_id=agent["id"],
        )
        return await wait_for_status(store, task["id"], "completed")

    task = asyncio.run(_run())
    assert "Prefer read-only inspection." in task["prompt"]
    assert "check the readme" in task["prompt"]
    assert task["limits"]["max_steps"] is None


def test_task_seeds_conversation_context(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    manager, store = build_manager(tmp_path, adapter)
    conversation = store.create_conversation(title="Thread", provider_id="fake")

    async def _run() -> dict[str, Any]:
        first = await manager.create_task(
            prompt="remember primrose",
            cwd=str(tmp_path),
            provider_id="fake",
            mode="ask",
            conversation_id=conversation["id"],
        )
        first = await wait_for_status(store, first["id"], "completed")
        store.add_conversation_turn(
            conversation["id"],
            prompt="remember primrose",
            task_id=first["id"],
            mode="ask",
        )
        adapter.turns = 0  # FakeAdapter is scripted for one task per manager
        second = await manager.create_task(
            prompt="what did I say?",
            cwd=str(tmp_path),
            provider_id="fake",
            mode="ask",
            conversation_id=conversation["id"],
        )
        return await wait_for_status(store, second["id"], "completed")

    second = asyncio.run(_run())
    assert second["status"] == "completed"
    first_input = (adapter.inputs[-1] or [])[0]
    payload = json.loads(first_input["content"])
    assert payload["type"] == "conversation_context"
    assert payload["conversation_id"] == conversation["id"]
    assert payload["prior_turns"][0]["prompt"] == "remember primrose"
    assert payload["prior_turns"][0]["task_status"] == "completed"


def test_task_missing_conversation_or_agent_rejected(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    manager, _ = build_manager(tmp_path, adapter)
    for kwargs in (
        {"conversation_id": "nope"},
        {"custom_agent_id": "nope"},
    ):
        try:
            asyncio.run(
                manager.create_task(
                    prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask", **kwargs
                )
            )
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")


def test_process_discovery_platform_gating(monkeypatch) -> None:
    import sys
    import termx.processes as processes

    real_platform = sys.platform

    for platform, expected in (("linux", True), ("darwin", True), ("win32", False)):
        monkeypatch.setattr(sys, "platform", platform)
        assert processes.supported() is expected
    monkeypatch.setattr(sys, "platform", "win32")
    assert processes.listeners() == []
    assert processes.termx_processes([]) == []
    monkeypatch.setattr(sys, "platform", real_platform)


def test_darwin_process_discovery_parsers(monkeypatch, tmp_path: Path) -> None:
    import sys
    import termx.processes as processes

    monkeypatch.setattr(sys, "platform", "darwin")
    lsof_listeners = (
        "COMMAND     PID   USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"
        "python3   12345 ubuntu   10u  IPv4 0xdeadbeef      0t0  TCP 127.0.0.1:8000 (LISTEN)\n"
        "node      22345 ubuntu   22u  IPv6 0xbeefdead      0t0  TCP *:3000 (LISTEN)\n"
        "malformed row\n"
    )
    ps_out = (
        "  PID  PPID COMM              ARGS\n"
        f"{os.getpid():6d}     1 python3           python3 -m termx\n"
        "99999 12345 sshd              sshd: root\n"
    )
    lsof_cwds = f"p{os.getpid()}\nfcwd\nn{tmp_path}\n"

    def fake_run(cmd, **_kw):
        class R:
            stdout = ""
        r = R()
        if cmd[:2] == ["lsof", "-nP"]:
            r.stdout = lsof_listeners
        elif cmd[:2] == ["lsof", "-n"]:
            r.stdout = lsof_cwds
        elif cmd[0] == "ps":
            r.stdout = ps_out
        return r

    monkeypatch.setattr(processes.subprocess, "run", fake_run)
    ports = processes.listeners()
    assert {p["port"] for p in ports} == {8000, 3000}
    loop = next(p for p in ports if p["port"] == 8000)
    assert loop["pid"] == 12345 and loop["process"] == "python3"
    assert loop["is_http"] is None and loop["url"] is None
    wild = next(p for p in ports if p["port"] == 3000)
    assert wild["is_http"] is None and wild["url"] is None

    procs = processes.termx_processes([str(tmp_path)])
    ours = next(p for p in procs if p["pid"] == os.getpid())
    assert ours["cwd"] == str(tmp_path) and ours["name"] == "python3"
    assert all(p["pid"] != 99999 for p in procs)  # unrelated system procs excluded


def test_delete_task_removes_orphan_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        record = store.task_worktree(task["id"])
        assert record is not None
        wt_path = record["worktree_path"]
        branch = record["branch"]
        assert Path(wt_path).exists()
        assert manager.delete_task(task["id"]) is True
        assert not Path(wt_path).exists()  # orphan checkout removed
        branches = _git(repo, "branch", "--list", branch)
        assert branches == ""  # orphan branch removed


def test_delete_task_preserves_kept_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    async def run() -> None:
        manager, store = build_manager(tmp_path, FakeAdapter())
        task = await _complete_worktree_task(manager, store, repo)
        record = store.task_worktree(task["id"])
        assert record is not None
        wt_path = record["worktree_path"]
        manager.resolve_worktree(task["id"], "keep")
        assert manager.delete_task(task["id"]) is True
        assert Path(wt_path).exists()  # kept worktrees belong to the user


def test_runbook_step_output_streams_and_stays_bounded(tmp_path: Path) -> None:
    """Live step output publishes mid-run and memory/output stay bounded."""
    import termx.runbooks as runbooks

    state = _runbook_state(tmp_path)
    store = state.agent_store
    book = store.create_runbook(
        name="chatty",
        project_id=None,
        steps=[
            {
                "kind": "shell",
                "command": (
                    "python3 -c \"import sys,time; "
                    "sys.stdout.write('x'*100000); sys.stdout.flush(); "
                    "time.sleep(0.4); sys.stdout.write('y'*100000)\""
                ),
                "confirm": False,
                "parallel": False,
            }
        ],
    )

    async def run() -> None:
        runner = runbooks.RunbookRunner(store)
        live = runner.start({**book, "steps": book["steps"]}, cwd=str(tmp_path))
        seen_partial = False
        deadline = time.time() + 20
        while time.time() < deadline:
            row = store.get_runbook_run(live["id"])
            partial = (row or {}).get("step_results") or []
            if partial and partial[-1].get("status") == "running" and partial[-1].get("output"):
                seen_partial = True
                break
            if (row or {}).get("status") in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.05)
        assert seen_partial, "no live step output was published during the run"
        final = await _wait_run(store, live["id"], "completed")
        step = final["step_results"][0]
        assert step["status"] == "completed"
        assert len(step["output"]) <= runbooks.MAX_STEP_OUTPUT
        assert step["output"].endswith("y")  # tail keeps the most recent bytes
        await runner.shutdown()

    asyncio.run(run())


def test_runbook_step_output_tail_truncated(tmp_path: Path) -> None:
    import termx.runbooks as runbooks

    state = _runbook_state(tmp_path)
    store = state.agent_store

    async def run() -> None:
        runner = runbooks.RunbookRunner(store)
        proc_out = "abcdef"
        emitted: list[dict] = []
        result = await runner._execute_step(
            "run-x",
            {
                "kind": "shell",
                "command": f"python3 -c \"print('{proc_out}'*20000)\"",
            },
            str(tmp_path),
            on_progress=emitted.append,
        )
        assert len(result["output"]) <= runbooks.MAX_STEP_OUTPUT
        # Child stdout translates \n -> \r\n on Windows.
        assert result["output"].replace("\r\n", "\n").endswith("abcdef\n")
        assert len(emitted) >= 1
        assert all(e["status"] == "running" for e in emitted)

    asyncio.run(run())


def test_runbook_confirm_resumes_after_restart(tmp_path: Path) -> None:
    """A run parked at awaiting_confirmation survives a host restart:
    a fresh runner resumes it at the gated step without re-parking."""
    import termx.runbooks as runbooks

    state = _runbook_state(tmp_path)
    store = state.agent_store
    book = store.create_runbook(
        name="gated",
        project_id=None,
        steps=[
            {"kind": "shell", "command": "echo before", "confirm": False, "parallel": False},
            {"kind": "shell", "command": "echo gated", "confirm": True, "parallel": False},
            {"kind": "shell", "command": "echo after", "confirm": False, "parallel": False},
        ],
    )

    async def run() -> None:
        runner = runbooks.RunbookRunner(store)
        live = runner.start({**book, "steps": book["steps"]}, cwd=str(tmp_path))
        parked = await _wait_run(store, live["id"], "awaiting_confirmation")
        assert parked["current_step"] == 1
        assert len(parked["step_results"]) == 1  # only 'before' ran
        # Simulate restart: drop the runner entirely, build a fresh one.
        runner2 = runbooks.RunbookRunner(store)
        runner2.confirm(live["id"])
        final = await _wait_run(store, live["id"], "completed")
        outputs = [s["output"].strip() for s in final["step_results"]]
        assert outputs == ["before", "gated", "after"]
        await runner2.shutdown()

    asyncio.run(run())


def test_runbook_dead_running_run_fails_on_restart(tmp_path: Path) -> None:
    """A run left 'running' by a dead host is failed, never replayed."""
    import termx.runbooks as runbooks

    state = _runbook_state(tmp_path)
    store = state.agent_store
    book = store.create_runbook(
        name="dead", project_id=None,
        steps=[{"kind": "shell", "command": "sleep 60", "confirm": False, "parallel": False}],
    )
    run = store.create_runbook_run(book["id"], cwd=str(tmp_path))
    store.update_runbook_run(run["id"], status="running")
    runbooks.RunbookRunner(store)  # init sweep
    row = store.get_runbook_run(run["id"])
    assert row["status"] == "failed"
    assert "restarted" in row["error"]




def test_runbook_tool_refuses_cross_project_runbook(tmp_path: Path) -> None:
    """A runbook bound to a different project must not rewrite the task's cwd."""
    from termx.agent.tools.runbooks import _run_runbook
    from termx.agent.tools.registry import ToolContext

    other = tmp_path / "other-project"
    other.mkdir()
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    runbook = store.create_runbook(
        name="foreign", project_id="p-other",
        steps=[{"kind": "shell", "command": "echo nope", "confirm": False, "parallel": False}],
    )

    class PF:
        def project(self, project_id):
            return {"id": project_id, "path": str(other)}

    ctx = ToolContext(
        task_id="t1", cwd=str(tmp_path), task={}, read_only=False,
        cancel=asyncio.Event(), emit=lambda *_a, **_k: {},
        store=store, manager=None, project_files=PF(),
    )

    async def run() -> None:
        outcome = await _run_runbook(
            _fn("rb-x", "run_runbook", runbook_id=runbook["id"]), ctx
        )
        assert "different project" in outcome.result["error"]
        assert store.list_runbook_runs(runbook["id"]) == []

    asyncio.run(run())
    store.close()


def test_runbook_tool_runs_within_bound_project(tmp_path: Path) -> None:
    """A runbook bound to the task's own project runs in ctx.cwd."""
    from termx.agent.tools.runbooks import _run_runbook
    from termx.agent.tools.registry import ToolContext

    async def run() -> None:
        adapter = FakeAdapter()
        manager, store = build_manager(tmp_path, adapter)
        runbook = store.create_runbook(
            name="local", project_id="p-same",
            steps=[{"kind": "shell", "command": "echo ok", "confirm": False, "parallel": False}],
        )

        class PF:
            def project(self, project_id):
                return {"id": project_id, "path": str(tmp_path)}

        ctx = ToolContext(
            task_id="t1", cwd=str(tmp_path), task={}, read_only=False,
            cancel=asyncio.Event(), emit=lambda *_a, **_k: {},
            store=store, manager=manager, project_files=PF(),
        )
        outcome = await _run_runbook(
            _fn("rb-y", "run_runbook", runbook_id=runbook["id"]), ctx
        )
        assert outcome.result["status"] == "completed"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_parent_approvals_survive_child_completion(tmp_path: Path) -> None:
    """A finishing child must not flip a parent that has its own pending
    tool approval back to 'running' — that strands the approval."""
    manager, store = build_manager(tmp_path, FakeAdapter())
    task = store.create_task(
        prompt="p", cwd=str(tmp_path), provider_id="fake", model="fake-model",
        limits={}, mode="agent",
    )
    approval = store.create_approval(task["id"], "tool", {"title": "own gate"})
    manager._pending_approval_calls[approval["id"]] = {
        "task_id": task["id"], "call": {}, "remaining_calls": [], "history": [],
    }
    store.update_task(task["id"], status="awaiting_approval")

    # Child finishes while parent's own approval is still pending.
    manager._drop_pending_approvals(task["id"], child_id="child-x")
    manager._unpause_if_idle(task["id"])
    assert store.get_task(task["id"])["status"] == "awaiting_approval"

    # Once no pending approvals remain, the unpause still works.
    manager._pending_approval_calls.pop(approval["id"])
    manager._unpause_if_idle(task["id"])
    assert store.get_task(task["id"])["status"] == "running"
    store.close()


def test_worktree_create_task_validation_failure_discards_checkout(tmp_path: Path) -> None:
    """A rejected create_task must not leave an orphan worktree + branch."""
    repo = tmp_path / "wt-src"
    repo.mkdir()
    _git_repo(repo)
    (tmp_path / "work").mkdir()
    manager, store = build_manager(tmp_path / "work", FakeAdapter())

    async def run() -> None:
        with pytest.raises(ValueError):
            await manager.create_task(
                prompt="nope",
                cwd=str(repo),
                provider_id="fake",
                model="not-a-configured-model",
                execution_mode="worktree",
            )

    asyncio.run(run())
    assert _git(repo, "branch", "--list", "termx/*") == ""
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    store.close()


def test_runbook_first_step_confirm_resumes_after_restart(tmp_path: Path) -> None:
    """A run parked at step 0 must not re-park: current_step=0 is a real index."""
    import termx.runbooks as runbooks

    state = _runbook_state(tmp_path)
    store = state.agent_store
    book = store.create_runbook(
        name="first-gate", project_id=None,
        steps=[
            {"kind": "shell", "command": "echo gated0", "confirm": True, "parallel": False},
            {"kind": "shell", "command": "echo after", "confirm": False, "parallel": False},
        ],
    )

    async def run() -> None:
        runner = runbooks.RunbookRunner(store)
        live = runner.start(book, cwd=str(tmp_path))
        parked = await _wait_run(store, live["id"], "awaiting_confirmation")
        assert parked["current_step"] == 0
        runner2 = runbooks.RunbookRunner(store)
        runner2.confirm(live["id"])
        final = await _wait_run(store, live["id"], "completed")
        outputs = [s["output"].strip() for s in final["step_results"]]
        assert outputs == ["gated0", "after"]
        await runner2.shutdown()

    asyncio.run(run())


def test_runbook_dead_run_sweep_not_capped(tmp_path: Path) -> None:
    """The restart sweep covers running rows older than the 200 newest runs."""
    import termx.runbooks as runbooks

    state = _runbook_state(tmp_path)
    store = state.agent_store
    book = store.create_runbook(
        name="sweep", project_id=None,
        steps=[{"kind": "shell", "command": "true", "confirm": False, "parallel": False}],
    )
    old = store.create_runbook_run(book["id"], cwd=str(tmp_path))
    for _ in range(210):
        newer = store.create_runbook_run(book["id"], cwd=str(tmp_path))
        store.update_runbook_run(newer["id"], status="completed", finished_at=time.time())
    runbooks.RunbookRunner(store)
    assert store.get_runbook_run(old["id"])["status"] == "failed"


def test_runbook_resume_uses_step_snapshot(tmp_path: Path) -> None:
    """Restart resume replays the run's captured steps, not an edited runbook."""
    import termx.runbooks as runbooks

    state = _runbook_state(tmp_path)
    store = state.agent_store
    book = store.create_runbook(
        name="edited", project_id=None,
        steps=[{"kind": "shell", "command": "echo original", "confirm": True, "parallel": False}],
    )

    async def run() -> None:
        runner = runbooks.RunbookRunner(store)
        live = runner.start(book, cwd=str(tmp_path))
        await _wait_run(store, live["id"], "awaiting_confirmation")
        # Edit the runbook while parked — the resume must ignore this.
        store.update_runbook(
            book["id"],
            steps=[{"kind": "shell", "command": "echo SWAPPED", "confirm": True, "parallel": False}],
        )
        runner2 = runbooks.RunbookRunner(store)
        runner2.confirm(live["id"])
        final = await _wait_run(store, live["id"], "completed")
        assert final["step_results"][0]["output"].strip() == "original"
        assert final["step_results"][0]["command"] == "echo original"
        await runner2.shutdown()

    asyncio.run(run())


def test_runbook_tool_accepts_runbook_bound_to_worktree_base(tmp_path: Path) -> None:
    """A worktree task may run a runbook bound to its base repo's project."""
    from termx.agent.tools.runbooks import _run_runbook
    from termx.agent.tools.registry import ToolContext

    other = tmp_path / "registered-project"
    other.mkdir()
    wt = tmp_path / "isolated-worktree"
    wt.mkdir()

    async def run() -> None:
        adapter = FakeAdapter()
        manager, store = build_manager(tmp_path, adapter)
        task = store.create_task(
            prompt="t", cwd=str(wt), provider_id="fake", model="fake-model",
            limits={}, mode="agent",
        )
        store.save_task_worktree(
            task["id"], mode="worktree", base_repo=str(other), base_ref="",
            worktree_path=str(wt), branch="termx/x",
        )
        runbook = store.create_runbook(
            name="bound", project_id="p-reg",
            steps=[{"kind": "shell", "command": "echo wt-ok", "confirm": False, "parallel": False}],
        )

        class PF:
            def project(self, project_id):
                return {"id": project_id, "path": str(other)}

        ctx = ToolContext(
            task_id=task["id"], cwd=str(wt), task=task, read_only=False,
            cancel=asyncio.Event(), emit=lambda *_a, **_k: {},
            store=store, manager=manager, project_files=PF(),
        )
        outcome = await _run_runbook(
            _fn("rb-w", "run_runbook", runbook_id=runbook["id"]), ctx
        )
        assert outcome.result["status"] == "completed"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_windows_flags_not_read_as_outside_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows `foo /flag` is a flag, not a path — commands like
    `whoami /groups` must not trip the outside-project gate."""
    import termx.agent.policy as policy

    monkeypatch.setattr(policy, "_WINDOWS", True)
    assert evaluate_shell("whoami /groups", str(tmp_path)).approval_required is False
    assert evaluate_shell("net user /add", str(tmp_path)).approval_required is False
    # ~ and parent traversal still gate on Windows.
    assert evaluate_shell("type ~\\secret.txt", str(tmp_path)).approval_required is True
    assert evaluate_shell("type ..\\up.txt", str(tmp_path)).approval_required is True


def test_windows_unquoted_drive_paths_are_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """shlex(posix=True) strips `C:\\a\\b.txt` to `C:ab.txt` — raw extraction
    must still surface drive/UNC paths so they can be gated."""
    import termx.agent.policy as policy

    monkeypatch.setattr(policy, "_WINDOWS", True)
    candidates = policy._windows_candidates(
        'type C:\\Users\\alice\\secrets.txt & echo done'
    )
    assert candidates == ["C:\\Users\\alice\\secrets.txt"]
    candidates = policy._windows_candidates('type "C:\\a b\\x.txt" \\\\srv\\share')
    assert candidates == ["C:\\a b\\x.txt", "\\\\srv\\share"]
    assert policy._windows_candidates("whoami /groups") == []
