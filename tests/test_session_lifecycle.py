"""Lock is a session boundary, separate from job cancellation and host shutdown."""
import asyncio
from time import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from termx.identity import AuthenticationError, SessionLocked
from termx.identity_locks import SessionLocks
from test_managed_pairing import PASSWORD, fixture, issue, exchange


def test_lock_denies_transport_keeps_enrolled_job_and_unlock_same_narrow_device(tmp_path):
    state,app,owner,client,headers=fixture(tmp_path)
    proof=issue(client,headers,('machine-view','agent-view'))
    credentials=exchange(client,proof).json()
    token=credentials['access_token'];sid=credentials['session_id'];raw=credentials['refresh_token']
    actor=state.identity.resolve(token)
    ordinary=actor.principal
    assert client.post('/auth/lock',headers={'Authorization':'Bearer '+token}).status_code==200
    assert client.get('/auth/me',headers={'Authorization':'Bearer '+token}).status_code==423
    assert client.get('/api/fixture',headers={'Authorization':'Bearer '+token}).status_code==423
    assert state.identity.current_principal(ordinary) is None
    assert state.identity.session_by_id(sid) is None
    enrolled=state.identity.execution_session(sid)
    assert enrolled.principal.scopes==('machine-view','agent-view')
    assert state.identity.current_principal(enrolled.principal).authority_execution
    with pytest.raises(HTTPException):state.authorization.require_principal(enrolled.principal,'host-admin')
    assert state.identity.resolve(owner.access_token) # another session is unaffected
    response=client.post('/auth/refresh',json={'refresh_token':raw})
    assert response.status_code==423
    # The proof remains unconsumed; only an explicit same-account reauth unlocks.
    wrong=client.post('/auth/unlock',json={'refresh_token':raw,'username':'owner','password':'wrong'})
    assert wrong.status_code==401
    assert client.post('/auth/lock-state',json={'refresh_token':raw}).json()['locked']
    restored=client.post('/auth/unlock',json={'refresh_token':raw,'username':'owner','password':PASSWORD})
    assert restored.status_code==200,restored.text
    value=restored.json()
    assert value['session_id']==sid and value['refresh_token']!=raw
    assert state.identity.resolve(value['access_token']).principal.scopes==ordinary.scopes
    assert state.identity.resolve(token) is None # old access epoch never revives
    assert client.post('/auth/unlock',json={'refresh_token':raw,'username':'owner','password':PASSWORD}).status_code==401
    assert client.post('/auth/refresh',json={'refresh_token':raw}).status_code==401 # reuse still revokes family
    assert state.identity.resolve(value['access_token']) is None
    assert state.identity.execution_session(sid) is None


def test_cookie_unlock_csrf_and_locked_logout_rotate_no_plaintext(tmp_path):
    state,app,owner,client,headers=fixture(tmp_path)
    signed=client.post('/auth/login',json={'username':'owner','password':PASSWORD},headers={'Origin':'https://localhost'})
    assert signed.status_code==200
    sid=signed.json()['session_id'];csrf=signed.json()['csrf_token']
    browser={'Origin':'https://localhost','X-Termx-CSRF':csrf}
    assert client.post('/auth/lock',headers=browser).status_code==200
    assert client.get('/auth/lock-state').json()['session_id']==sid
    assert client.post('/auth/unlock',json={'username':'owner','password':PASSWORD},headers={'Origin':'https://localhost'}).status_code==403
    unlocked=client.post('/auth/unlock',json={'username':'owner','password':PASSWORD},headers=browser)
    assert unlocked.status_code==200 and unlocked.json()['session_id']==sid
    new_csrf=unlocked.json()['csrf_token']
    assert new_csrf!=csrf
    assert client.post('/auth/lock',headers={'Origin':'https://localhost','X-Termx-CSRF':new_csrf}).status_code==200
    assert client.post('/auth/logout',headers={'Origin':'https://localhost','X-Termx-CSRF':new_csrf}).status_code==200
    assert state.identity.execution_session(sid) is None
    assert PASSWORD.encode() not in state.identity.path.read_bytes()


def test_lock_pending_pair_proofs_revoke_and_authority_changes_deny_unlock(tmp_path):
    state,app,owner,client,headers=fixture(tmp_path)
    ticket=issue(client,headers)
    actor=state.identity.resolve(owner.access_token)
    assert client.post('/auth/lock',headers=headers).status_code==200
    assert exchange(client,ticket).status_code==401
    state.identity.disable(actor.principal.id)
    assert client.post('/auth/unlock',json={'refresh_token':owner.refresh_token,'username':'owner','password':PASSWORD}).status_code==401
    assert state.identity.execution_session(owner.session_id) is None


def test_host_stop_is_explicit_current_host_admin_only_and_isolated_callback(tmp_path):
    state,app,owner,client,headers=fixture(tmp_path)
    called=[]
    state.request_shutdown=lambda:called.append('isolated-fixture')
    proof=issue(client,headers,('machine-view',));paired=exchange(client,proof).json()
    other={'Authorization':'Bearer '+paired['access_token']}
    assert client.get('/auth/host/lifecycle',headers=other).status_code==403
    assert client.post('/auth/host/stop',headers=other,json={'host_id':state.identity.host_id,'acknowledge':True}).status_code==403
    assert client.get('/auth/host/lifecycle',headers=headers).json()['can_stop']
    assert client.post('/auth/host/stop',headers=headers,json={'host_id':state.identity.host_id}).status_code==400
    assert client.post('/auth/host/stop',headers=headers,json={'host_id':'wrong','acknowledge':True}).status_code==400
    assert called==[]
    assert client.post('/auth/host/stop',headers=headers,json={'host_id':state.identity.host_id,'acknowledge':True}).status_code==200
    assert called==['isolated-fixture']
    state.request_shutdown=None
    assert client.post('/auth/host/stop',headers=headers,json={'host_id':state.identity.host_id,'acknowledge':True}).status_code==503


def test_unlock_requires_same_canonical_principal(tmp_path):
    state,app,owner,client,headers=fixture(tmp_path)
    from termx.identity import Identity
    class Other:
        id='other';label='Other controlled adapter'
        async def authenticate(self,evidence):return Identity('controlled','different','assertion')
    principal=state.identity.create_principal('Other', ['machine-view'])
    state.identity.map_identity(Identity('controlled','different','assertion'),principal.id)
    state.identity.register_adapter(Other())
    client.post('/auth/lock',headers=headers)
    result=client.post('/auth/unlock',json={'method':'other','refresh_token':owner.refresh_token,'assertion':'fixture'})
    assert result.status_code==401
    assert state.session_locks.is_locked(state.session_locks.refresh_identity(owner.refresh_token))


def test_protect_surfaces_retains_private_barriers_only_matching_device(tmp_path):
    from termx.identity_locks import protect_surfaces
    from termx.desktop.recording import capture_privacy_revision, clear_capture_private, set_capture_private
    state,app,owner,client,headers=fixture(tmp_path)
    actor=state.identity.resolve(owner.access_token)
    rows=[{'id':'own','principal_id':actor.principal.id,'session_id':actor.session_id,'state':'agent'},
          {'id':'other-device','principal_id':actor.principal.id,'session_id':'other','state':'agent'},
          {'id':'other-account','principal_id':'foreign','session_id':actor.session_id,'state':'agent'}]
    changed=[]
    class Records:
        def list(self,kind):return rows if kind=='tab' else []
        def get(self,kind,id):return None
    def takeover(id,principal,**kw):
        changed.append(id)
        row=next(row for row in rows if row['id']==id);row['state']='private'
        set_capture_private('lock-fixture:'+id,expires_at=float('inf'),valid=lambda:row['state']=='private')
    state.browser=SimpleNamespace(records=Records(),takeover=takeover)
    try:
        asyncio.run(protect_surfaces(state,actor))
        state.session_locks.lock(actor)
        assert changed==['own']
        with pytest.raises(PermissionError):capture_privacy_revision()
        # Unlock cannot silently resume observation or revive an old handoff.
        restored=asyncio.run(state.session_locks.unlock(owner.refresh_token,'local-password',{'username':'owner','password':PASSWORD},peer='unlock-fixture'))
        assert restored.session_id==owner.session_id
        with pytest.raises(PermissionError):capture_privacy_revision()
        assert rows[1]['state']=='agent'
    finally:clear_capture_private('lock-fixture:own')


from test_oidc_tls_interoperability import tls_idp

def test_unlock_real_tls_oidc_pkce_same_session_browser_binding(tls_idp,tmp_path,monkeypatch):
    import httpx
    from urllib.parse import parse_qs,urlsplit
    from termx.identity import Identity
    from termx.identity_adapters import OidcAdapter,OidcConfig
    state,app,owner,client,headers=fixture(tmp_path)
    actor=state.identity.resolve(owner.access_token)
    adapter=OidcAdapter(state.identity,OidcConfig('tls-unlock','TLS unlock fixture',tls_idp['issuer'],'client','https://localhost/auth/oidc/tls-unlock/callback'))
    original=OidcAdapter._request
    async def request(adapter,method,url,**kw):
        async with httpx.AsyncClient(verify=tls_idp['trust'],trust_env=False) as transport:
            adapter.client=transport
            return await original(adapter,method,url,**kw)
    monkeypatch.setattr(OidcAdapter,'_request',request)
    state.identity.register_adapter(adapter)
    state.identity.map_identity(Identity(tls_idp['issuer'],'stable-subject','oidc'),actor.principal.id)
    assert client.post('/auth/lock',headers=headers).status_code==200
    begin=client.post('/auth/oidc/tls-unlock/unlock-begin',json={'refresh_token':owner.refresh_token})
    assert begin.status_code==200,begin.text
    with httpx.Client(verify=tls_idp['trust'],trust_env=False) as browser:
        authorized=browser.get(begin.json()['authorization_url'],follow_redirects=False)
    assert authorized.status_code==302
    callback=client.get(authorized.headers['location'],follow_redirects=False)
    assert callback.status_code==303,callback.text
    restored=client.get('/auth/me').json()
    assert restored['session_id']==owner.session_id and restored['principal']['id']==actor.principal.id
    assert state.identity.resolve(owner.access_token) is None
    assert client.get(authorized.headers['location'],follow_redirects=False).status_code==401
    assert '/token' in tls_idp['requests'] and '/jwks' in tls_idp['requests']


from test_managed_pairing import tls_host

def test_real_tls_cookie_lock_closes_existing_wss_then_same_sid_unlock(tls_host):
    import httpx
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed
    state,url,trust=tls_host
    with httpx.Client(base_url=url,verify=trust,trust_env=False) as client:
        logged=client.post('/auth/login',headers={'Origin':url},json={'username':'owner','password':PASSWORD})
        assert logged.status_code==200
        original=logged.json()['session_id']
        headers={'Origin':url,'X-Termx-CSRF':logged.json()['csrf_token']}
        with connect(url.replace('https:','wss:')+'/graphql',ssl=trust,proxy=None,origin=url,
                     additional_headers={'Cookie':'termx_access='+client.cookies['termx_access']}) as ws:
            ws.send('controlled browser fixture')
            assert ws.recv(timeout=5)
            assert client.post('/auth/lock',headers=headers).status_code==200
            assert client.get('/auth/me').status_code==423
            assert client.get('/auth/lock-state').json()['session_id']==original
            with pytest.raises(ConnectionClosed):ws.recv(timeout=5)
        assert client.post('/auth/refresh',headers=headers,json={}).status_code==423
        recovered=client.post('/auth/unlock',headers=headers,json={'username':'owner','password':PASSWORD})
        assert recovered.status_code==200
        assert recovered.json()['session_id']==original
        assert client.get('/auth/me').json()['session_id']==original


def test_queued_stop_current_revocation_prevents_effect_and_notifies_cancellation(tmp_path,monkeypatch):
    state,app,owner,client,headers=fixture(tmp_path)
    from termx import identity_http,notify
    import anyio
    calls=[];events=[]
    state.request_shutdown=lambda:calls.append('must-not-run')
    original=asyncio.sleep
    async def while_queued(duration):
        actor=state.identity.resolve(owner.access_token)
        state.identity.revoke(owner.session_id,actor.principal.id)
        await original(0)
    monkeypatch.setattr(identity_http.asyncio,'sleep',while_queued)
    monkeypatch.setattr(notify,'host_stop_cancelled',lambda:events.append('cancelled'))
    response=client.post('/auth/host/stop',headers=headers,json={'host_id':state.identity.host_id,'acknowledge':True})
    assert response.status_code==200
    assert events==['cancelled'] and calls==[]
    assert not getattr(state,'host_stop_requested',False)


def test_failed_transport_shutdown_still_locks_canonical_device(tmp_path):
    state,app,owner,client,headers=fixture(tmp_path)
    class BrokenRtc:
        def close_device(self,*args):raise RuntimeError('controlled peer closure failure')
    state.rtc=BrokenRtc()
    response=client.post('/auth/lock',headers=headers)
    assert response.status_code==503 and response.json()['detail'].startswith('Session locked;')
    assert state.identity.resolve(owner.access_token) is None
    assert client.post('/auth/lock-state',json={'refresh_token':owner.refresh_token}).json()['locked']
    assert client.post('/auth/unlock',json={'refresh_token':owner.refresh_token,'username':'owner','password':PASSWORD}).status_code==200


def test_locked_cookie_admission_keeps_origin_csrf_and_expired_401_distinctions(tmp_path):
    state,app,owner,client,headers=fixture(tmp_path)
    login=client.post('/auth/login',headers={'Origin':'https://localhost'},json={'username':'owner','password':PASSWORD})
    sid=login.json()['session_id'];csrf=login.json()['csrf_token']
    valid={'Origin':'https://localhost','X-Termx-CSRF':csrf}
    assert client.post('/auth/lock',headers=valid).status_code==200
    assert client.get('/api/fixture').status_code==423
    assert client.post('/api/fixture',headers={'Origin':'https://foreign.example','X-Termx-CSRF':csrf}).status_code==403
    assert client.post('/api/fixture',headers={'Origin':'https://localhost'}).status_code==403
    assert client.post('/api/fixture',headers=valid).status_code==423
    state.identity.revoke(sid,state.session_locks.refresh_identity(client.cookies['termx_refresh']).principal.id)
    assert client.get('/api/fixture').status_code==401
    assert client.post('/api/fixture',headers=valid).status_code==401
