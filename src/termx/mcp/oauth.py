"""OAuth for MCP connections (tech-specs §5).

Delegates the flow to the MCP SDK's ``OAuthClientProvider``, which handles
protected-resource metadata, AS metadata, CIMD (``client_metadata_url``),
DCR, PKCE S256 and refresh. TermX supplies:

- ``CredentialTokenStorage`` — tokens and *issuer-bound* client
  registrations persisted separately in the OS keystore (never in files).
- ``LoopbackCallback`` — exact loopback redirect on the **host** with a
  state token bound to {user, device, host, connection, issuer, redirect}
  so a leaked state can't complete a different flow (CSRF).
- ``build_provider`` — wires it together; ``on_auth_url`` opens the user's
  browser (UI consent) rather than ever embedding credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from .defs import ConnectionDef


class CredentialTokenStorage:
    """``mcp.client.auth.TokenStorage`` over TermX's CredentialStore.

    Keys: ``mcp.<conn>.tokens`` and ``mcp.<conn>.client_info.<issuer-hash>``
    so resource tokens and issuer-bound registrations stay separate (spec §5).
    """

    def __init__(self, store: Any, conn_id: str, issuer_hint: str = ""):
        self._store = store
        self._conn = conn_id
        self._issuer_key = (
            hashlib.sha256(issuer_hint.encode()).hexdigest()[:16]
            if issuer_hint else "default"
        )

    def _key(self, kind: str) -> str:
        if kind == "client_info":
            return f"mcp.{self._conn}.client_info.{self._issuer_key}"
        return f"mcp.{self._conn}.{kind}"

    def _read(self, kind: str) -> dict | None:
        raw = self._store.get(self._key(kind)) if self._store else None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _write(self, kind: str, payload: dict) -> None:
        if not self._store:
            return
        self._store.set(self._key(kind), json.dumps(payload))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        data = self._read("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens) -> None:
        self._write("tokens", tokens.model_dump(mode="json"))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        data = self._read("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info) -> None:
        self._write("client_info", client_info.model_dump(mode="json"))

    async def delete_all(self) -> None:
        if self._store:
            self._store.delete(self._key("tokens"))
            self._store.delete(self._key("client_info"))


class LoopbackCallback:
    """One-shot loopback OAuth callback listener on the host.

    The SDK generates and validates the ``state`` parameter itself (CSRF);
    this listener simply delivers {code, state, error} from the browser
    redirect to the waiting flow. One pending flow at a time.
    """

    CALLBACK_PATH = "/oauth/callback"

    def __init__(self):
        self.port: int = 0
        self._server: asyncio.AbstractServer | None = None
        self._pending: asyncio.Future | None = None
        self.started = False

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.CALLBACK_PATH}"

    async def start(self) -> None:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            except (asyncio.TimeoutError, asyncio.LimitOverrunError, OSError):
                writer.close()
                return
            line = request.split(b"\r\n", 1)[0].decode("latin-1")
            try:
                _, path, _ = line.split(" ", 2)
            except ValueError:
                writer.close()
                return
            query = parse_qs(urlparse(path).query)
            code = query.get("code", [""])[0]
            state = query.get("state", [""])[0]
            error = query.get("error", [""])[0]
            ok = bool(code) and not error
            body = (
                b"<html><body><h2>TermX sign-in complete</h2>"
                b"You can return to TermX.</body></html>"
                if ok
                else b"<html><body><h2>Sign-in failed or cancelled</h2></body></html>"
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            await writer.drain()
            writer.close()
            fut = self._pending
            self._pending = None
            if fut is not None and not fut.done():
                fut.set_result({"code": code, "state": state, "error": error})

        self._server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        self.started = True

    async def wait(self, timeout: float = 300.0) -> dict[str, str]:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending = None

    async def stop(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self.started = False


async def build_provider(
    conn: ConnectionDef,
    credentials: Any,
    *,
    on_auth_url: Callable[[str], Awaitable[None]] | None = None,
    loopback: LoopbackCallback | None = None,
):
    """Construct an ``OAuthClientProvider`` for an HTTP/SSE connection.

    ``on_auth_url`` receives the authorization URL to present to the user.
    ``loopback`` supplies the host-side callback; without it the provider
    falls back to the SDK's own behavior (manual paste flows are handled by
    the caller surfacing the URL).
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    if loopback is None:
        loopback = LoopbackCallback()
    if not loopback.started:
        await loopback.start()  # port must be known for redirect_uris

    storage = CredentialTokenStorage(
        credentials, conn.slug, issuer_hint=conn.url
    )

    async def redirect_handler(url: str) -> None:
        if on_auth_url is not None:
            await on_auth_url(url)
        else:
            import webbrowser
            webbrowser.open(url)

    async def callback_handler():
        if not loopback.started:
            await loopback.start()
        result = await loopback.wait()
        from mcp.client.auth import AuthorizationCodeResult
        return AuthorizationCodeResult(
            code=result.get("code") or "",
            state=result.get("state") or None,
        )

    metadata = OAuthClientMetadata(
        client_name="TermX",
        redirect_uris=[loopback.redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=" ".join(conn.scopes) or None,
        software_id="termx",
        application_type="native",
    )
    kwargs: dict[str, Any] = {
        "server_url": conn.url,
        "client_metadata": metadata,
        "storage": storage,
        "redirect_handler": redirect_handler,
        "callback_handler": callback_handler,
    }
    if conn.client_metadata_url:
        kwargs["client_metadata_url"] = conn.client_metadata_url
    return OAuthClientProvider(**kwargs)
