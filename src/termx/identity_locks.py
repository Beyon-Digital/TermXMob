"""Lock is distinct from revocation: retain enrolled jobs and require reauthentication."""
from __future__ import annotations

import secrets
from time import time

from termx.audit import log_event
from termx.identity import AuthenticationError,SessionIdentity,SessionCredentials,_digest


class SessionLocks:
    def __init__(self,identity):
        self.identity=identity
        with identity._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS unlock_oidc (
                state_hash TEXT PRIMARY KEY, adapter_id TEXT NOT NULL,
                session_id TEXT NOT NULL, refresh_digest TEXT NOT NULL,
                policy_version INTEGER NOT NULL, expires REAL NOT NULL)''')

    def refresh_identity(self, raw):
        if not raw or len(raw)>256:return None
        with self.identity._db() as db:
            row=db.execute('''SELECT s.* FROM sessions s JOIN refresh_tokens r ON r.session_id=s.id
                              WHERE r.digest=? AND r.consumed=0''',(_digest(raw),)).fetchone()
            principal=self.identity._live_principal(db,row,allow_locked=True)
        return SessionIdentity(principal,row['id'],row['expires']) if principal else None

    def is_locked(self,identity):
        with self.identity._db() as db:
            row=db.execute('SELECT locked FROM sessions WHERE id=? AND principal_id=?',(identity.session_id,identity.principal.id)).fetchone()
        return bool(row and row['locked'])

    def lock(self,identity):
        with self.identity._db() as db:
            session=db.execute('SELECT * FROM sessions WHERE id=?',(identity.session_id,)).fetchone()
            live=self.identity._live_principal(db,session)
            if not live or live.id!=identity.principal.id:raise AuthenticationError('Current session required')
            db.execute('UPDATE sessions SET locked=1 WHERE id=?',(identity.session_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='session_proofs'").fetchone():
                db.execute('UPDATE session_proofs SET consumed=1 WHERE sponsor=?',(identity.session_id,))
        log_event('auth_session_locked',principal_id=identity.principal.id,session_id=identity.session_id)

    async def unlock(self,raw,method,evidence,*,peer):
        actor=self.refresh_identity(raw)
        if not actor or not self.is_locked(actor):raise AuthenticationError('Locked session proof required')
        verified,principal=await self.identity.verify_login(method,evidence,peer=peer)
        with self.identity._db() as db:
            credentials=self._finish(db,_digest(raw),actor.session_id,principal,method,verified)
        log_event("auth_session_unlocked",principal_id=principal.id,session_id=actor.session_id,adapter_id=method)
        return credentials

    def _finish(self,db,digest,sid,verified,method,evidence):
        row=db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        principal=self.identity._live_principal(db,row,allow_locked=True)
        proof=db.execute('SELECT * FROM refresh_tokens WHERE digest=? AND session_id=? AND consumed=0',(digest,sid)).fetchone()
        if not proof or not principal or not row['locked'] or principal.id!=verified.id:
            raise AuthenticationError('Unlock identity or device proof denied')
        mapping=db.execute('SELECT principal_id FROM identities WHERE issuer=? AND subject=?',(evidence.issuer,evidence.subject)).fetchone()
        if not mapping or mapping[0]!=principal.id:raise AuthenticationError('Canonical identity mapping changed')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='adapter_status'").fetchone():
            status=db.execute('SELECT enabled FROM adapter_status WHERE adapter_id=?',(method,)).fetchone()
            if status and not status[0]:raise AuthenticationError('Authentication method disabled')
        raw,csrf=secrets.token_urlsafe(48),secrets.token_urlsafe(32)
        db.execute('UPDATE refresh_tokens SET consumed=1 WHERE session_id=?',(sid,))
        db.execute('INSERT INTO refresh_tokens VALUES (?,?,0)',(_digest(raw),sid))
        db.execute('UPDATE sessions SET locked=0,auth_generation=auth_generation+1,last_seen=?,csrf_hash=?,adapter_id=?,strength=? WHERE id=?',
                   (time(),_digest(csrf),method,evidence.strength,sid))
        token=self.identity._issue(db,principal,sid)
        return SessionCredentials(token,raw,csrf,sid)

    def bind_oidc(self,actor,raw,method,state):
        with self.identity._db() as db:
            row=db.execute('SELECT * FROM sessions WHERE id=?',(actor.session_id,)).fetchone()
            principal=self.identity._live_principal(db,row,allow_locked=True)
            if not principal or not row['locked'] or principal.id!=actor.principal.id:
                raise AuthenticationError('Current locked device proof required')
            db.execute('DELETE FROM unlock_oidc WHERE expires<=?',(time(),))
            db.execute('INSERT INTO unlock_oidc VALUES (?,?,?,?,?,?)',
                       (_digest(state),method,actor.session_id,_digest(raw),principal.policy_version,time()+300))

    def oidc_pending(self,method,state):
        with self.identity._db() as db:
            return db.execute('SELECT * FROM unlock_oidc WHERE state_hash=? AND adapter_id=?',(_digest(state),method)).fetchone()

    async def finish_oidc(self,method,state,evidence,*,peer):
        pending=self.oidc_pending(method,state)
        if not pending:raise AuthenticationError('Unlock flow unavailable')
        verified,principal=await self.identity.verify_login(method,evidence,peer=peer)
        with self.identity._db() as db:
            proof=db.execute('SELECT * FROM unlock_oidc WHERE state_hash=?',(_digest(state),)).fetchone()
            if not proof or proof['expires']<=time() or principal.policy_version!=proof['policy_version']:
                raise AuthenticationError('Unlock flow expired or policy changed')
            credentials=self._finish(db,proof['refresh_digest'],proof['session_id'],principal,method,verified)
            db.execute('DELETE FROM unlock_oidc WHERE state_hash=?',(_digest(state),))
        log_event("auth_session_unlocked",principal_id=principal.id,session_id=credentials.session_id,adapter_id=method)
        return credentials


async def protect_surfaces(state, actor):
    """Retain privacy while revoking only this enrolled session's grants."""
    import asyncio
    browser=getattr(state,'browser',None)
    if browser:
        def private_tabs():
            for row in browser.records.list('tab'):
                if row.get('principal_id') != actor.principal.id or row.get('state') in {'closed','crashed'}:continue
                grant=browser.records.get('grant',row.get('grant_id')) if row.get('grant_id') else None
                sid=(grant or {}).get('session_id') or row.get('session_id')
                if sid==actor.session_id:
                    browser.takeover(row['id'],actor.principal.id,private=True,session_id=actor.session_id,policy_version=actor.principal.policy_version)
        await asyncio.to_thread(private_tabs)
    capture=getattr(state,'window_recording',None)
    if capture:
        def private_windows():
            for row in capture.records.list('window-capture'):
                if row.get('principal_id')==actor.principal.id and row.get('session_id')==actor.session_id and not row.get('stopped'):
                    try:capture.configure(row['id'],actor.principal.id,private=True)
                    except (KeyError,PermissionError):pass # stale consent is already denied
        await asyncio.to_thread(private_windows)
    desktop=getattr(state,'desktop',None)
    if desktop:
        for row in list(desktop._views.values()):
            if row.get('principal_id')==actor.principal.id and row.get('session_id')==actor.session_id:
                await desktop.stop_viewer(row['id'],actor.principal.id)
