"""Rendered desktop issuance -> native bearer recovery using a real isolated TLS host.

This proves the shared UI and canonical mobile transport port, not installed Expo
SecureStore or native webview operation. Reports contain no credential values.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
from time import monotonic,sleep

import httpx
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import NameOID
from playwright.sync_api import sync_playwright


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'fixture':'isolated canonical host over pinned TLS','steps':[],'errors':[],
            'limits':['Installed mobile SecureStore not exercised','Installed native desktop bridge not exercised','No provider or paid API calls']}
    page=None
    with tempfile.TemporaryDirectory(prefix='termx-rendered-pairing-') as temporary:
        root=Path(temporary)
        os.environ['TERMX_CONFIG_DIR']=str(root/'config');os.environ['TERMX_AGENTS_DIR']=str(root/'agents');os.environ['TERMX_ENGINE_STARTUP_REFRESH']='0'
        from termx.app import AppState,create_app
        state=AppState(passcode=None)
        owner=state.identity.setup_owner('pair-fixture-owner','rendered-pair-fixture-password-123')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        key=generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256()))
        certificate=root/'fixture-ca.pem';private=root/'fixture-key.pem'
        certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM));private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        spki=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        pinned=base64.b64encode(hashlib.sha256(spki).digest()).decode()
        trust=ssl.create_default_context(cafile=str(certificate))
        origin=f'https://localhost:{port}'
        server=uvicorn.Server(uvicorn.Config(app,ssl_certfile=str(certificate),ssl_keyfile=str(private),log_level='error',lifespan='on'))
        worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+15
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Isolated pairing host failed to start')
            sleep(.02)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.environ.get('TERMX_CHROMIUM_EXECUTABLE'),args=['--ignore-certificate-errors-spki-list='+pinned])
                context=browser.new_context(viewport={'width':1440,'height':1100},reduced_motion='reduce')
                page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.goto(origin)
                page.get_by_label('Username',exact=True).fill('pair-fixture-owner');page.get_by_label('Password',exact=True).fill('rendered-pair-fixture-password-123')
                page.get_by_role('button',name='Continue with password').click()
                page.get_by_role('button',name='Pair device · QR',exact=True).wait_for(timeout=60000)
                page.get_by_role('button',name='Pair device · QR',exact=True).click()
                form=page.get_by_role('form',name='Create managed pairing ticket')
                form.wait_for(timeout=30000)
                assert not form.get_by_label('Host administration',exact=True).is_checked()
                report['steps'].append('Actual desktop password login and Access scope form')
                page.get_by_role('button',name='Create one-use pairing ticket').click()
                ticket=page.get_by_label('One-use pairing ticket',exact=True).input_value()
                host=page.get_by_label('Host ID',exact=True).input_value()
                page.get_by_label('Machine HTTPS address').fill(origin)
                link=page.get_by_label('One-use pairing link').input_value()
                from urllib.parse import urlsplit,parse_qs
                assert not urlsplit(link).query
                assert parse_qs(urlsplit(link).fragment)['termx_pair']==[ticket]
                report['steps'].append('Real one-use issuer response, scoped grant and HTTPS fragment link')
                with httpx.Client(base_url=origin,verify=trust,trust_env=False) as native:
                    paired=native.post('/auth/pair/exchange',json={'ticket':ticket,'host_id':host,'device_name':'Rendered phone fixture'})
                    assert paired.status_code==200,paired.status_code
                    device=paired.json();headers={'Authorization':'Bearer '+device['access_token']}
                    current=native.get('/auth/me',headers=headers)
                    assert current.status_code==200 and current.json()['principal']['id']==owner.id
                    assert current.json()['principal']['scopes']==['machine-view','files-read','agent-view']
                    assert native.get('/auth/admin/principals',headers=headers).status_code==403
                    assert native.post('/api/workspace/sessions',headers=headers,json={'title':'Denied phone mutation'}).status_code==403
                    assert native.post('/auth/pair/exchange',json={'ticket':ticket,'host_id':host,'device_name':'Replay fixture'}).status_code==401
                    rotated=native.post('/auth/refresh',json={'refresh_token':device['refresh_token']})
                    assert rotated.status_code==200
                    device=rotated.json();headers={'Authorization':'Bearer '+device['access_token']}
                    report['steps'].append('Canonical native bearer exchange, narrowed authority, refresh rotation and replay denial')
                    page.get_by_role('button',name='Revoke and clear pairing ticket').click()
                    page.get_by_label('One-use pairing ticket',exact=True).wait_for(state='detached')
                    # Reload the real Access collection after the independently paired phone appears.
                    page.get_by_role('button',name='Models & engines',exact=True).click();page.get_by_role('region',name='Workspace managers').get_by_role('button',name='Access & sessions',exact=True).click()
                    phone=page.locator('article').filter(has=page.get_by_role('heading',name='Rendered phone fixture',exact=True))
                    phone.wait_for(timeout=30000);phone.scroll_into_view_if_needed();page.screenshot(path=str(destination/'paired-session.png'))
                    phone.get_by_role('button',name='Revoke session',exact=True).click()
                    phone.get_by_text('Revoked',exact=False).wait_for(timeout=30000)
                    assert native.get('/auth/me',headers=headers).status_code==401
                    assert native.post('/auth/refresh',json={'refresh_token':device['refresh_token']}).status_code==401
                    report['steps'].append('Actual desktop session revoke blocks native access and refresh')
                # Issue a fresh proof and use the actual cancel control before it is consumed.
                page.get_by_role('button',name='Create one-use pairing ticket').click()
                discarded=page.get_by_label('One-use pairing ticket').input_value()
                page.get_by_role('button',name='Revoke and clear pairing ticket').click()
                page.get_by_label('One-use pairing ticket').wait_for(state='detached')
                with httpx.Client(base_url=origin,verify=trust,trust_env=False) as native:
                    assert native.post('/auth/pair/exchange',json={'ticket':discarded,'host_id':host,'device_name':'Discarded fixture'}).status_code==401
                # Scan-equivalent navigation opens a fresh browser and uses the actual QR sign-in UI.
                page.get_by_role('button',name='Create one-use pairing ticket').click()
                qr=page.get_by_role('img',name='One-use device sign-in QR code')
                qr.wait_for(timeout=10000)
                assert qr.get_attribute('src').startswith('data:image/png;base64,')
                browser_link=page.get_by_label('One-use pairing link').input_value()
                fresh=browser.new_context()
                paired_page=fresh.new_page();paired_page.goto(browser_link)
                paired_page.get_by_role('button',name='Connect with pairing code').click()
                paired_page.get_by_role('heading',name='Control plane',exact=True).wait_for(timeout=30000)
                assert not paired_page.url.split('#')[1:]
                who=paired_page.evaluate("async()=> (await fetch('/auth/me')).json()")
                assert who['principal']['id']==owner.id
                assert sorted(who['principal']['scopes'])==sorted(['machine-view','files-read','agent-view'])
                cookies={item['name']:item for item in fresh.cookies()}
                assert cookies['termx_access']['httpOnly'] and cookies['termx_refresh']['httpOnly']
                assert not paired_page.evaluate('Object.keys(localStorage).some(k=>/token|ticket/i.test(k))')
                fresh.close()
                report['steps'].append('Rendered QR, fresh browser pairing, fragment scrubbing, HttpOnly cookies and narrowed authority')
                assert ticket.encode() not in state.identity.path.read_bytes()
                assert discarded.encode() not in state.identity.path.read_bytes()
                assert not report['errors'],report['errors']
                report['steps'].append('Actual cancel control invalidates unconsumed proof; no raw ticket persisted')
                page.screenshot(path=str(destination/'ticket-revoked.png'))
                report['ui_bundle']=(Path(__file__).resolve().parents[1]/'desktop/workspace/dist/index.html').read_text().split('src="',1)[1].split('"',1)[0]
                context.close();browser.close()
        finally:
            server.should_exit=True;worker.join(timeout=15);listener.close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    import sys
    print(json.dumps(run(sys.argv[1]),indent=2))
