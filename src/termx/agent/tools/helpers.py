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


def sandbox_profile(ctx: Any) -> str:
    """The execution profile this tool call's spawns must run under.

    Custom agents may pin `sandbox_profile` (host|workspace|agent); the
    default is "agent" — the restricted default. Never silently upgrades an
    agent profile to host.
    """
    agent_id = (getattr(ctx, "task", None) or {}).get("custom_agent_id")
    if agent_id and getattr(ctx, "store", None) is not None:
        custom = ctx.store.task_agent(ctx.task)
        if custom is not None and custom.get("sandbox_profile"):
            return str(custom["sandbox_profile"])
    return "agent"


def sandbox_spawn(ctx: Any, profile: str | None = None) -> Any:
    """Resolve the SandboxRunner for this tool call (host fallback when the
    context carries no runner — tests, Ask-mode read tools)."""
    lookup = getattr(ctx, "sandbox_runner", None)
    if lookup is not None:
        return lookup(profile or "agent")
    from termx.sandbox import runner_for

    return runner_for(profile or "agent")


def sandbox_grants(ctx: Any, profile: str, call_id: str = "") -> frozenset[str]:
    """Capability grants effective for this call's spawn: remembered
    capability rules ∪ one-shot capability approvals, intersected with what
    the backend can grant. Never raises — missing context means no grants."""
    manager = getattr(ctx, "manager", None)
    fn = getattr(manager, "spawn_grants", None)
    if fn is None:
        return frozenset()
    try:
        return frozenset(
            fn(str(getattr(ctx, "task_id", "") or ""), call_id, profile or "agent")
        )
    except Exception:
        return frozenset()


def sandbox_network(grants: frozenset[str] | set[str]) -> str:
    """Spawn network mode implied by an already-resolved grant set.

    Grants must be resolved exactly once per spawn — ``sandbox_grants``
    consumes one-shot capability grants, so deriving network from a second
    lookup would see an empty set and silently re-isolate the network."""
    if any(c.startswith("net.outbound") for c in grants):
        return "outbound"
    return "none"
