"""Unattended authority is checked by the real runtime before tool effects.

These tests intentionally do not call the scheduler's revocation tick after
changing authority: the manager itself must block execution and recovery.
"""
import asyncio
from time import time
import pytest
from test_agent import FakeComputer, _execution_checkpoint
from termx.app import AppState, create_app
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.tokens import SCOPES
from termx.workspace.swarms import assemble

@pytest.fixture
def anyio_backend():return 'asyncio'

class GatedAdapter:
    def __init__(self):self.plans=[];self.started=set();self.release=asyncio.Event()
    async def test(self):return 'No-network authority fixture'
    async def plan(self,prompt,cwd,manifest):
        self.plans.append(prompt);return {'summary':prompt,'steps':['Read fixture'],'tools':['read_file'],'risks':[]},'plan'
    async def turn(self,**kwargs):
        self.started.add(kwargs['prompt']);await self.release.wait()
        return ProviderTurn('authority-fixture','',[ProviderCall('function','read-fixture','read_file',{'path':'read.txt'})],{},[])

@pytest.fixture
async def runtime(tmp_path):
    adapter=GatedAdapter();state=AppState(passcode=None,credentials=CredentialStore(memory={}),adapter_factory=lambda *_:adapter);create_app(state);state.agent._computer=FakeComputer()
    owner=state.identity.setup_owner('authority-owner','authority-fixture-password-123');folder=tmp_path/'project';folder.mkdir();(folder/'read.txt').write_text('Controlled fixture only')
    project=state.projects.register(str(folder),name='Authority fixture');state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No-network fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['shell'],api_key='fixture-only')
    yield state,owner,folder,project,adapter
    adapter.release.set();await state.automation.close();await state.workspace.close();await state.agent.close();state.workspace.store.close();state.agent_store.close()

async def eventually(predicate):
    async def poll():
        while not predicate():await asyncio.sleep(.005)
    await asyncio.wait_for(poll(),3)

def delegation(runtime,principal=None):
    state,owner,folder,project,_=runtime;principal=principal or owner
    conversation=state.workspace.create_session(principal,title='Delegated inspection',project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture',mode='agent')
    goal=state.automation.goal(principal,conversation_id=conversation['id'],success_criteria='Inspect fixture',max_runs=2,limits=None)
    grant=state.automation.grant(principal,goal_id=goal['id'],expires_at=time()+3600,max_runs=2,limits=None)
    return conversation,grant

async def scheduled(runtime,principal=None):
    state,owner,_,_,adapter=runtime;principal=principal or owner;conversation,grant=delegation(runtime,principal);now=time()
    state.automation.schedule(principal,goal_id=grant['goal_id'],grant_id=grant['id'],prompt='Scheduled root',spec={'kind':'once','at':now+1},missed='once',enabled=True)
    state.automation.clock=lambda:now+2;await state.automation.tick()
    await eventually(lambda:bool(state.workspace.store.list('schedule_run')) and bool(state.workspace.store.list('schedule_run')[0].get('task_id')))
    root=state.workspace.store.list('schedule_run')[0]['task_id'];await eventually(lambda:bool(adapter.started) or state.agent_store.get_task(root)['status'] in {'cancelled','failed'});assert adapter.started, state.agent_store.get_task(root)
    return root,grant,conversation

def revoke(state,grant):
    row=state.workspace.store.get('delegation',grant['id']);state.workspace.store.update('delegation',grant['id'],{**row,'revoked':True})

def assert_blocked(state,identifier):
    events=state.agent_store.events(identifier);assert state.agent_store.get_task(identifier)['status']=='cancelled';assert any(event['type']=='task.authority.revoked' for event in events);assert not any(event['type'] in {'tool.started','tool.finished'} for event in events)

@pytest.mark.anyio
@pytest.mark.parametrize('reason',['revoke','expired','disabled','project_policy'])
async def test_scheduled_root_rechecks_authority_before_tools_without_scheduler_tick(runtime,reason):
    state,owner,_,project,adapter=runtime;root,grant,_=await scheduled(runtime)
    if reason=='revoke':revoke(state,grant)
    elif reason=='expired':
        row=state.workspace.store.get('delegation',grant['id']);state.workspace.store.update('delegation',grant['id'],{**row,'expires_at':time()-1})
    elif reason=='disabled':state.identity.disable(owner.id)
    else:state.authorization.grant_project(owner.id,project['id'],[])
    adapter.release.set();await eventually(lambda:state.agent_store.get_task(root)['status'] in {'cancelled','failed'});assert_blocked(state,root)

@pytest.mark.anyio
@pytest.mark.parametrize('legacy_metadata',[False,True])
async def test_child_retains_delegated_grant_and_legacy_child_resolves_parent(runtime,legacy_metadata):
    state,owner,_,_,adapter=runtime;root,grant,_=await scheduled(runtime)
    result=await assemble(state.workspace,owner,root,request_id='authority-child-team',members=[{'agent':'Inspector','task':'Child inspection','mode':'ask','instructions':''}]);assert result['ok'];child=result['members'][0]['task_id'];await eventually(lambda:'Child inspection' in adapter.started)
    metadata=state.workspace.store.get('task',child);assert metadata['delegation_id']==grant['id']
    if legacy_metadata:state.workspace.store.update('task',child,{**metadata,'delegation_id':None})
    revoke(state,grant);adapter.release.set();await eventually(lambda:state.agent_store.get_task(child)['status'] in {'cancelled','failed'});assert_blocked(state,child)

@pytest.mark.anyio
async def test_prepared_recovery_cannot_repeat_tool_after_delegation_revocation(runtime):
    state,_,folder,_,adapter=runtime;root,grant,_=await scheduled(runtime)
    state.agent.cancel(root);await eventually(lambda:state.agent_store.get_task(root)['status']=='cancelled');await asyncio.sleep(.01)
    checkpoint=_execution_checkpoint(state.agent_store,root,str(folder),state='prepared');state.agent_store.update_task(root,status='recovering');revoke(state,grant)
    await state.agent.recover_task(root);await eventually(lambda:state.agent_store.get_task(root)['status'] in {'cancelled','failed'});assert_blocked(state,root);assert not (folder/'recovered.txt').exists();assert state.agent_store.get_checkpoint(checkpoint['id'])['side_effect_state']=='prepared'

@pytest.mark.anyio
async def test_authority_revoked_during_dispatch_blocks_provider_planning(runtime,monkeypatch):
    state,owner,_,_,adapter=runtime;conversation,grant=delegation(runtime);original=state.workspace._dispatch_created
    def created(*args,**kwargs):
        original(*args,**kwargs);revoke(state,grant)
    monkeypatch.setattr(state.workspace,'_dispatch_created',created)
    task=await state.workspace.send(owner,conversation['id'],prompt='Must not plan',request_id='authority-before-plan',delegation_id=grant['id']);assert_blocked(state,task['id']);assert adapter.plans==[];assert not adapter.started

@pytest.mark.anyio
async def test_project_grant_removal_cancels_trusted_operator_before_tool(runtime):
    state,_,_,project,adapter=runtime;operator=state.identity.create_local_user('authority-operator','operator-fixture-password-123',list(SCOPES));state.authorization.set_role(operator.id,'operator',trusted_execution=True);state.authorization.grant_project(operator.id,project['id'],['agent-view','agent-control','agent-run','files-read','files-write']);operator=state.identity.principal_by_id(operator.id)
    root,_,_=await scheduled(runtime,operator);state.authorization.revoke_project(operator.id,project['id']);adapter.release.set();await eventually(lambda:state.agent_store.get_task(root)['status'] in {'cancelled','failed'});assert_blocked(state,root)
@pytest.mark.anyio
async def test_revocation_after_tool_admission_is_rechecked_at_invoke_boundary(runtime):
    state,_,_,_,adapter=runtime;root,grant,_=await scheduled(runtime)
    def revoke_on_admission(task_id,event):
        if event['task_id']==root and event['type']=='tool.started':revoke(state,grant)
    state.agent._listeners.add(revoke_on_admission);adapter.release.set();await eventually(lambda:state.agent_store.get_task(root)['status'] in {'cancelled','failed'})
    events=state.agent_store.events(root);assert state.agent_store.get_task(root)['status']=='cancelled';assert any(event['type']=='tool.started' for event in events);assert any(event['type']=='task.authority.revoked' for event in events);assert not any(event['type']=='tool.finished' for event in events)

@pytest.mark.anyio
async def test_prepared_recovery_with_valid_delegation_executes_the_saved_write(runtime):
    state,owner,folder,_,adapter=runtime;conversation,grant=delegation(runtime)
    task=await state.workspace.send(owner,conversation['id'],prompt='Recover valid work',request_id='authority-valid-recovery',delegation_id=grant['id']);root=task['id'];assert task['status']=='awaiting_approval'
    _execution_checkpoint(state.agent_store,root,str(folder),state='prepared');state.agent_store.update_task(root,status='recovering');await state.agent.recover_task(root)
    await eventually(lambda:(folder/'recovered.txt').exists());assert (folder/'recovered.txt').read_text()=='v1';assert not any(event['type']=='task.authority.revoked' for event in state.agent_store.events(root))

@pytest.mark.anyio
async def test_approved_plan_cannot_start_provider_turn_after_grant_revocation(runtime):
    state,owner,_,_,adapter=runtime;conversation,grant=delegation(runtime)
    task=await state.workspace.send(owner,conversation['id'],prompt='Do not start provider',request_id='authority-before-provider',delegation_id=grant['id']);revoke(state,grant)
    await state.agent.resolve_approval(task['id'],task['approvals'][0]['id'],'approved');await eventually(lambda:state.agent_store.get_task(task['id'])['status'] in {'cancelled','failed'});assert_blocked(state,task['id']);assert not adapter.started
