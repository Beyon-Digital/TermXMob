"""Validated run budgets shared by host settings and task creation."""

from time import time
from typing import Any

DEFAULT_LIMITS = {
    "max_steps": 256,
    "max_seconds": 14400,
    "shell_timeout_s": 120,
    "max_parallel_subagents": 3,
    "max_subagents_total": 8,
}
LIMIT_RANGES = {
    "max_steps": (1, 100000),
    "max_seconds": (1, 604800),
    "shell_timeout_s": (1, 86400),
    "max_parallel_subagents": (1, 8),
    "max_subagents_total": (1, 32),
}


def resolve_limits(value: dict[str, Any] | None = None,
                   defaults: dict[str, Any] | None = None) -> dict[str, int]:
    if value is not None and not isinstance(value, dict):
        raise ValueError("limits must be an object")
    source = {**DEFAULT_LIMITS, **(defaults or {}), **(value or {})}
    unknown = source.keys() - LIMIT_RANGES.keys()
    if unknown:
        raise ValueError(f"unknown run limits: {', '.join(sorted(unknown))}")
    for key, (low, high) in LIMIT_RANGES.items():
        item = source[key]
        if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    return source


# Bounds on one unattended Auto-mode run. A breach stops and asks again.
# `cost_usd` only counts turns whose model has a known price; an unpriced
# model never trips it (its tokens still count toward `tokens`).
DEFAULT_AUTO_CEILINGS = {
    "tool_calls": 100,
    "steps": 50,
    "seconds": 1800,
    "tokens": 500000,
    "cost_usd": 5.0,
}
AUTO_CEILING_RANGES = {
    "tool_calls": (1, 100000),
    "steps": (1, 100000),
    "seconds": (1, 604800),
    "tokens": (1, 1000000000),
    "cost_usd": (0.01, 100000),
}


def resolve_auto_ceilings(value: dict[str, Any] | None = None,
                          defaults: dict[str, Any] | None = None) -> dict[str, int | float]:
    if value is not None and not isinstance(value, dict):
        raise ValueError("auto ceilings must be an object")
    source = {**DEFAULT_AUTO_CEILINGS, **(defaults or {}), **(value or {})}
    unknown = source.keys() - AUTO_CEILING_RANGES.keys()
    if unknown:
        raise ValueError(f"unknown auto ceilings: {', '.join(sorted(unknown))}")
    for key, (low, high) in AUTO_CEILING_RANGES.items():
        item = source[key]
        kinds = (int, float) if key == "cost_usd" else (int,)
        if isinstance(item, bool) or not isinstance(item, kinds) or not low <= item <= high:
            kind = "a number" if key == "cost_usd" else "an integer"
            raise ValueError(f"{key} must be {kind} between {low} and {high}")
    return source


def auto_usage(auto: dict[str, Any], step: int, now: float | None = None) -> dict[str, int | float]:
    """What the current Auto allowance has used: tool calls, provider turns, seconds, tokens, priced cost."""
    now = time() if now is None else now
    return {
        "tool_calls": max(0, step - int(auto.get("base_step") or 0)),
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
        "ceilings": auto.get("ceilings"),
        "used": auto_usage(auto, int(runtime.get("step") or 0)),
    }
