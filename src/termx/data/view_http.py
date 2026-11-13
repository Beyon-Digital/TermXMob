"""Routes for saved views, their history and runs, drill-down and CSV export.

Behind the same `managed_actor` as every other Data route. Bodies go through `read_body` (size, depth, finite
numbers, UTF-8), unknown fields are 422, and coded errors are `{"detail": {"code", "message", ...}}`.
"""
from __future__ import annotations

import inspect
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import Field, model_validator

from termx.data.query_http import _Input, read_body
from termx.data.query_spec import Scalar, QuerySpec, Text

Kind = Literal['table', 'pivot', 'segment']
Name = Text
Revision = int


class ViewCreate(_Input):
    kind: Kind
    name: Text = Field(min_length=1, max_length=1000)
    description: Text = Field(default='', max_length=5000)
    spec: QuerySpec


class ViewUpdate(_Input):
    revision: int = Field(strict=True, ge=1)
    name: Text | None = Field(default=None, min_length=1, max_length=1000)
    description: Text | None = Field(default=None, max_length=5000)
    spec: QuerySpec | None = None

    @model_validator(mode='after')
    def _something_changes(self):
        if self.name is None and self.description is None and self.spec is None:
            raise ValueError('Give a new name, description or spec.')
        return self


class RunInput(_Input):
    request_id: Text = Field(min_length=1, max_length=200)
    mode: Literal['rows', 'count'] = 'rows'
    offset: int | None = Field(default=None, strict=True, ge=0, le=1_000_000)


class ColumnCoordinate(_Input):
    value: Scalar | None = None
    empty: bool = Field(default=False, strict=True)


class Coordinates(_Input):
    rows: list[Scalar | None] | None = Field(default=None, max_length=2)
    column: ColumnCoordinate | None = None
    groups: list[Scalar | None] | None = Field(default=None, max_length=6)


class DrillInput(_Input):
    coordinates: Coordinates
    request_id: Text | None = Field(default=None, min_length=1, max_length=200)


def build_view_router(service, actor) -> APIRouter:
    router = APIRouter(prefix='/api/data')
    views = service.views
    runtime = service.runtime

    async def principal(request: Request):
        value = actor(request)
        return await value if inspect.isawaitable(value) else value

    @router.get('/workspaces/{wid}/views')
    async def list_views(wid: str, request: Request, kind: Kind | None = Query(default=None),
                         connection_id: str | None = Query(default=None, max_length=200)):
        return views.list(await principal(request), wid, kind, connection_id)

    @router.post('/workspaces/{wid}/views', status_code=201)
    async def create_view(wid: str, request: Request):
        who = await principal(request)
        body = await read_body(request, ViewCreate)
        return await views.create(who, wid, body.kind, body.name, body.description, body.spec)

    @router.get('/workspaces/{wid}/views/{vid}')
    async def get_view(wid: str, vid: str, request: Request):
        return views.get(await principal(request), wid, vid)

    @router.patch('/workspaces/{wid}/views/{vid}')
    async def update_view(wid: str, vid: str, request: Request):
        who = await principal(request)
        body = await read_body(request, ViewUpdate)
        return await views.update(who, wid, vid, body.revision, body.name, body.description, body.spec)

    @router.delete('/workspaces/{wid}/views/{vid}')
    async def delete_view(wid: str, vid: str, request: Request, revision: int = Query(ge=1)):
        return views.archive(await principal(request), wid, vid, revision)

    @router.get('/workspaces/{wid}/views/{vid}/history')
    async def history(wid: str, vid: str, request: Request, limit: int = Query(default=50, ge=1, le=200),
                      before: int | None = Query(default=None, ge=1)):
        return views.history(await principal(request), wid, vid, limit, before)

    @router.get('/workspaces/{wid}/views/{vid}/runs')
    async def runs(wid: str, vid: str, request: Request, limit: int = Query(default=50, ge=1, le=200),
                   before: str | None = Query(default=None, max_length=300)):
        return views.runs(await principal(request), wid, vid, limit, before)

    @router.post('/workspaces/{wid}/views/{vid}/run')
    async def run_view(wid: str, vid: str, request: Request):
        who = await principal(request)
        body = await read_body(request, RunInput)
        return await views.run(who, wid, vid, body.request_id, body.mode, body.offset)

    @router.post('/workspaces/{wid}/views/{vid}/drill')
    async def drill(wid: str, vid: str, request: Request):
        who = await principal(request)
        body = await read_body(request, DrillInput)
        return await views.drill(who, wid, vid, body.coordinates.model_dump(exclude_unset=True), body.request_id)

    @router.get('/workspaces/{wid}/views/{vid}/runs/{jid}/export.csv')
    async def export(wid: str, vid: str, jid: str, request: Request):
        data, truncated, filename = views.export_csv(await principal(request), wid, vid, jid)
        return Response(data, media_type='text/csv; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'X-Data-Truncated': str(truncated).lower(), 'X-Data-Export-Scope': 'view-run',
            'X-Data-CSV-Safety': 'formula-prefixed-text', 'Cache-Control': 'no-store'})

    return router
