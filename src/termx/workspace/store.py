"""Small durable workspace aggregates alongside the existing AgentStore ledger."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from time import time
from typing import Any


class WorkspaceStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS records (
            kind TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL,
            project TEXT, revision INTEGER NOT NULL DEFAULT 1, body TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY(kind,id));
          CREATE INDEX IF NOT EXISTS records_scope ON records(kind,owner,project);
          CREATE TABLE IF NOT EXISTS requests (
            owner TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
            state TEXT NOT NULL, result TEXT, created REAL NOT NULL,
            PRIMARY KEY(owner,key));
          CREATE TABLE IF NOT EXISTS audit (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
            kind TEXT NOT NULL, resource TEXT NOT NULL, event TEXT NOT NULL,
            payload TEXT NOT NULL, created REAL NOT NULL);
        ''')
        self.db.commit()
        if os.name == 'posix':
            path.chmod(0o600)

    def close(self):
        with self.lock:
            self.db.close()

    @staticmethod
    def unpack(row) -> dict[str, Any]:
        return {**json.loads(row['body']), 'id': row['id'], 'owner': row['owner'],
                'project_id': row['project'], 'revision': row['revision'],
                'created_at': row['created'], 'updated_at': row['updated']}

    def create(self, kind: str, owner: str, body: dict, project: str | None = None,
               identifier: str | None = None):
        identifier = identifier or uuid.uuid4().hex
        now = time()
        with self.lock:
            self.db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?)',
                            (kind, identifier, owner, project, 1, json.dumps(body), now, now))
            self.db.commit()
            return self.get(kind, identifier)

    def get(self, kind: str, identifier: str):
        with self.lock:
            row = self.db.execute('SELECT * FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
            return self.unpack(row) if row else None

    def list(self, kind: str, owner: str | None = None, project: str | None = None):
        sql, args = 'SELECT * FROM records WHERE kind=?', [kind]
        if owner is not None:
            sql += ' AND owner=?'; args.append(owner)
        if project is not None:
            sql += ' AND project=?'; args.append(project)
        with self.lock:
            rows = self.db.execute(sql + ' ORDER BY updated DESC,id', args).fetchall()
            return [self.unpack(row) for row in rows]

    def update(self, kind: str, identifier: str, body: dict, revision: int | None = None):
        with self.lock:
            current = self.get(kind, identifier)
            if current is None:
                raise KeyError(identifier)
            if revision is not None and current['revision'] != revision:
                raise Conflict('Resource changed; reload before saving')
            clean = {k: v for k, v in body.items() if k not in {'id', 'owner', 'project_id', 'revision', 'created_at', 'updated_at'}}
            if kind == 'conversation':
                clean = {k:v for k,v in clean.items() if k in {'engine','cwd','draft_text','draft_context','scroll','linked_from','transfer','extension_ids','workflow','group_id','scratch','runner_id','runner_credential_ref','run_limits','worktree_id','worktree_digest','custom_agent_id','custom_agent_revision'}}
            self.db.execute('UPDATE records SET body=?,revision=revision+1,updated=? WHERE kind=? AND id=?',
                            (json.dumps(clean), time(), kind, identifier))
            self.db.commit()
            return self.get(kind, identifier)

    def reserve_schedule_run(self, schedule, now, next_due):
        """Atomically claim one due occurrence and its shared goal/grant budget."""
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                current = self.get('schedule', schedule['id'])
                grant = self.get('delegation', schedule['grant_id'])
                goal = self.get('goal', schedule['goal_id'])
                if not current or not current['enabled'] or current['revision'] != schedule['revision']:
                    self.db.rollback()
                    return None
                if (not grant or not goal or grant['revoked'] or grant['expires_at'] <= now
                        or grant['used_runs'] >= grant['max_runs'] or goal['status'] != 'active'
                        or goal['runs_started'] >= goal['max_runs']):
                    raise Conflict('Delegated authority or shared run budget exhausted')
                identifier = uuid.uuid4().hex
                body = {'schedule_id':schedule['id'],'goal_id':goal['id'],
                        'status':'dispatching','due':schedule['next_run'],'task_id':None}
                self.db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?)',
                    ('schedule_run', identifier, schedule['owner'], schedule.get('project_id'),
                     1, json.dumps(body), now, now))
                for kind, record, changes in (
                    ('schedule',current,{'next_run':next_due,'last_run':identifier}),
                    ('delegation',grant,{'used_runs':grant['used_runs']+1}),
                    ('goal',goal,{'runs_started':goal['runs_started']+1})):
                    clean = {k:v for k,v in {**record,**changes}.items()
                             if k not in {'id','owner','project_id','revision','created_at','updated_at'}}
                    self.db.execute('UPDATE records SET body=?,revision=revision+1,updated=? WHERE kind=? AND id=?',
                                    (json.dumps(clean),now,kind,record['id']))
                self.db.commit()
                return self.get('schedule_run',identifier)
            except BaseException:
                self.db.rollback()
                raise

    def delete(self, kind, identifier):
        with self.lock:
            self.db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, identifier))
            self.db.commit()

    def log(self, owner, kind, identifier, event, payload=None):
        # Callers pass bounded metadata, never model context, argv or credentials.
        with self.lock:
            self.db.execute('INSERT INTO audit(owner,kind,resource,event,payload,created) VALUES(?,?,?,?,?,?)',
                            (owner, kind, identifier, event, json.dumps(payload or {}), time()))
            self.db.commit()

    def logs(self, owner: str, limit=100):
        with self.lock:
            return [{**dict(row), 'payload': json.loads(row['payload'])} for row in self.db.execute(
                'SELECT * FROM audit WHERE owner=? ORDER BY seq DESC LIMIT ?', (owner, max(1,min(limit,500))))]


class Conflict(ValueError):
    pass
