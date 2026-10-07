import asyncio
import io
import json
import os
import tarfile
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from termx.runners import RunnerService

def test_archive_security_and_request_dedup(tmp_path):
    service=RunnerService(tmp_path,SimpleNamespace(get=lambda _: 'private-fixture'))
    config={'context':'test','policy_version':1}
    with service.db() as db:
        db.execute('INSERT INTO runners VALUES(?,?,?,?,?,?,?)',('runner','alice','project',json.dumps(config),'container','ready',9999999999))
    calls=[]
    async def docker(config,*args,**kwargs):
        calls.append(args)
        if args[0]=='exec' and '/bin/sh' in args:return b'private-fixture\nTERMX_EXIT:0\n'
        return b''
    service.docker=docker
    async def run():
        with pytest.raises(HTTPException):service.row('bob','runner')
        payload={'request_id':'request-123','argv':['echo','test'],'seconds':5,'secrets':{'TERMX_SECRET_TOKEN':'ref'}}
        first=await service.run('alice','runner',payload)
        await asyncio.gather(*service.workers.values())
        duplicate=await service.run('alice','runner',payload)
        assert duplicate['id']==first['id'] and duplicate['status']=='completed'
        assert duplicate['result']['stdout']=='[REDACTED]'
        assert 'private-fixture' not in service.path.read_bytes().decode(errors='ignore')
        with pytest.raises(HTTPException):await service.run('alice','runner',{**payload,'argv':['other']})
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w') as archive:
            member=tarfile.TarInfo('../escape');member.size=1;archive.addfile(member,io.BytesIO(b'x'))
        with pytest.raises(HTTPException):await service.upload('alice','runner',buffer.getvalue())
        with pytest.raises(HTTPException):await service.results('alice','runner','../escape')
        await service.close()
    asyncio.run(run())
    assert sum('tar' in call for call in calls)==1

@pytest.mark.skipif(os.environ.get('TERMX_TEST_DOCKER')!='1',reason='Explicit real Docker validation')
def test_real_dedicated_container_upload_secret_results_budget_teardown(tmp_path):
    service=RunnerService(tmp_path,SimpleNamespace(get=lambda _: 'scoped-secret-fixture'))
    async def run():
        runner=await service.provision('alice','project',{'image':'alpine:latest','lease_seconds':60,'network':'none'})
        try:
            buffer=io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w') as archive:
                raw=b'input data';member=tarfile.TarInfo('input.txt');member.size=len(raw);archive.addfile(member,io.BytesIO(raw))
            await service.upload('alice',runner['id'],buffer.getvalue())
            payload={'request_id':'real-run-123','argv':['sh','-c','cat input.txt > result.txt; echo "$TERMX_SECRET_TOKEN"; id; test ! -e /var/run/docker.sock'],
                     'seconds':10,'secrets':{'TERMX_SECRET_TOKEN':'secret-ref'}}
            job=await service.run('alice',runner['id'],payload)
            await asyncio.gather(*service.workers.values())
            job=service.jobs('alice',runner['id'])[0]
            assert job['status']=='completed',job
            assert '[REDACTED]' in job['result']['stdout'] and 'uid=65534' in job['result']['stdout']
            raw=await service.results('alice',runner['id'],'result.txt')
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:assert archive.extractfile('result.txt').read()==b'input data'
            await service.run('alice',runner['id'],{'request_id':'budget-run-123','argv':['sleep','10'],'seconds':1})
            await asyncio.gather(*service.workers.values())
            assert service.jobs('alice',runner['id'])[0]['status']=='budget-stopped'
            assert service.row('alice',runner['id'])['status']=='stopped'
        finally:
            await service.stop('alice',runner['id'],teardown=True)
            await service.close()
    asyncio.run(run())
