import asyncio,os
import pytest
from termx.browser.service import BrowserService
from termx.browser.tools import BrowserToolAdapter
from termx.auto_review import ReviewRequired

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_real_tab_lifecycle_requires_explicit_scope_inherits_no_more_and_consumes_once(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site=await website();other,foreign=await website();live=True
        service=BrowserService(tmp_path,development_origins=[site,foreign],session_valid=lambda *_:live)
        adapter=BrowserToolAdapter(service)
        try:
            profile=service.create_profile('owner','project','Lifecycle fixture');tab=await service.create_tab('owner','sid',profile['id'],site)
            permissions=await service._pages[tab['id']].evaluate("async()=>({microphone:(await navigator.permissions.query({name:'microphone'})).state,camera:(await navigator.permissions.query({name:'camera'})).state})")
            assert permissions=={'microphone':'denied','camera':'denied'}
            basic=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','navigate'])
            def args(tab,url=None):return {'tab_id':tab['id'],'document_revision':tab['document_revision'],'lease_revision':tab['lease_revision'],**({'url':url} if url else {})}
            def trusted(call):return dict(task_id='task',principal_id='owner',session_id='sid',call_id=call,authority=lambda:live)
            with pytest.raises(PermissionError):await adapter.execute('browser_open_tab',args(basic['tab'],site+'/next'),**trusted('no-scope'))
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','navigate','open_tab','close_tab'],policy_version=1)
            with pytest.raises(PermissionError):await adapter.execute('browser_open_tab',args(grant['tab'],foreign),**trusted('other-origin'))
            opened=await adapter.execute('browser_open_tab',args(grant['tab'],site+'/next'),**trusted('open-once'))
            child=opened['tab'];child_grant=service.records.get('grant',child['grant_id'])
            assert child['state']=='agent' and child['profile_id']==tab['profile_id']
            assert all(child_grant[key]==grant['grant'][key] for key in ('principal_id','session_id','project_id','origins','actions','run_id','policy_version'))
            assert child_grant['expires_at']<=grant['grant']['expires_at']
            with pytest.raises(ReviewRequired):await adapter.execute('browser_open_tab',args(grant['tab'],site+'/next'),**trusted('open-once'))
            assert len([t for t in service.tabs('owner') if t['state']=='agent'])==2
            with pytest.raises(ReviewRequired):await adapter.execute('browser_close_tab',args(child),**trusted('close-once'))
            assert not service._pages[child['id']].is_closed()
            service.review.decide('close-once',principal_id='owner',approve=True)
            assert (await adapter.execute('browser_close_tab',args(child),**trusted('close-once')))['closed']==child['id']
            assert service.get(child['id'],'owner')['state']=='closed' and service.records.get('review','close-once')['status']=='completed'
            with pytest.raises(PermissionError):await adapter.execute('browser_close_tab',args(child),**trusted('close-once'))
            service.takeover(tab['id'],'owner',private=True)
            with pytest.raises(PermissionError):await adapter.execute('browser_open_tab',args(grant['tab'],site),**trusted('private'))
            assert len(service.records.list('review'))==2
        finally:await service.close();server.close();other.close();await server.wait_closed();await other.wait_closed()
    asyncio.run(run())
