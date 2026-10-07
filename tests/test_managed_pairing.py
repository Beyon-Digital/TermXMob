"""Managed mobile proofs use canonical sessions, real TLS and live device grants."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
import threading
from time import time, sleep
from types import SimpleNamespace

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.testclient import TestClient
import uvicorn

from termx.auth import Auth
from termx.authorization import AuthorizationService
from termx.identity import AuthenticationError, AuthenticationService
from termx.identity_http import mount_identity
from termx.identity_guard import SessionGuard
from termx.tokens import TokenStore
from test_oidc_tls_interoperability import tls_idp

PASSWORD = 'isolated-pair-test-password-123'


def fixture(tmp_path):
    identity = AuthenticationService(tmp_path/'identity.sqlite3')
    owner = identity.setup_owner('owner',PASSWORD)
    tokens = TokenStore(tmp_path/'tokens.json')
    state = SimpleNamespace(identity=identity,tokens=tokens,auth=Auth(identity=identity,token_store=tokens))
    state.authorization = AuthorizationService(identity,state.auth)
    app = FastAPI()
    mount_identity(app,state)
    app.add_middleware(SessionGuard,state=state)
    @app.websocket('/graphql')
    async def echo(websocket: WebSocket):
        access = websocket.headers.get('authorization','').removeprefix('Bearer ')
        actor = identity.resolve(access)
        if not actor:
            return await websocket.close(code=4401)
        await websocket.accept()
        try:
            while True:
                await websocket.receive_text()
                await websocket.send_json({'principal':actor.principal.id,'scopes':list(identity.resolve(access).principal.scopes)})
        except Exception:
            pass
    @app.get('/api/fixture')
    def protected():
        return {'ok':True}
    credentials = asyncio.run(identity.login('local-password',{'username':'owner','password':PASSWORD},peer='fixture'))
    client = TestClient(app,base_url='https://localhost',client=('192.0.2.1',12345))
    return state,app,credentials,client,{'Authorization':'Bearer '+credentials.access_token}


def issue(client,headers,scopes=('machine-view',),**extra):
    response = client.post('/auth/pair/issue',headers=headers,json={'scopes':list(scopes),**extra})
    assert response.status_code==200,response.text
    return response.json()


def exchange(client,proof,name='Synthetic mobile fixture'):
    return client.post('/auth/pair/exchange',json={'ticket':proof['ticket'],'host_id':proof['host_id'],'device_name':name})


def test_pair_recovery_refresh_grant_never_expands_and_revoke_is_live(tmp_path):
    state,_,admin,client,headers=fixture(tmp_path)
    proof=issue(client,headers,('machine-view','agent-view'))
    assert proof['expires_in']==300
    paired=exchange(client,proof)
    assert paired.status_code==200,paired.text
    body=paired.json(); principal=state.identity.resolve(body['access_token']).principal
    assert principal.id==state.identity.resolve(admin.access_token).principal.id
    assert principal.scopes==('machine-view','agent-view')
    assert state.identity.current_principal(principal).scopes==principal.scopes
    with pytest.raises(HTTPException):state.authorization.require_principal(principal,'host-admin')
    with pytest.raises(HTTPException):state.authorization.require_creation_principal(principal,'agent-control')
    assert exchange(client,proof).status_code==401
    assert body['refresh_token'].encode() not in state.identity.path.read_bytes()
    assert proof['ticket'].encode() not in state.identity.path.read_bytes()
    # A host policy expansion must not expand an existing device's immutable grant.
    state.identity.set_scopes(principal.id,list(state.identity.principal_by_id(principal.id).scopes))
    refreshed=client.post('/auth/refresh',json={'refresh_token':body['refresh_token']})
    assert refreshed.status_code==200,refreshed.text
    assert state.identity.resolve(refreshed.json()['access_token']).principal.scopes==principal.scopes
    assert client.post('/auth/refresh',json={'refresh_token':body['refresh_token']}).status_code==401
    assert state.identity.resolve(refreshed.json()['access_token']) is None
    assert state.identity.resolve(admin.access_token) is not None


def test_pending_proofs_fail_after_scope_role_session_expiry_and_wrong_host(tmp_path):
    state,_,admin,client,headers=fixture(tmp_path)
    for mutate in ('policy','revoke','expire'):
        proof=issue(client,headers)
        if mutate=='policy':
            state.authorization.set_role(state.identity.resolve(admin.access_token).principal.id,'owner')
        elif mutate=='expire':
            with state.identity._db() as db:db.execute('UPDATE session_proofs SET expires=1')
        else:state.identity.revoke(admin.session_id,state.identity.resolve(admin.access_token).principal.id)
        assert exchange(client,proof).status_code==401
        if mutate=='revoke':
            admin=asyncio.run(state.identity.login('local-password',{'username':'owner','password':PASSWORD},peer='new-fixture'))
            headers={'Authorization':'Bearer '+admin.access_token}
    proof=issue(client,headers)
    wrong={**proof,'host_id':'foreign-host'}
    assert exchange(client,wrong).status_code==401
    assert exchange(client,proof).status_code==200
    assert client.post('/auth/pair/issue',headers=headers,json={'scopes':['imaginary']}).status_code==403


def test_explicit_legacy_association_does_not_extend_retired_access(tmp_path):
    state,_,admin,client,headers=fixture(tmp_path)
    raw=state.tokens.issue(scopes=['machine-view','files-read'],device_name='Legacy phone',expires_at=1)
    state.identity.end_legacy_migration()
    assert state.auth.scopes(raw) is None
    assert client.post('/auth/pair/issue',headers=headers,json={'scopes':['files-read'],'legacy_token':raw}).status_code==403
    proof=issue(client,headers,('files-read',),legacy_token=raw,associate_legacy=True)
    assert state.tokens.association_record(raw)
    paired=exchange(client,proof)
    assert paired.status_code==200,paired.text
    assert state.tokens.association_record(raw) is None
    assert state.identity.resolve(paired.json()['access_token']).principal.scopes==('files-read',)
    assert state.auth.scopes(raw) is None
    assert state.identity.migration_deadline==0
    # No ownership or resource claims are fabricated for old host aggregates.
    assert state.authorization.resource_owner('conversation','unclaimed') is None


def test_socket_ticket_exact_path_host_single_use_and_live_revocation(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    state,_,admin,client,headers=fixture(tmp_path)
    proof=issue(client,headers);paired=exchange(client,proof).json()
    actor={'Authorization':'Bearer '+paired['access_token']}
    assert client.post('/auth/socket-ticket',headers=actor,json={'path':'/api/../secret'}).status_code==403
    ticket=client.post('/auth/socket-ticket',headers=actor,json={'path':'/graphql'}).json()
    assert ticket['expires_in']==30
    url=f"wss://localhost/graphql?st={ticket['ticket']}&host_id={ticket['host_id']}"
    with client.websocket_connect(url) as ws:
        ws.send_text('hello');reply=ws.receive_json()
        assert reply['scopes']==['machine-view']
        state.identity.revoke(paired['session_id'],reply['principal'])
        with pytest.raises(WebSocketDisconnect):
            ws.send_text('revoked');ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url):pass
    snapshot=state.authorization.resource_snapshot(state.identity.resolve(admin.access_token).principal,'agent-view','conversation',[])
    state.identity.revoke(admin.session_id,snapshot.principal_id)
    with pytest.raises(HTTPException):state.authorization.validate_resource_snapshot(snapshot)


@pytest.fixture
def tls_host(tmp_path):
    state,app,_,_,_=fixture(tmp_path)
    key=generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
          .not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256()))
    certificate=tmp_path/'fixture-ca.pem';private=tmp_path/'fixture-key.pem'
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    import socket
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,ssl_certfile=str(certificate),ssl_keyfile=str(private),log_level='error',lifespan='off'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    deadline=time()+5
    while not server.started and time()<deadline:sleep(.02)
    assert server.started
    try:yield state,f'https://localhost:{port}',ssl.create_default_context(cafile=str(certificate))
    finally:server.should_exit=True;thread.join(timeout=5);sock.close()


def test_native_password_pair_refresh_socket_over_real_verified_tls(tls_host):
    state,url,trust=tls_host
    with httpx.Client(base_url=url,verify=trust,trust_env=False) as client:
        login=client.post('/auth/login',json={'method':'local-password','username':'owner','password':PASSWORD,'transport':'bearer','device_name':'TLS native fixture'})
        assert login.status_code==200,login.text
        headers={'Authorization':'Bearer '+login.json()['access_token']}
        proof=issue(client,headers,('machine-view',))
        paired=exchange(client,proof)
        assert paired.status_code==200,paired.text
        device=paired.json();headers={'Authorization':'Bearer '+device['access_token']}
        assert client.get('/auth/me',headers=headers).json()['host_id']==proof['host_id']
        ticket=client.post('/auth/socket-ticket',headers=headers,json={'path':'/graphql'}).json()
        from websockets.sync.client import connect
        with connect(url.replace('https:','wss:')+f"/graphql?st={ticket['ticket']}&host_id={proof['host_id']}",ssl=trust,proxy=None) as ws:
            ws.send('TLS controlled request')
            import json
            assert json.loads(ws.recv())['scopes']==['machine-view']
        rotated=client.post('/auth/refresh',json={'refresh_token':device['refresh_token']})
        assert rotated.status_code==200 and rotated.json()['refresh_token']!=device['refresh_token']
        assert client.post('/auth/logout',headers={'Authorization':'Bearer '+rotated.json()['access_token']}).status_code==200
        assert client.post('/auth/refresh',json={'refresh_token':rotated.json()['refresh_token']}).status_code==401
    # Trust is pinned to the fixture certificate; ordinary system trust rejects it.
    with httpx.Client(base_url=url,trust_env=False) as untrusted:
        with pytest.raises(httpx.ConnectError):untrusted.get('/auth/methods')


def test_remote_http_and_foreign_origin_are_denied_before_pairing(tmp_path):
    _,app,_,client,headers=fixture(tmp_path)
    remote=TestClient(app,base_url='http://host.invalid',client=('192.0.2.4',123))
    assert remote.post('/auth/pair/issue',headers=headers,json={'scopes':['machine-view']}).status_code==403
    assert client.post('/auth/pair/issue',headers={**headers,'Origin':'https://evil.invalid'},json={'scopes':['machine-view']}).status_code==403


def test_proof_is_one_use_across_independent_service_process_connections(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from termx.identity_pairing import ManagedPairing
    state,_,_,client,headers=fixture(tmp_path)
    proof=issue(client,headers)
    other=ManagedPairing(AuthenticationService(state.identity.path),state.tokens)
    def redeem(service):
        try:return service.exchange(proof['ticket'],host_id=proof['host_id'],device_name='Race fixture').session_id
        except AuthenticationError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(redeem,[state.managed_pairing,other]))
    assert sum(result is not None for result in results)==1
    assert len(state.identity.list_sessions(state.identity.resolve(headers['Authorization'][7:]).principal.id))==2


def test_socket_proof_wrong_path_host_expiry_and_jwt_url_never_admitted(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    state,_,admin,client,headers=fixture(tmp_path)
    ticket=client.post('/auth/socket-ticket',headers=headers,json={'path':'/graphql'}).json()
    with pytest.raises(AuthenticationError):state.managed_pairing.admit_socket(ticket['ticket'],host_id=ticket['host_id'],path='/api/desktop/session')
    with pytest.raises(AuthenticationError):state.managed_pairing.admit_socket(ticket['ticket'],host_id='wrong-host',path='/graphql')
    assert state.identity.resolve(state.managed_pairing.admit_socket(ticket['ticket'],host_id=ticket['host_id'],path='/graphql')).session_id==admin.session_id
    expired=client.post('/auth/socket-ticket',headers=headers,json={'path':'/graphql'}).json()
    with state.identity._db() as db:db.execute("UPDATE session_proofs SET expires=1 WHERE purpose='socket'")
    with pytest.raises(AuthenticationError):state.managed_pairing.admit_socket(expired['ticket'],host_id=expired['host_id'],path='/graphql')
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('wss://localhost/graphql?k='+admin.access_token):pass


def test_sponsor_can_revoke_pending_ticket_without_revoking_a_foreign_sponsors_ticket(tmp_path):
    state,_,_,client,headers=fixture(tmp_path)
    proof=issue(client,headers)
    another=asyncio.run(state.identity.login('local-password',{'username':'owner','password':PASSWORD},peer='other-fixture'))
    foreign={'Authorization':'Bearer '+another.access_token}
    assert client.post('/auth/pair/revoke',headers=foreign,json={'ticket':proof['ticket']}).json()=={'revoked':False}
    assert client.post('/auth/pair/revoke',headers=headers,json={'ticket':proof['ticket']}).json()=={'revoked':True}
    assert exchange(client,proof).status_code==401


def test_real_workspace_routes_cannot_expand_owner_phone_grant(tmp_path):
    from termx.app import AppState,create_app
    state=AppState(passcode=None)
    owner=state.identity.setup_owner('owner',PASSWORD)
    credentials=asyncio.run(state.identity.login('local-password',{'username':'owner','password':PASSWORD},peer='fixture'))
    client=TestClient(create_app(state),base_url='https://localhost',client=('192.0.2.1',12345))
    proof=issue(client,{'Authorization':'Bearer '+credentials.access_token},('agent-view',))
    paired=exchange(client,proof).json();headers={'Authorization':'Bearer '+paired['access_token']}
    assert client.get('/api/workspace/sessions',headers=headers).status_code==200
    assert client.post('/api/workspace/sessions',headers=headers,json={'title':'Must not create'}).status_code==403
    assert state.workspace.sessions(state.identity.principal_by_id(owner.id))==[]


def test_real_tls_oidc_identity_can_explicitly_pair_without_local_password(tls_idp,tmp_path):
    from urllib.parse import parse_qs,urlsplit
    from termx.identity import Identity
    from termx.identity_adapters import OidcAdapter,OidcConfig
    state,app,_,client,admin=fixture(tmp_path)
    from termx.authorization_http import mount_authorization
    mount_authorization(app,state)
    person=state.identity.create_principal('TLS SSO phone',['machine-view','agent-view'])
    state.authorization.set_role(person.id,'viewer')
    state.identity.map_identity(Identity(tls_idp['issuer'],'stable-subject','oidc'),person.id)
    async def authenticate():
        async with httpx.AsyncClient(verify=tls_idp['trust'],trust_env=False) as transport:
            adapter=OidcAdapter(state.identity,OidcConfig('tls-phone','TLS phone',tls_idp['issuer'],'client','https://localhost/auth/oidc/tls-phone/callback'),client=transport)
            state.identity.register_adapter(adapter)
            url,binding=await adapter.begin()
            response=await transport.get(url,follow_redirects=False)
            params=parse_qs(urlsplit(response.headers['location']).query)
            return await state.identity.login('tls-phone',{'state':params['state'][0],'code':params['code'][0],'binding':binding},peer='oidc-fixture')
    credentials=asyncio.run(authenticate())
    headers={'Authorization':'Bearer '+credentials.access_token}
    proof=issue(client,headers,('machine-view',))
    device=exchange(client,proof)
    assert device.status_code==200,device.text
    assert device.json()['principal']['id']==person.id
    assert device.json()['principal']['scopes']==['machine-view']
    with state.identity._db() as db:
        row=db.execute('SELECT adapter_id,strength FROM sessions WHERE id=?',(device.json()['session_id'],)).fetchone()
        assert row['adapter_id']=='tls-phone' and row['strength']=='explicit-device-pair'
    # Retiring the enterprise adapter revokes its paired sessions as well.
    retired=client.put('/auth/admin/adapters/tls-phone',headers=admin,json={'enabled':False,'session_policy':'revoke'})
    assert retired.status_code==200,retired.text
    assert state.identity.resolve(device.json()['access_token']) is None
