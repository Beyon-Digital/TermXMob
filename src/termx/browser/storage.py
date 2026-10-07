"""Protected durable browser/review records; profile content stays out of audit."""
from __future__ import annotations
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from time import time

class Records:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        self.root = root
        self.path = root / 'browser.sqlite3'
        self._lock = threading.RLock()
        with self.transaction() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL NOT NULL,body TEXT NOT NULL);''')
        os.chmod(self.path, 0o600)
    @contextmanager
    def transaction(self):
        with self._lock:
            db = sqlite3.connect(self.path)
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
    def get(self, kind: str, id: str):
        with self.transaction() as db:
            row = db.execute('SELECT body FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
            return json.loads(row[0]) if row else None
    def put(self, kind: str, id: str, value: dict):
        with self.transaction() as db:
            db.execute('INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',(kind,id,json.dumps(value)))
        return value
    def delete(self, kind: str, id: str):
        with self.transaction() as db:
            db.execute('DELETE FROM records WHERE kind=? AND id=?',(kind,id))
    def list(self, kind: str):
        with self.transaction() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM records WHERE kind=? ORDER BY id',(kind,))]
    def audit(self, **metadata):
        # No raw arguments, page content, URLs with query, provider output or exception.
        allowed = {'principal_id','session_id','tab_id','action_id','rule_id','tool_id','decision','reason','grant_id','outcome','origin','revision','provider_id','model','latency_ms'}
        body = {k:v for k,v in metadata.items() if k in allowed}
        with self.transaction() as db:
            db.execute('INSERT INTO events(created,body) VALUES(?,?)',(time(),json.dumps(body)))
    def events(self, limit=100):
        with self.transaction() as db:
            return [{'id':r[0],'created_at':r[1],**json.loads(r[2])} for r in db.execute('SELECT id,created,body FROM events ORDER BY id DESC LIMIT ?',(min(limit,1000),))]
