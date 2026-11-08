"""Bots over GraphQL: owner privacy, ceiling enforcement and stale-edit protection."""
import asyncio

from test_authorization import client_fixture, PASSWORD
from termx.authorization import ROLES

FIELDS = 'id revision name state template tools ceiling effective violations needs_you peers rules custom_agent_id conversation_id approval_mode sandbox_profile'


def query(client, headers, document, variables=None):
    response = client.post('/graphql', headers=headers, json={'query': document, 'variables': variables or {}})
    assert response.status_code == 200, response.text
    return response.json()


def denied(result, status):
    assert result.get('errors'), result
    assert result['errors'][0].get('extensions', {}).get('http_status') == status, result


def user(identity, state, name, role):
    principal = identity.create_local_user(name, PASSWORD, list(ROLES[role]))
    state.authorization.set_role(principal.id, role)
    token = asyncio.run(identity.login('local-password', {'username': name, 'password': PASSWORD}, peer='local')).access_token
    return {'Authorization': 'Bearer ' + token}


def create(client, owner, template='inbox_triager', name='Triage'):
    result = query(client, owner, f'mutation($i:BotInput!){{create_bot(input:$i){{{FIELDS}}}}}',
                   {'i': {'name': name, 'template': template}})
    assert not result.get('errors'), result
    return result['data']['create_bot']


def test_templates_create_list_and_get(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    templates = query(client, owner, '{bot_templates{id name requires_project tools}}')['data']['bot_templates']
    assert {t['id'] for t in templates} == {'inbox_triager', 'repo_watcher', 'daily_report'}
    assert next(t for t in templates if t['id'] == 'repo_watcher')['requires_project'] is True
    bot = create(client, owner)
    assert bot['state'] == 'active' and bot['violations'] == [] and bot['peers'] == []
    assert 'run_shell' not in bot['ceiling']['tools'] and 'computer.control' in bot['ceiling']['deny_capabilities']
    listed = query(client, owner, f'{{bots{{{FIELDS}}}}}')['data']['bots']
    assert [row['id'] for row in listed] == [bot['id']]
    fetched = query(client, owner, f'query($id:String!){{bot(bot_id:$id){{{FIELDS}}}}}', {'id': bot['id']})
    assert fetched['data']['bot']['custom_agent_id'] == bot['custom_agent_id']
    # The agent is owned by the creator, like any custom agent made through the UI.
    assert state.authorization.resource_owner('custom_agent', bot['custom_agent_id'])


def test_create_rejects_a_template_that_needs_a_project(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    result = query(client, owner, 'mutation($i:BotInput!){create_bot(input:$i){id}}', {'i': {'name': 'W', 'template': 'repo_watcher'}})
    denied(result, 400)
    assert query(client, owner, '{bots{id}}')['data']['bots'] == []


def test_patch_state_ceiling_and_delete(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    patch = 'mutation($id:String!,$i:BotPatchInput!){patch_bot(bot_id:$id,input:$i){revision instructions name}}'
    ok = query(client, owner, patch, {'id': bot['id'], 'i': {'revision': bot['revision'], 'instructions': 'Be brief.', 'name': 'Quiet'}})
    assert ok['data']['patch_bot']['instructions'] == 'Be brief.' and ok['data']['patch_bot']['revision'] == bot['revision'] + 1
    denied(query(client, owner, patch, {'id': bot['id'], 'i': {'revision': bot['revision'], 'instructions': 'stale'}}), 409)
    revision = ok['data']['patch_bot']['revision']
    denied(query(client, owner, patch, {'id': bot['id'], 'i': {'revision': revision, 'tools': ['read_file', 'run_shell']}}), 403)
    denied(query(client, owner, patch, {'id': bot['id'], 'i': {'revision': revision, 'tools': ['all']}}), 400)

    paused = query(client, owner, 'mutation($id:String!){set_bot_state(bot_id:$id,state:"paused"){state}}', {'id': bot['id']})
    assert paused['data']['set_bot_state']['state'] == 'paused'
    denied(query(client, owner, 'mutation($id:String!){bot_run_now(bot_id:$id){task_id}}', {'id': bot['id']}), 409)
    denied(query(client, owner, 'mutation($id:String!){set_bot_state(bot_id:$id,state:"archived"){state}}', {'id': bot['id']}), 400)

    current = query(client, owner, 'query($id:String!){bot(bot_id:$id){revision ceiling}}', {'id': bot['id']})['data']['bot']
    ceiling = {'tools': ['read_file', 'list_files'], 'approval_mode': 'standard', 'sandbox_profile': 'agent', 'deny_capabilities': []}
    narrowed = query(client, owner, 'mutation($id:String!,$r:Int!,$c:BotCeilingInput!){set_bot_ceiling(bot_id:$id,revision:$r,ceiling:$c){tools effective}}',
                     {'id': bot['id'], 'r': current['revision'], 'c': ceiling})
    assert narrowed['data']['set_bot_ceiling']['tools'] == ['read_file', 'list_files']
    assert state.agent_store.list_policy_rules(scope_type='custom_agent', scope_id=bot['custom_agent_id'], effect='deny') == []

    deleted = query(client, owner, 'mutation($id:String!){delete_bot(bot_id:$id){state}}', {'id': bot['id']})
    assert deleted['data']['delete_bot']['state'] == 'archived'
    assert state.agent_store.get_custom_agent(bot['custom_agent_id']) is None
    assert query(client, owner, '{bots{id}}')['data']['bots'] == []
    archived = query(client, owner, '{bots(include_archived:true){id state}}')['data']['bots']
    assert [(row['id'], row['state']) for row in archived] == [(bot['id'], 'archived')]


def test_another_user_cannot_see_or_change_a_bot(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bob = user(identity, state, 'bob', 'operator')
    bot = create(client, owner)
    assert query(client, bob, '{bots{id}}')['data']['bots'] == []
    denied(query(client, bob, 'query($id:String!){bot(bot_id:$id){id instructions}}', {'id': bot['id']}), 404)
    denied(query(client, bob, 'mutation($id:String!,$i:BotPatchInput!){patch_bot(bot_id:$id,input:$i){id}}',
                 {'id': bot['id'], 'i': {'revision': bot['revision'], 'instructions': 'mine now'}}), 404)
    denied(query(client, bob, 'mutation($id:String!){set_bot_state(bot_id:$id,state:"paused"){id}}', {'id': bot['id']}), 404)
    denied(query(client, bob, 'mutation($id:String!){delete_bot(bot_id:$id){id}}', {'id': bot['id']}), 404)
    denied(query(client, bob, 'mutation($id:String!){bot_run_now(bot_id:$id){task_id}}', {'id': bot['id']}), 404)
    assert query(client, owner, 'query($id:String!){bot(bot_id:$id){state}}', {'id': bot['id']})['data']['bot']['state'] == 'active'


def test_a_viewer_cannot_create_or_change_bots(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    viewer = user(identity, state, 'vera', 'viewer')
    bot = create(client, owner)
    denied(query(client, viewer, 'mutation($i:BotInput!){create_bot(input:$i){id}}', {'i': {'name': 'X', 'template': 'inbox_triager'}}), 403)
    denied(query(client, viewer, 'mutation($id:String!){set_bot_state(bot_id:$id,state:"paused"){id}}', {'id': bot['id']}), 403)
    listed = query(client, viewer, '{bots{id}}')
    assert listed.get('errors') or listed['data']['bots'] == []


def test_a_paused_bots_agent_cannot_be_started_through_create_agent_task(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    start = 'mutation($i:AgentTaskInput!){create_agent_task(input:$i){id}}'
    variables = {'i': {'prompt': 'hello', 'cwd': str(tmp_path), 'provider_id': 'missing', 'mode': 'ask', 'custom_agent_id': bot['custom_agent_id']}}
    query(client, owner, 'mutation($id:String!){set_bot_state(bot_id:$id,state:"paused"){state}}', {'id': bot['id']})
    denied(query(client, owner, start, variables), 409)


def test_revoke_rule_only_touches_this_bots_remembered_approvals(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    rule = state.agent_store.create_policy_rule(effect='allow', scope_type='custom_agent', scope_id=bot['custom_agent_id'],
                                                action_type='tool', tool='read_file', fingerprint='fp', display='read_file')
    shown = query(client, owner, 'query($id:String!){bot(bot_id:$id){rules}}', {'id': bot['id']})['data']['bot']['rules']
    assert [item['id'] for item in shown] == [rule['id']]
    other = state.agent_store.create_policy_rule(effect='allow', scope_type='project', scope_id='p', action_type='tool',
                                                 tool='read_file', fingerprint='fp2', display='x')
    revoke = 'mutation($id:String!,$r:String!){revoke_bot_rule(bot_id:$id,rule_id:$r){rules}}'
    denied(query(client, owner, revoke, {'id': bot['id'], 'r': other['id']}), 404)
    done = query(client, owner, revoke, {'id': bot['id'], 'r': rule['id']})
    assert done['data']['revoke_bot_rule']['rules'] == []
