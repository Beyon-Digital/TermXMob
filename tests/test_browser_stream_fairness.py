import asyncio
import base64
import os
from time import monotonic

import pytest
from termx.browser.service import BrowserService


def test_human_viewers_share_one_capture_and_cancellation_does_not_cancel_other_viewer(tmp_path):
    async def run():
        service=BrowserService(tmp_path)
        try:
            tab={'id':'tab','principal_id':'owner','project_id':'project','profile_id':'profile','session_id':'sid','url':'https://example.com','state':'human','document_revision':1,'lease_revision':1,'grant_id':None,'recording':False}
            service.records.put('tab','tab',tab);service._pages['tab']=object()
            started=asyncio.Event();release=asyncio.Event()
            class Capture:
                calls=0
                async def send(self,method,args):
                    assert method=='Page.captureScreenshot'
                    self.calls+=1;started.set();await release.wait()
                    return {'data':base64.b64encode(b'human-frame').decode()}
            capture=Capture();service._capture_cdp['tab']=capture
            first=asyncio.create_task(service.frame('tab','owner',human=True));await started.wait()
            second=asyncio.create_task(service.frame('tab','owner',human=True));await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):await first
            release.set()
            assert await second==b'human-frame'
            assert await service.frame('tab','owner',human=True)==b'human-frame'
            assert capture.calls==1
            service.takeover('tab','owner',private=True)
            assert 'tab' not in service._frame_cache
        finally:
            await service.close()
    asyncio.run(run())


@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_real_two_viewer_streams_do_not_starve_scoped_controls(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        viewers=[];done=asyncio.Event();counts=[0,0];durations=[]
        try:
            profile=service.create_profile('owner','project','Fixture')
            tab=await service.create_tab('owner','sid',profile['id'],site)
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','capture','scroll'],policy_version=1)
            async def viewer(index):
                while not done.is_set():
                    frame=await service.frame(tab['id'],'owner',human=True)
                    assert frame[:2]==b'\xff\xd8';counts[index]+=1
                    await asyncio.sleep(.02)
            viewers=[asyncio.create_task(viewer(index)) for index in range(2)]
            for index in range(15):
                current=service.get(tab['id'],'owner');start=monotonic()
                await asyncio.wait_for(service.action(tab['id'],'owner',session_id='sid',run_id='task',grant_id=grant['grant']['id'],action_id='scroll-'+str(index),action='scroll',args={'x':0,'y':25},document_revision=current['document_revision'],lease_revision=current['lease_revision'],policy_version=1),2)
                durations.append(monotonic()-start);await asyncio.sleep(.05)
            assert min(counts)>=5 and max(durations)<2
            assert len([row for row in service.records.list('review') if row['status']=='completed'])==15
        finally:
            done.set()
            for viewer in viewers:viewer.cancel()
            await asyncio.gather(*viewers,return_exceptions=True)
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())
