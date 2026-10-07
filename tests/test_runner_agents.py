import asyncio
import json
import os
import sys
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from time import time
import pytest
from fastapi import HTTPException
from termx.agent.store import AgentStore
from termx.agent.manager import AgentManager
from termx.agent.secrets import CredentialStore
from termx.browser.storage import Records
from termx.auto_review import DecisionBroker,ReviewVerdict
from termx.runners.service import RunnerService
from termx.runners.agent import RunnerAgentService
from termx.runners.worker import capabilities,frame,file_state
from termx.agent.providers import ProviderCall

class SafeReviewer:
    version='fixture-v1'
    async def evaluate(self,action,context):return ReviewVerdict('ALLOW','aligned','fixture reviewer',self.version,time()+15)

def fixture_state(tmp_path):
    principal=SimpleNamespace(id='owner',policy_version=1)
    credentials=CredentialStore(memory={'api':'private-fixture-api-key'})
    store=AgentStore(tmp_path/'agent')
    store.put_provider('api',kind='openai-compatible',name='Explicit API account',base_url='https://provider.invalid',model='model-a,model-b',capabilities=['shell'],secret_configured=True)
    store.put_provider('subscription',kind='chatgpt',name='Subscription',base_url='',model='model-a',capabilities=['shell'],secret_configured=True)
    manager=AgentManager(store,credentials,SimpleNamespace())
    runners=RunnerService(tmp_path/'runners',credentials)
    config={'context':'fixture','policy_version':1,'network':'none','image_id':'sha256:fixture','cpu':1,'memory_mb':512}
    with runners.db() as db:db.execute('INSERT INTO runners VALUES(?,?,?,?,?,?,?)',('runner','owner','project',json.dumps(config),'container','ready',time()+300))
    host={'ReadonlyRootfs':True,'NetworkMode':'none','Binds':None,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges=true'],'PidsLimit':128,'Memory':512*1024*1024,'NanoCpus':1000000000,'Tmpfs':{'/workspace':'rw','/tmp':'rw','/run/termx':'rw'}}
    inspect={'Image':'sha256:fixture','State':{'Running':True},'Config':{'User':'65534:65534'},'Mounts':[],'HostConfig':host}
    async def docker(config,*args,**kwargs):
        if args[0]=='inspect':return json.dumps([inspect]).encode()
        if '--capabilities' in args:return json.dumps(capabilities()).encode()
        return b''
    runners.docker=docker
    state=SimpleNamespace(agent=manager,credentials=credentials,runners=runners,identity=SimpleNamespace(principal_by_id=lambda _:principal),authorization=SimpleNamespace(require_principal=lambda *a,**k:None),browser=SimpleNamespace(review=DecisionBroker(Records(tmp_path/'reviews'),SafeReviewer())))
    service=RunnerAgentService(state)
    return service,state,principal,inspect

def test_qualified_image_boundary_and_explicit_account(tmp_path):
    service,state,principal,inspect=fixture_state(tmp_path)
    async def run():
        assert service.accounts(principal)==[{'provider_id':'api','credential_ref':'api','name':'Explicit API account','models':['model-a','model-b']}]
        for provider,reference in [('api','other'),('subscription','subscription')]:
            with pytest.raises(HTTPException):await service.preflight(principal,'runner',provider,reference)
        valid=await service.preflight(principal,'runner','api','api','model-b')
        assert valid['provider']['model']=='model-b'
        for field,bad in [('NetworkMode','bridge'),('ReadonlyRootfs',False),('PidsLimit',0),('NanoCpus',0),('Binds',['/host:/workspace'])]:
            old=inspect['HostConfig'][field];inspect['HostConfig'][field]=bad
            with pytest.raises(HTTPException):await service.preflight(principal,'runner','api','api')
            inspect['HostConfig'][field]=old
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())

@pytest.mark.parametrize('invalid_frame',[frame({'type':'invalid-host-control'}),b'{invalid json\n',frame(['not-an-object'])])
def test_worker_invalid_control_cancels_pending_broker_and_exits_without_stdin_eof(tmp_path,invalid_frame):
    root=tmp_path/'remote-invalid';root.mkdir();target=root/'hello.txt';target.write_text('unchanged')
    async def run():
        env={**os.environ,'TERMX_RUNNER_TEST_ROOT':str(root),'TERMX_CONFIG_DIR':str(tmp_path/'isolated-invalid'),'TERMX_RUNNER_DIAGNOSTICS':'1'}
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','termx.runners.worker',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,limit=8*1024*1024+2)
        process.stdin.write(frame({'type':'start','protocol':1,'provider':{'id':'api','model':'fixture'},'prompt':'Bounded protocol refusal','mode':'agent','limits':{'max_seconds':10,'max_steps':2}}))
        await process.stdin.drain()
        events=[];last_task=None;phase='await-planning-rpc';planning=False
        try:
            async with asyncio.timeout(30):
                while raw:=await process.stdout.readline():
                    value=json.loads(raw)
                    if value['type']=='event':events.append(value['event']['type']);last_task=value['task']
                    if value['type']=='rpc':
                        assert value['method']=='plan'
                        planning=True;break
                assert planning,'Worker exited before its pending planning RPC'
            phase='reject-invalid-host-control'
            async with asyncio.timeout(5):
                process.stdin.write(invalid_frame);await process.stdin.drain()
                while raw:=await process.stdout.readline():
                    value=json.loads(raw)
                    if value['type']=='event':
                        events.append(value['event']['type']);last_task=value['task']
                    assert value['type']!='rpc'
                await asyncio.wait_for(process.wait(),3)
                assert process.returncode==0,(await process.stderr.read()).decode()
            assert events.count('runner.failure')==1 and not any(event.startswith('tool.') for event in events)
            assert 'task.cancelled' not in events and last_task['status']=='failed'
            assert last_task['error']=='Host runner control protocol failed'
            assert target.read_text()=='unchanged'
        except TimeoutError:
            prekill_returncode=process.returncode
            if process.returncode is None:process.kill()
            await process.wait()
            stderr=await asyncio.wait_for(process.stderr.read(16384),2)
            raise AssertionError(json.dumps({'phase':phase,'startup_timeout_seconds':30,'cancellation_timeout_seconds':5,
                'event_types':events[-16:],'alive_before_fixture_kill':prekill_returncode is None,
                'stderr':stderr.decode('utf-8','replace')})) from None
        finally:
            if process.returncode is None:process.kill()
            await process.wait()
    asyncio.run(run())

@pytest.mark.parametrize('control',['cancel','eof'])
def test_worker_normal_cancellation_of_pending_broker_is_not_protocol_failure(tmp_path,control):
    root=tmp_path/'remote-cancel';root.mkdir();target=root/'hello.txt';target.write_text('unchanged')
    async def run():
        env={**os.environ,'TERMX_RUNNER_TEST_ROOT':str(root),'TERMX_CONFIG_DIR':str(tmp_path/'isolated-cancel'),'TERMX_RUNNER_DIAGNOSTICS':'1'}
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','termx.runners.worker',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,limit=8*1024*1024+2)
        process.stdin.write(frame({'type':'start','protocol':1,'provider':{'id':'api','model':'fixture'},'prompt':'Bounded cancellation','mode':'agent','limits':{'max_seconds':10,'max_steps':2}}))
        await process.stdin.drain()
        events=[];last_task=None;phase='await-planning-rpc';planning=False
        try:
            # Loading the real worker's modules/private stores is distinct
            # from cancellation latency. Bound readiness separately so a slow
            # Windows import cannot consume the effect-control deadline.
            async with asyncio.timeout(30):
                while raw:=await process.stdout.readline():
                    value=json.loads(raw)
                    if value['type']=='event':events.append(value['event']['type']);last_task=value['task']
                    if value['type']=='rpc':
                        assert value['method']=='plan'
                        planning=True;break
                assert planning,'Worker exited before its pending planning RPC'
            # Start this strict deadline only after the host actually revokes
            # the pending operation, retaining the no-effects/no-retry proof.
            phase='cancel-pending-planning-rpc:'+control
            async with asyncio.timeout(5):
                if control=='cancel':
                    process.stdin.write(frame({'type':'cancel'}));await process.stdin.drain()
                else:
                    process.stdin.close();await process.stdin.wait_closed()
                while raw:=await process.stdout.readline():
                    value=json.loads(raw)
                    if value['type']=='event':events.append(value['event']['type']);last_task=value['task']
                    assert value['type']!='rpc'
                await asyncio.wait_for(process.wait(),3)
                assert process.returncode==0,(await process.stderr.read()).decode()
            assert events.count('task.cancelled')==1 and 'runner.failure' not in events
            assert not any(event.startswith('tool.') for event in events)
            assert last_task['status']=='cancelled' and target.read_text()=='unchanged'
        except TimeoutError:
            prekill_returncode=process.returncode
            if process.returncode is None:process.kill()
            await process.wait()
            stderr=await asyncio.wait_for(process.stderr.read(16384),2)
            raise AssertionError(json.dumps({'phase':phase,'startup_timeout_seconds':30,'cancellation_timeout_seconds':5,
                'event_types':events[-16:],'alive_before_fixture_kill':prekill_returncode is None,
                'stderr':stderr.decode('utf-8','replace')})) from None
        finally:
            if process.returncode is None:process.kill()
            await process.wait()
    asyncio.run(run())

def test_canonical_admission_request_dedup_and_restart_no_replay(tmp_path):
    service,state,principal,_=fixture_state(tmp_path)
    dispatched=[]
    async def drive(task_id,*args):dispatched.append(task_id)
    service._drive=drive
    async def run():
        claimed=[]
        first=await service.create_task(principal,'runner','api','Do bounded work',provider_id='api',request_id='request-123',limits={'max_seconds':30},on_created=lambda identifier:claimed.append(identifier))
        assert first['engine']=='runner' and service.owns(first)
        assert claimed==[first['id']]
        await asyncio.gather(*service.workers.values())
        duplicate=await service.create_task(principal,'runner','api','Do bounded work',provider_id='api',request_id='request-123',limits={'max_seconds':30})
        assert duplicate['id']==first['id'] and dispatched==[first['id']]
        with pytest.raises(HTTPException):await service.create_task(principal,'runner','api','Different request',provider_id='api',request_id='request-123',limits={'max_seconds':30})
        with pytest.raises(HTTPException):await service.create_task(principal,'runner','api','Concurrent request',provider_id='api',request_id='request-456',limits={'max_seconds':30})
        await service.startup_reconcile()
        assert state.agent.store.get_task(first['id'])['status']=='failed'
        assert 'unknown' in state.agent.store.get_task(first['id'])['error']
        assert dispatched==[first['id']]
        assert 'private-fixture-api-key' not in service.runners.path.read_bytes().decode(errors='ignore')
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())

def test_consumed_review_binds_file_state_and_authority(tmp_path):
    service,state,principal,_=fixture_state(tmp_path)
    async def run():
        service._drive=lambda *a:asyncio.sleep(0)
        task=await service.create_task(principal,'runner','api','Write hello.txt',provider_id='api',request_id='review-123',limits={'max_seconds':30})
        await asyncio.gather(*service.workers.values())
        job=service._job(task['id']);args={'call':{'type':'function','name':'write_file','arguments':{'path':'hello.txt','content':'hello'}},'state':[['/workspace/hello.txt',None]]}
        result=await service._review(job,'rpc-one',args)
        assert result['allowed']
        assert state.browser.review.records.get('review',result['review_id'])['status']=='executing'
        assert (await service._review(job,'rpc-one',args))['allowed'] is False
        changed={**args,'state':[['/workspace/hello.txt','0'*64]]}
        with pytest.raises(ValueError):await service._review(job,'rpc-one',changed)
        sensitive={**args,'state':[['/workspace/.env',None]]}
        assert not (await service._review(job,'rpc-two',sensitive))['allowed']
        principal.policy_version=2
        assert not service._valid(job)
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())

@pytest.mark.parametrize('change_after_review',[False,True])
def test_real_worker_reuses_agent_loop_and_checks_post_review_file(tmp_path,change_after_review):
    root=tmp_path/'remote';root.mkdir();target=root/'hello.txt';target.write_text('before')
    argv=[sys.executable,'-c',"from pathlib import Path; print(Path('hello.txt').read_text())"]
    verify_command=subprocess.list2cmdline(argv) if os.name=='nt' else ' '.join(shlex.quote(arg) for arg in argv)
    async def run():
        env={**os.environ,'TERMX_RUNNER_TEST_ROOT':str(root),'TERMX_CONFIG_DIR':str(tmp_path/'isolated'),'TERMX_RUNNER_DIAGNOSTICS':'1'}
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','termx.runners.worker',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,limit=8*1024*1024+2)
        def send(value):process.stdin.write(frame(value))
        send({'type':'start','protocol':1,'provider':{'id':'api','model':'fixture'},'prompt':'Write hello.txt with after','mode':'agent','limits':{'max_seconds':20,'max_steps':4}})
        turns=0;reviewed=False;events=[];finished=None;phase='await-startup-frame';methods=[]
        try:
            async with asyncio.timeout(30):
                while raw:=await process.stdout.readline():
                    value=json.loads(raw)
                    phase='frame:'+str(value.get('type'))
                    if value['type']=='event':
                        events.append(value['event'])
                        phase='event:'+str(value['event'].get('type'))
                        if value['event']['type']=='approval.requested':send({'type':'approval','approval_id':value['event']['payload']['approval']['id'],'decision':'approved'})
                    elif value['type']=='rpc':
                        method=value['method'];arguments=value['arguments'];methods.append(method);phase='rpc:'+method
                        if method=='plan':result=[{'summary':'Write hello','steps':['Write file'],'tools':['shell'],'risks':[]},'plan-1']
                        elif method=='turn':
                            turns+=1
                            result={'response_id':'turn-'+str(turns),'text':'done' if turns>2 else '', 'calls':[] if turns>2 else [{'type':'function','call_id':'shell-1','name':'run_shell','arguments':{'command':verify_command,'purpose':'Verify the written file'},'actions':[],'safety_checks':[]}] if turns==2 else [{'type':'function','call_id':'write-1','name':'write_file','arguments':{'path':'hello.txt','content':'after','expected_revision':__import__('termx.project_files',fromlist=['ProjectFiles']).ProjectFiles.revision(b'before')},'actions':[],'safety_checks':[]}], 'usage':{},'output_items':[]}
                        elif method=='review':
                            reviewed=True
                            assert arguments['state']==([[str(target),__import__('hashlib').sha256(b'before').hexdigest()]] if arguments['call']['name']=='write_file' else [])
                            if change_after_review and arguments['call']['name']=='write_file':target.write_text('external change')
                            result={'allowed':True,'review_id':'review-one'}
                        elif method=='effect-result':result={'recorded':True}
                        else:raise AssertionError(method)
                        send({'type':'rpc-result','id':value['id'],'result':result})
                    elif value['type']=='finished':finished=value['task'];break
                assert finished is not None,(await process.stderr.read()).decode()
                phase='await-normal-worker-exit-with-host-input-open'
                await asyncio.wait_for(process.wait(),3)
                assert process.returncode==0,(await process.stderr.read()).decode()
        except TimeoutError:
            prekill_returncode=process.returncode
            if process.returncode is None:process.kill()
            await process.wait()
            stderr=await asyncio.wait_for(process.stderr.read(16384),2)
            raise AssertionError(json.dumps({'worker_timeout_seconds':30,'phase':phase,'provider_turns':turns,
                'reviewed':reviewed,'rpc_methods':methods[-16:],'event_types':[event['type'] for event in events[-16:]],
                'prekill_returncode':prekill_returncode,'alive_before_fixture_kill':prekill_returncode is None,
                'returncode':process.returncode,'stderr':stderr.decode('utf-8','replace')})) from None
        finally:
            if process.returncode is None:process.kill()
            await process.wait()
        assert reviewed
        if change_after_review:
            assert target.read_text()=='external change'
            assert finished['status']=='failed'
        else:
            assert target.read_text()=='after', json.dumps({'status':finished['status'],'error':finished.get('error'),'tools':[e for e in events if e['type'].startswith('tool.')]})
            assert finished['status']=='completed'
        assert not any('private-fixture-api-key' in json.dumps(event) for event in events)
    asyncio.run(run())

def test_worker_protocol_limits_and_boundary(tmp_path):
    with pytest.raises(ValueError):frame({'data':'x'*(8*1024*1024)})
    with pytest.raises(ValueError):file_state(ProviderCall(type='function',call_id='x',name='write_file',arguments={'path':'../escape'}),str(tmp_path))
    assert 'computer' not in capabilities()['tools'] and 'browser_action' not in capabilities()['tools']

@pytest.mark.skipif(os.environ.get('TERMX_TEST_RUNNER_AGENT')!='1',reason='Explicit qualified Docker image validation')
def test_real_network_isolated_runner_agent_provider_review_controls(tmp_path):
    import io
    import tarfile
    from termx.agent.providers import ProviderTurn
    service,state,principal,_=fixture_state(tmp_path)
    state.runners=RunnerService(tmp_path/'real-runners',state.credentials)
    service=RunnerAgentService(state)
    class Provider:
        def __init__(self):self.calls=0
        async def plan(self,*args):return {'summary':'Create a runner artifact','steps':['Write file','Verify isolated execution'],'tools':['shell'],'risks':[]},'plan'
        async def turn(self,**args):
            assert args['cwd']=='/workspace' and not args['allow_computer']
            self.calls+=1
            calls=[ProviderCall(type='function',call_id='write',name='write_file',arguments={'path':'agent-result.txt','content':'reviewed remote artifact'})] if self.calls==1 else [ProviderCall(type='function',call_id='check',name='run_shell',arguments={'command':'test -z "$TERMX_AI_API_API_KEY" && test ! -e /var/run/docker.sock && cat agent-result.txt'})] if self.calls==2 else []
            return ProviderTurn('turn-'+str(self.calls),'Verified' if not calls else '',calls,{})
    provider=Provider();state.agent._adapter=lambda _:provider
    async def run():
        runner=await state.runners.provision(principal.id,'project',{'image':os.environ.get('TERMX_RUNNER_IMAGE','termx-runner:workspace'),'lease_seconds':120,'network':'none','cpu':1,'memory_mb':512,'policy_version':1})
        try:
            preflight=await service.preflight(principal,runner['id'],'api','api')
            assert preflight['capabilities']['credential_transport']=='host-stdio-broker'
            claimed=[]
            task=await service.create_task(principal,runner['id'],'api','Create and verify agent-result.txt in this isolated runner',provider_id='api',request_id='actual-agent-001',limits={'max_seconds':60},on_created=lambda identifier:claimed.append(identifier))
            assert claimed==[task['id']]
            async with asyncio.timeout(70):
                while True:
                    live=state.agent.store.get_task(task['id'],include_events=True)
                    for approval in live['approvals']:
                        if approval['status']=='pending':await service.resolve_approval(task['id'],approval['id'],'approved')
                    if live['status'] in {'completed','failed','cancelled'}:break
                    await asyncio.sleep(.05)
                if service.workers:await asyncio.gather(*service.workers.values())
            assert state.agent.store.get_task(task['id'])['status']=='completed',state.agent.store.get_task(task['id'])
            data=await state.runners.results(principal.id,runner['id'],'agent-result.txt')
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:assert archive.extractfile('agent-result.txt').read()==b'reviewed remote artifact'
            records=state.browser.review.history(principal.id)
            assert any(record['effect']=='edit' and record['status']=='completed' for record in records)
            assert any(record['effect']=='unknown' and record['status']=='completed' for record in records)
            assert 'private-fixture-api-key' not in json.dumps(state.agent.store.get_task(task['id'],include_events=True))
            second=await service.create_task(principal,runner['id'],'api','Pause on plan',provider_id='api',request_id='actual-agent-002',limits={'max_seconds':30})
            service.cancel(second['id'])
            await asyncio.gather(*service.workers.values())
            assert state.agent.store.get_task(second['id'])['status']=='cancelled'
            assert state.runners.row(principal.id,runner['id'])['status']=='stopped'
        finally:
            await service.close();await state.runners.stop(principal.id,runner['id'],teardown=True);await state.runners.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())

def test_cancel_before_dispatch_stops_runner_and_does_not_leave_active_job(tmp_path):
    service,state,principal,_=fixture_state(tmp_path)
    async def never_dispatch(*args):await asyncio.sleep(60)
    service._drive=never_dispatch
    async def run():
        task=await service.create_task(principal,'runner','api','Bounded work',request_id='cancel-before-dispatch',limits={'max_seconds':30})
        service.cancel(task['id'])
        await asyncio.gather(*service.workers.values())
        assert state.agent.store.get_task(task['id'])['status']=='cancelled'
        assert state.runners.row(principal.id,'runner')['status']=='stopped'
        assert state.runners.jobs(principal.id,'runner')[0]['status']=='cancelled'
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())

def test_running_runner_checks_live_delegation_and_originating_session(tmp_path):
    service,state,principal,_=fixture_state(tmp_path)
    async def drive(*args):return None
    service._drive=drive
    async def run():
        task=await service.create_task(principal,'runner','api','Scheduled work',request_id='delegated-001',limits={'max_seconds':30})
        await asyncio.gather(*service.workers.values())
        job=service._job(task['id']);grant={'revoked':False}
        def validate(actor,conversation,identifier):
            if grant['revoked']:raise PermissionError('Revoked delegated grant')
        state.workspace=SimpleNamespace(store=SimpleNamespace(get=lambda kind,identifier:{'delegation_id':'grant','conversation_id':'conversation'} if kind=='task' else {'id':'conversation'}),validate_delegation=validate)
        assert service._valid(job)
        grant['revoked']=True
        assert not service._valid(job)
        grant['revoked']=False
        records=Records(tmp_path/'session-records');state.browser.records=records
        records.put('agent-task-authority',task['id'],{'session_id':'managed-session'})
        lookups=[];live={'principal':principal}
        def execution_session(identifier):
            lookups.append(identifier)
            return SimpleNamespace(principal=live['principal']) if live['principal'] else None
        state.identity.execution_session=execution_session
        assert service._valid(job)
        assert lookups==['managed-session']
        live['principal']=SimpleNamespace(id='other-owner',policy_version=principal.policy_version)
        assert not service._valid(job)
        live['principal']=SimpleNamespace(id=principal.id,policy_version=principal.policy_version+1)
        assert not service._valid(job)
        live['principal']=None
        assert not service._valid(job)
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())

def test_canonical_runner_uploads_and_events_never_adopt_remote_ids(tmp_path):
    import base64
    service,state,principal,_=fixture_state(tmp_path)
    async def drive(*args):return None
    service._drive=drive
    async def run():
        task=await service.create_task(principal,'runner','api','Describe the explicit image',mode='ask',request_id='image-upload-001',limits={'max_seconds':30},attachments=[{'name':'fixture.png','mime':'image/png','data':base64.b64encode(b'controlled-fixture-image').decode()}])
        await asyncio.gather(*service.workers.values())
        artifacts=state.agent.store.artifacts(task['id'])
        assert len(artifacts)==1 and artifacts[0]['kind']=='upload'
        assert Path(state.agent.store.get_artifact(task['id'],artifacts[0]['id'])['path']).read_bytes()==b'controlled-fixture-image'
        service._event(task['id'],{'type':'event','event':{'type':'task.created','payload':{'task':{'id':'remote-unclaimed','engine':'internal'}}},'task':{'status':'running'}})
        service._event(task['id'],{'type':'event','event':{'type':'user.media','payload':{'artifacts':[{'id':'remote-artifact'}]}},'task':{'status':'running'}})
        events=state.agent.store.events(task['id'])
        created=[e for e in events if e['type']=='task.created'][-1]
        assert created['payload']['task']['id']==task['id'] and created['payload']['task']['engine']=='runner'
        assert events[-1]['payload']['artifacts'][0]['id']==artifacts[0]['id']
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())

def test_remote_provider_advertises_only_qualified_coding_tools(tmp_path):
    service,state,principal,_=fixture_state(tmp_path)
    adapter=service._adapter(service._account('api','api'),['read_file','write_file'])
    names={schema['name'] for schema in adapter._function_tools(False,allow_subagents=True)}
    assert names=={'read_file','write_file'}
    local=state.agent._adapter(service._account('api','api'))
    assert 'browser_action' in {schema['name'] for schema in local._function_tools(False)}
    asyncio.run(state.agent.close());state.agent.store.close()
