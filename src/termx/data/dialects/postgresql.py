"""PostgreSQL adapter."""
from __future__ import annotations

from termx.data.dialects.base import Dialect, TypeKind, declared_words


class PostgreSQLDialect(Dialect):
    engine = 'postgresql'
    label = 'PostgreSQL'
    sqlglot_dialect = 'postgres'
    version_floor = '12'
    bucketable_kinds = frozenset({'date', 'datetime'})
    table_qualification = 'schema'
    case_folding = 'unicode'
    text_equality = 'exact'
    bool_bind = 'boolean'
    max_alias_bytes = 63        # NAMEDATALEN - 1; longer aliases are silently cut

    def table_ref(self, schema: str | None, table: str, alias: str) -> str:
        name = (self.quote(schema) + '.' if schema else '') + self.quote(table)
        return f'{name} AS {self.quote(alias)}'

    def classify_type(self, declared: str) -> TypeKind:
        text = declared_words(declared)
        if text.endswith('[]') or text.startswith('array') or text.startswith('_'):
            return 'array'
        if text == 'uuid':
            return 'uuid'
        if text in ('bool', 'boolean'):
            return 'boolean'
        if text in ('smallint', 'integer', 'int', 'bigint', 'int2', 'int4', 'int8', 'smallserial',
                    'serial', 'bigserial', 'serial2', 'serial4', 'serial8', 'oid'):
            return 'integer'
        if text in ('numeric', 'decimal', 'real', 'double precision', 'float', 'float4', 'float8', 'money'):
            return 'number'
        if text in ('text', 'varchar', 'character varying', 'char', 'character', 'bpchar', 'citext', 'name'):
            return 'text'
        if text == 'date':
            return 'date'
        if text.startswith(('timestamp', 'timestamptz')):
            return 'datetime'
        if text in ('time', 'timetz') or text.startswith('time with') or text.startswith('time without'):
            return 'time'
        if text in ('json', 'jsonb'):
            return 'json'
        if text == 'bytea':
            return 'binary'
        return 'other'

    def coerce_param(self, value: object, kind: str) -> object:
        return self._coerce_base(value, kind)

    def avg_expr(self, expr: str) -> str:
        return f'CAST(AVG({expr}) AS DOUBLE PRECISION)'

    def bool_literal(self, value: bool) -> str:
        return 'TRUE' if value else 'FALSE'

    def cast_text(self, expr: str) -> str:
        return f'CAST({expr} AS TEXT)'

    def text_match(self, column: str, kind: str, mode: str, param: str) -> str:
        return self._text_match(column, kind, mode, param)

    def in_list(self, column: str, params: list[str], negate: bool) -> str:
        return self._in_list(column, params, negate)

    def between(self, column: str, low: str, high: str, *, high_exclusive: bool = False) -> str:
        return self._between(column, low, high, high_exclusive)

    def null_ordering_impl(self):
        return 'native'

    def order_item(self, expr: str, direction: str) -> str:
        # PostgreSQL sorts NULLs first for DESC by default; state NULLS LAST for both.
        return f'{expr} {self._direction(direction)} NULLS LAST'

    def limit_offset(self, limit: int, offset: int) -> str:
        return self._limit_offset(limit, offset)

    def date_bucket(self, column: str, kind: str, bucket: str) -> str:
        self._check_bucket(kind, bucket)
        pattern = {'day': 'YYYY-MM-DD', 'week': 'IYYY-"W"IW', 'month': 'YYYY-MM',
                   'quarter': 'YYYY-"Q"Q', 'year': 'YYYY'}[bucket]
        return f"to_char({column},'{pattern}')"

    def statement_timeout_sql(self, seconds: float) -> str:
        # set_config(..., is_local=true) is SET LOCAL with a bindable value; it ends with the transaction.
        return f"SELECT set_config('statement_timeout', '{int(seconds * 1000)}', true)"

    def is_statement_timeout(self, error: BaseException) -> bool:
        return getattr(error, 'sqlstate', None) == '57014' and 'statement timeout' in str(error)
