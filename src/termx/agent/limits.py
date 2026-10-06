"""Validated run budgets shared by host settings and task creation."""

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
