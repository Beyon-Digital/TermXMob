"""Legacy tool specs delegated to AgentManager internals.

share_file, spawn_subagent, and computer keep their exact existing behavior;
they are expressed as ToolSpecs so the registry, scheduler, and provider
schema generation treat every call uniformly.
"""
from __future__ import annotations

from termx.agent.policy import PolicyDecision
from termx.agent.providers import ProviderCall
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec, make_always


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
    return ctx.manager._decide_computer(call)


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
            decide=make_always(
                "Share a file",
                lambda call: (
                    f"Attaches {str(call.arguments.get('path') or '').strip()} to the chat."
                    if str(call.arguments.get("path") or "").strip()
                    else "Attaches a project file to the chat."
                ),
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="spawn_subagent",
            description=(
                "Delegate one bounded sub-task to a sub-agent running in the same project folder. "
                "Use it to hand off well-scoped work (research a question, write a file, verify a "
                "fix) while you coordinate. The sub-agent runs autonomously to completion and "
                "returns its result; its steps appear nested under this call. Pass instructions "
                "to give it a role or rules to follow."
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


def _decide_spawn(call: ProviderCall, _ctx: ToolContext) -> PolicyDecision:
    agent = str(call.arguments.get("agent") or "sub-agent")
    task_hint = str(call.arguments.get("task") or "").strip()
    detail = f'Hands the task off to the "{agent}" sub-agent.'
    if task_hint:
        detail = f"{detail} Task: {task_hint[:200]}"
    return PolicyDecision(False, True, "Delegate to a sub-agent", detail)
