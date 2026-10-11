"""Opt-in Expo push delivery. Credentials never leave the host or enter this store.

Registrations bind to a live managed session or the legacy administrator
passcode fingerprint. Authority is rechecked before each delivery. Payloads
contain generic text and opaque task identifiers, never prompts or results.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import sqlite3
import threading
from pathlib import Path
from time import time

import httpx
from fastapi import HTTPException
from termx.private_files import protect_private_path

EXPO = 'https://exp.host/--/api/v2/push/'
TOKEN = re.compile(r'^(?:ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{8,200}\]$')
EVENTS = {'task.completed': 'Task completed', 'task.failed': 'Task needs attention', 'approval.requested': 'Approval needed'}


class PushDelivery:
    def __init__(self, state, path: Path):
        self.state = state
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        protect_private_path(path)
        self.lock = threading.RLock()
        self.worker = None
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS push_devices(token TEXT PRIMARY KEY, owner TEXT NOT NULL, host TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS push_jobs(id TEXT PRIMARY KEY, token TEXT NOT NULL, task TEXT NOT NULL, title TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, created REAL NOT NULL, receipt TEXT);
        ''')
        state.agent._listeners.add(self.on_event)

    def owner(self, secret):
        identity = self.state.identity.resolve(secret)
        if identity:
            return 'session:' + identity.session_id
        if not self.state.identity.configured and secret and self.state.auth.passcode and hmac.compare_digest(secret, self.state.auth.passcode):
            return 'legacy:' + hashlib.sha256(secret.encode()).hexdigest()
        raise HTTPException(403, 'Push requires a managed session or the host administrator passcode. Reconnect with one of these credentials.')

    def register(self, secret, token, host):
        if not self.state.auth.allows(secret, 'agent-view'):
            raise HTTPException(403, 'Agent visibility is required')
        owner = self.owner(secret)
        if not TOKEN.fullmatch(token) or not isinstance(host, str) or len(host) > 300 or not re.fullmatch(r'https?://[^\s/@?#]+:\d+', host):
            raise HTTPException(400, 'Invalid push registration')
        with self.lock, self.db:
            existing = self.db.execute('SELECT owner FROM push_devices WHERE token=?', (token,)).fetchone()
            if existing and existing[0] != owner:
                old = self.state.identity.session_by_id(existing[0][8:]) if existing[0].startswith('session:') else None
                new = self.state.identity.session_by_id(owner[8:]) if owner.startswith('session:') else None
                if old and (not new or old.principal.id != new.principal.id):
                    raise HTTPException(409, 'This token belongs to another active session')
                self._remove(token)
            count = self.db.execute('SELECT count(*) FROM push_devices WHERE owner=?', (owner,)).fetchone()[0]
            if not existing and count >= 12:
                raise HTTPException(409, 'Device notification limit reached')
            self.db.execute('INSERT OR REPLACE INTO push_devices VALUES(?,?,?,?)', (token, owner, host, time()))
        return {'enabled': True}

    def unregister(self, secret, token):
        owner = self.owner(secret)
        with self.lock, self.db:
            row = self.db.execute('SELECT owner FROM push_devices WHERE token=?', (token,)).fetchone()
            if row and row[0] == owner:
                self._remove(token)
        return {'enabled': False}

    def _remove(self, token):
        self.db.execute('DELETE FROM push_jobs WHERE token=?', (token,))
        self.db.execute('DELETE FROM push_devices WHERE token=?', (token,))

    def allowed(self, owner, task):
        if owner.startswith('session:'):
            session = self.state.identity.session_by_id(owner[8:])
            if not session:
                return False
            try:
                self.state.authorization.require_principal(session.principal, 'agent-view', resource_kind='task', resource_id=task)
                return True
            except HTTPException:
                return False
        secret = self.state.auth.passcode
        return bool(not self.state.identity.configured and secret and hmac.compare_digest(owner, 'legacy:' + hashlib.sha256(secret.encode()).hexdigest()))

    def on_event(self, task_id, event):
        title = EVENTS.get(event.get('type'))
        if not title:
            return
        # A replay uses the same sequence and therefore cannot enqueue twice.
        sequence = event.get('sequence')
        if sequence is None:
            return
        now = time()
        with self.lock, self.db:
            self.db.execute('DELETE FROM push_jobs WHERE created<?', (now - 86400,))
            for token, owner in self.db.execute('SELECT token,owner FROM push_devices').fetchall():
                if not self.allowed(owner, task_id):
                    continue
                key = hashlib.sha256(f'{token}:{task_id}:{sequence}'.encode()).hexdigest()
                self.db.execute('INSERT OR IGNORE INTO push_jobs(id,token,task,title,due,created) VALUES(?,?,?,?,?,?)', (key, token, task_id, title, now, now))

    async def drain(self, client):
        with self.lock:
            rows = self.db.execute('SELECT j.id,j.token,j.task,j.title,j.attempts,j.receipt,d.owner,d.host FROM push_jobs j JOIN push_devices d ON d.token=j.token WHERE j.due<=? AND j.attempts<6 ORDER BY j.due LIMIT 30', (time(),)).fetchall()
        for identifier, token, task, title, attempts, receipt, owner, host in rows:
            if not self.allowed(owner, task):
                with self.lock, self.db:
                    self.db.execute('DELETE FROM push_jobs WHERE id=?', (identifier,))
                continue
            try:
                if receipt:
                    response = await client.post(EXPO + 'getReceipts', json={'ids': [receipt]})
                else:
                    response = await client.post(EXPO + 'send', json={'to': token, 'title': title, 'body': 'Open TermX to review this task.', 'sound': 'default', 'channelId': 'default', 'data': {'host': host, 'taskId': task, 'eventId': identifier}})
                response.raise_for_status()
                result = response.json().get('data', {})
                if receipt:
                    result = result.get(receipt, {})
                if isinstance(result, list):
                    result = result[0] if result else {}
                if result.get('details', {}).get('error') == 'DeviceNotRegistered':
                    with self.lock, self.db: self._remove(token)
                    continue
                if result.get('status') != 'ok':
                    raise ValueError('Push not acknowledged')
                with self.lock, self.db:
                    if not receipt and result.get('id'):
                        self.db.execute('UPDATE push_jobs SET receipt=?,due=?,attempts=0 WHERE id=?', (result['id'], time() + 900, identifier))
                    else:
                        # Retain a one-day tombstone so replayed events stay deduplicated.
                        self.db.execute('UPDATE push_jobs SET due=?,attempts=6 WHERE id=?', (time() + 86400, identifier))
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                with self.lock, self.db:
                    self.db.execute('UPDATE push_jobs SET attempts=attempts+1,due=? WHERE id=?', (time() + min(900, 15 * 2 ** attempts), identifier))

    def start(self):
        async def run():
            async with httpx.AsyncClient(timeout=15) as client:
                while True:
                    await self.drain(client)
                    await asyncio.sleep(15)
        self.worker = asyncio.create_task(run())

    async def close(self):
        self.state.agent._listeners.discard(self.on_event)
        if self.worker:
            self.worker.cancel()
            try: await self.worker
            except asyncio.CancelledError: pass
        with self.lock: self.db.close()
