"""Per-task metrics accumulator, persisted on the task row at terminal states."""
from __future__ import annotations

from typing import Any


class TaskMetrics:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = {
            "provider_calls": 0,
            "provider_ms": 0,
            "provider_first_ms": None,
            "provider_first_token_ms": None,
            "provider_stream_ms": 0,
            "provider_output_tokens": 0,
            "provider_retries": 0,
            "provider_rate_limited": 0,
            "tool_ms": {},
            "tool_calls": {},
            "shell_ms": 0,
            "shell_calls": 0,
            "screenshots": 0,
            "screenshot_bytes": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            # USD from per-model prices; None until a priced turn is recorded.
            "cost_usd": None,
            # Tokens from turns whose model has no known price.
            "unpriced_tokens": 0,
            "subagents": 0,
            "approval_wait_ms": 0,
        }
        if existing:
            for key, value in existing.items():
                if key in {"tool_ms", "tool_calls"} and isinstance(value, dict):
                    self._data[key].update(value)
                elif key in self._data:
                    self._data[key] = value

    def record_provider(
        self,
        duration_ms: int,
        usage: dict[str, Any] | None = None,
        prices: tuple[float | None, float | None] | None = None,
    ) -> float | None:
        """Record one provider turn; returns its cost in USD, or None when the price is unknown.

        ``prices`` is (input, output) USD per 1M tokens. An unknown price is
        never treated as zero: the turn's tokens count as unpriced instead.
        """
        self._data["provider_calls"] += 1
        self._data["provider_ms"] += duration_ms
        if self._data["provider_first_ms"] is None:
            self._data["provider_first_ms"] = duration_ms
        cost = None
        if usage:
            turn_in = _count(usage.get("input_tokens"))
            turn_out = _count(usage.get("output_tokens"))
            price_in, price_out = prices or (None, None)
            if price_in is not None and price_out is not None:
                cost = (turn_in * price_in + turn_out * price_out) / 1_000_000
                self._data["cost_usd"] = (self._data["cost_usd"] or 0.0) + cost
            else:
                self._data["unpriced_tokens"] += turn_in + turn_out
            self._data["input_tokens"] += turn_in
            self._data["output_tokens"] += turn_out
            self._data["provider_output_tokens"] += turn_out
        return cost

    def record_provider_stream(self, first_token_ms: int | None, stream_ms: int) -> None:
        if self._data["provider_first_token_ms"] is None and first_token_ms is not None:
            self._data["provider_first_token_ms"] = int(first_token_ms)
        self._data["provider_stream_ms"] += int(stream_ms)

    def record_provider_retry(self, *, rate_limited: bool = False) -> None:
        self._data["provider_retries"] += 1
        if rate_limited:
            self._data["provider_rate_limited"] += 1

    def record_tool(self, name: str, duration_ms: int) -> None:
        tool_ms = self._data["tool_ms"]
        tool_calls = self._data["tool_calls"]
        key = name or "unknown"
        tool_ms[key] = tool_ms.get(key, 0) + duration_ms
        tool_calls[key] = tool_calls.get(key, 0) + 1

    def record_shell(self, duration_ms: int) -> None:
        self._data["shell_ms"] += duration_ms
        self._data["shell_calls"] += 1

    def record_screenshot(self, size_bytes: int) -> None:
        self._data["screenshots"] += 1
        self._data["screenshot_bytes"] += int(size_bytes)

    def record_subagent(self) -> None:
        self._data["subagents"] += 1

    def record_approval_wait(self, duration_ms: int) -> None:
        self._data["approval_wait_ms"] += max(0, int(duration_ms))

    def snapshot(self) -> dict[str, Any]:
        return {
            key: (dict(value) if isinstance(value, dict) else value)
            for key, value in self._data.items()
        }


def usage_tokens(usage: dict[str, Any] | None) -> int:
    """Input plus output tokens of one provider turn, tolerant of missing or malformed values."""
    return _count((usage or {}).get("input_tokens")) + _count((usage or {}).get("output_tokens"))


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
