"""Managed-session Data Studio routes; no host passcode or implicit grants."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.data.service import DataService
from termx.identity_http import ACCESS_COOKIE, loopback, same_origin


class DataInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RevisionInput(DataInput):
    revision: int = Field(ge=1)


class WorkspaceInput(DataInput):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceUpdate(WorkspaceInput):
    revision: int = Field(ge=1)


class MemberInput(DataInput):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    principal_id: str | None = Field(default=None, min_length=1, max_length=128)
    role: Literal['editor', 'viewer'] = 'viewer'


class MemberUpdate(DataInput):
    revision: int = Field(ge=1)
    role: Literal['owner', 'editor', 'viewer']


def managed_actor(state):
    """Return a fresh managed identity, with cookie CSRF even when standalone."""
    def actor(request: Request):
        peer = request.client.host if request.client else ''
        if request.url.scheme != 'https' and not loopback(peer):
            raise HTTPException(403, 'TLS is required for remote Data access')
        authorization = request.headers.get('authorization', '')
        bearer = None
        if authorization:
            parts = authorization.split(' ', 1)
            if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1].strip():
                raise HTTPException(401, 'Managed Bearer sign-in required')
            bearer = parts[1].strip()
        raw = bearer or request.cookies.get(ACCESS_COOKIE)
        current = state.identity.resolve(raw, allow_locked=True)
        if current is None:
            raise HTTPException(401, 'Managed sign-in required')
        if current.locked:
            raise HTTPException(423, 'Session locked; unlock required')
        origin = request.headers.get('origin')
        if origin and not same_origin(origin, request.url.scheme, request.headers.get('host', '')):
            raise HTTPException(403, 'Same-origin Data request required')
        if not bearer and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if (not same_origin(origin, request.url.scheme, request.headers.get('host', '')) or
                    not state.identity.valid_csrf(raw, request.headers.get('x-termx-csrf'))):
                raise HTTPException(403, 'Same-origin request and CSRF token required')
        return current.principal
    return actor


def build_data_router(state):
    service = getattr(state, 'data_studio', None)
    if service is None:
        directory = Path(state.delivery.directory).parent / 'workspace-artifacts' / 'data'
        service = state.data_studio = DataService(state, directory)
    router = APIRouter(prefix='/api/data')
    actor = managed_actor(state)

    @router.get('/bootstrap')
    def bootstrap(request: Request):
        principal = actor(request)
        return {'workspaces': service.workspaces(principal), 'engines': service.runtime.engines(),
                'capabilities': {'team_workspaces': True, 'roles': ['owner', 'editor', 'viewer'],
                                 'source_writes': False, 'optimistic_revisions': True,
                                 'report_publication': True, 'copilot': True}}

    @router.post('/workspaces', status_code=201)
    def create_workspace(body: WorkspaceInput, request: Request):
        return service.create_workspace(actor(request), body.name)

    @router.get('/workspaces/{wid}')
    def workspace(wid: str, request: Request):
        return service.snapshot(actor(request), wid)

    @router.patch('/workspaces/{wid}')
    def update_workspace(wid: str, body: WorkspaceUpdate, request: Request):
        return service.update_workspace(actor(request), wid, body.revision, body.name)

    @router.delete('/workspaces/{wid}')
    def archive_workspace(wid: str, request: Request, revision: int | None = Query(default=None, ge=1),
                          body: RevisionInput | None = Body(default=None)):
        expected = body.revision if body else revision
        if expected is None or (body and revision is not None and body.revision != revision):
            raise HTTPException(422, 'Provide the current revision')
        return service.archive_workspace(actor(request), wid, expected)

    @router.get('/workspaces/{wid}/members')
    def members(wid: str, request: Request):
        return service.members(actor(request), wid)

    @router.post('/workspaces/{wid}/members', status_code=201)
    def add_member(wid: str, body: MemberInput, request: Request):
        return service.add_member(actor(request), wid, **body.model_dump())

    @router.patch('/workspaces/{wid}/members/{mid}')
    def update_member(wid: str, mid: str, body: MemberUpdate, request: Request):
        return service.change_member(actor(request), wid, mid, body.revision, role=body.role)

    @router.delete('/workspaces/{wid}/members/{mid}')
    def delete_member(wid: str, mid: str, request: Request, revision: int | None = Query(default=None, ge=1),
                      body: RevisionInput | None = Body(default=None)):
        expected = body.revision if body else revision
        if expected is None or (body and revision is not None and body.revision != revision):
            raise HTTPException(422, 'Provide the current revision')
        return service.change_member(actor(request), wid, mid, expected, remove=True)

    return router


def mount_data(app, state):
    """Mount isolated metadata, execution, BI, and Copilot APIs once."""
    if getattr(state, 'data_studio', None) is None:
        state.data_studio = DataService(state, Path(state.delivery.directory).parent / 'workspace-artifacts' / 'data')
    service = state.data_studio
    from termx.data.runtime import build_runtime_router
    from termx.data.bi import build_bi_router
    from termx.data.copilot import build_copilot_router
    actor = managed_actor(state)
    app.include_router(build_data_router(state))
    app.include_router(build_runtime_router(service, actor))
    app.include_router(build_bi_router(service, actor))
    app.include_router(build_copilot_router(service, actor))
    return service
