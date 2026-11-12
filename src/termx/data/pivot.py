"""Pivot compilation: rows by columns by one measure, as one grouped statement.

Cells use conditional aggregation (`FN(CASE WHEN <column is this value> THEN <measure> END)`), so one scan
of the data fills every cell and the plain aggregate fills the Total column. The column values come from a
bounded probe (`GROUP BY <column> LIMIT 13`): thirteen distinct values mean the pivot is refused and no subset is
ever chosen. Every value the data supplies, and every filter value, is a bound parameter. The SQL aliases are
neutral (`pv_r1`, `pv_c1`, `pv_total`); the display labels travel as metadata in the compile output.

Pure: nothing here reads a database.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from termx.data.query_spec import (PIVOT_MAX_COLUMNS, PIVOT_PROBE_ROWS, CompiledQuery, PivotDim, SpecError, SpecErrors,
                                   _Compiler, _Out)

EMPTY_LABEL = '(empty)'
BLANK_LABEL = '(blank)'
COLUMN_KINDS = ('text', 'integer', 'boolean', 'uuid')
_KIND_NAMES = {'number': 'decimal numbers', 'json': 'JSON', 'binary': 'binary data', 'array': 'lists of values',
               'time': 'times of day', 'other': 'values of mixed type'}


@dataclass(frozen=True)
class PivotColumnValue:
    value: object          # JSON scalar; None only when `empty`
    empty: bool = False


@dataclass
class _Dim:
    spec: PivotDim
    alias: str
    column: str
    kind: str              # the column's own kind
    out_kind: str          # the kind of the values the dimension produces
    expr: str
    bare: str
    derived: bool
    label: str
    path: str


def column_label(value: object, empty: bool) -> str:
    if empty:
        return EMPTY_LABEL
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    text = str(value)
    return text if text else BLANK_LABEL


def _dimension(c: _Compiler, dim: PivotDim, path: str, *, column: bool) -> _Dim | None:
    found = c.resolve_column(dim.table_alias, dim.column, path + '.column', path + '.table_alias')
    if found is None:
        return None
    alias, _, info = found
    bare = c.col_sql(alias, info.name)
    expr, out_kind = bare, info.kind
    if dim.bucket:
        if info.kind not in c.d.bucketable_kinds:
            c.err('bucket_requires_date', path + '.bucket',
                  f"'{info.name}' is not a date column, so it cannot be grouped by {dim.bucket}.")
            return None
        message = (f"'{info.name}' holds text, so dates are read from that text. Values that are not ISO dates appear empty.")
        if info.kind in ('text', 'other') and not any(w['message'] == message for w in c.warnings):
            c.warnings.append({'code': 'text_date', 'message': message})
        expr = c.d.date_bucket(bare, info.kind, dim.bucket)
        out_kind = 'text'
    elif column:
        if info.kind in ('date', 'datetime'):
            c.err('pivot_needs_bucket', path + '.bucket',
                  f"'{info.name}' is a date, so pivot columns need a step: day, week, month, quarter or year.")
            return None
        if info.kind not in COLUMN_KINDS:
            c.err('pivot_column_kind', path + '.column',
                  f"{_KIND_NAMES.get(info.kind, 'These values').capitalize()} can't be used as columns. Pick a text, whole number, "
                  'yes/no or date column.')
            return None
    elif info.kind == 'json':
        c.err('bad_type', path + '.column', f"'{info.name}' holds JSON, which cannot be grouped.")
        return None
    if info.kind == 'text' and not dim.bucket:
        expr = c.d.text_operand(bare, info.charset)
    default = f'{info.name} by {dim.bucket}' if dim.bucket else info.name
    return _Dim(dim, alias, info.name, info.kind, out_kind, expr, bare, expr != bare, dim.label or default, path)


def _dimensions(c: _Compiler) -> tuple[list[_Dim], _Dim | None]:
    pivot = c.spec.pivot
    rows = [d for i, dim in enumerate(pivot.rows) if (d := _dimension(c, dim, f'pivot.rows[{i}]', column=False)) is not None]
    column = _dimension(c, pivot.columns, 'pivot.columns', column=True) if pivot.columns is not None else None
    seen: dict[tuple, str] = {}
    for dim in [*rows, *([column] if column else [])]:
        key = (dim.alias, dim.column, dim.spec.bucket)
        if key in seen:
            c.err('pivot_duplicate_dimension', dim.path, f"'{dim.column}' is already used in this pivot.")
        seen[key] = dim.path
    explicit = [d for d in rows if d.spec.label]
    for dim in explicit:
        c.label_ok(dim.spec.label, dim.path + '.label')
    names = [d.label.lower() for d in rows]
    for dim in rows:
        if not dim.spec.label and names.count(dim.label.lower()) > 1:
            dim.label = f'{dim.alias}.{dim.column}' + (f' by {dim.spec.bucket}' if dim.spec.bucket else '')
    labels = [d.label.lower() for d in rows]
    if not any(e.code == 'pivot_duplicate_dimension' for e in c.errors):
        for dim, label in zip(rows, labels):
            if labels.count(label) > 1:
                c.err('duplicate_label', dim.path + '.label', f"Two rows are named '{dim.label}'. Give one a different name.")
                break
    return rows, column


def _conflicts(c: _Compiler) -> None:
    spec = c.spec
    for name in ('select', 'group_by', 'aggregates', 'sort'):
        if getattr(spec, name):
            c.err('pivot_conflict', name, f'A pivot sets its own rows, columns and value, so {name.replace("_", " ")} must be empty.')


def _fingerprint(c: _Compiler) -> str:
    return hashlib.sha256(json.dumps({t: dict(sorted(cols.items())) for t, cols in sorted(c.used.items())},
                                     sort_keys=True).encode()).hexdigest()[:16]


def build_pivot(c: _Compiler) -> CompiledQuery:
    spec, d, pivot = c.spec, c.d, c.spec.pivot
    _conflicts(c)
    from_lines, join_infos = c.tables_and_joins()
    rows, column = _dimensions(c)
    value = c.aggregate(0, pivot.value, 'pivot.value')
    if value is not None and pivot.value.label:
        c.label_ok(pivot.value.label, 'pivot.value.label')
    values = c.pivot_values if column is not None else None
    outs: list[_Out] = []
    for i, dim in enumerate(rows, 1):
        outs.append(_Out(dim.label, dim.expr, dim.out_kind, 'pivot_row', dim.alias, dim.column, bucket=dim.spec.bucket,
                         alias=f'pv_r{i}', derived=dim.derived))
    if value is not None and column is not None and values:
        for i, item in enumerate(values, 1):
            predicate = f'{column.expr} IS NULL' if item.empty else \
                f'{column.expr} = {c.bind(d.pivot_cell_value(column.kind, item.value))}'
            expr = d.conditional_aggregate(pivot.value.fn, value.value_column, predicate)
            outs.append(_Out(column_label(item.value, item.empty), expr, value.kind, 'pivot_cell', fn=pivot.value.fn,
                             alias=f'pv_c{i}', column_value=item.value, column_empty=item.empty))
    if value is not None and (pivot.totals or column is None):
        outs.append(_Out(value.label, value.expr, value.kind, 'pivot_total', fn=pivot.value.fn, alias='pv_total'))
    where = c.filters()
    if column is not None and values:
        plain = [item for item in values if not item.empty]
        parts = []
        if plain:
            parts.append(d.in_list(column.expr, [c.bind(d.pivot_cell_value(column.kind, item.value)) for item in plain], False))
        if any(item.empty for item in values):
            parts.append(f'{column.expr} IS NULL')
        restriction = parts[0] if len(parts) == 1 else '(' + ' OR '.join(parts) + ')'
        where = f'{where} AND {restriction}' if where else restriction
    if c.errors:
        raise SpecErrors(c.errors)
    for info in join_infos:
        if info['alias'] in c.many_to_many:
            c.warnings.append({'code': 'many_to_many', 'join_alias': info['alias'], 'message':
                f"Neither side of the match with {c.aliases[info['alias']].name} is a unique key, so rows can multiply a great deal. "
                'This query may be slow, and it is stopped if it takes too long.'})
        elif info['fan_out'] == 'many':
            c.warnings.append({'code': 'fan_out', 'message': info['message'], 'join_alias': info['alias']})
    lines = ['SELECT ' + ', '.join(f'{o.expr} AS {d.quote(o.alias)}' for o in outs), *from_lines]
    if where:
        lines.append('WHERE ' + where)
    grouping: list[str] = []
    for dim in rows:
        for item in d.group_by_items(dim.expr, dim.derived, None if dim.spec.bucket else dim.bare):
            if item not in grouping:
                grouping.append(item)
    lines.append('GROUP BY ' + ', '.join(grouping))
    direction = pivot.sort.direction
    keys = ([(value.expr, direction)] if pivot.sort.by == 'total' and value is not None else []) + \
        [(dim.expr, direction if pivot.sort.by == 'rows' else 'asc') for dim in rows]
    ordering, seen = [], set()
    for expr, way in keys:
        if expr not in seen:
            seen.add(expr)
            ordering.append(d.order_item(expr, way))
    lines.append('ORDER BY ' + ', '.join(ordering))
    lines.append(d.limit_offset(spec.limit + 1, spec.offset))
    return CompiledQuery('\n'.join(lines), dict(c.params), [o.public() for o in outs], spec.limit, spec.offset,
                         spec.limit + 1, join_infos, c.warnings, _fingerprint(c))


def build_probe(c: _Compiler) -> CompiledQuery:
    """`SELECT <column> FROM ... WHERE <filters> GROUP BY <column> LIMIT 13`, with no ORDER BY, so the database
    collation never decides which values appear."""
    d, pivot = c.d, c.spec.pivot
    if pivot is None or pivot.columns is None:
        c.err('pivot_conflict', 'pivot.columns', 'This pivot has no columns to look up.')
        raise SpecErrors(c.errors)
    _conflicts(c)
    from_lines, join_infos = c.tables_and_joins()
    column = _dimension(c, pivot.columns, 'pivot.columns', column=True)
    where = c.filters()
    if c.errors:
        raise SpecErrors(c.errors)
    lines = [f'SELECT {column.expr} AS {d.quote("v")}', *from_lines]
    if where:
        lines.append('WHERE ' + where)
    lines.append('GROUP BY ' + ', '.join(d.group_by_items(column.expr, column.derived, None if pivot.columns.bucket else column.bare)))
    lines.append(d.limit_offset(PIVOT_PROBE_ROWS, 0))
    meta = [{'label': 'v', 'alias': 'v', 'value_kind': column.out_kind, 'role': 'pivot_probe', 'name': column.label}]
    return CompiledQuery('\n'.join(lines), dict(c.params), meta, PIVOT_PROBE_ROWS, 0, PIVOT_PROBE_ROWS, join_infos,
                         c.warnings, _fingerprint(c))


def pivot_values_from_rows(rows: list[list], kind: str, name: str) -> list[PivotColumnValue]:
    """Probe rows as ordered column values. Non-null values sort in Python (false before true, numbers
    ascending, text by code point, bucket labels as text, which is chronological); the empty column is last."""
    if len(rows) >= PIVOT_PROBE_ROWS:
        raise SpecErrors([SpecError('pivot_too_many_columns', 'pivot.columns',
            f'{name[:1].upper() + name[1:]} has more than {PIVOT_MAX_COLUMNS} different values. Pivots show at most '
            f'{PIVOT_MAX_COLUMNS} columns. Group by a larger step, or filter first.')])
    found: dict[object, None] = {}
    empty = False
    for row in rows:
        raw = row[0]
        if raw is None:
            empty = True
        elif kind == 'boolean':
            found[bool(raw)] = None
        elif kind == 'integer':
            found[int(raw)] = None
        else:
            found[str(raw)] = None
    ordered = sorted(found)
    return [PivotColumnValue(v, False) for v in ordered] + ([PivotColumnValue(None, True)] if empty else [])
