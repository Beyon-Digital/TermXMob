"""Durable, actor-scoped source-read jobs and the Data Studio runtime API."""
from __future__ import annotations

import asyncio
import csv
from dataclasses import replace
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from termx.config import config_dir
from termx.data.connectors import (PREVIEW_ROWS, NetworkSQLConnector, SQLiteConnector,
    engine_spec, engines, quote_identifier, validate_read)
from termx.data.store import identifier_for

TERMINAL = {'completed', 'failed', 'cancelled', 'unknown'}


def _revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(422, 'A current revision is required.')
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise HTTPException(422, 'Name must contain 1–200 characters.')
    return value.strip()


def _safe_error(error):
    if isinstance(error, HTTPException):
        return str(error.detail)[:500]
    if isinstance(error, sqlite3.Error):
        return 'SQLite read failed: ' + str(error)[:300]
    if isinstance(error, (InterruptedError, asyncio.CancelledError)):
        return 'Query cancelled.'
    # Driver exceptions can contain credentials, raw URLs or source SQL. Their
    # repr/message never reaches jobs, audit, logs, or the HTTP response.
    return 'The source read failed. Check the connection, TLS trust, role permissions and query, then run it again.'


class DataRuntime:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.boot_id = uuid.uuid4().hex
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._active: dict[str, Any] = {}
        self._job_lock = threading.RLock()
        self._closed = False

    engines = staticmethod(engines)
    quote_identifier = staticmethod(quote_identifier)

    def _protected(self):
        state = self.service.state
        roots = [Path(config_dir()), Path(self.service.directory)]
        files = [Path(self.store.path)]
        for obj_name in ('identity', 'agent_store', 'store', 'tokens'):
            obj = getattr(state, obj_name, None)
            path = getattr(obj, 'path', None)
            if path:
                files.append(Path(path))
                if obj_name == 'store':
                    # The whole configured host state directory is private,
                    # including stores introduced after this adapter shipped.
                    roots.append(Path(path).parent)
        delivery = getattr(state, 'delivery', None)
        if getattr(delivery, 'directory', None):
            roots.append(Path(delivery.directory).parent)
        return [p.expanduser().resolve() for p in roots], [p.expanduser().resolve() for p in files]

    def validate_path(self, value: Any, expected: dict | None = None) -> tuple[Path, dict]:
        if not isinstance(value, str) or not value or '\x00' in value or value.startswith('file:'):
            raise HTTPException(422, 'Enter an absolute database file path on the host.')
        source = Path(value).expanduser()
        if not source.is_absolute():
            raise HTTPException(422, 'The database path must be absolute on the host.')
        try:
            source = source.resolve(strict=True)
            info = source.stat()
        except (OSError, RuntimeError):
            raise HTTPException(422, 'The database file does not exist or cannot be resolved.') from None
        if not source.is_file():
            raise HTTPException(422, 'The source must be an existing regular database file.')
        roots, files = self._protected()
        if any(source == root or source.is_relative_to(root) for root in roots):
            raise HTTPException(403, 'TermX host state and internal stores cannot be enrolled as business sources.')
        for internal in files:
            try:
                if source == internal or source.samefile(internal):
                    raise HTTPException(403, 'Internal TermX stores cannot be enrolled as business sources.')
            except OSError:
                pass
        signature = {'device': info.st_dev, 'inode': info.st_ino}
        if expected and signature != expected:
            raise HTTPException(409, 'The source file identity changed. Update the connection and test it again.')
        return source, signature

    def _file_authority(self, actor, path: Path) -> dict:
        """Bind an explicitly delegated host-file read to live host authority."""
        state = self.service.state
        current = state.identity.current_principal(actor)
        if current is None or 'files-read' not in current.scopes:
            raise HTTPException(403, 'Host file-read authority is required to enroll this local source.')
        projects = getattr(state, 'projects', None)
        matching = []
        if projects is not None:
            for project in projects.projects():
                try:
                    root = Path(project['path']).expanduser().resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if path.is_relative_to(root):
                    matching.append((len(root.parts), project['id']))
        project_id = max(matching, key=lambda item: item[0])[1] if matching else None
        authorization = getattr(state, 'authorization', None)
        if authorization is not None:
            authorization.require_principal(current, 'files-read', project_id=project_id)
        # Minimal isolated service fixtures have no AuthorizationService. They
        # still require an actual live managed principal with files-read scope.
        return {'principal_id': current.id, 'session_id': current.authority_session_id,
                'project_id': project_id, 'policy_version': current.policy_version}

    def assert_connection_source(self, connection, wid):
        if connection.get('engine') != 'sqlite':
            return
        path, _ = self.validate_path(connection['config']['path'], connection.get('_file_identity'))
        saved = connection.get('_file_authority') or {}
        principal = self.service.state.identity.principal_by_id(connection.get('created_by'))
        if principal is None:
            raise HTTPException(403, 'The local source creator no longer has host file-read authority.')
        if saved.get('session_id'):
            principal = replace(principal, authority_session_id=saved['session_id'])
        current = self._file_authority(principal, path)
        if saved and current != saved:
            raise HTTPException(403, 'The source creator file authority changed. Its creator must update the profile before using this source again.')
        # Source sharing cannot survive removal of the delegating member.
        self.service.require(principal, wid)

    def _config(self, engine: str, value: dict) -> tuple[dict, dict | None]:
        engine_spec(engine)
        if not isinstance(value, dict):
            raise HTTPException(422, 'Connection configuration must be an object.')
        allowed = {'path'} if engine == 'sqlite' else {'host', 'port', 'database', 'username', 'tls'}
        if set(value) - allowed:
            raise HTTPException(422, 'Unsupported connection setting. Drivers and URLs are server managed.')
        if engine == 'sqlite':
            path, signature = self.validate_path(value.get('path'))
            return {'path': str(path)}, signature
        config = {}
        for key in ('host', 'database', 'username'):
            item = value.get(key)
            if not isinstance(item, str) or not item.strip() or len(item) > 255 or '://' in item or any(ord(c) < 32 for c in item):
                raise HTTPException(422, 'Host, database and username are required plain text values.')
            if key == 'host' and (any(c in item for c in '/?@#') or '://' in item):
                raise HTTPException(422, 'Enter a host name or IP address, not a URL or DSN.')
            config[key] = item.strip()
        port = value.get('port') or (5432 if engine == 'postgresql' else 3306)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise HTTPException(422, 'The port must be between 1 and 65535.')
        if value.get('tls', True) is not True:
            raise HTTPException(422, 'Network adapters require certificate-verified TLS.')
        return {**config, 'port': port, 'tls': True}, None

    def create_connection(self, actor, wid: str, body: dict) -> dict:
        self.service.require(actor, wid, write=True)
        if set(body) - {'name', 'engine', 'config', 'password', 'shared_read'}:
            raise HTTPException(422, 'Unknown connection property.')
        engine = body.get('engine', '')
        config, signature = self._config(engine, body.get('config', {}))
        file_authority = self._file_authority(actor, Path(config['path'])) if engine == 'sqlite' else None
        password = body.get('password')
        if password is not None and not isinstance(password, str):
            raise HTTPException(422, 'Password must be text.')
        if engine == 'sqlite' and password:
            raise HTTPException(422, 'SQLite password encryption is not supported by this adapter.')
        shared = body.get('shared_read', False)
        if not isinstance(shared, bool):
            raise HTTPException(422, 'Shared read must be a boolean.')
        credential = 'data_' + uuid.uuid4().hex if password else None
        if credential:
            try:
                self.service.state.credentials.set(credential, password)
            except Exception:
                raise HTTPException(503, 'Secure credential storage could not save the source password.') from None
        try:
            record = self.store.create('connection', wid, actor.id, {
                'name': _name(body.get('name')), 'engine': engine, 'config': config,
                'created_by': actor.id, 'shared_read': shared, 'credential_ref': credential,
                'credential_generation': 1 if credential else 0, 'source_generation': 1,
                '_file_identity': signature, '_file_authority': file_authority, 'read_policy': {'verified': engine == 'sqlite'},
            })
        except BaseException:
            if credential:
                self.service.state.credentials.delete(credential)
            raise
        self.store.audit(actor.id, wid, 'connection.created', record['id'])
        return self.service.public(record)

    def _connection_owner(self, actor, wid, cid):
        self.service.require(actor, wid, write=True)
        record = self.store.get('connection', cid)
        if not record or record['workspace_id'] != wid or record.get('archived'):
            raise HTTPException(404, 'Data connection not found.')
        if record.get('created_by') != actor.id:
            raise HTTPException(403, 'Only the connection creator can change its profile or credentials.')
        return record

    def update_connection(self, actor, wid: str, cid: str, body: dict) -> dict:
        old = self._connection_owner(actor, wid, cid)
        revision = _revision(body.get('revision'))
        if revision != old['revision']:
            raise HTTPException(409, 'The connection changed. Reload its current revision.')
        if set(body) - {'revision', 'name', 'engine', 'config', 'password', 'shared_read'}:
            raise HTTPException(422, 'Unknown connection property.')
        engine = body.get('engine', old['engine'])
        config, signature = self._config(engine, body.get('config', old['config']))
        file_authority = self._file_authority(actor, Path(config['path'])) if engine == 'sqlite' else None
        shared = body.get('shared_read', old.get('shared_read', False))
        if not isinstance(shared, bool):
            raise HTTPException(422, 'Shared read must be a boolean.')
        password = body.get('password')
        if password is not None and not isinstance(password, str):
            raise HTTPException(422, 'Password must be text.')
        if password and engine == 'sqlite':
            raise HTTPException(422, 'SQLite password encryption is not supported by this adapter.')
        changes = {'name': _name(body.get('name', old['name'])), 'engine': engine, 'config': config,
                   'shared_read': shared, '_file_identity': signature, '_file_authority': file_authority, 'source_generation': old.get('source_generation', 1) + 1,
                   'read_policy': {'verified': engine == 'sqlite'}}
        credential = 'data_' + uuid.uuid4().hex if password else None
        if credential:
            try:
                self.service.state.credentials.set(credential, password)
            except Exception:
                raise HTTPException(503, 'Secure credential storage could not save the source password.') from None
            changes.update(credential_ref=credential, credential_generation=old.get('credential_generation', 0) + 1)
        elif engine == 'sqlite' and old.get('credential_ref'):
            changes.update(credential_ref=None, credential_generation=old.get('credential_generation', 0) + 1)
        try:
            record = self.store.update('connection', cid, changes, revision)
        except BaseException:
            if credential:
                self.service.state.credentials.delete(credential)
            raise
        if 'credential_ref' in changes and old.get('credential_ref'):
            self.service.state.credentials.delete(old['credential_ref'])
        self.store.audit(actor.id, wid, 'connection.updated', cid)
        return self.service.public(record)

    def archive_connection(self, actor, wid: str, cid: str, revision: int) -> dict:
        old = self._connection_owner(actor, wid, cid)
        result = self.store.archive('connection', cid, _revision(revision))
        # Archival is reversible metadata state. Preserve this profile's opaque
        # vault secret; explicit credential rotation is a separate operation.
        self.store.audit(actor.id, wid, 'connection.archived', cid)
        return self.service.public(result)

    def _source(self, actor, wid, definition, from_report=False):
        self.service.require(actor, wid, write=not from_report)
        if not isinstance(definition, dict):
            raise HTTPException(422, 'Query definition must be an object.')
        context = definition.get('report_context')
        if from_report:
            if not isinstance(context, dict) or not context.get('report_id'):
                raise HTTPException(403, 'A live authorized report is required for a report read.')
            self.service.bi.authorize_runtime(actor, wid, context)
        elif context is not None:
            raise HTTPException(422, 'Report authority is server managed.')
        connection = self.service.require_connection(actor, wid, definition.get('connection_id', ''), report=from_report)
        engine_spec(connection['engine'])
        self.assert_connection_source(connection, wid)
        return connection

    @staticmethod
    def fingerprint(connection):
        return {key: connection.get(key) for key in ('id', 'revision', 'source_generation', 'credential_generation')}

    def _connector(self, connection, cancel=None, active=None):
        if connection['engine'] == 'sqlite':
            path, _ = self.validate_path(connection['config']['path'], connection.get('_file_identity'))
            return SQLiteConnector(path, cancel, active)
        credential = connection.get('credential_ref')
        password = self.service.state.credentials.get(credential) if credential else None
        if not password:
            raise HTTPException(409, 'An explicitly stored source password is required. Update this profile; host password files and ambient credentials are never inherited.')
        return NetworkSQLConnector(connection['engine'], connection['config'], password, cancel, active)

    async def test(self, actor, wid: str, cid: str) -> dict:
        self.service.require(actor, wid, write=True)
        connection = self.service.require_connection(actor, wid, cid)
        self.assert_connection_source(connection, wid)
        try:
            result = await asyncio.to_thread(self._connector(connection).test)
        except Exception as error:
            return {'ok': False, 'engine': connection['engine'], 'message': _safe_error(error), 'read_available': False}
        current = self.service.require_connection(actor, wid, cid)
        self.service.require(actor, wid, write=True)
        self.assert_connection_source(current, wid)
        if self.fingerprint(current) != self.fingerprint(connection):
            raise HTTPException(409, 'The connection changed during its test. Test the current profile again.')
        # Testing a shared source does not permit changing its credentials. This
        # receipt only records an independently verified, current read policy.
        self.store.update('connection', cid, {'read_policy': {'verified': True,
            'source_generation': current.get('source_generation'), 'credential_generation': current.get('credential_generation'),
            'protection': result.get('protection'), 'qualified': connection['engine'] == 'sqlite'}}, current['revision'])
        self.store.audit(actor.id, wid, 'connection.tested', cid, {'ok': True})
        return result

    async def schema(self, actor, wid: str, cid: str) -> dict:
        self.service.require(actor, wid, write=True)
        connection = self.service.require_connection(actor, wid, cid)
        self.assert_connection_source(connection, wid)
        try:
            result = await asyncio.to_thread(self._connector(connection).schema)
        except Exception as error:
            if isinstance(error, HTTPException):
                raise
            raise HTTPException(422, _safe_error(error)) from None
        current = self.service.require_connection(actor, wid, cid)
        self.service.require(actor, wid, write=True)
        self.assert_connection_source(current, wid)
        if self.fingerprint(current) != self.fingerprint(connection):
            raise HTTPException(409, 'The connection changed while reading its schema.')
        return result

    def _read_definition(self, definition, connection):
        if not isinstance(definition, dict) or set(definition) - {'connection_id', 'sql', 'parameters', 'report_context'}:
            raise HTTPException(422, 'Invalid query definition.')
        sql, parameters = validate_read(definition.get('sql'), connection['engine'], definition.get('parameters'))
        policy = connection.get('read_policy') or {}
        if connection['engine'] != 'sqlite' and (not policy.get('verified')
            or policy.get('source_generation') != connection.get('source_generation')
            or policy.get('credential_generation') != connection.get('credential_generation')):
            raise HTTPException(409, 'Test this source first to verify its current read-only role and session.')
        return {'connection_id': connection['id'], 'sql': sql, 'parameters': parameters,
                **({'report_context': definition['report_context']} if definition.get('report_context') else {})}

    async def execute_read(self, actor, wid: str, definition: dict, *, from_report=False,
                           max_rows=PREVIEW_ROWS, cancel_event=None, _active=None) -> dict:
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 0 <= max_rows <= PREVIEW_ROWS:
            raise HTTPException(422, 'The result preview bound is invalid.')
        connection = self._source(actor, wid, definition, from_report)
        query = self._read_definition(definition, connection)
        result = await asyncio.to_thread(self._connector(connection, cancel_event, _active).execute,
                                        query['sql'], query['parameters'], max_rows)
        current = self._source(actor, wid, definition, from_report)
        if self.fingerprint(current) != self.fingerprint(connection):
            raise HTTPException(409, 'Source authority or credentials changed while the query was running. Its result was discarded.')
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('Query cancelled.')
        return result

    def _public_job(self, record):
        keys = ('id', 'status', 'connection_id', 'result', 'error', 'created_at', 'updated_at',
                'report_context', 'cancel_requested', 'cancellation', 'preview_limit')
        # Result column/cell names are user data; do not recursively remove a
        # legitimate column named "password" from an explicitly authorized read.
        return {key: record[key] for key in keys if key in record}

    def _authorize_job(self, actor, wid, job):
        self.service.require(actor, wid)
        if not job or job['workspace_id'] != wid or job.get('actor_id') != actor.id:
            raise HTTPException(404, 'Query job not found.')
        self._source(actor, wid, job['_definition'], job.get('_from_report', False))
        current = self.service.require_connection(actor, wid, job['connection_id'], report=job.get('_from_report', False))
        if self.fingerprint(current) != job.get('_source_fingerprint'):
            raise HTTPException(409, 'This job belongs to an older source or credential policy. Run it again.')

    async def run(self, actor, wid: str, definition: dict, request_id: str, *, from_report=False) -> dict:
        if self._closed:
            raise HTTPException(503, 'The query runtime is shutting down.')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise HTTPException(422, 'A request_id containing 1–200 characters is required.')
        connection = self._source(actor, wid, definition, from_report)
        query = self._read_definition(definition, connection)
        payload = {'definition': query, 'from_report': bool(from_report)}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        jid = identifier_for('query-run', wid, actor.id, request_id)
        with self._job_lock:
            existing = self.store.get('job', jid)
            if existing:
                self._authorize_job(actor, wid, existing)
                if existing.get('_request_digest') != digest:
                    raise HTTPException(409, 'This request_id was already used with a different query.')
                return self.get_job(actor, wid, jid)
            body = {'actor_id': actor.id, 'connection_id': connection['id'], 'status': 'queued',
                    'preview_limit': PREVIEW_ROWS, '_definition': query, '_from_report': bool(from_report),
                    '_source_fingerprint': self.fingerprint(connection), '_request_digest': digest,
                    '_boot_id': self.boot_id}
            if query.get('report_context'):
                body['report_context'] = query['report_context']
            try:
                job = self.store.create('job', wid, actor.id, body, identifier=jid)
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                existing = self.store.get('job', jid)
                self._authorize_job(actor, wid, existing)
                if existing.get('_request_digest') != digest:
                    raise HTTPException(409, 'This request_id was already used with a different query.')
                return self.get_job(actor, wid, jid)
            cancel = self._cancel[jid] = threading.Event()
            self._tasks[jid] = asyncio.create_task(self._worker(actor, wid, jid, cancel))
        self.store.audit(actor.id, wid, 'query.queued', jid, {'connection_id': connection['id']})
        return self._public_job(job)

    def _update_job(self, jid, changes):
        with self._job_lock:
            record = self.store.get('job', jid)
            if record:
                return self.store.update('job', jid, changes, record['revision'])

    async def _worker(self, actor, wid, jid, cancel):
        try:
            job = self.store.get('job', jid)
            self._authorize_job(actor, wid, job)
            if cancel.is_set():
                raise InterruptedError('Query cancelled before dispatch.')
            self._update_job(jid, {'status': 'running'})
            result = await self.execute_read(actor, wid, job['_definition'], from_report=job['_from_report'],
                cancel_event=cancel, _active=lambda db: self._set_active(jid, db))
            self._authorize_job(actor, wid, self.store.get('job', jid))
            if cancel.is_set():
                raise InterruptedError('Query cancelled.')
            self._update_job(jid, {'status': 'completed', 'result': result})
            self.store.audit(actor.id, wid, 'query.completed', jid, {'rows': result['row_count'], 'truncated': result['truncated']})
        except BaseException as error:
            cancelled = cancel.is_set() or isinstance(error, (InterruptedError, asyncio.CancelledError))
            self._update_job(jid, {'status': 'cancelled' if cancelled else 'failed',
                                   'error': None if cancelled else _safe_error(error), 'result': None})
        finally:
            self._active.pop(jid, None)
            self._cancel.pop(jid, None)
            self._tasks.pop(jid, None)

    def _set_active(self, jid, db):
        with self._job_lock:
            if db is None:
                self._active.pop(jid, None)
            else:
                self._active[jid] = db
                if self._cancel.get(jid) and self._cancel[jid].is_set():
                    db.interrupt()

    def get_job(self, actor, wid: str, jid: str) -> dict:
        record = self.store.get('job', jid)
        self._authorize_job(actor, wid, record)
        if record['status'] not in TERMINAL and record.get('_boot_id') != self.boot_id:
            record = self._update_job(jid, {'status': 'unknown', 'error': 'The host restarted before the source outcome was recorded. This query was not replayed.', 'result': None})
        return self._public_job(record)

    def cancel(self, actor, wid: str, jid: str) -> dict:
        record = self.store.get('job', jid)
        self._authorize_job(actor, wid, record)
        if record['status'] in TERMINAL:
            return self._public_job(record)
        event = self._cancel.get(jid)
        if event:
            event.set()
        with self._job_lock:
            db = self._active.get(jid)
            if db is not None:
                db.interrupt()
        engine = self.store.get('connection', record['connection_id'])['engine']
        changes = {'cancel_requested': True, 'cancellation': ('SQLite interrupt requested.' if engine == 'sqlite'
                   else 'Source termination is not qualified. Cancellation is pending until the driver returns; results will be discarded.')}
        # A queued task will observe cancellation before opening the source. An
        # active network operation remains running until its actual outcome.
        updated = self._update_job(jid, changes)
        return self._public_job(updated)

    def export_csv(self, actor, wid: str, jid: str) -> tuple[str, bool]:
        job = self.get_job(actor, wid, jid)
        if job['status'] != 'completed' or not job.get('result'):
            raise HTTPException(409, 'Only a completed query result can be exported.')
        result = job['result']
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        def safe(cell):
            if isinstance(cell, dict):
                cell = json.dumps(cell, ensure_ascii=False)
            if isinstance(cell, str) and cell.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')):
                return "'" + cell
            return cell
        writer.writerow([safe(column['name']) for column in result['columns']])
        writer.writerows([[safe(cell) for cell in row] for row in result['rows']])
        # Recheck after creating the export as well as before reading the result.
        self._authorize_job(actor, wid, self.store.get('job', jid))
        return stream.getvalue(), bool(result['truncated'])

    def create_query(self, actor, wid: str, body: dict) -> dict:
        self.service.require(actor, wid, write=True)
        if set(body) - {'name', 'connection_id', 'sql', 'parameters'}:
            raise HTTPException(422, 'Unknown query property.')
        connection = self.service.require_connection(actor, wid, body.get('connection_id', ''))
        sql, parameters = validate_read(body.get('sql'), connection['engine'], body.get('parameters'))
        record = self.store.create('query', wid, actor.id, {'name': _name(body.get('name')),
            'connection_id': connection['id'], 'sql': sql, 'parameters': parameters, 'created_by': actor.id})
        self.store.audit(actor.id, wid, 'query.saved', record['id'])
        return self.service.public(record)

    def update_query(self, actor, wid: str, qid: str, body: dict) -> dict:
        self.service.require(actor, wid, write=True)
        old = self.store.get('query', qid)
        if not old or old['workspace_id'] != wid or old.get('archived'):
            raise HTTPException(404, 'Saved query not found.')
        self.service.require_connection(actor, wid, old['connection_id'])
        if set(body) - {'revision', 'name', 'connection_id', 'sql', 'parameters'}:
            raise HTTPException(422, 'Unknown query property.')
        connection = self.service.require_connection(actor, wid, body.get('connection_id', old['connection_id']))
        sql, parameters = validate_read(body.get('sql', old['sql']), connection['engine'], body.get('parameters', old.get('parameters')))
        record = self.store.update('query', qid, {'name': _name(body.get('name', old['name'])),
            'connection_id': connection['id'], 'sql': sql, 'parameters': parameters}, _revision(body.get('revision')))
        self.store.audit(actor.id, wid, 'query.updated', qid)
        return self.service.public(record)

    def archive_query(self, actor, wid: str, qid: str, revision: int) -> dict:
        self.service.require(actor, wid, write=True)
        old = self.store.get('query', qid)
        if not old or old['workspace_id'] != wid or old.get('archived'):
            raise HTTPException(404, 'Saved query not found.')
        self.service.require_connection(actor, wid, old['connection_id'])
        result = self.store.archive('query', qid, _revision(revision))
        self.store.audit(actor.id, wid, 'query.archived', qid)
        return self.service.public(result)

    async def run_saved(self, actor, wid: str, qid: str, request_id: str):
        self.service.require(actor, wid, write=True)
        query = self.store.get('query', qid)
        if not query or query['workspace_id'] != wid or query.get('archived'):
            raise HTTPException(404, 'Saved query not found.')
        return await self.run(actor, wid, {key: query[key] for key in ('connection_id', 'sql', 'parameters') if key in query}, request_id)

    async def close(self):
        self._closed = True
        for event in list(self._cancel.values()):
            event.set()
        with self._job_lock:
            for db in list(self._active.values()):
                db.interrupt()
        tasks = list(self._tasks.values())
        if tasks:
            # SQLite interrupts; network cancellation is honestly pending until
            # the source returns. No arbitrary task deadline or silent replay.
            await asyncio.gather(*tasks, return_exceptions=True)


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ConnectionCreate(_Input):
    name: str
    engine: str
    config: dict
    password: str | None = None
    shared_read: bool = False


class ConnectionUpdate(_Input):
    revision: int
    name: str | None = None
    engine: str | None = None
    config: dict | None = None
    password: str | None = None
    shared_read: bool | None = None


class QueryCreate(_Input):
    name: str
    connection_id: str
    sql: str
    parameters: dict = Field(default_factory=dict)


class QueryUpdate(_Input):
    revision: int
    name: str | None = None
    connection_id: str | None = None
    sql: str | None = None
    parameters: dict | None = None


class RunInput(_Input):
    request_id: str
    connection_id: str
    sql: str
    parameters: dict = Field(default_factory=dict)


class RevisionInput(_Input):
    revision: int = Field(ge=1)


class RequestId(_Input):
    request_id: str


def build_runtime_router(service, actor):
    router = APIRouter(prefix='/api/data')
    runtime = service.runtime

    @router.get('/engines')
    def engine_list(request: Request):
        service.live_actor(actor(request))
        return runtime.engines()

    @router.get('/workspaces/{wid}/connections')
    def connections(wid: str, request: Request):
        return service.connections(actor(request), wid)

    @router.post('/workspaces/{wid}/connections')
    async def create_connection(wid: str, body: ConnectionCreate, request: Request):
        return await asyncio.to_thread(runtime.create_connection, actor(request), wid, body.model_dump(exclude_none=True))

    @router.patch('/workspaces/{wid}/connections/{cid}')
    async def update_connection(wid: str, cid: str, body: ConnectionUpdate, request: Request):
        return await asyncio.to_thread(runtime.update_connection, actor(request), wid, cid, body.model_dump(exclude_unset=True))

    @router.delete('/workspaces/{wid}/connections/{cid}')
    async def delete_connection(wid: str, cid: str, request: Request, revision: int | None = Query(default=None, ge=1),
                                body: RevisionInput | None = Body(default=None)):
        expected = body.revision if body else revision
        if expected is None or (body and revision is not None and body.revision != revision):
            raise HTTPException(422, 'Provide the current revision.')
        return await asyncio.to_thread(runtime.archive_connection, actor(request), wid, cid, expected)

    @router.post('/workspaces/{wid}/connections/{cid}/test')
    async def test_connection(wid: str, cid: str, request: Request):
        return await runtime.test(actor(request), wid, cid)

    @router.get('/workspaces/{wid}/connections/{cid}/schema')
    async def schema(wid: str, cid: str, request: Request):
        return await runtime.schema(actor(request), wid, cid)

    @router.get('/workspaces/{wid}/queries')
    def queries(wid: str, request: Request):
        return service.queries(actor(request), wid)

    @router.post('/workspaces/{wid}/queries')
    def create_query(wid: str, body: QueryCreate, request: Request):
        return runtime.create_query(actor(request), wid, body.model_dump())

    @router.patch('/workspaces/{wid}/queries/{qid}')
    def update_query(wid: str, qid: str, body: QueryUpdate, request: Request):
        return runtime.update_query(actor(request), wid, qid, body.model_dump(exclude_unset=True))

    @router.delete('/workspaces/{wid}/queries/{qid}')
    def delete_query(wid: str, qid: str, request: Request, revision: int | None = Query(default=None, ge=1),
                     body: RevisionInput | None = Body(default=None)):
        expected = body.revision if body else revision
        if expected is None or (body and revision is not None and body.revision != revision):
            raise HTTPException(422, 'Provide the current revision.')
        return runtime.archive_query(actor(request), wid, qid, expected)

    @router.post('/workspaces/{wid}/query-runs')
    async def run_query(wid: str, body: RunInput, request: Request):
        return await runtime.run(actor(request), wid, body.model_dump(exclude={'request_id'}), body.request_id)

    @router.post('/workspaces/{wid}/queries/{qid}/run')
    async def run_saved(wid: str, qid: str, body: RequestId, request: Request):
        return await runtime.run_saved(actor(request), wid, qid, body.request_id)

    @router.get('/workspaces/{wid}/jobs/{jid}')
    def job(wid: str, jid: str, request: Request):
        return runtime.get_job(actor(request), wid, jid)

    @router.post('/workspaces/{wid}/jobs/{jid}/cancel')
    def cancel(wid: str, jid: str, request: Request):
        return runtime.cancel(actor(request), wid, jid)

    @router.get('/workspaces/{wid}/jobs/{jid}/export.csv')
    def export(wid: str, jid: str, request: Request):
        data, truncated = runtime.export_csv(actor(request), wid, jid)
        return Response(data, media_type='text/csv; charset=utf-8', headers={
            'Content-Disposition': 'attachment; filename="query-preview.csv"',
            'X-Data-Truncated': str(truncated).lower(), 'X-Data-Export-Scope': 'query-preview',
            'X-Data-CSV-Safety': 'formula-prefixed-text', 'Cache-Control': 'no-store'})

    return router
