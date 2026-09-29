"""Async sub-agent coordination tools (AG2-012/013).

`spawn_subagent` returns a durable handle immediately; these tools let the
parent fan work out to several children and fan their results back in:
`await_subagents` blocks until children settle (or a timeout), `subagent_status`
queries them without waiting, and `cancel_subagent` stops named children.
"""
from __future__ import annotations

from termx.agent.providers import ProviderCall
from termx.agent.tools.registry import (
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    decide_never,
)

_CHILD_IDS_PARAM = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "One child task id to target.",
        },
        "task_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Child task ids to target. Omit to target every child of this task.",
        },
    },
    "additionalProperties": False,
}


async def _await_subagents(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    result = await ctx.manager._await_subagents(ctx.task_id, call, ctx.cancel)
    return ToolOutcome(result, event={"name": call.name})


async def _subagent_status(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    result = ctx.manager._subagent_statuses(ctx.task_id, call)
    return ToolOutcome(result, event={"name": call.name})


async def _cancel_subagent(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    result = ctx.manager._cancel_subagent(ctx.task_id, call)
    return ToolOutcome(result, event={"name": call.name})


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="await_subagents",
            description=(
                "Wait for spawned sub-agents to finish and collect their results. "
                "Targets the named children, or every child of this task when no "
                "ids are given. Returns each child's final status and result; on "
                "timeout it returns the statuses collected so far with "
                "timed_out=true."
            ),
            parameters={
                "type": "object",
                "properties": {
                    **_CHILD_IDS_PARAM["properties"],
                    "timeout_s": {
                        "type": "integer",
                        "description": "Seconds to wait before returning partial results (default 300, max 600).",
                    },
                },
                "additionalProperties": False,
            },
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_await_subagents,
            decide=decide_never,
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="subagent_status",
            description=(
                "Check spawned sub-agents without waiting: returns each child's "
                "current status plus its result once finished. Targets the named "
                "children, or every child of this task when no ids are given."
            ),
            parameters=_CHILD_IDS_PARAM,
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_subagent_status,
            decide=decide_never,
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="cancel_subagent",
            description=(
                "Cancel running sub-agents of this task. Targets the named "
                "children, or every child of this task when no ids are given. "
                "Children that already finished are reported under "
                "already_finished."
            ),
            parameters=_CHILD_IDS_PARAM,
            mutability="write",
            parallel_safe=False,
            approval="never",
            execute=_cancel_subagent,
            decide=decide_never,
            expose_read_only=False,
        )
    )
