"""Account declarations survive actual managed GraphQL credential updates."""
from test_authorization import client_fixture
from termx.agent.secrets import CredentialStore


def test_managed_provider_media_declarations_and_safe_rotation(tmp_path):
    _, state, client, owner = client_fixture(tmp_path)
    # Account persistence is exercised through the real manager/store; this
    # synthetic secret uses the isolated in-memory credential backend only.
    state.credentials = CredentialStore(memory={})
    state.agent.credentials = state.credentials
    def save(**fields):
        response = client.post('/graphql', headers=owner, json={
            'query': 'mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id capabilities secret_configured}}',
            'variables': {'input': {'id':'media-account','kind':'openai-compatible',
                'name':'Explicit API','base_url':'https://example.invalid/v1',
                'model':'explicit-model', **fields}}})
        assert response.status_code == 200
        return response.json()

    result = save(capabilities=['image','audio'], api_key='isolated-first-key')
    assert not result.get('errors'), result
    assert set(result['data']['save_agent_provider']['capabilities']) == {'image','audio'}
    inventory = client.post('/graphql', headers=owner, json={
        'query':'{agent_providers{id kind capabilities secret_configured}}'}).json()
    row = next(row for row in inventory['data']['agent_providers'] if row['id']=='media-account')
    assert row['kind']=='openai-compatible' and set(row['capabilities'])=={'image','audio'}
    assert row['secret_configured'] is True and 'isolated-first-key' not in str(inventory)
    # A legacy credential-only update omits capabilities; it cannot erase media
    # declarations or silently add a different account capability.
    rotated = save(api_key='isolated-rotated-key')
    assert not rotated.get('errors'), rotated
    assert set(rotated['data']['save_agent_provider']['capabilities'])=={'image','audio'}
    assert state.credentials.get('media-account')=='isolated-rotated-key'
    edited = save(capabilities=['image'], api_key=None)
    assert not edited.get('errors'), edited
    assert edited['data']['save_agent_provider']['capabilities']==['image']
    assert state.credentials.get('media-account')=='isolated-rotated-key'
    rejected = save(capabilities=['image','invented-entitlement'],api_key='must-not-be-written')
    assert rejected['errors'][0]['extensions']['http_status']==400, rejected
    assert state.agent_store.get_provider('media-account')['capabilities']==['image']
    assert state.credentials.get('media-account')=='isolated-rotated-key'
    # The fixture never calls a provider: declaring capabilities is metadata,
    # and does not imply endpoint/model entitlement or consent to billed usage.
    assert state.agent_store.list_tasks()==[]
