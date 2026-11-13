"""Per-bot schedules, re-authorization, the needs-you inbox and completion reports.

Schedules are the existing delegated ``AutomationService`` records, bound to the bot's conversation.
A bot cannot rewrite its own authority: editing its prompt or permissions parks its schedules until
the owner re-authorizes them, and a scheduled run that needs approval waits for the owner.
"""
from __future__ import annotations

import asyncio
from time import time
from types import SimpleNamespace

import pytest

from test_bots import call, close, main_turn, make, setup
from termx.agent.tools import default_registry
from termx.workspace.automation import AutomationService
from termx.workspace.bot_schedules import MAX_SCHEDULES
from termx.workspace.store import Conflict

SPEC = {'kind': 'interval', 'seconds': 3600}


def scheduler(workspace, identity):
    clock = [time()]
    automation = AutomationService(workspace, principal_lookup=identity.principal_by_id, clock=lambda: clock[0])
    workspace.state.automation = automation
    return automation, clock


def add(workspace, owner, bot, request_id='1', prompt='Check the repo', **extra):
    return workspace.bots.schedules.create(owner, bot['id'], request_id=f'request-{request_id}', prompt=prompt,
                                           spec=extra.pop('spec', SPEC), success_criteria='A short report', **extra)


async def dispatch_ready(workspace, owner, bot, started):
    """Wait for this real dispatch, rather than assuming run_now's 50 ms return window linked its task."""
    worker = workspace.state.automation._dispatch_workers.get(started['id'])
    if worker is not None:
        await asyncio.wait_for(worker, timeout=5)
    ready = next(row for row in workspace.bots.schedules.runs(owner, bot['id']) if row['id'] == started['id'])
    assert ready['status'] in {'running', 'completed'}, ready
    assert ready['task_id'], ready
    task = workspace.state.agent_store.get_task(ready['task_id'])
    assert task and task['custom_agent_id'] == bot['custom_agent_id'], ready
    metadata = workspace.store.get('task', ready['task_id'])
    assert metadata and metadata['conversation_id'] == bot['conversation_id'], ready
    return ready


# -- creating and listing ---------------------------------------------------------------

def test_create_binds_the_schedule_to_the_bots_conversation_and_is_idempotent(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        created = add(workspace, owner, bot)
        assert created['enabled'] and created['state'] == 'enabled' and created['bot_id'] == bot['id']
        assert created['conversation_id'] == bot['conversation_id'] and created['needs_reauthorization'] is False
        assert created['next_run'] and created['max_runs'] >= 1 and created['grant_expires_at'] > time()
        assert add(workspace, owner, bot)['id'] == created['id']
        assert [row['id'] for row in workspace.bots.schedules.list(owner, bot['id'])] == [created['id']]
        goal = workspace.store.get('goal', workspace.store.get('schedule', created['id'])['goal_id'])
        assert goal['conversation_id'] == bot['conversation_id']
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_schedules_are_per_bot_and_capped(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        first, second = make(workspace, owner), make(workspace, owner, name='Other')
        for index in range(MAX_SCHEDULES):
            add(workspace, owner, first, request_id=f'r{index}')
        with pytest.raises(Conflict):
            add(workspace, owner, first, request_id='one-too-many')
        add(workspace, owner, second)
        assert len(workspace.bots.schedules.list(owner, first['id'])) == MAX_SCHEDULES
        assert len(workspace.bots.schedules.list(owner, second['id'])) == 1
        # An archived schedule frees a slot.
        row = workspace.bots.schedules.list(owner, first['id'])[0]
        workspace.bots.schedules.archive(owner, first['id'], row['id'], row['revision'])
        add(workspace, owner, first, request_id='fits-now')
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_a_paused_or_deleted_bot_cannot_get_new_schedules(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        workspace.bots.set_state(owner, bot['id'], 'paused')
        with pytest.raises(Conflict):
            add(workspace, owner, bot)
        workspace.bots.archive(owner, bot['id'])
        with pytest.raises(Conflict):
            add(workspace, owner, bot, request_id='r2')
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_bad_schedules_are_refused_without_leaving_records(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        with pytest.raises(ValueError):
            add(workspace, owner, bot, spec={'kind': 'interval', 'seconds': 5})
        with pytest.raises(ValueError):
            add(workspace, owner, bot, request_id='r3', grant_days=500)
        assert workspace.store.list('goal', owner.id) == [] and workspace.store.list('delegation', owner.id) == []
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_another_bots_or_conversations_schedule_cannot_be_reached_through_this_bot(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        first, second = make(workspace, owner), make(workspace, owner, name='Other')
        mine = add(workspace, owner, first)
        with pytest.raises(KeyError):
            workspace.bots.schedules.enable(owner, second['id'], mine['id'], False, mine['revision'])
        with pytest.raises(KeyError):
            workspace.bots.schedules.archive(owner, second['id'], mine['id'], mine['revision'])
        with pytest.raises(KeyError):
            asyncio.run(workspace.bots.schedules.revoke(owner, second['id'], mine['grant_id']))
    finally:
        asyncio.run(close(workspace, manager, ledger))


# -- pause and resume ----------------------------------------------------------------------

def test_pausing_a_bot_turns_off_its_schedules_and_resuming_restores_only_those(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        on, off = add(workspace, owner, bot, request_id='on'), add(workspace, owner, bot, request_id='off')
        workspace.bots.schedules.enable(owner, bot['id'], off['id'], False, off['revision'])
        workspace.bots.set_state(owner, bot['id'], 'paused')
        by_id = {row['id']: row for row in workspace.bots.schedules.list(owner, bot['id'])}
        assert not by_id[on['id']]['enabled'] and by_id[on['id']]['paused_by_bot'] is True
        assert not by_id[off['id']]['enabled'] and by_id[off['id']]['paused_by_bot'] is False
        workspace.bots.set_state(owner, bot['id'], 'active')
        by_id = {row['id']: row for row in workspace.bots.schedules.list(owner, bot['id'])}
        assert by_id[on['id']]['enabled'] and by_id[on['id']]['paused_by_bot'] is False
        assert not by_id[off['id']]['enabled']
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_a_paused_bot_starts_no_scheduled_runs(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            automation, clock = scheduler(workspace, identity)
            bot = make(workspace, owner)
            add(workspace, owner, bot)
            workspace.bots.set_state(owner, bot['id'], 'paused')
            clock[0] += 4000
            await automation.tick()
            assert workspace.store.list('schedule_run', owner.id) == []
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- re-authorize ---------------------------------------------------------------------------

def test_editing_the_prompt_parks_schedules_until_the_owner_reauthorizes(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            automation, clock = scheduler(workspace, identity)
            bot = make(workspace, owner)
            schedule = add(workspace, owner, bot)
            old_grant = schedule['grant_id']
            workspace.bots.patch(owner, bot['id'], revision=bot['revision'], instructions='Do something else entirely.')
            parked = workspace.bots.schedules.list(owner, bot['id'])[0]
            assert not parked['enabled'] and parked['needs_reauthorization'] is True and parked['state'] == 'needs_reauthorization'
            assert 'prompt or permissions' in parked['reason']
            clock[0] += 4000
            await automation.tick()
            assert workspace.store.list('schedule_run', owner.id) == []
            with pytest.raises(PermissionError):
                workspace.bots.schedules.enable(owner, bot['id'], schedule['id'], True, parked['revision'])
            with pytest.raises(PermissionError):
                await workspace.bots.schedules.run_now(owner, bot['id'], schedule['id'], parked['revision'])

            fresh = workspace.bots.get(owner, bot['id'])
            done = workspace.bots.schedules.reauthorize(owner, bot['id'], grant_days=14)
            assert [row['id'] for row in done] == [schedule['id']]
            back = workspace.bots.schedules.list(owner, bot['id'])[0]
            assert back['enabled'] and back['needs_reauthorization'] is False and back['grant_id'] != old_grant
            assert workspace.store.get('delegation', old_grant)['revoked'] is True
            assert workspace.store.get('delegation', back['grant_id'])['revoked'] is False
            assert fresh['revision'] >= bot['revision']
            clock[0] += 4000
            await automation.tick()
            assert len(workspace.store.list('schedule_run', owner.id)) == 1
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_reauthorize_leaves_user_disabled_schedules_off_and_skips_current_ones(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        on, off = add(workspace, owner, bot, request_id='on'), add(workspace, owner, bot, request_id='off')
        workspace.bots.schedules.enable(owner, bot['id'], off['id'], False, off['revision'])
        assert workspace.bots.schedules.reauthorize(owner, bot['id']) == []  # nothing needs it
        workspace.bots.patch(owner, bot['id'], revision=bot['revision'], description='New description')
        done = workspace.bots.schedules.reauthorize(owner, bot['id'])
        assert {row['id'] for row in done} == {on['id'], off['id']}
        by_id = {row['id']: row for row in workspace.bots.schedules.list(owner, bot['id'])}
        assert by_id[on['id']]['enabled'] and not by_id[off['id']]['enabled']
        assert not by_id[off['id']]['needs_reauthorization']
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_reauthorize_cannot_revive_a_schedule_with_no_runs_left(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        schedule = add(workspace, owner, bot, max_runs=1)
        goal = workspace.store.get('goal', workspace.store.get('schedule', schedule['id'])['goal_id'])
        workspace.store.update('goal', goal['id'], {**goal, 'runs_started': 1})
        workspace.bots.patch(owner, bot['id'], revision=bot['revision'], description='Changed')
        with pytest.raises(ValueError, match='no runs left'):
            workspace.bots.schedules.reauthorize(owner, bot['id'])
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_revoking_a_grant_stops_its_schedules(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            scheduler(workspace, identity)
            bot = make(workspace, owner)
            schedule = add(workspace, owner, bot)
            await workspace.bots.schedules.revoke(owner, bot['id'], schedule['grant_id'])
            row = workspace.bots.schedules.list(owner, bot['id'])[0]
            assert not row['enabled'] and row['state'] == 'grant_revoked'
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- running and runs --------------------------------------------------------------------------

def test_run_now_runs_the_bot_and_lists_the_run(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            scheduler(workspace, identity)
            bot = make(workspace, owner)
            schedule = add(workspace, owner, bot)
            started = await workspace.bots.schedules.run_now(owner, bot['id'], schedule['id'], schedule['revision'])
            started = await dispatch_ready(workspace, owner, bot, started)
            assert started['status'] in {'running', 'completed'} and started['manual'] is True
            runs = workspace.bots.schedules.runs(owner, bot['id'])
            assert [row['id'] for row in runs] == [started['id']] and runs[0]['schedule_id'] == schedule['id']
            assert runs[0]['bot_id'] == bot['id']
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_run_now_returns_before_a_blocked_dispatch_links_its_real_task(tmp_path, monkeypatch):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        automation, _ = scheduler(workspace, identity)
        original_send = workspace.send

        async def gated_send(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_send(*args, **kwargs)

        monkeypatch.setattr(workspace, 'send', gated_send)
        try:
            bot = make(workspace, owner)
            schedule = add(workspace, owner, bot, prompt='HOLD after the real dispatch')
            before = {task['id'] for task in ledger.list_tasks(limit=500)}
            started = await workspace.bots.schedules.run_now(owner, bot['id'], schedule['id'], schedule['revision'])
            await asyncio.wait_for(entered.wait(), timeout=5)
            assert entered.is_set() and started['status'] == 'dispatching' and started['task_id'] is None
            assert workspace.store.get('schedule_run', started['id'])['task_id'] is None
            assert {task['id'] for task in ledger.list_tasks(limit=500)} == before

            release.set()
            ready = await dispatch_ready(workspace, owner, bot, started)
            assert ready['status'] == 'running' and ready['manual'] is True
            assert {task['id'] for task in ledger.list_tasks(limit=500)} - before == {ready['task_id']}
            assert workspace.store.get('task', ready['task_id'])['delegation_id'] == schedule['grant_id']
            assert ready['task_id'] in manager._workers and not manager._workers[ready['task_id']].done()
        finally:
            release.set()
            await automation.close()
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- the needs-you inbox ---------------------------------------------------------------------------

def test_a_scheduled_run_that_needs_approval_parks_and_shows_in_the_inbox(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            automation, clock = scheduler(workspace, identity)
            bot = make(workspace, owner)
            schedule = add(workspace, owner, bot, prompt='HOLD for a moment')
            started = await workspace.bots.schedules.run_now(owner, bot['id'], schedule['id'], schedule['revision'])
            started = await dispatch_ready(workspace, owner, bot, started)
            task_id = workspace.store.get('schedule_run', started['id'])['task_id']
            ledger.update_task(task_id, status='awaiting_approval')
            approval = ledger.create_approval(task_id, 'tool', {'title': 'Run git status'})
            await automation.tick()
            assert ledger.get_approval(approval['id'])['status'] == 'pending'  # never auto-approved
            items = workspace.bots.inbox(owner)
            found = [item for item in items if item['kind'] == 'approval']
            assert len(found) == 1 and found[0]['bot_id'] == bot['id'] and found[0]['task_id'] == task_id
            assert found[0]['approval_id'] == approval['id'] and 'git status' in found[0]['title']
            assert found[0]['scheduled'] is True
            assert workspace.bots.inbox(owner, bot_id=bot['id']) == items
            assert workspace.bots.get(owner, bot['id'])['needs_you'] == 1
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_inbox_lists_needs_attention_runs_parked_schedules_and_proposed_memory(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot, other = make(workspace, owner), make(workspace, owner, name='Other')
        schedule = add(workspace, owner, bot)
        goal_id = workspace.store.get('schedule', schedule['id'])['goal_id']
        run = workspace.store.create('schedule_run', owner.id, {'schedule_id': schedule['id'], 'goal_id': goal_id,
                                     'status': 'needs_attention', 'reason': 'Task ledger unavailable'})
        workspace.store.create('memory', owner.id, {'content': 'Prefers short reports', 'provenance': 'run', 'scope': 'agent',
                                                    'agent_id': bot['custom_agent_id'], 'author': 'agent', 'state': 'proposed'})
        workspace.store.create('memory', owner.id, {'content': 'Other bot note', 'provenance': 'run', 'scope': 'agent',
                                                    'agent_id': other['custom_agent_id'], 'author': 'agent', 'state': 'proposed'})
        workspace.bots.patch(owner, bot['id'], revision=bot['revision'], description='Changed')
        kinds = {item['kind']: item for item in workspace.bots.inbox(owner, bot_id=bot['id'])}
        assert set(kinds) == {'run', 'schedule', 'memory'}
        assert kinds['run']['run_id'] == run['id'] and 'Task ledger' in kinds['run']['detail']
        assert kinds['schedule']['schedule_id'] == schedule['id']
        assert kinds['memory']['detail'] == 'Prefers short reports'
        assert all(item['bot_id'] == bot['id'] for item in kinds.values())
        # The owner sees every bot's items, and the bot's own count is the same total.
        everything = workspace.bots.inbox(owner)
        assert {item['bot_id'] for item in everything} == {bot['id'], other['id']}
        assert workspace.bots.get(owner, bot['id'])['needs_you'] == 3
        workspace.bots.schedules.acknowledge_run(owner, bot['id'], run['id'])
        assert 'run' not in {item['kind'] for item in workspace.bots.inbox(owner, bot_id=bot['id'])}
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_inbox_is_owner_private_and_hides_archived_bots(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        workspace.store.create('memory', owner.id, {'content': 'note', 'provenance': 'run', 'scope': 'agent',
                                                    'agent_id': bot['custom_agent_id'], 'author': 'agent', 'state': 'proposed'})
        other = identity.create_local_user('someone', 'another strong password', ['agent-view', 'agent-run', 'agent-control'])
        assert workspace.bots.inbox(other) == []
        with pytest.raises((KeyError, PermissionError)):
            workspace.bots.inbox(other, bot_id=bot['id'])
        workspace.bots.archive(owner, bot['id'])
        assert workspace.bots.inbox(owner) == []
    finally:
        asyncio.run(close(workspace, manager, ledger))


# -- completion reports ------------------------------------------------------------------------------

def test_a_finished_scheduled_run_posts_a_reference_only_report(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            automation, clock = scheduler(workspace, identity)
            bot = make(workspace, owner)
            schedule = add(workspace, owner, bot)
            started = await workspace.bots.schedules.run_now(owner, bot['id'], schedule['id'], schedule['revision'])
            started = await dispatch_ready(workspace, owner, bot, started)
            task_id = workspace.store.get('schedule_run', started['id'])['task_id']
            ledger.update_task(task_id, status='completed', result='Found two new commits; nothing risky.')
            before = len(ledger.list_tasks(limit=500))
            await automation.tick()
            await automation.tick()
            activity = workspace.orchestrator.activity(owner)['activity']
            reports = [row for row in activity if row['kind'] == 'bot.run.finished']
            assert len(reports) == 1  # idempotent across ticks
            assert reports[0]['status'] == 'completed' and reports[0]['task_id'] == task_id
            assert reports[0]['session_id'] == bot['conversation_id']
            assert bot['name'] in reports[0]['message'] and 'two new commits' in reports[0]['message']
            assert len(ledger.list_tasks(limit=500)) == before  # reports never start a model turn
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


# -- the schedule_bot orchestrator tool --------------------------------------------------------------

def test_schedule_bot_creates_a_schedule_for_the_main_agent_only(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            scheduler(workspace, identity)
            parent = await main_turn(workspace, owner, manager)
            bot = make(workspace, owner)
            result = await workspace.orchestrator.command(parent['id'], call(
                'schedule_bot', 's1', bot_id=bot['id'], task='Check the repo', spec=SPEC, max_runs=5, days=7))
            assert result['ok'] is True and result['schedule']['bot_id'] == bot['id'] and result['schedule']['enabled']
            assert result['schedule']['max_runs'] == 5
            rows = workspace.bots.schedules.list(owner, bot['id'])
            assert [row['prompt'] for row in rows] == ['Check the repo']
            again = await workspace.orchestrator.command(parent['id'], call(
                'schedule_bot', 's1', bot_id=bot['id'], task='Check the repo', spec=SPEC, max_runs=5, days=7))
            assert again == result and len(workspace.bots.schedules.list(owner, bot['id'])) == 1  # receipt, no second schedule
            bad = await workspace.orchestrator.command(parent['id'], call(
                'schedule_bot', 's2', bot_id=bot['id'], task='x', spec={'kind': 'interval', 'seconds': 1}))
            assert bad['ok'] is False and 'Interval' in bad['error']
            # Only the owner's unscheduled main agent may schedule; a bot's own task is refused.
            task = await workspace.bots.assign_task(owner, bot['id'], prompt='HOLD', request_id='own')
            with pytest.raises(PermissionError):
                await workspace.orchestrator.command(task['id'], call('schedule_bot', 's3', bot_id=bot['id'], task='x', spec=SPEC))
            meta = workspace.store.get('task', parent['id'])
            workspace.store.update('task', parent['id'], {**meta, 'delegation_id': 'grant-1'})
            with pytest.raises(PermissionError):
                await workspace.orchestrator.command(parent['id'], call('schedule_bot', 's4', bot_id=bot['id'], task='x', spec=SPEC))
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_schedule_bot_always_asks_the_owner_and_shows_what_it_grants(tmp_path):
    spec = default_registry().get('schedule_bot')
    ctx = SimpleNamespace(task_id='t', manager=SimpleNamespace(orchestrator=None))
    decision = spec.decide(call('schedule_bot', bot_id='b1', task='Check the repo', spec=SPEC, max_runs=5, days=7), ctx)
    assert decision.approval_required and decision.allowed is False
    assert 'Check the repo' in decision.consequence and '5 runs' in decision.consequence and '7 days' in decision.consequence
    assert spec.approval == 'always'
    assert 'spec' in spec.parameters['properties'] and 'when' not in spec.parameters['properties']


def test_the_rest_scheduler_applies_the_same_bot_rules(tmp_path):
    """The REST routes call AutomationService directly, so the rules live there rather than only in the GraphQL wrappers."""
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        automation, _ = scheduler(workspace, identity)
        bot = make(workspace, owner)
        base = dict(request_id='rest-request-1', conversation_id=bot['conversation_id'], success_criteria='ok', max_runs=3,
                    limits={}, prompt='Check', spec=SPEC, grant_days=7)
        # A bot's schedule is the bot itself: no saved shell commands and no unattended browser grants.
        with pytest.raises(ValueError, match='ceiling'):
            automation.create(owner, **{**base, 'command': 'ls'})
        with pytest.raises(ValueError, match='ceiling'):
            automation.create(owner, **{**base, 'runbook_id': 'anything'})
        assert workspace.store.list('goal', owner.id) == []
        row = automation.create(owner, **base)
        workspace.bots.patch(owner, bot['id'], revision=bot['revision'], description='Changed')
        with pytest.raises(PermissionError, match='Re-authorize'):
            automation.enable(owner, row['id'], True, workspace.store.get('schedule', row['id'])['revision'])
        with pytest.raises(PermissionError, match='Re-authorize'):
            asyncio.run(automation.run_now(owner, row['id'], workspace.store.get('schedule', row['id'])['revision']))
        workspace.bots.set_state(owner, bot['id'], 'paused')
        with pytest.raises(Conflict):
            automation.create(owner, **{**base, 'request_id': 'rest-request-2'})
    finally:
        asyncio.run(close(workspace, manager, ledger))


# -- review fixes ------------------------------------------------------------------------------------

def test_a_schedule_stopped_by_a_permission_change_is_parked_and_recoverable(tmp_path):
    async def run():
        workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
        try:
            automation, clock = scheduler(workspace, identity)
            bot = make(workspace, owner)
            schedule = add(workspace, owner, bot)
            grant = workspace.store.get('delegation', schedule['grant_id'])
            workspace.store.update('delegation', grant['id'], {**grant, 'policy_version': grant['policy_version'] + 1000})
            clock[0] += 4000
            await automation.tick()
            row = workspace.bots.schedules.list(owner, bot['id'])[0]
            assert not row['enabled'] and row['state'] == 'needs_attention' and row['needs_reauthorization'] is True
            assert 'permissions changed' in row['reason']
            assert [item['kind'] for item in workspace.bots.inbox(owner, bot_id=bot['id'])] == ['schedule']
            assert workspace.bots.get(owner, bot['id'])['needs_you'] == 1
            done = workspace.bots.schedules.reauthorize(owner, bot['id'])
            assert [item['id'] for item in done] == [schedule['id']] and done[0]['enabled']
            assert workspace.bots.inbox(owner, bot_id=bot['id']) == []
        finally:
            await close(workspace, manager, ledger)
    asyncio.run(run())


def test_editing_a_paused_bot_then_resuming_parks_its_schedules(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        schedule = add(workspace, owner, bot)
        workspace.bots.set_state(owner, bot['id'], 'paused')
        workspace.bots.patch(owner, bot['id'], revision=workspace.bots.get(owner, bot['id'])['revision'], instructions='Changed while paused')
        workspace.bots.set_state(owner, bot['id'], 'active')
        row = workspace.bots.schedules.list(owner, bot['id'])[0]
        assert not row['enabled'] and row['needs_reauthorization'] is True
        assert [item['kind'] for item in workspace.bots.inbox(owner, bot_id=bot['id'])] == ['schedule']
        assert [item['id'] for item in workspace.bots.schedules.reauthorize(owner, bot['id'])] == [schedule['id']]
        assert workspace.bots.schedules.list(owner, bot['id'])[0]['enabled']
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_reauthorize_does_not_restore_legacy_task_quotas(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        scheduler(workspace, identity)
        bot = make(workspace, owner)
        schedule = add(workspace, owner, bot, limits={'max_steps': 60})
        assert all(value is None for value in workspace.store.get('delegation', schedule['grant_id'])['limits'].values())
        workspace.bots.patch(owner, bot['id'], revision=bot['revision'], limits={'max_steps': 3}, instructions='Review the updated task')
        done = workspace.bots.schedules.reauthorize(owner, bot['id'])
        assert all(value is None for value in workspace.store.get('delegation', done[0]['grant_id'])['limits'].values())
    finally:
        asyncio.run(close(workspace, manager, ledger))


@pytest.mark.parametrize('legacy_limits', [None, {}, {'max_steps': None}, {'max_steps': 60, 'max_seconds': None}])
def test_reauthorize_normalizes_legacy_limits_and_keeps_grant_authority(tmp_path, legacy_limits):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        _automation, clock = scheduler(workspace, identity)
        bot = make(workspace, owner)
        schedule = add(workspace, owner, bot, max_runs=5)
        goal = workspace.store.get('goal', schedule['goal_id'])
        workspace.store.update('goal', goal['id'], {**goal, 'limits': legacy_limits, 'runs_started': 2})
        workspace.bots.patch(owner, bot['id'], revision=bot['revision'], instructions='Review the changed task')
        done = workspace.bots.schedules.reauthorize(owner, bot['id'], grant_days=7)
        assert len(done) == 1 and done[0]['enabled']
        grant = workspace.store.get('delegation', done[0]['grant_id'])
        assert all(value is None for value in grant['limits'].values())
        assert grant['max_runs'] == 3 and grant['used_runs'] == 0
        assert grant['expires_at'] == clock[0] + 7 * 86400
        assert grant['owner'] == owner.id and grant['policy_version'] == owner.policy_version
        assert workspace.store.get('delegation', schedule['grant_id'])['revoked'] is True
        assert 'computer_permission' not in grant
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_the_rest_goal_grant_schedule_path_cannot_skip_the_bot_rules(tmp_path):
    workspace, owner, manager, ledger, registry, identity = setup(tmp_path)
    try:
        automation, clock = scheduler(workspace, identity)
        bot = make(workspace, owner)

        def raw(conversation_id, index):
            goal = automation.goal(owner, conversation_id=conversation_id, success_criteria='ok', max_runs=3, limits={})
            grant = automation.grant(owner, goal_id=goal['id'], expires_at=clock[0] + 86400, max_runs=3, limits={})
            return automation.schedule(owner, goal_id=goal['id'], grant_id=grant['id'], prompt=f'p{index}', spec=SPEC, enabled=True)

        for index in range(MAX_SCHEDULES):
            raw(bot['conversation_id'], index)
        with pytest.raises(Conflict, match='at most'):
            raw(bot['conversation_id'], MAX_SCHEDULES)
        other = make(workspace, owner, name='Other')
        workspace.bots.set_state(owner, other['id'], 'paused')
        with pytest.raises(Conflict):
            raw(other['conversation_id'], 0)
    finally:
        asyncio.run(close(workspace, manager, ledger))


def test_schedule_bot_shows_the_owner_all_of_what_it_grants_and_refuses_long_text(tmp_path):
    spec = default_registry().get('schedule_bot')
    ctx = SimpleNamespace(task_id='t', manager=SimpleNamespace(orchestrator=None))
    task = 'Check the repo. ' + 'x' * 1500 + ' END-OF-TASK'
    decision = spec.decide(call('schedule_bot', bot_id='b1', task=task, spec=SPEC, success_criteria='Reports briefly CRITERIA-END'), ctx)
    assert decision.approval_required and 'END-OF-TASK' in decision.consequence and 'CRITERIA-END' in decision.consequence
    long = spec.decide(call('schedule_bot', bot_id='b1', task='y' * 5000, spec=SPEC), ctx)
    assert long.allowed is False and not long.approval_required and 'shorter' in long.consequence
    criteria = spec.decide(call('schedule_bot', bot_id='b1', task='ok', spec=SPEC, success_criteria='z' * 900), ctx)
    assert criteria.allowed is False and 'shorter' in criteria.consequence
