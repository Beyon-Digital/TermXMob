"""Same-origin cookie facade and native bearer entry into canonical sessions."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
from dataclasses import asdict
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.identity import AuthenticationError, LoginLimited, SessionCredentials, SessionLocked

if TYPE_CHECKING:
    from termx.app import AppState

ACCESS_COOKIE = "termx_access"
REFRESH_COOKIE = "termx_refresh"
CSRF_COOKIE = "termx_csrf"


def loopback(peer: str) -> bool:
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


def same_origin(origin: str | None, scheme: str, host: str) -> bool:
    if not origin:
        return False
    value = urlsplit(origin)
    return value.scheme == scheme and value.netloc == host and not value.path and not value.query and not value.fragment


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(default="local-password", max_length=128)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=1024)
    assertion: str = Field(default="", max_length=16384)
    device_name: str = Field(default="", max_length=128)
    transport: str = "cookie"


class UnlockInput(LoginInput):
    refresh_token: str | None = Field(default=None,max_length=256)


class HostStopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_id: str = Field(min_length=1,max_length=128)
    acknowledge: bool = False


class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str | None = Field(default=None, max_length=256)


class PairIssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] = Field(min_length=1, max_length=32)
    legacy_token: str | None = Field(default=None, max_length=512)
    associate_legacy: bool = False


class PairExchangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=32, max_length=256)
    host_id: str = Field(min_length=1, max_length=128)
    device_name: str = Field(min_length=1, max_length=128)


class SocketProofInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)


class PairRevokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=32, max_length=256)


def mount_identity(app, state: AppState) -> None:
    router = APIRouter(prefix="/auth")
    service = state.identity
    app_state = state
    from termx.identity_pairing import ManagedPairing
    pairing = ManagedPairing(service, state.tokens)
    state.managed_pairing = pairing
    from termx.identity_locks import SessionLocks, protect_surfaces
    locks = SessionLocks(service)
    state.session_locks = locks

    def transport(request: Request) -> None:
        peer = request.client.host if request.client else ""
        if request.url.scheme != "https" and not loopback(peer):
            raise HTTPException(403, "TLS is required for remote authentication")
        origin = request.headers.get("origin")
        if origin and not same_origin(origin, request.url.scheme, request.headers.get("host", "")):
            raise HTTPException(403, "authentication requires the same origin")

    def credential(request: Request) -> str | None:
        return extract_passcode(authorization=request.headers.get("authorization")) or request.cookies.get(ACCESS_COOKIE)

    def current(request: Request):
        transport(request)
        identity = service.resolve(credential(request),allow_locked=True)
        if identity is None:
            raise HTTPException(401, "sign in required")
        if identity.locked:
            raise HTTPException(423,"Session locked; unlock required")
        return identity

    def csrf(request: Request, token: str | None, *, refresh: bool = False) -> None:
        if not same_origin(request.headers.get("origin"), request.url.scheme, request.headers.get("host", "")):
            raise HTTPException(403, "same-origin request required")
        if not service.valid_csrf(token, request.headers.get("x-termx-csrf"), refresh=refresh):
            raise HTTPException(403, "invalid CSRF token")

    def issue(request: Request, response: Response, credentials: SessionCredentials, mode: str):
        response.headers["Cache-Control"] = "no-store"
        if mode == "bearer":
            return asdict(credentials)
        secure = request.url.scheme == "https"
        response.set_cookie(ACCESS_COOKIE, credentials.access_token, max_age=credentials.expires_in,
                            httponly=True, secure=secure, samesite="strict", path="/")
        response.set_cookie(REFRESH_COOKIE, credentials.refresh_token, max_age=credentials.refresh_expires_in,
                            httponly=True, secure=secure, samesite="strict", path="/auth")
        # The anti-CSRF value is readable so a reloaded/detached window can
        # supply its header. It grants no API authority; credentials stay HttpOnly.
        response.set_cookie(CSRF_COOKIE, credentials.csrf_token, max_age=credentials.refresh_expires_in,
                            secure=secure, samesite="strict", path="/")
        return {"session_id": credentials.session_id, "expires_in": credentials.expires_in,
                "csrf_token": credentials.csrf_token,"refresh_expires_in":credentials.refresh_expires_in}

    @router.get("/methods")
    def methods(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"configured": service.configured, "methods": service.methods(), "host_id": service.host_id}

    @router.post("/setup")
    async def setup(body: LoginInput, request: Request, response: Response):
        transport(request)
        # First-owner claiming is always host-local AND requires the existing
        # administrator bootstrap credential when one is configured.
        if not request.client or not loopback(request.client.host):
            raise HTTPException(403, "initial setup requires local host access")
        bootstrap = request.headers.get("x-termx-passcode", "")
        if state.auth.passcode and not hmac.compare_digest(bootstrap, state.auth.passcode):
            raise HTTPException(401, "host administrator credential required")
        # Even on an open local host, a foreign website cannot claim ownership.
        if not same_origin(request.headers.get("origin"), request.url.scheme, request.headers.get("host", "")):
            raise HTTPException(403, "local same-origin setup required")
        if body.transport not in {"cookie", "bearer"}:
            raise HTTPException(400, "unknown session transport")
        try:
            await asyncio.to_thread(service.setup_owner, body.username, body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except AuthenticationError as exc:
            raise HTTPException(409, str(exc)) from exc
        credentials = await service.login("local-password", {"username": body.username, "password": body.password},
                                          peer=request.client.host, device_name=body.device_name)
        return issue(request, response, credentials, body.transport)

    @router.post("/login")
    async def login(body: LoginInput, request: Request, response: Response):
        transport(request)
        if body.transport not in {"cookie", "bearer"}:
            raise HTTPException(400, "unknown session transport")
        if body.transport == "cookie" and not request.headers.get("origin"):
            raise HTTPException(403, "same-origin request required")
        if hasattr(service.adapters.get(body.method), "begin"):
            raise HTTPException(400, "use the identity provider redirect flow")
        try:
            credentials = await service.login(body.method, {"username": body.username, "password": body.password, "assertion": body.assertion},
                                              peer=request.client.host if request.client else "", device_name=body.device_name)
        except LoginLimited as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
        except AuthenticationError as exc:
            raise HTTPException(401, str(exc)) from exc
        return issue(request, response, credentials, body.transport)

    @router.post("/oidc/{method}/begin")
    async def oidc_begin(method: str, request: Request, response: Response):
        from termx.identity_adapters import OidcAdapter
        transport(request)
        if not same_origin(request.headers.get("origin"), request.url.scheme, request.headers.get("host", "")):
            raise HTTPException(403, "same-origin request required")
        adapter = service.adapters.get(method)
        if not service.configured or not isinstance(adapter, OidcAdapter) or method not in {m['id'] for m in service.methods()}:
            raise HTTPException(404, "identity provider not configured")
        callback = str(request.base_url).rstrip("/") + f"/auth/oidc/{method}/callback"
        if callback != adapter.config.redirect_uri:
            raise HTTPException(403, "identity callback origin mismatch")
        try:
            service._attempt(request.client.host if request.client else "")
            url, binding = await adapter.begin()
        except LoginLimited as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
        except AuthenticationError as exc:
            raise HTTPException(503, str(exc)) from exc
        response.set_cookie(f"termx_oidc_{method}", binding, max_age=300, httponly=True,
                            secure=request.url.scheme == "https", samesite="lax", path=f"/auth/oidc/{method}")
        response.headers["Cache-Control"] = "no-store"
        return {"authorization_url": url}

    @router.get("/oidc/{method}/callback")
    async def oidc_callback(method: str, request: Request, response: Response, state: str = "", code: str = ""):
        from termx.identity_adapters import OidcAdapter
        transport(request)
        lifecycle=getattr(app_state,'identity_lifecycle',None)
        if lifecycle:
            proof=await lifecycle.draft_callback(method,state=state,code=code,
                binding=request.cookies.get(f"termx_oidc_{method}",""),callback_uri=str(request.url).split("?",1)[0])
            if proof:
                redirect=RedirectResponse('/?auth_adapter_test=passed',status_code=303)
                redirect.delete_cookie(f"termx_oidc_{method}",path=f"/auth/oidc/{method}")
                return redirect
        adapter = service.adapters.get(method)
        if not isinstance(adapter, OidcAdapter) or str(request.url).split("?", 1)[0] != adapter.config.redirect_uri:
            raise HTTPException(403, "identity callback origin mismatch")
        transport(request)
        try:
            evidence = {"state":state,"code":code,"binding":request.cookies.get(f"termx_oidc_{method}","")}
            if locks.oidc_pending(method,state):
                credentials = await locks.finish_oidc(method,state,evidence,peer=request.client.host if request.client else "")
            else:
                credentials = await service.login(method,evidence,peer=request.client.host if request.client else "",device_name="Browser SSO")
        except AuthenticationError as exc:
            raise HTTPException(401, str(exc)) from exc
        redirect = RedirectResponse("/", status_code=303)
        redirect.delete_cookie(f"termx_oidc_{method}", path=f"/auth/oidc/{method}")
        issue(request, redirect, credentials, "cookie")
        return redirect

    @router.post("/refresh")
    def refresh(body: RefreshInput, request: Request, response: Response):
        transport(request)
        raw = body.refresh_token or request.cookies.get(REFRESH_COOKIE)
        if not body.refresh_token:
            csrf(request, raw, refresh=True)
        try:
            credentials = service.refresh(raw or "")
        except SessionLocked as exc:
            raise HTTPException(423,str(exc)) from exc
        except AuthenticationError as exc:
            raise HTTPException(401, str(exc)) from exc
        return issue(request, response, credentials, "bearer" if body.refresh_token else "cookie")

    def locked_device(request, raw=None):
        transport(request)
        actor = service.resolve(credential(request),allow_locked=True)
        raw = raw or request.cookies.get(REFRESH_COOKIE)
        refresh_actor = locks.refresh_identity(raw)
        if actor and refresh_actor and actor.session_id != refresh_actor.session_id:
            raise HTTPException(401,"Device proof mismatch")
        actor = refresh_actor or actor
        if not actor: raise HTTPException(401,"Current device session required")
        return actor,raw

    def lock_status(actor):
        return {'locked':locks.is_locked(actor),'principal':{'id':actor.principal.id,'display_name':actor.principal.display_name},
                'session_id':actor.session_id,'host_id':service.host_id,'methods':service.methods()}

    @router.get('/lock-state')
    def lock_state(request:Request,response:Response):
        actor,_ = locked_device(request)
        response.headers['Cache-Control']='no-store'
        return lock_status(actor)

    @router.post('/lock-state')
    def native_lock_state(body:RefreshInput,request:Request,response:Response):
        actor,_ = locked_device(request,body.refresh_token)
        if not body.refresh_token: csrf(request,request.cookies.get(REFRESH_COOKIE),refresh=True)
        response.headers['Cache-Control']='no-store'
        return lock_status(actor)

    @router.post('/lock')
    async def lock_session(request:Request,response:Response):
        actor=current(request)
        if not request.headers.get('authorization'):csrf(request,credential(request))
        # Strip existing observation/control grants before changing the auth
        # bit. These barriers remain private until explicit fresh handoff.
        failure=None
        try:await protect_surfaces(app_state,actor)
        except Exception as exc:failure=exc
        # Even a transport failure must not strand a local-only lock overlay
        # with an otherwise still-authorized device credential.
        await asyncio.to_thread(locks.lock,actor)
        rtc=getattr(app_state,'rtc',None)
        try:
            if rtc:await asyncio.to_thread(rtc.close_device,actor.principal.id,actor.session_id)
        except Exception as exc:failure=failure or exc
        response.headers['Cache-Control']='no-store'
        if failure:raise HTTPException(503,'Session locked; remote observation shutdown could not be confirmed. Private barriers remain retained.')
        return {**lock_status(actor),'jobs_not_cancelled':True,'observation_requires_fresh_consent':True}

    @router.post('/unlock')
    async def unlock_session(body:UnlockInput,request:Request,response:Response):
        actor,raw=locked_device(request,body.refresh_token)
        if not body.refresh_token:csrf(request,raw,refresh=True)
        try:
            credentials=await locks.unlock(raw or '',body.method,{'username':body.username,'password':body.password,'assertion':body.assertion},
                                           peer=request.client.host if request.client else '')
        except LoginLimited as exc:raise HTTPException(429,str(exc),headers={'Retry-After':'60'}) from exc
        except AuthenticationError as exc:raise HTTPException(401,str(exc)) from exc
        return issue(request,response,credentials,'bearer' if body.refresh_token else 'cookie')

    @router.post('/oidc/{method}/unlock-begin')
    async def unlock_oidc_begin(method:str,body:RefreshInput,request:Request,response:Response):
        from termx.identity_adapters import OidcAdapter
        actor,raw=locked_device(request,body.refresh_token)
        if not body.refresh_token:csrf(request,raw,refresh=True)
        adapter=service.adapters.get(method)
        if not locks.is_locked(actor) or not isinstance(adapter,OidcAdapter) or method not in {m['id'] for m in service.methods()}:
            raise HTTPException(403,'Configured unlock identity provider required')
        if str(request.base_url).rstrip('/')+f'/auth/oidc/{method}/callback' != adapter.config.redirect_uri:
            raise HTTPException(403,'Identity callback origin mismatch')
        try:
            service._attempt(request.client.host if request.client else '')
            url,binding=await adapter.begin()
            state=parse_qs(urlsplit(url).query).get('state',[''])[0]
            if not state:raise AuthenticationError('Identity state unavailable')
            locks.bind_oidc(actor,raw,method,state)
        except LoginLimited as exc:raise HTTPException(429,str(exc)) from exc
        except AuthenticationError as exc:raise HTTPException(401,str(exc)) from exc
        response.set_cookie(f'termx_oidc_{method}',binding,max_age=300,httponly=True,secure=request.url.scheme=='https',samesite='lax',path=f'/auth/oidc/{method}')
        response.headers['Cache-Control']='no-store'
        return {'authorization_url':url}

    def host_operator(request):
        actor=current(request)
        app_state.authorization.require_principal(actor.principal,'host-admin')
        return actor

    @router.get('/host/lifecycle')
    def host_lifecycle(request:Request,response:Response):
        host_operator(request)
        response.headers['Cache-Control']='no-store'
        return {'host_id':service.host_id,'can_stop':callable(getattr(app_state,'request_shutdown',None)),
                'effects':['Disconnect clients and stop local tasks, terminals and observation','Pause schedules until this host restarts',
                           'Cancel reachable cloud tasks; dedicated machines are retained']}

    @router.post('/host/stop')
    def stop_host(body:HostStopInput,request:Request,response:Response,background:BackgroundTasks):
        actor=host_operator(request)
        if not request.headers.get('authorization'):csrf(request,credential(request))
        if body.host_id!=service.host_id or not body.acknowledge:raise HTTPException(400,'Explicit current-host shutdown acknowledgement required')
        callback=getattr(app_state,'request_shutdown',None)
        if not callable(callback):raise HTTPException(503,'This host launcher does not support managed shutdown')
        from termx.audit import log_event
        log_event('host_stop_requested',principal_id=actor.principal.id,session_id=actor.session_id,host_id=service.host_id)
        async def shutdown():
            await asyncio.sleep(0.1)
            import inspect
            live=await asyncio.to_thread(service.resolve,credential(request))
            from termx.notify import host_stop_cancelled
            if not live or live.session_id!=actor.session_id:
                host_stop_cancelled()
                return
            try:await asyncio.to_thread(app_state.authorization.require_principal,live.principal,'host-admin')
            except HTTPException:
                host_stop_cancelled()
                return
            app_state.host_stop_requested=True
            from termx.notify import host_stopping
            host_stopping()
            result=callback()
            if inspect.isawaitable(result):await result
        background.add_task(shutdown)
        response.headers['Cache-Control']='no-store'
        return {'accepted':True,'host_id':service.host_id}

    @router.get("/me")
    def me(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        identity = current(request)
        return {"principal": asdict(identity.principal), "session_id": identity.session_id,
                "expires_at": identity.expires_at, "host_id": service.host_id}

    @router.get("/sessions")
    def sessions(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return service.list_sessions(current(request).principal.id)

    @router.post("/pair/issue")
    def pair_issue(body: PairIssueInput, request: Request, response: Response):
        actor = current(request)
        if not request.headers.get('authorization'):
            csrf(request,credential(request))
        response.headers['Cache-Control'] = 'no-store'
        try:
            return pairing.issue(actor,body.scopes,legacy_token=body.legacy_token,associate_legacy=body.associate_legacy)
        except AuthenticationError as exc:
            raise HTTPException(403,str(exc)) from exc

    @router.post("/pair/exchange")
    def pair_exchange(body: PairExchangeInput, request: Request, response: Response):
        transport(request)
        try:
            service._attempt(request.client.host if request.client else '')
            credentials = pairing.exchange(body.ticket,host_id=body.host_id,device_name=body.device_name)
        except LoginLimited as exc:
            raise HTTPException(429,str(exc),headers={'Retry-After':'60'}) from exc
        except AuthenticationError as exc:
            raise HTTPException(401,str(exc)) from exc
        result = issue(request,response,credentials,'bearer')
        actor = service.resolve(credentials.access_token)
        return {**result,'host_id':service.host_id,'principal':asdict(actor.principal)}

    @router.post("/socket-ticket")
    def socket_ticket(body: SocketProofInput, request: Request, response: Response):
        actor = current(request)
        if not request.headers.get('authorization'):
            csrf(request,credential(request))
        response.headers['Cache-Control'] = 'no-store'
        try:
            return pairing.socket(actor,body.path)
        except AuthenticationError as exc:
            raise HTTPException(403,str(exc)) from exc

    @router.post("/pair/revoke")
    def pair_revoke(body: PairRevokeInput, request: Request, response: Response):
        actor=current(request)
        if not request.headers.get('authorization'):
            csrf(request,credential(request))
        response.headers['Cache-Control']='no-store'
        try:
            return {'revoked':pairing.revoke(actor,body.ticket)}
        except AuthenticationError as exc:
            raise HTTPException(401,str(exc)) from exc

    @router.delete("/sessions/{session_id}")
    def revoke(session_id: str, request: Request):
        transport(request)
        if not request.headers.get("authorization"):
            csrf(request, credential(request))
        identity = current(request)
        if not service.revoke(session_id, identity.principal.id):
            raise HTTPException(404, "session not found")
        if getattr(app_state,"rtc",None):app_state.rtc.close_device(identity.principal.id,session_id)
        return {"ok": True}

    @router.post("/logout")
    def logout(request: Request, response: Response, body: RefreshInput | None = None):
        transport(request)
        raw=body.refresh_token if body else None
        identity,proof=locked_device(request,raw)
        if not raw and not request.headers.get("authorization"):
            if locks.is_locked(identity):csrf(request,proof,refresh=True)
            else:csrf(request,credential(request))
        service.revoke(identity.session_id, identity.principal.id)
        if getattr(app_state,"rtc",None):app_state.rtc.close_device(identity.principal.id,identity.session_id)
        response.delete_cookie(ACCESS_COOKIE, path="/")
        response.delete_cookie(REFRESH_COOKIE, path="/auth")
        response.delete_cookie(CSRF_COOKIE, path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True}

    app.include_router(router)
