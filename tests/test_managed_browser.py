import asyncio
import os
from pathlib import Path
from time import time
import pytest
from termx.browser.network import NetworkPolicy,TargetDenied,origin,EgressProxy
from termx.browser.service import BrowserService
from termx.auto_review import ReviewRequired,ActionBlocked

@pytest.mark.parametrize('url',['file:///etc/passwd','http://user:password@example.com','http://localhost/#secret','chrome://settings','javascript:alert(1)','data:text/html,bad'])
def test_broker_rejects_privileged_url(url):
    with pytest.raises(TargetDenied):origin(url)

def test_ssrf_private_metadata_and_exact_development_grant():
    async def run():
        policy=NetworkPolicy()
        for host in ('127.0.0.1','169.254.169.254','metadata.google.internal','::1','10.0.0.1'):
            with pytest.raises(TargetDenied):await policy.resolve(host,8000,'http')
        allowed=NetworkPolicy(['http://127.0.0.1:8123'])
        assert await allowed.resolve('127.0.0.1',8123,'http')=='127.0.0.1'
        with pytest.raises(TargetDenied):await allowed.resolve('127.0.0.1',8124,'http')
    asyncio.run(run())

async def website(redirect_url=None,requests=None):
    async def handler(reader,writer):
        try:
            head=await reader.readuntil(b'\r\n\r\n');path=head.split(b' ')[1].decode()
            if requests is not None:requests.append(path)
            if path=='/redirect':
                writer.write(b'HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/meta-data\r\nContent-Length:0\r\nConnection:close\r\n\r\n')
            elif path=='/cross' and redirect_url:
                writer.write(f'HTTP/1.1 302 Found\r\nLocation: {redirect_url}\r\nContent-Length:0\r\nConnection:close\r\n\r\n'.encode())
            else:
                html=b'''<!doctype html><html><title>Fixture browser</title><h1>Task page</h1><input id="draft" aria-label="Draft name"><input id="password" type="password"><button id="send" onclick="document.querySelector('h1').innerText='SENT'">Send message</button><button id="mystery" onclick="document.querySelector('h1').innerText='CHANGED'">Mystery</button><a id="next" href="/next">Next page</a><input id="file" type="file"><p>Public context</p></html>'''
                writer.write(f'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {len(html)}\r\nConnection:close\r\n\r\n'.encode()+html)
            await writer.drain()
        except Exception:pass
        finally:writer.close()
    server=await asyncio.start_server(handler,'127.0.0.1',0)
    return server,f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_token_and_private_fields_require_private_human_login(tmp_path):
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        try:
            profile=service.create_profile('owner','project','Fixture')
            tab=await service.create_tab('owner','session',profile['id'],site)
            await service._pages[tab['id']].evaluate('''()=>{const token=document.createElement('input');token.id='api_token';token.setAttribute('aria-label','Token field excluded');document.body.append(token);const privateField=document.createElement('input');privateField.id='private-field';privateField.setAttribute('data-private','');document.body.append(privateField)}''')
            observed=await service.observe(tab['id'],'owner',human=True)
            assert not any(row['name']=='Token field excluded' for row in observed['elements'])
            granted=await service.handoff(tab['id'],'owner','session',run_id='task',origins=[site],actions=['observe','type'],policy_version=1)
            tab=granted['tab']
            for index,selector in enumerate(('#api_token','#private-field')):
                with pytest.raises(ActionBlocked):await service.action(tab['id'],'owner',session_id='session',policy_version=1,run_id='task',grant_id=granted['grant']['id'],action_id='protected-field-'+str(index),action='type',args={'selector':selector,'text':'NEVER_SAVE_CREDENTIAL'},document_revision=tab['document_revision'],lease_revision=tab['lease_revision'])
            assert 'NEVER_SAVE_CREDENTIAL' not in service.records.path.read_bytes().decode(errors='ignore')
        finally:await service.close();server.close();await server.wait_closed()
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required; set PLAYWRIGHT_BROWSERS_PATH')
def test_real_managed_browser_grant_input_private_takeover_and_stale(tmp_path):
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        try:
            profile=service.create_profile('owner','project','Test isolated profile')
            tab=await service.create_tab('owner','session',profile['id'],site)
            assert tab['title']=='Fixture browser'
            snapshot=await service.observe(tab['id'],'owner',human=True)
            assert not any(x['type']=='password' for x in snapshot['elements'])
            assert (await service.frame(tab['id'],'owner',human=True)).startswith(b'\xff\xd8')
            grant=await service.handoff(tab['id'],'owner','session',run_id='task',origins=[site],actions=['observe','capture','click','type','navigate','scroll','upload'],policy_version=1)
            tab=grant['tab']
            def args(action_id,action,arguments):return dict(session_id='session',run_id='task',grant_id=grant['grant']['id'],action_id=action_id,action=action,args=arguments,document_revision=tab['document_revision'],lease_revision=tab['lease_revision'],policy_version=1)
            with pytest.raises(ReviewRequired) as pending:
                await service.action(tab['id'],'owner',**args('send-once','click',{'selector':'#send'}))
            assert await service._pages[tab['id']].locator('h1').inner_text()=='Task page'
            service.review.decide('send-once',principal_id='owner',approve=True)
            await service.action(tab['id'],'owner',**args('send-once','click',{'selector':'#send'}))
            assert await service._pages[tab['id']].locator('h1').inner_text()=='SENT'
            with pytest.raises(ReviewRequired):await service.action(tab['id'],'owner',**args('send-once','click',{'selector':'#send'}))
            with pytest.raises(ActionBlocked):await service.action(tab['id'],'owner',**args('credential','type',{'selector':'#password','text':'never-forward'}))
            assert await service._pages[tab['id']].locator('#password').input_value()==''
            # Unknown controls now require manual takeover. Use an understood
            # send control here to exercise exact human approval staleness.
            await service._pages[tab['id']].locator('#mystery').evaluate("e=>{e.innerText='Send original message'}")
            with pytest.raises(ReviewRequired):
                await service.action(tab['id'],'owner',**args('changed-target','click',{'selector':'#mystery'}))
            await service._pages[tab['id']].locator('#mystery').evaluate("e=>{e.innerText='Delete account'}")
            service.review.decide('changed-target',principal_id='owner',approve=True)
            with pytest.raises(ValueError,match='document changed'):
                await service.action(tab['id'],'owner',**args('changed-target','click',{'selector':'#mystery'}))
            assert await service._pages[tab['id']].locator('h1').inner_text()=='SENT'
            service.takeover(tab['id'],'owner',private=True)
            with pytest.raises(PermissionError):await service.observe(tab['id'],'owner',grant_id=grant['grant']['id'],run_id='task')
            with pytest.raises(PermissionError):await service.frame(tab['id'],'owner',grant_id=grant['grant']['id'],run_id='task')
            assert await service.frame(tab['id'],'owner',human=True)
            await service.human_action(tab['id'],'owner','type',{'selector':'#password','text':'private-secret'})
            assert 'private-secret' not in service.records.path.read_bytes().decode(errors='ignore')
            resumed=await service.handoff(tab['id'],'owner','session',run_id='task',origins=[site],actions=['observe','capture','click','type','navigate'],policy_version=1)
            assert resumed['tab']['lease_revision']>tab['lease_revision']
            with pytest.raises(PermissionError):await service.action(tab['id'],'owner',**args('old','scroll',{}))
            with pytest.raises(KeyError):service.get(tab['id'],'another-user')
            await service.human_action(tab['id'],'owner','navigate',{'url':site+'/next'})
            await asyncio.sleep(.05)
            with pytest.raises(ValueError):service.annotate(tab['id'],'owner',revision=tab['document_revision'],comment='stale comment')
            other=service.create_profile('owner','project2','Second profile')
            tab2=await service.create_tab('owner','session',other['id'],site)
            await service._pages[tab['id']].context.add_cookies([{'name':'account','value':'owner-cookie','url':site}])
            assert not await service._pages[tab2['id']].context.cookies()
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_browser_redirect_metadata_blocked(tmp_path):
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        try:
            p=service.create_profile('owner','project','Redirect test')
            with pytest.raises(Exception):await service.create_tab('owner','session',p['id'],site+'/redirect')
        finally:await service.close();server.close();await server.wait_closed()
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_redirect_hop_outside_agent_origins_never_contacts_destination(tmp_path):
    async def run():
        destination_requests=[]
        second,other=await website(requests=destination_requests)
        first,site=await website(redirect_url=other)
        service=BrowserService(tmp_path,development_origins=[site,other])
        try:
            profile=service.create_profile('owner','project','Bounded redirect')
            tab=await service.create_tab('owner','sid',profile['id'],site)
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['navigate','observe'])
            with pytest.raises(Exception):
                await service.action(tab['id'],'owner',session_id='sid',run_id='task',grant_id=grant['grant']['id'],action_id='redirect',action='navigate',args={'url':site+'/cross'},document_revision=grant['tab']['document_revision'],lease_revision=grant['tab']['lease_revision'])
            assert destination_requests==[] and service.get(tab['id'],'owner')['state']=='human'
        finally:
            await service.close();first.close();second.close();await first.wait_closed();await second.wait_closed()
    asyncio.run(run())

def test_browser_http_profile_owner_revocation_and_cookie_csrf(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from termx.identity import AuthenticationService
    from termx.app import AppState,create_app
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    identity=AuthenticationService(tmp_path/'auth.sqlite3')
    identity.setup_owner('owner','test-password-not-production')
    credentials=asyncio.run(identity.login('local-password',{'username':'owner','password':'test-password-not-production'},peer='127.0.0.1'))
    state=AppState(identity=identity)
    app=create_app(state)
    client=TestClient(app,base_url='https://localhost')
    token={'Authorization':'Bearer '+credentials.access_token}
    assert client.get('/api/browser/profiles').status_code==403
    profile=client.post('/api/browser/profiles',headers=token,json={'name':'Own profile','project_id':''})
    assert profile.status_code==200,profile.text
    assert client.get('/api/browser/profiles',headers=token).json()[0]['id']==profile.json()['id']
    assert client.post('/api/browser/reviewer',headers=token,json={'provider_id':'invented','model':'invented','version':'v1'}).status_code==400
    assert client.post('/api/browser/tabs',headers=token,json={'profile_id':'foreign'}).status_code==404
    client.cookies.set('termx_access',credentials.access_token)
    assert client.post('/api/browser/profiles',json={'name':'csrf-bypass'}).status_code==403
    identity.revoke(credentials.session_id,identity.resolve(credentials.access_token).principal.id)
    assert client.get('/api/browser/profiles',headers=token).status_code==401

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_internal_agent_browser_tools_approval_resume_and_no_shell_escape(tmp_path):
    from termx.agent.manager import AgentManager
    from termx.agent.providers import ProviderCall
    from termx.agent.store import AgentStore
    from termx.agent.secrets import CredentialStore
    from termx.agent.tools import default_registry
    from time import monotonic
    async def run():
        server,site=await website();service=BrowserService(tmp_path/'browser',development_origins=[site])
        store=AgentStore(tmp_path/'agent.sqlite3',tmp_path/'artifacts')
        manager=AgentManager(store,CredentialStore({}),None)
        manager.browser=service
        async def no_drive(*args,**kwargs):pass
        manager._drive=no_drive
        task=store.create_task(prompt='Check the page',cwd=str(tmp_path),provider_id='no-paid-provider',model='fixture',limits={'max_steps':10,'max_seconds':60},status='running')
        try:
            profile=service.create_profile('owner','project','Agent profile')
            tab=await service.create_tab('owner','session',profile['id'],site)
            grant=await service.handoff(tab['id'],'owner','session',run_id=task['id'],origins=[site],actions=['observe','click','capture'],policy_version=1)
            paused,history,step=await manager._run_calls(task['id'],[ProviderCall('function','observe','browser_observe',{'tab_id':tab['id']})],[],0,monotonic())
            assert not paused and step==1 and 'Task page' in str(history)
            args={'tab_id':tab['id'],'action':'click','args':{'selector':'#send'},'document_revision':grant['tab']['document_revision'],'lease_revision':grant['tab']['lease_revision']}
            call=ProviderCall('function','send','browser_action',args)
            paused,history,step=await manager._run_calls(task['id'],[call],history,step,monotonic())
            assert paused and store.get_task(task['id'])['status']=='awaiting_approval'
            approval=store.approvals(task['id'])[0]
            assert await service._pages[tab['id']].locator('h1').inner_text()=='Task page'
            await manager.resolve_approval(task['id'],approval['id'],'approved')
            await asyncio.gather(*manager._workers.values())
            assert await service._pages[tab['id']].locator('h1').inner_text()=='SENT'
            assert service.records.get('review',task['id']+':send')['status']=='completed'
            shell=ProviderCall('function','escape','run_shell',{'command':'curl http://169.254.169.254'})
            with pytest.raises(PermissionError):await manager._run_calls(task['id'],[shell],history,step,monotonic(),approved_first=True)
            with pytest.raises(PermissionError):await manager._run_calls(task['id'],[ProviderCall('function','delegate','spawn_subagent',{})],history,step,monotonic(),approved_first=True)
            schemas={t['name'] for t in default_registry().provider_tools()}
            assert {'browser_tabs','browser_observe','browser_action'}<=schemas
            ask={t['name'] for t in default_registry().provider_tools(read_only=True)}
            assert 'browser_action' not in ask
            public=ProviderCall('function','private','browser_action',{'action':'type','args':{'text':'secret-password'}}).public()
            assert 'secret-password' not in str(public)
        finally:await service.close();server.close();await server.wait_closed();store.close()
    asyncio.run(run())
