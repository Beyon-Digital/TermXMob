"""MySQL adapter (8.0 and later)."""
from __future__ import annotations

from termx.data.dialects._mysql_family import MySQLFamilyDialect


class MySQLDialect(MySQLFamilyDialect):
    engine = 'mysql'
    label = 'MySQL'
    version_floor = '8.0'
    # MySQL 8 defaults to utf8mb4_0900_ai_ci: accent- and case-insensitive.
    collation_note = 'Default collation utf8mb4_0900_ai_ci is accent- and case-insensitive.'
