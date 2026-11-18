"""Anthropic Messages adapter against the real SDK on a mock transport (no network).

The SDK is the real ``anthropic`` package talking to ``httpx2.MockTransport``, so streaming
accumulation, typed exceptions and request building are the SDK's own. The live smoke test at the
bottom only runs with ``ANTHROPIC_API_KEY`` set.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

import anthropic
import httpx2
import pytest

from test_agent import FakeAdapter, build_manager, wait_for_status
from test_model_routing import _info, _routed
from termx.agent.metrics import TaskMetrics
from termx.agent.providers import (
    ANTHROPIC_MODELS,
    AnthropicMessagesAdapter,
    ProviderError,
    ProviderTurn,
    anthropic_base_url,
)
from termx.agent.retry import classify

MANIFEST = {"files": ["a.py"], "omitted": 0}
KEY = "sk-ant-test-secret-do-not-leak"
THINKING = {"type": "thinking", "thinking": "", "signature": "EqQB-sig-one"}


def sse(*events: dict[str, Any]) -> httpx2.Response:
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def reply(blocks: list[dict[str, Any]], stop: str = "end_turn", usage: dict[str, Any] | None = None,
          details: dict[str, Any] | None = None, message_id: str = "msg_1") -> httpx2.Response:
    """A streamed Messages reply: each block is delivered the way the API streams it."""
    usage = {"input_tokens": 10, "output_tokens": 5, **(usage or {})}
    events: list[dict[str, Any]] = [{"type": "message_start", "message": {
        "id": message_id, "type": "message", "role": "assistant", "content": [], "model": "claude-opus-5-5",
        "stop_reason": None, "stop_sequence": None, "usage": {**usage, "output_tokens": 1}}}]
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "text":
            events.append({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
            for chunk in block.get("chunks") or [block["text"]]:
                events.append({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": chunk}})
        elif kind == "tool_use":
            events.append({"type": "content_block_start", "index": index, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}})
            raw = block.get("raw") or json.dumps(block["input"])
            for cut in range(0, len(raw), 7):
                events.append({"type": "content_block_delta", "index": index,
                               "delta": {"type": "input_json_delta", "partial_json": raw[cut:cut + 7]}})
        elif kind == "thinking":
            events.append({"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            events.append({"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": block["signature"]}})
        elif kind == "redacted_thinking":
            events.append({"type": "content_block_start", "index": index, "content_block": block})
        events.append({"type": "content_block_stop", "index": index})
    delta: dict[str, Any] = {"stop_reason": stop, "stop_sequence": None}
    if details:
        delta["stop_details"] = details
    events.append({"type": "message_delta", "delta": delta, "usage": {"output_tokens": usage["output_tokens"]}})
    events.append({"type": "message_stop"})
    return sse(*events)


def error(status: int, kind: str, message: str, **headers: str) -> httpx2.Response:
    return httpx2.Response(status, headers=headers, json={"type": "error", "error": {"type": kind, "message": message}})


class Server:
    """Plays replies in order (a callable may build one from the request) and records every request."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.requests: list[httpx2.Request] = []
        self.bodies: list[dict[str, Any]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content or b"{}"))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item

    def sdk(self, base_url: str = "https://api.anthropic.com", key: str = KEY) -> Any:
        return anthropic.AsyncAnthropic(
            api_key=key, base_url=base_url, max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)),
        )


def make_adapter(server: Server, **kwargs: Any) -> AnthropicMessagesAdapter:
    options: dict[str, Any] = {"model": "claude-opus-5-5", "api_key": KEY, "capabilities": ["shell", "functions"]}
    options.update(kwargs)
    options.setdefault("sdk", server.sdk(anthropic_base_url(options.get("base_url", "")), options["api_key"]))
    return AnthropicMessagesAdapter(**options)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


SEEN: dict[str, list[Any]] = {"deltas": [], "events": []}


def stream(adapter: AnthropicMessagesAdapter, **kwargs: Any) -> ProviderTurn:
    """One streaming turn; the text deltas and tool-call events it emitted are left in ``SEEN``."""
    SEEN["deltas"], SEEN["events"] = [], []
    kwargs.setdefault("input_items", [{"role": "user", "content": "go"}])
    return run(adapter.stream_turn(prompt="task", cwd="/work", manifest=MANIFEST, on_delta=SEEN["deltas"].append,
                                   on_event=lambda kind, data: SEEN["events"].append((kind, data)), **kwargs))


# -- request shape ---------------------------------------------------------------------------


def test_streaming_text_turn_and_the_request_the_api_receives():
    server = Server(reply([{"type": "text", "text": "Hello there.", "chunks": ["Hello ", "there."]}],
                          usage={"input_tokens": 12, "output_tokens": 4}))
    turn = stream(make_adapter(server))
    assert turn.text == "Hello there." and turn.response_id == "msg_1" and not turn.calls
    assert SEEN["deltas"] == ["Hello ", "there."]
    assert turn.usage["input_tokens"] == 12 and turn.usage["output_tokens"] == 4

    request, body = server.requests[0], server.bodies[0]
    assert request.url.path == "/v1/messages" and request.headers["x-api-key"] == KEY
    assert body["stream"] is True and body["model"] == "claude-opus-5-5"
    # Caching: a breakpoint on the system block and automatic caching of the conversation.
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and "Termx" in body["system"][0]["text"]
    assert body["cache_control"] == {"type": "ephemeral"}
    # Adaptive thinking and effort; no budget, sampling or forced tool choice (current models 400 on them).
    assert body["thinking"]["type"] == "adaptive" and body["output_config"] == {"effort": "high"}
    for rejected in ("temperature", "top_p", "top_k", "tool_choice"):
        assert rejected not in body
    assert "budget_tokens" not in json.dumps(body["thinking"])
    # Tools carry input_schema and stream eagerly; the first user message is the task.
    assert body["tools"] and all(t["eager_input_streaming"] and "input_schema" in t for t in body["tools"])
    names = [t["name"] for t in body["tools"]]
    assert names == sorted(names)
    first = body["messages"][0]
    assert first["role"] == "user" and "Task:\ntask" in first["content"][0]["text"]
    # First party: drop (not fail on) stale thinking blocks, and re-run refusals on a fallback model.
    assert "thinking-binding-controls-2026-08-01" in request.headers["anthropic-beta"]
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert body["fallbacks"] == "default" and request.url.params.get("beta") == "true"


def test_a_proxy_base_url_gets_no_beta_controls_and_old_models_no_thinking():
    server = Server(reply([{"type": "text", "text": "ok"}]))
    adapter = make_adapter(server, base_url="https://gateway.example/anthropic/v1", model="claude-sonnet-4-5")
    with pytest.warns(DeprecationWarning):  # the SDK flags a retiring model id
        stream(adapter)
    request, body = server.requests[0], server.bodies[0]
    assert str(request.url).startswith("https://gateway.example/anthropic/v1/messages")
    assert "anthropic-beta" not in request.headers and request.url.params.get("beta") is None
    assert not {"thinking", "output_config", "fallbacks"} & body.keys()


def test_haiku_gets_thinking_but_no_server_side_fallback():
    server = Server(reply([{"type": "text", "text": "ok"}]))
    stream(make_adapter(server, model="claude-haiku-5-5"))
    body = server.bodies[0]
    assert body["thinking"]["type"] == "adaptive" and "fallbacks" not in body
    assert server.requests[0].headers["anthropic-beta"] == "thinking-binding-controls-2026-08-01"


def test_explicit_output_cap_is_sent_and_previous_response_id_is_never():
    server = Server(reply([{"type": "text", "text": "ok"}]))
    stream(make_adapter(server, max_output_tokens=2048), previous_response_id="resp_old")
    assert server.bodies[0]["max_tokens"] == 2048 and "previous_response_id" not in server.bodies[0]


def test_base_urls_are_normalised_to_the_origin():
    assert anthropic_base_url("https://api.anthropic.com/v1/") == "https://api.anthropic.com"
    assert anthropic_base_url("https://x.test/v1/messages") == "https://x.test"
    assert anthropic_base_url("") == "https://api.anthropic.com"


# -- tool use and thinking replay -------------------------------------------------------------


def test_tool_call_turn_then_replay_keeps_thinking_and_merges_results():
    tool = {"type": "tool_use", "id": "toolu_1", "name": "run_shell", "input": {"command": "ls", "purpose": "look"}}
    server = Server(
        reply([{"type": "thinking", "signature": "EqQB-sig-one"}, {"type": "text", "text": "Checking."}, tool],
              stop="tool_use", usage={"input_tokens": 3, "cache_creation_input_tokens": 900, "output_tokens": 30}),
        reply([{"type": "text", "text": "All done."}], usage={"input_tokens": 4, "cache_read_input_tokens": 920}),
    )
    adapter = make_adapter(server)
    first = stream(adapter)
    assert [(c.call_id, c.name, c.arguments) for c in first.calls] == [("toolu_1", "run_shell", tool["input"])]
    assert first.text == "Checking."
    # The call id and the streamed argument fragments reach the stream callback.
    events = SEEN["events"]
    assert events and {data["call_id"] for _, data in events} == {"toolu_1"}
    assert "".join(data["delta"] for _, data in events) == json.dumps(tool["input"])
    # The thinking block is kept, opaque and first.
    assert first.output_items[0] == THINKING

    history = [{"role": "user", "content": "go"}, *first.output_items,
               {"type": "function_call_output", "call_id": "toolu_1", "output": "a.py"}]
    second = stream(adapter, input_items=history)
    assert second.text == "All done." and not second.calls

    messages = server.bodies[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"][0] == THINKING  # replayed unchanged
    assert [b["type"] for b in messages[1]["content"]] == ["thinking", "text", "tool_use"]
    assert messages[2]["content"] == [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.py"}]


def test_redacted_thinking_round_trips():
    redacted = {"type": "redacted_thinking", "data": "EmwKAhgB-opaque"}
    server = Server(reply([redacted, {"type": "text", "text": "hi"}]), reply([{"type": "text", "text": "again"}]))
    adapter = make_adapter(server)
    first = stream(adapter)
    assert first.output_items[0] == redacted
    stream(adapter, input_items=[{"role": "user", "content": "go"}, *first.output_items, {"role": "user", "content": "more"}])
    assert server.bodies[1]["messages"][1]["content"][0] == redacted


def test_parallel_tool_calls_come_back_in_order():
    calls = [{"type": "tool_use", "id": f"toolu_{i}", "name": "read_file", "input": {"path": f"{i}.py"}} for i in range(2)]
    turn = stream(make_adapter(Server(reply(calls, stop="tool_use"))))
    assert [c.call_id for c in turn.calls] == ["toolu_0", "toolu_1"]


def test_malformed_streamed_tool_input_is_a_retryable_error_not_a_tool_call():
    bad = {"type": "tool_use", "id": "toolu_1", "name": "run_shell", "input": {}, "raw": '{"command": "rm -rf'}
    with pytest.raises(ProviderError) as caught:
        stream(make_adapter(Server(reply([bad], stop="tool_use"))))
    assert caught.value.network and classify(caught.value).kind == "retryable"


# -- stop reasons ------------------------------------------------------------------------------


def test_refusal_fails_the_turn_with_the_category_and_is_not_retried():
    server = Server(reply([{"type": "text", "text": "I can't."}], stop="refusal",
                          details={"type": "refusal", "category": "cyber", "explanation": "not allowed"}))
    with pytest.raises(ProviderError) as caught:
        stream(make_adapter(server))
    assert "declined" in str(caught.value) and "(cyber)" in str(caught.value)
    assert classify(caught.value).kind == "non_retryable"


def test_max_tokens_is_an_incomplete_response():
    server = Server(reply([{"type": "text", "text": "cut off"}], stop="max_tokens"))
    with pytest.raises(ProviderError, match=r"incomplete \(reason=max_output_tokens\)"):
        stream(make_adapter(server))


def test_pause_turn_is_resumed_and_the_pieces_are_joined():
    server = Server(
        reply([{"type": "text", "text": "part one. "}], stop="pause_turn", usage={"input_tokens": 5, "output_tokens": 3}),
        reply([{"type": "text", "text": "part two."}], usage={"input_tokens": 6, "output_tokens": 4}, message_id="msg_2"),
    )
    turn = stream(make_adapter(server))
    assert turn.text == "part one. \npart two." and turn.response_id == "msg_2"
    assert turn.usage["output_tokens"] == 7 and turn.usage["input_tokens"] == 11
    assert server.bodies[1]["messages"][-1] == {"role": "assistant", "content": [{"type": "text", "text": "part one. "}]}


# -- errors ------------------------------------------------------------------------------------


@pytest.mark.parametrize("status,kind,expected", [
    (429, "rate_limit_error", "rate_limited"),
    (529, "overloaded_error", "retryable"),
    (500, "api_error", "retryable"),
    (401, "authentication_error", "non_retryable"),
    (403, "permission_error", "non_retryable"),
    (404, "not_found_error", "non_retryable"),
    (400, "invalid_request_error", "non_retryable"),
])
def test_http_errors_map_to_provider_errors_the_retry_policy_understands(status, kind, expected):
    server = Server(error(status, kind, "boom", **{"retry-after": "7"}))
    with pytest.raises(ProviderError) as caught:
        stream(make_adapter(server))
    assert caught.value.status_code == status
    assert classify(caught.value).kind == expected
    if status == 429:
        assert caught.value.retry_after_s == 7.0 and "rate limited" in str(caught.value)


def test_a_rejected_key_message_never_contains_the_key():
    server = Server(error(401, "authentication_error", f"invalid x-api-key {KEY}"))
    with pytest.raises(ProviderError) as caught:
        stream(make_adapter(server))
    assert KEY not in str(caught.value) and caught.value.__cause__ is None


def test_connection_failures_are_network_errors_without_the_transport_text():
    server = Server(httpx2.ConnectError(f"cannot connect with {KEY}"))
    with pytest.raises(ProviderError) as caught:
        stream(make_adapter(server))
    assert caught.value.network and KEY not in str(caught.value)
    assert classify(caught.value).kind == "retryable"


def test_an_error_event_in_the_stream_keeps_its_retry_class():
    overloaded = sse(
        {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [],
                                              "model": "x", "usage": {"input_tokens": 1, "output_tokens": 1}}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    with pytest.raises(ProviderError) as caught:
        stream(make_adapter(Server(overloaded)))
    assert caught.value.status_code == 529 and classify(caught.value).kind == "retryable"


def test_missing_key_fails_before_any_request():
    server = Server()
    with pytest.raises(ProviderError) as caught:
        stream(make_adapter(server, api_key="", sdk=server.sdk(key="x")))
    assert caught.value.status_code == 401 and not server.requests


def test_a_rejected_thinking_block_is_retried_once_without_thinking_or_beta_controls():
    stale = error(400, "invalid_request_error",
                  "messages.1.content.0: Invalid `signature` in `thinking` block. The block is bound to a different conversation.")
    server = Server(stale, reply([{"type": "text", "text": "recovered"}]))
    history = [{"role": "user", "content": "go"}, dict(THINKING),
               {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
               {"role": "user", "content": "next"}]
    turn = stream(make_adapter(server), input_items=history)
    assert turn.text == "recovered" and len(server.bodies) == 2
    retry = server.bodies[1]
    assert "anthropic-beta" not in server.requests[1].headers and "fallbacks" not in retry
    assert "block_binding" not in retry["thinking"]
    assert "thinking" not in json.dumps(retry["messages"])


def test_other_bad_requests_are_not_retried():
    server = Server(error(400, "invalid_request_error", "max_tokens: too large"))
    with pytest.raises(ProviderError):
        stream(make_adapter(server))
    assert len(server.bodies) == 1


def test_thinking_blocks_the_api_dropped_are_reported_in_usage():
    ok = reply([{"type": "text", "text": "hi"}])
    # input_transformations arrives on the final message; add it through message_delta-less shortcut.
    server = Server(ok)
    adapter = make_adapter(server)
    original = adapter._stream_message

    async def with_drop(kwargs, on_delta, on_event):
        message, raw = await original(kwargs, on_delta, on_event)
        message["input_transformations"] = [{"type": "thinking_dropped", "path": "messages.1.content.0",
                                             "reason": "prefix_binding_mismatch"}]
        return message, raw

    adapter._stream_message = with_drop  # type: ignore[method-assign]
    assert stream(adapter).usage["thinking_blocks_dropped"] == 1


# -- usage and metrics -------------------------------------------------------------------------


def test_cache_tokens_count_toward_the_prompt_and_are_recorded_in_metrics():
    server = Server(reply([{"type": "text", "text": "ok"}],
                          usage={"input_tokens": 50, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50, "output_tokens": 10}))
    turn = stream(make_adapter(server))
    assert turn.usage["input_tokens"] == 1000
    metrics = TaskMetrics()
    cost = metrics.record_provider(5, turn.usage, (3.0, 15.0))
    snap = metrics.snapshot()
    assert snap["cache_read_tokens"] == 900 and snap["cache_creation_tokens"] == 50
    assert snap["input_tokens"] == 1000 and snap["output_tokens"] == 10
    # 50 fresh + 900 read at 0.1x + 50 written at 1.25x, then 10 output tokens.
    assert cost == pytest.approx((50 + 90 + 62.5) * 3.0 / 1e6 + 10 * 15.0 / 1e6)


def test_metrics_without_cache_fields_price_every_prompt_token_in_full():
    metrics = TaskMetrics()
    cost = metrics.record_provider(5, {"input_tokens": 1000, "output_tokens": 10}, (3.0, 15.0))
    assert cost == pytest.approx((1000 * 3.0 + 10 * 15.0) / 1e6)
    assert metrics.snapshot()["cache_read_tokens"] == 0


# -- planning and the connection test ----------------------------------------------------------


def test_plan_uses_the_same_transport_and_parses_the_json_plan():
    plan_json = json.dumps({"summary": "Do it", "steps": ["a", "b", "c"], "tools": ["shell"], "risks": []})
    server = Server(reply([{"type": "text", "text": plan_json}]))
    plan, response_id = run(make_adapter(server).plan("Do it", "/work", MANIFEST))
    assert plan["steps"] == ["a", "b", "c"] and response_id == "msg_1"
    assert "tools" not in server.bodies[0] and server.bodies[0]["output_config"] == {"effort": "high"}


def models_page(*ids: str) -> httpx2.Response:
    caps = {k: {"supported": True} for k in (
        "batch", "citations", "code_execution", "image_input", "pdf_input", "structured_outputs")}
    caps.update(context_management={"supported": False}, effort={"supported": True},
                server_tools={"supported": False}, thinking={"supported": True, "types": {"adaptive": {"supported": True}}})
    data = [{"id": i, "type": "model", "display_name": i, "created_at": "2026-01-01T00:00:00Z", "lifecycle": "active",
             "max_input_tokens": 1_000_000, "max_tokens": 128_000, "capabilities": caps} for i in ids]
    return httpx2.Response(200, json={"data": data, "has_more": False, "first_id": ids[0], "last_id": ids[-1]})


def test_connection_test_lists_models_for_free_and_checks_the_selected_one():
    server = Server(models_page("claude-opus-5-5", "claude-haiku-5-5"))
    assert run(make_adapter(server).test()) == "Connected. 2 models available."
    assert server.requests[0].url.path == "/v1/models"
    server = Server(models_page("claude-haiku-5-5"))
    with pytest.raises(ProviderError) as caught:
        run(make_adapter(server).test())
    assert caught.value.status_code == 404 and "not offered" in str(caught.value)
    server = Server(error(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(ProviderError) as caught:
        run(make_adapter(server).test())
    assert caught.value.status_code == 401 and len(server.requests) == 1  # a bad key never falls through to a billed call


def test_billed_connection_test_sends_one_small_low_effort_request():
    server = Server(reply([{"type": "text", "text": "OK"}]))
    assert run(make_adapter(server).test(billed=True)) == "OK"
    body = server.bodies[0]
    assert body["output_config"] == {"effort": "low"} and body["max_tokens"] == 1024


# -- manager wiring ----------------------------------------------------------------------------


class ThinkingLoop(FakeAdapter):
    """Reads a file every turn; every assistant turn carries a thinking block. Records what it was sent."""

    def __init__(self, turns: int = 12, pad: int = 3_000) -> None:
        super().__init__()
        self.limit, self.pad = turns, pad
        self.histories: list[list[dict[str, Any]]] = []
        self.manifests: list[Any] = []

    async def turn(self, **kwargs: Any) -> ProviderTurn:
        self.histories.append(list(kwargs.get("input_items") or []))
        self.manifests.append(kwargs.get("manifest"))
        self.turns += 1
        if self.turns > self.limit:
            return ProviderTurn("done", "Finished.", [], {}, [])
        from termx.agent.providers import ProviderCall

        call = ProviderCall(type="function", call_id=f"r{self.turns}", name="read_file", arguments={"path": "a.txt"})
        return ProviderTurn(f"response-{self.turns}", "", [call], {}, [
            {"type": "thinking", "thinking": "", "signature": f"sig-{self.turns}"},
            {"role": "assistant", "content": "p" * self.pad},
            {"type": "function_call", "call_id": call.call_id, "name": "read_file", "arguments": json.dumps(call.arguments)},
        ])


def test_compaction_strips_thinking_from_the_retained_turns_and_the_manifest_stays_frozen(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    adapter = ThinkingLoop()
    manager, store = _routed(tmp_path, adapter)
    manager.save_provider(provider_id="claude", kind="anthropic", name="Claude", base_url="https://api.anthropic.com",
                          model="claude-small", capabilities=["shell", "computer"], api_key="k")
    store.set_provider_catalog("claude", {"claude-small": _info(8000, 1000)}, refreshed_at=1.0)

    async def go() -> None:
        task = await manager.create_task(prompt="read", cwd=str(tmp_path), provider_id="claude", model="claude-small", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        await manager.close()
        store.close()

    run(go())
    assert any(item.get("type") == "thinking" for item in adapter.histories[3])  # kept while it fits
    marked = [h for h in adapter.histories if any("context_checkpoint" in json.dumps(i) for i in h)]
    assert marked, "the small window should have forced a compaction"
    # The first request after a compaction carries only retained turns: their thinking blocks are gone,
    # while the same tail (function calls and text) is still there.
    assert not any(item.get("type") == "thinking" for item in marked[0])
    assert any(item.get("type") == "function_call" for item in marked[0])
    # The first message is rendered from the manifest: one object for the whole task.
    assert len({id(m) for m in adapter.manifests}) == 1


# -- live smoke (opt in) ------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="set ANTHROPIC_API_KEY to run the live smoke test")
def test_live_smoke_second_turn_reads_the_prompt_cache():
    """Two turns on the real API with a prefix long enough to cache; turn 2 must read it back."""
    adapter = AnthropicMessagesAdapter(
        model=os.environ.get("TERMX_LIVE_ANTHROPIC_MODEL", "claude-haiku-5-5"), api_key=os.environ["ANTHROPIC_API_KEY"],
        capabilities=["shell", "functions"], max_output_tokens=2048,
    )

    async def go() -> list[ProviderTurn]:
        turns: list[ProviderTurn] = []
        history = [{"role": "user", "content": "Reply with the single word: ready."}]
        for _ in range(2):
            turn = await adapter.turn(prompt="Smoke test", cwd="/work", manifest=MANIFEST, input_items=history, read_only=True)
            turns.append(turn)
            history = [*history, *turn.output_items, {"role": "user", "content": "Again, one word."}]
        await adapter.sdk.close()
        return turns

    first, second = run(go())
    assert first.text and second.text
    assert second.usage.get("cache_read_input_tokens", 0) > 0, second.usage


def test_seed_models_default_to_opus():
    assert ANTHROPIC_MODELS[0] == "claude-opus-5-5"
    assert set(ANTHROPIC_MODELS) == {"claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
