"""Routes for the visual query builder. Auth, CSRF, TLS and lock handling come from `actor`."""
from __future__ import annotations

import inspect
import json
import math
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from termx.data.query_spec import Text, QuerySpec

MAX_BODY_BYTES = 1_000_000
MAX_BODY_DEPTH = 32
M = TypeVar('M', bound=BaseModel)


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CompileInput(_Input):
    spec: QuerySpec


class RunSpecInput(_Input):
    request_id: Text = Field(min_length=1, max_length=200)
    spec: QuerySpec


def _reject_constant(name: str):
    raise ValueError('Numbers must be finite.')


def _reject_infinite(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError('Numbers must be finite.')
    return number


def _check_tree(value) -> None:
    """Iterative, so a hostile body cannot cost more than its own size: bound the depth and refuse
    strings that cannot be encoded as UTF-8 (lone surrogates)."""
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_BODY_DEPTH:
            raise ValueError('The request is nested too deeply.')
        if isinstance(node, str):
            node.encode('utf-8')
        elif isinstance(node, dict):
            for key, item in node.items():
                key.encode('utf-8')
                stack.append((item, depth + 1))
        elif isinstance(node, list):
            stack.extend((item, depth + 1) for item in node)


async def read_body(request: Request, model: type[M]) -> M:
    """Parse and validate a JSON body so every hostile input is a 4xx.

    FastAPI echoes the offending `input` in its 422 body, and a NaN or Infinity there is not JSON
    serialisable (a 500). Parsing here rejects non-finite numbers up front and returns only the
    location and message of each problem.
    """
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, 'The request is too large.')
    try:
        data = json.loads(raw, parse_constant=_reject_constant, parse_float=_reject_infinite)
        _check_tree(data)
    except (ValueError, RecursionError, UnicodeError):
        raise HTTPException(422, 'The request body must be valid JSON with finite numbers, text that can be '
                                 f'encoded as UTF-8 and at most {MAX_BODY_DEPTH} levels of nesting.') from None
    try:
        return model.model_validate(data)
    except ValidationError as error:
        problems = [{'loc': list(item['loc']), 'msg': item['msg'], 'type': item['type']} for item in error.errors()]
        raise HTTPException(422, problems) from None


def build_query_router(service, actor) -> APIRouter:
    router = APIRouter(prefix='/api/data')
    queries = service.query

    async def principal(request: Request):
        value = actor(request)
        return await value if inspect.isawaitable(value) else value

    @router.post('/workspaces/{wid}/query-specs/compile')
    async def compile_spec(wid: str, request: Request):
        actor = await principal(request)
        body = await read_body(request, CompileInput)
        return await queries.compile(actor, wid, body.spec)

    @router.post('/workspaces/{wid}/query-specs/run')
    async def run_spec(wid: str, request: Request):
        actor = await principal(request)
        body = await read_body(request, RunSpecInput)
        return await queries.run(actor, wid, body.spec, body.request_id)

    @router.get('/workspaces/{wid}/connections/{cid}/relations')
    async def relations(wid: str, cid: str, request: Request, table: str | None = Query(default=None, max_length=128)):
        return await queries.relations(await principal(request), wid, cid, table)

    @router.get('/workspaces/{wid}/connections/{cid}/capabilities')
    async def capabilities(wid: str, cid: str, request: Request):
        return queries.capabilities(await principal(request), wid, cid)

    return router
