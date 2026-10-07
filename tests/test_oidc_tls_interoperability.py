"""OIDC public-client interoperability over a real, certificate-verified TLS IdP."""
from __future__ import annotations
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
from time import time
from urllib.parse import parse_qs, urlencode, urlsplit
import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from termx.identity_adapters import OidcAdapter, OidcConfig
from termx.identity import AuthenticationService
from termx.authorization import ROLES
from tests.test_authorization import client_fixture

@pytest.fixture
def tls_idp(tmp_path):
 key=generate_private_key(public_exponent=65537,key_size=2048)
 name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
 certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(key,hashes.SHA256()))
 cert=tmp_path/'idp-ca.pem';private=tmp_path/'idp-private.pem'
 cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM));private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 shared={'codes':{},'requests':[]}
 jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='tls-provider-key',alg='RS256',use='sig')
 class Provider(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def reply(self,value,status=200):
   raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_GET(self):
   parts=urlsplit(self.path);shared['requests'].append(parts.path)
   if parts.path=='/.well-known/openid-configuration':self.reply({'issuer':shared['issuer'],'authorization_endpoint':shared['issuer']+'/authorize','token_endpoint':shared['issuer']+'/token','jwks_uri':shared['issuer']+'/jwks','code_challenge_methods_supported':['S256']})
   elif parts.path=='/jwks':self.reply({'keys':[jwk]})
   elif parts.path=='/authorize':
    params=parse_qs(parts.query)
    if params.get('code_challenge_method')!=['S256'] or params.get('client_id')!=['client']:return self.reply({'error':'invalid_request'},400)
    code=f"code-{len(shared['codes'])}";shared['codes'][code]=params
    self.send_response(302);self.send_header('Location',params['redirect_uri'][0]+'?'+urlencode({'code':code,'state':params['state'][0]}));self.send_header('Content-Length','0');self.end_headers()
   else:self.reply({'error':'not_found'},404)
  def do_POST(self):
   shared['requests'].append(self.path)
   form=parse_qs(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode())
   flow=shared['codes'].pop(form.get('code',[''])[0],None)
   challenge=base64.urlsafe_b64encode(hashlib.sha256(form.get('code_verifier',[''])[0].encode()).digest()).rstrip(b'=').decode()
   if not flow or form.get('grant_type')!=['authorization_code'] or form.get('client_id')!=['client'] or challenge!=flow['code_challenge'][0] or form.get('redirect_uri')!=flow['redirect_uri']:return self.reply({'error':'invalid_grant'},400)
   now=int(time());token=jwt.encode({'iss':shared['issuer'],'aud':'client','sub':'stable-subject','iat':now,'exp':now+90,'nonce':flow['nonce'][0]},key,algorithm='RS256',headers={'kid':'tls-provider-key'})
   self.reply({'id_token':token,'token_type':'Bearer'})
 server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
 shared['issuer']=f'https://localhost:{server.server_port}'
 context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,private);server.socket=context.wrap_socket(server.socket,server_side=True)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 shared['trust']=ssl.create_default_context(cafile=str(cert))
 try:yield shared
 finally:server.shutdown();server.server_close();thread.join(timeout=3)

@pytest.mark.parametrize("origin",["https://localhost","http://127.0.0.1:8787"])
def test_oidc_discovery_authorize_pkce_exchange_jwks_nonce_policy_and_session(tls_idp,tmp_path,monkeypatch,origin):
 identity,state,client,admin=client_fixture(tmp_path)
 if origin.startswith('http:'):client=TestClient(client.app,base_url=origin,client=('127.0.0.1',50000))
 principal=identity.create_principal('TLS SSO viewer',list(ROLES['viewer']));state.authorization.set_role(principal.id,'viewer')
 original=OidcAdapter._request
 async def request(adapter,method,url,**kwargs):
  # Only CA trust changes; real DNS, TLS, HTTP and status handling run.
  async with httpx.AsyncClient(verify=tls_idp['trust'],trust_env=False) as transport:
   adapter.client=transport
   return await original(adapter,method,url,**kwargs)
 monkeypatch.setattr(OidcAdapter,'_request',request)
 config={'version':1,'adapters':[{'kind':'oidc','id':'tls-sso','label':'TLS test provider','issuer':tls_idp['issuer'],'client_id':'client','redirect_uri':origin+'/auth/oidc/tls-sso/callback'}],'bindings':[{'issuer':tls_idp['issuer'],'subject':'stable-subject','principal_id':principal.id}]}
 started=client.post('/auth/admin/adapters/configuration/begin',headers=admin,json={'configuration':config,'adapter_id':'tls-sso'})
 assert started.status_code==200,started.text
 with httpx.Client(verify=tls_idp['trust'],trust_env=False) as browser:
  authorized=browser.get(started.json()['authorization_url'],follow_redirects=False)
 assert authorized.status_code==302
 params=parse_qs(urlsplit(authorized.headers['location']).query)
 proof=client.get('/auth/oidc/tls-sso/callback',params={k:v[0] for k,v in params.items()},follow_redirects=False)
 assert proof.status_code==303 and 'passed' in proof.headers['location'],proof.text
 assert client.put('/auth/admin/adapters/configuration',headers=admin,json={'configuration':config,'session_policy':'expire'}).status_code==200
 started=client.post('/auth/oidc/tls-sso/begin',headers={'Origin':origin})
 with httpx.Client(verify=tls_idp['trust'],trust_env=False) as browser:authorized=browser.get(started.json()['authorization_url'],follow_redirects=False)
 callback=client.get(authorized.headers['location'],follow_redirects=False)
 assert callback.status_code==303,callback.text
 managed=client.get('/auth/me');assert managed.status_code==200,managed.text
 assert managed.json()['principal']['id']==principal.id
 assert managed.json()['session_id']
 assert client.get('/auth/access').json()['role']=='viewer'
 assert client.get('/auth/admin/principals').status_code==403
 assert any(c.name=='termx_refresh' for c in client.cookies.jar)
 assert client.get(authorized.headers['location'],follow_redirects=False).status_code==401
 assert tls_idp['requests'].count('/authorize')==2 and '/token' in tls_idp['requests'] and '/jwks' in tls_idp['requests']


def test_loopback_native_oidc_redirect_is_exact_and_issuer_still_tls():
 OidcConfig('native','Native','https://idp.example','client','http://127.0.0.1:8787/auth/oidc/native/callback')
 for issuer,redirect in [('http://idp.example','http://127.0.0.1:8787/auth/oidc/native/callback'),('https://idp.example','http://localhost:8787/auth/oidc/native/callback'),('https://idp.example','http://127.0.0.1:8787/other'),('https://idp.example','http://example.com/auth/oidc/native/callback')]:
  with pytest.raises(ValueError):OidcConfig('native','Native',issuer,'client',redirect)
