"""Bounded device lifetimes; tightening is durable and never resurrects a SID."""
from __future__ import annotations

import math
from time import time

from termx.audit import log_event


class SessionPolicy:
    def __init__(self, identity, *, idle_default: int, absolute_default: int):
        self.identity = identity
        with identity._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS session_policy (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                idle_ttl_seconds INTEGER NOT NULL, absolute_ttl_seconds INTEGER NOT NULL,
                revision INTEGER NOT NULL)''')
            db.execute('INSERT OR IGNORE INTO session_policy VALUES (1,?,?,1)',
                       (idle_default, absolute_default))
            if 'idle_ttl_seconds' not in {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}:
                db.execute(f'ALTER TABLE sessions ADD COLUMN idle_ttl_seconds INTEGER NOT NULL DEFAULT {idle_default}')

    @staticmethod
    def read(db):
        row = db.execute('SELECT idle_ttl_seconds,absolute_ttl_seconds,revision FROM session_policy WHERE singleton=1').fetchone()
        if row is None:
            raise RuntimeError('Device session policy unavailable')
        return dict(row)

    def inventory(self):
        with self.identity._db() as db:
            return {**self.read(db), 'access_ttl_seconds': 300}

    def deadline(self, db, session):
        policy = self.read(db)
        return min(session['expires'], session['created'] + policy['absolute_ttl_seconds'],
                   session['last_seen'] + min(session['idle_ttl_seconds'], policy['idle_ttl_seconds']))

    def absolute_remaining(self, db, session_id):
        row = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        policy = self.read(db)
        return max(0, math.ceil(min(row['expires'], row['created'] + policy['absolute_ttl_seconds']) - time()))

    def update(self, *, revision, idle_ttl_seconds, absolute_ttl_seconds, actor_id):
        values = (revision, idle_ttl_seconds, absolute_ttl_seconds)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError('Session lifetimes and revision require integers')
        # Idle must exceed the fixed five-minute access/refresh cadence.
        if not 600 <= idle_ttl_seconds <= 30 * 86400 or not idle_ttl_seconds <= absolute_ttl_seconds <= 365 * 86400:
            raise ValueError('Idle must be 10 minutes–30 days; absolute must cover idle and be at most 365 days')
        with self.identity._db() as db:
            old = self.read(db)
            if old['revision'] != revision:
                raise ValueError('Session policy changed; reload before saving')
            now = time()
            # Existing sessions keep the smallest limits ever assigned. New
            # sessions alone adopt later increases; expired sessions stay dead.
            db.execute('''UPDATE sessions SET
                idle_ttl_seconds=MIN(idle_ttl_seconds,?),
                expires=MIN(expires,created+?)''',
                (min(idle_ttl_seconds,old['idle_ttl_seconds']),min(absolute_ttl_seconds,old['absolute_ttl_seconds'])))
            result = db.execute('''UPDATE sessions SET revoked=1 WHERE revoked=0
                AND (expires<=? OR last_seen+idle_ttl_seconds<=?)''', (now, now))
            db.execute('UPDATE session_policy SET idle_ttl_seconds=?,absolute_ttl_seconds=?,revision=revision+1 WHERE singleton=1',
                       (idle_ttl_seconds, absolute_ttl_seconds))
            policy = self.read(db)
        log_event('auth_session_policy_changed', actor_id=actor_id, **policy, expired_sessions=result.rowcount)
        return {**policy, 'access_ttl_seconds': 300, 'expired_sessions': result.rowcount}
