"""Dispatch ownership with in-process fake coroutines only.

No engine executable, network, account credential, or inference is involved.
ACP unsolicited session_update notifications intentionally remain unscoped:
their negotiated wire payload has no per-prompt turn identifier.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, SystemMessage, TextBlock

from termx.engines.acp import AcpEngine
from termx.engines.claude import ClaudeEngine
from termx.engines.types import EngineSessionBinding


def _result(session_id):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                         is_error=False, num_turns=1, session_id=session_id)


def _state(driver, label, entered, outcome):
    if driver == "acp":
        async def prompt(**_kwargs):
            entered.set()
            await outcome
            return SimpleNamespace(stop_reason="end_turn", usage=None)
        return {"conn": SimpleNamespace(prompt=prompt), "proc": SimpleNamespace(returncode=None)}

    class Client:
        async def connect(self):
            pass

        async def query(self, _prompt):
            pass

        async def receive_response(self):
            # The new reader has a partially assembled SDK message before the
            # old reader finishes. Its de-duplication map must remain its own.
            if label == "new":
                yield SystemMessage(subtype="init", data={"session_id": "new-native-session"})
                yield StreamEvent(uuid="new-start", session_id="new-native-session", parent_tool_use_id=None,
                                  event={"type": "message_start", "message": {"id": "new-message"}})
                yield StreamEvent(uuid="new-delta", session_id="new-native-session", parent_tool_use_id=None,
                                  event={"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "New partial"}})
            entered.set()
            await outcome
            if label == "old":
                yield SystemMessage(subtype="init", data={"session_id": "old-native-session"})
                yield StreamEvent(uuid="old-start", session_id="old-native-session", parent_tool_use_id=None,
                                  event={"type": "message_start", "message": {"id": "old-message"}})
                yield StreamEvent(uuid="old-delta", session_id="old-native-session", parent_tool_use_id=None,
                                  event={"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "Old partial"}})
            yield AssistantMessage(content=[TextBlock("New partial completed" if label == "new" else "Old partial completed")],
                                   model="fixture", message_id=label+"-message")
            yield _result(label+"-native-session")

    return {"client": Client()}


@pytest.mark.parametrize("driver", ["acp", "claude"])
@pytest.mark.parametrize("late", ["completed", "error", "cancelled"])
def test_late_driver_completion_keeps_original_dispatch_and_cannot_finish_reused_binding(driver, late):
    async def run():
        events = []
        engine = (AcpEngine if driver == "acp" else ClaudeEngine)(event_sink=lambda _id, event: events.append(event))
        binding = EngineSessionBinding.new(driver, "pending_original" if driver == "claude" else "native-session")
        engine._bindings[binding.binding_id] = binding
        old_entered, new_entered = asyncio.Event(), asyncio.Event()
        old_outcome, new_outcome = asyncio.get_running_loop().create_future(), asyncio.get_running_loop().create_future()
        old = _state(driver, "old", old_entered, old_outcome)
        engine._sessions[binding.binding_id] = old
        binding.extensions_snapshot["termx_dispatch_id"] = "host-old-task"
        await engine.send(binding, "Old authorized turn")
        reader_key = "turn_task" if driver == "acp" else "reader"
        old_reader = old[reader_key]
        await old_entered.wait()

        # Resume/replacement reuses the public binding, but owns a fresh state
        # and host dispatch. No vendor process is created in this fixture.
        new = _state(driver, "new", new_entered, new_outcome)
        engine._sessions[binding.binding_id] = new
        binding.extensions_snapshot["termx_dispatch_id"] = "host-new-task"
        await engine.send(binding, "New authorized turn")
        await new_entered.wait()
        new_reader = new[reader_key]
        binding.updated_at = 123.0
        if late == "cancelled":
            old_reader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old_reader
        else:
            if late == "error":
                if driver == "acp":
                    old["proc"].returncode = 1
                old_outcome.set_exception(RuntimeError("Old fixture transport failed"))
            else:
                old_outcome.set_result(None)
            await old_reader

        completed = [event for event in events if event.type == "engine.turn.completed"]
        assert len(completed) == 1
        assert completed[0].native["dispatch_id"] == "host-old-task"
        assert completed[0].payload["status"] == {"completed": "completed", "error": "failed", "cancelled": "interrupted"}[late]
        assert binding.status == "active"
        assert binding.updated_at == 123.0
        assert engine._sessions[binding.binding_id] is new
        assert new[reader_key] is new_reader and not new_reader.done()
        if driver == "acp":
            assert completed[0].native["session_id"] == "native-session"
            assert "turn_id" not in completed[0].native
        else:
            assert binding.native_session_id == "new-native-session"
            assert new["stream_messages"][None]["id"] == "new-message"
            if late == "completed":
                assert completed[0].native["session_id"] == "old-native-session"
                assert all(event.native.get("dispatch_id") == "host-old-task" for event in events
                           if event.payload.get("delta", "").startswith("Old"))
        new_outcome.set_result(None)
        await new_reader
        assert binding.status == "idle"
        assert [event.native["dispatch_id"] for event in events if event.type == "engine.turn.completed"] == ["host-old-task", "host-new-task"]
        if driver == "claude":
            assert "".join(event.payload["delta"] for event in events
                           if event.type == "engine.message.delta" and event.native.get("dispatch_id") == "host-new-task") == "New partial completed"
    asyncio.run(run())


@pytest.mark.parametrize("driver", ["acp", "claude"])
def test_standalone_driver_generation_guard_does_not_require_a_host_dispatch_id(driver):
    async def run():
        events, state_ref = [], {}
        binding = EngineSessionBinding.new(driver, "native-session")

        def sink(_identifier, event):
            events.append(event)
            if event.type == "engine.turn.completed":
                # A completion observer installs a newer generation before the
                # old driver's finally block can run, without any host token.
                state_ref["state"]["dispatch_generation"] = object()
                binding.status = "active"
                binding.updated_at = 456.0

        engine = (AcpEngine if driver == "acp" else ClaudeEngine)(event_sink=sink)
        entered, outcome = asyncio.Event(), asyncio.get_running_loop().create_future()
        state = _state(driver, "old", entered, outcome)
        state_ref["state"] = state
        engine._sessions[binding.binding_id] = state
        engine._bindings[binding.binding_id] = binding
        await engine.send(binding, "Standalone fixture")
        await entered.wait()
        reader = state["turn_task" if driver == "acp" else "reader"]
        outcome.set_result(None)
        await reader
        assert binding.status == "active" and binding.updated_at == 456.0
        assert all("dispatch_id" not in event.native for event in events)
    asyncio.run(run())
