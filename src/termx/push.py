"""Opt-in Expo push delivery. Credentials never leave the host or enter this store.

Registrations bind to a live managed session or the legacy administrator
passcode fingerprint. Authority is rechecked before each delivery. Payloads
contain generic text and opaque task identifiers, never prompts or results.

Besides the task events (``task.completed``, ``task.failed``, ``approval.requested``; their
payload is unchanged for the mobile app), ``enqueue`` carries three more kinds, each with
only a host-written title and a status line, never prompt text or terminal output:
``cli.session.attention`` (a CLI session started waiting for input or a permission decision),
``orchestrator.report`` (the main agent's task graph finished) and ``host.recovered`` (the host
restarted and re-adopted or lost work). Each goes only to registered devices whose principal
may see the resource, is rate-limited per resource and capped per device, and is dropped
if that authority is gone when it would be sent.
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
TASK_BODY = 'Open TermX to review this task.'
# kind -> (scope the receiving principal needs, ledger kind of the resource it is checked on, seconds between pushes per resource)
KINDS = {
    'cli.session.attention': ('terminal-view', 'terminal', 60),
    'orchestrator.report': ('agent-view', 'conversation', 30),
    'host.recovered': ('host-admin', None, 300),
}
MAX_TEXT = 140  # a push is a nudge: titles and bodies are short, host-written status lines
DEVICE_HOURLY_CAP = 20  # new-kind pushes per device per hour; task pushes keep their existing behaviour


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
        # ``task`` holds the resource id for every kind. Older stores gain the new columns in place; their rows are task jobs.
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(push_jobs)')}
        for name in ('kind', 'body', 'audience'):
            if name not in columns:
                self.db.execute(f"ALTER TABLE push_jobs ADD COLUMN {name} TEXT" + (" NOT NULL DEFAULT 'task'" if name == 'kind' else ''))
        self.db.commit()
        self.attached = False
        self.closed = False
        self._attention = {}
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

    def authorized(self, owner, kind, resource, audience):
        """May the device owned by ``owner`` hear about this resource right now? Checked at enqueue and again at send."""
        if kind == 'task':
            return self.allowed(owner, resource)
        scope, ledger_kind, _ = KINDS[kind]
        if owner.startswith('session:'):
            session = self.state.identity.session_by_id(owner[8:])
            if not session or (audience not in (None, '*') and session.principal.id != audience):
                return False
            try:
                if ledger_kind:
                    self.state.authorization.require_principal(session.principal, scope, resource_kind=ledger_kind, resource_id=resource)
                else:
                    self.state.authorization.require_principal(session.principal, scope)
                return True
            except HTTPException:
                return False
        # The compatibility passcode is the single host owner: it hears about host-owned records and host-wide events only.
        return audience in (None, '*', 'host') and self.allowed(owner, resource)

    def enqueue(self, kind, resource_id, title, body='', *, audience=None, key=''):
        """Queue one push of a non-task kind for every opted-in device that may see ``resource_id``.

        ``title`` and ``body`` are host-written status lines (clipped; never prompt text or terminal output).
        ``audience`` is the owning principal id, or ``host`` for the compatibility owner: only that owner's devices
        qualify. A resource is pushed at most once per kind per interval, a device at most ``DEVICE_HOURLY_CAP``
        times an hour, and a repeated ``key`` within an interval never queues twice. Returns devices queued."""
        if kind not in KINDS:
            raise ValueError('Unknown push kind')
        interval = KINDS[kind][2]
        title, body = str(title)[:MAX_TEXT], str(body)[:MAX_TEXT]
        now, queued = time(), 0
        with self.lock, self.db:
            self.db.execute('DELETE FROM push_jobs WHERE created<?', (now - 86400,))
            for token, owner in self.db.execute('SELECT token,owner FROM push_devices').fetchall():
                if not self.authorized(owner, kind, resource_id, audience):
                    continue
                if self.db.execute('SELECT 1 FROM push_jobs WHERE token=? AND kind=? AND task=? AND created>?', (token, kind, resource_id, now - interval)).fetchone():
                    continue
                if self.db.execute("SELECT count(*) FROM push_jobs WHERE token=? AND kind<>'task' AND created>?", (token, now - 3600)).fetchone()[0] >= DEVICE_HOURLY_CAP:
                    continue
                identifier = hashlib.sha256(f'{token}:{kind}:{resource_id}:{key}:{int(now // interval)}'.encode()).hexdigest()
                queued += self.db.execute('INSERT OR IGNORE INTO push_jobs(id,token,task,title,due,created,kind,body,audience) VALUES(?,?,?,?,?,?,?,?,?)',
                                          (identifier, token, resource_id, title, now, now, kind, body, audience)).rowcount
        return queued

    # -- sources of the new kinds ------------------------------------------------

    def attach(self):
        """Subscribe to the host's own event sources. Idempotent; called when the worker starts."""
        if self.attached:
            return
        self.attached = True
        cli = getattr(self.state, 'cli_sessions', None)
        if cli is not None:
            cli.add_listener(self.on_cli_event)
        graph = getattr(getattr(getattr(self.state, 'workspace', None), 'orchestrator', None), 'graph', None)
        if graph is not None:
            graph.add_listener(self.on_graph_event)

    def on_cli_event(self, kind, session):
        """A CLI session flipped to waiting for input or a permission decision: status only, never the prompt or the screen."""
        if self.closed:
            return
        waiting = bool(session.get('attention'))
        known = self._attention.get(session['id'])
        self._attention[session['id']] = waiting
        while len(self._attention) > 512:
            self._attention.pop(next(iter(self._attention)))
        if not waiting or known:
            return  # only the flip to attention notifies, not each repeat of it
        record = self.state.cli_sessions.store.get('cli_session', session['id']) or {}
        label = str(session.get('label') or 'A CLI session')[:40]
        approval = session.get('status') == 'needs_approval'
        self.enqueue('cli.session.attention', record.get('pty_session_id') or session['id'],
                     'CLI session needs approval' if approval else 'CLI session is waiting for you',
                     f'{label} is waiting for a permission decision.' if approval else f'{label} is waiting for input.',
                     audience=record.get('owner') or 'host', key=str(session.get('status')))

    def on_graph_event(self, kind, info):
        """The main agent's task graph finished: counts only, never node titles, prompts or results."""
        if kind != 'orchestrator.report' or self.closed:
            return
        counts = info.get('counts') or {}
        parts = [f'{counts[name]} {name}' for name in ('completed', 'failed', 'cancelled') if counts.get(name)]
        self.enqueue('orchestrator.report', info['session_id'], 'Main agent finished its plan', (', '.join(parts) + '.') if parts else 'Open TermX to review it.',
                     audience=info['owner'], key=str(info.get('signature', '')))

    async def drain(self, client):
        with self.lock:
            rows = self.db.execute('SELECT j.id,j.token,j.task,j.title,j.attempts,j.receipt,d.owner,d.host,j.kind,j.body,j.audience FROM push_jobs j JOIN push_devices d ON d.token=j.token WHERE j.due<=? AND j.attempts<6 ORDER BY j.due LIMIT 30', (time(),)).fetchall()
        for identifier, token, task, title, attempts, receipt, owner, host, kind, body, audience in rows:
            if not self.authorized(owner, kind or 'task', task, audience):
                with self.lock, self.db:
                    self.db.execute('DELETE FROM push_jobs WHERE id=?', (identifier,))
                continue
            try:
                if receipt:
                    response = await client.post(EXPO + 'getReceipts', json={'ids': [receipt]})
                else:
                    data = {'host': host, 'taskId': task, 'eventId': identifier} if (kind or 'task') == 'task' else {
                        'host': host, 'kind': kind, 'resourceId': task, 'eventId': identifier}
                    response = await client.post(EXPO + 'send', json={'to': token, 'title': title, 'body': body or TASK_BODY, 'sound': 'default', 'channelId': 'default', 'data': data})
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
        self.attach()

        async def run():
            async with httpx.AsyncClient(timeout=15) as client:
                while True:
                    await self.drain(client)
                    await asyncio.sleep(15)
        self.worker = asyncio.create_task(run())

    async def close(self):
        self.closed = True
        self.state.agent._listeners.discard(self.on_event)
        if self.worker:
            self.worker.cancel()
            try: await self.worker
            except asyncio.CancelledError: pass
        with self.lock: self.db.close()
