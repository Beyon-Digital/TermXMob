"""Memory domain: scoped notes (user, project, agent) and proposals written by unattended agent runs."""

from __future__ import annotations

from typing import Callable

import strawberry
from strawberry.types import Info

from termx.graphql import types as T
from termx.graphql.context import TermxContext
from termx.graphql.domains.desktop_workspace import principal as session_principal
from termx.graphql.errors import fail, resolver
from termx.workspace.store import Conflict

Ctx = Info[TermxContext, None]


@strawberry.type
class MemoryEntry(T.DictBacked):
    """One note. `scope` is user, project or agent. `author` is user or agent; agent-written notes reach prompts as untrusted data.
    `state` is active, or proposed while the owner has not yet decided to keep it (proposals are never used in a prompt)."""

    id: str
    revision: int
    content: str
    provenance: str
    project_id: str | None
    retention_days: int
    excluded: bool
    expires_at: float | None
    scope: str
    agent_id: str | None
    author: str
    state: str


@strawberry.input
class MemoryInput:
    """`scope` defaults to project when a project is given, else user. Scope, agent and project cannot change once saved."""

    content: str
    provenance: str
    project_id: str | None = None
    retention_days: int = 30
    excluded: bool = False
    scope: str | None = None
    agent_id: str | None = None
    identifier: str | None = None
    revision: int | None = None


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


@strawberry.type
class MemoryQueries:
    @strawberry.field
    @resolver
    def memories(
        self, info: Ctx, project_id: str | None = None, scope: str | None = None, agent_id: str | None = None,
        state: str | None = None,
    ) -> list[MemoryEntry]:
        """Without `agent_id` every agent's notes are listed for the owner; with it, the notes that agent gets."""
        ctx = info.context
        ctx.require("agent-view")
        actor = session_principal(info)
        rows = _run(ctx.state.workspace.memories, actor, project_id=project_id, scope=scope, agent_id=agent_id,
                    state=state, all_agents=agent_id is None)
        return MemoryEntry.wrap_all(rows)


@strawberry.type
class MemoryMutations:
    @strawberry.mutation
    @resolver
    def save_memory(self, info: Ctx, input: MemoryInput) -> MemoryEntry:
        ctx = info.context
        ctx.require("agent-control")
        actor = session_principal(info)
        row = _run(ctx.state.workspace.save_memory, actor, content=input.content, provenance=input.provenance,
                   project_id=input.project_id, retention_days=input.retention_days, excluded=input.excluded,
                   identifier=input.identifier, revision=input.revision, scope=input.scope, agent_id=input.agent_id)
        return MemoryEntry.wrap(row)

    @strawberry.mutation
    @resolver
    def resolve_memory_proposal(self, info: Ctx, memory_id: str, keep: bool) -> MemoryEntry | None:
        """Keep a proposed note (returns it, now active) or discard it (returns null)."""
        ctx = info.context
        ctx.require("agent-control")
        actor = session_principal(info)
        row = _run(ctx.state.workspace.resolve_memory_proposal, actor, memory_id, keep=keep)
        return MemoryEntry.wrap(row)
