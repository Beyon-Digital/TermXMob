"""Unattended grants start plans, preserve tool consent, and remain revocable."""
import asyncio
from time import time

import pytest
from fastapi import HTTPException

from test_agent import FakeAdapter, _FanOutAdapter, _fn, build_manager
from test_durable_workspace import automation_fixture, workspace
from termx.workspace.automation import AutomationService


async def eventually(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(poll(), 10)


async def dispatched_run(service, run):
    # tick deliberately returns while provider planning is still in progress.
    # Store rows are snapshots, so refresh the durable row until it has a task.
    await eventually(lambda: bool(service.store.get('schedule_run', run['id']).get('task_id')))
    return service.store.get('schedule_run', run['id'])


def add_schedule(service, owner, automation, path, prompt, clock):
    conversation = service.create_session(owner, project_id='project-a', cwd=str(path), engine='internal',
        provider_id='fake', model='fake-model', mode='agent')
    goal = automation.goal(owner, conversation_id=conversation['id'], success_criteria='Complete inspection', max_runs=2, limits=None)
    grant = automation.grant(owner, goal_id=goal['id'], expires_at=clock[0]+3600, max_runs=2, limits=None)
    schedule = automation.schedule(owner, goal_id=goal['id'], grant_id=grant['id'], prompt=prompt,
        spec={'kind':'once', 'at':clock[0]+1}, missed='once', enabled=True)
    return grant, schedule


@pytest.mark.anyio
async def test_unattended_plan_starts_but_consequential_tool_waits(workspace, tmp_path):
    service, owner, path = workspace
    runtime = tmp_path/'real-runtime'
    runtime.mkdir()
    manager, ledger = build_manager(runtime, _FanOutAdapter({'Publish': ([[_fn('publish', 'run_shell', command='git push origin main')]], 0)}))
    service.state.agent._listeners.discard(service._on_event)
    service.state.agent = manager
    service.state.agent_store = service.agents = ledger
    manager._listeners.add(service._on_event)
    clock = [time()]
    automation = AutomationService(service, principal_lookup=service.principal_lookup, clock=lambda:clock[0])
    try:
        grant, schedule = add_schedule(service, owner, automation, path, 'Publish', clock)
        clock[0] += 2
        await automation.tick()
        run = await dispatched_run(service, service.store.list('schedule_run')[0])
        await eventually(lambda: bool(ledger.approvals(run['task_id'])) and any(
            a['kind']=='tool' and a['status']=='pending' for a in ledger.approvals(run['task_id'])))
        approvals = ledger.approvals(run['task_id'])
        assert next(a for a in approvals if a['kind']=='plan')['status'] == 'approved'
        assert ledger.get_task(run['task_id'])['status'] == 'awaiting_approval'
        assert any(e['event']=='delegation.plan_approved' for e in service.store.logs(owner.id))
        automation.revoke(owner, grant['id'])
        await automation.tick()
        assert ledger.get_task(run['task_id'])['status']=='cancelled'
        assert service.store.get('schedule_run',run['id'])['status']=='cancelled'
    finally:
        await automation.close()
        await manager.close()
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize('reason', ['expire', 'disable', 'policy'])
async def test_active_scheduled_execution_stops_when_authority_changes(workspace, reason):
    service, owner, _ = workspace
    automation, _, _, grant, _, clock = automation_fixture(workspace)
    clock[0] += 61
    await automation.tick()
    run = service.store.list('schedule_run')[0]
    if reason == 'expire':
        clock[0] = grant['expires_at']+1
    elif reason == 'disable':
        service.state.identity.disable(owner.id)
    else:
        service.state.identity.set_scopes(owner.id, ['agent-view'])
    await automation.tick()
    assert service.agents.get_task(run['task_id'])['status']=='cancelled'
    assert service.store.get('schedule_run',run['id'])['status']=='cancelled'


@pytest.mark.anyio
async def test_slow_planning_does_not_block_other_schedules_or_remote_stop(workspace, tmp_path):
    service, owner, path = workspace
    planning = asyncio.Event()

    class SlowAdapter(_FanOutAdapter):
        async def plan(self, prompt, cwd, manifest):
            if 'Slow' in prompt:
                planning.set()
                await asyncio.Event().wait()
            return await super().plan(prompt, cwd, manifest)

    runtime=tmp_path/'slow-runtime'
    runtime.mkdir()
    manager, ledger = build_manager(runtime, SlowAdapter({'Fast': ([],0)}))
    service.state.agent._listeners.discard(service._on_event)
    service.state.agent=manager
    service.state.agent_store=service.agents=ledger
    manager._listeners.add(service._on_event)
    clock=[time()]
    automation=AutomationService(service, principal_lookup=service.principal_lookup, clock=lambda:clock[0])
    try:
        _, slow=add_schedule(service, owner, automation, path, 'Slow', clock)
        _, fast=add_schedule(service, owner, automation, path, 'Fast', clock)
        clock[0]+=2
        await asyncio.wait_for(automation.tick(), 1)
        await asyncio.wait_for(planning.wait(), 10)
        runs={r['schedule_id']:r for r in service.store.list('schedule_run')}
        fast_run=await dispatched_run(service, runs[fast['id']])
        await eventually(lambda: ledger.get_task(fast_run['task_id'])['status']=='completed')
        slow_run=runs[slow['id']]
        await asyncio.wait_for(automation.control_run(owner,slow_run['id'],'cancel'),2)
        assert service.store.get('schedule_run',slow_run['id'])['status']=='cancelled'
        task_id=automation._dispatched_task_id(slow_run)
        assert task_id and ledger.get_task(task_id)['status']=='cancelled'
    finally:
        await automation.close()
        await manager.close()
        ledger.close()


@pytest.mark.anyio
async def test_restart_reconciles_only_durably_known_dispatch_without_replay(workspace):
    service, _, _ = workspace
    automation, _, _, _, _, clock = automation_fixture(workspace)
    clock[0] += 61
    await automation.tick()
    run = service.store.list('schedule_run')[0]
    service.store.update('schedule_run', run['id'], {**run, 'status':'dispatching', 'task_id':None})
    restarted = AutomationService(service, principal_lookup=service.principal_lookup, clock=lambda:clock[0])
    recovered = service.store.get('schedule_run', run['id'])
    assert recovered['status']=='running' and recovered['task_id']==run['task_id']
    await restarted.tick()
    assert service.state.agent.calls == 1
    assert len(service.store.list('schedule_run')) == 1


@pytest.mark.anyio
async def test_manual_run_is_stoppable_during_before_turn_hook(workspace, monkeypatch):
    service, owner, _ = workspace
    automation, _, _, _, schedule, _ = automation_fixture(workspace)
    entered = asyncio.Event()

    async def blocked_hook(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, 'run_hooks', blocked_hook)
    run = await asyncio.wait_for(automation.run_now(owner, schedule['id'], schedule['revision']), 1)
    assert run['status']=='dispatching' and entered.is_set()
    assert run['id'] in automation._dispatch_workers
    await asyncio.wait_for(automation.control_run(owner, run['id'], 'cancel'), 1)
    assert service.state.agent.calls == 0
    assert service.store.get('schedule_run', run['id'])['status']=='cancelled'
    assert run['id'] not in automation._dispatch_workers
    await automation.close()


@pytest.mark.anyio
async def test_project_authority_http_denial_cancels_instead_of_stalling_tick(workspace, monkeypatch):
    service, _, _ = workspace
    automation, _, _, _, _, clock = automation_fixture(workspace)
    clock[0] += 61
    await automation.tick()
    run = service.store.list('schedule_run')[0]

    def denied(*args, **kwargs):
        raise HTTPException(403, 'Project execution grant revoked')

    monkeypatch.setattr(service, 'session', denied)
    await automation.tick()
    assert service.agents.get_task(run['task_id'])['status']=='cancelled'
    assert service.store.get('schedule_run', run['id'])['status']=='cancelled'


@pytest.mark.anyio
async def test_restart_finishes_initial_plan_approval_without_replaying_task(workspace, tmp_path, monkeypatch):
    service, owner, path = workspace
    runtime = tmp_path/'plan-runtime'
    runtime.mkdir()
    adapter = _FanOutAdapter({'Inspect': ([], 0)})
    manager, ledger = build_manager(runtime, adapter)
    service.state.agent._listeners.discard(service._on_event)
    service.state.agent = manager
    service.state.agent_store = service.agents = ledger
    manager._listeners.add(service._on_event)
    clock = [time()]
    automation = AutomationService(service, principal_lookup=service.principal_lookup, clock=lambda:clock[0])
    try:
        _, _ = add_schedule(service, owner, automation, path, 'Inspect', clock)

        async def interrupted_before_approval(*args):
            pass

        monkeypatch.setattr(automation, '_approve_initial_plan', interrupted_before_approval)
        clock[0] += 2
        await automation.tick()
        run = await dispatched_run(service, service.store.list('schedule_run')[0])
        task_id = run['task_id']
        assert ledger.get_task(task_id)['status']=='awaiting_approval'
        # Rebuild the actual host manager too: pending plan is a safe durable
        # boundary and must survive its startup recovery classification.
        await manager.close()
        manager._listeners.discard(service._on_event)
        manager = type(manager)(ledger, manager.credentials, desktop=None,
            adapter_factory=lambda _provider, _key: adapter, runner_for=manager._runner_for)
        manager.execution_authority = service.validate_task_execution
        service.state.agent = manager
        manager._listeners.add(service._on_event)
        assert ledger.get_task(task_id)['status']=='awaiting_approval'
        # A fresh scheduler replays no dispatch: it finishes only the durable
        # initial-plan decision under the still-current execution grant.
        restarted = AutomationService(service, principal_lookup=service.principal_lookup, clock=lambda:clock[0])
        await restarted.tick()
        await eventually(lambda: ledger.get_task(task_id)['status']=='completed')
        assert len(ledger.list_tasks()) == 1
        assert next(a for a in ledger.approvals(task_id) if a['kind']=='plan')['status']=='approved'
        await restarted.close()
    finally:
        await automation.close()
        await manager.close()
        ledger.close()
