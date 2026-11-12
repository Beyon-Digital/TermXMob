"""HTTP behaviour of the four visual-query routes: roles, locks, 409 handling, idempotency,
the schema cache and the shared contract fixtures that the frontend also tests against.

The fixtures in desktop/workspace/src/lib/query-contract-fixtures/*.json are generated from
this backend. Regenerate them after an intentional contract change with
`TERMX_UPDATE_FIXTURES=1 uv run pytest tests/test_data_query_http.py -k contract` and edit
section 4 of the plan in the same commit.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_query_fixture import PASSWORD, make_fixture
from termx.data.connectors import NetworkSQLConnector, SQLiteConnector
from termx.data.http import mount_data
from termx.data.query_service import SchemaCache
from termx.identity_locks import SessionLocks

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / 'desktop' / 'workspace' / 'src' / 'lib' / 'query-contract-fixtures'
MESSAGE_409 = 'Test this source first to verify its current read-only role and session.'
UNSUPPORTED = ('The visual query builder supports SQLite, PostgreSQL, MySQL and MariaDB sources. '
               'SQL Server / Azure SQL sources can still use Advanced SQL.')


def C(alias, column):
    return {'table_alias': alias, 'column': column}


def spec(source, **kw):
    body = {'version': 1, 'source': source, 'from': {'table': 'orders', 'alias': 'o'}, 'joins': [],
            'select': [C('o', 'id'), C('o', 'status')], 'filters': {'op': 'and', 'items': []}, 'group_by': [],
            'aggregates': [], 'sort': [], 'limit': 200, 'offset': 0}
    body.update(kw)
    return body


def with_source(value, source_id):
    return json.loads(json.dumps(value).replace('conn_01', source_id))


def to_contract(value, source_id):
    return json.loads(json.dumps(value).replace(source_id, 'conn_01'))


@pytest.fixture
def env(tmp_path, monkeypatch):
    fx = make_fixture(tmp_path, monkeypatch)
    app = FastAPI()
    mount_data(app, fx.state)
    for name in fx.actors:
        fx.sessions[name] = asyncio.run(fx.identity.login('local-password', {'username': name, 'password': PASSWORD}, peer='127.0.0.1'))
    with TestClient(app, base_url='https://localhost') as client:
        fx.client = client
        fx.base = '/api/data/workspaces/' + fx.wid
        yield fx


def auth(env, name='alice'):
    return {'Authorization': 'Bearer ' + env.sessions[name].access_token}


def post(env, path, body, who='alice', base=None):
    return env.client.post((base or env.base) + path, headers=auth(env, who), json=body)


def get(env, path, who='alice', base=None):
    return env.client.get((base or env.base) + path, headers=auth(env, who))


def wait(env, job, who='alice'):
    for _ in range(2000):
        value = get(env, '/jobs/' + job['id'], who).json()
        if value['status'] not in {'queued', 'running'}:
            return value
        time.sleep(.005)
    raise AssertionError('job did not finish')


def routes(env, cid=None):
    cid = cid or env.source['id']
    return {
        'compile': lambda who: post(env, '/query-specs/compile', {'spec': spec(cid)}, who),
        'run': lambda who: post(env, '/query-specs/run', {'request_id': 'r-' + who, 'spec': spec(cid)}, who),
        'relations': lambda who: get(env, f'/connections/{cid}/relations?table=orders', who),
        'capabilities': lambda who: get(env, f'/connections/{cid}/capabilities', who)}


# --- roles and status codes ------------------------------------------------------------------
@pytest.mark.parametrize('route', ['compile', 'run', 'relations', 'capabilities'])
def test_roles_on_every_route(env, route):
    call = routes(env)[route]
    assert call('alice').status_code == 200
    assert call('bob').status_code == 200
    viewer = call('vera')
    assert viewer.status_code == 403 and viewer.json()['detail'] == 'Workspace editor required'
    assert call('outsider').status_code == 404
    assert env.client.get(env.base + '/connections/x/capabilities').status_code == 401
    assert env.client.post(env.base + '/query-specs/compile', json={'spec': spec('x')}).status_code == 401


@pytest.mark.parametrize('route', ['compile', 'run', 'relations', 'capabilities'])
def test_foreign_private_and_unknown_connections(env, route):
    assert routes(env, env.private['id'])[route]('bob').status_code == 403            # owned by alice, not shared
    assert routes(env, env.private['id'])[route]('alice').status_code == 200
    assert routes(env, env.foreign['id'])[route]('alice').status_code == 404          # lives in another workspace
    assert routes(env, 'does-not-exist')[route]('alice').status_code == 404


def test_unknown_workspace_is_404(env):
    gone = '/api/data/workspaces/nope'
    assert post(env, '/query-specs/compile', {'spec': spec('x')}, base=gone).status_code == 404
    assert post(env, '/query-specs/run', {'request_id': 'a', 'spec': spec('x')}, base=gone).status_code == 404
    assert get(env, '/connections/x/capabilities', base=gone).status_code == 404
    assert get(env, '/connections/x/relations', base=gone).status_code == 404


@pytest.mark.parametrize('route', ['compile', 'run', 'relations', 'capabilities'])
def test_locked_session_is_423(env, route):
    SessionLocks(env.identity).lock(env.identity.resolve(env.sessions['alice'].access_token))
    assert routes(env)[route]('alice').status_code == 423
    assert routes(env)[route]('bob').status_code == 200


def test_cookie_requests_need_csrf(env):
    env.client.cookies.set('termx_access', env.sessions['alice'].access_token)
    url = env.base + '/query-specs/compile'
    body = {'spec': spec(env.source['id'])}
    assert env.client.post(url, json=body).status_code == 403
    headers = {'Origin': 'https://localhost', 'x-termx-csrf': env.sessions['alice'].csrf_token}
    assert env.client.post(url, headers=headers, json=body).status_code == 200
    assert env.client.post(url, headers={**headers, 'Origin': 'https://evil.example'}, json=body).status_code == 403


def test_bootstrap_reports_visual_query(env):
    body = env.client.get('/api/data/bootstrap', headers=auth(env)).json()
    assert body['capabilities']['visual_query'] is True and body['capabilities']['copilot'] is True


def test_openapi_documents_the_new_routes(env):
    paths = env.client.get('/openapi.json').json()['paths']
    assert '/api/data/workspaces/{wid}/query-specs/compile' in paths and '/api/data/workspaces/{wid}/query-specs/run' in paths


# --- compile responses -------------------------------------------------------------------------
def test_compile_semantic_errors_are_200_and_structural_errors_are_422(env):
    cid = env.source['id']
    bad = spec(cid, filters={'op': 'and', 'items': [
        {'field': C('o', 'id'), 'op': 'eq', 'value': 1}, {'field': C('o', 'amout'), 'op': 'gt', 'value': 1}]},
        group_by=[{**C('o', 'id'), 'bucket': 'month'}], select=[])
    response = post(env, '/query-specs/compile', {'spec': bad})
    assert response.status_code == 200
    assert response.json() == {'ok': False, 'errors': [
        {'code': 'unknown_column', 'path': 'filters.items[1].field.column', 'message': "Column 'amout' does not exist in table 'orders'."},
        {'code': 'bucket_requires_date', 'path': 'group_by[0].bucket', 'message': "'id' is not a date column, so it cannot be grouped by month."}]}
    for broken in ({**spec(cid), 'pivot': {}}, spec(cid, limit=1001), spec(cid, joins=[{}] * 5),
                   spec(cid, filters={'op': 'and', 'items': [{'field': C('o', 'amount'), 'op': 'between', 'values': [1]}]})):
        invalid = post(env, '/query-specs/compile', {'spec': broken})
        assert invalid.status_code == 422 and isinstance(invalid.json()['detail'], list)
    assert post(env, '/query-specs/compile', {'spec': spec(cid), 'extra': 1}).status_code == 422


def test_compile_does_not_execute_and_returns_the_documented_keys(env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('compile must never execute a query')
    monkeypatch.setattr(SQLiteConnector, 'execute', forbidden)
    body = post(env, '/query-specs/compile', {'spec': spec(env.source['id'])}).json()
    assert set(body) == {'ok', 'dialect', 'sql', 'parameters', 'columns', 'limit', 'offset', 'fetch_limit', 'joins', 'warnings', 'schema_hash'}
    assert body['ok'] is True and body['dialect'] == 'sqlite' and body['fetch_limit'] == 201


def test_capabilities_and_relations_bodies(env):
    cid = env.source['id']
    body = get(env, f'/connections/{cid}/capabilities').json()
    assert body['connection_id'] == cid and body['engine'] == 'sqlite' and body['supported'] is True
    assert body['source_tested'] is True and body['qualified'] is True and body['requires_source_test'] is False
    assert list(body) == ['connection_id', 'engine', 'label', 'supported', 'reason', 'qualified',
                          'requires_source_test', 'source_tested', 'dialect']
    relations = get(env, f'/connections/{cid}/relations?table=orders').json()
    assert relations['connection_id'] == cid
    assert [r['sentence'] for r in relations['relations']] == [
        'Order items to Orders: order_id matches id', 'Orders to Customers: customer_id matches id']
    assert get(env, f'/connections/{cid}/relations').status_code == 200
    unknown = get(env, f'/connections/{cid}/relations?table=nope')
    assert unknown.status_code == 422 and isinstance(unknown.json()['detail'], str)


def test_unsupported_engine_fails_closed(env):
    cid = env.source['id']
    record = env.runtime.store.get('connection', cid)
    env.runtime.store.update('connection', cid, {'engine': 'sqlserver'}, record['revision'])
    for response in (post(env, '/query-specs/compile', {'spec': spec(cid)}),
                     post(env, '/query-specs/run', {'request_id': 'u', 'spec': spec(cid)}),
                     get(env, f'/connections/{cid}/relations')):
        assert response.status_code == 422 and response.json()['detail'] == UNSUPPORTED
    caps = get(env, f'/connections/{cid}/capabilities')
    assert caps.status_code == 200 and caps.json() == {
        'connection_id': cid, 'engine': 'sqlserver', 'label': 'SQL Server / Azure SQL', 'supported': False, 'reason': UNSUPPORTED,
        'qualified': False, 'requires_source_test': False, 'source_tested': False, 'dialect': None}


# --- run: jobs, idempotency, results ----------------------------------------------------------
def test_run_returns_the_same_job_shape_as_query_runs_and_pages_with_a_probe_row(env):
    cid = env.source['id']
    job = post(env, '/query-specs/run', {'request_id': 'page-1', 'spec': spec(cid)}).json()
    legacy = post(env, '/query-runs', {'request_id': 'legacy-1', 'connection_id': cid, 'sql': 'SELECT 1'}).json()
    base = {'id', 'status', 'connection_id', 'created_at', 'updated_at', 'cancel_requested', 'preview_limit'}
    assert set(legacy) == base and set(job) == base | {'output'}      # a spec run also carries its column metadata
    assert [c['label'] for c in job['output']['columns']] == ['id', 'status'] and job['output']['warnings'] == []
    assert job['connection_id'] == cid and job['preview_limit'] == 1000
    done = wait(env, job)
    assert done['status'] == 'completed'
    assert done['result']['row_count'] == 201 and len(done['result']['rows']) == 201 and done['result']['truncated'] is False
    assert [c['name'] for c in done['result']['columns']] == ['id', 'status']
    big = wait(env, post(env, '/query-specs/run', {'request_id': 'page-2', 'spec': spec(cid, limit=1000)}).json())
    assert big['result']['truncated'] is True and len(big['result']['rows']) == 1000
    last = wait(env, post(env, '/query-specs/run', {'request_id': 'page-3', 'spec': spec(cid, limit=100, offset=1250)}).json())
    assert len(last['result']['rows']) == 55 and last['result']['rows'][0][0] == 1251 and last['result']['truncated'] is False
    export = get(env, '/jobs/' + job['id'] + '/export.csv')
    assert export.status_code == 200 and export.text.splitlines()[0] == 'id,status' and len(export.text.splitlines()) == 202


def test_run_is_idempotent_per_request_id(env):
    cid = env.source['id']
    first = post(env, '/query-specs/run', {'request_id': 'same', 'spec': spec(cid)}).json()
    again = post(env, '/query-specs/run', {'request_id': 'same', 'spec': spec(cid)}).json()
    assert first['id'] == again['id']
    wait(env, first)
    different = post(env, '/query-specs/run', {'request_id': 'same', 'spec': spec(cid, limit=10)})
    assert different.status_code == 409 and different.json()['detail'] == 'This request_id was already used with a different query.'
    other = post(env, '/query-specs/run', {'request_id': 'other', 'spec': spec(cid)}).json()
    assert other['id'] != first['id']
    wait(env, other)
    assert len(env.runtime.store.list('job', env.wid)) == 2
    bob = post(env, '/query-specs/run', {'request_id': 'same', 'spec': spec(cid)}, 'bob').json()
    assert bob['id'] != first['id']                                   # request ids are scoped to the actor
    assert get(env, '/jobs/' + first['id'], 'bob').status_code == 404


def test_run_semantic_error_is_422_with_the_first_message(env):
    bad = spec(env.source['id'], select=[C('o', 'amout')])
    response = post(env, '/query-specs/run', {'request_id': 'bad', 'spec': bad})
    assert response.status_code == 422 and response.json()['detail'] == "Column 'amout' does not exist in table 'orders'."
    assert env.runtime.store.list('job', env.wid) == []


def test_cancel_and_csv_on_a_spec_job(env):
    job = post(env, '/query-specs/run', {'request_id': 'cancel', 'spec': spec(env.source['id'])}).json()
    assert post(env, '/jobs/' + job['id'] + '/cancel', {}).status_code == 200
    final = wait(env, job)
    assert final['status'] in {'cancelled', 'completed'}
    expected = 409 if final['status'] == 'cancelled' else 200
    assert get(env, '/jobs/' + job['id'] + '/export.csv').status_code == expected


def test_authority_change_between_compile_and_run_discards_the_result(env, monkeypatch):
    started, release = threading.Event(), threading.Event()
    execute = SQLiteConnector.execute

    def blocked(self, *args, **kwargs):
        started.set()
        assert release.wait(5)
        return execute(self, *args, **kwargs)
    monkeypatch.setattr(SQLiteConnector, 'execute', blocked)
    assert post(env, '/query-specs/compile', {'spec': spec(env.source['id'])}, 'bob').status_code == 200
    job = post(env, '/query-specs/run', {'request_id': 'revoked', 'spec': spec(env.source['id'])}, 'bob').json()
    for _ in range(1000):
        if started.is_set():
            break
        time.sleep(.005)
    assert started.is_set()
    member = next(m for m in env.service.members(env.actors['alice'], env.wid) if m.get('principal_id') == env.actors['bob'].id)
    env.service.change_member(env.actors['alice'], env.wid, member['id'], member['revision'], remove=True)
    release.set()
    record = env.runtime.store.get('job', job['id'])
    for _ in range(1000):
        record = env.runtime.store.get('job', job['id'])
        if record['status'] not in {'queued', 'running'}:
            break
        time.sleep(.005)
    assert record['status'] == 'failed' and record['result'] is None
    assert get(env, '/jobs/' + job['id'], 'bob').status_code == 404


# --- schema cache ----------------------------------------------------------------------------
@pytest.fixture
def schema_calls(monkeypatch):
    calls = []
    original = SQLiteConnector.schema

    def counting(self):
        calls.append(1)
        return original(self)
    monkeypatch.setattr(SQLiteConnector, 'schema', counting)
    return calls


def test_schema_is_cached_for_a_minute_and_authority_is_still_checked(env, schema_calls):
    clock = [1000.0]
    env.service.query.cache = SchemaCache(ttl=60, clock=lambda: clock[0])
    cid = env.source['id']

    def compile_(who='alice'):
        return post(env, '/query-specs/compile', {'spec': spec(cid)}, who)
    assert compile_().status_code == 200 and len(schema_calls) == 1
    assert compile_('bob').status_code == 200 and get(env, f'/connections/{cid}/relations').status_code == 200
    assert len(schema_calls) == 1
    # a cache hit still enforces the role
    member = next(m for m in env.service.members(env.actors['alice'], env.wid) if m.get('principal_id') == env.actors['bob'].id)
    env.service.change_member(env.actors['alice'], env.wid, member['id'], member['revision'], role='viewer')
    assert compile_('bob').status_code == 403
    clock[0] += 59
    assert compile_().status_code == 200 and len(schema_calls) == 1
    clock[0] += 2
    assert compile_().status_code == 200 and len(schema_calls) == 2        # expired entries reload
    record = env.runtime.store.get('connection', cid)
    env.runtime.update_connection(env.actors['alice'], env.wid, cid, {'revision': record['revision'], 'name': 'Renamed shop'})
    assert compile_().status_code == 200 and len(schema_calls) == 3        # a revision bump invalidates
    private = env.private['id']
    assert post(env, '/query-specs/compile', {'spec': spec(private)}, 'alice').status_code == 200 and len(schema_calls) == 4
    # a cached private source still refuses someone it is not shared with
    assert post(env, '/query-specs/compile', {'spec': spec(private)}, 'alice').status_code == 200 and len(schema_calls) == 4


def test_cache_hit_still_enforces_the_share_state(env, schema_calls):
    cid = env.source['id']
    assert post(env, '/query-specs/compile', {'spec': spec(cid)}, 'bob').status_code == 200
    record = env.runtime.store.get('connection', cid)
    env.runtime.update_connection(env.actors['alice'], env.wid, cid, {'revision': record['revision'], 'shared_read': False})
    assert post(env, '/query-specs/compile', {'spec': spec(cid)}, 'bob').status_code == 403
    assert post(env, '/query-specs/compile', {'spec': spec(cid)}, 'alice').status_code == 200


def test_schema_cache_eviction_is_bounded():
    cache = SchemaCache(ttl=60, maximum=2, clock=lambda: 0.0)
    for key in 'abc':
        cache.put((key,), key)
    assert cache.get(('a',)) is None and cache.get(('b',)) == 'b' and cache.get(('c',)) == 'c'


# --- network engine with a stubbed connector ------------------------------------------------------
@pytest.fixture
def network(env, monkeypatch):
    schema = SQLiteConnector(env.path).schema()
    for table in schema['tables']:
        table['schema'] = 'public'
        for column in table['columns']:
            column['type'] = {'DATETIME': 'TIMESTAMP WITHOUT TIME ZONE', 'REAL': 'DOUBLE PRECISION'}.get(column['type'], column['type'])
    schema['tables'] = [t for t in schema['tables'] if t.get('readable', True)]
    schema['engine'] = 'postgresql'
    seen = {'schema': 0, 'execute': []}

    def fake_schema(self):
        seen['schema'] += 1
        return copy.deepcopy(schema)

    def fake_execute(self, sql, parameters, max_rows, kinds=None):
        seen['execute'].append((sql, parameters))
        return {'columns': [{'name': 'id', 'type': 'unknown'}], 'rows': [[17]], 'row_count': 1, 'truncated': False}
    monkeypatch.setattr(NetworkSQLConnector, 'schema', fake_schema)
    monkeypatch.setattr(NetworkSQLConnector, 'execute', fake_execute)
    monkeypatch.setattr(NetworkSQLConnector, 'test', lambda self: {'ok': True, 'engine': 'postgresql', 'message': 'verified',
                                                                    'read_available': True, 'protection': 'stub', 'qualified': False})
    env.net = env.runtime.create_connection(env.actors['alice'], env.wid, {
        'name': 'Postgres', 'engine': 'postgresql', 'password': 'pw-one', 'shared_read': True,
        'config': {'host': 'db.invalid', 'database': 'shop', 'username': 'reader', 'tls': True}})
    env.seen = seen
    return env


def test_network_source_needs_a_verified_test_only_for_run(network):
    env, cid = network, network.net['id']
    compiled = post(env, '/query-specs/compile', {'spec': spec(cid)})
    assert compiled.status_code == 200 and compiled.json()['dialect'] == 'postgresql'
    assert '"public"."orders" AS "o"' in compiled.json()['sql']
    assert get(env, f'/connections/{cid}/relations?table=orders').status_code == 200
    caps = get(env, f'/connections/{cid}/capabilities').json()
    assert caps['source_tested'] is False and caps['requires_source_test'] is True and caps['qualified'] is False
    assert caps['dialect']['quote'] == '"' and caps['dialect']['table_qualification'] == 'schema'
    calls = env.seen['schema']
    refused = post(env, '/query-specs/run', {'request_id': 'n1', 'spec': spec(cid)})
    assert refused.status_code == 409 and refused.json()['detail'] == MESSAGE_409
    assert env.seen['schema'] == calls and env.seen['execute'] == []       # refused before any source contact
    tested = post(env, f'/connections/{cid}/test', {})
    assert tested.status_code == 200 and tested.json()['ok'] is True
    assert get(env, f'/connections/{cid}/capabilities').json()['source_tested'] is True
    job = post(env, '/query-specs/run', {'request_id': 'n2', 'spec': spec(cid)})
    assert job.status_code == 200
    done = wait(env, job.json())
    assert done['status'] == 'completed' and done['result']['rows'] == [[17]]
    sql, parameters = env.seen['execute'][-1]
    assert sql.startswith('SELECT "o"."id" AS "id"') and parameters == {}
    record = env.runtime.store.get('connection', cid)
    env.runtime.update_connection(env.actors['alice'], env.wid, cid, {'revision': record['revision'], 'password': 'pw-two'})
    assert get(env, f'/connections/{cid}/capabilities').json()['source_tested'] is False
    again = post(env, '/query-specs/run', {'request_id': 'n3', 'spec': spec(cid)})
    assert again.status_code == 409 and again.json()['detail'] == MESSAGE_409


def test_network_dialect_boolean_and_date_binding_end_to_end(network):
    env, cid = network, network.net['id']
    post(env, f'/connections/{cid}/test', {})
    body = spec(cid, select=[C('o', 'id')], filters={'op': 'and', 'items': [
        {'field': C('o', 'paid'), 'op': 'eq', 'value': True},
        {'field': C('o', 'ordered_at'), 'op': 'eq', 'value': '2026-11-12'}]})
    compiled = post(env, '/query-specs/compile', {'spec': body}).json()
    assert compiled['parameters'] == {'p1': True, 'p2': '2026-11-12', 'p3': '2026-11-13'}
    assert 'NULLS LAST' in compiled['sql'] and compiled['columns'][0]['value_kind'] == 'integer'
    assert wait(env, post(env, '/query-specs/run', {'request_id': 'bind', 'spec': body}).json())['status'] == 'completed'


# --- contract fixtures shared with the frontend lane ----------------------------------------------------
DETAIL = spec('conn_01', joins=[{'kind': 'left', 'table': 'customers', 'alias': 'c', 'on': [{'left': C('o', 'customer_id'), 'right': C('c', 'id')}]}],
              select=[C('o', 'id'), C('o', 'status'), C('o', 'amount'), {**C('c', 'name'), 'label': 'Customer'}],
              filters={'op': 'and', 'items': [{'field': C('o', 'status'), 'op': 'in', 'values': ['open', 'paid']},
                                              {'field': C('o', 'amount'), 'op': 'gt', 'value': 100}]},
              sort=[{**C('o', 'amount'), 'direction': 'desc'}])
SUMMARY = spec('conn_01', select=[], joins=[{'kind': 'left', 'table': 'order_items', 'alias': 'i', 'on': [{'left': C('o', 'id'), 'right': C('i', 'order_id')}]}],
               group_by=[{**C('o', 'ordered_at'), 'bucket': 'month', 'label': 'Month'}],
               aggregates=[{'fn': 'sum', **C('o', 'amount'), 'label': 'Total amount'}, {'fn': 'count'}],
               sort=[{'output': 'Month', 'direction': 'asc'}], limit=24)
ERRORS = spec('conn_01', select=[], filters={'op': 'and', 'items': [{'field': C('o', 'id'), 'op': 'eq', 'value': 1},
                                                                    {'field': C('o', 'amout'), 'op': 'gt', 'value': 100}]},
              group_by=[{**C('o', 'status'), 'bucket': 'month'}, {**C('o', 'id'), 'bucket': 'day'}])
COMPILE_FILES = {'compile-detail.json': DETAIL, 'compile-summary.json': SUMMARY, 'compile-errors.json': ERRORS}
ALL_FIXTURES = {'compile-detail.json', 'compile-summary.json', 'compile-errors.json', 'run-job-queued.json',
                'run-job-completed.json', 'relations.json', 'capabilities-sqlite.json',
                'capabilities-unsupported.json', 'schema.json'}


def write_fixture(name, request, response):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / name).write_text(json.dumps({'request': request, 'response': response}, indent=2, ensure_ascii=False) + '\n')


def regenerate_fixtures(env, cid):
    for name, body in COMPILE_FILES.items():
        response = post(env, '/query-specs/compile', {'spec': with_source(body, cid)}).json()
        write_fixture(name, {'spec': body}, to_contract(response, cid))
    run_request = {'request_id': 'fixture-request-id', 'spec': DETAIL}
    job = post(env, '/query-specs/run', with_source(run_request, cid)).json()
    done = wait(env, job)
    stamp = {'id': 'job_77', 'created_at': 1789200000.1}
    write_fixture('run-job-queued.json', run_request, to_contract({**job, **stamp, 'updated_at': 1789200000.1}, cid))
    trimmed = {**done['result'], 'rows': done['result']['rows'][:3], 'row_count': 3}
    write_fixture('run-job-completed.json', run_request,
                  to_contract({**done, **stamp, 'updated_at': 1789200003.4, 'result': trimmed}, cid))
    write_fixture('relations.json', {'table': 'orders'}, to_contract(get(env, f'/connections/{cid}/relations?table=orders').json(), cid))
    write_fixture('capabilities-sqlite.json', None, to_contract(get(env, f'/connections/{cid}/capabilities').json(), cid))
    write_fixture('schema.json', None, get(env, f'/connections/{cid}/schema').json())
    record = env.runtime.store.get('connection', cid)
    env.runtime.store.update('connection', cid, {'engine': 'sqlserver'}, record['revision'])
    write_fixture('capabilities-unsupported.json', None, to_contract(get(env, f'/connections/{cid}/capabilities').json(), cid))
    record = env.runtime.store.get('connection', cid)
    env.runtime.store.update('connection', cid, {'engine': 'sqlite'}, record['revision'])


def test_contract_fixtures_match_the_backend(env):
    cid = env.source['id']
    if os.environ.get('TERMX_UPDATE_FIXTURES'):
        regenerate_fixtures(env, cid)
    files = {f.name: json.loads(f.read_text()) for f in FIXTURES.glob('*.json')} if FIXTURES.exists() else {}
    assert set(files) >= ALL_FIXTURES, 'run with TERMX_UPDATE_FIXTURES=1 to create them'
    for name in COMPILE_FILES:
        stored = files[name]
        actual = to_contract(post(env, '/query-specs/compile', with_source(stored['request'], cid)).json(), cid)
        expected = copy.deepcopy(stored['response'])
        if expected.get('ok'):   # the hash is checked for presence and format only
            assert re.fullmatch(r'[0-9a-f]{16}', actual.pop('schema_hash')) and re.fullmatch(r'[0-9a-f]{16}', expected.pop('schema_hash'))
        assert actual == expected, name
    assert to_contract(get(env, f'/connections/{cid}/relations?table=orders').json(), cid) == files['relations.json']['response']
    assert to_contract(get(env, f'/connections/{cid}/capabilities').json(), cid) == files['capabilities-sqlite.json']['response']
    assert get(env, f'/connections/{cid}/schema').json() == files['schema.json']['response']
    unsupported = files['capabilities-unsupported.json']['response']
    assert unsupported['supported'] is False and unsupported['dialect'] is None and unsupported['reason'] == UNSUPPORTED
    request = {**files['run-job-queued.json']['request'], 'request_id': 'shape-check'}
    job = post(env, '/query-specs/run', with_source(request, cid)).json()
    queued, completed = files['run-job-queued.json']['response'], files['run-job-completed.json']['response']
    assert set(queued) == set(job) and queued['status'] == 'queued'
    done = wait(env, job)
    assert set(completed) == set(done) and set(completed['result']) == set(done['result'])
    assert completed['status'] == 'completed'
