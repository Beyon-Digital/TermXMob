import asyncio
import pytest
from termx.browser.service import BrowserService
from termx.desktop.recording import WindowRecordingService,assert_agent_capture_allowed,capture_privacy_revision

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
