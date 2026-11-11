"""Studio project ownership, reversible archiving and effect-reservation safety."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from termx.auth import Auth
from termx.authorization import AuthorizationService, ROLES
from termx.identity import AuthenticationService
from termx.media.http import mount_media
from termx.media.studio_projects import StudioProjectInput, StudioProjectsStore, project_mutation

PASSWORD = 'isolated-studio-project-password-123'


@pytest.fixture
def workspace(tmp_path):
    identity = AuthenticationService(tmp_path / 'identity.sqlite3')
    identity.setup_owner('owner', PASSWORD)
    authorization = AuthorizationService(identity, Auth(identity=identity))
    for username in ('alice', 'bob'):
        principal = identity.create_local_user(username, PASSWORD, list(ROLES['operator']))
        authorization.set_role(principal.id, 'operator')
    def login(username):
        result = asyncio.run(identity.login('local-password', {'username': username, 'password': PASSWORD}, peer='local'))
        return {'Authorization': 'Bearer ' + result.access_token}
    def no_provider(*args):
        raise AssertionError('Project management must not access providers or credentials')
    state = SimpleNamespace(delivery=SimpleNamespace(directory=tmp_path/'delivery'), identity=identity,
        authorization=authorization, agent_store=SimpleNamespace(get_provider=no_provider, list_providers=lambda: []),
        credentials=SimpleNamespace(get=no_provider))
    app = FastAPI(); mount_media(app, state)
    with TestClient(app) as client:
        yield client, state, login


def create(client, headers, name='Launch campaign'):
    response = client.post('/api/media/studio/projects', headers=headers,
                           json={'name': name, 'description': 'Product media'})
    assert response.status_code == 201, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def job(state, owner, project, status='running'):
    with state.studio.db() as db:
        db.execute("INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,created,updated) VALUES(?,?,?,?,?,'fixture','kie','fixture','image','fixture',?,1,1)",
                   ('job-' + status, owner, project, 'request-' + status, 'digest', status))


def test_owned_crud_revision_and_restart_without_filesystem_enrollment(workspace):
    client, state, login = workspace
    alice, bob = login('alice'), login('bob')
    project = create(client, alice, '  Launch campaign  ')
    assert project['name'] == 'Launch campaign'
    assert project['id'].startswith('studio_') and project['revision'] == 1 and not project['archived']
    assert set(project) == {'id','name','description','revision','archived','created_at','updated_at'}
    assert not hasattr(state, 'projects')  # No Agent folder or Git project service needed.
    assert client.get('/api/media/studio/projects', headers=alice).json() == {'projects': [project]}
    assert client.get('/api/media/studio/projects', headers=bob).json() == {'projects': []}
    url = '/api/media/studio/projects/' + project['id']
    body = {'name': 'Renamed', 'description': 'New description', 'revision': 1}
    assert client.put(url, headers=bob, json=body).status_code == 404
    response = client.put(url, headers=alice, json=body)
    assert response.status_code == 200
    updated = response.json()
    assert updated['revision'] == 2 and updated['name'] == 'Renamed'
    assert client.put(url, headers=alice, json=body).status_code == 409
    assert StudioProjectsStore(state.studio, state.artifacts).list(state.identity.resolve(alice['Authorization'][7:]).principal.id) == [updated]
    assert client.post(url+'/archive', headers=alice, json={'revision': 1}).status_code == 409
    assert client.post(url+'/archive', headers=alice, json={'revision': 2}).json()['archived'] is True
    assert client.post(url+'/restore', headers=alice, json={'revision': 3}).json()['archived'] is False


def test_identity_scope_exact_owner_and_no_project_grant_widening(workspace):
    client, state, login = workspace
    alice, bob = login('alice'), login('bob')
    assert client.get('/api/media/studio/projects').status_code == 401
    assert client.post('/api/media/studio/projects', json={'name': 'Private'}).status_code == 401
    project = create(client, alice)
    identifier = project['id']
    assert client.get('/api/media/studio/jobs?project_id='+identifier, headers=alice).status_code == 200
    assert client.get('/api/media/studio/jobs?project_id='+identifier, headers=bob).status_code == 404
    assert client.get('/api/media/studio/jobs?project_id=studio_missing', headers=alice).status_code == 404
    assert not state.authorization.can(alice['Authorization'][7:], 'agent-view', project_id='unrelated-agent-project')
    with state.identity._db() as db:
        assert db.execute('SELECT COUNT(*) FROM project_grants').fetchone()[0] == 0
    assert client.post('/api/media/artifacts', headers=alice, json={'kind':'document','title':'Draft','content':'Text','project_id':identifier}).status_code == 403
    principal = state.identity.resolve(alice['Authorization'][7:]).principal
    # Project ownership does not silently make an operator trusted for paid/execution effects.
    state.authorization.set_role(principal.id, 'operator', trusted_execution=True)
    assert client.post('/api/media/artifacts', headers=alice, json={'kind':'document','title':'Draft','content':'Text','project_id':identifier}).status_code == 200
    state.authorization.set_role(principal.id, 'viewer')
    assert client.put('/api/media/studio/projects/'+identifier, headers=alice,
                      json={'name':'Denied','description':'','revision':1}).status_code == 403
    state.identity.disable(principal.id)
    assert client.get('/api/media/studio/projects', headers=alice).status_code == 401


def test_archive_retains_artifacts_history_inputs_and_references(workspace):
    client, state, login = workspace
    alice = login('alice'); project = create(client, alice); identifier = project['id']
    owner = state.identity.resolve(alice['Authorization'][7:]).principal.id
    saved = state.artifacts.create(owner, identifier, 'document', 'Script', 'Preserved script')
    upload = state.artifacts.create(owner, identifier, 'input', 'Source', blob=b'source', mime='text/plain')
    job(state, owner, identifier, 'completed')
    with state.studio_subjects.db() as db:
        db.execute("INSERT INTO studio_subjects VALUES('reference',?,?, 'Cast','Preserved','[]',1)", (owner, identifier))
    response = client.post('/api/media/studio/projects/'+identifier+'/archive', headers=alice, json={'revision':1})
    assert response.status_code == 200 and response.json()['archived']
    assert state.artifacts.get(owner, saved['id'])['content'] == 'Preserved script'
    assert state.artifacts.binary(owner, upload['id'])[0] == b'source'
    assert state.studio_subjects.owned(owner, 'reference')['description'] == 'Preserved'
    assert len(client.get('/api/media/studio/history?project_id='+identifier, headers=alice).json()['items']) == 1
    assert client.get('/api/media/artifacts/'+saved['id'], headers=alice).status_code == 200
    assert client.get('/api/media/studio/subjects?project_id='+identifier, headers=alice).status_code == 200
    principal = state.identity.resolve(alice['Authorization'][7:]).principal
    state.authorization.set_role(principal.id, 'operator', trusted_execution=True)
    assert client.post('/api/media/artifacts', headers=alice, json={'kind':'document','title':'New','content':'No','project_id':identifier}).status_code == 409
    with pytest.raises(HTTPException, match='Restore'):
        with project_mutation(state, owner, identifier):
            pytest.fail('Archived project mutation must not enter')
    assert client.post('/api/media/studio/projects/'+identifier+'/restore', headers=alice, json={'revision':2}).status_code == 200
    assert state.artifacts.get(owner, saved['id'])['content'] == 'Preserved script'


@pytest.mark.parametrize('status', ['submitting','queued','running','unknown'])
def test_archive_refuses_active_or_unconfirmed_generation(workspace, status):
    client, state, login = workspace
    headers = login('alice'); project = create(client, headers)
    owner = state.identity.resolve(headers['Authorization'][7:]).principal.id
    with project_mutation(state, owner, project['id']):
        job(state, owner, project['id'], status)
    response = client.post('/api/media/studio/projects/'+project['id']+'/archive', headers=headers, json={'revision':1})
    assert response.status_code == 409 and 'Reconcile' in response.json()['detail']
    assert not state.studio_projects.owned(owner, project['id'])['archived']


@pytest.mark.parametrize('status', ['executing','unknown'])
def test_archive_refuses_legacy_media_outcomes(workspace, status):
    client, state, login = workspace
    headers = login('alice'); project = create(client, headers)
    owner = state.identity.resolve(headers['Authorization'][7:]).principal.id
    with state.artifacts.db() as db:
        db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?)', (owner,'operation','digest',status,None,project['id'],1))
    assert client.post('/api/media/studio/projects/'+project['id']+'/archive', headers=headers, json={'revision':1}).status_code == 409


@pytest.mark.parametrize('body', [{'name':'  '}, {'name':'Invalid\x00name'}, {'name':'x'*121}, {'name':'Project','description':'bad\x00'}, {'name':'Project','unexpected':True}])
def test_strict_visible_bounded_metadata(body):
    with pytest.raises(ValidationError):
        StudioProjectInput.model_validate(body)


def test_token_scope_ceiling_and_failed_claim_do_not_create_project(workspace, monkeypatch):
    client, state, login = workspace
    principal = state.identity.create_local_user('limited', PASSWORD, ['agent-view', 'files-read'])
    state.authorization.set_role(principal.id, 'operator')
    state.identity.set_scopes(principal.id, ['agent-view', 'files-read'])
    headers = login('limited')
    assert client.get('/api/media/studio/projects', headers=headers).status_code == 200
    assert client.post('/api/media/studio/projects', headers=headers, json={'name':'Denied'}).status_code == 403
    assert state.studio_projects.list(principal.id) == []
    alice = login('alice')
    owner = state.identity.resolve(alice['Authorization'][7:]).principal.id
    def reject(*args, **kwargs):
        raise HTTPException(403, 'Claim denied')
    monkeypatch.setattr(state.authorization, 'claim', reject)
    assert client.post('/api/media/studio/projects', headers=alice, json={'name':'Unclaimed'}).status_code == 403
    assert state.studio_projects.list(owner) == []


def test_archive_during_catalog_load_prevents_provider_reservation(workspace, monkeypatch):
    from termx.media.studio import StudioInput
    client, state, login = workspace
    headers = login('alice'); project = create(client, headers)
    owner = state.identity.resolve(headers['Authorization'][7:]).principal.id
    # A request authorized before catalog loading must recheck active collection
    # state when it later reserves the effect, even without an HTTP callback.
    monkeypatch.setattr(state.studio, 'account', lambda *args: 'mock-key')
    async def run():
        loading, resume = asyncio.Event(), asyncio.Event()
        async def catalog():
            loading.set()
            await resume.wait()
        monkeypatch.setattr(state.studio, '_load_catalog', catalog)
        pending = asyncio.create_task(state.studio.create(owner, project['id'], StudioInput(
            request_id='archive-race-request', project_id=project['id'], provider_id='fixture',
            model='nano-banana-pro', prompt='An orange fox', acknowledge_billing=True).model_dump()))
        await loading.wait()
        state.studio_projects.change(owner, project['id'], 1, archived=True)
        resume.set()
        with pytest.raises(HTTPException, match='Restore'):
            await pending
        assert state.studio.list(owner, project['id']) == [] and not state.studio.tasks
    asyncio.run(run())


def test_archive_serializes_with_threaded_generation_reservation(workspace):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    client, state, login = workspace
    headers = login('alice'); project = create(client, headers)
    owner = state.identity.resolve(headers['Authorization'][7:]).principal.id
    reserved, release, archiving = Event(), Event(), Event()
    def reserve():
        with project_mutation(state, owner, project['id']):
            job(state, owner, project['id'], 'running')
            reserved.set()
            assert release.wait(2)
    def archive():
        archiving.set()
        return state.studio_projects.change(owner, project['id'], 1, archived=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reservation = pool.submit(reserve)
        assert reserved.wait(2)
        archival = pool.submit(archive)
        assert archiving.wait(2) and not archival.done()
        release.set()
        reservation.result(timeout=2)
        with pytest.raises(HTTPException, match='Reconcile'):
            archival.result(timeout=2)
    assert not state.studio_projects.owned(owner, project['id'])['archived']
