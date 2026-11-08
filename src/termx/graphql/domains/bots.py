"""Bots domain: background agents that run as their owner under a ceiling.

Authorization follows the bot's custom agent (`_require_custom_agent`), so a bot is as private as the
agent file behind it, and the workspace service re-checks ownership and the owner's live scopes.
"""

from __future__ import annotations

from typing import Callable

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx.graphql import types as T
from termx.graphql.context import TermxContext
from termx.graphql.domains.chat import _require_custom_agent
from termx.graphql.domains.desktop_workspace import principal as session_principal
from termx.graphql.errors import fail, resolver
from termx.workspace.store import Conflict

Ctx = Info[TermxContext, None]


@strawberry.type
class BotTemplate(T.DictBacked):
    """A starting point for a new bot. Every template is read-only and denies network, publishing and computer use."""

    id: str
    name: str
    description: str
    requires_project: bool
    tools: list[str]
    instructions: str


@strawberry.type
class BotRun(T.DictBacked):
    task_id: str
    status: str


@strawberry.type
class Bot(T.DictBacked):
    """`ceiling` is what the bot may ever do. `effective` is what it can do now: the agent's own settings narrowed by the ceiling.
    `violations` lists anything a hand-edited agent file asks for beyond the ceiling; such a bot refuses to run."""

    id: str
    revision: int
    name: str
    state: str
    template: str | None
    description: str
    instructions: str
    project_id: str | None
    conversation_id: str
    custom_agent_id: str
    model: str | None
    provider_id: str | None
    tools: list[str]
    approval_mode: str | None
    sandbox_profile: str | None
    limits: JSON
    ceiling: JSON
    peers: list[str]
    effective: JSON | None
    violations: list[str]
    rules: JSON
    needs_you: int
    active_tasks: int
    last_task: JSON | None
    created_by: str
    config_changed_at: float | None
    created_at: float
    updated_at: float


@strawberry.input
class BotInput:
    """Pick a template or write `instructions`. `tools` defaults to the template's read-only tools. `request_id` makes a retry safe."""

    name: str
    template: str | None = None
    instructions: str | None = None
    description: str | None = None
    project_id: str | None = None
    tools: list[str] | None = None
    model: str | None = None
    provider_id: str | None = None
    peers: list[str] | None = None
    request_id: str | None = None


@strawberry.input
class BotPatchInput:
    """Only the fields you send change. Nothing here can exceed the bot's ceiling."""

    revision: int
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    model: str | None = None
    provider_id: str | None = None
    tools: list[str] | None = None
    approval_mode: str | None = None
    sandbox_profile: str | None = None
    limits: JSON | None = None
    peers: list[str] | None = None


@strawberry.input
class BotCeilingInput:
    tools: list[str]
    approval_mode: str
    sandbox_profile: str
    deny_capabilities: list[str]


def _run(fn: Callable, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError:
        fail(404, "Resource not found")
    except PermissionError as exc:
        fail(403, str(exc) or "Permission denied")
    except Conflict as exc:
        fail(409, str(exc))
    except ValueError as exc:
        fail(400, str(exc))


def _authorize(info: Ctx, bot_id: str, scope: str):
    """The caller's session may use this bot's custom agent at `scope`. Returns the principal and the workspace service."""
    ctx = info.context
    ctx.require(scope)
    actor = session_principal(info)
    service = ctx.state.workspace
    bot = service.store.get("bot", bot_id)
    if bot is None or bot["owner"] != actor.id:
        fail(404, "Resource not found")
    agent = ctx.state.agent_store.get_custom_agent(bot["custom_agent_id"])
    if agent is not None:  # an archived bot's agent file is gone; the owner check above still applies
        _require_custom_agent(ctx, agent, scope)
    return actor, service


@strawberry.type
class BotQueries:
    @strawberry.field
    @resolver
    def bots(self, info: Ctx, include_archived: bool = False) -> list[Bot]:
        ctx = info.context
        ctx.require("agent-view")
        ctx.state.authorization.require_creation(ctx.secret, "agent-view")
        actor = session_principal(info)
        service = ctx.state.workspace
        visible = []
        for view in _run(service.bots.list, actor, include_archived=include_archived):
            agent = ctx.state.agent_store.get_custom_agent(view["custom_agent_id"])
            try:
                if agent is not None:
                    _require_custom_agent(ctx, agent)
            except HTTPException as error:
                if error.status_code == 403:
                    continue
                raise
            visible.append(view)
        return Bot.wrap_all(visible)

    @strawberry.field
    @resolver
    def bot(self, info: Ctx, bot_id: str) -> Bot:
        actor, service = _authorize(info, bot_id, "agent-view")
        return Bot.wrap(_run(service.bots.get, actor, bot_id))

    @strawberry.field
    @resolver
    def bot_templates(self, info: Ctx) -> list[BotTemplate]:
        info.context.require("agent-view")
        return BotTemplate.wrap_all(info.context.state.workspace.bots.templates())


@strawberry.type
class BotMutations:
    @strawberry.mutation
    @resolver
    def create_bot(self, info: Ctx, input: BotInput) -> Bot:
        ctx = info.context
        ctx.require_host("agent-control")
        ctx.require("agent-run")
        actor = session_principal(info)
        view = _run(ctx.state.workspace.bots.create, actor, name=input.name, template=input.template,
                    instructions=input.instructions, description=input.description, project_id=input.project_id,
                    tools=input.tools, model=input.model, provider_id=input.provider_id, peers=input.peers,
                    request_id=input.request_id)
        return Bot.wrap(view)

    @strawberry.mutation
    @resolver
    def patch_bot(self, info: Ctx, bot_id: str, input: BotPatchInput) -> Bot:
        actor, service = _authorize(info, bot_id, "agent-control")
        changes = {key: value for key, value in {
            "name": input.name, "description": input.description, "instructions": input.instructions,
            "model": input.model, "provider_id": input.provider_id, "tools": input.tools,
            "approval_mode": input.approval_mode, "sandbox_profile": input.sandbox_profile,
            "limits": input.limits, "peers": input.peers}.items() if value is not None}
        return Bot.wrap(_run(service.bots.patch, actor, bot_id, revision=input.revision, **changes))

    @strawberry.mutation
    @resolver
    def set_bot_state(self, info: Ctx, bot_id: str, state: str, revision: int | None = None) -> Bot:
        """`active` or `paused`. Pausing stops new runs; a run in progress finishes."""
        actor, service = _authorize(info, bot_id, "agent-control")
        return Bot.wrap(_run(service.bots.set_state, actor, bot_id, state, revision=revision))

    @strawberry.mutation
    @resolver
    def set_bot_ceiling(self, info: Ctx, bot_id: str, revision: int, ceiling: BotCeilingInput) -> Bot:
        """Only a signed-in owner can change a ceiling. Narrowing it also narrows the agent's own settings."""
        info.context.require_host("agent-control")
        actor, service = _authorize(info, bot_id, "agent-control")
        return Bot.wrap(_run(service.bots.set_ceiling, actor, bot_id, revision=revision, ceiling={
            "tools": ceiling.tools, "approval_mode": ceiling.approval_mode,
            "sandbox_profile": ceiling.sandbox_profile, "deny_capabilities": ceiling.deny_capabilities}))

    @strawberry.mutation
    @resolver
    def delete_bot(self, info: Ctx, bot_id: str) -> Bot:
        """Stops the bot, revokes its schedules and permissions and removes its agent file. Its conversation history stays."""
        actor, service = _authorize(info, bot_id, "agent-control")
        return Bot.wrap(_run(service.bots.archive, actor, bot_id))

    @strawberry.mutation
    @resolver
    def revoke_bot_rule(self, info: Ctx, bot_id: str, rule_id: str) -> Bot:
        """Revoke a remembered approval for this bot. Rules that come from the ceiling are changed with `set_bot_ceiling`."""
        actor, service = _authorize(info, bot_id, "agent-control")
        return Bot.wrap(_run(service.bots.revoke_rule, actor, bot_id, rule_id))

    @strawberry.mutation
    @resolver
    async def bot_run_now(self, info: Ctx, bot_id: str, prompt: str | None = None, report_to: str | None = None) -> BotRun:
        """Runs the bot once. `report_to` is where the result goes: `owner` (default), `orchestrator` or `bot:<id>`."""
        actor, service = _authorize(info, bot_id, "agent-run")
        try:
            task = await service.bots.run_now(actor, bot_id, prompt=prompt, report_to=report_to)
        except KeyError:
            fail(404, "Resource not found")
        except PermissionError as exc:
            fail(403, str(exc) or "Permission denied")
        except Conflict as exc:
            fail(409, str(exc))
        except ValueError as exc:
            fail(400, str(exc))
        return BotRun.wrap({"task_id": task["id"], "status": task["status"]})
