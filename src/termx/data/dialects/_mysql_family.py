"""Abstract base shared by the MySQL and MariaDB adapters.

It carries only what the two servers do identically: backtick quoting, `date_format`
based buckets, emulated NULL ordering and the declared-type vocabulary that both
inherit. Each concrete adapter declares its own engine id, label, version floor and
any divergence, so a one-server difference stays a one-class change.
"""
from __future__ import annotations

import re
from abc import ABC

from termx.data.dialects.base import Dialect, TypeKind, declared_words


class MySQLFamilyDialect(Dialect, ABC):
    sqlglot_dialect = 'mysql'
    bucketable_kinds = frozenset({'date', 'datetime'})
    quote_char = '`'
    table_qualification = 'database'
    case_folding = 'unicode'
    text_equality = 'collation'     # = and <> follow the column collation, case-insensitive by default
    bool_bind = 'boolean'

    def table_ref(self, schema: str | None, table: str, alias: str) -> str:
        # `schema` is the configured database; MySQL has no separate schema level.
        name = (self.quote(schema) + '.' if schema else '') + self.quote(table)
        return f'{name} AS {self.quote(alias)}'

    # Declared types that both servers share. Subclasses extend `_extra_kind`.
    def classify_type(self, declared: str) -> TypeKind:
        raw = (declared or '').strip().lower()
        if re.match(r'^(tinyint|bool|boolean)(\(1\))?(\s+(signed|unsigned|zerofill))*$', raw) and raw not in ('tinyint',):
            return 'boolean'
        text = declared_words(raw)
        text = re.sub(r'\s+(unsigned|signed|zerofill)', '', text)
        if text in ('tinyint', 'smallint', 'mediumint', 'int', 'integer', 'bigint', 'year', 'serial'):
            return 'integer'
        if text in ('decimal', 'numeric', 'dec', 'fixed', 'float', 'double', 'double precision', 'real'):
            return 'number'
        if text in ('char', 'varchar', 'tinytext', 'text', 'mediumtext', 'longtext', 'enum', 'set'):
            return 'text'
        if text == 'date':
            return 'date'
        if text in ('datetime', 'timestamp'):
            return 'datetime'
        if text == 'time':
            return 'time'
        if text == 'json':
            return 'json'
        if text in ('binary', 'varbinary', 'tinyblob', 'blob', 'mediumblob', 'longblob'):
            return 'binary'
        return self._extra_kind(text)

    def _extra_kind(self, text: str) -> TypeKind:
        return 'other'

    def coerce_param(self, value: object, kind: str) -> object:
        return self._coerce_base(value, kind)   # pymysql sends bool as 1/0

    def bool_literal(self, value: bool) -> str:
        return 'TRUE' if value else 'FALSE'

    def cast_text(self, expr: str) -> str:
        return f'CAST({expr} AS CHAR)'

    def text_match(self, column: str, kind: str, mode: str, param: str) -> str:
        return self._text_match(column, kind, mode, param)

    def in_list(self, column: str, params: list[str], negate: bool) -> str:
        return self._in_list(column, params, negate)

    def between(self, column: str, low: str, high: str, *, high_exclusive: bool = False) -> str:
        return self._between(column, low, high, high_exclusive)

    def null_ordering_impl(self):
        return 'emulated'

    def order_item(self, expr: str, direction: str) -> str:
        # No NULLS LAST: NULLs sort first ascending, so lead with the IS NULL flag.
        return f'({expr} IS NULL), {expr} {self._direction(direction)}'

    def group_by_items(self, expr: str, derived: bool) -> list[str]:
        # With ONLY_FULL_GROUP_BY (the MySQL default) the emulated null flag in ORDER BY must itself be a
        # GROUP BY expression when it wraps a derived expression. Grouping by it does not change the groups.
        return [expr, f'({expr} IS NULL)'] if derived else [expr]

    def limit_offset(self, limit: int, offset: int) -> str:
        return self._limit_offset(limit, offset)

    def date_bucket(self, column: str, kind: str, bucket: str) -> str:
        self._check_bucket(kind, bucket)
        x = column
        if bucket == 'quarter':
            return f"CONCAT(date_format({x},'%Y'),'-Q',CAST(quarter({x}) AS CHAR))"
        pattern = {'day': '%Y-%m-%d', 'week': '%x-W%v', 'month': '%Y-%m', 'year': '%Y'}[bucket]
        return f"date_format({x},'{pattern}')"
