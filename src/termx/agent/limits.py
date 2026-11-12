"""Compatibility settings and usage reporting for unlimited task execution."""

from time import time
from typing import Any

# Stable compatibility keys for task rows and older clients. Task execution
# does not enforce usage quotas; workflows and providers validate their own.
DEFAULT_LIMITS = {
    "max_steps": None,
    "max_seconds": None,
    "shell_timeout_s": None,
    "max_parallel_subagents": None,
    "max_subagents_total": None,
}
LIMIT_RANGES = {key: (1, None) for key in DEFAULT_LIMITS}


def resolve_limits(value: dict[str, Any] | None = None,
                   defaults: dict[str, Any] | None = None) -> dict[str, None]:
    """Normalize legacy task usage settings without carrying their quotas forward."""
    if value is not None and not isinstance(value, dict):
        raise ValueError("limits must be an object")
    source = {**(defaults or {}), **(value or {})}
    unknown = source.keys() - DEFAULT_LIMITS.keys()
    if unknown:
        raise ValueError(f"unknown run limits: {', '.join(sorted(unknown))}")
    for key, item in source.items():
        if item is not None and (isinstance(item, bool) or not isinstance(item, int) or item < 1):
            raise ValueError(f"{key} must be a positive integer or null")
    return dict(DEFAULT_LIMITS)


# Auto still controls approval policy. Its usage counters are informational.
DEFAULT_AUTO_CEILINGS = {
    "tool_calls": None,
    "steps": None,
    "seconds": None,
    "tokens": None,
    "cost_usd": None,
}
AUTO_CEILING_RANGES = {key: (0.01 if key == "cost_usd" else 1, None)
                       for key in DEFAULT_AUTO_CEILINGS}


def resolve_auto_ceilings(value: dict[str, Any] | None = None,
                          defaults: dict[str, Any] | None = None) -> dict[str, None]:
    """Accept old Auto usage settings without changing approval permissions."""
    import math

    if value is not None and not isinstance(value, dict):
        raise ValueError("auto ceilings must be an object")
    source = {**(defaults or {}), **(value or {})}
    unknown = source.keys() - DEFAULT_AUTO_CEILINGS.keys()
    if unknown:
        raise ValueError(f"unknown auto ceilings: {', '.join(sorted(unknown))}")
    for key, item in source.items():
        kinds = (int, float) if key == "cost_usd" else (int,)
        if item is not None and (isinstance(item, bool) or not isinstance(item, kinds)
                                 or (isinstance(item, float) and not math.isfinite(item)) or item <= 0):
            kind = "a positive number" if key == "cost_usd" else "a positive integer"
            raise ValueError(f"{key} must be {kind} or null")
    return dict(DEFAULT_AUTO_CEILINGS)


def auto_usage(auto: dict[str, Any], step: int, now: float | None = None) -> dict[str, int | float]:
    """What the current Auto allowance has used: tool calls, provider turns, seconds, tokens, priced cost."""
    now = time() if now is None else now
    return {
        # The runtime step only advances between provider turns, so the auto-approved count keeps the total current.
        "tool_calls": max(0, step - int(auto.get("base_step") or 0), int(auto.get("calls") or 0)),
        "steps": int(auto.get("turns") or 0),
        "seconds": int(now - float(auto.get("since") or now)),
        "tokens": int(auto.get("tokens") or 0),
        "cost_usd": round(float(auto.get("cost_usd") or 0), 6),
    }


def auto_summary(runtime: dict[str, Any] | None) -> dict[str, Any] | None:
    """Public Auto-mode state: switch, ceilings and what the current allowance has used."""
    runtime = runtime or {}
    auto = runtime.get("auto")
    if not auto:
        return None
    return {
        "enabled": bool(auto.get("enabled")),
        "ceilings": dict(DEFAULT_AUTO_CEILINGS),
        "used": auto_usage(auto, int(runtime.get("step") or 0)),
    }
