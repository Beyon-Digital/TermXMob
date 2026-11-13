"""Transactional metadata for Data Studio, isolated from business sources."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import HTTPException

_RESERVED = {'id', 'workspace_id', 'revision', 'created_at', 'updated_at'}
_SECRET_KEYS = {'password', 'passwd', 'api_key', 'apikey', 'access_token', 'refresh_token',
                'secret', 'client_secret', 'authorization', 'dsn', 'connection_string'}


SCHEMA_VERSION = 2
# Every statement is CREATE ... IF NOT EXISTS: opening a store is idempotent and never alters a row.
SCHEMA = '''
    CREATE TABLE IF NOT EXISTS data_records (
        kind TEXT NOT NULL, id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL, revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        body TEXT NOT NULL CHECK(json_valid(body))
    );
    CREATE INDEX IF NOT EXISTS data_workspace_records
        ON data_records(kind, workspace_id, created_at);
    CREATE TABLE IF NOT EXISTS data_audit (
        id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL, action TEXT NOT NULL,
        resource_id TEXT NOT NULL, detail TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS data_workspace_audit
        ON data_audit(workspace_id, created_at);
    CREATE TABLE IF NOT EXISTS data_view_revisions (
        view_id TEXT NOT NULL, revision INTEGER NOT NULL,
        workspace_id TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('created','updated','archived')),
        created_at TEXT NOT NULL,
        name TEXT NOT NULL,
        snapshot TEXT NOT NULL CHECK(json_valid(snapshot)),
        changes TEXT NOT NULL CHECK(json_valid(changes)),
        PRIMARY KEY (view_id, revision)
    );
    CREATE INDEX IF NOT EXISTS data_view_revisions_ws
        ON data_view_revisions(workspace_id, view_id, revision DESC);
    CREATE INDEX IF NOT EXISTS data_job_view
        ON data_records(json_extract(body,'$.view_id'), created_at)
        WHERE kind='job' AND json_extract(body,'$.view_id') IS NOT NULL;
    PRAGMA user_version=2;
'''


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier_for(kind: str, *parts: str) -> str:
    """Stable, namespaced IDs for memberships and scoped request receipts."""
    return uuid.uuid5(uuid.NAMESPACE_URL, 'termx:data:' + json.dumps([kind, *parts], ensure_ascii=False)).hex


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, 'Data metadata must be valid JSON') from exc


def _validate(kind: str, body: dict[str, Any]) -> None:
    if not isinstance(body, dict) or set(body) & _RESERVED:
        raise HTTPException(422, 'Metadata identity and revision fields are server managed')
    if kind in {'connection', 'copilot_settings'}:
        def check(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if str(key).lower().replace('-', '_') in _SECRET_KEYS:
                        raise HTTPException(422, 'Store credentials in secure storage, not Data metadata')
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)
        check(body)
    _json(body)


class DataStore:
    """Short SQLite transactions; each operation uses its own connection.

    Archived records remain available by ID for durable receipt checks, while
    list() returns active records. All writes compare revisions atomically.
    """

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'data.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        from termx.private_files import protect_private_path
        protect_private_path(self.directory, directory=True)
        protect_private_path(self.path)
        self._backup_before_migration()
        with self.transaction() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
                raise HTTPException(409, 'This Data metadata schema requires a newer TermX version')
            db.executescript(SCHEMA)

    def _backup_before_migration(self) -> None:
        """Copy a non-empty version 1 store aside once, before the additive version 2 objects are created.

        The copy keeps the pre-views state recoverable: a build from before saved views refuses to open a
        version 2 store, and restoring this file returns to that state (views made since are lost).
        """
        backup = self.path.with_name(self.path.name + '.pre-views-v1')
        if backup.exists():
            return
        source = sqlite3.connect(self.path, timeout=15)
        try:
            if source.execute('PRAGMA user_version').fetchone()[0] != 1:
                return
            if source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='data_records'").fetchone() is None:
                return
            if source.execute('SELECT 1 FROM data_records LIMIT 1').fetchone() is None:
                return
            fd = os.open(backup, os.O_CREAT | os.O_RDWR | os.O_EXCL, 0o600)
            os.close(fd)
            target = sqlite3.connect(backup)
            try:
                source.backup(target)
            finally:
                target.close()
            from termx.private_files import protect_private_path
            protect_private_path(backup)
        finally:
            source.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=15000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _record(row) -> dict[str, Any] | None:
        if row is None:
            return None
        return {**json.loads(row['body']), 'id': row['id'], 'workspace_id': row['workspace_id'],
                'revision': row['revision'], 'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def _get(self, db, kind: str, identifier: str) -> dict[str, Any] | None:
        return self._record(db.execute('SELECT * FROM data_records WHERE kind=? AND id=?', (kind, identifier)).fetchone())

    def _list(self, db, kind: str, workspace_id: str) -> list[dict[str, Any]]:
        rows = db.execute('''SELECT * FROM data_records WHERE kind=? AND workspace_id=?
            AND COALESCE(json_extract(body, '$.archived'), 0)=0 ORDER BY created_at,id''', (kind, workspace_id)).fetchall()
        return [self._record(row) for row in rows]

    def _create(self, db, kind: str, workspace_id: str, actor_id: str,
                body: dict[str, Any], identifier: str | None = None) -> dict[str, Any]:
        _validate(kind, body)
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', kind):
            raise HTTPException(422, 'Invalid Data metadata kind')
        identifier = identifier or uuid.uuid4().hex
        now = timestamp()
        stored = {**body, 'created_by': actor_id}
        try:
            db.execute('INSERT INTO data_records VALUES (?,?,?,?,?,?,?)',
                       (kind, identifier, workspace_id, 1, now, now, _json(stored)))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, 'Data record already exists') from exc
        return self._get(db, kind, identifier)

    def create(self, kind: str, workspace_id: str, actor_id: str, body: dict[str, Any],
               identifier: str | None = None) -> dict[str, Any]:
        with self.transaction() as db:
            return self._create(db, kind, workspace_id, actor_id, body, identifier)

    def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        with self._read() as db:
            return self._get(db, kind, identifier)

    def list(self, kind: str, workspace_id: str) -> list[dict[str, Any]]:
        with self._read() as db:
            return self._list(db, kind, workspace_id)

    def _update(self, db, kind: str, identifier: str, changes: dict[str, Any], revision: int) -> dict[str, Any]:
        _validate(kind, changes)
        record = self._get(db, kind, identifier)
        if record is None:
            raise HTTPException(404, 'Data record not found')
        if isinstance(revision, bool) or not isinstance(revision, int) or record['revision'] != revision:
            raise HTTPException(409, 'This record changed; reload before saving')
        if record.get('archived') and changes.get('archived') is not False:
            raise HTTPException(409, 'This record is archived')
        body = {key: value for key, value in record.items() if key not in _RESERVED}
        body.update(changes)
        _validate(kind, body)
        changed = db.execute('''UPDATE data_records SET body=?,revision=revision+1,updated_at=?
            WHERE kind=? AND id=? AND revision=?''', (_json(body), timestamp(), kind, identifier, revision))
        if changed.rowcount != 1:
            raise HTTPException(409, 'This record changed; reload before saving')
        return self._get(db, kind, identifier)

    def update(self, kind: str, identifier: str, changes: dict[str, Any], revision: int) -> dict[str, Any]:
        with self.transaction() as db:
            return self._update(db, kind, identifier, changes, revision)

    def archive(self, kind: str, identifier: str, revision: int) -> dict[str, Any]:
        return self.update(kind, identifier, {'archived': True}, revision)

    def _audit(self, db, actor_id: str, wid: str, action: str, resource_id: str, detail=None) -> None:
        db.execute('INSERT INTO data_audit VALUES (?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, actor_id, wid, action, resource_id, _json(detail or {}), timestamp()))

    def audit(self, actor_id: str, wid: str, action: str, resource_id: str, detail=None) -> None:
        with self.transaction() as db:
            self._audit(db, actor_id, wid, action, resource_id, detail)

    def activity(self, wid: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._read() as db:
            rows = db.execute('''SELECT * FROM data_audit WHERE workspace_id=?
                ORDER BY created_at DESC,id DESC LIMIT ?''', (wid, limit)).fetchall()
        return [{**dict(row), 'detail': json.loads(row['detail'])} for row in rows]

    # --- saved views: revision history, references and run history ------------------------------
    def _revision_insert(self, db, *, view_id: str, revision: int, workspace_id: str, actor_id: str, actor_name: str,
                         action: str, name: str, snapshot: dict, changes: list) -> None:
        """Append one history row. Callers pass the record's new revision and call this inside the same
        transaction as the record write, so both commit or neither does."""
        db.execute('INSERT INTO data_view_revisions VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (view_id, revision, workspace_id, actor_id, actor_name, action, timestamp(), name,
                    _json(snapshot), _json(changes)))

    def revisions(self, workspace_id: str, view_id: str, limit: int, before: int | None) -> list[dict[str, Any]]:
        """Newest first; `before` is an exclusive revision bound. Returns up to `limit` + 1 rows."""
        with self._read() as db:
            rows = db.execute('''SELECT * FROM data_view_revisions WHERE workspace_id=? AND view_id=?
                AND (? IS NULL OR revision < ?) ORDER BY revision DESC LIMIT ?''',
                              (workspace_id, view_id, before, before, limit + 1)).fetchall()
        return [{**dict(row), 'snapshot': json.loads(row['snapshot']), 'changes': json.loads(row['changes'])} for row in rows]

    def _view_referrers(self, db, workspace_id: str, segment_id: str) -> list[dict[str, Any]]:
        """Non-archived views whose spec directly uses this segment."""
        rows = db.execute('''SELECT * FROM data_records r WHERE kind='view' AND workspace_id=?
            AND COALESCE(json_extract(body,'$.archived'),0)=0
            AND EXISTS (SELECT 1 FROM json_each(r.body,'$.references') WHERE value=?)
            ORDER BY created_at,id''', (workspace_id, segment_id)).fetchall()
        return [self._record(row) for row in rows]

    def view_jobs(self, workspace_id: str, view_id: str, *, actor_id: str | None, limit: int,
                  cursor: tuple[str, str] | None) -> list[dict[str, Any]]:
        """Run history rows (no result cells) from the durable jobs of one view, newest first."""
        sql = '''SELECT id, created_at, updated_at, json_extract(body,'$.actor_id') AS actor_id,
                json_extract(body,'$.actor_name') AS actor_name, json_extract(body,'$.view_revision') AS view_revision,
                json_extract(body,'$.mode') AS mode, json_extract(body,'$.status') AS status,
                json_extract(body,'$.duration_ms') AS duration_ms, json_extract(body,'$.error') AS error,
                json_extract(body,'$.result.row_count') AS row_count, json_extract(body,'$.result.truncated') AS truncated
            FROM data_records INDEXED BY data_job_view WHERE kind='job' AND json_extract(body,'$.view_id')=? AND workspace_id=?'''
        args: list[Any] = [view_id, workspace_id]
        if actor_id is not None:
            sql += " AND json_extract(body,'$.actor_id')=?"
            args.append(actor_id)
        if cursor is not None:
            sql += ' AND (created_at < ? OR (created_at = ? AND id < ?))'
            args += [cursor[0], cursor[0], cursor[1]]
        sql += ' ORDER BY created_at DESC, id DESC LIMIT ?'
        args.append(limit + 1)
        with self._read() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]
