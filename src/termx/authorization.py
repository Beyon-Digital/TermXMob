"""Live host/project/resource authorization, independent of identity providers.

This policy protects application resources. Local processes still execute as the
host OS user: only administrators/trusted operators may launch them. It never
advertises hostile-tenant isolation without an enforcing runtime adapter.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Protocol

from fastapi import HTTPException

from termx.audit import log_event
from termx.auth import Auth
from termx.identity import AuthenticationService, _digest
from termx.tokens import SCOPES

VIEWER = ('machine-view', 'terminal-view', 'files-read', 'git-read', 'agent-view')
OPERATOR = tuple(s for s in SCOPES if s not in {'host-admin', 'network-manage', 'ai-settings', 'desktop-view', 'desktop-control'})
ROLES = {'owner': tuple(SCOPES), 'admin': tuple(SCOPES), 'operator': OPERATOR, 'viewer': VIEWER}
EXECUTION = {'terminal-control', 'agent-run'}


@dataclass(frozen=True)
class AuthorizationDecision:
    permitted: bool
    reason: str
    constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResourceAuthoritySnapshot:
    """One collection decision, never a cached or delegated execution grant."""
    principal_id: str
    policy_version: int
    action: str
    resource_kind: str
    resources: tuple[tuple[str, str | None], ...]
    expires_at: float | None = None
    administrator: bool = False
    authority_session_id: str | None = None

    @property
    def allowed_ids(self) -> frozenset[str]:
        return frozenset(identifier for identifier, _ in self.resources)


class AuthorizationPort(Protocol):
    def evaluate(self, credential: str | None, action: str, *, project_id: str | None = None,
                 resource_kind: str | None = None, resource_id: str | None = None,
                 host_id: str | None = None) -> AuthorizationDecision: ...


class AuthorizationService:
    def __init__(self, identity: AuthenticationService, auth: Auth | None = None):
        self.identity = identity
        self.auth = auth or Auth(identity=identity)
        with identity._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS host_roles (
                    principal_id TEXT PRIMARY KEY, role TEXT NOT NULL,
                    trusted_execution INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS project_grants (
                    principal_id TEXT NOT NULL, project_id TEXT NOT NULL, scopes TEXT NOT NULL,
                    expires REAL, PRIMARY KEY(principal_id, project_id));
                CREATE TABLE IF NOT EXISTS resource_owners (
                    kind TEXT NOT NULL, resource_id TEXT NOT NULL, principal_id TEXT NOT NULL,
                    project_id TEXT, PRIMARY KEY(kind, resource_id));
                CREATE TABLE IF NOT EXISTS recovery_codes (
                    digest TEXT PRIMARY KEY, created REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS adapter_status (
                    adapter_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1, health TEXT NOT NULL DEFAULT 'unknown',
                    checked REAL, session_policy TEXT NOT NULL DEFAULT 'expire');
            ''')
            # Adopt the original sole local owner once. Subsequent accounts are
            # created explicitly and never inherit administrator authority.
            row = db.execute("SELECT value FROM metadata WHERE key='authorization_initialized'").fetchone()
            if not row and db.execute("SELECT 1 FROM metadata WHERE key='configured'").fetchone():
                owner = db.execute("SELECT principal_id FROM identities WHERE issuer='termx:local' ORDER BY rowid LIMIT 1").fetchone()
                if owner:
                    db.execute("INSERT OR IGNORE INTO host_roles VALUES (?, 'owner', 1)", (owner[0],))
                    db.execute("INSERT INTO metadata VALUES ('authorization_initialized', '1')")

    def resource_snapshot(self, principal, action: str, resource_kind: str,
                          resource_ids) -> ResourceAuthoritySnapshot:
        """Read current authority once for a bounded, already-owned collection.

        Callers must still bind each row's owner/project/path to its ledger entry
        and validate the snapshot immediately before publishing their collection.
        No decision survives into a later request or authorizes an execution.
        """
        identifiers = tuple(dict.fromkeys(resource_ids))
        if action not in VIEWER:
            raise ValueError('collection snapshots cannot authorize execution or mutations')
        if action not in SCOPES or not resource_kind or len(resource_kind) > 64 or len(identifiers) > 5000:
            raise ValueError('invalid bounded resource collection')
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in identifiers):
            raise ValueError('invalid resource identity')
        now = time()
        with self.identity._db() as db:
            if principal.authority_session_id:
                session = db.execute('SELECT * FROM sessions WHERE id=? AND principal_id=?',
                                     (principal.authority_session_id,principal.id)).fetchone()
                current = self.identity._live_principal(db,session)
                if not current or action not in current.scopes:
                    raise HTTPException(403,'session disabled or collection action denied')
            live = db.execute('SELECT scopes,policy_version FROM principals WHERE id=? AND enabled=1',
                              (principal.id,)).fetchone()
            role = db.execute('SELECT role,trusted_execution FROM host_roles WHERE principal_id=?',
                              (principal.id,)).fetchone()
            if not live or action not in json.loads(live['scopes']) or not role or action not in ROLES.get(role['role'], ()):
                raise HTTPException(403, 'principal disabled or collection action denied')
            admin = role['role'] in {'owner','admin'}
            if admin:
                return ResourceAuthoritySnapshot(principal.id, live['policy_version'], action,
                    resource_kind, tuple((identifier,None) for identifier in identifiers), administrator=True,
                    authority_session_id=principal.authority_session_id)
            grants = {row['project_id']:row for row in db.execute(
                'SELECT project_id,scopes,expires FROM project_grants WHERE principal_id=?', (principal.id,))}
            allowed = []
            expires = []
            for offset in range(0,len(identifiers),500):
                chunk = identifiers[offset:offset+500]
                rows = db.execute('SELECT resource_id,project_id FROM resource_owners WHERE kind=? AND principal_id=? AND resource_id IN ('+
                                  ','.join('?' for _ in chunk)+')', (resource_kind,principal.id,*chunk))
                for row in rows:
                    project = row['project_id']
                    grant = grants.get(project)
                    if project and (not grant or (grant['expires'] is not None and grant['expires'] <= now) or action not in json.loads(grant['scopes'])):
                        continue
                    allowed.append((row['resource_id'],project))
                    if project and grant['expires'] is not None:
                        expires.append(grant['expires'])
            return ResourceAuthoritySnapshot(principal.id, live['policy_version'], action,
                resource_kind, tuple(allowed), min(expires) if expires else None,
                authority_session_id=principal.authority_session_id)

    def validate_resource_snapshot(self, snapshot: ResourceAuthoritySnapshot) -> None:
        """Recheck durable current policy immediately before collection output."""
        with self.identity._db() as db:
            if snapshot.authority_session_id:
                session = db.execute('SELECT * FROM sessions WHERE id=? AND principal_id=?',
                                     (snapshot.authority_session_id,snapshot.principal_id)).fetchone()
                current = self.identity._live_principal(db,session)
                if not current or snapshot.action not in current.scopes:
                    raise HTTPException(403,'collection session authority changed')
            live = db.execute('SELECT scopes,policy_version FROM principals WHERE id=? AND enabled=1',
                              (snapshot.principal_id,)).fetchone()
            if (not live or live['policy_version'] != snapshot.policy_version or
                snapshot.action not in json.loads(live['scopes']) or
                (snapshot.expires_at is not None and snapshot.expires_at <= time())):
                raise HTTPException(403, 'collection authority changed; reload current permissions')

    def require_principal(self, principal, action: str, *, project_id: str | None = None,
                          resource_kind: str | None = None, resource_id: str | None = None):
        # Used by enrolled durable jobs after an explicit delegated grant.
        # Reload authority: callers cannot resurrect disabled users/stale scopes.
        live = self.identity.current_principal(principal)
        if live is None or action not in live.scopes:
            raise HTTPException(403, 'principal disabled or action denied')
        with self.identity._db() as db:
            role = db.execute('SELECT role, trusted_execution FROM host_roles WHERE principal_id=?', (live.id,)).fetchone()
            if role and role['role'] in {'owner', 'admin'}:
                return AuthorizationDecision(True, 'live administrator', ('trusted-shared-machine',))
            if not role or action not in ROLES.get(role['role'], ()):
                raise HTTPException(403, 'host role denies action')
            if action in EXECUTION and not role['trusted_execution']:
                raise HTTPException(403, 'execution requires trusted host-user authority')
            if resource_kind or resource_id:
                resource = db.execute('SELECT * FROM resource_owners WHERE kind=? AND resource_id=?',(resource_kind,resource_id)).fetchone()
                if not resource or resource['principal_id'] != live.id or (project_id and project_id != resource['project_id']):
                    raise HTTPException(403, 'resource authority denied')
                project_id = resource['project_id']
                if not project_id:
                    return AuthorizationDecision(True,'owned scratch resource',('trusted-shared-machine',))
            if project_id:
                grant=db.execute('SELECT scopes,expires FROM project_grants WHERE principal_id=? AND project_id=?',(live.id,project_id)).fetchone()
                if grant and (grant['expires'] is None or grant['expires'] > time()) and action in json.loads(grant['scopes']):
                    return AuthorizationDecision(True,'live project grant',('trusted-shared-machine',))
            elif action == 'machine-view':
                return AuthorizationDecision(True,'host status read')
        raise HTTPException(403, 'project or resource authority denied')

    def claim_principal(self, principal, kind: str, resource_id: str, project_id: str | None = None):
        live=self.identity.current_principal(principal)
        if not live:
            raise HTTPException(403,'principal disabled')
        if project_id:
            self.require_principal(live,'agent-view',project_id=project_id)
        with self.identity._db() as db:
            old=db.execute('SELECT principal_id,project_id FROM resource_owners WHERE kind=? AND resource_id=?',(kind,resource_id)).fetchone()
            if old and (old[0]!=live.id or old[1]!=project_id):
                raise HTTPException(409,'resource already claimed')
            db.execute('INSERT OR IGNORE INTO resource_owners VALUES (?,?,?,?)',(kind,resource_id,live.id,project_id))

    def resource_owner(self, kind: str, resource_id: str) -> dict | None:
        """Read ledger provenance only; this neither claims nor grants access.

        Server callers must use current require_* policy before publishing or
        executing resources. Missing rows have no invented legacy owner.
        """
        if not kind or len(kind)>64 or not resource_id or len(resource_id)>256:
            raise ValueError('invalid resource identity')
        with self.identity._db() as db:
            row=db.execute('SELECT kind,resource_id,principal_id,project_id FROM resource_owners WHERE kind=? AND resource_id=?',
                           (kind,resource_id)).fetchone()
        return dict(row) if row else None

    def require_creation_principal(self, principal, action: str):
        live = self.identity.current_principal(principal)
        if not live or action not in live.scopes:
            raise HTTPException(403, 'principal disabled or creation action denied')
        with self.identity._db() as db:
            role = db.execute('SELECT role, trusted_execution FROM host_roles WHERE principal_id=?', (live.id,)).fetchone()
        if not role or action not in ROLES.get(role['role'], ()):
            raise HTTPException(403, 'host role denies creation action')
        if action in EXECUTION:
            # Launches require an actual project/resource decision, not a
            # collection/creation permission with no filesystem boundary.
            raise HTTPException(403, 'execution requires project or resource authorization')
        return AuthorizationDecision(True, 'live private resource creation', ('trusted-shared-machine',))

    require_principal_creation = require_creation_principal

    def require_creation(self, credential: str | None, action: str):
        scopes=self.auth.scopes(credential)
        if scopes is None or action not in scopes:
            raise HTTPException(403,'creation action denied')
        session=self.identity.resolve(credential)
        if session and action not in ROLES.get(self.role(session.principal.id), ()):
            # Initial owner is adopted through its first host policy evaluation.
            self.require(credential,action)

    def role(self, principal_id: str) -> str | None:
        with self.identity._db() as db:
            row = db.execute('SELECT role FROM host_roles WHERE principal_id=?', (principal_id,)).fetchone()
        return row[0] if row else None

    def revision(self, credential: str | None) -> int:
        session = self.identity.resolve(credential)
        return session.principal.policy_version if session else 0

    def evaluate(self, credential: str | None, action: str, *, project_id: str | None = None,
                 resource_kind: str | None = None, resource_id: str | None = None,
                 host_id: str | None = None) -> AuthorizationDecision:
        if action not in SCOPES:
            return AuthorizationDecision(False, 'unknown action')
        if host_id and host_id != self.identity.host_id:
            return AuthorizationDecision(False, 'cross-host authority is forbidden')
        scopes = self.auth.scopes(credential)
        if scopes is None:
            return AuthorizationDecision(False, 'sign in required')
        if action not in scopes:
            return AuthorizationDecision(False, 'missing action scope')
        session = self.identity.resolve(credential) if self.identity.configured else None
        if session is None:
            # Existing passcode/paired compatibility only; Auth enforces its
            # bounded migration window. They have no invented user identity.
            return AuthorizationDecision(True, 'legacy host authority', ('trusted-shared-machine',))
        with self.identity._db() as db:
            row = db.execute('SELECT role, trusted_execution FROM host_roles WHERE principal_id=?', (session.principal.id,)).fetchone()
            role = row['role'] if row else None
            # The setup owner may be claimed after this service was composed.
            if role is None and not db.execute("SELECT 1 FROM metadata WHERE key='authorization_initialized'").fetchone():
                initial = db.execute("SELECT principal_id FROM identities WHERE issuer='termx:local' ORDER BY rowid LIMIT 1").fetchone()
                if initial and initial[0] == session.principal.id:
                    role = 'owner'
                    db.execute("INSERT INTO host_roles VALUES (?, 'owner', 1)", (session.principal.id,))
                    db.execute("INSERT INTO metadata VALUES ('authorization_initialized', '1')")
            if role in {'owner', 'admin'}:
                return AuthorizationDecision(True, 'host administrator', ('trusted-shared-machine',))
            if action not in ROLES.get(role, ()):
                return AuthorizationDecision(False, 'host role denies action')
            if action in EXECUTION and (not row or not row['trusted_execution']):
                return AuthorizationDecision(False, 'execution requires trusted host-user authority; OS tenant isolation unavailable')
            if resource_kind or resource_id:
                if not resource_kind or not resource_id:
                    return AuthorizationDecision(False, 'incomplete resource identity')
                resource = db.execute('SELECT principal_id, project_id FROM resource_owners WHERE kind=? AND resource_id=?',
                                      (resource_kind, resource_id)).fetchone()
                if resource is None or resource['principal_id'] != session.principal.id:
                    return AuthorizationDecision(False, 'resource belongs to another principal or is unclaimed')
                if project_id and resource['project_id'] != project_id:
                    return AuthorizationDecision(False, 'resource project mismatch')
                project_id = resource['project_id']
                if not project_id:
                    return AuthorizationDecision(True, 'owned scratch resource', ('trusted-shared-machine',))
            if project_id:
                grant = db.execute('SELECT scopes, expires FROM project_grants WHERE principal_id=? AND project_id=?',
                                   (session.principal.id, project_id)).fetchone()
                if not grant or (grant['expires'] is not None and grant['expires'] <= time()):
                    return AuthorizationDecision(False, 'project membership missing or expired')
                if action not in json.loads(grant['scopes']):
                    return AuthorizationDecision(False, 'project grant denies action')
                return AuthorizationDecision(True, 'project grant', ('trusted-shared-machine',))
            if action == 'machine-view':
                return AuthorizationDecision(True, 'host status read')
            return AuthorizationDecision(False, 'host-wide operation requires administrator')

    def can(self, credential: str | None, action: str, **resource) -> bool:
        return self.evaluate(credential, action, **resource).permitted

    def require(self, credential: str | None, action: str, **resource) -> AuthorizationDecision:
        decision = self.evaluate(credential, action, **resource)
        if not decision.permitted:
            log_event('authorization_denied', action=action, reason=decision.reason,
                      project_id=resource.get('project_id'), resource_kind=resource.get('resource_kind'), resource_id=resource.get('resource_id'))
            raise HTTPException(401 if self.auth.scopes(credential) is None else 403, decision.reason)
        return decision

    def claim(self, credential: str | None, kind: str, resource_id: str, project_id: str | None = None) -> None:
        session = self.identity.resolve(credential)
        if not session:
            return  # Compatibility resources are unclaimed; never visible to managed members.
        if not kind or not resource_id or len(kind) > 64 or len(resource_id) > 256:
            raise ValueError('invalid resource identity')
        if project_id:
            self.require(credential, 'agent-view', project_id=project_id)
        with self.identity._db() as db:
            old = db.execute('SELECT principal_id, project_id FROM resource_owners WHERE kind=? AND resource_id=?', (kind, resource_id)).fetchone()
            if old and (old[0] != session.principal.id or old[1] != project_id):
                raise HTTPException(409, 'resource identity already claimed')
            db.execute('INSERT OR IGNORE INTO resource_owners VALUES (?, ?, ?, ?)', (kind, resource_id, session.principal.id, project_id))

    def require_path(self, credential: str | None, action: str, path: str, projects: list[dict]) -> str | None:
        resolved = Path(path).expanduser().resolve()
        matching = [p for p in projects if resolved.is_relative_to(Path(p['path']).resolve())]
        project = max(matching, key=lambda p: len(p['path'])) if matching else None
        self.require(credential, action, project_id=project['id'] if project else None)
        return project['id'] if project else None

    def list_principals(self) -> list[dict]:
        with self.identity._db() as db:
            rows = db.execute('''SELECT p.id, p.display_name, p.enabled, p.policy_version,
                r.role, r.trusted_execution FROM principals p LEFT JOIN host_roles r ON r.principal_id=p.id''').fetchall()
        return [dict(row) for row in rows]

    def set_role(self, principal_id: str, role: str, *, trusted_execution: bool = False, actor_id: str = '') -> None:
        if role not in ROLES:
            raise ValueError('unknown role')
        with self.identity._db() as db:
            principal = db.execute('SELECT enabled FROM principals WHERE id=?', (principal_id,)).fetchone()
            if principal is None:
                raise ValueError('principal not found')
            old = db.execute('SELECT role FROM host_roles WHERE principal_id=?', (principal_id,)).fetchone()
            if old and old[0] == 'owner' and role != 'owner':
                raise ValueError('owner transfer requires explicit recovery procedure')
            if role == 'owner' and db.execute("SELECT 1 FROM host_roles WHERE role='owner' AND principal_id<>?", (principal_id,)).fetchone():
                raise ValueError('host already has an owner')
            db.execute('INSERT INTO host_roles VALUES (?, ?, ?) ON CONFLICT(principal_id) DO UPDATE SET role=excluded.role, trusted_execution=excluded.trusted_execution',
                       (principal_id, role, int(trusted_execution or role in {'owner', 'admin'})))
            db.execute('UPDATE principals SET scopes=?, policy_version=policy_version+1 WHERE id=?', (json.dumps(ROLES[role]), principal_id))
        log_event('authorization_role_changed', principal_id=principal_id, role=role, actor_id=actor_id, trusted_execution=trusted_execution)

    def grant_project(self, principal_id: str, project_id: str, scopes: list[str], *, expires: float | None = None, actor_id: str = '') -> None:
        if not project_id or not set(scopes) <= set(SCOPES) or 'host-admin' in scopes:
            raise ValueError('invalid project grant')
        if expires is not None and expires <= time():
            raise ValueError('grant expiry must be in the future')
        with self.identity._db() as db:
            if not db.execute('SELECT 1 FROM principals WHERE id=? AND enabled=1', (principal_id,)).fetchone():
                raise ValueError('principal not found')
            db.execute('INSERT INTO project_grants VALUES (?, ?, ?, ?) ON CONFLICT(principal_id,project_id) DO UPDATE SET scopes=excluded.scopes, expires=excluded.expires',
                       (principal_id, project_id, json.dumps(sorted(set(scopes))), expires))
            db.execute('UPDATE principals SET policy_version=policy_version+1 WHERE id=?', (principal_id,))
        log_event('authorization_project_granted', actor_id=actor_id, principal_id=principal_id, project_id=project_id, scopes=scopes, expires=expires)

    def revoke_project(self, principal_id: str, project_id: str, *, actor_id: str = '') -> None:
        with self.identity._db() as db:
            db.execute('DELETE FROM project_grants WHERE principal_id=? AND project_id=?', (principal_id, project_id))
            db.execute('UPDATE principals SET policy_version=policy_version+1 WHERE id=?', (principal_id,))
        log_event('authorization_project_revoked', principal_id=principal_id, project_id=project_id, actor_id=actor_id)

    def project_grants(self, principal_id: str) -> list[dict]:
        with self.identity._db() as db:
            rows = db.execute('SELECT project_id, scopes, expires FROM project_grants WHERE principal_id=?', (principal_id,)).fetchall()
        return [{**dict(row), 'scopes': json.loads(row['scopes'])} for row in rows]

    def new_recovery_code(self, *, actor_id: str) -> str:
        raw = secrets.token_urlsafe(48)
        with self.identity._db() as db:
            db.execute('DELETE FROM recovery_codes')
            db.execute('INSERT INTO recovery_codes VALUES (?, ?, 0)', (_digest(raw), time()))
        log_event('auth_recovery_rotated', actor_id=actor_id)
        return raw

    def recover_owner(self, raw: str, password: str) -> str:
        from termx.identity import _password_hash
        if not 12 <= len(password) <= 1024:
            raise ValueError('password must be 12–1024 characters')
        salt = secrets.token_bytes(16)
        digest = _password_hash(password, salt)
        with self.identity._db() as db:
            code = db.execute('SELECT used FROM recovery_codes WHERE digest=?', (_digest(raw),)).fetchone()
            if not code or code['used']:
                raise ValueError('invalid recovery credential')
            owner = db.execute("SELECT r.principal_id, i.subject FROM host_roles r JOIN identities i ON i.principal_id=r.principal_id WHERE r.role='owner' AND i.issuer='termx:local'").fetchone()
            if owner is None:
                raise ValueError('local owner recovery unavailable')
            db.execute('UPDATE recovery_codes SET used=1 WHERE digest=?', (_digest(raw),))
            db.execute('UPDATE passwords SET salt=?, digest=? WHERE username=?', (salt.hex(), digest, owner['subject']))
            db.execute('UPDATE principals SET enabled=1, scopes=?, policy_version=policy_version+1 WHERE id=?', (json.dumps(SCOPES), owner['principal_id']))
            db.execute('UPDATE sessions SET revoked=1 WHERE principal_id=?', (owner['principal_id'],))
            db.execute("INSERT INTO adapter_status (adapter_id,enabled) VALUES ('local-password',1) ON CONFLICT(adapter_id) DO UPDATE SET enabled=1,version=version+1")
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('migration_deadline', '0')")
        log_event('auth_owner_recovered', principal_id=owner['principal_id'])
        return owner['subject']
