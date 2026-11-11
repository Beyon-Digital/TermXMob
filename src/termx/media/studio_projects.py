"""Private Studio collections: no filesystem enrollment, cascades or deletion.

Archiving is reversible. The same short mutation gate protects archive decisions
and provider reservations; no lock is held during a provider request.
"""
from __future__ import annotations

import threading
import unicodedata
import uuid
from contextlib import contextmanager, nullcontext
from time import time

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from termx.auth import extract_passcode


class StudioProjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=1000)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('Use a visible project name without control characters')
        return value

    @field_validator('description')
    @classmethod
    def clean_description(cls, value):
        if any(unicodedata.category(c).startswith('C') and c not in '\n\t' for c in value):
            raise ValueError('Description contains control characters')
        return value.strip()


class StudioProjectUpdate(StudioProjectInput):
    revision: int = Field(ge=1)


class StudioProjectRevision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=1)


def studio_project_id(identifier):
    return isinstance(identifier, str) and identifier.startswith('studio_')


def project_mutation(state, owner, project):
    store = getattr(state, 'studio_projects', None)
    return store.mutation(owner, project) if store and studio_project_id(project) else nullcontext()


class StudioProjectsStore:
    def __init__(self, studio, artifacts):
        self.studio, self.artifacts = studio, artifacts
        self.lock = threading.RLock()
        with studio.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS studio_projects (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                description TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                archived INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS studio_projects_owner ON studio_projects(owner,updated DESC,id);""")

    @staticmethod
    def public(row):
        return {'id': row['id'], 'name': row['name'], 'description': row['description'],
                'revision': row['revision'], 'archived': bool(row['archived']),
                'created_at': row['created'], 'updated_at': row['updated']}

    def owned(self, owner, identifier):
        with self.studio.db() as db:
            row = db.execute('SELECT * FROM studio_projects WHERE owner=? AND id=?', (owner, identifier)).fetchone()
        if row is None:
            raise HTTPException(404, 'Studio project not found')
        return self.public(row)

    def list(self, owner):
        with self.studio.db() as db:
            rows = db.execute('SELECT * FROM studio_projects WHERE owner=? ORDER BY archived,updated DESC,id', (owner,)).fetchall()
        return [self.public(row) for row in rows]

    def create(self, owner, body, *, claim=None):
        identifier, now = 'studio_' + uuid.uuid4().hex, time()
        with self.lock, self.studio.db() as db:
            db.execute('INSERT INTO studio_projects VALUES(?,?,?,?,1,0,?,?)',
                       (identifier, owner, body.name, body.description, now, now))
            if claim is not None:
                claim(identifier)
        return self.owned(owner, identifier)

    @contextmanager
    def mutation(self, owner, identifier):
        with self.lock:
            current = self.owned(owner, identifier)
            if current['archived']:
                raise HTTPException(409, 'Restore this Studio project before making changes or generating media')
            yield

    def change(self, owner, identifier, revision, *, body=None, archived=None):
        with self.lock, self.studio.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM studio_projects WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            if row is None:
                raise HTTPException(404, 'Studio project not found')
            if row['revision'] != revision:
                raise HTTPException(409, 'Studio project changed in another window. Reload projects before saving')
            if archived is True:
                active = db.execute("SELECT 1 FROM studio_jobs WHERE owner=? AND project=? AND status IN ('submitting','queued','running','unknown') LIMIT 1", (owner, identifier)).fetchone()
                with self.artifacts.db() as assets:
                    operation = assets.execute("SELECT 1 FROM operations WHERE owner=? AND project=? AND status IN ('executing','unknown') LIMIT 1", (owner, identifier)).fetchone()
                if active or operation:
                    raise HTTPException(409, 'This project has active generation or an unconfirmed provider outcome. Reconcile it before archiving')
            if body is not None:
                if row['archived']:
                    raise HTTPException(409, 'Restore this Studio project before editing it')
                db.execute('UPDATE studio_projects SET name=?,description=?,revision=revision+1,updated=? WHERE id=? AND owner=?',
                           (body.name, body.description, time(), identifier, owner))
            else:
                if not isinstance(archived, bool):
                    raise HTTPException(400, 'Choose whether to archive or restore this Studio project')
                db.execute('UPDATE studio_projects SET archived=?,revision=revision+1,updated=? WHERE id=? AND owner=?',
                           (int(archived), time(), identifier, owner))
        return self.owned(owner, identifier)


def mount_studio_projects(router, state):
    store = state.studio_projects = StudioProjectsStore(state.studio, state.artifacts)

    def identity(request):
        raw = extract_passcode(request.headers.get('x-termx-passcode'), request.headers.get('authorization'))
        current = state.identity.resolve(raw)
        if not current:
            raise HTTPException(401, 'Managed sign-in required')
        return raw, current.principal.id

    def access(request, identifier, scope='files-write'):
        raw, owner = identity(request)
        store.owned(owner, identifier)
        state.authorization.require(raw, scope, resource_kind='studio_project', resource_id=identifier)
        return owner

    @router.get('/studio/projects')
    def list_projects(request: Request, response: Response):
        raw, owner = identity(request)
        state.authorization.require_creation(raw, 'agent-view')
        rows = store.list(owner)
        result = []
        for row in rows:
            try:
                state.authorization.require(raw, 'agent-view', resource_kind='studio_project', resource_id=row['id'])
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
            else:
                result.append(row)
        response.headers['Cache-Control'] = 'no-store'
        return {'projects': result}

    @router.post('/studio/projects', status_code=201)
    def create_project(body: StudioProjectInput, request: Request, response: Response):
        raw, owner = identity(request)
        state.authorization.require_creation(raw, 'files-write')
        def claim(identifier):
            state.authorization.require_creation(raw, 'files-write')
            if identity(request)[1] != owner:
                raise HTTPException(403, 'Managed identity changed before creating this project')
            state.authorization.claim(raw, 'studio_project', identifier)
        project = store.create(owner, body, claim=claim)
        response.headers['Cache-Control'] = 'no-store'
        return project

    @router.put('/studio/projects/{identifier}')
    def update_project(identifier: str, body: StudioProjectUpdate, request: Request, response: Response):
        owner = access(request, identifier)
        response.headers['Cache-Control'] = 'no-store'
        return store.change(owner, identifier, body.revision, body=body)

    @router.post('/studio/projects/{identifier}/archive')
    def archive_project(identifier: str, body: StudioProjectRevision, request: Request, response: Response):
        owner = access(request, identifier)
        response.headers['Cache-Control'] = 'no-store'
        return store.change(owner, identifier, body.revision, archived=True)

    @router.post('/studio/projects/{identifier}/restore')
    def restore_project(identifier: str, body: StudioProjectRevision, request: Request, response: Response):
        owner = access(request, identifier)
        response.headers['Cache-Control'] = 'no-store'
        return store.change(owner, identifier, body.revision, archived=False)

    return store
