"""Anthropic Messages wire format for the agent.

The agent keeps its history as Responses-style items (messages, ``function_call``,
``function_call_output``). The Anthropic adapter translates that history into
Messages API turns for each request and translates the reply back into
Responses-style items, so the rest of the runtime never sees the difference. Pure
functions: no network and no dependency on the provider classes.

Thinking blocks are opaque. They are stored as ``thinking`` / ``redacted_thinking``
items holding the block exactly as the API returned it, and replayed in place:
the API binds each block to the conversation that produced it. Any other block
type the API returns (for example ``fallback``) is kept in an ``anthropic_block``
item and replayed unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Any

from termx.agent.chat_wire import NO_RESULT

THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})
PASSTHROUGH_TYPE = "anthropic_block"
# History items only this wire produces. Other adapters must not send them.
ITEM_TYPES = THINKING_TYPES | {PASSTHROUGH_TYPE}
CONTINUE_PROMPT = "Continue the approved task."
_THINKING_FIELDS = {"thinking": ("type", "thinking", "signature"), "redacted_thinking": ("type", "data")}
_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_DATA_URL = re.compile(r"^data:([\w.+/-]+);base64,(.+)$", re.DOTALL)
_INCOMPLETE = {"max_tokens": "max_output_tokens", "model_context_window_exceeded": "context_window_exceeded",
               "refusal": "refusal"}


# History -> Messages ------------------------------------------------------


def messages_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages API turns for a Responses-style transcript.

    Every ``tool_use`` must be answered by a ``tool_result`` in the very next user
    message, and the API wants the results of one turn together. Results are merged
    into one user message in call order. User messages that arrive while calls are
    still unanswered follow the results in that same message, unanswered calls get a
    placeholder result, and results without a matching call are dropped. A history
    never ends on an assistant turn: current models reject a prefill.
    """
    out: list[dict[str, Any]] = []
    pending: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    held: list[dict[str, Any]] = []
    assistant: dict[str, Any] | None = None

    def user(blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == "user":
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": "user", "content": list(blocks)})

    def settle() -> None:
        user([results.get(call_id) or _tool_result(call_id, NO_RESULT) for call_id in pending])
        pending.clear()
        results.clear()
        user(held)
        held.clear()

    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        role = item.get("role")
        if kind in THINKING_TYPES or kind == PASSTHROUGH_TYPE or kind == "function_call" or (
            kind == "message" and role == "assistant"
        ):
            block = _assistant_block(item)
            if block is None:
                continue
            if assistant is None:
                settle()
                assistant = {"role": "assistant", "content": []}
                out.append(assistant)
            assistant["content"].append(block)
            if block["type"] == "tool_use":
                pending.append(block["id"])
        elif kind == "function_call_output":
            assistant = None
            call_id = str(item.get("call_id") or "")
            if call_id in pending and call_id not in results:
                results[call_id] = _tool_result(call_id, _result_content(item.get("output")))
                if all(pending_id in results for pending_id in pending):
                    settle()
        elif role in {"user", "system", "developer"}:
            assistant = None
            blocks = _user_blocks(item.get("content"))
            if role != "user":
                blocks = [{"type": "text", "text": f"System instruction:\n{_blocks_text(blocks)}"}] if blocks else []
            if pending:
                held.extend(blocks)
            else:
                user(blocks)
        # Reasoning and other provider-specific items have no Messages equivalent.
    settle()
    if out and out[-1]["role"] == "assistant":
        out.append({"role": "user", "content": [{"type": "text", "text": CONTINUE_PROMPT}]})
    return out


def anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Responses function schemas as Messages tools.

    ``eager_input_streaming`` streams large tool inputs as they are generated. The
    adapter validates the accumulated JSON before any call can run.
    """
    return [
        {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters") or {"type": "object", "properties": {}},
            "eager_input_streaming": True,
        }
        for tool in tools
        if tool.get("type") == "function"
    ]


def assistant_blocks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The assistant content blocks for reply ``items``, for resuming a paused turn."""
    return [block for item in items if (block := _assistant_block(item)) is not None]


def strip_thinking(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``items`` without thinking blocks.

    Thinking blocks are only valid in the conversation prefix that produced them. After a
    compaction the retained turns sit behind a new summary, so their blocks would be rejected.
    """
    return [item for item in items if not (isinstance(item, dict) and item.get("type") in THINKING_TYPES)]


def _assistant_block(item: dict[str, Any]) -> dict[str, Any] | None:
    kind = item.get("type")
    if kind in THINKING_TYPES:
        return {key: item[key] for key in _THINKING_FIELDS[kind] if key in item}
    if kind == PASSTHROUGH_TYPE:
        block = item.get("block")
        return dict(block) if isinstance(block, dict) else None
    if kind == "function_call":
        arguments = item.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        return {"type": "tool_use", "id": str(item.get("call_id") or item.get("id") or ""),
                "name": str(item.get("name") or ""), "input": arguments if isinstance(arguments, dict) else {}}
    text = _plain_text(item.get("content"))
    # Earlier text is part of the prefix later thinking blocks are bound to: never trim it.
    return {"type": "text", "text": text} if text.strip() else None


def _plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part["text"]
        for part in content or []
        if isinstance(part, dict) and part.get("type") in {"output_text", "input_text", "text"}
        and isinstance(part.get("text"), str)
    )


def _tool_result(call_id: str, content: Any) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": call_id, "content": content}


def _result_content(output: Any) -> str | list[dict[str, Any]]:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        blocks = _user_blocks(output)
        if blocks:
            return blocks
    return json.dumps(output, ensure_ascii=False, default=str)


def _user_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks: list[dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "input_image":
            image = part.get("image_url")
            blocks.append(_image_block(str(image.get("url") if isinstance(image, dict) else image or "")))
        elif isinstance(part.get("text"), str) and part["text"].strip():
            blocks.append({"type": "text", "text": part["text"]})
    return blocks


def _image_block(url: str) -> dict[str, Any]:
    match = _DATA_URL.match(url)
    if match and match.group(1).lower() in _IMAGE_TYPES:
        return {"type": "image", "source": {"type": "base64", "media_type": match.group(1).lower(), "data": match.group(2)}}
    if url.startswith(("https://", "http://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "text", "text": "[image omitted: unsupported format]"}


def _blocks_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(block["text"] for block in blocks if block["type"] == "text")


# Messages reply -> Responses-shaped body ---------------------------------


def response_from_message(message: dict[str, Any], raw_inputs: dict[int, str] | None = None) -> dict[str, Any]:
    """A Responses-shaped body (``output`` items, ``usage``, ``status``) from a Messages reply.

    ``raw_inputs`` maps a content-block index to the tool input JSON exactly as streamed. With
    eager input streaming the API does not validate it, so it is parsed here and a malformed or
    non-object input raises ``ValueError`` rather than reaching a tool.
    """
    output: list[dict[str, Any]] = []
    for index, block in enumerate(message.get("content") or []):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            if isinstance(block.get("text"), str) and block["text"].strip():
                output.append({"type": "message", "role": "assistant",
                               "content": [{"type": "output_text", "text": block["text"]}]})
        elif kind == "tool_use":
            output.append({"type": "function_call", "call_id": str(block.get("id") or ""),
                           "name": str(block.get("name") or ""),
                           "arguments": json.dumps(_tool_input(block, (raw_inputs or {}).get(index)), ensure_ascii=False)})
        elif kind in THINKING_TYPES:
            output.append({key: block[key] for key in _THINKING_FIELDS[kind] if key in block})
        elif kind:
            output.append({"type": PASSTHROUGH_TYPE, "block": dict(block)})
    result: dict[str, Any] = {
        "id": str(message.get("id") or ""),
        "status": "completed",
        "output": output,
        "usage": usage_from_message(message.get("usage")),
    }
    reason = _INCOMPLETE.get(str(message.get("stop_reason") or ""))
    if reason:
        result["status"] = "incomplete"
        result["incomplete_details"] = {"reason": reason}
    return result


def usage_from_message(usage: Any) -> dict[str, Any]:
    """Responses-style usage. Messages ``input_tokens`` excludes cached tokens; here it is the whole prompt."""
    if not isinstance(usage, dict):
        return {}
    fresh, read, written = (_count(usage.get(key)) for key in
                            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    output = _count(usage.get("output_tokens"))
    out: dict[str, Any] = {"input_tokens": fresh + read + written, "output_tokens": output,
                           "total_tokens": fresh + read + written + output}
    if "cache_read_input_tokens" in usage:
        out["cache_read_input_tokens"] = read
        out["input_tokens_details"] = {"cached_tokens": read}
    if "cache_creation_input_tokens" in usage:
        out["cache_creation_input_tokens"] = written
    return out


def _tool_input(block: dict[str, Any], raw: str | None) -> dict[str, Any]:
    if raw is not None and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("The model returned malformed tool input") from exc
        if not isinstance(parsed, dict):
            raise ValueError("The model returned malformed tool input")
        return parsed
    value = block.get("input")
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
