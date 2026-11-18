"""GraphQL subscriptions — the graphql-ws successors of the event WebSockets.

``activity_events`` and ``agent_task_events`` carry the exact payload shapes
and heartbeat/replay semantics of the sockets they replace. PTY, desktop and
LSP stay on their duplex WebSocket endpoints (binary/interactive protocols).
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import strawberry
from fastapi import HTTPException
from strawberry.types import Info

from termx.graphql.context import TermxContext
from termx.graphql.errors import fail, to_graphql_error
from termx.graphql import types as T
from termx.workspace.public_events import context_task_events
from termx.cli_sessions.gql import CliSessionEvent
from termx.graphql.domains.host_status import HostStatus
from termx.cli_sessions.status import ENDED as ENDED_STATUSES

CLI_POLL_SECONDS = 1.0
HOST_STATUS_POLL_SECONDS = 2.0
HOST_STATUS_KEEPALIVE_SECONDS = 30.0

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
    async def host_status_events(self, info: Ctx) -> AsyncGenerator[HostStatus, None]:
        """The host's health: one snapshot on connect, then one whenever something other than uptime changes
        (and every 30 s so uptime and the heartbeat age stay fresh). Scope re-checked on every poll."""
        from termx.graphql.domains.host_status import host_status_service
        from termx.host_status import stable_view

        ctx = info.context
        ctx.require_host("machine-view")
        service = host_status_service(ctx)
        last_view: dict | None = None
        last_sent = 0.0
        while True:
            ctx.require_host("machine-view")  # authority can change while the socket is open
            snapshot = await asyncio.to_thread(service.snapshot)
            view = stable_view(snapshot)
            now = asyncio.get_running_loop().time()
            if view != last_view or now - last_sent >= HOST_STATUS_KEEPALIVE_SECONDS:
                last_view, last_sent = view, now
                yield HostStatus.wrap(snapshot)
            await asyncio.sleep(HOST_STATUS_POLL_SECONDS)

    @strawberry.subscription
    async def cli_session_events(self, info: Ctx) -> AsyncGenerator[CliSessionEvent, None]:
        """Poll-and-diff of the caller's CLI sessions: created / status / exited (see docs/cli-sessions.md)."""
        from termx.cli_sessions.http import owner_for, visible

        ctx = info.context
        ctx.require("terminal-view")
        state = ctx.state
        service = getattr(state, "cli_sessions", None)
        if service is None:
            fail(503, "CLI sessions are unavailable")
        last: dict[str, tuple] = {}
        while True:
            ctx.require("terminal-view")  # authority can change while the socket is open
            owner = owner_for(state, ctx.secret)
            current = {row["id"]: row for row in service.rows(owner) if visible(state, ctx.secret, row)}
            for session_id, row in current.items():
                key = (row.get("status"), row.get("status_source"), row.get("ended_at"))
                previous = last.get(session_id)
                public = service.public(row)
                if previous is None:
                    yield CliSessionEvent.wrap({"type": "cli.session.created", "session": public})
                if row.get("status") in ENDED_STATUSES:
                    if previous is None or previous[0] not in ENDED_STATUSES:
                        yield CliSessionEvent.wrap({"type": "cli.session.exited", "session": public})
                elif previous is not None and previous != key:
                    yield CliSessionEvent.wrap({"type": "cli.session.status", "session": public})
            last = {session_id: (row.get("status"), row.get("status_source"), row.get("ended_at")) for session_id, row in current.items()}
            await asyncio.sleep(CLI_POLL_SECONDS)

    @strawberry.subscription
    async def agent_task_events(
        self, info: Ctx, task_id: str, after: int = 0
    ) -> AsyncGenerator[T.TaskEventEnvelope, None]:
        ctx = info.context
        def require_task():
            try:
                ctx.require_resource('agent-view', 'task', task_id)
            except HTTPException as exc:
                raise to_graphql_error(exc) from exc
        require_task()
        state = ctx.state
        if state.agent_store.get_task(task_id) is None:
            fail(404, "task not found")
        queue = state.agent.subscribe(task_id)
        cursor = max(0, after)
        try:
            for event in state.agent_store.events(task_id, after=cursor):
                require_task()
                yield T.TaskEventEnvelope.wrap(context_task_events(ctx, [event])[0])
                cursor = max(cursor, int(event["sequence"]))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    require_task()
                    yield T.TaskEventEnvelope.wrap(
                        {"type": "heartbeat", "sequence": cursor, "task_id": task_id}
                    )
                    continue
                sequence = int(event["sequence"])
                if sequence <= cursor:
                    continue
                require_task()
                yield T.TaskEventEnvelope.wrap(context_task_events(ctx, [event])[0])
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
