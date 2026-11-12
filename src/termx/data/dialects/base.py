"""Dialect adapters for the visual query builder.

One concrete adapter per supported engine. Nothing here talks to a database: an
adapter only renders SQL text fragments, classifies declared types and normalises
bound values. The compiler (`termx.data.query_spec`) composes these fragments, and
the compiled statement still passes `validate_read` and the source's own read-only
enforcement before it runs.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import uuid
from abc import ABC, abstractmethod
from typing import ClassVar, Literal

TypeKind = Literal['text', 'integer', 'number', 'boolean', 'date', 'datetime', 'time', 'json', 'binary', 'uuid', 'array', 'other']
Bucket = Literal['day', 'week', 'month', 'quarter', 'year']
TextMode = Literal['contains', 'starts_with']
Direction = Literal['asc', 'desc']
AggregateName = Literal['count', 'count_distinct', 'sum', 'avg', 'min', 'max']

LIKE_ESCAPE = '!'  # identical on every engine; backslash escaping is not
BUCKETS: tuple[str, ...] = ('day', 'week', 'month', 'quarter', 'year')
TEXT_MODES: tuple[str, ...] = ('contains', 'starts_with')
BUCKET_LABEL_FORMATS = {'day': 'YYYY-MM-DD', 'week': 'YYYY-Www', 'month': 'YYYY-MM',
                        'quarter': 'YYYY-Qn', 'year': 'YYYY'}
FILTER_OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in',
              'contains', 'starts_with', 'is_null', 'not_null']
AGGREGATES = ['count', 'count_distinct', 'sum', 'avg', 'min', 'max']
MAX_IN_VALUES = 200
MAX_JOINS = 4
MAX_FILTERS = 50
INT64 = 2 ** 63

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DATETIME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d{1,6})?)?$')
TIME_RE = re.compile(r'^(\d{2}):(\d{2})(?::(\d{2})(\.\d{1,6})?)?$')
_NUMBER_RE = re.compile(r'^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$')
_TRUE = {'true', 't', 'yes', 'y', '1'}
_FALSE = {'false', 'f', 'no', 'n', '0'}


class Dialect(ABC):
    engine: ClassVar[str]               # connectors.py engine id
    label: ClassVar[str]
    sqlglot_dialect: ClassVar[str]      # same value connectors.dialect() returns
    version_floor: ClassVar[str]        # documented minimum server version
    bucketable_kinds: ClassVar[frozenset[str]]
    quote_char: ClassVar[str] = '"'
    table_qualification: ClassVar[Literal['none', 'schema', 'database']] = 'none'
    case_folding: ClassVar[Literal['ascii', 'unicode']] = 'unicode'
    text_equality: ClassVar[Literal['exact', 'collation']] = 'exact'
    bool_bind: ClassVar[Literal['integer', 'boolean']] = 'boolean'
    # Kinds with a total order on this engine: range filters, min and max.
    orderable_kinds: ClassVar[frozenset[str]] = frozenset({'integer', 'number', 'text', 'date', 'datetime', 'time'})
    # SQLite columns without a declared type hold any storage class, so joins on them are not type checked.
    loose_typing: ClassVar[bool] = False
    # Longest output alias the server keeps verbatim, in UTF-8 bytes. PostgreSQL truncates at 63.
    max_alias_bytes: ClassVar[int] = 255

    # Identifiers and names -------------------------------------------------------------
    def quote(self, identifier: str) -> str:
        """Double the quote character. Names that no engine path can carry safely are refused."""
        if not isinstance(identifier, str) or not identifier or ':' in identifier \
                or any(ord(c) < 32 or ord(c) == 127 for c in identifier):
            raise ValueError('Unsupported identifier.')
        marker = self.quote_char
        return marker + identifier.replace(marker, marker + marker) + marker

    @abstractmethod
    def table_ref(self, schema: str | None, table: str, alias: str) -> str:
        """FROM item including `AS alias`."""

    def param(self, name: str) -> str:
        if not re.fullmatch(r'p[0-9]{1,5}', name):
            raise ValueError('Unsupported parameter name.')
        return ':' + name

    # Types and values ------------------------------------------------------------------
    @abstractmethod
    def classify_type(self, declared: str) -> TypeKind: ...

    @abstractmethod
    def coerce_param(self, value: object, kind: str) -> object:
        """Normalise a user value for a column of `kind`; raises ValueError."""

    @abstractmethod
    def bool_literal(self, value: bool) -> str: ...

    @abstractmethod
    def cast_text(self, expr: str) -> str: ...

    # Predicates ------------------------------------------------------------------------
    def escape_like(self, value: str) -> str:
        return value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace('%', LIKE_ESCAPE + '%').replace('_', LIKE_ESCAPE + '_')

    def like_pattern(self, value: str, mode: str) -> str:
        """Escape, then wrap, the value that is bound to `text_match`."""
        _check_mode(mode)
        escaped = self.escape_like(value)
        return '%' + escaped + '%' if mode == 'contains' else escaped + '%'

    @abstractmethod
    def text_match(self, column: str, kind: str, mode: str, param: str) -> str: ...

    @abstractmethod
    def in_list(self, column: str, params: list[str], negate: bool) -> str: ...

    @abstractmethod
    def between(self, column: str, low: str, high: str, *, high_exclusive: bool) -> str: ...

    # Ordering and paging ---------------------------------------------------------------
    @abstractmethod
    def order_item(self, expr: str, direction: str) -> str: ...

    @abstractmethod
    def limit_offset(self, limit: int, offset: int) -> str: ...

    def group_by_items(self, expr: str, derived: bool, column: str | None = None) -> list[str]:
        """GROUP BY entries for one group expression. `derived` is true when `expr` is an expression over the
        column rather than the bare column. `column` is that bare column when the expression only changes how its
        text compares (never for a date bucket)."""
        return [expr]

    # Aggregation -----------------------------------------------------------------------
    def text_operand(self, expr: str, charset: str | None = None) -> str:
        """A text column as it must be compared, grouped or counted: exact, like SQLite and PostgreSQL.
        Engines whose column collation folds case or accents override this."""
        return expr

    def avg_expr(self, expr: str) -> str:
        """AVG as a double on every engine, so the scale of the result does not depend on the server."""
        return f'AVG({expr})'

    def count_distinct(self, expr: str) -> str:
        return f'COUNT(DISTINCT {expr})'

    def conditional_aggregate(self, fn: str, value: str | None, predicate: str) -> str:
        """`FN(CASE WHEN p THEN v END)`. No FILTER clause: MySQL and MariaDB lack it."""
        if fn not in AGGREGATES:
            raise ValueError('Unsupported aggregate.')
        inner = f'CASE WHEN {predicate} THEN {value if value is not None else "1"} END'
        if fn == 'count_distinct':
            return self.count_distinct(inner)
        return f'{fn.upper()}({inner})'

    @abstractmethod
    def date_bucket(self, column: str, kind: str, bucket: str) -> str: ...

    # Session limits --------------------------------------------------------------------
    def statement_timeout_sql(self, seconds: float) -> str | None:
        """Statement that makes the server stop a read after `seconds`, or None when the engine is bounded
        in-process (SQLite uses a progress handler). Rendered from a number, never from user text."""
        return None

    def is_statement_timeout(self, error: BaseException) -> bool:
        """Whether a driver exception means the server stopped the statement at the deadline."""
        return False

    # Capability matrix -----------------------------------------------------------------
    @abstractmethod
    def null_ordering_impl(self) -> Literal['native', 'emulated']: ...

    def qualified(self) -> bool:
        from termx.data.connectors import engines
        item = next((e for e in engines() if e['id'] == self.engine), None)
        return bool(item and item['capabilities']['qualified'])

    def requires_source_test(self) -> bool:
        from termx.data.connectors import engines
        item = next((e for e in engines() if e['id'] == self.engine), None)
        return bool(item and item['capabilities']['requires_source_test'])

    def capabilities(self) -> dict:
        """The connection-independent part of GET .../capabilities."""
        return {
            'engine': self.engine, 'label': self.label, 'supported': True, 'reason': None,
            'qualified': self.qualified(), 'requires_source_test': self.requires_source_test(),
            'dialect': {
                'quote': self.quote_char, 'param_style': 'named_colon',
                'table_qualification': self.table_qualification,
                'text_match': {'modes': list(TEXT_MODES), 'case': 'insensitive',
                               'case_folding': self.case_folding, 'like_escape': LIKE_ESCAPE},
                'text_equality': self.text_equality, 'filter_ops': list(FILTER_OPS),
                'max_in_values': MAX_IN_VALUES, 'null_ordering': 'last',
                'null_ordering_impl': self.null_ordering_impl(),
                'boolean': {'literal_true': self.bool_literal(True), 'literal_false': self.bool_literal(False),
                            'bind': self.bool_bind},
                'limit_syntax': 'limit_offset', 'aggregates': list(AGGREGATES),
                'conditional_aggregation': 'case',
                'date_bucket': {'buckets': list(BUCKETS), 'week_standard': 'iso8601',
                                'labels': dict(BUCKET_LABEL_FORMATS),
                                'kinds': [k for k in ('date', 'datetime', 'text', 'other') if k in self.bucketable_kinds]},
                'join_kinds': ['inner', 'left'], 'max_joins': MAX_JOINS, 'max_filters': MAX_FILTERS,
                'pivot': False, 'version_floor': self.version_floor,
            },
        }

    # Shared helpers used by the concrete adapters --------------------------------------
    def _in_list(self, column: str, params: list[str], negate: bool) -> str:
        if not params or len(params) > MAX_IN_VALUES:
            raise ValueError(f'A list takes 1 to {MAX_IN_VALUES} values.')
        items = ', '.join(params)
        return f'({column} NOT IN ({items}) OR {column} IS NULL)' if negate else f'{column} IN ({items})'

    def _between(self, column: str, low: str, high: str, high_exclusive: bool) -> str:
        if high_exclusive:
            return f'({column} >= {low} AND {column} < {high})'
        return f'{column} BETWEEN {low} AND {high}'

    def _text_match(self, column: str, kind: str, mode: str, param: str) -> str:
        _check_mode(mode)
        subject = column if kind == 'text' else self.cast_text(column)
        return f"LOWER({subject}) LIKE LOWER({param}) ESCAPE '{LIKE_ESCAPE}'"

    @staticmethod
    def _limit_offset(limit: int, offset: int) -> str:
        for number in (limit, offset):
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise ValueError('Paging values must be non-negative integers.')
        if limit < 1:
            raise ValueError('The limit must be at least 1.')
        return f'LIMIT {int(limit)} OFFSET {int(offset)}'

    @staticmethod
    def _direction(direction: str) -> str:
        if direction not in ('asc', 'desc'):
            raise ValueError('Direction must be asc or desc.')
        return direction.upper()

    def _check_bucket(self, kind: str, bucket: str) -> None:
        if bucket not in BUCKETS:
            raise ValueError('Unsupported date bucket.')
        if kind not in self.bucketable_kinds:
            raise ValueError(f'A {kind} column cannot be grouped by date on {self.label}.')

    def _coerce_base(self, value: object, kind: str) -> object:
        """Engine-independent normalisation. Bool-to-int is left to the adapter."""
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Numbers must be finite.')
        if kind == 'integer':
            if isinstance(value, bool):
                raise ValueError('Enter a whole number.')
            if isinstance(value, int):
                result = value
            elif isinstance(value, float) and value.is_integer():
                result = int(value)
            elif isinstance(value, str) and re.fullmatch(r'\s*[+-]?\d{1,30}\s*', value):
                result = int(value)
            else:
                raise ValueError('Enter a whole number.')
            if not -INT64 <= result < INT64:
                raise ValueError('That number is too large.')
            return result
        if kind == 'number':
            if isinstance(value, bool):
                raise ValueError('Enter a number.')
            if isinstance(value, (int, float)):
                result = value
            elif isinstance(value, str) and _NUMBER_RE.match(value.strip()):
                text = value.strip()
                result = int(text) if re.fullmatch(r'[+-]?\d{1,18}', text) else float(text)
            else:
                raise ValueError('Enter a number.')
            result = _fit_driver_number(result)
            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError('Enter a number.')
            return result
        if kind == 'boolean':
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
                return value.strip().lower() in _TRUE
            raise ValueError('Enter yes or no.')
        if kind == 'date':
            if isinstance(value, str) and DATE_RE.match(value.strip()):
                try:
                    return dt.date.fromisoformat(value.strip()).isoformat()
                except ValueError:
                    pass
            raise ValueError('Enter a date as YYYY-MM-DD.')
        if kind == 'datetime':
            if isinstance(value, str):
                text = value.strip()
                if DATE_RE.match(text):
                    try:
                        return dt.date.fromisoformat(text).isoformat()  # whole day; the compiler builds the range
                    except ValueError:
                        raise ValueError('Enter a date as YYYY-MM-DD.') from None
                found = DATETIME_RE.match(text)
                if found:
                    day, hour, minute, second, fraction = found.groups()
                    try:
                        dt.date.fromisoformat(day)
                        dt.time(int(hour), int(minute), int(second or 0))
                    except ValueError:
                        raise ValueError('Enter a date and time as YYYY-MM-DD HH:MM:SS.') from None
                    return f'{day} {hour}:{minute}:{second or "00"}{fraction or ""}'
            raise ValueError('Enter a date and time as YYYY-MM-DD HH:MM:SS, without a time zone.')
        if kind == 'time':
            if isinstance(value, str):
                found = TIME_RE.match(value.strip())
                if found:
                    hour, minute, second, fraction = found.groups()
                    try:
                        dt.time(int(hour), int(minute), int(second or 0))
                    except ValueError:
                        raise ValueError('Enter a time as HH:MM:SS.') from None
                    return f'{hour}:{minute}:{second or "00"}{fraction or ""}'
            raise ValueError('Enter a time as HH:MM:SS.')
        if kind == 'uuid':
            if isinstance(value, str):
                try:
                    return str(uuid.UUID(value.strip()))
                except ValueError:
                    pass
            raise ValueError('Enter a UUID, for example 123e4567-e89b-12d3-a456-426614174000.')
        if isinstance(value, str) and '\x00' in value:
            raise ValueError('Text cannot contain a null character.')
        if kind == 'text':
            if isinstance(value, bool):
                raise ValueError('Enter text.')
            if isinstance(value, (str, int, float)):
                return value if isinstance(value, str) else str(value)
            raise ValueError('Enter text.')
        if isinstance(value, (str, int, float, bool)):
            return value if isinstance(value, (str, bool)) else _fit_driver_number(value)
        raise ValueError('Unsupported value.')


def _fit_driver_number(value: int | float) -> int | float:
    """Drivers bind integers as 64-bit values (SQLite raises OverflowError beyond that). A larger whole
    number can only be compared as a float, which is how the string form is already read."""
    if isinstance(value, int) and not -INT64 <= value < INT64:
        try:
            return float(value)
        except OverflowError:
            raise ValueError('That number is too large.') from None
    return value


def _check_mode(mode: str) -> None:
    if mode not in TEXT_MODES:
        raise ValueError('Unsupported text match mode.')


def declared_words(declared: str) -> str:
    """Lower-case declared type text without length/precision, for matching."""
    return re.sub(r'\(.*?\)', '', (declared or '')).strip().lower()
