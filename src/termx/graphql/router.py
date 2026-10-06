"""FastAPI mount for the termx GraphQL API.

``context_getter`` runs as a FastAPI dependency for both HTTP and WebSocket
requests. For subscriptions, the passcode/token arrives in the graphql-ws
``connection_init`` payload and is validated in ``on_ws_connect`` — the
equivalent of the REST WS handlers' 4401 close-before-accept.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import BackgroundTasks, Request, WebSocket
from strawberry.exceptions import ConnectionRejectionError
from strawberry.fastapi import GraphQLRouter
from strawberry.subscriptions import (
    GRAPHQL_TRANSPORT_WS_PROTOCOL,
    GRAPHQL_WS_PROTOCOL,
)

from termx.graphql.context import (
    TermxContext,
    context_from_http,
    secret_from_params,
)

if TYPE_CHECKING:
    from termx.app import AppState

_SUBSCRIPTION_PROTOCOLS = (GRAPHQL_TRANSPORT_WS_PROTOCOL, GRAPHQL_WS_PROTOCOL)

_NO_REQUEST = cast(Request, None)
_NO_WEBSOCKET = cast(WebSocket, None)


class TermxGraphQLRouter(GraphQLRouter[TermxContext, None]):
    """Authenticate graphql-ws connection-init credentials up front."""

    async def on_ws_connect(self, context: TermxContext) -> None:
        params = getattr(context, "connection_params", None)
        secret = secret_from_params(params if isinstance(params, dict) else None)
        if secret:
            context.secret = secret
        # Match the REST WS 4401: an invalid secret on an auth-required host
        # rejects the connection before any subscription resolver runs. Scope
        # enforcement still happens per resolver.
        if context.scopes is None:
            raise ConnectionRejectionError("invalid passcode")


def mount(app: Any, state: AppState) -> TermxGraphQLRouter:
    """Attach the GraphQL router at /graphql (HTTP queries + WS subscriptions)."""
    from termx.graphql.schema import schema

    async def get_context(
        background_tasks: BackgroundTasks,
        request: Request = _NO_REQUEST,
        ws: WebSocket = _NO_WEBSOCKET,
    ) -> TermxContext:
        conn = request or ws
        if conn is None:
            raise RuntimeError("GraphQL context requires an HTTP or WebSocket connection")
        return context_from_http(state, conn)

    router = TermxGraphQLRouter(
        schema,
        context_getter=get_context,
        subscription_protocols=_SUBSCRIPTION_PROTOCOLS,
    )
    app.include_router(router, prefix="/graphql")
    return router
