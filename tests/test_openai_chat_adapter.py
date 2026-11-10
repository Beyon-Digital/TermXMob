"""OpenAI Chat Completions adapter against a recorded-response fake server (no network)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from test_agent import FakeAdapter, build_manager, wait_for_pending_approval, wait_for_status
from termx.agent.providers import OpenAIChatAdapter, OpenAIResponsesAdapter, ProviderError

TOOL_REPLY = {
    "id": "chatcmpl-1",
    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "run_shell", "arguments": '{"command": "printf hi", "purpose": "say hi"}'}}]}}],
    "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
}
TEXT_REPLY = {
    "id": "chatcmpl-2",
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "All done."}}],
    "usage": {"prompt_tokens": 80, "completion_tokens": 4, "total_tokens": 84},
}


class FakeServer:
    """Replays recorded replies in order and keeps every request it saw."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content or b"{}"))
        reply = self.replies.pop(0)
        if isinstance(reply, httpx.Response):
            return reply
        if self.bodies[-1].get("stream"):
            return as_stream(reply)
        return httpx.Response(200, json=reply)

    def client(self, adapter: OpenAIChatAdapter) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), headers=adapter.request_headers())


def make_adapter(server: FakeServer, **kwargs: Any) -> OpenAIChatAdapter:
    options: dict[str, Any] = {
        "base_url": "http://localhost:11434/v1", "model": "llama3", "api_key": "k",
        "capabilities": ["shell", "functions"],
    }
    options.update(kwargs)
    adapter = OpenAIChatAdapter(**options)
    adapter.client = server.client(adapter)
    return adapter


MANIFEST = {"files": ["a.py"], "omitted": 0}


def as_stream(reply: dict[str, Any]) -> httpx.Response:
    """The same recorded completion, delivered the way a server streams it."""
    choice = reply["choices"][0]
    message = choice["message"]
    chunk = lambda delta, finish=None: {"id": reply["id"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    events: list[Any] = []
    if message.get("content"):
        events.append(chunk({"role": "assistant", "content": message["content"]}))
    for index, call in enumerate(message.get("tool_calls") or []):
        events.append(chunk({"tool_calls": [{"index": index, **call}]}))
    events.append(chunk({}, choice["finish_reason"]))
    if reply.get("usage"):
        events.append({"id": reply["id"], "choices": [], "usage": reply["usage"]})
    return sse(*events, "[DONE]")


def sse(*events: Any) -> httpx.Response:
    body = "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def test_turn_posts_chat_completions_and_returns_calls_and_usage() -> None:
    async def run() -> None:
        server = FakeServer([TOOL_REPLY])
        adapter = make_adapter(server)
        turn = await adapter.turn(prompt="Say hi", cwd="/work", manifest=MANIFEST)
        request, body = server.requests[0], server.bodies[0]
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer k"
        assert body["model"] == "llama3"
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user" and "Say hi" in body["messages"][1]["content"]
        assert "input" not in body and "instructions" not in body and "store" not in body
        names = {tool["function"]["name"] for tool in body["tools"]}
        assert "run_shell" in names and "exit_plan" not in names
        assert body["max_tokens"] == 2200
        assert turn.response_id == "chatcmpl-1"
        assert [(c.name, c.call_id, c.arguments["command"]) for c in turn.calls] == [("run_shell", "call_1", "printf hi")]
        assert turn.usage["input_tokens"] == 50 and turn.usage["output_tokens"] == 10
        assert turn.output_items[0]["type"] == "function_call"
    asyncio.run(run())


def test_history_is_translated_into_messages_on_the_next_request() -> None:
    async def run() -> None:
        server = FakeServer([TOOL_REPLY, TEXT_REPLY])
        adapter = make_adapter(server)
        first = await adapter.turn(prompt="Say hi", cwd="/work", manifest=MANIFEST)
        history = [*first.output_items, {"type": "function_call_output", "call_id": "call_1", "output": "hi"}]
        second = await adapter.turn(prompt="Say hi", cwd="/work", manifest=MANIFEST, input_items=history)
        roles = [m["role"] for m in server.bodies[1]["messages"]]
        assert roles == ["system", "user", "assistant", "tool"]
        assert server.bodies[1]["messages"][2]["tool_calls"][0]["id"] == "call_1"
        assert server.bodies[1]["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "hi"}
        assert second.text == "All done." and second.calls == []
    asyncio.run(run())


def test_plan_mode_sends_exit_plan_and_plan_instructions() -> None:
    async def run() -> None:
        server = FakeServer([TEXT_REPLY])
        adapter = make_adapter(server)
        await adapter.turn(prompt="Plan it", cwd="/work", manifest=MANIFEST, read_only=True, plan_mode=True)
        body = server.bodies[0]
        assert "Plan mode" in body["messages"][0]["content"]
        assert "exit_plan" in {tool["function"]["name"] for tool in body["tools"]}
    asyncio.run(run())


def test_provider_without_tool_capabilities_sends_no_tools() -> None:
    async def run() -> None:
        server = FakeServer([TEXT_REPLY])
        adapter = make_adapter(server, capabilities=["image"])
        await adapter.turn(prompt="Chat", cwd="/work", manifest=MANIFEST, read_only=True)
        assert "tools" not in server.bodies[0]
    asyncio.run(run())


def test_max_output_tokens_uses_the_models_limit_and_openai_key_name() -> None:
    async def run() -> None:
        server = FakeServer([TEXT_REPLY, TEXT_REPLY])
        local = make_adapter(server, max_output_tokens=4096)
        await local.turn(prompt="x", cwd="/w", manifest=MANIFEST)
        hosted = make_adapter(server, base_url="https://api.openai.com/v1/chat/completions", max_output_tokens=4096)
        await hosted.turn(prompt="x", cwd="/w", manifest=MANIFEST)
        assert server.bodies[0]["max_tokens"] == 4096
        assert "max_tokens" not in server.bodies[1] and server.bodies[1]["max_completion_tokens"] == 4096
        assert str(server.requests[1].url) == "https://api.openai.com/v1/chat/completions"
    asyncio.run(run())


def test_desktop_tools_are_ordinary_function_tools_for_a_chat_model(ready_desktop) -> None:
    async def run() -> None:
        server = FakeServer([TEXT_REPLY])
        adapter = make_adapter(server, capabilities=["shell", "functions"])
        await adapter.turn(prompt="x", cwd="/w", manifest=MANIFEST)
        names = {tool["function"]["name"] for tool in server.bodies[0]["tools"]}
        assert {"desktop_apps", "desktop_snapshot", "desktop_find", "desktop_get", "desktop_act"} <= names
        assert "use_computer" not in names and not any(tool.get("type") == "computer" for tool in server.bodies[0]["tools"])
    asyncio.run(run())


def test_truncated_reply_is_a_provider_error() -> None:
    async def run() -> None:
        reply = {"id": "x", "choices": [{"finish_reason": "length", "message": {"content": "par"}}]}
        adapter = make_adapter(FakeServer([reply]))
        with pytest.raises(ProviderError, match="incomplete"):
            await adapter.turn(prompt="x", cwd="/w", manifest=MANIFEST)
    asyncio.run(run())


def test_http_error_keeps_status_and_retry_after() -> None:
    async def run() -> None:
        limited = httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "3"})
        adapter = make_adapter(FakeServer([limited]))
        with pytest.raises(ProviderError) as caught:
            await adapter.turn(prompt="x", cwd="/w", manifest=MANIFEST)
        assert caught.value.status_code == 429 and caught.value.retry_after_s == 3.0
    asyncio.run(run())


def test_error_object_in_a_200_body_is_a_provider_error() -> None:
    async def run() -> None:
        adapter = make_adapter(FakeServer([{"error": {"message": "model not loaded", "code": "no_model"}}]))
        with pytest.raises(ProviderError, match="model not loaded"):
            await adapter.turn(prompt="x", cwd="/w", manifest=MANIFEST)
    asyncio.run(run())


def test_plan_returns_a_structured_plan() -> None:
    async def run() -> None:
        content = json.dumps({"summary": "Do it", "steps": ["a", "b"], "tools": ["shell"], "risks": []})
        reply = {"id": "p1", "choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        server = FakeServer([reply])
        plan, response_id = await make_adapter(server).plan("Do it", "/w", MANIFEST)
        assert plan["steps"] == ["a", "b"] and response_id == "p1"
        assert server.bodies[0]["messages"][0]["role"] == "system" and "tools" not in server.bodies[0]
    asyncio.run(run())


def test_stream_turn_emits_deltas_and_assembles_tool_calls() -> None:
    async def run() -> None:
        chunk = lambda delta, finish=None: {"id": "s1", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        server = FakeServer([sse(
            chunk({"role": "assistant", "content": "Run"}),
            chunk({"content": "ning"}),
            chunk({"tool_calls": [{"index": 0, "id": "call_s", "type": "function",
                                    "function": {"name": "run_shell", "arguments": '{"command":'}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "ls", "purpose": "list"}'}}]}),
            chunk({}, "tool_calls"),
            {"id": "s1", "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
            "[DONE]",
        )])
        adapter = make_adapter(server)
        deltas: list[str] = []
        events: list[tuple[str, dict[str, Any]]] = []
        turn = await adapter.stream_turn(
            prompt="x", cwd="/w", manifest=MANIFEST,
            on_delta=deltas.append, on_event=lambda kind, data: events.append((kind, data)),
        )
        assert server.bodies[0]["stream"] is True
        assert server.bodies[0]["stream_options"] == {"include_usage": True}
        assert deltas == ["Run", "ning"]
        assert [e[1]["call_id"] for e in events] == ["call_s", "call_s"]
        assert turn.text == "Running"
        assert turn.calls[0].call_id == "call_s" and turn.calls[0].arguments == {"command": "ls", "purpose": "list"}
        assert turn.usage["input_tokens"] == 9
    asyncio.run(run())


def test_stream_without_completion_is_retryable_network_error() -> None:
    async def run() -> None:
        partial = sse({"id": "s", "choices": [{"index": 0, "delta": {"content": "x"}}]}, "[DONE]")
        adapter = make_adapter(FakeServer([partial]))
        with pytest.raises(ProviderError) as caught:
            await adapter.stream_turn(prompt="x", cwd="/w", manifest=MANIFEST)
        assert caught.value.network is True
    asyncio.run(run())


def test_stream_error_event_is_a_provider_error() -> None:
    async def run() -> None:
        adapter = make_adapter(FakeServer([sse({"error": {"message": "overloaded", "code": "busy"}})]))
        with pytest.raises(ProviderError, match="overloaded"):
            await adapter.stream_turn(prompt="x", cwd="/w", manifest=MANIFEST)
    asyncio.run(run())


def test_stream_http_error_is_reported() -> None:
    async def run() -> None:
        adapter = make_adapter(FakeServer([httpx.Response(503, json={"error": {"message": "down"}})]))
        with pytest.raises(ProviderError) as caught:
            await adapter.stream_turn(prompt="x", cwd="/w", manifest=MANIFEST)
        assert caught.value.status_code == 503
    asyncio.run(run())


def test_connection_test_uses_the_free_model_list_first() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "llama3"}]})
            return httpx.Response(404)
        adapter = OpenAIChatAdapter(base_url="http://localhost:1234/v1", model="llama3", api_key="",
                                    capabilities=["shell"])
        adapter.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert "1 model" in await adapter.test()
    asyncio.run(run())


def test_billed_test_posts_a_chat_completion() -> None:
    async def run() -> None:
        server = FakeServer([{"id": "t", "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}]}])
        assert await make_adapter(server)._billed_test() == "OK"
        assert server.bodies[0]["messages"][-1]["role"] == "user"
    asyncio.run(run())


# Manager integration ---------------------------------------------------------


def chat_manager(tmp_path: Path, server: FakeServer, *, wire: str | None = "chat", capabilities=None):
    manager, store = build_manager(tmp_path, FakeAdapter())

    def factory(provider: dict[str, Any], key: str):
        adapter = manager._default_adapter(provider, key)
        adapter.client = server.client(adapter)
        return adapter

    manager._adapter_factory = factory
    manager.save_provider(
        provider_id="local", kind="openai-compatible", name="Local", base_url="http://localhost:11434/v1",
        model="llama3", capabilities=capabilities or ["shell", "functions"], wire=wire,
    )
    return manager, store


def test_wire_defaults_to_responses_and_chat_selects_the_chat_adapter(tmp_path: Path) -> None:
    manager, store = build_manager(tmp_path, FakeAdapter())
    assert manager._provider("fake")["wire"] == "responses"
    provider = manager._provider("fake")
    assert type(manager._default_adapter({**provider, "id": "fake"}, "k")) is OpenAIResponsesAdapter
    manager.save_provider(
        provider_id="fake", kind="openai-compatible", name="Fake", base_url="http://127.0.0.1:9999/v1",
        model="m", capabilities=None, wire="chat",
    )
    assert manager._provider("fake")["wire"] == "chat"
    assert type(manager._default_adapter(manager._provider("fake"), "k")) is OpenAIChatAdapter
    # Saving again without a wire keeps the stored one.
    manager.save_provider(
        provider_id="fake", kind="openai-compatible", name="Fake", base_url="http://127.0.0.1:9999/v1",
        model="m", capabilities=None,
    )
    assert manager._provider("fake")["wire"] == "chat"
    store.close()


def test_unknown_wire_is_rejected(tmp_path: Path) -> None:
    manager, store = build_manager(tmp_path, FakeAdapter())
    with pytest.raises(ValueError, match="wire"):
        manager.save_provider(
            provider_id="fake", kind="openai-compatible", name="Fake", base_url="http://127.0.0.1:9999/v1",
            model="m", capabilities=None, wire="soap",
        )
    store.close()


def test_agent_task_runs_a_tool_loop_over_chat_completions(tmp_path: Path) -> None:
    async def run() -> None:
        plan = json.dumps({"summary": "Say hi", "steps": ["Run printf"], "tools": ["shell"], "risks": []})
        plan_reply = {"id": "plan", "choices": [{"finish_reason": "stop", "message": {"content": plan}}]}
        server = FakeServer([plan_reply, TOOL_REPLY, TEXT_REPLY])
        manager, store = chat_manager(tmp_path, server)
        task = await manager.create_task(prompt="Say hi", cwd=str(tmp_path), provider_id="local")
        _task, approval = await wait_for_pending_approval(store, task["id"], "plan")
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        done = await wait_for_status(store, task["id"], "completed")
        assert done["result"] == "All done."
        assert server.bodies[0].get("stream") is None  # the one-shot plan is not streamed
        assert [m["role"] for m in server.bodies[2]["messages"]] == ["system", "user", "assistant", "tool"]
        assert "hi" in server.bodies[2]["messages"][3]["content"]
        assert any(e["type"] == "provider.usage" for e in store.events(task["id"]))
        await manager.close()
        store.close()
    asyncio.run(run())


def test_plan_mode_task_works_over_chat_completions(tmp_path: Path) -> None:
    async def run() -> None:
        plan_call = {"id": "p", "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "exit_plan", "arguments": json.dumps({"plan_markdown": "1. edit"})}}]}}]}
        server = FakeServer([plan_call])
        manager, store = chat_manager(tmp_path, server)
        task = await manager.create_task(prompt="Plan", cwd=str(tmp_path), provider_id="local", mode="plan")
        await wait_for_status(store, task["id"], "awaiting_approval")
        assert "exit_plan" in {t["function"]["name"] for t in server.bodies[0]["tools"]}
        await manager.close()
        store.close()
    asyncio.run(run())


def test_model_override_without_tools_sends_none_and_blocks_agent_mode(tmp_path: Path) -> None:
    async def run() -> None:
        server = FakeServer([TEXT_REPLY])
        manager, store = chat_manager(tmp_path, server)
        manager.save_provider(
            provider_id="local", kind="openai-compatible", name="Local", base_url="http://localhost:11434/v1",
            model="llama3", capabilities=None, model_meta={"llama3": {"capabilities": ["vision"]}},
        )
        with pytest.raises(ValueError, match="cannot call tools"):
            await manager.create_task(prompt="Edit", cwd=str(tmp_path), provider_id="local", mode="agent")
        task = await manager.create_task(prompt="What is this?", cwd=str(tmp_path), provider_id="local", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        assert "tools" not in server.bodies[0]
        assert manager.tools_supported(manager._provider("local"), "llama3") is False
        await manager.close()
        store.close()
    asyncio.run(run())


def test_tools_stay_available_when_capabilities_are_unknown_or_listed(tmp_path: Path) -> None:
    manager, store = chat_manager(tmp_path, FakeServer([]))
    provider = manager._provider("local")
    assert manager.tools_supported(provider, "llama3") is True
    manager.save_provider(
        provider_id="local", kind="openai-compatible", name="Local", base_url="http://localhost:11434/v1",
        model="llama3", capabilities=None, model_meta={"llama3": {"capabilities": ["tools", "vision"]}},
    )
    assert manager.tools_supported(manager._provider("local"), "llama3") is True
    manager.save_provider(
        provider_id="local", kind="openai-compatible", name="Local", base_url="http://localhost:11434/v1",
        model="llama3", capabilities=["image"], model_meta={},
    )
    assert manager.tools_supported(manager._provider("local"), "llama3") is False
    store.close()


def test_provider_with_only_computer_still_supports_agent_mode(tmp_path: Path) -> None:
    manager, store = chat_manager(tmp_path, FakeServer([]))
    manager.save_provider(
        provider_id="local", kind="openai-compatible", name="Local", base_url="http://localhost:11434/v1",
        model="llama3", capabilities=["computer"], model_meta={},
    )
    assert manager.tools_supported(manager._provider("local"), "llama3") is True
    store.close()
