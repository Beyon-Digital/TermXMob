"""GraphQL types for CLI-session events (kept out of graphql/types.py to avoid merge churn)."""
from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from termx.graphql.types import DictBacked


@strawberry.type
class CliSession(DictBacked):
    id: str
    cli: str
    label: str | None
    title: str | None
    status: str
    status_source: str | None
    status_detail: str | None
    status_updated_at: float | None
    project_id: str | None
    cwd: str | None
    isolation: str | None
    worktree: JSON | None
    pty_session_id: str | None
    sandbox_profile: str | None
    mode: str | None
    started_at: float | None
    updated_at: float | None
    ended_at: float | None
    exit_code: int | None
    ended_reason: str | None
    native_session_id: str | None
    resumable: bool | None
    attention: bool | None


@strawberry.type
class CliSessionEvent(DictBacked):
    """``cli.session.created`` | ``cli.session.status`` | ``cli.session.exited``."""

    type: str

    @strawberry.field
    def session(self) -> CliSession | None:
        return CliSession.wrap(object.__getattribute__(self, "_d").get("session"))
