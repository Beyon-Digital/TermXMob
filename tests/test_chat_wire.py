"""Chat Completions wire translation: Responses-style history <-> chat messages."""
from __future__ import annotations

import json
from typing import Any

import pytest

from termx.agent.chat_wire import (
    ChatStream,
    chat_tools,
    messages_from_items,
    response_from_chat,
)

SYSTEM = "You are Termx."


def _call(call_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}


def _output(call_id: str, text: str) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def _assistant(text: str) -> dict[str, Any]:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def test_system_prompt_and_plain_user_turns() -> None:
    messages = messages_from_items(SYSTEM, [{"role": "user", "content": "Task: hi"}, {"role": "user", "content": "more"}])
    assert messages == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Task: hi"},
        {"role": "user", "content": "more"},
    ]


def test_assistant_text_and_tool_calls_merge_into_one_message_followed_by_tool_results() -> None:
    items = [
        {"role": "user", "content": "go"},
        _assistant("Looking."),
        _call("c1", "read_file", path="a.py"),
        _call("c2", "read_file", path="b.py"),
        _output("c1", "A"),
        _output("c2", "B"),
    ]
    messages = messages_from_items(SYSTEM, items)
    assert messages[2] == {
        "role": "assistant",
        "content": "Looking.",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
            {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "b.py"}'}},
        ],
    }
    assert messages[3:] == [
        {"role": "tool", "tool_call_id": "c1", "content": "A"},
        {"role": "tool", "tool_call_id": "c2", "content": "B"},
    ]


def test_tool_call_without_text_has_null_content() -> None:
    messages = messages_from_items("", [_call("c1", "ls"), _output("c1", "ok")])
    assert messages[0]["content"] is None and messages[0]["role"] == "assistant"


def test_user_message_between_tool_results_waits_until_all_results_are_in() -> None:
    # The computer tool adds an image message right after its own result; strict
    # servers reject any non-tool message between an assistant tool_calls and its results.
    items = [
        _call("c1", "use_computer"),
        _call("c2", "read_file"),
        _output("c1", "Computer action completed."),
        {"role": "user", "content": [
            {"type": "input_text", "text": "Inspect the screenshot."},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"},
        ]},
        _output("c2", "text"),
    ]
    messages = messages_from_items("", items)
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool", "user"]
    assert messages[3]["content"] == [
        {"type": "text", "text": "Inspect the screenshot."},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ]


def test_missing_and_orphan_tool_results_never_reach_the_server() -> None:
    items = [
        _output("ghost", "orphan"),
        _call("c1", "run_shell"),
        {"role": "user", "content": "steer"},
        _assistant("done"),
    ]
    messages = messages_from_items("", items)
    assert [m["role"] for m in messages] == ["assistant", "tool", "user", "assistant"]
    assert messages[1]["tool_call_id"] == "c1"
    assert "no result" in messages[1]["content"].lower()
    assert all(m.get("tool_call_id") != "ghost" for m in messages)


def test_unresolved_call_at_end_gets_a_placeholder_result() -> None:
    messages = messages_from_items("", [_call("c1", "run_shell")])
    assert [m["role"] for m in messages] == ["assistant", "tool"]


def test_native_computer_items_have_no_chat_equivalent_and_are_dropped() -> None:
    items = [
        {"type": "computer_call", "call_id": "c1", "actions": []},
        {"type": "computer_call_output", "call_id": "c1", "output": {"image_url": "data:image/jpeg;base64,BBBB"}},
        {"role": "user", "content": "go"},
    ]
    assert messages_from_items("", items) == [{"role": "user", "content": "go"}]


def test_reasoning_items_are_dropped() -> None:
    messages = messages_from_items("", [{"type": "reasoning", "summary": []}, {"role": "user", "content": "x"}])
    assert messages == [{"role": "user", "content": "x"}]


def test_chat_tools_wrap_function_schemas() -> None:
    schema = {"type": "function", "name": "ls", "description": "List", "parameters": {"type": "object"}}
    assert chat_tools([schema]) == [
        {"type": "function", "function": {"name": "ls", "description": "List", "parameters": {"type": "object"}}}
    ]


CHAT_WITH_TOOLS = {
    "id": "chatcmpl-1",
    "choices": [{
        "index": 0,
        "finish_reason": "tool_calls",
        "message": {
            "role": "assistant",
            "content": "Reading.",
            "tool_calls": [{"id": "call_a", "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}],
        },
    }],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
              "prompt_tokens_details": {"cached_tokens": 100}},
}


def test_response_from_chat_yields_responses_items_and_usage() -> None:
    body = response_from_chat(CHAT_WITH_TOOLS)
    assert body["id"] == "chatcmpl-1" and body["status"] == "completed"
    assert body["output"] == [
        _assistant("Reading."),
        {"type": "function_call", "call_id": "call_a", "name": "read_file", "arguments": '{"path": "a.py"}'},
    ]
    assert body["usage"]["input_tokens"] == 120
    assert body["usage"]["output_tokens"] == 30
    assert body["usage"]["total_tokens"] == 150
    assert body["usage"]["input_tokens_details"] == {"cached_tokens": 100}


def test_round_trip_history_survives_translation() -> None:
    body = response_from_chat(CHAT_WITH_TOOLS)
    history = [{"role": "user", "content": "go"}, *body["output"], _output("call_a", "contents")]
    messages = messages_from_items("", history)
    assert messages[1] == CHAT_WITH_TOOLS["choices"][0]["message"]
    assert messages[2] == {"role": "tool", "tool_call_id": "call_a", "content": "contents"}


def test_response_from_chat_handles_missing_call_ids_and_dict_arguments() -> None:
    body = response_from_chat({"id": "x", "choices": [{"finish_reason": "tool_calls", "message": {
        "content": None,
        "tool_calls": [{"function": {"name": "ls", "arguments": {"path": "."}}},
                       {"function": {"name": "pwd", "arguments": ""}}]}}]})
    calls = [item for item in body["output"] if item["type"] == "function_call"]
    assert [c["call_id"] for c in calls] == ["call_x_0", "call_x_1"]
    assert json.loads(calls[0]["arguments"]) == {"path": "."}
    assert calls[1]["arguments"] == "{}"
    assert body["usage"] == {}


def test_response_from_chat_accepts_content_parts() -> None:
    body = response_from_chat({"id": "x", "choices": [{"finish_reason": "stop", "message": {
        "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]})
    assert body["output"] == [_assistant("a\nb")]


@pytest.mark.parametrize("reason,expected", [("length", "max_output_tokens"), ("content_filter", "content_filter")])
def test_truncated_or_filtered_choice_is_incomplete(reason: str, expected: str) -> None:
    body = response_from_chat({"id": "x", "choices": [{"finish_reason": reason, "message": {"content": "par"}}]})
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": expected}


def test_response_without_choices_is_rejected() -> None:
    with pytest.raises(ValueError):
        response_from_chat({"id": "x", "choices": []})


def test_stream_assembles_text_and_fragmented_tool_calls() -> None:
    stream = ChatStream()
    events = [
        {"id": "s1", "choices": [{"delta": {"role": "assistant", "content": "Hel"}}]},
        {"id": "s1", "choices": [{"delta": {"content": "lo"}}]},
        {"id": "s1", "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_z", "type": "function", "function": {"name": "read_file", "arguments": ""}}]}}]},
        {"id": "s1", "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"pa'}}]}}]},
        {"id": "s1", "choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th": "x"}'}}]}}]},
        {"id": "s1", "choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"id": "s1", "choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
    ]
    texts: list[str] = []
    fragments: list[tuple[str, str]] = []
    for event in events:
        text, args = stream.feed(event)
        if text:
            texts.append(text)
        fragments.extend(args)
    assert texts == ["Hel", "lo"]
    assert fragments == [("call_z", '{"pa'), ("call_z", 'th": "x"}')]
    body = response_from_chat(stream.body())
    assert body["output"] == [
        _assistant("Hello"),
        {"type": "function_call", "call_id": "call_z", "name": "read_file", "arguments": '{"path": "x"}'},
    ]
    assert body["usage"]["input_tokens"] == 7 and body["usage"]["output_tokens"] == 3


def test_stream_without_finish_reason_is_not_complete() -> None:
    stream = ChatStream()
    stream.feed({"id": "s", "choices": [{"delta": {"content": "x"}}]})
    assert stream.finished is False
    stream.feed({"id": "s", "choices": [{"delta": {}, "finish_reason": "stop"}]})
    assert stream.finished is True


def test_stream_separates_parallel_calls_that_all_use_index_zero() -> None:
    stream = ChatStream()
    events = [
        {"index": 0, "id": "a", "function": {"name": "f", "arguments": '{"x":'}},
        {"index": 0, "function": {"arguments": "1}"}},
        {"index": 0, "id": "b", "function": {"name": "g", "arguments": '{"y":2}'}},
    ]
    fragments: list[tuple[str, str]] = []
    for call in events:
        fragments.extend(stream.feed({"choices": [{"delta": {"tool_calls": [call]}}]})[1])
    stream.feed({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
    calls = [item for item in response_from_chat(stream.body())["output"] if item["type"] == "function_call"]
    assert [(c["call_id"], c["name"], c["arguments"]) for c in calls] == [
        ("a", "f", '{"x":1}'), ("b", "g", '{"y":2}'),
    ]
    assert fragments == [("a", '{"x":'), ("a", "1}"), ("b", '{"y":2}')]


def test_call_ids_made_up_by_the_client_are_unique_per_reply() -> None:
    reply = {"choices": [{"finish_reason": "tool_calls", "message": {
        "tool_calls": [{"function": {"name": "ls", "arguments": "{}"}}]}}]}
    first = response_from_chat(reply)["output"][0]["call_id"]
    second = response_from_chat(reply)["output"][0]["call_id"]
    assert first != second

    ids: list[str] = []
    for _ in range(2):
        stream = ChatStream()
        _, fragments = stream.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "ls", "arguments": "{}"}}]}}]})
        stream.feed({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        call_id = response_from_chat(stream.body())["output"][0]["call_id"]
        assert fragments == [(call_id, "{}")]
        ids.append(call_id)
    assert ids[0] != ids[1]
