"""BI source semantics, immutable publications, and live workspace/source policy."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from termx.data.bi import BIService, ReportDefinition, build_bi_router, compile_visual
from termx.data.service import DataService


def denied(code, fn):
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == code


@pytest.fixture
def setup(tmp_path):
    alice = SimpleNamespace(id='alice', display_name='Alice', scopes=('files-read',), policy_version=1, authority_session_id=None)
    bob = SimpleNamespace(id='bob', display_name='Bob', scopes=('files-read',), policy_version=1, authority_session_id=None)
    eve = SimpleNamespace(id='eve', display_name='Eve', scopes=('files-read',), policy_version=1, authority_session_id=None)
    principals = {actor.id: actor for actor in (alice, bob, eve)}
    identity = SimpleNamespace(principal_by_id=principals.get, current_principal=lambda actor: principals.get(actor.id))
    service = DataService(SimpleNamespace(identity=identity), tmp_path / 'metadata')
    wid = service.create_workspace(alice, 'BI fixtures')['id']
    viewer = service.store.create('member', wid, alice.id, {'principal_id': bob.id, 'role': 'viewer'},
                                  service.membership_id(wid, bob.id))
    path = tmp_path / 'business.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sales (category TEXT, amount REAL, label TEXT)')
        db.executemany('INSERT INTO sales VALUES (?,?,?)', [
            ('A', -2.5, 'plain'), ('A', 12.5, '100%_literal'), ('B', None, 'blank'),
            (None, 4.0, 'other'), ('B', 20.0, 'contains 100%_literal!'),
        ])
    connection = service.store.create('connection', wid, alice.id,
        {'name': 'Fixture', 'engine': 'sqlite', 'config': {'path': str(path)}, 'shared_read': False,
         'source_generation': 1, 'credential_generation': 0})
    query = service.store.create('query', wid, alice.id, {'name': 'Sales dataset',
        'connection_id': connection['id'], 'sql': 'SELECT category, amount, label FROM sales', 'parameters': {}})
    app = FastAPI()
    app.include_router(build_bi_router(service, lambda request: principals[request.headers.get('x-actor', 'alice')]))
    return SimpleNamespace(service=service, bi=service.bi, wid=wid, alice=alice, bob=bob, eve=eve,
                           connection=connection, query=query, viewer=viewer, path=path,
                           client=TestClient(app), principals=principals)


def definition(setup, **changes):
    return {'name': 'Revenue', 'query_id': setup.query['id'],
            'visual': {'kind': 'bar', 'x': 'category', 'y': 'amount', 'aggregate': 'sum'}, 'filters': [], **changes}


def share(setup, enabled=True):
    current = setup.service.store.get('connection', setup.connection['id'])
    return setup.service.store.update('connection', current['id'], {'shared_read': enabled}, current['revision'])


def test_published_snapshot_is_immutable_and_never_exposes_saved_sql(setup):
    s = setup
    report = s.bi.create_report(s.alice, s.wid, definition(s))
    assert not report['published']
    assert s.bi.reports(s.bob, s.wid) == []
    published = s.bi.publish_report(s.alice, s.wid, report['id'], report['revision'])
    assert published['revision'] == 2 and published['version'] == 1
    # Workspace membership alone does not grant use of its creator's source.
    assert s.bi.reports(s.bob, s.wid) == []
    share(s)
    visible = s.bi.report(s.bob, s.wid, report['id'])
    assert visible['name'] == 'Revenue' and visible['published_revision'] == 1
    draft = s.bi.update_report(s.alice, s.wid, report['id'], {'name': 'Unpublished draft'}, published['revision'])
    s.service.store.update('query', s.query['id'], {'sql': 'SELECT category, amount * 100 AS amount, label FROM sales'}, s.query['revision'])
    assert s.bi.report(s.bob, s.wid, report['id'])['name'] == 'Revenue'
    version = s.service.store.get('report_version', published['published_version_id'])
    assert version['definition']['name'] == 'Revenue'
    assert version['query_snapshot']['sql'] == s.query['sql']
    versions = s.bi.versions(s.alice, s.wid, report['id'])
    assert versions[0]['definition']['name'] == 'Revenue'
    assert 'query_snapshot' not in str(versions) and 'SELECT ' not in str(versions)
    assert 'Unpublished draft' not in str(s.bi.versions(s.bob, s.wid, report['id']))
    denied(409, lambda: s.bi.update_report(s.alice, s.wid, report['id'], {'name': 'Lost update'}, 1))
    denied(409, lambda: s.bi.publish_report(s.alice, s.wid, report['id'], 1))
    assert len(s.service.store.list('report_version', s.wid)) == 1
    republished = s.bi.publish_report(s.alice, s.wid, report['id'], draft['revision'])
    assert republished['version'] == 2
    assert len(s.bi.versions(s.alice, s.wid, report['id'])) == 2
    assert len(s.bi.versions(s.bob, s.wid, report['id'])) == 1
    assert s.bi.report(s.bob, s.wid, report['id'])['name'] == 'Unpublished draft'


def test_all_nested_ids_are_workspace_and_source_authorized(setup):
    s = setup
    other = s.service.create_workspace(s.alice, 'Other')['id']
    denied(404, lambda: s.bi.create_report(s.alice, other, definition(s)))
    report = s.bi.create_report(s.alice, s.wid, definition(s))
    denied(404, lambda: s.bi.create_dashboard(s.alice, other, {'name': 'Cross workspace', 'report_ids': [report['id']]}))
    denied(404, lambda: s.bi.report(s.eve, s.wid, report['id']))
    denied(403, lambda: s.bi.create_report(s.bob, s.wid, definition(s)))
    denied(403, lambda: s.bi.publish_report(s.bob, s.wid, report['id'], report['revision']))
    s.service.store.update('member', s.viewer['id'], {'role': 'editor'}, s.viewer['revision'])
    denied(403, lambda: s.bi.create_report(s.bob, s.wid, definition(s)))
    share(s)
    assert s.bi.create_report(s.bob, s.wid, definition(s))['name'] == 'Revenue'
    share(s, False)
    assert s.bi.reports(s.bob, s.wid) == []


def test_dashboard_crud_published_visibility_and_revisions(setup):
    s = setup
    report = s.bi.create_report(s.alice, s.wid, definition(s))
    dashboard = s.bi.create_dashboard(s.alice, s.wid, {'name': 'Overview', 'report_ids': [report['id']]})
    assert s.bi.dashboards(s.bob, s.wid) == []
    s.bi.publish_report(s.alice, s.wid, report['id'], report['revision'])
    share(s)
    assert s.bi.dashboards(s.bob, s.wid)[0]['report_ids'] == [report['id']]
    changed = s.bi.update_dashboard(s.alice, s.wid, dashboard['id'], {'name': 'Updated'}, 1)
    denied(409, lambda: s.bi.update_dashboard(s.alice, s.wid, dashboard['id'], {'name': 'Stale'}, 1))
    denied(422, lambda: s.bi.create_dashboard(s.alice, s.wid, {'name': 'Duplicate', 'report_ids': [report['id'], report['id']]}))
    archived = s.bi.archive_dashboard(s.alice, s.wid, dashboard['id'], changed['revision'])
    assert archived['archived'] and not s.bi.dashboards(s.alice, s.wid)
    assert s.service.store.get('dashboard', dashboard['id']) is not None


def test_live_report_source_publication_and_membership_authority(setup):
    s = setup
    report = s.bi.create_report(s.alice, s.wid, definition(s))
    published = s.bi.publish_report(s.alice, s.wid, report['id'], 1)
    context = {'report_id': report['id'], 'report_version_id': published['published_version_id'],
               'connection_id': s.connection['id']}
    share(s)
    s.bi.authorize_runtime(s.bob, s.wid, context)
    denied(403, lambda: s.bi.authorize_runtime(s.bob, s.wid, {**context, 'report_version_id': None}))
    denied(403, lambda: s.bi.authorize_runtime(s.bob, s.wid, {**context, 'connection_id': 'other'}))
    share(s, False)
    denied(403, lambda: s.bi.authorize_runtime(s.bob, s.wid, context))
    share(s)
    draft = s.bi.update_report(s.alice, s.wid, report['id'], {'name': 'Next'}, published['revision'])
    s.bi.publish_report(s.alice, s.wid, report['id'], draft['revision'])
    denied(403, lambda: s.bi.authorize_runtime(s.bob, s.wid, context))
    s.service.store.archive('member', s.viewer['id'], s.viewer['revision'])
    denied(404, lambda: s.bi.reports(s.bob, s.wid))
    assert s.bi.reports(s.alice, s.wid)


def test_archive_report_revokes_jobs_and_dashboard_visibility(setup):
    s = setup
    report = s.bi.create_report(s.alice, s.wid, definition(s))
    s.bi.create_dashboard(s.alice, s.wid, {'name': 'Overview', 'report_ids': [report['id']]})
    context = {'report_id': report['id'], 'report_version_id': None, 'connection_id': s.connection['id']}
    s.bi.archive_report(s.alice, s.wid, report['id'], 1)
    denied(404, lambda: s.bi.authorize_runtime(s.alice, s.wid, context))
    assert s.bi.dashboards(s.alice, s.wid) == []


def test_http_typed_reports_filters_and_revision_conflicts(setup):
    s = setup
    base = f'/api/data/workspaces/{s.wid}'
    assert s.client.post(base + '/reports', json={**definition(s), 'sql': 'SELECT 1'}).status_code == 422
    assert s.client.post(base + '/reports', json=definition(s, visual={'kind': 'scatter', 'x': 'category'})).status_code == 422
    assert s.client.post(base + '/reports', json=definition(s, visual={'kind': 'metric', 'aggregate': 'none'})).status_code == 422
    assert s.client.post(base + '/reports', json=definition(s, filters=[{'field': 'amount', 'operator': 'eq', 'value': {'sql': 'DROP TABLE sales'}}])).status_code == 422
    report = s.client.post(base + '/reports', json=definition(s)).json()
    assert s.client.get(base + '/reports').json()[0]['id'] == report['id']
    assert s.client.patch(base + '/reports/' + report['id'], json={'revision': 1, 'name': 'Changed'}).status_code == 200
    assert s.client.patch(base + '/reports/' + report['id'], json={'revision': 1, 'name': 'Conflict'}).status_code == 409
    assert s.client.post(base + '/reports/' + report['id'] + '/publish', json={'revision': 2}).status_code == 200
    versions = s.client.get(base + '/reports/' + report['id'] + '/versions').json()
    assert versions[0]['version'] == 1 and 'query_snapshot' not in versions[0]
    assert s.client.post(base + '/reports', headers={'x-actor': 'bob'}, json=definition(s)).status_code == 403


def execute_compiled(path, visual, filters=None, sql='SELECT category, amount, label FROM sales', parameters=None):
    quote = lambda value: '"' + value.replace('"', '""') + '"'
    definition = ReportDefinition.model_validate({'name': 'Fixture', 'query_id': 'fixture', 'visual': visual, 'filters': filters or []}).model_dump()
    source = f'({sql}) AS "_termx_dataset"'
    with sqlite3.connect(path) as db:
        cursor = db.execute(f'SELECT * FROM {source} WHERE 1 = 0', parameters or {})
        columns = [{'name': item[0], 'type': 'unknown'} for item in cursor.description]
        compiled = compile_visual(definition, source, columns, quote, parameters or {})
        result = db.execute(compiled['sql'], compiled['parameters'])
        return [item[0] for item in result.description], result.fetchall(), compiled


@pytest.mark.parametrize('kind', ['bar', 'line', 'area', 'pie', 'pivot'])
def test_grouped_visuals_use_actual_negative_null_and_decimal_values(setup, kind):
    columns, rows, _ = execute_compiled(setup.path, {'kind': kind, 'x': 'category', 'y': 'amount', 'aggregate': 'sum'})
    assert columns == ['category', 'amount']
    assert rows == [(None, 4.0), ('A', 10.0), ('B', 20.0)]


@pytest.mark.parametrize('aggregate,expected', [('sum', 34.0), ('count', 4), ('avg', 8.5), ('min', -2.5), ('max', 20.0)])
def test_metric_aggregates_are_actual_source_values(setup, aggregate, expected):
    columns, rows, _ = execute_compiled(setup.path, {'kind': 'metric', 'y': 'amount', 'aggregate': aggregate})
    assert columns == ['amount'] and rows == [(expected,)]


def test_table_scatter_counts_and_full_query_totals_exceed_preview(setup):
    with sqlite3.connect(setup.path) as db:
        db.executemany('INSERT INTO sales VALUES (?,?,?)', [('A', 1, 'many')] * 2500)
    _, rows, _ = execute_compiled(setup.path, {'kind': 'metric', 'aggregate': 'count'})
    assert rows == [(2505,)]
    _, rows, _ = execute_compiled(setup.path, {'kind': 'metric', 'y': 'amount', 'aggregate': 'sum'})
    assert rows == [(2534.0,)]
    columns, rows, _ = execute_compiled(setup.path, {'kind': 'table'})
    assert columns == ['category', 'amount', 'label'] and len(rows) == 2505
    columns, rows, _ = execute_compiled(setup.path, {'kind': 'scatter', 'x': 'category', 'y': 'amount'})
    assert columns == ['category', 'amount'] and len(rows) == 2505


def test_filters_bind_values_escape_contains_and_handle_null_membership(setup):
    visual = {'kind': 'metric', 'aggregate': 'count'}
    _, rows, compiled = execute_compiled(setup.path, visual,
        [{'field': 'label', 'operator': 'contains', 'value': '100%_literal'}])
    assert rows == [(2,)] and '100%_literal' not in compiled['sql']
    _, rows, compiled = execute_compiled(setup.path, visual,
        [{'field': 'category', 'operator': 'eq', 'value': "A' OR 1=1 --"}])
    assert rows == [(0,)] and "A' OR" not in compiled['sql']
    _, rows, _ = execute_compiled(setup.path, visual, [{'field': 'category', 'operator': 'in', 'value': [None, 'A']}])
    assert rows == [(3,)]
    _, rows, _ = execute_compiled(setup.path, visual, [{'field': 'category', 'operator': 'not_in', 'value': [None, 'A']}])
    assert rows == [(2,)]
    _, rows, _ = execute_compiled(setup.path, visual, [{'field': 'category', 'operator': 'in', 'value': []}])
    assert rows == [(0,)]
    _, rows, _ = execute_compiled(setup.path, visual, [{'field': 'amount', 'operator': 'is_null'}])
    assert rows == [(1,)]


def test_quoted_field_names_missing_fields_and_saved_parameters(setup):
    sql = 'SELECT category AS "odd""name", amount FROM sales WHERE amount > :threshold'
    columns, rows, compiled = execute_compiled(setup.path, {'kind': 'bar', 'x': 'odd"name', 'y': 'amount', 'aggregate': 'sum'},
        [{'field': 'amount', 'operator': 'lt', 'value': 15}], sql=sql, parameters={'threshold': 0, '_termx_bi_2': 'reserved'})
    assert columns == ['odd"name', 'amount'] and rows == [(None, 4.0), ('A', 12.5)]
    assert compiled['parameters']['threshold'] == 0 and compiled['parameters']['_termx_bi_2'] == 'reserved'
    with pytest.raises(HTTPException) as exc:
        execute_compiled(setup.path, {'kind': 'metric', 'y': 'missing', 'aggregate': 'sum'})
    assert exc.value.status_code == 422


def test_dashboard_keeps_individual_failure_state(setup):
    s = setup
    first = s.bi.create_report(s.alice, s.wid, definition(s))
    second = s.bi.create_report(s.alice, s.wid, definition(s, name='Second'))
    dashboard = s.bi.create_dashboard(s.alice, s.wid, {'name': 'Overview', 'report_ids': [first['id'], second['id']]})

    async def run_report(actor, wid, rid, request_id):
        if rid == first['id']:
            raise HTTPException(422, 'Source field changed')
        return {'id': request_id, 'status': 'queued', 'connection_id': s.connection['id']}

    s.bi.run_report = run_report
    result = asyncio.run(s.bi.run_dashboard(s.alice, s.wid, dashboard['id'], 'dashboard-request'))
    assert result['tiles'][0]['error']['detail'] == 'Source field changed'
    assert result['tiles'][1]['job']['status'] == 'queued'
    assert 'result' not in result['tiles'][1]['job']


async def finished(runtime, actor, wid, job):
    for _ in range(500):
        current = runtime.get_job(actor, wid, job['id'])
        if current['status'] in {'completed', 'failed', 'cancelled', 'unknown'}:
            assert current['status'] == 'completed', current.get('error')
            return current
        await asyncio.sleep(.01)
    pytest.fail('Local SQLite fixture job did not reach a terminal state')


def test_actual_runtime_full_dataset_aggregate_table_preview_and_csv(setup):
    s = setup
    runtime = s.service.runtime
    connection = runtime.create_connection(s.alice, s.wid, {'name': 'Actual SQLite', 'engine': 'sqlite', 'config': {'path': str(s.path)}})
    query = runtime.create_query(s.alice, s.wid, {'name': 'Actual dataset', 'connection_id': connection['id'], 'sql': s.query['sql']})
    with sqlite3.connect(s.path) as db:
        db.executemany('INSERT INTO sales VALUES (?,?,?)', [('A', 1, 'many')] * 2500)
    report = s.bi.create_report(s.alice, s.wid, definition(s, query_id=query['id'], visual={'kind': 'metric', 'y': 'amount', 'aggregate': 'sum'}))
    table = s.bi.create_report(s.alice, s.wid, definition(s, name='Actual table', query_id=query['id'], visual={'kind': 'table'}))

    async def scenario():
        try:
            job = await s.bi.run_report(s.alice, s.wid, report['id'], 'actual-sum')
            done = await finished(runtime, s.alice, s.wid, job)
            assert done['result']['rows'] == [[2534.0]] and not done['result']['truncated']
            assert done['report_context']['semantics'] == 'full_query'
            assert done['report_context']['dataset_query_id'] == query['id']
            repeat = await s.bi.run_report(s.alice, s.wid, report['id'], 'actual-sum')
            assert repeat['id'] == job['id'] and repeat['status'] == 'completed'
            csv, truncated = runtime.export_csv(s.alice, s.wid, job['id'])
            assert '2534' in csv and not truncated
            table_job = await s.bi.run_report(s.alice, s.wid, table['id'], 'actual-table')
            table_done = await finished(runtime, s.alice, s.wid, table_job)
            assert table_done['result']['row_count'] == 1000 and table_done['result']['truncated']
            assert len(table_done['result']['rows']) == 1000
            _, truncated = runtime.export_csv(s.alice, s.wid, table_job['id'])
            assert truncated  # This CSV truthfully exports the displayed query preview.
            changed = s.bi.update_report(s.alice, s.wid, report['id'], {'filters': [{'field': 'amount', 'operator': 'gt', 'value': 5}]}, report['revision'])
            with pytest.raises(HTTPException) as error:
                await s.bi.run_report(s.alice, s.wid, changed['id'], 'actual-sum')
            assert error.value.status_code == 409
        finally:
            await runtime.close()
    asyncio.run(scenario())


def test_actual_runtime_viewer_uses_immutable_query_and_live_revocation(setup):
    s = setup
    runtime = s.service.runtime
    connection = runtime.create_connection(s.alice, s.wid, {'name': 'Actual shared SQLite', 'engine': 'sqlite', 'config': {'path': str(s.path)}, 'shared_read': True})
    query = runtime.create_query(s.alice, s.wid, {'name': 'Actual dataset', 'connection_id': connection['id'], 'sql': s.query['sql']})
    report = s.bi.create_report(s.alice, s.wid, definition(s, query_id=query['id'], visual={'kind': 'metric', 'y': 'amount', 'aggregate': 'sum'}))
    s.bi.publish_report(s.alice, s.wid, report['id'], 1)
    runtime.update_query(s.alice, s.wid, query['id'], {'revision': 1, 'sql': 'SELECT category, amount * 10 AS amount, label FROM sales'})

    async def scenario():
        try:
            viewer_job = await s.bi.run_report(s.bob, s.wid, report['id'], 'published-actual')
            viewer_done = await finished(runtime, s.bob, s.wid, viewer_job)
            assert viewer_done['result']['rows'] == [[34.0]]
            assert viewer_done['report_context']['report_version_id']
            author_done = await finished(runtime, s.alice, s.wid, await s.bi.run_report(s.alice, s.wid, report['id'], 'draft-actual'))
            assert author_done['result']['rows'] == [[340.0]]
            denied(404, lambda: runtime.get_job(s.alice, s.wid, viewer_job['id']))
            with pytest.raises(HTTPException) as error:
                await runtime.run(s.bob, s.wid, {'connection_id': connection['id'], 'sql': 'SELECT * FROM sales'}, 'viewer-sql')
            assert error.value.status_code == 403
            text, truncated = runtime.export_csv(s.bob, s.wid, viewer_job['id'])
            assert '34' in text and not truncated
            runtime.update_connection(s.alice, s.wid, connection['id'], {'revision': connection['revision'], 'shared_read': False})
            denied(403, lambda: runtime.get_job(s.bob, s.wid, viewer_job['id']))
            denied(403, lambda: runtime.export_csv(s.bob, s.wid, viewer_job['id']))
        finally:
            await runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['table', 'bar', 'line', 'area', 'scatter', 'pie', 'metric', 'pivot'])
def test_actual_runtime_all_visual_families_and_bound_filter(setup, kind):
    s = setup
    visual = {'kind': kind, 'x': 'category', 'y': 'amount', 'aggregate': 'none' if kind in {'table', 'scatter'} else 'sum'}
    report = s.bi.create_report(s.alice, s.wid, definition(s, visual=visual,
        filters=[{'field': 'label', 'operator': 'contains', 'value': '100%_literal'}]))

    async def scenario():
        try:
            done = await finished(s.service.runtime, s.alice, s.wid, await s.bi.run_report(s.alice, s.wid, report['id'], 'visual-' + kind))
            rows = done['result']['rows']
            if kind == 'metric':
                assert rows == [[32.5]]
            else:
                assert rows == [['A', 12.5], ['B', 20.0]]
        finally:
            await s.service.runtime.close()
    asyncio.run(scenario())


def test_actual_runtime_rejects_missing_bindings_before_job_dispatch(setup):
    s = setup
    report = s.bi.create_report(s.alice, s.wid, definition(s, visual={'kind': 'metric', 'y': 'missing', 'aggregate': 'sum'}))

    async def scenario():
        try:
            with pytest.raises(HTTPException) as error:
                await s.bi.run_report(s.alice, s.wid, report['id'], 'invalid-binding')
            assert error.value.status_code == 422
            assert s.service.store.list('job', s.wid) == []
        finally:
            await s.service.runtime.close()
    asyncio.run(scenario())


def test_actual_runtime_probe_failure_is_visible_and_source_remains_unchanged(setup):
    s = setup
    s.service.store.update('query', s.query['id'], {'sql': 'SELECT * FROM missing_fixture_table'}, 1)
    report = s.bi.create_report(s.alice, s.wid, definition(s))

    async def scenario():
        try:
            with pytest.raises(HTTPException) as error:
                await s.bi.run_report(s.alice, s.wid, report['id'], 'unavailable-source')
            assert error.value.status_code == 422
            assert 'could not be read' in error.value.detail
            assert 'missing_fixture_table' not in error.value.detail
            with sqlite3.connect(s.path) as db:
                assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0] == 5
        finally:
            await s.service.runtime.close()
    asyncio.run(scenario())
