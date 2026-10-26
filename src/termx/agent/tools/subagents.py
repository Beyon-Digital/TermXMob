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
    make_always,
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


async def _managed_session(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    service = getattr(ctx.manager, 'orchestrator', None)
    if service is None:
        return ToolOutcome({'ok':False,'error':'Managed conversation coordination is unavailable'})
    try:
        result = await service.command(ctx.task_id, call)
    except (KeyError, ValueError, PermissionError) as exc:
        result = {'ok':False,'error':str(exc)}
    return ToolOutcome(result, event={'name':call.name})


def register(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(name='list_agent_projects',
        description='Main agent only: list enrolled project IDs and names currently authorized for this user. Returns no file contents, credentials or model configuration.',
        parameters={'type':'object','properties':{},'additionalProperties':False},
        mutability='read',parallel_safe=True,approval='never',execute=_managed_session,decide=decide_never,expose_read_only=False))
    registry.register(ToolSpec(name='spawn_project_agent',
        description='Main agent only: delegate one bounded task in an explicitly selected authorized project. The Internal Agent uses an isolated worktree or independent snapshot and the current main provider/model, shared budgets and normal permission gates. This does not copy context or credentials from other projects and cannot select native or ACP engines.',
        parameters={'type':'object','properties':{'project_id':{'type':'string','description':'An enrolled ID returned by list_agent_projects.'},
            'task':{'type':'string'},'agent':{'type':'string'},'instructions':{'type':'string'},
            'mode':{'type':'string','enum':['agent','ask']}},'required':['project_id','task'],'additionalProperties':False},
        mutability='external',parallel_safe=False,approval='always',execute=_managed_session,
        decide=make_always('Delegate work in a selected project',lambda call:
            'Start a bounded agent in project '+str(call.arguments.get('project_id') or '')+'. Task: '+str(call.arguments.get('task') or '')[:200]),
        expose_read_only=False))
    session_parameters = {'type':'object','properties':{'session_id':{'type':'string',
        'description':'The inspectable child conversation ID returned by spawn_subagent or managed_session_status.'}},
        'additionalProperties':False}
    registry.register(ToolSpec(name='managed_session_status',
        description='Main agent only: inspect the current status, direct-user ownership, result and error of persistent delegated child conversations, including work from prior turns. Omit session_id to list them. No worker is started.',
        parameters=session_parameters,mutability='read',parallel_safe=True,approval='never',
        execute=_managed_session,decide=decide_never,expose_read_only=False))
    registry.register(ToolSpec(name='message_managed_session',
        description='Main agent only: steer a running delegated child, or send one bounded follow-up turn in its existing isolated conversation. Requires explicit action approval. Refuses while the user directly owns the child; only the user can return control. Never repeats a lost dispatch.',
        parameters={**session_parameters,'properties':{**session_parameters['properties'],
            'message':{'type':'string','description':'A concrete bounded instruction for the delegated child.'}},
            'required':['session_id','message']},mutability='external',parallel_safe=False,approval='always',
        execute=_managed_session,decide=make_always('Send a delegated agent instruction',
            'Steers an existing worker or starts one bounded follow-up turn using its current permissions and budget.'),expose_read_only=False))
    registry.register(ToolSpec(name='cancel_managed_session',
        description='Main agent only: stop active work in an owned delegated child conversation. Refuses direct-user-owned children. Does not delete its conversation or result.',
        parameters={**session_parameters,'required':['session_id']},mutability='write',parallel_safe=False,
        approval='never',execute=_managed_session,decide=decide_never,expose_read_only=False))
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
