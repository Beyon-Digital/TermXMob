"""Strawberry context: exposes AppState plus the resolved auth secret/scopes.

Mirrors the REST model: a secret arrives via ``X-Termx-Passcode`` header,
``Authorization: Bearer``, or the ``k`` query param / connection param. It is
resolved to a scope list lazily; ``require`` reproduces the 401 vs 403 split
of ``_require_scope`` in app.py.

For WebSocket (subscription) connections, auth material lives in the
graphql-ws ``connection_init`` payload; ``TermxGraphQLRouter.on_ws_connect``
fills ``secret`` before any subscription resolver runs.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request, WebSocket
from strawberry.fastapi import BaseContext

from termx.auth import extract_passcode
from termx.graphql.errors import fail

if TYPE_CHECKING:
    from termx.app import AppState

_UNSET = object()


class TermxContext(BaseContext):
    def __init__(
        self,
        state: AppState,
        secret: str | None,
        request: Request | WebSocket | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.secret = secret
        self.request = request
        self._scopes: Any = _UNSET

    @property
    def scopes(self) -> list[str] | None:
        if self._scopes is _UNSET:
            self._scopes = self.state.auth.scopes(self.secret)
        return self._scopes

    @property
    def authenticated(self) -> bool:
        return self.scopes is not None

    def require(self, scope: str) -> None:
        if self.scopes is None:
            fail(401, "invalid passcode")
        if scope not in self.scopes:
            fail(403, f"missing scope: {scope}")

    def client_host(self) -> str:
        client = getattr(self.request, "client", None)
        return (client.host if client else "") or ""

    def require_passcode(self) -> str:
        """Demand the literal passcode credential (used by /api/pair parity)."""
        if not self.secret or not hmac.compare_digest(
            self.state.auth.passcode or "", self.secret.strip()
        ):
            fail(401, "invalid passcode")
        return self.secret


def _headers_from_connection_params(params: dict[str, Any] | None) -> dict[str, str]:
    """Pull auth material out of a graphql-ws ``connection_init`` payload.

    Clients send ``{"headers": {"x-termx-passcode": ...}}``; the flat keys
    ``k`` / ``x-termx-passcode`` / ``authorization`` are also accepted.
    """
    params = params or {}
    nested = params.get("headers") if isinstance(params.get("headers"), dict) else {}
    lowered = {str(k).lower(): str(v) for k, v in params.items() if isinstance(v, str)}
    nested_lowered = {str(k).lower(): str(v) for k, v in nested.items() if isinstance(v, str)}
    return {**lowered, **nested_lowered}


def secret_from_params(params: dict[str, Any] | None) -> str | None:
    headers = _headers_from_connection_params(params)
    return extract_passcode(
        headers.get("x-termx-passcode"),
        headers.get("authorization"),
        headers.get("k"),
    )


def context_from_http(state: AppState, request: Request | WebSocket) -> TermxContext:
    secret = extract_passcode(
        request.headers.get("x-termx-passcode"),
        request.headers.get("authorization"),
        request.query_params.get("k"),
    )
    return TermxContext(state=state, secret=secret, request=request)
