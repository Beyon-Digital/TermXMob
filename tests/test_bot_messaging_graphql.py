"""Bot messages, trigger fields and the webhook endpoint through the real app."""
import hashlib
import hmac
import json
from time import time

from test_authorization import client_fixture
from test_bots_graphql import denied, query, user

TOOLS = ['memory_read', 'read_inbox', 'send_message', 'mark_message']
MESSAGE = 'id thread_id sender recipient kind subject body status reason hops automatic in_reply_to'
SCHEDULE = 'id revision enabled state trigger report_to webhook_path webhook_secret next_run spec'


def create(client, headers, name='Chatty', peers=None):
    result = query(client, headers, 'mutation($i:BotInput!){create_bot(input:$i){id revision conversation_id name}}',
                   {'i': {'name': name, 'instructions': 'Pass messages along.', 'tools': TOOLS, 'peers': peers}})
    assert not result.get('errors'), result
    return result['data']['create_bot']


def send(client, headers, to, **extra):
    document = f'mutation($i:SendBotMessageInput!){{send_bot_message(input:$i){{{MESSAGE}}}}}'
    return query(client, headers, document, {'i': {'to': to, 'body': 'Please look at the build.', 'subject': 'Build', **extra}})


def test_owner_sends_lists_and_marks_messages(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    sent = send(client, owner, 'bot:' + bot['id'])['data']['send_bot_message']
    assert sent['sender']['kind'] == 'owner' and sent['recipient']['id'] == bot['id'] and sent['status'] == 'unread' and sent['hops'] == 1
    listed = query(client, owner, f'query($b:String!){{bot_messages(bot_id:$b){{{MESSAGE}}}}}', {'b': bot['id']})['data']['bot_messages']
    assert [m['id'] for m in listed] == [sent['id']]
    threaded = query(client, owner, 'query($b:String!,$t:String){bot_messages(bot_id:$b,thread_id:$t){id}}', {'b': bot['id'], 't': 'other'})
    assert threaded['data']['bot_messages'] == []
    bad = send(client, owner, 'bot:' + bot['id'], kind='command')
    denied(bad, 400)
    marked = query(client, owner, 'mutation($m:String!){mark_message(message_id:$m,status:"done"){id status}}', {'m': sent['id']})
    assert marked['data']['mark_message']['status'] == 'done'
    denied(query(client, owner, 'mutation($m:String!){mark_message(message_id:$m,status:"unread"){id}}', {'m': sent['id']}), 400)
    denied(query(client, owner, 'mutation($m:String!){mark_message(message_id:$m,status:"done"){id}}', {'m': 'missing'}), 404)


def test_a_bot_message_to_the_owner_shows_in_needs_you(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    service = state.workspace.bots.messages
    service.send(state.identity.principal_by_id(state.workspace.store.get('bot', bot['id'])['owner']), {'kind': 'bot', 'id': bot['id']},
                 'owner', kind='report', subject='Deploy is blocked', body='Staging is down.')
    items = query(client, owner, '{needs_you{kind message_id title detail bot_id}}')['data']['needs_you']
    assert [(i['kind'], i['bot_id']) for i in items] == [('message', bot['id'])] and 'Deploy is blocked' in items[0]['title']
    query(client, owner, 'mutation($m:String!){mark_message(message_id:$m,status:"read"){id}}', {'m': items[0]['message_id']})
    assert query(client, owner, '{needs_you{id}}')['data']['needs_you'] == []


def test_messages_are_private_to_their_owner(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bob = user(identity, state, 'bob', 'operator')
    bot = create(client, owner)
    sent = send(client, owner, 'bot:' + bot['id'])['data']['send_bot_message']
    denied(query(client, bob, 'query($b:String!){bot_messages(bot_id:$b){id}}', {'b': bot['id']}), 404)
    denied(send(client, bob, 'bot:' + bot['id']), 404)
    denied(query(client, bob, 'mutation($m:String!){mark_message(message_id:$m,status:"done"){id}}', {'m': sent['id']}), 404)
    assert state.workspace.store.get('bot_message', sent['id'])['status'] == 'unread'


def schedule(client, headers, bot, trigger, request_id='graphql-trigger-1', **extra):
    result = query(client, headers, f'mutation($c:String!,$i:BotScheduleInput!){{create_schedule(conversation_id:$c,input:$i){{{SCHEDULE}}}}}',
                   {'c': bot['conversation_id'], 'i': {'request_id': request_id, 'prompt': 'Handle it', 'trigger': trigger, **extra}})
    assert not result.get('errors'), result
    return result['data']['create_schedule']


def test_trigger_and_report_to_on_schedule_create_and_edit(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot, {'kind': 'message'}, report_to='owner')
    assert created['trigger'] == {'kind': 'message'} and created['report_to']['kind'] == 'owner' and created['enabled'] and created['next_run'] is None
    edit = ('mutation($c:String!,$s:String!,$i:BotScheduleEditInput!){edit_schedule(conversation_id:$c,schedule_id:$s,input:$i){revision report_to trigger}}')
    edited = query(client, owner, edit, {'c': bot['conversation_id'], 's': created['id'], 'i': {'revision': created['revision'], 'report_to': 'orchestrator'}})
    assert edited['data']['edit_schedule']['report_to']['kind'] == 'orchestrator'
    denied(query(client, owner, edit, {'c': bot['conversation_id'], 's': created['id'],
                                       'i': {'revision': edited['data']['edit_schedule']['revision'], 'trigger': {'kind': 'webhook'}}}), 400)
    result = query(client, owner, 'mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){id}}',
                   {'c': bot['conversation_id'], 'i': {'request_id': 'graphql-trigger-bad', 'prompt': 'x', 'trigger': {'kind': 'sometimes'}}})
    denied(result, 400)


def signed(secret, body):
    stamp = str(int(time()))
    mac = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return {'X-Termx-Timestamp': stamp, 'X-Termx-Signature': 'sha256=' + mac}


def test_webhook_secret_is_shown_once_and_the_endpoint_works_without_a_session(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot, {'kind': 'webhook'})
    secret = created['webhook_secret']
    assert secret and created['enabled'] is False and created['webhook_path'] == f'/hooks/bots/{bot["id"]}/{created["id"]}'
    listed = query(client, owner, f'query($c:String!){{schedules(conversation_id:$c){{{SCHEDULE}}}}}', {'c': bot['conversation_id']})
    assert listed['data']['schedules'][0]['webhook_secret'] is None and secret not in json.dumps(listed)
    body = json.dumps({'event': 'deploy'}).encode()
    assert client.post(created['webhook_path'], content=body, headers=signed(secret, body)).status_code == 404  # off until enabled
    toggle = 'mutation($c:String!,$s:String!,$r:Int!){set_schedule_enabled(conversation_id:$c,schedule_id:$s,enabled:true,revision:$r){revision}}'
    query(client, owner, toggle, {'c': bot['conversation_id'], 's': created['id'], 'r': created['revision']})
    assert client.post(created['webhook_path'], content=body, headers=signed(secret, body)).status_code == 202  # no sign-in needed
    assert client.post(created['webhook_path'], content=body, headers=signed('wrong', body)).status_code == 401
    rotated = query(client, owner, f'mutation($c:String!,$s:String!){{rotate_webhook_secret(conversation_id:$c,schedule_id:$s){{{SCHEDULE}}}}}',
                    {'c': bot['conversation_id'], 's': created['id']})['data']['rotate_webhook_secret']
    assert rotated['webhook_secret'] and rotated['webhook_secret'] != secret
    assert client.post(created['webhook_path'], content=body + b' ', headers=signed(secret, body + b' ')).status_code == 401


def test_another_user_cannot_create_or_rotate_a_webhook(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bob = user(identity, state, 'bob', 'operator')
    bot = create(client, owner)
    created = schedule(client, owner, bot, {'kind': 'webhook'})
    variables = {'c': bot['conversation_id'], 's': created['id']}
    denied(query(client, bob, 'mutation($c:String!,$s:String!){rotate_webhook_secret(conversation_id:$c,schedule_id:$s){id}}', variables), 404)
    denied(query(client, bob, 'mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){id}}',
                 {'c': bot['conversation_id'], 'i': {'request_id': 'bob-hook-1', 'prompt': 'x', 'trigger': {'kind': 'webhook'}}}), 404)
