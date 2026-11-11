"""Managed identity, explicit membership, and source authority for Data Studio."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from termx.data.store import DataStore, identifier_for
from termx.identity import Identity

_HIDDEN_KEYS = {'password', 'passwd', 'api_key', 'apikey', 'secret', 'client_secret',
                'access_token', 'refresh_token', 'authorization', 'dsn', 'connection_string',
                'credential_ref', 'credential_id', 'credentials'}


def public(value: Any) -> Any:
    """Serialize metadata without opaque secret handles or credential material."""
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()
                if str(key).lower().replace('-', '_') not in _HIDDEN_KEYS and not str(key).startswith('_')}
    if isinstance(value, (list, tuple)):
        return [public(item) for item in value]
    return value


class DataService:
    def __init__(self, state, directory: Path | str):
        self.state = state
        self.store = DataStore(directory)
        self.directory = self.store.directory
        self._runtime = None
        self._bi = None
        self._copilot = None

    # Deferred imports avoid cycles between policy, typed tools, and execution.
    @property
    def runtime(self):
        if self._runtime is None:
            from termx.data.runtime import DataRuntime
            self._runtime = DataRuntime(self)
        return self._runtime

    @property
    def bi(self):
        if self._bi is None:
            from termx.data.bi import BIService
            self._bi = BIService(self)
        return self._bi

    @property
    def copilot(self):
        if self._copilot is None:
            from termx.data.copilot import DataCopilot
            self._copilot = DataCopilot(self, self.state)
        return self._copilot

    async def close(self):
        for component in (self._copilot, self._runtime):
            if component is not None and hasattr(component, 'close'):
                await component.close()

    public = staticmethod(public)

    def live_actor(self, actor):
        if not actor or not getattr(actor, 'id', None):
            raise HTTPException(401, 'Managed sign-in required')
        identity = self.state.identity
        # A background tool cannot preserve access using a stale Principal or
        # an execution-session grant after its interactive session is locked.
        session_id = getattr(actor, 'authority_session_id', None)
        if session_id:
            current = identity.session_by_id(session_id)
            live = current.principal if current and current.principal.id == actor.id else None
        else:
            live = identity.principal_by_id(actor.id)
        if live is None:
            raise HTTPException(401, 'Session expired, locked, or revoked; sign in again')
        return live

    @staticmethod
    def membership_id(wid: str, principal_id: str) -> str:
        return identifier_for('member', wid, principal_id)

    def _membership(self, wid: str, principal_id: str):
        record = self.store.get('member', self.membership_id(wid, principal_id))
        return record if record and record['workspace_id'] == wid and not record.get('archived') else None

    def require(self, actor, wid: str, *, write: bool = False, owner: bool = False):
        actor = self.live_actor(actor)
        workspace = self.store.get('workspace', wid)
        member = self._membership(wid, actor.id)
        # Do not reveal whether a guessed workspace exists to outsiders.
        if not workspace or workspace.get('archived') or workspace['workspace_id'] != wid or not member:
            raise HTTPException(404, 'Data workspace not found')
        role = member.get('role')
        if role not in {'owner', 'editor', 'viewer'}:
            raise HTTPException(403, 'Data workspace access revoked')
        if owner and role != 'owner':
            raise HTTPException(403, 'Workspace owner required')
        if write and role not in {'owner', 'editor'}:
            raise HTTPException(403, 'Workspace editor required')
        return {**workspace, 'role': role}

    def require_connection(self, actor, wid: str, cid: str, *, report: bool = False):
        workspace = self.require(actor, wid)
        record = self.store.get('connection', cid)
        if not record or record['workspace_id'] != wid or record.get('archived') or record.get('revoked'):
            raise HTTPException(404, 'Data connection not found')
        if workspace['role'] == 'viewer':
            if not report or not record.get('shared_read', False):
                raise HTTPException(403, 'Published report access to a shared connection required')
        elif record.get('created_by') != actor.id and not record.get('shared_read', False):
            raise HTTPException(403, 'Connection owner must explicitly share read access')
        return record

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise HTTPException(422, 'Name must contain 1–200 characters')
        return name.strip()

    def create_workspace(self, actor, name: str):
        actor = self.live_actor(actor)
        import uuid
        wid = uuid.uuid4().hex
        with self.store.transaction() as db:
            workspace = self.store._create(db, 'workspace', wid, actor.id, {'name': self._name(name)}, wid)
            self.store._create(db, 'member', wid, actor.id,
                               {'principal_id': actor.id, 'display_name': actor.display_name, 'role': 'owner'},
                               self.membership_id(wid, actor.id))
            self.store._audit(db, actor.id, wid, 'workspace.created', wid)
        return public({**workspace, 'role': 'owner'})

    def workspaces(self, actor):
        actor = self.live_actor(actor)
        # Memberships are indexed by principal through stable IDs. Query only
        # current memberships; there is no automatic host administrator grant.
        with self.store._read() as db:
            rows = db.execute('''SELECT workspace_id FROM data_records WHERE kind='member'
                AND json_extract(body,'$.principal_id')=? AND COALESCE(json_extract(body,'$.archived'),0)=0
                ORDER BY created_at,id''', (actor.id,)).fetchall()
        result = []
        for row in rows:
            try:
                result.append(public(self.require(actor, row['workspace_id'])))
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        return result

    def _owner_in_transaction(self, db, actor_id: str, wid: str):
        workspace = self.store._get(db, 'workspace', wid)
        member = self.store._get(db, 'member', self.membership_id(wid, actor_id))
        if not workspace or workspace.get('archived') or not member or member.get('archived'):
            raise HTTPException(404, 'Data workspace not found')
        if member.get('role') != 'owner':
            raise HTTPException(403, 'Workspace owner required')
        return workspace

    def update_workspace(self, actor, wid: str, revision: int, name: str):
        self.require(actor, wid, owner=True)
        with self.store.transaction() as db:
            self._owner_in_transaction(db, actor.id, wid)
            record = self.store._update(db, 'workspace', wid, {'name': self._name(name)}, revision)
            self.store._audit(db, actor.id, wid, 'workspace.updated', wid)
        return public({**record, 'role': 'owner'})

    def archive_workspace(self, actor, wid: str, revision: int):
        self.require(actor, wid, owner=True)
        with self.store.transaction() as db:
            self._owner_in_transaction(db, actor.id, wid)
            record = self.store._update(db, 'workspace', wid, {'archived': True}, revision)
            self.store._audit(db, actor.id, wid, 'workspace.archived', wid)
        return public({**record, 'role': 'owner'})

    def members(self, actor, wid: str):
        self.require(actor, wid)
        result = []
        for record in self.store.list('member', wid):
            principal = self.state.identity.principal_by_id(record['principal_id'])
            result.append(public({**record, 'display_name': principal.display_name if principal else record.get('display_name', ''),
                                  'enabled': principal is not None}))
        return result

    def add_member(self, actor, wid: str, *, username: str | None = None,
                   principal_id: str | None = None, role: str = 'viewer'):
        self.require(actor, wid, owner=True)
        if bool(username) == bool(principal_id):
            raise HTTPException(422, 'Provide one exact existing username or principal ID')
        if role not in {'editor', 'viewer'}:
            raise HTTPException(422, 'New members must be editors or viewers')
        principal = (self.state.identity.principal_by_id(principal_id) if principal_id else
                     self.state.identity.principal_for(Identity('termx:local', username.strip(), 'password')))
        if principal is None:
            raise HTTPException(404, 'Existing managed user not found')
        mid = self.membership_id(wid, principal.id)
        with self.store.transaction() as db:
            self._owner_in_transaction(db, actor.id, wid)
            existing = self.store._get(db, 'member', mid)
            body = {'principal_id': principal.id, 'display_name': principal.display_name, 'role': role, 'archived': False}
            if existing and not existing.get('archived'):
                raise HTTPException(409, 'This user is already a workspace member')
            record = (self.store._update(db, 'member', mid, body, existing['revision']) if existing else
                      self.store._create(db, 'member', wid, actor.id, body, mid))
            self.store._audit(db, actor.id, wid, 'member.added', mid, {'principal_id': principal.id, 'role': role})
        return public(record)

    def change_member(self, actor, wid: str, mid: str, revision: int, *, role: str | None = None, remove: bool = False):
        self.require(actor, wid, owner=True)
        if not remove and role not in {'owner', 'editor', 'viewer'}:
            raise HTTPException(422, 'Invalid workspace role')
        with self.store.transaction() as db:
            self._owner_in_transaction(db, actor.id, wid)
            record = self.store._get(db, 'member', mid)
            if not record or record['workspace_id'] != wid or record.get('archived'):
                raise HTTPException(404, 'Workspace member not found')
            if record['revision'] != revision:
                raise HTTPException(409, 'This membership changed; reload before saving')
            if record['role'] == 'owner' and (remove or role != 'owner'):
                owners = [member for member in self.store._list(db, 'member', wid) if member['role'] == 'owner']
                if len(owners) <= 1:
                    raise HTTPException(409, 'Keep at least one workspace owner')
            changes = {'archived': True} if remove else {'role': role}
            updated = self.store._update(db, 'member', mid, changes, revision)
            self.store._audit(db, actor.id, wid, 'member.removed' if remove else 'member.updated', mid,
                              {'principal_id': record['principal_id'], **changes})
        return public(updated)

    def connections(self, actor, wid: str):
        workspace = self.require(actor, wid)
        if workspace['role'] == 'viewer':
            return []
        result = []
        for record in self.store.list('connection', wid):
            try:
                self.require_connection(actor, wid, record['id'])
                result.append(public(record))
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
        return result

    def queries(self, actor, wid: str):
        workspace = self.require(actor, wid)
        if workspace['role'] == 'viewer':
            return []
        result = []
        for record in self.store.list('query', wid):
            try:
                self.require_connection(actor, wid, record.get('connection_id', ''))
                result.append(public(record))
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
        return result

    def snapshot(self, actor, wid: str):
        workspace = self.require(actor, wid)
        result = {'workspace': public(workspace), 'members': self.members(actor, wid),
                  'connections': self.connections(actor, wid), 'queries': self.queries(actor, wid),
                  'reports': self.bi.reports(actor, wid), 'dashboards': self.bi.dashboards(actor, wid),
                  'activity': public(self.store.activity(wid)) if workspace['role'] == 'owner' else []}
        self.require(actor, wid)
        return result
