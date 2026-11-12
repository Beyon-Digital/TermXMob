"""SQLite adapter. Dates are stored as text, so text and untyped columns may be bucketed."""
from __future__ import annotations

import sqlite3

from termx.data.dialects.base import Dialect, TypeKind, declared_words


def sqlite_version() -> tuple[int, ...]:
    """The in-process SQLite library, which is also the engine the read-only connector uses."""
    return tuple(sqlite3.sqlite_version_info)


class SQLiteDialect(Dialect):
    engine = 'sqlite'
    label = 'SQLite'
    sqlglot_dialect = 'sqlite'
    version_floor = '3.30'
    bucketable_kinds = frozenset({'date', 'datetime', 'text', 'other'})
    table_qualification = 'none'
    case_folding = 'ascii'          # lower() folds ASCII only
    text_equality = 'exact'
    bool_bind = 'integer'
    loose_typing = True
    # SQLite orders every storage class, so untyped and boolean columns are orderable too.
    orderable_kinds = Dialect.orderable_kinds | {'other', 'boolean'}

    def table_ref(self, schema: str | None, table: str, alias: str) -> str:
        # Single attached database: names are never qualified.
        return f'{self.quote(table)} AS {self.quote(alias)}'

    def classify_type(self, declared: str) -> TypeKind:
        text = declared_words(declared)
        if text in ('', 'unknown'):
            return 'other'
        if 'json' in text:
            return 'json'
        if 'bool' in text:
            return 'boolean'
        if 'datetime' in text or 'timestamp' in text:
            return 'datetime'
        if 'date' in text:
            return 'date'
        if text == 'time' or text.startswith('time '):
            return 'time'
        if 'int' in text:
            return 'integer'
        if any(word in text for word in ('char', 'clob', 'text')):
            return 'text'
        if 'blob' in text:
            return 'binary'
        if any(word in text for word in ('real', 'floa', 'doub', 'numeric', 'decimal', 'number')):
            return 'number'
        return 'other'

    def coerce_param(self, value: object, kind: str) -> object:
        result = self._coerce_base(value, kind)
        return int(result) if isinstance(result, bool) else result  # SQLite has no boolean type

    def bool_literal(self, value: bool) -> str:
        return '1' if value else '0'

    def cast_text(self, expr: str) -> str:
        return f'CAST({expr} AS TEXT)'

    def text_match(self, column: str, kind: str, mode: str, param: str) -> str:
        return self._text_match(column, kind, mode, param)

    def in_list(self, column: str, params: list[str], negate: bool) -> str:
        return self._in_list(column, params, negate)

    def between(self, column: str, low: str, high: str, *, high_exclusive: bool = False) -> str:
        return self._between(column, low, high, high_exclusive)

    def null_ordering_impl(self):
        return 'native' if sqlite_version() >= (3, 30) else 'emulated'

    def order_item(self, expr: str, direction: str) -> str:
        word = self._direction(direction)
        if self.null_ordering_impl() == 'native':
            return f'{expr} {word} NULLS LAST'
        return f'({expr} IS NULL), {expr} {word}'

    def limit_offset(self, limit: int, offset: int) -> str:
        return self._limit_offset(limit, offset)

    def date_bucket(self, column: str, kind: str, bucket: str) -> str:
        self._check_bucket(kind, bucket)
        x = column
        if bucket == 'day':
            return f"strftime('%Y-%m-%d', {x})"
        if bucket == 'week':
            # ISO 8601: the week belongs to the year of its Thursday.
            thursday = f"date({x},'-3 days','weekday 4')"
            # `printf('%s-W%02d', NULL, NULL)` would yield '-W00'; concatenating onto
            # the year keeps NULL and unparseable dates NULL, as on the other engines.
            return (f"strftime('%Y', {thursday}) || "
                    f"printf('-W%02d', (CAST(strftime('%j', {thursday}) AS INTEGER) - 1) / 7 + 1)")
        if bucket == 'month':
            return f"strftime('%Y-%m', {x})"
        if bucket == 'quarter':
            return f"strftime('%Y', {x}) || '-Q' || ((CAST(strftime('%m', {x}) AS INTEGER) + 2) / 3)"
        return f"strftime('%Y', {x})"
