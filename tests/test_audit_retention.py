from datetime import datetime,timedelta,timezone
import json
import asyncio
from pathlib import Path
import pytest
from termx.audit import log_event,read_events,retention_policy,set_retention_policy,prune_events
from termx.config import config_dir
from termx.authorization import ROLES
from test_authorization import client_fixture,PASSWORD


def test_bounded_audit_retention_expiration_redaction_and_stream_query(monkeypatch):
    path=config_dir()/'audit.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
    old=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat();now=datetime.now(timezone.utc).isoformat()
    with path.open('wb') as output:
        output.write((json.dumps({'ts':old,'kind':'expired'})+'\n').encode())
        output.write(b'x'*70000+b'\n')
        for i in range(110):output.write((json.dumps({'ts':now,'kind':'retained','seq':i,'nested':{'password':'never-retain'}})+'\n').encode())
    policy={'retention_days':1,'max_bytes':65536,'max_events':100}
    assert set_retention_policy(policy)==retention_policy()==policy
    result=prune_events()
    assert result['removed_events']==12 and result['retained_events']==100
    def no_whole_file(*args,**kwargs):raise AssertionError('Audit query must not read the entire file into memory')
    monkeypatch.setattr(Path,'read_text',no_whole_file)
    latest=read_events(2);assert [item['seq'] for item in latest]==[108,109]
    assert all(not row.get('nested',{}).get('password') for row in latest)
    log_event('new',password='password-secret',token='raw-token',token_id='safe-reference',nested={'Authorization':'Bearer never-write'},url='https://user:pass@example.com/a?access_token=secret&safe=ok#token')
    assert len(read_events(-1))==100
    assert path.stat().st_size<=policy['max_bytes']
    last=read_events(1)[0]
    assert last['token_id']=='safe-reference' and last['nested']=={} and last['url']=='https://example.com/a?safe=ok'
    assert 'password' not in last and 'token' not in last


def test_admin_retention_mutations_require_current_managed_authority(tmp_path):
    identity,state,client,admin=client_fixture(tmp_path)
    user=identity.create_local_user('audit-viewer',PASSWORD,list(ROLES['viewer']));state.authorization.set_role(user.id,'viewer')
    cred=asyncio.run(identity.login('local-password',{'username':'audit-viewer','password':PASSWORD},peer='local'))
    denied={'Authorization':'Bearer '+cred.access_token}
    route='/auth/admin/audit/retention'
    assert client.get(route,headers=denied).status_code==403
    policy={'retention_days':30,'max_bytes':65536,'max_events':100}
    assert client.put(route,headers=denied,json=policy).status_code==403
    saved=client.put(route,headers=admin,json=policy);assert saved.status_code==200,saved.text
    assert client.get(route,headers=admin).json()==policy
    assert client.post('/auth/admin/audit/prune',headers=admin,json={}).status_code==200
    assert any(row['kind']=='audit_retention_changed' for row in read_events())
    assert client.put(route,headers=admin,json={**policy,'retention_days':0}).status_code==422
    for bad in ({**policy,'retention_days':True},{**policy,'max_bytes':True},{**policy,'max_events':100001}):
        assert client.put(route,headers=admin,json=bad).status_code==422
        with pytest.raises(ValueError):set_retention_policy(bad)


def test_audit_private_replace_failure_closes_descriptor_and_keeps_existing_log(monkeypatch):
    import os
    import termx.audit as audit
    import termx.private_files as private
    path=config_dir()/'audit.jsonl'
    log_event('existing')
    before=path.read_bytes()
    opened=[]
    original_temp=audit.tempfile.mkstemp
    original_protect=private.protect_private_path
    def temporary(*args,**kwargs):
        result=original_temp(*args,**kwargs);opened.append(result);return result
    def deny_temporary(candidate,*args,**kwargs):
        if candidate.name.startswith('.audit-'):raise PermissionError('Controlled private ACL refusal')
        return original_protect(candidate,*args,**kwargs)
    monkeypatch.setattr(audit.tempfile,'mkstemp',temporary)
    monkeypatch.setattr(private,'protect_private_path',deny_temporary)
    with pytest.raises(PermissionError):prune_events()
    assert path.read_bytes()==before
    assert opened and not Path(opened[0][1]).exists()
    with pytest.raises(OSError):os.fstat(opened[0][0])


def test_audit_deep_malformed_record_and_cyclic_metadata_do_not_break_bounded_operations():
    path=config_dir()/'audit.jsonl'
    log_event('first')
    with path.open('ab') as target:target.write(b'['*2000+b'0'+b']'*2000+b'\n')
    cycle={};cycle['again']=cycle;cycle['password']='must-not-persist'
    log_event('cyclic',nested=cycle)
    assert [row['kind'] for row in read_events()]==['first','cyclic']
    result=prune_events()
    assert result['removed_events']==1 and result['retained_events']==2
    assert b'must-not-persist' not in path.read_bytes()
