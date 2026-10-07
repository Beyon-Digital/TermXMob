"""Opt-in, real workspace + managed browser UI proof; no provider query."""
from __future__ import annotations
import asyncio,json,os,socket,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import uvicorn
from playwright.sync_api import sync_playwright

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='termx-browser-ui-proof-') as temporary:
        root=Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderCall,ProviderTurn
        from test_agent import FakeAdapter
        resume_requested=threading.Event();private_resume_requested=threading.Event();finish=threading.Event()
        class FixtureAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                self.turns+=1;calls=[]
                if self.turns==1:calls=[ProviderCall('function','wait','browser_wait_for_handoff',{'seconds':120})]
                elif self.turns==2:
                    tab=next(t for t in state.browser.records.list('tab') if t['state']=='agent')
                    calls=[ProviderCall('function','send','browser_action',{'tab_id':tab['id'],'action':'click','args':{'selector':'#send'},'document_revision':tab['document_revision'],'lease_revision':tab['lease_revision']})]
                elif self.turns in {3,5}:
                    event=resume_requested if self.turns==3 else private_resume_requested
                    while not event.is_set():await asyncio.sleep(.025)
                    calls=[ProviderCall('function','resume-wait-'+str(self.turns),'browser_wait_for_handoff',{'seconds':120})]
                elif self.turns in {4,6}:
                    tab=next(t for t in state.browser.records.list('tab') if t['state']=='agent')
                    calls=[ProviderCall('function','after-resume-'+str(self.turns),'browser_action',{'tab_id':tab['id'],'action':'zoom','args':{'zoom':1},'document_revision':tab['document_revision'],'lease_revision':tab['lease_revision']})]
                elif self.turns==7:
                    while not finish.is_set():await asyncio.sleep(.025)
                return ProviderTurn('response-'+str(self.turns),'Task completed' if not calls else '',calls,{},[])
        adapter=FixtureAdapter();state=AppState(passcode=None,adapter_factory=lambda *_:adapter)
        owner=state.identity.setup_owner('fixture-owner','browser-ui-password-123')
        project=state.projects.register(str(root),name='Browser proof fixture')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='Isolated fixture',base_url='http://127.0.0.1:9999/v1',model='fixture',capabilities=['shell'],api_key='fixture-not-a-provider-key')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        conversation=state.workspace.create_session(owner,title='Browser proof',project_id=project['id'],cwd=str(root),provider_id='fixture',mode='agent')
        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path=='/fixture.txt':
                    body=b'Permitted fixture download';self.send_response(200);self.send_header('Content-Disposition','attachment; filename=fixture.txt');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
                body=b'<html><title>Browser proof fixture</title><style>body{font:20px system-ui;background:#fbfdf8;padding:60px}input,button{font:inherit;padding:12px;margin:10px}</style><h1>Controlled fixture page</h1><input id="draft" aria-label="Draft"><input type="password" id="password"><input id="file" type="file"><a href="/fixture.txt" download>Download fixture</a><button id="send" onclick="document.querySelector(\'h1\').innerText=\'SENT\'">Send message</button></html>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*_):pass
        site=ThreadingHTTPServer(('127.0.0.1',0),Site);site_thread=threading.Thread(target=site.serve_forever,daemon=True);site_thread.start()
        origin=f'http://127.0.0.1:{site.server_port}'
        from termx.browser.network import NetworkPolicy
        state.browser.network=NetworkPolicy([origin])
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));host_loops=[]
        async def serve():host_loops.append(asyncio.get_running_loop());await host.serve(sockets=[listener])
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start()
        def host_call(coro):return asyncio.run_coroutine_threadsafe(coro,host_loops[0]).result(timeout=30)
        deadline=monotonic()+20
        while not host.started:
            if monotonic()>deadline:raise RuntimeError('Isolated host did not start')
            sleep(.05)
        report={'provider_queries':0,'errors':[]};page=None
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
                page.goto(f'http://127.0.0.1:{port}/?session={conversation["id"]}')
                page.get_by_label('Username',exact=True).fill('fixture-owner');page.get_by_label('Password',exact=True).fill('browser-ui-password-123');page.get_by_role('button',name='Continue with password').click()
                page.wait_for_function('(() => {const composer=document.querySelector("textarea[aria-label=Message]");return !!composer && !composer.disabled})()',timeout=60000)
                page.get_by_role('textbox',name='Message',exact=True).fill('Browser proof');page.get_by_role('button',name='Send message',exact=True).click()
                page.get_by_role('button',name='Allow once',exact=True).wait_for(timeout=15000);page.get_by_role('button',name='Allow once',exact=True).click()
                page.keyboard.press('Control+3')
                page.get_by_role('button',name='Create a browser profile').click();page.get_by_label('New profile name').fill('Fixture profile');page.get_by_role('button',name='Create profile',exact=True).click()
                page.get_by_label('Page address').fill(origin);page.get_by_role('button',name='Go',exact=True).click()
                page.get_by_text('Live browser connected',exact=True).wait_for(timeout=30000)
                frame=page.locator('.browser-live-view');frame.wait_for();assert frame.evaluate('image=>image.naturalWidth')==1440
                page.screenshot(path=str(destination/'browser-human.png'))
                page.get_by_role('button',name='Let agent use',exact=True).click();page.get_by_role('button',name='Hand over & observe').click()
                page.get_by_role('button',name='Approve once',exact=True).wait_for(timeout=30000);page.screenshot(path=str(destination/'browser-approval.png'));page.get_by_role('button',name='Approve once',exact=True).click()
                deadline=monotonic()+20
                while not any(r['status']=='completed' for r in state.browser.records.list('review')):
                    if monotonic()>deadline:
                        (destination/'diagnostics.json').write_text(json.dumps({'reviews':state.browser.records.list('review'),'tabs':state.browser.records.list('tab'),'tasks':[{'task':t,'events':state.agent_store.events(t['id'])} for t in state.agent_store.list_tasks()]},indent=2))
                        page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                        raise AssertionError('Real approved browser action did not complete')
                    sleep(.1)
                page.get_by_role('button',name='Ask about page',exact=True).click();page.get_by_text('SENT',exact=True).wait_for(timeout=15000)
                page.get_by_role('button',name='Close',exact=True).click()
                active=next(t for t in state.browser.records.list('tab') if t['state']=='agent');first_grant=active['grant_id']
                page.get_by_role('button',name='Take over',exact=True).click();page.get_by_role('button',name='Let agent use',exact=True).wait_for()
                resume_requested.set()
                assert state.browser.records.get('grant',first_grant)['revoked']
                page.get_by_role('button',name='Let agent use',exact=True).click();page.get_by_role('button',name='Hand over & observe').click();page.get_by_role('button',name='Take over',exact=True).wait_for()
                assert state.browser.get(active['id'],owner.id)['grant_id']!=first_grant
                deadline=monotonic()+15
                while not any(r['id'].endswith(':after-resume-4') and r['status']=='completed' for r in state.browser.records.list('review')):
                    if monotonic()>deadline:raise AssertionError('Live engine did not resume its actual browser tool after takeover')
                    page.wait_for_timeout(100)
                report.update(human_takeover=True,explicit_resume_new_grant=True)
                page.get_by_role('button',name='Private login',exact=True).click();page.get_by_text('Capture paused',exact=True).wait_for()
                assert page.get_by_role('button',name='Ask about page',exact=True).is_disabled() and frame.is_visible()
                page.screenshot(path=str(destination/'browser-private.png'))
                private_resume_requested.set()
                page.get_by_role('button',name='Resume agent',exact=True).click();page.get_by_label('Approved uploads and downloads').check();page.get_by_role('button',name='Hand over & observe').click();page.get_by_role('button',name='Take over',exact=True).wait_for()
                deadline=monotonic()+15
                while not any(r['id'].endswith(':after-resume-6') and r['status']=='completed' for r in state.browser.records.list('review')):
                    if monotonic()>deadline:raise AssertionError('Live engine did not resume after private login')
                    page.wait_for_timeout(100)
                report['live_engine_actions_after_both_resumes']=True
                page.locator('.browser-upload input[type=file]').set_input_files({'name':'fixture-upload.txt','mimeType':'text/plain','buffer':b'Permitted fixture upload'})
                deadline=monotonic()+15
                while not state.browser.records.list('upload'):
                    if monotonic()>deadline:raise AssertionError('Approved upload reference did not arrive')
                    page.wait_for_timeout(100)
                ref=state.browser.records.list('upload')[0];tab=state.browser.get(active['id'],owner.id);grant=state.browser.records.get('grant',tab['grant_id'])
                from termx.auto_review import ReviewRequired
                async def upload_once():
                    current=state.browser.get(tab['id'],owner.id)
                    return await state.browser.action(tab['id'],owner.id,session_id=grant['session_id'],run_id=grant['run_id'],grant_id=grant['id'],action_id='ui-upload-exact',action='upload',args={'selector':'#file','file_id':ref['id']},document_revision=current['document_revision'],lease_revision=current['lease_revision'],policy_version=grant['policy_version'])
                try:host_call(upload_once());raise AssertionError('Upload bypassed exact human gate')
                except ReviewRequired:pass
                page.get_by_role('button',name='Approve once',exact=True).wait_for();page.get_by_role('button',name='Approve once',exact=True).click()
                deadline=monotonic()+15
                while state.browser.records.get('review','ui-upload-exact')['status']!='approved_once':
                    if monotonic()>deadline:raise AssertionError('Upload approval did not arrive')
                    page.wait_for_timeout(100)
                host_call(upload_once())
                async def uploaded_name():return await state.browser._pages[tab['id']].locator('#file').evaluate('e=>e.files[0].name')
                assert host_call(uploaded_name())=='fixture-upload.txt';report['approved_upload_actual_file']=True
                page.get_by_role('button',name='Take over',exact=True).click();page.get_by_role('button',name='Let agent use',exact=True).wait_for();page.get_by_role('button',name='Page controls',exact=True).click();page.get_by_role('button',name='Focus a',exact=True).click();page.get_by_role('button',name='Close',exact=True).click()
                page.get_by_text('Downloads',exact=True).click();download_link=page.get_by_role('link',name='fixture.txt',exact=True);download_link.wait_for(timeout=15000)
                with page.expect_download() as transfer:download_link.click()
                assert Path(transfer.value.path()).read_bytes()==b'Permitted fixture download';report['approved_download_actual_bytes']=True
                page.get_by_role('button',name='Ask about page',exact=True).click();page.get_by_label('Page annotation').fill('Fixture annotation');page.get_by_role('button',name='Annotate',exact=True).click();page.get_by_text('Fixture annotation',exact=True).wait_for();page.get_by_role('button',name='Close',exact=True).click()
                old_revision=state.browser.get(tab['id'],owner.id)['document_revision'];page.get_by_role('button',name='Reload page',exact=True).click()
                deadline=monotonic()+15
                while state.browser.get(tab['id'],owner.id)['document_revision']==old_revision:
                    if monotonic()>deadline:raise AssertionError('Reload did not advance document revision')
                    page.wait_for_timeout(100)
                page.get_by_role('button',name='Ask about page',exact=True).click();page.get_by_text('Fixture annotation · Stale after navigation',exact=True).wait_for();page.get_by_role('button',name='Close',exact=True).click();report['annotation_staleness_rendered']=True
                async def crash():
                    try:await state.browser._capture_cdp[tab['id']].send('Page.crash')
                    except Exception:pass
                crash_future=asyncio.run_coroutine_threadsafe(crash(),host_loops[0])
                try:page.get_by_text('Browser tab crashed',exact=True).wait_for(timeout=15000)
                except Exception:
                    page.screenshot(path=str(destination/'crash-failure.png'));(destination/'crash-failure.html').write_text(page.content());(destination/'crash-state.json').write_text(json.dumps(state.browser.records.list('tab'),indent=2));raise
                page.screenshot(path=str(destination/'browser-crashed.png'));page.get_by_role('button',name='Reopen crashed tab',exact=True).click();page.get_by_role('button',name='Let agent use',exact=True).wait_for(timeout=30000)
                restored=state.browser.records.get('tab',tab['id'])['recovered_tab_id'];assert state.browser.get(restored,owner.id)['state']=='human' and state.browser.get(restored,owner.id)['grant_id'] is None
                page.locator('.browser-live-view').wait_for();page.screenshot(path=str(destination/'browser-recovered.png'));report['crash_recovery_human_new_tab']=True
                report.update(live_frame=True,task_handoff=True,exact_approval_resumed=True,private_human_view=True,private_agent_context_blocked=True)
                assert report['errors']==[]
                finish.set();context.close();browser.close()
        except Exception:
            if page:
                try:
                    if not page.is_closed():page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                except Exception:pass
            raise
        finally:
            finish.set();resume_requested.set();private_resume_requested.set()
            host.should_exit=True;worker.join(timeout=15);listener.close();site.shutdown();site.server_close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
        return report

if __name__=='__main__':
    import sys
    print(json.dumps(run(sys.argv[1]),indent=2))
