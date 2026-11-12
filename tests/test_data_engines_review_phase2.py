"""Engines review of phase 2 (pivots, segments): failing tests that prove cross-engine differences.

SQLite cases run everywhere. Live cases need TERMX_TEST_PG_URL, TERMX_TEST_MYSQL_URL and TERMX_TEST_MARIADB_URL.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from test_data_conformance import LIVE_URLS, index_for, live_raw
from termx.data.connectors import SQLiteConnector, typed_scalar
from termx.data.dialects import get_dialect
from termx.data.query_spec import QuerySpec, compile_spec
from termx.data.runtime import DataRuntime

DDL = {
    'postgresql': 'CREATE TABLE {s}.t(id integer PRIMARY KEY, g varchar(20), n integer)',
    'mysql': 'CREATE TABLE {s}.t(id int PRIMARY KEY, g varchar(20), n int)',
}
DDL['mariadb'] = DDL['mysql']
ROWS = [{'id': 1, 'g': 'a', 'n': 1}, {'id': 2, 'g': 'a ', 'n': 1}, {'id': 3, 'g': 'b', 'n': 2},
        {'id': 4, 'g': 'b', 'n': 1}, {'id': 5, 'g': 'b', 'n': 1}]


def pivot(rows=('g',), fn='count', measure=None):
    value = {'fn': fn, **({'table_alias': 's', 'column': measure} if measure else {})}
    return {'version': 1, 'source': 'conn', 'from': {'table': 't', 'alias': 's'}, 'joins': [], 'select': [],
            'filters': {'op': 'and', 'items': []}, 'group_by': [], 'aggregates': [], 'sort': [], 'limit': 200, 'offset': 0,
            'pivot': {'rows': [{'table_alias': 's', 'column': r} for r in rows], 'columns': None, 'value': value, 'totals': True,
                      'sort': {'by': 'rows', 'direction': 'asc'}}}


@contextlib.contextmanager
def live_table(engine):
    url = os.environ.get(LIVE_URLS[engine])
    if not url:
        pytest.skip(f'{LIVE_URLS[engine]} is not set')
    from sqlalchemy import create_engine, text
    source = create_engine(url)
    name = 'termx_r_' + uuid.uuid4().hex[:12]
    quoted = f'"{name}"' if engine == 'postgresql' else f'`{name}`'
    with source.connect() as conn:
        try:
            conn.execute(text(f'CREATE SCHEMA {quoted}' if engine == 'postgresql' else f'CREATE DATABASE {quoted}'))
            conn.execute(text(DDL[engine].format(s=quoted)))
            conn.execute(text(f'INSERT INTO {quoted}.t(id,g,n) VALUES(:id,:g,:n)'), ROWS)
            conn.commit()
            yield type('Live', (), {'conn': conn, 'name': name, 'engine': engine})
        finally:
            conn.rollback()
            conn.execute(text(f'DROP SCHEMA IF EXISTS {quoted} CASCADE' if engine == 'postgresql' else f'DROP DATABASE IF EXISTS {quoted}'))
            conn.commit()
    source.dispose()


def run_live(live, body):
    from sqlalchemy import text
    compiled = compile_spec(QuerySpec.model_validate(body), index_for(live_raw(live), live.engine, live.name), get_dialect(live.engine))
    return [[typed_scalar(c, k['value_kind']) for c, k in zip(row, compiled.columns)]
            for row in live.conn.execute(text(compiled.sql), compiled.parameters).fetchall()]


def run_sqlite(path, body):
    raw = SQLiteConnector(path).schema()
    DataRuntime._annotate_kinds(raw, 'sqlite')
    compiled = compile_spec(QuerySpec.model_validate(body), index_for(raw, 'sqlite'), get_dialect('sqlite'))
    return SQLiteConnector(path).execute(compiled.sql, compiled.parameters, 1000, [c['value_kind'] for c in compiled.columns])['rows']


@pytest.mark.parametrize('engine', ['postgresql', 'mysql', 'mariadb'])
def test_trailing_space_text_stays_a_separate_group(engine):
    """SQLite and PostgreSQL keep 'a' and 'a ' apart. utf8mb4_bin is a PAD SPACE collation, so MySQL and MariaDB merge them."""
    with live_table(engine) as live:
        rows = run_live(live, pivot())
        assert sorted((r[0], r[1]) for r in rows) == [('a', 1), ('a ', 1), ('b', 3)]


@pytest.mark.parametrize('engine', ['postgresql', 'mysql', 'mariadb'])
def test_average_of_whole_numbers_matches_sqlite(engine):
    """AVG over an integer column is DECIMAL on MySQL and MariaDB, cut at 9 places before the cast to DOUBLE."""
    with live_table(engine) as live:
        rows = {r[0]: r[1] for r in run_live(live, pivot(fn='avg', measure='n'))}
        assert rows['b'] == pytest.approx(4 / 3, abs=1e-12)


def test_sqlite_text_that_is_not_an_iso_date_is_empty_not_a_made_up_day(tmp_path):
    """The compiler warns that non-ISO text appears empty. SQLite reads '2026' and '12345' as Julian day numbers,
    'now' as today and '2026-02-30' as March 2."""
    path = Path(tmp_path) / 'd.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, g TEXT, n INTEGER)')
        db.executemany('INSERT INTO t VALUES(?,?,?)', [(1, '2026', 0), (2, '12345', 0), (3, 'now', 0), (4, '2026-02-30', 0), (5, '2026-01-05', 0)])
    body = pivot()
    body['pivot']['rows'] = [{'table_alias': 's', 'column': 'g', 'bucket': 'day'}]
    labels = {r[0] for r in run_sqlite(path, body)}
    assert labels == {'2026-01-05', None}
