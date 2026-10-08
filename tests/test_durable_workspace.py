from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from time import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from termx.agent.execution import ShellResult
from termx.agent.limits import resolve_limits
from termx.agent.store import AgentStore
from termx.identity import AuthenticationService, Principal
from termx.workspace.automation import AutomationService, next_run
from termx.workspace.extensions import ExtensionService
from termx.workspace.http import mount_workspace
from termx.workspace.service import WorkspaceService
from termx.workspace.store import Conflict, WorkspaceStore


class Agent:
    def __init__(self,store):
        self._listeners=set();self.store=store;self.calls=0;self.cancelled=[]
    async def create_task(self,**kwargs):
        self.calls+=1
        task=self.store.create_task(prompt=kwargs['prompt'],cwd=kwargs['cwd'],provider_id='fixture',
                                    model='fixture',limits=kwargs['limits'],mode=kwargs['mode'])
        kwargs.get('on_created',lambda tid:None)(task['id'])
        await asyncio.sleep(0)
        return task
    def cancel(self,tid):
        self.cancelled.append(tid)
        return self.store.update_task(tid,status='cancelled')
    def steer(self,tid,message):
        return {'id':tid,'message':message}


class Engines:
    def __init__(self,store):
        self.store=store
    def engines(self):
        return ['fixture-acp']
    async def create_task(self,**kwargs):
        return self.store.create_task(prompt=kwargs['prompt'],cwd=kwargs['cwd'],provider_id='fixture',
                            model=kwargs.get('model') or 'fixture',limits=kwargs['limits'],mode=kwargs['mode'],engine=kwargs['engine'])
    async def cancel(self,tid):
        return self.store.update_task(tid,status='cancelled')


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def workspace(tmp_path):
    identity=AuthenticationService(tmp_path/'identity.sqlite3')
    owner=identity.setup_owner('owner','strong fixture password')
    ledger=AgentStore(tmp_path/'agent.sqlite3',tmp_path/'artifacts')
    state=SimpleNamespace(identity=identity,agent_store=ledger,agent=Agent(ledger),engines=Engines(ledger))
    service=WorkspaceService(state,tmp_path/'workspace.sqlite3',project_check=lambda *args:None)
    service.principal_lookup=identity.principal_by_id
    yield service,owner,tmp_path
    service.store.close();ledger.close()


def session(workspace,engine='internal'):
    service,owner,path=workspace
    return service.create_session(owner,title='Build useful tools',project_id='project-a',cwd=str(path),engine=engine,provider_id='fixture',model='fixture')


def test_persisted_grouped_sessions_drafts_scopes_and_optimistic_revision(workspace):
    service,owner,path=workspace
    first=session(workspace)
    service.update_session(owner,first['id'],revision=1,changes={'pinned':True,'draft_text':'unfinished prompt','scroll':125})
    store=WorkspaceStore(path/'workspace.sqlite3')
    assert store.get('conversation',first['id'])['draft_text']=='unfinished prompt'
    store.close()
    assert service.sessions(owner,query='TOOLS')[0]['id']==first['id']
    with pytest.raises(Conflict):
        service.update_session(owner,first['id'],revision=1,changes={'title':'lost update'})
    stranger=Principal('someone','Someone',owner.scopes,1)
    with pytest.raises(KeyError):
        service.session(stranger,first['id'])
    assert service.sessions(owner,archived=True)==[]


def test_live_scope_reduction_disables_memory_and_chat(workspace):
    service,owner,path=workspace
    row=session(workspace)
    service.state.identity.set_scopes(owner.id,['machine-view'])
    with pytest.raises(PermissionError):
        service.session(owner,row['id'])
    with pytest.raises(PermissionError):
        service.save_memory(owner,content='context',provenance='user')


@pytest.mark.anyio
async def test_duplicate_prompt_returns_same_task_and_context_is_session_local(workspace):
    service,owner,path=workspace
    first=session(workspace);second=session(workspace)
    a,b=await asyncio.gather(service.send(owner,first['id'],prompt='hello',request_id='idempotent-123'),
                             service.send(owner,first['id'],prompt='hello',request_id='idempotent-123'))
    assert a['id']==b['id'] and service.state.agent.calls==1
    assert len(service.session(owner,first['id'])['turns'])==1
    with pytest.raises(Conflict):
        await service.send(owner,first['id'],prompt='different',request_id='idempotent-123')
    with pytest.raises(Conflict):
        service.update_session(owner,first['id'],revision=2,changes={'model':'other'})
    assert service.session(owner,second['id'])['model']=='fixture'
    with pytest.raises(Conflict,match='linked fork'):
        service.update_session(owner,second['id'],revision=1,changes={'engine':'fixture-acp'})


@pytest.mark.anyio
async def test_two_engines_keep_independent_active_run_configuration(workspace):
    service, owner, path = workspace
    first = session(workspace)
    second = service.create_session(owner, title='Native chat', project_id='project-a',
                                    cwd=str(path), engine='fixture-acp', model='native-model')
    calls = []
    original_agent = service.state.agent.create_task
    original_native = service.state.engines.create_task

    async def internal(**kwargs):
        calls.append(('internal', dict(kwargs)))
        return await original_agent(**kwargs)

    async def native(**kwargs):
        calls.append(('native', dict(kwargs)))
        return await original_native(**kwargs)

    service.state.agent.create_task = internal
    service.state.engines.create_task = native
    a, b = await asyncio.gather(
        service.send(owner, first['id'], prompt='Internal work', request_id='engine-internal'),
        service.send(owner, second['id'], prompt='Native work', request_id='engine-native'))
    assert a['id'] != b['id']
    assert a['engine'] == 'internal' and b['engine'] == 'fixture-acp'
    dispatched = dict(calls)
    assert dispatched['internal']['provider_id'] == 'fixture'
    assert dispatched['internal']['model'] == 'fixture'
    assert dispatched['native']['engine'] == 'fixture-acp'
    assert dispatched['native']['model'] == 'native-model'
    assert dispatched['internal']['conversation_id'] == first['id']
    assert dispatched['native']['conversation_id'] == second['id']

    # Changing another chat's configuration cannot retarget either live task.
    idle = session(workspace)
    service.update_session(owner, idle['id'], revision=idle['revision'],
                           changes={'provider_id': 'different-account', 'model': 'new-model'})
    for row in (first, second):
        current = service.session(owner, row['id'])
        with pytest.raises(Conflict, match='active turn'):
            service.update_session(owner, row['id'], revision=current['revision'],
                                   changes={'model': 'retargeted-model'})
    assert service.agents.get_task(a['id'])['model'] == 'fixture'
    assert service.agents.get_task(b['id'])['model'] == 'native-model'
    assert service.session(owner, first['id'])['provider_id'] == 'fixture'
    assert service.session(owner, second['id'])['engine'] == 'fixture-acp'
    assert len(service.session(owner, first['id'])['turns']) == 1
    assert len(service.session(owner, second['id'])['turns']) == 1


@pytest.mark.anyio
async def test_reviewed_cross_engine_fork_transfers_public_context_only_once(workspace):
    service,owner,path=workspace
    original=session(workspace)
    task=await service.send(owner,original['id'],prompt='make a plan',request_id='request-original')
    service.agents.update_task(task['id'],status='completed',result='A useful answer')
    turn=service.session(owner,original['id'])['turns'][0]
    (path/'source.py').write_text('answer = 42\n')
    preview=service.fork_preview(owner,original['id'],engine='fixture-acp',turn_ids=[turn['id']],files=['source.py'],summary='Goal context')
    assert [item['role'] for item in preview['transfer']['messages']]==['user','assistant']
    fork=service.commit_fork(owner,preview['id'],digest=preview['digest'],model='other')
    assert fork['linked_from']==original['id'] and fork['engine']=='fixture-acp'
    assert service.session(owner,original['id'])['engine']=='internal'
    assert service.session(owner,original['id'])['turns'][0]['task_id']==task['id']
    assert service.agents.engine_session_for_conversation(fork['id']) is None
    child=await service.send(owner,fork['id'],prompt='continue',request_id='request-child')
    assert 'A useful answer' in child['prompt'] and 'answer = 42' in child['prompt']
    with pytest.raises(Conflict):
        service.commit_fork(owner,preview['id'],digest=preview['digest'])


def test_cross_engine_context_disallows_other_turns_secrets_paths_and_changed_preview(workspace):
    service,owner,path=workspace
    original=session(workspace)
    with pytest.raises(ValueError):
        service.fork_preview(owner,original['id'],engine='fixture-acp',turn_ids=['foreign'])
    (path/'.env').write_text('token=secret')
    with pytest.raises(ValueError):
        service.fork_preview(owner,original['id'],engine='fixture-acp',turn_ids=[],files=['.env'])
    preview=service.fork_preview(owner,original['id'],engine='fixture-acp',turn_ids=[])
    service.update_session(owner,original['id'],revision=1,changes={'title':'Changed'})
    with pytest.raises(Conflict):
        service.commit_fork(owner,preview['id'],digest=preview['digest'])


def test_memory_project_exclusions_retention_export_edits_and_deletion(workspace):
    service,owner,path=workspace
    global_memory=service.save_memory(owner,content='Prefer concise explanations',provenance='user settings')
    project=service.save_memory(owner,content='Use pytest',provenance='user reviewed AGENTS.md',project_id='project-a')
    service.save_memory(owner,content='Excluded until reviewed',provenance='generated summary',project_id='project-a',excluded=True)
    service.save_memory(owner,content='Other project',provenance='project b',project_id='project-b')
    assert {m['id'] for m in service.memories(owner,project_id='project-a',include_excluded=False)}=={global_memory['id'],project['id']}
    edited=service.save_memory(owner,content='Use pytest -q',provenance='user edit',project_id='project-a',identifier=project['id'],revision=1)
    assert edited['revision']==2
    with pytest.raises(ValueError):
        service.save_memory(owner,content='Move scopes',provenance='user',project_id='project-b',identifier=project['id'],revision=2)
    service.store.update('memory',global_memory['id'],{**global_memory,'expires_at':time()-1})
    assert [m['id'] for m in service.memories(owner,project_id='project-a',include_excluded=False)]==[project['id']]
    service.delete_memory(owner,project['id'])
    assert service.memories(owner,project_id='project-a',include_excluded=False)==[]
    assert {log['event'] for log in service.store.logs(owner.id)} >= {'saved','deleted','retention_expired'}


@pytest.mark.anyio
async def test_hooks_scope_failure_timeout_logs_and_no_cross_project_execution(workspace,monkeypatch):
    service,owner,path=workspace
    calls=[]
    async def fake(command,cwd,**kwargs):
        calls.append((command,cwd,kwargs))
        return ShellResult(command,cwd,'token=secretvalue123',None,timed_out=True)
    monkeypatch.setattr('termx.workspace.service.run_shell',fake)
    service.save_hook(owner,project_id='project-a',event='before_turn',argv=['python','check.py'],cwd=str(path),timeout_s=2,enabled=True)
    service.save_hook(owner,project_id='project-b',event='before_turn',argv=['python','other.py'],cwd=str(path),enabled=True)
    with pytest.raises(Conflict):
        await service.run_hooks(owner,'project-a','before_turn',str(path))
    assert len(calls)==1 and calls[0][2]['timeout_s']==2 and calls[0][2]['network']=='none'
    run=service.store.list('hook_run',owner.id)[0]
    assert run['timed_out'] and 'secretvalue123' not in run['output']
    assert 'command' not in run
    with pytest.raises(ValueError):
        service.save_hook(owner,project_id='project-a',event='unknown',argv=['true'],cwd=str(path))


def test_schedule_timezone_dst_and_interval_validation():
    before=datetime(2026,3,7,3,0,tzinfo=ZoneInfo('America/New_York')).timestamp()
    stamp=next_run({'kind':'daily','timezone':'America/New_York','time':'02:30'},before)
    assert datetime.fromtimestamp(stamp,ZoneInfo('America/New_York')).day==9  # DST gap skipped.
    before=datetime(2026,11,1,1,31,tzinfo=ZoneInfo('America/New_York'),fold=0).timestamp()
    stamp=next_run({'kind':'daily','timezone':'America/New_York','time':'01:30'},before)
    assert datetime.fromtimestamp(stamp,ZoneInfo('America/New_York')).day==2  # Fold runs once.
    assert next_run({'kind':'interval','seconds':60},100)==160
    for spec in ({'kind':'interval','seconds':True},{'kind':'daily','timezone':'bad','time':'22:00'},{'kind':'daily','timezone':'UTC','time':'25:00'}):
        with pytest.raises(ValueError):
            next_run(spec,100)


def automation_fixture(workspace,*,missed='once',overlap='skip',max_runs=2):
    service,owner,path=workspace
    clock=[time()]
    automations=AutomationService(service,principal_lookup=service.state.identity.principal_by_id,clock=lambda:clock[0])
    conversation=session(workspace)
    limits=resolve_limits({'max_steps':3,'max_seconds':20})
    goal=automations.goal(owner,conversation_id=conversation['id'],success_criteria='Tests pass',max_runs=max_runs,limits=limits)
    grant=automations.grant(owner,goal_id=goal['id'],expires_at=clock[0]+5000,max_runs=max_runs,limits=limits)
    schedule=automations.schedule(owner,goal_id=goal['id'],grant_id=grant['id'],prompt='Inspect the project',
                                 spec={'kind':'interval','seconds':60},missed=missed,overlap=overlap,enabled=True)
    return automations,conversation,goal,grant,schedule,clock


@pytest.mark.anyio
async def test_schedule_closed_ui_restart_idempotency_budgets_and_approval_wait(workspace):
    service,owner,path=workspace
    automation,conversation,goal,grant,schedule,clock=automation_fixture(workspace,max_runs=1)
    clock[0]+=61
    # New scheduler instance mirrors daemon restart, UI is absent.
    automation=AutomationService(service,principal_lookup=service.state.identity.principal_by_id,clock=lambda:clock[0])
    await asyncio.gather(automation.tick(),automation.tick())
    runs=service.store.list('schedule_run',owner.id)
    assert len(runs)==1 and runs[0]['status']=='running'
    tid=runs[0]['task_id']
    assert service.agents.get_task(tid)['limits']['max_steps']==3
    service.agents.update_task(tid,status='awaiting_approval')
    clock[0]+=61;await automation.tick()
    assert service.agents.get_task(tid)['status']=='awaiting_approval'
    assert service.store.get('schedule',schedule['id'])['state']=='needs_attention' # budget exhausted, never consent.
    assert service.state.agent.calls==1


@pytest.mark.anyio
async def test_schedule_revoked_grant_live_authority_and_missed_policy(workspace):
    service,owner,path=workspace
    automation,conversation,goal,grant,schedule,clock=automation_fixture(workspace,missed='skip')
    clock[0]+=180;await automation.tick()
    assert service.store.list('schedule_run',owner.id)[0]['status']=='skipped'
    assert service.state.agent.calls==0
    service.state.identity.set_scopes(owner.id,['agent-view','agent-run','agent-control'])
    clock[0]+=61;await automation.tick()
    row=service.store.get('schedule',schedule['id'])
    assert row['state']=='needs_attention' and 'Authority changed' in row['reason']
    assert service.state.agent.calls==0


@pytest.mark.anyio
async def test_schedule_overlap_queue_and_cancelled_goal_stop_tree(workspace):
    service,owner,path=workspace
    automation,conversation,goal,grant,schedule,clock=automation_fixture(workspace,overlap='queue',max_runs=3)
    clock[0]+=61;await automation.tick()
    run=service.store.list('schedule_run',owner.id)[0]
    child=service.agents.create_task(prompt='child',cwd=str(path),provider_id='fixture',model='fixture',limits=resolve_limits(),parent_id=run['task_id'])
    clock[0]+=61;await automation.tick()
    assert service.state.agent.calls==1
    assert service.store.get('schedule',schedule['id'])['next_run']<clock[0]
    await automation.cancel_goal(owner,goal['id'])
    assert set(service.state.agent.cancelled)=={run['task_id'],child['id']}
    assert not service.store.get('schedule',schedule['id'])['enabled']


def bundle(version='1',permissions=None):
    return {'format':'termx-bundle/v1','id':'fixture','version':version,'permissions':permissions or ['files-read'],
            'files':{'skills/fixture/SKILL.md':'# Fixture\nUse the approved project context.'}}


def test_extensions_review_private_registry_update_diff_rollback_and_secret_free_export(workspace):
    service,owner,path=workspace
    extensions=ExtensionService(service)
    registry_dir=path/'private-registry';registry_dir.mkdir()
    (registry_dir/'fixture.json').write_text(json.dumps(bundle()))
    registry=extensions.registry(owner,name='Private',path=str(registry_dir))
    assert extensions.registry_packages(owner,registry['id'])[0]['id']=='fixture'
    preview=extensions.registry_preview(owner,registry['id'],'fixture.json')
    initial=extensions.install(owner,preview['id'],preview['digest'])
    assert Path(initial['path'],'skills/fixture/SKILL.md').is_file()
    assert extensions.export(owner,'fixture')==bundle()
    update=extensions.preview(owner,bundle('2',['files-read','files-write']))
    assert update['permission_diff']=={'added':['files-write'],'removed':[]}
    extensions.install(owner,update['id'],update['digest'])
    rollback=extensions.rollback_preview(owner,'fixture',initial['active_digest'])
    assert rollback['permission_diff']['removed']==['files-write']
    result=extensions.install(owner,rollback['id'],rollback['digest'])
    assert result['version']=='1'
    with pytest.raises(Conflict):
        extensions.install(owner,rollback['id'],rollback['digest'])
    extensions.remove(owner,'fixture')
    assert extensions.active_roots()==[]


@pytest.mark.parametrize('mutation',[
    lambda b:b.update(permissions=['host-admin']),
    lambda b:b.update(files={'../escape':'bad'}),
    lambda b:b.update(files={'skills/.env':'bad'}),
    lambda b:b.update(files={'skills/fixture/SKILL.md':'api_key=secretlongvalue123'}),
    lambda b:b.update(id='../escape'),
    lambda b:b.update(format='unknown'),
])
def test_extension_rejects_unsafe_bundle(workspace,mutation):
    service,owner,path=workspace
    extensions=ExtensionService(service)
    data=bundle();mutation(data)
    with pytest.raises(ValueError):
        extensions.preview(owner,data)


@pytest.mark.anyio
async def test_extension_custom_adapter_and_in_use_removal_safe(workspace):
    service,owner,path=workspace
    extensions=ExtensionService(service)
    extensions.register_adapter('custom-fixture/v1',lambda data:bundle(data['release']))
    preview=extensions.preview(owner,{'release':'1'},'custom-fixture/v1')
    extensions.install(owner,preview['id'],preview['digest'])
    conversation=session(workspace)
    task=await service.send(owner,conversation['id'],prompt='hello',request_id='lease-fixture')
    extensions.acquire(owner,'fixture',task['id'])
    with pytest.raises(Conflict):
        extensions.remove(owner,'fixture')
    updated=extensions.preview(owner,bundle('2'))
    with pytest.raises(Conflict):
        extensions.install(owner,updated['id'],updated['digest'])
    service.agents.update_task(task['id'],status='completed')
    extensions.remove(owner,'fixture')
    assert extensions.store.get('extension_version','fixture:'+preview['digest']) is not None
    with pytest.raises(ValueError,match='Unsupported'):
        extensions.preview(owner,{},'unrecognized')


def test_workspace_http_managed_session_contract_and_validation(workspace):
    service,owner,path=workspace
    app=FastAPI();service.state.workspace=service
    mount_workspace(app,service.state)
    credentials=asyncio.run(service.state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='fixture'))
    client=TestClient(app,headers={'Authorization':'Bearer '+credentials.access_token})
    create=client.post('/api/workspace/sessions',json={'title':'HTTP fixture','cwd':str(path),'project_id':'project-a'})
    assert create.status_code==200,create.text
    identifier=create.json()['id']
    assert client.get('/api/workspace/sessions/'+identifier).status_code==200
    assert client.patch('/api/workspace/sessions/'+identifier,json={'revision':1,'changes':{'title':'Renamed'}}).status_code==200
    assert client.patch('/api/workspace/sessions/'+identifier,json={'revision':1,'changes':{'title':'Stale'}}).status_code==409
    assert client.post('/api/workspace/memory',json={'content':'HTTP context','provenance':'user','surprise':True}).status_code==422
    assert client.post('/api/workspace/memory',json={'content':'HTTP context','provenance':'user'}).status_code==200
    assert client.get('/api/workspace/memory/export').json()['format']=='termx-memory/v1'
    assert TestClient(app).get('/api/workspace/sessions').status_code==401
    service.state.identity.disable(owner.id)
    assert client.get('/api/workspace/sessions').status_code==401


@pytest.mark.anyio
async def test_shared_schedule_budget_is_claimed_atomically_across_schedules(workspace):
    service,owner,path=workspace
    automation,conversation,goal,grant,first,clock=automation_fixture(workspace,max_runs=1)
    second=automation.schedule(owner,goal_id=goal['id'],grant_id=grant['id'],prompt='second',
                              spec={'kind':'interval','seconds':60},missed='once',enabled=True)
    clock[0]+=61
    # Two scheduler instances get separate snapshots of the same due slot.
    other=AutomationService(service,principal_lookup=service.state.identity.principal_by_id,clock=lambda:clock[0])
    await asyncio.gather(automation.tick(),other.tick())
    assert service.state.agent.calls==1
    assert service.store.get('delegation',grant['id'])['used_runs']==1
    assert service.store.get('goal',goal['id'])['runs_started']==1


@pytest.mark.anyio
async def test_uncertain_native_dispatch_is_never_replayed(workspace,monkeypatch):
    service,owner,path=workspace
    conversation=session(workspace,engine='fixture-acp')
    count=0
    async def fail(**kwargs):
        nonlocal count
        count+=1
        raise ValueError('engine connection lost after send')
    monkeypatch.setattr(service.state.engines,'create_task',fail)
    with pytest.raises(ValueError):
        await service.send(owner,conversation['id'],prompt='external action',request_id='uncertain-native')
    with pytest.raises(Conflict,match='reconciliation'):
        await service.send(owner,conversation['id'],prompt='external action',request_id='uncertain-native')
    assert count==1


def test_excluded_memory_remains_inspectable_but_is_not_in_model_context(workspace):
    service,owner,path=workspace
    memory=service.save_memory(owner,content='Review before sharing',provenance='user',excluded=True)
    assert service.memories(owner)[0]['id']==memory['id']
    assert service.memories(owner,include_excluded=False)==[]

@pytest.mark.anyio
async def test_selected_extension_runs_real_context_and_holds_version_lease(workspace):
    service,owner,path=workspace
    extensions=ExtensionService(service,path/'installed')
    service.state.extensions=extensions
    preview=extensions.preview(owner,{'format':'termx-bundle/v1','id':'useful','version':'1','permissions':['files-read'],
        'files':{'skills/useful/SKILL.md':'Always explain the acceptance criteria.'}})
    extensions.install(owner,preview['id'],preview['digest'])
    row=session(workspace)
    service.update_session(owner,row['id'],revision=1,changes={'extension_ids':['useful']})
    task=await service.send(owner,row['id'],prompt='Implement this',request_id='selected-skill-task')
    assert 'Always explain the acceptance criteria.' in task['prompt']
    assert service.session(owner,row['id'])['turns'][0]['prompt']=='Implement this'
    assert extensions._in_use('useful')[0]['task_id']==task['id']
    with pytest.raises(Conflict):extensions.remove(owner,'useful')
    with pytest.raises(Conflict):service.update_session(owner,row['id'],revision=3,changes={'extension_ids':[]})
    service.agents.update_task(task['id'],status='completed')
    extensions.remove(owner,'useful')

@pytest.mark.anyio
async def test_browser_workflow_is_dedicated_immutable_and_forwarded(workspace):
    service,owner,path=workspace
    service.state.engines.engines=lambda:['claude']
    with pytest.raises(ValueError):service.create_session(owner,project_id='project-a',cwd=str(path),workflow='browser')
    row=service.create_session(owner,project_id='project-a',cwd=str(path),engine='claude',workflow='browser')
    captured={}
    original=service.state.engines.create_task
    async def native(**kwargs):
        captured.update(kwargs)
        return await original(**kwargs)
    service.state.engines.create_task=native
    task=await service.send(owner,row['id'],prompt='Review this page',request_id='browser-only-task')
    assert captured['workflow']=='browser'
    service.agents.update_task(task['id'],status='completed')
    with pytest.raises(Conflict):service.update_session(owner,row['id'],revision=2,changes={'workflow':None})
    assert service.session(owner,row['id'])['workflow']=='browser'

@pytest.mark.anyio
async def test_extension_version_is_held_before_native_session_creation(workspace):
    service,owner,path=workspace
    service.state.extensions=ExtensionService(service,path/'installed')
    extension=service.state.extensions
    preview=extension.preview(owner,{'format':'termx-bundle/v1','id':'race-safe','version':'1','permissions':[],
        'files':{'instructions/README.md':'Reviewed instructions'}})
    extension.install(owner,preview['id'],preview['digest'])
    row=session(workspace,engine='fixture-acp')
    service.update_session(owner,row['id'],revision=1,changes={'extension_ids':['race-safe']})
    entered=asyncio.Event();resume=asyncio.Event();original=service.state.engines.create_task
    async def native(**kwargs):
        entered.set();await resume.wait();return await original(**kwargs)
    service.state.engines.create_task=native
    dispatch=asyncio.create_task(service.send(owner,row['id'],prompt='Go',request_id='extension-race-native'))
    await entered.wait()
    try:
        with pytest.raises(Conflict):extension.remove(owner,'race-safe')
    finally:resume.set()
    task=await dispatch
    assert extension._in_use('race-safe')[0]['task_id']==task['id']

@pytest.mark.anyio
async def test_task_authority_uses_exact_originating_managed_session_before_worker(workspace):
    from termx.browser.storage import Records
    service,owner,path=workspace
    credentials=await service.state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='fixture')
    other=await service.state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='other window')
    service.state.browser=SimpleNamespace(records=Records(path/'browser'))
    row=session(workspace)
    original=service.state.agent.create_task
    async def started(**kwargs):
        task=service.agents.create_task(prompt=kwargs['prompt'],cwd=kwargs['cwd'],provider_id='fixture',model='fixture',limits=kwargs['limits'])
        kwargs['on_created'](task['id'])
        authority=service.state.browser.records.get('agent-task-authority',task['id'])
        assert authority['session_id']==credentials.session_id!=other.session_id
        assert authority['principal_id']==owner.id and authority['project_id']=='project-a'
        return task
    service.state.agent.create_task=started
    await service.send(owner,row['id'],prompt='Inspect page',request_id='origin-session-task',managed_session_id=credentials.session_id)

def test_conversation_group_moves_preserve_execution_location(workspace):
    service,owner,path=workspace
    row=session(workspace)
    moved=service.update_session(owner,row['id'],revision=1,changes={'group_id':'project-b','title':'Renamed'})
    assert moved['group_id']=='project-b' and moved['project_id']=='project-a'
    assert moved['cwd']==row['cwd'] and moved['title']=='Renamed'

@pytest.mark.anyio
async def test_trusted_operator_scratch_is_owned_generated_and_revocable(workspace):
    from fastapi import HTTPException
    from termx.authorization import AuthorizationService,ROLES
    service,owner,path=workspace
    authz=AuthorizationService(service.state.identity)
    service.state.authorization=authz
    user=service.state.identity.create_local_user('operator','strong operator password',list(ROLES['operator']))
    authz.set_role(user.id,'operator',trusted_execution=True)
    user=service.state.identity.principal_by_id(user.id)
    row=service.create_session(user,title='Scratch chat')
    assert Path(row['cwd']).is_relative_to(service.scratch_root(user.id)) and row['project_id'] is None
    task=await service.send(user,row['id'],prompt='Explain this',request_id='scratch-operator-task')
    assert task['cwd']==row['cwd']
    service.agents.update_task(task['id'],status='completed')
    with pytest.raises(HTTPException):
        service.create_session(user,title='Arbitrary host folder',cwd=str(path))
    authz.set_role(user.id,'operator',trusted_execution=False)
    with pytest.raises(HTTPException):
        await service.send(user,row['id'],prompt='Run again',request_id='scratch-revoked-task')

@pytest.mark.anyio
async def test_explicit_retry_preserves_original_prompt_and_idempotency(workspace):
    service,owner,path=workspace
    row=session(workspace)
    first=await service.send(owner,row['id'],prompt='Original instruction',request_id='retry-first-prompt')
    service.agents.update_task(first['id'],status='failed',error='Fixture failure')
    retried=await service.retry(owner,first['id'],request_id='retry-explicit-new')
    assert retried['id']!=first['id'] and 'Original instruction' in retried['prompt']
    again=await service.retry(owner,first['id'],request_id='retry-explicit-new')
    assert again['id']==retried['id'] and len(service.session(owner,row['id'])['turns'])==2

@pytest.mark.anyio
async def test_cloud_target_requires_explicit_account_and_never_runs_local_agent(workspace):
    service,owner,path=workspace
    row=session(workspace)
    runner={'id':'qualified','owner':owner.id,'project':'project-a','status':'ready','expires':time()+60,'configuration':{'network':'none'}}
    service.state.runners=SimpleNamespace(row=lambda owner_id,identifier:runner)
    class Cloud:
        def __init__(self):self.calls=[];self.cancelled=[]
        async def preflight(self,*args):self.calls.append(('preflight',args))
        async def create_task(self,**kwargs):
            self.calls.append(('task',kwargs));task=service.agents.create_task(prompt=kwargs['prompt'],cwd='/workspace',provider_id=kwargs['provider_id'],model=kwargs['model'],mode=kwargs['mode'],limits=kwargs['limits'],engine='runner')
            kwargs['on_created'](task['id']);return task
        def owns(self,task):return task['engine']=='runner'
        def cancel(self,identifier):self.cancelled.append(identifier);service.agents.update_task(identifier,status='cancelled')
    cloud=Cloud();service.state.runner_agents=cloud
    with pytest.raises(ValueError):service.update_session(owner,row['id'],revision=1,changes={'runner_id':'qualified'})
    updated=service.update_session(owner,row['id'],revision=1,changes={'runner_id':'qualified','runner_credential_ref':'fixture'})
    task=await service.send(owner,row['id'],prompt='Build remotely',request_id='remote-only-task')
    assert task['engine']=='runner' and task['cwd']=='/workspace' and service.state.agent.calls==0
    assert service.store.get('task',task['id'])['execution_location']=='runner:qualified/workspace'
    await AutomationService(service,principal_lookup=service.principal_lookup).cancel_tree(owner,task['id'])
    assert cloud.cancelled==[task['id']] and cloud.calls[1][1]['credential_ref']=='fixture'
    runner['configuration']['network']='bridge'
    with pytest.raises(PermissionError):service.update_session(owner,row['id'],revision=updated['revision']+1,changes={'model':'another'})

@pytest.mark.anyio
async def test_schedule_grant_refuses_changed_execution_target(workspace):
    service,owner,path=workspace
    row=session(workspace);clock=[time()]
    automation=AutomationService(service,principal_lookup=service.principal_lookup,clock=lambda:clock[0])
    goal=automation.goal(owner,conversation_id=row['id'],success_criteria='Verified output',max_runs=2,limits=None)
    grant=automation.grant(owner,goal_id=goal['id'],expires_at=clock[0]+3600,max_runs=2,limits=None)
    schedule=automation.schedule(owner,goal_id=goal['id'],grant_id=grant['id'],prompt='Execute bounded task',spec={'kind':'interval','seconds':60},enabled=True)
    service.update_session(owner,row['id'],revision=1,changes={'model':'different-selected-model'})
    clock[0]+=61;await automation.tick()
    current=service.store.get('schedule',schedule['id'])
    assert not current['enabled'] and 'execution target changed' in current['reason'] and service.state.agent.calls==0

@pytest.mark.anyio
async def test_saved_session_budgets_apply_to_dispatch_and_invalid_limits_do_not_persist(workspace):
    service,owner,path=workspace;row=session(workspace)
    settings=service.update_session(owner,row['id'],revision=1,changes={'run_limits':{'max_steps':3,'max_parallel_subagents':1}})
    assert settings['run_limits']['max_steps']==3
    with pytest.raises(ValueError):service.update_session(owner,row['id'],revision=2,changes={'run_limits':{'max_parallel_subagents':99}})
    task=await service.send(owner,row['id'],prompt='Bounded run',request_id='session-saved-budget')
    assert task['limits']['max_steps']==3 and task['limits']['max_parallel_subagents']==1
    with pytest.raises(Conflict):service.update_session(owner,row['id'],revision=3,changes={'run_limits':{'max_steps':4}})

@pytest.mark.anyio
async def test_retry_reloads_private_image_artifact_without_exposing_path_and_verifies_integrity(workspace):
    import base64
    service,owner,path=workspace;row=session(workspace)
    first=await service.send(owner,row['id'],prompt='Original image instruction',request_id='first-image-task')
    image=b'fixture normalized image bytes';artifact=service.agents.save_artifact(first['id'],'upload','image/png',image)
    assert 'path' not in service.agents.artifacts(first['id'])[0]
    service.agents.update_task(first['id'],status='failed')
    captured={};create=service.state.agent.create_task
    async def dispatch(**kwargs):captured.update(kwargs);return await create(**kwargs)
    service.state.agent.create_task=dispatch
    retried=await service.retry(owner,first['id'],request_id='retry-image-new-task')
    assert captured['attachments'][0]['data']==base64.b64encode(image).decode()
    service.agents.update_task(retried['id'],status='completed')
    private=service.agents.get_artifact(first['id'],artifact['id']);Path(private['path']).write_bytes(b'changed artifact')
    with pytest.raises(Conflict):await service.retry(owner,first['id'],request_id='retry-image-tampered')

@pytest.mark.anyio
async def test_enrolled_worktree_owns_session_files_dispatch_and_schedule_target(workspace):
    import subprocess,threading
    from termx.development.delivery import DeliveryService
    service,owner,path=workspace
    root=path/'project';root.mkdir()
    def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True).stdout
    git('init','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (root/'main.txt').write_text('Main checkout\n');git('add','.');git('commit','-m','Fixture')
    project={'id':'project-a','name':'Fixture','path':str(root)}
    service.state.projects=SimpleNamespace(project=lambda identifier:project if identifier=='project-a' else (_ for _ in ()).throw(KeyError(identifier)),projects=lambda:[project],lock=threading.RLock())
    from termx.authorization import AuthorizationService
    service.state.authorization=AuthorizationService(service.state.identity)
    owner=service.state.identity.create_principal('Trusted operator',list(owner.scopes))
    service.state.authorization.set_role(owner.id,'operator',trusted_execution=True)
    grant_scopes=['agent-view','agent-control','agent-run','files-read','files-write','terminal-control']
    service.state.authorization.grant_project(owner.id,'project-a',grant_scopes)
    delivery=DeliveryService(path/'delivery');service.state.delivery=delivery
    result=delivery.effect('project-a',str(root),'worktree-create',{'branch':'codex/isolated','base':'main'})
    row=service.create_session(owner,project_id='project-a',worktree_id=result['id'],provider_id='fixture',model='fixture')
    assert row['cwd']==str(Path(result['path']).resolve()) and row['worktree_digest']
    with pytest.raises(ValueError,match='local worktree'):service.update_session(owner,row['id'],revision=1,changes={'runner_id':'remote','runner_credential_ref':'fixture'})
    files,pid=service.session_files(owner,row['id'],expected_worktree=result['id'])
    current=files.read(pid,'main.txt');files.save(pid,'main.txt','Isolated edits\n',current['revision'])
    service.state.authorization.grant_project(owner.id,'project-a',[scope for scope in grant_scopes if scope!='files-write'])
    with pytest.raises(__import__('fastapi').HTTPException):service.session_files(owner,row['id'],'files-write',expected_worktree=result['id'])
    service.state.authorization.grant_project(owner.id,'project-a',grant_scopes)
    assert (root/'main.txt').read_text()=='Main checkout\n'
    assert (Path(result['path'])/'main.txt').read_text()=='Isolated edits\n'
    (Path(result['path'])/'outside').symlink_to(root/'main.txt')
    with pytest.raises(__import__('fastapi').HTTPException):files.read(pid,'outside')
    with pytest.raises(Conflict):service.session_files(owner,row['id'],expected_worktree=None)
    with pytest.raises((PermissionError,KeyError,__import__('fastapi').HTTPException)):service.create_session(owner,project_id='other-project',worktree_id=result['id'])
    with pytest.raises(ValueError):service.create_session(owner,project_id='project-a',worktree_id=result['id'],cwd=str(root))
    automation=AutomationService(service,principal_lookup=service.principal_lookup)
    goal=automation.goal(owner,conversation_id=row['id'],success_criteria='Isolated result',max_runs=2,limits=None)
    grant=automation.grant(owner,goal_id=goal['id'],expires_at=time()+600,max_runs=2,limits=None)
    assert grant['execution_target']['worktree_id']==result['id'] and grant['execution_target']['worktree_digest']==row['worktree_digest']
    task=await service.send(owner,row['id'],prompt='Work in the isolated branch',request_id='isolated-worktree-run')
    assert task['cwd']==row['cwd'] and service.store.get('task',task['id'])['worktree_id']==result['id']
    with pytest.raises(Conflict):service.update_session(owner,row['id'],revision=service.store.get('conversation',row['id'])['revision'],changes={'worktree_id':None})
    service.agents.update_task(task['id'],status='completed')
    updated=service.update_session(owner,row['id'],revision=service.store.get('conversation',row['id'])['revision'],changes={'worktree_id':None})
    assert updated['cwd']==str(root) and not updated['worktree_id']
    with pytest.raises(PermissionError):service.validate_delegation(owner,updated,grant['id'])
    selected=service.update_session(owner,row['id'],revision=updated['revision'],changes={'worktree_id':result['id']})
    # A removed checkout cannot remain an execution target through stale enrollment.
    import shutil
    shutil.rmtree(result['path']);git('worktree','prune')
    with pytest.raises(OSError):service.worktree_target('project-a',result['id'])
    unavailable=service.session(owner,row['id'],turns=False)
    assert unavailable['worktree_unavailable']
    with pytest.raises(OSError):await service.send(owner,row['id'],prompt='Removed checkout must refuse',request_id='removed-worktree-run')
    recovered=service.update_session(owner,row['id'],revision=selected['revision'],changes={'worktree_id':None})
    assert recovered['cwd']==str(root)


def test_bulk_session_projection_binds_canonical_target_and_revalidates_live_grant(workspace,monkeypatch):
    from fastapi import HTTPException
    from termx.authorization import AuthorizationService
    service,owner,path=workspace
    project={'id':'project-a','path':str(path),'name':'Fixture'}
    service.state.projects=SimpleNamespace(project=lambda identifier:project,projects=lambda:[project])
    authz=AuthorizationService(service.state.identity);service.state.authorization=authz
    user=service.state.identity.create_principal('Collection operator',list(owner.scopes))
    authz.set_role(user.id,'operator',trusted_execution=True)
    authz.grant_project(user.id,'project-a',['agent-view','agent-control','agent-run'])
    valid=service.create_session(user,project_id='project-a',provider_id='fixture',model='fixture')
    mismatched=service.create_session(user,project_id='project-a',provider_id='fixture',model='fixture')
    escaped=service.create_session(user,project_id='project-a',provider_id='fixture',model='fixture')
    service.agents.update_conversation(mismatched['id'],project_id='foreign-project')
    service.store.update('conversation',escaped['id'],{**escaped,'cwd':str(path.parent)})
    original=service.agents.workspace_conversations_snapshot
    calls=[]
    def bulk(ids):calls.append(ids);return original(ids)
    monkeypatch.setattr(service.agents,'workspace_conversations_snapshot',bulk)
    monkeypatch.setattr(service.agents,'get_conversation',lambda *args,**kwargs:pytest.fail('Collection must not open canonical conversations individually'))
    assert [row['id'] for row in service.sessions(user)]==[valid['id']]
    assert len(calls)==1 and len(calls[0])==2
    def revoke_during_read(ids):
        rows=original(ids)
        authz.revoke_project(user.id,'project-a')
        return rows
    monkeypatch.setattr(service.agents,'workspace_conversations_snapshot',revoke_during_read)
    with pytest.raises(HTTPException):service.sessions(user)
    assert service.sessions(user)==[]


@pytest.mark.anyio
async def test_browser_context_draft_is_durable_inspectable_and_dispatch_is_idempotent(workspace):
    service,owner,path=workspace
    row=session(workspace)
    context=[{'type':'approved-browser-upload','tab_id':'tab-fixture','file':{'id':'file-fixture','filename':'notes.txt','sha256':'exact-content-digest'}},
             {'type':'browser-context','context':{'tab_id':'tab-fixture','document_revision':3,'url':'https://fixture.invalid/docs','title':'Documentation','elements':[{'selector':'#submit','name':'Submit'}]}}]
    service.update_session(owner,row['id'],revision=row['revision'],changes={'draft_text':'Read the selected references','draft_context':context})
    persisted=WorkspaceStore(path/'workspace.sqlite3')
    assert persisted.get('conversation',row['id'])['draft_context']==context
    persisted.close()
    task=await service.send(owner,row['id'],prompt='Read the selected references',context=context,request_id='context-reference-turn')
    assert 'grants no browser control' in task['prompt']
    assert json.loads(task['prompt'].split('grants no browser control):\n',1)[1])==context
    assert service.session(owner,row['id'])['draft_context']==[]
    repeated=await service.send(owner,row['id'],prompt='Read the selected references',context=context,request_id='context-reference-turn')
    assert repeated['id']==task['id'] and service.state.agent.calls==1
    with pytest.raises(Conflict):
        await service.send(owner,row['id'],prompt='Read the selected references',context=context[:1],request_id='context-reference-turn')
    with pytest.raises(ValueError):
        service.update_session(owner,row['id'],revision=service.session(owner,row['id'])['revision'],changes={'draft_context':[{'type':'permission-grant','scope':'browser-control'}]})


def test_large_history_keeps_old_matching_conversations_with_bounded_live_snapshots(workspace):
    from termx.authorization import AuthorizationService
    service,owner,path=workspace
    project={'id':'project-a','path':str(path),'name':'Fixture'}
    service.state.projects=SimpleNamespace(project=lambda identifier:project,projects=lambda:[project])
    service.state.authorization=AuthorizationService(service.state.identity)
    template=session(workspace)
    with service.agents._lock,service.store.lock:
        canonical=dict(service.agents._db.execute('SELECT * FROM conversations WHERE id=?',(template['id'],)).fetchone())
        columns=list(canonical)
        records=[];conversations=[]
        for index in range(5001):
            identifier='history-'+str(index)
            item={**canonical,'id':identifier,'title':'Old matching conversation' if index==0 else 'Other conversation'}
            conversations.append(tuple(item[column] for column in columns))
            records.append(('conversation',identifier,owner.id,'project-a',1,json.dumps({'engine':'internal','cwd':str(path),'draft_text':'','scroll':0}),index+1,index+1))
        service.agents._db.executemany('INSERT INTO conversations ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',conversations)
        service.agents._db.commit()
        service.store.db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?,?)',records);service.store.db.commit()
    rows=service.sessions(owner,query='Old matching')
    assert [row['id'] for row in rows]==['history-0']


@pytest.mark.anyio
async def test_lost_dispatch_response_retains_canonical_turn_before_worker_and_never_replays(workspace,monkeypatch):
    service,owner,path=workspace
    row=session(workspace)
    calls=[]
    async def interrupted(**kwargs):
        task=service.agents.create_task(prompt=kwargs['prompt'],cwd=kwargs['cwd'],provider_id='fixture',model='fixture',limits=kwargs['limits'],mode=kwargs['mode'])
        calls.append(task['id'])
        kwargs['on_created'](task['id'])
        assert service.agents.workspace_turns_page(row['id'])[0]['task_id']==task['id']
        raise asyncio.CancelledError()
    monkeypatch.setattr(service.state.agent,'create_task',interrupted)
    with pytest.raises(asyncio.CancelledError):await service.send(owner,row['id'],prompt='Original user intent',request_id='lost-before-response')
    resumed=await service.send(owner,row['id'],prompt='Original user intent',request_id='lost-before-response')
    assert resumed['id']==calls[0] and len(calls)==1
    service.agents.add_conversation_turn(row['id'],prompt='Original user intent',task_id=resumed['id'])
    turns=service.agents.workspace_turns_page(row['id'])
    assert len(turns)==1 and turns[0]['prompt']=='Original user intent'


@pytest.mark.anyio
async def test_restart_heals_dispatched_receipt_without_canonical_turn_or_new_worker(workspace):
    service,owner,path=workspace
    row=session(workspace)
    task=await service.send(owner,row['id'],prompt='Digest-bound original request',request_id='restart-between-commits')
    with service.agents._lock:
        service.agents._db.execute('DELETE FROM conversation_turns WHERE conversation_id=?',(row['id'],));service.agents._db.commit()
    # New service against the same persisted receipts mirrors the host restart.
    reopened=WorkspaceService(service.state,path/'workspace.sqlite3',project_check=lambda *args:None)
    try:
        healed=await reopened.send(owner,row['id'],prompt='Digest-bound original request',request_id='restart-between-commits')
        assert healed['id']==task['id'] and service.state.agent.calls==1
        turn=service.agents.workspace_turns_page(row['id'])[0]
        assert turn['prompt']=='Digest-bound original request' and turn['mode']==task['mode'] and turn['model']==task['model']
        with pytest.raises(Conflict):await reopened.send(owner,row['id'],prompt='Changed intent',request_id='restart-between-commits')
        assert len(service.agents.workspace_turns_page(row['id']))==1
    finally:reopened.store.close()


@pytest.mark.anyio
async def test_dispatch_does_not_erase_a_new_draft_written_while_waiting_for_response(workspace,monkeypatch):
    service,owner,path=workspace
    row=session(workspace)
    original=service.state.agent.create_task
    async def write_followup(**kwargs):
        task=await original(**kwargs)
        current=service.session(owner,row['id'],turns=False)
        service.update_session(owner,row['id'],revision=current['revision'],changes={'draft_text':'Next unsent instruction'})
        return task
    monkeypatch.setattr(service.state.agent,'create_task',write_followup)
    await service.send(owner,row['id'],prompt='Original submitted instruction',request_id='preserve-new-draft')
    assert service.session(owner,row['id'],turns=False)['draft_text']=='Next unsent instruction'
