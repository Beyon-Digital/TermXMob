"""Store migration 1 -> 2 (saved views): additive, backed up, idempotent, atomic."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat

import pytest
from fastapi import HTTPException

from termx.data.store import DataStore, timestamp
from termx.private_files import private_path_permissions

V1_DDL = '''
CREATE TABLE data_records (kind TEXT NOT NULL, id TEXT NOT NULL PRIMARY KEY, workspace_id TEXT NOT NULL,
    revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL CHECK(json_valid(body)));
CREATE INDEX data_workspace_records ON data_records(kind, workspace_id, created_at);
CREATE TABLE data_audit (id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, workspace_id TEXT NOT NULL, action TEXT NOT NULL,
    resource_id TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX data_workspace_audit ON data_audit(workspace_id, created_at);
PRAGMA user_version=1;
'''


def v1_store(directory, rows=True):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'data.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript(V1_DDL)
        if rows:
            for i, kind in enumerate(['workspace', 'member', 'connection', 'query', 'report', 'job']):
                db.execute('INSERT INTO data_records VALUES (?,?,?,?,?,?,?)',
                           (kind, f'{kind}-{i}', 'w1', 1, timestamp(), timestamp(), json.dumps({'name': kind, 'n': i})))
            db.execute('INSERT INTO data_audit VALUES (?,?,?,?,?,?,?)', ('a1', 'p1', 'w1', 'x', 'r', '{}', timestamp()))
    return path


def digest(path):
    db = sqlite3.connect(path)
    try:
        rows = db.execute('SELECT * FROM data_records ORDER BY id').fetchall() + db.execute('SELECT * FROM data_audit ORDER BY id').fetchall()
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    finally:
        db.close()


def version(path):
    db = sqlite3.connect(path)
    try:
        return db.execute('PRAGMA user_version').fetchone()[0]
    finally:
        db.close()


def test_a_version_1_store_is_migrated_without_touching_a_row(tmp_path):
    path = v1_store(tmp_path / 'm')
    before = digest(path)
    DataStore(tmp_path / 'm')
    db = sqlite3.connect(path)
    names = {r[0] for r in db.execute('SELECT name FROM sqlite_master')}
    db.close()
    assert version(path) == 2
    assert {'data_view_revisions', 'data_view_revisions_ws', 'data_job_view'} <= names
    assert digest(path) == before
    backup = tmp_path / 'm' / 'data.sqlite3.pre-views-v1'
    assert backup.exists()
    assert all(private_path_permissions(item) for item in (path.parent, path, backup))
    if os.name != 'nt':
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in (path, backup))
    assert digest(backup) == before
    assert version(backup) == 1


def test_reopening_is_a_no_op_and_does_not_recopy(tmp_path):
    path = v1_store(tmp_path / 'm')
    DataStore(tmp_path / 'm')
    backup = tmp_path / 'm' / 'data.sqlite3.pre-views-v1'
    stamp = backup.stat().st_mtime_ns
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO data_records VALUES ('view','v1','w1',1,'t','t','{}')")
    DataStore(tmp_path / 'm')
    DataStore(tmp_path / 'm')
    assert backup.stat().st_mtime_ns == stamp
    assert sqlite3.connect(backup).execute("SELECT COUNT(*) FROM data_records WHERE kind='view'").fetchone()[0] == 0
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM data_records WHERE kind='view'").fetchone()[0] == 1


def test_an_empty_version_1_store_and_a_new_store_skip_the_backup(tmp_path):
    v1_store(tmp_path / 'empty', rows=False)
    DataStore(tmp_path / 'empty')
    DataStore(tmp_path / 'fresh')
    assert not (tmp_path / 'empty' / 'data.sqlite3.pre-views-v1').exists()
    assert not (tmp_path / 'fresh' / 'data.sqlite3.pre-views-v1').exists()
    assert version(tmp_path / 'fresh' / 'data.sqlite3') == 2


def test_a_newer_schema_is_refused_and_an_old_build_would_refuse_version_2(tmp_path):
    path = v1_store(tmp_path / 'm')
    DataStore(tmp_path / 'm')
    # A PR 1 build checked `user_version > 1`; a migrated store is version 2, so it refuses to open (documented downgrade).
    assert version(path) > 1
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=3')
    with pytest.raises(HTTPException) as error:
        DataStore(tmp_path / 'm')
    assert error.value.status_code == 409 and 'newer TermX' in error.value.detail


def test_a_revision_row_and_its_record_write_are_atomic(tmp_path):
    store = DataStore(tmp_path / 'm')
    with pytest.raises(RuntimeError):
        with store.transaction() as db:
            record = store._create(db, 'view', 'w1', 'p1', {'name': 'x'})
            store._revision_insert(db, view_id=record['id'], revision=1, workspace_id='w1', actor_id='p1', actor_name='A',
                                   action='created', name='x', snapshot={}, changes=[])
            raise RuntimeError('boom')
    assert store.list('view', 'w1') == []
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM data_view_revisions').fetchone()[0] == 0
    with store.transaction() as db:
        record = store._create(db, 'view', 'w1', 'p1', {'name': 'x'})
        store._revision_insert(db, view_id=record['id'], revision=1, workspace_id='w1', actor_id='p1', actor_name='A',
                               action='created', name='x', snapshot={'a': 1}, changes=[])
    rows = store.revisions('w1', record['id'], 10, None)
    assert len(rows) == 1 and rows[0]['snapshot'] == {'a': 1}


def test_the_run_history_query_uses_the_job_index(tmp_path):
    store = DataStore(tmp_path / 'm')
    db = sqlite3.connect(store.path)
    plan = ' '.join(r[3] for r in db.execute('''EXPLAIN QUERY PLAN SELECT id FROM data_records INDEXED BY data_job_view WHERE kind='job'
        AND json_extract(body,'$.view_id')=? AND workspace_id=? ORDER BY created_at DESC, id DESC''', ('v', 'w')))
    db.close()
    assert 'data_job_view' in plan, plan


def test_run_history_pages_by_cursor_and_filters_by_actor(tmp_path):
    store = DataStore(tmp_path / 'm')
    for i in range(5):
        store.create('job', 'w1', 'p%d' % (i % 2), {'actor_id': 'p%d' % (i % 2), 'view_id': 'v1', 'status': 'completed',
                                                   'result': {'row_count': i, 'truncated': False}, 'mode': 'rows', 'view_revision': 1})
    store.create('job', 'w1', 'p0', {'actor_id': 'p0', 'status': 'completed'})   # not a view run
    everyone = store.view_jobs('w1', 'v1', actor_id=None, limit=10, cursor=None)
    assert len(everyone) == 5
    mine = store.view_jobs('w1', 'v1', actor_id='p1', limit=10, cursor=None)
    assert {r['actor_id'] for r in mine} == {'p1'} and len(mine) == 2
    page = store.view_jobs('w1', 'v1', actor_id=None, limit=2, cursor=None)
    assert len(page) == 3
    rest = store.view_jobs('w1', 'v1', actor_id=None, limit=10, cursor=(page[1]['created_at'], page[1]['id']))
    assert {r['id'] for r in rest}.isdisjoint({page[0]['id'], page[1]['id']}) and len(rest) == 3
