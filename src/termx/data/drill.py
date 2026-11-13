"""Drill-down: the pivot cell or group a person clicked, as the QuerySpec that returns its underlying rows.

Pure. The input is the view's stored spec plus coordinates taken from the result (the values of the row
dimensions and the column the person picked); the output is a new spec made of the view's own filters plus one
condition per coordinate. A bucketed coordinate (a month, a week) becomes a half-open date range on the underlying
column, and an empty one becomes an `is_null` test. Coordinate values only ever become filter values, which the
compiler binds.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
from typing import Callable

from pydantic import ValidationError

from termx.data.query_spec import QuerySpec

INEXACT_NOTE = ('Some text in this date column could not be read as a date, and those rows are shown as empty in the result. '
                'The rows listed here leave them out.')


class DrillError(ValueError):
    """The coordinates do not fit the view."""


def bucket_range(label: object, bucket: str) -> tuple[str, str]:
    """`label` as written by the engines ('2026-02', '2026-W05', ...) to [start, next) ISO dates."""
    if not isinstance(label, str):
        raise DrillError('A date step needs its text label, for example 2026-02.')
    invalid = DrillError(f"'{label[:40]}' is not a valid {bucket}.")
    try:
        if bucket == 'day' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', label):
            start = dt.date.fromisoformat(label)
            end = start + dt.timedelta(days=1)
        elif bucket == 'week' and (found := re.fullmatch(r'(\d{4})-W(\d{2})', label)):
            start = dt.date.fromisocalendar(int(found[1]), int(found[2]), 1)
            end = start + dt.timedelta(days=7)
        elif bucket == 'month' and (found := re.fullmatch(r'(\d{4})-(\d{2})', label)):
            start = dt.date(int(found[1]), int(found[2]), 1)
            end = dt.date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        elif bucket == 'quarter' and (found := re.fullmatch(r'(\d{4})-Q([1-4])', label)):
            start = dt.date(int(found[1]), (int(found[2]) - 1) * 3 + 1, 1)
            end = dt.date(start.year + (start.month == 10), (start.month + 2) % 12 + 1, 1)
        elif bucket == 'year' and re.fullmatch(r'\d{4}', label):
            start = dt.date(int(label), 1, 1)
            end = dt.date(int(label) + 1, 1, 1)
        else:
            raise invalid
    except (ValueError, OverflowError) as error:
        if error is invalid:
            raise
        raise invalid from None
    return start.isoformat(), end.isoformat()


def _leaf(alias: str, column: str, op: str, **kw) -> dict:
    return {'field': {'table_alias': alias, 'column': column}, 'op': op, **kw}


def conditions(dim: dict, value: object, *, empty: bool, kind: str | None) -> tuple[list[dict], bool]:
    """Filter leaves for one coordinate, and whether they match the result's rows exactly."""
    alias, column, bucket = dim['table_alias'], dim['column'], dim.get('bucket')
    if empty or value is None:
        if not (empty and value is None):
            raise DrillError('An empty coordinate has no value, and a value is not empty.')
        return [_leaf(alias, column, 'is_null')], not (bucket and kind in ('text', 'other'))
    if bucket:
        start, end = bucket_range(value, bucket)
        if bucket == 'day' and kind in ('date', 'datetime'):
            return [_leaf(alias, column, 'eq', value=start)], True
        return [_leaf(alias, column, 'gte', value=start), _leaf(alias, column, 'lt', value=end)], True
    if isinstance(value, (dict, list)):
        raise DrillError('That value cannot be used to find rows.')
    return [_leaf(alias, column, 'eq', value=value)], True


def _arity(name: str, given: list | None, expected: int) -> list:
    if given is None or len(given) != expected:
        raise DrillError(f'Expected {expected} {name} value{"s" if expected != 1 else ""}.')
    return given


def drill_spec(view_kind: str, spec: dict, coordinates: dict, kind_of: Callable[[str, str], str | None]) -> tuple[dict, bool, list[str]]:
    """Returns (spec, exact, notes). Raises DrillError for coordinates that do not fit the view."""
    out = copy.deepcopy(spec)
    pivot = out.get('pivot')
    allowed = {'rows', 'column', 'groups'}
    if set(coordinates) - allowed:
        raise DrillError('Unknown coordinates.')
    leaves: list[dict] = []
    exact = True
    if pivot:
        if coordinates.get('groups'):
            raise DrillError('A pivot is found by its rows and column, not by groups.')
        rows = _arity('row', coordinates.get('rows'), len(pivot['rows']))
        for dim, value in zip(pivot['rows'], rows):
            found, ok = conditions(dim, value, empty=value is None, kind=kind_of(dim['table_alias'], dim['column']))
            leaves += found
            exact = exact and ok
        column = coordinates.get('column')
        if column is not None:
            if not pivot.get('columns'):
                raise DrillError('This pivot has no columns.')
            dim = pivot['columns']
            found, ok = conditions(dim, column.get('value'), empty=bool(column.get('empty')), kind=kind_of(dim['table_alias'], dim['column']))
            leaves += found
            exact = exact and ok
        out.pop('pivot')
        out.update(select=[], group_by=[], aggregates=[], sort=[])
    elif out.get('group_by') or out.get('aggregates'):
        if coordinates.get('rows') or coordinates.get('column'):
            raise DrillError('A grouped table is found by its groups.')
        groups = _arity('group', coordinates.get('groups'), len(out['group_by']))
        for dim, value in zip(out['group_by'], groups):
            found, ok = conditions(dim, value, empty=value is None, kind=kind_of(dim['table_alias'], dim['column']))
            leaves += found
            exact = exact and ok
        out.update(select=[], group_by=[], aggregates=[], sort=[])
    elif any(coordinates.get(k) for k in allowed):
        raise DrillError('This view lists rows already, so it has no cells to open.')
    out['offset'] = 0
    out['limit'] = 200 if leaves or pivot else out.get('limit', 200)
    root = out.get('filters') or {'op': 'and', 'items': []}
    base = list(root['items']) if root.get('op') == 'and' else [root]
    out['filters'] = {'op': 'and', 'items': base + leaves}
    try:
        QuerySpec.model_validate(out)
    except ValidationError:
        raise DrillError('These coordinates do not fit this view.') from None
    return out, exact, ([] if exact else [INEXACT_NOTE])
