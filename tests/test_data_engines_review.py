"""Reviewer (engines lens) probes. Each test documents a defect or a fail-closed guarantee."""
from __future__ import annotations

import pytest

from test_data_query_http import env, get, post, spec, wait  # noqa: F401  (fixture re-export)
from termx.data.connectors import CATALOG
from termx.data.dialects import DIALECTS
from termx.data.query_spec import SpecErrors

SUPPORTED = set(DIALECTS)


@pytest.mark.parametrize('engine', [row[0] for row in CATALOG if row[0] not in SUPPORTED] + ['postgres', 'MySQL', 'sqlite3', '', 'x y'])
def test_every_other_engine_id_fails_closed_on_all_four_routes(env, engine):
    cid = env.source['id']
    record = env.runtime.store.get('connection', cid)
    env.runtime.store.update('connection', cid, {'engine': engine}, record['revision'])
    for response in (post(env, '/query-specs/compile', {'spec': spec(cid)}),
                     post(env, '/query-specs/run', {'request_id': 'u', 'spec': spec(cid)}),
                     get(env, f'/connections/{cid}/relations')):
        assert response.status_code == 422, (engine, response.status_code, response.text[:200])
        assert 'visual query builder supports SQLite, PostgreSQL, MySQL and MariaDB' in response.json()['detail']
    caps = get(env, f'/connections/{cid}/capabilities')
    assert caps.status_code == 200 and caps.json()['supported'] is False and caps.json()['dialect'] is None


def test_number_filter_beyond_int64_does_not_surface_a_generic_connection_error_on_sqlite(env):
    """compile succeeds, SQLite raises OverflowError, _safe_error maps it to 'check the connection, TLS...'."""
    cid = env.source['id']
    body = spec(cid, filters={'op': 'and', 'items': [{'field': {'table_alias': 'o', 'column': 'amount'}, 'op': 'gt', 'value': 10 ** 30}]})
    compiled = post(env, '/query-specs/compile', {'spec': body}).json()
    assert compiled['ok'] is True
    response = post(env, '/query-specs/run', {'request_id': 'big', 'spec': body})
    job = wait(env, response.json())
    message = str(job.get('error') or job)
    assert 'TLS' not in message and 'Check the connection' not in message, message[:400]


# --- live-engine probes (skip unless TERMX_TEST_PG_URL / _MYSQL_URL / _MARIADB_URL are set) ----------
from sqlalchemy import text  # noqa: E402

from test_data_conformance import C, S, compile_for, live_database, live_raw  # noqa: E402

LIVE = ['postgresql', 'mysql', 'mariadb']


@pytest.mark.parametrize('engine', LIVE)
@pytest.mark.parametrize('label', ['é' * 31, None])
def test_result_column_names_equal_the_compiled_labels(engine, label):
    """PostgreSQL truncates aliases to 63 bytes. A label that fits comes back verbatim, and the default label
    'Total ' + a 60-character column name is shortened by the compiler rather than by the server."""
    with live_database(engine) as live:
        column = 'c' * 60
        q = '"' if engine == 'postgresql' else '`'
        live.conn.execute(text(f'CREATE TABLE {q}{live.name}{q}.t(id integer PRIMARY KEY, {q}{column}{q} integer)'))
        live.conn.execute(text(f'INSERT INTO {q}{live.name}{q}.t VALUES (1, 5)'))
        raw = live_raw(live)
        if label:
            body = S(**{'from': {'table': 'orders', 'alias': 'o'}}, select=[{**C('o', 'id'), 'label': label}], limit=2)
        else:
            body = S(**{'from': {'table': 't', 'alias': 't'}}, aggregates=[{'fn': 'sum', **C('t', column)}])
        compiled = compile_for(raw, engine, body, live.name)
        result = live.conn.execute(text(compiled.sql), compiled.parameters)
        assert list(result.keys()) == [c['label'] for c in compiled.columns]


def test_an_explicit_label_longer_than_the_engine_alias_limit_is_refused():
    """40 two-byte letters are 80 bytes: PostgreSQL would cut them to 31 characters, so the compiler says so."""
    with live_database('postgresql') as live:
        raw = live_raw(live)
        body = S(**{'from': {'table': 'orders', 'alias': 'o'}}, select=[{**C('o', 'id'), 'label': 'é' * 40}], limit=2)
        with pytest.raises(SpecErrors) as caught:
            compile_for(raw, 'postgresql', body, live.name)
        assert [e.code for e in caught.value.errors] == ['label_too_long']


@pytest.mark.parametrize('engine', ['mysql', 'mariadb'])
def test_column_names_containing_a_backtick_are_introspected_verbatim(engine):
    """SQLAlchemy's MySQL reflection returns 'back``tick' for a column created as `back``tick`, so the compiler
    rejects the real name and a table with such a column cannot be opened at all."""
    with live_database(engine) as live:
        live.conn.execute(text(f'CREATE TABLE `{live.name}`.bt(id int PRIMARY KEY, `back``tick` int)'))
        raw = live_raw(live)
        names = [c['name'] for t in raw['tables'] if t['name'] == 'bt' for c in t['columns']]
        assert names == ['id', 'back`tick'], names


@pytest.mark.parametrize('engine', LIVE)
def test_distinct_text_values_are_counted_the_same_on_every_engine(engine):
    """customers 4 and 5 are 'ALICE' and 'alice'. SQLite and PostgreSQL count them as two values (exact equality);
    MySQL and MariaDB merge them under the column collation in GROUP BY and COUNT(DISTINCT), as in eq/ne/in."""
    with live_database(engine) as live:
        raw = live_raw(live)
        body = S(**{'from': {'table': 'customers', 'alias': 'c'}}, aggregates=[{'fn': 'count_distinct', **C('c', 'name')}])
        compiled = compile_for(raw, engine, body, live.name)
        got = live.conn.execute(text(compiled.sql), compiled.parameters).scalar()
        assert got == len({row['name'] for row in live.rows['customers'] if row['name'] is not None})


def test_postgresql_uuid_filter_with_a_non_uuid_value_is_rejected_by_the_compiler_not_the_server():
    """uuid columns classify as 'other', which accepts any text value for eq/ne/in/not_in. PostgreSQL then raises
    invalid_text_representation at run time, which the runtime reports as a generic connection failure."""
    with live_database('postgresql') as live:
        live.conn.execute(text(f'CREATE TABLE "{live.name}".u(id integer PRIMARY KEY, token uuid)'))
        raw = live_raw(live)
        body = S(**{'from': {'table': 'u', 'alias': 'u'}}, select=[C('u', 'id')],
                 filters={'op': 'and', 'items': [{'field': C('u', 'token'), 'op': 'eq', 'value': '1234'}]})
        try:
            compiled = compile_for(raw, 'postgresql', body, live.name)
        except Exception:
            return  # rejected at compile time: acceptable
        live.conn.execute(text(compiled.sql), compiled.parameters)
