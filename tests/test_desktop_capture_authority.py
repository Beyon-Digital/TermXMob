import asyncio
from fastapi.testclient import TestClient

def test_managed_viewers_start_watch_only_control_is_connection_scoped_and_global_stop_is_owned(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    from termx.identity import AuthenticationService
    from termx.desktop import session as port
    from termx.desktop.capture import CaptureError
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    monkeypatch.setattr(port,'permission_snapshot',lambda:{'screen_recording':'granted','accessibility':'granted'})
    monkeypatch.setattr(port,'list_displays',lambda:[])
    def unavailable(*_):raise CaptureError('Fixture: no physical capture requested')
    monkeypatch.setattr(port,'grab_jpeg',unavailable)
    events=[];monkeypatch.setattr(port,'apply_event',lambda payload,*_:events.append(payload))
    identity=AuthenticationService(tmp_path/'identity.sqlite3');owner=identity.setup_owner('owner','fixture-watch-password-123')
    login=asyncio.run(identity.login('local-password',{'username':'owner','password':'fixture-watch-password-123'},peer='127.0.0.1'))
    state=AppState(identity=identity);client=TestClient(create_app(state),base_url='https://localhost');headers={'Authorization':'Bearer '+login.access_token}
    def message(ws,kind):
        for _ in range(8):
            row=ws.receive_json()
            if row['type']==kind:return row
        raise AssertionError('Missing acknowledgement '+kind)
    with client.websocket_connect('/api/desktop/session',headers=headers) as first:
        assert message(first,'hello')['view_only'] is True
        first.send_json({'type':'control','view_only':False});assert message(first,'control')['view_only'] is False
        with client.websocket_connect('/api/desktop/session',headers=headers) as second:
            assert message(second,'hello')['view_only'] is True
            second.send_json({'type':'key','key':'A','action':'down'});assert message(second,'denied')['message']=='view-only';assert not events
            current=client.get('/api/desktop/recording/status',headers=headers)
            assert current.status_code==200,current.text
            views=[r for r in current.json()['captures'] if r['kind']=='computer']
            assert sorted(r['state'] for r in views)==['control','watching']
            controlled=next(r for r in views if r['state']=='control')
            assert 'session_id' not in current.text and 'principal_id' not in current.text
            # Foreign identity cannot manipulate a connection by guessing its id.
            try:asyncio.run(state.desktop.stop_viewer(controlled['id'],'foreign'))
            except KeyError:pass
            else:raise AssertionError('Foreign stop accepted')
            assert client.delete('/api/desktop/recording/viewers/'+controlled['id'],headers=headers).status_code==200
            assert first.receive()['code']==1000
            assert [r['state'] for r in client.get('/api/desktop/recording/status',headers=headers).json()['captures'] if r['kind']=='computer']==['watching']
        assert not events # A view-only connection closing must not release another controller's keys.


def test_second_owner_window_retains_private_preview_but_fresh_session_can_stop_expired_capture(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    from termx.identity import AuthenticationService
    from termx.desktop.recording import assert_agent_capture_allowed
    from test_window_recording import Port
    import pytest
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    monkeypatch.setenv('TERMX_ENGINE_STARTUP_REFRESH','0')
    identity=AuthenticationService(tmp_path/'identity.sqlite3');owner=identity.setup_owner('owner','fixture-private-window-password-123')
    def login():return asyncio.run(identity.login('local-password',{'username':'owner','password':'fixture-private-window-password-123'},peer='127.0.0.1'))
    first,second=login(),login();state=AppState(identity=identity)
    client=TestClient(create_app(state),base_url='https://localhost');state.window_recording.port=Port()
    first_headers={'Authorization':'Bearer '+first.access_token};second_headers={'Authorization':'Bearer '+second.access_token}
    created=client.post('/api/desktop/recording/captures',headers=first_headers,json={'window_id':'1'});assert created.status_code==200,created.text
    identifier=created.json()['id'];path='/api/desktop/recording/captures/'+identifier
    assert client.patch(path,headers=first_headers,json={'private':True}).status_code==200
    preview=client.get(path+'/preview',headers=second_headers);assert preview.status_code==200 and preview.json()['image'].startswith('data:image/jpeg')
    identity.revoke(first.session_id,owner.id)
    assert client.post(path+'/input',headers=second_headers,json={'event':{'type':'key','key':'Tab','action':'down'}}).status_code==403
    assert client.get(path+'/preview',headers=second_headers).status_code==403
    with pytest.raises(PermissionError):assert_agent_capture_allowed()
    status=client.get('/api/desktop/recording/status',headers=second_headers);assert status.status_code==200,status.text
    assert status.json()['captures']==[{'id':identifier,'kind':'window','state':'private','expired':True,'stop_allowed':True}]
    assert 'Test' not in status.text and 'session_id' not in status.text and 'image' not in status.text
    assert client.delete(path,headers=second_headers).status_code==200
    assert_agent_capture_allowed()
    assert not state.window_recording.port.events
