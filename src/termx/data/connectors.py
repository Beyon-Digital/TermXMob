"""Fixed, source-read-only adapters and an honest engine capability catalog.

Parsing decides eligibility. SQLite's authorizer and each SQL server's current
read-only transaction/role checks enforce authority independently of that parser.
No adapters are loaded from source configuration or user-provided Python.
"""
from __future__ import annotations

import base64
import datetime as dt
import decimal
import importlib.util
import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

PREVIEW_ROWS = 1000
STATEMENT_TIMEOUT_DEFAULT = 30.0
STATEMENT_TIMEOUT_ENV = 'TERMX_DATA_STATEMENT_TIMEOUT_SECONDS'


def statement_timeout_seconds() -> float:
    """Server-side deadline for one source read. Configurable per host; never unbounded."""
    try:
        value = float(os.environ.get(STATEMENT_TIMEOUT_ENV, ''))
    except ValueError:
        value = STATEMENT_TIMEOUT_DEFAULT
    return min(max(value, 0.1), 600.0) if math.isfinite(value) else STATEMENT_TIMEOUT_DEFAULT


class StatementTimeout(Exception):
    """The source did not finish inside the deadline and the statement was stopped."""
    def __init__(self, seconds: float):
        self.seconds = seconds
        shown = f'{seconds:g}'
        super().__init__(f'The query ran longer than {shown} seconds and was stopped. '
                         'Add a filter, remove a combined table or lower the row limit, then run it again.')
# A deliberately small, common, side-effect-free function vocabulary. Source
# functions and extensions are not included merely because they parse as SELECT.
SAFE_FUNCTIONS = frozenset('''abs avg count sum min max total round ceil ceiling floor
coalesce nullif ifnull iif if greatest least cast try_cast convert extract
substring substr length char_length character_length lower upper trim ltrim rtrim
replace instr strpos position concat concat_ws left right reverse repeat
like glob regexp_like between in exists case if strftime date datetime time
julianday unixepoch current_date current_time current_timestamp date_trunc date_part
to_char to_date to_timestamp year month day hour minute second
row_number rank dense_rank percent_rank cume_dist ntile lag lead first_value last_value
nth_value group_concat string_agg array_agg json_agg json_group_array json_group_object
json_extract json_extract_scalar json_value json_query json_type json_valid
hex unhex quote unicode char typeof octet_length format printf
power pow sqrt sign exp ln log log10 mod stddev stddev_pop stddev_samp variance
var_pop var_samp bool_and bool_or every any_value percentile_cont percentile_disc
filter array_size array_length cardinality
date_format quarter time_to_str ts_or_ds_to_timestamp and or collate'''.split())
# `and`/`or`/`collate` are how sqlglot names operators it models as functions; `time_to_str` and `ts_or_ds_to_timestamp`
# are its canonical nodes for strftime/to_char/date_format. `xor` is deliberately absent.


def dependency_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _field(name: str, label: str, kind: str = 'text', **kw: Any) -> dict:
    return {'name': name, 'label': label, 'type': kind, **kw}


NETWORK_FIELDS = [
    _field('host', 'Host', required=True), _field('port', 'Port', 'number'),
    _field('database', 'Database', required=True), _field('username', 'Username', required=True),
    _field('tls', 'Verify TLS', 'boolean', default=True),
]
# The catalog covers families; a listed connector never implies a driver exists.
CATALOG = [
    ('sqlite', 'SQLite', 'relational', 'sql'),
    ('postgresql', 'PostgreSQL', 'relational', 'sql'),
    ('mysql', 'MySQL', 'relational', 'sql'), ('mariadb', 'MariaDB', 'relational', 'sql'),
    ('sqlserver', 'SQL Server / Azure SQL', 'relational', 'sql'),
    ('oracle', 'Oracle', 'relational', 'sql'), ('db2', 'IBM Db2', 'relational', 'sql'),
    ('duckdb', 'DuckDB', 'analytical', 'sql'), ('clickhouse', 'ClickHouse', 'analytical', 'sql'),
    ('snowflake', 'Snowflake', 'warehouse', 'sql'), ('bigquery', 'BigQuery', 'warehouse', 'sql'),
    ('redshift', 'Amazon Redshift', 'warehouse', 'sql'), ('synapse', 'Azure Synapse', 'warehouse', 'sql'),
    ('databricks', 'Databricks', 'lakehouse', 'sql'), ('trino', 'Trino / Presto', 'lakehouse', 'sql'),
    ('athena', 'Amazon Athena', 'lakehouse', 'sql'), ('spark', 'Apache Spark', 'lakehouse', 'sql'),
    ('mongodb', 'MongoDB', 'document', 'document'), ('cosmosdb', 'Azure Cosmos DB', 'document', 'document'),
    ('couchbase', 'Couchbase', 'document', 'sql++'), ('dynamodb', 'Amazon DynamoDB', 'key-value', 'partiql'),
    ('redis', 'Redis', 'key-value', 'native'), ('cassandra', 'Apache Cassandra', 'wide-column', 'cql'),
    ('elasticsearch', 'Elasticsearch', 'search', 'query-dsl'), ('opensearch', 'OpenSearch', 'search', 'query-dsl'),
    ('neo4j', 'Neo4j', 'graph', 'cypher'), ('influxdb', 'InfluxDB', 'time-series', 'sql'),
    ('csv', 'CSV / TSV', 'file', 'table'), ('parquet', 'Parquet / Arrow', 'file', 'table'),
    ('excel', 'Excel', 'file', 'table'), ('rest', 'REST API', 'api', 'http'),
    ('graphql', 'GraphQL', 'api', 'graphql'),
]


def engines() -> list[dict]:
    result = []
    for identifier, label, family, language in CATALOG:
        sqlite = identifier == 'sqlite'
        network = identifier in {'postgresql', 'mysql', 'mariadb'}
        dependencies = ('sqlglot',) if sqlite else ('sqlglot', 'sqlalchemy', 'psycopg' if identifier == 'postgresql' else 'pymysql')
        installed = (sqlite or network) and all(dependency_installed(name) for name in dependencies)
        status = 'qualified' if sqlite and installed else ('installed' if network and installed else 'unsupported')
        reason = None
        if network and installed:
            reason = 'Driver installed. Test each source to verify its read-only role and session. Real-server integration fixtures are not yet qualified.'
        elif sqlite and not installed:
            reason = 'The SQL parser dependency is not installed.'
        elif network:
            reason = 'The fixed database driver or SQL parser is not installed.'
        else:
            reason = 'No qualified read-only adapter is installed for this engine. Generic SQL/ODBC does not establish compatibility.'
        result.append({'id': identifier, 'label': label, 'family': family, 'language': language,
                       'status': status, 'ready': bool(installed), 'reason': reason,
                       'fields': [_field('path', 'Database path on host', 'file', required=True)] if sqlite else (NETWORK_FIELDS if network else []),
                       'capabilities': {'read': bool(installed), 'schema': bool(installed),
                           'cancel': sqlite and installed, 'readonly': bool(installed), 'parameters': bool(installed),
                           'export_csv': bool(installed), 'write': False, 'qualified': sqlite and installed,
                           'requires_source_test': network}})
    return result


def engine_spec(identifier: str) -> dict:
    found = next((item for item in engines() if item['id'] == identifier), None)
    if not found or not found['ready']:
        raise HTTPException(422, found['reason'] if found else 'Unknown database engine.')
    return found


def dialect(engine: str) -> str:
    return {'sqlite': 'sqlite', 'postgresql': 'postgres', 'mysql': 'mysql', 'mariadb': 'mysql'}.get(engine, '')


def quote_identifier(engine: str, identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier or '\x00' in identifier:
        raise HTTPException(422, 'A valid column name is required.')
    marker = '`' if engine in {'mysql', 'mariadb'} else '"'
    return marker + identifier.replace(marker, marker + marker) + marker


def validate_read(sql: str, engine: str, parameters: dict | None = None) -> tuple[str, dict]:
    """Conservative AST eligibility check; never replaces source enforcement."""
    import sqlglot
    from sqlglot import exp
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 1_000_000:
        raise HTTPException(422, 'Enter one read-only SQL query (at most 1 MB).')
    if parameters is not None and not isinstance(parameters, dict):
        raise HTTPException(422, 'Query parameters must be a named object.')
    params = parameters or {}
    if any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k)
           or not isinstance(v, (str, int, float, bool, type(None)))
           or (isinstance(v, float) and not math.isfinite(v)) for k, v in params.items()):
        raise HTTPException(422, 'Use named parameters with finite scalar values.')
    try:
        trees = [tree for tree in sqlglot.parse(sql, read=dialect(engine)) if tree is not None]
    except Exception:
        raise HTTPException(422, 'The SQL could not be parsed for this engine.') from None
    if len(trees) != 1 or not isinstance(trees[0], exp.Query):
        raise HTTPException(422, 'Only one SELECT query is allowed.')
    tree = trees[0]
    blocked = {'Insert', 'Update', 'Delete', 'Merge', 'Create', 'Drop', 'Alter', 'Command', 'Copy',
               'Into', 'Lock', 'Transaction', 'Commit', 'Rollback', 'Pragma', 'Attach', 'Detach',
               'Grant', 'Revoke', 'Execute', 'Set', 'Use', 'Analyze', 'Vacuum', 'LoadData',
               'Parameter', 'SessionParameter', 'UserDefinedFunction', 'Directory', 'Export', 'Operator'}
    for node in tree.walk():
        if type(node).__name__ in blocked:
            raise HTTPException(422, 'This SQL operation is not eligible for read-only execution.')
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            raise HTTPException(422, 'Table functions and external sources are not supported.')
        if isinstance(node, exp.Dot) and any(isinstance(part, exp.Func) for part in node.walk()):
            raise HTTPException(422, 'Schema-qualified source functions are not supported.')
        if engine == 'postgresql' and isinstance(node, exp.DataType) and str(node.this).split('.')[-1].upper() in {'USERDEFINED', 'OBJECT', 'UNKNOWN'}:
            raise HTTPException(422, 'Custom source types and casts are not qualified for read-only execution.')
        if isinstance(node, exp.Func):
            name = node.name.lower() if isinstance(node, exp.Anonymous) else node.sql_name().lower()
            if name not in SAFE_FUNCTIONS:
                raise HTTPException(422, 'This function is not in the read-only function allowlist.')
    # Strip the parsed statement terminator, not any semicolon inside a literal.
    return sql.rstrip().removesuffix(';').rstrip(), dict(params)


def scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'encoding': 'base64', 'value': base64.b64encode(value).decode('ascii')}
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    return str(value)


_DB_DATETIME = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$')
RESULT_KINDS = frozenset({'text', 'integer', 'number', 'boolean', 'date', 'datetime', 'time', 'json', 'binary', 'uuid', 'array', 'other'})


def typed_scalar(value: Any, kind: str | None) -> Any:
    """`scalar` plus one JSON type per column kind, whichever engine produced the value.

    PostgreSQL, MySQL and MariaDB return DECIMAL (SUM of integers, AVG, NUMERIC columns) as Decimal and
    MySQL returns BOOLEAN as 0 or 1; SQLite returns plain numbers and 0/1 as well but spells a datetime with
    a space. A table cell must not change JSON type from one engine to the next.
    """
    if value is None:
        return None
    if kind == 'integer' and isinstance(value, decimal.Decimal) and value.is_finite():
        return int(value) if value == value.to_integral_value() else float(value)
    if kind == 'number' and isinstance(value, decimal.Decimal):
        number = float(value)
        return number if math.isfinite(number) else str(value)
    if kind == 'boolean' and isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if kind == 'datetime' and isinstance(value, str) and _DB_DATETIME.match(value):
        return value.replace(' ', 'T', 1)
    return scalar(value)


def rows_result(columns: list[str], rows: list, max_rows: int, types: list[str] | None = None) -> dict:
    truncated = len(rows) > max_rows
    return {'columns': [{'name': name, 'type': (types[i] if types else 'unknown')} for i, name in enumerate(columns)],
            'rows': [[scalar(cell) for cell in row] for row in rows[:max_rows]],
            'row_count': min(len(rows), max_rows), 'truncated': truncated}


MAX_CELL_BYTES = 64 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024


def collect_result(columns: list[str], cursor, max_rows: int, kinds: list[str] | None = None) -> dict:
    """Bound display payloads while consuming one source row at a time."""
    output = {'columns': [{'name': name, 'type': 'unknown'} for name in columns],
              'rows': [], 'row_count': 0, 'truncated': False}
    kinds = kinds if kinds and len(kinds) == len(columns) else [None] * len(columns)
    used = len(json.dumps(output, ensure_ascii=False).encode('utf-8'))
    clipped = False
    while True:
        row = cursor.fetchone()
        if row is None:
            break
        if len(output['rows']) >= max_rows:
            output['truncated'] = True
            break
        values = []
        for cell, kind in zip(row, kinds):
            value = typed_scalar(cell, kind)
            if isinstance(value, str) and len(value.encode('utf-8')) > MAX_CELL_BYTES:
                value = value.encode('utf-8')[:MAX_CELL_BYTES].decode('utf-8', errors='ignore') + '… [preview truncated]'
                clipped = True
            elif isinstance(value, dict) and value.get('encoding') == 'base64' and len(value['value']) > MAX_CELL_BYTES:
                value = {**value, 'value': value['value'][:MAX_CELL_BYTES], 'truncated': True}
                clipped = True
            values.append(value)
        size = len(json.dumps(values, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        if used + size > MAX_RESULT_BYTES:
            output['truncated'] = True
            output['notice'] = 'The preview reached its 4 MB display size. The source query was not treated as a complete result.'
            break
        output['rows'].append(values)
        used += size
    output['row_count'] = len(output['rows'])
    if clipped:
        output['truncated'] = True
        output['notice'] = 'Large cells were shortened to 64 KB for display. This preview and its CSV export contain shortened cells.'
    return output


class SQLiteConnector:
    """Only an explicitly registered file, opened mode=ro with a VM authorizer."""
    def __init__(self, path: Path, cancel: threading.Event | None = None,
                 active: Callable[[Any], None] | None = None):
        self.path, self.cancel, self.active = path, cancel or threading.Event(), active

    @contextmanager
    def connection(self, *, schema: bool = False):
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=0)
        try:
            if hasattr(db, 'enable_load_extension'):
                db.enable_load_extension(False)
            if hasattr(db, 'setconfig'):
                db.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
                db.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False)
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            if db.execute('PRAGMA query_only').fetchone()[0] != 1:
                raise HTTPException(409, 'SQLite did not enter read-only query mode.')
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
            db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1_000_000)
            db.set_authorizer(self._authorizer(schema))
            timeout = statement_timeout_seconds()
            deadline = time.monotonic() + timeout
            expired = []

            def stop() -> int:
                if self.cancel.is_set():
                    return 1
                if time.monotonic() > deadline:
                    expired.append(True)
                    return 1
                return 0
            db.set_progress_handler(stop, 1000)
            if self.active:
                self.active(db)
            if self.cancel.is_set():
                raise InterruptedError('Query cancelled.')
            try:
                yield db
            except sqlite3.OperationalError:
                if expired and not self.cancel.is_set():
                    raise StatementTimeout(timeout) from None
                raise
        finally:
            if self.active:
                self.active(None)
            db.close()

    @staticmethod
    def _authorizer(schema: bool):
        def authorize(action, arg1, arg2, database, trigger):
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and database in {'main', None}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).lower() in SAFE_FUNCTIONS:
                return sqlite3.SQLITE_OK
            if schema and action == sqlite3.SQLITE_PRAGMA and arg1 in {'table_xinfo', 'foreign_key_list'}:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return authorize

    def test(self) -> dict:
        with self.connection() as db:
            db.execute('SELECT 1').fetchone()
        return {'ok': True, 'engine': 'sqlite', 'message': 'SQLite read-only file and authorizer verified.',
                'read_available': True, 'protection': 'mode=ro, query_only, defensive mode, authorizer, extensions disabled'}

    def schema(self) -> dict:
        tables = []
        with self.connection(schema=True) as db:
            names = db.execute("SELECT name,type FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            for name, kind in names:
                # Identifier quoting is fixed; no SQL string supplied by a caller.
                try:
                    columns = db.execute('PRAGMA table_xinfo(' + quote_identifier('sqlite', name) + ')').fetchall()
                except sqlite3.DatabaseError:
                    if kind != 'view':
                        raise
                    tables.append({'name': name, 'schema': 'main', 'kind': kind, 'columns': [], 'readable': False,
                                   'reason': 'This source view cannot be inspected under the read-only policy.',
                                   'foreign_keys': [], 'row_estimate': None})
                    continue
                tables.append({'name': name, 'schema': 'main', 'kind': kind, 'row_estimate': None,
                               'columns': [{'name': c[1], 'type': c[2] or 'unknown', 'nullable': not bool(c[3]), 'primary_key': bool(c[5])} for c in columns if c[6] != 1],
                               'foreign_keys': self._foreign_keys(db, name)})
        self._resolve_implicit_keys(tables)
        return {'tables': tables, 'engine': 'sqlite'}

    @staticmethod
    def _foreign_keys(db, table: str) -> list[dict]:
        grouped: dict[int, list] = {}
        for row in db.execute('PRAGMA foreign_key_list(' + quote_identifier('sqlite', table) + ')').fetchall():
            grouped.setdefault(row[0], []).append(row)   # id, seq, table, from, to, ...
        return [{'columns': [r[3] for r in sorted(rows, key=lambda r: r[1])], 'referred_table': rows[0][2],
                 'referred_columns': [r[4] for r in sorted(rows, key=lambda r: r[1])]}
                for _, rows in sorted(grouped.items())]

    @staticmethod
    def _resolve_implicit_keys(tables: list[dict]) -> None:
        # `REFERENCES parent` without columns means the parent's primary key.
        by_name = {t['name']: t for t in tables}
        for table in tables:
            for key in table['foreign_keys']:
                if any(column is None for column in key['referred_columns']):
                    parent = by_name.get(key['referred_table'])
                    primary = [c['name'] for c in (parent or {}).get('columns', []) if c['primary_key']]
                    key['referred_columns'] = primary if len(primary) == len(key['columns']) else []

    def execute(self, sql: str, parameters: dict, max_rows: int, kinds: list[str] | None = None) -> dict:
        with self.connection() as db:
            cursor = db.execute(sql, parameters)
            if not cursor.description:
                raise HTTPException(422, 'The query did not return a result set.')
            return collect_result([column[0] for column in cursor.description], cursor, max_rows, kinds)


class NetworkSQLConnector:
    """Fixed SQLAlchemy dialects, explicit TLS, strict live privilege proofs.

    Network integration is shipped as an installed adapter, not a qualified
    server/version claim. Each Test and execution repeats read-only role checks.
    Views and executable source routines are intentionally excluded.
    """
    def __init__(self, engine: str, config: dict, password: str | None,
                 cancel: threading.Event | None = None, active: Callable[[Any], None] | None = None):
        self.engine, self.config, self.password = engine, config, password
        self.cancel, self.active = cancel or threading.Event(), active

    @contextmanager
    def connection(self):
        if not self.password:
            raise HTTPException(409, 'An explicitly stored source password is required; ambient host credentials are not used.')
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import URL
        driver = 'postgresql+psycopg' if self.engine == 'postgresql' else 'mysql+pymysql'
        cfg = self.config
        query = {'sslmode': 'verify-full', 'sslrootcert': 'system', 'sslcertmode': 'disable',
                 'passfile': os.devnull, 'gssencmode': 'disable',
                 'options': '-c default_transaction_read_only=on -c search_path=pg_catalog,public'} if self.engine == 'postgresql' else {}
        connect_args = {'connect_timeout': 10}
        if self.engine != 'postgresql':
            connect_args.update({'local_infile': False, 'ssl': {'check_hostname': True},
                                 'ssl_verify_cert': True, 'ssl_verify_identity': True})
        source = create_engine(URL.create(driver, username=cfg['username'], password=self.password,
            host=cfg['host'], port=cfg['port'], database=cfg['database'], query=query),
            connect_args=connect_args, hide_parameters=True, echo=False, pool_pre_ping=False)
        try:
            with source.connect() as conn:
                if self.engine == 'postgresql':
                    conn = conn.execution_options(isolation_level='REPEATABLE READ', postgresql_readonly=True)
                else:
                    conn.exec_driver_sql('SET SESSION TRANSACTION READ ONLY')
                    conn.commit()
                with conn.begin():
                    self._prove_readonly(conn)
                    if self.active:
                        # Network cancellation has no qualified acknowledgement;
                        # runtime waits for source return and discards the result.
                        self.active(None)
                    if self.cancel.is_set():
                        raise InterruptedError('Query cancelled.')
                    timeout = self._apply_deadline(conn)
                    try:
                        yield conn
                    except Exception as error:
                        if self._timed_out(error):
                            raise StatementTimeout(timeout) from None
                        raise
                # Read-only transactions never commit source changes.
        finally:
            source.dispose()

    def _apply_deadline(self, conn) -> float:
        """Ask the server to stop any statement of this session at the deadline."""
        from termx.data.dialects import get_dialect
        seconds = statement_timeout_seconds()
        statement = get_dialect(self.engine).statement_timeout_sql(seconds)
        if statement:
            conn.exec_driver_sql(statement)
        return seconds

    def _timed_out(self, error: Exception) -> bool:
        from termx.data.dialects import get_dialect
        driver_error = getattr(error, 'orig', None) or error
        return get_dialect(self.engine).is_statement_timeout(driver_error)

    def _prove_readonly(self, conn):
        from sqlalchemy import text
        if self.engine == 'postgresql':
            if conn.exec_driver_sql('SHOW transaction_read_only').scalar() != 'on':
                raise HTTPException(409, 'The PostgreSQL session is not read-only.')
            role = conn.exec_driver_sql('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user').one()
            if any(role):
                raise HTTPException(409, 'Use a dedicated PostgreSQL read-only role without elevated privileges.')
            unsafe = conn.exec_driver_sql("""SELECT
              EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','p','v','m','f')
                AND pg_catalog.has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')) OR
              EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                AND pg_catalog.has_schema_privilege(n.oid,'CREATE')) OR
              pg_catalog.has_database_privilege(current_database(),'TEMPORARY') OR
              EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND pg_catalog.has_function_privilege(p.oid,'EXECUTE')) OR
              EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname IN ('pg_read_server_files','pg_write_server_files','pg_execute_server_program')
                AND pg_catalog.pg_has_role(r.oid,'MEMBER'))""").scalar()
            if unsafe:
                raise HTTPException(409, 'This PostgreSQL role retains writes, temporary/schema creation, executable source functions, or server-file authority. Use a dedicated restricted read-only role.')
            conn.exec_driver_sql('SET LOCAL search_path TO pg_catalog, public')
        else:
            variable = '@@tx_read_only' if self.engine == 'mariadb' else '@@transaction_read_only'
            if not conn.exec_driver_sql('SELECT ' + variable).scalar():
                raise HTTPException(409, 'The MySQL/MariaDB session is not read-only.')
            grants = [str(row[0]) for row in conn.exec_driver_sql('SHOW GRANTS FOR CURRENT_USER()')]
            for grant in grants:
                match = re.match(r'^GRANT (.+?) ON ', grant, re.I)
                privileges = {part.strip().upper() for part in match.group(1).split(',')} if match else set()
                if not privileges or not privileges <= {'SELECT', 'SHOW VIEW', 'USAGE'}:
                    raise HTTPException(409, 'Use a dedicated MySQL/MariaDB account with only SELECT/SHOW VIEW. Role grants, routines, file and administration privileges cannot be qualified here.')
            if not grants:
                raise HTTPException(409, 'The source role grants could not be verified.')

    def test(self) -> dict:
        with self.connection() as conn:
            conn.exec_driver_sql('SELECT 1').one()
        return {'ok': True, 'engine': self.engine, 'message': 'Current source role and read-only transaction verified. Server integration qualification remains pending.',
                'read_available': True, 'protection': 'Read-only transaction; live privilege proof; SQL/function allowlist; base tables only', 'qualified': False}

    def schema(self) -> dict:
        with self.connection() as conn:
            return self.schema_from(conn)

    def schema_from(self, conn) -> dict:
        """Introspect through an already verified read-only connection."""
        from sqlalchemy import inspect, text
        metadata = inspect(conn)
        schema = self.config.get('schema') or ('public' if self.engine == 'postgresql' else self.config['database'])
        estimates = self._row_estimates(conn, schema, text)
        names = metadata.get_table_names(schema=schema)
        if self.engine == 'postgresql':
            tables = self._postgresql_tables(conn, metadata, schema, names)
        else:
            tables = self._mysql_family_tables(conn, schema, names, text)
        for table in tables:
            table['row_estimate'] = estimates.get(table['name'])
        return {'tables': tables, 'engine': self.engine, 'limitations': 'Base tables only; source views and routines are excluded.'}

    @staticmethod
    def _postgresql_tables(conn, metadata, schema: str, names: list[str]) -> list[dict]:
        tables = []
        for name in names:
            primary = set(metadata.get_pk_constraint(name, schema=schema).get('constrained_columns') or [])
            keys = [{'columns': list(k['constrained_columns']), 'referred_table': k['referred_table'],
                     'referred_columns': list(k['referred_columns'])}
                    for k in metadata.get_foreign_keys(name, schema=schema)
                    if k.get('referred_schema') in (None, schema) and k.get('referred_table')]
            tables.append({'name': name, 'schema': schema, 'kind': 'table', 'row_estimate': None, 'columns': [
                {'name': c['name'], 'type': declared_type(c['type'], conn.dialect), 'nullable': bool(c.get('nullable', True)),
                 'primary_key': c['name'] in primary} for c in metadata.get_columns(name, schema=schema)],
                'foreign_keys': keys})
        return tables

    @staticmethod
    def _mysql_family_tables(conn, schema: str, names: list[str], text) -> list[dict]:
        """MySQL and MariaDB names come from information_schema. SQLAlchemy's reflection parses
        SHOW CREATE TABLE and returns a backtick in a name doubled, so the real name would not be found."""
        columns: dict[str, list[dict]] = {name: [] for name in names}
        for table, column, declared, nullable, key, charset in conn.execute(text(
                'SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, CHARACTER_SET_NAME '
                'FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=:schema ORDER BY TABLE_NAME, ORDINAL_POSITION'),
                {'schema': schema}).all():
            if table in columns:
                columns[table].append({'name': column, 'type': str(declared), 'nullable': str(nullable).upper() == 'YES',
                                       'primary_key': str(key).upper() == 'PRI', 'charset': charset})
        grouped: dict[tuple[str, str], list[tuple]] = {}
        for constraint, table, column, position, ref_schema, ref_table, ref_column in conn.execute(text(
                'SELECT CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, REFERENCED_TABLE_SCHEMA, '
                'REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE '
                'WHERE TABLE_SCHEMA=:schema AND REFERENCED_TABLE_NAME IS NOT NULL '
                'ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION'), {'schema': schema}).all():
            if table in columns and ref_schema == schema:
                grouped.setdefault((table, constraint), []).append((column, ref_table, ref_column))
        keys: dict[str, list[dict]] = {name: [] for name in names}
        for (table, _), rows in grouped.items():
            keys[table].append({'columns': [r[0] for r in rows], 'referred_table': rows[0][1],
                                'referred_columns': [r[2] for r in rows]})
        return [{'name': name, 'schema': schema, 'kind': 'table', 'row_estimate': None,
                 'columns': columns[name], 'foreign_keys': keys[name]} for name in names]

    def _row_estimates(self, conn, schema: str, text) -> dict:
        """Planner statistics, never a table scan. Negative or missing values become None."""
        if self.engine == 'postgresql':
            rows = conn.execute(text(
                "SELECT c.relname, c.reltuples FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=:schema AND c.relkind IN ('r','p')"), {'schema': schema}).all()
        else:
            rows = conn.execute(text(
                'SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=:schema'), {'schema': schema}).all()
        return {str(name): int(count) for name, count in rows if count is not None and float(count) >= 0}

    def execute(self, sql: str, parameters: dict, max_rows: int, kinds: list[str] | None = None) -> dict:
        import sqlglot
        from sqlglot import exp
        from sqlalchemy import inspect, text
        tree = sqlglot.parse_one(sql, read=dialect(self.engine))
        aliases = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        with self.connection() as conn:
            inspector = inspect(conn)
            # Keep only base-table source reads. A view can call a definer routine
            # that AST inspection of the submitted statement cannot see.
            for table in tree.find_all(exp.Table):
                if not table.db and table.name in aliases:
                    continue
                schema = table.db or ('public' if self.engine == 'postgresql' else self.config['database'])
                if self.engine in {'mysql', 'mariadb'} and schema != self.config['database']:
                    raise HTTPException(422, 'This connection only grants reads inside its explicitly enrolled database.')
                if table.catalog or table.name not in inspector.get_table_names(schema=schema):
                    raise HTTPException(422, 'Network reads currently support registered database base tables only; views and external sources are excluded.')
                if self.engine == 'postgresql':
                    safe_table = conn.execute(text("""SELECT c.relkind IN ('r','p') AND NOT EXISTS(
                        SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
                        JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
                        WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND tn.nspname<>'pg_catalog')
                        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname=:schema AND c.relname=:name"""), {'schema': schema, 'name': table.name}).scalar()
                    if not safe_table:
                        raise HTTPException(422, 'PostgreSQL reads currently require base tables with built-in column types; external tables and custom type routines are excluded.')
                else:
                    storage = conn.execute(text('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=:schema AND TABLE_NAME=:name'),
                                           {'schema': schema, 'name': table.name}).scalar()
                    if str(storage).upper() not in {'INNODB', 'MYISAM', 'ARIA', 'MEMORY', 'CSV', 'ARCHIVE', 'MRG_MYISAM'}:
                        raise HTTPException(422, 'External or unqualified table storage engines are not supported by this read-only adapter.')
            result = conn.execution_options(stream_results=True, max_row_buffer=100).execute(text(sql), parameters)
            try:
                if not result.returns_rows:
                    raise HTTPException(422, 'The query did not return rows.')
                output = collect_result(list(result.keys()), result, max_rows, kinds)
                if self.cancel.is_set():
                    raise InterruptedError('Query cancelled after the source returned; source cancellation is unqualified.')
                return output
            finally:
                result.close()


def declared_type(sql_type, dialect) -> str:
    """The type as the server declares it, e.g. MySQL `TINYINT(1)`, which `str()` renders without its width."""
    try:
        return sql_type.compile(dialect=dialect)
    except Exception:
        return str(sql_type)


def validate_read_sql(sql: str, engine: str = 'sqlite') -> str:
    """Eligibility-only convenience for server-side saved-query proposals."""
    return validate_read(sql, engine)[0]
