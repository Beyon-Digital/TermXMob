"""QuerySpec models and the pure compiler that turns a spec into one parameterised SELECT.

The compiler does no I/O. It is given the introspected schema and a dialect, validates every
name against that schema (exact match, no folding), renders identifiers through the dialect's
quoting and binds every user value as `:pN`. Only `limit` and `offset` are rendered into the
statement text, as validated integers. The caller still runs `validate_read` on the result.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from termx.data.dialects.base import DATE_RE, MAX_FILTERS, Dialect

MAX_PARAMETERS = 1000
MAX_NESTING = 4
PIVOT_MAX_COLUMNS = 12
PIVOT_PROBE_ROWS = PIVOT_MAX_COLUMNS + 1
SEGMENT_MAX_DEPTH = 3
SEGMENT_MAX_EXPANSIONS = 20
SEGMENT_MAX_LEAVES = 200

ALIAS_PATTERN = r'^[A-Za-z_][A-Za-z0-9_]{0,31}$'
Alias = Annotated[str, Field(pattern=ALIAS_PATTERN)]


def _encodable(value: str) -> str:
    """Lone surrogates are valid Python text but cannot be sent to a driver or hashed."""
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('Text must be valid Unicode.') from None
    return value


Text = Annotated[str, AfterValidator(_encodable)]
Name = Annotated[Text, Field(min_length=1, max_length=128)]
Label = Annotated[Text, Field(min_length=1, max_length=60)]
Scalar = Union[
    Annotated[bool, Field(strict=True)],
    Annotated[int, Field(strict=True)],
    Annotated[float, Field(strict=True, allow_inf_nan=False)],
    Annotated[Text, Field(strict=True, max_length=500)]]
Bucket = Literal['day', 'week', 'month', 'quarter', 'year']
LeafOp = Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in',
                 'contains', 'starts_with', 'is_null', 'not_null']


class Spec(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, populate_by_name=True)


class TableRef(Spec):
    table: Name
    alias: Alias


class ColumnRef(Spec):
    table_alias: Alias
    column: Name


class JoinOn(Spec):
    left: ColumnRef
    right: ColumnRef


class Join(Spec):
    kind: Literal['inner', 'left']
    table: Name
    alias: Alias
    on: list[JoinOn] = Field(min_length=1, max_length=4)


class SelectColumn(Spec):
    table_alias: Alias
    column: Name
    label: Label | None = None


class FilterLeaf(Spec):
    field: ColumnRef
    op: LeafOp
    value: Scalar | None = None
    values: list[Scalar] | None = Field(default=None, max_length=200)

    @model_validator(mode='after')
    def _shape(self):
        op, value, values = self.op, self.value, self.values
        if op in ('eq', 'ne', 'gt', 'gte', 'lt', 'lte'):
            ok = value is not None and values is None
        elif op in ('contains', 'starts_with'):
            ok = isinstance(value, str) and values is None
        elif op in ('in', 'not_in'):
            ok = value is None and values is not None and 1 <= len(values) <= 200
        elif op == 'between':
            ok = value is None and values is not None and len(values) == 2
        else:
            ok = value is None and values is None
        if not ok:
            raise ValueError(f"The '{op}' filter does not accept these values.")
        return self


class FilterSegment(Spec):
    """Membership of a saved segment: the segment's filters, inlined at compile time."""
    segment: str = Field(min_length=1, max_length=64)
    negate: bool = Field(default=False, strict=True)


def _filter_tag(value: Any) -> str:
    if isinstance(value, dict):
        if 'segment' in value:
            return 'segment'
        op = value.get('op')
    else:
        if isinstance(value, FilterSegment):
            return 'segment'
        op = getattr(value, 'op', None)
    return 'group' if op in ('and', 'or') else 'leaf'


class FilterGroup(Spec):
    op: Literal['and', 'or']
    items: list['FilterNode'] = Field(max_length=MAX_FILTERS)


FilterNode = Annotated[Union[Annotated[FilterLeaf, Tag('leaf')], Annotated[FilterGroup, Tag('group')],
                             Annotated[FilterSegment, Tag('segment')]], Discriminator(_filter_tag)]
FilterGroup.model_rebuild()


class GroupBy(Spec):
    table_alias: Alias
    column: Name
    bucket: Bucket | None = None
    label: Label | None = None


class Aggregate(Spec):
    fn: Literal['count', 'count_distinct', 'sum', 'avg', 'min', 'max']
    table_alias: Alias | None = None
    column: Name | None = None
    label: Label | None = None

    @model_validator(mode='after')
    def _shape(self):
        if (self.table_alias is None) != (self.column is None):
            raise ValueError('Give both table_alias and column, or neither.')
        if self.fn != 'count' and self.column is None:
            raise ValueError(f"The '{self.fn}' aggregate needs a column.")
        return self


class PivotDim(Spec):
    table_alias: Alias
    column: Name
    bucket: Bucket | None = None
    label: Label | None = None


class PivotValue(Aggregate):
    """The measure of a pivot; same shape and rules as a summary aggregate."""


class PivotSort(Spec):
    by: Literal['rows', 'total'] = 'rows'
    direction: Literal['asc', 'desc'] = 'asc'


class PivotSpec(Spec):
    rows: list[PivotDim] = Field(min_length=1, max_length=2)
    columns: PivotDim | None = None
    value: PivotValue
    totals: bool = Field(default=True, strict=True)
    sort: PivotSort = Field(default_factory=PivotSort)

    @model_validator(mode='after')
    def _has_a_value_column(self):
        if self.columns is None and not self.totals:
            raise ValueError('A pivot without columns needs its total column.')
        return self


class ColumnSort(Spec):
    table_alias: Alias
    column: Name
    direction: Literal['asc', 'desc'] = 'asc'


class OutputSort(Spec):
    output: Label
    direction: Literal['asc', 'desc'] = 'asc'


def _empty_filters() -> FilterGroup:
    return FilterGroup(op='and', items=[])


class QuerySpec(Spec):
    version: Literal[1] = 1
    source: Text = Field(min_length=1, max_length=200)
    from_: TableRef = Field(alias='from')
    joins: list[Join] = Field(default_factory=list, max_length=4)
    select: list[SelectColumn] = Field(default_factory=list, max_length=200)
    filters: FilterGroup = Field(default_factory=_empty_filters)
    group_by: list[GroupBy] = Field(default_factory=list, max_length=6)
    aggregates: list[Aggregate] = Field(default_factory=list, max_length=12)
    sort: list[Union[ColumnSort, OutputSort]] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=200, ge=1, le=1000, strict=True)
    offset: int = Field(default=0, ge=0, le=1_000_000, strict=True)
    pivot: PivotSpec | None = None

    @model_validator(mode='after')
    def _filter_limits(self):
        leaves = 0

        def walk(group: FilterGroup, depth: int):
            nonlocal leaves
            if depth > MAX_NESTING:
                raise ValueError(f'Filter groups can be nested at most {MAX_NESTING} levels deep.')
            for item in group.items:
                if isinstance(item, FilterGroup):
                    if not item.items:
                        raise ValueError('A nested filter group needs at least one filter.')
                    walk(item, depth + 1)
                else:
                    leaves += 1
                    if leaves > MAX_FILTERS:
                        raise ValueError(f'At most {MAX_FILTERS} filters are allowed.')
        walk(self.filters, 1)
        return self


# --- schema index ------------------------------------------------------------------------
@dataclass(frozen=True)
class ColumnInfo:
    name: str
    kind: str
    declared: str = ''
    primary_key: bool = False
    charset: str | None = None      # MySQL family only; decides how text is compared exactly


@dataclass(frozen=True)
class TableInfo:
    name: str
    schema: str | None
    kind: str
    columns: dict[str, ColumnInfo]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[dict, ...] = ()
    row_estimate: int | None = None
    readable: bool = True


class SchemaIndex:
    """Case-sensitive lookup over the introspected tables of one source."""

    def __init__(self, tables: dict[str, TableInfo]):
        self.tables = tables

    @classmethod
    def from_schema(cls, schema: dict, dialect: Dialect) -> 'SchemaIndex':
        tables: dict[str, TableInfo] = {}
        for raw in schema.get('tables', []):
            columns: dict[str, ColumnInfo] = {}
            for column in raw.get('columns', []):
                kind = column.get('value_kind') or dialect.classify_type(column.get('type') or '')
                columns[column['name']] = ColumnInfo(column['name'], kind, str(column.get('type') or ''),
                                                     bool(column.get('primary_key')), column.get('charset'))
            tables[raw['name']] = TableInfo(
                raw['name'], raw.get('schema'), raw.get('kind') or 'table', columns,
                tuple(name for name, info in columns.items() if info.primary_key),
                tuple(raw.get('foreign_keys') or ()), raw.get('row_estimate'), raw.get('readable', True) is not False)
        return cls(tables)


# --- compiler output ---------------------------------------------------------------------
@dataclass(frozen=True)
class SpecError:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict:
        return {'code': self.code, 'path': self.path, 'message': self.message}


_SECTIONS = ('from', 'joins', 'select', 'filters', 'group_by', 'aggregates', 'sort', 'pivot')


def _section(error: SpecError) -> int:
    head = re.split(r'[.\[]', error.path, maxsplit=1)[0]
    return _SECTIONS.index(head) if head in _SECTIONS else len(_SECTIONS)


class SpecErrors(Exception):
    """Every problem found, ordered like the spec (stable within a section)."""
    def __init__(self, errors: list[SpecError]):
        self.errors = sorted(errors, key=_section)
        super().__init__(self.errors[0].message if self.errors else 'Invalid query.')


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: dict[str, object]
    columns: list[dict]
    limit: int
    offset: int
    fetch_limit: int
    joins: list[dict]
    warnings: list[dict]
    schema_hash: str


_FAN_OUT_MESSAGE = 'Each {left} row can match many {table} rows, so counts and totals may repeat.'
_ROWS_MULTIPLY_MESSAGE = 'Each {left} row can match many {table} rows, so the result can have more rows than {left}.'
_MANY_TO_MANY_MESSAGE = ('Neither side of the match with {table} is a unique key, so rows can multiply a great deal. '
                         'This query may be slow, and it is stopped if it takes too long.')
_NO_PK_MESSAGE = '{table} has no primary key, so matching rows cannot be guaranteed to be unique.'
_AGGREGATE_LABELS = {'count': 'Number of {}', 'count_distinct': 'Unique {}', 'sum': 'Total {}',
                     'avg': 'Average {}', 'min': 'Smallest {}', 'max': 'Largest {}'}
_NUMERIC = {'integer', 'number'}
_OPAQUE = {'json', 'binary', 'array'}   # no equality or ordering on every engine
_OPAQUE_NAMES = {'json': 'JSON', 'binary': 'binary data', 'array': 'a list of values'}


def _bad_name(name: str) -> bool:
    return not isinstance(name, str) or ':' in name or any(ord(c) < 32 or ord(c) == 127 for c in name)


def _compatible(dialect: Dialect, a: str, b: str) -> bool:
    if a in _OPAQUE or b in _OPAQUE:
        return False
    if dialect.loose_typing and 'other' in (a, b):
        return True   # SQLite columns without a declared type hold any storage class
    groups = [{'integer', 'number'}, {'date', 'datetime'}]
    if a == b:
        return True
    return any(a in g and b in g for g in groups)


@dataclass
class _Out:
    label: str
    expr: str
    kind: str
    role: str
    table_alias: str | None = None
    column: str | None = None
    fn: str | None = None
    bucket: str | None = None
    label_path: str = 'select'
    explicit: bool = False
    derived: bool = False   # an expression over the column (bucket, exact text), not the bare column
    alias: str | None = None            # pivot outputs: a neutral SQL alias; the label is metadata only
    column_value: object = None
    column_empty: bool = False
    value_column: str | None = None

    def public(self) -> dict:
        body: dict = {'label': self.label, 'value_kind': self.kind, 'role': self.role}
        if self.alias is not None:
            body['alias'] = self.alias
        for key in ('table_alias', 'column', 'fn', 'bucket'):
            if getattr(self, key) is not None:
                body[key] = getattr(self, key)
        if self.role == 'pivot_cell':
            body['column_value'] = self.column_value
            body['column_empty'] = self.column_empty
        return body


class _Compiler:
    def __init__(self, spec: QuerySpec, index: SchemaIndex, dialect: Dialect, segments: dict | None = None,
                 pivot_values: list | None = None, dataset: bool = False):
        self.spec, self.index, self.d = spec, index, dialect
        self.dataset = dataset   # a derived table for a report: all rows, no ordering or paging of its own
        self.segments: dict = segments or {}
        self.pivot_values = pivot_values
        self.expansions = 0
        self.expanded_leaves = 0
        self._empty_segments: set[str] = set()
        self._leaf_cap = False
        self.errors: list[SpecError] = []
        self.params: dict[str, object] = {}
        self.aliases: dict[str, TableInfo] = {}     # exact alias -> table
        self.order: list[str] = []                  # aliases in FROM/JOIN order
        self.used: dict[str, dict[str, str]] = {}   # table -> column -> kind, for the schema hash
        self.warnings: list[dict] = []
        self.join_left: dict[str, str] = {}          # join alias -> table on the left of its first match
        self.many_to_many: set[str] = set()         # join aliases where neither side of ON is a key
        self._too_many = False

    # --- helpers -------------------------------------------------------------------------
    def err(self, code: str, path: str, message: str) -> None:
        self.errors.append(SpecError(code, path, message))

    def use(self, table: TableInfo, column: ColumnInfo | None = None) -> None:
        entry = self.used.setdefault(table.name, {})
        if column is not None:
            entry[column.name] = column.kind

    def bind(self, value: object) -> str:
        name = f'p{len(self.params) + 1}'
        if len(self.params) >= MAX_PARAMETERS:
            if not self._too_many:
                self._too_many = True
                self.err('too_many_values', 'filters', f'A query can bind at most {MAX_PARAMETERS} values.')
            return self.d.param('p1')
        self.params[name] = value
        return self.d.param(name)

    def resolve_table(self, name: str, path: str) -> TableInfo | None:
        table = self.index.tables.get(name)
        if table is None:
            self.err('unknown_table', path, f"Table '{name}' does not exist in this source.")
            return None
        if not table.readable:
            self.err('unknown_table', path, f"Table '{name}' cannot be read under the read-only policy.")
            return None
        if _bad_name(name):
            self.err('unsupported_name', path, f"Table '{name}' has a name the query builder cannot use. Use Advanced SQL.")
            return None
        return table

    def resolve_column(self, ref_alias: str, column: str, path: str, alias_path: str | None = None):
        """Return (alias, table, column) or None after recording an error."""
        table = self.aliases.get(ref_alias)
        if table is None:
            self.err('unknown_alias', alias_path or path.rsplit('.', 1)[0] + '.table_alias',
                     f"No table is combined as '{ref_alias}'.")
            return None
        info = table.columns.get(column)
        if info is None:
            self.err('unknown_column', path, f"Column '{column}' does not exist in table '{table.name}'.")
            return None
        if _bad_name(column):
            self.err('unsupported_name', path, f"Column '{column}' has a name the query builder cannot use. Use Advanced SQL.")
            return None
        self.use(table, info)
        return ref_alias, table, info

    def col_sql(self, alias: str, column: str) -> str:
        return f'{self.d.quote(alias)}.{self.d.quote(column)}'

    # --- phase 1: tables and joins ------------------------------------------------------
    def tables_and_joins(self) -> tuple[list[str], list[dict]]:
        spec, lines, infos = self.spec, [], []
        seen: set[str] = set()

        def claim(alias: str, path: str) -> bool:
            if alias.lower() in seen:
                self.err('duplicate_alias', path, f"The name '{alias}' is used for more than one table.")
                return False
            seen.add(alias.lower())
            return True

        base = self.resolve_table(spec.from_.table, 'from.table')
        base_ok = claim(spec.from_.alias, 'from.alias')
        if base is not None and base_ok:
            self.aliases[spec.from_.alias] = base
            self.order.append(spec.from_.alias)
            self.use(base)
            lines.append('FROM ' + self.d.table_ref(base.schema, base.name, spec.from_.alias))
        for i, join in enumerate(spec.joins):
            path = f'joins[{i}]'
            table = self.resolve_table(join.table, path + '.table')
            claimed = claim(join.alias, path + '.alias')
            earlier = list(self.order)
            if table is not None and claimed:
                self.aliases[join.alias] = table
                self.order.append(join.alias)
                self.use(table)
            conditions, right_columns, left_table, left_columns = [], set(), None, {}
            for j, pair in enumerate(join.on):
                p = f'{path}.on[{j}]'
                if pair.right.table_alias != join.alias:
                    self.err('bad_join', p + '.right.table_alias', f"The right side of a match must be a column of '{join.alias}'.")
                    continue
                if pair.left.table_alias not in earlier:
                    self.err('bad_join', p + '.left.table_alias',
                             'The left side of a match must be a column of the base table or a table combined before this one.')
                    continue
                left = self.resolve_column(pair.left.table_alias, pair.left.column, p + '.left.column', p + '.left.table_alias')
                right = self.resolve_column(pair.right.table_alias, pair.right.column, p + '.right.column', p + '.right.table_alias') \
                    if table is not None and claimed else None
                if left is None or right is None:
                    continue
                if not _compatible(self.d, left[2].kind, right[2].kind):
                    self.err('join_type_mismatch', p, f"'{left[1].name}.{left[2].name}' ({left[2].kind}) cannot be matched "
                             f"with '{right[1].name}.{right[2].name}' ({right[2].kind}).")
                    continue
                a, b = self.col_sql(pair.left.table_alias, pair.left.column), self.col_sql(pair.right.table_alias, pair.right.column)
                if left[2].kind == 'text' and right[2].kind == 'text':   # same exactness as filters and groups
                    a, b = self.d.text_operand(a, left[2].charset), self.d.text_operand(b, right[2].charset)
                conditions.append(f'{a} = {b}')
                right_columns.add(pair.right.column)
                left_columns.setdefault(pair.left.table_alias, set()).add(pair.left.column)
                left_table = left_table or left[1].name
            if table is None or not claimed or len(conditions) != len(join.on):
                continue
            keyword = 'INNER JOIN' if join.kind == 'inner' else 'LEFT JOIN'
            lines.append(f'{keyword} {self.d.table_ref(table.schema, table.name, join.alias)} ON ' + ' AND '.join(conditions))
            if table.primary_key and set(table.primary_key) <= right_columns:
                info = {'alias': join.alias, 'fan_out': 'none', 'message': None}
            elif not table.primary_key:
                info = {'alias': join.alias, 'fan_out': 'unknown', 'message': _NO_PK_MESSAGE.format(table=table.name)}
            else:
                info = {'alias': join.alias, 'fan_out': 'many',
                        'message': _FAN_OUT_MESSAGE.format(left=left_table, table=table.name)}
            infos.append(info)
            self.join_left[join.alias] = left_table
            right_unique = bool(table.primary_key) and set(table.primary_key) <= right_columns
            left_unique = any(self.aliases[a].primary_key and set(self.aliases[a].primary_key) <= cols
                              for a, cols in left_columns.items())
            if not right_unique and not left_unique:
                self.many_to_many.add(join.alias)
            for key in table.primary_key:
                self.use(table, table.columns[key])
        return lines, infos

    # --- phase 2: outputs ----------------------------------------------------------------
    def label_ok(self, label: str, path: str) -> bool:
        if _bad_name(label):
            self.err('unsupported_name', path, 'A result name cannot contain a colon or control characters.')
            return False
        return True

    def outputs(self) -> tuple[list[_Out], bool]:
        spec = self.spec
        summary = bool(spec.group_by or spec.aggregates)
        outs: list[_Out] = []
        if summary:
            if spec.select:
                self.err('summary_conflict', 'select', 'Choose either columns to show or a summary, not both.')
            for i, group in enumerate(spec.group_by):
                path = f'group_by[{i}]'
                found = self.resolve_column(group.table_alias, group.column, path + '.column', path + '.table_alias')
                if found is None:
                    continue
                alias, table, info = found
                expr = self.col_sql(alias, info.name)
                kind = info.kind
                if group.bucket:
                    if info.kind not in self.d.bucketable_kinds:
                        self.err('bucket_requires_date', path + '.bucket',
                                 f"'{info.name}' is not a date column, so it cannot be grouped by {group.bucket}.")
                        continue
                    if info.kind in ('text', 'other'):
                        self.warnings.append({'code': 'text_date', 'message':
                            f"'{info.name}' holds text, so dates are read from that text. Values that are not ISO dates appear empty."})
                    expr = self.d.date_bucket(expr, info.kind, group.bucket)
                    kind = 'text'
                elif info.kind == 'json':
                    self.err('bad_type', path + '.column', f"'{info.name}' holds JSON, which cannot be grouped.")
                    continue
                elif info.kind == 'text':
                    expr = self.d.text_operand(expr, info.charset)
                default = f'{info.name} by {group.bucket}' if group.bucket else info.name
                outs.append(_Out(group.label or default, expr, kind, 'group', alias, info.name, bucket=group.bucket,
                                 label_path=path + '.label', explicit=bool(group.label),
                                 derived=expr != self.col_sql(alias, info.name)))
            for i, agg in enumerate(spec.aggregates):
                out = self.aggregate(i, agg)
                if out is not None:
                    outs.append(out)
        else:
            columns = [(f'select[{i}]', c) for i, c in enumerate(spec.select)]
            if columns:
                for path, c in columns:
                    found = self.resolve_column(c.table_alias, c.column, path + '.column', path + '.table_alias')
                    if found is None:
                        continue
                    alias, table, info = found
                    outs.append(_Out(c.label or info.name, self.col_sql(alias, info.name), info.kind, 'column',
                                     alias, info.name, label_path=path + '.label', explicit=bool(c.label)))
            elif self.order:
                for alias in self.order:
                    table = self.aliases[alias]
                    for info in table.columns.values():
                        if _bad_name(info.name):
                            continue   # only reported when the spec names the column
                        self.use(table, info)
                        outs.append(_Out(info.name, self.col_sql(alias, info.name), info.kind, 'column', alias, info.name))
                if not outs:
                    self.err('unknown_column', 'select', 'There are no readable columns to show.')
        self.finish_labels(outs)
        return outs, summary

    def aggregate(self, i: int, agg: Aggregate, path: str | None = None) -> _Out | None:
        pivot = path is not None
        path = path or f'aggregates[{i}]'
        bad = 'pivot_value_type' if pivot else 'bad_type'
        expr_col, info, alias = None, None, None
        if agg.column is not None and agg.table_alias is not None:
            found = self.resolve_column(agg.table_alias, agg.column, path + '.column', path + '.table_alias')
            if found is None:
                return None
            alias, _, info = found
            expr_col = self.col_sql(alias, info.name)
            if agg.fn == 'count_distinct' and info.kind == 'text':
                expr_col = self.d.text_operand(expr_col, info.charset)
            if agg.fn in ('min', 'max') and info.kind == 'text':
                expr_col = self.d.text_order_operand(expr_col, info.charset)
            if agg.fn in ('sum', 'avg') and info.kind not in _NUMERIC:
                self.err(bad, path + '.column', f"'{info.name}' is not a number column, so it cannot be "
                         f"{'totalled' if agg.fn == 'sum' else 'averaged'}.")
                return None
            if agg.fn in ('min', 'max') and info.kind not in self.d.orderable_kinds:
                self.err(bad, path + '.column', f"'{info.name}' cannot be used for the {'smallest' if agg.fn == 'min' else 'largest'} value.")
                return None
            if agg.fn == 'count_distinct' and info.kind == 'json':
                self.err(bad, path + '.column', f"'{info.name}' holds JSON, which has no unique values.")
                return None
        if agg.fn == 'count':
            expr = f'COUNT({expr_col or "*"})'
        elif agg.fn == 'count_distinct':
            expr = self.d.count_distinct(expr_col or '*')
        elif agg.fn == 'avg':
            expr = self.d.avg_expr(expr_col)
        else:
            expr = f'{agg.fn.upper()}({expr_col})'
        if agg.fn in ('count', 'count_distinct'):
            kind = 'integer'
        elif agg.fn == 'avg' or info is None:
            kind = 'number'
        elif agg.fn in ('min', 'max'):
            kind = info.kind
        else:
            kind = 'integer' if info.kind == 'integer' else 'number'
        if agg.label:
            label = agg.label
        elif info is None:
            label = 'Number of rows'
        else:
            label = _AGGREGATE_LABELS[agg.fn].format(info.name)
        out = _Out(label, expr, kind, 'aggregate', alias, info.name if info else None, fn=agg.fn,
                   label_path=path + '.label', explicit=bool(agg.label))
        out.value_column = expr_col       # the column expression, for a pivot cell
        return out

    def finish_labels(self, outs: list[_Out]) -> None:
        # A default label that collides with another default becomes "alias.column".
        counts: dict[str, int] = {}
        for out in outs:
            counts[out.label.lower()] = counts.get(out.label.lower(), 0) + 1
        for out in outs:
            if not out.explicit and counts[out.label.lower()] > 1 and out.table_alias and out.column and out.role == 'column':
                out.label = f'{out.table_alias}.{out.column}'
        seen: dict[str, _Out] = {}
        limit = self.d.max_alias_bytes
        for out in outs:
            path = out.label_path
            if not self.label_ok(out.label, path):
                continue
            if len(out.label.encode('utf-8')) > limit:
                if out.explicit:
                    self.err('label_too_long', path, f'A result name can be at most {limit} bytes on {self.d.label}. '
                             'Use a shorter name.')
                    continue
                # The server would cut a longer alias, so the result column would no longer match its label.
                out.label = out.label.encode('utf-8')[:limit].decode('utf-8', errors='ignore').rstrip()
            key = out.label.lower()
            if key in seen:
                self.err('duplicate_label', path, f"Two results are named '{out.label}'. Give one a different name.")
            seen[key] = out

    # --- phase 3: filters ----------------------------------------------------------------
    def filters(self) -> str | None:
        return self.group(self.spec.filters, 'filters', True, None, (), self.spec)

    def group(self, node: FilterGroup, path: str, root: bool, amap: dict[str, str] | None, stack: tuple[str, ...],
              host: QuerySpec) -> str | None:
        """Render a filter tree. `amap` maps the aliases of a segment's tree onto this query's aliases and `host`
        is the spec whose table names the tree's aliases refer to. Errors inside a segment report the path of
        the outermost reference."""
        parts = []
        for i, item in enumerate(node.items):
            sub = f'{path}.items[{i}]' if amap is None else path
            if isinstance(item, FilterGroup):
                text = self.group(item, sub, False, amap, stack, host)
            elif isinstance(item, FilterSegment):
                text = self.segment(item, sub, amap, stack, host)
            else:
                if amap is not None:
                    self.expanded_leaves += 1
                    if self.expanded_leaves > SEGMENT_MAX_LEAVES:
                        if not getattr(self, '_leaf_cap', False):
                            self._leaf_cap = True
                            self.err('segment_too_large', path, f'The segments used here add up to more than {SEGMENT_MAX_LEAVES} filters.')
                        continue
                    item = item.model_copy(update={'field': ColumnRef(
                        table_alias=amap.get(item.field.table_alias, item.field.table_alias), column=item.field.column)})
                before = len(self.errors)
                text = self.leaf(item, sub)
                if amap is not None:   # a problem inside a segment is reported at the outermost reference
                    self.errors[before:] = [SpecError(e.code, path, e.message) for e in self.errors[before:]]
            if text:
                parts.append(text)
        if not parts:
            return None
        joined = f' {node.op.upper()} '.join(parts)
        if len(parts) > 1 and not (root and node.op == 'and'):
            return f'({joined})'
        return joined

    def segment(self, node: FilterSegment, path: str, amap: dict[str, str] | None, stack: tuple[str, ...],
                host: QuerySpec) -> str | None:
        from termx.data.segments import MISSING, cycle_message, map_aliases
        found = self.segments.get(node.segment)
        if found is None:
            self.err('segment_missing', path, MISSING)
            return None
        if node.segment in stack:
            names = tuple(self.segments[s].name for s in (*stack, node.segment) if s in self.segments)
            self.err('segment_cycle', path, cycle_message(names))
            return None
        if len(stack) + 1 > SEGMENT_MAX_DEPTH:
            self.err('segment_depth', path, f'Segments can use other segments at most {SEGMENT_MAX_DEPTH} levels deep.')
            return None
        self.expansions += 1
        if self.expansions > SEGMENT_MAX_EXPANSIONS:
            if self.expansions == SEGMENT_MAX_EXPANSIONS + 1:
                self.err('segment_too_large', path, f'A query can use at most {SEGMENT_MAX_EXPANSIONS} segments in total.')
            return None
        if found.source != self.spec.source:
            self.err('segment_source_mismatch', path, f"The segment '{found.name}' belongs to a different source.")
            return None
        local, problem = map_aliases(host.from_, host.joins, found)
        if problem is not None:
            self.err(problem[0], path, problem[1])
            return None
        final = {k: (amap.get(v, v) if amap is not None else v) for k, v in local.items()}
        text = self.group(found.filters, path, False, final, (*stack, node.segment), found.spec)
        if not found.filters.items:
            if node.segment not in self._empty_segments:
                self._empty_segments.add(node.segment)
                self.warnings.append({'code': 'segment_empty', 'message':
                    f"The segment '{found.name}' has no filters, so it matches every row."})
            text = '1 = 1'
        elif text is None:
            return None
        if node.negate:
            return f'CASE WHEN ({text}) THEN 1 ELSE 0 END = 0'
        return text

    def leaf(self, node: FilterLeaf, path: str) -> str | None:
        ref = node.field
        found = self.resolve_column(ref.table_alias, ref.column, path + '.field.column', path + '.field.table_alias')
        if found is None:
            return None
        alias, _, info = found
        x, kind, op, d = self.col_sql(alias, info.name), info.kind, node.op, self.d
        name = info.name
        if kind == 'text' and op in ('eq', 'ne', 'in', 'not_in', 'contains', 'starts_with'):
            x = d.text_operand(x, info.charset)
        if op == 'is_null':
            return f'{x} IS NULL'
        if op == 'not_null':
            return f'{x} IS NOT NULL'
        if op in ('contains', 'starts_with'):
            try:
                text = d.coerce_param(node.value, 'text')
            except ValueError as error:
                return self.bad_value(path + '.value', name, error, node.value)
            return d.text_match(x, kind, op, self.bind(d.like_pattern(str(text), op)))
        if kind in _OPAQUE:
            self.err('bad_operator', path + '.op', f"'{name}' holds {_OPAQUE_NAMES[kind]}. "
                     'Filter it by whether it is empty, or by text it contains.')
            return None
        if op in ('gt', 'gte', 'lt', 'lte', 'between') and kind not in d.orderable_kinds:
            self.err('bad_operator', path + '.op', f"'{name}' is a {kind} column, which has no before or after.")
            return None
        raw = list(node.values) if node.values is not None else [node.value]
        values, failed = [], False
        for k, item in enumerate(raw):
            where = path + (f'.values[{k}]' if node.values is not None else '.value')
            try:
                values.append(d.coerce_param(item, kind))
            except ValueError as error:
                self.bad_value(where, name, error, item)
                failed = True
        if failed:
            return None
        day_only = kind == 'datetime'
        if op in ('in', 'not_in'):
            if day_only and any(isinstance(v, str) and DATE_RE.match(v) for v in values):
                return self.day_list(x, name, values, op == 'not_in', path)
            return d.in_list(x, [self.bind(v) for v in values], op == 'not_in')
        if op == 'between':
            low, high = values
            high_whole_day = day_only and isinstance(high, str) and bool(DATE_RE.match(high))
            if high_whole_day:
                high = self.next_day(high, path + '.values[1]', name)
                if high is None:
                    return None
            return d.between(x, self.bind(low), self.bind(high), high_exclusive=high_whole_day)
        value = values[0]
        whole_day = day_only and isinstance(value, str) and bool(DATE_RE.match(value))
        if whole_day:
            nxt = self.next_day(value, path + '.value', name)
            if nxt is None:
                return None
            if op == 'eq':
                return f'({x} >= {self.bind(value)} AND {x} < {self.bind(nxt)})'
            if op == 'ne':
                return f'({x} < {self.bind(value)} OR {x} >= {self.bind(nxt)} OR {x} IS NULL)'
            if op == 'gt':
                return f'{x} >= {self.bind(nxt)}'
            if op == 'gte':
                return f'{x} >= {self.bind(value)}'
            if op == 'lt':
                return f'{x} < {self.bind(value)}'
            return f'{x} < {self.bind(nxt)}'   # lte
        if op == 'eq':
            return f'{x} = {self.bind(value)}'
        if op == 'ne':
            return f'({x} <> {self.bind(value)} OR {x} IS NULL)'
        symbol = {'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
        return f'{x} {symbol} {self.bind(value)}'

    def day_list(self, x: str, name: str, values: list, negate: bool, path: str) -> str | None:
        parts = []
        for v in values:
            if isinstance(v, str) and DATE_RE.match(v):
                nxt = self.next_day(v, path + '.values', name)
                if nxt is None:
                    return None
                parts.append(f'({x} >= {self.bind(v)} AND {x} < {self.bind(nxt)})')
            else:
                parts.append(f'{x} = {self.bind(v)}')
        joined = ' OR '.join(parts)
        return f'(NOT ({joined}) OR {x} IS NULL)' if negate else (f'({joined})' if len(parts) > 1 else joined)

    def next_day(self, value: str, path: str, name: str) -> str | None:
        try:
            return (dt.date.fromisoformat(value) + dt.timedelta(days=1)).isoformat()
        except (ValueError, OverflowError):
            self.err('bad_value', path, f"'{name}': that date has no following day.")
            return None

    def bad_value(self, path: str, name: str, error: Exception, value: object) -> None:
        shown = str(value)
        shown = shown if len(shown) <= 40 else shown[:37] + '...'
        self.err('bad_value', path, f"{name}: {error} ('{shown}')")

    # --- phase 4: sort -------------------------------------------------------------------
    def sorting(self, outs: list[_Out], summary: bool) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []   # (expr, direction)
        by_label = {o.label: o for o in outs}
        for i, key in enumerate(self.spec.sort):
            path = f'sort[{i}]'
            if isinstance(key, OutputSort):
                out = by_label.get(key.output)
                if out is None:
                    self.err('bad_sort', path + '.output', f"'{key.output}' is not one of the results, so it cannot be sorted.")
                    continue
                if out.kind in _OPAQUE:
                    self.err('bad_sort', path + '.output', f"'{key.output}' cannot be sorted.")
                    continue
                items.append((out.expr, key.direction))
                continue
            found = self.resolve_column(key.table_alias, key.column, path + '.column', path + '.table_alias')
            if found is None:
                continue
            alias, _, info = found
            if info.kind in _OPAQUE:
                self.err('bad_sort', path + '.column', f"'{info.name}' cannot be sorted.")
                continue
            if summary:
                match = next((o for o in outs if o.role == 'group' and (o.table_alias, o.column) == (alias, info.name)), None)
                if match is None:
                    self.err('bad_sort', path + '.column', 'A summary can be sorted by a column it groups by, or by one of its results.')
                    continue
                items.append((match.expr, key.direction))
            else:
                items.append((self.col_sql(alias, info.name), key.direction))
        return items

    def tie_breakers(self, outs: list[_Out], summary: bool, joins: list[dict]) -> list[str]:
        if summary:
            return [o.expr for o in outs if o.role == 'group']
        base = self.aliases.get(self.spec.from_.alias)
        if base is None:
            return []
        exprs = []
        if base.primary_key:
            exprs += [self.col_sql(self.spec.from_.alias, c) for c in base.primary_key]
        else:
            exprs += [o.expr for o in outs if o.kind not in _OPAQUE]
        for join in joins:
            if join['fan_out'] == 'none':
                continue
            table = self.aliases.get(join['alias'])
            if table is not None and table.primary_key:
                exprs += [self.col_sql(join['alias'], c) for c in table.primary_key]
            elif table is not None:
                exprs += [o.expr for o in outs if o.kind not in _OPAQUE and o.table_alias == join['alias']]
        return exprs

    # --- assembly ------------------------------------------------------------------------
    def build(self) -> CompiledQuery:
        if self.spec.pivot is not None:
            from termx.data.pivot import build_pivot
            return build_pivot(self)
        spec, d = self.spec, self.d
        from_lines, join_infos = self.tables_and_joins()
        outs, summary = self.outputs()
        where = self.filters()
        order_items = self.sorting(outs, summary)
        if self.errors:
            raise SpecErrors(self.errors)
        for info in join_infos:
            if info['alias'] in self.many_to_many:
                table = self.aliases[info['alias']].name
                self.warnings.append({'code': 'many_to_many', 'join_alias': info['alias'],
                                      'message': _MANY_TO_MANY_MESSAGE.format(table=table)})
            elif info['fan_out'] == 'many':
                if spec.aggregates:   # repeated rows only change what is counted or totalled
                    self.warnings.append({'code': 'fan_out', 'message': info['message'], 'join_alias': info['alias']})
                else:
                    self.warnings.append({'code': 'row_multiplication', 'join_alias': info['alias'],
                                          'message': _ROWS_MULTIPLY_MESSAGE.format(
                                              left=self.join_left[info['alias']], table=self.aliases[info['alias']].name)})
        select = ', '.join(f'{o.expr} AS {d.quote(o.label)}' for o in outs)
        lines = ['SELECT ' + select, *from_lines]
        if where:
            lines.append('WHERE ' + where)
        if summary and spec.group_by:
            grouping: list[str] = []
            for o in outs:
                if o.role == 'group':
                    grouping += [g for g in d.group_by_items(o.expr, o.derived, None if o.bucket else self.col_sql(o.table_alias, o.column)) if g not in grouping]
            lines.append('GROUP BY ' + ', '.join(grouping))
        ordering, seen = [], set()
        for expr, direction in order_items:
            if expr not in seen:
                seen.add(expr)
                ordering.append(d.order_item(expr, direction))
        for expr in self.tie_breakers(outs, summary, join_infos):
            if expr not in seen:
                seen.add(expr)
                ordering.append(d.order_item(expr, 'asc'))
        if ordering and not self.dataset:
            lines.append('ORDER BY ' + ', '.join(ordering))
        fetch_limit = spec.limit + 1
        if not self.dataset:
            lines.append(d.limit_offset(fetch_limit, spec.offset))
        if self.errors:
            raise SpecErrors(self.errors)
        digest = hashlib.sha256(json.dumps(
            {t: dict(sorted(c.items())) for t, c in sorted(self.used.items())}, sort_keys=True).encode()).hexdigest()[:16]
        return CompiledQuery('\n'.join(lines), dict(self.params), [o.public() for o in outs], spec.limit, spec.offset,
                             fetch_limit, join_infos, self.warnings, digest)


def compile_spec(spec: QuerySpec, schema: SchemaIndex, dialect: Dialect, *, segments: dict | None = None,
                 pivot_values: list | None = None, dataset: bool = False) -> CompiledQuery:
    """Pure: no I/O, no clock. Raises SpecErrors listing every problem found.

    With `dataset` the statement has no ORDER BY and no LIMIT, so a report can wrap it as a derived table
    and aggregate every row.

    `segments` is the transitive closure of the segments the spec uses (id -> ResolvedSegment) and
    `pivot_values` the distinct column values a pivot found with `compile_pivot_probe`. With `pivot_values`
    None a pivot is only validated: its statement has row and total columns but no cell columns."""
    return _Compiler(spec, schema, dialect, segments, pivot_values, dataset).build()


def compile_pivot_probe(spec: QuerySpec, schema: SchemaIndex, dialect: Dialect, *, segments: dict | None = None) -> CompiledQuery:
    """The bounded distinct read that decides a pivot's columns. Pure; the caller executes it."""
    from termx.data.pivot import build_probe
    return build_probe(_Compiler(spec, schema, dialect, segments, None))
