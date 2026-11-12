"""Fixed registry of query-builder dialects. Nothing is loaded from configuration."""
from __future__ import annotations

from fastapi import HTTPException

from termx.data.dialects.base import Dialect
from termx.data.dialects.mariadb import MariaDBDialect
from termx.data.dialects.mysql import MySQLDialect
from termx.data.dialects.postgresql import PostgreSQLDialect
from termx.data.dialects.sqlite import SQLiteDialect

DIALECTS: dict[str, type[Dialect]] = {
    cls.engine: cls for cls in (SQLiteDialect, PostgreSQLDialect, MySQLDialect, MariaDBDialect)}


def unsupported_message(engine: str) -> str:
    from termx.data.connectors import CATALOG
    label = next((item[1] for item in CATALOG if item[0] == engine), engine if isinstance(engine, str) else 'These')
    return ('The visual query builder supports SQLite, PostgreSQL, MySQL and MariaDB sources. '
            f'{label} sources can still use Advanced SQL.')


def dialect_or_none(engine: str) -> Dialect | None:
    cls = DIALECTS.get(engine) if isinstance(engine, str) else None
    return cls() if cls else None


def get_dialect(engine: str) -> Dialect:
    found = dialect_or_none(engine)
    if found is None:
        raise HTTPException(422, unsupported_message(engine))
    return found


def unsupported_capabilities(engine: str) -> dict:
    from termx.data.connectors import CATALOG
    label = next((item[1] for item in CATALOG if item[0] == engine), engine)
    return {'engine': engine, 'label': label, 'supported': False, 'reason': unsupported_message(engine),
            'qualified': False, 'requires_source_test': False, 'dialect': None}
