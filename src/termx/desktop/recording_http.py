"""The window capture surface requires fresh managed-session consent."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.desktop.capture import CaptureError
from termx.desktop.recording import WindowRecordingService


class Route(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try: return await handler(request)
            except CaptureError as exc: return JSONResponse({'error': str(exc)}, status_code=503)
            except PermissionError as exc: return JSONResponse({'error': str(exc)}, status_code=403)
            except ValueError as exc: return JSONResponse({'error': str(exc)}, status_code=400)
            except KeyError: return JSONResponse({'error': 'Recording not found'}, status_code=404)
        return safe


class Body(BaseModel): model_config = ConfigDict(extra='forbid')
class Start(Body): window_id: str; expires_in: int = 600
class Configure(Body): private: bool | None = None; recording: bool | None = None; redactions: list[dict] | None = None
class Input(Body): event: dict; parameter: str | None = None
class Draft(Body): name: str
class Edit(Body): content: str = Field(max_length=100_000)
class Test(Body): target_capture_id: str; parameters: dict[str, str] = Field(default_factory=dict); safe_target: bool = False
class Publish(Body): version: str


def mount_window_recording(app, state):
    state.window_recording = WindowRecordingService(state.browser.records, session_valid=state.browser.session_valid)
    service = state.window_recording
    router = APIRouter(prefix='/api/desktop/recording', route_class=Route)

    def actor(request, scope='desktop-view', identifier=None):
        token = extract_passcode(request.headers.get('x-termx-passcode'), request.headers.get('authorization'))
        session = state.identity.resolve(token)
        if not session: raise HTTPException(401, 'Window recording requires a managed sign-in session')
        state.authorization.require(token, scope, resource_kind='window-capture' if identifier else None, resource_id=identifier)
        return token, session

    @router.get('/windows')
    async def windows(request: Request):
        actor(request)
        import asyncio
        return {**service.port.capability(), 'windows': await asyncio.to_thread(service.port.list) if service.port.capability()['supported'] else []}

    @router.get('/status')
    async def capture_status(request: Request):
        import asyncio
        token, session = actor(request)
        policy = state.authorization.revision(token)
        def snapshot():
            rows = []
            for candidate in service.records.list('window-capture'):
                if candidate['principal_id'] != session.principal.id or (candidate['stopped'] and not candidate['private']): continue
                if not state.authorization.can(token, 'desktop-view', resource_kind='window-capture', resource_id=candidate['id']): continue
                try: current = service.get(candidate['id'], session.principal.id)
                except (KeyError, PermissionError):
                    current = service.records.get('window-capture', candidate['id'])
                    if not current or not current['private']: continue
                rows.append({'id': current['id'], 'kind': 'window', 'state': 'private' if current['private'] else 'recording' if current['recording'] else 'preview',
                    'expired': bool(current['stopped']),
                    'stop_allowed': state.authorization.can(token, 'desktop-control', resource_kind='window-capture', resource_id=current['id'])})
            for tab in state.browser.records.list('tab'):
                if tab['principal_id'] != session.principal.id or tab['state'] in {'closed', 'crashed'}: continue
                viewed = tab['id'] in state.browser.viewing_tabs(session.principal.id)
                agent = tab['state'] == 'agent' and state.browser._grant_valid(tab, state.browser.records.get('grant',tab.get('grant_id')))
                developer = state.browser._diagnostic_consent_valid(tab)
                private=tab['state']=='private'
                if not (viewed or agent or tab['recording'] or developer or private): continue
                kwargs = {'project_id': tab['project_id'] or None, 'resource_kind': 'browser-tab', 'resource_id': tab['id']}
                if state.authorization.can(token, 'desktop-view', **kwargs):
                    rows.append({'id': tab['id'], 'kind': 'tab', 'state': 'private' if private else 'agent' if agent else 'recording' if tab['recording'] else 'developer' if developer else 'watching', 'stop_allowed': state.authorization.can(token, 'desktop-control', **kwargs) if agent or tab['recording'] or developer else viewed if private else True})
            for row in state.desktop.viewers(session.principal.id):
                rows.append({**row,'kind':'computer','stop_allowed':True})
            return rows
        rows = await asyncio.to_thread(snapshot)
        actor(request)
        if state.authorization.revision(token) != policy: raise HTTPException(403, 'Capture authority changed')
        # This metadata query performs no capture, grants no consent and exposes
        # no private window names, tab URLs, recorded values or screenshots.
        return {'captures': rows}

    @router.delete('/viewers/{identifier}')
    async def stop_viewer(identifier:str,request:Request):
        _,session=actor(request)
        return await state.desktop.stop_viewer(identifier,session.principal.id)

    @router.post('/captures')
    async def start(request: Request, body: Start):
        token, session = actor(request, 'desktop-control')
        row = service.start(session.principal.id, session.session_id, state.authorization.revision(token), body.window_id, body.expires_in)
        state.authorization.claim(token, 'window-capture', row['id'])
        return row

    @router.patch('/captures/{identifier}')
    async def configure(identifier: str, request: Request, body: Configure):
        _, session = actor(request, 'desktop-control', identifier)
        return service.configure(identifier, session.principal.id, **body.model_dump(exclude_none=True))

    @router.delete('/captures/{identifier}')
    async def stop(identifier: str, request: Request):
        _, session = actor(request, 'desktop-control', identifier)
        return service.stop(identifier, session.principal.id)

    @router.get('/captures/{identifier}/frame')
    async def frame(identifier: str, request: Request):
        _, session = actor(request, identifier=identifier)
        return Response(await service.frame(identifier, session.principal.id, human=True), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @router.post('/captures/{identifier}/input')
    async def input(identifier: str, request: Request, body: Input):
        _, session = actor(request, 'desktop-control', identifier)
        return await service.input(identifier, session.principal.id, body.event, parameter=body.parameter)

    @router.get('/captures/{identifier}/preview')
    async def preview(identifier: str, request: Request):
        _, session = actor(request, identifier=identifier)
        import base64
        data = await service.frame(identifier, session.principal.id, human=True)
        return JSONResponse({'image': 'data:image/jpeg;base64,' + base64.b64encode(data).decode()}, headers={'Cache-Control': 'no-store'})

    @router.post('/captures/{identifier}/draft')
    async def draft(identifier: str, request: Request, body: Draft):
        _, session = actor(request, 'desktop-control', identifier)
        return service.draft(identifier, session.principal.id, body.name)

    @router.patch('/drafts/{identifier}')
    async def edit(identifier: str, request: Request, body: Edit):
        _, session = actor(request, 'desktop-control')
        return service.edit(identifier, session.principal.id, body.content)

    @router.post('/drafts/{identifier}/test')
    async def test(identifier: str, request: Request, body: Test):
        _, session = actor(request, 'desktop-control', body.target_capture_id)
        return await service.test(identifier, session.principal.id, **body.model_dump())

    @router.post('/drafts/{identifier}/publish')
    async def publish(identifier: str, request: Request, body: Publish):
        _, session = actor(request, 'host-admin')
        return service.publish(identifier, session.principal.id, session.principal, state.extensions, body.version)

    app.include_router(router)
