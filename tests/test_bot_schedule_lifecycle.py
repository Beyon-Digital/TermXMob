"""Bot schedule loose ends: the cap holds on every creation path and archiving a bot leaves nothing behind."""
from __future__ import annotations

import asyncio

from test_authorization import client_fixture
from test_bot_messaging import OWNER, bot_party, chatty, send
from test_bot_schedules import SPEC, add, scheduler
from test_bot_schedules_graphql import schedule as graphql_schedule
from test_bots import call, close, main_turn, make, setup
from test_bots_graphql import create, denied, query
from test_event_triggers import webhook
from termx.workspace.bot_schedules import MAX_SCHEDULES


def test_graphql_refuses_the_schedule_over_the_cap(tmp_path):
    _identity, _state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    for index in range(MAX_SCHEDULES):
        graphql_schedule(client, owner, bot, request_id=f'graphql-request-{index}')
    over = query(client, owner, 'mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){id}}',
                 {'c': bot['conversation_id'], 'i': {'request_id': 'graphql-over-cap', 'prompt': 'One more', 'spec': SPEC}})
    denied(over, 409)


def test_the_rest_automation_route_refuses_the_schedule_over_the_cap(tmp_path):
    _identity, _state, client, owner = client_fixture(tmp_path)
    bot = create(client, owner)
    body = dict(conversation_id=bot['conversation_id'], success_criteria='ok', max_runs=3, limits={}, prompt='Check',
                spec=SPEC, grant_days=7)
    for index in range(MAX_SCHEDULES):
        made = client.post('/api/workspace/automations', headers=owner, json={**body, 'request_id': f'rest-request-{index}'})
        assert made.status_code == 200, made.text
    over = client.post('/api/workspace/automations', headers=owner, json={**body, 'request_id': 'rest-over-cap'})
    assert over.status_code == 409, over.text


def test_the_orchestrator_tool_refuses_the_schedule_over_the_cap(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            scheduler(workspace, identity)
            parent = await main_turn(workspace, owner, manager)
            bot = make(workspace, owner)
            for index in range(MAX_SCHEDULES):
                add(workspace, owner, bot, request_id=f'seed-{index}')
            result = await workspace.orchestrator.command(parent['id'], call(
                'schedule_bot', 'over', bot_id=bot['id'], task='One more', spec=SPEC, max_runs=5, days=7))
            assert result['ok'] is False and 'at most' in result['error']
            assert len(workspace.bots.schedules.list(owner, bot['id'])) == MAX_SCHEDULES
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_archiving_a_bot_removes_its_schedules_webhook_secrets_and_unread_messages(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = chatty(workspace, owner)
        timed = add(workspace, owner, bot)
        hooked, secret = webhook(workspace, owner, bot)
        assert secret and len(workspace.store.list('bot_webhook')) == 1
        unread = send(workspace, owner, bot_party(bot), OWNER)
        assert unread['status'] == 'unread'
        workspace.bots.archive(owner, bot['id'])
        for row in (timed, hooked):
            stored = workspace.store.get('schedule', row['id'])
            assert stored['archived'] is True and stored['enabled'] is False and stored['state'] == 'archived'
            assert workspace.store.get('delegation', stored['grant_id'])['revoked'] is True
        assert workspace.store.list('bot_webhook') == []
        assert workspace.store.get('bot_message', unread['id'])['status'] == 'done'
        assert [m for m in workspace.store.list('bot_message', owner.id) if m['status'] in {'unread', 'parked'}] == []
    finally:
        asyncio.run(close(workspace, manager, ledger))
