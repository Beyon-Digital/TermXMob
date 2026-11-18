"""Usage history: what this owner has done with TermX, kept so an agent can answer "what do I use most".

Events are small redacted facts (a finished task, a visited site, a CLI session) in the workspace store's
`usage_events` table, with an FTS5 index over the subject. Other slices feed it through `record()`; the agent reads it
through the `usage_query` tool. Nothing here stores a raw prompt, page content or credential: `record()` redacts and
bounds every field, and a caller passes a short display line (a title, a host), never a transcript.

Every query is for one owner. A project argument limits what is visible: events with no project (browsing, CLI
sessions) plus that project's own.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from time import time
from typing import Any

from termx.agent.policy import redact
from termx.workspace.store import fts_match

_KIND = re.compile(r'^[a-z][a-z0-9_.-]{0,31}$')
_USERINFO = re.compile(r'(?i)(://)[^/\s@]+@')
_SECRET_PARAM = re.compile(r'(?i)([?&](?:[a-z_]*(?:token|key|secret|password|passwd|auth|sig|session|code)[a-z_]*)=)[^&\s#]*')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]+')
SUBJECT_MAX = 200
DETAIL_MAX = 1000
RETENTION_DAYS = 180
ALL = ...  # `project_id` default: no project filter (the owner's whole history)

_SCHEMA = '''
  CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT, kind TEXT NOT NULL,
    subject TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
  CREATE INDEX IF NOT EXISTS usage_events_owner ON usage_events(owner, kind, at);
'''
_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS usage_fts USING fts5("
        "subject, event_id UNINDEXED, owner UNINDEXED, tokenize='porter unicode61')")


def clean_text(value: Any, limit: int) -> str:
    """One redacted, single-line, bounded string: what is safe to keep and to hand back to a model."""
    text = _CONTROL.sub(' ', str(value or ''))
    text = _SECRET_PARAM.sub(r'\1[redacted]', _USERINFO.sub(r'\1', redact(text)))
    return ' '.join(text.split())[:limit]


def _detail(value: Any) -> str:
    if not value:
        return ''
    if isinstance(value, dict):
        compact = {clean_text(key, 40): (clean_text(item, 200) if isinstance(item, str) else item)
                   for key, item in list(value.items())[:20]
                   if isinstance(item, (str, int, float, bool)) or item is None}
        return json.dumps(compact, ensure_ascii=False, sort_keys=True)[:DETAIL_MAX]
    return clean_text(value, DETAIL_MAX)


def parse_since(value: Any, *, now: float | None = None) -> float | None:
    """An epoch number, an ISO date or time, or a relative span ("30m", "24h", "7d", "2w") as an epoch cutoff."""
    if value in (None, ''):
        return None
    now = time() if now is None else now
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().lower()
    span = re.fullmatch(r'(\d{1,5})\s*([mhdw])', text)
    if span:
        return now - int(span.group(1)) * {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}[span.group(2)]
    try:
        return float(text)
    except ValueError:
        pass
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('since must be a number of seconds, an ISO date, or a span like 7d, 24h or 30m') from None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


class UsageHistory:
    def __init__(self, store, *, retention_days: int = RETENTION_DAYS) -> None:
        self.store = store
        self.retention = retention_days * 86400
        self.lock = store.lock
        with self.lock:
            store.db.executescript(_SCHEMA)
            self.fts = self._ensure_fts()
            self.prune()

    # -- index ----------------------------------------------------------------------------------------------------

    def _ensure_fts(self) -> bool:
        db = self.store.db
        try:
            db.execute(_FTS)
            indexed = db.execute('SELECT COUNT(*) FROM usage_fts').fetchone()[0]
            events = db.execute('SELECT COUNT(*) FROM usage_events').fetchone()[0]
            if indexed != events:
                db.execute('DELETE FROM usage_fts')
                db.execute('INSERT INTO usage_fts(subject,event_id,owner) SELECT subject,id,owner FROM usage_events')
            db.commit()
            return True
        except sqlite3.OperationalError:
            return False

    def prune(self, *, now: float | None = None) -> int:
        """Drop events past the retention window. Returns how many went."""
        cutoff = (time() if now is None else now) - self.retention
        with self.lock:
            db = self.store.db
            if self.fts:
                db.execute('DELETE FROM usage_fts WHERE event_id IN (SELECT id FROM usage_events WHERE at<?)', (cutoff,))
            gone = db.execute('DELETE FROM usage_events WHERE at<?', (cutoff,)).rowcount
            db.commit()
            return gone

    # -- writing ----------------------------------------------------------------------------------------------------

    def record(self, owner: str, kind: str, subject: str, *, detail: Any = None, project_id: str | None = None,
               at: float | None = None, key: str | None = None) -> str | None:
        """Keep one event. `subject` is a short display line (a task title, a host); it and `detail` (text or a flat
        dict) are redacted and bounded here, so a caller cannot store a secret by accident.

        `key` makes the call idempotent: recording the same key twice keeps one event. Returns the event id, or None
        when the subject is empty or the event was already recorded."""
        if not owner or not _KIND.match(str(kind or '')):
            raise ValueError('A usage event needs an owner and a kind such as task, site or cli_session')
        text = clean_text(subject, SUBJECT_MAX)
        if not text:
            return None
        identifier = f'{kind}:{key}' if key else uuid.uuid4().hex
        with self.lock:
            db = self.store.db
            added = db.execute('INSERT OR IGNORE INTO usage_events(id,owner,project_id,kind,subject,detail,at) '
                               'VALUES(?,?,?,?,?,?,?)',
                               (identifier, owner, project_id or None, kind, text, _detail(detail),
                                time() if at is None else float(at))).rowcount
            if added and self.fts:
                try:
                    db.execute('INSERT INTO usage_fts(subject,event_id,owner) VALUES(?,?,?)', (text, identifier, owner))
                except sqlite3.OperationalError:
                    self.fts = False
            db.commit()
        return identifier if added else None

    def clear(self, owner: str, kind: str | None = None) -> int:
        """Forget this owner's history (all of it, or one kind). Returns how many events went."""
        where, args = 'owner=?', [owner]
        if kind:
            where += ' AND kind=?'
            args.append(kind)
        with self.lock:
            db = self.store.db
            if self.fts:
                db.execute(f'DELETE FROM usage_fts WHERE event_id IN (SELECT id FROM usage_events WHERE {where})', args)
            gone = db.execute(f'DELETE FROM usage_events WHERE {where}', args).rowcount
            db.commit()
            return gone

    # -- reading ----------------------------------------------------------------------------------------------------

    @staticmethod
    def _scope(owner, kind, since, project_id, alias=''):
        where, args = [f'{alias}owner=?'], [owner]
        if kind:
            where.append(f'{alias}kind=?')
            args.append(kind)
        if since is not None:
            where.append(f'{alias}at>=?')
            args.append(since)
        if project_id is not ALL:
            if project_id:
                where.append(f'({alias}project_id IS NULL OR {alias}project_id=?)')
                args.append(project_id)
            else:
                where.append(f'{alias}project_id IS NULL')
        return ' AND '.join(where), args

    @staticmethod
    def _event(row) -> dict[str, Any]:
        item = {'id': row['id'], 'kind': row['kind'], 'subject': row['subject'], 'at': row['at']}
        if row['detail']:
            item['detail'] = row['detail']
        return item

    def top(self, owner: str, *, kind: str | None = None, since: Any = None, limit: int = 10,
            project_id: Any = ALL) -> list[dict[str, Any]]:
        """Most-used subjects, by event count and then recency. One row per (kind, subject)."""
        where, args = self._scope(owner, kind, parse_since(since), project_id)
        with self.lock:
            rows = self.store.db.execute(
                f'SELECT kind,subject,COUNT(*) AS count,MAX(at) AS last_at FROM usage_events WHERE {where} '
                'GROUP BY kind,subject ORDER BY count DESC,last_at DESC,subject LIMIT ?',
                (*args, _limit(limit))).fetchall()
        return [{'kind': r['kind'], 'subject': r['subject'], 'count': r['count'], 'last_at': r['last_at']} for r in rows]

    def recent(self, owner: str, *, kind: str | None = None, since: Any = None, limit: int = 10,
               project_id: Any = ALL) -> list[dict[str, Any]]:
        where, args = self._scope(owner, kind, parse_since(since), project_id)
        with self.lock:
            rows = self.store.db.execute(
                f'SELECT * FROM usage_events WHERE {where} ORDER BY at DESC,id LIMIT ?', (*args, _limit(limit))).fetchall()
        return [self._event(r) for r in rows]

    def search(self, owner: str, query: str, *, kind: str | None = None, since: Any = None, limit: int = 10,
               project_id: Any = ALL) -> list[dict[str, Any]]:
        """Events whose subject matches the words of `query`, best first (bm25). Falls back to a substring match
        when FTS5 is unavailable or finds nothing."""
        cutoff = parse_since(since)
        text = ' '.join(str(query or '').split())
        if not text:
            return self.recent(owner, kind=kind, since=cutoff, limit=limit, project_id=project_id)
        where, args = self._scope(owner, kind, cutoff, project_id, 'e.')
        size = _limit(limit)
        if self.fts:
            for match in (fts_match(text), fts_match(text, any_term=True)):
                if match is None:
                    break
                try:
                    with self.lock:
                        rows = self.store.db.execute(
                            'SELECT e.* FROM usage_fts f JOIN usage_events e ON e.id=f.event_id '
                            f'WHERE usage_fts MATCH ? AND {where} ORDER BY bm25(usage_fts),e.at DESC LIMIT ?',
                            (match, *args, size)).fetchall()
                except sqlite3.OperationalError:
                    break
                if rows:
                    return [self._event(r) for r in rows]
        escaped = text.lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with self.lock:
            rows = self.store.db.execute(
                f"SELECT e.* FROM usage_events e WHERE {where} AND lower(e.subject) LIKE ? ESCAPE '\\' "
                'ORDER BY e.at DESC LIMIT ?', (*args, f'%{escaped}%', size)).fetchall()
        return [self._event(r) for r in rows]


def _limit(value: Any) -> int:
    try:
        return max(1, min(int(value), 50))
    except (TypeError, ValueError):
        return 10
