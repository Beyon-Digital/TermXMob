"""QuerySpec models, the compiler, relations and schema enrichment against a real SQLite file."""
from __future__ import annotations

import copy
import hashlib
import math
import sqlite3

import pytest
import sqlglot
from pydantic import ValidationError

from data_query_fixture import create_sqlite
from termx.data.connectors import SQLiteConnector, validate_read
from termx.data.dialects import get_dialect
from termx.data.query_spec import QuerySpec, SchemaIndex, SpecErrors, compile_spec
from termx.data.relations import suggest_relations
from termx.data.runtime import DataRuntime


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
    return {'kind': kind, 'table': table, 'alias': alias,
            'on': [{'left': C(*left), 'right': C(alias, right)}]}


EXAMPLE = S(
    joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id')],
    select=[C('o', 'id'), C('o', 'status'), C('o', 'amount'), {**C('c', 'name'), 'label': 'Customer'}],
    filters=AND(F('o', 'status', 'in', values=['open', 'paid']), F('o', 'amount', 'gt', value=100)),
    sort=[{**C('o', 'amount'), 'direction': 'desc'}])
EXAMPLE_SQL = '\n'.join([
    'SELECT "o"."id" AS "id", "o"."status" AS "status", "o"."amount" AS "amount", "c"."name" AS "Customer"',
    'FROM "orders" AS "o"',
    'LEFT JOIN "customers" AS "c" ON "o"."customer_id" = "c"."id"',
    'WHERE "o"."status" IN (:p1, :p2) AND "o"."amount" > :p3',
    'ORDER BY "o"."amount" DESC NULLS LAST, "o"."id" ASC NULLS LAST',
    'LIMIT 201 OFFSET 0'])


@pytest.fixture(scope='module')
def shop(tmp_path_factory):
    path = tmp_path_factory.mktemp('shop') / 'shop.sqlite3'
    rows = create_sqlite(path)
    raw = SQLiteConnector(path).schema()
    DataRuntime._annotate_kinds(raw, 'sqlite')
    return type('Shop', (), {'path': path, 'rows': rows, 'raw': raw,
                              'index': SchemaIndex.from_schema(raw, get_dialect('sqlite'))})


def compile_dict(shop, body, engine='sqlite', index=None):
    return compile_spec(QuerySpec.model_validate(body), index or shop.index, get_dialect(engine))


def errors(shop, body, engine='sqlite', index=None):
    with pytest.raises(SpecErrors) as raised:
        compile_dict(shop, body, engine, index)
    return [(e.code, e.path) for e in raised.value.errors]


def execute(shop, compiled, rows=5000):
    return SQLiteConnector(shop.path).execute(compiled.sql, compiled.parameters, rows)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the documented example --------------------------------------------------------------
def test_contract_example_compiles_to_the_documented_statement(shop):
    compiled = compile_dict(shop, EXAMPLE)
    assert compiled.sql == EXAMPLE_SQL
    assert compiled.parameters == {'p1': 'open', 'p2': 'paid', 'p3': 100}
    assert compiled.columns == [
        {'label': 'id', 'value_kind': 'integer', 'role': 'column', 'table_alias': 'o', 'column': 'id'},
        {'label': 'status', 'value_kind': 'text', 'role': 'column', 'table_alias': 'o', 'column': 'status'},
        {'label': 'amount', 'value_kind': 'number', 'role': 'column', 'table_alias': 'o', 'column': 'amount'},
        {'label': 'Customer', 'value_kind': 'text', 'role': 'column', 'table_alias': 'c', 'column': 'name'}]
    assert (compiled.limit, compiled.offset, compiled.fetch_limit) == (200, 0, 201)
    assert compiled.joins == [{'alias': 'c', 'fan_out': 'none', 'message': None}] and compiled.warnings == []
    assert len(compiled.schema_hash) == 16 and int(compiled.schema_hash, 16) >= 0
    validate_read(compiled.sql, 'sqlite', compiled.parameters)
    result = execute(shop, compiled)
    assert [c['name'] for c in result['columns']] == ['id', 'status', 'amount', 'Customer']
    assert len(result['rows']) == 201


def test_summary_columns_and_labels(shop):
    compiled = compile_dict(shop, S(group_by=[{**C('o', 'ordered_at'), 'bucket': 'month', 'label': 'Month'}],
                                    aggregates=[{'fn': 'sum', **C('o', 'amount'), 'label': 'Total amount'}, {'fn': 'count'}]))
    assert compiled.columns == [
        {'label': 'Month', 'value_kind': 'text', 'role': 'group', 'table_alias': 'o', 'column': 'ordered_at', 'bucket': 'month'},
        {'label': 'Total amount', 'value_kind': 'number', 'role': 'aggregate', 'fn': 'sum', 'table_alias': 'o', 'column': 'amount'},
        {'label': 'Number of rows', 'value_kind': 'integer', 'role': 'aggregate', 'fn': 'count'}]
    assert 'GROUP BY CASE WHEN' in compiled.sql and 'ORDER BY CASE WHEN' in compiled.sql
    assert ' AS "Month"' in compiled.sql
    # output aliases never appear in ORDER BY or GROUP BY (PostgreSQL rejects them in expressions)
    order = compiled.sql.split('ORDER BY')[1]
    assert '"Month"' not in order and '"Month"' not in compiled.sql.split('GROUP BY')[1].split('ORDER BY')[0]
    execute(shop, compiled)


def test_default_labels(shop):
    compiled = compile_dict(shop, S(group_by=[C('o', 'status'), {**C('o', 'ordered_at'), 'bucket': 'year'}],
        aggregates=[{'fn': 'count'}, {'fn': 'count', **C('o', 'note')}, {'fn': 'count_distinct', **C('o', 'status')},
                    {'fn': 'sum', **C('o', 'amount')}, {'fn': 'avg', **C('o', 'amount')},
                    {'fn': 'min', **C('o', 'amount')}, {'fn': 'max', **C('o', 'amount')}]))
    assert [c['label'] for c in compiled.columns] == [
        'status', 'ordered_at by year', 'Number of rows', 'Number of note', 'Unique status', 'Total amount',
        'Average amount', 'Smallest amount', 'Largest amount']
    expanded = compile_dict(shop, S(joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id')], limit=5))
    labels = [c['label'] for c in expanded.columns]
    assert labels[:2] == ['o.id', 'customer_id'] and 'c.id' in labels and 'name' in labels
    assert '*' not in expanded.sql.replace('COUNT(*)', '')


# --- model limits ------------------------------------------------------------------------
def many(n, make):
    return [make(i) for i in range(n)]


BAD_MODELS = {
    'unknown key': S(extra=1),
    'pivot key': {**S(), 'pivot': {'rows': []}},
    'five joins': S(joins=many(5, lambda i: JOIN('customers', f'c{i}', ('o', 'customer_id'), 'id'))),
    'join without on': S(joins=[{'kind': 'left', 'table': 'customers', 'alias': 'c', 'on': []}]),
    'five on pairs': S(joins=[{'kind': 'left', 'table': 'customers', 'alias': 'c',
                               'on': many(5, lambda i: {'left': C('o', 'customer_id'), 'right': C('c', 'id')})}]),
    'cross join': S(joins=[{**JOIN('customers', 'c', ('o', 'customer_id'), 'id'), 'kind': 'cross'}]),
    'limit 0': S(limit=0), 'limit 1001': S(limit=1001), 'limit bool': S(limit=True), 'limit float': S(limit=200.0),
    'negative offset': S(offset=-1), 'offset too big': S(offset=1_000_001),
    'between one value': S(filters=AND(F('o', 'amount', 'between', values=[1]))),
    'between three values': S(filters=AND(F('o', 'amount', 'between', values=[1, 2, 3]))),
    'in empty': S(filters=AND(F('o', 'status', 'in', values=[]))),
    'in 201': S(filters=AND(F('o', 'id', 'in', values=list(range(201))))),
    'contains number': S(filters=AND(F('o', 'note', 'contains', value=5))),
    'is_null with value': S(filters=AND(F('o', 'note', 'is_null', value='x'))),
    'eq without value': S(filters=AND(F('o', 'note', 'eq'))),
    'eq with values': S(filters=AND(F('o', 'note', 'eq', value='a', values=['b']))),
    'unknown op': S(filters=AND(F('o', 'note', 'regex', value='a'))),
    'nan value': S(filters=AND(F('o', 'amount', 'gt', value=float('nan')))),
    'inf value': S(filters=AND(F('o', 'amount', 'gt', value=float('inf')))),
    '501 chars': S(filters=AND(F('o', 'note', 'eq', value='x' * 501))),
    'null value': S(filters=AND(F('o', 'note', 'eq', value=None))),
    'list value': S(filters=AND(F('o', 'note', 'eq', value=['a']))),
    'depth 5': S(filters=AND(AND(AND(AND(AND(F('o', 'id', 'eq', value=1))))))),
    'empty nested group': S(filters=AND(OR())),
    '51 leaves': S(filters=AND(*[F('o', 'id', 'eq', value=i) for i in range(51)])),
    '7 group_by': S(group_by=many(7, lambda i: C('o', 'id'))),
    '13 aggregates': S(aggregates=many(13, lambda i: {'fn': 'count'})),
    '5 sorts': S(sort=many(5, lambda i: {**C('o', 'id'), 'direction': 'asc'})),
    '201 select': S(select=many(201, lambda i: C('o', 'id'))),
    'sum without column': S(aggregates=[{'fn': 'sum'}]),
    'half column ref': S(aggregates=[{'fn': 'count', 'column': 'id'}]),
    'bad alias space': S(**{'from': {'table': 'orders', 'alias': 'a b'}}),
    'bad alias quote': S(**{'from': {'table': 'orders', 'alias': 'a" OR "1"="1'}}),
    'alias unicode': S(**{'from': {'table': 'orders', 'alias': 'ａ'}}),
    'alias too long': S(**{'from': {'table': 'orders', 'alias': 'a' * 33}}),
    'alias digit first': S(**{'from': {'table': 'orders', 'alias': '1a'}}),
    'empty label': S(select=[{**C('o', 'id'), 'label': ''}]),
    'long label': S(select=[{**C('o', 'id'), 'label': 'x' * 61}]),
    'sort direction injection': S(sort=[{**C('o', 'id'), 'direction': 'desc; DROP TABLE orders'}]),
    'bad version': S(version=2),
    'bad bucket': S(group_by=[{**C('o', 'ordered_at'), 'bucket': 'fortnight'}]),
}


@pytest.mark.parametrize('name', sorted(BAD_MODELS))
def test_structural_limits_reject_with_validation_errors(name):
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(BAD_MODELS[name])


def test_model_boundaries_that_must_pass():
    QuerySpec.model_validate(S(joins=many(4, lambda i: JOIN('customers', f'c{i}', ('o', 'customer_id'), 'id')), limit=1000, offset=1_000_000))
    QuerySpec.model_validate(S(filters=AND(OR(AND(OR(F('o', 'id', 'eq', value=1)))))))
    QuerySpec.model_validate(S(filters=AND(*[F('o', 'id', 'eq', value=i) for i in range(50)])))
    QuerySpec.model_validate(S(filters=AND(F('o', 'id', 'in', values=list(range(200))))))
    QuerySpec.model_validate(S(select=many(200, lambda i: C('o', 'id')), group_by=[], limit=1))
    spec = QuerySpec.model_validate(S(filters=AND(F('o', 'paid', 'eq', value=True), F('o', 'amount', 'gt', value=1.5))))
    assert spec.limit == 200 and spec.offset == 0
    with pytest.raises(ValidationError):
        spec.limit = 5   # frozen


# --- names and types ---------------------------------------------------------------------
def test_unknown_names_and_aliases(shop):
    assert errors(shop, S(**{'from': {'table': 'Orders', 'alias': 'o'}})) == [('unknown_table', 'from.table')]
    assert errors(shop, S(select=[C('o', 'amout')])) == [('unknown_column', 'select[0].column')]
    assert errors(shop, S(select=[C('o', 'ID')])) == [('unknown_column', 'select[0].column')]
    assert errors(shop, S(select=[C('zz', 'id')])) == [('unknown_alias', 'select[0].table_alias')]
    assert errors(shop, S(select=[C('c', 'id')], joins=[])) == [('unknown_alias', 'select[0].table_alias')]
    assert errors(shop, S(joins=[JOIN('nope', 'c', ('o', 'customer_id'), 'id')])) == [('unknown_table', 'joins[0].table')]
    # a column of another table through the wrong alias
    assert errors(shop, S(select=[C('o', 'name')])) == [('unknown_column', 'select[0].column')]


def test_errors_are_collected_in_spec_order_with_the_documented_messages(shop):
    body = S(select=[C('o', 'id')], filters=AND(F('o', 'id', 'eq', value=1), F('o', 'amout', 'gt', value=1)),
             sort=[{**C('o', 'nope'), 'direction': 'asc'}])
    with pytest.raises(SpecErrors) as raised:
        compile_dict(shop, body)
    assert [(e.code, e.path) for e in raised.value.errors] == [
        ('unknown_column', 'filters.items[1].field.column'), ('unknown_column', 'sort[0].column')]
    assert raised.value.errors[0].message == "Column 'amout' does not exist in table 'orders'."
    both = S(group_by=[{**C('o', 'id'), 'bucket': 'month'}], filters=AND(F('o', 'amout', 'gt', value=1)))
    assert [e for e in errors(shop, both)] == [('unknown_column', 'filters.items[0].field.column'),
                                               ('bucket_requires_date', 'group_by[0].bucket')]


def test_alias_rules(shop):
    assert errors(shop, S(joins=[JOIN('customers', 'O', ('o', 'customer_id'), 'id')])) == [('duplicate_alias', 'joins[0].alias')]
    assert errors(shop, S(joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id'), JOIN('customers', 'C', ('o', 'customer_id'), 'id')])
                  ) == [('duplicate_alias', 'joins[1].alias')]


def test_join_rules(shop):
    later = S(joins=[JOIN('customers', 'c', ('i', 'order_id'), 'id'), JOIN('order_items', 'i', ('o', 'id'), 'order_id')])
    assert ('bad_join', 'joins[0].on[0].left.table_alias') in errors(shop, later)
    wrong_right = S(joins=[{'kind': 'left', 'table': 'customers', 'alias': 'c',
                            'on': [{'left': C('o', 'customer_id'), 'right': C('o', 'id')}]}])
    assert errors(shop, wrong_right) == [('bad_join', 'joins[0].on[0].right.table_alias')]
    mismatch = S(joins=[JOIN('customers', 'c', ('o', 'status'), 'id')])
    assert errors(shop, mismatch) == [('join_type_mismatch', 'joins[0].on[0]')]
    assert errors(shop, S(joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'name')])) == [('join_type_mismatch', 'joins[0].on[0]')]
    compile_dict(shop, S(joins=[JOIN('customers', 'c', ('o', 'ordered_at'), 'created_on')]))   # datetime to date is compatible
    compile_dict(shop, S(joins=[JOIN('customers', 'c', ('o', 'amount'), 'id')]))                 # number to integer is compatible


def test_type_rules(shop):
    assert errors(shop, S(aggregates=[{'fn': 'sum', **C('o', 'status')}])) == [('bad_type', 'aggregates[0].column')]
    assert errors(shop, S(aggregates=[{'fn': 'avg', **C('o', 'note')}])) == [('bad_type', 'aggregates[0].column')]
    pg = SchemaIndex.from_schema(shop.raw, get_dialect('postgresql'))
    assert errors(shop, S(aggregates=[{'fn': 'max', **C('o', 'paid')}]), 'postgresql', pg) == [('bad_type', 'aggregates[0].column')]
    assert errors(shop, S(group_by=[{**C('o', 'status'), 'bucket': 'month'}]), 'postgresql', pg) == [('bucket_requires_date', 'group_by[0].bucket')]
    assert errors(shop, S(group_by=[{**C('o', 'status'), 'bucket': 'month'}]), 'mariadb', pg) == [('bucket_requires_date', 'group_by[0].bucket')]
    sqlite_text = compile_dict(shop, S(group_by=[{**C('o', 'status'), 'bucket': 'month'}]))
    assert [w['code'] for w in sqlite_text.warnings] == ['text_date']
    assert errors(shop, S(group_by=[{**C('o', 'id'), 'bucket': 'day'}])) == [('bucket_requires_date', 'group_by[0].bucket')]
    assert errors(shop, S(select=[C('o', 'id')], aggregates=[{'fn': 'count'}])) == [('summary_conflict', 'select')]
    both = compile_dict(shop, S(select=[C('o', 'id'), C('c', 'id')], joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id')]))
    assert [c['label'] for c in both.columns] == ['o.id', 'c.id']


def test_duplicate_and_bad_labels(shop):
    # a default label that collides with an explicit one is qualified instead of failing
    qualified = compile_dict(shop, S(select=[C('o', 'id'), {**C('o', 'status'), 'label': 'ID'}]))
    assert [c['label'] for c in qualified.columns] == ['o.id', 'ID']
    assert errors(shop, S(select=[{**C('o', 'id'), 'label': 'x'}, {**C('o', 'status'), 'label': 'X'}])) == [('duplicate_label', 'select[1].label')]
    assert errors(shop, S(select=[{**C('o', 'id'), 'label': 'a:b'}])) == [('unsupported_name', 'select[0].label')]
    assert errors(shop, S(select=[{**C('o', 'id'), 'label': 'a\nb'}])) == [('unsupported_name', 'select[0].label')]
    assert errors(shop, S(group_by=[C('o', 'status')], aggregates=[{'fn': 'count', 'label': 'status'}])) == [('duplicate_label', 'aggregates[0].label')]
    assert errors(shop, S(group_by=[C('o', 'status'), C('o', 'status')])) == [('duplicate_label', 'group_by[1].label')]


def test_operator_rules(shop):
    compile_dict(shop, S(filters=AND(F('o', 'paid', 'gt', value=True))))   # SQLite orders booleans
    pg = SchemaIndex.from_schema(shop.raw, get_dialect('postgresql'))
    assert errors(shop, S(filters=AND(F('o', 'paid', 'gt', value=True))), 'postgresql', pg) == [('bad_operator', 'filters.items[0].op')]
    assert errors(shop, S(filters=AND(F('o', 'paid', 'between', values=[False, True]))), 'mysql', pg) == [('bad_operator', 'filters.items[0].op')]
    bad_value = errors(shop, S(filters=AND(F('o', 'amount', 'gt', value='abc'))))
    assert bad_value == [('bad_value', 'filters.items[0].value')]
    with pytest.raises(SpecErrors) as raised:
        compile_dict(shop, S(filters=AND(F('o', 'amount', 'gt', value='abc'))))
    assert 'amount' in raised.value.errors[0].message and 'abc' in raised.value.errors[0].message
    assert errors(shop, S(filters=AND(F('o', 'ordered_at', 'gt', value='yesterday')))) == [('bad_value', 'filters.items[0].value')]
    assert errors(shop, S(filters=AND(F('o', 'ordered_at', 'between', values=['2026-01-01', 'x'])))) == [('bad_value', 'filters.items[0].values[1]')]
    assert errors(shop, S(filters=AND(F('o', 'id', 'in', values=[1, 'x'])))) == [('bad_value', 'filters.items[0].values[1]')]
    assert errors(shop, S(filters=AND(F('o', 'note', 'contains', value='a\x00b')))) == [('bad_value', 'filters.items[0].value')]
    assert errors(shop, S(filters=AND(F('o', 'ordered_at', 'lte', value='9999-12-31')))) == [('bad_value', 'filters.items[0].value')]


def test_too_many_bound_values(shop):
    body = S(filters=OR(*[F('o', 'id', 'in', values=list(range(200))) for i in range(6)]))
    assert errors(shop, body) == [('too_many_values', 'filters')]
    compile_dict(shop, S(filters=OR(*[F('o', 'id', 'in', values=list(range(200))) for i in range(5)])))


def test_sort_rules(shop):
    assert errors(shop, S(sort=[{'output': 'amount; DELETE', 'direction': 'asc'}])) == [('bad_sort', 'sort[0].output')]
    assert errors(shop, S(select=[C('o', 'id')], sort=[{'output': 'status'}])) == [('bad_sort', 'sort[0].output')]
    ok = compile_dict(shop, S(select=[C('o', 'id')], sort=[{**C('o', 'amount'), 'direction': 'desc'}]))   # detail may sort by an unselected column
    assert ok.sql.count('ORDER BY "o"."amount" DESC NULLS LAST, "o"."id"') == 1
    grouped = S(group_by=[C('o', 'status')], aggregates=[{'fn': 'count'}])
    assert errors(shop, {**grouped, 'sort': [{**C('o', 'amount')}]}) == [('bad_sort', 'sort[0].column')]
    assert errors(shop, {**grouped, 'sort': [{**C('o', 'nope')}]}) == [('unknown_column', 'sort[0].column')]
    by_output = compile_dict(shop, {**grouped, 'sort': [{'output': 'Number of rows', 'direction': 'desc'}]})
    assert 'ORDER BY COUNT(*) DESC NULLS LAST, "o"."status" ASC NULLS LAST' in by_output.sql
    by_group = compile_dict(shop, {**grouped, 'sort': [{**C('o', 'status'), 'direction': 'desc'}]})
    assert by_group.sql.count('"o"."status"') == 3 and 'ORDER BY "o"."status" DESC NULLS LAST\n' in by_group.sql


def test_unsupported_names_are_reported_only_when_referenced(shop):
    assert errors(shop, S(**{'from': {'table': 'weird', 'alias': 'w'}}, select=[C('w', 'we:ird')])) == [('unsupported_name', 'select[0].column')]
    expanded = compile_dict(shop, S(**{'from': {'table': 'weird', 'alias': 'w'}}))
    assert [c['label'] for c in expanded.columns] == ['id']
    unreadable = errors(shop, S(**{'from': {'table': 'unsafe_view', 'alias': 'u'}}))
    assert unreadable == [('unknown_table', 'from.table')]
    compile_dict(shop, S(**{'from': {'table': 'order_totals', 'alias': 't'}}))   # readable view


# --- filters -----------------------------------------------------------------------------
def test_filter_shapes(shop):
    def where(node):
        return compile_dict(shop, S(select=[C('o', 'id')], filters=node)).sql.split('WHERE ')[1].split('\nORDER')[0]
    assert where(AND(F('o', 'status', 'ne', value='closed'))) == '("o"."status" <> :p1 OR "o"."status" IS NULL)'
    assert where(AND(F('o', 'status', 'not_in', values=['a', 'b']))) == '("o"."status" NOT IN (:p1, :p2) OR "o"."status" IS NULL)'
    assert where(AND(F('o', 'amount', 'between', values=[1, 2]))) == '"o"."amount" BETWEEN :p1 AND :p2'
    assert where(AND(F('o', 'note', 'is_null'))) == '"o"."note" IS NULL'
    assert where(AND(F('o', 'note', 'not_null'))) == '"o"."note" IS NOT NULL'
    assert where(AND(F('o', 'note', 'contains', value='50%_'))) == "LOWER(\"o\".\"note\") LIKE LOWER(:p1) ESCAPE '!'"
    assert where(AND(F('o', 'id', 'starts_with', value='1'))) == "LOWER(CAST(\"o\".\"id\" AS TEXT)) LIKE LOWER(:p1) ESCAPE '!'"
    assert where(OR(F('o', 'id', 'eq', value=1), F('o', 'id', 'eq', value=2))) == '("o"."id" = :p1 OR "o"."id" = :p2)'
    assert where(OR(F('o', 'id', 'eq', value=1))) == '"o"."id" = :p1'
    assert where(AND(F('o', 'id', 'eq', value=1), OR(F('o', 'id', 'eq', value=2), F('o', 'id', 'eq', value=3)))
                 ) == '"o"."id" = :p1 AND ("o"."id" = :p2 OR "o"."id" = :p3)'
    compiled = compile_dict(shop, S(filters=AND(F('o', 'note', 'contains', value='50%_!x'))))
    assert compiled.parameters == {'p1': '%50!%!_!!x%'}
    starts = compile_dict(shop, S(filters=AND(F('o', 'note', 'starts_with', value='a_'))))
    assert starts.parameters == {'p1': 'a!_%'}


def test_whole_day_ranges_on_datetime_columns(shop):
    def run(op, value=None, values=None):
        leaf = F('o', 'ordered_at', op, **({'value': value} if values is None else {'values': values}))
        compiled = compile_dict(shop, S(select=[C('o', 'id')], filters=AND(leaf)))
        return compiled, [r[0] for r in execute(shop, compiled)['rows']]
    compiled, ids = run('eq', '2026-11-12')
    assert compiled.parameters == {'p1': '2026-11-12', 'p2': '2026-11-13'} and ids[:3] == [5, 7, 8]
    assert {5, 7, 8} <= set(ids) and 9 not in ids
    assert 9 in run('gt', '2026-11-12')[1] and 8 not in run('gt', '2026-11-12')[1]
    assert 7 in run('gte', '2026-11-12')[1] and 6 in run('gte', '2026-11-12')[1]
    assert 7 not in run('lt', '2026-11-12')[1] and 4 in run('lt', '2026-11-12')[1]
    assert 8 in run('lte', '2026-11-12')[1] and 9 not in run('lte', '2026-11-12')[1]
    between = run('between', values=['2026-11-12', '2026-11-12'])
    assert between[0].parameters == {'p1': '2026-11-12', 'p2': '2026-11-13'} and set(between[1]) >= {5, 7, 8}
    assert '(("o"."ordered_at" >= :p1 AND "o"."ordered_at" < :p2))' not in between[0].sql
    assert '("o"."ordered_at" >= :p1 AND "o"."ordered_at" < :p2)' in between[0].sql
    exact = run('eq', '2026-11-12 14:30:00')
    assert exact[1] == [5] and exact[0].parameters == {'p1': '2026-11-12 14:30:00'}
    assert 5 not in run('ne', '2026-11-12')[1] and None not in run('ne', '2026-11-12')[1]
    days = run('in', values=['2024-02-29', '2026-11-12'])[1]
    assert {3, 5, 7, 8} <= set(days) and 9 not in days
    not_days = run('not_in', values=['2026-11-12'])[1]
    assert 5 not in not_days and 9 in not_days
    date_column = compile_dict(shop, S(**{'from': {'table': 'customers', 'alias': 'c'}}, filters=AND(F('c', 'created_on', 'eq', value='2020-03-01'))))
    assert date_column.parameters == {'p1': '2020-03-01'} and '>=' not in date_column.sql


def test_boolean_values_are_bound_as_integers_on_sqlite(shop):
    compiled = compile_dict(shop, S(filters=AND(F('o', 'paid', 'eq', value=True))))
    assert compiled.parameters == {'p1': 1} and type(compiled.parameters['p1']) is int
    on_pg = compile_dict(shop, S(filters=AND(F('o', 'paid', 'eq', value=True))), 'postgresql')
    assert on_pg.parameters == {'p1': True}


# --- summary and ordering ------------------------------------------------------------------
def test_summary_and_paging_tie_breakers(shop):
    grouped = compile_dict(shop, S(group_by=[C('o', 'status'), C('o', 'paid')], aggregates=[{'fn': 'count'}]))
    assert grouped.sql.split('ORDER BY ')[1].split('\n')[0] == '"o"."status" ASC NULLS LAST, "o"."paid" ASC NULLS LAST'
    single = compile_dict(shop, S(aggregates=[{'fn': 'sum', **C('o', 'amount')}]))
    assert 'ORDER BY' not in single.sql and 'GROUP BY' not in single.sql
    assert single.sql.endswith('LIMIT 201 OFFSET 0')
    no_pk = compile_dict(shop, S(**{'from': {'table': 'loose', 'alias': 'l'}}, limit=3, offset=1))
    assert 'ORDER BY "l"."a" ASC NULLS LAST, "l"."b" ASC NULLS LAST' in no_pk.sql and no_pk.sql.endswith('LIMIT 4 OFFSET 1')


def test_paging_is_stable_and_pages_concatenate_to_the_unpaged_order(shop):
    def ids(offset, limit):
        compiled = compile_dict(shop, S(select=[C('o', 'id')], sort=[{**C('o', 'amount'), 'direction': 'desc'}], limit=limit, offset=offset))
        rows = execute(shop, compiled)['rows']
        return [r[0] for r in rows[:limit]], len(rows) > limit
    full = [r[0] for r in execute(shop, compile_dict(shop, S(select=[C('o', 'id')], sort=[{**C('o', 'amount'), 'direction': 'desc'}], limit=1000)))['rows']][:1000]
    paged = []
    for offset in range(0, 1000, 100):
        page, more = ids(offset, 100)
        paged += page
        assert more
    assert paged == full
    last, more = ids(1300, 100)
    assert len(last) == 5 and not more


def test_fan_out_classification_and_warning(shop):
    full = compile_dict(shop, S(joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id')]))
    assert full.joins[0]['fan_out'] == 'none'
    partial = S(joins=[JOIN('order_items', 'i', ('o', 'id'), 'order_id')], aggregates=[{'fn': 'count'}])
    many_join = compile_dict(shop, partial)
    assert many_join.joins == [{'alias': 'i', 'fan_out': 'many',
                                'message': 'Each orders row can match many order_items rows, so counts and totals may repeat.'}]
    assert many_join.warnings == [{'code': 'fan_out', 'message': many_join.joins[0]['message'], 'join_alias': 'i'}]
    # detail rows: nothing is counted, but the result can have more rows than the base table
    assert compile_dict(shop, {**partial, 'aggregates': []}).warnings == [
        {'code': 'row_multiplication', 'join_alias': 'i',
         'message': 'Each orders row can match many order_items rows, so the result can have more rows than orders.'}]
    assert compile_dict(shop, S(joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id')])).warnings == []
    composite = S(joins=[{'kind': 'left', 'table': 'order_items', 'alias': 'i', 'on': [
        {'left': C('o', 'id'), 'right': C('i', 'order_id')}, {'left': C('o', 'status'), 'right': C('i', 'sku')}]}], aggregates=[{'fn': 'count'}])
    assert compile_dict(shop, composite).joins[0]['fan_out'] == 'none'   # the whole composite key is matched
    no_key = S(**{'from': {'table': 'customers', 'alias': 'c'}}, joins=[JOIN('loose', 'l', ('c', 'id'), 'a')], aggregates=[{'fn': 'count'}])
    assert compile_dict(shop, no_key).joins[0]['fan_out'] == 'unknown'
    # fan-out joins add the joined key to the paging order
    detail = compile_dict(shop, S(joins=[JOIN('order_items', 'i', ('o', 'id'), 'order_id')], select=[C('o', 'id'), C('i', 'sku')]))
    assert 'ORDER BY "o"."id" ASC NULLS LAST, "i"."order_id" ASC NULLS LAST, "i"."sku" ASC NULLS LAST' in detail.sql


def test_four_joins_execute(shop):
    body = S(joins=[JOIN('customers', 'c', ('o', 'customer_id'), 'id'), JOIN('customers', 'c2', ('o', 'customer_id'), 'id', 'inner'),
                    JOIN('order_items', 'i', ('o', 'id'), 'order_id'), JOIN('orders', 'o2', ('i', 'order_id'), 'id')],
             select=[C('o', 'id'), C('c', 'name'), {**C('c2', 'region'), 'label': 'Region'}, C('i', 'sku'), {**C('o2', 'status'), 'label': 'Again'}], limit=50)
    result = execute(shop, compile_dict(shop, body))
    assert len(result['rows']) == 51


def test_deterministic_output(shop):
    first, second = compile_dict(shop, EXAMPLE), compile_dict(shop, copy.deepcopy(EXAMPLE))
    assert (first.sql, first.parameters, first.schema_hash) == (second.sql, second.parameters, second.schema_hash)
    other = compile_dict(shop, S(select=[C('o', 'id')]))
    assert other.schema_hash != first.schema_hash


# --- values never reach the statement text -------------------------------------------------
VALUES = ["' OR 1=1 --", '\\', '%', '_', 'x' * 500, "O'Brien", '"; DROP TABLE orders; --', '’', '!', '\n', '1; SELECT 1']


@pytest.mark.parametrize('value', VALUES)
def test_every_value_is_bound(shop, value):
    before = digest(shop.path)
    body = S(select=[C('o', 'id')], filters=AND(F('o', 'status', 'eq', value=value), F('o', 'note', 'contains', value=value),
                                                 F('o', 'status', 'in', values=[value, 'a']), F('o', 'status', 'ne', value=value)))
    compiled = compile_dict(shop, body)
    assert set(compiled.parameters) == {f'p{i}' for i in range(1, len(compiled.parameters) + 1)}
    stripped = compiled.sql
    for needle in ('DROP', 'OR 1=1', 'SELECT 1', "O'Brien", '\\'):
        assert needle not in stripped
    assert len(sqlglot.parse(compiled.sql, read='sqlite')) == 1
    execute(shop, compiled)
    assert digest(shop.path) == before


def test_only_limit_and_offset_numbers_are_rendered(shop):
    import re
    compiled = compile_dict(shop, S(select=[C('o', 'id')], filters=AND(F('o', 'id', 'gt', value=12345), F('o', 'amount', 'lt', value=9876.5)),
                                    limit=77, offset=13))
    bare = re.sub(r'"[^"]*"', '', compiled.sql)
    bare = re.sub(r':p\d+', '', bare)
    assert re.findall(r'\d+', bare) == ['78', '13']
    assert compiled.parameters == {'p1': 12345, 'p2': 9876.5}


# --- injection attempts ---------------------------------------------------------------------
HOSTILE = 'orders" ; DROP TABLE orders; --'
INJECTIONS = {
    'table name': S(**{'from': {'table': HOSTILE, 'alias': 'o'}}),
    'join table name': S(joins=[JOIN(HOSTILE, 'c', ('o', 'customer_id'), 'id')]),
    'column quote and or': S(select=[C('o', 'id") OR 1=1 --')]),
    'column semicolon': S(select=[C('o', 'amount; DELETE FROM orders')]),
    'alias quote': S(**{'from': {'table': 'orders', 'alias': 'a" OR "1"="1'}}),
    'alias space': S(**{'from': {'table': 'orders', 'alias': 'o x'}}),
    'alias unicode': S(**{'from': {'table': 'orders', 'alias': 'ｏ'}}),
    'label sqlite_master': S(select=[{**C('o', 'id'), 'label': 'x" FROM sqlite_master --'}]),
    'label bind lookalike': S(select=[{**C('o', 'id'), 'label': 'a:b'}]),
    'label p1': S(select=[{**C('o', 'id'), 'label': ':p1'}]),
    'label nul': S(select=[{**C('o', 'id'), 'label': 'a\x00b'}]),
    'label newline': S(select=[{**C('o', 'id'), 'label': 'a\nb'}]),
    'label 61': S(select=[{**C('o', 'id'), 'label': 'a' * 61}]),
    'sort output': S(select=[C('o', 'id')], sort=[{'output': 'amount; DELETE', 'direction': 'asc'}]),
    'sort unselected summary': S(group_by=[C('o', 'status')], aggregates=[{'fn': 'count'}], sort=[C('o', 'amount')]),
    'sort direction': S(sort=[{**C('o', 'id'), 'direction': 'desc; DROP'}]),
    'sqlite_master table': S(**{'from': {'table': 'sqlite_master', 'alias': 'm'}}),
    'main.sqlite_master': S(**{'from': {'table': 'main.sqlite_master', 'alias': 'm'}}),
    'sqlite_schema': S(**{'from': {'table': 'sqlite_schema', 'alias': 'm'}}),
    'sqlite_temp_master': S(**{'from': {'table': 'sqlite_temp_master', 'alias': 'm'}}),
    'load_extension column': S(select=[C('o', "load_extension('x')")]),
    'qualified column': S(select=[C('o', 'main.orders.id')]),
    'fullwidth quote': S(select=[C('o', 'id＂')]),
    'fullwidth table': S(**{'from': {'table': 'orders＂', 'alias': 'o'}}),
    'pragma table': S(**{'from': {'table': 'pragma_table_info("orders")', 'alias': 'p'}}),
    'value in filter column': S(filters=AND(F('o', "id' OR '1'='1", 'eq', value=1))),
    'group column': S(group_by=[C('o', 'status") ; DROP TABLE orders; --')], aggregates=[{'fn': 'count'}]),
    'aggregate column': S(aggregates=[{'fn': 'sum', **C('o', 'amount) FROM orders; DROP TABLE orders; --')}]),
}


@pytest.mark.parametrize('name', sorted(INJECTIONS))
def test_injection_attempts_are_rejected_or_stay_inside_quotes(shop, name):
    before = digest(shop.path)
    outcome = 'rejected'
    try:
        spec = QuerySpec.model_validate(INJECTIONS[name])
        compiled = compile_spec(spec, shop.index, get_dialect('sqlite'))
        outcome = 'compiled'
    except (ValidationError, SpecErrors):
        pass
    if outcome == 'compiled':
        # Executed: hostile text may only be a quoted label or a bound value.
        tree = sqlglot.parse_one(compiled.sql, read='sqlite')
        assert {t.name for t in tree.find_all(sqlglot.exp.Table)} <= {'orders', 'customers'}
        validate_read(compiled.sql, 'sqlite', compiled.parameters)
        result = execute(shop, compiled)
        assert [c['name'] for c in result['columns']] == [c['label'] for c in compiled.columns]
    assert digest(shop.path) == before
    with sqlite3.connect(shop.path) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {'customers', 'orders', 'order_items', 'weird', 'loose'}


def test_hostile_label_executes_as_a_plain_column_name(shop):
    label = 'x" FROM sqlite_master --'
    compiled = compile_dict(shop, S(select=[{**C('o', 'id'), 'label': label}], limit=1))
    assert '"x"" FROM sqlite_master --"' in compiled.sql
    assert [c['name'] for c in execute(shop, compiled)['columns']] == [label]


# --- relations --------------------------------------------------------------------------------
def make_index(tables):
    raw = {'tables': [{'name': name, 'schema': None, 'kind': 'table', 'columns': [
        {'name': c, 'type': t, 'primary_key': c in pk} for c, t in cols.items()],
        'foreign_keys': fks} for name, cols, pk, fks in tables]}
    return SchemaIndex.from_schema(raw, get_dialect('sqlite'))


def test_relations_on_the_fixture_database(shop):
    result = suggest_relations(shop.index, get_dialect('sqlite'), 'orders')
    assert [(r['child_table'], r['parent_table'], r['source']) for r in result] == [
        ('order_items', 'orders', 'foreign_key'), ('orders', 'customers', 'foreign_key')]
    assert result[1] == {'child_table': 'orders', 'child_columns': ['customer_id'], 'parent_table': 'customers',
                         'parent_columns': ['id'], 'source': 'foreign_key', 'confidence': 'declared',
                         'cardinality': 'many_to_one', 'sentence': 'Orders to Customers: customer_id matches id'}
    assert suggest_relations(shop.index, get_dialect('sqlite'), 'weird') == []


def test_relations_name_matches_plurals_and_composites():
    index = make_index([
        ('customers', {'id': 'INTEGER', 'name': 'TEXT'}, ['id'], []),
        ('categories', {'id': 'INTEGER'}, ['id'], []),
        ('regions', {'region_id': 'INTEGER', 'name': 'TEXT'}, ['region_id'], []),
        ('boxes', {'id': 'INTEGER'}, ['id'], []),
        ('orders', {'id': 'INTEGER', 'customer_id': 'INTEGER', 'categoryid': 'INTEGER', 'region_id': 'INTEGER',
                    'box_id': 'INTEGER', 'status_id': 'TEXT', 'order_id': 'INTEGER'}, ['id'],
         [{'columns': ['order_id'], 'referred_table': 'orders', 'referred_columns': ['id']}]),
        ('lines', {'order_id': 'INTEGER', 'n': 'INTEGER', 'customer_id': 'INTEGER'}, ['order_id', 'n'], []),
        ('notes', {'a': 'INTEGER', 'b': 'INTEGER'}, [], [{'columns': ['a', 'b'], 'referred_table': 'lines', 'referred_columns': ['order_id', 'n']}]),
        ('statuses', {'id': 'INTEGER'}, ['id'], [])])
    result = suggest_relations(index, get_dialect('sqlite'))
    pairs = [(r['child_table'], tuple(r['child_columns']), r['parent_table'], r['source']) for r in result]
    assert pairs[0] == ('notes', ('a', 'b'), 'lines', 'foreign_key')        # declared first
    assert ('orders', ('order_id',), 'orders', 'foreign_key') in pairs
    names = [p for p in pairs if p[3] == 'name_match']
    assert ('orders', ('customer_id',), 'customers', 'name_match') in names
    assert ('orders', ('categoryid',), 'categories', 'name_match') in names     # ies/y plural, no underscore
    assert ('orders', ('box_id',), 'boxes', 'name_match') in names              # es plural
    assert ('orders', ('region_id',), 'regions', 'name_match') in names         # same-name primary key
    assert ('lines', ('customer_id',), 'customers', 'name_match') in names
    assert not any(p[1] == ('status_id',) for p in pairs)                       # text vs integer key
    assert not any(p[1] == ('order_id',) and p[0] == 'orders' and p[3] == 'name_match' for p in pairs)   # declared pair is not repeated
    sentence = next(r for r in result if r['child_table'] == 'notes')['sentence']
    assert sentence == 'Notes to Lines: a, b match order_id, n'
    assert pairs == [(r['child_table'], tuple(r['child_columns']), r['parent_table'], r['source']) for r in suggest_relations(index, get_dialect('sqlite'))]
    only = suggest_relations(index, get_dialect('sqlite'), 'categories')
    assert {r['child_table'] for r in only} == {'orders'} and all('categories' in (r['child_table'], r['parent_table']) for r in only)


def test_relations_are_capped_at_200():
    tables = [('parent', {'id': 'INTEGER'}, ['id'], [])] + [
        (f'child_{i:03d}', {'id': 'INTEGER', 'parent_id': 'INTEGER'}, ['id'],
         [{'columns': ['parent_id'], 'referred_table': 'parent', 'referred_columns': ['id']}]) for i in range(250)]
    result = suggest_relations(make_index(tables), get_dialect('sqlite'))
    assert len(result) == 200 and result[0]['child_table'] == 'child_000' and result[-1]['child_table'] == 'child_199'


# --- schema enrichment and the authorizer ------------------------------------------------------
def test_schema_has_value_kinds_foreign_keys_and_row_estimates(shop):
    tables = {t['name']: t for t in shop.raw['tables']}
    assert all('value_kind' in c for t in tables.values() for c in t['columns'])
    assert {c['name']: c['value_kind'] for c in tables['orders']['columns']} == {
        'id': 'integer', 'customer_id': 'integer', 'status': 'text', 'amount': 'number', 'ordered_at': 'datetime',
        'note': 'text', 'paid': 'boolean'}
    assert {c['name']: c['value_kind'] for c in tables['loose']['columns']} == {'a': 'other', 'b': 'other'}
    assert tables['orders']['foreign_keys'] == [{'columns': ['customer_id'], 'referred_table': 'customers', 'referred_columns': ['id']}]
    assert tables['order_items']['foreign_keys'] == [{'columns': ['order_id'], 'referred_table': 'orders', 'referred_columns': ['id']}]
    assert tables['customers']['foreign_keys'] == [] and all(t['row_estimate'] is None for t in tables.values())
    assert tables['unsafe_view']['readable'] is False and tables['unsafe_view']['foreign_keys'] == []


def test_declared_types_keep_the_width_mysql_uses_for_booleans():
    from sqlalchemy.dialects import mysql, postgresql
    from termx.data.connectors import declared_type
    assert declared_type(mysql.TINYINT(display_width=1), mysql.dialect()) == 'TINYINT(1)'
    assert get_dialect('mysql').classify_type(declared_type(mysql.TINYINT(display_width=1), mysql.dialect())) == 'boolean'
    assert get_dialect('mysql').classify_type(declared_type(mysql.TINYINT(), mysql.dialect())) == 'integer'
    assert declared_type(postgresql.TIMESTAMP(), postgresql.dialect()) == 'TIMESTAMP WITHOUT TIME ZONE'
    from sqlalchemy.types import NullType
    assert declared_type(NullType(), mysql.dialect()) == 'NULL'


def test_implicit_foreign_key_targets_resolve_to_the_parent_primary_key(tmp_path):
    path = tmp_path / 'fk.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE parent(a INTEGER, b INTEGER, PRIMARY KEY(a, b))')
        db.execute('CREATE TABLE child(x INTEGER, y INTEGER, FOREIGN KEY(x, y) REFERENCES parent)')
    child = next(t for t in SQLiteConnector(path).schema()['tables'] if t['name'] == 'child')
    assert child['foreign_keys'] == [{'columns': ['x', 'y'], 'referred_table': 'parent', 'referred_columns': ['a', 'b']}]


def test_authorizer_allows_foreign_key_list_only_in_schema_mode(shop):
    connector = SQLiteConnector(shop.path)
    with connector.connection(schema=True) as db:
        assert db.execute('PRAGMA foreign_key_list("orders")').fetchall()
        assert db.execute('PRAGMA table_xinfo("orders")').fetchall()
    with connector.connection(schema=False) as db:
        for sql in ('PRAGMA foreign_key_list("orders")', 'PRAGMA table_xinfo("orders")', 'PRAGMA table_info("orders")'):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(sql).fetchall()
    with connector.connection(schema=True) as db:
        for sql in ('PRAGMA table_info("orders")', 'PRAGMA writable_schema=ON', 'PRAGMA database_list', 'PRAGMA query_only=OFF',
                    'PRAGMA index_list("orders")'):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(sql).fetchall()
    with sqlite3.connect(shop.path) as db:
        assert db.execute('PRAGMA writable_schema').fetchone()[0] == 0


def test_compile_never_reads_rows_or_sqlite_master(shop):
    sql = compile_dict(shop, EXAMPLE).sql.lower()
    assert 'sqlite_' not in sql and 'pragma' not in sql
    assert math.isfinite(len(sql))
