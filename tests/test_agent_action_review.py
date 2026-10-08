import asyncio
from pathlib import Path
from time import time
from termx.browser.service import BrowserService
from termx.auto_review import ReviewVerdict

def test_thirty_typed_writes_reviewed_without_repeated_user_prompts_and_hard_deny(tmp_path):
    from test_agent import _FanOutAdapter,_fn,build_manager,wait_for_status
    async def run():
        adapter=_FanOutAdapter({'Edit code':([[ _fn('w'+str(i),'write_file',path=f'file{i}.py',content=f'VALUE = {i}\n') for i in range(30) ]],0.)})
        manager,store=build_manager(tmp_path,adapter)
        service=BrowserService(tmp_path/'browser',session_valid=lambda p,s,v:p=='owner' and s=='sid' and v==1)
        class Reviewer:
            version='evaluated-small-v1'
            def __init__(self):self.calls=0
            async def evaluate(self,action,context):
                self.calls+=1
                assert action.intended_effect=='edit' and context['task_summary']=='Edit code'
                return ReviewVerdict('ALLOW','aligned','Typed project edit',self.version,time()+15)
        reviewer=Reviewer();service.review.reviewer=reviewer;manager.browser=service
        try:
            task=await manager.create_task(prompt='Edit code',cwd=str(tmp_path),provider_id='fake',limits={'max_steps':32},on_created=lambda id:service.records.put('agent-task-authority',id,{'id':id,'principal_id':'owner','session_id':'sid','project_id':'project','policy_version':1}))
            await manager.resolve_approval(task['id'],task['approvals'][0]['id'],'approved')
            # This performs thirty real files and durable review/audit commits;
            # the single-effect helper's 3s deadline is not an IO budget.
            await wait_for_status(store,task['id'],'completed',timeout=30)
            assert reviewer.calls==30 and all((tmp_path/f'file{i}.py').read_text()==f'VALUE = {i}\n' for i in range(30))
            assert not [a for a in store.approvals(task['id']) if a['kind']=='tool']
            assert len([r for r in service.records.list('review') if r['status']=='completed'])==30
            # The same qualified model cannot approve a protected file target.
            from termx.agent.action_review import proposal
            from termx.auto_review import ActionBlocked
            binding=service.records.get('agent-task-authority',task['id'])
            envelope,validate,hard=proposal(service,task['id'],binding,'write_file',{'path':'.env','content':'TOKEN=must-not-leak'},str(tmp_path),call_id='protected')
            try:await service.review.authorize(envelope,validate=validate,hard_deny=hard)
            except ActionBlocked:pass
            else:raise AssertionError('protected file permitted')
            assert reviewer.calls==30 and 'must-not-leak' not in service.records.path.read_bytes().decode(errors='ignore')
        finally:await manager.close();store.close()
    asyncio.run(run())

def test_unknown_shell_parks_exact_human_approval_and_revocation_blocks_execution(tmp_path):
    from test_agent import _FanOutAdapter,_fn,build_manager,wait_for_pending_approval,wait_for_status
    async def run():
        adapter=_FanOutAdapter({'Run command':([[_fn('command','run_shell',command='printf done > outcome.txt',purpose='Write a local marker')]],0.)})
        manager,store=build_manager(tmp_path,adapter);live=True
        service=BrowserService(tmp_path/'browser',session_valid=lambda *_:live);manager.browser=service
        try:
            task=await manager.create_task(prompt='Run command',cwd=str(tmp_path),provider_id='fake',on_created=lambda id:service.records.put('agent-task-authority',id,{'id':id,'principal_id':'owner','session_id':'sid','project_id':'project','policy_version':1}))
            await manager.resolve_approval(task['id'],task['approvals'][0]['id'],'approved')
            _,approval=await wait_for_pending_approval(store,task['id'],'tool')
            assert not (tmp_path/'outcome.txt').exists() and approval['payload']['browser_review']['effect']=='unknown'
            live=False
            await manager.resolve_approval(task['id'],approval['id'],'approved')
            await wait_for_status(store,task['id'],'completed')
            assert not (tmp_path/'outcome.txt').exists()
        finally:await manager.close();store.close()
    asyncio.run(run())

def test_model_pin_change_after_permit_parks_exact_internal_write_then_resumes_once(tmp_path):
    from test_agent import _FanOutAdapter,_fn,build_manager,wait_for_pending_approval,wait_for_status
    async def run():
        adapter=_FanOutAdapter({'Edit code':([[_fn('exact-write','write_file',path='once.py',content='VALUE = 1\n')]],0.)})
        manager,store=build_manager(tmp_path,adapter);service=BrowserService(tmp_path/'browser');manager.browser=service
        class Reviewer:
            version='qualified-v1';expected_model='snapshot-A';calls=0
            async def evaluate(self,*_):
                self.calls+=1;return ReviewVerdict('ALLOW','aligned','Typed edit',self.version,time()+15)
        reviewer=Reviewer();service.review.reviewer=reviewer;original=service.review.authorize
        async def swap(*args,**kwargs):
            row=await original(*args,**kwargs)
            if row.get('decision_source')=='model':reviewer.expected_model='snapshot-B'
            return row
        service.review.authorize=swap
        try:
            task=await manager.create_task(prompt='Edit code',cwd=str(tmp_path),provider_id='fake',on_created=lambda id:service.records.put('agent-task-authority',id,{'id':id,'principal_id':'owner','session_id':'sid','project_id':'project','policy_version':1}))
            await manager.resolve_approval(task['id'],task['approvals'][0]['id'],'approved')
            _,approval=await wait_for_pending_approval(store,task['id'],'tool')
            assert not (tmp_path/'once.py').exists() and approval['payload']['browser_review']['status']=='needs_user'
            await manager.resolve_approval(task['id'],approval['id'],'approved');await wait_for_status(store,task['id'],'completed')
            assert (tmp_path/'once.py').read_text()=='VALUE = 1\n' and reviewer.calls==1
            assert len([row for row in service.records.list('review') if row['status']=='completed'])==1
        finally:await manager.close();store.close()
    asyncio.run(run())


def test_structured_credential_content_is_bounded_and_never_blanket_eligible():
    import json
    from termx.agent.action_review import contains_credentials
    assert contains_credentials({'content':json.dumps({'nested':[{'password':'private-fixture-value'}]})})
    assert contains_credentials({'content':json.dumps(json.dumps({'password':'private-fixture-value'}))})
    assert contains_credentials({'metadata':{'authorization':'Bearer private-fixture-value'}})
    assert contains_credentials({'patch':'+CONFIG = {"api_key": "private-fixture-value"}\n'})
    assert contains_credentials({'content':'-----BEGIN PRIVATE KEY-----\nfixture'})
    assert contains_credentials({'content':'x'*(256*1024+1)})
    nested='safe'
    for _ in range(18):nested=[nested]
    assert contains_credentials({'content':nested})
    assert not contains_credentials({'content':'VALUE = 1\n','token_id':'safe-reference','metadata':{'password_hint':'public hint'}})


def test_structured_credentials_require_once_only_human_decisions_without_reviewer_or_remembered_allow(tmp_path):
    import json
    from test_agent import _FanOutAdapter,_fn,build_manager,wait_for_pending_approval,wait_for_status
    async def run():
        content=json.dumps({'connection':{'password':'private-fixture-value'}})
        adapter=_FanOutAdapter({'Exact credential write':([[_fn('first','write_file',path='settings.json',content=content)],[_fn('second','write_file',path='settings.json',content=content)]],0.)})
        manager,store=build_manager(tmp_path,adapter)
        service=BrowserService(tmp_path/'browser',session_valid=lambda *_:True);manager.browser=service
        class Reviewer:
            version='fixture-must-not-run';calls=0
            async def evaluate(self,*_):
                self.calls+=1
                raise AssertionError('Credential content must bypass the model')
        reviewer=Reviewer();service.review.reviewer=reviewer
        # Even a previously owner-scoped exact allow cannot bypass the gate.
        from termx.auto_review import canonical_hash
        digest=canonical_hash({'tool':'write_file','cwd':str(tmp_path),'arguments':{'path':'settings.json','content':content}})
        bound={'principal_id':'owner','session_id':'sid','policy_version':1,'conversation_id':'conversation'}
        store.create_policy_rule(effect='allow',scope_type='conversation',scope_id='conversation',action_type='tool',tool='write_file',fingerprint=digest,consent_binding=bound,sandbox_profile='agent',expires_at=time()+300)
        try:
            task=await manager.create_task(prompt='Exact credential write',cwd=str(tmp_path),provider_id='fake',on_created=lambda id:service.records.put('agent-task-authority',id,{'id':id,'project_id':'project',**bound}))
            await manager.resolve_approval(task['id'],task['approvals'][0]['id'],'approved')
            _,first=await wait_for_pending_approval(store,task['id'],'tool')
            assert first['payload']['remember_options']==[] and not (tmp_path/'settings.json').exists() and reviewer.calls==0
            assert store.list_policy_rules()[0]['times_used']>0, 'The matching allow must be overridden by credential review'
            assert tuple(first['payload']['browser_review']['envelope']['data_labels'])==('secret',)
            try:await manager.resolve_approval(task['id'],first['id'],'approved',remember='project')
            except ValueError:pass
            else:raise AssertionError('Sensitive approval advertised blanket consent')
            assert store.get_approval(first['id'])['status']=='pending'
            await manager.resolve_approval(task['id'],first['id'],'approved')
            _,second=await wait_for_pending_approval(store,task['id'],'tool')
            assert second['id']!=first['id'] and second['payload']['remember_options']==[] and reviewer.calls==0
            assert (tmp_path/'settings.json').read_text()==content
            await manager.resolve_approval(task['id'],second['id'],'denied')
            await wait_for_status(store,task['id'],'cancelled')
            assert len([event for event in store.events(task['id']) if event['type']=='tool.finished' and not event['payload'].get('result',{}).get('refused')])==1
            assert len(store.list_policy_rules())==1
            assert 'private-fixture-value' not in service.records.path.read_bytes().decode(errors='ignore')
        finally:
            await manager.close();await service.close();store.close()
    asyncio.run(run())
