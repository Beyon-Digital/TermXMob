"""The REST schedule routes follow the same host-session rule as the GraphQL schedule mutations.

A bot's schedule is unattended authority. Changing what it reads or when it runs, or turning it on, needs
the owner's host session; stopping it never does.
"""
import asyncio

from test_authorization import client_fixture, PASSWORD
from test_bots_graphql import create, query, user
from test_bot_schedules_graphql import schedule, listed

API = '/api/workspace'


def demoted_owner(identity, state, client, stopped=False):
    """An admin who made a bot and a schedule, then lost host-wide authority (an untrusted operator still owns them)."""
    headers = user(identity, state, 'dana', 'admin')
    bot = create(client, headers)
    created = schedule(client, headers, bot)
    if stopped:
        toggle = 'mutation($c:String!,$s:String!,$r:Int!){set_schedule_enabled(conversation_id:$c,schedule_id:$s,enabled:false,revision:$r){revision}}'
        revision = query(client, headers, toggle, {'c': bot['conversation_id'], 's': created['id'], 'r': created['revision']})['data']['set_schedule_enabled']['revision']
        created = {**created, 'revision': revision}
    principal = next(row for row in state.authorization.list_principals() if row['display_name'] == 'dana')
    state.authorization.set_role(principal['id'], 'operator', trusted_execution=False)
    token = asyncio.run(identity.login('local-password', {'username': 'dana', 'password': PASSWORD}, peer='local')).access_token
    return {'Authorization': 'Bearer ' + token}, bot, created


def test_rest_cannot_change_what_a_bot_schedule_runs_without_a_host_session(tmp_path):
    identity, state, client, _owner = client_fixture(tmp_path)
    headers, bot, created = demoted_owner(identity, state, client)
    path = f"{API}/schedules/{created['id']}"
    for change in ({'prompt': 'Something else'}, {'spec': {'kind': 'interval', 'seconds': 7200}}):
        response = client.patch(path, headers=headers, json={'revision': created['revision'], **change})
        assert response.status_code == 403, response.text
    assert listed(client, headers, bot)[0]['prompt'] == 'Check the repo'


def test_rest_cannot_turn_a_stopped_bot_schedule_on_without_a_host_session(tmp_path):
    identity, state, client, _owner = client_fixture(tmp_path)
    headers, bot, created = demoted_owner(identity, state, client, stopped=True)
    path = f"{API}/schedules/{created['id']}"
    on = client.patch(path, headers=headers, json={'revision': created['revision'], 'enabled': True})
    assert on.status_code == 403, on.text
    assert listed(client, headers, bot)[0]['enabled'] is False


def test_rest_creation_routes_refuse_a_bot_schedule_without_a_host_session(tmp_path):
    """Defence in depth: the conversation's own agent-run check already refuses this principal on both routes, and
    require_bot_host sits behind it. This pins the outcome (403, nothing created), not which check fired."""
    identity, state, client, _owner = client_fixture(tmp_path)
    headers, bot, created = demoted_owner(identity, state, client, stopped=True)
    row = state.workspace.store.get('schedule', created['id'])
    body = dict(goal_id=row['goal_id'], grant_id=row['grant_id'], prompt='New', spec={'kind': 'interval', 'seconds': 3600}, enabled=True)
    assert client.post(f'{API}/schedules', headers=headers, json=body).status_code == 403
    automation = dict(conversation_id=bot['conversation_id'], success_criteria='ok', max_runs=3, limits={}, prompt='New',
                      spec={'kind': 'interval', 'seconds': 3600}, grant_days=7, request_id='rest-untrusted')
    assert client.post(f'{API}/automations', headers=headers, json=automation).status_code == 403
    assert len(state.workspace.store.list('schedule', row['owner'])) == 1


def test_rest_still_edits_timing_policy_without_a_host_session(tmp_path):
    identity, state, client, _owner = client_fixture(tmp_path)
    headers, bot, created = demoted_owner(identity, state, client)
    path = f"{API}/schedules/{created['id']}"
    response = client.patch(path, headers=headers, json={'revision': created['revision'], 'overlap': 'queue'})
    assert response.status_code == 200, response.text  # timing-neutral policy does not widen what the bot reads


def test_rest_still_works_with_a_host_session(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot)
    path = f"{API}/schedules/{created['id']}"
    edited = client.patch(path, headers=owner, json={'revision': created['revision'], 'prompt': 'Look again'})
    assert edited.status_code == 200, edited.text
    off = client.patch(path, headers=owner, json={'revision': edited.json()['revision'], 'enabled': False})
    on = client.patch(path, headers=owner, json={'revision': off.json()['revision'], 'enabled': True})
    assert on.status_code == 200 and on.json()['enabled'] is True, on.text
    row = state.workspace.store.get('schedule', created['id'])
    body = dict(goal_id=row['goal_id'], grant_id=row['grant_id'], prompt='Second', spec={'kind': 'interval', 'seconds': 3600})
    assert client.post(f'{API}/schedules', headers=owner, json=body).status_code == 200


def test_rest_schedule_routes_do_not_accept_triggers_or_report_targets(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot)
    path = f"{API}/schedules/{created['id']}"
    for extra in ({'trigger': {'kind': 'message'}}, {'report_to': 'owner'}):
        assert client.patch(path, headers=owner, json={'revision': created['revision'], **extra}).status_code == 422

