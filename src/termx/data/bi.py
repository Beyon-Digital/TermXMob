"""Versioned, source-authorized reports and dashboards over reusable saved queries.

The first semantic layer is deliberately small: saved SQL defines the dataset and
one typed visual defines grouping, aggregation and filters. All computation is
pushed into that query's source; a limited result is never treated as a total.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


VisualKind = Literal['table', 'bar', 'line', 'area', 'scatter', 'pie', 'metric', 'pivot']
Aggregate = Literal['none', 'sum', 'count', 'avg', 'min', 'max']
FilterOperator = Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'in', 'not_in', 'is_null', 'not_null']


def _field_name(value: str | None) -> str | None:
    if value is not None and (not value.strip() or '\x00' in value):
        raise ValueError('A field must be a nonempty column name')
    return value


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool, int)) or (isinstance(value, float) and math.isfinite(value))


class VisualDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: VisualKind = 'table'
    x: str | None = Field(default=None, max_length=512)
    y: str | None = Field(default=None, max_length=512)
    aggregate: Aggregate | None = None

    _names = field_validator('x', 'y')(_field_name)

    @model_validator(mode='after')
    def bindings(self):
        if self.aggregate is None:
            self.aggregate = 'none' if self.kind in {'table', 'scatter'} else ('sum' if self.y else 'count')
        if self.kind == 'scatter' and (not self.x or not self.y or self.aggregate != 'none'):
            raise ValueError('Scatter requires x and y fields with no aggregation')
        if self.kind in {'bar', 'line', 'area', 'pie', 'pivot'} and not self.x:
            raise ValueError('This visual requires an x field')
        if self.kind in {'bar', 'line', 'area', 'pie'} and self.aggregate == 'none' and not self.y:
            raise ValueError('A nonaggregated chart requires a y field')
        if self.kind == 'metric' and self.aggregate == 'none':
            raise ValueError('A metric requires an aggregate over the complete saved query')
        if self.aggregate not in {'none', 'count'} and not self.y:
            raise ValueError('This aggregate requires a y field')
        if self.kind == 'pivot' and self.aggregate == 'none':
            raise ValueError('A pivot requires an aggregate')
        if self.aggregate != 'none' and self.kind != 'metric' and self.x and self.x == self.y:
            raise ValueError('Grouping and value fields must differ')
        return self


class ReportFilter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str = Field(min_length=1, max_length=512)
    operator: FilterOperator
    value: Any = None

    _name = field_validator('field')(_field_name)

    @model_validator(mode='after')
    def values(self):
        if self.operator in {'is_null', 'not_null'}:
            if self.value is not None:
                raise ValueError('Null filters do not take a value')
        elif self.operator in {'in', 'not_in'}:
            if not isinstance(self.value, list) or not all(_scalar(value) for value in self.value):
                raise ValueError('Membership filters require a list of scalar values')
        elif not _scalar(self.value):
            raise ValueError('A filter value must be a scalar')
        if self.operator == 'contains' and not isinstance(self.value, str):
            raise ValueError('Contains requires text')
        if self.operator in {'gt', 'gte', 'lt', 'lte'} and self.value is None:
            raise ValueError('An ordered comparison requires a value')
        return self


class ReportDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    query_id: str = Field(min_length=1, max_length=200)
    visual: VisualDefinition = Field(default_factory=VisualDefinition)
    filters: list[ReportFilter] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=8000)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('A report name is required')
        return value.strip()


class DashboardDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    report_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('A dashboard name is required')
        if len(set(self.report_ids)) != len(self.report_ids):
            raise ValueError('A report can appear only once in a dashboard')
        if any(not value or len(value) > 200 for value in self.report_ids):
            raise ValueError('Invalid report identifier')
        return self


class RunInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=200)


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


class ReportPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    query_id: str | None = Field(default=None, min_length=1, max_length=200)
    visual: VisualDefinition | None = None
    filters: list[ReportFilter] | None = None
    description: str | None = Field(default=None, max_length=8000)


class DashboardPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    report_ids: list[str] | None = None


def _validated(model, definition):
    try:
        return model.model_validate(definition).model_dump()
    except ValidationError as exc:
        # Pydantic's error context can contain non-JSON ValueError objects.
        raise HTTPException(422, exc.errors(include_context=False, include_input=False)) from exc


def _definition(record):
    return {key: deepcopy(record[key]) for key in ('name', 'query_id', 'visual', 'filters', 'description') if key in record}


class BIService:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def _record(self, kind, wid, identifier):
        record = self.store.get(kind, identifier)
        if not record or record.get('workspace_id') != wid or record.get('archived'):
            raise HTTPException(404, f'{kind.replace("_", " ").title()} not found')
        return record

    def _query(self, actor, wid, query_id, *, report=False):
        query = self._record('query', wid, query_id)
        self.service.require_connection(actor, wid, query['connection_id'], report=report)
        return query

    def _published(self, wid, report):
        identifier = report.get('published_version_id')
        version = self._record('report_version', wid, identifier) if identifier else None
        if not version or version.get('report_id') != report['id']:
            raise HTTPException(404, 'Published report not found')
        return version

    def validate_definition(self, actor, wid, definition):
        """Validate a draft or Copilot proposal without executing or changing a source."""
        self.service.require(actor, wid, write=True)
        normalized = _validated(ReportDefinition, definition)
        self._query(actor, wid, normalized['query_id'])
        return normalized

    def _visible(self, actor, wid, report):
        workspace = self.service.require(actor, wid)
        if workspace['role'] == 'viewer':
            version = self._published(wid, report)
            snapshot = version['query_snapshot']
            if version['definition']['query_id'] != snapshot['id'] or snapshot.get('workspace_id') != wid:
                raise HTTPException(404, 'Published report dependency not found')
            self.service.require_connection(actor, wid, snapshot['connection_id'], report=True)
            result = {key: deepcopy(report[key]) for key in ('id', 'workspace_id', 'revision', 'created_at') if key in report}
            result.update(deepcopy(version['definition']))
            result.update(published=True, published_version_id=version['id'],
                          published_revision=version['report_revision'], published_at=version['created_at'],
                          updated_at=version['created_at'], version=version['version'])
            return result
        self._query(actor, wid, report['query_id'])
        result = self.service.public(report)
        result['published'] = bool(report.get('published_version_id'))
        if report.get('published_version_id'):
            version = self._published(wid, report)
            result['published_revision'] = version['report_revision']
            result['version'] = version['version']
        return result

    def reports(self, actor, wid):
        self.service.require(actor, wid)
        result = []
        for report in self.store.list('report', wid):
            try:
                result.append(self._visible(actor, wid, report))
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
        return result

    def report(self, actor, wid, rid):
        self.service.require(actor, wid)
        return self._visible(actor, wid, self._record('report', wid, rid))

    def versions(self, actor, wid, rid):
        workspace = self.service.require(actor, wid)
        report = self._record('report', wid, rid)
        self._visible(actor, wid, report)
        versions = []
        for version in self.store.list('report_version', wid):
            if version.get('report_id') != rid:
                continue
            if workspace['role'] == 'viewer' and version['id'] != report.get('published_version_id'):
                continue
            try:
                snapshot = version['query_snapshot']
                if snapshot.get('workspace_id') != wid or snapshot.get('id') != version['definition']['query_id']:
                    continue
                self.service.require_connection(actor, wid, snapshot['connection_id'], report=True)
            except HTTPException as exc:
                if exc.status_code in {403, 404}:
                    continue
                raise
            versions.append({key: deepcopy(version[key]) for key in
                             ('id', 'workspace_id', 'report_id', 'version', 'report_revision', 'created_at', 'definition')})
        return sorted(versions, key=lambda version: version['version'], reverse=True)

    def create_report(self, actor, wid, definition):
        normalized = self.validate_definition(actor, wid, definition)
        report = self.store.create('report', wid, actor.id, normalized)
        self.store.audit(actor.id, wid, 'report.create', report['id'])
        return self._visible(actor, wid, report)

    def update_report(self, actor, wid, rid, changes, revision):
        self.service.require(actor, wid, write=True)
        report = self._record('report', wid, rid)
        self._query(actor, wid, report['query_id'])
        if set(changes) - {'name', 'query_id', 'visual', 'filters', 'description'}:
            raise HTTPException(422, 'Unknown report field')
        definition = {**_definition(report), **changes}
        normalized = self.validate_definition(actor, wid, definition)
        updated = self.store.update('report', rid, normalized, revision)
        self.store.audit(actor.id, wid, 'report.update', rid)
        return self._visible(actor, wid, updated)

    def archive_report(self, actor, wid, rid, revision):
        self.service.require(actor, wid, write=True)
        report = self._record('report', wid, rid)
        self._query(actor, wid, report['query_id'])
        result = self.store.archive('report', rid, revision)
        self.store.audit(actor.id, wid, 'report.archive', rid)
        return self.service.public(result)

    def publish_report(self, actor, wid, rid, revision):
        # Publication, the pinned query dependency, and the report pointer are
        # one metadata transaction. A conflicting draft cannot orphan a version.
        with self.store.transaction() as db:
            self.service.require(actor, wid, write=True)
            report = self._record('report', wid, rid)
            if report['revision'] != revision:
                raise HTTPException(409, 'Report changed; reload before publishing')
            definition = self.validate_definition(actor, wid, _definition(report))
            query = self._query(actor, wid, definition['query_id'])
            prior = self._published(wid, report) if report.get('published_version_id') else None
            version_id = str(uuid5(NAMESPACE_URL, f'termx-data-report:{wid}:{rid}:{revision}'))
            version = self.store._create(db, 'report_version', wid, actor.id, {
                'report_id': rid, 'report_revision': revision, 'version': (prior['version'] if prior else 0) + 1,
                'definition': definition,
                'query_snapshot': {key: deepcopy(query[key]) for key in
                                   ('id', 'workspace_id', 'revision', 'connection_id', 'sql', 'parameters') if key in query},
            }, identifier=version_id)
            updated = self.store._update(db, 'report', rid, {'published_version_id': version_id,
                                                           'published_at': version['created_at']}, revision)
            self.store._audit(db, actor.id, wid, 'report.publish', rid, {'version_id': version_id})
        return self._visible(actor, wid, updated)

    def authorize_runtime(self, actor, wid, context):
        """Recheck published report and live source grants for every job access."""
        workspace = self.service.require(actor, wid)
        if not isinstance(context, dict) or not context.get('report_id'):
            raise HTTPException(403, 'A report authorization context is required')
        report = self._record('report', wid, context['report_id'])
        version_id = context.get('report_version_id')
        if version_id:
            version = self._record('report_version', wid, version_id)
            if version.get('report_id') != report['id']:
                raise HTTPException(403, 'Report version does not belong to this report')
            if workspace['role'] == 'viewer' and report.get('published_version_id') != version_id:
                raise HTTPException(403, 'This publication is no longer shared')
            snapshot = version['query_snapshot']
            if snapshot.get('workspace_id') != wid or version['definition']['query_id'] != snapshot.get('id'):
                raise HTTPException(403, 'Report dependency does not belong to this workspace')
            cid = snapshot['connection_id']
        else:
            self.service.require(actor, wid, write=True)
            cid = self._query(actor, wid, report['query_id'])['connection_id']
        if context.get('connection_id') != cid:
            raise HTTPException(403, 'Report source has changed; run the report again')
        self.service.require_connection(actor, wid, cid, report=workspace['role'] == 'viewer')

    async def run_report(self, actor, wid, rid, request_id):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise HTTPException(422, 'A request_id containing 1–200 characters is required')
        workspace = self.service.require(actor, wid)
        report = self._record('report', wid, rid)
        if workspace['role'] == 'viewer':
            version = self._published(wid, report)
            definition, query = deepcopy(version['definition']), deepcopy(version['query_snapshot'])
            version_id = version['id']
        else:
            definition = self.validate_definition(actor, wid, _definition(report))
            query = self._query(actor, wid, definition['query_id'])
            version_id = None
        render_visual = deepcopy(definition['visual'])
        if render_visual['aggregate'] == 'count' and not render_visual.get('y'):
            render_visual['y'] = 'count_value' if render_visual.get('x') == 'count' and render_visual['kind'] != 'metric' else 'count'
        context = {'report_id': rid, 'report_version_id': version_id, 'connection_id': query['connection_id'],
                   'visual': render_visual, 'semantics': 'full_query', 'filters_applied': True,
                   'dataset_query_id': query['id'], 'dataset_query_revision': query['revision']}
        self.authorize_runtime(actor, wid, context)
        connection = self.service.require_connection(actor, wid, query['connection_id'], report=True)
        sql = str(query['sql']).strip().rstrip(';').rstrip()
        quote = lambda field: self.service.runtime.quote_identifier(connection['engine'], field)
        source = f'({sql}) AS {quote("_termx_dataset")}'
        probe = {'connection_id': query['connection_id'], 'sql': f'SELECT * FROM {source} WHERE 1 = 0',
                 'parameters': deepcopy(query.get('parameters') or {}), 'report_context': context}
        try:
            columns = await self.service.runtime.execute_read(actor, wid, probe, from_report=True, max_rows=0)
        except HTTPException:
            raise
        except Exception:
            # Driver failures can contain source SQL or credential URLs. The
            # explicit Run gets an actionable error without exposing either.
            raise HTTPException(422, 'The report dataset could not be read. Check its connection and saved query, then run again.') from None
        compiled = compile_visual(definition, source, columns['columns'], quote, query.get('parameters') or {})
        self.authorize_runtime(actor, wid, context)
        return await self.service.runtime.run(actor, wid, {
            'connection_id': query['connection_id'], **compiled, 'report_context': context,
        }, request_id, from_report=True)

    def _dashboard(self, actor, wid, dashboard):
        for rid in dashboard['report_ids']:
            self.report(actor, wid, rid)
        return self.service.public(dashboard)

    def dashboards(self, actor, wid):
        self.service.require(actor, wid)
        result = []
        for dashboard in self.store.list('dashboard', wid):
            try:
                result.append(self._dashboard(actor, wid, dashboard))
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
        return result

    def dashboard(self, actor, wid, did):
        self.service.require(actor, wid)
        return self._dashboard(actor, wid, self._record('dashboard', wid, did))

    def _validate_dashboard(self, actor, wid, definition):
        self.service.require(actor, wid, write=True)
        result = _validated(DashboardDefinition, definition)
        for rid in result['report_ids']:
            self.report(actor, wid, rid)
        return result

    def create_dashboard(self, actor, wid, definition):
        normalized = self._validate_dashboard(actor, wid, definition)
        result = self.store.create('dashboard', wid, actor.id, normalized)
        self.store.audit(actor.id, wid, 'dashboard.create', result['id'])
        return self._dashboard(actor, wid, result)

    def update_dashboard(self, actor, wid, did, changes, revision):
        self.service.require(actor, wid, write=True)
        current = self._record('dashboard', wid, did)
        self._dashboard(actor, wid, current)
        if set(changes) - {'name', 'report_ids'}:
            raise HTTPException(422, 'Unknown dashboard field')
        normalized = self._validate_dashboard(actor, wid, {**{key: current[key] for key in ('name', 'report_ids')}, **changes})
        result = self.store.update('dashboard', did, normalized, revision)
        self.store.audit(actor.id, wid, 'dashboard.update', did)
        return self._dashboard(actor, wid, result)

    def archive_dashboard(self, actor, wid, did, revision):
        self.service.require(actor, wid, write=True)
        current = self._record('dashboard', wid, did)
        self._dashboard(actor, wid, current)
        result = self.store.archive('dashboard', did, revision)
        self.store.audit(actor.id, wid, 'dashboard.archive', did)
        return self.service.public(result)

    async def run_dashboard(self, actor, wid, did, request_id):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise HTTPException(422, 'A request_id containing 1–200 characters is required')
        dashboard = self.dashboard(actor, wid, did)
        tiles = []
        for rid in dashboard['report_ids']:
            try:
                tile_request = uuid5(NAMESPACE_URL, f'termx-dashboard-run:{wid}:{did}:{request_id}:{rid}').hex
                job = await self.run_report(actor, wid, rid, tile_request)
                tiles.append({'report_id': rid, 'job': job})
            except HTTPException as exc:
                tiles.append({'report_id': rid, 'error': {'status': exc.status_code, 'detail': exc.detail}})
        self.dashboard(actor, wid, did)
        return {'dashboard_id': did, 'revision': dashboard['revision'], 'tiles': tiles}


def compile_visual(definition, source, columns, quote, parameters):
    """Compile only allowlisted operators and quoted identifiers, with bound values.

    ``source`` is a server-built derived table from validated saved read SQL.
    ``columns`` comes from a zero-row source query, never user-provided metadata.
    A pivot is a single row-dimension pivot with one aggregate value column.
    """
    visual = definition['visual']
    known = {column['name'] for column in columns}
    bound_fields = [visual.get('x'), visual.get('y')] + [item['field'] for item in definition.get('filters', [])]
    for field in bound_fields:
        if field is not None and field not in known:
            raise HTTPException(422, f'Field is not present in the saved query: {field}')
    params = deepcopy(parameters)
    terms = []

    def bind(value):
        index = len(params)
        key = f'_termx_bi_{index}'
        while key in params:
            index += 1
            key = f'_termx_bi_{index}'
        params[key] = value
        return f':{key}'

    for item in definition.get('filters', []):
        field, operator, value = quote(item['field']), item['operator'], item.get('value')
        if operator in {'is_null', 'not_null'}:
            terms.append(f'{field} IS {"NOT " if operator == "not_null" else ""}NULL')
        elif operator in {'eq', 'ne'} and value is None:
            terms.append(f'{field} IS {"NOT " if operator == "ne" else ""}NULL')
        elif operator in {'in', 'not_in'}:
            if not value:
                terms.append('1 = 1' if operator == 'not_in' else '1 = 0')
            else:
                # Membership has explicit null semantics, independent of SQL's
                # UNKNOWN result for NULL IN/NOT IN lists.
                values = [entry for entry in value if entry is not None]
                parts = []
                if values:
                    parts.append(f'{field} {"NOT " if operator == "not_in" else ""}IN ({", ".join(bind(entry) for entry in values)})')
                if None in value:
                    parts.append(f'{field} IS {"NOT " if operator == "not_in" else ""}NULL')
                terms.append('(' + (' AND ' if operator == 'not_in' else ' OR ').join(parts) + ')')
        elif operator == 'contains':
            escaped = value.replace('!', '!!').replace('%', '!%').replace('_', '!_')
            terms.append(f"{field} LIKE {bind('%' + escaped + '%')} ESCAPE '!' ")
        else:
            symbol = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[operator]
            terms.append(f'{field} {symbol} {bind(value)}')
    aggregate = visual.get('aggregate') or 'none'
    x, y = visual.get('x'), visual.get('y')
    grouping = None
    if aggregate == 'none':
        fields = [field for field in (x, y) if field]
        projection = ', '.join(quote(field) for field in dict.fromkeys(fields)) if fields else '*'
    else:
        value = '*' if aggregate == 'count' and not y else quote(y)
        alias = y or ('count_value' if x == 'count' and visual['kind'] != 'metric' else 'count')
        metric = f'{aggregate.upper()}({value}) AS {quote(alias)}'
        grouping = x if visual['kind'] != 'metric' else None
        projection = f'{quote(grouping)}, {metric}' if grouping else metric
    sql = f'SELECT {projection} FROM {source}'
    if terms:
        sql += ' WHERE ' + ' AND '.join(terms)
    if grouping:
        sql += f' GROUP BY {quote(grouping)} ORDER BY {quote(grouping)}'
    elif aggregate == 'none' and x and visual['kind'] in {'line', 'area'}:
        sql += f' ORDER BY {quote(x)}'
    return {'sql': sql, 'parameters': params}


def build_bi_router(service, actor):
    router = APIRouter(prefix='/api/data')
    bi = service.bi

    @router.get('/workspaces/{wid}/reports')
    def reports(wid: str, request: Request):
        return bi.reports(actor(request), wid)

    @router.post('/workspaces/{wid}/reports')
    def create_report(wid: str, body: ReportDefinition, request: Request):
        return bi.create_report(actor(request), wid, body.model_dump())

    @router.get('/workspaces/{wid}/reports/{rid}')
    def report(wid: str, rid: str, request: Request):
        return bi.report(actor(request), wid, rid)

    @router.get('/workspaces/{wid}/reports/{rid}/versions')
    def versions(wid: str, rid: str, request: Request):
        return bi.versions(actor(request), wid, rid)

    @router.patch('/workspaces/{wid}/reports/{rid}')
    def update_report(wid: str, rid: str, body: ReportPatch, request: Request):
        changes = body.model_dump(exclude_unset=True, exclude={'revision'})
        return bi.update_report(actor(request), wid, rid, changes, body.revision)

    @router.delete('/workspaces/{wid}/reports/{rid}')
    def archive_report(wid: str, rid: str, body: RevisionInput, request: Request):
        return bi.archive_report(actor(request), wid, rid, body.revision)

    @router.post('/workspaces/{wid}/reports/{rid}/publish')
    def publish_report(wid: str, rid: str, body: RevisionInput, request: Request):
        return bi.publish_report(actor(request), wid, rid, body.revision)

    @router.post('/workspaces/{wid}/reports/{rid}/run')
    async def run_report(wid: str, rid: str, body: RunInput, request: Request):
        return await bi.run_report(actor(request), wid, rid, body.request_id)

    @router.get('/workspaces/{wid}/dashboards')
    def dashboards(wid: str, request: Request):
        return bi.dashboards(actor(request), wid)

    @router.post('/workspaces/{wid}/dashboards')
    def create_dashboard(wid: str, body: DashboardDefinition, request: Request):
        return bi.create_dashboard(actor(request), wid, body.model_dump())

    @router.get('/workspaces/{wid}/dashboards/{did}')
    def dashboard(wid: str, did: str, request: Request):
        return bi.dashboard(actor(request), wid, did)

    @router.patch('/workspaces/{wid}/dashboards/{did}')
    def update_dashboard(wid: str, did: str, body: DashboardPatch, request: Request):
        return bi.update_dashboard(actor(request), wid, did, body.model_dump(exclude_unset=True, exclude={'revision'}), body.revision)

    @router.delete('/workspaces/{wid}/dashboards/{did}')
    def archive_dashboard(wid: str, did: str, body: RevisionInput, request: Request):
        return bi.archive_dashboard(actor(request), wid, did, body.revision)

    @router.post('/workspaces/{wid}/dashboards/{did}/run')
    async def run_dashboard(wid: str, did: str, body: RunInput, request: Request):
        return await bi.run_dashboard(actor(request), wid, did, body.request_id)

    return router
