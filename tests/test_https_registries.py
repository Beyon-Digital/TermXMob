from __future__ import annotations
import hashlib,json,ssl,threading
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from termx.agent.secrets import CredentialStore
from termx.workspace.extensions import ExtensionService
from termx.workspace.https_registry import HTTPSRegistryTransport,public_addresses,checked_url
from termx.workspace.store import Conflict
from test_durable_workspace import workspace

@pytest.fixture
def tls(tmp_path):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).sign(key,hashes.SHA256()))
    certfile=tmp_path/'cert.pem';keyfile=tmp_path/'key.pem'
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM));keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    responses={};requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path,self.headers.get('Authorization')))
            status,headers,body=responses.get(self.path,(404,{},b''))
            self.send_response(status)
            for k,v in headers.items():self.send_header(k,v)
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(certfile,keyfile)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    client=ssl.create_default_context(cafile=str(certfile))
    transport=HTTPSRegistryTransport(resolver=lambda host,port:['127.0.0.1'],context=client)
    yield f'https://localhost:{server.server_port}',transport,responses,requests
    server.shutdown();server.server_close();worker.join(2)

def bundle(identifier='inspect',dependencies=None):
    result={'format':'termx-bundle/v1','id':identifier,'version':'1','permissions':['files-read'],'files':{'skills/'+identifier+'/SKILL.md':'Inspect the current source.'}}
    if dependencies is not None:result['dependencies']=dependencies
    return result

def publish(responses,data,path='/package'):
    raw=json.dumps(data).encode();responses[path]=(200,{'Content-Type':'application/json','Content-Length':str(len(raw))},raw)
    index={'format':'termx-registry/v1','packages':[{'id':data['id'],'version':data['version'],'url':path,'sha256':hashlib.sha256(raw).hexdigest(),'permissions':data['permissions'],'dependencies':data.get('dependencies',[])}]}
    responses['/index']=(200,{},json.dumps(index).encode())
    return index

def test_real_tls_private_download_review_rotation_and_host_secret(workspace,tls):
    ws,owner,path=workspace;url,transport,responses,requests=tls
    ws.state.credentials=CredentialStore(memory={});service=ExtensionService(ws,path/'bundles');service.transport=transport
    publish(responses,bundle())
    registry=service.registry(owner,name='Private TLS',kind='private_https',url=url+'/index',credential='private-fixture-secret')
    assert 'credential_ref' not in registry and 'private-fixture-secret' not in json.dumps(registry)
    packages=service.registry_packages(owner,registry['id']);assert packages[0]['filename']=='inspect@1'
    preview=service.registry_preview(owner,registry['id'],'inspect@1')
    assert preview['provenance']['package']['sha256']==hashlib.sha256(responses['/package'][2]).hexdigest()
    assert preview['permission_diff']['added']==['files-read']
    assert all(credential=='Bearer private-fixture-secret' for _,credential in requests)
    assert 'private-fixture-secret' not in json.dumps(ws.store.list('registry')+ws.store.list('extension_preview'))
    service.rotate_registry_credential(owner,registry['id'],'rotated-fixture-secret',registry['revision'])
    with pytest.raises(Conflict,match='credential changed'):service.install(owner,preview['id'],preview['digest'])
    preview=service.registry_preview(owner,registry['id'],'inspect@1')
    installed=service.install(owner,preview['id'],preview['digest'])
    assert installed['provenance']['authentication']=='host-credential-reference'
    assert service.export(owner,'inspect')==bundle()
    assert requests[-1][1]=='Bearer rotated-fixture-secret'

def test_real_tls_tamper_redirect_size_and_public_auth(workspace,tls):
    ws,owner,path=workspace;url,transport,responses,requests=tls
    service=ExtensionService(ws,path/'bundles');service.transport=transport
    publish(responses,bundle());registry=service.registry(owner,name='Public',kind='public_https',url=url+'/index')
    responses['/package']=(200,{},json.dumps(bundle('changed')).encode())
    with pytest.raises(Conflict,match='digest changed'):service.registry_preview(owner,registry['id'],'inspect@1')
    assert not ws.store.list('extension')
    responses['/index']=(302,{'Location':'https://example.com/steal'},b'')
    with pytest.raises(ValueError,match='changed origin'):service.registry_packages(owner,registry['id'])
    assert all(credential is None for _,credential in requests)
    responses['/index']=(200,{'Content-Length':'2100001'},b'')
    with pytest.raises(ValueError,match='byte budget'):service.registry_packages(owner,registry['id'])
    responses['/index']=(200,{'Content-Encoding':'gzip'},b'{}')
    with pytest.raises(ValueError,match='compressed'):service.registry_packages(owner,registry['id'])

def test_default_transport_denies_protected_dns_and_credentials_in_url(monkeypatch):
    import socket
    for ip in ('127.0.0.1','10.2.3.4','169.254.169.254','::1','::ffff:127.0.0.1'):
        monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))])
        with pytest.raises(ValueError,match='protected'):public_addresses('registry.example',443)
    for url in ('http://example.com/index','https://user:pass@example.com/index','https://example.com/index?access_token=secret'):
        with pytest.raises(ValueError):checked_url(url)
    bad=ssl.create_default_context();bad.check_hostname=False
    with pytest.raises(ValueError,match='verification'):HTTPSRegistryTransport(context=bad)

def test_dependencies_are_reviewed_exactly_and_dispatch_expands_grants(workspace,tls):
    ws,owner,path=workspace;url,transport,responses,_=tls
    service=ExtensionService(ws,path/'bundles');service.transport=transport
    dependency=bundle('base');preview=service.preview(owner,dependency)
    requirements=[{'id':'base','version':'1','digest':preview['digest']}]
    publish(responses,bundle('inspect',requirements));registry=service.registry(owner,name='Deps',kind='public_https',url=url+'/index')
    main=service.registry_preview(owner,registry['id'],'inspect@1')
    assert not main['dependencies'][0]['satisfied']
    with pytest.raises(Conflict,match='dependencies'):service.install(owner,main['id'],main['digest'])
    service.install(owner,preview['id'],preview['digest']);service.install(owner,main['id'],main['digest'])
    assert {row['id'] for row in service.execution_context(owner,['inspect'],'p',str(path))}=={'base','inspect'}
    service.remove(owner,'base')
    with pytest.raises(Conflict,match='dependency changed'):service.execution_context(owner,['inspect'],'p',str(path))


def test_registry_owner_and_live_authority_are_rechecked_after_download(workspace,tls):
    ws,owner,path=workspace;url,transport,responses,requests=tls
    service=ExtensionService(ws,path/'bundles');service.transport=transport
    publish(responses,bundle());registry=service.registry(owner,name='Owned',kind='public_https',url=url+'/index')
    stranger=ws.state.identity.create_principal('Foreign administrator',list(owner.scopes))
    with pytest.raises(KeyError):service.registry_packages(stranger,registry['id'])
    assert requests==[]
    class RevokingTransport:
        def fetch(self,*args,**kwargs):
            result=transport.fetch(*args,**kwargs)
            ws.state.identity.disable(owner.id)
            return result
    service.transport=RevokingTransport()
    with pytest.raises(PermissionError):service.registry_preview(owner,registry['id'],'inspect@1')
    assert not ws.store.list('extension_preview') and not ws.store.list('extension')


def test_dns_wait_and_outstanding_work_are_bounded(monkeypatch):
    from time import monotonic
    import termx.workspace.https_registry as registry
    release=threading.Event();finished=threading.Event()
    def stalled(*args,**kwargs):
        release.wait(2);finished.set();return []
    monkeypatch.setattr(registry.socket,'getaddrinfo',stalled)
    monkeypatch.setattr(registry,'MAX_DNS_SECONDS',.02)
    monkeypatch.setattr(registry,'_DNS_SLOTS',threading.BoundedSemaphore(1))
    started=monotonic()
    try:
        with pytest.raises(ValueError,match='time budget'):registry.public_addresses('registry.example',443)
        assert monotonic()-started<.5
        with pytest.raises(ValueError,match='capacity'):registry.public_addresses('registry.example',443)
    finally:
        release.set();assert finished.wait(1)
