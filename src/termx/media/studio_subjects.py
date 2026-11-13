"""Owned project reference sets. Saving metadata never calls a model provider."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import unicodedata
import uuid
import warnings
from contextlib import contextmanager
from pathlib import Path
from time import time
from typing import Annotated

from fastapi import HTTPException, Path as PathParam, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from termx.auth import extract_passcode
from termx.media.studio_projects import project_mutation
from termx.private_files import protect_private_path

IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_PIXELS = 32_000_000
MAX_SUBJECTS = 100
IMAGE_FORMATS = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}
ImageId = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')]


class StudioSubjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str | None = Field(default=None, min_length=1, max_length=128, strict=True)
    name: str = Field(min_length=1, max_length=80, strict=True)
    description: str = Field(default='', max_length=1000, strict=True)
    image_ids: list[ImageId] = Field(min_length=1, max_length=5, strict=True)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('Use a visible name without control characters')
        return value

    @field_validator('description')
    @classmethod
    def clean_description(cls, value):
        if any(unicodedata.category(c).startswith('C') and c not in '\n\t' for c in value):
            raise ValueError('Description contains control characters')
        return value.strip()

    @field_validator('image_ids')
    @classmethod
    def unique_images(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('Choose distinct reference images')
        return value


class StudioSubjectStore:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.path = Path(artifacts.root) / 'studio-subjects.sqlite3'
        # Protect the directory first so SQLite journals inherit its private policy.
        protect_private_path(self.path.parent, directory=True)
        # Create and protect the file before SQLite opens it, including first use.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        protect_private_path(self.path)
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS studio_subjects (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, project TEXT,
                name TEXT NOT NULL, description TEXT NOT NULL,
                image_ids TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS studio_subjects_scope
                ON studio_subjects(owner,project,created DESC,id DESC);""")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def public(row):
        return {'id': row['id'], 'project_id': row['project'], 'name': row['name'],
                'description': row['description'], 'image_ids': json.loads(row['image_ids']),
                'created': row['created']}

    def owned(self, owner, identifier):
        with self.db() as db:
            row = db.execute('SELECT * FROM studio_subjects WHERE owner=? AND id=?', (owner, identifier)).fetchone()
        if row is None:
            raise HTTPException(404, 'Saved reference set not found')
        return self.public(row)

    def list(self, owner, project):
        with self.db() as db:
            rows = db.execute('SELECT * FROM studio_subjects WHERE owner=? AND project IS ? '
                              'ORDER BY created DESC,id DESC LIMIT ?', (owner, project, MAX_SUBJECTS)).fetchall()
        return [self.public(row) for row in rows]

    def validate_image(self, owner, project, identifier):
        item = self.artifacts.get(owner, identifier)
        if item['project'] != project:
            raise HTTPException(403, 'Reference image belongs to another project')
        if item['kind'] not in {'input', 'image'} or item['mime'] not in IMAGE_FORMATS:
            raise HTTPException(400, 'Choose a binary PNG, JPEG or WebP reference image')
        # Check the bound before reading bytes. Paths stay inside this service.
        with self.artifacts.db() as db:
            row = db.execute('SELECT blob FROM versions WHERE artifact=? AND version=?',
                             (identifier, item['version'])).fetchone()
        from PIL import Image
        try:
            if not row or not row['blob'] or not 0 < Path(row['blob']).stat().st_size <= IMAGE_BYTES:
                raise HTTPException(400, 'Reference images must be between 1 byte and 8 MiB')
            raw, mime = self.artifacts.binary(owner, identifier, item['version'])
            if not 0 < len(raw) <= IMAGE_BYTES:
                raise HTTPException(400, 'Reference images must be between 1 byte and 8 MiB')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != IMAGE_FORMATS[mime] or image.width * image.height > IMAGE_PIXELS or getattr(image, 'n_frames', 1) != 1:
                        raise ValueError('Unsupported image format or dimensions')
                    image.verify()
                # Header verification alone can accept truncated JPEG data.
                with Image.open(io.BytesIO(raw)) as decoded:
                    decoded.load()
        except HTTPException:
            raise
        except (OSError, ValueError, KeyError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(400, 'Reference image cannot be read as a static PNG, JPEG or WebP') from exc

    def create(self, owner, body: StudioSubjectInput):
        for identifier in body.image_ids:
            self.validate_image(owner, body.project_id, identifier)
        identifier = uuid.uuid4().hex
        created = time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('SELECT COUNT(*) FROM studio_subjects WHERE owner=? AND project IS ?',
                               (owner, body.project_id)).fetchone()[0]
            if count >= MAX_SUBJECTS:
                raise HTTPException(409, 'This project has 100 saved reference sets. Remove a set before saving another.')
            db.execute('INSERT INTO studio_subjects VALUES(?,?,?,?,?,?,?)',
                       (identifier, owner, body.project_id, body.name, body.description, json.dumps(body.image_ids), created))
        return self.owned(owner, identifier)

    def delete(self, owner, identifier):
        # Only metadata is removed. Shared uploaded image artifacts remain.
        with self.db() as db:
            result = db.execute('DELETE FROM studio_subjects WHERE owner=? AND id=?', (owner, identifier))
            if not result.rowcount:
                raise HTTPException(404, 'Saved reference set not found')


def mount_studio_subjects(router, state, actor):
    store = state.studio_subjects = StudioSubjectStore(state.artifacts)

    @router.get('/studio/subjects')
    def list_subjects(request: Request, response: Response,
                      project_id: str | None = Query(default=None, min_length=1, max_length=128)):
        response.headers['Cache-Control'] = 'no-store'
        return store.list(actor(request, project_id, 'agent-view'), project_id)

    @router.post('/studio/subjects', status_code=201)
    def create_subject(body: StudioSubjectInput, request: Request, response: Response):
        owner = actor(request, body.project_id, 'agent-run')
        response.headers['Cache-Control'] = 'no-store'
        with project_mutation(state, owner, body.project_id):
            return store.create(owner, body)

    @router.delete('/studio/subjects/{identifier}', status_code=204)
    def delete_subject(request: Request,
                       identifier: str = PathParam(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')):
        # Resolve ownership before authorizing the stored project. No global
        # grant is needed, and guessed IDs never reveal another owner's scope.
        raw = extract_passcode(request.headers.get('x-termx-passcode'), request.headers.get('authorization'))
        current = state.identity.resolve(raw)
        if not current:
            raise HTTPException(401, 'Managed sign-in required')
        owner = current.principal.id
        subject = store.owned(owner, identifier)
        if actor(request, subject['project_id'], 'agent-run') != owner:
            raise HTTPException(401, 'Managed sign-in changed')
        with project_mutation(state, owner, subject['project_id']):
            store.delete(owner, identifier)
        return Response(status_code=204, headers={'Cache-Control': 'no-store'})
