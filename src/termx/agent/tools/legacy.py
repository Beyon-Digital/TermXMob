"""Legacy tool specs delegated to AgentManager internals.

share_file, spawn_subagent, and computer keep their exact existing behavior;
they are expressed as ToolSpecs so the registry, scheduler, and provider
schema generation treat every call uniformly.
"""
from __future__ import annotations

import sys
from dataclasses import replace

from termx.agent.policies.defaults import tool_capabilities
from termx.agent.policies.fingerprint import computer_fingerprint, computer_label
from termx.agent.policy import PolicyDecision
from termx.agent.providers import ProviderCall
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec, make_always

# Asked every time: a remembered allow for the application never covers these,
# and neither `autonomous` nor Auto may approve them. Input goes to other
# applications, outside any sandbox or worktree.
_ASK_EVERY_TIME = frozenset({"Sensitive text entry", "Provider safety check"})


async def _share_file(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    result = await ctx.manager._share_file(ctx.task_id, ctx.task, call)
    return ToolOutcome(result, event={"name": call.name})


async def _spawn_subagent(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        refusal = {
            "ok": False,
            "refused": True,
            "output": "Refused: Ask mode cannot spawn sub-agents. Switch to Agent mode.",
        }
        return ToolOutcome(refusal, event={"name": call.name})
    result = await ctx.manager._spawn_subagent(ctx.task_id, ctx.task, call, ctx.cancel)
    if ctx.metrics is not None:
        ctx.metrics.record_subagent()
    return ToolOutcome(result, event={"name": call.name})


async def _computer(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    artifact, items = await ctx.manager._execute_computer(ctx.task_id, call, ctx.cancel)
    return ToolOutcome(None, items, event={"artifact": artifact})


def _decide_computer(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    base = ctx.manager._decide_computer(call)
    engine = ctx.policy_engine
    if engine is None or ctx.read_only:
        # Ask and Plan refuse the desktop before this decision is used.
        return base
    unattended = ctx.manager.is_unattended
    if unattended is not None and unattended(ctx.task_id):
        # Nobody is there to watch or stop it, so no rule or approval mode opens the desktop.
        return PolicyDecision(
            False,
            False,
            "Unattended computer control is off",
            "Unattended runs cannot control the desktop. Finish without it, "
            "or tell the owner this task needs them at the computer.",
            auto_resolved="deny",
            required_capabilities=tool_capabilities("computer"),
        )
    app = computer_label(ctx.manager._computer.target_app())
    if not app and sys.platform == "darwin" and not base.approval_required:
        # macOS can name the front application, so a failed lookup is an unknown
        # target, not the whole desktop: app-scoped rules cannot vouch for it.
        base = PolicyDecision(
            True,
            True,
            "Unknown target application",
            "The application that receives this input could not be identified",
        )
    return engine.decide_tool(
        # Native and function-shaped calls are one tool to rules and approvals.
        replace(call, name="computer"),
        ctx,
        fingerprint=computer_fingerprint(app),
        display=f"computer control of {app}" if app else "computer control of the desktop",
        matcher={"app": app} if app else {"scope": "desktop"},
        base=base,
        capabilities=tool_capabilities("computer"),
        risk=None if base.approval_required else "safe",
        # A rule for the whole desktop would cover every application, so it is never offered.
        ask_each_time=not app or base.reason in _ASK_EVERY_TIME,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="share_file",
            description=(
                "Share a file from the project folder with the user as a chat attachment: "
                "screenshots, images, reports, generated media, or any file the user should see. "
                "The file is read and attached to the conversation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path inside the project folder (absolute or relative).",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional one-line caption shown with the attachment.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            mutability="external",
            parallel_safe=False,
            approval="always",
            execute=_share_file,
            decide=_decide_share_file,
        )
    )
    registry.register(
        ToolSpec(
            name="spawn_subagent",
            description=(
                "Delegate one bounded sub-task. Write children use an isolated Git worktree, or a bounded independent snapshot for non-Git projects. Read-only children may inspect the parent folder. "
                "Returns immediately with the child's task_id — the sub-agent runs concurrently "
                "while you keep working, so you can spawn several and coordinate them. Collect "
                "results with await_subagents, inspect with subagent_status, stop with "
                "cancel_subagent; its steps appear nested under this call. Pass instructions "
                "to give it a role or rules to follow, and files and context to brief it. It returns its final "
                "message and the files it changed. When called by the main agent, this also creates "
                "a persistent inspectable child conversation and returns its session_id."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The concrete task to hand off.",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Short label for the sub-agent's role, e.g. 'code reviewer'.",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Optional rules or persona for the sub-agent to follow.",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional project paths the sub-agent should start from.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional results or decisions from earlier work that this task builds on.",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Optional model id for this sub-agent. Defaults to the configured worker "
                            "route, else the provider's default model; it never inherits your model."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["agent", "ask"],
                        "description": (
                            "Autonomy level: 'agent' (default) runs with full tools; "
                            "'ask' runs read-only — it may inspect and report but cannot "
                            "modify files, use the desktop, or spawn further sub-agents. "
                            "Pre-configured delegates advertise their mode; pass it through unchanged."
                        ),
                    },
                },
                "required": ["task"],
                "additionalProperties": False,
            },
            mutability="external",
            parallel_safe=False,
            approval="always",
            execute=_spawn_subagent,
            decide=_decide_spawn,
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="computer",
            description="Control the selected desktop.",
            parameters={"type": "object", "properties": {}},
            mutability="computer",
            parallel_safe=False,
            approval="policy",
            execute=_computer,
            decide=_decide_computer,
            expose_read_only=False,
        )
    )


def _decide_spawn(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    if ctx.task.get("parent_id"):
        # Terminal-parent gate: no approval can lift the depth cap — refuse
        # inline instead of escalating an approval that grants nothing.
        return PolicyDecision(False, False, "Sub-agents cannot spawn sub-agents", "max depth 1")
    agent = str(call.arguments.get("agent") or "sub-agent")
    task_hint = str(call.arguments.get("task") or "").strip()
    detail = f'Hands the task off to the "{agent}" sub-agent.'
    if task_hint:
        detail = f"{detail} Task: {task_hint[:200]}"
    base = PolicyDecision(False, True, "Delegate to a sub-agent", detail)
    engine = getattr(ctx, "policy_engine", None)
    if engine is not None:
        # Delegation remembers on the delegate's label — "always allow the
        # 'publisher' sub-agent in this project", not on free-text task bodies.
        from termx.agent.policies.fingerprint import tool_key

        return engine.decide_tool(
            call,
            ctx,
            fingerprint=tool_key("spawn_subagent", agent),
            display=f"sub-agent {agent}",
            matcher={"agent": agent},
            base=base,
            capabilities=("process.execute", "process.children"),
            # Delegating is itself consequential: autonomous mode never
            # self-approves spawning new sub-agents (a remembered rule still can).
            risk="delegation",
        )
    return base


def _decide_share_file(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    path = str(call.arguments.get("path") or "").strip()
    base = make_always(
        "Share a file",
        f"Attaches {path} to the chat." if path else "Attaches a project file to the chat.",
    )(call, ctx)
    engine = getattr(ctx, "policy_engine", None)
    if engine is not None and path:
        # Remembering binds the exact file path — sharing one file never
        # pre-approves attaching others.
        from termx.agent.policies.fingerprint import tool_key

        return engine.decide_tool(
            call,
            ctx,
            fingerprint=tool_key("share_file", path),
            display=f"share {path}",
            matcher={"path": path},
            base=base,
            capabilities=("fs.workspace.read",),
        )
    return base
