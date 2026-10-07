import asyncio
import pytest
from termx.browser.service import BrowserService

@pytest.mark.parametrize('capture',[False,True])
def test_takeover_while_observation_in_flight_never_returns_private_context(tmp_path,capture):
    async def run():
        service=BrowserService(tmp_path)
        try:
            tab={'id':'tab','principal_id':'owner','project_id':'project','profile_id':'profile','session_id':'sid','url':'https://example.com','title':'Public','state':'agent','document_revision':1,'lease_revision':1,'grant_id':'grant','recording':False}
            grant={'id':'grant','tab_id':'tab','principal_id':'owner','session_id':'sid','project_id':'project','run_id':'task','profile_id':'profile','origins':['https://example.com'],'actions':['observe','capture'],'expires_at':9999999999,'lease_revision':1,'policy_version':0,'revoked':False}
            service.records.put('tab','tab',tab);service.records.put('grant','grant',grant)
            class Page:
                def is_closed(self):return False
                def locator(self,_):return object()
                async def evaluate(self,_):
                    service.takeover('tab','owner',private=True)
                    return [{'name':'private in-flight response'}]
                async def screenshot(self,**_):
                    service.takeover('tab','owner',private=True)
                    return b'private in-flight response'
            service._pages['tab']=Page()
            operation=service.frame if capture else service.observe
            with pytest.raises(PermissionError):await operation('tab','owner',grant_id='grant',run_id='task')
            assert 'private in-flight response' not in service.records.path.read_bytes().decode(errors='ignore')
        finally:
            await service.close()
    asyncio.run(run())

def test_handoff_cannot_overwrite_a_private_takeover_while_title_is_in_flight(tmp_path):
    async def run():
        service=BrowserService(tmp_path,development_origins=['http://127.0.0.1:9999'])
        try:
            tab={'id':'tab','principal_id':'owner','project_id':'project','profile_id':'profile','session_id':'sid','url':'http://127.0.0.1:9999','title':'Public','state':'human','document_revision':1,'lease_revision':1,'grant_id':None,'recording':False}
            service.records.put('tab','tab',tab)
            class Page:
                url='http://127.0.0.1:9999'
                async def title(self):
                    service.takeover('tab','owner',private=True)
                    return 'PRIVATE_TITLE_NEVER_PERSIST'
            service._pages['tab']=Page()
            with pytest.raises(PermissionError,match='changed during handoff'):
                await service.handoff('tab','owner','sid',run_id='task',origins=[Page.url],actions=['observe'])
            current=service.get('tab','owner')
            assert current['state']=='private' and current['grant_id'] is None and current['lease_revision']==3
            assert not service.records.list('grant')
            assert 'PRIVATE_TITLE_NEVER_PERSIST' not in service.records.path.read_bytes().decode(errors='ignore')
        finally:
            await service.close()
    asyncio.run(run())
