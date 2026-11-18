"""REST for CLI sessions: ``/api/cli-sessions`` and ``/api/cli-agents``.

Scopes are the terminal ones; no new scopes exist. Viewing needs ``terminal-view``;
starting and steering need ``terminal-control``, and a managed principal must also hold
that scope on the session's project (starting) or own the session's terminal (steering),
exactly as for ``/api/sessions/{id}/pty``. A compatibility passcode/device token has no
principal and acts as the single host owner. The TUI itself streams over the existing
``/api/sessions/{pty_session_id}/pty`` websocket.

``POST /api/cli-sessions/hook/{token}`` is the one unauthenticated route: a loopback-only
capability URL handed to the CLI's own hook (see ``hooks.py``).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.cli_sessions import catalogue as C
from termx.cli_sessions import hooks as H
from termx.cli_sessions.service import HOST_OWNER, MAX_SEND_CHARS, CliSessionError, CliSessionService
from termx.config import validate_cwd

AGENTS_TTL_S = 20.0


class CreateCliSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cli: str = Field(min_length=1, max_length=32)
    project_id: str | None = Field(default=None, max_length=128)
    cwd: str | None = Field(default=None, max_length=4096)
    prompt: str | None = Field(default=None, max_length=C.MAX_PROMPT_CHARS)
    isolation: Literal["none", "worktree"] = "none"
    mode: Literal["default", "read_only", "bypass"] = "default"
    model: str | None = Field(default=None, max_length=128)
    sandbox_profile: Literal["host", "workspace"] = "host"
    # The human's explicit acknowledgement; only honoured with a restricted sandbox profile.
    confirm_bypass: bool = False
    title: str | None = Field(default=None, max_length=120)


class CliInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=MAX_SEND_CHARS)
    submit: bool = True


def credential(request: Request) -> str | None:
    return extract_passcode(
        request.headers.get("x-termx-passcode"), request.headers.get("authorization"), request.query_params.get("k")
    )


def owner_for(state: Any, token: str | None) -> str:
    """The record owner for a credential: its managed principal, else the single host owner."""
    session = state.identity.resolve(token) if state.identity.configured else None
    return session.principal.id if session else HOST_OWNER


def require_scope(state: Any, token: str | None, scope: str) -> None:
    if not state.auth.check(token):
        raise HTTPException(401, "invalid passcode")
    if not state.auth.allows(token, scope):
        raise HTTPException(403, f"missing scope: {scope}")


def visible(state: Any, token: str | None, row: dict[str, Any], scope: str = "terminal-view") -> bool:
    return state.authorization.can(token, scope, resource_kind="terminal", resource_id=row["pty_session_id"])


def mount_cli_sessions(app, state) -> CliSessionService:
    service = getattr(state, "cli_sessions", None)
    if service is None:
        service = state.cli_sessions = CliSessionService(state)
    service.reconcile()
    router = APIRouter(prefix="/api")
    cache: dict[str, Any] = {"at": 0.0, "agents": []}

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CliSessionError as exc:
            raise HTTPException(exc.status, exc.message) from None

    async def acall(coro):
        try:
            return await coro
        except CliSessionError as exc:
            raise HTTPException(exc.status, exc.message) from None

    def gate_row(request: Request, session_id: str, scope: str) -> tuple[str, str | None, dict[str, Any]]:
        token = credential(request)
        require_scope(state, token, scope)
        owner = owner_for(state, token)
        row = call(service.row, owner, session_id)
        # The terminal behind it must still be this principal's, with the scope on its project.
        state.authorization.require(token, scope, resource_kind="terminal", resource_id=row["pty_session_id"])
        return owner, token, row

    @router.get("/cli-agents")
    async def cli_agents(request: Request) -> dict[str, Any]:
        require_scope(state, credential(request), "terminal-view")
        if time.monotonic() - cache["at"] > AGENTS_TTL_S or not cache["agents"]:
            cache["agents"] = await C.detect_installed()
            cache["at"] = time.monotonic()
        return {"agents": cache["agents"]}

    @router.get("/cli-sessions")
    def list_sessions(request: Request) -> dict[str, Any]:
        token = credential(request)
        require_scope(state, token, "terminal-view")
        owner = owner_for(state, token)
        return {"sessions": [service.public(row) for row in service.rows(owner) if visible(state, token, row)]}

    @router.post("/cli-sessions", status_code=201)
    async def create_session(request: Request, body: CreateCliSession) -> dict[str, Any]:
        token = credential(request)
        require_scope(state, token, "terminal-control")
        owner = owner_for(state, token)
        projects = state.projects
        if body.project_id:
            project = projects.project(body.project_id)  # 404 when unknown
            base = project["path"]
            if body.cwd:
                try:
                    requested = Path(validate_cwd(body.cwd)).resolve()
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
                if not requested.is_relative_to(Path(base).resolve()):
                    raise HTTPException(400, "cwd is outside the project")
                base = str(requested)
            state.authorization.require(token, "terminal-control", project_id=body.project_id)
            project_id = body.project_id
        else:
            try:
                base = validate_cwd(body.cwd or state.store.get().terminal.cwd)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            project_id = state.authorization.require_path(token, "terminal-control", base, projects.projects())
        if body.isolation == "worktree":
            for scope in ("git-read", "git-write"):
                state.authorization.require(token, scope, project_id=project_id)

        def claim(pty_id: str) -> None:
            state.authorization.claim(token, "terminal", pty_id, project_id)

        return await acall(
            service.start(
                owner=owner, cli=body.cli, cwd=base, project_id=project_id, prompt=body.prompt, mode=body.mode,
                model=body.model, isolation=body.isolation, sandbox_profile=body.sandbox_profile,
                confirm_bypass=body.confirm_bypass, title=body.title, claim=claim,
            )
        )

    @router.get("/cli-sessions/{session_id}")
    def get_session(request: Request, session_id: str) -> dict[str, Any]:
        owner, _, row = gate_row(request, session_id, "terminal-view")
        return service.public(row)

    @router.post("/cli-sessions/{session_id}/input")
    async def send_input(request: Request, session_id: str, body: CliInput) -> dict[str, Any]:
        owner, _, _ = gate_row(request, session_id, "terminal-control")
        return await acall(service.send(owner, session_id, body.text, body.submit))

    @router.post("/cli-sessions/{session_id}/interrupt")
    async def interrupt(request: Request, session_id: str) -> dict[str, Any]:
        owner, _, _ = gate_row(request, session_id, "terminal-control")
        return await acall(service.interrupt(owner, session_id))

    @router.post("/cli-sessions/{session_id}/stop")
    async def stop(request: Request, session_id: str) -> dict[str, Any]:
        owner, _, _ = gate_row(request, session_id, "terminal-control")
        return await acall(service.stop(owner, session_id))

    @router.post("/cli-sessions/hook/{token}")
    async def hook(token: str, request: Request) -> dict[str, Any]:
        host = (request.client.host if request.client else "") or ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(403, "hooks are loopback-only")
        size, chunks = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > H.MAX_HOOK_BYTES:
                raise HTTPException(413, "hook payload too large")
            chunks.append(chunk)
        return call(service.apply_hook, token, b"".join(chunks))

    app.include_router(router)
    return service
