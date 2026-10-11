import asyncio
import json
import os
import sys
from pathlib import Path
from time import time

import pytest
from fastapi import HTTPException
from termx.runners.machines import capture, ssh_command, validate_config
from termx.runners.worker import capabilities
from termx.runners.http import Machine
from termx.agent.providers import ProviderTurn, ProviderCall
from test_runner_agents import fixture_state


def configuration(**changes):
    return {**Machine(project_id='project',name='Build VM',host='vm.example.test',user='ubuntu',root='/home/ubuntu/workspace',trust_machine=True).model_dump(exclude={'project_id'}),'policy_version':1,**changes}


@pytest.mark.parametrize('changes',[{'host':'-oProxyCommand=oops'},{'user':'u;touch /tmp/oops'},{'root':'/home/../etc'},{'root':'/'},{'trust_machine':False},{'identity_file':'relative.pem'},{'provider':'ec2','instance_id':''}])
def test_machine_rejects_unsafe_or_incomplete_configuration(changes):
    with pytest.raises(HTTPException):validate_config(configuration(**changes))


def test_machine_ssh_quotes_remote_arguments_and_keeps_keys_local():
    command=ssh_command(configuration(identity_file='/private/keys/runner key'),'python','-c','print("$(touch nope)")')
    assert 'StrictHostKeyChecking=yes' in command
    assert 'ForwardAgent=no' in command
    assert command[-2]=='ubuntu@vm.example.test'
    assert command[-1]=='''python -c 'print("$(touch nope)")' '''.strip()
    assert command[command.index('-i')+1]=='/private/keys/runner key'


def test_machine_lifecycle_qualification_owner_and_cloud_controls(tmp_path,monkeypatch):
    service,state,principal,_=fixture_state(tmp_path)
    async def run():
        machines=state.runners.machines
        row=machines.register(principal.id,'project',configuration(provider='ec2',region='us-east-1',instance_id='i-example'))
        assert row['status']=='registered' and row['expires']>time()+86400
        with pytest.raises(HTTPException):machines.row('other-owner',row['id'])
        with pytest.raises(HTTPException):await service.preflight(principal,row['id'],'api','api')
        async def probe(config):return capabilities(config['root'])
        monkeypatch.setattr(machines,'probe',probe)
        await machines.connect(principal.id,row['id'])
        admission=await service.preflight(principal,row['id'],'api','api')
        assert admission['capabilities']['network']=='machine'
        commands=[]
        async def fake_capture(command,**kwargs):commands.append(command);return b'{}'
        monkeypatch.setattr('termx.runners.machines.capture',fake_capture)
        await machines.cloud_action(principal.id,row['id'],'stop')
        assert commands[-1][-4:]==['ec2','stop-instances','--instance-ids','i-example']
        assert machines.row(principal.id,row['id'])['status']=='cloud-stop-pending'
        with pytest.raises(HTTPException):await service.preflight(principal,row['id'],'api','api')
        await machines.connect(principal.id,row['id'])
        await state.runners.stop(principal.id,row['id'],teardown=True)
        assert len(commands)==1 # disabling/removing idle execution does not stop a cloud VM
        with pytest.raises(HTTPException):await machines.connect(principal.id,row['id'])
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())


def test_machine_setup_is_deduplicated_and_revocation_prevents_ready(tmp_path,monkeypatch):
    service,state,principal,_=fixture_state(tmp_path)
    async def run():
        machines=state.runners.machines;row=machines.register(principal.id,'project',configuration())
        started=asyncio.Event();release=asyncio.Event();calls=[]
        async def setup(config):calls.append(config);started.set();await release.wait()
        async def probe(config):return capabilities(config['root'])
        monkeypatch.setattr(machines,'setup',setup);monkeypatch.setattr(machines,'probe',probe)
        assert machines.begin_setup(principal.id,row['id'])['status']=='setting-up'
        await started.wait()
        machines.begin_setup(principal.id,row['id'])
        assert len(calls)==1
        state.runners.authority=lambda row:False
        release.set();await asyncio.gather(*list(machines.operations.values()))
        assert machines.row(principal.id,row['id'])['status']=='unreachable'
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())


def test_lightsail_power_changes_require_idle_and_fail_closed(tmp_path,monkeypatch):
    service,state,principal,_=fixture_state(tmp_path)
    async def run():
        machines=state.runners.machines
        row=machines.register(principal.id,'project',configuration(provider='lightsail',region='eu-west-1',profile='team',instance_id='build-vm'))
        machines.status(row['id'],'ready')
        with state.runners.db() as db:db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',('job',row['id'],'request-1','digest','{}','running',time(),None,None))
        calls=[]
        async def success(command,**kwargs):calls.append(command);return b'{"state":{"name":"running"}}'
        monkeypatch.setattr('termx.runners.machines.capture',success)
        await machines.cloud_action(principal.id,row['id'],'status')
        assert calls[-1][-4:]==['lightsail','get-instance-state','--instance-name','build-vm']
        assert calls[-1][calls[-1].index('--profile')+1]=='team'
        with pytest.raises(HTTPException):await machines.cloud_action(principal.id,row['id'],'stop')
        assert len(calls)==1
        with state.runners.db() as db:db.execute("UPDATE jobs SET status='completed' WHERE id='job'")
        async def lost_response(*args,**kwargs):raise TimeoutError()
        monkeypatch.setattr('termx.runners.machines.capture',lost_response)
        with pytest.raises(TimeoutError):await machines.cloud_action(principal.id,row['id'],'reboot')
        assert machines.row(principal.id,row['id'])['status']=='cloud-outcome-unknown'
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())


def test_restart_cancels_unknown_machine_jobs_and_never_resurrects_removed_target(tmp_path,monkeypatch):
    service,state,principal,_=fixture_state(tmp_path)
    async def run():
        machines=state.runners.machines;row=machines.register(principal.id,'project',configuration())
        with state.runners.db() as db:db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',('interrupted',row['id'],'request-1','digest','{}','outcome-unknown',time(),None,None))
        calls=[]
        async def success(command,**kwargs):calls.append(command);return b''
        monkeypatch.setattr('termx.runners.machines.capture',success)
        await state.runners.stop(principal.id,row['id'],teardown=True)
        assert '--stop-job interrupted' in calls[0][-1]
        assert state.runners.jobs(principal.id,row['id'])[0]['status']=='stopped'
        await state.runners.stop(principal.id,row['id'])
        assert machines.row(principal.id,row['id'])['status']=='removed'
        assert len(calls)==1
        await service.close();await state.agent.close();state.agent.store.close()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get('TERMX_TEST_SSH')!='1',reason='Opt-in real loopback SSH server')
def test_real_ssh_machine_agent_workspace_and_persistence(tmp_path):
    from machine_ssh_fixture import ssh_machine
    service,state,principal,_=fixture_state(tmp_path)
    with ssh_machine(tmp_path/'ssh') as config:
        root=Path(config['root']);root.mkdir(parents=True)
        # Reuse the installed test environment; the browser proof also exercises setup.
        (root/'.termx-runtime').symlink_to(sys.prefix,target_is_directory=True)
        (root/'persistent.txt').write_text('survives tasks')
        class Provider:
            def __init__(self):self.turns=0
            async def plan(self,*args):return {'summary':'Write remote file','steps':['Write','Check'],'tools':['shell'],'risks':[]},'plan'
            async def turn(self,**kwargs):
                assert kwargs['cwd']==config['root']
                self.turns+=1
                calls=[ProviderCall(type='function',call_id='write',name='write_file',arguments={'path':'agent-result.txt','content':'written over SSH'})] if self.turns==1 else [ProviderCall(type='function',call_id='check',name='run_shell',arguments={'command':'cat agent-result.txt'})] if self.turns==2 else []
                return ProviderTurn('fixture-turn-'+str(self.turns),'SSH machine completed.' if not calls else '',calls,{})
        provider=Provider();state.agent._adapter=lambda _:provider
        async def run():
            machines=state.runners.machines
            assert (await capture(ssh_command(config,'printf','ssh-connected')))==b'ssh-connected'
            row=machines.register(principal.id,'project',config)
            await machines.connect(principal.id,row['id'])
            task=await service.create_task(principal,row['id'],'api','Write and verify a remote file',model='model-a',mode='agent',request_id='machine-task-1',limits={'max_seconds':30,'max_steps':4})
            async with asyncio.timeout(40):
                while service.workers:
                    live=service.store.get_task(task['id'],include_events=True)
                    for approval in live['approvals']:
                        if approval['status']=='pending':await service.resolve_approval(task['id'],approval['id'],'approved')
                    await asyncio.sleep(.05)
            result=service.store.get_task(task['id'])
            assert result['status']=='completed',result.get('error')
            assert result['cwd']==config['root']
            assert result['result']=='SSH machine completed.'
            assert (root/'agent-result.txt').read_text()=='written over SSH'
            assert 'private-fixture-api-key' not in json.dumps(service.store.get_task(task['id'],include_events=True))
            assert machines.row(principal.id,row['id'])['status']=='ready'
            second=await service.create_task(principal,row['id'],'api','Wait for plan approval',model='model-a',mode='agent',request_id='machine-task-2',limits={'max_seconds':30})
            async with asyncio.timeout(10):
                while service.store.get_task(second['id'])['status']!='awaiting_approval':await asyncio.sleep(.05)
            await state.runners.stop(principal.id,row['id'])
            await asyncio.gather(*list(service.workers.values()))
            assert machines.row(principal.id,row['id'])['status']=='stopped'
            assert (root/'.termx-control'/(second['id']+'.done')).exists()
            assert (root/'persistent.txt').read_text()=='survives tasks'
            assert await capture(ssh_command(config,'printf','still-running'))==b'still-running'
            await service.close();await state.agent.close();state.agent.store.close()
        asyncio.run(run())


@pytest.mark.skipif(os.environ.get('TERMX_TEST_SSH')!='1',reason='Opt-in real loopback SSH server')
def test_machine_worker_budget_survives_unresponsive_control_plane(tmp_path):
    from machine_ssh_fixture import ssh_machine
    from termx.runners.worker import frame
    with ssh_machine(tmp_path/'ssh') as config:
        root=Path(config['root']);root.mkdir();(root/'.termx-runtime').symlink_to(sys.prefix,target_is_directory=True)
        async def run():
            command=ssh_command(config,config['root']+'/.termx-runtime/bin/python','-I','-m','termx.runners.worker','--machine-root',config['root'],'--job-id','budget-test','--seconds','2')
            process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                process.stdin.write(frame({'type':'start','protocol':1,'provider':{'id':'api','model':'fixture'},'prompt':'Wait for broker','mode':'agent','limits':{'max_seconds':30,'max_steps':2}}));await process.stdin.drain()
                # Keep stdin open but never answer the provider RPC.
                await asyncio.wait_for(process.wait(),8)
                assert (root/'.termx-control/budget-test.done').exists(),(await process.stderr.read()).decode()
            finally:
                if process.returncode is None:process.kill()
                await process.wait()
        asyncio.run(run())


@pytest.mark.skipif(os.environ.get('TERMX_TEST_SSH_SETUP')!='1',reason='Opt-in SSH runtime installation with package downloads')
def test_real_ssh_installs_control_plane_source_in_fresh_runtime(tmp_path,monkeypatch):
    from machine_ssh_fixture import ssh_machine
    from termx.runners.service import RunnerService
    from termx.agent.secrets import CredentialStore
    import termx.runners.machines as machines
    original=machines.capture
    async def local_fixture_capture(command,data=None,**kwargs):
        # Only the loopback fixture shares this environment's network proxy and
        # CA configuration. Production machines use their own network setup.
        if command[0]=='ssh':command=[command[0],'-o','SendEnv=HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE PIP_INDEX_URL PIP_TRUSTED_HOST',*command[1:]]
        return await original(command,data=data,**kwargs)
    monkeypatch.setattr(machines,'capture',local_fixture_capture)
    with ssh_machine(tmp_path/'ssh') as config:
        config['python_bootstrap']=sys.executable
        service=RunnerService(tmp_path/'ledger',CredentialStore(memory={}))
        async def run():
            row=service.machines.register('fixture','project',config)
            try:
                result=await service.machines.connect('fixture',row['id'],setup=True)
                assert result['status']=='ready'
                assert (Path(config['root'])/'.termx-runtime/source/termx/runners/worker.py').exists()
                assert (await service.machines.probe(result['configuration']))['machine_control']=='stop-file-v1'
            finally:await service.close()
        asyncio.run(run())
