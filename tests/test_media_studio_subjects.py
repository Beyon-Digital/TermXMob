"""Saved-set metadata, scoped HTTP authority and bounded image validation."""
import io
import json
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from termx.media.http import mount_media
from termx.media.studio_subjects import StudioSubjectInput, StudioSubjectStore
from termx.private_files import private_path_permissions


def png():
    stream = io.BytesIO()
    Image.new('RGB', (3, 4), 'blue').save(stream, format='PNG')
    return stream.getvalue()


@pytest.fixture
def workspace(tmp_path):
    grants = {owner: {(scope, project) for scope in ('agent-view', 'agent-run') for project in (None, 'project-a', 'project-b')}
              for owner in ('alice', 'bob')}
    decisions = []
    def authorize(token, scope, project_id=None):
        decisions.append((token, scope, project_id))
        if (scope, project_id) not in grants.get(token, set()):
            raise HTTPException(403, 'Project access revoked')
    def no_credentials(*args):
        raise AssertionError('Saving reference sets must not access provider credentials')
    state = SimpleNamespace(delivery=SimpleNamespace(directory=tmp_path/'delivery'),
        identity=SimpleNamespace(resolve=lambda token: SimpleNamespace(principal=SimpleNamespace(id=token)) if token in grants else None),
        authorization=SimpleNamespace(require=authorize),
        agent_store=SimpleNamespace(get_provider=no_credentials, list_providers=no_credentials),
        credentials=SimpleNamespace(get=no_credentials))
    app = FastAPI(); mount_media(app, state)
    with TestClient(app) as client:
        yield client, state, grants, decisions


def image(state, owner='alice', project='project-a', **changes):
    args = dict(kind='input', title='Portrait', blob=png(), mime='image/png')
    args.update(changes)
    return state.artifacts.create(owner, project, **args)['id']


def body(image_ids, **changes):
    return dict(project_id='project-a', name='Remy', description='Blue jacket', image_ids=image_ids, **changes)


def test_http_crud_restarts_and_keeps_underlying_images(workspace):
    client, state, _, decisions = workspace
    photo = image(state)
    headers = {'Authorization': 'Bearer alice'}
    payload = body([photo]); payload['name'] = '  Remy  '; payload['description'] = '  Blue jacket  '
    response = client.post('/api/media/studio/subjects', headers=headers, json=payload)
    assert response.status_code == 201
    subject = response.json()
    assert subject['name'] == 'Remy' and subject['description'] == 'Blue jacket'
    assert set(subject) == {'id', 'project_id', 'name', 'description', 'image_ids', 'created'}
    assert subject['image_ids'] == [photo] and subject['created'] > 0
    assert response.headers['cache-control'] == 'no-store'
    listing = client.get('/api/media/studio/subjects?project_id=project-a', headers=headers)
    assert listing.json() == [subject] and listing.headers['cache-control'] == 'no-store'
    assert StudioSubjectStore(state.artifacts).list('alice', 'project-a') == [subject]
    assert private_path_permissions(state.artifacts.root)
    assert private_path_permissions(state.studio_subjects.path)
    if os.name != 'nt':
        assert stat.S_IMODE(state.artifacts.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(state.studio_subjects.path.stat().st_mode) == 0o600
    deleted = client.delete('/api/media/studio/subjects/' + subject['id'], headers=headers)
    assert deleted.status_code == 204 and deleted.content == b''
    assert client.get('/api/media/studio/subjects?project_id=project-a', headers=headers).json() == []
    assert state.artifacts.binary('alice', photo)[0] == png()
    assert ('alice', 'agent-run', 'project-a') in decisions


@pytest.mark.parametrize('existing', [False, True], ids=['first-use', 'existing-empty'])
def test_subject_metadata_is_private_before_open_and_journals_stay_private(tmp_path, monkeypatch, existing):
    root = tmp_path / 'workspace-artifacts'
    root.mkdir()
    if os.name != 'nt':
        root.chmod(0o755)
    path = root / 'studio-subjects.sqlite3'
    if existing:
        path.touch()
        path.chmod(0o666)
    connect = sqlite3.connect
    opened = []

    def private_connect(database, *args, **kwargs):
        assert Path(database) == path
        assert private_path_permissions(root)
        assert private_path_permissions(path)
        if os.name != 'nt':
            assert stat.S_IMODE(root.stat().st_mode) == 0o700
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        opened.append(Path(database))
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', private_connect)
    artifacts = SimpleNamespace(root=root)
    store = StudioSubjectStore(artifacts)
    assert opened == [path]
    with store.db() as db:
        db.execute('INSERT INTO studio_subjects VALUES(?,?,?,?,?,?,?)',
                   ('saved-set', 'alice', 'project-a', 'Saved', '', '["image-id"]', 10))
        journal = path.with_name(path.name + '-journal')
        assert journal.is_file()
        # Journals inherit the protected parent's owner/SYSTEM/admin policy.
        assert private_path_permissions(journal, require_protected=False)
    assert StudioSubjectStore(artifacts).list('alice', 'project-a') == [
        {'id': 'saved-set', 'project_id': 'project-a', 'name': 'Saved', 'description': '',
         'image_ids': ['image-id'], 'created': 10},
    ]
    assert len(opened) == 4


def test_http_identity_exact_owner_project_and_revocation(workspace):
    client, state, grants, decisions = workspace
    alice, bob = {'Authorization': 'Bearer alice'}, {'Authorization': 'Bearer bob'}
    photo = image(state)
    subject = client.post('/api/media/studio/subjects', headers=alice, json=body([photo])).json()
    assert client.get('/api/media/studio/subjects?project_id=project-a').status_code == 401
    assert client.post('/api/media/studio/subjects', json=body([photo])).status_code == 401
    assert client.delete('/api/media/studio/subjects/' + subject['id']).status_code == 401
    assert client.get('/api/media/studio/subjects?project_id=project-a', headers=bob).json() == []
    assert client.get('/api/media/studio/subjects?project_id=project-b', headers=alice).json() == []
    assert client.get('/api/media/studio/subjects', headers=alice).json() == []
    assert client.delete('/api/media/studio/subjects/' + subject['id'], headers=bob).status_code == 404
    assert client.post('/api/media/studio/subjects', headers=bob, json=body([photo])).status_code == 404
    foreign = image(state, project='project-b')
    assert client.post('/api/media/studio/subjects', headers=alice, json=body([foreign])).status_code == 403
    grants['alice'].remove(('agent-view', 'project-a'))
    assert client.get('/api/media/studio/subjects?project_id=project-a', headers=alice).status_code == 403
    grants['alice'].remove(('agent-run', 'project-a'))
    assert client.post('/api/media/studio/subjects', headers=alice, json=body([photo])).status_code == 403
    assert client.delete('/api/media/studio/subjects/' + subject['id'], headers=alice).status_code == 403
    # Removing all global grants must not prevent an owned project deletion.
    grants['alice'] = {('agent-run', 'project-a')}
    decisions.clear()
    assert client.delete('/api/media/studio/subjects/' + subject['id'], headers=alice).status_code == 204
    assert decisions == [('alice', 'agent-run', 'project-a')]


@pytest.mark.parametrize('change', [
    {'name': ''}, {'name': '   '}, {'name': 'x'*81}, {'name': 'hidden\nname'}, {'name': 'hidden\u202ename'},
    {'name': 12}, {'description': 'x'*1001}, {'description': 'nul\x00'}, {'image_ids': []},
    {'image_ids': ['a']*2}, {'image_ids': ['a','b','c','d','e','f']}, {'image_ids': [12]},
    {'image_ids': ['../secret']}, {'image_ids': ['x'*129]}, {'project_id': 'x'*129}, {'unknown': 'field'},
])
def test_http_input_bounds_precede_metadata_persistence(workspace, change):
    client, state, _, _ = workspace
    payload = body([image(state)]); payload.update(change)
    assert client.post('/api/media/studio/subjects', headers={'Authorization': 'Bearer alice'}, json=payload).status_code == 422
    assert state.studio_subjects.list('alice', 'project-a') == []


@pytest.mark.parametrize('mime,blob,kind', [
    ('image/jpeg', png(), 'input'), ('image/svg+xml', b'<svg/>', 'input'),
    ('image/png', b'not an image', 'input'), ('image/png', png(), 'audio'),
    ('image/png', b'', 'input'), ('image/png', b'x'*(8*1024*1024+1), 'input'),
], ids=['mime-content-mismatch', 'svg-content', 'invalid-image-bytes',
        'non-image-artifact-kind', 'empty-payload', 'oversized-8mib-plus-one'])
def test_saved_references_must_be_bounded_binary_images(workspace, mime, blob, kind):
    client, state, _, _ = workspace
    photo = image(state, mime=mime, blob=blob, kind=kind)
    response = client.post('/api/media/studio/subjects', headers={'Authorization': 'Bearer alice'}, json=body([photo]))
    assert response.status_code == 400
    assert state.studio_subjects.list('alice', 'project-a') == []


def test_nonbinary_animation_and_oversized_dimensions_rejected(workspace, monkeypatch):
    client, state, _, _ = workspace
    headers = {'Authorization': 'Bearer alice'}
    nonbinary = image(state, blob=None, content={'label': 'metadata'})
    assert client.post('/api/media/studio/subjects', headers=headers, json=body([nonbinary])).status_code == 400
    stream = io.BytesIO()
    Image.new('RGB', (3,4), 'red').save(stream, format='PNG', save_all=True,
        append_images=[Image.new('RGB',(3,4),'blue')], duration=100, loop=0)
    animation = image(state, blob=stream.getvalue())
    assert client.post('/api/media/studio/subjects', headers=headers, json=body([animation])).status_code == 400
    from termx.media import studio_subjects
    monkeypatch.setattr(studio_subjects, 'IMAGE_PIXELS', 5)
    assert client.post('/api/media/studio/subjects', headers=headers, json=body([image(state)])).status_code == 400


def test_lists_are_bounded_stably_ordered_and_cap_creation_per_exact_scope(workspace):
    client, state, _, _ = workspace
    store = state.studio_subjects
    photo = image(state)
    with store.db() as db:
        for number in range(101):
            db.execute('INSERT INTO studio_subjects VALUES(?,?,?,?,?,?,?)',
                       (f'set-{number:03}', 'alice', 'project-a', 'Set', '', json.dumps([photo]), 10))
        db.execute('INSERT INTO studio_subjects VALUES(?,?,?,?,?,?,?)', ('foreign', 'bob', 'project-a', 'Private', '', json.dumps([photo]), 999))
    rows = client.get('/api/media/studio/subjects?project_id=project-a', headers={'Authorization':'Bearer alice'}).json()
    assert len(rows) == 100 and [row['id'] for row in rows] == [f'set-{number:03}' for number in range(100,0,-1)]
    response = client.post('/api/media/studio/subjects', headers={'Authorization':'Bearer alice'}, json=body([photo]))
    assert response.status_code == 409
    assert client.get('/api/media/studio/subjects?project_id=' + 'x'*129, headers={'Authorization':'Bearer alice'}).status_code == 422
    assert client.delete('/api/media/studio/subjects/' + 'x'*129, headers={'Authorization':'Bearer alice'}).status_code == 422
    other = image(state, project='project-b')
    assert store.create('alice', StudioSubjectInput(project_id='project-b', name='Separate', image_ids=[other]))['project_id'] == 'project-b'


def test_five_images_are_saved_as_ids_without_paths_or_payloads(workspace):
    client, state, _, _ = workspace
    photos = [image(state) for _ in range(5)]
    result = client.post('/api/media/studio/subjects', headers={'Authorization':'Bearer alice'}, json=body(photos))
    assert result.status_code == 201 and result.json()['image_ids'] == photos
    assert str(state.artifacts.root) not in result.text and 'base64' not in result.text


def test_truncated_jpeg_is_rejected_before_creating_metadata(workspace):
    client, state, _, _ = workspace
    stream = io.BytesIO()
    Image.new('RGB', (64,64), 'blue').save(stream, format='JPEG')
    raw = stream.getvalue()
    photo = image(state, blob=raw[:-20], mime='image/jpeg')
    response = client.post('/api/media/studio/subjects', headers={'Authorization':'Bearer alice'}, json=body([photo]))
    assert response.status_code == 400
    assert state.studio_subjects.list('alice', 'project-a') == []
