"""Representative real 30-action browser task; fixture reviewer, no paid calls."""
import asyncio,json,os
from pathlib import Path
from time import time
import pytest
from termx.auto_review import ReviewVerdict
from termx.browser.service import BrowserService

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_thirty_scoped_safe_browser_actions_audit_rule_and_fixture_model_decisions(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site]);calls=[]
        class Reviewer:
            version='fixture-small-reviewer-v1';provider_id='fixture-no-network';model='fixture'
            async def evaluate(self,envelope,context):
                calls.append(envelope.action_id)
                assert envelope.intended_effect=='edit'
                return ReviewVerdict('ALLOW','fixture_safe_edit','Routine field edit',self.version,time()+15)
        service.review.reviewer=Reviewer()
        try:
            profile=service.create_profile('owner','project','Thirty action isolation')
            tab=await service.create_tab('owner','sid',profile['id'],site)
            grant=await service.handoff(tab['id'],'owner','sid',run_id='thirty-action-task',origins=[site],actions=['observe','scroll','find','zoom','type','capture'],policy_version=1)
            operations=[('observe',{}),('scroll',{'y':25}),('find',{'text':'Public context'}),('zoom',{'zoom':1}),('type',{'selector':'#draft','text':'Fixture draft'}),('capture',{})]*5
            for index,(action,args) in enumerate(operations):
                current=service.get(tab['id'],'owner')
                await service.action(tab['id'],'owner',session_id='sid',run_id='thirty-action-task',grant_id=grant['grant']['id'],action_id='safe-'+str(index),action=action,args=args,document_revision=current['document_revision'],lease_revision=current['lease_revision'],policy_version=1)
            rows=service.records.list('review');audit=service.records.events(1000)
            assert len(rows)==30 and all(row['status']=='completed' for row in rows)
            rules=sum(row['reviewer_version']=='host-policy-v1' for row in rows)
            model=sum(row['reviewer_version']==Reviewer.version for row in rows)
            assert rules==25 and model==5 and len(calls)==5
            assert len([event for event in audit if event.get('decision')=='ALLOW'])==30
            assert not any(row['status']=='needs_user' for row in rows)
            proof={'actions':30,'completed':30,'human_prompts':0,'rule_based_approvals':rules,'fixture_model_based_approvals':model,'fixture_reviewer_calls':len(calls),'paid_provider_queries':0,'production_reviewer_qualified':False,'audited_decisions':30,'rendered_final_input':await service._pages[tab['id']].locator('#draft').input_value()}
            destination=os.environ.get('TERMX_BROWSER_REVIEW_PROOF')
            if destination:Path(destination).write_text(json.dumps(proof,indent=2))
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_renderer_crash_reopens_human_tab_and_never_revives_old_grant(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site]);crash=None
        try:
            profile=service.create_profile('owner','project','Crash fixture')
            tab=await service.create_tab('owner','sid',profile['id'],site)
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe'])
            # Page.crash deliberately destroys its command target. Keep and
            # drain that pending command when its browser closes.
            crash=asyncio.create_task(service._capture_cdp[tab['id']].send('Page.crash'))
            for _ in range(40):
                if service.get(tab['id'],'owner')['state']=='crashed':break
                await asyncio.sleep(.025)
            assert service.get(tab['id'],'owner')['state']=='crashed'
            assert service.records.get('grant',grant['grant']['id'])['revoked']
            restored=await service.recover_tab(tab['id'],'owner','sid')
            assert restored['id']!=tab['id'] and restored['state']=='human' and restored['grant_id'] is None
            assert (await service.recover_tab(tab['id'],'owner','sid'))['id']==restored['id']
            with pytest.raises(PermissionError):await service.observe(restored['id'],'owner',grant_id=grant['grant']['id'],run_id='task')
            assert (await service.frame(restored['id'],'owner',human=True))[:2]==b'\xff\xd8'
        finally:
            await service.close()
            if crash:await asyncio.gather(crash,return_exceptions=True)
            server.close();await server.wait_closed()
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_explicit_reopen_recreates_closed_profile_context_without_restoring_grant(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        try:
            profile=service.create_profile('owner','project','Context failure fixture')
            tab=await service.create_tab('owner','sid',profile['id'],site)
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe'])
            previous=service._contexts[profile['id']]
            await previous.close()  # Actual browser context ends outside user tab-close flow.
            assert service.get(tab['id'],'owner')['state']=='crashed'
            assert service.records.get('grant',grant['grant']['id'])['revoked']
            recovered=await service.recover_tab(tab['id'],'owner','sid')
            assert service._contexts[profile['id']] is not previous
            assert recovered['state']=='human' and recovered['grant_id'] is None
            assert (await service.frame(recovered['id'],'owner',human=True))[:2]==b'\xff\xd8'
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_first_internal_observe_parks_and_exact_call_resumes_after_handoff(tmp_path):
    from time import monotonic
    from test_managed_browser import website
    from termx.agent.manager import AgentManager
    from termx.agent.providers import ProviderCall
    from termx.agent.secrets import CredentialStore
    from termx.agent.store import AgentStore,ACTIVE_STATUSES
    async def run():
        server,site=await website();service=BrowserService(tmp_path/'broker',development_origins=[site]);store=AgentStore(tmp_path/'agent.sqlite3',tmp_path/'artifacts');manager=AgentManager(store,CredentialStore({}),None);manager.browser=service
        service.task_live=lambda task_id:(store.get_task(task_id) or {}).get('status') in ACTIVE_STATUSES-{'cancelling'}
        operation=None
        try:
            task=store.create_task(prompt='Observe after explicit handoff',cwd=str(tmp_path),provider_id='fixture-no-provider',model='fixture',limits={'max_steps':10,'max_seconds':60},status='running')
            profile=service.create_profile('owner','project','First call fixture');tab=await service.create_tab('owner','sid',profile['id'],site)
            operation=asyncio.create_task(manager._run_calls(task['id'],[ProviderCall('function','original-observe','browser_observe',{'tab_id':tab['id']})],[],0,monotonic()))
            for _ in range(100):
                if store.get_task(task['id'])['status']=='paused':break
                await asyncio.sleep(.02)
            assert store.get_task(task['id'])['status']=='paused' and not operation.done()
            assert store.approvals(task['id'])==[]
            await service.handoff(tab['id'],'owner','sid',run_id=task['id'],origins=[site],actions=['observe'])
            paused,history,step=await asyncio.wait_for(operation,3)
            assert not paused and step==1 and 'Task page' in str(history)
            assert store.get_task(task['id'])['status']=='running'
            events=store.events(task['id'])
            assert sum(event['type']=='task.handoff.required' for event in events)==1
            assert sum(event['type']=='task.handoff.resumed' for event in events)==1
            assert sum(event['type']=='tool.finished' and event['payload'].get('call_id')=='original-observe' for event in events)==1
        finally:
            if operation and not operation.done():operation.cancel();await asyncio.gather(operation,return_exceptions=True)
            await service.close();server.close();await server.wait_closed();store.close()
    asyncio.run(run())

@pytest.mark.parametrize('terminal',['completed','cancelled','failed'])
@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_terminal_task_cannot_gain_retain_or_execute_browser_control(tmp_path,terminal):
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site]);status=['running'];service.task_live=lambda task:status[0]=='running'
        try:
            profile=service.create_profile('owner','project','Task end fixture');tab=await service.create_tab('owner','sid',profile['id'],site)
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','scroll'])
            status[0]=terminal
            with pytest.raises(PermissionError):await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe'])
            with pytest.raises(PermissionError):await service.action(tab['id'],'owner',session_id='sid',run_id='task',grant_id=grant['grant']['id'],action_id='late',action='scroll',args={'y':25},document_revision=tab['document_revision'],lease_revision=grant['tab']['lease_revision'])
            for _ in range(30):
                if service.get(tab['id'],'owner')['state']=='human':break
                await asyncio.sleep(.025)
            assert service.get(tab['id'],'owner')['state']=='human'
            assert service.records.get('grant',grant['grant']['id'])['revoked']
            assert service.records.get('review','late') is None
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())
