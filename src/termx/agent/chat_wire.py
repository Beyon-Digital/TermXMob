"""Chat Completions wire format for the agent.

The agent keeps its history as Responses-style items (messages, ``function_call``,
``function_call_output``). The chat adapter translates that history into chat
messages for each request and translates the chat reply back into Responses-style
items, so the rest of the runtime never sees the difference. Pure functions: no
network and no dependency on the provider classes.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

NO_RESULT = "No result was recorded for this call."
_INCOMPLETE = {"length": "max_output_tokens", "content_filter": "content_filter"}


# History -> chat messages -----------------------------------------------


def messages_from_items(system: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat messages for a Responses-style transcript.

    Servers require every assistant ``tool_calls`` entry to be answered by a tool
    message before any other role appears. User messages that arrive while calls
    are still unanswered are held back, unanswered calls get a placeholder result,
    and results without a matching call are dropped.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    pending: list[str] = []
    held: list[dict[str, Any]] = []
    assistant: dict[str, Any] | None = None

    def settle() -> None:
        for call_id in pending:
            out.append({"role": "tool", "tool_call_id": call_id, "content": NO_RESULT})
        pending.clear()
        out.extend(held)
        held.clear()

    def answer(call_id: str, content: str) -> None:
        nonlocal assistant
        assistant = None
        if call_id not in pending:
            return
        pending.remove(call_id)
        out.append({"role": "tool", "tool_call_id": call_id, "content": content})
        if not pending:
            out.extend(held)
            held.clear()

    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        role = item.get("role")
        if kind == "function_call" or (kind == "message" and role == "assistant"):
            if assistant is None:
                settle()
                assistant = {"role": "assistant", "content": None}
                out.append(assistant)
            if kind == "message":
                text = _text(item.get("content"))
                if text:
                    assistant["content"] = f"{assistant['content']}\n{text}" if assistant["content"] else text
                continue
            call_id = str(item.get("call_id") or item.get("id") or "")
            assistant.setdefault("tool_calls", []).append({
                "id": call_id,
                "type": "function",
                "function": {"name": str(item.get("name") or ""), "arguments": _arguments_text(item.get("arguments"))},
            })
            pending.append(call_id)
        elif kind == "function_call_output":
            output = item.get("output")
            answer(str(item.get("call_id") or ""), output if isinstance(output, str) else json.dumps(output))
        elif role in {"user", "system", "developer"}:
            assistant = None
            message = {"role": "user" if role == "user" else "system", "content": _user_content(item.get("content"))}
            if pending:
                held.append(message)
            else:
                out.append(message)
        # Reasoning and other provider-specific items (including native computer items,
        # which this adapter never requests) have no chat equivalent.
    settle()
    return out


def chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Responses function schemas wrapped in the chat ``function`` envelope."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
        if tool.get("type") == "function"
    ]


def _arguments_text(value: Any) -> str:
    if isinstance(value, str):
        return value or "{}"
    return json.dumps(value if isinstance(value, dict) else {})


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    parts = [
        part["text"]
        for part in content or []
        if isinstance(part, dict) and part.get("type") in {"output_text", "input_text", "text"}
        and isinstance(part.get("text"), str)
    ]
    return "\n".join(parts).strip()


def _user_content(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "input_image":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            parts.append({"type": "image_url", "image_url": {"url": str(url or "")}})
        elif isinstance(part.get("text"), str):
            parts.append({"type": "text", "text": part["text"]})
    # Plain text stays a string: some local servers reject part arrays.
    if all(part["type"] == "text" for part in parts):
        return "\n".join(part["text"] for part in parts)
    return parts


# Chat reply -> Responses-shaped body ---------------------------------------


def response_from_chat(body: dict[str, Any]) -> dict[str, Any]:
    """A Responses-shaped body (``output`` items, ``usage``, ``status``) from a chat completion."""
    choices = body.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if choice is None:
        raise ValueError("Provider returned no choices")
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    response_id = str(body.get("id") or "")
    output: list[dict[str, Any]] = []
    text = _text(message.get("content"))
    if text:
        output.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") if isinstance(call, dict) and isinstance(call.get("function"), dict) else None
        if function is None:
            continue
        output.append({
            "type": "function_call",
            "call_id": str(call.get("id") or f"call_{response_id or uuid4().hex[:8]}_{index}"),
            "name": str(function.get("name") or ""),
            "arguments": _arguments_text(function.get("arguments")),
        })
    result: dict[str, Any] = {
        "id": response_id,
        "status": "completed",
        "output": output,
        "usage": _usage(body.get("usage")),
    }
    reason = _INCOMPLETE.get(str(choice.get("finish_reason") or ""))
    if reason:
        result["status"] = "incomplete"
        result["incomplete_details"] = {"reason": reason}
    return result


def _usage(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}
    out: dict[str, Any] = {}
    for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"),
                           ("total_tokens", "total_tokens")):
        if usage.get(source) is not None:
            out[target] = usage[source]
    for source, target in (("prompt_tokens_details", "input_tokens_details"),
                           ("completion_tokens_details", "output_tokens_details")):
        if isinstance(usage.get(source), dict):
            out[target] = usage[source]
    return out


# Streaming ------------------------------------------------------------------


class ChatStream:
    """Folds ``chat.completion.chunk`` events into one chat completion body."""

    def __init__(self) -> None:
        self.id = ""
        self.finish_reason: str | None = None
        self.usage: Any = None
        self._text: list[str] = []
        self._calls: dict[int, dict[str, str]] = {}
        self._generated: set[int] = set()  # calls whose id this client made up
        self._alias: dict[int, int] = {}  # server index -> index of its latest call

    @property
    def finished(self) -> bool:
        return self.finish_reason is not None

    def feed(self, event: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
        """Apply one chunk; returns the new text and ``(call_id, arguments_fragment)`` pairs."""
        self.id = str(event.get("id") or self.id)
        if event.get("usage"):
            self.usage = event["usage"]
        text = ""
        fragments: list[tuple[str, str]] = []
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                self.finish_reason = str(choice["finish_reason"])
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if isinstance(delta.get("content"), str) and delta["content"]:
                text += delta["content"]
                self._text.append(delta["content"])
            for call in delta.get("tool_calls") or []:
                if isinstance(call, dict):
                    fragment = self._apply_call(call)
                    if fragment:
                        fragments.append(fragment)
        return text, fragments

    def _apply_call(self, call: dict[str, Any]) -> tuple[str, str] | None:
        raw = call.get("index")
        call_id = str(call.get("id") or "")
        if isinstance(raw, int):
            index = self._alias.get(raw, raw)
        else:
            # Servers that omit the index send each call whole, or continue the last one.
            index = len(self._calls) if call_id or not self._calls else max(self._calls)
        entry = self._calls.get(index)
        if entry is not None and call_id and entry["id"] != call_id:
            if index in self._generated:
                entry["id"] = call_id
                self._generated.discard(index)
            else:
                # Some servers number every parallel call 0 and tell them apart by id.
                index = max(self._calls) + 1
                entry = None
                if isinstance(raw, int):
                    self._alias[raw] = index
        if entry is None:
            entry = self._calls[index] = {"id": call_id or f"call_{uuid4().hex[:8]}", "name": "", "arguments": ""}
            if not call_id:
                self._generated.add(index)
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        if function.get("name") and not entry["name"]:
            entry["name"] = str(function["name"])
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        if isinstance(arguments, str) and arguments:
            entry["arguments"] += arguments
            return entry["id"], arguments
        return None

    def body(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self._text) or None}
        if self._calls:
            message["tool_calls"] = [
                {"id": call["id"], "type": "function",
                 "function": {"name": call["name"], "arguments": call["arguments"]}}
                for _, call in sorted(self._calls.items())
            ]
        return {
            "id": self.id,
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason}],
            "usage": self.usage,
        }
