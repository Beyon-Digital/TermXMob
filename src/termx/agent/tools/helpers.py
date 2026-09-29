"""Shared helpers for structured Agent tools."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from termx.agent.providers import ProviderCall


def call_string(call: ProviderCall, key: str, default: str = "") -> str:
    value = call.arguments.get(key)
    if value is None:
        return default
    return str(value)


def call_int(call: ProviderCall, key: str, default: int) -> int:
    try:
        return int(call.arguments.get(key, default))
    except (TypeError, ValueError):
        return default


def call_bool(call: ProviderCall, key: str, default: bool = False) -> bool:
    value = call.arguments.get(key)
    if value is None:
        return default
    return bool(value)


def denied_result(path: str, reason: str = "Path is denied by agent policy") -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "refused": True, "output": f"Refused: {reason}."}
    if path:
        result["path"] = path
    return result


def error_result(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "output": message, **extra}


def http_error_result(exc: HTTPException, **extra: Any) -> dict[str, Any]:
    detail = str(exc.detail) if exc.detail is not None else str(exc)
    result: dict[str, Any] = {"ok": False, "output": detail}
    if exc.status_code in {401, 403, 404}:
        result["refused"] = True
    if exc.status_code == 409:
        result["conflict"] = True
    result.update(extra)
    return result


def items_json(call: ProviderCall, result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": json.dumps(result, ensure_ascii=False),
        }
    ]
