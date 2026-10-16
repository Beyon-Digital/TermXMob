"""Real verified HTTPS/WSS using throwaway CAs; never edits system trust."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import ssl

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient
import websockets

from termx.auth import Auth
from termx.cli import _start_server, build_parser
from termx.identity_guard import SessionGuard
from termx.lan_tls import load_identity, ssl_context, spki, supported_name, advertised_names
from termx.net import http_urls


def test_identity_persists_renews_addresses_and_requires_deliberate_rotation(tmp_path):
    names=['127.0.0.1','laptop.local','192.168.1.10','fd00::1']
    initial=load_identity(tmp_path,names)
    pins=initial.metadata(8788)['public_key_hashes']
    assert load_identity(tmp_path,names).metadata(8788)['public_key_hashes']==pins
    assert initial.renew(sorted(names)) is False
    # A new address renews the certificate without silently replacing identity.
    old_serial=initial.certificate.serial_number
    assert initial.renew(sorted([*names,'192.168.1.11']))
    assert initial.certificate.serial_number!=old_serial
    assert [spki(initial.ca),spki(initial.certificate)]==pins
    assert initial.renew(initial.names,now=datetime.now(timezone.utc)+timedelta(days=24))
    rotated=load_identity(tmp_path,names,rotate=True)
    assert not set(rotated.metadata(8788)['public_key_hashes']) & set(pins)
    sans=rotated.certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert 'laptop.local' in sans.get_values_for_type(x509.DNSName)
    assert {str(ip) for ip in sans.get_values_for_type(x509.IPAddress)}=={'127.0.0.1','192.168.1.10','fd00::1'}
    assert all(path.stat().st_mode&0o077==0 for path in rotated.root.iterdir())
    assert rotated.root.stat().st_mode&0o077==0


@pytest.mark.parametrize('name',['8.8.8.8','example.com','*.local','x.local/evil','fe80::1%eth0','0.0.0.0','::','x..local','-host.local'])
def test_unsupported_names_fail_closed(name):
    with pytest.raises(ValueError):supported_name(name)


def test_corruption_missing_state_and_symlinks_do_not_reset_trust(tmp_path):
    identity=load_identity(tmp_path,['127.0.0.1'])
    path=identity.root/'identity.json'
    saved=path.read_text()
    path.write_text('{}')
    with pytest.raises(RuntimeError,match='will not reset automatically'):load_identity(tmp_path,['127.0.0.1'])
    path.unlink()
    with pytest.raises(RuntimeError,match='missing'):load_identity(tmp_path,['127.0.0.1'])
    target=tmp_path/'fixture.json';target.write_text(saved);path.symlink_to(target)
    with pytest.raises(PermissionError,match='symbolic link'):load_identity(tmp_path,['127.0.0.1'])


def test_loopback_default_and_discovery_never_advertise_plaintext_lan(monkeypatch):
    assert build_parser().parse_args([]).host=='127.0.0.1'
    monkeypatch.setattr('termx.lan_tls.lan_ipv4_addresses',lambda:['192.168.3.4'])
    assert '192.168.3.4' in advertised_names('0.0.0.0',['desk.local'])
    assert '192.168.3.4' not in advertised_names('127.0.0.1',[])
    assert http_urls(8787)==['http://127.0.0.1:8787']


def test_real_https_wss_verify_ca_hostname_origin_and_auth(tmp_path):
    async def run():
        identity=load_identity(tmp_path/'host',['127.0.0.1','localhost'])
        other=load_identity(tmp_path/'other',['127.0.0.1'])
        verified=ssl.create_default_context(cafile=str(identity.root/'ca.pem'))
        wrong=ssl.create_default_context(cafile=str(other.root/'ca.pem'))
        app=FastAPI()
        state=SimpleNamespace(identity=SimpleNamespace(configured=False,resolve=lambda _:None),auth=Auth(passcode='isolated-fixture-secret'))
        app.add_middleware(SessionGuard,state=state)
        @app.get('/api/fixture')
        def fixture():return {'ok':True}
        @app.websocket('/api/socket')
        async def socket(ws:WebSocket):
            if not state.auth.check(ws.headers.get('authorization','').removeprefix('Bearer ')):
                await ws.close(code=4401);return
            await ws.accept()
            await ws.send_text('verified fixture')
            await ws.close()
        started=await _start_server(app,'127.0.0.1',0,tls=ssl_context(identity))
        assert started
        server,task,port=started
        base=f'https://127.0.0.1:{port}'
        try:
            async with httpx.AsyncClient(verify=verified,trust_env=False) as client:
                assert (await client.get(base+'/api/fixture')).json()=={'ok':True}
            async with httpx.AsyncClient(verify=wrong,trust_env=False) as client:
                with pytest.raises(httpx.ConnectError):await client.get(base+'/api/fixture')
            # A trusted CA still does not authorize a hostname outside the SAN.
            with pytest.raises(ssl.SSLCertVerificationError):
                reader,writer=await asyncio.open_connection('127.0.0.1',port,ssl=verified,server_hostname='wrong.local')
            async with websockets.connect(f'wss://127.0.0.1:{port}/api/socket',ssl=verified,origin=base,additional_headers={'Authorization':'Bearer isolated-fixture-secret'}) as ws:
                assert await ws.recv()=='verified fixture'
            with pytest.raises(websockets.exceptions.InvalidStatus):
                async with websockets.connect(f'wss://127.0.0.1:{port}/api/socket',ssl=verified,origin='https://evil.local',additional_headers={'Authorization':'Bearer isolated-fixture-secret'}):pass
            with pytest.raises(websockets.exceptions.InvalidStatus):
                async with websockets.connect(f'wss://127.0.0.1:{port}/api/socket',ssl=verified):pass
        finally:
            server.should_exit=True
            await asyncio.wait_for(task,10)
    asyncio.run(run())


@pytest.mark.parametrize('path',['/auth/methods','/auth/login','/auth/pair/exchange','/api/health','/graphql'])
def test_remote_http_rejects_even_spoofed_forwarded_scheme(path):
    app=FastAPI()
    state=SimpleNamespace(identity=SimpleNamespace(configured=False),auth=Auth(passcode='fixture'))
    app.add_middleware(SessionGuard,state=state)
    client=TestClient(app,client=('192.168.1.2',4321))
    response=client.get(path,headers={'X-Forwarded-Proto':'https'})
    assert response.status_code==403
    assert response.json()['detail']=='TLS is required for remote access'


def test_connect_metadata_prefers_tls_lan_and_never_a_guessed_http_address(tmp_path):
    from termx.lan_tls import connection_urls, binding_for
    from termx.graphql.domains.core import _connect_target
    identity=load_identity(tmp_path,['localhost','127.0.0.1','::1','desk.local','192.168.1.4'])
    state=SimpleNamespace(lan_identity=identity,lan_port=8788,port=8787,tunnels=SimpleNamespace(status_public=lambda:{}))
    urls=connection_urls(state)
    assert all(url.startswith('https://') for url in urls)
    assert urls[0] in {'https://desk.local:8788','https://192.168.1.4:8788'}
    assert _connect_target(state)[0]==urls[0]
    assert binding_for(state,'https://desk.local:8788')['ca_der']==identity.metadata(8788)['ca_der']
    assert binding_for(state,'https://unlisted.local:8788') is None


def test_tls_settings_require_admin_csrf_and_stage_only_private_valid_configuration(tmp_path):
    from test_managed_pairing import fixture
    from termx.lan_tls_http import mount_lan_tls
    state,app,owner,client,headers=fixture(tmp_path)
    mount_lan_tls(app,state)
    assert client.get('/auth/lan-tls').status_code==403
    assert client.get('/auth/lan-tls',headers=headers).json()['active']=={'enabled':False}
    response=client.put('/auth/lan-tls',headers=headers,json={'enabled':True,'host':'0.0.0.0','port':8788,'names':['desk.local','192.168.1.4']})
    assert response.status_code==200,response.text
    assert response.json()['restart_required']
    assert getattr(state,'lan_identity',None) is None
    assert client.put('/auth/lan-tls',headers=headers,json={'enabled':True,'names':['public.example']}).status_code==400
    assert client.put('/auth/lan-tls',headers=headers,json={'enabled':True,'host':'desk.local'}).status_code==400
    assert client.put('/auth/lan-tls',headers=headers,json={'enabled':True,'port':0}).status_code==422
    assert client.get('/auth/lan-tls/ca.pem',headers=headers).status_code==409
    state.lan_identity=load_identity(tmp_path/'ca',['127.0.0.1'])
    state.lan_port=8788
    ca=client.get('/auth/lan-tls/ca.pem',headers=headers)
    assert ca.status_code==200 and 'PRIVATE KEY' not in ca.text
    assert ca.headers['cache-control']=='no-store'


def test_address_advertisement_matches_listener_family_and_interface(monkeypatch):
    monkeypatch.setattr('termx.lan_tls.lan_ipv4_addresses',lambda:['192.168.1.4'])
    assert advertised_names('0.0.0.0',[])==['127.0.0.1','192.168.1.4','localhost']
    assert advertised_names('::',[])==['::1','localhost']
    assert advertised_names('192.168.1.4',[])==['192.168.1.4']
    with pytest.raises(ValueError,match='address family'):advertised_names('0.0.0.0',['fd00::1'])
    with pytest.raises(ValueError,match='interface'):advertised_names('192.168.1.4',['192.168.1.5'])
