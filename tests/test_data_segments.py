"""Segments: saved filter groups inlined into another query by the compiler."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pivot_fixture import create_sales_sqlite
from test_data_conformance import ENGINES, index_for, structural_checks
from termx.data.connectors import SQLiteConnector
from termx.data.dialects import get_dialect
from termx.data.query_spec import QuerySpec, SpecErrors, compile_spec
from termx.data.runtime import DataRuntime
from termx.data.segments import ResolvedSegment, check_references, count_leaves, map_aliases, references_of, resolve_record


def C(alias, column):
    return {'table_alias': alias, 'column': column}


def F(column, op, alias='s', **kw):
    return {'field': C(alias, column), 'op': op, **kw}


def AND(*items):
    return {'op': 'and', 'items': list(items)}


def OR(*items):
    return {'op': 'or', 'items': list(items)}


def REF(sid, negate=False):
    return {'segment': sid, 'negate': negate}


CUSTOMER_JOIN = {'kind': 'left', 'table': 'customers', 'alias': 'c', 'on': [{'left': C('s', 'customer_id'), 'right': C('c', 'id')}]}


def S(filters=None, table='sales', alias='s', joins=None, source='conn', **kw):
    body = {'version': 1, 'source': source, 'from': {'table': table, 'alias': alias}, 'joins': joins or [], 'select': [C(alias, 'id')],
            'filters': filters or AND(), 'group_by': [], 'aggregates': [], 'sort': [{**C(alias, 'id'), 'direction': 'asc'}],
            'limit': 1000, 'offset': 0}
    body.update(kw)
    return body


def seg(sid, filters=None, name=None, **kw):
    spec = QuerySpec.model_validate(S(filters, **kw))
    return ResolvedSegment(sid, name or f'Segment {sid}', 1, spec.source, spec)


def segments(*items):
    return {s.id: s for s in items}


@pytest.fixture(scope='module')
def shop(tmp_path_factory):
    path = tmp_path_factory.mktemp('segments') / 'sales.sqlite3'
    rows = create_sales_sqlite(path)
    raw = SQLiteConnector(path).schema()
    DataRuntime._annotate_kinds(raw, 'sqlite')
    return type('Shop', (), {'path': path, 'rows': rows, 'raw': raw, 'connector': SQLiteConnector(path)})


def compiled(shop, body, known=(), engine='sqlite'):
    return compile_spec(QuerySpec.model_validate(body), index_for(shop.raw, engine), get_dialect(engine), segments=segments(*known))


def codes(shop, body, known=(), engine='sqlite'):
    with pytest.raises(SpecErrors) as error:
        compiled(shop, body, known, engine)
    return [(e.code, e.path) for e in error.value.errors]


def ids(shop, body, known=()):
    result = compiled(shop, body, known)
    rows = shop.connector.execute(result.sql, result.parameters, 1000, [c['value_kind'] for c in result.columns])['rows']
    return {r[0] for r in rows}


BIG = seg('big', AND(F('amount', 'gt', value=100)))
EAST = seg('east', AND(F('region', 'eq', value='East')))


# --- models ---------------------------------------------------------------------------------------------
def test_segment_nodes_parse_in_any_group_and_count_as_one_filter():
    spec = QuerySpec.model_validate(S(AND(REF('a'), OR(REF('b', True), F('qty', 'eq', value=1)))))
    assert references_of(spec.filters) == ['a', 'b'] and count_leaves(spec.filters) == 3
    assert spec.filters.items[0].negate is False and spec.filters.items[1].items[0].negate is True
    many = S(AND(*[REF(f's{i}') for i in range(50)]))
    QuerySpec.model_validate(many)
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(S(AND(*[REF(f's{i}') for i in range(51)])))


@pytest.mark.parametrize('node', [{'segment': ''}, {'segment': 'x' * 65}, {'segment': 5}, {'segment': 'a', 'negate': 'no'},
                                  {'segment': 'a', 'op': 'eq'}, {'segment': 'a', 'field': C('s', 'id')}, {'segment': 'a', 'extra': 1},
                                  {'segment': None}])
def test_invalid_segment_nodes_are_rejected(node):
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(S(AND(node)))


def test_references_are_unique_and_ordered():
    spec = QuerySpec.model_validate(S(AND(REF('b'), OR(REF('a'), REF('b')), REF('c', True))))
    assert references_of(spec.filters) == ['b', 'a', 'c']


def test_resolve_record_only_accepts_live_segments():
    body = {'id': 'x', 'name': 'N', 'revision': 3, 'view_kind': 'segment', 'spec': S(AND(F('qty', 'eq', value=1)))}
    assert resolve_record(body).revision == 3
    assert resolve_record({**body, 'archived': True}) is None
    assert resolve_record({**body, 'view_kind': 'pivot'}) is None
    assert resolve_record({**body, 'spec': {'nonsense': 1}}) is None
    assert resolve_record(None) is None


# --- statements -----------------------------------------------------------------------------------------
@pytest.mark.parametrize('engine', ENGINES)
def test_a_segment_is_inlined_and_negation_uses_the_case_form(shop, engine):
    plain = compiled(shop, S(AND(REF('big'))), [BIG], engine)
    negated = compiled(shop, S(AND(REF('big', True))), [BIG], engine)
    q = get_dialect(engine).quote
    assert f'WHERE {q("s")}.{q("amount")} > :p1' in plain.sql
    assert f'WHERE CASE WHEN ({q("s")}.{q("amount")} > :p1) THEN 1 ELSE 0 END = 0' in negated.sql
    assert 'SELECT' not in plain.sql.split('WHERE', 1)[1] and plain.parameters == {'p1': 100}
    for result in (plain, negated):
        structural_checks(result, engine)


@pytest.mark.parametrize('engine', ENGINES)
def test_nesting_and_mixed_filters(shop, engine):
    inner = seg('inner', AND(F('qty', 'in', values=[1, 2]), F('region', 'eq', value='West')))
    outer = seg('outer', AND(REF('inner'), F('amount', 'gt', value=5)))
    body = S(AND(F('price', 'lt', value=9.5), OR(REF('outer'), REF('big', True))))
    result = compiled(shop, body, [inner, outer, BIG], engine)
    structural_checks(result, engine)
    q = get_dialect(engine).quote
    assert list(result.parameters.values()) == [9.5, 1, 2, 'West', 5, 100]
    assert f'({q("s")}.{q("qty")} IN (:p2, :p3) AND' in result.sql or f'{q("s")}.{q("qty")} IN (:p2, :p3)' in result.sql
    assert ' OR CASE WHEN (' in result.sql


def test_a_multi_part_segment_is_parenthesised_inside_its_host(shop):
    either = seg('either', OR(F('qty', 'eq', value=1), F('qty', 'eq', value=2)))
    sql = compiled(shop, S(AND(F('region', 'eq', value='East'), REF('either'))), [either]).sql
    assert 'WHERE "s"."region" = :p1 AND ("s"."qty" = :p2 OR "s"."qty" = :p3)' in sql


def test_three_levels_are_allowed_and_a_fourth_is_not(shop):
    chain = [seg('l4', AND(F('qty', 'eq', value=1))), seg('l3', AND(REF('l4'))), seg('l2', AND(REF('l3'))), seg('l1', AND(REF('l2')))]
    assert codes(shop, S(AND(REF('l1'))), chain) == [('segment_depth', 'filters.items[0]')]
    assert ids(shop, S(AND(REF('l2'))), chain) == {r['id'] for r in shop.rows['sales'] if r['qty'] == 1}


# --- semantics (SQLite, against Python) -----------------------------------------------------------------
def test_members_and_complement_partition_the_table_even_when_the_test_is_null(shop):
    everyone = {r['id'] for r in shop.rows['sales']}
    inside = ids(shop, S(AND(REF('big'))), [BIG])
    outside = ids(shop, S(AND(REF('big', True))), [BIG])
    assert inside == {r['id'] for r in shop.rows['sales'] if r['amount'] is not None and r['amount'] > 100}
    assert inside | outside == everyone and not inside & outside
    assert any(r['amount'] is None and r['id'] in outside for r in shop.rows['sales'])


def test_a_segment_with_or_and_nested_groups_matches_the_oracle(shop):
    mixed = seg('mixed', OR(AND(F('region', 'eq', value='East'), F('qty', 'gte', value=3)), F('flag', 'eq', value=True)))
    expected = {r['id'] for r in shop.rows['sales'] if (r['region'] == 'East' and r['qty'] >= 3) or r['flag'] is True}
    assert ids(shop, S(AND(REF('mixed'))), [mixed]) == expected
    everyone = {r['id'] for r in shop.rows['sales']}
    assert ids(shop, S(AND(REF('mixed', True))), [mixed]) == everyone - expected


def test_combining_segments_with_ordinary_filters(shop):
    body = S(AND(REF('big'), REF('east', True)))
    expected = {r['id'] for r in shop.rows['sales'] if (r['amount'] or 0) > 100 and r['region'] != 'East'}
    assert ids(shop, body, [BIG, EAST]) == expected


def test_an_empty_segment_matches_every_row_and_its_complement_none(shop):
    empty = seg('empty', AND())
    result = compiled(shop, S(AND(REF('empty'))), [empty])
    assert [w['code'] for w in result.warnings] == ['segment_empty'] and 'WHERE 1 = 1' in result.sql
    assert len(ids(shop, S(AND(REF('empty'))), [empty])) == 120
    assert ids(shop, S(AND(REF('empty', True))), [empty]) == set()
    twice = compiled(shop, S(AND(REF('empty'), REF('empty', True))), [empty])
    assert [w['code'] for w in twice.warnings] == ['segment_empty']


def test_segments_work_inside_a_pivot_and_a_summary(shop):
    body = S(AND(REF('big')), select=[], sort=[], group_by=[{**C('s', 'region'), 'label': 'Region'}], aggregates=[{'fn': 'count'}])
    result = compiled(shop, body, [BIG])
    rows = shop.connector.execute(result.sql, result.parameters, 100)['rows']
    assert sum(r[1] for r in rows) == len(ids(shop, S(AND(REF('big'))), [BIG]))
    pivot = S(AND(REF('big', True)), select=[], sort=[], pivot={'rows': [C('s', 'region')], 'value': {'fn': 'count'}})
    result = compiled(shop, pivot, [BIG])
    rows = shop.connector.execute(result.sql, result.parameters, 100)['rows']
    assert sum(r[1] for r in rows) == len(ids(shop, S(AND(REF('big', True))), [BIG]))


# --- references that cannot be resolved -----------------------------------------------------------------
def test_missing_segments_all_report_the_same_message(shop):
    first = codes(shop, S(AND(REF('nope'))))
    assert first == [('segment_missing', 'filters.items[0]')]
    with pytest.raises(SpecErrors) as a:
        compiled(shop, S(AND(REF('nope'))))
    with pytest.raises(SpecErrors) as b:
        compiled(shop, S(AND(REF("x'; DROP TABLE sales;--"))))
    assert a.value.errors[0].message == b.value.errors[0].message
    assert 'was deleted' in a.value.errors[0].message


def test_cycles_are_caught_at_compile_time_even_in_a_corrupt_store(shop):
    a = seg('a', AND(REF('b')), name='Alpha')
    b = seg('b', AND(REF('a')), name='Beta')
    found = codes(shop, S(AND(REF('a'))), [a, b])
    assert found == [('segment_cycle', 'filters.items[0]')]
    with pytest.raises(SpecErrors) as error:
        compiled(shop, S(AND(REF('a'))), [a, b])
    assert "'Alpha'" in error.value.errors[0].message and "'Beta'" in error.value.errors[0].message
    selfie = seg('me', AND(REF('me')))
    assert codes(shop, S(AND(REF('me'))), [selfie]) == [('segment_cycle', 'filters.items[0]')]
    c = seg('c', AND(REF('d'))), seg('d', AND(REF('e'))), seg('e', AND(REF('c')))
    assert codes(shop, S(AND(REF('c'))), c) == [('segment_cycle', 'filters.items[0]')]


def test_the_same_segment_twice_in_a_row_is_not_a_cycle(shop):
    assert ids(shop, S(AND(REF('big'), REF('big', True))), [BIG]) == set()
    nested = seg('twice', AND(REF('big'), REF('big')))
    assert ids(shop, S(AND(REF('twice'))), [BIG, nested]) == ids(shop, S(AND(REF('big'))), [BIG])


def test_expansion_and_leaf_caps(shop):
    assert codes(shop, S(AND(*[REF('big') for _ in range(21)])), [BIG]) == [('segment_too_large', 'filters.items[20]')]
    ids(shop, S(AND(*[REF('big') for _ in range(20)])), [BIG])
    wide = seg('wide', AND(*[F('qty', 'ne', value=n) for n in range(50)]))
    five = codes(shop, S(AND(*[REF('wide') for _ in range(5)])), [wide])
    assert five and five[0][0] == 'segment_too_large'
    ids(shop, S(AND(*[REF('wide') for _ in range(4)])), [wide])


def test_compatibility_with_the_host_query(shop):
    other_table = seg('cust', AND(F('segment', 'eq', alias='c', value='retail')), table='customers', alias='c')
    assert codes(shop, S(AND(REF('cust'))), [other_table]) == [('segment_base_mismatch', 'filters.items[0]')]
    foreign = seg('foreign', AND(F('qty', 'eq', value=1)), source='another')
    assert codes(shop, S(AND(REF('foreign'))), [foreign]) == [('segment_source_mismatch', 'filters.items[0]')]
    needs_customers = seg('retail', AND(F('segment', 'eq', alias='c', value='retail')), joins=[CUSTOMER_JOIN])
    assert codes(shop, S(AND(REF('retail'))), [needs_customers]) == [('segment_join_missing', 'filters.items[0]')]
    with pytest.raises(SpecErrors) as error:
        compiled(shop, S(AND(REF('retail'))), [needs_customers])
    assert 'customers table combined first' in error.value.errors[0].message


def test_a_matching_join_is_found_under_any_alias_and_the_hosts_alias_is_used(shop):
    needs_customers = seg('retail', AND(F('segment', 'eq', alias='c', value='retail')), joins=[CUSTOMER_JOIN])
    host_join = {**CUSTOMER_JOIN, 'alias': 'cust', 'on': [{'left': C('s', 'customer_id'), 'right': C('cust', 'id')}]}
    result = compiled(shop, S(AND(REF('retail')), joins=[host_join]), [needs_customers])
    assert '"cust"."segment" = :p1' in result.sql and '"c"."segment"' not in result.sql
    customers = {c['id']: c for c in shop.rows['customers']}
    expected = {r['id'] for r in shop.rows['sales'] if r['customer_id'] and customers[r['customer_id']]['segment'] == 'retail'}
    assert ids(shop, S(AND(REF('retail')), joins=[host_join]), [needs_customers]) == expected
    inner_host = {**host_join, 'kind': 'inner'}
    assert codes(shop, S(AND(REF('retail')), joins=[inner_host]), [needs_customers]) == [('segment_join_missing', 'filters.items[0]')]


def test_an_alias_used_for_another_table_is_a_conflict(shop):
    needs_customers = seg('retail', AND(F('segment', 'eq', alias='c', value='retail')), joins=[CUSTOMER_JOIN])
    squatter = {'kind': 'left', 'table': 'sales', 'alias': 'c', 'on': [{'left': C('s', 'id'), 'right': C('c', 'id')}]}
    assert codes(shop, S(AND(REF('retail')), joins=[squatter]), [needs_customers]) == [('segment_alias_conflict', 'filters.items[0]')]


def test_nested_segments_remap_through_two_hosts(shop):
    inner = seg('retail', AND(F('segment', 'eq', alias='c', value='retail')), joins=[CUSTOMER_JOIN])
    outer = seg('retail_big', AND(REF('retail'), F('amount', 'gt', value=100)), joins=[{**CUSTOMER_JOIN, 'alias': 'x', 'on': [{'left': C('s', 'customer_id'), 'right': C('x', 'id')}]}])
    host = {**CUSTOMER_JOIN, 'alias': 'k', 'on': [{'left': C('s', 'customer_id'), 'right': C('k', 'id')}]}
    result = compiled(shop, S(AND(REF('retail_big')), joins=[host]), [inner, outer])
    assert '"k"."segment" = :p1' in result.sql and result.parameters == {'p1': 'retail', 'p2': 100}


def test_the_base_alias_may_differ(shop):
    renamed = seg('renamed', AND(F('amount', 'gt', alias='x', value=100)), alias='x')
    assert ids(shop, S(AND(REF('renamed'))), [renamed]) == ids(shop, S(AND(REF('big'))), [BIG])
    assert '"t"."amount" > :p1' in compiled(shop, S(AND(REF('renamed')), alias='t'), [renamed]).sql


def test_a_segment_that_names_a_missing_column_reports_it_at_the_reference(shop):
    broken = seg('broken', AND(F('does_not_exist', 'eq', value=1)))
    assert codes(shop, S(AND(REF('broken'))), [broken]) == [('unknown_column', 'filters.items[0]')]


# --- pure helpers ---------------------------------------------------------------------------------------
def test_map_aliases_reports_the_first_problem():
    host = QuerySpec.model_validate(S())
    mapping, problem = map_aliases(host.from_, host.joins, BIG)
    assert mapping == {'s': 's'} and problem is None
    mapping, problem = map_aliases(host.from_, host.joins, seg('x', AND(), table='customers', alias='c'))
    assert mapping is None and problem[0] == 'segment_base_mismatch'


def test_check_references_finds_loops_depth_and_mismatches():
    world = {s.id: s for s in [seg('a', AND(REF('b'))), seg('b', AND(REF('c'))), seg('c'), seg('d', AND(REF('a'))),
                               seg('t', table='customers', alias='c'), seg('o', source='other')]}
    look = world.get

    def run(root, direct):
        return [e.code for e in check_references(root, 'conn', 'sales', direct, look)]
    assert run('new', ['a']) == []
    assert run('c', ['a']) == ['segment_cycle']           # c would use a -> b -> c
    assert run('me', ['me']) == ['segment_cycle']
    assert run('new', ['d']) == ['segment_depth']         # d -> a -> b -> c is four levels
    assert run('new', ['zzz']) == ['segment_missing']
    assert run('new', ['t']) == ['segment_base_mismatch']
    assert run('new', ['o']) == ['segment_source_mismatch']
    assert run('new', ['a', 'zzz', 't']) == ['segment_missing', 'segment_base_mismatch']
