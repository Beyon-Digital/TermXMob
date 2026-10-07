import asyncio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from termx.app import AppState,create_app
from termx.authorization import ROLES

def test_view_transport_cannot_enable_input_clipboard_or_rtc(monkeypatch):
    from termx.desktop.capture import CaptureError
    import termx.desktop.session as desktop
    monkeypatch.setattr(desktop,'permission_snapshot',lambda:{})
    monkeypatch.setattr(desktop,'grab_jpeg',lambda *_: (_ for _ in ()).throw(CaptureError('Fixture capture unavailable')))
    effects=[]
    monkeypatch.setattr(desktop,'clipboard_get',lambda:effects.append('read'))
    monkeypatch.setattr(desktop,'clipboard_set',lambda _:effects.append('write'))
    monkeypatch.setattr(desktop,'apply_event',lambda *args:effects.append('input'))
    state=AppState(passcode='fixture-passcode');token=state.tokens.issue(['desktop-view'])
    client=TestClient(create_app(state))
    with client.websocket_connect('/api/desktop/session',headers={'Authorization':'Bearer '+token}) as ws:
        assert ws.receive_json()['type']=='hello'
        for payload in [{'type':'control','view_only':False},{'type':'clipboard','action':'get'},
                        {'type':'text','text':'Should not be typed'},{'type':'rtc','action':'offer'}]:
            ws.send_json(payload)
            while True:
                reply=ws.receive_json()
                if reply['type']=='denied':break
            assert reply['message']=='desktop-control permission required'
    assert not effects

def test_raw_file_and_execution_transports_enforce_project_resource_scope(tmp_path):
    state=AppState(passcode=None);owner=state.identity.setup_owner('owner','transport-password-123')
    folder=tmp_path/'project';folder.mkdir();file=folder/'notes.txt';file.write_text('Project context')
    project=state.projects.register(str(folder),name='Transport fixture')
    alice=state.identity.create_local_user('alice','transport-password-123',list(ROLES['operator']))
    bob=state.identity.create_local_user('bob','transport-password-123',list(ROLES['operator']))
    state.authorization.set_role(alice.id,'operator',trusted_execution=True)
    state.authorization.set_role(bob.id,'operator',trusted_execution=True)
    state.authorization.grant_project(alice.id,project['id'],['files-read','agent-view','agent-run','terminal-control'])
    def headers(username):
        credential=asyncio.run(state.identity.login('local-password',{'username':username,'password':'transport-password-123'},peer='local'))
        return {'Authorization':'Bearer '+credential.access_token}
    ah,bh=headers('alice'),headers('bob');client=TestClient(create_app(state))
    assert client.get('/api/fs/download',params={'path':str(file)},headers=ah).text=='Project context'
    assert client.get('/api/fs/download',params={'path':str(file)},headers=bh).status_code==403
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect('/api/projects/'+project['id']+'/lsp/python',headers=bh):pass
    assert denied.value.code==4403
    # Neither a guessed terminal ID nor another user's task grants execution.
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect('/api/sessions/another-users-terminal/pty',headers=bh):pass
    assert denied.value.code==4403
    task=state.agent_store.create_task(prompt='Artifact scope fixture',cwd=str(folder),provider_id='fixture',model='fixture',limits={})
    state.authorization.claim(ah['Authorization'][7:],'task',task['id'],project['id'])
    artifact=state.agent_store.save_artifact(task['id'],'file','text/plain',b'Scoped result')
    path='/api/agent/tasks/'+task['id']+'/artifacts/'+artifact['id']
    assert client.get(path,headers=ah).content==b'Scoped result'
    assert client.get(path,headers=bh).status_code==403
