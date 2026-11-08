"""Same-origin voice facade. Auth mirrors media: a managed session, the scope checked live on every call."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field

from termx.audit import log_event
from termx.auth import extract_passcode
from termx.config import config_dir
from termx.voice.engines import EngineError, EngineRegistry
from termx.voice.engines.provider import ProviderEngine
from termx.voice.engines.woof import WoofEngine
from termx.voice.live import LiveVoice, refuse
from termx.voice.service import MAX_AUDIO_BYTES, MAX_SPEECH_CHARS, VoiceService


class SpeakInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(max_length=MAX_SPEECH_CHARS * 2)
    voice: str | None = Field(default=None, max_length=64)
    engine: str | None = Field(default=None, max_length=16)


class DownloadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    license_sha256: str = Field(min_length=64, max_length=64)


def build_voice_service(state, root=None) -> VoiceService:
    registry = EngineRegistry()
    registry.register(ProviderEngine(state.media, state.agent_store, state.credentials))
    registry.register(WoofEngine(root or config_dir() / 'voice'))
    return VoiceService(state, registry, state.workspace.store)


def voice_owner(state, raw, scope: str = 'agent-view') -> str:
    """The signed-in owner for a credential, with the scope checked now."""
    current = state.identity.resolve(raw)
    if not current:
        raise HTTPException(401, 'Managed sign-in required')
    state.authorization.require(raw, scope)
    return current.principal.id


def mount_voice(app, state):
    state.voice = build_voice_service(state)
    state.voice_live = LiveVoice(state)
    router = APIRouter(prefix='/api/voice')

    def actor(request: Request, scope: str = 'agent-view') -> str:
        raw = extract_passcode(request.headers.get('x-termx-passcode'), request.headers.get('authorization'))
        return voice_owner(state, raw, scope)

    def woof() -> WoofEngine:
        engine = state.voice.registry.get('woof')
        if not isinstance(engine, WoofEngine):
            raise HTTPException(404, 'Woof is not available')
        return engine

    @router.get('/status')
    def status(request: Request):
        return state.voice.status(actor(request))

    @router.post('/transcribe')
    async def transcribe(request: Request):
        owner = actor(request, 'agent-run')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                raise HTTPException(413, 'Recording exceeds 10 MB; make a shorter one')
            chunks.append(chunk)
        mime = request.headers.get('content-type', '').split(';')[0].strip().lower()
        return await state.voice.transcribe(owner, b''.join(chunks), mime)

    @router.post('/speak')
    async def speak(body: SpeakInput, request: Request):
        owner = actor(request, 'agent-run')
        job = state.voice.prepare(owner, body.text, engine=body.engine, voice=body.voice)
        return StreamingResponse(job.stream(), media_type='application/x-ndjson', background=BackgroundTask(job.release),
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @router.get('/engines/woof/license')
    async def license_texts(request: Request):
        actor(request)
        try:
            return await woof().license_texts()
        except EngineError as exc:
            raise HTTPException(502, str(exc)) from exc

    @router.post('/engines/woof/download', status_code=202)
    async def download(body: DownloadInput, request: Request, background: BackgroundTasks):
        owner = actor(request, 'host-admin')
        engine = woof()
        status = engine.status()
        if not status['installable']:
            raise HTTPException(409, status['detail'])
        try:
            texts = await engine.license_texts()
        except EngineError as exc:
            raise HTTPException(502, str(exc)) from exc
        if body.license_sha256 != texts['license']['sha256']:
            raise HTTPException(409, 'Accept the current Woof license text before downloading the model')
        if engine.status()['state'] == 'downloading':
            raise HTTPException(409, 'A Woof download is already running')
        state.voice.save_settings(owner, {'accept_woof_license': body.license_sha256})
        log_event('voice_engine_download', engine='woof', revision=engine.manifest.revision)

        async def run():
            try:
                await engine.install(body.license_sha256)
            except Exception:
                pass  # the engine keeps a safe message that status reports

        background.add_task(run)
        return engine.status()

    @router.delete('/engines/woof')
    def remove(request: Request):
        actor(request, 'host-admin')
        try:
            woof().remove()
        except EngineError as exc:
            raise HTTPException(409, str(exc)) from exc
        log_event('voice_engine_remove', engine='woof')
        return {'ok': True}

    @router.websocket('/live')
    async def live(websocket: WebSocket, conversation_id: str | None = None, k: str | None = None):
        """Same sign-in as the terminal socket. The socket is accepted first so a refusal reaches the browser as a reason and a
        next step; a close before accept shows up there as code 1006 with nothing to act on."""
        await websocket.accept()
        token = extract_passcode(websocket.headers.get('x-termx-passcode'), websocket.headers.get('authorization'), k)
        if not state.auth.check(token):
            return await refuse(websocket, 'unauthorized')
        try:
            if not state.auth.allows(token, 'agent-run'):
                return await refuse(websocket, 'forbidden')
            state.authorization.require(token, 'agent-run')
        except HTTPException:
            return await refuse(websocket, 'forbidden')
        identity = state.identity.resolve(token)
        if identity is None:
            return await refuse(websocket, 'unauthorized')
        try:
            if not conversation_id:
                raise KeyError('conversation_id')
            state.workspace.record(identity.principal, 'conversation', conversation_id, scope='agent-run')
        except (KeyError, ValueError):
            return await refuse(websocket, 'not_found')
        except (HTTPException, PermissionError):
            return await refuse(websocket, 'forbidden')
        await state.voice_live.serve(websocket, identity, token, conversation_id)

    app.include_router(router)
