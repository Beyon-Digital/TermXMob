"""Bot schedules over GraphQL: owner privacy, the re-authorize flow and the inbox."""
from test_authorization import client_fixture
from test_bots_graphql import create, denied, query, user

SCHEDULE = 'id revision bot_id conversation_id enabled state reason needs_reauthorization paused_by_bot grant_id max_runs next_run prompt'
SPEC = {'kind': 'interval', 'seconds': 3600}


def schedule(client, headers, bot, request_id='graphql-request-1', **extra):
    result = query(client, headers, f'mutation($c:String!,$i:BotScheduleInput!){{create_schedule(conversation_id:$c,input:$i){{{SCHEDULE}}}}}',
                   {'c': bot['conversation_id'], 'i': {'request_id': request_id, 'prompt': 'Check the repo', 'spec': SPEC, **extra}})
    assert not result.get('errors'), result
    return result['data']['create_schedule']


def listed(client, headers, bot):
    return query(client, headers, f'query($c:String!){{schedules(conversation_id:$c){{{SCHEDULE}}}}}',
                 {'c': bot['conversation_id']})['data']['schedules']


def test_create_list_edit_enable_and_archive(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot, max_runs=5)
    assert created['enabled'] and created['bot_id'] == bot['id'] and created['max_runs'] == 5 and not created['needs_reauthorization']
    assert [row['id'] for row in listed(client, owner, bot)] == [created['id']]
    edit = ('mutation($c:String!,$s:String!,$i:BotScheduleEditInput!)'
            '{edit_schedule(conversation_id:$c,schedule_id:$s,input:$i){revision prompt}}')
    edited = query(client, owner, edit, {'c': bot['conversation_id'], 's': created['id'], 'i': {'revision': created['revision'], 'prompt': 'Look again'}})
    assert edited['data']['edit_schedule']['prompt'] == 'Look again'
    denied(query(client, owner, edit, {'c': bot['conversation_id'], 's': created['id'], 'i': {'revision': created['revision'], 'prompt': 'stale'}}), 409)
    toggle = ('mutation($c:String!,$s:String!,$r:Int!){set_schedule_enabled(conversation_id:$c,schedule_id:$s,enabled:false,revision:$r)'
              '{enabled state}}')
    off = query(client, owner, toggle, {'c': bot['conversation_id'], 's': created['id'], 'r': edited['data']['edit_schedule']['revision']})
    assert off['data']['set_schedule_enabled'] == {'enabled': False, 'state': 'disabled'}
    current = listed(client, owner, bot)[0]
    gone = query(client, owner, 'mutation($c:String!,$s:String!,$r:Int!){archive_schedule(conversation_id:$c,schedule_id:$s,revision:$r){state}}',
                 {'c': bot['conversation_id'], 's': created['id'], 'r': current['revision']})
    assert gone['data']['archive_schedule']['state'] == 'archived' and listed(client, owner, bot) == []


def test_a_prompt_edit_parks_schedules_and_reauthorize_brings_them_back(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot)
    patch = 'mutation($id:String!,$i:BotPatchInput!){patch_bot(bot_id:$id,input:$i){revision}}'
    assert not query(client, owner, patch, {'id': bot['id'], 'i': {'revision': bot['revision'], 'instructions': 'Do something else.'}}).get('errors')
    parked = listed(client, owner, bot)[0]
    assert parked['needs_reauthorization'] and not parked['enabled'] and 'prompt or permissions' in parked['reason']
    enable = ('mutation($c:String!,$s:String!,$r:Int!){set_schedule_enabled(conversation_id:$c,schedule_id:$s,enabled:true,revision:$r){id}}')
    denied(query(client, owner, enable, {'c': bot['conversation_id'], 's': created['id'], 'r': parked['revision']}), 403)
    inbox = query(client, owner, 'query($b:String){needs_you(bot_id:$b){kind schedule_id bot_id title}}', {'b': bot['id']})['data']['needs_you']
    assert [(item['kind'], item['schedule_id']) for item in inbox] == [('schedule', created['id'])]
    done = query(client, owner, f'mutation($c:String!){{reauthorize_schedules(conversation_id:$c,grant_days:14){{{SCHEDULE}}}}}', {'c': bot['conversation_id']})
    row = done['data']['reauthorize_schedules'][0]
    assert row['enabled'] and not row['needs_reauthorization'] and row['grant_id'] != created['grant_id']
    assert query(client, owner, 'query{needs_you{id}}')['data']['needs_you'] == []


def test_changing_what_a_schedule_runs_or_turning_it_back_on_needs_a_host_session(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from termx.graphql.context import TermxContext
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot)
    monkeypatch.setattr(TermxContext, 'require_host', lambda self, scope: (_ for _ in ()).throw(HTTPException(403, 'Host session required')))
    c = bot['conversation_id']
    edit = ('mutation($c:String!,$s:String!,$i:BotScheduleEditInput!){edit_schedule(conversation_id:$c,schedule_id:$s,input:$i){revision}}')
    for change in ({'prompt': 'Something else'}, {'spec': {'kind': 'interval', 'seconds': 7200}}, {'report_to': 'owner'},
                   {'trigger': {'kind': 'message'}}):
        denied(query(client, owner, edit, {'c': c, 's': created['id'], 'i': {'revision': created['revision'], **change}}), 403)
    assert listed(client, owner, bot)[0]['prompt'] == 'Check the repo'
    toggle = 'mutation($c:String!,$s:String!,$r:Int!,$e:Boolean!){set_schedule_enabled(conversation_id:$c,schedule_id:$s,enabled:$e,revision:$r){enabled}}'
    harmless = query(client, owner, edit, {'c': c, 's': created['id'], 'i': {'revision': created['revision'], 'overlap': 'skip'}})
    assert not harmless.get('errors'), harmless  # timing-neutral fields don't widen what the bot reads
    off = query(client, owner, toggle, {'c': c, 's': created['id'], 'r': listed(client, owner, bot)[0]['revision'], 'e': False})
    assert off['data']['set_schedule_enabled'] == {'enabled': False}  # stopping is always allowed
    denied(query(client, owner, toggle, {'c': c, 's': created['id'], 'r': listed(client, owner, bot)[0]['revision'], 'e': True}), 403)


def test_pause_and_resume_follow_the_bot(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    schedule(client, owner, bot)
    query(client, owner, 'mutation($id:String!){set_bot_state(bot_id:$id,state:"paused"){state}}', {'id': bot['id']})
    row = listed(client, owner, bot)[0]
    assert not row['enabled'] and row['paused_by_bot']
    query(client, owner, 'mutation($id:String!){set_bot_state(bot_id:$id,state:"active"){state}}', {'id': bot['id']})
    assert listed(client, owner, bot)[0]['enabled']


def test_another_user_cannot_reach_a_bots_schedules_or_inbox(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    bob = user(identity, state, 'bob', 'operator')
    bot = create(client, owner)
    created = schedule(client, owner, bot)
    variables = {'c': bot['conversation_id']}
    denied(query(client, bob, f'query($c:String!){{schedules(conversation_id:$c){{id}}}}', variables), 404)
    denied(query(client, bob, 'query($c:String!){schedule_runs(conversation_id:$c){id}}', variables), 404)
    denied(query(client, bob, 'query($b:String){needs_you(bot_id:$b){id}}', {'b': bot['id']}), 404)
    denied(query(client, bob, 'mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){id}}',
                 {**variables, 'i': {'request_id': 'bob-request-1', 'prompt': 'x', 'spec': SPEC}}), 404)
    denied(query(client, bob, 'mutation($c:String!){reauthorize_schedules(conversation_id:$c){id}}', variables), 404)
    denied(query(client, bob, 'mutation($c:String!,$s:String!,$r:Int!){run_schedule_now(conversation_id:$c,schedule_id:$s,revision:$r){id}}',
                 {**variables, 's': created['id'], 'r': created['revision']}), 404)
    assert query(client, bob, 'query{needs_you{id}}')['data']['needs_you'] == []
    assert listed(client, owner, bot)[0]['enabled']


def test_a_viewer_cannot_create_or_reauthorize(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    viewer = user(identity, state, 'vera', 'viewer')
    bot = create(client, owner)
    denied(query(client, viewer, 'mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){id}}',
                 {'c': bot['conversation_id'], 'i': {'request_id': 'viewer-request-1', 'prompt': 'x', 'spec': SPEC}}), 403)
    denied(query(client, viewer, 'mutation($c:String!){reauthorize_schedules(conversation_id:$c){id}}', {'c': bot['conversation_id']}), 403)


def test_a_conversation_that_is_not_a_bot_is_not_found_and_bad_input_is_a_400(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    denied(query(client, owner, 'query{schedules(conversation_id:"nope"){id}}'), 404)
    bot = create(client, owner)
    result = query(client, owner, 'mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){id}}',
                   {'c': bot['conversation_id'], 'i': {'request_id': 'graphql-request-2', 'prompt': 'x', 'spec': {'kind': 'interval', 'seconds': 1}}})
    denied(result, 400)
    assert listed(client, owner, bot) == []


def test_the_aggregate_inbox_leaves_out_bots_the_token_cannot_see(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from termx.graphql.domains import bot_schedules
    identity, state, client, owner = client_fixture(tmp_path)
    mine, hidden = create(client, owner), create(client, owner, name='Hidden triage')
    for bot in (mine, hidden):
        row = state.workspace.store.get('bot', bot['id'])
        state.workspace.store.create('memory', row['owner'], {'content': 'note ' + bot['id'], 'provenance': 'run', 'scope': 'agent',
                                                              'agent_id': row['custom_agent_id'], 'author': 'agent', 'state': 'proposed'})
    assert {item['bot_id'] for item in query(client, owner, 'query{needs_you{bot_id}}')['data']['needs_you']} == {mine['id'], hidden['id']}
    blocked = state.workspace.store.get('bot', hidden['id'])['custom_agent_id']

    def scoped(ctx, agent, scope='agent-view'):
        if agent['id'] == blocked:
            raise HTTPException(403, 'custom agent is private or not shared')

    monkeypatch.setattr(bot_schedules, '_require_custom_agent', scoped)
    seen = query(client, owner, 'query{needs_you{bot_id}}')['data']['needs_you']
    assert {item['bot_id'] for item in seen} == {mine['id']}
