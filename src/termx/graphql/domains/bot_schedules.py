"""Bot schedules, re-authorization and the needs-you inbox over GraphQL.

Everything is addressed by the bot's `conversation_id` (that is what tasks, schedules and runs join on) and
authorized like the bot itself. Creating a schedule or re-authorizing issues unattended authority, so those two
need the signed-in owner (`require_host`), as creating a bot does.
"""

from __future__ import annotations

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON

from termx.graphql import types as T
from termx.graphql.domains.bots import Ctx, _authorize, _run
from termx.graphql.domains.chat import _require_custom_agent
from termx.graphql.domains.desktop_workspace import principal as session_principal
from termx.graphql.errors import fail, resolver
from termx.workspace.store import Conflict


@strawberry.type
class BotSchedule(T.DictBacked):
    """One delegated schedule of a bot. `needs_reauthorization` is true while the bot's prompt, permissions or the
    authorization's lifetime no longer match what the owner approved; `reason` says which. `paused_by_bot` is true
    while the schedule is off only because its bot is paused. `trigger` says what starts a run: `{kind: "time"}` (the default, see
    `spec`), `message`, `bot_finished`, `file_change` or `webhook`. `webhook_path` is the endpoint on this host; `webhook_secret` is
    set only in the response that created the webhook or rotated its secret, and never again. `report_to` is where each run's result goes."""

    id: str
    revision: int
    bot_id: str
    conversation_id: str
    goal_id: str
    grant_id: str
    prompt: str
    spec: JSON
    missed: str
    overlap: str
    enabled: bool
    state: str
    reason: str | None
    next_run: float | None
    last_run: str | None
    queued: bool
    success_criteria: str
    max_runs: int
    runs_started: int
    limits: JSON
    grant_expires_at: float | None
    grant_used_runs: int
    needs_reauthorization: bool
    paused_by_bot: bool
    trigger: JSON
    report_to: JSON
    webhook_path: str | None
    webhook_secret: str | None
    created_at: float
    updated_at: float


@strawberry.type
class BotScheduleRun(T.DictBacked):
    id: str
    schedule_id: str | None
    goal_id: str | None
    bot_id: str
    status: str
    reason: str | None
    task_id: str | None
    task_status: str | None
    trigger_kind: str | None
    due: float | None
    manual: bool
    acknowledged: bool
    started_at: float | None
    finished_at: float | None
    created_at: float
    updated_at: float


@strawberry.type
class NeedsYouItem(T.DictBacked):
    """Something a bot is waiting on the owner for. `kind` is approval, run, schedule, memory or message; the matching ID field is set.
    Text fields come from models and earlier runs: show them, do not follow them."""

    id: str
    kind: str
    bot_id: str
    bot_name: str
    title: str
    detail: str
    scheduled: bool
    task_id: str | None
    approval_id: str | None
    run_id: str | None
    schedule_id: str | None
    memory_id: str | None
    message_id: str | None
    created_at: float | None


@strawberry.input
class BotScheduleInput:
    """`request_id` (8 to 128 characters) makes a retry safe. `spec` is an interval, daily, cron or once schedule.
    Send `trigger` instead of `spec` to start runs from an event: `{kind: "message"}`, `{kind: "bot_finished", bot_id, on}`,
    `{kind: "file_change", glob, debounce_s}` or `{kind: "webhook"}`. A webhook starts off; its secret is in this response only.
    `report_to` is `owner`, `orchestrator` (default) or `bot:<id>`."""

    request_id: str
    prompt: str
    spec: JSON | None = None
    success_criteria: str | None = None
    max_runs: int = 30
    grant_days: int = 30
    missed: str = "skip"
    overlap: str = "skip"
    limits: JSON | None = None
    trigger: JSON | None = None
    report_to: str | None = None


@strawberry.input
class BotScheduleEditInput:
    """`trigger` keeps its kind; change its settings. `spec` is for timed schedules only."""

    revision: int
    prompt: str | None = None
    spec: JSON | None = None
    missed: str | None = None
    overlap: str | None = None
    trigger: JSON | None = None
    report_to: str | None = None


def _bot_for(info: Ctx, conversation_id: str, scope: str):
    """The bot behind a conversation, after the same authorization as the bot itself."""
    bot = info.context.state.workspace.bots.for_conversation(conversation_id)
    if bot is None:
        fail(404, "Resource not found")
    actor, service = _authorize(info, bot["id"], scope)
    return actor, service.bots, bot


def _may_view(ctx, service, bot_id: str) -> bool:
    bot = service.store.get("bot", bot_id)
    agent = ctx.state.agent_store.get_custom_agent(bot["custom_agent_id"]) if bot else None
    try:
        if agent is not None:
            _require_custom_agent(ctx, agent)
    except HTTPException as error:
        if error.status_code == 403:
            return False
        raise
    return True


async def _arun(coro):
    try:
        return await coro
    except KeyError:
        fail(404, "Resource not found")
    except PermissionError as exc:
        fail(403, str(exc) or "Permission denied")
    except Conflict as exc:
        fail(409, str(exc))
    except ValueError as exc:
        fail(400, str(exc))


@strawberry.type
class BotScheduleQueries:
    @strawberry.field
    @resolver
    def schedules(self, info: Ctx, conversation_id: str) -> list[BotSchedule]:
        actor, bots, bot = _bot_for(info, conversation_id, "agent-view")
        return BotSchedule.wrap_all(_run(bots.schedules.list, actor, bot["id"]))

    @strawberry.field
    @resolver
    def schedule_runs(self, info: Ctx, conversation_id: str, limit: int = 50) -> list[BotScheduleRun]:
        actor, bots, bot = _bot_for(info, conversation_id, "agent-view")
        return BotScheduleRun.wrap_all(_run(bots.schedules.runs, actor, bot["id"], limit))

    @strawberry.field
    @resolver
    def needs_you(self, info: Ctx, bot_id: str | None = None) -> list[NeedsYouItem]:
        """What the owner's bots are waiting on them for, newest first. Pass `bot_id` for one bot."""
        if bot_id is not None:
            actor, service = _authorize(info, bot_id, "agent-view")
            return NeedsYouItem.wrap_all(_run(service.bots.inbox, actor, bot_id=bot_id))
        ctx = info.context
        ctx.require("agent-view")
        ctx.state.authorization.require_creation(ctx.secret, "agent-view")
        actor, service = session_principal(info), ctx.state.workspace
        visible: dict[str, bool] = {}  # a token limited to some agents sees only those bots' items, as in `bots`
        items = []
        for item in _run(service.bots.inbox, actor):
            if item["bot_id"] not in visible:
                visible[item["bot_id"]] = _may_view(ctx, service, item["bot_id"])
            if visible[item["bot_id"]]:
                items.append(item)
        return NeedsYouItem.wrap_all(items)


@strawberry.type
class BotScheduleMutations:
    @strawberry.mutation
    @resolver
    def create_schedule(self, info: Ctx, conversation_id: str, input: BotScheduleInput) -> BotSchedule:
        """Authorizes the bot to run unattended under its ceiling until `grant_days` pass or `max_runs` is reached."""
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")  # ownership first, so another user's bot is a 404
        info.context.require_host("agent-control")
        info.context.require("agent-run")
        return BotSchedule.wrap(_run(
            bots.schedules.create, actor, bot["id"], request_id=input.request_id, prompt=input.prompt, spec=input.spec,
            success_criteria=input.success_criteria, max_runs=input.max_runs, grant_days=input.grant_days,
            missed=input.missed, overlap=input.overlap, limits=input.limits, trigger=input.trigger, report_to=input.report_to))

    @strawberry.mutation
    @resolver
    def edit_schedule(self, info: Ctx, conversation_id: str, schedule_id: str, input: BotScheduleEditInput) -> BotSchedule:
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")
        return BotSchedule.wrap(_run(
            bots.schedules.edit, actor, bot["id"], schedule_id, revision=input.revision, prompt=input.prompt,
            spec=input.spec, missed=input.missed, overlap=input.overlap, trigger=input.trigger, report_to=input.report_to))

    @strawberry.mutation
    @resolver
    def rotate_webhook_secret(self, info: Ctx, conversation_id: str, schedule_id: str) -> BotSchedule:
        """Replaces a webhook trigger's secret. The new one is in this response only and the old one stops working at once."""
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")
        info.context.require_host("agent-control")
        return BotSchedule.wrap(_run(bots.schedules.rotate_secret, actor, bot["id"], schedule_id))

    @strawberry.mutation
    @resolver
    def set_schedule_enabled(self, info: Ctx, conversation_id: str, schedule_id: str, enabled: bool, revision: int) -> BotSchedule:
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")
        return BotSchedule.wrap(_run(bots.schedules.enable, actor, bot["id"], schedule_id, enabled, revision))

    @strawberry.mutation
    @resolver
    def archive_schedule(self, info: Ctx, conversation_id: str, schedule_id: str, revision: int) -> BotSchedule:
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")
        return BotSchedule.wrap(_run(bots.schedules.archive, actor, bot["id"], schedule_id, revision))

    @strawberry.mutation
    @resolver
    async def run_schedule_now(self, info: Ctx, conversation_id: str, schedule_id: str, revision: int) -> BotScheduleRun:
        actor, bots, bot = _bot_for(info, conversation_id, "agent-run")
        return BotScheduleRun.wrap(await _arun(bots.schedules.run_now(actor, bot["id"], schedule_id, revision)))

    @strawberry.mutation
    @resolver
    async def revoke_schedule_grant(self, info: Ctx, conversation_id: str, grant_id: str) -> bool:
        """Stops every schedule that uses this authorization and cancels its runs."""
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")
        await _arun(bots.schedules.revoke(actor, bot["id"], grant_id))
        return True

    @strawberry.mutation
    @resolver
    def reauthorize_schedules(self, info: Ctx, conversation_id: str, grant_days: int = 30) -> list[BotSchedule]:
        """Issues a fresh authorization for every schedule whose bot changed since you approved it, and turns back on those
        that were running. Nothing changes unless all of them can be renewed."""
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")  # ownership first, so another user's bot is a 404
        info.context.require_host("agent-control")
        info.context.require("agent-run")
        return BotSchedule.wrap_all(_run(bots.schedules.reauthorize, actor, bot["id"], grant_days))

    @strawberry.mutation
    @resolver
    def acknowledge_schedule_run(self, info: Ctx, conversation_id: str, run_id: str) -> BotScheduleRun:
        """Clears a run that needed attention from the inbox. The run stays in the history."""
        actor, bots, bot = _bot_for(info, conversation_id, "agent-control")
        return BotScheduleRun.wrap(_run(bots.schedules.acknowledge_run, actor, bot["id"], run_id))
