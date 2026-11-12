"""Folder picker boundaries use live sign-in and real project path resolution."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from test_authorization import fixture as authorization_fixture
from termx.project_files import ProjectFiles
from termx.workspace.store import Conflict


@pytest.fixture
def paths(tmp_path, monkeypatch):
    from termx.workspace.path_http import build_workspace_paths_router

    identity, authorization, owner, alice, bob, login = authorization_fixture(tmp_path)
    root = tmp_path / 'project'; root.mkdir()
    (root / 'src').mkdir(); (root / 'docs').mkdir()
    (root / 'app.py').write_text('print("fixture")\n')
    (root / '.hidden').mkdir()
    outside = tmp_path / 'private'; outside.mkdir()
    (outside / 'private-name.txt').write_text('fixture')
    (root / 'outside-link').symlink_to(outside, target_is_directory=True)
    (root / 'inside-link').symlink_to(root / 'src', target_is_directory=True)
    projects = {
        'project-a': {'id': 'project-a', 'name': 'Fixture project', 'path': str(root)},
        'project-b': {'id': 'project-b', 'name': 'Other project', 'path': str(outside)},
    }

    class Files(ProjectFiles):
        def project(self, identifier):
            if identifier not in projects:
                raise HTTPException(404, 'Project not found')
            return projects[identifier]
        def projects(self):
            return list(projects.values())

    files = object.__new__(Files); files.lock = threading.RLock()
    checkout = tmp_path / 'worktree'; checkout.mkdir()
    (checkout / 'checkout-only.txt').write_text('fixture')
    class CheckoutFiles(Files):
        def project(self, identifier):
            return {**super().project(identifier), 'path': str(checkout)}
    checkout_files = object.__new__(CheckoutFiles); checkout_files.lock = files.lock

    def require(actor, scope, project_id=None, *args, **kwargs):
        authorization.require_principal(actor, scope, project_id=project_id)

    def session_files(actor, identifier, scope='files-read', expected_worktree=None):
        if identifier != 'session-a': raise KeyError(identifier)
        require(actor, scope, 'project-a')
        if expected_worktree != 'worktree-a':
            raise Conflict('Execution checkout changed')
        return checkout_files, 'project-a'

    state = SimpleNamespace(identity=identity, auth=authorization.auth,
                            authorization=authorization, projects=files,
                            workspace=SimpleNamespace(require=require, session_files=session_files))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: root))
    app = FastAPI(); app.include_router(build_workspace_paths_router(state))
    client = TestClient(app)
    def get(name='owner', **params):
        headers = {'Authorization': 'Bearer ' + login(name)} if name else {}
        return client.get('/api/workspace/paths', params=params, headers=headers)
    return SimpleNamespace(get=get, client=client, state=state, root=root, outside=outside,
                           checkout=checkout, alice=alice, bob=bob, owner=owner,
                           login=login, files=files, identity=identity,
                           authorization=authorization)


def test_host_picker_starts_at_home_and_has_parent_and_breadcrumbs(paths):
    response = paths.get()
    assert response.status_code == 200
    body = response.json()
    assert body['path'] == str(paths.root)
    assert body['parent'] == str(paths.root.parent)
    assert body['breadcrumbs'][-1]['path'] == str(paths.root)
    assert {'src', 'docs', 'inside-link'} <= {row['name'] for row in body['entries']}
    assert 'app.py' not in {row['name'] for row in body['entries']}
    assert '.hidden' not in {row['name'] for row in body['entries']}


def test_host_picker_filters_files_hidden_entries_and_query(paths):
    assert paths.get(kind='file', query='app').json()['entries'] == [
        {'name': 'app.py', 'path': str(paths.root / 'app.py'), 'dir': False}
    ]
    hidden = paths.get(show_hidden=True).json()
    assert '.hidden' in {row['name'] for row in hidden['entries']}
    assert paths.get(query='no-such-folder').json()['entries'] == []
    assert paths.get(query='x' * 201).status_code in {400, 422}
    assert paths.get(offset=-1).status_code == 422
    assert paths.get(kind='invalid').status_code == 422


def test_host_picker_requires_unlocked_live_host_creation_authority_before_io(paths, monkeypatch):
    reads = []
    original = Path.iterdir
    def counted(path):
        # The sandbox env sweeper is a process-wide daemon thread that earlier
        # tests may have started; only the picker's own reads matter here.
        if threading.current_thread().name != 'termx-env-sweep':
            reads.append(str(path))
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', counted)
    assert paths.get(None).status_code == 401
    paths.authorization.grant_project(paths.alice.id, 'project-a', ['files-read', 'files-write'])
    assert paths.get('alice', path=str(paths.root)).status_code == 403
    assert paths.get('bob', path=str(paths.root)).status_code == 403
    token = paths.login('owner')
    actor = paths.identity.resolve(token)
    with paths.identity._db() as db:
        db.execute('UPDATE sessions SET locked=1 WHERE id=?', (actor.session_id,))
    assert paths.client.get('/api/workspace/paths', headers={'Authorization': 'Bearer ' + token}).status_code == 423
    paths.identity.set_scopes(paths.owner.id, ['files-read'])
    assert paths.get('owner').status_code == 403
    assert reads == []


def test_project_picker_stays_relative_and_confined_to_granted_root(paths):
    paths.authorization.grant_project(paths.alice.id, 'project-a', ['files-read'])
    response = paths.get('alice', project_id='project-a', kind='file')
    assert response.status_code == 200
    body = response.json()
    assert body['path'] == '' and body['parent'] is None
    entries = {row['name']: row for row in body['entries']}
    assert entries['src']['path'] == 'src'
    assert entries['app.py']['path'] == 'app.py'
    assert 'outside-link' not in entries
    assert str(paths.outside) not in str(body)
    assert paths.get('alice', project_id='project-a', path='src').status_code == 200
    assert paths.get('alice', project_id='project-b').status_code == 403
    assert paths.get('bob', project_id='project-a').status_code == 403
    for target in ['../private', 'src/../../private', str(paths.outside), 'outside-link']:
        result = paths.get('alice', project_id='project-a', path=target)
        assert result.status_code == 403
        assert 'entries' not in result.json()


def test_session_picker_uses_exact_authorized_execution_checkout(paths):
    paths.authorization.grant_project(paths.alice.id, 'project-a', ['files-read'])
    response = paths.get('alice', session_id='session-a', worktree_id='worktree-a', kind='file')
    assert response.status_code == 200
    assert [row['name'] for row in response.json()['entries']] == ['checkout-only.txt']
    assert paths.get('alice', session_id='session-a', kind='file').status_code == 409
    assert paths.get('alice', session_id='session-a', worktree_id='other').status_code == 409
    assert paths.get('bob', session_id='session-a', worktree_id='worktree-a').status_code == 403
    assert paths.get('alice', session_id='session-missing', worktree_id='worktree-a').status_code == 404


def test_project_picker_rechecks_live_authority_after_listing(paths, monkeypatch):
    paths.authorization.grant_project(paths.alice.id, 'project-a', ['files-read'])
    original = Path.iterdir
    def revoke_after_read(path):
        result = list(original(path))
        paths.authorization.revoke_project(paths.alice.id, 'project-a')
        return iter(result)
    monkeypatch.setattr(Path, 'iterdir', revoke_after_read)
    response = paths.get('alice', project_id='project-a')
    assert response.status_code == 403
    assert 'entries' not in response.json()


def test_picker_paginates_folder_results_without_losing_filter(paths):
    for index in range(205):
        (paths.root / f'page-{index:03}').mkdir()
    first = paths.get(query='page-').json()
    assert first['total'] == 205
    assert len(first['entries']) == 200
    assert first['next_offset'] == 200
    second = paths.get(query='page-', offset=first['next_offset']).json()
    assert [row['name'] for row in second['entries']] == [f'page-{index:03}' for index in range(200, 205)]
    assert second['next_offset'] is None


def test_host_picker_rechecks_session_before_publishing_folder_names(paths, monkeypatch):
    original = Path.iterdir
    def lock_after_read(path):
        result = list(original(path))
        with paths.identity._db() as db:
            db.execute('UPDATE sessions SET locked=1 WHERE principal_id=?', (paths.owner.id,))
        return iter(result)
    monkeypatch.setattr(Path, 'iterdir', lock_after_read)
    response = paths.get()
    assert response.status_code == 423
    assert 'entries' not in response.json()
