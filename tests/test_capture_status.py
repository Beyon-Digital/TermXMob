import asyncio

from fastapi.testclient import TestClient


def test_capture_status_is_owner_scoped_live_metadata_without_new_observation(tmp_path, monkeypatch):
    from termx.app import AppState, create_app
    from termx.identity import AuthenticationService
    from test_window_recording import Port
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
    identity = AuthenticationService(tmp_path / 'identity.sqlite3')
    owner = identity.setup_owner('owner', 'fixture-capture-password-123')
    other = identity.create_local_user('other', 'fixture-other-password-123', ['desktop-view'])
    def login(name, password):
        return asyncio.run(identity.login('local-password', {'username': name, 'password': password}, peer='127.0.0.1'))
    credentials = login('owner', 'fixture-capture-password-123')
    stranger = login('other', 'fixture-other-password-123')
    state = AppState(identity=identity)
    app = create_app(state)
    port = Port()
    state.window_recording.port = port
    row = state.window_recording.start(owner.id, credentials.session_id, owner.policy_version, '1')
    foreign = state.window_recording.start(other.id, stranger.session_id, other.policy_version, '2')
    state.window_recording.configure(row['id'], owner.id, private=True)
    state.browser.records.put('tab', 'recording-tab', {'id': 'recording-tab', 'principal_id': owner.id, 'project_id': '', 'state': 'human', 'recording': True, 'url': 'https://secret-private.example', 'title': 'DO_NOT_EXPOSE_TITLE'})
    client = TestClient(app, base_url='https://localhost')
    headers = {'Authorization': 'Bearer ' + credentials.access_token}
    result = client.get('/api/desktop/recording/status', headers=headers)
    assert result.status_code == 200, result.text
    assert sorted((item['kind'], item['state']) for item in result.json()['captures']) == [('tab', 'recording'), ('window', 'private')]
    assert 'DO_NOT_EXPOSE' not in result.text and 'secret-private' not in result.text
    assert not port.events
    denied = client.get('/api/desktop/recording/status', headers={'Authorization': 'Bearer ' + stranger.access_token})
    assert denied.status_code == 403
    assert row['id'] not in denied.text and foreign['id'] not in result.text
    assert client.delete('/api/desktop/recording/captures/' + row['id'], headers=headers).status_code == 200
    assert [item['kind'] for item in client.get('/api/desktop/recording/status', headers=headers).json()['captures']] == ['tab']
    identity.revoke(credentials.session_id, owner.id)
    assert client.get('/api/desktop/recording/status', headers=headers).status_code == 401
    state.window_recording.stop(foreign['id'], other.id)
