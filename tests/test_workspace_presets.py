"""Preset selection crosses durable ownership, dispatch and immutable tool policy."""
import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from test_durable_workspace import workspace
from termx.workspace.presets import resolve_preset,available_presets
from termx.workspace.store import Conflict
from termx.authorization import AuthorizationService
from termx.agent.manager import AgentManager
from termx.agent.secrets import CredentialStore
from termx.agent.providers import ProviderCall,ProviderTurn
from termx.agent.tools.helpers import sandbox_profile


def preset(service,name='Scoped helper',**kwargs):
    return service.agents.create_custom_agent(name=name,instructions='Only explain the selected source.',provider_id='fixture',model='fixture',tools=['read_file'],**kwargs)


def test_preset_defaults_persist_and_dispatch_without_retargeting_other_session(workspace):
    service,owner,path=workspace;profile=preset(service)
    first=service.create_session(owner,project_id='project-a',cwd=str(path),custom_agent_id=profile['id'])
    second=service.create_session(owner,project_id='project-a',cwd=str(path),provider_id='other',model='other-model')
    assert first['provider_id']=='fixture' and first['model']=='fixture' and first['custom_agent_id']==profile['id']
    capture=[];create=service.state.agent.create_task
    async def dispatch(**kwargs):capture.append(kwargs);return await create(**kwargs)
    service.state.agent.create_task=dispatch
    async def run():
        task=await service.send(owner,first['id'],prompt='Read the source',request_id='preset-request-1')
        assert capture[0]['custom_agent_id']==profile['id']
        assert capture[0]['custom_agent_snapshot']['instructions']==profile['instructions']
        with pytest.raises(Conflict):service.update_session(owner,first['id'],revision=first['revision'],changes={'custom_agent_id':None})
        service.state.agent.cancel(task['id'])
    asyncio.run(run())
    untouched=service.session(owner,second['id'])
    assert untouched['custom_agent_id'] is None and untouched['provider_id']=='other' and untouched['model']=='other-model'
    changed=service.update_session(owner,first['id'],revision=service.session(owner,first['id'])['revision'],changes={'custom_agent_id':None})
    assert changed['custom_agent_id'] is None and changed['custom_agent_revision'] is None
    assert service.store.get('conversation',first['id'])['custom_agent_id'] is None


def test_changed_preset_requires_explicit_renewal_and_unavailable_never_dispatches(workspace):
    service,owner,path=workspace;profile=preset(service)
    row=service.create_session(owner,project_id='project-a',cwd=str(path),custom_agent_id=profile['id'])
    service.agents.update_custom_agent(profile['id'],instructions='New reviewed instructions')
    async def run():
        with pytest.raises(Conflict,match='changed'):await service.send(owner,row['id'],prompt='Explain',request_id='changed-preset-1')
    asyncio.run(run());assert service.state.agent.calls==0
    refreshed=service.update_session(owner,row['id'],revision=row['revision'],changes={'custom_agent_id':profile['id']})
    assert refreshed['custom_agent_revision']!=row['custom_agent_revision']
    service.agents.delete_custom_agent(profile['id'])
    with pytest.raises(ValueError,match='unavailable'):service.create_session(owner,cwd=str(path),custom_agent_id=profile['id'])
    asyncio.run(run_missing(service,owner,row))

async def run_missing(service,owner,row):
    with pytest.raises(ValueError,match='unavailable'):await service.send(owner,row['id'],prompt='Explain',request_id='missing-preset-1')
    assert service.state.agent.calls==0


def test_preset_provenance_and_qualified_container_capabilities(workspace):
    service,owner,path=workspace;profile=preset(service)
    authz=AuthorizationService(service.state.identity)
    service.state.authorization=authz
    foreign=service.state.identity.setup_owner if False else SimpleNamespace(id='foreign',policy_version=1)
    # Simulate a real persisted foreign claim; even host admin cannot select it.
    with service.state.identity._db() as db:db.execute('INSERT INTO resource_owners VALUES(?,?,?,?)',('custom_agent',profile['id'],foreign.id,None))
    with pytest.raises(PermissionError,match='another principal'):resolve_preset(service,owner,profile['id'])
    assert available_presets(service,owner)==[]
    with service.state.identity._db() as db:db.execute('DELETE FROM resource_owners WHERE kind=?',('custom_agent',))
    allowed=resolve_preset(service,owner,profile['id'],runner_id='qualified')
    assert allowed['tools']==['read_file']
    service.agents.update_custom_agent(profile['id'],tools=['computer_action'])
    with pytest.raises(ValueError,match='qualified runner'):resolve_preset(service,owner,profile['id'],runner_id='qualified')
    with service.agents._lock:
        service.agents._db.execute('UPDATE custom_agents SET enabled=0 WHERE id=?',(profile['id'],));service.agents._db.commit()
    with pytest.raises(ValueError,match='disabled'):resolve_preset(service,owner,profile['id'])


def test_real_agent_freezes_instructions_tool_allowlist_and_sandbox_before_run(workspace):
    service,owner,path=workspace;profile=preset(service,approval_mode='standard',sandbox_profile='agent')
    service.agents.put_provider('fixture',kind='openai-compatible',name='Fixture',base_url='https://fixture.invalid',model='fixture',capabilities=['functions'],secret_configured=True)
    prompts=[];block=asyncio.Event()
    class Adapter:
        async def turn(self,**kwargs):prompts.append(kwargs['prompt']);await block.wait();return ProviderTurn('response','answer',[],{})
    manager=AgentManager(service.agents,CredentialStore(memory={'fixture':'test-only'}),SimpleNamespace(),adapter_factory=lambda *_:Adapter())
    service.state.agent=manager
    async def run():
        row=service.create_session(owner,project_id='project-a',cwd=str(path),custom_agent_id=profile['id'])
        task=await service.send(owner,row['id'],prompt='Explain the source',request_id='frozen-local-preset')
        original=service.agents.task_agent(task)
        service.agents.update_custom_agent(profile['id'],instructions='Mutated library',sandbox_profile='host',approval_mode='autonomous',tools=['run_shell'])
        assert sandbox_profile(SimpleNamespace(task=task,store=service.agents))=='agent'
        assert service.agents.task_agent(task)==original
        await asyncio.sleep(.05)
        assert prompts and 'Only explain the selected source.' in prompts[0] and 'Mutated library' not in prompts[0]
        ctx=SimpleNamespace(task=task,cancel=asyncio.Event())
        with pytest.raises(PermissionError,match='outside'):await manager._invoke_tool(SimpleNamespace(call=ProviderCall(type='function',call_id='blocked',name='run_shell',arguments={}),spec=object()),ctx)
        block.set();await asyncio.sleep(.1);await manager.close()
    asyncio.run(run())


def test_native_preset_engine_capability_and_conversation_binding(workspace):
    service,owner,path=workspace;profile=preset(service)
    with service.agents._lock:
        service.agents._db.execute('UPDATE custom_agents SET engine=? WHERE id=?',('fixture-acp',profile['id']));service.agents._db.commit()
    with pytest.raises(ValueError,match='different engine'):service.create_session(owner,cwd=str(path),custom_agent_id=profile['id'])
    row=service.create_session(owner,cwd=str(path),engine='fixture-acp',custom_agent_id=profile['id'])
    observed=[];dispatch=service.state.engines.create_task
    async def create(**kwargs):observed.append(kwargs);return await dispatch(**kwargs)
    service.state.engines.create_task=create
    async def run():
        task=await service.send(owner,row['id'],prompt='Explain this project',request_id='native-preset-task')
        service.agents.update_task(task['id'],status='completed')
        assert observed[0]['custom_agent']['id']==profile['id'] and observed[0]['custom_agent']['engine']=='fixture-acp'
        with pytest.raises(Conflict,match='keep their original preset'):service.update_session(owner,row['id'],revision=service.session(owner,row['id'])['revision'],changes={'custom_agent_id':None})
    asyncio.run(run())


def test_runner_admission_freezes_owned_preset_and_rejects_changed_definition(tmp_path):
    from test_runner_agents import fixture_state
    service,state,principal,_=fixture_state(tmp_path)
    from termx.workspace.service import WorkspaceService
    state.identity=SimpleNamespace(principal_by_id=lambda _:principal)
    state.engines=SimpleNamespace(engines=lambda:[])
    state.agent_store=state.agent.store
    state.workspace=WorkspaceService(state,tmp_path/'workspace',project_check=lambda *args:None)
    profile=state.agent.store.create_custom_agent(name='Cloud guide',instructions='Cloud scoped instructions',tools=['read_file'])
    from termx.workspace.presets import resolve_preset
    # Fixture authz has no owner ledger, so wire the equivalent shared library gate.
    state.authorization.resource_owner=lambda *args:None
    state.authorization.require_creation_principal=lambda *args:None
    captured=[]
    async def drive(tid,request,runner):captured.append(request)
    service._drive=drive
    async def run():
        binding=resolve_preset(state.workspace,principal,profile['id'],project_id='project',runner_id='runner')
        task=await service.create_task(principal,'runner','api','Explain remote source',provider_id='api',request_id='runner-preset-1',custom_agent=binding)
        await asyncio.gather(*service.workers.values())
        state.agent.store.update_custom_agent(profile['id'],instructions='Changed after admission',tools=['write_file'])
        frozen=state.agent.store.task_agent(task)
        assert frozen['instructions']=='Cloud scoped instructions' and frozen['tools']==['read_file']
        assert captured[0]['custom_agent']==frozen
        with pytest.raises(ValueError,match='frozen'):await service._review(service._job(task['id']),'forbidden',{'call':{'type':'function','name':'write_file','arguments':{}},'state':[]})
        with pytest.raises(HTTPException,match='changed'):await service.create_task(principal,'runner','api','Changed',provider_id='api',request_id='runner-preset-2',custom_agent=binding)
        await service.close();await state.agent.close();state.workspace.store.close();state.agent.store.close()
    asyncio.run(run())


def test_actual_stdio_worker_applies_frozen_instructions_and_refuses_extra_tools(tmp_path):
    import os,sys,json
    from termx.runners.worker import frame
    root=tmp_path/'worker';root.mkdir();target=root/'untouched.txt';target.write_text('original')
    async def run():
        env={**os.environ,'TERMX_RUNNER_TEST_ROOT':str(root),'TERMX_CONFIG_DIR':str(tmp_path/'config')}
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','termx.runners.worker',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,limit=8*1024*1024+2)
        process.stdin.write(frame({'type':'start','protocol':1,'provider':{'id':'api','model':'fixture'},'prompt':'Explain source','mode':'ask','limits':{'max_steps':3,'max_seconds':10},'custom_agent':{'id':'preset','name':'Cloud guide','instructions':'Frozen cloud instruction','tools':['read_file'],'limits':{},'workspace_revision':'snapshot'}}))
        turns=0;finished=None
        try:
            async with asyncio.timeout(15):
                while raw:=await process.stdout.readline():
                    value=json.loads(raw)
                    if value['type']=='rpc':
                        assert value['method']=='turn','Forbidden tool must be refused before host review'
                        assert 'Frozen cloud instruction' in value['arguments']['prompt']
                        turns+=1
                        result={'response_id':'turn-'+str(turns),'text':'done','calls':[] if turns>1 else [{'type':'function','call_id':'outside','name':'write_file','arguments':{'path':'untouched.txt','content':'changed'},'actions':[],'safety_checks':[]}],'usage':{},'output_items':[]}
                        process.stdin.write(frame({'type':'rpc-result','id':value['id'],'result':result}))
                    elif value['type']=='finished':finished=value['task'];break
                assert finished and finished['status']=='failed' and 'outside' in finished['error']
                assert turns==1
                await asyncio.wait_for(process.wait(),3);assert process.returncode==0
                assert target.read_text()=='original'
        finally:
            if process.returncode is None:process.kill()
            await process.wait()
    asyncio.run(run())


@pytest.mark.skipif(__import__('os').environ.get('TERMX_TEST_RUNNER_AGENT')!='1',reason='Explicit task-owned qualified Docker image proof')
def test_actual_network_isolated_container_executes_compatible_frozen_preset(tmp_path):
    import os,io,tarfile
    from test_runner_agents import fixture_state
    from termx.runners.service import RunnerService
    from termx.runners.agent import RunnerAgentService
    from termx.workspace.service import WorkspaceService
    service,state,principal,_=fixture_state(tmp_path)
    state.runners=RunnerService(tmp_path/'real-preset-runners',state.credentials)
    state.agent_store=state.agent.store;state.engines=SimpleNamespace(engines=lambda:[])
    state.authorization.resource_owner=lambda *args:None
    state.authorization.require_creation_principal=lambda *args:None
    state.workspace=WorkspaceService(state,tmp_path/'workspace',project_check=lambda *args:None)
    service=RunnerAgentService(state)
    profile=state.agent.store.create_custom_agent(name='Qualified cloud helper',instructions='Only create preset-proof.txt with the reviewed content.',tools=['write_file'])
    class Provider:
        def __init__(self):self.calls=0
        async def plan(self,prompt,*args):assert profile['instructions'] in prompt;return {'summary':'Create scoped artifact','steps':['Write reviewed file'],'tools':[],'risks':[]},'plan'
        async def turn(self,**args):
            assert profile['instructions'] in args['prompt'];self.calls+=1
            calls=[ProviderCall(type='function',call_id='scoped-write',name='write_file',arguments={'path':'preset-proof.txt','content':'reviewed preset output'})] if self.calls==1 else []
            return ProviderTurn('turn-'+str(self.calls),'Done' if not calls else '',calls,{})
    state.agent._adapter=lambda *_:Provider() # below replace with one task-local fixture
    provider=Provider();state.agent._adapter=lambda *_:provider
    async def run():
        runner=await state.runners.provision(principal.id,'project',{'image':os.environ['TERMX_RUNNER_IMAGE'],'lease_seconds':120,'network':'none','cpu':1,'memory_mb':512,'policy_version':1})
        try:
            binding=resolve_preset(state.workspace,principal,profile['id'],project_id='project',runner_id=runner['id'])
            task=await service.create_task(principal,runner['id'],'api','Create the artifact',provider_id='api',request_id='actual-preset-001',custom_agent=binding,limits={'max_seconds':60})
            state.agent.store.update_custom_agent(profile['id'],instructions='Edited library must not retarget running task',tools=['run_shell'])
            async with asyncio.timeout(70):
                while True:
                    live=state.agent.store.get_task(task['id'],include_events=True)
                    for approval in live['approvals']:
                        if approval['status']=='pending':await service.resolve_approval(task['id'],approval['id'],'approved')
                    if live['status'] in {'completed','failed','cancelled'}:break
                    await asyncio.sleep(.05)
                if service.workers:await asyncio.gather(*service.workers.values())
            assert live['status']=='completed',live
            raw=await state.runners.results(principal.id,runner['id'],'preset-proof.txt')
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:assert archive.extractfile('preset-proof.txt').read()==b'reviewed preset output'
            assert state.agent.store.task_agent(task)['instructions']==profile['instructions']
            assert any(record['effect']=='edit' and record['status']=='completed' for record in state.browser.review.history(principal.id))
        finally:
            await service.close();await state.runners.stop(principal.id,runner['id'],teardown=True);await state.runners.close();await state.agent.close();state.workspace.store.close();state.agent.store.close()
    asyncio.run(run())


def test_task_and_immutable_preset_commit_atomically(workspace):
    import sqlite3
    service,owner,path=workspace;profile=preset(service)
    store=service.agents;before=len(store.list_tasks())
    with store._lock:store._db.execute("CREATE TRIGGER reject_preset BEFORE INSERT ON task_agent_presets BEGIN SELECT RAISE(ABORT,'simulated snapshot write failure'); END")
    with pytest.raises(sqlite3.IntegrityError):store.create_task(prompt='Atomic',cwd=str(path),provider_id='fixture',model='fixture',limits={},custom_agent_id=profile['id'],custom_agent_snapshot=profile)
    assert len(store.list_tasks())==before
    with store._lock:store._db.execute('DROP TRIGGER reject_preset')
    task=store.create_task(prompt='Atomic',cwd=str(path),provider_id='fixture',model='fixture',limits={},custom_agent_id=profile['id'],custom_agent_snapshot=profile)
    assert store.task_agent(task)==profile
    store.freeze_task_agent(task['id'],profile)
    with pytest.raises(ValueError,match='immutable'):store.freeze_task_agent(task['id'],{**profile,'tools':['run_shell']})
