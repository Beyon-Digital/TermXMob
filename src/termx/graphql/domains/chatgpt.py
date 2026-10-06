"""Host ChatGPT registration and account controls. Never expose credentials."""
from __future__ import annotations

import hashlib
import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver

Ctx = Info[TermxContext, None]


def _owner(ctx):
    return hashlib.sha256((ctx.secret or "passcode-off-host").encode()).hexdigest()


@strawberry.type
class ChatGPTQueries:
    @strawberry.field
    @resolver
    async def chatgpt_accounts(self, info: Ctx) -> JSON:
        info.context.require("ai-settings")
        return await info.context.state.chatgpt.accounts()

    @strawberry.field
    @resolver
    def chatgpt_sign_in_status(self, info: Ctx, attempt_id: str) -> JSON:
        ctx = info.context
        ctx.require("ai-settings")
        try:
            return ctx.state.chatgpt.status(attempt_id, _owner(ctx))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc


@strawberry.type
class ChatGPTMutations:
    @strawberry.mutation
    @resolver
    async def start_chatgpt_sign_in(self, info: Ctx, account_id: str | None = None) -> JSON:
        ctx = info.context
        ctx.require("ai-settings")
        try:
            return await ctx.state.chatgpt.start(_owner(ctx), account_id)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(400, "Could not start ChatGPT sign-in on the host" if isinstance(exc, OSError) else str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def cancel_chatgpt_sign_in(self, info: Ctx, attempt_id: str) -> JSON:
        ctx = info.context
        ctx.require("ai-settings")
        try:
            return await ctx.state.chatgpt.cancel(attempt_id, _owner(ctx))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @strawberry.mutation
    @resolver
    async def refresh_chatgpt_models(self, info: Ctx, account_id: str) -> JSON:
        ctx = info.context
        ctx.require("ai-settings")
        try:
            return await ctx.state.chatgpt.models(account_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, "Could not reach ChatGPT. Try again later.") from exc

    @strawberry.mutation
    @resolver
    async def acknowledge_chatgpt_plan(self, info: Ctx, account_id: str) -> JSON:
        ctx = info.context
        ctx.require("ai-settings")
        try:
            await ctx.state.chatgpt.acknowledge(account_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @strawberry.mutation
    @resolver
    async def sign_out_chatgpt(self, info: Ctx, account_id: str) -> JSON:
        ctx = info.context
        ctx.require("ai-settings")
        for task in ctx.state.agent_store.active_tasks_for_provider(account_id):
            if task.get("provider_id") == account_id and task["status"] not in {"completed", "failed", "cancelled"}:
                ctx.state.agent.cancel(task["id"])
        try:
            return await ctx.state.chatgpt.sign_out(account_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
