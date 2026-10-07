"""GraphQL subscriptions — the graphql-ws successors of the event WebSockets.

``activity_events`` and ``agent_task_events`` carry the exact payload shapes
and heartbeat/replay semantics of the sockets they replace. PTY, desktop and
LSP stay on their duplex WebSocket endpoints (binary/interactive protocols).
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import strawberry
from strawberry.types import Info

from termx.graphql.context import TermxContext
from termx.graphql.errors import fail
from termx.graphql import types as T

Ctx = Info[TermxContext, None]


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def activity_events(self, info: Ctx) -> AsyncGenerator[T.ActivityEvent, None]:
        ctx = info.context
        ctx.require_host('machine-view')
        # Host-wide activity/notification streams contain other users' resources.
        if ctx.state.identity.configured and ctx.state.identity.resolve(ctx.secret):
            ctx.require_host('host-admin')
        from termx.activity import activity_snapshot

        state = ctx.state
        last: dict[str, dict] = {}
        while True:
            snapshot = activity_snapshot(state)
            current = {item["id"]: item for item in snapshot["activity"]}
            for item_id, item in current.items():
                if last.get(item_id) != item:
                    yield T.ActivityEvent.wrap({"type": "activity.upsert", "activity": item})
            for item_id in last.keys() - current.keys():
                yield T.ActivityEvent.wrap({"type": "activity.remove", "id": item_id})
            last = current
            await asyncio.sleep(2.0)

    @strawberry.subscription
    async def agent_task_events(
        self, info: Ctx, task_id: str, after: int = 0
    ) -> AsyncGenerator[T.TaskEventEnvelope, None]:
        ctx = info.context
        ctx.require_resource('agent-view', 'task', task_id)
        state = ctx.state
        if state.agent_store.get_task(task_id) is None:
            fail(404, "task not found")
        queue = state.agent.subscribe(task_id)
        cursor = max(0, after)
        try:
            for event in state.agent_store.events(task_id, after=cursor):
                yield T.TaskEventEnvelope.wrap(event)
                cursor = max(cursor, int(event["sequence"]))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield T.TaskEventEnvelope.wrap(
                        {"type": "heartbeat", "sequence": cursor, "task_id": task_id}
                    )
                    continue
                sequence = int(event["sequence"])
                if sequence <= cursor:
                    continue
                yield T.TaskEventEnvelope.wrap(event)
                cursor = sequence
        finally:
            state.agent.unsubscribe(task_id, queue)

    @strawberry.subscription
    async def notification_events(self, info: Ctx) -> AsyncGenerator[T.HostNotification, None]:
        ctx = info.context
        ctx.require_host('machine-view')
        # Host-wide activity/notification streams contain other users' resources.
        if ctx.state.identity.configured and ctx.state.identity.resolve(ctx.secret):
            ctx.require_host('host-admin')
        state = ctx.state
        queue = state.notifications.subscribe()
        try:
            while True:
                item = await queue.get()
                yield T.HostNotification.wrap(item)
        finally:
            state.notifications.unsubscribe(queue)
