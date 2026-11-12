"""Routes for the visual query builder. Auth, CSRF, TLS and lock handling come from `actor`."""
from __future__ import annotations

import inspect

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.data.query_spec import QuerySpec


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CompileInput(_Input):
    spec: QuerySpec


class RunSpecInput(_Input):
    request_id: str = Field(min_length=1, max_length=200)
    spec: QuerySpec


def build_query_router(service, actor) -> APIRouter:
    router = APIRouter(prefix='/api/data')
    queries = service.query

    async def principal(request: Request):
        value = actor(request)
        return await value if inspect.isawaitable(value) else value

    @router.post('/workspaces/{wid}/query-specs/compile')
    async def compile_spec(wid: str, body: CompileInput, request: Request):
        return await queries.compile(await principal(request), wid, body.spec)

    @router.post('/workspaces/{wid}/query-specs/run')
    async def run_spec(wid: str, body: RunSpecInput, request: Request):
        return await queries.run(await principal(request), wid, body.spec, body.request_id)

    @router.get('/workspaces/{wid}/connections/{cid}/relations')
    async def relations(wid: str, cid: str, request: Request, table: str | None = Query(default=None, max_length=128)):
        return await queries.relations(await principal(request), wid, cid, table)

    @router.get('/workspaces/{wid}/connections/{cid}/capabilities')
    async def capabilities(wid: str, cid: str, request: Request):
        return queries.capabilities(await principal(request), wid, cid)

    return router
