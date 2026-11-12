"""Reports whose dataset is a saved view (table, pivot or segment), on every engine.

SQLite runs end to end through the HTTP routes and the real runtime. PostgreSQL, MySQL and MariaDB compile the same
wrapped statement (a view's dataset statement inside a derived table, then the report visual) and run it on live
servers when TERMX_TEST_PG_URL, TERMX_TEST_MYSQL_URL and TERMX_TEST_MARIADB_URL are set.
"""
from __future__ import annotations

import collections

import pytest
from sqlalchemy import text

from data_pivot_fixture import build_sales, create_sales_sqlite
from test_data_conformance import index_for, live_raw
from test_data_pivot_live import sales_database
from test_data_views import (AND, C, F, REF, auth, call, env, finished as view_finished, make_view, month, pivot_spec,  # noqa: F401
                             segment, spec, table_view, wait)
from termx.data.bi import compile_visual
from termx.data.connectors import SQLiteConnector
from termx.data.dialects import get_dialect
from termx.data.query_spec import QuerySpec, compile_spec


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

