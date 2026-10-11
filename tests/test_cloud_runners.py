import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from time import time

import pytest
from fastapi import HTTPException
from termx.runners.cloud.service import CloudService
from termx.runners.cloud.base import CloudError
from termx.runners.cloud.bootstrap import identities, cloud_init
from termx.runners.cloud.models import Launch
from termx.agent.providers import ProviderCall, ProviderTurn
from test_runner_agents import fixture_state
from test_runner_machines import configuration
from cloud_fixtures import cloud, CREDENTIALS, launch_plan


async def fixture(tmp_path,kind,monkeypatch):
    agents,state,principal,_=fixture_state(tmp_path)
    provider=cloud(kind)
    service=CloudService(state.runners,lambda row:row['configuration']['policy_version']==principal.policy_version)
    service.provider_factory=provider.factory
    account=await service.save_account(principal.id,kind,'Fixture account',CREDENTIALS[kind])
    async def attach(d,connection,keys):
        row=state.runners.machines.register(principal.id,'project',configuration(name=d['plan']['name'],cloud_deployment=d['id']))
        state.runners.machines.status(row['id'],'ready');service.update(principal.id,d['id'],runner_id=row['id'])
    monkeypatch.setattr(service,'attach',attach)
    return service,provider,account,agents,state,principal

async def settle(service):
    while service.workers:await asyncio.gather(*list(service.workers.values()))

async def close(service,agents,state):
    await service.close();await agents.close();await state.agent.close();state.agent.store.close()


@pytest.mark.parametrize('kind',['aws','azure','gcp'])
@pytest.mark.parametrize('existing',[False,True])
def test_account_catalog_launch_idempotency_and_full_cleanup(tmp_path,monkeypatch,kind,existing):
    async def run():
        service,provider,account,agents,state,principal=await fixture(tmp_path,kind,monkeypatch)
        config=launch_plan(kind,account['id'])
        if existing:
            config.update(network_mode='existing',public_ip=False);config['network'],config['subnet']=provider.borrowed()
        borrowed=set(provider.rows)
        review=await service.preview(principal,config)
        d=service.launch(principal,review['id']);duplicate=service.launch(principal,review['id'])
        assert duplicate['id']==d['id']
        await settle(service);ready=service.row(principal.id,d['id'])
        assert ready['status']=='ready',ready
        assert state.runners.row(principal.id,ready['runner_id'])['configuration']['kind']=='machine'
        assert any(r['kind']=='vm' for r in ready['resources'])
        if kind=='aws':
            body=next(body for method,body in provider.calls if method=='run_instances')
            assert 'IamInstanceProfile' not in body
            assert body['MetadataOptions']['HttpTokens']=='required'
            assert all(disk['Ebs']['DeleteOnTermination'] for disk in body['BlockDeviceMappings'])
        elif kind=='azure':
            body=next(body for method,path,body in provider.calls if method=='PUT' and '/virtualMachines/' in path)
            assert 'identity' not in body
            storage=body['properties']['storageProfile']
            assert all(disk['deleteOption']=='Delete' for disk in [storage['osDisk'],*storage['dataDisks']])
        else:
            body=next(body for method,path,body in provider.calls if method=='POST' and path.endswith('/instances'))
            assert body['serviceAccounts']==[]
            assert all(disk['autoDelete'] for disk in body['disks'])
        assert 'fixture-secret-only' not in json.dumps(service.accounts(principal.id))
        assert 'fixture-secret-only' not in state.runners.path.read_bytes().decode(errors='ignore')
        assert 'PRIVATE KEY' not in state.runners.path.read_bytes().decode(errors='ignore')
        with pytest.raises(HTTPException):service.row('other-owner',d['id'])
        with pytest.raises(HTTPException):service.delete_account(principal.id,account['id'])
        await service.request_cleanup(principal.id,d['id']);await settle(service)
        final=service.row(principal.id,d['id'])
        assert final['status']=='deleted',final
        assert set(provider.rows)==borrowed
        assert state.credentials.get('cloud-machine:'+d['id']) is None
        assert state.runners.row(principal.id,ready['runner_id'])['status']=='removed'
        service.delete_account(principal.id,account['id'])
        await close(service,agents,state)
    asyncio.run(run())


@pytest.mark.parametrize('kind',['aws','azure','gcp'])
def test_lost_create_response_reconciles_owned_resource_and_rolls_back(tmp_path,monkeypatch,kind):
    async def run():
        service,provider,account,agents,state,principal=await fixture(tmp_path,kind,monkeypatch)
        factory=service.provider_factory
        def drop_response(kind,credentials):
            adapter=factory(kind,credentials);create=adapter.create
            async def lost(resource,name,params,d):
                value=await create(resource,name,params,d)
                if resource=='vm':raise CloudError('Simulated response lost after provider accepted the VM')
                return value
            adapter.create=lost;return adapter
        service.provider_factory=drop_response
        review=await service.preview(principal,launch_plan(kind,account['id']))
        d=service.launch(principal,review['id']);await settle(service)
        final=service.row(principal.id,d['id'])
        assert final['status']=='deleted',final
        assert 'response lost' in final['error']
        assert not provider.rows
        assert any(r['kind']=='vm' and r['state']=='deleted' for r in final['resources'])
        await close(service,agents,state)
    asyncio.run(run())


@pytest.mark.parametrize('kind',['aws','azure','gcp'])
def test_cleanup_failure_retains_identity_for_retry_and_preserves_foreign_resources(tmp_path,monkeypatch,kind):
    async def run():
        service,provider,account,agents,state,principal=await fixture(tmp_path,kind,monkeypatch)
        review=await service.preview(principal,launch_plan(kind,account['id']))
        d=service.launch(principal,review['id']);await settle(service)
        provider.fail='terminate_instances' if kind=='aws' else '/virtualMachines/' if kind=='azure' else '/instances/'
        await service.request_cleanup(principal.id,d['id']);await settle(service)
        assert service.row(principal.id,d['id'])['status']=='cleanup-failed'
        assert state.credentials.get('cloud-machine:'+d['id'])
        provider.fail=None
        await service.request_cleanup(principal.id,d['id']);await settle(service)
        assert service.row(principal.id,d['id'])['status']=='deleted'
        assert not provider.rows
        await close(service,agents,state)
    asyncio.run(run())


def test_preview_is_bound_to_owner_account_revision_and_project_authority(tmp_path,monkeypatch):
    async def run():
        service,provider,account,agents,state,principal=await fixture(tmp_path,'aws',monkeypatch)
        plan=launch_plan('aws',account['id'])
        for changes in ({'size':'not-a-real-size'},{'region':'wrong-region'},{'subnet':'wrong-subnet','network_mode':'existing','network':'wrong-network'}):
            with pytest.raises(HTTPException):await service.preview(principal,{**plan,**changes})
        preview=await service.preview(principal,plan)
        with pytest.raises(HTTPException):service.launch(SimpleNamespace(id='other',policy_version=1),preview['id'])
        await service.save_account(principal.id,'aws','Rotated',CREDENTIALS['aws'],account['id'])
        with pytest.raises(HTTPException):service.launch(principal,preview['id'])
        preview=await service.preview(principal,plan);principal.policy_version+=1
        with pytest.raises(HTTPException):service.launch(principal,preview['id'])
        assert not provider.rows
        await close(service,agents,state)
    asyncio.run(run())


def test_bootstrap_has_pinned_host_key_and_no_cloud_account_credentials():
    import yaml
    keys=identities();config=yaml.safe_load(cloud_init(keys))
    assert config['ssh_keys']['ed25519_public']==keys['host_public']
    assert config['users'][0]['ssh_authorized_keys']==[keys['public']]
    assert config['users'][0]['sudo'] is False
    assert 'gpasswd -d termx' in config['write_files'][0]['content']
    assert 'gpasswd -d termx' not in yaml.safe_load(cloud_init(keys,True))['write_files'][0]['content']
    assert config['ssh_pwauth'] is False
    assert 'private_key' not in config
    with pytest.raises(ValueError):Launch(**{**launch_plan('aws'),'ssh_cidr':'0.0.0.0/33'})
    with pytest.raises(ValueError):Launch(**{**launch_plan('aws'),'accept_cost':False})
    with pytest.raises(ValueError):Launch(**{**launch_plan('aws'),'public_ip':False})


@pytest.mark.parametrize('reason',['lifetime','after-task','authority','restart'])
def test_automatic_cleanup_and_restart_do_not_replay_provisioning(tmp_path,monkeypatch,reason):
    async def run():
        service,provider,account,agents,state,principal=await fixture(tmp_path,'aws',monkeypatch)
        plan=launch_plan('aws',account['id'])
        if reason=='after-task':plan['cleanup']='after-task'
        review=await service.preview(principal,plan)
        d=service.launch(principal,review['id']);await settle(service)
        creates=sum(method=='run_instances' for method,_ in provider.calls)
        if reason=='lifetime':service.update(principal.id,d['id'],expires=time()-1)
        if reason=='after-task':monkeypatch.setattr(state.runners,'jobs',lambda *args:[{'status':'completed'}])
        if reason=='authority':service.authority=lambda _:False
        if reason=='restart':
            service.update(principal.id,d['id'],status='bootstrapping')
            await service.close()
            service=CloudService(state.runners,lambda _:True);service.provider_factory=provider.factory
        service.start()
        async with asyncio.timeout(10):
            while service.row(principal.id,d['id'])['status']!='deleted':await asyncio.sleep(.02)
        assert not provider.rows
        assert sum(method=='run_instances' for method,_ in provider.calls)==creates
        await close(service,agents,state)
    asyncio.run(run())


@pytest.mark.parametrize('kind',['aws','azure','gcp'])
def test_changed_ownership_never_deletes_or_claims_cleanup_succeeded(tmp_path,monkeypatch,kind):
    async def run():
        service,provider,account,agents,state,principal=await fixture(tmp_path,kind,monkeypatch)
        review=await service.preview(principal,launch_plan(kind,account['id']))
        d=service.launch(principal,review['id']);await settle(service)
        if kind=='aws':
            vm=next(row for row in provider.rows.values() if row.get('kind')=='instance');vm['Tags']=[]
        elif kind=='azure':
            vm=next(row for path,row in provider.rows.items() if '/virtualMachines/' in path);vm['tags']={}
        else:
            vm=next(row for path,row in provider.rows.items() if '/instances/' in path);vm['labels']={};vm['description']='changed owner'
        await service.request_cleanup(principal.id,d['id']);await settle(service)
        assert service.row(principal.id,d['id'])['status']=='cleanup-failed'
        assert vm in provider.rows.values()
        await close(service,agents,state)
    asyncio.run(run())


@pytest.mark.skipif(os.name=='nt',reason='Real Linux worker fixture')
@pytest.mark.parametrize('kind',['aws','azure','gcp'])
def test_mock_cloud_machine_runs_real_agent_then_cleans_up(tmp_path,monkeypatch,kind):
    async def run():
        service,provider,account,agents,state,principal=await fixture(tmp_path,kind,monkeypatch)
        root=tmp_path/'cloud-workspace';root.mkdir()
        async def attach(d,connection,keys):
            row=state.runners.machines.register(principal.id,'project',configuration(root=str(root),cloud_deployment=d['id']))
            service.update(principal.id,d['id'],runner_id=row['id']);state.runners.machines.status(row['id'],'ready')
        monkeypatch.setattr(service,'attach',attach)
        monkeypatch.setattr(state.runners.machines,'worker_command',lambda config,*args:[sys.executable,'-I','-m','termx.runners.worker','--machine-root',config['root'],*args])
        class Provider:
            turns=0
            async def plan(self,*args):return {'summary':'Use the cloud machine','steps':['Write artifact'],'tools':['shell'],'risks':[]},'plan'
            async def turn(self,**kwargs):
                assert kwargs['cwd']==str(root);self.turns+=1
                calls=[ProviderCall(type='function',call_id='write',name='write_file',arguments={'path':'result.txt','content':'Cloud runner used successfully'})] if self.turns==1 else []
                return ProviderTurn('turn-'+str(self.turns),'Completed on cloud runner' if not calls else '',calls,{})
        model=Provider();state.agent._adapter=lambda _:model
        review=await service.preview(principal,launch_plan(kind,account['id']))
        d=service.launch(principal,review['id']);await settle(service)
        ready=service.row(principal.id,d['id']);assert ready['status']=='ready',ready
        task=await agents.create_task(principal,ready['runner_id'],'api','Write result.txt',model='model-a',request_id='cloud-real-task',limits={'max_seconds':30})
        async with asyncio.timeout(40):
            while agents.workers:
                live=state.agent.store.get_task(task['id'],include_events=True)
                for approval in live['approvals']:
                    if approval['status']=='pending':await agents.resolve_approval(task['id'],approval['id'],'approved')
                await asyncio.sleep(.05)
        assert state.agent.store.get_task(task['id'])['status']=='completed'
        assert (root/'result.txt').read_text()=='Cloud runner used successfully'
        await service.request_cleanup(principal.id,d['id']);await settle(service)
        assert service.row(principal.id,d['id'])['status']=='deleted'
        assert not provider.rows
        await close(service,agents,state)
    asyncio.run(run())
