"""Same-origin cookie facade and native bearer entry into canonical sessions."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
from dataclasses import asdict
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.identity import ABSOLUTE_TTL, AuthenticationError, LoginLimited, SessionCredentials

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


class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str | None = Field(default=None, max_length=256)


def mount_identity(app, state: AppState) -> None:
    router = APIRouter(prefix="/auth")
    service = state.identity
    app_state = state

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
        identity = service.resolve(credential(request))
        if identity is None:
            raise HTTPException(401, "sign in required")
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
        response.set_cookie(REFRESH_COOKIE, credentials.refresh_token, max_age=ABSOLUTE_TTL,
                            httponly=True, secure=secure, samesite="strict", path="/auth")
        # The anti-CSRF value is readable so a reloaded/detached window can
        # supply its header. It grants no API authority; credentials stay HttpOnly.
        response.set_cookie(CSRF_COOKIE, credentials.csrf_token, max_age=ABSOLUTE_TTL,
                            secure=secure, samesite="strict", path="/")
        return {"session_id": credentials.session_id, "expires_in": credentials.expires_in,
                "csrf_token": credentials.csrf_token}

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
            credentials = await service.login(method, {"state": state, "code": code,
                                                       "binding": request.cookies.get(f"termx_oidc_{method}", "")},
                                              peer=request.client.host if request.client else "", device_name="Browser SSO")
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
        except AuthenticationError as exc:
            raise HTTPException(401, str(exc)) from exc
        return issue(request, response, credentials, "bearer" if body.refresh_token else "cookie")

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

    @router.delete("/sessions/{session_id}")
    def revoke(session_id: str, request: Request):
        transport(request)
        if not request.headers.get("authorization"):
            csrf(request, credential(request))
        identity = current(request)
        if not service.revoke(session_id, identity.principal.id):
            raise HTTPException(404, "session not found")
        return {"ok": True}

    @router.post("/logout")
    def logout(request: Request, response: Response):
        transport(request)
        if not request.headers.get("authorization"):
            csrf(request, credential(request))
        identity = current(request)
        service.revoke(identity.session_id, identity.principal.id)
        response.delete_cookie(ACCESS_COOKIE, path="/")
        response.delete_cookie(REFRESH_COOKIE, path="/auth")
        response.delete_cookie(CSRF_COOKIE, path="/")
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True}

    app.include_router(router)
