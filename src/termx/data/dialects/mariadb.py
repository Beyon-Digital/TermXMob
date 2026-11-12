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
    collation_note = 'Default collations (utf8mb4_general_ci, uca1400 from 11.5) are case-insensitive.'

    def _extra_kind(self, text: str):
        # Native MariaDB types render and compare as text.
        if text in ('uuid', 'inet4', 'inet6'):
            return 'text'
        return 'other'
