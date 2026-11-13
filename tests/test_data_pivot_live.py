"""Live pivot and segment conformance on PostgreSQL, MySQL and MariaDB (env gated, skipped when unset).

Containers (not started by CI). Start them, run, then remove them:

    docker run -d --rm --name termx-test-pg -e POSTGRES_PASSWORD=termx -e POSTGRES_USER=termx \\
        -e POSTGRES_DB=termx -p 55432:5432 postgres:16
    docker run -d --rm --name termx-test-mysql -e MYSQL_ROOT_PASSWORD=termx -e MYSQL_DATABASE=termx \\
        -p 53306:3306 mysql:8.4
    docker run -d --rm --name termx-test-mariadb -e MARIADB_ROOT_PASSWORD=termx -e MARIADB_DATABASE=termx \\
        -p 53307:3306 mariadb:10.11

    TERMX_TEST_PG_URL=postgresql+psycopg://termx:termx@127.0.0.1:55432/termx \\
    TERMX_TEST_MYSQL_URL=mysql+pymysql://root:termx@127.0.0.1:53306/termx \\
    TERMX_TEST_MARIADB_URL=mysql+pymysql://root:termx@127.0.0.1:53307/termx \\
    uv run pytest tests/test_data_pivot_live.py -q

    docker rm -f termx-test-pg termx-test-mysql termx-test-mariadb

Each run loads the `sales` fixture into a uniquely named schema (PostgreSQL) or database (MySQL, MariaDB) and drops it
afterwards. The same cases as the SQLite suite run against the same Python oracle: every cell, every total, the column
order, the empty column, hostile values, bucket labels, case-variant text and decimal results.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import uuid

import pytest

from data_pivot_fixture import build_sales, oracle_pivot, sort_rows
from test_data_conformance import LIVE_URLS, index_for, live_raw, structural_checks
from test_data_pivot import CASES, CORPUS, F, AND, OR, P, S, C, month
from test_data_segments import AND as SAND, BIG, EAST, F as SF, REF, S as SSPEC, seg
from termx.data.connectors import NetworkSQLConnector, typed_scalar
from termx.data.dialects import get_dialect
from termx.data.pivot import pivot_values_from_rows
from termx.data.query_spec import QuerySpec, SpecErrors, compile_pivot_probe, compile_spec

DDL = {
    'postgresql': {
        'customers': 'CREATE TABLE {s}.customers(id integer PRIMARY KEY, name varchar(100), segment varchar(20))',
        'sales': 'CREATE TABLE {s}.sales(id integer PRIMARY KEY, region varchar(50), amount integer, price double precision, day date, '
                 'stamp timestamp, flag boolean, tier varchar(10), tag text, qty integer, customer_id integer REFERENCES {s}.customers(id), note text)'},
    'mysql': {
        'customers': 'CREATE TABLE {s}.customers(id int PRIMARY KEY, name varchar(100), segment varchar(20))',
        'sales': 'CREATE TABLE {s}.sales(id int PRIMARY KEY, region varchar(50), amount int, price double, day date, stamp datetime, '
                 'flag boolean, tier varchar(10), tag text, qty int, customer_id int, note text, '
                 'FOREIGN KEY(customer_id) REFERENCES {s}.customers(id))'},
}
DDL['mariadb'] = DDL['mysql']


@contextlib.contextmanager
def sales_database(engine_name):
    url = os.environ.get(LIVE_URLS[engine_name])
    if not url:
        pytest.skip(f'{LIVE_URLS[engine_name]} is not set')
    from sqlalchemy import create_engine, text
    source = create_engine(url)
    name = 'termx_p_' + uuid.uuid4().hex[:12]
    quoted = f'"{name}"' if engine_name == 'postgresql' else f'`{name}`'
    rows = build_sales()
    with source.connect() as conn:
        try:
            conn.execute(text(f'CREATE SCHEMA {quoted}' if engine_name == 'postgresql' else f'CREATE DATABASE {quoted}'))
            for table in ('customers', 'sales'):
                conn.execute(text(DDL[engine_name][table].format(s=quoted)))
                names = list(rows[table][0])
                conn.execute(text(f'INSERT INTO {quoted}.{table}({",".join(names)}) VALUES({",".join(":" + n for n in names)})'), rows[table])
            conn.commit()
            if engine_name == 'mariadb':
                conn.exec_driver_sql("SET SESSION sql_mode = CONCAT(@@sql_mode, ',ONLY_FULL_GROUP_BY')")
            if engine_name != 'postgresql':
                assert 'ONLY_FULL_GROUP_BY' in conn.exec_driver_sql('SELECT @@sql_mode').scalar()
            yield type('Live', (), {'conn': conn, 'name': name, 'rows': rows, 'engine': engine_name})
        finally:
            conn.rollback()
            conn = conn.execution_options(stream_results=False)
            conn.execute(text(f'DROP SCHEMA IF EXISTS {quoted} CASCADE' if engine_name == 'postgresql' else f'DROP DATABASE IF EXISTS {quoted}'))
            conn.commit()
    source.dispose()


def index(live):
    return index_for(live_raw(live), live.engine, live.name)


def fetch(live, sql, parameters, kinds):
    from sqlalchemy import text
    return [[typed_scalar(cell, kind) for cell, kind in zip(row, kinds)] for row in live.conn.execute(text(sql), parameters).fetchall()]


def run_pivot(live, body, segments=None):
    spec, idx, d = QuerySpec.model_validate(body), index(live), get_dialect(live.engine)
    compile_spec(spec, idx, d, segments=segments)
    values = None
    if spec.pivot.columns is not None:
        probe = compile_pivot_probe(spec, idx, d, segments=segments)
        structural_checks(probe, live.engine)
        kind = probe.columns[0]['value_kind']
        values = pivot_values_from_rows(fetch(live, probe.sql, probe.parameters, [kind]), kind, 'Column')
    compiled = compile_spec(spec, idx, d, segments=segments, pivot_values=values)
    structural_checks(compiled, live.engine)
    return compiled, fetch(live, compiled.sql, compiled.parameters, [c['value_kind'] for c in compiled.columns]), values


def check_case(live, name, body, row_dims, column_dim, fn, measure):
    compiled, rows, values = run_pivot(live, body)
    oracle_columns, oracle = oracle_pivot(live.rows['sales'], row_dims, column_dim, fn, measure)
    width = len(row_dims)
    cells = [c for c in compiled.columns if c['role'] == 'pivot_cell']
    assert [(c['column_value'], c['column_empty']) for c in cells] == [(v, v is None) for v in oracle_columns], name     # same order on every engine
    got = {tuple(r[:width]): r[width:] for r in rows}
    assert len(got) == len(rows) == len(oracle), (name, sorted(map(str, got)), sorted(map(str, oracle)))
    assert set(got) == set(oracle), name
    for key, cells_got in got.items():
        expected = [oracle[key][v] for v in oracle_columns] + ([oracle[key]['total']] if compiled.columns[-1]['role'] == 'pivot_total' else [])
        assert len(cells_got) == len(expected)
        for have, want in zip(cells_got, expected):
            if isinstance(want, float) or isinstance(have, float):
                assert have == pytest.approx(want), (name, key, cells_got, expected)
            else:
                assert have == want and type(have) is type(want), (name, key, cells_got, expected)
    text_rows = any(getattr(d, '__name__', '') == 'region' for d in row_dims)
    if not text_rows:                              # text order follows each engine's collation; everything else is exact
        assert [tuple(r[:width]) for r in rows] == sort_rows(oracle), name


def live_suite(engine):
    with sales_database(engine) as live:
        failures = []
        for name, body, row_dims, column_dim, fn, measure in CASES:
            try:
                check_case(live, name, body, row_dims, column_dim, fn, measure)
            except Exception as error:   # noqa: BLE001
                live.conn.rollback()
                failures.append(f'{name}: {type(error).__name__}: {str(error)[:400]}')
        assert not failures, f'{len(failures)} of {len(CASES)} pivots failed on {engine}:\n' + '\n'.join(failures)
        check_static_corpus(live)
        check_probe_limits(live)
        check_exact_text(live)
        check_hostile_values(live)
        check_segments(live)
        check_network_path(live)


def check_static_corpus(live):
    """Every corpus pivot (filters, joins, paging, sorting by total) compiles and executes without a server error."""
    for name, body in CORPUS.items():
        try:
            compiled, rows, _ = run_pivot(live, body)
            assert rows is not None
        except SpecErrors as error:
            raise AssertionError(f'{name}: {error.errors[0].message}') from None


def check_probe_limits(live):
    twelve = S(P(['region'], 'tier'), filters=OR(AND(F('id', 'lte', value=11)), F('id', 'eq', value=100)))
    compiled, rows, values = run_pivot(live, twelve)
    assert len(values) == 12 and values[-1].empty
    thirteen = S(P(['region'], 'tier'), filters=OR(AND(F('id', 'lte', value=12)), F('id', 'eq', value=100)))
    with pytest.raises(SpecErrors) as error:
        run_pivot(live, thirteen)
    assert error.value.errors[0].code == 'pivot_too_many_columns'


def check_exact_text(live):
    """East / east / (empty) / '' and NULL are five separate rows with the right counts."""
    compiled, rows, _ = run_pivot(live, S(P(['region'])))
    counts = {r[0]: r[1] for r in rows}
    expected = {}
    for r in live.rows['sales']:
        expected[r['region']] = expected.get(r['region'], 0) + 1
    assert counts == expected and {'East', 'east', '(empty)', '', None} <= set(counts)


def check_hostile_values(live):
    compiled, rows, values = run_pivot(live, S(P([month()], 'tag', fn='count')))
    hostile = {v.value for v in values if not v.empty}
    assert "Robert'); DROP TABLE sales;--" in hostile and 'x' * 200 in hostile and '' in hostile
    from sqlalchemy import text
    assert live.conn.execute(text(f'SELECT COUNT(*) FROM {get_dialect(live.engine).quote(live.name)}.sales')).scalar() == 120


def segment_ids(live, body, known):
    spec, idx, d = QuerySpec.model_validate(body), index(live), get_dialect(live.engine)
    compiled = compile_spec(spec, idx, d, segments={s.id: s for s in known})
    structural_checks(compiled, live.engine)
    return {r[0] for r in fetch(live, compiled.sql, compiled.parameters, [c['value_kind'] for c in compiled.columns])}


def check_segments(live):
    sales = live.rows['sales']
    everyone = {r['id'] for r in sales}
    inside = segment_ids(live, SSPEC(SAND(REF('big'))), [BIG])
    outside = segment_ids(live, SSPEC(SAND(REF('big', True))), [BIG])
    assert inside == {r['id'] for r in sales if r['amount'] is not None and r['amount'] > 100}
    assert inside | outside == everyone and not inside & outside          # NULL amounts land in the complement
    inner = seg('inner', SAND(SF('qty', 'in', values=[1, 2]), SF('region', 'eq', value='East')))
    outer = seg('outer', SAND(REF('inner'), SF('amount', 'gt', value=5)))
    nested = segment_ids(live, SSPEC(SAND(SF('price', 'lt', value=9.5), REF('outer'))), [inner, outer])
    assert nested == {r['id'] for r in sales if r['price'] is not None and r['price'] < 9.5 and r['qty'] in (1, 2)
                      and r['region'] == 'East' and r['amount'] is not None and r['amount'] > 5}
    either = seg('either', SAND(SF('region', 'eq', value='East')))
    mixed = segment_ids(live, SSPEC(SAND(REF('big'), REF('east', True))), [BIG, EAST])
    assert mixed == {r['id'] for r in sales if (r['amount'] or 0) > 100 and r['region'] != 'East'} and either


def check_network_path(live):
    """The pivot statements also pass NetworkSQLConnector.execute's own read-only and base-table checks."""
    from sqlalchemy import text  # noqa: F401
    config = {'database': live.name, 'schema': live.name, 'host': 'x', 'port': 1, 'username': 'x'}
    connector = NetworkSQLConnector(live.engine, config, 'unused')

    @contextlib.contextmanager
    def connection(self):
        yield live.conn
    original = NetworkSQLConnector.connection
    NetworkSQLConnector.connection = connection
    try:
        for name in ('text_by_month', 'two_row_dims', 'sorted_by_total', 'with_join'):
            compiled, _, _ = run_pivot(live, CORPUS[name])
            result = connector.execute(compiled.sql, compiled.parameters, 1000)
            assert result['row_count'] > 0 and [c['name'] for c in result['columns']] == [c['alias'] for c in compiled.columns]
    finally:
        NetworkSQLConnector.connection = original


def test_postgresql_live_pivots_and_segments():
    live_suite('postgresql')


def test_mysql_live_pivots_and_segments():
    live_suite('mysql')


def test_mariadb_live_pivots_and_segments():
    live_suite('mariadb')
