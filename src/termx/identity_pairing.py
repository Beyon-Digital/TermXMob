"""Explicit managed device grants and one-use, path-bound socket admission.

All pending proofs are hashed and bound to a currently live canonical session.
Legacy proof possession never supplies a principal or prolongs legacy access.
"""
from __future__ import annotations

import json
import secrets
import uuid
from time import time

from termx.audit import log_event
from termx.identity import AuthenticationError, _digest
from termx.tokens import SCOPES

PAIR_TTL = 300
SOCKET_TTL = 30


class ManagedPairing:
    def __init__(self, identity, tokens=None):
        self.identity = identity
        self.tokens = tokens
        with identity._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS session_proofs (
                digest TEXT PRIMARY KEY, purpose TEXT NOT NULL, sponsor TEXT NOT NULL,
                principal_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
                scopes TEXT NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
                path TEXT, legacy_id TEXT)''')

    def issue(self, session, scopes, *, legacy_token=None, associate_legacy=False):
        requested = sorted(set(scopes))
        if not requested or not set(requested) <= set(SCOPES):
            raise AuthenticationError('explicit valid device scopes required')
        legacy = None
        if legacy_token is not None:
            if not associate_legacy or 'host-admin' not in session.principal.scopes:
                raise AuthenticationError('explicit administrator legacy association required')
            legacy = self.tokens.association_record(legacy_token) if self.tokens else None
            if not legacy or not set(requested) <= set(legacy['scopes']):
                raise AuthenticationError('legacy proof or scope association denied')
        elif associate_legacy:
            raise AuthenticationError('legacy proof required')
        return self._issue(session, 'pair', requested, PAIR_TTL, legacy_id=legacy['id'] if legacy else None)

    def socket(self, session, path):
        # Exact routes only; the ticket never grants scope or a new resource.
        from re import fullmatch
        if not fullmatch(r'/graphql|/api/desktop/session|/api/sessions/[A-Za-z0-9_-]{1,128}/pty|/api/projects/[A-Za-z0-9_-]{1,128}/lsp/[A-Za-z0-9_-]{1,64}|/api/browser/tabs/[A-Za-z0-9_-]{1,128}/view', path):
            raise AuthenticationError('unsupported socket path')
        return self._issue(session,'socket',list(session.principal.scopes),SOCKET_TTL,path=path)

    def _issue(self, session, purpose, scopes, ttl, *, path=None, legacy_id=None):
        raw = secrets.token_urlsafe(48)
        expires = time()+ttl
        with self.identity._db() as db:
            sponsor = db.execute('SELECT * FROM sessions WHERE id=?',(session.session_id,)).fetchone()
            principal = self.identity._live_principal(db,sponsor)
            if (not principal or principal.id != session.principal.id or
                principal.policy_version != session.principal.policy_version or not set(scopes) <= set(principal.scopes)):
                raise AuthenticationError('current session cannot grant these scopes')
            # Bound outstanding proof storage and discard expired/consumed rows.
            db.execute('DELETE FROM session_proofs WHERE expires<=? OR consumed=1',(time(),))
            count = db.execute('SELECT COUNT(*) FROM session_proofs WHERE sponsor=?',(session.session_id,)).fetchone()[0]
            if count >= 20:
                raise AuthenticationError('too many pending device proofs')
            db.execute('INSERT INTO session_proofs VALUES (?,?,?,?,?,?,?,0,?,?)',
                       (_digest(raw),purpose,session.session_id,principal.id,principal.policy_version,json.dumps(scopes),expires,path,legacy_id))
        log_event('auth_device_proof_issued',principal_id=principal.id,purpose=purpose)
        return {'ticket':raw,'expires_at':expires,'expires_in':ttl,'host_id':self.identity.host_id,'scopes':scopes}

    def _consume(self, db, ticket, purpose, *, host_id, path=None):
        if host_id != self.identity.host_id or not ticket or len(ticket)>256:
            raise AuthenticationError('invalid device proof')
        proof = db.execute('SELECT * FROM session_proofs WHERE digest=?',(_digest(ticket),)).fetchone()
        if not proof or proof['purpose']!=purpose or proof['consumed'] or proof['expires']<=time() or proof['path']!=path:
            raise AuthenticationError('expired or consumed device proof')
        session = db.execute('SELECT * FROM sessions WHERE id=?',(proof['sponsor'],)).fetchone()
        principal = self.identity._live_principal(db,session)
        scopes = json.loads(proof['scopes'])
        if (not principal or principal.id!=proof['principal_id'] or principal.policy_version!=proof['policy_version'] or
            not set(scopes)<=set(principal.scopes)):
            raise AuthenticationError('device proof authority changed')
        db.execute('UPDATE session_proofs SET consumed=1 WHERE digest=?',(_digest(ticket),))
        return proof,session,principal,scopes

    def exchange(self, ticket, *, host_id, device_name):
        sid,raw,csrf = uuid.uuid4().hex,secrets.token_urlsafe(48),secrets.token_urlsafe(32)
        with self.identity._db() as db:
            proof,sponsor,principal,scopes = self._consume(db,ticket,'pair',host_id=host_id)
            if proof['legacy_id']:
                legacy = next((row for row in self.tokens.list_public() if row['id']==proof['legacy_id']),None) if self.tokens else None
                if not legacy or not set(scopes)<=set(legacy['scopes']) or not self.tokens.revoke(legacy['id']):
                    raise AuthenticationError('legacy association changed')
            now = time()
            policy=self.identity.session_policy.read(db)
            db.execute('''INSERT INTO sessions
                (id,principal_id,device_name,strength,created,last_seen,expires,revoked,csrf_hash,adapter_id,grant_scopes,idle_ttl_seconds)
                VALUES (?,?,?,'explicit-device-pair',?,?,?,0,?,?,?,?)''',
                (sid,principal.id,device_name[:128],now,now,now+policy['absolute_ttl_seconds'],_digest(csrf),sponsor['adapter_id'],json.dumps(scopes),policy['idle_ttl_seconds']))
            db.execute('INSERT INTO refresh_tokens VALUES (?,?,0)',(_digest(raw),sid))
            access = self.identity._issue(db,principal,sid)
            credentials=self.identity._credentials(db,access,raw,csrf,sid)
        log_event('auth_device_paired',principal_id=principal.id,session_id=sid,legacy_association=bool(proof['legacy_id']))
        return credentials

    def admit_socket(self, ticket, *, host_id, path):
        with self.identity._db() as db:
            _,session,principal,_ = self._consume(db,ticket,'socket',host_id=host_id,path=path)
            return self.identity._issue(db,principal,session['id'])

    def revoke(self, session, ticket):
        with self.identity._db() as db:
            sponsor=db.execute('SELECT * FROM sessions WHERE id=?',(session.session_id,)).fetchone()
            principal=self.identity._live_principal(db,sponsor)
            if not principal or principal.id!=session.principal.id:
                raise AuthenticationError('current pairing session required')
            result=db.execute("UPDATE session_proofs SET consumed=1 WHERE digest=? AND sponsor=? AND purpose='pair' AND consumed=0",
                              (_digest(ticket),session.session_id))
        log_event('auth_device_proof_revoked',principal_id=principal.id,revoked=bool(result.rowcount))
        return bool(result.rowcount)
