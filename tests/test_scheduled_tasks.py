from datetime import datetime, timezone

import pytest
from test_durable_workspace import workspace, session, automation_fixture, anyio_backend
from termx.workspace.automation import AutomationService, next_run
from termx.workspace.store import Conflict


def utc(value): return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_cron_ranges_steps_lists_sunday_and_day_union():
    assert next_run({'kind':'cron','expression':'*/15 9-17 * * 1-5','timezone':'UTC'},utc('2026-10-09T17:59'))==utc('2026-10-12T09:00')
    assert next_run({'kind':'cron','expression':'0 8,16 * * 7','timezone':'UTC'},utc('2026-10-10T20:00'))==utc('2026-10-11T08:00')
    # Restricted day-of-month and weekday use the traditional cron OR rule.
    assert next_run({'kind':'cron','expression':'0 0 15 * 1','timezone':'UTC'},utc('2026-10-11T12:00'))==utc('2026-10-12T00:00')
    assert next_run({'kind':'cron','expression':'0 0 29 2 *','timezone':'UTC'},utc('2026-01-01T00:00'))==utc('2028-02-29T00:00')


@pytest.mark.parametrize('expression',['* * * *','60 * * * *','*/0 * * * *','0 0 31 2 *','0 24 * * *','0 0 * * MON','0 0 * * 8','* * * * *;echo hi','1-0 * * * *'])
def test_invalid_cron_is_rejected(expression):
    with pytest.raises(ValueError): next_run({'kind':'cron','expression':expression,'timezone':'UTC'},utc('2026-01-01T00:00'))


def test_cron_dst_skips_gap_and_duplicate_fold():
    spec={'kind':'cron','expression':'30 2 * * *','timezone':'America/New_York'}
    assert next_run(spec,utc('2026-03-08T05:00'))==utc('2026-03-09T06:30')
    spec['expression']='30 1 * * *'
    assert next_run(spec,utc('2026-11-01T05:31'))==utc('2026-11-02T06:30')


@pytest.mark.anyio
async def test_run_now_paused_schedule_preserves_due_and_rejects_replay(workspace):
    service,owner,_=workspace
    automation,conversation,goal,grant,schedule,clock=automation_fixture(workspace,max_runs=3)
    row=automation.enable(owner,schedule['id'],False,schedule['revision'])
    run=await automation.run_now(owner,row['id'],row['revision'])
    assert run['manual'] and run['status']=='running'
    current=service.store.get('schedule',row['id'])
    assert not current['enabled'] and current['next_run']==row['next_run']
    with pytest.raises(Conflict): await automation.run_now(owner,row['id'],row['revision'])
    with pytest.raises(Conflict): await automation.run_now(owner,row['id'],current['revision'])
    assert service.store.get('delegation',grant['id'])['used_runs']==1


@pytest.mark.anyio
async def test_once_dispatches_exactly_once_and_reconciles(workspace):
    service,owner,_=workspace
    automation,_,goal,grant,schedule,clock=automation_fixture(workspace)
    row=automation.edit(owner,schedule['id'],revision=schedule['revision'],spec={'kind':'once','at':clock[0]+10})
    assert len(automation.preview(row['spec']))==1
    clock[0]+=11
    await automation.tick();await automation.tick()
    assert service.state.agent.calls==1
    current=service.store.get('schedule',row['id'])
    assert current['next_run'] is None and not current['enabled']
    run=service.store.list('schedule_run')[0]
    service.agents.update_task(run['task_id'],status='completed')
    await automation.tick()
    assert service.store.get('schedule_run',run['id'])['status']=='completed'


def test_creation_is_idempotent_and_failed_creation_cleans_up(workspace):
    service,owner,_=workspace
    chat=session(workspace)
    automation=AutomationService(service,principal_lookup=service.principal_lookup)
    payload=dict(request_id='request-123',conversation_id=chat['id'],success_criteria='Report checks',max_runs=3,limits={},prompt='Run tests',spec={'kind':'cron','expression':'0 9 * * 1-5','timezone':'UTC'},grant_days=7)
    row=automation.create(owner,**payload)
    assert automation.create(owner,**payload)['id']==row['id']
    assert len(service.store.list('delegation'))==1
    with pytest.raises(Conflict): automation.create(owner,**{**payload,'prompt':'Changed'})
    with pytest.raises(ValueError): automation.create(owner,**{**payload,'request_id':'invalid-123','prompt':' '})
    assert len(service.store.list('goal'))==1 and len(service.store.list('delegation'))==1


@pytest.mark.anyio
async def test_archive_keeps_history_and_blocks_dispatch_and_edits(workspace):
    service,owner,_=workspace
    automation,_,_,_,row,clock=automation_fixture(workspace)
    automation.archive(owner,row['id'],row['revision'])
    clock[0]+=61;await automation.tick()
    assert service.state.agent.calls==0
    current=service.store.get('schedule',row['id'])
    with pytest.raises(ValueError): await automation.run_now(owner,row['id'],current['revision'])
    with pytest.raises(ValueError): automation.enable(owner,row['id'],True,current['revision'])
    with pytest.raises(ValueError): automation.edit(owner,row['id'],revision=current['revision'],prompt='New')


@pytest.mark.anyio
async def test_queue_wait_is_not_misclassified_as_missed(workspace):
    service,owner,_=workspace
    automation,_,_,_,row,clock=automation_fixture(workspace,overlap='queue',missed='skip',max_runs=3)
    clock[0]+=61;await automation.tick()
    first=service.store.list('schedule_run')[0]
    clock[0]+=61;await automation.tick()
    assert service.store.get('schedule',row['id'])['queued']
    clock[0]+=180
    service.agents.update_task(first['task_id'],status='completed')
    await automation.tick()
    assert service.state.agent.calls==2
    assert not service.store.get('schedule',row['id'])['queued']


@pytest.mark.anyio
async def test_shell_cron_runs_without_model_and_records_output(workspace):
    import asyncio
    from termx.runbooks import RunbookRunner
    service,owner,_=workspace
    service.state.agent.runbooks=RunbookRunner(service.agents)
    chat=session(workspace)
    clock=[utc('2026-10-11T00:00')]
    automation=AutomationService(service,principal_lookup=service.principal_lookup,clock=lambda:clock[0])
    row=automation.create(owner,request_id='shell-cron-123',conversation_id=chat['id'],success_criteria='Exit zero',max_runs=2,limits={},prompt='Shell proof',command='printf scheduled-command-proof',spec={'kind':'cron','expression':'* * * * *','timezone':'UTC'},grant_days=1)
    run=await automation.run_now(owner,row['id'],row['revision'])
    for _ in range(100):
        await asyncio.sleep(.02);await automation.tick()
        current=service.store.get('schedule_run',run['id'])
        if current['status']!='running': break
    assert current['status']=='completed'
    assert current['step_results'][0]['output']=='scheduled-command-proof'
    assert service.state.agent.calls==0
    service.agents.update_runbook(row['runbook_id'],steps=[{'command':'echo changed','confirm':False}])
    row=service.store.get('schedule',row['id'])
    with pytest.raises(PermissionError,match='changed'): await automation.run_now(owner,row['id'],row['revision'])


@pytest.mark.anyio
async def test_scheduled_command_time_budget_cancels_process(workspace):
    import asyncio
    from termx.runbooks import RunbookRunner
    service,owner,_=workspace
    service.state.agent.runbooks=RunbookRunner(service.agents)
    chat=session(workspace)
    clock=[utc('2026-10-11T00:00')]
    automation=AutomationService(service,principal_lookup=service.principal_lookup,clock=lambda:clock[0])
    row=automation.create(owner,request_id='shell-timeout-123',conversation_id=chat['id'],success_criteria='Exit zero',max_runs=2,limits={'max_seconds':1},prompt='Timeout proof',command='sleep 30',spec={'kind':'interval','seconds':60},grant_days=1)
    run=await automation.run_now(owner,row['id'],row['revision'])
    await asyncio.sleep(.1)
    clock[0]+=2;await automation.tick()
    assert service.store.get('schedule_run',run['id'])['status']=='cancelled'
    assert service.agents.get_runbook_run(run['runbook_run_id'])['status']=='cancelled'


@pytest.mark.anyio
async def test_scheduled_saved_commands_preserve_confirmation_and_revoke(workspace):
    import asyncio
    from termx.runbooks import RunbookRunner
    service,owner,_=workspace
    service.state.agent.runbooks=RunbookRunner(service.agents)
    chat=session(workspace)
    book=service.agents.create_runbook(name='Confirmation',project_id='project-a',steps=[{'command':'printf approved','confirm':True}])
    automation=AutomationService(service,principal_lookup=service.principal_lookup)
    row=automation.create(owner,request_id='confirmed-123',conversation_id=chat['id'],success_criteria='Exit zero',max_runs=2,limits={},prompt='Confirmation proof',runbook_id=book['id'],spec={'kind':'interval','seconds':60},grant_days=1)
    run=await automation.run_now(owner,row['id'],row['revision'])
    await asyncio.sleep(.05);await automation.tick()
    assert service.store.get('schedule_run',run['id'])['task_status']=='awaiting_confirmation'
    assert not service.agents.get_runbook_run(run['runbook_run_id'])['step_results']
    await automation.control_run(owner,run['id'],'confirm')
    for _ in range(100):
        await asyncio.sleep(.02);await automation.tick()
        if service.store.get('schedule_run',run['id'])['status']!='running': break
    assert service.store.get('schedule_run',run['id'])['status']=='completed'
    current=service.store.get('schedule',row['id'])
    run=await automation.run_now(owner,row['id'],current['revision'])
    await asyncio.sleep(.05)
    automation.revoke(owner,row['grant_id'])
    await automation.cancel_delegation_runs(owner,row['grant_id'])
    assert service.agents.get_runbook_run(run['runbook_run_id'])['status']=='cancelled'


def test_schedule_http_global_catalog_and_mutation_revision(workspace):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from termx.workspace.http import mount_workspace
    service,owner,_=workspace
    chat=session(workspace)
    app=FastAPI();service.state.workspace=service;mount_workspace(app,service.state)
    credentials=asyncio.run(service.state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='fixture'))
    client=TestClient(app,headers={'Authorization':'Bearer '+credentials.access_token})
    payload=dict(request_id='http-create-123',conversation_id=chat['id'],success_criteria='Check passes',max_runs=2,limits={},prompt='Check project',spec={'kind':'interval','seconds':60},grant_days=7)
    response=client.post('/api/workspace/automations',json=payload)
    assert response.status_code==200,response.text
    row=response.json()
    assert client.get('/api/workspace/automations').json()['schedule'][0]['id']==row['id']
    assert client.get('/api/workspace/automations?project_id=another').json()['schedule']==[]
    assert client.patch('/api/workspace/schedules/'+row['id'],json={'revision':row['revision'],'enabled':False}).status_code==200
    assert client.post('/api/workspace/schedules/'+row['id']+'/run',json={'revision':row['revision']}).status_code==409
    assert client.post('/api/workspace/schedules/preview',json={'kind':'cron','expression':'* * * *','timezone':'UTC'}).status_code==400
    assert TestClient(app).get('/api/workspace/automations').status_code==401
    service.state.identity.set_scopes(owner.id,['agent-view'])
    denied=client.post('/api/workspace/automations',json={**payload,'request_id':'denied-create'})
    assert denied.status_code==403


@pytest.mark.anyio
async def test_scheduled_shell_step_timeout_is_persisted(workspace):
    import asyncio
    from termx.runbooks import RunbookRunner
    service,owner,_=workspace
    service.state.agent.runbooks=RunbookRunner(service.agents)
    chat=session(workspace)
    automation=AutomationService(service,principal_lookup=service.principal_lookup)
    row=automation.create(owner,request_id='step-timeout-123',conversation_id=chat['id'],success_criteria='Exit zero',max_runs=1,limits={'shell_timeout_s':1},prompt='Bounded shell',command='sleep 30',spec={'kind':'interval','seconds':60},grant_days=1)
    run=await automation.run_now(owner,row['id'],row['revision'])
    command=service.agents.get_runbook_run(run['runbook_run_id'])
    assert command['steps'][0]['_timeout_s']==1
    await asyncio.sleep(1.2);await automation.tick()
    assert service.store.get('schedule_run',run['id'])['status']=='failed'
