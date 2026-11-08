"""Scoped memory over GraphQL and REST."""
from __future__ import annotations

import asyncio
import json
from time import time

from fastapi.testclient import TestClient

from termx.app import AppState, create_app

PASSWORD = 'strong-password-fixture'
FIELDS = 'id revision content provenance project_id scope agent_id author state'
SAVE = 'mutation($input: MemoryInput!){save_memory(input:$input){%s}}' % FIELDS
LIST = 'query($scope:String,$agent_id:String,$state:String){memories(scope:$scope,agent_id:$agent_id,state:$state){%s}}' % FIELDS
RESOLVE = 'mutation($id:String!,$keep:Boolean!){resolve_memory_proposal(memory_id:$id,keep:$keep){%s}}' % FIELDS


def _api(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    state = AppState()
    owner = state.identity.setup_owner('owner', PASSWORD)
    client = TestClient(create_app(state))
    token = asyncio.run(state.identity.login('local-password', {'username': 'owner', 'password': PASSWORD},
                                             peer='local', device_name='owner')).access_token
    return state, owner, client, {'Authorization': 'Bearer ' + token}


def _gql(client, headers, query, variables=None):
    return client.post('/graphql', headers=headers, json={'query': query, 'variables': variables or {}}).json()


def _agent(state, name='Helper'):
    return state.agent_store.create_custom_agent(name=name, instructions='Explain.', provider_id='fixture', model='fixture',
                                                 tools=['read_file'])


def _status(response):
    return response['errors'][0]['extensions']['http_status']


def test_graphql_saves_and_filters_memory_by_scope_and_agent(tmp_path, monkeypatch):
    state, owner, client, headers = _api(tmp_path, monkeypatch)
    profile, other = _agent(state), _agent(state, 'Other')
    saved = _gql(client, headers, SAVE, {'input': {'content': 'Agent only', 'provenance': 'me', 'scope': 'agent', 'agent_id': profile['id']}})
    assert not saved.get('errors'), saved
    assert saved['data']['save_memory']['scope'] == 'agent' and saved['data']['save_memory']['author'] == 'user'
    _gql(client, headers, SAVE, {'input': {'content': 'Everyone', 'provenance': 'me'}})
    everything = _gql(client, headers, LIST)['data']['memories']
    assert {m['content'] for m in everything} == {'Agent only', 'Everyone'}
    assert [m['content'] for m in _gql(client, headers, LIST, {'scope': 'agent'})['data']['memories']] == ['Agent only']
    mine = _gql(client, headers, LIST, {'agent_id': profile['id']})['data']['memories']
    assert {m['content'] for m in mine} == {'Agent only', 'Everyone'}
    theirs = _gql(client, headers, LIST, {'agent_id': other['id']})['data']['memories']
    assert [m['content'] for m in theirs] == ['Everyone']
    missing = _gql(client, headers, SAVE, {'input': {'content': 'x', 'provenance': 'me', 'scope': 'agent', 'agent_id': 'missing'}})
    assert _status(missing) == 400
    assert _status(_gql(client, headers, LIST, {'scope': 'galaxy'})) == 400
    secret = _gql(client, headers, SAVE, {'input': {'content': 'api_key = sk-live-abcdefghijklmnop1234567890', 'provenance': 'me'}})
    assert _status(secret) == 400 and 'abcdefghij' not in json.dumps(secret)


def test_graphql_keeps_and_discards_proposals(tmp_path, monkeypatch):
    state, owner, client, headers = _api(tmp_path, monkeypatch)
    profile = _agent(state)
    body = {'provenance': 'Written by agent Helper in task abc', 'excluded': False, 'expires_at': time() + 999, 'retention_days': 30,
            'scope': 'agent', 'agent_id': profile['id'], 'author': 'agent', 'state': 'proposed'}
    keep = state.workspace.store.create('memory', owner.id, {**body, 'content': 'keep me'})['id']
    drop = state.workspace.store.create('memory', owner.id, {**body, 'content': 'drop me'})['id']
    proposed = _gql(client, headers, LIST, {'state': 'proposed'})['data']['memories']
    assert {m['content'] for m in proposed} == {'keep me', 'drop me'}
    kept = _gql(client, headers, RESOLVE, {'id': keep, 'keep': True})['data']['resolve_memory_proposal']
    assert kept['state'] == 'active' and kept['author'] == 'agent'
    dropped = _gql(client, headers, RESOLVE, {'id': drop, 'keep': False})
    assert not dropped.get('errors') and dropped['data']['resolve_memory_proposal'] is None
    assert _status(_gql(client, headers, RESOLVE, {'id': keep, 'keep': True})) == 400  # already resolved
    assert _status(_gql(client, headers, RESOLVE, {'id': drop, 'keep': False})) == 404  # already gone
    assert _gql(client, headers, LIST, {'state': 'proposed'})['data']['memories'] == []


def test_graphql_memory_requires_a_signed_in_session(tmp_path, monkeypatch):
    state, owner, client, headers = _api(tmp_path, monkeypatch)
    for query, variables in ((LIST, None), (SAVE, {'input': {'content': 'x', 'provenance': 'me'}})):
        response = client.post('/graphql', json={'query': query, 'variables': variables or {}})
        assert response.status_code in {401, 403} and 'errors' not in response.json()
    assert state.workspace.memories(owner, all_agents=True) == []


def test_rest_memory_still_works_and_accepts_scope_and_agent(tmp_path, monkeypatch):
    state, owner, client, headers = _api(tmp_path, monkeypatch)
    profile = _agent(state)
    plain = client.post('/api/workspace/memory', headers=headers, json={'content': 'REST fact', 'provenance': 'me'})
    assert plain.status_code == 200 and plain.json()['scope'] == 'user'
    scoped = client.post('/api/workspace/memory', headers=headers,
                         json={'content': 'REST agent fact', 'provenance': 'me', 'scope': 'agent', 'agent_id': profile['id']})
    assert scoped.status_code == 200 and scoped.json()['agent_id'] == profile['id']
    assert client.post('/api/workspace/memory', headers=headers,
                       json={'content': 'x', 'provenance': 'me', 'scope': 'galaxy'}).status_code == 422
    everything = client.get('/api/workspace/memory', headers=headers).json()['memory']
    assert {m['content'] for m in everything} == {'REST fact', 'REST agent fact'}
    for_agent = client.get('/api/workspace/memory', headers=headers, params={'agent_id': profile['id']}).json()['memory']
    assert {m['content'] for m in for_agent} == {'REST fact', 'REST agent fact'}
    for_other = client.get('/api/workspace/memory', headers=headers, params={'agent_id': 'someone-else'}).json()['memory']
    assert {m['content'] for m in for_other} == {'REST fact'}
    exported = client.get('/api/workspace/memory/export', headers=headers).json()
    assert exported['format'] == 'termx-memory/v1' and len(exported['memory']) == 2
