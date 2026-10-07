"""Shared cookie, transport and live-socket session enforcement.

The guard never turns a session into a scope grant. Existing route resolvers
continue to enforce scopes; this layer invalidates all transports on logout,
expiry or policy reduction, including an idle socket awaiting its next input.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

from termx.auth import extract_passcode
from termx.identity_http import ACCESS_COOKIE, loopback, same_origin


class SessionGuard:
    def __init__(self, app, state) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        path = scope.get("path", "")
        # Static shell and method discovery can render the entry screen.
        protected = path.startswith(("/graphql", "/api/"))
        peer = (scope.get("client") or ("", 0))[0]
        service = self.state.identity
        if protected and not self.state.auth.required and not loopback(peer):
            await self._reject(scope, receive, send, 403, "configure authentication locally before remote access")
            return
        managed = service.configured
        query = parse_qs(scope.get("query_string", b"").decode())
        query_token = query.get("k", [None])[0]
        if protected and managed and query_token and query_token.count(".") == 2:
            await self._reject(scope, receive, send, 401, "session credentials cannot be sent in a URL")
            return
        if protected and scope["scheme"] not in {"https", "wss"} and not loopback(peer):
            await self._reject(scope, receive, send, 403, "TLS is required for remote access")
            return
        cookies = SimpleCookie()
        with suppress(Exception):
            cookies.load(headers.get("cookie", ""))
        cookie = cookies.get(ACCESS_COOKIE)
        raw_cookie = cookie.value if cookie else None
        token = extract_passcode(headers.get("x-termx-passcode"), headers.get("authorization"))
        cookie_auth = token is None and raw_cookie is not None
        if protected and cookie_auth:
            scheme = "https" if scope["scheme"] == "wss" else "http" if scope["scheme"] == "ws" else scope["scheme"]
            origin_ok = same_origin(headers.get("origin"), scheme, headers.get("host", ""))
            if scope["type"] == "websocket" and not origin_ok:
                await self._reject(scope, receive, send, 403, "same-origin socket required")
                return
            if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                if not origin_ok or not service.valid_csrf(raw_cookie, headers.get("x-termx-csrf")):
                    await self._reject(scope, receive, send, 403, "same-origin request and CSRF token required")
                    return
            token = raw_cookie
            scope = dict(scope, headers=[*scope.get("headers", []), (b"authorization", f"Bearer {token}".encode())])
        if scope["type"] != "websocket" or not protected:
            await self.app(scope, receive, send)
            return
        token = token or query_token
        initial_scopes = self.state.auth.scopes(token) if token or not self.state.auth.required else None
        revoked = False
        initial_identity = service.resolve(token)
        initial_version = initial_identity.principal.policy_version if initial_identity else None

        def invalid():
            nonlocal initial_version
            current = service.resolve(token)
            if current:
                if initial_version is None:
                    initial_version = current.principal.policy_version
                elif initial_version != current.principal.policy_version:
                    return True
            if initial_scopes is None:
                return False
            scopes = self.state.auth.scopes(token)
            return scopes is None or not set(initial_scopes) <= set(scopes)

        async def guarded_receive():
            nonlocal token, initial_scopes, revoked
            message = await receive()
            if message.get("type") == "websocket.receive" and path.startswith("/graphql"):
                # Browser WS credentials can arrive with connection_init.
                try:
                    payload = json.loads(message.get("text") or "{}")
                    if payload.get("type") == "connection_init":
                        from termx.graphql.context import secret_from_params
                        token = secret_from_params(payload.get("payload")) or token
                except (ValueError, TypeError, AttributeError):
                    pass
            if token and initial_scopes is None:
                initial_scopes = self.state.auth.scopes(token)
            if message.get("type") == "websocket.receive" and invalid():
                if not revoked:
                    await send({"type": "websocket.close", "code": 4401})
                revoked = True
                return {"type": "websocket.disconnect", "code": 4401}
            return message

        async def guarded_send(message):
            nonlocal initial_scopes, revoked
            if token and initial_scopes is None:
                initial_scopes = self.state.auth.scopes(token)
            if message.get("type") == "websocket.send" and invalid():
                if not revoked:
                    await send({"type": "websocket.close", "code": 4401})
                revoked = True
                return
            if revoked:
                return
            await send(message)

        async def watch():
            nonlocal revoked
            while True:
                await asyncio.sleep(0.25)
                if initial_scopes is None:
                    continue
                if revoked:
                    return
                if invalid():
                    revoked = True
                    await send({"type": "websocket.close", "code": 4401})
                    return

        app_task = asyncio.create_task(self.app(scope, guarded_receive, guarded_send))
        watch_task = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((app_task, watch_task), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in (app_task, watch_task):
                task.cancel()
            await asyncio.gather(app_task, watch_task, return_exceptions=True)

    @staticmethod
    async def _reject(scope, receive, send, code, detail):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
        else:
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, send)
