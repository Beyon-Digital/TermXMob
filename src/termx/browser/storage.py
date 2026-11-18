"""Protected durable browser/review records; profile content stays out of audit."""
from __future__ import annotations
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from time import time

# Saved sites need indexed ordering (frecency), per-site child rows and bounded counters, so they are real tables
# beside the generic JSON `records`. `rank` is log2(frecency)+last_visited/half-life: ordering by it equals ordering
# by the time-decayed score at any moment, so a plain index serves the sidebar query. `site_visits` is a bounded
# visit counter for origins nobody saved yet (it feeds suggestions); it holds origins and counts, never URLs with
# queries or page content.
SITES_SCHEMA='''CREATE TABLE IF NOT EXISTS sites(id TEXT PRIMARY KEY,principal_id TEXT NOT NULL,project_id TEXT NOT NULL DEFAULT '',origin TEXT NOT NULL,start_url TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',profile_id TEXT NOT NULL,open_mode TEXT NOT NULL DEFAULT 'tab' CHECK(open_mode IN ('tab','app')),pinned INTEGER NOT NULL DEFAULT 1,position INTEGER NOT NULL DEFAULT 0,visit_count INTEGER NOT NULL DEFAULT 0,last_visited REAL,frecency REAL NOT NULL DEFAULT 0,rank REAL NOT NULL DEFAULT 0,tab_id TEXT,created REAL NOT NULL,updated REAL NOT NULL,UNIQUE(principal_id,profile_id,origin));
CREATE INDEX IF NOT EXISTS sites_order ON sites(principal_id,pinned DESC,rank DESC);
CREATE INDEX IF NOT EXISTS sites_profile ON sites(profile_id);
CREATE TABLE IF NOT EXISTS site_notes(id TEXT PRIMARY KEY,site_id TEXT NOT NULL,author TEXT NOT NULL CHECK(author IN ('user','agent')),content TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS site_notes_site ON site_notes(site_id,created);
CREATE TABLE IF NOT EXISTS site_visits(principal_id TEXT NOT NULL,profile_id TEXT NOT NULL,origin TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',project_id TEXT NOT NULL DEFAULT '',visit_count INTEGER NOT NULL DEFAULT 0,last_visited REAL NOT NULL,frecency REAL NOT NULL DEFAULT 0,rank REAL NOT NULL DEFAULT 0,PRIMARY KEY(principal_id,profile_id,origin));
CREATE INDEX IF NOT EXISTS site_visits_rank ON site_visits(principal_id,rank DESC);'''

class Records:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        self.root = root
        self.path = root / 'browser.sqlite3'
        self._lock = threading.RLock()
        with self.transaction() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,created REAL NOT NULL,body TEXT NOT NULL);
'''+SITES_SCHEMA)
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
