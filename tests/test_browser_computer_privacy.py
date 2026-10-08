import asyncio
import pytest
from termx.browser.service import BrowserService
from termx.desktop.recording import WindowRecordingService,assert_agent_capture_allowed,capture_privacy_revision
from termx.agent.computer import ComputerController

def test_browser_and_window_private_intervals_overlap_and_revoke_independently(tmp_path):
    from test_window_recording import Port
    live=True
    browser=BrowserService(tmp_path/'broker',session_valid=lambda p,s,v:live and s=='current-sid' and v==4)
    browser.records.put('tab','tab',{'id':'tab','principal_id':'owner','project_id':'project','profile_id':'profile','session_id':'old-sid','url':'about:blank','state':'human','lease_revision':1,'grant_id':None,'recording':False})
    windows=WindowRecordingService(browser.records,port=Port(),session_valid=lambda *_:True)
    capture=windows.start('owner','window-sid',2,'1')
    try:
        baseline=capture_privacy_revision()
        browser.takeover('tab','owner',private=True,session_id='current-sid',policy_version=4)
        with pytest.raises(PermissionError):assert_agent_capture_allowed()
        windows.configure(capture['id'],'owner',private=True)
        browser.takeover('tab','owner')
        with pytest.raises(PermissionError):assert_agent_capture_allowed() # Window privacy is independent.
        windows.configure(capture['id'],'owner',private=False)
        assert capture_privacy_revision()>baseline
        browser.takeover('tab','owner',private=True,session_id='current-sid',policy_version=4)
        with pytest.raises(PermissionError):assert_agent_capture_allowed()
        live=False
        with pytest.raises(PermissionError):capture_privacy_revision() # Another human viewer may still show private pixels.
        asyncio.run(browser.close());closed=capture_privacy_revision();assert closed>baseline
        asyncio.run(browser.close());assert capture_privacy_revision()==closed
    finally:
        windows.close();asyncio.run(browser.close())

def test_browser_private_round_trip_discards_inflight_computer_frame_and_blocks_input(tmp_path,monkeypatch):
    from termx.agent import computer
    browser=BrowserService(tmp_path)
    browser.records.put('tab','tab',{'id':'tab','principal_id':'owner','project_id':'project','profile_id':'profile','session_id':'sid','url':'about:blank','state':'human','lease_revision':1,'grant_id':None,'recording':False})
    def capture(*_):
        browser.takeover('tab','owner',private=True)
        browser.takeover('tab','owner')
        return b'PRIVATE_FRAME_MUST_NOT_RETURN'
    monkeypatch.setattr(computer,'grab_jpeg',capture)
    effects=[];monkeypatch.setattr(computer,'apply_event',lambda event,*_:effects.append(event))
    async def run():
        controller=ComputerController()
        try:
            with pytest.raises(PermissionError,match='in flight'):await controller.screenshot()
            browser.takeover('tab','owner',private=True)
            with pytest.raises(PermissionError,match='paused'):await controller.execute([{'type':'type','text':'NEVER_TYPE'}])
            assert all(row['type']=='release_all' for row in effects)
        finally:await browser.close()
        assert_agent_capture_allowed()
    asyncio.run(run())
