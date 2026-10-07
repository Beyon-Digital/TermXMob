"""Tested, atomic provider configuration activation with explicit session policy."""
from __future__ import annotations

import hashlib
import json
from time import time

from fastapi import HTTPException

from termx.audit import log_event
from termx.identity import AuthenticationError
from termx.identity_adapters import prepare_adapter_configuration


def config_hash(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class AdapterLifecycle:
    def __init__(self, identity, authorization):
        self.identity,self.authorization=identity,authorization
        with identity._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS adapter_drafts (
                    config_hash TEXT NOT NULL, actor_session TEXT NOT NULL, config TEXT NOT NULL,
                    expires REAL NOT NULL, PRIMARY KEY(config_hash,actor_session));
                CREATE TABLE IF NOT EXISTS adapter_proofs (
                    config_hash TEXT NOT NULL, actor_session TEXT NOT NULL, adapter_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL, policy_version INTEGER NOT NULL, action TEXT NOT NULL,
                    project_id TEXT, expires REAL NOT NULL,
                    PRIMARY KEY(config_hash,actor_session,adapter_id));
                CREATE TABLE IF NOT EXISTS adapter_test_flows (state_hash TEXT PRIMARY KEY, actor_session TEXT NOT NULL, config_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS adapter_config_history (
                    version INTEGER PRIMARY KEY, config TEXT NOT NULL, actor_id TEXT NOT NULL, activated REAL NOT NULL);
            ''')

    def prepare(self, config):
        try:
            return prepare_adapter_configuration(self.identity,config,replace=True)
        except (ValueError,KeyError,TypeError) as exc:
            raise HTTPException(400,'invalid authentication adapter configuration') from exc

    async def begin(self, actor, config, adapter_id):
        candidates=self.prepare(config)
        adapter=next((a for a in candidates if a.id==adapter_id),None)
        if not adapter or not hasattr(adapter,'begin'):
            raise HTTPException(400,'configured redirect adapter required')
        digest=config_hash(config)
        with self.identity._db() as db:
            db.execute('DELETE FROM adapter_drafts WHERE expires<=?',(time(),))
            db.execute('INSERT INTO adapter_drafts VALUES (?,?,?,?) ON CONFLICT(config_hash,actor_session) DO UPDATE SET config=excluded.config,expires=excluded.expires',
                       (digest,actor.session_id,json.dumps(config),time()+600))
        try:
            url,binding=await adapter.begin()
            from urllib.parse import parse_qs,urlsplit
            from termx.identity_adapters import _hash
            flow_state=parse_qs(urlsplit(url).query)['state'][0]
            with self.identity._db() as db:
                db.execute('INSERT INTO adapter_test_flows VALUES (?,?,?)',(_hash(flow_state),actor.session_id,digest))
            return url,binding
        except AuthenticationError as exc:
            raise HTTPException(503,str(exc)) from exc

    def proposed_principal(self, config, verified):
        principal=self.identity.principal_for(verified)
        matches=[b['principal_id'] for b in config.get('bindings',[]) if b['issuer']==verified.issuer and b['subject']==verified.subject]
        if len(set(matches))>1:
            raise HTTPException(400,'conflicting identity bindings')
        if matches:
            if principal and principal.id!=matches[0]:
                raise HTTPException(409,'identity rebinding requires explicit migration')
            principal=self.identity.principal_by_id(matches[0])
        if not principal:
            raise HTTPException(403,'verified identity has no enabled principal mapping')
        return principal

    async def test(self, actor, config, adapter_id, evidence, *, action='machine-view', project_id=None):
        adapter=next((a for a in self.prepare(config) if a.id==adapter_id),None)
        if not adapter:
            raise HTTPException(404,'adapter not found in configuration')
        try:
            verified=await adapter.authenticate(evidence)
        except AuthenticationError as exc:
            raise HTTPException(401,'adapter login verification failed') from exc
        principal=self.proposed_principal(config,verified)
        decision=self.authorization.require_principal(principal,action,project_id=project_id)
        with self.identity._db() as db:
            db.execute('INSERT INTO adapter_proofs VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(config_hash,actor_session,adapter_id) DO UPDATE SET principal_id=excluded.principal_id,policy_version=excluded.policy_version,action=excluded.action,project_id=excluded.project_id,expires=excluded.expires',
                       (config_hash(config),actor.session_id,adapter_id,principal.id,principal.policy_version,action,project_id,time()+300))
        log_event('auth_adapter_login_test',actor_id=actor.principal.id,adapter_id=adapter_id,principal_id=principal.id,policy='permit')
        return {'adapter_id':adapter_id,'principal_id':principal.id,'policy':decision.reason,'expires_in':300,'login_verified':True}

    async def draft_callback(self, adapter_id, state, code, binding, callback_uri):
        # Resolve the exact stored PKCE flow/config and initiating administrator.
        from termx.identity_adapters import _hash
        with self.identity._db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='oidc_flows'").fetchone():
                return None
            flow=db.execute('SELECT config_hash FROM oidc_flows WHERE state_hash=? AND adapter_id=?',(_hash(state),adapter_id)).fetchone()
            test_flow=db.execute('SELECT * FROM adapter_test_flows WHERE state_hash=?',(_hash(state),)).fetchone()
            drafts=db.execute('SELECT * FROM adapter_drafts WHERE expires>? AND actor_session=? AND config_hash=?',(time(),test_flow['actor_session'],test_flow['config_hash'])).fetchall() if test_flow else []
        if not flow:
            return None
        for draft in drafts:
            config=json.loads(draft['config'])
            adapter=next((a for a in self.prepare(config) if a.id==adapter_id),None)
            if not adapter or adapter.config_hash != flow['config_hash']:
                continue
            if adapter.config.redirect_uri != callback_uri:
                raise HTTPException(403,'draft callback origin mismatch')
            actor=self.identity.session_by_id(draft['actor_session'])
            if not actor:
                raise HTTPException(401,'administrator test session expired')
            self.authorization.require_principal(actor.principal,'host-admin')
            return await self.test(actor,config,adapter_id,{'state':state,'code':code,'binding':binding})
        return None

    def activate(self, actor, config, *, session_policy='revoke'):
        candidates=self.prepare(config)
        digest=config_hash(config)
        if session_policy not in {'revoke','expire'}:
            raise HTTPException(400,'explicit session policy required')
        # All health/login/policy proofs are bound to this full config, current
        # administrator session and live tested principal authority.
        with self.identity._db() as db:
            rows=db.execute('SELECT * FROM adapter_proofs WHERE config_hash=? AND actor_session=? AND expires>?',(digest,actor.session_id,time())).fetchall()
        proofs={r['adapter_id']:r for r in rows}
        for adapter in candidates:
            proof=proofs.get(adapter.id)
            principal=self.identity.principal_by_id(proof['principal_id']) if proof else None
            if not proof or not principal or principal.policy_version!=proof['policy_version']:
                raise HTTPException(409,'each adapter requires a current login and policy test')
            self.authorization.require_principal(principal,proof['action'],project_id=proof['project_id'])
        old={a.id for a in self.identity.adapters.values()}-{'local-password'}
        new={a.id for a in candidates}
        with self.identity._db() as db:
            local=db.execute("SELECT enabled FROM adapter_status WHERE adapter_id='local-password'").fetchone()
            if not new and local and not local[0]:
                raise HTTPException(409,'configuration must retain an enabled sign-in method')
            for binding in config.get('bindings',[]):
                old_mapping=db.execute('SELECT principal_id FROM identities WHERE issuer=? AND subject=?',(binding['issuer'],binding['subject'])).fetchone()
                if old_mapping and old_mapping[0]!=binding['principal_id']:
                    raise HTTPException(409,'identity binding changed during activation')
                db.execute('INSERT OR IGNORE INTO identities VALUES (?,?,?)',(binding['issuer'],binding['subject'],binding['principal_id']))
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM adapter_config_history').fetchone()[0]
            db.execute('INSERT INTO adapter_config_history VALUES (?,?,?,?)',(version,json.dumps(config),actor.principal.id,time()))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('runtime_adapter_config',?)",(json.dumps(config),))
            for adapter in candidates:
                db.execute("INSERT INTO adapter_status (adapter_id,enabled,version,health,checked,session_policy) VALUES (?,1,?,'healthy',?,?) ON CONFLICT(adapter_id) DO UPDATE SET enabled=1,version=excluded.version,health='healthy',checked=excluded.checked,session_policy=excluded.session_policy",(adapter.id,version,time(),session_policy))
            if session_policy=='revoke':
                changed=old|new
                for adapter_id in changed:
                    db.execute('UPDATE sessions SET revoked=1 WHERE adapter_id=?',(adapter_id,))
            db.execute('DELETE FROM adapter_proofs WHERE actor_session=?',(actor.session_id,))
        self.identity.adapters={'local-password':self.identity.adapters['local-password'],**{a.id:a for a in candidates}}
        log_event('auth_adapter_configuration_activated',actor_id=actor.principal.id,version=version,adapter_ids=sorted(new),session_policy=session_policy)
        return {'version':version,'adapters':sorted(new),'session_policy':session_policy}

    def configuration(self):
        with self.identity._db() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='runtime_adapter_config'").fetchone()
            version=db.execute('SELECT COALESCE(MAX(version),0) FROM adapter_config_history').fetchone()[0]
        return {'version':version,'configuration':json.loads(row[0]) if row else {'version':1,'adapters':[],'bindings':[]}}
