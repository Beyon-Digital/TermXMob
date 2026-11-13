"""Cross-engine parity: every adapter renders its own correct SQL for the same spec, and the same spec returns
the same JSON types on every engine. Unit tests run everywhere; live tests need TERMX_TEST_PG_URL,
TERMX_TEST_MYSQL_URL and TERMX_TEST_MARIADB_URL (see test_data_conformance.py) and skip otherwise.
"""
from __future__ import annotations

import decimal
import os

import pytest
from sqlalchemy import text

from termx.data.connectors import (STATEMENT_TIMEOUT_ENV, NetworkSQLConnector, SQLiteConnector, StatementTimeout,
                                   statement_timeout_seconds, typed_scalar)
from termx.data.dialects import get_dialect
from termx.data.query_spec import QuerySpec, SchemaIndex, SpecErrors, compile_spec

from test_data_conformance import (AND, C, F, S, compile_for, live_database, live_raw,  # noqa: F401  (fixtures)
                                   sqlite_shop)

ENGINES = ['sqlite', 'postgresql', 'mysql', 'mariadb']
LIVE = ['postgresql', 'mysql', 'mariadb']


def schema(columns, engine, table='t', primary=('id',)):
    """A one-table SchemaIndex from (name, declared type) pairs."""
    raw = {'tables': [{'name': table, 'schema': None, 'kind': 'table', 'foreign_keys': [], 'columns': [
        {'name': name, 'type': declared, 'primary_key': name in primary, 'nullable': True} for name, declared in columns]}]}
    return SchemaIndex.from_schema(raw, get_dialect(engine))


def one(engine, columns, **kw):
    body = S(**{'from': {'table': 't', 'alias': 't'}}, **kw)
    return compile_spec(QuerySpec.model_validate(body), schema(columns, engine), get_dialect(engine))


# --- request values -------------------------------------------------------------------------------------
@pytest.mark.parametrize('field', ['source', 'label', 'filter'])
def test_the_model_refuses_text_that_cannot_be_encoded(field):
    body = S(source='\ud800' if field == 'source' else 'conn',
             select=[{**C('o', 'id'), 'label': '\ud800' if field == 'label' else 'id'}],
             filters=AND(F('o', 'status', 'eq', value='\ud800')) if field == 'filter' else AND())
    with pytest.raises(ValueError):
        QuerySpec.model_validate(body)


# --- output names ---------------------------------------------------------------------------------------
def test_alias_limit_is_counted_in_bytes_per_engine():
    columns = [('id', 'integer'), ('c' * 60, 'integer')]
    long_label = 'é' * 40   # 80 bytes
    with pytest.raises(SpecErrors) as caught:
        one('postgresql', columns, select=[{**C('t', 'id'), 'label': long_label}])
    assert [e.code for e in caught.value.errors] == ['label_too_long'] and 'PostgreSQL' in caught.value.errors[0].message
    for engine in ('sqlite', 'mysql', 'mariadb'):
        assert one(engine, columns, select=[{**C('t', 'id'), 'label': long_label}]).columns[0]['label'] == long_label
    default = one('postgresql', columns, aggregates=[{'fn': 'sum', **C('t', 'c' * 60)}]).columns[0]['label']
    assert default == ('Total ' + 'c' * 60)[:63] and len(default.encode()) <= 63
    assert one('mysql', columns, aggregates=[{'fn': 'sum', **C('t', 'c' * 60)}]).columns[0]['label'] == 'Total ' + 'c' * 60


def test_a_default_label_is_cut_on_a_character_boundary():
    columns = [('id', 'integer'), ('é' * 40, 'integer')]
    label = one('postgresql', columns, aggregates=[{'fn': 'max', **C('t', 'é' * 40)}]).columns[0]['label']
    label.encode('utf-8')
    assert len(label.encode()) <= 63 and label.startswith('Largest é')


# --- text semantics -------------------------------------------------------------------------------------
TEXT_COLUMNS = [('id', 'integer'), ('name', 'varchar(20)')]


@pytest.mark.parametrize('engine', ENGINES)
def test_text_is_compared_grouped_and_counted_exactly_on_every_engine(engine):
    d = get_dialect(engine)
    name = d.quote('t') + '.' + d.quote('name')
    operand = d.text_operand(name)
    compiled = one(engine, TEXT_COLUMNS, filters=AND(F('t', 'name', 'eq', value='Alice')), group_by=[C('t', 'name')],
                   aggregates=[{'fn': 'count_distinct', **C('t', 'name')}])
    assert f'{operand} = :p1' in compiled.sql and f'COUNT(DISTINCT {operand})' in compiled.sql
    assert f'GROUP BY {operand}' in compiled.sql
    if engine in ('sqlite', 'postgresql'):
        assert operand == name   # already exact
    else:
        assert 'utf8mb4_bin' in operand


def test_mysql_and_mariadb_each_render_their_own_exact_text_operand():
    assert get_dialect('mysql').text_operand('x') == 'x COLLATE utf8mb4_bin'
    assert get_dialect('mariadb').text_operand('x') == 'x COLLATE utf8mb4_bin'
    for engine in ('mysql', 'mariadb'):
        # a column in another character set is converted first, since utf8mb4_bin is invalid for it
        assert get_dialect(engine).text_operand('x', 'latin1') == 'CONVERT(x USING utf8mb4) COLLATE utf8mb4_bin'
    # MariaDB's ONLY_FULL_GROUP_BY check needs the bare column in GROUP BY next to the collated expression
    assert get_dialect('mariadb').group_by_items('x COLLATE utf8mb4_bin', True, 'x') == \
        ['x COLLATE utf8mb4_bin', 'x', '(x COLLATE utf8mb4_bin IS NULL)']
    assert get_dialect('mysql').group_by_items('x COLLATE utf8mb4_bin', True, 'x') == \
        ['x COLLATE utf8mb4_bin', '(x COLLATE utf8mb4_bin IS NULL)']
    assert get_dialect('mariadb').group_by_items("date_format(x,'%Y')", True, None) == \
        ["date_format(x,'%Y')", "(date_format(x,'%Y') IS NULL)"]


def test_a_join_on_text_columns_matches_exactly_on_the_mysql_family():
    raw = {'tables': [{'name': n, 'schema': None, 'kind': 'table', 'foreign_keys': [], 'columns': [
        {'name': 'id', 'type': 'integer', 'primary_key': True}, {'name': 'code', 'type': 'varchar(5)'}]} for n in ('a', 'b')]}
    body = S(**{'from': {'table': 'a', 'alias': 'a'}}, select=[C('a', 'id')],
             joins=[{'kind': 'inner', 'table': 'b', 'alias': 'b', 'on': [{'left': C('a', 'code'), 'right': C('b', 'code')}]}])
    sql = compile_spec(QuerySpec.model_validate(body), SchemaIndex.from_schema(raw, get_dialect('mysql')), get_dialect('mysql')).sql
    assert '`a`.`code` COLLATE utf8mb4_bin = `b`.`code` COLLATE utf8mb4_bin' in sql


# --- numbers ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize('engine,expected', [
    ('sqlite', 'AVG("t"."n")'), ('postgresql', 'CAST(AVG("t"."n") AS DOUBLE PRECISION)'),
    ('mysql', 'CAST(AVG(`t`.`n`) AS DOUBLE)'), ('mariadb', 'CAST(AVG(`t`.`n`) AS DOUBLE)')])
def test_average_is_a_double_on_every_engine(engine, expected):
    assert expected in one(engine, [('id', 'integer'), ('n', 'integer')], aggregates=[{'fn': 'avg', **C('t', 'n')}]).sql


def test_integers_beyond_int64_are_bound_as_floats_in_number_and_untyped_columns():
    for engine, declared in (('sqlite', 'real'), ('postgresql', 'numeric'), ('sqlite', '')):
        compiled = one(engine, [('id', 'integer'), ('n', declared)], filters=AND(F('t', 'n', 'gt', value=10 ** 40)))
        assert compiled.parameters == {'p1': 1e40}
    with pytest.raises(SpecErrors) as caught:   # a whole-number column keeps its range check
        one('sqlite', [('id', 'integer')], filters=AND(F('t', 'id', 'gt', value=10 ** 40)))
    assert caught.value.errors[0].code == 'bad_value'


def test_typed_cells_have_one_json_type_per_kind():
    D = decimal.Decimal
    assert typed_scalar(D('210'), 'integer') == 210 and isinstance(typed_scalar(D('210'), 'integer'), int)
    assert typed_scalar(D('44.0000'), 'integer') == 44 and isinstance(typed_scalar(D('44.0000'), 'integer'), int)
    assert typed_scalar(D('7.3333'), 'number') == 7.3333 and isinstance(typed_scalar(D('7.3333'), 'number'), float)
    assert typed_scalar(D('1.10'), 'number') == 1.1
    assert typed_scalar(1, 'boolean') is True and typed_scalar(0, 'boolean') is False and typed_scalar(True, 'boolean') is True
    assert typed_scalar(2, 'boolean') == 2          # not a flag: left alone
    assert typed_scalar('2026-12-31 23:59:59', 'datetime') == '2026-12-31T23:59:59'
    assert typed_scalar('2026-12-31 23:59:59.5', 'datetime') == '2026-12-31T23:59:59.5'
    assert typed_scalar('free text 12:00', 'datetime') == 'free text 12:00'
    assert typed_scalar(None, 'integer') is None and typed_scalar('x', None) == 'x'
    assert typed_scalar(D('1E+400'), 'number') == '1E+400'   # not representable as a double: stays exact text


# --- uuid and arrays ------------------------------------------------------------------------------------
def test_uuid_filters_are_validated_by_the_compiler():
    columns = [('id', 'integer'), ('token', 'uuid')]
    good = one('postgresql', columns, filters=AND(F('t', 'token', 'eq', value=' 123E4567-E89B-12D3-A456-426614174000 ')))
    assert good.parameters == {'p1': '123e4567-e89b-12d3-a456-426614174000'}
    for value in ('1234', '', 'not-a-uuid', 5):
        with pytest.raises(SpecErrors) as caught:
            one('postgresql', columns, filters=AND(F('t', 'token', 'eq', value=value)))
        assert caught.value.errors[0].code == 'bad_value' and 'UUID' in caught.value.errors[0].message
    with pytest.raises(SpecErrors):
        one('postgresql', columns, filters=AND(F('t', 'token', 'in', values=['123e4567-e89b-12d3-a456-426614174000', 'x'])))
    one('postgresql', columns, filters=AND(F('t', 'token', 'is_null')))
    assert one('mariadb', [('id', 'int'), ('token', 'uuid')],
               filters=AND(F('t', 'token', 'eq', value='123e4567-e89b-12d3-a456-426614174000'))).parameters
    with pytest.raises(SpecErrors):
        one('mariadb', [('id', 'int'), ('token', 'uuid')], filters=AND(F('t', 'token', 'eq', value='1234')))


@pytest.mark.parametrize('declared', ['integer[]', 'text[]', 'ARRAY', '_int4'])
def test_postgresql_arrays_only_filter_by_emptiness_or_text(declared):
    columns = [('id', 'integer'), ('tags', declared)]
    one('postgresql', columns, filters=AND(F('t', 'tags', 'is_null')))
    one('postgresql', columns, filters=AND(F('t', 'tags', 'contains', value='x')))
    for op, extra in (('eq', {'value': 'x'}), ('in', {'values': ['x']})):
        with pytest.raises(SpecErrors) as caught:
            one('postgresql', columns, filters=AND(F('t', 'tags', op, **extra)))
        assert caught.value.errors[0].code == 'bad_operator' and 'a list of values' in caught.value.errors[0].message


# --- joins ----------------------------------------------------------------------------------------------
def test_row_multiplication_is_warned_about_without_aggregates(sqlite_shop):
    def join(left, right):
        return [{'kind': 'inner', 'table': 'orders', 'alias': 'b', 'on': [{'left': C('o', left), 'right': C('b', right)}]}]
    pk_to_pk = compile_for(sqlite_shop.raw, 'sqlite', S(joins=join('id', 'id'), select=[C('o', 'id')]))
    assert pk_to_pk.warnings == []
    many = compile_for(sqlite_shop.raw, 'sqlite', S(joins=join('id', 'customer_id'), select=[C('o', 'id')]))
    assert [w['code'] for w in many.warnings] == ['row_multiplication']
    both = compile_for(sqlite_shop.raw, 'sqlite', S(joins=join('status', 'status'), select=[C('o', 'id')]))
    assert [w['code'] for w in both.warnings] == ['many_to_many']
    counted = compile_for(sqlite_shop.raw, 'sqlite', S(joins=join('status', 'status'), aggregates=[{'fn': 'count'}]))
    assert [w['code'] for w in counted.warnings] == ['many_to_many']


# --- deadlines -----------------------------------------------------------------------------------------
@pytest.mark.parametrize('engine,statement', [
    ('sqlite', None), ('postgresql', "SELECT set_config('statement_timeout', '2500', true)"),
    ('mysql', 'SET SESSION MAX_EXECUTION_TIME = 2500'), ('mariadb', 'SET SESSION max_statement_time = 2.500')])
def test_each_adapter_renders_its_own_statement_deadline(engine, statement):
    assert get_dialect(engine).statement_timeout_sql(2.5) == statement


def test_statement_timeout_is_configurable_and_bounded(monkeypatch):
    assert statement_timeout_seconds() == 30
    for raw, want in (('2', 2), ('0', 0.1), ('99999', 600), ('nan', 30), ('x', 30), ('inf', 30)):
        monkeypatch.setenv(STATEMENT_TIMEOUT_ENV, raw)
        assert statement_timeout_seconds() == want, raw


def test_sqlite_stops_a_runaway_statement_at_the_deadline(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / 'big.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE t(a INTEGER)')
        db.executemany('INSERT INTO t VALUES(?)', [(i % 3,) for i in range(600)])
    monkeypatch.setenv(STATEMENT_TIMEOUT_ENV, '0.5')
    sql = 'SELECT COUNT(*) FROM t AS a JOIN t AS b ON a.a=b.a JOIN t AS c ON a.a=c.a JOIN t AS d ON a.a=d.a'
    with pytest.raises(StatementTimeout) as caught:
        SQLiteConnector(path).execute(sql, {}, 10)
    assert 'longer than 0.5 seconds' in str(caught.value)


def test_a_cancelled_sqlite_run_is_not_reported_as_a_timeout(tmp_path, monkeypatch):
    import sqlite3
    import threading
    path = tmp_path / 'big.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE t(a INTEGER)')
        db.executemany('INSERT INTO t VALUES(?)', [(i % 3,) for i in range(600)])
    event = threading.Event()
    threading.Timer(0.3, event.set).start()
    sql = 'SELECT COUNT(*) FROM t AS a JOIN t AS b ON a.a=b.a JOIN t AS c ON a.a=c.a JOIN t AS d ON a.a=d.a'
    with pytest.raises(sqlite3.OperationalError):
        SQLiteConnector(path, event).execute(sql, {}, 10)


@pytest.mark.parametrize('engine', LIVE)
def test_the_server_stops_a_runaway_statement_at_the_deadline(engine, monkeypatch):
    monkeypatch.setenv(STATEMENT_TIMEOUT_ENV, '1')
    with live_database(engine) as live:
        connector = NetworkSQLConnector(engine, {'database': live.name, 'schema': live.name}, 'unused')
        q = get_dialect(engine).quote
        t = f'{q(live.name)}.{q("dates")}'
        heavy = f'SELECT MAX(CONCAT(a.d, b.d, c.d, d.d)) FROM {t} a JOIN {t} b ON 1=1 JOIN {t} c ON 1=1 JOIN {t} d ON 1=1'
        assert connector._apply_deadline(live.conn) == 1
        try:
            with pytest.raises(Exception) as caught:
                live.conn.execute(text(heavy)).scalar()
            assert connector._timed_out(caught.value), repr(caught.value)[:300]
        finally:
            live.conn.rollback()
            reset = {'mysql': 'SET SESSION MAX_EXECUTION_TIME = 0', 'mariadb': 'SET SESSION max_statement_time = 0'}.get(engine)
            if reset:
                live.conn.exec_driver_sql(reset)


@pytest.mark.parametrize('engine', LIVE)
def test_an_ordinary_error_is_not_mistaken_for_a_timeout(engine):
    with live_database(engine) as live:
        connector = NetworkSQLConnector(engine, {'database': live.name, 'schema': live.name}, 'unused')
        with pytest.raises(Exception) as caught:
            live.conn.execute(text('SELECT * FROM does_not_exist'))
        live.conn.rollback()
        assert not connector._timed_out(caught.value)


# --- the same spec returns the same JSON types on every engine ------------------------------------------
TYPE_SPECS = {
    'avg_and_sum': S(**{'from': {'table': 'order_items', 'alias': 'i'}}, group_by=[{**C('i', 'sku')}],
                     aggregates=[{'fn': 'avg', **C('i', 'qty')}, {'fn': 'sum', **C('i', 'qty')}, {'fn': 'count'}],
                     sort=[{**C('i', 'sku'), 'direction': 'asc'}]),
    'sum_of_decimals': S(aggregates=[{'fn': 'sum', **C('o', 'amount')}, {'fn': 'avg', **C('o', 'amount')}]),
    'flag': S(group_by=[C('o', 'paid')], aggregates=[{'fn': 'count'}], sort=[{**C('o', 'paid'), 'direction': 'asc'}]),
    'timestamps': S(select=[C('o', 'id'), C('o', 'ordered_at')], sort=[{**C('o', 'id'), 'direction': 'asc'}], limit=3),
}


def types_of(result):
    return [[type(cell).__name__ for cell in row] for row in result['rows']]


def sqlite_types(sqlite_shop):
    out = {}
    for name, body in TYPE_SPECS.items():
        compiled = compile_for(sqlite_shop.raw, 'sqlite', body)
        kinds = [c['value_kind'] for c in compiled.columns]
        out[name] = SQLiteConnector(sqlite_shop.path).execute(compiled.sql, compiled.parameters, 50, kinds)
    return out


def test_sqlite_cells_follow_their_kinds(sqlite_shop):
    results = sqlite_types(sqlite_shop)
    assert types_of(results['avg_and_sum'])[0] == ['str', 'float', 'int', 'int']
    assert all(t == ['bool', 'int'] or t == ['NoneType', 'int'] for t in types_of(results['flag']))
    assert {row[1][:11].count('T') for row in results['timestamps']['rows']} == {1}   # 'YYYY-MM-DDT...'


@pytest.mark.parametrize('engine', LIVE)
def test_live_results_have_the_same_json_types_as_sqlite(engine, sqlite_shop, monkeypatch):
    expected = sqlite_types(sqlite_shop)
    with live_database(engine) as live:
        raw = live_raw(live)
        connector = NetworkSQLConnector(engine, {'database': live.name, 'schema': live.name, 'host': 'x', 'port': 1, 'username': 'x'}, 'unused')

        import contextlib

        @contextlib.contextmanager
        def connection(self):
            yield live.conn
        monkeypatch.setattr(NetworkSQLConnector, 'connection', connection)
        for name, body in TYPE_SPECS.items():
            compiled = compile_for(raw, engine, body, live.name)
            kinds = [c['value_kind'] for c in compiled.columns]
            got = connector.execute(compiled.sql, compiled.parameters, 50, kinds)
            assert types_of(got) == types_of(expected[name]), (name, got['rows'][:3], expected[name]['rows'][:3])
            if name == 'avg_and_sum':
                assert [row[1] for row in got['rows']] == pytest.approx([row[1] for row in expected[name]['rows']])
            if name == 'timestamps':
                assert got['rows'] == expected[name]['rows']


@pytest.mark.parametrize('engine', LIVE)
def test_live_text_counts_equal_sqlite(engine, sqlite_shop):
    """The conformance corpus also covers eq/ne/in; this pins GROUP BY and COUNT(DISTINCT) on text."""
    body = S(**{'from': {'table': 'customers', 'alias': 'c'}}, group_by=[C('c', 'name')],
             aggregates=[{'fn': 'count_distinct', **C('c', 'region')}], sort=[{**C('c', 'name'), 'direction': 'asc'}], limit=1000)
    lite = compile_for(sqlite_shop.raw, 'sqlite', body)
    want = SQLiteConnector(sqlite_shop.path).execute(lite.sql, lite.parameters, 5000)['rows']
    with live_database(engine) as live:
        compiled = compile_for(live_raw(live), engine, body, live.name)
        got = [list(r) for r in live.conn.execute(text(compiled.sql), compiled.parameters).fetchall()]
    assert sorted(map(repr, got)) == sorted(map(repr, want))
    assert ['ALICE', 1] in got and ['alice', 1] in got


@pytest.mark.parametrize('engine', LIVE)
def test_live_uuid_filter_is_rejected_before_the_server_sees_it(engine):
    if engine == 'mysql':
        pytest.skip('MySQL has no uuid column type')
    with live_database(engine) as live:
        if engine == 'mariadb' and live.version_parts < (10, 7):
            pytest.skip('MariaDB Community native UUID requires 10.7; this server is ' + live.version)
        q = get_dialect(engine).quote
        ddl = 'uuid' if engine in ('postgresql', 'mariadb') else 'char(36)'
        live.conn.execute(text(f'CREATE TABLE {q(live.name)}.u(id integer PRIMARY KEY, token {ddl})'))
        raw = live_raw(live)
        body = S(**{'from': {'table': 'u', 'alias': 'u'}}, select=[C('u', 'id')],
                 filters=AND(F('u', 'token', 'eq', value='1234')))
        with pytest.raises(SpecErrors):
            compile_for(raw, engine, body, live.name)
        ok = S(**{'from': {'table': 'u', 'alias': 'u'}}, select=[C('u', 'id')],
               filters=AND(F('u', 'token', 'eq', value='123e4567-e89b-12d3-a456-426614174000')))
        compiled = compile_for(raw, engine, ok, live.name)
        assert live.conn.execute(text(compiled.sql), compiled.parameters).fetchall() == []


@pytest.mark.parametrize('engine', ['mysql', 'mariadb'])
def test_live_columns_report_their_character_set(engine):
    with live_database(engine) as live:
        raw = live_raw(live)
        orders = next(t for t in raw['tables'] if t['name'] == 'orders')
        status = next(c for c in orders['columns'] if c['name'] == 'status')
        assert status['charset'] == 'utf8mb4' and status['type'].startswith('varchar')
        assert next(c for c in orders['columns'] if c['name'] == 'id')['charset'] is None
