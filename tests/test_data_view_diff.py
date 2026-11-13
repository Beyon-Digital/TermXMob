"""The pure edit-history diff."""
from __future__ import annotations

import copy

import pytest

from termx.data.view_diff import diff


def C(alias, column):
    return {'table_alias': alias, 'column': column}


def leaf(column, op, value, alias='s'):
    return {'field': C(alias, column), 'op': op, 'value': value}


def snapshot(**spec):
    base = {'version': 1, 'source': 'c', 'from': {'table': 'sales', 'alias': 's'}, 'joins': [], 'select': [], 'filters': {'op': 'and', 'items': []},
            'group_by': [], 'aggregates': [], 'sort': [], 'limit': 200, 'offset': 0}
    base.update(spec)
    return {'view_kind': 'table', 'name': 'Big sales', 'description': '', 'spec': base}


def test_no_change_is_empty():
    s = snapshot(filters={'op': 'and', 'items': [leaf('amount', 'gte', 5)]})
    assert diff(s, copy.deepcopy(s)) == []


def test_name_and_description():
    old, new = snapshot(), snapshot()
    new.update(name='Large orders', description='Weekly')
    assert diff(old, new) == [{'field': 'name', 'op': 'changed', 'before': 'Big sales', 'after': 'Large orders'},
                              {'field': 'description', 'op': 'changed', 'before': '', 'after': 'Weekly'}]


def test_filters_report_each_added_and_removed_leaf():
    old = snapshot(filters={'op': 'and', 'items': [leaf('amount', 'gte', 1000), leaf('qty', 'eq', 1)]})
    new = snapshot(filters={'op': 'and', 'items': [leaf('qty', 'eq', 1), leaf('amount', 'gte', 5000)]})
    assert diff(old, new) == [{'field': 'filters', 'op': 'added', 'after': leaf('amount', 'gte', 5000)},
                              {'field': 'filters', 'op': 'removed', 'before': leaf('amount', 'gte', 1000)}]


def test_a_moved_leaf_inside_nested_groups_is_not_a_filter_change_but_a_match_change():
    inner = {'op': 'or', 'items': [leaf('qty', 'eq', 1), leaf('qty', 'eq', 2)]}
    old = snapshot(filters={'op': 'and', 'items': [inner, leaf('amount', 'gt', 1)]})
    new = snapshot(filters={'op': 'and', 'items': [leaf('qty', 'eq', 1), leaf('qty', 'eq', 2), leaf('amount', 'gt', 1)]})
    result = diff(old, new)
    assert [c['field'] for c in result] == ['filters.match'] and result[0]['before'] == {'op': 'and', 'groups': ['or']}


def test_the_match_mode_flip():
    old = snapshot(filters={'op': 'and', 'items': [leaf('a', 'eq', 1), leaf('b', 'eq', 2)]})
    new = snapshot(filters={'op': 'or', 'items': [leaf('a', 'eq', 1), leaf('b', 'eq', 2)]})
    assert diff(old, new) == [{'field': 'filters.match', 'op': 'changed', 'before': 'and', 'after': 'or'}]


def test_segment_nodes_are_filters_too():
    old = snapshot()
    new = snapshot(filters={'op': 'and', 'items': [{'segment': 'abc', 'negate': True}]})
    assert diff(old, new) == [{'field': 'filters', 'op': 'added', 'after': {'segment': 'abc', 'negate': True}}]
    toggled = snapshot(filters={'op': 'and', 'items': [{'segment': 'abc', 'negate': False}]})
    assert [c['op'] for c in diff(new, toggled)] == ['added', 'removed']


def test_reordering_select_is_one_changed_entry():
    old = snapshot(select=[C('s', 'id'), C('s', 'qty')])
    new = snapshot(select=[C('s', 'qty'), C('s', 'id')])
    assert diff(old, new) == [{'field': 'select', 'op': 'changed', 'before': old['spec']['select'], 'after': new['spec']['select']}]


@pytest.mark.parametrize('field,item', [('joins', {'kind': 'left', 'table': 'customers', 'alias': 'c', 'on': []}),
                                        ('group_by', C('s', 'region')), ('aggregates', {'fn': 'count'}),
                                        ('sort', {**C('s', 'id'), 'direction': 'asc'}), ('select', C('s', 'id'))])
def test_list_fields_add_and_remove(field, item):
    empty, full = snapshot(), snapshot(**{field: [item]})
    assert diff(empty, full) == [{'field': field, 'op': 'added', 'after': item}]
    assert diff(full, empty) == [{'field': field, 'op': 'removed', 'before': item}]


def test_from_and_limit_change():
    old, new = snapshot(), snapshot(**{'from': {'table': 'customers', 'alias': 'c'}}, limit=500)
    assert diff(old, new) == [{'field': 'from', 'op': 'changed', 'before': {'table': 'sales', 'alias': 's'}, 'after': {'table': 'customers', 'alias': 'c'}},
                              {'field': 'limit', 'op': 'changed', 'before': 200, 'after': 500}]


def test_pivot_fields():
    pivot = {'rows': [C('s', 'region')], 'columns': {**C('s', 'day'), 'bucket': 'month'}, 'value': {'fn': 'count'}, 'totals': True,
             'sort': {'by': 'rows', 'direction': 'asc'}}
    old = snapshot(pivot=pivot)
    new_pivot = {**pivot, 'rows': [C('s', 'region'), C('s', 'flag')], 'columns': None, 'value': {'fn': 'sum', **C('s', 'amount')},
                 'totals': False, 'sort': {'by': 'total', 'direction': 'desc'}}
    result = {(c['field'], c['op']) for c in diff(old, snapshot(pivot=new_pivot))}
    assert result == {('pivot.rows', 'added'), ('pivot.columns', 'removed'), ('pivot.value', 'changed'), ('pivot.totals', 'changed'),
                      ('pivot.sort', 'changed')}
    assert diff(snapshot(pivot={**pivot, 'columns': None}), old) == [{'field': 'pivot.columns', 'op': 'added', 'after': pivot['columns']}]


def test_unicode_big_values_and_a_stable_order():
    big = 'é' * 400
    old = snapshot(filters={'op': 'and', 'items': [leaf('note', 'eq', big)]})
    new = snapshot(filters={'op': 'and', 'items': [leaf('note', 'eq', big + '!')]})
    first = diff(old, new)
    assert first == diff(old, new) and first[0]['op'] == 'added' and first[0]['after']['value'].endswith('!')
    everything = diff(snapshot(), {**snapshot(limit=1, **{'select': [C('s', 'id')]}), 'name': 'x'})
    assert [c['field'] for c in everything] == ['name', 'select', 'limit']


def test_missing_sections_are_treated_as_empty():
    assert diff({'name': 'a', 'spec': {}}, {'name': 'a', 'spec': {}}) == []
    assert diff({'name': 'a'}, {'name': 'b'}) == [{'field': 'name', 'op': 'changed', 'before': 'a', 'after': 'b'}]
