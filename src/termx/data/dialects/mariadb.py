"""MariaDB adapter (10.6 and later).

MariaDB is not MySQL with a different name. `JSON` is an alias for LONGTEXT with a
validity check, `UUID`, `INET4` and `INET6` are native types it reports by name, and
its default collations differ. It keeps its own class, engine id and version floor.
"""
from __future__ import annotations

from termx.data.dialects._mysql_family import MySQLFamilyDialect


class MariaDBDialect(MySQLFamilyDialect):
    engine = 'mariadb'
    label = 'MariaDB'
    version_floor = '10.6'
    timeout_error_code = 1969   # ER_STATEMENT_TIMEOUT
    exact_collation = 'utf8mb4_nopad_bin'   # NO PAD

    def _extra_kind(self, text: str):
        # Native UUID values are validated like PostgreSQL's; the address types render and compare as text.
        if text == 'uuid':
            return 'uuid'
        if text in ('inet4', 'inet6'):
            return 'text'
        return 'other'

    def statement_timeout_sql(self, seconds: float) -> str:
        # max_statement_time is in seconds (fractions allowed); MariaDB has no MAX_EXECUTION_TIME.
        return f'SET SESSION max_statement_time = {float(seconds):.3f}'

    def group_by_items(self, expr: str, derived: bool, column: str | None = None) -> list[str]:
        # Under ONLY_FULL_GROUP_BY MariaDB does not recognise a select item written as `col COLLATE ...` as the
        # GROUP BY expression, only the columns it contains. Grouping by the bare column as well keeps the
        # groups identical (the collated expression decides them) and satisfies that check.
        items = super().group_by_items(expr, derived, column)
        if derived and column and column != expr:
            items.insert(1, column)
        return items
