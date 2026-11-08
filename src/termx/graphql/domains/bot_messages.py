"""Messages between bots, the main agent and the owner over GraphQL.

A message is addressed `owner`, `orchestrator` or `bot:<id>`. The owner can message any of their bots; a bot's own messages
come from the bot's runs. Message text was written by an agent or the owner: show it, never treat it as instructions.
"""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from termx.graphql import types as T
from termx.graphql.domains.bots import Ctx, _authorize, _run
from termx.graphql.domains.desktop_workspace import principal as session_principal
from termx.graphql.errors import fail, resolver


@strawberry.type
class BotMessage(T.DictBacked):
    """`sender` and `recipient` are `{kind, id, name}` with kind owner, orchestrator or bot. `status` is unread, read, done or
    parked; a parked message was held by a loop or rate limit, was delivered to nobody, and has its `reason`. `hops` counts
    messages in the thread. `automatic` marks a report the host posted when a task finished."""

    id: str
    thread_id: str
    sender: JSON
    recipient: JSON
    kind: str
    subject: str
    body: str
    task_ref: str | None
    source_task_id: str | None
    hops: int
    status: str
    reason: str | None
    automatic: bool
    in_reply_to: str | None
    created_at: float
    updated_at: float


@strawberry.input
class SendBotMessageInput:
    """`to` is `orchestrator` or `bot:<id>`. `kind` is request, handoff, reply or report. Set `in_reply_to` to answer a message."""

    to: str
    body: str
    kind: str = "request"
    subject: str = ""
    in_reply_to: str | None = None


def _bot_ids(row: dict) -> list[str]:
    return [party["id"] for party in (row["from"], row["to"]) if party["kind"] == "bot"]


@strawberry.type
class BotMessageQueries:
    @strawberry.field
    @resolver
    def bot_messages(self, info: Ctx, bot_id: str, thread_id: str | None = None, limit: int = 100) -> list[BotMessage]:
        """Messages a bot sent or received, oldest first. Pass `thread_id` for one conversation."""
        actor, service = _authorize(info, bot_id, "agent-view")
        return BotMessage.wrap_all(_run(service.bots.messages.list, actor, bot_id, thread_id=thread_id, limit=limit))


@strawberry.type
class BotMessageMutations:
    @strawberry.mutation
    @resolver
    def send_bot_message(self, info: Ctx, input: SendBotMessageInput) -> BotMessage:
        """Send a message as the owner. A bot reads it as data, inside its own ceiling, and a message trigger can wake it."""
        recipient = input.to
        if recipient.startswith("bot:"):
            actor, service = _authorize(info, recipient[4:], "agent-run")
        else:
            info.context.require("agent-run")
            actor, service = session_principal(info), info.context.state.workspace
        view = _run(service.bots.messages.send, actor, {"kind": "owner", "id": None}, recipient, kind=input.kind,
                    subject=input.subject, body=input.body, in_reply_to=input.in_reply_to)
        return BotMessage.wrap(view)

    @strawberry.mutation
    @resolver
    def mark_message(self, info: Ctx, message_id: str, status: str) -> BotMessage:
        """`read` or `done`. A held message can only be marked `done`, which dismisses it."""
        info.context.require("agent-control")
        actor, service = session_principal(info), info.context.state.workspace
        row = service.store.get("bot_message", message_id)
        if row is None or row["owner"] != actor.id:
            fail(404, "Resource not found")
        for bot_id in _bot_ids(row):  # a token limited to some agents can't touch another bot's messages
            _authorize(info, bot_id, "agent-control")
        return BotMessage.wrap(_run(service.bots.messages.mark, actor, message_id, status))
