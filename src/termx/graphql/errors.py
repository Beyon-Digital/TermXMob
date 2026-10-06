"""Error mapping for the GraphQL boundary.

Resolver bodies were ported from the REST handlers and still speak in HTTP
terms; helpers here keep the 401/403/404/409 split intact via GraphQL
extensions so clients can distinguish auth failures from domain errors.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException
from graphql import GraphQLError

_CODE_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    501: "NOT_IMPLEMENTED",
    502: "BAD_GATEWAY",
    503: "UNAVAILABLE",
}


def code_for_status(status_code: int) -> str:
    return _CODE_BY_STATUS.get(status_code, "INTERNAL")


def to_graphql_error(exc: HTTPException) -> GraphQLError:
    detail: Any = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail)
        extensions = {"code": code_for_status(exc.status_code), "http_status": exc.status_code, **detail}
    else:
        message = str(detail)
        extensions = {"code": code_for_status(exc.status_code), "http_status": exc.status_code}
    return GraphQLError(message, extensions=extensions)


def fail(status_code: int, detail: Any) -> NoReturn:
    """Raise the GraphQL equivalent of ``HTTPException(status_code, detail)``."""
    raise to_graphql_error(HTTPException(status_code=status_code, detail=detail))


def translate(exc: HTTPException) -> NoReturn:
    raise to_graphql_error(exc) from exc


def resolver(fn: Any) -> Any:
    """Decorator for resolver bodies ported verbatim from REST handlers.

    Translates ``HTTPException`` into a ``GraphQLError`` carrying the
    original status code + detail in ``extensions``.

    Sync bodies are dispatched through the anyio worker threadpool, mirroring
    how FastAPI ran ``def`` endpoints — ported handlers do blocking I/O
    (subprocesses, urllib, sqlite, unix sockets) and must not stall the event
    loop that also carries subscriptions, PTY and desktop sockets.
    """
    import functools
    import inspect

    from starlette.concurrency import run_in_threadpool

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except HTTPException as exc:
                raise to_graphql_error(exc) from exc

        return async_wrapper

    @functools.wraps(fn)
    async def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        call = functools.partial(fn, *args, **kwargs)
        try:
            return await run_in_threadpool(call)
        except HTTPException as exc:
            raise to_graphql_error(exc) from exc

    return sync_wrapper
