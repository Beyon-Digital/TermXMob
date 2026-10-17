"""Bound native display data without replacing the event's routing envelope."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from termx.agent.providers import _redact_value


def _size(value: Any) -> int:
    # The default ASCII JSON encoding includes escaped Unicode and controls.
    return len(json.dumps(value, default=str))


def _prefix_length(text: str, limit: int) -> int:
    low, high = 0, min(len(text), limit)
    while low < high:
        middle = (low + high + 1) // 2
        if _size(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return low


def text_chunks(text: str, limit: int) -> Iterator[str]:
    """Split text at JSON-safe sizes, preserving every character in order."""
    while text:
        length = _prefix_length(text, max(16, limit))
        yield text[:length]
        text = text[length:]


def bounded_fields(payload: dict[str, Any], *, keep: set[str], limit: int) -> dict[str, Any]:
    """Preserve IDs/status and independently redact and bound rich fields.

    Callers pass the small protocol envelope in ``keep``; nested envelopes
    must be bounded before being kept. IDs are not redacted or shortened, as
    that could merge unrelated parallel calls. Display data is sanitized
    before truncation, so a preview cannot expose a partial secret.
    """
    result = {key: value if key in keep else _redact_value(value) for key, value in payload.items()}
    if _size(result) <= limit:
        return result
    rich = [key for key in result if key not in keep]
    shell = {key: value if key in keep else None for key, value in result.items()}
    overhead = _size(shell) - 4 * len(rich)
    budget = max(64, (limit - overhead) // max(1, len(rich)))
    for key in rich:
        value = result[key]
        if _size(value) <= budget:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        preview = {"truncated": True, "preview": ""}
        length = _prefix_length(text, budget - _size(preview) + 2)
        preview["preview"] = text[:length]
        result[key] = preview
    return result
