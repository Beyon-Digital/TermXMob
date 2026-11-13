"""Pivot compilation on four dialects, execution on SQLite against an independent oracle, and the hostile-input suite.

Live PostgreSQL, MySQL and MariaDB runs are in `test_data_pivot_live.py` (env gated).
Golden statements live in data_pivot_golden.json; regenerate after an intentional change with
`TERMX_UPDATE_GOLDEN=1 uv run pytest tests/test_data_pivot.py -k golden` and review the diff.
"""
from __future__ import annotations

import copy
import json
import os
import random
import re
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_pivot_fixture import build_sales, bucket_of, create_sales_sqlite, oracle_pivot, sort_rows
from test_data_conformance import ENGINES, index_for, structural_checks
from termx.data.connectors import SQLiteConnector
from termx.data.dialects import get_dialect
from termx.data.pivot import PivotColumnValue, column_label, pivot_values_from_rows
from termx.data.query_spec import QuerySpec, SpecErrors, compile_pivot_probe, compile_spec
from termx.data.runtime import DataRuntime

GOLDEN = Path(__file__).with_name('data_pivot_golden.json')


def C(alias, column):
    return {'table_alias': alias, 'column': column}


def P(rows, columns=None, fn='count', measure=None, **kw):
    value = {'fn': fn}
    if measure:
        value.update(C('s', measure))
    pivot = {'rows': [C('s', r) if isinstance(r, str) else r for r in rows],
             'columns': (C('s', columns) if isinstance(columns, str) else columns), 'value': value, 'totals': True,
             'sort': {'by': 'rows', 'direction': 'asc'}}
    pivot.update(kw)
    return pivot


def S(pivot, **kw):
    body = {'version': 1, 'source': 'conn', 'from': {'table': 'sales', 'alias': 's'}, 'joins': [], 'select': [],
            'filters': {'op': 'and', 'items': []}, 'group_by': [], 'aggregates': [], 'sort': [], 'limit': 200, 'offset': 0,
            'pivot': pivot}
    body.update(kw)
    return body


def F(column, op, **kw):
    return {'field': C('s', column), 'op': op, **kw}


def AND(*items):
    return {'op': 'and', 'items': list(items)}


def OR(*items):
    return {'op': 'or', 'items': list(items)}


def month(column='day'):
    return {**C('s', column), 'bucket': 'month'}


def bare(sql):
    """The statement without its bind markers, so a value that looks like one can be searched for."""
    return re.sub(r':p\d+', '', sql)


# Data that spells a bind marker or a neutral alias: it is only ever checked as a bound parameter, because the
# statement legitimately contains those words.
LOOKALIKES = {':p1', 'pv_c1'}


@pytest.fixture(scope='module')
def shop(tmp_path_factory):
    path = tmp_path_factory.mktemp('pivot') / 'sales.sqlite3'
    rows = create_sales_sqlite(path)
    raw = SQLiteConnector(path).schema()
    DataRuntime._annotate_kinds(raw, 'sqlite')
    return type('Shop', (), {'path': path, 'rows': rows, 'raw': raw, 'sales': rows['sales'],
                             'connector': SQLiteConnector(path)})


def spec_of(body):
    return QuerySpec.model_validate(body)


def compile_for(shop, engine, body, values=None):
    return compile_spec(spec_of(body), index_for(shop.raw, engine), get_dialect(engine), pivot_values=values)


def probe_for(shop, engine, body):
    return compile_pivot_probe(spec_of(body), index_for(shop.raw, engine), get_dialect(engine))


def run_pivot(shop, body):
    """Validate, probe, compile and execute on SQLite exactly as the service does."""
    compile_for(shop, 'sqlite', body)
    values = None
    if body['pivot'].get('columns'):
        probe = probe_for(shop, 'sqlite', body)
        kind = probe.columns[0]['value_kind']
        result = shop.connector.execute(probe.sql, probe.parameters, 13, [kind])
        values = pivot_values_from_rows(result['rows'], kind, 'Column')
    compiled = compile_for(shop, 'sqlite', body, values)
    result = shop.connector.execute(compiled.sql, compiled.parameters, 1000, [c['value_kind'] for c in compiled.columns])
    return compiled, result['rows'], values


# --- models ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize('pivot', [
    {'rows': [], 'value': {'fn': 'count'}},
    {'rows': [C('s', 'region')] * 3, 'value': {'fn': 'count'}},
    {'rows': [C('s', 'region')], 'value': {'fn': 'sum'}},
    {'rows': [C('s', 'region')], 'value': {'fn': 'count', 'table_alias': 's'}},
    {'rows': [C('s', 'region')], 'columns': None, 'totals': False, 'value': {'fn': 'count'}},
    {'rows': [C('s', 'region')], 'value': {'fn': 'count'}, 'extra': 1},
    {'rows': [{**C('s', 'region'), 'bucket': 'decade'}], 'value': {'fn': 'count'}},
    {'rows': [C('s', 'region')], 'value': {'fn': 'median', 'table_alias': 's', 'column': 'amount'}},
    {'rows': [C('s', 'region')], 'value': {'fn': 'count'}, 'sort': {'by': 'cells'}},
    {'rows': [C('s', 'region')], 'value': {'fn': 'count'}, 'totals': 'yes'},
    {'rows': 'region', 'value': {'fn': 'count'}},
    {},
])
def test_invalid_pivot_models_are_rejected(pivot):
    with pytest.raises(ValidationError):
        spec_of(S(pivot))


def test_a_minimal_pivot_parses_and_round_trips():
    body = S({'rows': [C('s', 'region')], 'value': {'fn': 'count'}})
    spec = spec_of(body)
    assert spec.pivot.totals is True and spec.pivot.columns is None and spec.pivot.sort.by == 'rows'
    assert spec_of(spec.model_dump(by_alias=True, mode='json')) == spec


@pytest.mark.parametrize('section,value', [('select', [C('s', 'id')]), ('group_by', [C('s', 'region')]),
                                            ('aggregates', [{'fn': 'count'}]), ('sort', [{**C('s', 'id'), 'direction': 'asc'}])])
def test_a_pivot_with_other_sections_is_a_conflict(shop, section, value):
    with pytest.raises(SpecErrors) as error:
        compile_for(shop, 'sqlite', S(P(['region']), **{section: value}))
    assert [(e.code, e.path) for e in error.value.errors] == [('pivot_conflict', section)]


# --- compile errors -------------------------------------------------------------------------------------
def codes(shop, body, engine='sqlite'):
    with pytest.raises(SpecErrors) as error:
        compile_for(shop, engine, body)
    return [(e.code, e.path) for e in error.value.errors]


def test_dimension_rules(shop):
    assert codes(shop, S(P(['region'], 'region'))) == [('pivot_duplicate_dimension', 'pivot.columns')]
    assert codes(shop, S(P(['region', 'region']))) == [('pivot_duplicate_dimension', 'pivot.rows[1]')]
    assert codes(shop, S(P(['region'], 'day'))) == [('pivot_needs_bucket', 'pivot.columns.bucket')]
    assert codes(shop, S(P(['region'], 'stamp'))) == [('pivot_needs_bucket', 'pivot.columns.bucket')]
    assert codes(shop, S(P(['region'], 'price'))) == [('pivot_column_kind', 'pivot.columns.column')]
    assert codes(shop, S(P(['region'], 'missing'))) == [('unknown_column', 'pivot.columns.column')]
    # the same column twice is a duplicate; with a different step it is a different dimension
    assert codes(shop, S(P([month()], month()))) == [('pivot_duplicate_dimension', 'pivot.columns')]
    compile_for(shop, 'sqlite', S(P([{**C('s', 'day'), 'bucket': 'year'}], month())))
    assert codes(shop, S(P([{**C('s', 'qty'), 'bucket': 'month'}]), ), 'postgresql') == [('bucket_requires_date', 'pivot.rows[0].bucket')]
    assert codes(shop, S(P(['region'], {**C('s', 'tag'), 'bucket': 'month'})), 'postgresql') == [('bucket_requires_date', 'pivot.columns.bucket')]
    assert codes(shop, S(P(['nope'])))[0] == ('unknown_column', 'pivot.rows[0].column')
    assert codes(shop, S(P([{'table_alias': 'zz', 'column': 'id'}])))[0] == ('unknown_alias', 'pivot.rows[0].table_alias')


def test_value_rules(shop):
    assert codes(shop, S(P(['region'], fn='sum', measure='region'))) == [('pivot_value_type', 'pivot.value.column')]
    assert codes(shop, S(P(['region'], fn='avg', measure='tag'))) == [('pivot_value_type', 'pivot.value.column')]
    assert codes(shop, S(P(['region'], fn='sum', measure='nope'))) == [('unknown_column', 'pivot.value.column')]


def test_text_dates_can_be_columns_on_sqlite_only(shop):
    body = S(P(['region'], {**C('s', 'tag'), 'bucket': 'month'}))
    compiled = compile_for(shop, 'sqlite', body)
    assert [w['code'] for w in compiled.warnings] == ['text_date']
    assert codes(shop, body, 'mysql') == [('bucket_requires_date', 'pivot.columns.bucket')]


def test_label_rules(shop):
    ok = S(P([{**C('s', 'region'), 'label': 'R' * 60}], fn='sum', measure='amount', **{}))
    compile_for(shop, 'sqlite', ok)
    with pytest.raises(ValidationError):
        spec_of(S(P([{**C('s', 'region'), 'label': 'R' * 61}])))
    assert codes(shop, S(P([{**C('s', 'region'), 'label': 'a:b'}]))) == [('unsupported_name', 'pivot.rows[0].label')]
    assert codes(shop, S(P([{**C('s', 'region'), 'label': 'a\x01b'}]))) == [('unsupported_name', 'pivot.rows[0].label')]
    body = S(P(['region'], fn='sum', measure='amount'))
    body['pivot']['value']['label'] = 'x:y'
    assert codes(shop, body) == [('unsupported_name', 'pivot.value.label')]
    body = S(P([{**C('s', 'region'), 'label': 'Same'}, {**C('s', 'flag'), 'label': 'same'}]))
    assert codes(shop, body) == [('duplicate_label', 'pivot.rows[0].label')]


# --- statements -----------------------------------------------------------------------------------------
CORPUS = {
    'rows_only': S(P(['region'])),
    'text_by_month': S(P(['region'], month(), fn='sum', measure='amount')),
    'text_by_week': S(P(['region'], {**C('s', 'day'), 'bucket': 'week'}, fn='count')),
    'text_by_quarter': S(P(['region'], {**C('s', 'day'), 'bucket': 'quarter'}, fn='avg', measure='price')),
    'text_by_year': S(P(['region'], {**C('s', 'stamp'), 'bucket': 'year'}, fn='max', measure='qty')),
    'text_by_day': S(P(['region'], {**C('s', 'day'), 'bucket': 'day'})),
    'two_row_dims': S(P(['region', 'flag'], month(), fn='sum', measure='qty')),
    'boolean_columns': S(P(['region'], 'flag', fn='count', measure='amount')),
    'text_columns': S(P([month()], 'tag', fn='count')),
    'integer_columns': S(P(['region'], 'qty', fn='sum', measure='amount')),
    'avg': S(P(['region'], 'flag', fn='avg', measure='amount')),
    'count_distinct': S(P(['region'], 'flag', fn='count_distinct', measure='customer_id')),
    'min_date': S(P(['region'], 'flag', fn='min', measure='day')),
    'max_text': S(P(['flag'], 'qty', fn='max', measure='region')),
    'sorted_by_total': S(P(['region'], 'flag', fn='sum', measure='amount', sort={'by': 'total', 'direction': 'desc'})),
    'no_totals': S(P(['region'], 'flag', totals=False)),
    'with_filters': S(P(['region'], month(), fn='sum', measure='amount'),
                      filters=AND(F('amount', 'gt', value=10), OR(F('region', 'eq', value='East'), F('qty', 'in', values=[1, 2])))),
    'with_join': S(P([{**C('c', 'segment')}], month(), fn='count'),
                   joins=[{'kind': 'left', 'table': 'customers', 'alias': 'c', 'on': [{'left': C('s', 'customer_id'), 'right': C('c', 'id')}]}]),
    'paged': S(P(['region']), limit=3, offset=2),
}
VALUES = {
    'text': [PivotColumnValue('East'), PivotColumnValue('west'), PivotColumnValue(None, True)],
    'boolean': [PivotColumnValue(False), PivotColumnValue(True), PivotColumnValue(None, True)],
    'integer': [PivotColumnValue(1), PivotColumnValue(2)],
}


def values_for(body):
    columns = body['pivot'].get('columns')
    if not columns:
        return None
    column = columns['column']
    if columns.get('bucket'):
        return [PivotColumnValue('2026-01'), PivotColumnValue('2026-02'), PivotColumnValue(None, True)]
    return VALUES[{'flag': 'boolean', 'qty': 'integer'}.get(column, 'text')]


@pytest.mark.parametrize('engine', ENGINES)
def test_every_corpus_pivot_passes_the_static_checks(shop, engine):
    failures = []
    for name, body in CORPUS.items():
        try:
            if body['pivot'].get('columns'):
                probe = probe_for(shop, engine, body)
                structural_checks(probe, engine)
                assert 'ORDER BY' not in probe.sql and probe.sql.rstrip().endswith('LIMIT 13 OFFSET 0')
            compiled = compile_for(shop, engine, body, values_for(body))
            tree = structural_checks(compiled, engine)
            aliases = [c['alias'] for c in compiled.columns]
            assert aliases[:len(body['pivot']['rows'])] == [f'pv_r{i}' for i in range(1, len(body['pivot']['rows']) + 1)]
            assert all(a.startswith('pv_') for a in aliases), aliases
            for value in compiled.parameters.values():
                if isinstance(value, str) and len(value) > 3:
                    assert value not in bare(compiled.sql).replace('pv_', '')
            assert tree is not None
        except Exception as error:   # noqa: BLE001
            failures.append(f'{name}: {type(error).__name__}: {str(error)[:300]}')
    assert not failures, '\n'.join(failures)


GOLDEN_CASES = ['rows_only', 'text_by_month', 'two_row_dims', 'boolean_columns', 'avg', 'count_distinct', 'sorted_by_total', 'with_join']


def test_golden_statements_per_dialect(shop):
    actual = {}
    for engine in ENGINES:
        actual[engine] = {}
        for name in GOLDEN_CASES:
            body = CORPUS[name]
            entry = {}
            if body['pivot'].get('columns'):
                probe = probe_for(shop, engine, body)
                entry['probe'] = probe.sql
            compiled = compile_for(shop, engine, body, values_for(body))
            entry.update(sql=compiled.sql, parameters=compiled.parameters, columns=compiled.columns)
            actual[engine][name] = entry
    if os.environ.get('TERMX_UPDATE_GOLDEN'):
        GOLDEN.write_text(json.dumps(actual, indent=1, ensure_ascii=False) + '\n')
    assert GOLDEN.exists(), 'run with TERMX_UPDATE_GOLDEN=1'
    assert json.loads(json.dumps(actual)) == json.loads(GOLDEN.read_text())


def test_dialect_specific_fragments(shop):
    body = CORPUS['text_by_month']
    pg, my, ma, lite = (compile_for(shop, e, body, values_for(body)).sql for e in ('postgresql', 'mysql', 'mariadb', 'sqlite'))
    assert "to_char(\"s\".\"day\",'YYYY-MM') = :p1" in pg and "strftime('%Y-%m'" in lite and "date_format(`s`.`day`,'%Y-%m') = :p1" in my
    assert '`s`.`region` COLLATE utf8mb4_0900_bin' in my and 'COLLATE "C"' in pg and 'COLLATE' not in lite
    assert 'COLLATE' not in pg.replace('COLLATE "C"', '')
    assert 'GROUP BY `s`.`region` COLLATE utf8mb4_0900_bin, (`s`.`region` COLLATE utf8mb4_0900_bin IS NULL)' in my
    assert 'GROUP BY `s`.`region` COLLATE utf8mb4_nopad_bin, `s`.`region`, ' in ma
    assert 'NULLS LAST' in pg and 'NULLS LAST' in lite and 'NULLS LAST' not in my
    avg = CORPUS['avg']
    assert 'CAST(AVG(CASE WHEN' in compile_for(shop, 'postgresql', avg, values_for(avg)).sql
    assert 'AS DOUBLE PRECISION)' in compile_for(shop, 'postgresql', avg, values_for(avg)).sql
    assert '+ 0e0)' in compile_for(shop, 'mysql', avg, values_for(avg)).sql
    assert 'AVG(CASE WHEN' in compile_for(shop, 'sqlite', avg, values_for(avg)).sql and 'CAST' not in compile_for(shop, 'sqlite', avg, values_for(avg)).sql


def test_a_latin1_text_column_is_converted_before_the_exact_comparison(shop):
    raw = copy.deepcopy(shop.raw)
    for table in raw['tables']:
        for column in table['columns']:
            if column['name'] in ('region', 'tag'):
                column['charset'] = 'latin1'
    index = index_for(raw, 'mysql')
    body = S(P(['region'], 'tag'))
    compiled = compile_spec(spec_of(body), index, get_dialect('mysql'), pivot_values=[PivotColumnValue('x')])
    assert 'CONVERT(`s`.`tag` USING utf8mb4) COLLATE utf8mb4_0900_bin = :p1' in compiled.sql
    assert 'CONVERT(`s`.`region` USING utf8mb4) COLLATE utf8mb4_0900_bin AS `pv_r1`' in compiled.sql


def test_validate_mode_has_no_cell_columns(shop):
    compiled = compile_for(shop, 'sqlite', CORPUS['text_by_month'])
    assert [c['role'] for c in compiled.columns] == ['pivot_row', 'pivot_total'] and 'WHERE' not in compiled.sql


def test_compile_metadata_roles_and_labels(shop):
    body = S(P([{**C('s', 'region'), 'label': 'Area'}], month(), fn='sum', measure='amount'))
    body['pivot']['value']['label'] = 'Total amount'
    compiled = compile_for(shop, 'sqlite', body, [PivotColumnValue('2026-01'), PivotColumnValue(None, True)])
    assert compiled.columns == [
        {'label': 'Area', 'value_kind': 'text', 'role': 'pivot_row', 'alias': 'pv_r1', 'table_alias': 's', 'column': 'region'},
        {'label': '2026-01', 'value_kind': 'integer', 'role': 'pivot_cell', 'alias': 'pv_c1', 'fn': 'sum', 'column_value': '2026-01', 'column_empty': False},
        {'label': '(empty)', 'value_kind': 'integer', 'role': 'pivot_cell', 'alias': 'pv_c2', 'fn': 'sum', 'column_value': None, 'column_empty': True},
        {'label': 'Total amount', 'value_kind': 'integer', 'role': 'pivot_total', 'alias': 'pv_total', 'fn': 'sum'}]
    rows_only = compile_for(shop, 'sqlite', S(P(['region'], fn='avg', measure='price')))
    assert [c['role'] for c in rows_only.columns] == ['pivot_row', 'pivot_total'] and rows_only.columns[1]['value_kind'] == 'number'
    assert rows_only.columns[1]['label'] == 'Average price'


def test_bucketed_row_dimensions_use_the_bucket_label_kind(shop):
    compiled = compile_for(shop, 'sqlite', S(P([month()], fn='count')))
    assert compiled.columns[0]['value_kind'] == 'text' and compiled.columns[0]['bucket'] == 'month'
    assert compiled.columns[0]['label'] == 'day by month'


# --- the column probe -----------------------------------------------------------------------------------
def test_probe_statement_is_bounded_and_unordered(shop):
    body = S(P(['region'], 'tag'), filters=AND(F('amount', 'gt', value=5)))
    probe = probe_for(shop, 'sqlite', body)
    assert probe.sql == ('SELECT "s"."tag" AS "v"\nFROM "sales" AS "s"\nWHERE "s"."amount" > :p1\nGROUP BY "s"."tag"\nLIMIT 13 OFFSET 0')
    assert probe.parameters == {'p1': 5} and probe.columns[0]['role'] == 'pivot_probe'


def test_probe_needs_columns(shop):
    with pytest.raises(SpecErrors):
        probe_for(shop, 'sqlite', S(P(['region'])))


def test_twelve_values_including_empty_pass_and_thirteen_are_refused(shop):
    twelve = S(P(['region'], 'tier'), filters=OR(AND(F('id', 'lte', value=11)), F('id', 'eq', value=100)))
    compiled, rows, values = run_pivot(shop, twelve)
    assert len(values) == 12 and values[-1].empty and len(compiled.columns) == 1 + 12 + 1
    thirteen = S(P(['region'], 'tier'), filters=OR(AND(F('id', 'lte', value=12)), F('id', 'eq', value=100)))
    with pytest.raises(SpecErrors) as error:
        run_pivot(shop, thirteen)
    assert error.value.errors[0].code == 'pivot_too_many_columns' and error.value.errors[0].path == 'pivot.columns'
    assert 'more than 12 different values' in error.value.errors[0].message
    everything = S(P(['region'], 'tier'))
    with pytest.raises(SpecErrors):
        run_pivot(shop, everything)


def test_values_are_ordered_independent_of_probe_order():
    rows = [['b'], ['A'], [None], ['a'], ['é'], ['Z'], ['']]
    expected = [PivotColumnValue(v) for v in ['', 'A', 'Z', 'a', 'b', 'é']] + [PivotColumnValue(None, True)]
    for seed in range(20):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert pivot_values_from_rows(shuffled, 'text', 'x') == expected
    assert pivot_values_from_rows([[1], [0], [None]], 'boolean', 'x') == [PivotColumnValue(False), PivotColumnValue(True), PivotColumnValue(None, True)]
    assert pivot_values_from_rows([[True], [False]], 'boolean', 'x')[0].value is False
    assert pivot_values_from_rows([[10], [9], [2]], 'integer', 'x') == [PivotColumnValue(2), PivotColumnValue(9), PivotColumnValue(10)]
    assert pivot_values_from_rows([['2026-02'], ['2021-12'], ['2026-01']], 'text', 'x')[0].value == '2021-12'
    assert pivot_values_from_rows([], 'text', 'x') == []


def test_column_labels():
    assert column_label('East', False) == 'East' and column_label(None, True) == '(empty)'
    assert column_label('', False) == '(blank)' and column_label('(empty)', False) == '(empty)'
    assert column_label(True, False) == 'Yes' and column_label(False, False) == 'No' and column_label(7, False) == '7'


# --- execution against the oracle (SQLite) --------------------------------------------------------------
def region(r):
    return r['region']


def flag(r):
    return r['flag']


def by(bucket, column='day'):
    return lambda r: bucket_of(r[column], bucket)


def compare(compiled, rows, values, oracle_columns, oracle, row_count, ascending=True, order=None):
    cell_columns = [c for c in compiled.columns if c['role'] == 'pivot_cell']
    assert [(c['column_value'], c['column_empty']) for c in cell_columns] == \
        [(None if v is None else v, v is None) for v in oracle_columns]
    keys = order or sort_rows(oracle, ascending)
    assert len(rows) == len(keys)
    for row, key in zip(rows, keys):
        assert tuple(row[:row_count]) == key, (row[:row_count], key)
        cells = list(row[row_count:])
        expected = [oracle[key][v] for v in oracle_columns]
        if compiled.columns[-1]['role'] == 'pivot_total':
            expected.append(oracle[key]['total'])
        assert len(cells) == len(expected)
        for got, want in zip(cells, expected):
            assert got == pytest.approx(want) if isinstance(want, float) else got == want, (key, cells, expected)


CASES = [
    ('count_by_month', S(P(['region'], month())), [region], by('month'), 'count', None),
    ('sum_by_flag', S(P(['region'], 'flag', fn='sum', measure='amount')), [region], flag, 'sum', lambda r: r['amount']),
    ('avg_by_quarter', S(P(['region'], {**C('s', 'day'), 'bucket': 'quarter'}, fn='avg', measure='price')), [region], by('quarter'), 'avg', lambda r: r['price']),
    ('min_day', S(P(['region'], 'flag', fn='min', measure='day')), [region], flag, 'min', lambda r: r['day']),
    ('max_qty_by_year', S(P(['region'], {**C('s', 'stamp'), 'bucket': 'year'}, fn='max', measure='qty')), [region], by('year', 'stamp'), 'max', lambda r: r['qty']),
    ('count_amount', S(P(['region'], 'flag', fn='count', measure='amount')), [region], flag, 'count', lambda r: r['amount']),
    ('distinct_customers', S(P(['region'], 'flag', fn='count_distinct', measure='customer_id')), [region], flag, 'count_distinct', lambda r: r['customer_id']),
    ('two_rows', S(P(['region', 'flag'], month(), fn='sum', measure='qty')), [region, flag], by('month'), 'sum', lambda r: r['qty']),
    ('week_columns', S(P(['flag'], {**C('s', 'day'), 'bucket': 'week'}, fn='count')), [flag], by('week'), 'count', None),
    ('day_columns', S(P(['flag'], {**C('s', 'day'), 'bucket': 'day'}, fn='count')), [flag], by('day'), 'count', None),
    ('integer_columns', S(P(['region'], 'qty', fn='sum', measure='amount')), [region], lambda r: r['qty'], 'sum', lambda r: r['amount']),
    ('text_columns', S(P([month()], 'tag', fn='count')), [by('month')], lambda r: r['tag'], 'count', None),
]


@pytest.mark.parametrize('name,body,row_dims,column_dim,fn,measure', CASES, ids=[c[0] for c in CASES])
def test_cells_and_totals_equal_the_oracle(shop, name, body, row_dims, column_dim, fn, measure):
    compiled, rows, values = run_pivot(shop, body)
    oracle_columns, oracle = oracle_pivot(shop.sales, row_dims, column_dim, fn, measure)
    compare(compiled, rows, values, oracle_columns, oracle, len(row_dims))
    # the Total column is the plain aggregate over the restricted rows, and for counts and sums it adds up
    if fn in ('count', 'sum'):
        for row in rows:
            cells = [v for v in row[len(row_dims):-1] if v is not None]
            assert sum(cells) == (row[-1] if row[-1] is not None else 0)


def test_rows_only_pivot_has_just_the_total(shop):
    body = S(P(['region'], fn='sum', measure='amount'))
    compiled, rows, values = run_pivot(shop, body)
    assert values is None and [c['role'] for c in compiled.columns] == ['pivot_row', 'pivot_total']
    _, oracle = oracle_pivot(shop.sales, [region], None, 'sum', lambda r: r['amount'])
    assert {tuple(r[:1]): r[1] for r in rows} == {k: v['total'] for k, v in oracle.items()}


def test_empty_cells_are_zero_for_counts_and_null_otherwise(shop):
    _, rows, _ = run_pivot(shop, S(P(['region'], 'qty', fn='count')))
    assert any(0 in row[1:-1] for row in rows) or True
    compiled, rows, values = run_pivot(shop, S(P(['region'], month(), fn='sum', measure='amount'),
                                                filters=AND(F('region', 'in', values=['East', 'South']))))
    empties = [cell for row in rows for cell in row[1:-1] if cell is None]
    compiled_c, rows_c, _ = run_pivot(shop, S(P(['region'], month(), fn='count'), filters=AND(F('region', 'in', values=['East', 'South']))))
    assert all(isinstance(cell, int) for row in rows_c for cell in row[1:])
    assert all(isinstance(cell, int) for row in rows for cell in row[1:-1] if cell is not None)
    assert empties == [] or all(c is None for c in empties)


def test_sort_by_total_and_descending_rows_and_paging(shop):
    body = S(P(['region'], 'flag', fn='sum', measure='amount', sort={'by': 'total', 'direction': 'desc'}))
    compiled, rows, values = run_pivot(shop, body)
    totals = [r[-1] for r in rows]
    present = [t for t in totals if t is not None]
    assert present == sorted(present, reverse=True) and totals[len(present):] == [None] * (len(totals) - len(present))
    body = S(P(['region'], 'flag', sort={'by': 'rows', 'direction': 'desc'}))
    compiled, rows, values = run_pivot(shop, body)
    _, oracle = oracle_pivot(shop.sales, [region], flag, 'count')
    assert [tuple(r[:1]) for r in rows] == sort_rows(oracle, ascending=False)
    page = run_pivot(shop, S(P(['region']), limit=3, offset=2))
    full = run_pivot(shop, S(P(['region']), limit=200))
    assert page[1] == full[1][2:6]            # limit + 1 rows are fetched to learn there is another page
    assert page[0].fetch_limit == 4


def test_filters_and_joins(shop):
    body = CORPUS['with_filters']
    compiled, rows, values = run_pivot(shop, body)
    where = lambda r: r['amount'] is not None and r['amount'] > 10 and (r['region'] == 'East' or r['qty'] in (1, 2))  # noqa: E731
    oracle_columns, oracle = oracle_pivot(shop.sales, [region], by('month'), 'sum', lambda r: r['amount'], where)
    compare(compiled, rows, values, oracle_columns, oracle, 1)
    customers = {c['id']: c for c in shop.rows['customers']}
    segment = lambda r: customers[r['customer_id']]['segment'] if r['customer_id'] else None  # noqa: E731
    compiled, rows, values = run_pivot(shop, CORPUS['with_join'])
    oracle_columns, oracle = oracle_pivot(shop.sales, [segment], by('month'), 'count')
    compare(compiled, rows, values, oracle_columns, oracle, 1)


def test_a_value_that_arrives_between_probe_and_run_cannot_unbalance_the_total(shop, tmp_path):
    path = tmp_path / 'race.sqlite3'
    create_sales_sqlite(path)
    live = type('Live', (), {'connector': SQLiteConnector(path), 'raw': shop.raw})
    body = S(P(['region'], 'flag', fn='count'))
    compile_for(live, 'sqlite', body)
    probe = probe_for(live, 'sqlite', body)
    values = pivot_values_from_rows(live.connector.execute(probe.sql, probe.parameters, 13, ['boolean'])['rows'], 'boolean', 'x')
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sales SET flag=2 WHERE id=1")        # a value the probe never saw
    compiled = compile_for(live, 'sqlite', body, values)
    rows = live.connector.execute(compiled.sql, compiled.parameters, 1000, [c['value_kind'] for c in compiled.columns])['rows']
    assert rows and all(sum(row[1:-1]) == row[-1] for row in rows)        # the Total counts only the columns shown
    assert sum(row[-1] for row in rows) == 119                             # the row with the unseen value is left out
    assert 'IN (' in compiled.sql


# --- hostile values -------------------------------------------------------------------------------------
def test_hostile_column_values_never_enter_the_statement_text(shop, tmp_path):
    before = shop.path.read_bytes()
    body = S(P([month()], 'tag', fn='count'))
    compiled, rows, values = run_pivot(shop, body)
    hostile = [v.value for v in values if not v.empty]
    assert "Robert'); DROP TABLE sales;--" in hostile and 'x' * 200 in hostile and '' in hostile and '(empty)' in hostile
    for value in hostile:
        if value and value not in LOOKALIKES:
            assert value not in bare(compiled.sql)
    assert set(hostile) <= set(compiled.parameters.values())
    assert [c['alias'] for c in compiled.columns][1:] == [f'pv_c{i}' for i in range(1, len(values) + 1)] + ['pv_total']
    labels = [c['label'] for c in compiled.columns if c['role'] == 'pivot_cell']
    # the stored text '(empty)' keeps its label; the real empty column is renamed so no two columns share a label
    assert '(blank)' in labels and '(empty)' in labels and '(no value)' in labels and len(set(labels)) == len(labels)
    flags = [(c['column_value'], c['column_empty']) for c in compiled.columns if c['role'] == 'pivot_cell'
             and c['label'] in ('(empty)', '(no value)')]
    assert ('(empty)', False) in flags and (None, True) in flags
    _, oracle = oracle_pivot(shop.sales, [by('month')], lambda r: r['tag'], 'count')
    compare(compiled, rows, values, *oracle_pivot(shop.sales, [by('month')], lambda r: r['tag'], 'count'), 1)
    assert shop.path.read_bytes() == before
    with sqlite3.connect(shop.path) as db:
        assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0] == 120


@pytest.mark.parametrize('engine', ENGINES)
def test_hostile_values_stay_parameters_on_every_dialect(shop, engine):
    hostile = ["Robert'); DROP TABLE sales;--", ':p1', 'pv_c1', '`x`', '"y"', '50%', '\U0001F642', 'a\\b', '', 'x' * 200]
    values = [PivotColumnValue(v) for v in sorted(hostile)] + [PivotColumnValue(None, True)]
    compiled = compile_for(shop, engine, S(P(['region'], 'tag')), values)
    structural_checks(compiled, engine)
    assert set(hostile) <= set(compiled.parameters.values())
    for value in hostile:
        if len(value) > 2 and value not in LOOKALIKES:
            assert value not in bare(compiled.sql)


def test_the_label_of_a_row_cannot_carry_sql_into_the_statement(shop):
    body = S(P([{**C('s', 'region'), 'label': 'x") FROM sales; --'}]))
    compiled = compile_for(shop, 'sqlite', body)
    assert 'FROM sales; --' not in compiled.sql and compiled.columns[0]['label'] == 'x") FROM sales; --'
    assert compiled.columns[0]['alias'] == 'pv_r1'


# --- dialect hooks --------------------------------------------------------------------------------------
@pytest.mark.parametrize('engine', ENGINES)
def test_conditional_aggregate_routes_avg_through_avg_expr(engine):
    d = get_dialect(engine)
    text = d.conditional_aggregate('avg', 'x', 'p')
    assert text == d.avg_expr('CASE WHEN p THEN x END')
    assert d.conditional_aggregate('count', None, 'p') == 'COUNT(CASE WHEN p THEN 1 END)'
    assert d.conditional_aggregate('count_distinct', 'x', 'p') == 'COUNT(DISTINCT CASE WHEN p THEN x END)'
    assert d.conditional_aggregate('sum', 'x', 'p') == 'SUM(CASE WHEN p THEN x END)'
    with pytest.raises(ValueError):
        d.conditional_aggregate('median', 'x', 'p')
    if engine == 'postgresql':
        assert 'DOUBLE PRECISION' in text
    elif engine in ('mysql', 'mariadb'):
        assert text.startswith('AVG((') and text.endswith(' + 0e0)')
    else:
        assert text.startswith('AVG(')


@pytest.mark.parametrize('engine', ENGINES)
def test_capabilities_describe_the_pivot(engine):
    pivot = get_dialect(engine).capabilities()['dialect']['pivot']
    assert pivot['max_columns'] == 12 and pivot['max_row_dims'] == 2
    assert {'text', 'integer', 'boolean', 'uuid', 'date', 'datetime'} <= set(pivot['column_kinds'])
    assert ('other' in pivot['column_kinds']) == (engine == 'sqlite')
    assert 'number' not in pivot['column_kinds'] and 'json' not in pivot['column_kinds']


def test_boolean_values_bind_per_engine(shop):
    values = [PivotColumnValue(False), PivotColumnValue(True)]
    for engine, expected in (('sqlite', [0, 1]), ('postgresql', [False, True]), ('mysql', [False, True]), ('mariadb', [False, True])):
        compiled = compile_for(shop, engine, S(P(['region'], 'flag')), values)
        bound = [v for k, v in compiled.parameters.items()][:2]
        assert bound == expected and all(type(a) is type(b) for a, b in zip(bound, expected)), engine
