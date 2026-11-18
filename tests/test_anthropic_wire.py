"""Responses-style history <-> Anthropic Messages wire mapping (pure functions, no network)."""
from __future__ import annotations

import json

import pytest

from termx.agent.anthropic_wire import (
    anthropic_tools,
    messages_from_items,
    response_from_message,
    strip_thinking,
)
from termx.agent.chat_wire import NO_RESULT

THINKING = {"type": "thinking", "thinking": "", "signature": "EqQBCkYIARgCKkD-opaque"}
REDACTED = {"type": "redacted_thinking", "data": "EmwKAhgB-opaque"}


def call(call_id: str, name: str = "run_shell", **arguments) -> dict:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


def result(call_id: str, output="ok") -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def assistant(text: str) -> dict:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def test_user_text_becomes_one_user_message():
    assert messages_from_items([{"role": "user", "content": "hello"}]) == [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    ]


def test_function_call_becomes_tool_use_and_its_output_a_tool_result():
    messages = messages_from_items([
        {"role": "user", "content": "go"},
        call("toolu_1", command="ls"),
        result("toolu_1", "a.py"),
    ])
    assert messages[1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "run_shell", "input": {"command": "ls"}}]}
    assert messages[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.py"}]}


def test_parallel_calls_share_one_assistant_message_and_all_results_one_user_message_in_call_order():
    messages = messages_from_items([
        {"role": "user", "content": "go"},
        call("a"), call("b"),
        result("b", "second"), result("a", "first"),
    ])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert [b["id"] for b in messages[1]["content"]] == ["a", "b"]
    assert [(b["tool_use_id"], b["content"]) for b in messages[2]["content"]] == [("a", "first"), ("b", "second")]


def test_user_message_arriving_while_calls_are_pending_follows_the_results_in_the_same_message():
    messages = messages_from_items([
        {"role": "user", "content": "go"},
        call("a"),
        {"role": "user", "content": "steer: stop early"},
        result("a"),
    ])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    kinds = [b["type"] for b in messages[2]["content"]]
    assert kinds == ["tool_result", "text"]  # results must come first


def test_unanswered_call_gets_a_placeholder_and_orphan_output_is_dropped():
    messages = messages_from_items([
        {"role": "user", "content": "go"},
        call("a"),
        result("ghost", "nobody asked"),
        call("b"),
    ])
    flat = json.dumps(messages)
    assert "ghost" not in flat
    placeholders = [b for m in messages for b in m["content"] if b.get("type") == "tool_result"]
    assert [(b["tool_use_id"], b["content"]) for b in placeholders] == [("a", NO_RESULT), ("b", NO_RESULT)]


def test_new_assistant_turn_after_results_starts_a_new_message():
    messages = messages_from_items([
        {"role": "user", "content": "go"},
        call("a"), result("a"),
        assistant("done"),
    ])
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    assert messages[3]["content"] == [{"type": "text", "text": "done"}]
    # A history that ends on an assistant turn would be a prefill, which current models reject.
    assert messages[4]["role"] == "user"


def test_thinking_blocks_are_replayed_unchanged_ahead_of_text_and_tool_use():
    messages = messages_from_items([
        {"role": "user", "content": "go"},
        dict(THINKING), dict(REDACTED), assistant("on it"), call("a", command="ls"),
        result("a"),
    ])
    assert messages[1]["content"][:3] == [THINKING, REDACTED, {"type": "text", "text": "on it"}]
    assert messages[1]["content"][3]["type"] == "tool_use"


def test_assistant_text_is_replayed_byte_for_byte():
    # Whitespace in earlier text is part of the prefix a later thinking block is bound to.
    text = "  padded\n"
    messages = messages_from_items([{"role": "user", "content": "go"}, assistant(text), {"role": "user", "content": "next"}])
    assert messages[1]["content"] == [{"type": "text", "text": text}]


def test_unknown_blocks_round_trip_through_the_passthrough_item():
    block = {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}}
    out = response_from_message({"id": "m", "content": [block, {"type": "text", "text": "hi"}], "stop_reason": "end_turn",
                                 "usage": {"input_tokens": 1, "output_tokens": 1}})
    messages = messages_from_items([{"role": "user", "content": "go"}, *out["output"]])
    assert messages[1]["content"][0] == block


def test_images_become_image_blocks():
    messages = messages_from_items([{"role": "user", "content": [
        {"type": "input_text", "text": "look"},
        {"type": "input_image", "image_url": "data:image/png;base64,QUJD"},
        {"type": "input_image", "image_url": {"url": "https://example.test/a.jpg"}},
        {"type": "input_image", "image_url": "data:image/bmp;base64,QUJD"},
    ]}])
    blocks = messages[0]["content"]
    assert blocks[0] == {"type": "text", "text": "look"}
    assert blocks[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    assert blocks[2] == {"type": "image", "source": {"type": "url", "url": "https://example.test/a.jpg"}}
    assert blocks[3]["type"] == "text" and "omitted" in blocks[3]["text"]


def test_tool_output_parts_keep_images_inside_the_tool_result():
    messages = messages_from_items([
        {"role": "user", "content": "go"}, call("a"),
        result("a", [{"type": "input_text", "text": "shot"}, {"type": "input_image", "image_url": "data:image/png;base64,QUJD"}]),
    ])
    content = messages[2]["content"][0]["content"]
    assert [b["type"] for b in content] == ["text", "image"]


def test_empty_text_and_malformed_arguments_do_not_produce_invalid_blocks():
    messages = messages_from_items([
        {"role": "user", "content": "go"}, assistant("   "),
        {"type": "function_call", "call_id": "a", "name": "t", "arguments": "{not json"}, result("a"),
    ])
    assistant_blocks = messages[1]["content"]
    assert [b["type"] for b in assistant_blocks] == ["tool_use"] and assistant_blocks[0]["input"] == {}


def test_system_items_fold_into_user_text_and_provider_specific_items_are_ignored():
    messages = messages_from_items([
        {"role": "user", "content": "go"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"role": "developer", "content": "be brief"},
    ])
    assert [m["role"] for m in messages] == ["user"]
    texts = [b["text"] for b in messages[0]["content"]]
    assert texts[0] == "go" and "be brief" in texts[1]


def test_tools_use_input_schema_and_eager_streaming():
    tools = anthropic_tools([
        {"type": "function", "name": "run_shell", "description": "Run", "parameters": {"type": "object", "properties": {"c": {"type": "string"}}}},
        {"type": "function", "name": "noargs", "description": ""},
        {"type": "computer", "name": "ignored"},
    ])
    assert tools[0] == {"name": "run_shell", "description": "Run", "eager_input_streaming": True,
                        "input_schema": {"type": "object", "properties": {"c": {"type": "string"}}}}
    assert tools[1]["input_schema"] == {"type": "object", "properties": {}} and len(tools) == 2


def test_response_maps_text_tool_use_thinking_and_usage():
    body = response_from_message({
        "id": "msg_1", "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_1", "name": "run_shell", "input": {"command": "ls"}, "caller": {"type": "direct"}},
        ],
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 190, "output_tokens": 7},
    })
    assert body["id"] == "msg_1" and body["status"] == "completed"
    assert body["output"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert body["output"][1]["content"][0]["text"] == "Checking."
    assert body["output"][2] == {"type": "function_call", "call_id": "toolu_1", "name": "run_shell",
                                 "arguments": json.dumps({"command": "ls"})}
    # Anthropic reports uncached input only; the prompt size is the sum, matching other providers.
    assert body["usage"]["input_tokens"] == 1000 and body["usage"]["output_tokens"] == 7
    assert body["usage"]["cache_read_input_tokens"] == 800 and body["usage"]["cache_creation_input_tokens"] == 190


@pytest.mark.parametrize("stop,reason", [("max_tokens", "max_output_tokens"), ("model_context_window_exceeded", "context_window_exceeded")])
def test_truncated_responses_are_incomplete(stop, reason):
    body = response_from_message({"id": "m", "stop_reason": stop, "content": [{"type": "text", "text": "cut"}], "usage": {}})
    assert body["status"] == "incomplete" and body["incomplete_details"] == {"reason": reason}


def test_streamed_raw_tool_input_wins_and_malformed_input_is_rejected():
    message = {"id": "m", "stop_reason": "tool_use", "usage": {},
               "content": [{"type": "tool_use", "id": "t", "name": "x", "input": {"truncated": True}}]}
    body = response_from_message(message, {0: '{"command": "ls"}'})
    assert json.loads(body["output"][0]["arguments"]) == {"command": "ls"}
    with pytest.raises(ValueError):
        response_from_message(message, {0: '{"command": "l'})
    with pytest.raises(ValueError):
        response_from_message(message, {0: "[1]"})


def test_strip_thinking_removes_only_thinking_items():
    items = [{"role": "user", "content": "x"}, dict(THINKING), dict(REDACTED), assistant("a"), call("c"), result("c")]
    assert strip_thinking(items) == [items[0], items[3], items[4], items[5]]
    assert strip_thinking([items[0]]) == [items[0]]
