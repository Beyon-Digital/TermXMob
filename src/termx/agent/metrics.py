"""Per-task metrics accumulator, persisted on the task row at terminal states."""
from __future__ import annotations

from typing import Any


class TaskMetrics:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = {
            "provider_calls": 0,
            "provider_ms": 0,
            "provider_first_ms": None,
            "tool_ms": {},
            "tool_calls": {},
            "shell_ms": 0,
            "shell_calls": 0,
            "screenshots": 0,
            "screenshot_bytes": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "subagents": 0,
            "approval_wait_ms": 0,
        }
        if existing:
            for key, value in existing.items():
                if key in {"tool_ms", "tool_calls"} and isinstance(value, dict):
                    self._data[key].update(value)
                elif key in self._data:
                    self._data[key] = value

    def record_provider(self, duration_ms: int, usage: dict[str, Any] | None = None) -> None:
        self._data["provider_calls"] += 1
        self._data["provider_ms"] += duration_ms
        if self._data["provider_first_ms"] is None:
            self._data["provider_first_ms"] = duration_ms
        if usage:
            for key, target in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
                try:
                    self._data[target] += int(usage.get(key) or 0)
                except (TypeError, ValueError):
                    pass

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
