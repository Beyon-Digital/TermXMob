"""A bot's schedule may carry a bounded computer permission, set only by the owner in a host session and only
when the bot's ceiling already allows computer control. The tool-name query that feeds the ceiling editor lives here too."""
from time import time

import pytest

from test_authorization import client_fixture
from test_bot_schedules_graphql import SCHEDULE, listed, schedule
from test_bots_graphql import create, denied, query

# Desktop control is set up (a macOS-shaped fake agent-desktop with a complete pin), whatever OS runs the suite.
pytestmark = pytest.mark.usefixtures('ready_desktop')

SPEC = {'kind': 'interval', 'seconds': 900}  # the permission has to outlast the first run

COMPUTER = SCHEDULE + ' computer_permission'
CREATE = 'mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){' + COMPUTER + '}}'
EDIT = 'mutation($c:String!,$s:String!,$i:BotScheduleEditInput!){edit_schedule(conversation_id:$c,schedule_id:$s,input:$i){' + COMPUTER + '}}'


def rows(client, owner, bot):
    return query(client, owner, 'query($c:String!){schedules(conversation_id:$c){' + COMPUTER + '}}', {'c': bot['conversation_id']})['data']['schedules']


def permission(**extra):
    return {'apps': ['Notes'], 'expires_at': time() + 3600, 'max_seconds': 120, **extra}


def allow_computer(client, owner, bot):
    """The owner widens the bot's ceiling and gives it the computer tool, the only way a schedule may name apps."""
    current = query(client, owner, 'query($id:String!){bot(bot_id:$id){revision ceiling}}', {'id': bot['id']})['data']['bot']
    ceiling = {'approval_mode': current['ceiling']['approval_mode'], 'sandbox_profile': current['ceiling']['sandbox_profile'],
               'tools': [*current['ceiling']['tools'], 'computer'],
               'deny_capabilities': [name for name in current['ceiling']['deny_capabilities'] if name != 'computer.control']}
    widened = query(client, owner, 'mutation($id:String!,$r:Int!,$c:BotCeilingInput!){set_bot_ceiling(bot_id:$id,revision:$r,ceiling:$c){revision}}',
                    {'id': bot['id'], 'r': current['revision'], 'c': ceiling})
    assert not widened.get('errors'), widened
    patched = query(client, owner, 'mutation($id:String!,$i:BotPatchInput!){patch_bot(bot_id:$id,input:$i){revision}}',
                    {'id': bot['id'], 'i': {'revision': widened['data']['set_bot_ceiling']['revision'], 'tools': ceiling['tools']}})
    assert not patched.get('errors'), patched


def test_a_schedule_has_no_computer_permission_unless_the_owner_adds_one(tmp_path, monkeypatch):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    assert schedule(client, owner, bot)['id']
    # The default ceiling denies computer control, so naming apps is refused rather than ignored.
    refused = query(client, owner, CREATE, {'c': bot['conversation_id'], 'i': {
        'request_id': 'computer-denied-1', 'prompt': 'Look', 'spec': SPEC, 'computer_permission': permission()}})
    denied(refused, 400)
    assert 'Permissions' in refused['errors'][0]['message']
    assert len(listed(client, owner, bot)) == 1


def test_a_schedule_can_be_created_with_and_edited_to_a_bounded_computer_permission(tmp_path, monkeypatch):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    allow_computer(client, owner, bot)
    c = bot['conversation_id']
    made = query(client, owner, CREATE, {'c': c, 'i': {'request_id': 'computer-ok-0001', 'prompt': 'Look', 'spec': SPEC, 'grant_days': 2,
                                                       'computer_permission': permission()}})
    assert not made.get('errors'), made
    row = made['data']['create_schedule']
    assert row['computer_permission']['apps'] == ['Notes'] and row['computer_permission']['max_seconds'] == 120
    # The expiry has to sit inside the authorization, apps are required and exact, and time is bounded.
    for bad in (permission(expires_at=time() + 5 * 86400), permission(apps=[]), permission(apps=['*']),
                permission(max_seconds=5), permission(max_seconds=7200), permission(expires_at=time() - 5)):
        denied(query(client, owner, EDIT, {'c': c, 's': row['id'], 'i': {'revision': row['revision'], 'computer_permission': bad}}), 400)
    assert rows(client, owner, bot)[0]['computer_permission']['apps'] == ['Notes']
    # Replace, then remove. Each change is its own edit, guarded by the revision.
    changed = query(client, owner, EDIT, {'c': c, 's': row['id'], 'i': {
        'revision': row['revision'], 'computer_permission': permission(apps=['Safari', 'Notes'], max_seconds=300)}})
    assert not changed.get('errors'), changed
    after = changed['data']['edit_schedule']
    assert after['computer_permission']['apps'] == ['Safari', 'Notes'] and after['revision'] > row['revision']
    denied(query(client, owner, EDIT, {'c': c, 's': row['id'], 'i': {'revision': row['revision'], 'computer_permission': permission()}}), 409)
    denied(query(client, owner, EDIT, {'c': c, 's': row['id'], 'i': {
        'revision': after['revision'], 'computer_permission': permission(), 'prompt': 'also this'}}), 400)
    denied(query(client, owner, EDIT, {'c': c, 's': row['id'], 'i': {
        'revision': after['revision'], 'computer_permission': permission(), 'clear_computer_permission': True}}), 400)
    cleared = query(client, owner, EDIT, {'c': c, 's': row['id'], 'i': {'revision': after['revision'], 'clear_computer_permission': True}})
    assert cleared['data']['edit_schedule']['computer_permission'] is None
    assert 'computer_permission' not in state.workspace.store.get('delegation', row['grant_id'])


def test_changing_a_computer_permission_needs_a_host_session(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from termx.graphql.context import TermxContext
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    allow_computer(client, owner, bot)
    created = schedule(client, owner, bot)
    c = bot['conversation_id']
    monkeypatch.setattr(TermxContext, 'require_host', lambda self, scope: (_ for _ in ()).throw(HTTPException(403, 'Host session required')))
    denied(query(client, owner, EDIT, {'c': c, 's': created['id'], 'i': {'revision': created['revision'], 'computer_permission': permission()}}), 403)
    denied(query(client, owner, EDIT, {'c': c, 's': created['id'], 'i': {'revision': created['revision'], 'clear_computer_permission': True}}), 403)
    denied(query(client, owner, CREATE, {'c': c, 'i': {'request_id': 'computer-host-01', 'prompt': 'x', 'spec': SPEC,
                                                       'computer_permission': permission()}}), 403)
    assert listed(client, owner, bot)[0]['revision'] == created['revision']


def test_an_edit_is_refused_while_the_ceiling_still_denies_computer_control(tmp_path, monkeypatch):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    created = schedule(client, owner, bot)
    denied(query(client, owner, EDIT, {'c': bot['conversation_id'], 's': created['id'], 'i': {
        'revision': created['revision'], 'computer_permission': permission()}}), 400)
    assert 'computer_permission' not in state.workspace.store.get('delegation', created['grant_id'])


def test_reauthorizing_does_not_carry_a_computer_permission_forward(tmp_path, monkeypatch):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    allow_computer(client, owner, bot)
    made = query(client, owner, CREATE, {'c': bot['conversation_id'], 'i': {
        'request_id': 'computer-reauth-1', 'prompt': 'Look', 'spec': SPEC, 'grant_days': 2, 'computer_permission': permission()}})
    assert made['data']['create_schedule']['computer_permission']
    current = query(client, owner, 'query($id:String!){bot(bot_id:$id){revision}}', {'id': bot['id']})['data']['bot']
    query(client, owner, 'mutation($id:String!,$i:BotPatchInput!){patch_bot(bot_id:$id,input:$i){revision}}',
          {'id': bot['id'], 'i': {'revision': current['revision'], 'instructions': 'Do something else.'}})
    assert listed(client, owner, bot)[0]['needs_reauthorization']
    done = query(client, owner, 'mutation($c:String!){reauthorize_schedules(conversation_id:$c,grant_days:7){' + COMPUTER + '}}',
                 {'c': bot['conversation_id']})
    row = done['data']['reauthorize_schedules'][0]
    assert row['enabled'] and row['computer_permission'] is None


def test_agent_tool_names_lists_the_host_registry_without_orchestration_tools(tmp_path):
    identity, state, client, owner = client_fixture(tmp_path)
    names = query(client, owner, '{agent_tool_names}')['data']['agent_tool_names']
    assert names == sorted(names) and len(names) == len(set(names))
    assert {'read_file', 'run_shell', 'read_skill', 'computer', 'send_message'} <= set(names)
    assert not {'create_bot', 'spawn_subagent', 'schedule_bot'} & set(names)
    anonymous = client.post('/graphql', json={'query': '{agent_tool_names}'})
    assert anonymous.status_code in (401, 403) or anonymous.json().get('errors')


def test_an_event_schedule_never_takes_a_computer_permission(tmp_path, monkeypatch):
    """A webhook payload or another bot's message must not be able to start a run that drives the desktop."""
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    allow_computer(client, owner, bot)
    c = bot['conversation_id']
    for kind in ('message', 'webhook'):
        refused = query(client, owner, CREATE, {'c': c, 'i': {'request_id': f'computer-event-{kind}', 'prompt': 'x', 'trigger': {'kind': kind},
                                                              'computer_permission': permission()}})
        denied(refused, 400)
        assert 'timed' in refused['errors'][0]['message']
    made = query(client, owner, CREATE, {'c': c, 'i': {'request_id': 'computer-event-plain', 'prompt': 'x', 'trigger': {'kind': 'message'}}})
    row = made['data']['create_schedule']
    denied(query(client, owner, EDIT, {'c': c, 's': row['id'], 'i': {'revision': row['revision'], 'computer_permission': permission()}}), 400)
    assert 'computer_permission' not in state.workspace.store.get('delegation', row['grant_id'])


def test_narrowing_the_ceiling_removes_computer_permissions(tmp_path, monkeypatch):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    allow_computer(client, owner, bot)
    c = bot['conversation_id']
    made = query(client, owner, CREATE, {'c': c, 'i': {'request_id': 'computer-narrow-1', 'prompt': 'Look', 'spec': SPEC, 'grant_days': 2,
                                                       'computer_permission': permission()}})['data']['create_schedule']
    assert 'computer_permission' in state.workspace.store.get('delegation', made['grant_id'])
    current = query(client, owner, 'query($id:String!){bot(bot_id:$id){revision ceiling}}', {'id': bot['id']})['data']['bot']
    ceiling = {**{k: current['ceiling'][k] for k in ('approval_mode', 'sandbox_profile', 'tools')},
               'deny_capabilities': [*current['ceiling']['deny_capabilities'], 'computer.control']}
    done = query(client, owner, 'mutation($id:String!,$r:Int!,$c:BotCeilingInput!){set_bot_ceiling(bot_id:$id,revision:$r,ceiling:$c){revision}}',
                 {'id': bot['id'], 'r': current['revision'], 'c': ceiling})
    assert not done.get('errors'), done
    assert 'computer_permission' not in state.workspace.store.get('delegation', made['grant_id'])
    assert rows(client, owner, bot)[0]['computer_permission'] is None


def test_adding_a_changing_tool_leaves_ask_mode_and_read_only_mode_takes_no_computer_permission(tmp_path, monkeypatch):
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    agents = state.workspace.agents
    assert agents.get_conversation(bot['conversation_id'], include_turns=False)['mode'] == 'ask'
    allow_computer(client, owner, bot)
    assert agents.get_conversation(bot['conversation_id'], include_turns=False)['mode'] == 'agent'
    agents.update_conversation(bot['conversation_id'], mode='ask')
    refused = query(client, owner, CREATE, {'c': bot['conversation_id'], 'i': {
        'request_id': 'computer-ask-0001', 'prompt': 'x', 'spec': SPEC, 'computer_permission': permission()}})
    denied(refused, 400)
    assert 'read-only' in refused['errors'][0]['message']


def test_rest_creation_with_a_computer_permission_needs_a_host_session(tmp_path, monkeypatch):
    """The browser's new-schedule form posts to REST. A token without host authority must not grant unattended computer control."""
    from test_bot_schedules_rest import API, demoted_owner
    identity, state, client, _owner = client_fixture(tmp_path)
    headers, bot, created = demoted_owner(identity, state, client, stopped=True)
    body = dict(conversation_id=bot['conversation_id'], success_criteria='ok', max_runs=3, limits={}, prompt='Look', spec=SPEC, grant_days=2,
                request_id='rest-computer-0001', computer_permission=permission())
    assert client.post(f'{API}/automations', headers=headers, json=body).status_code == 403
    assert len(state.workspace.store.list('schedule', state.workspace.store.get('schedule', created['id'])['owner'])) == 1


def _created(client, owner, bot, request_id):
    return query(client, owner, CREATE, {'c': bot['conversation_id'], 'i': {
        'request_id': request_id, 'prompt': 'Look', 'spec': SPEC, 'grant_days': 2, 'computer_permission': permission()}})


@pytest.mark.parametrize('system', ['macos', 'linux', 'windows'])
def test_a_schedule_takes_a_computer_permission_on_every_os_with_a_pinned_build(tmp_path, fake_agent_desktop, system):
    """The list is enforced from the application a ref belongs to, so no OS has to name the application in front."""
    fake_agent_desktop(system)
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    allow_computer(client, owner, bot)
    made = _created(client, owner, bot, f'computer-{system}-01')
    assert not made.get('errors'), made
    assert made['data']['create_schedule']['computer_permission']['apps'] == ['Notes']


@pytest.mark.parametrize('system, arch', [('linux', 'x86_64'), ('windows', 'x86_64'), ('macos', 'x86_64')])
def test_a_schedule_is_refused_a_computer_permission_where_the_build_has_no_complete_pin(tmp_path, system, arch):
    """The shipped pins list per-file hashes for macOS arm64 only until they are generated for the other platforms."""
    from termx.desktop import agent_desktop as ad
    ad.set_client(ad.AgentDesktopClient(home_root=tmp_path / 'desktop-control', platform=ad.HostPlatform(system, arch, True if system == 'linux' else None)))
    identity, state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    allow_computer(client, owner, bot)
    refused = _created(client, owner, bot, f'computer-nopin-{system}')
    denied(refused, 400)
    message = refused['errors'][0]['message']
    assert 'no verified build' in message and 'front' not in message
    assert len(listed(client, owner, bot)) == 0
