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

    result = save(capabilities=['image','video','audio'], api_key='isolated-first-key')
    assert not result.get('errors'), result
    assert set(result['data']['save_agent_provider']['capabilities']) == {'image','video','audio'}
    inventory = client.post('/graphql', headers=owner, json={
        'query':'{agent_providers{id kind capabilities secret_configured}}'}).json()
    row = next(row for row in inventory['data']['agent_providers'] if row['id']=='media-account')
    assert row['kind']=='openai-compatible' and set(row['capabilities'])=={'image','video','audio'}
    assert row['secret_configured'] is True and 'isolated-first-key' not in str(inventory)
    # A legacy credential-only update omits capabilities; it cannot erase media
    # declarations or silently add a different account capability.
    rotated = save(api_key='isolated-rotated-key')
    assert not rotated.get('errors'), rotated
    assert set(rotated['data']['save_agent_provider']['capabilities'])=={'image','video','audio'}
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


def test_anthropic_account_declaration_seeds_models_and_speaks_the_messages_wire(tmp_path):
    from termx.agent.providers import ANTHROPIC_MODELS, AnthropicMessagesAdapter
    _, state, client, owner = client_fixture(tmp_path)
    state.credentials = CredentialStore(memory={})
    state.agent.credentials = state.credentials
    def save(**fields):
        response = client.post('/graphql', headers=owner, json={
            'query': 'mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id kind base_url model models wire capabilities secret_configured}}',
            'variables': {'input': {'id':'claude','kind':'anthropic','name':'Claude', **fields}}})
        assert response.status_code == 200
        return response.json()

    # The input's URL default is OpenAI's: for this kind it means api.anthropic.com. No models typed: seed them.
    saved = save(api_key='isolated-anthropic-key', capabilities=['shell','functions'])
    assert not saved.get('errors'), saved
    row = saved['data']['save_agent_provider']
    assert row['kind']=='anthropic' and row['wire']=='messages'
    assert row['base_url']=='https://api.anthropic.com'
    assert row['models']==list(ANTHROPIC_MODELS) and row['models'][0]=='claude-opus-5-5'
    assert row['secret_configured'] is True and 'isolated-anthropic-key' not in str(saved)

    # A pasted /v1 URL is accepted and stored as the origin; typed models are kept.
    # (A key issued for api.anthropic.com is not carried to another origin, so it is supplied again.)
    typed = save(base_url='https://gateway.example/anthropic/v1/', model='claude-sonnet-5-5', api_key='isolated-anthropic-key')
    assert typed['data']['save_agent_provider']['base_url']=='https://gateway.example/anthropic'
    assert typed['data']['save_agent_provider']['models']==['claude-sonnet-5-5']

    # Another wire is refused for this kind, and the Anthropic wire for the OpenAI kinds.
    assert save(wire='chat')['errors'][0]['extensions']['http_status']==400
    other = client.post('/graphql', headers=owner, json={
        'query': 'mutation($input:AgentProviderInput!){save_agent_provider(input:$input){id}}',
        'variables': {'input': {'id':'oa','kind':'openai-compatible','base_url':'https://example.invalid/v1',
                                'model':'m','wire':'messages'}}}).json()
    assert other['errors'][0]['extensions']['http_status']==400
    assert state.agent_store.get_provider('oa') is None

    # The manager builds the native adapter, sharing one pooled SDK client until the key rotates.
    provider = state.agent.store.get_provider('claude')
    first = state.agent._adapter({**provider, 'model': 'claude-opus-5-5'})
    again = state.agent._adapter({**provider, 'model': 'claude-opus-5-5'})
    assert isinstance(first, AnthropicMessagesAdapter) and first.sdk is again.sdk
    assert first.api_key == 'isolated-anthropic-key'
    assert save(api_key='isolated-rotated-anthropic-key')['data']['save_agent_provider']['secret_configured'] is True
    rotated = state.agent._adapter({**state.agent.store.get_provider('claude'), 'model': 'claude-opus-5-5'})
    assert rotated.sdk is not first.sdk and rotated.api_key == 'isolated-rotated-anthropic-key'


def test_a_frozen_snapshot_stays_the_first_one_offered():
    from termx.agent.context import ContextEngine
    engine = ContextEngine('/tmp')
    first, later = {'files': ['a']}, {'files': ['a', 'b']}
    assert engine.frozen_snapshot(first) is first
    assert engine.frozen_snapshot(later) is first
