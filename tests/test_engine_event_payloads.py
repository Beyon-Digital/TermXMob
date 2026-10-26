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


def _codex_wire_fixture():
    events = []
    engine = CodexEngine(event_sink=lambda _binding_id, event: events.append(event))
    binding = EngineSessionBinding.new("codex", "wire-thread", status="active")
    engine._bindings[binding.binding_id] = binding
    engine._threads[binding.native_session_id] = binding.binding_id

    def notify(method, **params):
        engine._on_notification(method, {"threadId": binding.native_session_id, **params})

    return engine, binding, events, notify


@pytest.mark.parametrize("method,payload", [
    ("item/agentMessage/delta", {"turnId": "old-turn", "itemId": "answer", "delta": "late old answer"}),
    ("item/completed", {"turnId": "old-turn", "item": {"id": "answer", "type": "agentMessage", "text": "old snapshot"}}),
    ("turn/completed", {"turn": {"id": "old-turn", "status": "completed"}}),
    ("turn/started", {"turn": {"id": "old-turn"}}),
])
def test_codex_retired_notification_cannot_mutate_current_turn(method, payload):
    engine, binding, events, notify = _codex_wire_fixture()
    notify("turn/started", turn={"id": "old-turn"})
    notify("turn/completed", turn={"id": "old-turn", "status": "completed"})
    notify("turn/started", turn={"id": "new-turn"})
    notify("item/agentMessage/delta", turnId="new-turn", itemId="answer", delta="New answer")
    text_before = dict(engine._message_text)
    before = len(events)
    notify(method, **payload)
    assert binding.native_turn_id == "new-turn"
    assert binding.status == "active"
    assert engine._message_text == text_before
    assert len(events) == before + 1
    assert events[-1].type == "engine.raw"
    assert events[-1].payload["ignored"] == "stale_native_turn"
    assert events[-1].native["turn_id"] == "old-turn"
    notify("turn/completed", turn={"id": "new-turn", "status": "completed"})
    assert binding.status == "idle"
    assert events[-1].type == "engine.turn.completed"
    assert events[-1].native["turn_id"] == "new-turn"


@pytest.mark.parametrize("identity", [{"turnId": "wire-turn"}, {"turn": {"id": "wire-turn"}}, {}])
def test_codex_notification_identity_is_wire_only(identity):
    engine, binding, events, notify = _codex_wire_fixture()
    binding.native_turn_id = "cached-turn"
    binding.status = "idle"
    notify("item/agentMessage/delta", itemId="answer", delta="Wire answer", **identity)
    assert events[-1].type == ("engine.message.delta" if identity else "engine.raw")
    assert events[-1].native.get("turn_id") == ("wire-turn" if identity else None)
    if not identity:
        assert "turn_id" not in events[-1].native


def test_codex_persisted_turn_owner_rejects_old_events_before_new_id_known():
    engine, binding, events, notify = _codex_wire_fixture()
    binding.extensions_snapshot.update(termx_dispatch_id="new-task", termx_turn_owners={"old-turn": "old-task"})
    notify("turn/completed", turn={"id": "old-turn", "status": "completed"})
    assert binding.status == "active"
    assert binding.native_turn_id is None
    assert events[-1].payload["ignored"] == "stale_native_turn"
    # Unknown completion cannot claim the current dispatch before native proof.
    notify("turn/completed", turn={"id": "new-turn", "status": "completed"})
    assert binding.status == "active"
    assert binding.native_turn_id is None
    notify("turn/started", turn={"id": "new-turn"})
    assert binding.status == "idle"
    assert binding.native_turn_id == "new-turn"
    assert events[-1].type == "engine.turn.completed"


def test_codex_unknown_notifications_wait_for_actual_rpc_turn_proof(monkeypatch):
    engine, binding, events, notify = _codex_wire_fixture()
    binding.extensions_snapshot["termx_dispatch_id"] = "new-task"

    async def ensure_conn():
        return None

    class Connection:
        async def request(self, method, params, **kwargs):
            notify("item/agentMessage/delta", turnId="unknown-old-turn", itemId="old", delta="private unrelated output")
            notify("turn/completed", turn={"id": "unknown-old-turn", "status": "completed"})
            notify("item/agentMessage/delta", turnId="actual-turn", itemId="answer", delta="Actual result")
            notify("turn/completed", turn={"id": "actual-turn", "status": "completed"})
            assert binding.status == "active"
            assert binding.native_turn_id is None
            assert engine._message_text == {}
            return {"turn": {"id": "actual-turn"}}

    engine._conn = Connection()
    monkeypatch.setattr(engine, "_ensure_conn", ensure_conn)
    assert asyncio.run(engine.send(binding, "synthetic current request")) == "actual-turn"
    assert binding.native_turn_id == "actual-turn"
    assert binding.status == "idle"
    proof = next(event for event in events if event.type == "engine.turn.bound")
    assert proof.native == {"session_id": binding.native_session_id, "turn_id": "actual-turn", "dispatch_id": "new-task"}
    assert "private unrelated output" not in json.dumps([event.payload for event in events])
    assert "".join(event.payload["delta"] for event in events if event.type == "engine.message.delta") == "Actual result"
    assert engine._unproven_notifications == {}


def test_codex_unknown_buffer_overflow_reports_host_scoped_failure_without_payload(monkeypatch):
    engine, binding, events, notify = _codex_wire_fixture()
    binding.extensions_snapshot["termx_dispatch_id"] = "current-task"
    monkeypatch.setattr("termx.engines.codex.MAX_UNPROVEN_EVENTS", 1)
    notify("item/agentMessage/delta", turnId="unknown-turn", itemId="answer", delta="private unknown content")
    notify("turn/completed", turn={"id": "unknown-turn", "status": "completed"})
    assert len(events) == 1
    assert events[0].type == "engine.turn.correlation.failed"
    assert events[0].native == {"session_id": binding.native_session_id, "dispatch_id": "current-task"}
    assert "private unknown content" not in json.dumps(events[0].payload)
    assert engine._unproven_notifications == {}
    assert engine._message_text == {}
    assert binding.native_turn_id is None


@pytest.mark.parametrize("stale", ["retired", "persisted_owner", "different_active"])
@pytest.mark.parametrize("method,expected", [
    ("item/commandExecution/requestApproval", {"decision": "decline"}),
    ("item/fileChange/requestApproval", {"decision": "decline"}),
    ("item/permissions/requestApproval", {"permissions": {}, "scope": "turn"}),
])
def test_codex_stale_request_is_refused_before_browser_permit_or_approval(monkeypatch, stale, method, expected):
    engine, binding, events, notify = _codex_wire_fixture()
    counters = {"authorize": 0, "permit": 0, "approval": 0}

    async def authorize(*args, **kwargs):
        counters["authorize"] += 1
        return (None, None, None)

    async def consume(*args, **kwargs):
        counters["permit"] += 1

    async def approval(*args, **kwargs):
        counters["approval"] += 1
        raise AssertionError("stale request reached approval sink")

    monkeypatch.setattr("termx.engines.action_review.authorize", authorize)
    monkeypatch.setattr("termx.engines.action_review.consume_reviewed", consume)
    engine.browser_service = object()
    engine._approval_sink = approval
    if stale == "retired":
        notify("turn/started", turn={"id": "old-turn"})
        notify("turn/completed", turn={"id": "old-turn", "status": "completed"})
    elif stale == "persisted_owner":
        binding.extensions_snapshot.update(termx_dispatch_id="new-task", termx_turn_owners={"old-turn": "old-task"})
    else:
        notify("turn/started", turn={"id": "new-turn"})
    params = {"threadId": binding.native_session_id, "turnId": "old-turn", "itemId": "old-action", "permissions": {"network": True}}
    assert asyncio.run(engine._on_server_request(method, params)) == expected
    assert counters == {"authorize": 0, "permit": 0, "approval": 0}
    assert engine._pending_decisions == {}
    assert engine._review_items == {}


def test_codex_fast_unknown_current_request_keeps_real_approval_path(monkeypatch):
    engine, binding, events, notify = _codex_wire_fixture()
    binding.extensions_snapshot.update(termx_dispatch_id="new-task", termx_turn_owners={"old-turn": "old-task"})
    calls = []

    async def authorize(*args, **kwargs):
        calls.append("authorize")
        return None

    async def approval(binding_id, token, method, params, kind):
        calls.append("approval")
        await engine.respond_approval(binding, token, "approve")
        return "real-fixture-approval"

    monkeypatch.setattr("termx.engines.action_review.authorize", authorize)
    engine.browser_service = object()
    engine._approval_sink = approval
    params = {"threadId": binding.native_session_id, "turnId": "new-turn", "itemId": "new-action"}
    assert asyncio.run(engine._on_server_request("item/commandExecution/requestApproval", params)) == {"decision": "accept"}
    assert calls == ["authorize", "approval"]
    assert engine._pending_decisions == {}


def test_codex_fast_completion_before_start_rpc_response_preserves_idle(monkeypatch):
    engine, binding, events, notify = _codex_wire_fixture()
    binding.extensions_snapshot["termx_dispatch_id"] = "fast-task"

    async def ensure_conn():
        return None

    class Connection:
        async def request(self, method, params, **kwargs):
            assert method == "turn/start"
            notify("turn/started", turn={"id": "fast-turn"})
            notify("item/agentMessage/delta", turnId="fast-turn", itemId="answer", delta="Fast result")
            notify("turn/completed", turn={"id": "fast-turn", "status": "completed"})
            return {"turn": {"id": "fast-turn"}}

    engine._conn = Connection()
    monkeypatch.setattr(engine, "_ensure_conn", ensure_conn)
    assert asyncio.run(engine.send(binding, "synthetic fast request")) == "fast-turn"
    assert binding.native_turn_id == "fast-turn"
    assert binding.status == "idle"
    applied = next(event for event in events if event.type == "engine.configuration.applied")
    assert applied.native["dispatch_id"] == "fast-task"
    assert "turn_id" not in applied.native
    assert "".join(event.payload["delta"] for event in events if event.type == "engine.message.delta") == "Fast result"


@pytest.mark.parametrize("new_send", [False, True])
def test_codex_obsolete_start_rpc_response_cannot_overwrite_new_turn(monkeypatch, new_send):
    engine, binding, events, notify = _codex_wire_fixture()

    async def run():
        first_completed = asyncio.Event()
        release_first_rpc = asyncio.Event()

        async def ensure_conn():
            return None

        class Connection:
            async def request(self, method, params, **kwargs):
                assert method == "turn/start"
                if params["input"][0]["text"] == "old request":
                    notify("turn/started", turn={"id": "old-turn"})
                    notify("turn/completed", turn={"id": "old-turn", "status": "completed"})
                    first_completed.set()
                    await release_first_rpc.wait()
                    return {"turn": {"id": "old-turn"}}
                notify("turn/started", turn={"id": "new-turn"})
                notify("item/agentMessage/delta", turnId="new-turn", itemId="answer", delta="Current answer")
                return {"turn": {"id": "new-turn"}}

        engine._conn = Connection()
        monkeypatch.setattr(engine, "_ensure_conn", ensure_conn)
        binding.extensions_snapshot["termx_dispatch_id"] = "old-task"
        old_send = asyncio.create_task(engine.send(binding, "old request"))
        await first_completed.wait()
        binding.extensions_snapshot["termx_dispatch_id"] = "new-task"
        if new_send:
            assert await engine.send(binding, "new request") == "new-turn"
        else:
            # Model the gateway rebinding while the older send still awaits its
            # reply. Actual notifications can arrive before the newer RPC reply.
            binding.native_turn_id = None
            notify("turn/started", turn={"id": "new-turn"})
            notify("item/agentMessage/delta", turnId="new-turn", itemId="answer", delta="Current answer")
        current_text = dict(engine._message_text)
        release_first_rpc.set()
        assert await old_send == "old-turn"
        assert binding.native_turn_id == "new-turn"
        assert binding.status == "active"
        assert engine._message_text == current_text

    asyncio.run(run())


def test_codex_late_old_turn_cannot_finish_new_gateway_task(tmp_path):
    store = AgentStore(tmp_path / "turns.sqlite3", tmp_path / "artifacts")
    gateway = EngineGateway(store, store.append_event)
    engine = CodexEngine(event_sink=gateway.on_engine_event)
    binding = EngineSessionBinding.new("codex", "wire-thread", status="active")
    engine._bindings[binding.binding_id] = gateway._bindings[binding.binding_id] = binding
    engine._threads[binding.native_session_id] = binding.binding_id

    def new_task():
        task = store.create_task(prompt="synthetic wire turn", cwd=str(tmp_path),
                                 provider_id="engine:fixture", model="fixture", limits={},
                                 engine="codex", status="running")
        gateway._binding_task[binding.binding_id] = task["id"]
        gateway._prepare_turn_dispatch(binding, task["id"])
        return task

    def notify(method, **params):
        engine._on_notification(method, {"threadId": binding.native_session_id, **params})

    try:
        old = new_task()
        notify("turn/started", turn={"id": "old-turn"})
        notify("item/agentMessage/delta", turnId="old-turn", itemId="answer", delta="Old result")
        notify("turn/completed", turn={"id": "old-turn", "status": "completed"})
        new = new_task()
        notify("turn/started", turn={"id": "new-turn"})
        notify("item/agentMessage/delta", turnId="new-turn", itemId="answer", delta="New result")
        notify("item/agentMessage/delta", turnId="old-turn", itemId="answer", delta="stale result")
        notify("turn/completed", turn={"id": "old-turn", "status": "completed"})
        assert store.get_task(new["id"])["status"] == "running"
        assert store.get_task(old["id"])["result"] == "Old result"
        assert binding.native_turn_id == "new-turn" and binding.status == "active"
        notify("turn/completed", turn={"id": "new-turn", "status": "completed"})
        assert store.get_task(new["id"])["result"] == "New result"
        assert store.get_task(new["id"])["status"] == "completed"
    finally:
        store.close()
