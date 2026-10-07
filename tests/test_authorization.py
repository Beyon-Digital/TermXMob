from __future__ import annotations

import asyncio
from time import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from termx.app import AppState, create_app
from termx.auth import Auth
from termx.authorization import AuthorizationService, ROLES
from termx.authorization_http import mount_authorization
from termx.identity import AuthenticationError, AuthenticationService

PASSWORD = 'isolated-test-password-123'


def fixture(tmp_path):
    identity=AuthenticationService(tmp_path/'auth.sqlite3')
    owner=identity.setup_owner('owner',PASSWORD)
    authz=AuthorizationService(identity,Auth(identity=identity))
    a=identity.create_local_user('alice',PASSWORD,list(ROLES['operator']))
    b=identity.create_local_user('bob',PASSWORD,list(ROLES['operator']))
    for p in (a,b):authz.set_role(p.id,'operator')
    def login(name):return asyncio.run(identity.login('local-password',{'username':name,'password':PASSWORD},peer='local')).access_token
    return identity,authz,owner,a,b,login


def test_two_users_cannot_cross_projects_resources_or_host(tmp_path):
    identity,authz,owner,a,b,login=fixture(tmp_path)
    alice,bob=login('alice'),login('bob')
    authz.grant_project(a.id,'one',['files-read','files-write','agent-view','agent-control'])
    authz.grant_project(b.id,'two',['files-read','agent-view'])
    assert authz.can(alice,'files-read',project_id='one')
    assert not authz.can(alice,'files-read',project_id='two')
    assert not authz.can(bob,'files-read',project_id='one')
    assert not authz.can(alice,'files-read',project_id='one',host_id='other-host')
    assert not authz.can(alice,'terminal-view')
    authz.claim(alice,'conversation','chat','one')
    assert authz.can(alice,'agent-view',resource_kind='conversation',resource_id='chat')
    assert not authz.can(bob,'agent-view',resource_kind='conversation',resource_id='chat')
    assert not authz.can(alice,'agent-view',resource_kind='conversation',resource_id='chat',project_id='two')
    with pytest.raises(HTTPException):authz.claim(bob,'conversation','chat','two')
    assert authz.can(login('owner'),'agent-view',resource_kind='conversation',resource_id='chat')


def test_collection_snapshot_batches_live_resource_grants_and_revalidates(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import termx.authorization as policy
    identity,authz,owner,alice,bob,_ = fixture(tmp_path)
    deadline = time()+120
    authz.grant_project(alice.id,'one',['agent-view'],expires=deadline)
    identifiers = [f'chat-{index}' for index in range(1002)]
    with identity._db() as db:
        db.executemany('INSERT INTO resource_owners VALUES (?,?,?,?)',
                       [('conversation',identifier,alice.id,'one') for identifier in identifiers])
        db.executemany('INSERT INTO resource_owners VALUES (?,?,?,?)', [
            ('conversation','scratch',alice.id,None),
            ('conversation','foreign',bob.id,'one'),
            ('conversation','ungranted',alice.id,'two')])
    original = identity._db
    transactions = []

    @contextmanager
    def counted():
        transactions.append(1)
        with original() as db:
            yield db

    monkeypatch.setattr(identity,'_db',counted)
    snapshot = authz.resource_snapshot(alice,'agent-view','conversation',
                                       identifiers+['scratch','foreign','unclaimed','ungranted'])
    assert len(transactions) == 1
    assert snapshot.allowed_ids == frozenset(identifiers+['scratch'])
    assert dict(snapshot.resources)['scratch'] is None
    assert dict(snapshot.resources)['chat-0'] == 'one'
    assert snapshot.expires_at == deadline and not snapshot.administrator
    authz.validate_resource_snapshot(snapshot)
    assert len(transactions) == 2
    monkeypatch.setattr(policy,'time',lambda:deadline+1)
    with pytest.raises(HTTPException):authz.validate_resource_snapshot(snapshot)
    expired = authz.resource_snapshot(alice,'agent-view','conversation',identifiers+['scratch'])
    assert expired.allowed_ids == {'scratch'}
    monkeypatch.setattr(policy,'time',time)
    authz.revoke_project(alice.id,'one')
    with pytest.raises(HTTPException):authz.validate_resource_snapshot(snapshot)
    identity.disable(alice.id)
    with pytest.raises(HTTPException):authz.validate_resource_snapshot(expired)
    with pytest.raises(HTTPException):authz.resource_snapshot(alice,'agent-view','conversation',['scratch'])
    administrator = authz.resource_snapshot(owner,'agent-view','conversation',['foreign','unclaimed'])
    assert administrator.administrator and administrator.allowed_ids == {'foreign','unclaimed'}
    with pytest.raises(ValueError,match='cannot authorize execution'):
        authz.resource_snapshot(owner,'agent-run','conversation',['foreign'])


def test_scratch_ownership_live_grants_and_denied_execution(tmp_path):
    identity,authz,owner,a,b,login=fixture(tmp_path)
    alice=login('alice')
    authz.claim(alice,'conversation','scratch')
    assert authz.can(alice,'agent-control',resource_kind='conversation',resource_id='scratch')
    assert not authz.can(alice,'agent-view',resource_kind='conversation',resource_id='unclaimed')
    authz.grant_project(a.id,'one',['agent-run','agent-view'])
    authz.claim(alice,'task','task','one')
    assert not authz.can(alice,'agent-run',resource_kind='task',resource_id='task')
    authz.set_role(a.id,'operator',trusted_execution=True)
    assert authz.can(alice,'agent-run',resource_kind='task',resource_id='task')
    before=authz.revision(alice)
    authz.revoke_project(a.id,'one')
    assert authz.revision(alice)>before
    assert not authz.can(alice,'agent-view',resource_kind='task',resource_id='task')
    assert not authz.can(alice,'agent-run')
    identity.disable(a.id)
    assert not authz.can(alice,'agent-view',resource_kind='conversation',resource_id='scratch')


def test_paths_symlinks_expiry_and_host_scope_ceiling(tmp_path):
    identity,authz,owner,a,b,login=fixture(tmp_path)
    project=tmp_path/'project';project.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    (project/'escape').symlink_to(outside,target_is_directory=True)
    authz.grant_project(a.id,'one',['files-read','files-write'])
    token=login('alice')
    projects=[{'id':'one','path':str(project)}]
    assert authz.require_path(token,'files-write',str(project/'new.txt'),projects)=='one'
    with pytest.raises(HTTPException):authz.require_path(token,'files-read',str(project/'escape'/'secret'),projects)
    identity.set_scopes(a.id,['files-read'])
    assert not authz.can(token,'files-write',project_id='one')
    with identity._db() as db:db.execute('UPDATE project_grants SET expires=?',(time()-1,))
    assert not authz.can(token,'files-read',project_id='one')
    with pytest.raises(ValueError):authz.grant_project(a.id,'one',['host-admin'])


def test_owner_guard_recovery_one_use_audit_and_no_secret(tmp_path):
    identity,authz,owner,a,b,login=fixture(tmp_path)
    token=login('owner')
    with pytest.raises(ValueError):authz.set_role(owner.id,'viewer')
    code=authz.new_recovery_code(actor_id=owner.id)
    assert code.encode() not in identity.path.read_bytes()
    assert authz.recover_owner(code,'recovered-password-123')=='owner'
    assert identity.resolve(token) is None
    with pytest.raises(ValueError):authz.recover_owner(code,'different-password-123')
    with pytest.raises(AuthenticationError):asyncio.run(identity.login('local-password',{'username':'owner','password':PASSWORD},peer='local'))
    assert asyncio.run(identity.login('local-password',{'username':'owner','password':'recovered-password-123'},peer='local'))
    from termx.audit import read_events
    events=read_events()
    assert any(e['kind']=='auth_owner_recovered' for e in events)
    assert code not in str(events)


def test_principal_worker_checks_reload_stale_groups_and_sessions(tmp_path):
    identity,authz,owner,a,b,login=fixture(tmp_path)
    authz.grant_project(a.id,'one',['agent-view'])
    authz.claim_principal(a,'conversation','job','one')
    authz.require_principal(a,'agent-view',resource_kind='conversation',resource_id='job')
    identity.set_scopes(a.id,[])
    with pytest.raises(HTTPException):authz.require_principal(a,'agent-view',resource_kind='conversation',resource_id='job')
    identity.disable(b.id)
    assert identity.principal_by_id(b.id) is None


def client_fixture(tmp_path):
    identity=AuthenticationService(tmp_path/'auth.sqlite3')
    identity.setup_owner('owner',PASSWORD)
    state=AppState(identity=identity)
    if not hasattr(state,'authorization'):state.authorization=AuthorizationService(identity,state.auth)
    app=create_app(state)
    if not getattr(app.state,'authorization_mounted',False):mount_authorization(app,state)
    client=TestClient(app,base_url='https://localhost')
    credentials=asyncio.run(identity.login('local-password',{'username':'owner','password':PASSWORD},peer='local'))
    return identity,state,client,{'Authorization':f'Bearer {credentials.access_token}'}


def test_admin_routes_principals_roles_projects_revocation(tmp_path):
    identity,state,client,admin=client_fixture(tmp_path)
    created=client.post('/auth/admin/principals',headers=admin,json={'name':'viewer','password':PASSWORD,'role':'viewer'})
    assert created.status_code==200,created.text
    pid=created.json()['id']
    assert PASSWORD not in str(client.get('/auth/admin/principals',headers=admin).json())
    token=asyncio.run(identity.login('local-password',{'username':'viewer','password':PASSWORD},peer='local')).access_token
    viewer={'Authorization':f'Bearer {token}'}
    assert client.get('/auth/admin/principals',headers=viewer).status_code==403
    root=tmp_path/'project';root.mkdir()
    project=state.projects.register(str(root))
    assert client.put(f'/auth/admin/principals/{pid}/projects/{project["id"]}',headers=admin,json={'scopes':['files-read']}).status_code==200
    access=client.get('/auth/access',headers=viewer).json()
    assert access['tenant_isolation'] is False
    assert access['projects'][0]['project_id']==project['id']
    assert client.delete(f'/auth/admin/principals/{pid}/sessions',headers=admin).json()['revoked']==1
    assert client.get('/auth/access',headers=viewer).status_code==401
    assert client.post('/auth/admin/migration/end',headers=admin).status_code==200
    assert identity.migration_deadline==0


def test_recovery_local_only_and_adapter_last_method(tmp_path):
    identity,state,client,admin=client_fixture(tmp_path)
    assert client.put('/auth/admin/adapters/local-password',headers=admin,json={'enabled':False}).status_code==409
    recovery=client.post('/auth/admin/recovery-code',headers=admin).json()['recovery_code']
    foreign=client.post('/auth/recovery',headers={'Origin':'https://evil.invalid'},json={'recovery_code':recovery,'password':'replacement-password-123'})
    assert foreign.status_code==403
    local=client.post('/auth/recovery',headers={'Origin':'https://localhost'},json={'recovery_code':recovery,'password':'replacement-password-123'})
    assert local.status_code==200,local.text
    assert client.get('/auth/admin/principals',headers=admin).status_code==401


def test_graphql_filters_foreign_projects_conversations_and_fs(tmp_path):
    identity,state,client,admin=client_fixture(tmp_path)
    user=identity.create_local_user('operator',PASSWORD,list(ROLES['operator']))
    state.authorization.set_role(user.id,'operator')
    p1=tmp_path/'one';p1.mkdir();p2=tmp_path/'two';p2.mkdir()
    one,two=state.projects.register(str(p1)),state.projects.register(str(p2))
    state.authorization.grant_project(user.id,one['id'],['files-read','agent-view','agent-control'])
    creds=asyncio.run(identity.login('local-password',{'username':'operator','password':PASSWORD},peer='local'))
    headers={'Authorization':f'Bearer {creds.access_token}'}
    def gql(query,variables=None):return client.post('/graphql',headers=headers,json={'query':query,'variables':variables or {}}).json()
    rows=gql('{projects{id}}')['data']['projects']
    assert rows==[{'id':one['id']}]
    assert gql('query($id:String!){project(project_id:$id){id}}',{'id':two['id']}).get('errors')
    own=gql('mutation{create_conversation(input:{title:"owned"}){id}}')['data']['create_conversation']['id']
    foreign=state.agent_store.create_conversation(title='foreign')
    assert gql('{conversations{id}}')['data']['conversations']==[{'id':own}]
    assert gql('query($id:String!){conversation(conversation_id:$id){id}}',{'id':foreign['id']}).get('errors')
    assert gql('query($path:String!){fs(path:$path){path}}',{'path':str(p2)}).get('errors')


def test_dynamic_adapter_test_atomic_activation_and_restart(tmp_path):
    import jwt
    import secrets
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    identity,state,client,admin=client_fixture(tmp_path)
    principal=identity.create_principal('Enterprise viewer',list(ROLES['viewer']))
    state.authorization.set_role(principal.id,'viewer')
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo).decode()
    config={'version':1,'adapters':[{'kind':'signed-assertion','id':'enterprise','label':'Enterprise',
            'issuer':'company','audience':'termx-sign-in','public_key':public}],
            'bindings':[{'issuer':'company','subject':'employee','principal_id':principal.id}]}
    def assertion():
        now=int(time())
        return jwt.encode({'iss':'company','aud':'termx-sign-in','sub':'employee','iat':now,'exp':now+120,'jti':secrets.token_hex(16)},key,algorithm='EdDSA',headers={'typ':'termx-identity+jwt'})
    activate={'configuration':config,'session_policy':'revoke'}
    assert client.put('/auth/admin/adapters/configuration',headers=admin,json=activate).status_code==409
    assert 'enterprise' not in identity.adapters
    assert identity.principal_for(__import__('termx.identity',fromlist=['Identity']).Identity('company','employee','test')) is None
    bad=client.post('/auth/admin/adapters/configuration/test',headers=admin,json={'configuration':config,'adapter_id':'enterprise','evidence':{'assertion':'invalid'}})
    assert bad.status_code==401
    tested=client.post('/auth/admin/adapters/configuration/test',headers=admin,json={'configuration':config,'adapter_id':'enterprise','evidence':{'assertion':assertion()}})
    assert tested.status_code==200,tested.text
    assert tested.json()['login_verified'] is True
    # A changed policy invalidates the exact configuration proof.
    identity.set_scopes(principal.id,list(ROLES['viewer']))
    assert client.put('/auth/admin/adapters/configuration',headers=admin,json=activate).status_code==409
    assert client.post('/auth/admin/adapters/configuration/test',headers=admin,json={'configuration':config,'adapter_id':'enterprise','evidence':{'assertion':assertion()}}).status_code==200
    active=client.put('/auth/admin/adapters/configuration',headers=admin,json=activate)
    assert active.status_code==200,active.text
    assert active.json()['version']==1
    recovered=AuthenticationService(identity.path)
    assert 'enterprise' in recovered.adapters and recovered.has_runtime_configuration
    credentials=asyncio.run(recovered.login('enterprise',{'assertion':assertion()},peer='local'))
    assert recovered.resolve(credentials.access_token).principal.id==principal.id
    assert client.put('/auth/admin/adapters/enterprise',headers=admin,json={'enabled':False,'session_policy':'revoke'}).status_code==200
    assert recovered.resolve(credentials.access_token) is None
    with pytest.raises(AuthenticationError):asyncio.run(recovered.login('enterprise',{'assertion':assertion()},peer='local'))
    # Explicit local recovery restores disabled local entry after provider loss.
    recovery=client.post('/auth/admin/recovery-code',headers=admin).json()['recovery_code']
    with identity._db() as db:
        db.execute("INSERT INTO adapter_status (adapter_id,enabled) VALUES ('local-password',0) ON CONFLICT(adapter_id) DO UPDATE SET enabled=0")
    assert client.post('/auth/recovery',headers={'Origin':'https://localhost'},json={'recovery_code':recovery,'password':'recovery-final-password-123'}).status_code==200
    assert asyncio.run(recovered.login('local-password',{'username':'owner','password':'recovery-final-password-123'},peer='local'))


def test_staged_oidc_browser_login_policy_proof_and_atomic_activation(tmp_path,monkeypatch):
    import base64
    import hashlib
    import json
    import httpx
    import jwt
    from urllib.parse import parse_qs,urlsplit
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
    from termx.identity_adapters import OidcAdapter
    identity,state,client,admin=client_fixture(tmp_path)
    principal=identity.create_principal('OIDC viewer',list(ROLES['viewer']))
    state.authorization.set_role(principal.id,'viewer')
    key=generate_private_key(public_exponent=65537,key_size=2048)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='provider-key',alg='RS256',use='sig')
    shared={}
    def provider(request):
        if request.url.path.endswith('openid-configuration'):
            return httpx.Response(200,json={'issuer':'https://idp.example','authorization_endpoint':'https://idp.example/authorize','token_endpoint':'https://idp.example/token','jwks_uri':'https://idp.example/jwks','code_challenge_methods_supported':['S256']})
        if request.url.path=='/jwks':return httpx.Response(200,json={'keys':[jwk]})
        if request.url.path=='/token':
            form=parse_qs(request.content.decode())
            actual=base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
            assert actual==shared['code_challenge'][0]
            now=int(time())
            token=jwt.encode({'iss':'https://idp.example','aud':'test-client','sub':'sso-subject','iat':now,'exp':now+60,'nonce':shared['nonce'][0]},key,algorithm='RS256',headers={'kid':'provider-key'})
            return httpx.Response(200,json={'id_token':token})
        raise AssertionError(request.url)
    # Inject only provider transport; the real PKCE, JWT, binding and HTTP
    # callback paths still execute. No external IdP/browser is used.
    original=OidcAdapter._request
    async def request(adapter,method,url,**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            adapter.client=http
            return await original(adapter,method,url,**kwargs)
    monkeypatch.setattr(OidcAdapter,'_request',request)
    config={'version':1,'adapters':[{'kind':'oidc','id':'sso','label':'Company','issuer':'https://idp.example','client_id':'test-client','redirect_uri':'https://localhost/auth/oidc/sso/callback'}],
            'bindings':[{'issuer':'https://idp.example','subject':'sso-subject','principal_id':principal.id}]}
    started=client.post('/auth/admin/adapters/configuration/begin',headers=admin,json={'configuration':config,'adapter_id':'sso'})
    assert started.status_code==200,started.text
    shared.update(parse_qs(urlsplit(started.json()['authorization_url']).query))
    callback=client.get('/auth/oidc/sso/callback',params={'code':'real-code','state':shared['state'][0]},follow_redirects=False)
    assert callback.status_code==303,callback.text
    assert callback.headers['location']=='/?auth_adapter_test=passed'
    # Testing doesn't exchange or overwrite the administrator's session.
    assert client.get('/auth/admin/principals',headers=admin).status_code==200
    activated=client.put('/auth/admin/adapters/configuration',headers=admin,json={'configuration':config,'session_policy':'expire'})
    assert activated.status_code==200,activated.text
    assert 'sso' in AuthenticationService(identity.path).adapters


def test_socket_closes_when_project_grant_changes_without_scope_change(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    identity,state,client,admin=client_fixture(tmp_path)
    principal=identity.create_local_user('operator',PASSWORD,list(ROLES['operator']))
    state.authorization.set_role(principal.id,'operator')
    state.authorization.grant_project(principal.id,'project',['agent-view'])
    credentials=asyncio.run(identity.login('local-password',{'username':'operator','password':PASSWORD},peer='local'))
    with client.websocket_connect('wss://localhost/graphql',subprotocols=['graphql-transport-ws'],headers={'Authorization':f'Bearer {credentials.access_token}'}) as socket:
        socket.send_json({'type':'connection_init','payload':{}})
        assert socket.receive_json()['type']=='connection_ack'
        state.authorization.revoke_project(principal.id,'project')
        with pytest.raises(WebSocketDisconnect) as exc:socket.receive_json()
        assert exc.value.code==4401


def test_setup_first_role_available_to_background_workers(tmp_path):
    identity=AuthenticationService(tmp_path/'identity.sqlite3')
    authz=AuthorizationService(identity)
    owner=identity.setup_owner('first',PASSWORD)
    assert authz.role(owner.id)=='owner'
    assert authz.require_principal(owner,'host-admin').permitted
