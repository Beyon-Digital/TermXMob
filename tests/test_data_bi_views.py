"""Reports whose dataset is a saved view (table, pivot or segment), on every engine.

SQLite runs end to end through the HTTP routes and the real runtime. PostgreSQL, MySQL and MariaDB compile the same
wrapped statement (a view's dataset statement inside a derived table, then the report visual) and run it on live
servers when TERMX_TEST_PG_URL, TERMX_TEST_MYSQL_URL and TERMX_TEST_MARIADB_URL are set.
"""
from __future__ import annotations

import collections
from copy import deepcopy

import pytest
from sqlalchemy import text

from data_pivot_fixture import build_sales, create_sales_sqlite
from test_data_conformance import index_for, live_raw
from test_data_pivot_live import sales_database
from test_data_views import (AND, C, F, REF, auth, call, env, finished as view_finished, make_view, month, pivot_spec,  # noqa: F401
                             run_threads, segment, spec, table_view, wait)
from termx.data.bi import compile_visual
from termx.data.connectors import SQLiteConnector
from termx.data.dialects import get_dialect
from termx.data.query_spec import QuerySpec, compile_spec
from termx.data.store import DataStore


def report_body(view, **changes):
    body = {'name': 'On a view', 'view_id': view['id'], 'visual': {'kind': 'bar', 'x': 'region', 'y': 'amount', 'aggregate': 'sum'},
            'filters': []}
    body.update(changes)
    return body


def make_report(env, view, who='alice', status=200, **changes):
    response = call(env, 'POST', '/reports', who, report_body(view, **changes))
    assert response.status_code in (200, 201), response.text
    return response.json()


def run_report(env, rid, who='alice', request_id=None):
    response = call(env, 'POST', f'/reports/{rid}/run', who, {'request_id': request_id or f'rr-{who}-{rid}-{len(env.runs)}'})
    env.runs.append(response)
    assert response.status_code == 200, response.text
    done = wait(env, response.json(), who)
    assert done['status'] == 'completed', done
    return done


@pytest.fixture
def bi_env(env):
    env.runs = []
    return env


def sums(rows):
    totals = collections.defaultdict(float)
    for row in rows:
        if row['amount'] is not None:
            totals[row['region']] += row['amount']
    return dict(totals)


def test_table_view_dataset_aggregates_every_row_not_the_views_page(bi_env):
    env = bi_env
    view = table_view(env, 'Paged sales', limit=50, offset=10)      # a page of 50; the report must see all 120 rows
    report = make_report(env, view)
    done = run_report(env, report['id'])
    got = {row[0]: row[1] for row in done['result']['rows']}
    assert got == sums(env.sales_rows['sales'])
    assert done['report_context']['dataset_view_id'] == view['id'] and 'dataset_query_id' not in done['report_context']
    count = make_report(env, view, name='How many', visual={'kind': 'metric', 'aggregate': 'count'})
    assert run_report(env, count['id'])['result']['rows'] == [[len(env.sales_rows['sales'])]]


def test_in_and_not_in_filters_on_a_view_dataset(bi_env):
    env = bi_env
    view = table_view(env, 'Filterable')
    rows = env.sales_rows['sales']
    for operator, keep in (('in', lambda r: r['region'] in ('East', 'West')),
                           ('not_in', lambda r: r['region'] not in ('East', 'West'))):
        report = make_report(env, view, name=operator, visual={'kind': 'metric', 'aggregate': 'count'},
                             filters=[{'field': 'region', 'operator': operator, 'value': ['East', 'West']}])
        expected = len([r for r in rows if keep(r) and r['region'] is not None])   # empty values match neither list
        assert run_report(env, report['id'])['result']['rows'] == [[expected]], operator


def test_pivot_view_dataset_uses_its_cells_and_totals(bi_env):
    env = bi_env
    view = make_view(env, 'pivot', 'Region by month', pivot_spec(env.cid, ['region'], month(), fn='count'))
    fields = call(env, 'GET', f'/report-datasets/views/{view["id"]}/fields').json()['fields']
    assert [f['name'] for f in fields][0] == 'pv_r1' and fields[-1]['name'] == 'pv_total' and fields[0]['label'] == 'region'
    report = make_report(env, view, name='Rows', visual={'kind': 'metric', 'y': 'pv_total', 'aggregate': 'sum'})
    assert run_report(env, report['id'])['result']['rows'] == [[len(env.sales_rows['sales'])]]
    chart = make_report(env, view, name='Chart', visual={'kind': 'bar', 'x': 'pv_r1', 'y': 'pv_total', 'aggregate': 'sum'})
    done = run_report(env, chart['id'])
    expected = collections.Counter(r['region'] for r in env.sales_rows['sales'])
    assert {row[0]: row[1] for row in done['result']['rows']} == dict(expected)
    assert [c['name'] for c in done['result']['columns']] == ['pv_r1', 'pv_total']
    assert [(c['alias'], c['label']) for c in done['output']['columns']] == [
        ('pv_r1', fields[0]['label']), ('pv_total', fields[-1]['label'])]
    assert done['report_context']['visual']['x'] == 'pv_r1' and done['report_context']['visual']['y'] == 'pv_total'


@pytest.mark.parametrize('visual,names,labels', [
    ({'kind': 'table'}, ['pv_r1', 'pv_total'], ['Sales region', 'Revenue']),
    ({'kind': 'scatter', 'x': 'pv_total', 'y': 'pv_r1'}, ['pv_total', 'pv_r1'], ['Revenue', 'Sales region']),
    ({'kind': 'metric', 'y': 'pv_total', 'aggregate': 'sum'}, ['pv_total'], ['Revenue']),
    ({'kind': 'bar', 'x': 'pv_r1', 'aggregate': 'count'}, ['pv_r1', 'count'], ['Sales region', 'Count']),
])
def test_report_output_labels_follow_the_visual_projection(bi_env, visual, names, labels):
    env = bi_env
    body = pivot_spec(env.cid, [{**C('s', 'region'), 'label': 'Sales region'}], fn='sum', measure='amount')
    body['pivot']['value']['label'] = 'Revenue'
    view = make_view(env, 'pivot', 'Friendly fields', body)
    report = make_report(env, view, visual=visual)
    done = run_report(env, report['id'])
    assert [c['name'] for c in done['result']['columns']] == names
    assert [c['alias'] for c in done['output']['columns']] == names
    assert [c['label'] for c in done['output']['columns']] == labels
    if visual.get('aggregate') == 'count':
        assert done['output']['columns'][-1]['value_kind'] == 'integer'
    if visual['kind'] == 'table':
        exported = call(env, 'GET', f'/jobs/{done["id"]}/export.csv')
        assert exported.status_code == 200, exported.text
        assert exported.text.splitlines()[0] == 'Sales region,Revenue'


def test_published_labels_remain_pinned_after_the_view_changes_and_is_archived(bi_env):
    env = bi_env
    body = pivot_spec(env.cid, [{**C('s', 'region'), 'label': 'Published region'}], fn='sum', measure='amount')
    body['pivot']['value']['label'] = 'Published revenue'
    view = make_view(env, 'pivot', 'Changing labels', body)
    report = make_report(env, view, visual={'kind': 'bar', 'x': 'pv_r1', 'y': 'pv_total', 'aggregate': 'sum'})
    published = call(env, 'POST', f'/reports/{report["id"]}/publish', body={'revision': report['revision']}).json()
    body['pivot']['rows'][0]['label'] = 'Draft region'
    body['pivot']['value']['label'] = 'Draft revenue'
    changed = call(env, 'PATCH', f'/views/{view["id"]}', body={'revision': view['revision'], 'spec': body})
    assert changed.status_code == 200, changed.text
    live = run_report(env, report['id'])
    assert [c['label'] for c in live['output']['columns']] == ['Draft region', 'Draft revenue']
    for archived in (False, True):
        if archived:
            assert call(env, 'DELETE', f'/views/{view["id"]}?revision={changed.json()["revision"]}').status_code == 200
        for who in (('alice', 'bob', 'vera') if archived else ('vera',)):
            done = run_report(env, report['id'], who)
            assert [c['alias'] for c in done['output']['columns']] == ['pv_r1', 'pv_total']
            assert [c['label'] for c in done['output']['columns']] == ['Published region', 'Published revenue']
            assert done['report_context']['report_version_id'] == published['published_version_id']


def test_segment_view_dataset(bi_env):
    env = bi_env
    big = segment(env, 'Big', F('amount', 'gt', value=100))
    report = make_report(env, big, name='Big count', visual={'kind': 'metric', 'aggregate': 'count'})
    expected = len([r for r in env.sales_rows['sales'] if r['amount'] is not None and r['amount'] > 100])
    assert run_report(env, report['id'])['result']['rows'] == [[expected]]


def test_publishing_pins_the_view_and_its_segments_for_viewers(bi_env):
    env = bi_env
    seg = segment(env, 'Big', F('amount', 'gt', value=100))
    view = table_view(env, 'Uses segment', filters=AND(REF(seg['id'])))
    report = make_report(env, view, name='Pinned', visual={'kind': 'metric', 'aggregate': 'count'})
    before = len([r for r in env.sales_rows['sales'] if r['amount'] is not None and r['amount'] > 100])
    published = call(env, 'POST', f'/reports/{report["id"]}/publish', 'alice', {'revision': report['revision']})
    assert published.status_code == 200, published.text
    assert call(env, 'GET', f'/reports/{report["id"]}/versions').json()[0]['definition']['view_id'] == view['id']
    # The segment and the view change afterwards. The publication keeps the old meaning.
    current = call(env, 'GET', '/views/' + seg['id']).json()
    changed = call(env, 'PATCH', '/views/' + seg['id'], 'alice',
                   {'revision': current['revision'], 'spec': spec(env.cid, select=[], sort=[], filters=AND(F('amount', 'gt', value=400)))})
    assert changed.status_code == 200, changed.text
    assert run_report(env, report['id'], 'vera')['result']['rows'] == [[before]]
    after = len([r for r in env.sales_rows['sales'] if r['amount'] is not None and r['amount'] > 400])
    assert run_report(env, report['id'], 'alice')['result']['rows'] == [[after]]
    shown = call(env, 'GET', f'/reports/{report["id"]}', 'vera').json()
    assert shown['published'] and shown['view_id'] == view['id'] and 'spec' not in shown


def test_archived_view_publication_survives_for_every_role_and_dashboard(bi_env):
    env = bi_env
    seg = segment(env, 'Pinned segment', F('amount', 'gt', value=100))
    view = table_view(env, 'Archived dataset', filters=AND(REF(seg['id'])))
    report = make_report(env, view, name='Pinned count', visual={'kind': 'metric', 'aggregate': 'count'})
    published = call(env, 'POST', f'/reports/{report["id"]}/publish', body={'revision': report['revision']}).json()
    version = deepcopy(env.service.store.get('report_version', published['published_version_id']))
    draft = call(env, 'PATCH', f'/reports/{report["id"]}', body={
        'revision': published['revision'], 'name': 'Unpublished sum',
        'visual': {'kind': 'metric', 'y': 'amount', 'aggregate': 'sum'},
        'filters': [{'field': 'amount', 'operator': 'gt', 'value': 400}]}).json()
    dashboard = call(env, 'POST', '/dashboards', body={'name': 'Pinned dashboard', 'report_ids': [report['id']]}).json()
    assert call(env, 'DELETE', f'/views/{view["id"]}?revision={view["revision"]}').status_code == 200
    assert call(env, 'DELETE', f'/views/{view["id"]}?revision={view["revision"]}').status_code == 404
    assert call(env, 'DELETE', f'/views/{seg["id"]}?revision={seg["revision"]}').status_code == 200
    assert call(env, 'GET', f'/views/{view["id"]}').status_code == 404

    # Opening existing metadata repeatedly needs no migration or mutation of the archived view or publication.
    DataStore(env.service.store.directory)
    DataStore(env.service.store.directory)
    expected = len([r for r in env.sales_rows['sales'] if r['amount'] is not None and r['amount'] > 100])
    jobs = {}
    for who in ('alice', 'bob', 'vera'):
        shown = call(env, 'GET', f'/reports/{report["id"]}', who)
        assert shown.status_code == 200, shown.text
        assert shown.json()['name'] == 'Pinned count' and shown.json()['revision'] == draft['revision']
        assert [r['id'] for r in call(env, 'GET', '/reports', who).json()] == [report['id']]
        assert call(env, 'GET', '/dashboards', who).json()[0]['id'] == dashboard['id']
        assert call(env, 'GET', f'/reports/{report["id"]}/versions', who).json()[0]['id'] == version['id']
        done = jobs[who] = run_report(env, report['id'], who, request_id=f'archived-{who}')
        assert done['result']['rows'] == [[expected]]
        assert done['report_context']['report_version_id'] == version['id']
        assert run_report(env, report['id'], who, request_id=f'archived-{who}')['id'] == done['id']
        tiles = call(env, 'POST', f'/dashboards/{dashboard["id"]}/run', who, {'request_id': f'dash-{who}'}).json()['tiles']
        assert 'error' not in tiles[0], tiles
        assert wait(env, tiles[0]['job'], who)['result']['rows'] == [[expected]]
    assert env.service.store.get('report_version', version['id']) == version
    assert env.service.store.get('report', report['id'])['revision'] == draft['revision']
    assert call(env, 'POST', f'/reports/{report["id"]}/publish', body={'revision': 1}).status_code == 409
    assert call(env, 'POST', f'/reports/{report["id"]}/publish', body={'revision': draft['revision']}).status_code == 404
    assert len(env.service.store.list('report_version', env.wid)) == 1

    current = env.service.store.get('connection', env.cid)
    env.runtime.update_connection(env.actors['alice'], env.wid, env.cid, {'revision': current['revision'], 'shared_read': False})
    for who in ('bob', 'vera'):
        assert call(env, 'GET', '/reports', who).json() == []
        assert call(env, 'GET', '/dashboards', who).json() == []
        assert call(env, 'POST', f'/reports/{report["id"]}/run', who, {'request_id': f'revoked-{who}'}).status_code == 403
        assert call(env, 'GET', f'/jobs/{jobs[who]["id"]}', who).status_code == 403
    assert run_report(env, report['id'], 'alice')['result']['rows'] == [[expected]]


def test_archived_report_can_be_rebound_without_running_an_unrelated_publication(bi_env):
    env = bi_env
    first, second = table_view(env, 'First source'), table_view(env, 'Second source')
    report = make_report(env, first)
    published = call(env, 'POST', f'/reports/{report["id"]}/publish', body={'revision': report['revision']}).json()
    draft = call(env, 'PATCH', f'/reports/{report["id"]}', body={'revision': published['revision'], 'view_id': second['id']}).json()
    blocked = call(env, 'DELETE', f'/views/{second["id"]}?revision={second["revision"]}')
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'view_in_use'
    # A pre-upgrade archive can still be repaired; no rewrite or silent draft snapshot is needed on opening it.
    env.service.store.archive('view', second['id'], second['revision'])
    DataStore(env.service.store.directory)
    assert call(env, 'POST', f'/reports/{report["id"]}/run', body={'request_id': 'unrelated-publication'}).status_code == 404
    assert call(env, 'PATCH', f'/reports/{report["id"]}', body={'revision': 1, 'view_id': first['id']}).status_code == 409
    repaired = call(env, 'PATCH', f'/reports/{report["id"]}', body={'revision': draft['revision'], 'view_id': first['id']})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()['revision'] == draft['revision'] + 1
    assert run_report(env, report['id'])['report_context']['report_version_id'] is None
    assert call(env, 'DELETE', f'/views/{first["id"]}?revision={first["revision"]}').status_code == 200
    assert call(env, 'DELETE', f'/reports/{report["id"]}', body={'revision': 1}).status_code == 409
    archived = call(env, 'DELETE', f'/reports/{report["id"]}', body={'revision': repaired.json()['revision']})
    assert archived.status_code == 200 and archived.json()['archived']
    assert call(env, 'GET', '/reports', 'vera').json() == []


def test_unpublished_reports_block_view_archive_until_their_dependencies_are_pinned(bi_env):
    env = bi_env
    view = table_view(env, 'Report source')
    published_report = make_report(env, view, name='Already published')
    call(env, 'POST', f'/reports/{published_report["id"]}/publish', body={'revision': published_report['revision']})
    draft = make_report(env, view, name='Needs publication')
    for _ in range(2):
        blocked = call(env, 'DELETE', f'/views/{view["id"]}?revision={view["revision"]}')
        assert blocked.status_code == 409, blocked.text
        detail = blocked.json()['detail']
        assert detail['code'] == 'view_in_use' and 'Publish' in detail['message']
        assert detail['used_by'] == [{'id': draft['id'], 'kind': 'report', 'name': 'Needs publication'}]
        assert call(env, 'GET', f'/views/{view["id"]}').json()['revision'] == view['revision']
        assert run_report(env, draft['id'])['report_context']['report_version_id'] is None
    assert call(env, 'POST', f'/reports/{draft["id"]}/publish', body={'revision': draft['revision']}).status_code == 200
    assert call(env, 'DELETE', f'/views/{view["id"]}?revision={view["revision"]}').status_code == 200
    assert run_report(env, draft['id'])['report_context']['report_version_id'] is not None


@pytest.mark.parametrize('invalid', ['snapshot', 'definition', 'report', 'workspace'])
def test_archive_needs_a_publication_belonging_to_this_report_view_and_workspace(bi_env, invalid):
    env = bi_env
    view = table_view(env, 'Protected source')
    report = make_report(env, view)
    published = call(env, 'POST', f'/reports/{report["id"]}/publish', body={'revision': report['revision']}).json()
    version = env.service.store.get('report_version', published['published_version_id'])
    changes = {'snapshot': {'view_snapshot': {**version['view_snapshot'], 'id': 'another-view'}},
               'definition': {'definition': {**version['definition'], 'view_id': 'another-view'}},
               'report': {'report_id': 'another-report'},
               'workspace': {'view_snapshot': {**version['view_snapshot'], 'workspace_id': env.other_wid}}}[invalid]
    env.service.store.update('report_version', version['id'], changes, version['revision'])
    blocked = call(env, 'DELETE', f'/views/{view["id"]}?revision={view["revision"]}')
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'view_in_use'
    assert env.service.store.get('view', view['id'])['revision'] == view['revision']


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_view_archive_cannot_race_a_new_unpublished_report_dependency(bi_env, operation):
    env = bi_env
    for round_ in range(5):
        view = table_view(env, f'Race {operation} {round_}')
        if operation == 'create':
            save = lambda: env.service.bi.create_report(env.actors['alice'], env.wid, report_body(view))
        else:
            original = table_view(env, f'Original {round_}')
            report = make_report(env, original)
            save = lambda: env.service.bi.update_report(env.actors['alice'], env.wid, report['id'],
                                                        {'view_id': view['id']}, report['revision'])
        results = run_threads([lambda: env.service.views.archive(env.actors['alice'], env.wid, view['id'], view['revision']), save])
        assert sorted(kind for kind, _ in results) == ['error', 'ok'], results
        error = next(value for kind, value in results if kind == 'error')
        assert error.status_code in (404, 409)
        archived = env.service.store.get('view', view['id']).get('archived')
        references = [r for r in env.service.store.list('report', env.wid) if r.get('view_id') == view['id']]
        assert not (archived and references)


def test_only_editors_pick_views_and_private_sources_stay_private(bi_env):
    env = bi_env
    view = table_view(env, 'Shared')
    assert call(env, 'POST', '/reports', 'vera', report_body(view)).status_code == 403
    carol_view = make_view(env, 'table', 'Carol only', spec(env.carol_cid), who='carol')
    assert call(env, 'POST', '/reports', 'alice', report_body(carol_view)).status_code == 404


def test_a_report_has_exactly_one_dataset_and_can_switch(bi_env):
    env = bi_env
    view = table_view(env, 'Switch')
    neither = call(env, 'POST', '/reports', 'alice', {**report_body(view), 'view_id': None})
    assert neither.status_code == 422
    query = env.runtime.create_query(env.actors['alice'], env.wid, {'name': 'Saved', 'connection_id': env.cid,
                                                                 'sql': 'SELECT region, amount FROM sales'})
    both = call(env, 'POST', '/reports', 'alice', {**report_body(view), 'query_id': query['id']})
    assert both.status_code == 422
    report = make_report(env, view)
    switched = call(env, 'PATCH', f'/reports/{report["id"]}', 'alice', {'revision': report['revision'], 'query_id': query['id']})
    assert switched.status_code == 200 and switched.json()['view_id'] is None and switched.json()['query_id'] == query['id']
    done = run_report(env, report['id'])
    assert {row[0]: row[1] for row in done['result']['rows']} == sums(env.sales_rows['sales'])
    assert 'dataset_query_id' in done['report_context']
    back = call(env, 'PATCH', f'/reports/{report["id"]}', 'alice', {'revision': switched.json()['revision'], 'view_id': view['id']})
    assert back.status_code == 200 and back.json()['query_id'] is None


# --- every engine: the wrapped statement ------------------------------------------------------------------------

BODIES = {
    'table': lambda: spec('conn', limit=20, offset=5),
    'pivot': lambda: pivot_spec('conn', ['region'], month(), fn='count'),
}
VISUAL = {'kind': 'bar', 'x': 'region', 'y': 'amount', 'aggregate': 'sum'}


def wrapped(engine, index, body, visual, filters=()):
    dialect = get_dialect(engine)
    dataset = compile_spec(QuerySpec.model_validate(body), index, dialect, dataset=True)
    source = f'({dataset.sql}) AS {dialect.quote("_termx_dataset")}'
    columns = [{'name': c.get('alias') or c['label']} for c in dataset.columns]
    texts = {c['name'] for c, d in zip(columns, dataset.columns) if d['value_kind'] == 'text'}
    operand = lambda field: dialect.text_operand(dialect.quote(field)) if field in texts else dialect.quote(field)  # noqa: E731
    compiled = compile_visual({'visual': visual, 'filters': list(filters)}, source, columns, dialect.quote, dataset.parameters, operand)
    return dataset, compiled


@pytest.fixture(scope='module')
def sqlite_raw(tmp_path_factory):
    path = tmp_path_factory.mktemp('raw') / 's.sqlite3'
    create_sales_sqlite(path)
    return SQLiteConnector(path).schema()


@pytest.mark.parametrize('engine', ['sqlite', 'postgresql', 'mysql', 'mariadb'])
@pytest.mark.parametrize('kind', ['table', 'pivot'])
def test_dataset_statement_has_no_paging_or_order_on_every_engine(engine, kind, sqlite_raw):
    index = index_for(sqlite_raw, engine)
    body = BODIES[kind]()
    dataset = compile_spec(QuerySpec.model_validate(body), index, get_dialect(engine), dataset=True)
    upper = dataset.sql.upper()
    assert 'LIMIT' not in upper and 'FETCH ' not in upper and 'ORDER BY' not in upper
    assert 'LIMIT' in compile_spec(QuerySpec.model_validate(body), index, get_dialect(engine)).sql.upper()
    _, compiled = wrapped(engine, index, body, {'kind': 'metric', 'aggregate': 'count'})
    quote = get_dialect(engine).quote('_termx_dataset')
    assert compiled['sql'].count(quote) == 1 and compiled['sql'].startswith('SELECT COUNT(*)')


def fetch(conn, sql, parameters):
    return [list(row) for row in conn.execute(text(sql), parameters).fetchall()]


def expected_sums():
    return sums(build_sales()['sales'])


def check_view_report(engine, conn, index):
    """A paged table view as the dataset: totals cover every row, filters bind, in / not_in keep their meaning."""
    body = spec('conn', limit=20, offset=5)
    body['from'] = {'table': 'sales', 'alias': 's'}
    _, compiled = wrapped(engine, index, body, VISUAL)
    got = {row[0]: float(row[1]) for row in fetch(conn, compiled.get('sql'), compiled['parameters']) if row[0] is not None}
    want = {k: v for k, v in expected_sums().items() if k is not None}
    assert got == want, (engine, got, want)
    rows = build_sales()['sales']
    for operator, keep in (('in', lambda r: r['region'] in ('East', 'West')), ('not_in', lambda r: r['region'] not in ('East', 'West'))):
        _, counted = wrapped(engine, index, body, {'kind': 'metric', 'aggregate': 'count'},
                             [{'field': 'region', 'operator': operator, 'value': ['East', 'West']}])
        result = fetch(conn, counted['sql'], counted['parameters'])[0][0]
        want_count = len([r for r in rows if keep(r) and r['region'] is not None])
        assert int(result) == want_count, (engine, operator)
    pivot = pivot_spec('conn', ['region'], None, fn='count')
    dataset, over_pivot = wrapped(engine, index, pivot, {'kind': 'metric', 'y': 'pv_total', 'aggregate': 'sum'})
    assert int(fetch(conn, over_pivot['sql'], over_pivot['parameters'])[0][0]) == len(rows), engine
    assert 'ORDER BY' not in dataset.sql


def test_view_dataset_on_sqlite_engine(tmp_path, sqlite_raw):
    path = tmp_path / 's.sqlite3'
    create_sales_sqlite(path)
    from sqlalchemy import create_engine
    with create_engine(f'sqlite:///{path}').connect() as conn:
        check_view_report('sqlite', conn, index_for(sqlite_raw, 'sqlite'))


@pytest.mark.parametrize('name', ['postgresql', 'mysql', 'mariadb'])
def test_view_dataset_on_live_engines(name):
    with sales_database(name) as live:
        raw = live_raw(live)
        check_view_report(name, live.conn, index_for(raw, name, live.name))

