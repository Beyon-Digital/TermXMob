"""Authenticated character authoring and companion snapshots."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from .packs import import_pack
from .service import CharacterService


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Generate(Input):
    request_id: str = Field(min_length=8, max_length=100)
    provider_id: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=8000)
    name: str = Field(min_length=1, max_length=80)
    phase: str = Field(default='reference', pattern='^(reference|clip)$')
    pack_id: str | None = None
    reference_id: str | None = None
    clip: str = Field(default='idle', pattern='^(idle|thinking|working|waiting|success|error|paused|sleeping|walk|hop|fall|sit|drag|wave)$')
    project_id: str | None = None
    acknowledge_billing: bool = False
    remove_background: bool = False


class Build(Input):
    artifact_id: str
    name: str = Field(min_length=1, max_length=80)
    pack_id: str | None = None
    clip: str = Field(default='idle', pattern='^(idle|thinking|working|waiting|success|error|paused|sleeping|walk|hop|fall|sit|drag|wave)$')
    columns: int = Field(default=4, ge=1, le=16)
    rows: int = Field(default=2, ge=1, le=16)
    duration_ms: int = Field(default=140, ge=20, le=5000)
    remove_background: bool = False


class Clip(Input):
    clip: str
    duration_ms: int = Field(ge=20, le=5000)
    foot_anchor: dict[str, float] | None = None


class Accept(Input):
    revision: int = Field(ge=1)


class FrameInput(Input):
    clip: str
    artifact_id: str


def mount_characters(app, state):
    service = state.characters = CharacterService(state, state.delivery.directory.parent / 'characters')
    state.agent.characters = service
    router = APIRouter(prefix='/api')

    def actor(request, scope='agent-view', project=None):
        raw = extract_passcode(request.headers.get('x-termx-passcode'), request.headers.get('authorization'))
        current = state.identity.resolve(raw)
        if not current:
            raise HTTPException(401, 'Managed sign-in required')
        state.authorization.require(raw, scope, project_id=project)
        return current.principal

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, 'Character not found') from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except PermissionError:
            raise HTTPException(403, 'Character authority unavailable') from None

    @router.get('/characters')
    def library(request: Request):
        return service.library(actor(request).id)

    @router.patch('/companions/preferences')
    async def preferences(request: Request):
        principal = actor(request)
        import json
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 128 * 1024:
                raise HTTPException(413, 'Companion preferences exceed the limit')
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'Use valid JSON for companion preferences') from None
        if not isinstance(body, dict):
            raise HTTPException(400, 'Use an object for companion preferences')
        # Assignment and identity choices must reference this user's enrolled agents.
        identities = [*body.get('pinned', []), *body.get('hidden', []), *body.get('assignments', {})] if all(
            isinstance(body.get(key, [] if key != 'assignments' else {}), list if key != 'assignments' else dict)
            for key in ('pinned', 'hidden', 'assignments')) else []
        for identifier in identities:
            if not isinstance(identifier, str):
                raise HTTPException(400, 'Invalid companion identity')
            kind = 'conversation' if state.workspace.store.get('conversation', identifier) else 'task'
            call(state.workspace.record, principal, kind, identifier)
        return call(service.update_preferences, principal.id, body)

    @router.get('/companions/runtime')
    def runtime(request: Request):
        return call(service.snapshot, actor(request))

    @router.get('/characters/packs/{identifier}/manifest')
    def manifest(identifier: str, request: Request, revision: int | None = None):
        return call(service.pack, actor(request).id, identifier, revision)[0]

    @router.get('/characters/packs/{identifier}/assets/{filename}')
    def asset(identifier: str, filename: str, request: Request, revision: int | None = None):
        _, assets = call(service.pack, actor(request).id, identifier, revision)
        if filename not in assets:
            raise HTTPException(404, 'Character atlas not found')
        return Response(assets[filename], media_type='image/png', headers={'Cache-Control': 'private, max-age=60'})

    @router.get('/characters/packs/{identifier}/export')
    def export(identifier: str, request: Request, revision: int | None = None):
        manifest, assets = call(service.pack, actor(request).id, identifier, revision)
        from .packs import export_pack
        return Response(export_pack(manifest, assets), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="termx-character.termxpet"'})

    @router.post('/characters/import')
    async def import_character(request: Request):
        principal = actor(request, 'agent-run')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 32 * 1024 * 1024:
                raise HTTPException(413, 'Character archive exceeds 32 MiB')
        manifest, assets = call(import_pack, bytes(data))
        return call(service.save_pack, principal.id, manifest, assets)

    @router.post('/characters/packs/{identifier}/accept')
    def accept(identifier: str, body: Accept, request: Request):
        return call(service.accept, actor(request, 'agent-run').id, identifier, body.revision)

    @router.post('/characters/build')
    def build(body: Build, request: Request):
        principal = actor(request, 'agent-run')
        artifact = state.artifacts.get(principal.id, body.artifact_id)
        actor(request, 'agent-run', artifact.get('project'))
        return call(service.build, principal.id, **body.model_dump(),
                    authority=lambda: actor(request, 'agent-run', artifact.get('project')))

    @router.patch('/characters/packs/{identifier}/clip')
    def edit_clip(identifier: str, body: Clip, request: Request):
        return call(service.edit_clip, actor(request, 'agent-run').id, identifier, **body.model_dump())

    @router.post('/characters/packs/{identifier}/frames/{index}')
    def replace_frame(identifier: str, index: int, body: FrameInput, request: Request):
        principal = actor(request, 'agent-run')
        artifact = state.artifacts.get(principal.id, body.artifact_id)
        actor(request, 'agent-run', artifact.get('project'))
        return call(service.replace_frame, principal.id, identifier, index, **body.model_dump(),
                    authority=lambda: actor(request, 'agent-run', artifact.get('project')))

    @router.post('/characters/generate')
    async def generate(body: Generate, request: Request):
        principal = actor(request, 'agent-run', body.project_id)
        def authority():
            actor(request, 'agent-run', body.project_id)
        return call(service.generate, principal.id, body.model_dump(), authority=authority)

    @router.post('/characters/operations/{identifier}/cancel')
    async def cancel(identifier: str, request: Request):
        return call(service.cancel, actor(request, 'agent-run').id, identifier)

    app.include_router(router)
