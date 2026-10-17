"""Claude native event/lifecycle contracts using the pinned SDK's real models.

No vendor executable, account credentials, network or paid turns are used.
"""
from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import (
    AssistantMessage, ResultMessage, StreamEvent, SystemMessage, TextBlock,
    ToolResultBlock, ToolUseBlock, UserMessage,
)

from termx.engines.claude import ClaudeEngine
from termx.engines.types import EffectiveRunConfiguration, EngineSessionBinding


def _fixture():
    events = []
    engine = ClaudeEngine(event_sink=lambda _id, event: events.append(event))
    binding = EngineSessionBinding.new("claude", "native-session")
    engine._bindings[binding.binding_id] = binding
    engine._sessions[binding.binding_id] = {"binding": binding}
    return engine, binding, events


def _stream(engine, binding, event, *, parent=None):
    engine._map_message(binding, StreamEvent(uuid="fixture", session_id="native-session",
                                            event=event, parent_tool_use_id=parent))


def test_claude_stream_text_is_immediate_and_final_snapshots_do_not_duplicate_it():
    engine, binding, events = _fixture()
    _stream(engine, binding, {"type": "message_start", "message": {"id": "root-message"}})
    _stream(engine, binding, {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "Hello "}})
    assert events[-1].type == "engine.message.delta"
    assert events[-1].payload["delta"] == "Hello "
    # Interleaved child output retains its native parent tool call; it must
    # neither steal the root stream's de-duplication state nor merge its prose.
    _stream(engine, binding, {"type": "message_start", "message": {"id": "child-message"}}, parent="agent-call")
    _stream(engine, binding, {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "Child"}}, parent="agent-call")
    _stream(engine, binding, {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "world"}})
    engine._map_message(binding, AssistantMessage(content=[TextBlock("Hello world")], model="fixture",
                                                 message_id="root-message"))
    engine._map_message(binding, AssistantMessage(content=[TextBlock("Child answer")], model="fixture",
                                                 message_id="child-message", parent_tool_use_id="agent-call"))
    texts = [event.payload for event in events if event.type == "engine.message.delta"]
    assert "".join(item["delta"] for item in texts if not item.get("parent_call_id")) == "Hello world"
    assert "".join(item["delta"] for item in texts if item.get("parent_call_id") == "agent-call") == "Child answer"
    assert len(texts) == 4  # two root deltas + child partial + its missing suffix
    assert all(item.get("message_id") in {"root-message", "child-message"} for item in texts)


def test_claude_message_without_stream_retains_markdown_and_complete_tool_calls():
    engine, binding, events = _fixture()
    markdown = "## Result\n\n```python\nprint('ready')\n```\n"
    engine._map_message(binding, AssistantMessage(content=[TextBlock(markdown)], model="fixture"))
    assert events[-1].payload["delta"] == markdown
    _stream(engine, binding, {"type": "content_block_delta", "index": 1,
                             "delta": {"type": "input_json_delta", "partial_json": '{"path":'}})
    assert not any(event.type == "engine.tool.started" for event in events)
    engine._map_message(binding, AssistantMessage(content=[ToolUseBlock("read-call", "Read", {"path": "main.py"})],
                                                 model="fixture", parent_tool_use_id="agent-call"))
    started = events[-1]
    assert started.type == "engine.tool.started"
    assert started.payload == {"call_id": "read-call", "tool": "Read", "input": {"path": "main.py"},
                               "parent_call_id": "agent-call"}


@pytest.mark.parametrize("failed", [False, True])
def test_claude_tool_results_keep_call_identity_and_actual_outcome(failed):
    engine, binding, events = _fixture()
    engine._map_message(binding, UserMessage(
        content=[ToolResultBlock(tool_use_id="read-call", content="password=fixture-secret-value done", is_error=failed)],
        parent_tool_use_id="agent-call",
    ))
    event = events[-1]
    assert event.type == ("engine.tool.failed" if failed else "engine.tool.completed")
    assert event.payload["call_id"] == "read-call"
    assert event.payload["parent_call_id"] == "agent-call"
    assert event.payload["status"] == ("failed" if failed else "completed")
    assert "fixture-secret-value" not in str(event.payload)
    assert event.payload["output"] == {"value": "[redacted] done"}


@pytest.mark.parametrize("terminal", ["eof", "error", "interrupted", "completed"])
def test_claude_requires_terminal_result_and_preserves_provider_error(terminal):
    async def run():
        engine, binding, events = _fixture()
        class Client:
            async def connect(self): pass
            async def query(self, prompt): pass
            async def receive_response(self):
                yield AssistantMessage(content=[TextBlock("Partial answer")], model="fixture")
                if terminal != "eof":
                    yield ResultMessage(subtype="error_during_execution" if terminal == "error" else "success",
                                        duration_ms=1, duration_api_ms=1, is_error=terminal == "error", num_turns=1,
                                        session_id="native-session", errors=["Fixture provider unavailable"] if terminal == "error" else None,
                                        terminal_reason="aborted_tools" if terminal == "interrupted" else None)
        await engine._run_turn(binding, {"client": Client()}, "fixture request")
        result = [event for event in events if event.type == "engine.turn.completed"]
        assert len(result) == 1
        assert result[0].payload["status"] == ({"eof": "failed", "error": "failed", "interrupted": "interrupted",
                                                 "completed": "completed"}[terminal])
        if terminal == "eof":
            assert "before a terminal result" in result[0].payload["error"]["message"]
        elif terminal == "error":
            assert result[0].payload["error"]["message"] == "Fixture provider unavailable"
    asyncio.run(run())


def test_claude_failed_initialization_never_resumes_a_pending_placeholder(tmp_path, monkeypatch):
    async def run():
        options = []
        class Client:
            def __init__(self, *, options): self.options = options
            async def connect(self): options.append(self.options.resume)
            async def disconnect(self): pass
            async def query(self, prompt): pass
            async def receive_response(self):
                yield SystemMessage(subtype="init", data={"session_id": "real-native-session"})
                yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                    is_error=False, num_turns=1, session_id="real-native-session")
        monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", Client)
        engine = ClaudeEngine()
        cfg = EffectiveRunConfiguration(cwd=str(tmp_path))
        binding = await engine.create_session(cfg)
        assert binding.native_session_id.startswith("pending_")
        # A first connection failed before native init. Only this new user
        # turn is dispatched; there is no native session to resume yet.
        await engine.configure_session(binding, cfg)
        await engine.send(binding, "explicit follow-up")
        await engine._sessions[binding.binding_id]["reader"]
        assert options == [None]
        assert binding.native_session_id == "real-native-session"
        await engine.configure_session(binding, cfg)
        await engine.send(binding, "next explicit follow-up")
        await engine._sessions[binding.binding_id]["reader"]
        assert options == [None, "real-native-session"]
        await engine.shutdown()
    asyncio.run(run())
