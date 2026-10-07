"""Authenticated development API; no raw adapter socket or arbitrary command."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from termx.auth import extract_passcode
from termx import git_ops


def secret(request):
    return extract_passcode(request.headers.get("x-termx-passcode"), request.headers.get("authorization"))


class CreateDebug(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(max_length=32)


class DebugCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(max_length=64)
    arguments: dict = Field(default_factory=dict)


class DeliveryPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(max_length=64)
    arguments: dict = Field(default_factory=dict)


class DeliveryExecute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool = False


def mount_development(app, state):
    router = APIRouter(prefix="/api/development")

    def require(request, scope, project_id=None, resource_id=None):
        return state.authorization.require(secret(request), scope, project_id=project_id,
            resource_kind="debug" if resource_id else None, resource_id=resource_id)

    def session(request, session_id):
        item = state.debug.sessions.get(session_id)
        if item is None:
            raise HTTPException(404, "Debug session not found")
        require(request, "agent-run", item.project_id, item.id)
        return item

    @router.get("/debug/adapters")
    def adapters(request: Request):
        require(request, "machine-view")
        return state.debug.capabilities()

    @router.post("/projects/{project_id}/debug")
    async def create(project_id: str, body: CreateDebug, request: Request):
        require(request, "agent-run", project_id)
        root = state.projects.project(project_id)["path"]
        item = await state.debug.create(project_id, root, body.language)
        state.authorization.claim(secret(request), "debug", item.id, project_id=project_id)
        return {"id": item.id, "project_id": project_id, "language": item.language, "status": item.status}

    @router.get("/debug/{session_id}/events")
    def events(session_id: str, request: Request, after: int = 0):
        item = session(request, session_id)
        records = [event for event in item.events if event["id"] > after]
        return {"events": records, "cursor": item.cursor, "status": item.status,
                "reset_required": bool(item.events and after < item.events[0]["id"] - 1)}

    @router.post("/debug/{session_id}/command")
    async def command(session_id: str, body: DebugCommand, request: Request):
        item = session(request, session_id)
        if body.command == "attach":
            require(request, "host-admin")
        args = item.validate(body.command, body.arguments)
        try:
            return await item.active.request(body.command, args)
        except (ConnectionError, TimeoutError) as exc:
            raise HTTPException(503, "Debug adapter unavailable; reconnect or end this session") from exc

    @router.delete("/debug/{session_id}")
    async def close(session_id: str, request: Request):
        await session(request, session_id).close()
        return {"ok": True}

    def actor(request):
        identity = state.identity.resolve(secret(request))
        return identity.principal.id if identity else "legacy"

    @router.get("/projects/{project_id}/delivery")
    def delivery(project_id: str, request: Request, worktree_id: str | None = None):
        require(request, "git-read", project_id)
        root = state.projects.project(project_id)["path"]
        target = state.delivery.target(project_id, root, worktree_id)
        status = git_ops.status(target)
        remotes = git_ops._require_ok(git_ops._git(target,'remote')).splitlines() if status['repo'] else []
        return {"status": status, "worktrees": state.delivery.worktrees(project_id), 'remotes': remotes}

    @router.get("/projects/{project_id}/delivery/diff")
    def diff(project_id: str, request: Request, path: str, staged: bool = False, worktree_id: str | None = None):
        require(request, "git-read", project_id)
        root = state.delivery.target(project_id, state.projects.project(project_id)["path"], worktree_id)
        state.delivery.paths(root, [path])
        return git_ops.diff(root, path, staged)

    @router.post("/projects/{project_id}/delivery/prepare")
    def prepare(project_id: str, body: DeliveryPrepare, request: Request):
        require(request, "git-write", project_id)
        return state.delivery.prepare(actor(request), project_id, state.projects.project(project_id)["path"],
                                      body.operation, body.arguments)

    @router.get("/projects/{project_id}/delivery/actions/{action_id}")
    def action(project_id: str, action_id: str, request: Request):
        require(request, "git-read", project_id)
        return state.delivery.record(action_id, actor(request), project_id)

    @router.get('/projects/{project_id}/delivery/actions')
    def recent_actions(project_id: str, request: Request):
        require(request,'git-read',project_id)
        return state.delivery.records(actor(request),project_id)

    @router.post("/projects/{project_id}/delivery/actions/{action_id}/execute")
    async def execute(project_id: str, action_id: str, body: DeliveryExecute, request: Request):
        import asyncio
        require(request, "git-write", project_id)
        return await asyncio.to_thread(state.delivery.execute, action_id, actor(request), project_id,
                                      state.projects.project(project_id)["path"], confirmed=body.confirmed)

    @router.get("/projects/{project_id}/delivery/pulls/{number}")
    async def pull_request(project_id: str, number: int, request: Request):
        import asyncio
        require(request, "git-read", project_id)
        return await asyncio.to_thread(state.delivery.github.read, state.projects.project(project_id)["path"], number)

    app.include_router(router)
