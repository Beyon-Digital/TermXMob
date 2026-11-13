"""Real disposable SQLite sources, managed identities and durable query jobs."""
from __future__ import annotations

import asyncio
from contextlib import closing
import csv
import io
import json
import os
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from termx.agent.secrets import CredentialStore
from termx.data.connectors import NetworkSQLConnector, SQLiteConnector, engines, validate_read
from termx.data.http import managed_actor
from termx.data.runtime import DataRuntime, build_runtime_router
from termx.data.service import DataService
from termx.identity import AuthenticationService

PASSWORD = 'Data-runtime-fixture-password-123'


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    host = tmp_path / 'host'
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(host))
    identity = AuthenticationService(host / 'identity.sqlite3')
    alice = identity.setup_owner('alice', PASSWORD)
    bob = identity.create_local_user('bob', PASSWORD, [])
    outsider = identity.create_local_user('outsider', PASSWORD, ['host-admin'])
    credentials = CredentialStore(memory={})
    state = SimpleNamespace(identity=identity, credentials=credentials, store=SimpleNamespace(path=host / 'config.json'))
    service = state.data_studio = DataService(state, tmp_path / 'metadata')
    actors = {'alice': alice, 'bob': bob, 'outsider': outsider}
    wid = service.create_workspace(alice, 'Business analysis')['id']
    member = service.add_member(alice, wid, username='bob', role='editor')
    path = tmp_path / 'business.sqlite3'
    # SQLite's own context manager commits the transaction; closing owns the file handle.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE sales(id INTEGER PRIMARY KEY,region TEXT NOT NULL,amount REAL,notes TEXT)')
        db.executemany('INSERT INTO sales VALUES(?,?,?,?)', [(i, 'East' if i % 2 else 'West', i * 2.5,
            '=SUM(A1:A2)' if i == 1 else 'ordinary') for i in range(1, 1306)])
        db.execute("CREATE VIEW unsafe_view AS SELECT load_extension('/nonexistent/extension') AS dangerous")
    runtime = service.runtime
    source = runtime.create_connection(alice, wid, {'name': 'Business SQLite', 'engine': 'sqlite',
        'config': {'path': str(path)}, 'shared_read': True})
    return SimpleNamespace(root=tmp_path, host=host, identity=identity, service=service, runtime=runtime,
        state=state, actors=actors, wid=wid, member=member, path=path, source=source, seed_connection=db)


def query(fixture, sql='SELECT * FROM sales ORDER BY id', parameters=None):
    return {'connection_id': fixture.source['id'], 'sql': sql, 'parameters': parameters or {}}


async def wait_job(fixture, actor, job):
    for _ in range(2000):
        value = fixture.runtime.get_job(actor, fixture.wid, job['id'])
        if value['status'] not in {'queued', 'running'}:
            return value
        await asyncio.sleep(.005)
    raise AssertionError('Fixture query did not complete')


def denied(code, fn):
    with pytest.raises(HTTPException) as exc:
        fn()
    assert exc.value.status_code == code


def test_catalog_never_claims_generic_or_uninstalled_engines_work():
    catalog = {item['id']: item for item in engines()}
    assert catalog['sqlite']['status'] == 'qualified' and catalog['sqlite']['ready']
    assert catalog['postgresql']['status'] == 'installed'
    assert not catalog['postgresql']['capabilities']['qualified']
    assert catalog['postgresql']['capabilities']['requires_source_test']
    for name in ('sqlserver', 'oracle', 'mongodb', 'redis', 'neo4j', 'bigquery', 'parquet', 'rest'):
        assert catalog[name]['status'] == 'unsupported' and not catalog[name]['ready']
        assert not catalog[name]['capabilities']['read']
    assert all(not item['capabilities']['write'] for item in catalog.values())
    assert {'relational', 'warehouse', 'lakehouse', 'document', 'search', 'graph', 'file', 'api'} <= {item['family'] for item in catalog.values()}


def test_actual_sqlite_test_schema_parameters_and_full_aggregate(fixture):
    async def scenario():
        a = fixture.actors['alice']
        test = await fixture.runtime.test(a, fixture.wid, fixture.source['id'])
        assert test['ok'] and test['read_available']
        metadata = await fixture.runtime.schema(a, fixture.wid, fixture.source['id'])
        sales = next(table for table in metadata['tables'] if table['name'] == 'sales')
        assert sales['columns'][0]['primary_key'] and sales['columns'][1]['type'] == 'TEXT'
        result = await fixture.runtime.execute_read(a, fixture.wid,
            query(fixture, 'SELECT region, SUM(amount) AS total, COUNT(*) AS n FROM sales WHERE amount > :minimum GROUP BY region ORDER BY region', {'minimum': 0}))
        assert sum(row[2] for row in result['rows']) == 1305
        assert sum(row[1] for row in result['rows']) == sum(i * 2.5 for i in range(1, 1306))
        assert not result['truncated']
        assert await fixture.runtime.execute_read(a, fixture.wid, query(fixture, 'SELECT id FROM sales WHERE id=:id', {'id': 7})) == {
            'columns': [{'name': 'id', 'type': 'unknown'}], 'rows': [[7]], 'row_count': 1, 'truncated': False}
        await fixture.runtime.close()
    asyncio.run(scenario())


def test_preview_truncation_csv_and_durable_idempotency(fixture):
    async def scenario():
        a = fixture.actors['alice']
        job = await fixture.runtime.run(a, fixture.wid, query(fixture), 'preview-1')
        assert job['status'] == 'queued'
        duplicate = await fixture.runtime.run(a, fixture.wid, query(fixture), 'preview-1')
        assert duplicate['id'] == job['id']
        completed = await wait_job(fixture, a, job)
        assert completed['status'] == 'completed'
        assert completed['result']['row_count'] == 1000 and completed['result']['truncated']
        payload, truncated = fixture.runtime.export_csv(a, fixture.wid, job['id'])
        rows = list(csv.reader(io.StringIO(payload)))
        assert len(rows) == 1001 and truncated
        assert rows[1][-1] == "'=SUM(A1:A2)"
        assert 'credential' not in json.dumps(completed) and '_definition' not in completed
        with pytest.raises(HTTPException) as raised:
            await fixture.runtime.run(a, fixture.wid, query(fixture, 'SELECT 2'), 'preview-1')
        assert raised.value.status_code == 409
        other = await fixture.runtime.run(fixture.actors['bob'], fixture.wid, query(fixture), 'preview-1')
        assert other['id'] != job['id']
        denied(404, lambda: fixture.runtime.get_job(fixture.actors['bob'], fixture.wid, job['id']))
        await wait_job(fixture, fixture.actors['bob'], other)
        reloaded = DataRuntime(fixture.service)
        assert reloaded.get_job(a, fixture.wid, job['id'])['result'] == completed['result']
        await fixture.runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('sql', [
    'DELETE FROM sales', 'UPDATE sales SET amount=0 RETURNING *', 'INSERT INTO sales VALUES(1,2,3,4)',
    'CREATE TABLE stolen(a)', 'DROP TABLE sales', "ATTACH DATABASE '/tmp/nope.db' AS stolen",
    'DETACH DATABASE main', 'PRAGMA query_only=OFF', 'PRAGMA database_list', 'VACUUM INTO \'/tmp/nope.db\'',
    'SELECT 1; SELECT 2', "SELECT load_extension('/tmp/nope')", "SELECT readfile('/etc/passwd')",
    "SELECT writefile('/tmp/nope','x')", 'SELECT pg_read_file(\'/etc/passwd\')',
    'SELECT pg_sleep(1)', "SELECT * FROM read_csv('/tmp/x.csv')", 'SELECT * FROM sales FOR UPDATE',
    'SELECT * INTO stolen FROM sales', "COPY sales TO '/tmp/nope'", 'WITH t AS (DELETE FROM sales RETURNING *) SELECT * FROM t',
])
def test_ast_denies_mutations_and_external_effects(sql):
    with pytest.raises(HTTPException):
        validate_read(sql, 'sqlite')


@pytest.mark.parametrize('sql', [
    'DELETE FROM sales', 'PRAGMA query_only=OFF', 'PRAGMA database_list',
    "ATTACH DATABASE ':memory:' AS stolen", "SELECT load_extension('/tmp/nope')",
    'SELECT * FROM pragma_database_list', 'SELECT * FROM unsafe_view',
])
def test_sqlite_vm_enforces_policy_even_when_parser_is_bypassed(fixture, sql):
    connector = SQLiteConnector(fixture.path)
    with pytest.raises(sqlite3.Error):
        connector.execute(sql, {}, 10)
    with sqlite3.connect(fixture.path) as db:
        assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0] == 1305


def test_protected_host_state_symlinks_hardlinks_and_changed_file(fixture):
    runtime, a, wid = fixture.runtime, fixture.actors['alice'], fixture.wid
    def enroll(path):
        return runtime.create_connection(a, wid, {'name': 'Private', 'engine': 'sqlite', 'config': {'path': str(path)}})
    internal = fixture.host / 'extra-business-looking.db'
    internal.touch()
    for path in (internal, fixture.identity.path, fixture.service.store.path):
        denied(403, lambda path=path: enroll(path))
    symlink = fixture.root / 'internal-link.db'
    symlink.symlink_to(fixture.identity.path)
    denied(403, lambda: enroll(symlink))
    hardlink = fixture.root / 'internal-hardlink.db'
    os.link(fixture.identity.path, hardlink)
    denied(403, lambda: enroll(hardlink))
    for invalid in ('relative.db', 'file:/tmp/a.db?mode=rw', str(fixture.root / 'missing.db')):
        denied(422, lambda invalid=invalid: enroll(invalid))
    external = fixture.root / 'source-link.db'
    external.symlink_to(fixture.path)
    good = enroll(external)
    assert good['config']['path'] == str(fixture.path.resolve())
    signature = runtime.store.get('connection', good['id'])['_file_identity']
    renamed = fixture.root / 'original-business.db'
    # Keep the seed connection strongly referenced so GC cannot hide a leaked handle on POSIX.
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        fixture.seed_connection.execute('SELECT 1')
    fixture.path.rename(renamed)
    fixture.path.symlink_to(fixture.identity.path)
    denied(403, lambda: runtime.validate_path(str(fixture.path), signature))


def test_enrollment_does_not_connect_and_never_stores_plaintext_credentials(fixture, monkeypatch):
    a, wid = fixture.actors['alice'], fixture.wid
    def forbidden(*args, **kwargs):
        raise AssertionError('Enrollment must not connect to a database')
    monkeypatch.setattr(NetworkSQLConnector, 'connection', forbidden)
    body = {'name': 'Postgres account', 'engine': 'postgresql', 'config': {
        'host': 'db.invalid', 'database': 'business', 'username': 'reader', 'tls': True}, 'password': 'secret-not-in-dto'}
    source = fixture.runtime.create_connection(a, wid, body)
    stored = fixture.runtime.store.get('connection', source['id'])
    handle = stored['credential_ref']
    assert handle.startswith('data_') and fixture.state.credentials.get(handle) == body['password']
    assert 'secret-not-in-dto' not in json.dumps(source) and handle not in json.dumps(source)
    assert 'secret-not-in-dto' not in fixture.service.store.path.read_bytes().decode('latin1')
    updated = fixture.runtime.update_connection(a, wid, source['id'], {'revision': source['revision'], 'password': '', 'name': 'Renamed'})
    assert fixture.runtime.store.get('connection', source['id'])['credential_ref'] == handle
    assert fixture.state.credentials.get(handle) == body['password']
    assert updated['credential_generation'] == source['credential_generation']
    for bad in ({'driver': 'evil'}, {'url': 'postgres://secret'}, {'tls': False}, {'host': 'postgres://secret'}):
        config = {**body['config'], **bad}
        denied(422, lambda config=config: fixture.runtime.create_connection(a, wid, {**body, 'config': config}))
    denied(403, lambda: fixture.runtime.update_connection(fixture.actors['bob'], wid, source['id'], {'revision': source['revision'], 'name': 'Hijacked'}))
    fixture.runtime.archive_connection(a, wid, source['id'], updated['revision'])
    assert fixture.state.credentials.get(handle) == body['password']


def test_live_membership_source_acl_role_and_result_revocation(fixture):
    async def scenario():
        a, b = fixture.actors['alice'], fixture.actors['bob']
        job = await fixture.runtime.run(b, fixture.wid, query(fixture, 'SELECT count(*) AS n FROM sales'), 'bob-read')
        assert (await wait_job(fixture, b, job))['status'] == 'completed'
        current = fixture.runtime.store.get('connection', fixture.source['id'])
        fixture.runtime.update_connection(a, fixture.wid, current['id'], {'revision': current['revision'], 'shared_read': False})
        denied(403, lambda: fixture.runtime.get_job(b, fixture.wid, job['id']))
        denied(403, lambda: fixture.runtime.export_csv(b, fixture.wid, job['id']))
        with pytest.raises(HTTPException) as raised:
            await fixture.runtime.run(b, fixture.wid, query(fixture), 'now-revoked')
        assert raised.value.status_code == 403
        member = fixture.service.change_member(a, fixture.wid, fixture.member['id'], fixture.member['revision'], role='viewer')
        with pytest.raises(HTTPException) as raised:
            await fixture.runtime.run(b, fixture.wid, query(fixture), 'viewer-sql')
        assert raised.value.status_code == 403
        fixture.service.change_member(a, fixture.wid, member['id'], member['revision'], remove=True)
        denied(404, lambda: fixture.runtime.get_job(b, fixture.wid, job['id']))
        await fixture.runtime.close()
    asyncio.run(scenario())


def test_revoke_during_execution_discards_result(fixture, monkeypatch):
    started, release = threading.Event(), threading.Event()
    execute = SQLiteConnector.execute
    def blocked(self, *args, **kwargs):
        started.set()
        assert release.wait(5), 'Fixture did not release source worker'
        return execute(self, *args, **kwargs)
    monkeypatch.setattr(SQLiteConnector, 'execute', blocked)
    async def scenario():
        a, b = fixture.actors['alice'], fixture.actors['bob']
        job = await fixture.runtime.run(b, fixture.wid, query(fixture, 'SELECT 42'), 'inflight')
        try:
            for _ in range(1000):
                if started.is_set():
                    break
                await asyncio.sleep(.005)
            assert started.is_set()
            fixture.service.change_member(a, fixture.wid, fixture.member['id'], fixture.member['revision'], remove=True)
        finally:
            release.set()
        await asyncio.gather(*list(fixture.runtime._tasks.values()))
        record = fixture.runtime.store.get('job', job['id'])
        assert record['status'] == 'failed' and record['result'] is None and record['error']
        denied(404, lambda: fixture.runtime.get_job(b, fixture.wid, job['id']))
        await fixture.runtime.close()
    asyncio.run(scenario())


def test_sqlite_running_and_queued_cancellation_are_actual(fixture):
    async def scenario():
        a = fixture.actors['alice']
        definition = query(fixture, 'WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<100000000) SELECT sum(x) FROM c')
        queued = await fixture.runtime.run(a, fixture.wid, definition, 'cancel-queued')
        fixture.runtime.cancel(a, fixture.wid, queued['id'])
        queued_end = await wait_job(fixture, a, queued)
        assert queued_end['status'] == 'cancelled' and queued_end['error'] is None
        running = await fixture.runtime.run(a, fixture.wid, definition, 'cancel-running')
        for _ in range(1000):
            if running['id'] in fixture.runtime._active:
                break
            await asyncio.sleep(.005)
        assert running['id'] in fixture.runtime._active
        requested = fixture.runtime.cancel(a, fixture.wid, running['id'])
        assert requested['cancel_requested']
        ended = await wait_job(fixture, a, running)
        assert ended['status'] == 'cancelled' and ended['result'] is None and ended['error'] is None
        denied(409, lambda: fixture.runtime.export_csv(a, fixture.wid, running['id']))
        await fixture.runtime.close()
    asyncio.run(scenario())


def test_worker_restart_returns_unknown_without_replaying(fixture):
    async def scenario():
        a = fixture.actors['alice']
        job = await fixture.runtime.run(a, fixture.wid, query(fixture, 'SELECT 1'), 'recovered')
        await wait_job(fixture, a, job)
        fixture.runtime._update_job(job['id'], {'status': 'running', 'result': None})
        restarted = DataRuntime(fixture.service)
        result = restarted.get_job(a, fixture.wid, job['id'])
        assert result['status'] == 'unknown' and not restarted._tasks
        assert 'not replayed' in result['error']
        await fixture.runtime.close()
    asyncio.run(scenario())


def test_saved_queries_validate_source_and_revisions(fixture):
    a, b, runtime, wid = fixture.actors['alice'], fixture.actors['bob'], fixture.runtime, fixture.wid
    saved = runtime.create_query(a, wid, {'name': 'Sales total', **query(fixture, 'SELECT sum(amount) total FROM sales')})
    updated = runtime.update_query(b, wid, saved['id'], {'revision': saved['revision'], 'name': 'Team total'})
    assert updated['name'] == 'Team total'
    denied(409, lambda: runtime.update_query(a, wid, saved['id'], {'revision': saved['revision'], 'name': 'Old'}))
    other = fixture.service.create_workspace(a, 'Other workspace')['id']
    denied(404, lambda: runtime.create_query(a, other, {'name': 'Cross source', **query(fixture)}))
    removed = runtime.archive_query(a, wid, saved['id'], updated['revision'])
    assert removed['archived']
    assert fixture.service.queries(a, wid) == []


def test_runtime_http_managed_auth_no_client_report_authority_and_csv_headers(fixture):
    session = asyncio.run(fixture.identity.login('local-password', {'username': 'alice', 'password': PASSWORD}, peer='127.0.0.1'))
    headers = {'Authorization': 'Bearer ' + session.access_token}
    app = FastAPI()
    app.include_router(build_runtime_router(fixture.service, managed_actor(fixture.state)))
    with TestClient(app, base_url='https://localhost') as client:
        assert client.get('/api/data/engines').status_code == 401
        assert client.get('/api/data/engines', headers=headers).status_code == 200
        base = '/api/data/workspaces/' + fixture.wid
        forbidden = client.post(base + '/query-runs', headers=headers, json={'request_id': 'bad-authority', **query(fixture), 'from_report': True})
        assert forbidden.status_code == 422
        posted = client.post(base + '/query-runs', headers=headers, json={'request_id': 'http-query', **query(fixture, 'SELECT 1 AS n')})
        assert posted.status_code == 200
        import time
        for _ in range(1000):
            job = client.get(base + '/jobs/' + posted.json()['id'], headers=headers).json()
            if job['status'] not in {'queued', 'running'}:
                break
            time.sleep(.005)
        assert job['status'] == 'completed'
        exported = client.get(base + '/jobs/' + job['id'] + '/export.csv', headers=headers)
        assert exported.status_code == 200 and exported.headers['X-Data-Export-Scope'] == 'query-preview'
        assert exported.headers['X-Data-Truncated'] == 'false' and exported.text == 'n\r\n1\r\n'


def test_data_owner_cannot_inherit_host_file_authority_and_projects_remain_scoped(fixture):
    from termx.authorization import AuthorizationService
    from termx.project_files import ProjectFiles
    state, s, a, b = fixture.state, fixture.service, fixture.actors['alice'], fixture.actors['bob']
    state.authorization = AuthorizationService(fixture.identity)
    state.projects = ProjectFiles()
    try:
        own_workspace = s.create_workspace(b, 'Data ownership grants no host files')['id']
        denied(403, lambda: fixture.runtime.create_connection(b, own_workspace, {'name': 'No scope', 'engine': 'sqlite', 'config': {'path': str(fixture.path)}}))
        state.authorization.set_role(b.id, 'viewer')
        project_root = fixture.root / 'business-project'
        project_root.mkdir()
        source_path = project_root / 'source.db'
        with sqlite3.connect(source_path) as db:
            db.execute('CREATE TABLE permitted(value)')
            db.execute('INSERT INTO permitted VALUES(42)')
        project = state.projects.register(str(project_root))
        state.authorization.grant_project(b.id, project['id'], ['files-read'])
        source = fixture.runtime.create_connection(b, fixture.wid, {'name': 'Explicit project source', 'engine': 'sqlite',
            'config': {'path': str(source_path)}, 'shared_read': True})
        # A project grant does not extend to sibling files, even for a Data owner.
        denied(403, lambda: fixture.runtime.create_connection(b, own_workspace, {'name': 'Outside grant', 'engine': 'sqlite', 'config': {'path': str(fixture.path)}}))
        nested = project_root / 'nested'
        nested.mkdir()
        nested_path = nested / 'child.db'
        with sqlite3.connect(nested_path) as db:
            db.execute('CREATE TABLE nested_values(value)')
        child = state.projects.register(str(nested))
        # The most specific registered project controls nested paths.
        denied(403, lambda: fixture.runtime.create_connection(b, fixture.wid, {'name': 'Nested denied', 'engine': 'sqlite', 'config': {'path': str(nested_path)}}))
        async def scenario():
            definition = {'connection_id': source['id'], 'sql': 'SELECT value FROM permitted'}
            job = await fixture.runtime.run(a, fixture.wid, definition, 'shared-project')
            assert (await wait_job(fixture, a, job))['result']['rows'] == [[42]]
            state.authorization.revoke_project(b.id, project['id'])
            with pytest.raises(HTTPException) as raised:
                await fixture.runtime.execute_read(a, fixture.wid, definition)
            assert raised.value.status_code == 403
            denied(403, lambda: fixture.runtime.get_job(a, fixture.wid, job['id']))
            denied(403, lambda: fixture.runtime.export_csv(a, fixture.wid, job['id']))
            await fixture.runtime.close()
        asyncio.run(scenario())
    finally:
        state.projects.close()


def test_delegating_source_session_revocation_is_live(fixture):
    async def scenario():
        session = await fixture.identity.login('local-password', {'username': 'alice', 'password': PASSWORD}, peer='127.0.0.1')
        actor = fixture.identity.resolve(session.access_token).principal
        source = fixture.runtime.create_connection(actor, fixture.wid, {'name': 'Session-delegated source',
            'engine': 'sqlite', 'config': {'path': str(fixture.path)}, 'shared_read': True})
        definition = {'connection_id': source['id'], 'sql': 'SELECT 1'}
        job = await fixture.runtime.run(fixture.actors['bob'], fixture.wid, definition, 'session-delegation')
        assert (await wait_job(fixture, fixture.actors['bob'], job))['status'] == 'completed'
        assert fixture.identity.revoke(session.session_id, actor.id)
        denied(403, lambda: fixture.runtime.get_job(fixture.actors['bob'], fixture.wid, job['id']))
        # The creator may explicitly re-enroll the source using current authority.
        repaired = fixture.runtime.update_connection(fixture.actors['alice'], fixture.wid, source['id'], {'revision': source['revision'], 'name': 'Re-authorized'})
        assert repaired['source_generation'] == 2
        assert (await fixture.runtime.execute_read(fixture.actors['bob'], fixture.wid, definition))['rows'] == [[1]]
        await fixture.runtime.close()
    asyncio.run(scenario())


def test_large_cell_and_total_preview_size_are_honestly_bounded(fixture):
    with sqlite3.connect(fixture.path) as db:
        db.execute('CREATE TABLE large_values(value TEXT)')
        db.executemany('INSERT INTO large_values VALUES(?)', [('x' * 60000,) for _ in range(100)])
    with sqlite3.connect(fixture.path) as db:
        db.execute('CREATE TABLE huge_cell(value TEXT)')
        db.execute('INSERT INTO huge_cell VALUES(?)', ('𝛌' * 80000,))
    async def actual():
        a = fixture.actors['alice']
        cell = await fixture.runtime.execute_read(a, fixture.wid, query(fixture, 'SELECT value FROM huge_cell'))
        assert cell['row_count'] == 1 and cell['truncated'] and '64 KB' in cell['notice']
        assert len(cell['rows'][0][0].encode('utf-8')) < 66000
        preview = await fixture.runtime.execute_read(a, fixture.wid, query(fixture, 'SELECT value FROM large_values'))
        assert 1 < preview['row_count'] < 100 and preview['truncated'] and '4 MB' in preview['notice']
        assert len(json.dumps(preview).encode('utf-8')) < 4 * 1024 * 1024
        await fixture.runtime.close()
    asyncio.run(actual())


def test_network_role_proofs_reject_effective_writes_and_unverified_sessions():
    # These prove our policy branches, not external server/protocol qualification.
    class Result:
        def __init__(self, value): self.value = value
        def scalar(self): return self.value
        def one(self): return self.value
        def __iter__(self): return iter(self.value)
    class Connection:
        def __init__(self, grants=None, readonly=True, elevated=False, unsafe=False):
            self.grants, self.readonly, self.elevated, self.unsafe = grants or [], readonly, elevated, unsafe
        def exec_driver_sql(self, sql):
            if sql == 'SHOW transaction_read_only': return Result('on' if self.readonly else 'off')
            if sql.startswith('SELECT rolsuper'): return Result((self.elevated, False, False, False, False))
            if sql.startswith('SELECT\n'): return Result(self.unsafe)
            if sql.startswith('SELECT @@'): return Result(self.readonly)
            if sql.startswith('SHOW GRANTS'): return Result([(g,) for g in self.grants])
            return Result(None)
    pg = NetworkSQLConnector('postgresql', {}, None)
    pg._prove_readonly(Connection())
    for connection in (Connection(readonly=False), Connection(elevated=True), Connection(unsafe=True)):
        denied(409, lambda connection=connection: pg._prove_readonly(connection))
    mysql = NetworkSQLConnector('mysql', {}, None)
    mysql._prove_readonly(Connection(grants=['GRANT USAGE ON *.* TO reader@host', 'GRANT SELECT, SHOW VIEW ON business.* TO reader@host']))
    for grant in ('GRANT ALL PRIVILEGES ON *.* TO reader@host', 'GRANT SELECT, FILE ON *.* TO reader@host',
                  'GRANT EXECUTE ON business.* TO reader@host', 'GRANT readonly_role TO reader@host'):
        denied(409, lambda grant=grant: mysql._prove_readonly(Connection(grants=[grant])))
    denied(409, lambda: mysql._prove_readonly(Connection(grants=['GRANT SELECT ON business.* TO reader@host'], readonly=False)))


@pytest.mark.parametrize('sql', ['SELECT 1::public.source_type', 'SELECT 1 OPERATOR(public.+) 2', 'SELECT public.abs(1)'])
def test_postgres_source_types_operators_and_functions_are_not_builtin_proof(sql):
    with pytest.raises(HTTPException):
        validate_read(sql, 'postgresql')


def test_network_driver_never_inherits_ambient_password_or_client_identity(monkeypatch):
    import sqlalchemy
    import psycopg.conninfo
    captured = {}
    class StopBeforeConnect(Exception): pass
    def create_engine(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        raise StopBeforeConnect()
    monkeypatch.setattr(sqlalchemy, 'create_engine', create_engine)
    config = {'host': 'db.invalid', 'port': 5432, 'database': 'business', 'username': 'reader', 'tls': True}
    monkeypatch.setenv('PGPASSWORD', 'ambient-host-secret')
    monkeypatch.setenv('PGSERVICE', 'ambient-host-service')
    without_password = NetworkSQLConnector('postgresql', config, None)
    denied(409, lambda: without_password.test())
    assert captured == {}
    connector = NetworkSQLConnector('postgresql', config, 'explicit-profile-secret')
    with pytest.raises(StopBeforeConnect):
        connector.test()
    url, options = captured['url'], captured['url'].query
    assert url.password == 'explicit-profile-secret'
    assert options['sslmode'] == 'verify-full' and options['sslrootcert'] == 'system'
    assert options['passfile'] == os.devnull and options['sslcertmode'] == 'disable'
    assert 'default_transaction_read_only=on' in options['options']
    assert captured['kwargs']['hide_parameters'] and not captured['kwargs']['echo']
    # libpq validates these fixed options without opening any server connection.
    assert psycopg.conninfo.conninfo_to_dict(psycopg.conninfo.make_conninfo(**dict(options)))['sslcertmode'] == 'disable'
