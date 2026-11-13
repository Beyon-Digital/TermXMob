"""Core policy tests use the real managed identity/session implementation."""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from termx.data.http import build_data_router
from termx.data.service import DataService
from termx.data.store import DataStore, identifier_for
from termx.identity import AuthenticationService
from termx.identity_locks import SessionLocks

PASSWORD = 'Data-fixture-password-123'


@pytest.fixture
def setup(tmp_path):
    identity = AuthenticationService(tmp_path / 'managed' / 'identity.sqlite3')
    alice = identity.setup_owner('alice', PASSWORD)
    bob = identity.create_local_user('bob', PASSWORD, [])
    outsider = identity.create_local_user('outsider', PASSWORD, ['host-admin'])

    def credentials(username):
        return asyncio.run(identity.login('local-password', {'username': username, 'password': PASSWORD}, peer='127.0.0.1'))

    sessions = {name: credentials(name) for name in ('alice', 'bob', 'outsider')}
    actors = {name: identity.resolve(session.access_token).principal for name, session in sessions.items()}
    state = SimpleNamespace(identity=identity)
    service = state.data_studio = DataService(state, tmp_path / 'artifacts' / 'data')
    # Independent core fixtures do not fabricate runtime/BI success; the empty
    # domains allow core snapshot serialization without invoking other lanes.
    service._runtime = SimpleNamespace(engines=lambda: [])
    service._bi = SimpleNamespace(reports=lambda *_: [], dashboards=lambda *_: [])
    app = FastAPI()
    app.include_router(build_data_router(state))
    client = TestClient(app, base_url='https://localhost')
    return SimpleNamespace(identity=identity, service=service, state=state, actors=actors,
                           sessions=sessions, client=client, alice=alice, bob=bob, outsider=outsider)


def bearer(setup, name='alice'):
    return {'Authorization': 'Bearer ' + setup.sessions[name].access_token}


def expect_status(code, fn):
    with pytest.raises(HTTPException) as raised:
        fn()
    assert raised.value.status_code == code


def test_explicit_memberships_and_no_host_admin_inheritance(setup):
    service, actors = setup.service, setup.actors
    workspace = service.create_workspace(actors['bob'], 'Research')
    assert workspace['role'] == 'owner'
    assert service.workspaces(actors['alice']) == []
    assert service.workspaces(actors['outsider']) == []
    expect_status(404, lambda: service.require(actors['alice'], workspace['id']))
    expect_status(404, lambda: service.require(actors['outsider'], workspace['id'], write=True))
    members = service.members(actors['bob'], workspace['id'])
    assert len(members) == 1 and members[0]['principal_id'] == setup.bob.id
    assert members[0]['role'] == 'owner'
    assert service.store.path.name == 'data.sqlite3'
    assert service.store.path != setup.identity.path


def test_exact_existing_member_add_revoke_and_readd_versions(setup):
    service, actors = setup.service, setup.actors
    wid = service.create_workspace(actors['alice'], 'Team')['id']
    expect_status(404, lambda: service.add_member(actors['alice'], wid, username='bo', role='editor'))
    expect_status(422, lambda: service.add_member(actors['alice'], wid, username='bob', principal_id=setup.bob.id))
    member = service.add_member(actors['alice'], wid, username='bob', role='editor')
    assert member['principal_id'] == setup.bob.id
    assert service.require(actors['bob'], wid, write=True)['role'] == 'editor'
    expect_status(403, lambda: service.add_member(actors['bob'], wid, username='outsider'))
    expect_status(409, lambda: service.add_member(actors['alice'], wid, username='bob'))
    viewer = service.change_member(actors['alice'], wid, member['id'], member['revision'], role='viewer')
    assert viewer['revision'] == 2
    expect_status(403, lambda: service.require(actors['bob'], wid, write=True))
    expect_status(409, lambda: service.change_member(actors['alice'], wid, member['id'], 1, remove=True))
    removed = service.change_member(actors['alice'], wid, member['id'], viewer['revision'], remove=True)
    assert removed['archived'] and removed['revision'] == 3
    expect_status(404, lambda: service.require(actors['bob'], wid))
    assert service.workspaces(actors['bob']) == []
    restored = service.add_member(actors['alice'], wid, principal_id=setup.bob.id, role='viewer')
    assert restored['id'] == member['id'] and restored['revision'] == 4
    assert service.require(actors['bob'], wid)['role'] == 'viewer'
    events = service.store.activity(wid)
    assert {'member.added', 'member.updated', 'member.removed'} <= {event['action'] for event in events}


def test_last_owner_and_cross_workspace_membership_isolation(setup):
    service, actors = setup.service, setup.actors
    wid = service.create_workspace(actors['alice'], 'One')['id']
    other = service.create_workspace(actors['alice'], 'Two')['id']
    owner = service.members(actors['alice'], wid)[0]
    expect_status(409, lambda: service.change_member(actors['alice'], wid, owner['id'], 1, remove=True))
    expect_status(409, lambda: service.change_member(actors['alice'], wid, owner['id'], 1, role='editor'))
    member = service.add_member(actors['alice'], other, username='bob')
    expect_status(404, lambda: service.change_member(actors['alice'], wid, member['id'], 1, remove=True))
    expect_status(404, lambda: service.require(actors['bob'], wid))
    promoted = service.change_member(actors['alice'], other, member['id'], 1, role='owner')
    assert service.require(actors['bob'], other, owner=True)['role'] == 'owner'
    assert promoted['revision'] == 2


def test_workspace_revision_conflict_soft_archive_and_reload(setup):
    service, actor = setup.service, setup.actors['alice']
    workspace = service.create_workspace(actor, 'Before')
    updated = service.update_workspace(actor, workspace['id'], 1, 'After')
    assert updated['name'] == 'After' and updated['revision'] == 2
    expect_status(409, lambda: service.update_workspace(actor, workspace['id'], 1, 'Lost change'))
    reloaded = DataService(setup.state, service.directory)
    assert reloaded.require(actor, workspace['id'])['name'] == 'After'
    archived = service.archive_workspace(actor, workspace['id'], 2)
    assert archived['archived'] and archived['revision'] == 3
    assert service.workspaces(actor) == []
    assert service.store.get('workspace', workspace['id'])['archived'] is True
    expect_status(404, lambda: reloaded.require(actor, workspace['id']))


def test_source_sharing_never_inherits_from_role_and_revocation_live(setup):
    service, actors = setup.service, setup.actors
    wid = service.create_workspace(actors['alice'], 'Shared')['id']
    member = service.add_member(actors['alice'], wid, username='bob', role='editor')
    source = service.store.create('connection', wid, setup.bob.id,
                                  {'name': 'Private source', 'engine': 'sqlite', 'config': {'path': '/fixtures/business.sqlite3'},
                                   'shared_read': False, 'credential_ref': 'data_opaque_ref'})
    # The workspace owner has no implicit access to another member's source.
    expect_status(403, lambda: service.require_connection(actors['alice'], wid, source['id']))
    assert service.require_connection(actors['bob'], wid, source['id'])['id'] == source['id']
    shared = service.store.update('connection', source['id'], {'shared_read': True}, 1)
    assert service.require_connection(actors['alice'], wid, source['id'])['revision'] == 2
    service.change_member(actors['alice'], wid, member['id'], member['revision'], role='viewer')
    expect_status(403, lambda: service.require_connection(actors['bob'], wid, source['id']))
    assert service.require_connection(actors['bob'], wid, source['id'], report=True)['id'] == source['id']
    service.store.update('connection', source['id'], {'shared_read': False}, shared['revision'])
    expect_status(403, lambda: service.require_connection(actors['bob'], wid, source['id'], report=True))
    another = service.create_workspace(actors['alice'], 'Other')['id']
    expect_status(404, lambda: service.require_connection(actors['alice'], another, source['id']))


def test_snapshot_omits_secrets_unshared_queries_viewer_drafts_and_activity(setup):
    service, actors = setup.service, setup.actors
    wid = service.create_workspace(actors['alice'], 'Snapshot')['id']
    member = service.add_member(actors['alice'], wid, username='bob', role='editor')
    source = service.store.create('connection', wid, setup.alice.id,
                                 {'name': 'Business', 'engine': 'sqlite', 'shared_read': True,
                                  'credential_ref': 'data_hidden', 'credential_id': 'data_hidden2',
                                  'config': {'path': '/fixtures/business.sqlite3'}})
    query = service.store.create('query', wid, setup.alice.id, {'name': 'Rows', 'connection_id': source['id'], 'sql': 'SELECT 1'})
    snapshot = service.snapshot(actors['bob'], wid)
    serialized = json.dumps(snapshot)
    assert snapshot['queries'][0]['id'] == query['id']
    assert 'data_hidden' not in serialized and 'credential_ref' not in serialized and 'credential_id' not in serialized
    assert snapshot['activity'] == []
    service.change_member(actors['alice'], wid, member['id'], member['revision'], role='viewer')
    snapshot = service.snapshot(actors['bob'], wid)
    assert snapshot['queries'] == []
    # A viewer learns only that a shared source exists, so the pivots and segments built on it can be listed. No path, account or policy.
    assert [(row['id'], row['name'], row['engine'], row['shared_read'], row['config']) for row in snapshot['connections']] == [(source['id'], 'Business', 'sqlite', True, {})]
    assert '/fixtures' not in json.dumps(snapshot) and 'credential' not in json.dumps(snapshot)
    service.store.update('connection', source['id'], {'shared_read': False}, source['revision'])
    assert service.snapshot(actors['bob'], wid)['connections'] == []
    assert snapshot['reports'] == [] and snapshot['activity'] == []
    expect_status(422, lambda: service.store.create('connection', wid, setup.alice.id, {'config': {'password': 'must-not-persist'}}))
    assert b'must-not-persist' not in service.store.path.read_bytes()


def test_principals_and_sessions_are_reloaded_for_each_use(setup):
    service, actor = setup.service, setup.actors['alice']
    wid = service.create_workspace(actor, 'Live')['id']
    setup.identity.revoke(setup.sessions['alice'].session_id, actor.id)
    expect_status(401, lambda: service.require(actor, wid))
    expect_status(401, lambda: service.create_workspace(actor, 'Denied'))
    bob = setup.actors['bob']
    bob_wid = service.create_workspace(bob, 'Bob')['id']
    setup.identity.disable(bob.id)
    expect_status(401, lambda: service.require(bob, bob_wid))
    assert setup.client.get('/api/data/bootstrap', headers=bearer(setup)).status_code == 401


def test_real_managed_bearer_cookie_csrf_lock_and_no_legacy_auth(setup):
    client = setup.client
    assert client.get('/api/data/bootstrap').status_code == 401
    assert client.get('/api/data/bootstrap', headers={'x-termx-passcode': 'host-password'}).status_code == 401
    assert client.get('/api/data/bootstrap?k=host-password').status_code == 401
    assert client.get('/api/data/bootstrap', headers={'Authorization': 'Bearer host-password'}).status_code == 401
    result = client.post('/api/data/workspaces', headers=bearer(setup), json={'name': 'HTTP team'})
    assert result.status_code == 201
    wid = result.json()['id']
    assert client.get('/api/data/workspaces/' + wid, headers=bearer(setup, 'outsider')).status_code == 404
    client.cookies.set('termx_access', setup.sessions['alice'].access_token)
    assert client.get('/api/data/bootstrap').status_code == 200
    assert client.post('/api/data/workspaces', json={'name': 'CSRF denied'}).status_code == 403
    cookie_headers = {'Origin': 'https://localhost', 'x-termx-csrf': setup.sessions['alice'].csrf_token}
    assert client.post('/api/data/workspaces', headers=cookie_headers, json={'name': 'Cookie team'}).status_code == 201
    assert client.post('/api/data/workspaces', headers={**cookie_headers, 'Origin': 'https://other.example'}, json={'name': 'Cross origin'}).status_code == 403
    current = setup.identity.resolve(setup.sessions['alice'].access_token)
    SessionLocks(setup.identity).lock(current)
    assert client.get('/api/data/bootstrap', headers=bearer(setup)).status_code == 423
    assert client.get('/api/data/bootstrap').status_code == 423


def test_http_members_revisions_and_delete_json_body(setup):
    client, headers = setup.client, bearer(setup)
    workspace = client.post('/api/data/workspaces', headers=headers, json={'name': 'Team'}).json()
    base = '/api/data/workspaces/' + workspace['id']
    member = client.post(base + '/members', headers=headers, json={'username': 'bob', 'role': 'editor'})
    assert member.status_code == 201
    mid = member.json()['id']
    assert client.patch(base + '/members/' + mid, headers=bearer(setup, 'bob'), json={'revision': 1, 'role': 'owner'}).status_code == 403
    changed = client.patch(base + '/members/' + mid, headers=headers, json={'revision': 1, 'role': 'viewer'})
    assert changed.status_code == 200 and changed.json()['revision'] == 2
    assert client.request('DELETE', base + '/members/' + mid, headers=headers, json={'revision': 1}).status_code == 409
    assert client.request('DELETE', base + '/members/' + mid, headers=headers, json={'revision': 2}).status_code == 200
    assert client.get(base, headers=bearer(setup, 'bob')).status_code == 404
    assert client.request('DELETE', base, headers=headers, json={'revision': 1}).status_code == 200
    assert client.get(base, headers=headers).status_code == 404


def test_store_concurrent_compare_and_swap_stable_ids_and_json(tmp_path):
    store = DataStore(tmp_path / 'data')
    record = store.create('query', 'workspace', 'alice', {'name': 'Before'})

    def change(name):
        try:
            return store.update('query', record['id'], {'name': name}, 1)['revision']
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(change, ['A', 'B']))
    assert sorted(outcomes) == [2, 409]
    assert store.get('query', record['id'])['revision'] == 2
    expect_status(422, lambda: store.update('query', record['id'], {'revision': 99}, 2))
    expect_status(422, lambda: store.create('query', 'workspace', 'alice', {'invalid': float('nan')}))
    assert identifier_for('job', 'alice', 'workspace', 'request') == identifier_for('job', 'alice', 'workspace', 'request')
    assert identifier_for('job', 'alice', 'workspace', 'request') != identifier_for('job', 'bob', 'workspace', 'request')
    archived = store.archive('query', record['id'], 2)
    assert archived['archived'] and store.list('query', 'workspace') == []
    assert DataStore(tmp_path / 'data').get('query', record['id'])['revision'] == 3
