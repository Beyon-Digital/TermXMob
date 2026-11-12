"""Per-dialect conformance: one corpus of specs, compiled for all four dialects and executed
for real against SQLite (always) and against PostgreSQL, MySQL and MariaDB (when the matching
environment variable is set). Results are compared with an independent Python oracle.

Live containers (not started by CI). Start them, run, then remove them:

    docker run -d --rm --name termx-test-pg -e POSTGRES_PASSWORD=termx -e POSTGRES_USER=termx \\
        -e POSTGRES_DB=termx -p 55432:5432 postgres:16
    docker run -d --rm --name termx-test-mysql -e MYSQL_ROOT_PASSWORD=termx -e MYSQL_DATABASE=termx \\
        -p 53306:3306 mysql:8.4
    docker run -d --rm --name termx-test-mariadb -e MARIADB_ROOT_PASSWORD=termx -e MARIADB_DATABASE=termx \\
        -p 53307:3306 mariadb:10.11

    TERMX_TEST_PG_URL=postgresql+psycopg://termx:termx@127.0.0.1:55432/termx \\
    TERMX_TEST_MYSQL_URL=mysql+pymysql://root:termx@127.0.0.1:53306/termx \\
    TERMX_TEST_MARIADB_URL=mysql+pymysql://root:termx@127.0.0.1:53307/termx \\
    uv run pytest tests/test_data_conformance.py -q

    docker rm -f termx-test-pg termx-test-mysql termx-test-mariadb

Each live test loads the fixture rows into a uniquely named schema (PostgreSQL) or database
(MySQL, MariaDB) and drops it afterwards.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import os
import re
import uuid

import pytest
import sqlglot
from sqlglot import exp

from data_query_fixture import build_rows, create_sqlite
from data_query_oracle import KINDS, Oracle, ascii_lower, bucket_label, normalise_rows
from termx.data.connectors import NetworkSQLConnector, SQLiteConnector, validate_read
from termx.data.dialects import get_dialect
from termx.data.query_spec import QuerySpec, SchemaIndex, compile_spec
from termx.data.runtime import DataRuntime

ENGINES = ['sqlite', 'postgresql', 'mysql', 'mariadb']
LIVE_URLS = {'postgresql': 'TERMX_TEST_PG_URL', 'mysql': 'TERMX_TEST_MYSQL_URL', 'mariadb': 'TERMX_TEST_MARIADB_URL'}


def C(alias, column):
    return {'table_alias': alias, 'column': column}


def F(alias, column, op, **kw):
    return {'field': C(alias, column), 'op': op, **kw}


def AND(*items):
    return {'op': 'and', 'items': list(items)}


def OR(*items):
    return {'op': 'or', 'items': list(items)}


def S(**kw):
    body = {'version': 1, 'source': 'conn', 'from': {'table': 'orders', 'alias': 'o'}, 'joins': [], 'select': [],
            'filters': AND(), 'group_by': [], 'aggregates': [], 'sort': [], 'limit': 200, 'offset': 0}
    body.update(kw)
    return body


def JOIN(table, alias, left, right, kind='left'):
    return {'kind': kind, 'table': table, 'alias': alias, 'on': [{'left': C(*left), 'right': C(alias, right)}]}


def ASC(alias, column):
    return {**C(alias, column), 'direction': 'asc'}


def DESC(alias, column):
    return {**C(alias, column), 'direction': 'desc'}


def orders(*filters, **kw):
    body = dict(select=[C('o', 'id'), C('o', 'status'), C('o', 'amount')], sort=[ASC('o', 'id')], limit=1000)
    body.update(kw)
    return S(filters=AND(*filters), **body)


def customers(*filters, **kw):
    body = dict(select=[C('c', 'id'), C('c', 'name')], sort=[ASC('c', 'id')], limit=1000)
    body.update(kw)
    return S(**{'from': {'table': 'customers', 'alias': 'c'}}, filters=AND(*filters), **body)


def count_by_bucket(bucket):
    return S(filters=AND(F('o', 'ordered_at', 'gte', value='2020-12-31'), F('o', 'ordered_at', 'lt', value='2027-02-01')),
             group_by=[{**C('o', 'ordered_at'), 'bucket': bucket, 'label': 'Period'}], aggregates=[{'fn': 'count'}],
             sort=[{'output': 'Period', 'direction': 'asc'}], limit=1000)


CUSTOMER_JOIN = JOIN('customers', 'c', ('o', 'customer_id'), 'id')
ITEM_JOIN = JOIN('order_items', 'i', ('o', 'id'), 'order_id')
CORPUS = {
    # comparison operators ---------------------------------------------------------------------
    'eq_text': orders(F('o', 'status', 'eq', value='open')),
    'ne_text_keeps_nulls': orders(F('o', 'status', 'ne', value='closed')),
    'eq_integer': orders(F('o', 'id', 'eq', value=17)),
    'eq_integer_as_text': orders(F('o', 'id', 'eq', value='17')),
    'gt_number': orders(F('o', 'amount', 'gt', value=400)),
    'gte_number': orders(F('o', 'amount', 'gte', value=400.5)),
    'lt_number': orders(F('o', 'amount', 'lt', value=10)),
    'lte_number': orders(F('o', 'amount', 'lte', value=10)),
    'between_number': orders(F('o', 'amount', 'between', values=[100, 120.5])),
    'in_text': orders(F('o', 'status', 'in', values=['open', 'paid'])),
    'not_in_text_keeps_nulls': orders(F('o', 'status', 'not_in', values=['open', 'paid'])),
    'in_integer': orders(F('o', 'id', 'in', values=[1, 5, 9, 1300])),
    'is_null': orders(F('o', 'amount', 'is_null')),
    'not_null': orders(F('o', 'note', 'not_null')),
    # text matching ------------------------------------------------------------------------------
    'contains_note': orders(F('o', 'note', 'contains', value='late')),
    'contains_percent_underscore_literal': orders(F('o', 'note', 'contains', value='50%_')),
    'contains_underscore_literal': customers(F('c', 'name', 'contains', value='_')),
    'contains_bang_literal': customers(F('c', 'name', 'contains', value='a!b')),
    'contains_case_insensitive': customers(F('c', 'name', 'contains', value='ALICE')),
    'contains_quote': customers(F('c', 'name', 'contains', value="it's")),
    'contains_after_accent': customers(F('c', 'name', 'contains', value='clair')),
    'contains_lower_accent': customers(F('c', 'name', 'contains', value='éclair')),
    'starts_with': customers(F('c', 'name', 'starts_with', value='Customer 1')),
    'starts_with_percent': customers(F('c', 'name', 'starts_with', value='100%')),
    'contains_on_integer_column': orders(F('o', 'id', 'contains', value='13')),
    # booleans -------------------------------------------------------------------------------------
    'bool_true': orders(F('o', 'paid', 'eq', value=True)),
    'bool_false': orders(F('o', 'paid', 'eq', value=False)),
    'bool_ne_true': orders(F('o', 'paid', 'ne', value=True)),
    'bool_in': orders(F('o', 'paid', 'in', values=[True])),
    # dates --------------------------------------------------------------------------------------
    'date_eq': customers(F('c', 'created_on', 'eq', value='2020-03-01')),
    'date_gt': customers(F('c', 'created_on', 'gt', value='2022-01-01')),
    'date_between': customers(F('c', 'created_on', 'between', values=['2021-01-01', '2021-12-31'])),
    'datetime_day_eq': orders(F('o', 'ordered_at', 'eq', value='2026-11-12')),
    'datetime_day_ne': orders(F('o', 'ordered_at', 'ne', value='2026-11-12'), F('o', 'id', 'lt', value=20)),
    'datetime_day_gt': orders(F('o', 'ordered_at', 'gt', value='2026-11-12'), F('o', 'id', 'lt', value=20)),
    'datetime_day_gte': orders(F('o', 'ordered_at', 'gte', value='2026-11-12'), F('o', 'id', 'lt', value=20)),
    'datetime_day_lt': orders(F('o', 'ordered_at', 'lt', value='2026-11-12'), F('o', 'id', 'lt', value=20)),
    'datetime_day_lte': orders(F('o', 'ordered_at', 'lte', value='2026-11-12'), F('o', 'id', 'lt', value=20)),
    'datetime_day_between': orders(F('o', 'ordered_at', 'between', values=['2026-11-12', '2026-11-12'])),
    'datetime_between_mixed': orders(F('o', 'ordered_at', 'between', values=['2020-12-31 00:00:00', '2021-01-03'])),
    'datetime_day_in': orders(F('o', 'ordered_at', 'in', values=['2024-02-29', '2026-11-12'])),
    'datetime_day_not_in': orders(F('o', 'ordered_at', 'not_in', values=['2026-11-12']), F('o', 'id', 'lt', value=20)),
    'datetime_exact': orders(F('o', 'ordered_at', 'eq', value='2026-11-12 14:30:00')),
    'datetime_iso_t': orders(F('o', 'ordered_at', 'gte', value='2026-11-12T14:30')),
    # groups -----------------------------------------------------------------------------------------
    'or_group': orders(OR(F('o', 'status', 'eq', value='open'), F('o', 'amount', 'gt', value=490)), F('o', 'customer_id', 'not_null')),
    'nested_groups': orders(OR(AND(F('o', 'status', 'eq', value='paid'), OR(F('o', 'amount', 'lt', value=5), F('o', 'amount', 'gt', value=495))),
                               AND(F('o', 'paid', 'eq', value=True), F('o', 'note', 'contains', value='late')))),
    'single_item_group': orders(OR(F('o', 'id', 'eq', value=3))),
    # joins -------------------------------------------------------------------------------------------
    'inner_join': S(joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id', 'inner')], select=[C('o', 'id'), C('c', 'name')],
                    sort=[ASC('o', 'id')], limit=1000),
    'left_join_filter_joined': S(joins=[CUSTOMER_JOIN], select=[C('o', 'id'), C('c', 'region')], filters=AND(F('c', 'region', 'eq', value='north')),
                                 sort=[ASC('o', 'id')], limit=1000),
    'left_join_unmatched': S(joins=[CUSTOMER_JOIN], select=[C('o', 'id'), C('c', 'name')], filters=AND(F('c', 'id', 'is_null')),
                             sort=[ASC('o', 'id')], limit=1000),
    'left_join_expanded_columns': S(joins=[CUSTOMER_JOIN], sort=[ASC('o', 'id')], limit=300),
    'fan_out_join_with_tie_breaker': S(joins=[ITEM_JOIN], select=[C('o', 'id'), C('i', 'sku'), C('i', 'qty')], sort=[ASC('o', 'id')],
                                       limit=100, offset=40),
    'four_joins': S(joins=[CUSTOMER_JOIN, JOIN('customers', 'c2', ('o', 'customer_id'), 'id', 'inner'), ITEM_JOIN,
                           JOIN('orders', 'o2', ('i', 'order_id'), 'id')],
                    select=[C('o', 'id'), C('c', 'name'), {**C('c2', 'region'), 'label': 'Region'}, C('i', 'sku'), {**C('o2', 'status'), 'label': 'Again'}],
                    sort=[ASC('o', 'id')], limit=150),
    'join_date_to_datetime': S(joins=[JOIN('customers', 'c', ('o', 'ordered_at'), 'created_on', 'inner')], select=[C('o', 'id'), C('c', 'id')],
                               limit=50, sort=[ASC('o', 'id')]),
    # summaries ----------------------------------------------------------------------------------------
    'count_by_status': S(group_by=[C('o', 'status')], aggregates=[{'fn': 'count'}]),
    'count_by_status_sorted_by_output': S(group_by=[C('o', 'status')], aggregates=[{'fn': 'count'}],
                                          sort=[{'output': 'Number of rows', 'direction': 'desc'}, ASC('o', 'status')]),
    'two_groups_through_join': S(joins=[CUSTOMER_JOIN], group_by=[C('c', 'region'), C('o', 'status')], aggregates=[{'fn': 'count'}]),
    'sum_by_month': S(group_by=[{**C('o', 'ordered_at'), 'bucket': 'month', 'label': 'Month'}],
                      aggregates=[{'fn': 'sum', **C('o', 'amount'), 'label': 'Total amount'}], sort=[{'output': 'Month', 'direction': 'asc'}], limit=1000),
    'all_aggregates_without_groups': S(aggregates=[{'fn': 'count'}, {'fn': 'count', **C('o', 'amount')}, {'fn': 'count_distinct', **C('o', 'customer_id')},
                                                   {'fn': 'sum', **C('o', 'amount')}, {'fn': 'avg', **C('o', 'amount')},
                                                   {'fn': 'min', **C('o', 'amount')}, {'fn': 'max', **C('o', 'amount')}]),
    'min_max_dates': S(aggregates=[{'fn': 'min', **C('o', 'ordered_at')}, {'fn': 'max', **C('o', 'ordered_at')}]),
    'empty_aggregate': S(filters=AND(F('o', 'id', 'lt', value=0)), aggregates=[{'fn': 'count'}, {'fn': 'sum', **C('o', 'amount')}, {'fn': 'max', **C('o', 'amount')}]),
    'sum_integer_column': S(joins=[ITEM_JOIN], aggregates=[{'fn': 'sum', **C('i', 'qty')}, {'fn': 'count_distinct', **C('i', 'sku')}]),
    'group_by_boolean': S(group_by=[C('o', 'paid')], aggregates=[{'fn': 'count'}], sort=[ASC('o', 'paid')]),
    'group_year_of_joined_date': S(joins=[CUSTOMER_JOIN], group_by=[{**C('c', 'created_on'), 'bucket': 'year', 'label': 'Year'}],
                                   aggregates=[{'fn': 'count'}], filters=AND(F('o', 'status', 'in', values=['open', 'paid'])),
                                   sort=[{'output': 'Year', 'direction': 'desc'}], limit=100),
    'top_customers_by_total': S(group_by=[C('c', 'id')], joins=[CUSTOMER_JOIN], aggregates=[{'fn': 'sum', **C('o', 'amount'), 'label': 'Total'}],
                                sort=[{'output': 'Total', 'direction': 'desc'}, ASC('c', 'id')], limit=5),
    'group_sorted_by_group_column': S(group_by=[{**C('o', 'ordered_at'), 'bucket': 'quarter', 'label': 'Quarter'}], aggregates=[{'fn': 'count'}],
                                      sort=[DESC('o', 'ordered_at')], limit=1000),
    'fan_out_summary': S(joins=[ITEM_JOIN], group_by=[C('o', 'status')], aggregates=[{'fn': 'count'}, {'fn': 'count_distinct', **C('o', 'id')}]),
    # buckets ---------------------------------------------------------------------------------------------
    **{f'bucket_{b}': count_by_bucket(b) for b in ('day', 'week', 'month', 'quarter', 'year')},
    # null ordering and paging ---------------------------------------------------------------------------
    'nulls_last_ascending': orders(F('o', 'id', 'lte', value=500), sort=[ASC('o', 'amount')], limit=1000),
    'nulls_last_descending': orders(F('o', 'id', 'lte', value=500), sort=[DESC('o', 'amount')], limit=1000),
    'page_one': orders(sort=[DESC('o', 'amount')], limit=10),
    'page_two': orders(sort=[DESC('o', 'amount')], limit=10, offset=10),
    'page_deep': orders(sort=[DESC('o', 'amount')], limit=10, offset=1295),
    'page_beyond_end': orders(sort=[DESC('o', 'amount')], limit=100, offset=1300),
    'limit_one': orders(limit=1),
    'sort_by_unselected_column': S(select=[C('o', 'id')], sort=[DESC('o', 'ordered_at')], limit=40),
    'sort_by_date_with_nulls': orders(sort=[ASC('o', 'ordered_at')], limit=1000),
    'sort_by_output': S(select=[C('o', 'id'), {**C('o', 'amount'), 'label': 'Value'}], sort=[{'output': 'Value', 'direction': 'desc'}], limit=60),
}
assert len(CORPUS) >= 40


# --- schema and compile helpers -------------------------------------------------------------------
@pytest.fixture(scope='module')
def sqlite_shop(tmp_path_factory):
    path = tmp_path_factory.mktemp('conformance') / 'shop.sqlite3'
    rows = create_sqlite(path)
    raw = SQLiteConnector(path).schema()
    DataRuntime._annotate_kinds(raw, 'sqlite')
    return type('Shop', (), {'path': path, 'rows': rows, 'raw': raw})


def index_for(raw, engine, schema_name=None):
    """The introspected fixture schema, qualified the way the given engine reports it."""
    raw = copy.deepcopy(raw)
    qualifier = {'sqlite': 'main', 'postgresql': schema_name or 'public', 'mysql': schema_name or 'shop', 'mariadb': schema_name or 'shop'}[engine]
    for table in raw['tables']:
        table['schema'] = qualifier
    return SchemaIndex.from_schema(raw, get_dialect(engine))


def compile_for(raw, engine, body, schema_name=None):
    return compile_spec(QuerySpec.model_validate(body), index_for(raw, engine, schema_name), get_dialect(engine))


def structural_checks(compiled, engine):
    dialect = get_dialect(engine)
    validate_read(compiled.sql, engine, compiled.parameters)
    parsed = sqlglot.parse(compiled.sql, read=dialect.sqlglot_dialect)
    assert len(parsed) == 1 and parsed[0] is not None
    again = sqlglot.transpile(compiled.sql, read=dialect.sqlglot_dialect, write=dialect.sqlglot_dialect)
    assert len(again) == 1 and sqlglot.parse_one(again[0], read=dialect.sqlglot_dialect) is not None
    # every bound name is used exactly as a placeholder, and nothing else is
    placeholders = {n.name for n in parsed[0].find_all(exp.Placeholder)}
    assert placeholders == set(compiled.parameters), (placeholders, compiled.parameters)
    for value in compiled.parameters.values():
        if isinstance(value, str) and len(value) > 2:
            assert f"'{value}'" not in compiled.sql
    if dialect.quote_char == '`':
        assert '"' not in compiled.sql, 'MySQL family output must not use double-quoted identifiers'
    else:
        assert '`' not in compiled.sql
    # no output alias appears in GROUP BY or ORDER BY: only qualified columns or expressions
    tree = parsed[0]
    for clause in (tree.args.get('group'), tree.args.get('order')):
        if clause is not None:
            for column in clause.find_all(exp.Column):
                assert column.table, f'unqualified (alias) reference {column.sql()} in {clause.sql()}'
    return tree


@pytest.mark.parametrize('engine', ENGINES)
def test_corpus_compiles_and_passes_every_static_check(sqlite_shop, engine):
    failures = []
    for name, body in CORPUS.items():
        try:
            structural_checks(compile_for(sqlite_shop.raw, engine, body), engine)
        except Exception as error:   # noqa: BLE001 - report every failing entry together
            failures.append(f'{name}: {type(error).__name__}: {error}')
    assert not failures, '\n'.join(failures)


def test_engine_specific_shapes_in_compiled_sql(sqlite_shop):
    body = CORPUS['sum_by_month']
    pg, my, ma, lite = (compile_for(sqlite_shop.raw, e, body, 'analytics') for e in ('postgresql', 'mysql', 'mariadb', 'sqlite'))
    assert 'FROM "analytics"."orders" AS "o"' in pg.sql and "to_char(" in pg.sql and 'NULLS LAST' in pg.sql
    assert 'FROM `analytics`.`orders` AS `o`' in my.sql and 'date_format(' in my.sql and '(date_format(' in my.sql.split('ORDER BY')[1]
    assert my.sql.replace('`', '"') != pg.sql and ma.sql == my.sql
    assert 'GROUP BY date_format(`o`.`ordered_at`,\'%Y-%m\'), (date_format(`o`.`ordered_at`,\'%Y-%m\') IS NULL)' in my.sql
    assert 'GROUP BY to_char("o"."ordered_at",\'YYYY-MM\')\n' in pg.sql
    assert 'FROM "orders" AS "o"' in lite.sql and 'strftime(' in lite.sql
    flags = compile_for(sqlite_shop.raw, 'mysql', CORPUS['bool_true'])
    assert flags.parameters == {'p1': True} and compile_for(sqlite_shop.raw, 'sqlite', CORPUS['bool_true']).parameters == {'p1': 1}
    cast = compile_for(sqlite_shop.raw, 'mysql', CORPUS['contains_on_integer_column'])
    assert 'CAST(`o`.`id` AS CHAR)' in cast.sql
    assert 'CAST("o"."id" AS TEXT)' in compile_for(sqlite_shop.raw, 'postgresql', CORPUS['contains_on_integer_column']).sql


# --- comparison with the oracle ---------------------------------------------------------------------------
def oracle_for(engine, rows):
    fold = ascii_lower if engine == 'sqlite' else str.lower
    # Every engine compares text exactly: the MySQL family is told to by the compiler (`text_operand`).
    return Oracle(rows, fold=fold)


def compare(engine, name, body, compiled, columns, db_rows, rows):
    spec = QuerySpec.model_validate(body)
    expected, labels, kinds, text_sort = oracle_for(engine, rows).evaluate(spec)
    assert columns == labels == [c['label'] for c in compiled.columns], f'{name}: labels'
    got, want = normalise_rows(db_rows), normalise_rows(expected)
    assert len(got) == len(want), f'{name}: {len(got)} rows, expected {len(want)}'
    if text_sort and engine != 'sqlite':
        # text collation differs between engines, so only compare the set of rows
        key = lambda row: repr(row)
        got, want = sorted(got, key=key), sorted(want, key=key)
    assert got == want, f'{name}: rows differ\n got {got[:3]}\n want {want[:3]}'


@pytest.mark.parametrize('name', sorted(CORPUS))
def test_sqlite_executes_the_corpus_like_the_oracle(sqlite_shop, name):
    body = CORPUS[name]
    compiled = compile_for(sqlite_shop.raw, 'sqlite', body)
    result = SQLiteConnector(sqlite_shop.path).execute(compiled.sql, compiled.parameters, 5000)
    compare('sqlite', name, body, compiled, [c['name'] for c in result['columns']], result['rows'], sqlite_shop.rows)


def test_sqlite_paging_concatenates_to_the_unpaged_order(sqlite_shop):
    def page(offset, limit, **extra):
        body = orders(sort=[DESC('o', 'amount')], limit=limit, offset=offset, **extra)
        compiled = compile_for(sqlite_shop.raw, 'sqlite', body)
        return SQLiteConnector(sqlite_shop.path).execute(compiled.sql, compiled.parameters, 5000)['rows']
    whole = [r[0] for r in page(0, 1000)][:1000]
    parts = [r[0] for offset in range(0, 1000, 125) for r in page(offset, 125)[:125]]
    assert parts == whole and len(set(parts)) == 1000


def test_sqlite_null_ordering_is_last_in_both_directions(sqlite_shop):
    for direction in ('asc', 'desc'):
        body = orders(F('o', 'id', 'lte', value=500), sort=[{**C('o', 'amount'), 'direction': direction}], limit=1000)
        compiled = compile_for(sqlite_shop.raw, 'sqlite', body)
        values = [r[2] for r in SQLiteConnector(sqlite_shop.path).execute(compiled.sql, compiled.parameters, 5000)['rows']]
        nulls = [i for i, v in enumerate(values) if v is None]
        assert nulls == list(range(len(values) - len(nulls), len(values))) and nulls


def test_sqlite_without_native_nulls_last_uses_the_emulated_form(sqlite_shop, monkeypatch):
    from termx.data.dialects import sqlite as module
    monkeypatch.setattr(module, 'sqlite_version', lambda: (3, 29, 0))
    body = orders(F('o', 'id', 'lte', value=500), sort=[DESC('o', 'amount')], limit=1000)
    compiled = compile_for(sqlite_shop.raw, 'sqlite', body)
    assert 'ORDER BY ("o"."amount" IS NULL), "o"."amount" DESC, ("o"."id" IS NULL), "o"."id" ASC' in compiled.sql
    result = SQLiteConnector(sqlite_shop.path).execute(compiled.sql, compiled.parameters, 5000)
    compare('sqlite', 'emulated', body, compiled, [c['name'] for c in result['columns']],
            result['rows'], sqlite_shop.rows)


def test_sqlite_text_columns_bucket_with_a_warning_and_bad_text_is_empty(tmp_path):
    import sqlite3
    path = tmp_path / 'text-dates.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, happened TEXT)')
        db.executemany('INSERT INTO events VALUES(?,?)', [(1, '2026-11-12'), (2, '2026-11-20 10:00:00'), (3, 'last tuesday'), (4, None)])
    raw = SQLiteConnector(path).schema()
    body = S(**{'from': {'table': 'events', 'alias': 'e'}}, group_by=[{**C('e', 'happened'), 'bucket': 'month'}], aggregates=[{'fn': 'count'}],
             sort=[{'output': 'Number of rows', 'direction': 'desc'}])
    compiled = compile_for(raw, 'sqlite', body)
    assert [w['code'] for w in compiled.warnings] == ['text_date']
    rows = SQLiteConnector(path).execute(compiled.sql, compiled.parameters, 100)['rows']
    assert sorted(rows, key=lambda r: (r[0] is None, r[0])) == [['2026-11', 2], [None, 2]]


# --- live engines ------------------------------------------------------------------------------------------
DDL = {
    'postgresql': {
        'customers': 'CREATE TABLE {s}.customers(id integer PRIMARY KEY, name varchar(100), region varchar(20), created_on date)',
        'orders': 'CREATE TABLE {s}.orders(id integer PRIMARY KEY, customer_id integer REFERENCES {s}.customers(id), status varchar(20), '
                  'amount double precision, ordered_at timestamp, note text, paid boolean)',
        'order_items': 'CREATE TABLE {s}.order_items(order_id integer REFERENCES {s}.orders(id), sku varchar(20), qty integer, PRIMARY KEY(order_id, sku))'},
    'mysql': {
        'customers': 'CREATE TABLE {s}.customers(id int PRIMARY KEY, name varchar(100), region varchar(20), created_on date)',
        'orders': 'CREATE TABLE {s}.orders(id int PRIMARY KEY, customer_id int, status varchar(20), amount double, ordered_at datetime, '
                  'note text, paid boolean, FOREIGN KEY(customer_id) REFERENCES {s}.customers(id))',
        'order_items': 'CREATE TABLE {s}.order_items(order_id int, sku varchar(20), qty int, PRIMARY KEY(order_id, sku), '
                       'FOREIGN KEY(order_id) REFERENCES {s}.orders(id))'},
}
DDL['mariadb'] = DDL['mysql']


@contextlib.contextmanager
def live_database(engine_name):
    url = os.environ.get(LIVE_URLS[engine_name])
    if not url:
        pytest.skip(f'{LIVE_URLS[engine_name]} is not set')
    from sqlalchemy import create_engine, text
    source = create_engine(url)
    name = 'termx_q_' + uuid.uuid4().hex[:12]
    quoted = f'"{name}"' if engine_name == 'postgresql' else f'`{name}`'
    rows = build_rows()
    with source.connect() as conn:
        try:
            conn.execute(text(f'CREATE SCHEMA {quoted}' if engine_name == 'postgresql' else f'CREATE DATABASE {quoted}'))
            for table in ('customers', 'orders', 'order_items'):
                conn.execute(text(DDL[engine_name][table].format(s=quoted)))
            for table in ('customers', 'orders', 'order_items'):
                names = list(rows[table][0])
                conn.execute(text(f'INSERT INTO {quoted}.{table}({",".join(names)}) VALUES({",".join(":" + n for n in names)})'), rows[table])
            conn.execute(text('CREATE TABLE ' + quoted + '.dates(d datetime)' if engine_name != 'postgresql'
                              else f'CREATE TABLE {quoted}.dates(d timestamp)'))
            conn.execute(text(f'CREATE TABLE {quoted}.dates_d(d date)'))
            start = dt.date(2019, 12, 20)
            days = [start + dt.timedelta(days=i) for i in range((dt.date(2028, 12, 31) - start).days + 1)]
            conn.execute(text(f'INSERT INTO {quoted}.dates(d) VALUES(:d)'),
                         [{'d': dt.datetime.combine(day, dt.time(14, 30))} for day in days] + [{'d': None}])
            conn.execute(text(f'INSERT INTO {quoted}.dates_d(d) VALUES(:d)'), [{'d': day} for day in days] + [{'d': None}])
            conn.commit()
            if engine_name == 'mariadb':
                conn.exec_driver_sql("SET SESSION sql_mode = CONCAT(@@sql_mode, ',ONLY_FULL_GROUP_BY')")
            if engine_name != 'postgresql':
                assert 'ONLY_FULL_GROUP_BY' in conn.exec_driver_sql('SELECT @@sql_mode').scalar()
            yield type('Live', (), {'conn': conn, 'name': name, 'rows': rows, 'days': days, 'engine': engine_name})
        finally:
            conn.rollback()
            conn = conn.execution_options(stream_results=False)
            conn.execute(text(f'DROP SCHEMA IF EXISTS {quoted} CASCADE' if engine_name == 'postgresql' else f'DROP DATABASE IF EXISTS {quoted}'))
            conn.commit()
    source.dispose()


def live_raw(live):
    config = {'database': live.name, 'schema': live.name}
    return NetworkSQLConnector(live.engine, config, 'unused').schema_from(live.conn)


def run_live_corpus(live, names=None):
    from sqlalchemy import text
    raw = live_raw(live)
    failures = []
    for name, body in CORPUS.items():
        if names and name not in names:
            continue
        try:
            compiled = compile_for(raw, live.engine, body, live.name)
            structural_checks(compiled, live.engine)
            result = live.conn.execute(text(compiled.sql), compiled.parameters)
            columns, rows = list(result.keys()), [list(r) for r in result.fetchall()]
            compare(live.engine, name, body, compiled, columns, rows, live.rows)
        except Exception as error:   # noqa: BLE001
            live.conn.rollback()
            failures.append(f'{name}: {type(error).__name__}: {str(error)[:300]}')
    return failures


def check_live_schema(live):
    raw = live_raw(live)
    tables = {t['name']: t for t in raw['tables']}
    assert {'customers', 'orders', 'order_items'} <= set(tables)
    kinds = {c['name']: c['value_kind'] if 'value_kind' in c else get_dialect(live.engine).classify_type(c['type']) for c in tables['orders']['columns']}
    assert kinds == {'id': 'integer', 'customer_id': 'integer', 'status': 'text', 'amount': 'number', 'ordered_at': 'datetime',
                     'note': 'text', 'paid': 'boolean'}, kinds
    assert tables['orders']['foreign_keys'] == [{'columns': ['customer_id'], 'referred_table': 'customers', 'referred_columns': ['id']}]
    assert tables['order_items']['foreign_keys'] == [{'columns': ['order_id'], 'referred_table': 'orders', 'referred_columns': ['id']}]
    assert all(t['schema'] == live.name for t in raw['tables'])
    assert all(t['row_estimate'] is None or t['row_estimate'] >= 0 for t in raw['tables'])
    from termx.data.relations import suggest_relations
    relations = suggest_relations(SchemaIndex.from_schema(raw, get_dialect(live.engine)), get_dialect(live.engine), 'orders')
    assert [r['sentence'] for r in relations if r['source'] == 'foreign_key'] == [
        'Order items to Orders: order_id matches id', 'Orders to Customers: customer_id matches id']


def check_live_buckets(live):
    from sqlalchemy import text
    d = get_dialect(live.engine)
    quoted = d.quote(live.name)
    for table, kind in (('dates', 'datetime'), ('dates_d', 'date')):
        for bucket in ('day', 'week', 'month', 'quarter', 'year'):
            expression = d.date_bucket(d.quote('x') + '.' + d.quote('d'), kind, bucket)
            rows = live.conn.execute(text(f'SELECT {d.quote("x")}.{d.quote("d")}, {expression} FROM {quoted}.{table} AS {d.quote("x")}')).fetchall()
            assert len(rows) == len(live.days) + 1
            for value, label in rows:
                if value is None:
                    assert label is None
                else:
                    day = value.date() if isinstance(value, dt.datetime) else value
                    assert label == bucket_label(day, bucket), (live.engine, table, bucket, day, label)


def check_live_network_connector_path(live, monkeypatch):
    """The compiled statements also pass NetworkSQLConnector.execute's own table checks."""
    config = {'database': live.name, 'schema': live.name, 'host': 'x', 'port': 1, 'username': 'x'}
    connector = NetworkSQLConnector(live.engine, config, 'unused')

    @contextlib.contextmanager
    def connection(self):
        yield live.conn
    monkeypatch.setattr(NetworkSQLConnector, 'connection', connection)
    raw = live_raw(live)
    for name in ('count_by_status', 'sum_by_month', 'left_join_filter_joined', 'datetime_day_between', 'fan_out_join_with_tie_breaker',
                 'contains_percent_underscore_literal', 'bucket_week', 'all_aggregates_without_groups'):
        compiled = compile_for(raw, live.engine, CORPUS[name], live.name)
        result = connector.execute(compiled.sql, compiled.parameters, 1000)
        assert [c['name'] for c in result['columns']] == [c['label'] for c in compiled.columns]
        compare(live.engine, name, CORPUS[name], compiled, [c['name'] for c in result['columns']], result['rows'], live.rows)


def check_live_identifier_edge_cases(live):
    """Hostile labels and values stay inside quotes and bind parameters on a real server."""
    from sqlalchemy import text
    raw = live_raw(live)
    label = 'x" `y` \'z\' % -- ;'
    body = S(select=[{**C('o', 'id'), 'label': label}], filters=AND(F('o', 'note', 'contains', value="'; DROP TABLE orders; --"),
                                                                 F('o', 'status', 'ne', value='%')), limit=3)
    compiled = compile_for(raw, live.engine, body, live.name)
    result = live.conn.execute(text(compiled.sql), compiled.parameters)
    assert list(result.keys()) == [label] and result.fetchall() == []
    assert live.conn.execute(text(f'SELECT COUNT(*) FROM {get_dialect(live.engine).quote(live.name)}.orders')).scalar() == 1305


def live_suite(engine, monkeypatch):
    with live_database(engine) as live:
        check_live_schema(live)
        check_live_buckets(live)
        failures = run_live_corpus(live)
        assert not failures, f'{len(failures)} of {len(CORPUS)} corpus entries failed on {engine}:\n' + '\n'.join(failures)
        check_live_identifier_edge_cases(live)
        check_live_network_connector_path(live, monkeypatch)


def test_postgresql_live_conformance(monkeypatch):
    live_suite('postgresql', monkeypatch)


def test_mysql_live_conformance(monkeypatch):
    live_suite('mysql', monkeypatch)


def test_mariadb_live_conformance(monkeypatch):
    live_suite('mariadb', monkeypatch)
