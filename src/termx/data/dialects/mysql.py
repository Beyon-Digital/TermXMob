"""MySQL adapter (8.0 and later)."""
from __future__ import annotations

from termx.data.dialects._mysql_family import MySQLFamilyDialect


class MySQLDialect(MySQLFamilyDialect):
    engine = 'mysql'
    label = 'MySQL'
    version_floor = '8.0'
    timeout_error_code = 3024   # ER_QUERY_TIMEOUT

    def statement_timeout_sql(self, seconds: float) -> str:
        # MAX_EXECUTION_TIME is in milliseconds and applies to read-only SELECT statements.
        return f'SET SESSION MAX_EXECUTION_TIME = {int(seconds * 1000)}'
