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
    manager: Any  # AgentManager — legacy executors (share_file/subagent) and desktop control
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
    payload keys (e.g. ``title`` for desktop calls, ``name`` for legacy
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
    mutability: str  # "read" | "write" | "external"
    parallel_safe: bool
    approval: str  # "never" | "policy" | "always"
    execute: ToolExecutor
    decide: ToolDecider
    description_read_only: str = ""
    expose_read_only: bool = True
    plan_only: bool = False  # offered to the model only in plan mode
    available: Callable[[], bool] | None = None  # hidden from the model while this returns False

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


# Preset, ceiling and bot tool lists keep saying "computer"; the registry expands it to the tools behind it.
TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "computer": ("desktop_apps", "desktop_snapshot", "desktop_find", "desktop_get", "desktop_act"),
}
DESKTOP_TOOLS = TOOL_GROUPS["computer"]


def expand_tools(names: Any) -> frozenset[str]:
    out: set[str] = set()
    for name in names or ():
        out.add(name)
        out.update(TOOL_GROUPS.get(name, ()))
    return frozenset(out)


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
        if call.type != "function":
            return None
        return self._specs.get(call.name)

    _SUBAGENT_TOOLS = frozenset(
        {"spawn_subagent", "await_subagents", "subagent_status", "cancel_subagent",
         "managed_session_status", "message_managed_session", "cancel_managed_session",
         "list_agent_projects", "spawn_project_agent","list_agent_engines","list_agent_sessions",
         "review_managed_session","adopt_managed_session","spawn_engine_agent",
         "plan_nodes","start_ready","node_status","retry_node","reassign_node","cancel_node",
         "list_bots","bot_status","pause_bot","create_bot","update_bot","resume_bot","delete_bot","assign_bot_task","schedule_bot"}
    )

    # Offered only to a bot's task or the main agent's, with the recipients that task may message.
    _MESSAGE_TOOLS = frozenset({"send_message", "read_inbox", "mark_message"})

    def provider_tools(
        self,
        *,
        read_only: bool = False,
        allow_subagents: bool = True,
        plan_mode: bool = False,
        message_recipients: list[dict[str, str]] | None = None,
        allowed_tools: list[str] | tuple[str, ...] | frozenset[str] | None = None,
        desktop: bool = True,
    ) -> list[dict[str, Any]]:
        """Function-tool schemas for the provider payload.

        ``desktop=False`` (a provider without function tools) hides the desktop tools. A spec whose ``available`` check
        fails (desktop control not set up on this computer) is not offered either.

        The messaging tools appear only when ``message_recipients`` is a list, and ``send_message`` then names exactly
        those recipients (``{"to": "bot:<id>", "name": ...}``) in its schema.

        ``allowed_tools`` (a bot's or custom agent's preset) hides every other tool, so the model is never told it may
        call something the dispatcher would refuse."""
        allowed = None if allowed_tools is None else expand_tools(allowed_tools)
        tools: list[dict[str, Any]] = []
        for name in sorted(self._specs):
            spec = self._specs[name]
            if allowed is not None and name not in allowed:
                continue
            if name in self._MESSAGE_TOOLS:
                if message_recipients is None or (name == "send_message" and not message_recipients):
                    continue
                if name == "send_message":
                    tools.append(_with_recipients(spec.provider_schema(read_only=read_only), message_recipients))
                    continue
            if spec.available is not None and not spec.available():
                continue
            if not desktop and name in DESKTOP_TOOLS:
                continue
            if spec.plan_only and not plan_mode:
                continue
            if read_only and not spec.expose_read_only:
                continue
            if not allow_subagents and name in self._SUBAGENT_TOOLS:
                continue
            tools.append(spec.provider_schema(read_only=read_only))
        return tools


def _with_recipients(schema: dict[str, Any], recipients: list[dict[str, str]]) -> dict[str, Any]:
    """A copy of the ``send_message`` schema whose ``to`` can only be one of ``recipients``."""
    properties = {**schema["parameters"]["properties"]}
    properties["to"] = {
        "type": "string",
        "enum": [item["to"] for item in recipients],
        "description": "Who to send to: " + ", ".join(f"{item['to']} ({item['name']})" for item in recipients),
    }
    return {**schema, "parameters": {**schema["parameters"], "properties": properties}}

