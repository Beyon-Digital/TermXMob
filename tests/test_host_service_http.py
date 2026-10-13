import secrets
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from termx.app import AppState,create_app


def test_cookie_hostadmin_controls_enforce_csrf_and_never_disclose_bootstrap(tmp_path,monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'profile'))
    fake=SimpleNamespace(status=Mock(return_value={'ready':False,'installed':False}),
        install=Mock(return_value={'installed':True,'ready':False}),start=Mock(return_value={'ready':True}),
        stop=Mock(),uninstall=Mock())
    monkeypatch.setattr('termx.host_service_http.HostService',lambda:fake)
    state=AppState(passcode='bootstrap-secret')
    with TestClient(create_app(state,web_dir=tmp_path/'served-assets'),base_url='https://localhost') as client:
        credentials=client.post('/auth/setup',headers={'Origin':'https://localhost','X-Termx-Passcode':'bootstrap-secret'},
            json={'username':'owner','password':'owner-service-password-123','transport':'cookie'}).json()
        assert client.get('/api/host/service').status_code==200
        assert client.post('/api/host/service/install',json={}).status_code==403
        headers={'Origin':'https://localhost','X-Termx-Csrf':credentials['csrf_token']}
        installed=client.post('/api/host/service/install',json={},headers=headers)
        assert installed.status_code==200 and 'bootstrap-secret' not in installed.text
        assert fake.install.call_args.kwargs['web_dir']==str((tmp_path/'served-assets').resolve())
        assert fake.install.call_args.kwargs['bootstrap_passcode']=='bootstrap-secret'
        stopped=client.post('/api/host/service/stop',json={},headers=headers)
        assert stopped.json()=={'accepted':True,'state':'stopping'}
        fake.stop.assert_called_once_with(wait=False)


def test_service_settings_refuse_non_admin_managed_user(tmp_path,monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'profile'))
    state=AppState(passcode='bootstrap-secret')
    state.identity.setup_owner('owner','owner-service-password-123')
    state.identity.create_local_user('viewer','viewer-service-password-123',['machine-view'])
    import asyncio
    credentials=asyncio.run(state.identity.login('local-password',{'username':'viewer','password':'viewer-service-password-123'},peer='test'))
    with TestClient(create_app(state),base_url='https://localhost',headers={'Authorization':'Bearer '+credentials.access_token}) as client:
        assert client.get('/api/host/service').status_code==403
        assert client.post('/api/host/service/install',json={}).status_code==403


def test_runtime_proof_requires_loopback_and_private_challenge(tmp_path,monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'profile'))
    state=AppState(passcode=None)
    token=secrets.token_hex(32)
    state.host_instance=SimpleNamespace(proof_token=token)
    state.host_runtime={'instance_id':'instance','profile_id':'profile','pid':123,'port':8787,'managed':True,'ready':True}
    with TestClient(create_app(state),client=('127.0.0.1',12345)) as client:
        assert client.get('/api/host/runtime').status_code==403
        accepted=client.get('/api/host/runtime',headers={'X-Termx-Host-Proof':token})
        assert accepted.status_code==200 and token not in accepted.text and accepted.json()['ready']
    remote=AppState(passcode='configured-passcode')
    remote.host_instance=SimpleNamespace(proof_token=token)
    remote.host_runtime=state.host_runtime
    with TestClient(create_app(remote),client=('192.0.2.1',12345),base_url='https://localhost') as client:
        assert client.get('/api/host/runtime',headers={'X-Termx-Host-Proof':token}).status_code==403
