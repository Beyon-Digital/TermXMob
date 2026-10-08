"""Account-owned project navigation preferences over canonical project IDs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.identity_http import ACCESS_COOKIE
from termx.workspace.store import Conflict


class ProjectPinChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(min_length=1, max_length=128)
    pinned: bool
    revision: int = Field(ge=0)


class ProjectPins:
    def __init__(self, workspace):
        self.workspace = workspace

    def _allowed(self, principal, identifier):
        self.workspace.require(principal, 'files-read', identifier)
        self.workspace.state.projects.project(identifier)

    def read(self, principal):
        live=self.workspace.state.identity.current_principal(principal)
        if not live or 'files-read' not in live.scopes:
            raise PermissionError('Current file-view authority required')
        principal=live
        row = self.workspace.store.get('project_pins', principal.id)
        visible = []
        for identifier in row.get('projects', []) if row else []:
            try:
                self._allowed(principal, identifier)
            except (PermissionError, KeyError, ValueError):
                continue
            except HTTPException as exc:
                if exc.status_code not in {403,404}:
                    raise
                continue
            visible.append(identifier)
        return {'revision': row['revision'] if row else 0, 'projects': visible}

    def set(self, principal, identifier, pinned, revision):
        self._allowed(principal, identifier)
        store = self.workspace.store
        with store.lock:
            current = store.get('project_pins', principal.id)
            if (current['revision'] if current else 0) != revision:
                raise Conflict('Project pins changed in another window. Reload before changing them.')
            projects = list(current.get('projects', [])) if current else []
            projects = [item for item in projects if item != identifier]
            if pinned:
                if len(projects) >= 500:
                    raise ValueError('Pin at most 500 projects')
                projects.append(identifier)
            if current:
                store.update('project_pins', principal.id, {'projects': projects}, revision)
            else:
                store.create('project_pins', principal.id, {'projects': projects}, identifier=principal.id)
            return self.read(principal)


def mount_project_pins(app, state):
    service = ProjectPins(state.workspace)
    router = APIRouter(prefix='/api/workspace/project-pins')

    def actor(request):
        credential = extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        identity = state.identity.resolve(credential)
        if not identity:
            raise HTTPException(401, 'Managed sign-in required')
        return identity.principal

    def call(fn, *args):
        try:
            return fn(*args)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except KeyError:
            raise HTTPException(404, 'Project unavailable') from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get('')
    def read(request: Request):
        return call(service.read, actor(request))

    @router.put('')
    def change(request: Request, body: ProjectPinChange):
        return call(service.set, actor(request), body.project_id, body.pinned, body.revision)

    app.include_router(router)
