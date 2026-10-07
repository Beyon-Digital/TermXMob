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
            await wait_for_status(store,task['id'],'completed')
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
