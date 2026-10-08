"""Canonical administrator-managed groups and bounded verified IdP mappings.

Verified claims are evidence, never roles. Only a configured issuer/name mapping
can enroll an already mapped principal; expired memberships confer no authority.
"""
from __future__ import annotations
import json
import math
import uuid
from time import time
from termx.audit import log_event


class IdentityGroups:
    def __init__(self, identity):
        self.identity=identity
        with identity._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS organizations (id TEXT PRIMARY KEY,label TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_groups (
                    id TEXT PRIMARY KEY,label TEXT NOT NULL,organization_id TEXT,role TEXT NOT NULL,
                    trusted_execution INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS group_memberships (
                    principal_id TEXT NOT NULL,group_id TEXT NOT NULL,source TEXT NOT NULL,expires REAL,
                    PRIMARY KEY(principal_id,group_id,source));
                CREATE TABLE IF NOT EXISTS group_claim_mappings (
                    issuer TEXT NOT NULL,claim_value TEXT NOT NULL,group_id TEXT NOT NULL,
                    PRIMARY KEY(issuer,claim_value));
                CREATE TABLE IF NOT EXISTS group_project_grants (
                    group_id TEXT NOT NULL,project_id TEXT NOT NULL,scopes TEXT NOT NULL,expires REAL,
                    PRIMARY KEY(group_id,project_id));
            ''')

    @staticmethod
    def memberships(db,principal_id):
        return db.execute('''SELECT DISTINCT g.* FROM identity_groups g JOIN group_memberships m ON m.group_id=g.id
            WHERE m.principal_id=? AND (m.expires IS NULL OR m.expires>?)''',(principal_id,time())).fetchall()

    def context(self,db,principal_id,base_scopes):
        from termx.authorization import ROLES
        rows=self.memberships(db,principal_id)
        scopes=set(base_scopes)
        for row in rows:scopes.update(ROLES[row['role']])
        ordered=tuple(dict.fromkeys([*base_scopes,*sorted(scopes-set(base_scopes))]))
        return ordered,tuple(r['id'] for r in rows),tuple(sorted({r['organization_id'] for r in rows if r['organization_id']}))

    def role(self,db,principal_id,base):
        rank={'viewer':0,'operator':1,'admin':2,'owner':3}
        candidates=[dict(base)] if base else []
        candidates.extend(dict(r) for r in self.memberships(db,principal_id))
        if not candidates:return None
        role=max(candidates,key=lambda row:rank[row['role']])['role']
        return {'role':role,'trusted_execution':any(r['trusted_execution'] for r in candidates)}

    def project_grants(self,db,principal_id):
        return db.execute('''SELECT p.project_id,p.scopes,
            CASE WHEN p.expires IS NULL THEN m.expires WHEN m.expires IS NULL THEN p.expires
            ELSE MIN(p.expires,m.expires) END AS expires
            FROM group_project_grants p JOIN group_memberships m ON p.group_id=m.group_id
            WHERE m.principal_id=? AND (m.expires IS NULL OR m.expires>?) AND (p.expires IS NULL OR p.expires>?)''',
            (principal_id,time(),time())).fetchall()

    @staticmethod
    def invalidate(db,principals):
        for pid in set(principals):
            db.execute('UPDATE principals SET policy_version=policy_version+1 WHERE id=?',(pid,))
            db.execute('UPDATE sessions SET revoked=1 WHERE principal_id=?',(pid,))

    def inventory(self):
        with self.identity._db() as db:
            organizations=[dict(r) for r in db.execute('SELECT * FROM organizations ORDER BY label')]
            groups=[]
            for row in db.execute('SELECT * FROM identity_groups ORDER BY label'):
                item=dict(row);item['trusted_execution']=bool(item['trusted_execution'])
                item['memberships']=[dict(r) for r in db.execute('SELECT principal_id,source,expires FROM group_memberships WHERE group_id=?',(row['id'],))]
                item['mappings']=[dict(r) for r in db.execute('SELECT issuer,claim_value FROM group_claim_mappings WHERE group_id=?',(row['id'],))]
                item['projects']=[{**dict(r),'scopes':json.loads(r['scopes'])} for r in db.execute('SELECT project_id,scopes,expires FROM group_project_grants WHERE group_id=?',(row['id'],))]
                groups.append(item)
        issuers=[]
        for adapter in self.identity.adapters.values():
            config=getattr(adapter,'config',adapter)
            issuer=getattr(config,'issuer',None)
            if issuer:issuers.append({'issuer':issuer,'label':adapter.label,'groups_claim':getattr(config,'groups_claim',None),'membership_ttl':getattr(config,'membership_ttl',300)})
        return {'organizations':organizations,'groups':groups,'issuers':issuers}

    def organization(self,label,actor_id):
        if not label.strip() or len(label)>128:raise ValueError('Organization name must be1–128 characters')
        identifier=uuid.uuid4().hex
        with self.identity._db() as db:db.execute('INSERT INTO organizations VALUES (?,?)',(identifier,label.strip()))
        log_event('identity_organization_created',organization_id=identifier,actor_id=actor_id)
        return {'id':identifier,'label':label.strip()}

    def save(self,*,identifier=None,revision=None,label,organization_id=None,role='viewer',trusted_execution=False,actor_id):
        if not label.strip() or len(label)>128 or role not in {'viewer','operator','admin'}:raise ValueError('Invalid group name or role')
        with self.identity._db() as db:
            if organization_id and not db.execute('SELECT 1 FROM organizations WHERE id=?',(organization_id,)).fetchone():raise ValueError('Unknown organization')
            if identifier:
                old=db.execute('SELECT revision FROM identity_groups WHERE id=?',(identifier,)).fetchone()
                if not old or old[0]!=revision:raise ValueError('Group revision changed; reload before saving')
                db.execute('UPDATE identity_groups SET label=?,organization_id=?,role=?,trusted_execution=?,revision=revision+1 WHERE id=?',
                    (label.strip(),organization_id,role,int(trusted_execution),identifier))
                self.invalidate(db,[r[0] for r in db.execute('SELECT principal_id FROM group_memberships WHERE group_id=?',(identifier,))])
            else:
                identifier=uuid.uuid4().hex
                db.execute('INSERT INTO identity_groups VALUES (?,?,?,?,?,1)',(identifier,label.strip(),organization_id,role,int(trusted_execution)))
        log_event('identity_group_saved',group_id=identifier,role=role,actor_id=actor_id)
        return next(g for g in self.inventory()['groups'] if g['id']==identifier)

    def member(self,group_id,principal_id,*,present,expires=None,actor_id):
        if expires is not None and (not math.isfinite(expires) or expires<=time()):raise ValueError('Membership expiry must be in the future')
        with self.identity._db() as db:
            if not db.execute('SELECT 1 FROM identity_groups WHERE id=?',(group_id,)).fetchone():raise ValueError('Unknown group')
            if not db.execute('SELECT 1 FROM principals WHERE id=? AND enabled=1',(principal_id,)).fetchone():raise ValueError('Unknown principal')
            if present:db.execute('INSERT INTO group_memberships VALUES (?,?,?,?) ON CONFLICT(principal_id,group_id,source) DO UPDATE SET expires=excluded.expires',(principal_id,group_id,'administrator',expires))
            else:db.execute('DELETE FROM group_memberships WHERE principal_id=? AND group_id=?',(principal_id,group_id))
            self.invalidate(db,[principal_id])
        log_event('identity_group_membership_changed',group_id=group_id,principal_id=principal_id,present=present,actor_id=actor_id)

    def mapping(self,group_id,issuer,claim_value,*,present,actor_id):
        configured={getattr(getattr(a,'config',None),'issuer',getattr(a,'issuer',None)) for a in self.identity.adapters.values()}
        if issuer not in configured or not claim_value or len(claim_value)>256:raise ValueError('Mapping requires a configured trusted issuer and bounded claim value')
        with self.identity._db() as db:
            if not db.execute('SELECT 1 FROM identity_groups WHERE id=?',(group_id,)).fetchone():raise ValueError('Unknown group')
            old=db.execute('SELECT group_id FROM group_claim_mappings WHERE issuer=? AND claim_value=?',(issuer,claim_value)).fetchone()
            if old and old[0]!=group_id:raise ValueError('Claim already mapped to another group')
            if present:db.execute('INSERT OR IGNORE INTO group_claim_mappings VALUES (?,?,?)',(issuer,claim_value,group_id))
            else:
                db.execute('DELETE FROM group_claim_mappings WHERE issuer=? AND claim_value=? AND group_id=?',(issuer,claim_value,group_id))
                ids=[r[0] for r in db.execute('SELECT principal_id FROM group_memberships WHERE group_id=? AND source=?',(group_id,'issuer:'+issuer))]
                db.execute('DELETE FROM group_memberships WHERE group_id=? AND source=?',(group_id,'issuer:'+issuer))
                self.invalidate(db,ids)
        log_event('identity_group_mapping_changed',group_id=group_id,issuer=issuer,present=present,actor_id=actor_id)

    def grant(self,group_id,project_id,scopes,*,expires=None,actor_id):
        from termx.tokens import SCOPES
        if not project_id or not set(scopes)<=set(SCOPES)-{'host-admin'} or (expires is not None and (not math.isfinite(expires) or expires<=time())):raise ValueError('Invalid group project grant')
        with self.identity._db() as db:
            if not db.execute('SELECT 1 FROM identity_groups WHERE id=?',(group_id,)).fetchone():raise ValueError('Unknown group')
            if scopes:db.execute('INSERT INTO group_project_grants VALUES (?,?,?,?) ON CONFLICT(group_id,project_id) DO UPDATE SET scopes=excluded.scopes,expires=excluded.expires',(group_id,project_id,json.dumps(sorted(set(scopes))),expires))
            else:db.execute('DELETE FROM group_project_grants WHERE group_id=? AND project_id=?',(group_id,project_id))
            self.invalidate(db,[r[0] for r in db.execute('SELECT principal_id FROM group_memberships WHERE group_id=?',(group_id,))])
        log_event('identity_group_project_changed',group_id=group_id,project_id=project_id,scopes=scopes,actor_id=actor_id)

    def sync(self,verified,principal_id):
        if verified.groups is None:return
        if (not isinstance(verified.groups,(tuple,list)) or len(verified.groups)>128
                or any(not isinstance(value,str) or not value or len(value)>256 for value in verified.groups)
                or isinstance(verified.membership_ttl,bool) or not isinstance(verified.membership_ttl,int)
                or not 60<=verified.membership_ttl<=86400):raise ValueError('Invalid verified group evidence')
        source='issuer:'+verified.issuer
        with self.identity._db() as db:
            ids={r[0] for r in db.execute('SELECT group_id FROM group_claim_mappings WHERE issuer=? AND claim_value IN ('+','.join('?' for _ in verified.groups)+')',(verified.issuer,*verified.groups))} if verified.groups else set()
            old={r[0] for r in db.execute('SELECT group_id FROM group_memberships WHERE principal_id=? AND source=? AND (expires IS NULL OR expires>?)',(principal_id,source,time()))}
            if old!=ids:
                self.invalidate(db,[principal_id])
            db.execute('DELETE FROM group_memberships WHERE principal_id=? AND source=?',(principal_id,source))
            db.executemany('INSERT INTO group_memberships VALUES (?,?,?,?)',[(principal_id,g,source,time()+verified.membership_ttl) for g in ids])
        log_event('identity_group_claims_synchronized',principal_id=principal_id,issuer=verified.issuer,groups=len(ids))
