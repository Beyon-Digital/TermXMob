import asyncio,json,os
import pytest
from fastapi.testclient import TestClient

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_real_view_only_socket_still_renders_denies_control_and_global_stop_is_owned(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    from termx.identity import AuthenticationService
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    identity=AuthenticationService(tmp_path/'identity.sqlite3');owner=identity.setup_owner('owner','fixture-view-only-password-123')
    def login():return asyncio.run(identity.login('local-password',{'username':'owner','password':'fixture-view-only-password-123'},peer='127.0.0.1'))
    initial=login();state=AppState(identity=identity)
    with TestClient(create_app(state),base_url='https://localhost') as client:
        admin={'Authorization':'Bearer '+initial.access_token}
        profile=client.post('/api/browser/profiles',headers=admin,json={'name':'Owned view-only fixture'}).json()
        response=client.post('/api/browser/tabs',headers=admin,json={'profile_id':profile['id']});assert response.status_code==200,response.text
        tab=response.json()
        # A current managed principal can retain view while losing control.
        with identity._db() as db:db.execute('UPDATE principals SET scopes=?,policy_version=policy_version+1 WHERE id=?',(json.dumps(['desktop-view']),owner.id))
        credentials=login();headers={'Authorization':'Bearer '+credentials.access_token}
        with client.websocket_connect('/api/browser/tabs/'+tab['id']+'/view',headers=headers) as socket:
            got_frame=False;got_state=False
            for _ in range(12):
                message=socket.receive()
                if message.get('bytes'):got_frame=True
                if message.get('text'):
                    value=json.loads(message['text'])
                    if value['type']=='state':assert value['can_control'] is False;got_state=True
                if got_frame and got_state:break
            assert got_frame and got_state
            socket.send_json({'action':'type','args':{'text':'REFUSED_VIEW_INPUT'}})
            for _ in range(12):
                message=socket.receive()
                if message.get('text') and (value:=json.loads(message['text']))['type']=='denied':break
            else:raise AssertionError('Missing meaningful control denial')
            assert value['message'] and 'REFUSED_VIEW_INPUT' not in str(value)
            assert state.browser.get(tab['id'],owner.id)['state']=='human'
            status=client.get('/api/desktop/recording/status',headers=headers)
            assert status.status_code==200 and status.json()['captures']==[{'id':tab['id'],'kind':'tab','state':'watching','stop_allowed':True}]
            assert client.post('/api/browser/tabs/'+tab['id']+'/capture/stop',headers=headers,json={}).status_code==200
            for _ in range(12):
                closed=socket.receive()
                if closed['type']=='websocket.close':break
            assert closed['code']==1000
        assert client.get('/api/desktop/recording/status',headers=headers).json()['captures']==[]

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_second_same_owner_private_view_survives_initiator_revoke_without_unpausing_computer(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    from termx.identity import AuthenticationService
    from termx.desktop.recording import capture_privacy_revision
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    identity=AuthenticationService(tmp_path/'identity.sqlite3');owner=identity.setup_owner('owner','fixture-second-private-password-123')
    def login():return asyncio.run(identity.login('local-password',{'username':'owner','password':'fixture-second-private-password-123'},peer='127.0.0.1'))
    first=login();second=login();state=AppState(identity=identity)
    with TestClient(create_app(state),base_url='https://localhost') as client:
        headers={'Authorization':'Bearer '+first.access_token};second_headers={'Authorization':'Bearer '+second.access_token}
        profile=client.post('/api/browser/profiles',headers=headers,json={'name':'Second private viewer'}).json()
        tab=client.post('/api/browser/tabs',headers=headers,json={'profile_id':profile['id']}).json()
        assert client.post('/api/browser/tabs/'+tab['id']+'/takeover',headers=headers,json={'private':True}).status_code==200
        with client.websocket_connect('/api/browser/tabs/'+tab['id']+'/view',headers=second_headers) as socket:
            def private_frame():
                for _ in range(12):
                    event=socket.receive()
                    if event.get('bytes'):assert event['bytes'][:2]==b'\xff\xd8';return
                raise AssertionError('Second valid human viewer lost its private frame')
            private_frame();identity.revoke(first.session_id,owner.id);private_frame()
            assert state.browser.get(tab['id'],owner.id)['state']=='private'
            with pytest.raises(PermissionError):capture_privacy_revision()
            assert client.get('/api/desktop/recording/status',headers=second_headers).json()['captures'][0]['state']=='private'
            assert client.post('/api/browser/tabs/'+tab['id']+'/takeover',headers=second_headers,json={'private':False}).status_code==200
            assert isinstance(capture_privacy_revision(),int)
