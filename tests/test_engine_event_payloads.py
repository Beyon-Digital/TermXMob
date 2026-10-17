"""Large native payloads preserve tool routing and complete assistant answers."""
from __future__ import annotations

import asyncio
import json

import pytest

from termx.agent.store import AgentStore
from termx.engines.acp import AcpEngine, _SessionClient
from termx.engines.codex import CodexEngine
from termx.engines.gateway import EngineGateway
from termx.engines.types import EngineSessionBinding


@pytest.fixture
def native_ledger(tmp_path):
    store = AgentStore(tmp_path / "events.sqlite3", tmp_path / "artifacts")
    gateway = EngineGateway(store, store.append_event)
    events = []

    def enroll(engine):
        task = store.create_task(prompt="Inspect the workspace", cwd=str(tmp_path),
                                 provider_id="engine:fixture", model="fixture", limits={},
                                 engine=engine.id, status="running")
        binding = EngineSessionBinding.new(engine.id, "native-fixture", cwd=str(tmp_path), status="active")
        gateway._bindings[binding.binding_id] = binding
        gateway._binding_task[binding.binding_id] = task["id"]
        engine._bindings[binding.binding_id] = binding

        def emit(binding_id, event):
            events.append(event)
            gateway.on_engine_event(binding_id, event)

        engine._event_sink = emit
        return task, binding

    yield store, events, enroll
    store.close()


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_large_acp_tool_update_retains_identity_status_and_parent_answer(native_ledger, status):
    from acp.schema import AgentMessageChunk, TextContentBlock, ToolCallProgress, ToolCallStart

    store, events, enroll = native_ledger
    engine = AcpEngine()
    task, binding = enroll(engine)
    client = _SessionClient(engine, binding.native_session_id, {"binding": binding})
    output = "api_key=synthetic_fixture_secret\n" + "large output 💡\n" * 3000

    async def update():
        await client.session_update(binding.native_session_id, ToolCallStart(
            session_update="tool_call", tool_call_id="tool-first", title="Read files", status="in_progress"))
        await client.session_update(binding.native_session_id, ToolCallProgress(
            session_update="tool_call_update", tool_call_id="tool-first", status=status,
            raw_output={"stdout": output},
            content=[{"type": "content", "content": {"type": "text", "text": output}}]))
        await client.session_update(binding.native_session_id, AgentMessageChunk(
            session_update="agent_message_chunk", content=TextContentBlock(type="text", text="Parent answer")))
        engine._emit(binding, "engine.turn.completed", {"status": "completed"})

    asyncio.run(update())
    started = next(event for event in events if event.type == "engine.tool.started")
    finished = next(event for event in events if event.type == "engine.tool.progress")
    assert started.payload["toolCallId"] == finished.payload["toolCallId"] == "tool-first"
    assert finished.payload["status"] == status
    assert finished.payload["rawOutput"]["truncated"] is True
    assert finished.payload["content"]["truncated"] is True
    encoded = json.dumps(finished.payload)
    assert len(encoded) <= 8192
    assert "synthetic_fixture_secret" not in encoded
    assert store.get_task(task["id"])["result"] == "Parent answer"


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_large_codex_tools_keep_parallel_routing_and_streamed_answer(native_ledger, status):
    store, events, enroll = native_ledger
    engine = CodexEngine()
    task, binding = enroll(engine)
    engine._threads[binding.native_session_id] = binding.binding_id

    def notify(method, **payload):
        engine._on_notification(method, {"threadId": binding.native_session_id, **payload})

    for identifier in ("first-command", "second-command"):
        notify("item/started", item={"id": identifier, "type": "commandExecution", "command": "inspect", "status": "inProgress"})
    output = "api_key=synthetic_fixture_secret\n" + "command output 💡\n" * 3000
    notify("item/commandExecution/outputDelta", itemId="first-command", delta=output)
    notify("item/commandExecution/outputDelta", itemId="second-command", delta="other command")
    notify("item/completed", item={"id": "first-command", "type": "commandExecution", "status": status,
                                   "exitCode": 0 if status == "completed" else 1, "aggregatedOutput": output})
    notify("item/completed", item={"id": "second-command", "type": "commandExecution", "status": "completed", "aggregatedOutput": "other command"})
    answer = "Parent response 💡\n" * 1200
    notify("item/agentMessage/delta", itemId="answer", delta=answer)
    notify("item/completed", item={"id": "answer", "type": "agentMessage", "text": answer + "Final sentence."})
    notify("turn/completed", turn={"id": "turn-one", "status": "completed"})

    items = [event.payload["item"] for event in events if event.type == "engine.item.completed"]
    first = next(item for item in items if item["id"] == "first-command")
    assert first["type"] == "commandExecution" and first["status"] == status
    assert first["aggregatedOutput"]["truncated"] is True
    commands = [event for event in events if event.type == "engine.command.output"]
    assert {event.payload["itemId"] for event in commands} == {"first-command", "second-command"}
    assert all(len(json.dumps(event.payload)) <= 8192 for event in events)
    assert "synthetic_fixture_secret" not in json.dumps([event.payload for event in events])
    assert store.get_task(task["id"])["result"] == answer + "Final sentence."


def test_codex_large_completion_only_text_is_not_replaced_by_a_preview(native_ledger):
    store, events, enroll = native_ledger
    engine = CodexEngine()
    task, binding = enroll(engine)
    engine._threads[binding.native_session_id] = binding.binding_id
    answer = "A complete answer with no partial notifications. 💡\n" * 1000
    engine._on_notification("item/completed", {
        "threadId": binding.native_session_id, "parent_call_id": "delegated-call",
        "item": {"id": "child-answer", "type": "agentMessage", "text": "Worker analysis. 💡\n" * 1000},
    })
    params = {"threadId": binding.native_session_id, "item": {"id": "answer", "type": "agentMessage", "text": answer}}
    engine._on_notification("item/completed", params)
    # A repeated final snapshot cannot duplicate an already emitted answer.
    engine._on_notification("item/completed", params)
    engine._on_notification("turn/completed", {"threadId": binding.native_session_id, "turn": {"id": "turn-one", "status": "completed"}})
    assert store.get_task(task["id"])["result"] == answer
    assert all(event.payload["parent_call_id"] == "delegated-call" for event in events
               if event.type == "engine.message.delta" and event.payload["itemId"] == "child-answer")
    assert all(len(json.dumps(event.payload)) <= 8192 for event in events)
