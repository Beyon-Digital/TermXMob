"""Controlled frames prove no publish after lock/revoke/private capture races."""
import asyncio
from types import SimpleNamespace
import pytest
import termx.desktop.webrtc as rtc
from test_webrtc import RealBackend


def test_rtc_owned_device_cannot_ice_replace_or_close_foreign_track():
    backend=RealBackend();manager=rtc.RtcManager(backend=backend)
    manager.handle_offer('one',{'sdp':'fixture'},principal_id='alice',managed_session_id='a',authorize=lambda:True)
    manager.handle_offer('two',{'sdp':'fixture'},principal_id='alice',managed_session_id='b',authorize=lambda:True)
    for action in (lambda:manager.handle_offer('one',{},principal_id='alice',managed_session_id='b',authorize=lambda:True),
                   lambda:manager.add_ice('one',{},principal_id='bob',managed_session_id='a'),
                   lambda:manager.close_owned('one',principal_id='bob',managed_session_id='a')):
        with pytest.raises(rtc.RtcError,match='another device'):action()
    assert backend.closed==[]
    manager.close_device('alice','a')
    assert backend.closed==['one'] and 'two' in manager._sessions
    manager.close_all()


@pytest.mark.parametrize('race',['lock','private','private-cleared','conversion-revoke'])
def test_screen_track_rechecks_authority_and_privacy_after_capture(monkeypatch,race):
    from termx.desktop import capture,recording
    state={'allowed':True,'epoch':0,'private':False,'captures':0,'converted':0}
    class Base:
        async def next_timestamp(self):return 1,1
        def stop(self):state['stopped']=True
    monkeypatch.setattr(rtc,'_VideoStreamTrack',Base)
    def epoch():
        if state['private']:raise PermissionError('Private fixture')
        return state['epoch']
    monkeypatch.setattr(recording,'capture_privacy_revision',epoch)
    def grab():
        state['captures']+=1
        if race=='lock':state['allowed']=False
        if race in {'private','private-cleared'}:
            state['epoch']+=1;state['private']=race=='private'
        return b'controlled-jpeg'
    monkeypatch.setattr(capture,'grab_jpeg',grab)
    def convert(raw):
        state['converted']+=1
        if race=='conversion-revoke':state['allowed']=False
        return SimpleNamespace(pts=None,time_base=None)
    monkeypatch.setattr(rtc,'_jpeg_to_video_frame',convert)
    track=rtc._screen_track(30,lambda:state['allowed'])
    with pytest.raises(rtc.RtcError):asyncio.run(track.recv())
    assert state['captures']==1 and state['stopped']
    assert state['converted']==(1 if race=='conversion-revoke' else 0)


def test_screen_track_denies_before_physical_capture(monkeypatch):
    from termx.desktop import capture
    class Base:
        async def next_timestamp(self):return 1,1
        def stop(self):pass
    monkeypatch.setattr(rtc,'_VideoStreamTrack',Base)
    monkeypatch.setattr(capture,'grab_jpeg',lambda:pytest.fail('capture occurred after revoke'))
    with pytest.raises(rtc.RtcError):asyncio.run(rtc._screen_track(authorize=lambda:False).recv())


def test_private_transition_closes_existing_peers_before_acknowledgement():
    from termx.desktop.recording import set_capture_private,clear_capture_private
    backend=RealBackend();manager=rtc.RtcManager(backend=backend)
    manager.handle_offer('a',{},principal_id='alice',managed_session_id='a',authorize=lambda:True)
    manager.handle_offer('b',{},principal_id='bob',managed_session_id='b',authorize=lambda:True)
    try:
        set_capture_private('rtc-private-transition-fixture',expires_at=float('inf'),valid=lambda:True)
        assert backend.closed==['a','b']
        assert not manager._sessions
    finally:clear_capture_private('rtc-private-transition-fixture')


def test_private_transition_failure_retains_barrier_and_never_acknowledges():
    from termx.desktop.recording import set_capture_private,clear_capture_private,capture_privacy_revision
    class Broken(RealBackend):
        def close(self,id):raise RuntimeError('controlled transport failure')
    manager=rtc.RtcManager(backend=Broken())
    manager.handle_offer('a',{},principal_id='alice',managed_session_id='a',authorize=lambda:True)
    try:
        with pytest.raises(PermissionError,match='retained'):set_capture_private('rtc-private-failure-fixture',expires_at=float('inf'),valid=lambda:True)
        with pytest.raises(PermissionError):capture_privacy_revision()
        assert manager._sessions['a'].closed
    finally:
        manager._backend=RealBackend();manager.close_all()
        clear_capture_private('rtc-private-failure-fixture')


def test_actual_aiortc_peer_close_on_private_transition(monkeypatch):
    aiortc=pytest.importorskip('aiortc')
    from termx.desktop.recording import set_capture_private,clear_capture_private
    configuration=aiortc.RTCConfiguration(iceServers=[])
    monkeypatch.setattr(rtc,'_RTCPeerConnection',lambda:aiortc.RTCPeerConnection(configuration))
    peer=aiortc.RTCPeerConnection(configuration)
    backend=rtc.AiortcBackend();manager=rtc.RtcManager(backend=backend)
    async def offer():
        peer.addTransceiver('video',direction='recvonly')
        await peer.setLocalDescription(await peer.createOffer())
        return {'type':peer.localDescription.type,'sdp':peer.localDescription.sdp}
    try:
        manager.handle_offer('private-peer',rtc._run_coro(offer()),principal_id='alice',managed_session_id='a',authorize=lambda:True)
        actual=backend._pcs['private-peer']
        set_capture_private('rtc-actual-peer-fixture',expires_at=float('inf'),valid=lambda:True)
        assert actual.connectionState=='closed'
        assert not manager._sessions and not backend._pcs
    finally:
        manager.close_all();rtc._run_coro(peer.close());clear_capture_private('rtc-actual-peer-fixture')
