"""Pure diff between two view snapshots, for the edit history.

A snapshot is `{view_kind, name, description, spec}` with the spec in its stored (canonical) form. The result is
a list of `{field, op, before?, after?}`. The server never writes sentences; the client words each change.
Fields: name, description, from, joins, filters, filters.match, select, group_by, aggregates, sort, limit,
pivot.rows, pivot.columns, pivot.value, pivot.totals, pivot.sort.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

LISTS = ('joins', 'select', 'group_by', 'aggregates', 'sort')


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _leaves(group: dict) -> list[dict]:
    """Filter leaves and segment nodes, depth first, without the groups around them."""
    found: list[dict] = []
    for item in group.get('items', []):
        if 'items' in item and 'op' in item and 'field' not in item:
            found.extend(_leaves(item))
        else:
            found.append(item)
    return found


def _shape(group: dict):
    """The nesting of and/or groups, without the filters in them."""
    nested = [_shape(i) for i in group.get('items', []) if 'items' in i and 'field' not in i]
    return group.get('op') if not nested else {'op': group.get('op'), 'groups': nested}


def _list_changes(field: str, old: list, new: list) -> list[dict]:
    old_keys, new_keys = [_key(i) for i in old], [_key(i) for i in new]
    if old_keys == new_keys:
        return []
    if sorted(old_keys) == sorted(new_keys):      # only the order changed
        return [{'field': field, 'op': 'changed', 'before': old, 'after': new}]
    changes: list[dict] = []
    remaining = Counter(old_keys)
    for item, key in zip(new, new_keys):
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            changes.append({'field': field, 'op': 'added', 'after': item})
    pending = Counter(new_keys)
    for item, key in zip(old, old_keys):
        if pending[key] > 0:
            pending[key] -= 1
        else:
            changes.append({'field': field, 'op': 'removed', 'before': item})
    return changes


def _value_change(field: str, old: Any, new: Any) -> list[dict]:
    if _key(old) == _key(new):
        return []
    if old is None:
        return [{'field': field, 'op': 'added', 'after': new}]
    if new is None:
        return [{'field': field, 'op': 'removed', 'before': old}]
    return [{'field': field, 'op': 'changed', 'before': old, 'after': new}]


def diff(old: dict, new: dict) -> list[dict]:
    changes: list[dict] = []
    for field in ('name', 'description'):
        if old.get(field, '') != new.get(field, ''):
            changes.append({'field': field, 'op': 'changed', 'before': old.get(field, ''), 'after': new.get(field, '')})
    a, b = old.get('spec') or {}, new.get('spec') or {}
    changes += _value_change('from', a.get('from'), b.get('from'))
    for field in ('joins',):
        changes += _list_changes(field, a.get(field, []), b.get(field, []))
    old_filters, new_filters = a.get('filters') or {'op': 'and', 'items': []}, b.get('filters') or {'op': 'and', 'items': []}
    changes += _list_changes('filters', _leaves(old_filters), _leaves(new_filters))
    if old_filters.get('op') != new_filters.get('op'):
        changes.append({'field': 'filters.match', 'op': 'changed', 'before': old_filters.get('op'), 'after': new_filters.get('op')})
    elif _key(_shape(old_filters)) != _key(_shape(new_filters)):
        changes.append({'field': 'filters.match', 'op': 'changed', 'before': _shape(old_filters), 'after': _shape(new_filters)})
    for field in ('select', 'group_by', 'aggregates', 'sort'):
        changes += _list_changes(field, a.get(field, []), b.get(field, []))
    changes += _value_change('limit', a.get('limit'), b.get('limit'))
    pa, pb = a.get('pivot') or {}, b.get('pivot') or {}
    changes += _list_changes('pivot.rows', pa.get('rows', []), pb.get('rows', []))
    for field in ('columns', 'value', 'totals', 'sort'):
        changes += _value_change(f'pivot.{field}', pa.get(field), pb.get(field))
    return changes
