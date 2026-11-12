"""Per-dialect unit tests: golden SQL, type classification, value coercion, the capability
matrix and the fail-closed registry. The compiled-corpus conformance suite (including the
env-gated live PostgreSQL, MySQL and MariaDB runs) lives in test_data_conformance.py."""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest
from fastapi import HTTPException

from termx.data.connectors import engines
from termx.data.dialects import DIALECTS, dialect_or_none, get_dialect, unsupported_capabilities
from termx.data.dialects import sqlite as sqlite_module
from termx.data.dialects.base import Dialect
from termx.data.dialects.mariadb import MariaDBDialect
from termx.data.dialects.mysql import MySQLDialect
from termx.data.dialects.postgresql import PostgreSQLDialect
from termx.data.dialects.sqlite import SQLiteDialect

ALL = ['sqlite', 'postgresql', 'mysql', 'mariadb']
MESSAGE = ('The visual query builder supports SQLite, PostgreSQL, MySQL and MariaDB sources. '
           '{label} sources can still use Advanced SQL.')


@pytest.fixture(params=ALL)
def dialect(request) -> Dialect:
    return get_dialect(request.param)


# --- registry --------------------------------------------------------------------------
def test_registry_is_fixed_and_one_concrete_class_per_engine():
    assert set(DIALECTS) == set(ALL)
    assert [type(get_dialect(e)) for e in ALL] == [SQLiteDialect, PostgreSQLDialect, MySQLDialect, MariaDBDialect]
    assert MySQLDialect is not MariaDBDialect and not issubclass(MariaDBDialect, MySQLDialect)
    assert not issubclass(MySQLDialect, MariaDBDialect)
    assert [(d.engine, d.sqlglot_dialect) for d in map(get_dialect, ALL)] == [
        ('sqlite', 'sqlite'), ('postgresql', 'postgres'), ('mysql', 'mysql'), ('mariadb', 'mysql')]
    with pytest.raises(TypeError):
        Dialect()  # abstract


@pytest.mark.parametrize('engine,label', [('sqlserver', 'SQL Server / Azure SQL'), ('oracle', 'Oracle'),
                                           ('duckdb', 'DuckDB'), ('mongodb', 'MongoDB'), ('nonsense', 'nonsense')])
def test_unknown_engine_fails_closed_with_the_exact_message(engine, label):
    with pytest.raises(HTTPException) as raised:
        get_dialect(engine)
    assert raised.value.status_code == 422 and raised.value.detail == MESSAGE.format(label=label)
    assert dialect_or_none(engine) is None
    capabilities = unsupported_capabilities(engine)
    assert capabilities['supported'] is False and capabilities['dialect'] is None
    assert capabilities['reason'] == MESSAGE.format(label=label)
    assert capabilities['qualified'] is False and capabilities['requires_source_test'] is False


def test_every_ready_relational_engine_has_a_dialect():
    for item in engines():
        if item['ready'] and item['family'] == 'relational':
            assert dialect_or_none(item['id']) is not None, item['id']


# --- quoting, params, like -------------------------------------------------------------
@pytest.mark.parametrize('engine,marker', [('sqlite', '"'), ('postgresql', '"'), ('mysql', '`'), ('mariadb', '`')])
def test_quote_doubles_the_quote_character(engine, marker):
    d = get_dialect(engine)
    assert d.quote('orders') == marker + 'orders' + marker
    assert d.quote(f'a{marker}b') == f'{marker}a{marker}{marker}b{marker}'
    other = '`' if marker == '"' else '"'
    assert d.quote(f'a{other}b') == f'{marker}a{other}b{marker}'


@pytest.mark.parametrize('bad', ['', 'a:b', 'a\x00b', 'a\nb', 'a\tb', 'a\x7fb'])
def test_quote_refuses_names_no_engine_path_carries_safely(dialect, bad):
    with pytest.raises(ValueError):
        dialect.quote(bad)


def test_param_is_named_colon_everywhere(dialect):
    assert dialect.param('p1') == ':p1'
    for bad in ('x', 'p1; DROP', 'P1', 'p', ''):
        with pytest.raises(ValueError):
            dialect.param(bad)


def test_escape_like_and_pattern(dialect):
    assert dialect.escape_like('100%_done!') == '100!%!_done!!'
    assert dialect.like_pattern('a_b', 'contains') == '%a!_b%'
    assert dialect.like_pattern('a%', 'starts_with') == 'a!%%'
    with pytest.raises(ValueError):
        dialect.like_pattern('a', 'ends_with')


@pytest.mark.parametrize('engine,cast', [('sqlite', 'CAST(X AS TEXT)'), ('postgresql', 'CAST(X AS TEXT)'),
                                          ('mysql', 'CAST(X AS CHAR)'), ('mariadb', 'CAST(X AS CHAR)')])
def test_text_match_casts_only_non_text_columns(engine, cast):
    d = get_dialect(engine)
    assert d.cast_text('X') == cast
    assert d.text_match('X', 'text', 'contains', ':p1') == "LOWER(X) LIKE LOWER(:p1) ESCAPE '!'"
    assert d.text_match('X', 'integer', 'starts_with', ':p1') == f"LOWER({cast}) LIKE LOWER(:p1) ESCAPE '!'"
    with pytest.raises(ValueError):
        d.text_match('X', 'text', 'regex', ':p1')


def test_in_list_between_limit_offset_and_aggregates(dialect):
    assert dialect.in_list('X', [':p1', ':p2'], False) == 'X IN (:p1, :p2)'
    assert dialect.in_list('X', [':p1'], True) == '(X NOT IN (:p1) OR X IS NULL)'
    for params in ([], [f':p{i}' for i in range(201)]):
        with pytest.raises(ValueError):
            dialect.in_list('X', params, False)
    assert dialect.between('X', ':a', ':b', high_exclusive=False) == 'X BETWEEN :a AND :b'
    assert dialect.between('X', ':a', ':b', high_exclusive=True) == '(X >= :a AND X < :b)'
    assert dialect.limit_offset(201, 400) == 'LIMIT 201 OFFSET 400'
    for limit, offset in ((0, 0), (-1, 0), (1, -1), (True, 0), ('1', 0), (1.5, 0), (1, '0; DROP')):
        with pytest.raises(ValueError):
            dialect.limit_offset(limit, offset)
    assert dialect.count_distinct('X') == 'COUNT(DISTINCT X)'
    assert dialect.conditional_aggregate('sum', 'X', 'P') == 'SUM(CASE WHEN P THEN X END)'
    assert dialect.conditional_aggregate('count', None, 'P') == 'COUNT(CASE WHEN P THEN 1 END)'
    assert dialect.conditional_aggregate('count_distinct', 'X', 'P') == 'COUNT(DISTINCT CASE WHEN P THEN X END)'
    assert 'FILTER' not in dialect.conditional_aggregate('avg', 'X', 'P')
    with pytest.raises(ValueError):
        dialect.conditional_aggregate('median', 'X', 'P')


def test_group_by_items_only_differ_for_derived_expressions_on_mysql_family():
    for engine in ('sqlite', 'postgresql'):
        assert get_dialect(engine).group_by_items('X', True) == ['X']
    for engine in ('mysql', 'mariadb'):
        assert get_dialect(engine).group_by_items('X', False) == ['X']
        assert get_dialect(engine).group_by_items('f(X)', True) == ['f(X)', '(f(X) IS NULL)']


def test_bool_literals_and_order_item(dialect):
    expected = ('1', '0') if dialect.engine == 'sqlite' else ('TRUE', 'FALSE')
    assert (dialect.bool_literal(True), dialect.bool_literal(False)) == expected
    with pytest.raises(ValueError):
        dialect.order_item('X', 'sideways')


def test_null_ordering_native_vs_emulated(monkeypatch):
    assert get_dialect('postgresql').order_item('X', 'desc') == 'X DESC NULLS LAST'
    for engine in ('mysql', 'mariadb'):
        d = get_dialect(engine)
        assert d.order_item('X', 'asc') == '(X IS NULL), X ASC'
        assert d.order_item('X', 'desc') == '(X IS NULL), X DESC'
        assert d.null_ordering_impl() == 'emulated'
    d = get_dialect('sqlite')
    monkeypatch.setattr(sqlite_module, 'sqlite_version', lambda: (3, 30, 0))
    assert d.order_item('X', 'asc') == 'X ASC NULLS LAST' and d.null_ordering_impl() == 'native'
    monkeypatch.setattr(sqlite_module, 'sqlite_version', lambda: (3, 29, 0))
    assert d.order_item('X', 'desc') == '(X IS NULL), X DESC' and d.null_ordering_impl() == 'emulated'
    assert d.capabilities()['dialect']['null_ordering_impl'] == 'emulated'


# --- type classification ---------------------------------------------------------------
CLASSIFY = {
    'sqlite': {'INTEGER': 'integer', 'bigint': 'integer', 'TEXT': 'text', 'VARCHAR(20)': 'text', 'REAL': 'number',
               'NUMERIC(10, 2)': 'number', 'DATE': 'date', 'DATETIME': 'datetime', 'TIMESTAMP': 'datetime',
               'BOOLEAN': 'boolean', 'BLOB': 'binary', 'JSON': 'json', 'tinyint(1)': 'integer',
               '': 'other', 'unknown': 'other', 'GEOMETRY': 'other'},
    'postgresql': {'INTEGER': 'integer', 'BIGINT': 'integer', 'int4': 'integer', 'VARCHAR(20)': 'text', 'TEXT': 'text',
                   'NUMERIC(10, 2)': 'number', 'DOUBLE PRECISION': 'number', 'BOOLEAN': 'boolean', 'DATE': 'date',
                   'TIMESTAMP WITHOUT TIME ZONE': 'datetime', 'TIMESTAMP WITH TIME ZONE': 'datetime',
                   'timestamptz': 'datetime', 'TIME WITHOUT TIME ZONE': 'time', 'JSONB': 'json', 'JSON': 'json',
                   'BYTEA': 'binary', 'UUID': 'uuid', 'uuid': 'uuid', 'INTEGER[]': 'array', 'text[]': 'array', '_int4': 'array', 'tinyint(1)': 'other'},
    'mysql': {'TINYINT(1)': 'boolean', 'tinyint(1)': 'boolean', 'TINYINT': 'integer', 'INTEGER': 'integer',
              'BIGINT(20)': 'integer', 'INT UNSIGNED': 'integer', 'VARCHAR(255)': 'text', 'LONGTEXT': 'text',
              "ENUM('a','b')": 'text', 'DATETIME': 'datetime', 'TIMESTAMP': 'datetime', 'DATE': 'date', 'TIME': 'time',
              'DECIMAL(10, 2)': 'number', 'DOUBLE': 'number', 'JSON': 'json', 'BLOB': 'binary', 'VARBINARY(16)': 'binary',
              'POINT': 'other', 'UUID': 'other'},
    'mariadb': {'TINYINT(1)': 'boolean', 'TINYINT': 'integer', 'VARCHAR(255)': 'text', 'DATETIME': 'datetime',
                'DECIMAL(10, 2)': 'number', 'LONGTEXT': 'text', 'UUID': 'uuid', 'INET6': 'text', 'POINT': 'other'},
}


@pytest.mark.parametrize('engine', ALL)
def test_classify_type(engine):
    d = get_dialect(engine)
    for declared, kind in CLASSIFY[engine].items():
        assert d.classify_type(declared) == kind, (engine, declared)


def test_tinyint_one_is_boolean_on_mysql_family_only():
    assert [get_dialect(e).classify_type('tinyint(1)') for e in ALL] == ['integer', 'other', 'boolean', 'boolean']


# --- value coercion --------------------------------------------------------------------
def test_coerce_param_bool_binding_differs_by_engine():
    assert get_dialect('sqlite').coerce_param(True, 'boolean') == 1
    assert get_dialect('sqlite').coerce_param('no', 'boolean') == 0
    for engine in ('postgresql', 'mysql', 'mariadb'):
        assert get_dialect(engine).coerce_param(True, 'boolean') is True
        assert get_dialect(engine).coerce_param('0', 'boolean') is False
    capabilities = {e: get_dialect(e).capabilities()['dialect']['boolean']['bind'] for e in ALL}
    assert capabilities == {'sqlite': 'integer', 'postgresql': 'boolean', 'mysql': 'boolean', 'mariadb': 'boolean'}


def test_coerce_param_numbers_dates_and_rejections(dialect):
    assert dialect.coerce_param('42', 'integer') == 42 and dialect.coerce_param(7.0, 'integer') == 7
    assert dialect.coerce_param('12.50', 'number') == 12.5 and dialect.coerce_param(3, 'number') == 3
    assert dialect.coerce_param('2026-11-12', 'date') == '2026-11-12'
    assert dialect.coerce_param('2026-11-12', 'datetime') == '2026-11-12'
    assert dialect.coerce_param('2026-11-12T14:30', 'datetime') == '2026-11-12 14:30:00'
    assert dialect.coerce_param('2026-11-12 14:30:01.25', 'datetime') == '2026-11-12 14:30:01.25'
    assert dialect.coerce_param('09:05', 'time') == '09:05:00'
    assert dialect.coerce_param(5, 'text') == '5' and dialect.coerce_param('x', 'other') == 'x'
    for value, kind in [('abc', 'integer'), (1.5, 'integer'), (True, 'integer'), (2 ** 70, 'integer'),
                        ('x', 'number'), (float('inf'), 'number'), (float('nan'), 'number'), (True, 'number'),
                        ('maybe', 'boolean'), (2, 'boolean'),
                        ('2026-13-01', 'date'), ('2026-02-30', 'date'), ('11/12/2026', 'date'), ('2026-11-12 10:00', 'date'),
                        ('2026-02-30', 'datetime'), ('2026-11-12T25:00', 'datetime'), ('2026-11-12T10:00Z', 'datetime'),
                        ('2026-11-12T10:00+02:00', 'datetime'), (5, 'datetime'), ('25:00', 'time'),
                        ('a\x00b', 'text'), (True, 'text'), ({'a': 1}, 'other'), ([1], 'text')]:
        with pytest.raises(ValueError):
            dialect.coerce_param(value, kind)


def test_iso_dates_roundtrip_through_python_dates(dialect):
    for day in ('2024-02-29', '2027-01-01'):
        assert dt.date.fromisoformat(dialect.coerce_param(day, 'date')).isoformat() == day


# --- date buckets: golden SQL ----------------------------------------------------------
# SQLite reads only text that starts with a real ISO date, bucketing the date part as written.
ISO_GUARD = "CASE WHEN X GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' AND date(substr(X,1,10)) = substr(X,1,10) THEN "
GOLDEN = {
    'sqlite': {bucket: ISO_GUARD + inner + ' END' for bucket, inner in {
        'day': "strftime('%Y-%m-%d', @@)",
        'week': "strftime('%Y', date(@@,'-3 days','weekday 4')) || printf('-W%02d', (CAST(strftime('%j', date(@@,'-3 days','weekday 4')) AS INTEGER) - 1) / 7 + 1)",
        'month': "strftime('%Y-%m', @@)",
        'quarter': "strftime('%Y', @@) || '-Q' || ((CAST(strftime('%m', @@) AS INTEGER) + 2) / 3)",
        'year': "strftime('%Y', @@)"}.items()},
    'postgresql': {
        'day': "to_char(X,'YYYY-MM-DD')", 'week': "to_char(X,'IYYY-\"W\"IW')", 'month': "to_char(X,'YYYY-MM')",
        'quarter': "to_char(X,'YYYY-\"Q\"Q')", 'year': "to_char(X,'YYYY')"},
    'mysql': {
        'day': "date_format(X,'%Y-%m-%d')", 'week': "date_format(X,'%x-W%v')", 'month': "date_format(X,'%Y-%m')",
        'quarter': "CONCAT(date_format(X,'%Y'),'-Q',CAST(quarter(X) AS CHAR))", 'year': "date_format(X,'%Y')"},
}
GOLDEN['mariadb'] = GOLDEN['mysql']


@pytest.mark.parametrize('engine', ALL)
def test_date_bucket_golden_sql(engine):
    d = get_dialect(engine)
    for bucket, sql in GOLDEN[engine].items():
        assert d.date_bucket('X', 'datetime', bucket) == (sql.replace('@@', 'substr(X,1,10)') if engine == 'sqlite' else sql)


def test_date_bucket_kinds_and_unknown_bucket(dialect):
    for kind in ('date', 'datetime'):
        dialect.date_bucket('X', kind, 'month')
    for kind in ('text', 'other'):
        if dialect.engine == 'sqlite':
            dialect.date_bucket('X', kind, 'month')
        else:
            with pytest.raises(ValueError):
                dialect.date_bucket('X', kind, 'month')
    for kind in ('integer', 'boolean', 'json'):
        with pytest.raises(ValueError):
            dialect.date_bucket('X', kind, 'day')
    with pytest.raises(ValueError):
        dialect.date_bucket('X', 'date', 'fortnight')


def oracle_label(bucket: str, day: dt.date) -> str:
    iso = day.isocalendar()
    return {'day': day.isoformat(), 'week': f'{iso[0]}-W{iso[1]:02d}', 'month': f'{day.year}-{day.month:02d}',
            'quarter': f'{day.year}-Q{(day.month - 1) // 3 + 1}', 'year': f'{day.year}'}[bucket]


def test_oracle_pins_the_documented_examples():
    assert oracle_label('week', dt.date(2026, 11, 12)) == '2026-W46'
    assert oracle_label('week', dt.date(2027, 1, 1)) == '2026-W53'
    assert oracle_label('quarter', dt.date(2026, 11, 12)) == '2026-Q4'


def test_sqlite_bucket_labels_match_iso_calendar_for_every_day_in_range():
    d = get_dialect('sqlite')
    start, end = dt.date(2019, 12, 20), dt.date(2028, 12, 31)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE d(v TEXT)')
        db.executemany('INSERT INTO d VALUES(?)', [(day.isoformat() + ' 14:30:00',) for day in days])
        for bucket in ('day', 'week', 'month', 'quarter', 'year'):
            rows = db.execute(f'SELECT v, {d.date_bucket("v", "datetime", bucket)} FROM d ORDER BY v').fetchall()
            assert [r[1] for r in rows] == [oracle_label(bucket, day) for day in days], bucket
            dates_only = db.execute(f"SELECT {d.date_bucket('substr(v,1,10)', 'date', bucket)} FROM d ORDER BY v").fetchall()
            assert [r[0] for r in dates_only] == [oracle_label(bucket, day) for day in days], bucket


def test_sqlite_bucket_of_null_and_unparseable_text_is_null():
    d = get_dialect('sqlite')
    with sqlite3.connect(':memory:') as db:
        for bucket in ('day', 'week', 'month', 'quarter', 'year'):
            row = db.execute('SELECT ' + d.date_bucket(':v', 'text', bucket), {'v': 'not a date'}).fetchone()
            assert row[0] is None, bucket
            assert db.execute('SELECT ' + d.date_bucket(':v', 'text', bucket), {'v': None}).fetchone()[0] is None


# --- capability matrix -----------------------------------------------------------------
MATRIX = {
    'sqlite': dict(qualified=True, requires_source_test=False, quote='"', table_qualification='none', case_folding='ascii',
                   text_equality='exact', bind='integer', literals=('1', '0'), kinds=['date', 'datetime', 'text', 'other'],
                   floor='3.30'),
    'postgresql': dict(qualified=False, requires_source_test=True, quote='"', table_qualification='schema', case_folding='unicode',
                       text_equality='exact', bind='boolean', literals=('TRUE', 'FALSE'), kinds=['date', 'datetime'], floor='12'),
    'mysql': dict(qualified=False, requires_source_test=True, quote='`', table_qualification='database', case_folding='unicode',
                  text_equality='exact', bind='boolean', literals=('TRUE', 'FALSE'), kinds=['date', 'datetime'], floor='8.0'),
    'mariadb': dict(qualified=False, requires_source_test=True, quote='`', table_qualification='database', case_folding='unicode',
                    text_equality='exact', bind='boolean', literals=('TRUE', 'FALSE'), kinds=['date', 'datetime'], floor='10.6'),
}


@pytest.mark.parametrize('engine', ALL)
def test_capability_matrix(engine):
    expected, d = MATRIX[engine], get_dialect(engine)
    result = d.capabilities()
    assert result['supported'] is True and result['reason'] is None and result['engine'] == engine
    assert result['label'] == d.label
    assert result['qualified'] is expected['qualified'] and result['requires_source_test'] is expected['requires_source_test']
    spec = result['dialect']
    assert spec['quote'] == expected['quote'] and spec['param_style'] == 'named_colon'
    assert spec['table_qualification'] == expected['table_qualification']
    assert spec['text_match'] == {'modes': ['contains', 'starts_with'], 'case': 'insensitive',
                                  'case_folding': expected['case_folding'], 'like_escape': '!'}
    assert spec['text_equality'] == expected['text_equality']
    assert spec['filter_ops'] == ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in',
                                  'contains', 'starts_with', 'is_null', 'not_null']
    assert spec['max_in_values'] == 200 and spec['null_ordering'] == 'last'
    assert spec['null_ordering_impl'] == ('native' if engine in ('postgresql', 'sqlite') else 'emulated')
    assert spec['boolean'] == {'literal_true': expected['literals'][0], 'literal_false': expected['literals'][1],
                               'bind': expected['bind']}
    assert spec['limit_syntax'] == 'limit_offset' and spec['conditional_aggregation'] == 'case'
    assert spec['aggregates'] == ['count', 'count_distinct', 'sum', 'avg', 'min', 'max']
    assert spec['date_bucket'] == {'buckets': ['day', 'week', 'month', 'quarter', 'year'], 'week_standard': 'iso8601',
        'labels': {'day': 'YYYY-MM-DD', 'week': 'YYYY-Www', 'month': 'YYYY-MM', 'quarter': 'YYYY-Qn', 'year': 'YYYY'},
        'kinds': expected['kinds']}
    assert spec['join_kinds'] == ['inner', 'left'] and spec['max_joins'] == 4 and spec['max_filters'] == 50
    assert spec['pivot']['max_columns'] == 12 and spec['pivot']['max_row_dims'] == 2
    assert {'text', 'integer', 'boolean', 'date', 'datetime'} <= set(spec['pivot']['column_kinds'])
    assert spec['version_floor'] == expected['floor']


def test_capability_flags_mirror_the_engine_catalog():
    catalog = {e['id']: e['capabilities'] for e in engines()}
    for engine in ALL:
        result = get_dialect(engine).capabilities()
        assert result['qualified'] == catalog[engine]['qualified']
        assert result['requires_source_test'] == catalog[engine]['requires_source_test']
