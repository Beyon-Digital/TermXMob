from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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

    async def execute(
        self,
        actions: list[dict[str, Any]],
        *,
        cancel: asyncio.Event | None = None,
    ) -> bytes:
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
