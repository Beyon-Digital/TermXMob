"""Saved views: tables, pivots and segments with revisions, references, run history, drill-down and export.

A view is a `data_records` row of kind `view` holding a validated QuerySpec. Every create, edit and archive also
writes one append-only row to `data_view_revisions` in the same transaction. Reference integrity (no segment
cycle, no archive under a live reference, unique names, the 500 view cap) is checked inside the write
transaction, so two concurrent saves cannot defeat it. Authority is re-derived on every call from live
membership and the connection's sharing state; nothing is cached.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from termx.data.dialects import get_dialect
from termx.data.drill import DrillError, drill_spec
from termx.data.query_spec import Aggregate, QuerySpec, SpecError, SpecErrors, compile_spec
from termx.data.segments import (ResolvedSegment, check_references, exceeds_limits, references_of, references_of_all,
                                 resolve_record)
from termx.data.store import timestamp
from termx.data.view_diff import diff

KINDS = ('table', 'pivot', 'segment')
MAX_VIEWS = 500
MAX_SPEC_BYTES = 100_000
MAX_CLOSURE = 64
NOT_FOUND = 'View not found.'


def coded(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {'code': code, 'message': message, **extra})


def canonical_spec(spec: QuerySpec) -> dict:
    """The spec as stored and returned: aliases applied, unset optional keys dropped, pivot columns explicit."""
    body = spec.model_dump(by_alias=True, mode='json', exclude_none=True)
    if body.get('pivot') is not None:
        body['pivot'].setdefault('columns', None)
    return body


def slug(name: str) -> str:
    text = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60].strip('-')
    return text or 'view'


class ViewService:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    # --- authority ---------------------------------------------------------------------------------------
    @property
    def runtime(self):
        return self.service.runtime

    @property
    def queries(self):
        return self.service.query

    def _role(self, actor, wid: str) -> str:
        return self.service.require(actor, wid)['role']

    def _usable(self, actor, wid: str, connection_id: str):
        """The connection when this person may use it (a viewer needs it shared), else None."""
        try:
            return self.service.require_connection(actor, wid, connection_id, report=True)
        except HTTPException as error:
            if error.status_code in (403, 404):
                return None
            raise

    def _load(self, wid: str, vid: str) -> dict:
        record = self.store.get('view', vid) if isinstance(vid, str) else None
        if not record or record['workspace_id'] != wid or record.get('archived'):
            raise HTTPException(404, NOT_FOUND)
        return record

    def authorize_run(self, actor, wid: str, vid: str) -> dict:
        """The view's connection when the person may run the view now. 404 hides views they cannot use."""
        self._role(actor, wid)
        record = self._load(wid, vid)
        connection = self._usable(actor, wid, record['connection_id'])
        if connection is None:
            raise HTTPException(404, NOT_FOUND)
        return connection

    def _authorize_edit(self, actor, wid: str, record: dict) -> dict:
        self.service.require(actor, wid, write=True)
        connection = self._usable(actor, wid, record['connection_id'])
        if connection is None:
            raise HTTPException(404, NOT_FOUND)
        return connection

    def _editor_in_transaction(self, db, actor, wid: str) -> str:
        """The role again, read inside the write transaction so a removed member cannot save."""
        workspace = self.store._get(db, 'workspace', wid)
        member = self.store._get(db, 'member', self.service.membership_id(wid, actor.id))
        if not workspace or workspace.get('archived') or not member or member.get('archived'):
            raise HTTPException(404, 'Data workspace not found')
        if member.get('role') not in ('owner', 'editor'):
            raise HTTPException(403, 'Workspace editor required')
        return member['role']

    # --- shapes ------------------------------------------------------------------------------------------
    @staticmethod
    def _person(record: dict, key: str) -> dict:
        return {'id': record.get(key) or '', 'name': record.get(key + '_name') or ''}

    def _summary(self, record: dict, role: str, used_by_count: int) -> dict:
        return {'id': record['id'], 'kind': record['view_kind'], 'name': record['name'], 'description': record.get('description', ''),
                'connection_id': record['connection_id'], 'base_table': record['base_table'], 'revision': record['revision'],
                'created_by': self._person(record, 'created_by'), 'updated_by': self._person(record, 'updated_by'),
                'created_at': record['created_at'], 'updated_at': record['updated_at'],
                'used_by_count': used_by_count if role != 'viewer' else 0,
                'can_edit': role in ('owner', 'editor'), 'can_run': True}

    def _detail(self, record: dict, role: str) -> dict:
        referrers = self._referrers(record['workspace_id'], record['id']) if role != 'viewer' else []
        references = []
        for sid in record.get('references', []):
            found = self.store.get('view', sid)
            alive = bool(found and found['workspace_id'] == record['workspace_id'] and not found.get('archived'))
            references.append({'id': sid, 'name': found['name'] if alive else 'Deleted segment', 'state': 'ok' if alive else 'missing'})
        return {**self._summary(record, role, len(referrers)), 'spec': record['spec'], 'references': references,
                'used_by': [{'id': r['id'], 'kind': r['view_kind'], 'name': r['name']} for r in referrers]}

    def _referrers(self, wid: str, vid: str, db=None) -> list[dict]:
        if db is not None:
            return self.store._view_referrers(db, wid, vid)
        with self.store._read() as read:
            return self.store._view_referrers(read, wid, vid)

    # --- reads -------------------------------------------------------------------------------------------
    def list(self, actor, wid: str, kind: str | None = None, connection_id: str | None = None) -> dict:
        role = self._role(actor, wid)
        records = self.store.list('view', wid)
        counts: dict[str, int] = {}
        for record in records:
            for sid in record.get('references', []):
                counts[sid] = counts.get(sid, 0) + 1
        usable: dict[str, bool] = {}
        views = []
        for record in records:
            if (kind and record['view_kind'] != kind) or (connection_id and record['connection_id'] != connection_id):
                continue
            cid = record['connection_id']
            if cid not in usable:
                usable[cid] = self._usable(actor, wid, cid) is not None
            if usable[cid]:
                views.append(self._summary(record, role, counts.get(record['id'], 0)))
        return {'views': views}

    def get(self, actor, wid: str, vid: str) -> dict:
        role = self._role(actor, wid)
        record = self._load(wid, vid)
        if self._usable(actor, wid, record['connection_id']) is None:
            raise HTTPException(404, NOT_FOUND)
        return self._detail(record, role)

    # --- segments ----------------------------------------------------------------------------------------
    def load_segments(self, wid: str, ids: list[str], db=None) -> dict[str, ResolvedSegment]:
        """The transitive closure of the segments a spec uses. Anything archived, foreign, malformed or beyond
        the cap is simply absent, which the compiler reports as `segment_missing`."""
        found: dict[str, ResolvedSegment] = {}
        pending, seen = list(ids), set()
        read = None
        if db is None:
            read = self.store._read()
            db = read.__enter__()
        try:
            while pending and len(seen) < MAX_CLOSURE:
                sid = pending.pop()
                if not isinstance(sid, str) or sid in seen or len(sid) > 64:
                    continue
                seen.add(sid)
                record = self.store._get(db, 'view', sid)
                if not record or record['workspace_id'] != wid:
                    continue
                segment = resolve_record(record)
                if segment is not None:
                    found[sid] = segment
                    pending += references_of(segment.filters)
        finally:
            if read is not None:
                read.__exit__(None, None, None)
        return found

    # --- validation --------------------------------------------------------------------------------------
    @staticmethod
    def _check_kind(kind: str, spec: QuerySpec) -> QuerySpec:
        if kind not in KINDS:
            raise coded(422, 'invalid_view', 'The view kind must be table, pivot or segment.')
        if kind == 'pivot' and spec.pivot is None:
            raise coded(422, 'invalid_view', 'A pivot view needs a pivot.')
        if kind == 'table' and spec.pivot is not None:
            raise coded(422, 'invalid_view', 'A table view cannot contain a pivot. Save it as a pivot.')
        if kind == 'segment':
            if spec.select or spec.group_by or spec.aggregates or spec.sort or spec.pivot is not None:
                raise coded(422, 'invalid_view', 'A segment is only a set of filters. Remove the columns, summary and sorting.')
            return spec.model_copy(update={'limit': 200, 'offset': 0})
        return spec

    @staticmethod
    def _text(value: object, label: str, low: int, high: int) -> str:
        if not isinstance(value, str):
            raise coded(422, 'invalid_view', f'{label} must be text.')
        text = value.strip()
        if not low <= len(text) <= high or any(ord(c) < 32 or ord(c) == 127 for c in text):
            how = f'{low} to {high} characters' if low else f'at most {high} characters'
            raise coded(422, 'invalid_view', f'{label} must be {how}, without control characters.')
        return text

    async def _prepare(self, actor, wid: str, kind: str, spec: QuerySpec, view_id: str | None) -> tuple[QuerySpec, dict, list[str]]:
        """Everything that can be checked without writing: connection authority, kind rules, the schema,
        the reference graph and the compile. Returns the normalised spec, its connection and its references."""
        spec = self._check_kind(kind, spec)
        connection = self.service.require_connection(actor, wid, spec.source)
        dialect = get_dialect(connection['engine'])
        self.runtime.assert_connection_source(connection, wid)
        if self.queries.cached_index(wid, connection) is None and not self.runtime.read_verified(connection):
            raise coded(409, 'source_untested', 'Test this source first to verify its current read-only role and session.')
        _, index, _ = await self.queries.schema_index(actor, wid, spec.source)
        direct = references_of(spec.filters)
        segments = self.load_segments(wid, direct)
        errors = check_references(view_id, spec.source, spec.from_.table, direct, segments.get)
        if not errors:
            try:
                compile_spec(spec, index, dialect, segments=segments)
            except SpecErrors as error:
                errors = error.errors
        if errors:
            raise coded(422, 'invalid_spec', errors[0].message, errors=[e.as_dict() for e in errors])
        return spec, connection, sorted(direct)

    def _graph_errors(self, db, wid: str, view_id: str | None, spec: QuerySpec, direct: list[str]) -> list[SpecError]:
        segments = self.load_segments(wid, direct, db)
        return check_references(view_id, spec.source, spec.from_.table, direct, segments.get)

    def _breaks_referrers(self, db, wid: str, vid: str, new_spec: QuerySpec, name: str, revision: int) -> list[tuple[dict, str]]:
        """Views, near or far, that use this segment and would hit a compile-time size limit (nesting depth, total
        segments, total filters) once it has `new_spec`. `check_references` only walks downward from the segment
        being saved, so this walks upward through every view that uses it."""
        found, seen, todo = [], {vid}, [vid]
        while todo and len(seen) <= MAX_VIEWS:
            for referrer in self._referrers(wid, todo.pop(), db):
                if referrer['id'] not in seen:
                    seen.add(referrer['id'])
                    todo.append(referrer['id'])
                    found.append(referrer)
        override = ResolvedSegment(vid, name, revision, new_spec.source, new_spec)
        broken = []
        for referrer in found:
            try:
                rspec = QuerySpec.model_validate(referrer['spec'])
            except ValidationError:
                continue
            segments = self.load_segments(wid, references_of(rspec.filters) + references_of(new_spec.filters), db)
            segments[vid] = override
            reason = exceeds_limits(references_of_all(rspec.filters), segments.get)
            if reason:
                broken.append((referrer, reason))
        return broken

    @staticmethod
    def _check_move(actor, record: dict, connection: dict | None) -> None:
        """A view may move to another source only when the people who can see it today can still use it there:
        the new source is shared, or the person moving it created both."""
        if connection is None or connection['id'] == record['connection_id']:
            return
        mine = record.get('created_by') == actor.id and connection.get('created_by') == actor.id
        if not (connection.get('shared_read') or mine):
            raise coded(403, 'source_not_shared', 'That source is private to its owner, so the people who use this view could no longer '
                        'open it. Ask the owner to share the source first, or save this as a new view.')

    # --- writes ------------------------------------------------------------------------------------------
    async def create(self, actor, wid: str, kind: str, name: str, description: str, spec: QuerySpec) -> dict:
        role = self.service.require(actor, wid, write=True)['role']
        name, description = self._text(name, 'The name', 1, 200), self._text(description or '', 'The description', 0, 1000)
        spec, connection, references = await self._prepare(actor, wid, kind, spec, None)
        stored = canonical_spec(spec)
        if len(str(stored).encode()) > MAX_SPEC_BYTES:
            raise coded(422, 'invalid_view', 'This view is too large to save.')
        person = self.service.live_actor(actor)
        return await asyncio.to_thread(self._commit_create, person, wid, role, kind, name, description, spec, stored, connection, references)

    def _commit_create(self, actor, wid, role, kind, name, description, spec, stored, connection, references) -> dict:
        with self.store.transaction() as db:
            role = self._editor_in_transaction(db, actor, wid)
            self._assert_unique(db, wid, kind, name, None)
            count = db.execute('''SELECT COUNT(*) FROM data_records WHERE kind='view' AND workspace_id=?
                AND COALESCE(json_extract(body,'$.archived'),0)=0''', (wid,)).fetchone()[0]
            if count >= MAX_VIEWS:
                raise coded(409, 'view_limit', f'A workspace can hold {MAX_VIEWS} views. Delete one you no longer use.')
            errors = self._graph_errors(db, wid, None, spec, references)
            if errors:
                raise coded(422, 'invalid_spec', errors[0].message, errors=[e.as_dict() for e in errors])
            body = {'view_kind': kind, 'name': name, 'name_key': name.casefold(), 'description': description,
                    'connection_id': connection['id'], 'base_table': spec.from_.table, 'spec': stored, 'references': references,
                    'created_by_name': actor.display_name, 'updated_by': actor.id, 'updated_by_name': actor.display_name,
                    'archived': False}
            record = self.store._create(db, 'view', wid, actor.id, body)
            snapshot = {'view_kind': kind, 'name': name, 'description': description, 'spec': stored}
            self.store._revision_insert(db, view_id=record['id'], revision=1, workspace_id=wid, actor_id=actor.id,
                                        actor_name=actor.display_name, action='created', name=name, snapshot=snapshot, changes=[])
            self.store._audit(db, actor.id, wid, 'view.created', record['id'], {'kind': kind})
        return self._detail(record, role)

    def _assert_unique(self, db, wid: str, kind: str, name: str, own_id: str | None) -> None:
        row = db.execute('''SELECT id FROM data_records WHERE kind='view' AND workspace_id=?
            AND COALESCE(json_extract(body,'$.archived'),0)=0 AND json_extract(body,'$.view_kind')=?
            AND json_extract(body,'$.name_key')=? AND id<>?''', (wid, kind, name.casefold(), own_id or '')).fetchone()
        if row:
            raise coded(409, 'name_taken', f"There is already a {kind} named '{name}'. Choose another name.")

    @staticmethod
    def _conflict(record: dict, expected: int) -> HTTPException:
        who, when = record.get('updated_by_name') or 'Someone', record['updated_at']
        clock = when[11:16] if len(when) >= 16 else when
        return coded(409, 'revision_conflict', f'{who} changed this view at {clock} UTC. Reload to see the latest version.',
                     current_revision=record['revision'], updated_by={'id': record.get('updated_by') or '', 'name': who}, updated_at=when)

    async def update(self, actor, wid: str, vid: str, revision: int, name: str | None, description: str | None,
                     spec: QuerySpec | None) -> dict:
        self.service.require(actor, wid, write=True)
        record = self._load(wid, vid)
        self._authorize_edit(actor, wid, record)
        if record['revision'] != revision:
            raise self._conflict(record, revision)
        if name is not None:
            name = self._text(name, 'The name', 1, 200)
        if description is not None:
            description = self._text(description, 'The description', 0, 1000)
        connection, references, stored = None, None, None
        if spec is not None:
            spec, connection, references = await self._prepare(actor, wid, record['view_kind'], spec, vid)
            self._check_move(self.service.live_actor(actor), record, connection)
            stored = canonical_spec(spec)
            if len(str(stored).encode()) > MAX_SPEC_BYTES:
                raise coded(422, 'invalid_view', 'This view is too large to save.')
        person = self.service.live_actor(actor)
        return await asyncio.to_thread(self._commit_update, person, wid, vid, revision, name, description, spec, stored, connection, references)

    def _commit_update(self, actor, wid, vid, revision, name, description, spec, stored, connection, references) -> dict:
        with self.store.transaction() as db:
            role = self._editor_in_transaction(db, actor, wid)
            record = self.store._get(db, 'view', vid)
            if not record or record['workspace_id'] != wid or record.get('archived'):
                raise HTTPException(404, NOT_FOUND)
            if record['revision'] != revision:
                raise self._conflict(record, revision)
            kind = record['view_kind']
            new_name = record['name'] if name is None else name
            new_description = record.get('description', '') if description is None else description
            new_spec = record['spec'] if stored is None else stored
            old = {'view_kind': kind, 'name': record['name'], 'description': record.get('description', ''), 'spec': record['spec']}
            new = {'view_kind': kind, 'name': new_name, 'description': new_description, 'spec': new_spec}
            changes = diff(old, new)
            moved = connection is not None and connection['id'] != record['connection_id']
            if moved:
                self._check_move(actor, record, self.store._get(db, 'connection', connection['id']))
                if not any(c['field'] == 'source' for c in changes):
                    changes.append({'field': 'source', 'op': 'changed', 'before': record['connection_id'], 'after': connection['id']})
            if not changes:
                return {**self._detail(record, role), **({'affected_views': len(self._referrers(wid, vid, db))} if kind == 'segment' else {})}
            if new_name != record['name']:
                self._assert_unique(db, wid, kind, new_name, vid)
            affected = None
            if kind == 'segment':
                referrers = self._referrers(wid, vid, db)
                affected = len(referrers)
                structural = {'source', 'from', 'joins'}
                if referrers and any(old['spec'].get(k) != new_spec.get(k) for k in structural):
                    raise coded(409, 'segment_in_use', f'{len(referrers)} view{"s use" if len(referrers) != 1 else " uses"} this segment, '
                                'so its table and combined tables cannot change. Remove it from them first.',
                                used_by=[{'id': r['id'], 'kind': r['view_kind'], 'name': r['name']} for r in referrers])
                if referrers and spec is not None:
                    broken = self._breaks_referrers(db, wid, vid, spec, new_name, revision + 1)
                    if broken:
                        count = len(broken)
                        raise coded(409, 'segment_in_use', f'{count} view{"s use" if count != 1 else " uses"} this segment in a way this change '
                                    f'would break: {broken[0][1]}. Simplify the segment, or the views that use it, first.',
                                    used_by=[{'id': r['id'], 'kind': r['view_kind'], 'name': r['name']} for r, _ in broken])
            updates: dict[str, Any] = {'name': new_name, 'name_key': new_name.casefold(), 'description': new_description,
                                       'updated_by': actor.id, 'updated_by_name': actor.display_name}
            if spec is not None:
                errors = self._graph_errors(db, wid, vid, spec, references)
                if errors:
                    raise coded(422, 'invalid_spec', errors[0].message, errors=[e.as_dict() for e in errors])
                updates.update(spec=stored, references=references, base_table=spec.from_.table, connection_id=connection['id'])
            updated = self.store._update(db, 'view', vid, updates, revision)
            self.store._revision_insert(db, view_id=vid, revision=updated['revision'], workspace_id=wid, actor_id=actor.id,
                                        actor_name=actor.display_name, action='updated', name=new_name, snapshot=new, changes=changes)
            self.store._audit(db, actor.id, wid, 'view.updated', vid, {'revision': updated['revision']})
            if moved:
                self.store._audit(db, actor.id, wid, 'view.source_changed', vid, {'from': record['connection_id'], 'to': connection['id']})
        result = self._detail(updated, role)
        if affected is not None:
            result['affected_views'] = affected
        return result

    def archive(self, actor, wid: str, vid: str, revision: int) -> dict:
        role = self.service.require(actor, wid, write=True)['role']
        record = self._load(wid, vid)
        self._authorize_edit(actor, wid, record)
        if role != 'owner' and record.get('created_by') != actor.id:
            raise HTTPException(403, 'Only the workspace owner or the person who created this view can delete it.')
        person = self.service.live_actor(actor)
        with self.store.transaction() as db:
            self._editor_in_transaction(db, person, wid)
            current = self.store._get(db, 'view', vid)
            if not current or current['workspace_id'] != wid or current.get('archived'):
                raise HTTPException(404, NOT_FOUND)
            if current['revision'] != revision:
                raise self._conflict(current, revision)
            referrers = self._referrers(wid, vid, db) if current['view_kind'] == 'segment' else []
            if referrers:
                raise coded(409, 'segment_in_use', f'{len(referrers)} view{"s use" if len(referrers) != 1 else " uses"} this segment. '
                            'Remove it from them first.',
                            used_by=[{'id': r['id'], 'kind': r['view_kind'], 'name': r['name']} for r in referrers])
            updated = self.store._update(db, 'view', vid, {'archived': True, 'updated_by': person.id, 'updated_by_name': person.display_name}, revision)
            snapshot = {'view_kind': current['view_kind'], 'name': current['name'], 'description': current.get('description', ''), 'spec': current['spec']}
            self.store._revision_insert(db, view_id=vid, revision=updated['revision'], workspace_id=wid, actor_id=person.id,
                                        actor_name=person.display_name, action='archived', name=current['name'], snapshot=snapshot, changes=[])
            self.store._audit(db, person.id, wid, 'view.archived', vid, {'revision': updated['revision']})
        return self._detail(updated, role)

    # --- history -----------------------------------------------------------------------------------------
    def history(self, actor, wid: str, vid: str, limit: int, before: int | None) -> dict:
        role = self._role(actor, wid)
        record = self._load(wid, vid)
        if self._usable(actor, wid, record['connection_id']) is None:
            raise HTTPException(404, NOT_FOUND)
        if role == 'viewer':
            raise HTTPException(403, 'Edit history is available to editors and owners.')
        rows = self.store.revisions(wid, vid, limit, before)
        page, more = rows[:limit], len(rows) > limit
        revisions = [{'revision': r['revision'], 'action': r['action'], 'at': r['created_at'],
                      'actor': {'id': r['actor_id'], 'name': r['actor_name']}, 'name': r['name'], 'changes': r['changes']} for r in page]
        return {'view_id': vid, 'revisions': revisions, 'next_before': page[-1]['revision'] if more and page else None}

    def runs(self, actor, wid: str, vid: str, limit: int, cursor: str | None) -> dict:
        role = self._role(actor, wid)
        self.authorize_run(actor, wid, vid)
        position = None
        if cursor is not None:
            stamp, _, jid = cursor.partition('|')
            if not stamp or not jid:
                raise HTTPException(422, 'The cursor is not valid.')
            position = (stamp, jid)
        rows = self.store.view_jobs(wid, vid, actor_id=None if role != 'viewer' else actor.id, limit=limit, cursor=position)
        page, more = rows[:limit], len(rows) > limit
        runs = []
        for row in page:
            terminal = row['status'] in ('completed', 'failed', 'cancelled', 'unknown')
            principal = self.service.state.identity.principal_by_id(row['actor_id'])
            runs.append({'job_id': row['id'], 'actor': {'id': row['actor_id'], 'name': row['actor_name'] or (principal.display_name if principal else '')},
                         'mine': row['actor_id'] == actor.id, 'view_revision': row['view_revision'], 'mode': row['mode'] or 'rows',
                         'status': row['status'], 'started_at': row['created_at'], 'finished_at': row['updated_at'] if terminal else None,
                         'duration_ms': row['duration_ms'], 'row_count': row['row_count'],
                         'truncated': None if row['truncated'] is None else bool(row['truncated']), 'error': row['error']})
        next_cursor = f"{page[-1]['created_at']}|{page[-1]['id']}" if more and page else None
        return {'view_id': vid, 'runs': runs, 'next_cursor': next_cursor}

    # --- running -----------------------------------------------------------------------------------------
    @staticmethod
    def _count_spec(kind: str, spec: QuerySpec) -> QuerySpec:
        if spec.pivot is not None:
            raise coded(422, 'invalid_view', 'A pivot cannot be counted. Run it to see its numbers.')
        if spec.group_by or spec.aggregates:
            raise coded(422, 'invalid_view', 'Counting is for views that list rows.')
        label = 'Members' if kind == 'segment' else 'Rows'
        return spec.model_copy(update={'select': [], 'sort': [], 'aggregates': [Aggregate(fn='count', label=label)], 'limit': 1, 'offset': 0})

    def _stored_spec(self, record: dict) -> QuerySpec:
        try:
            return QuerySpec.model_validate(record['spec'])
        except ValidationError:
            raise coded(422, 'invalid_view', 'This view can no longer be read. Open it and save it again.') from None

    async def _start(self, actor, wid: str, record: dict, connection: dict, spec: QuerySpec, request_id: str, mode: str) -> dict:
        if not self.runtime.read_verified(connection):
            self._untested()
        result = await self.queries.compile_for_view(actor, wid, spec, record['id'], connection)
        if not result['ok']:
            errors = result['errors']
            raise coded(422, 'invalid_spec', errors[0]['message'], errors=errors)
        output = {'columns': result['columns'], 'warnings': result['warnings']}
        definition = {'connection_id': connection['id'], 'sql': result['sql'], 'parameters': result['parameters'],
                      'column_kinds': [c['value_kind'] for c in result['columns']]}
        person = self.service.live_actor(actor)
        job = await self.runtime.run(actor, wid, definition, request_id, view_id=record['id'],
                                     extra={'view_revision': record['revision'], 'mode': mode, 'output': output,
                                            'actor_name': person.display_name})
        self.store.audit(actor.id, wid, 'view.run', record['id'], {'revision': record['revision'], 'mode': mode})
        return job

    @staticmethod
    def _untested():
        raise coded(409, 'source_untested', 'Test this source first to verify its current read-only role and session.')

    async def run(self, actor, wid: str, vid: str, request_id: str, mode: str = 'rows', offset: int | None = None) -> dict:
        connection = self.authorize_run(actor, wid, vid)
        record = self._load(wid, vid)
        get_dialect(connection['engine'])
        spec = self._stored_spec(record)
        if offset is not None:
            spec = spec.model_copy(update={'offset': offset})
        if mode == 'count':
            spec = self._count_spec(record['view_kind'], spec)
        return await self._start(actor, wid, record, connection, spec, request_id, mode)

    async def drill(self, actor, wid: str, vid: str, coordinates: dict, request_id: str | None) -> dict:
        role = self._role(actor, wid)
        connection = self.authorize_run(actor, wid, vid)
        if role == 'viewer':
            raise HTTPException(403, 'Underlying rows are available to editors and owners.')
        record = self._load(wid, vid)
        dialect = get_dialect(connection['engine'])
        view_spec = self._stored_spec(record)
        _, index, _ = await self.queries.schema_index(actor, wid, view_spec.source)
        tables = {view_spec.from_.alias: view_spec.from_.table, **{j.alias: j.table for j in view_spec.joins}}

        def kind_of(alias: str, column: str):
            info = index.tables.get(tables.get(alias, ''))
            found = info.columns.get(column) if info else None
            return found.kind if found else None
        try:
            spec_dict, exact, notes = drill_spec(record['view_kind'], record['spec'], coordinates, kind_of)
        except DrillError as error:
            raise coded(422, 'bad_coordinates', str(error)) from None
        column = coordinates.get('column')
        if view_spec.pivot is not None and column is not None:
            # The value must be one of the columns the pivot shows now, not any value a caller can make up.
            shown = await self.queries.compile_for_view(actor, wid, view_spec, vid, connection)
            if not shown['ok']:
                raise coded(422, 'invalid_spec', shown['errors'][0]['message'], errors=shown['errors'])
            cells = [(c.get('column_value'), bool(c.get('column_empty'))) for c in shown['columns'] if c['role'] == 'pivot_cell']
            if (column.get('value'), bool(column.get('empty'))) not in cells:
                raise coded(422, 'bad_coordinates', 'That column is not in this pivot.')
        drill = QuerySpec.model_validate(spec_dict)
        compiled = await self.queries.compile_for_view(actor, wid, drill, vid, connection)
        if not compiled['ok']:
            raise coded(422, 'invalid_spec', compiled['errors'][0]['message'], errors=compiled['errors'])
        job = None
        if request_id:
            job = await self._start(actor, wid, record, connection, drill, request_id, 'rows')
        return {'spec': canonical_spec(drill), 'exact': exact, 'notes': notes, 'job': job}

    def export_csv(self, actor, wid: str, vid: str, jid: str) -> tuple[str, bool, str]:
        self.authorize_run(actor, wid, vid)
        record = self._load(wid, vid)
        job = self.store.get('job', jid) if isinstance(jid, str) else None
        if not job or job['workspace_id'] != wid or job.get('view_id') != vid:
            raise HTTPException(404, 'Query job not found.')
        data, truncated = self.runtime.export_csv(actor, wid, jid)
        return data, truncated, slug(record['name']) + '.csv'
