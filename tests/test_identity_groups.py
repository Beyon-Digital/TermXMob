"""Current canonical group authority; real TLS verified claims, no provider calls."""
import asyncio
from time import time
import httpx
import pytest
from fastapi import HTTPException
from termx.identity import Identity,AuthenticationError
from termx.identity_adapters import OidcAdapter,OidcConfig
from termx.authorization import ROLES
from test_authorization import fixture,client_fixture,PASSWORD
from test_oidc_tls_interoperability import tls_idp


def test_group_membership_project_resource_isolation_snapshot_expiry_and_revocation(tmp_path,monkeypatch):
    identity,authz,owner,alice,bob,login=fixture(tmp_path)
    identity.set_scopes(alice.id,list(ROLES['viewer']));authz.set_role(alice.id,'viewer')
    org=identity.groups.organization('Engineering',owner.id)
    group=identity.groups.save(label='Developers',organization_id=org['id'],role='operator',trusted_execution=True,actor_id=owner.id)
    identity.groups.grant(group['id'],'one',['files-read','agent-view','agent-run'],actor_id=owner.id)
    identity.groups.member(group['id'],alice.id,present=True,expires=time()+60,actor_id=owner.id)
    token=login('alice');actor=identity.resolve(token).principal
    assert actor.groups==(group['id'],) and actor.organizations==(org['id'],)
    assert authz.can(token,'agent-run',project_id='one')
    assert not authz.can(token,'agent-run',project_id='two')
    assert not authz.can(token,'host-admin') and not authz.can(login('bob'),'agent-view',project_id='one')
    authz.claim(token,'conversation','owned','one')
    snapshot=authz.resource_snapshot(actor,'agent-view','conversation',['owned'])
    assert snapshot.allowed_ids=={'owned'} and snapshot.expires_at
    import termx.identity_groups as group_module
    import termx.authorization as policy_module
    expired=snapshot.expires_at+1
    monkeypatch.setattr(group_module,'time',lambda:expired)
    monkeypatch.setattr(policy_module,'time',lambda:expired)
    assert not authz.can(token,'agent-view',project_id='one')
    with pytest.raises(HTTPException):authz.validate_resource_snapshot(snapshot)
    monkeypatch.setattr(group_module,'time',time)
    monkeypatch.setattr(policy_module,'time',time)
    authz.grant_project(alice.id,'two',['files-read'])
    assert authz.can(token,'files-read',project_id='two'),'Individual project grants must survive group expiry'
    identity.groups.member(group['id'],alice.id,present=True,actor_id=owner.id)
    token=login('alice');session=identity.resolve(token)
    identity.groups.member(group['id'],alice.id,present=False,actor_id=owner.id)
    assert identity.resolve(token) is None and identity.execution_session(session.session_id) is None
    assert authz.role(alice.id)=='viewer'


def test_admin_groups_http_current_authority_claim_trust_and_revision(tmp_path):
    identity,state,client,admin=client_fixture(tmp_path)
    alice=identity.create_local_user('group-viewer',PASSWORD,list(ROLES['viewer']));state.authorization.set_role(alice.id,'viewer')
    token=asyncio.run(identity.login('local-password',{'username':'group-viewer','password':PASSWORD},peer='local'))
    assert client.get('/auth/admin/groups',headers={'Authorization':'Bearer '+token.access_token}).status_code==403
    created=client.post('/auth/admin/groups',headers=admin,json={'label':'Readers'});assert created.status_code==200,created.text
    group=created.json()
    assert client.post('/auth/admin/groups',headers=admin,json={**{k:group[k] for k in ('label','role','trusted_execution','organization_id')},'identifier':group['id'],'revision':99}).status_code==400
    assert client.put('/auth/admin/groups/'+group['id']+'/mappings',headers=admin,json={'issuer':'https://untrusted.example','claim_value':'admins','present':True}).status_code==400
    issued=client.put('/auth/admin/groups/'+group['id']+'/members',headers=admin,json={'principal_id':alice.id,'present':True});assert issued.status_code==200
    assert identity.resolve(token.access_token) is None
    assert client.get('/auth/admin/groups',headers=admin).json()['groups'][0]['memberships'][0]['principal_id']==alice.id
    assert client.put('/auth/admin/groups/'+group['id']+'/members',headers=admin,json={'principal_id':alice.id,'present':True,'expires':0}).status_code==400


def test_oidc_actual_tls_group_claim_mapping_refresh_removal_and_no_claim_role_inference(tls_idp,tmp_path):
    identity,authz,owner,alice,bob,login=fixture(tmp_path)
    authz.set_role(alice.id,'viewer')
    issuer=tls_idp['issuer'];identity.map_identity(Identity(issuer,'stable-subject','admin-binding'),alice.id)
    async def run():
        async with httpx.AsyncClient(verify=tls_idp['trust'],trust_env=False) as transport:
            adapter=OidcAdapter(identity,OidcConfig('group-sso','Group SSO',issuer,'client','https://localhost/auth/oidc/group-sso/callback',groups_claim='groups',membership_ttl=60),client=transport)
            identity.register_adapter(adapter)
            group=identity.groups.save(label='Project readers',role='viewer',actor_id=owner.id)
            identity.groups.grant(group['id'],'one',['files-read'],actor_id=owner.id)
            identity.groups.mapping(group['id'],issuer,'engineering-readers',present=True,actor_id=owner.id)
            async def sign_in(groups):
                tls_idp['groups']=groups
                url,binding=await adapter.begin();reply=await transport.get(url)
                from urllib.parse import urlsplit,parse_qs
                params=parse_qs(urlsplit(reply.headers['location']).query)
                return await identity.login(adapter.id,{'state':params['state'][0],'code':params['code'][0],'binding':binding},peer='local')
            first=await sign_in(['engineering-readers','admin'])
            assert authz.can(first.access_token,'files-read',project_id='one')
            assert not authz.can(first.access_token,'host-admin'),'Unmapped admin text is not authority'
            with identity._db() as db:
                membership=db.execute('SELECT expires FROM group_memberships WHERE principal_id=?',(alice.id,)).fetchone()
            assert time()<membership[0]<=time()+60
            second=await sign_in([])
            assert identity.resolve(first.access_token) is None
            with pytest.raises(AuthenticationError):identity.refresh(first.refresh_token)
            assert not authz.can(second.access_token,'files-read',project_id='one')
            assert identity.resolve(second.access_token).principal.groups==()
            assert all(path in tls_idp['requests'] for path in ('/.well-known/openid-configuration','/authorize','/token','/jwks'))
    asyncio.run(run())


def test_group_removal_closes_live_socket_before_next_effect(tmp_path):
    from types import SimpleNamespace
    from termx.identity_guard import SessionGuard
    identity,authz,owner,alice,bob,login=fixture(tmp_path)
    group=identity.groups.save(label='Temporary operators',role='operator',actor_id=owner.id)
    identity.groups.member(group['id'],alice.id,present=True,actor_id=owner.id)
    token=login('alice')
    async def run():
        incoming,outgoing=asyncio.Queue(),asyncio.Queue();effects=[]
        async def app(scope,receive,send):
            await send({'type':'websocket.accept'})
            while True:
                message=await receive()
                if message['type']=='websocket.disconnect':return
                effects.append(message['text']);await send({'type':'websocket.send','text':'ack'})
        scope={'type':'websocket','path':'/api/group-fixture','scheme':'ws','client':('127.0.0.1',1234),'query_string':b'',
               'headers':[(b'authorization',('Bearer '+token).encode())]}
        guard=asyncio.create_task(SessionGuard(app,SimpleNamespace(identity=identity,auth=authz.auth))(scope,incoming.get,outgoing.put))
        try:
            assert (await asyncio.wait_for(outgoing.get(),5))['type']=='websocket.accept'
            await incoming.put({'type':'websocket.receive','text':'allowed'})
            assert (await asyncio.wait_for(outgoing.get(),5))['text']=='ack'
            await asyncio.to_thread(identity.groups.member,group['id'],alice.id,present=False,actor_id=owner.id)
            await incoming.put({'type':'websocket.receive','text':'denied-after-removal'})
            assert await asyncio.wait_for(outgoing.get(),5)=={'type':'websocket.close','code':4401}
            await asyncio.wait_for(guard,5);assert effects==['allowed']
        finally:guard.cancel();await asyncio.gather(guard,return_exceptions=True)
    asyncio.run(run())


def test_signed_custom_group_evidence_pinned_claim_mapping_and_malformed_claims(tmp_path,monkeypatch):
    import uuid
    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    from termx.identity_adapters import SignedAssertionAdapter
    identity,authz,owner,alice,bob,login=fixture(tmp_path)
    authz.set_role(alice.id,'viewer');issuer='urn:termx:enterprise-test'
    identity.map_identity(Identity(issuer,'stable-user','admin-binding'),alice.id)
    key=Ed25519PrivateKey.generate()
    adapter=SignedAssertionAdapter(identity,adapter_id='enterprise-test',label='Controlled custom assertion',issuer=issuer,audience='termx-test',
        public_key=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo).decode(),groups_claim='teams',membership_ttl=60)
    identity.register_adapter(adapter)
    group=identity.groups.save(label='Configured administrators',role='admin',actor_id=owner.id)
    identity.groups.mapping(group['id'],issuer,'approved-administrators',present=True,actor_id=owner.id)
    def assertion(groups):
        now=int(time())
        return jwt.encode({'iss':issuer,'aud':'termx-test','sub':'stable-user','iat':now,'exp':now+60,'jti':uuid.uuid4().hex,'teams':groups},key,algorithm='EdDSA',headers={'typ':'termx-identity+jwt'})
    async def run():
        credentials=await identity.login(adapter.id,{'assertion':assertion(['approved-administrators'])},peer='local')
        assert authz.can(credentials.access_token,'host-admin')
        snapshot=authz.resource_snapshot(identity.resolve(credentials.access_token).principal,'agent-view','conversation',['foreign-resource'])
        assert snapshot.administrator and snapshot.allowed_ids=={'foreign-resource'}
        with pytest.raises(AuthenticationError):await identity.login(adapter.id,{'assertion':assertion('approved-administrators')},peer='local')
        # Freshly proved membership after expiry is a new authority grant. It
        # must revoke old device sessions rather than silently re-elevate them.
        import termx.identity_groups as group_module
        monkeypatch.setattr(group_module,'time',lambda:time()+61)
        assert not authz.can(credentials.access_token,'host-admin')
        with pytest.raises(HTTPException):authz.validate_resource_snapshot(snapshot)
        old=credentials
        credentials=await identity.login(adapter.id,{'assertion':assertion(['approved-administrators'])},peer='local')
        assert identity.resolve(old.access_token) is None
        with pytest.raises(AuthenticationError):identity.refresh(old.refresh_token)
        assert authz.can(credentials.access_token,'host-admin')
        identity.groups.mapping(group['id'],issuer,'approved-administrators',present=False,actor_id=owner.id)
        assert identity.resolve(credentials.access_token) is None and authz.role(alice.id)=='viewer'
        assert identity.principal_by_id(alice.id).groups==()
    asyncio.run(run())
