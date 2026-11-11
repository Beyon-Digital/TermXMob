"""Explicit, optional request controls for one provider account."""
from __future__ import annotations

import math
from typing import Any


DEFAULT_PROVIDER_LIMITS: dict[str, int | float | None] = {
    "max_output_tokens": None,
    "request_timeout_s": None,
}


def clean_provider_limits(raw: dict[str, Any]) -> dict[str, int | float | None]:
    if not isinstance(raw, dict) or raw.keys() - DEFAULT_PROVIDER_LIMITS.keys():
        raise ValueError("provider limits must contain only max_output_tokens and request_timeout_s")
    limits = {**DEFAULT_PROVIDER_LIMITS, **raw}
    output = limits["max_output_tokens"]
    if output is not None and (type(output) is not int or output <= 0):
        raise ValueError("provider max_output_tokens must be a positive integer or null")
    timeout = limits["request_timeout_s"]
    if timeout is not None:
        try:
            valid_timeout = type(timeout) in {int, float} and timeout > 0 and math.isfinite(timeout)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("provider request_timeout_s must be a positive finite number or null")
    return limits
