"""Agent tool registry: typed specs, provider schemas, and dispatch metadata.

Every callable the model can name is described by a ToolSpec carrying its
mutability class, parallel-safety, and approval policy. The registry generates
the provider ``tools[]`` array and the scheduler uses the same metadata to
decide which calls may execute concurrently.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

from termx.agent.policy import PolicyDecision

if TYPE_CHECKING:
    from termx.agent.providers import ProviderCall
    from termx.agent.store import AgentStore


class ToolExecutor(Protocol):
    def __call__(self, call: "ProviderCall", ctx: "ToolContext") -> Awaitable["ToolOutcome"]: ...


class ToolDecider(Protocol):
    def __call__(self, call: "ProviderCall", ctx: "ToolContext") -> PolicyDecision: ...


@dataclass
class ToolContext:
    """Services a tool may touch while executing one provider call."""

    task_id: str
    cwd: str
    task: dict[str, Any]
    read_only: bool
    cancel: asyncio.Event
    emit: Callable[[str, dict[str, Any]], dict[str, Any]]
    store: "AgentStore"
    manager: Any  # AgentManager — legacy executors (share_file/subagent/computer)
    project_files: Any = None  # termx.project_files.ProjectFiles
    project_id: str = ""
    metrics: Any = None  # termx.agent.metrics.TaskMetrics
    policy_engine: Any = None  # termx.agent.policies.engine.PolicyEngine
    sandbox_runner: Any = None  # Callable[[str], SandboxRunner] — profile lookup


@dataclass
class ToolOutcome:
    """One tool call's result.

    ``result`` is the already-redacted payload persisted under
    ``tool.finished.result``; ``items`` are the transcript entries appended to
    the model history in call order; ``event`` carries extra tool.finished
    payload keys (e.g. ``artifact`` for computer calls, ``name`` for legacy
    tools). ``cancelled`` asks the dispatcher to raise CancelledError after the
    finished event is recorded.
    """

    result: dict[str, Any] | None
    items: list[dict[str, Any]] = field(default_factory=list)
    event: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False

    def finished_payload(self, call: "ProviderCall") -> dict[str, Any]:
        payload: dict[str, Any] = {"call_id": call.call_id}
        if self.result is not None:
            payload["result"] = self.result
        payload.update(self.event)
        return payload

    def output_items(self, call: "ProviderCall") -> list[dict[str, Any]]:
        if self.items:
            return self.items
        return [
            {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(self.result or {}, ensure_ascii=False),
            }
        ]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    mutability: str  # "read" | "write" | "external" | "computer"
    parallel_safe: bool
    approval: str  # "never" | "policy" | "always"
    execute: ToolExecutor
    decide: ToolDecider
    description_read_only: str = ""
    expose_read_only: bool = True

    def provider_schema(self, *, read_only: bool = False) -> dict[str, Any]:
        description = self.description_read_only if read_only and self.description_read_only else self.description
        return {
            "type": "function",
            "name": self.name,
            "description": description,
            "parameters": self.parameters,
        }

    def decision(self, call: "ProviderCall", ctx: ToolContext) -> PolicyDecision:
        return self.decide(call, ctx)


def decide_never(_call: "ProviderCall", _ctx: ToolContext) -> PolicyDecision:
    return PolicyDecision(True, False, "Within approved task", "Reads project state")


def make_always(reason: str, detail: Any) -> ToolDecider:
    def decide(call: "ProviderCall", _ctx: ToolContext) -> PolicyDecision:
        consequence = detail(call) if callable(detail) else str(detail)
        return PolicyDecision(False, True, reason, consequence)

    return decide


def unknown_decision() -> PolicyDecision:
    return PolicyDecision(False, True, "Unknown tool", "Runs an unsupported tool request")


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def decision(self, call: "ProviderCall", ctx: ToolContext) -> PolicyDecision:
        spec = self.spec_for(call)
        return spec.decide(call, ctx) if spec is not None else unknown_decision()

    def spec_for(self, call: "ProviderCall") -> ToolSpec | None:
        if call.type == "computer":
            return self._specs.get("computer")
        if call.type != "function":
            return None
        return self._specs.get(call.name)

    _SUBAGENT_TOOLS = frozenset(
        {"spawn_subagent", "await_subagents", "subagent_status", "cancel_subagent"}
    )

    def provider_tools(
        self, *, read_only: bool = False, allow_subagents: bool = True
    ) -> list[dict[str, Any]]:
        """Function-tool schemas for the provider payload (computer excluded —
        the adapter appends its native or function-shaped computer tool)."""
        tools: list[dict[str, Any]] = []
        for name in sorted(self._specs):
            spec = self._specs[name]
            if spec.mutability == "computer":
                continue
            if read_only and not spec.expose_read_only:
                continue
            if not allow_subagents and name in self._SUBAGENT_TOOLS:
                continue
            tools.append(spec.provider_schema(read_only=read_only))
        return tools
