"""Authoring domain: describe something in plain words and get a draft to review.

A draft is never saved. It pre-fills the same form the person would otherwise fill in by hand, and
the create mutation for that kind still decides whether it is allowed.
"""

from __future__ import annotations

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx.authoring import DraftError, draft_definition
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver

Ctx = Info[TermxContext, None]


@strawberry.input
class DraftMessageInput:
    """`role` is `user` or `assistant`. The last message must come from the user."""

    role: str
    text: str


@strawberry.input
class DraftDefinitionInput:
    """Needs the same permissions as creating that kind. A skill also takes the `project_id` it will be saved to, if any."""

    kind: str
    messages: list[DraftMessageInput]
    current_draft: JSON | None = None
    provider_id: str | None = None
    model: str | None = None
    project_id: str | None = None


@strawberry.type
class DraftDefinition:
    """`status` is `drafted`, `needs_input` (the model asks a question) or `no_provider` (no model account is set up).
    `draft` fields depend on the kind. Nothing is created or stored."""

    status: str
    reply: str
    draft: JSON | None
    warnings: list[str]
    provider_id: str | None
    model: str | None


def _authorize(ctx: TermxContext, input: DraftDefinitionInput) -> None:
    """The permissions of the matching create operation."""
    if input.kind == "agent":
        ctx.require_host("agent-control")
    elif input.kind == "bot":
        ctx.require_host("agent-control")
        ctx.require("agent-run")
    elif input.kind == "skill":
        if input.project_id:
            ctx.require_project("files-write", input.project_id)
        else:
            ctx.require_host("host-admin")
    elif input.kind == "character":
        ctx.require_host("agent-run")
    else:
        raise HTTPException(400, {"message": "Choose agent, bot, skill or character.", "code": "BAD_REQUEST"})


@strawberry.type
class AuthoringMutations:
    @strawberry.mutation
    @resolver
    async def draft_definition(self, info: Ctx, input: DraftDefinitionInput) -> DraftDefinition:
        """Asks the model once, with no tools, for a draft of an agent, bot, skill or character. Stores nothing."""
        ctx = info.context
        _authorize(ctx, input)
        try:
            result = await draft_definition(
                ctx.state.agent, kind=input.kind,
                messages=[{"role": m.role, "text": m.text} for m in input.messages],
                current_draft=input.current_draft, provider_id=input.provider_id, model=input.model)
        except DraftError as exc:
            raise HTTPException(exc.status, {"message": exc.message, "code": exc.code.upper()}) from None
        return DraftDefinition(**result)
