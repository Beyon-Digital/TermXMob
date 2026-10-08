"""Lifetime configuration applies to current devices, never renewing old grants."""
import asyncio
from time import time
from types import SimpleNamespace

import pytest

from termx.identity import AuthenticationError,AuthenticationService
from termx.identity_guard import SessionGuard
from termx.identity_pairing import ManagedPairing
from termx.authorization import ROLES
from test_authorization import fixture,client_fixture,PASSWORD


def credentials(identity,name):
    return asyncio.run(identity.login('local-password',{'username':name,'password':PASSWORD},peer='fixture'))


def test_policy_defaults_persistence_and_no_widening_or_resurrection(tmp_path):
    identity,authz,owner,alice,bob,_=fixture(tmp_path)
    old=credentials(identity,'alice');live=credentials(identity,'bob')
    pairing=ManagedPairing(identity)
    proof=pairing.issue(identity.resolve(old.access_token),['machine-view'])
    socket=pairing.socket(identity.resolve(old.access_token),'/graphql')
    assert identity.session_policy.inventory()=={'idle_ttl_seconds':86400,'absolute_ttl_seconds':2592000,'access_ttl_seconds':300,'revision':1}
    with identity._db() as db:
        db.execute('UPDATE sessions SET created=?,last_seen=? WHERE id=?',(time()-800,time()-700,old.session_id))
    changed=identity.session_policy.update(revision=1,idle_ttl_seconds=600,absolute_ttl_seconds=1200,actor_id=owner.id)
    assert changed['expired_sessions']==1
    assert identity.resolve(old.access_token) is None and identity.execution_session(old.session_id) is None
    with pytest.raises(AuthenticationError):identity.refresh(old.refresh_token)
    with pytest.raises(AuthenticationError):pairing.exchange(proof['ticket'],host_id=identity.host_id,device_name='Expired sponsor')
    with pytest.raises(AuthenticationError):pairing.admit_socket(socket['ticket'],host_id=identity.host_id,path='/graphql')
    with identity._db() as db:
        cap=db.execute('SELECT expires,idle_ttl_seconds FROM sessions WHERE id=?',(live.session_id,)).fetchone()
    assert cap['idle_ttl_seconds']==600
    legacy=credentials(identity,'alice')
    # A migrated/older writer may carry the historical default in its row;
    # current policy already constrained it, and an increase must freeze that
    # prior effective limit rather than revive the larger historical values.
    with identity._db() as db:
        db.execute('UPDATE sessions SET idle_ttl_seconds=86400,expires=created+2592000 WHERE id=?',(legacy.session_id,))
    identity.session_policy.update(revision=2,idle_ttl_seconds=3600,absolute_ttl_seconds=7200,actor_id=owner.id)
    assert identity.resolve(old.access_token) is None
    with identity._db() as db:
        unchanged=db.execute('SELECT expires,idle_ttl_seconds FROM sessions WHERE id=?',(live.session_id,)).fetchone()
    assert tuple(unchanged)==tuple(cap)
    with identity._db() as db:
        migrated=db.execute('SELECT * FROM sessions WHERE id=?',(legacy.session_id,)).fetchone()
    assert migrated['idle_ttl_seconds']==600 and migrated['expires']-migrated['created']==1200
    new=credentials(identity,'alice')
    assert new.refresh_expires_in==7200 and new.expires_in==300
    assert AuthenticationService(identity.path).session_policy.inventory()['revision']==3
    with pytest.raises(ValueError):identity.session_policy.update(revision=1,idle_ttl_seconds=600,absolute_ttl_seconds=1200,actor_id=owner.id)


def test_policy_admin_http_and_cookie_native_pairing_lifetimes(tmp_path):
    identity,state,client,admin=client_fixture(tmp_path)
    viewer=identity.create_local_user('policy-viewer',PASSWORD,list(ROLES['viewer']));state.authorization.set_role(viewer.id,'viewer')
    denied={'Authorization':'Bearer '+credentials(identity,'policy-viewer').access_token}
    path='/auth/admin/session-policy'
    assert client.get(path,headers=denied).status_code==403
    policy={'revision':1,'idle_ttl_seconds':600,'absolute_ttl_seconds':1200}
    assert client.put(path,headers=denied,json=policy).status_code==403
    assert client.put(path,headers=admin,json={**policy,'idle_ttl_seconds':True}).status_code==422
    assert client.put(path,headers=admin,json={**policy,'idle_ttl_seconds':1300}).status_code==400
    changed=client.put(path,headers=admin,json=policy);assert changed.status_code==200,changed.text
    assert client.put(path,headers=admin,json=policy).status_code==400
    response=client.post('/auth/login',headers={'Origin':'https://localhost'},json={'method':'local-password','username':'owner','password':PASSWORD,'transport':'cookie'})
    assert response.status_code==200,response.text
    cookies=response.headers.get_list('set-cookie')
    assert all('Max-Age=1200' in row for row in cookies if row.startswith(('termx_refresh=','termx_csrf=')))
    assert response.json()['refresh_expires_in']==1200
    bearer=credentials(identity,'owner')
    assert bearer.refresh_expires_in==1200
    paired=state.managed_pairing.exchange(state.managed_pairing.issue(identity.resolve(bearer.access_token),['machine-view'])['ticket'],host_id=identity.host_id,device_name='Synthetic mobile')
    assert paired.refresh_expires_in==1200
    with identity._db() as db:
        row=db.execute('SELECT * FROM sessions WHERE id=?',(paired.session_id,)).fetchone()
        assert row['idle_ttl_seconds']==600 and row['expires']-row['created']==1200
    with identity._db() as db:
        db.execute('UPDATE sessions SET expires=? WHERE id=?',(time()+120,bearer.session_id))
    refreshed=identity.refresh(bearer.refresh_token)
    assert 118<=refreshed.expires_in<=120 and 119<=refreshed.refresh_expires_in<=120


def test_policy_reduction_closes_live_socket_before_effect(tmp_path):
    identity,authz,owner,alice,bob,_=fixture(tmp_path)
    actor=credentials(identity,'alice')
    with identity._db() as db:db.execute('UPDATE sessions SET last_seen=? WHERE id=?',(time()-700,actor.session_id))
    async def run():
        incoming,outgoing=asyncio.Queue(),asyncio.Queue();effects=[]
        async def app(scope,receive,send):
            await send({'type':'websocket.accept'})
            while True:
                message=await receive()
                if message['type']=='websocket.disconnect':return
                effects.append(message['text']);await send({'type':'websocket.send','text':'ack'})
        scope={'type':'websocket','path':'/api/policy-fixture','scheme':'ws','client':('127.0.0.1',1234),'query_string':b'',
               'headers':[(b'authorization',('Bearer '+actor.access_token).encode())]}
        guard=asyncio.create_task(SessionGuard(app,SimpleNamespace(identity=identity,auth=authz.auth))(scope,incoming.get,outgoing.put))
        try:
            assert (await asyncio.wait_for(outgoing.get(),5))['type']=='websocket.accept'
            await incoming.put({'type':'websocket.receive','text':'before-tightening'})
            assert (await asyncio.wait_for(outgoing.get(),5))['text']=='ack'
            await asyncio.to_thread(identity.session_policy.update,revision=1,idle_ttl_seconds=600,absolute_ttl_seconds=1200,actor_id=owner.id)
            await incoming.put({'type':'websocket.receive','text':'after-tightening'})
            assert await asyncio.wait_for(outgoing.get(),5)=={'type':'websocket.close','code':4401}
            await asyncio.wait_for(guard,5);assert effects==['before-tightening']
        finally:guard.cancel();await asyncio.gather(guard,return_exceptions=True)
    asyncio.run(run())
