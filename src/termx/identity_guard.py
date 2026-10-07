"""Shared cookie, transport and live-socket session enforcement.

The guard never turns a session into a scope grant. Existing route resolvers
continue to enforce scopes; this layer invalidates all transports on logout,
expiry or policy reduction, including an idle socket awaiting its next input.
"""
from __future__ import annotations

import asyncio
import anyio
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
        managed = await asyncio.to_thread(lambda: service.configured)
        required = self.state.auth.passcode is not None or managed
        if protected and not required and not loopback(peer):
            await self._reject(scope, receive, send, 403, "configure authentication locally before remote access")
            return
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
        socket_proof = query.get('st',[None])[0]
        if protected and socket_proof:
            from termx.identity import AuthenticationError
            origin_scheme = 'https' if scope['scheme']=='wss' else 'http'
            if (scope['type']!='websocket' or token or query_token or
                (headers.get('origin') and not same_origin(headers['origin'],origin_scheme,headers.get('host','')))):
                await self._reject(scope,receive,send,401,'invalid socket admission transport')
                return
            try:
                token = await asyncio.to_thread(self.state.managed_pairing.admit_socket,socket_proof,
                    host_id=query.get('host_id',[''])[0],path=path)
            except AuthenticationError:
                await self._reject(scope,receive,send,401,'socket proof expired or invalid')
                return
            scope = dict(scope,headers=[*scope.get('headers',[]),(b'authorization',f'Bearer {token}'.encode())])
        cookie_auth = token is None and raw_cookie is not None
        if protected and cookie_auth:
            scheme = "https" if scope["scheme"] == "wss" else "http" if scope["scheme"] == "ws" else scope["scheme"]
            origin_ok = same_origin(headers.get("origin"), scheme, headers.get("host", ""))
            if scope["type"] == "websocket" and not origin_ok:
                await self._reject(scope, receive, send, 403, "same-origin socket required")
                return
            if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                if not origin_ok or not await asyncio.to_thread(service.valid_csrf, raw_cookie, headers.get("x-termx-csrf")):
                    await self._reject(scope, receive, send, 403, "same-origin request and CSRF token required")
                    return
            token = raw_cookie
            scope = dict(scope, headers=[*scope.get("headers", []), (b"authorization", f"Bearer {token}".encode())])
        if scope["type"] != "websocket" or not protected:
            await self.app(scope, receive, send)
            return
        token = token or query_token
        def inspect_current():
            current = service.resolve(token)
            scopes = list(current.principal.scopes) if current else self.state.auth.scopes(token)
            return current, scopes

        initial_identity, resolved_scopes = await asyncio.to_thread(inspect_current)
        initial_scopes = resolved_scopes if token or not required else None
        revoked = False
        disconnected = False
        initial_version = initial_identity.principal.policy_version if initial_identity else None
        validation_lock = asyncio.Lock()

        async def invalid():
            nonlocal initial_version, initial_scopes
            # Each frame still resolves current durable session/principal state.
            # Serialize snapshots without holding the event loop during SQLite IO.
            async with validation_lock:
                current, scopes = await asyncio.to_thread(inspect_current)
                if token and initial_scopes is None:
                    initial_scopes = scopes
                if current:
                    if initial_version is None:
                        initial_version = current.principal.policy_version
                    elif initial_version != current.principal.policy_version:
                        return True
                if initial_scopes is None:
                    return False
                return scopes is None or not set(initial_scopes) <= set(scopes)

        async def guarded_receive():
            nonlocal token, initial_scopes, revoked, disconnected
            message = await receive()
            if message.get('type') == 'websocket.disconnect':
                disconnected = True
            if message.get("type") == "websocket.receive" and path.startswith("/graphql"):
                # Browser WS credentials can arrive with connection_init.
                try:
                    payload = json.loads(message.get("text") or "{}")
                    if payload.get("type") == "connection_init":
                        from termx.graphql.context import secret_from_params
                        token = secret_from_params(payload.get("payload")) or token
                except (ValueError, TypeError, AttributeError):
                    pass
            if message.get("type") == "websocket.receive" and await invalid():
                if not revoked:
                    await send({"type": "websocket.close", "code": 4401})
                revoked = True
                return {"type": "websocket.disconnect", "code": 4401}
            return message

        async def guarded_send(message):
            nonlocal initial_scopes, revoked
            if message.get("type") == "websocket.send" and await invalid():
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
                if await invalid():
                    revoked = True
                    await send({"type": "websocket.close", "code": 4401})
                    return

        app_task = asyncio.create_task(self.app(scope, guarded_receive, guarded_send))
        watch_task = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((app_task, watch_task), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    await task
                except asyncio.CancelledError:
                    if not (disconnected or revoked):
                        raise
        finally:
            for task in (app_task, watch_task):
                task.cancel()
            # ASGI transports use cancellation scopes during disconnect. A
            # second cancellation during gather would replace the originating
            # scope's cancellation and surface as an unrelated cancelled future.
            # Shield teardown only; application errors still propagate above.
            with anyio.CancelScope(shield=True):
                await asyncio.gather(app_task, watch_task, return_exceptions=True)

    @staticmethod
    async def _reject(scope, receive, send, code, detail):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
        else:
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, send)
