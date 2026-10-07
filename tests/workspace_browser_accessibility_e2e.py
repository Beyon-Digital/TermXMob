"""Real keyboard-only browser/manager workflow and axe; no paid inference."""
from __future__ import annotations
import asyncio,json,os,re,socket,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import uvicorn
from playwright.sync_api import sync_playwright

def run(destination,axe_source):
    signin_only=os.environ.get('TERMX_A11Y_SIGNIN_ONLY')=='1'
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    axe=Path(axe_source);assert axe.is_file()
    report={'tool':'axe-core/4.10.3','states':[],'keyboard':[],'errors':[],'paid_provider_queries':0,'limitations':['Rendered DOM and host-semantic browser controls are audited; pixels inside a remotely rendered third-party page and screen-reader speech require separate human evaluation.']}
    with tempfile.TemporaryDirectory(prefix='termx-browser-a11y-') as temporary:
        root=Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderCall,ProviderTurn
        from test_agent import FakeAdapter
        finish=threading.Event()
        class FixtureAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                self.turns+=1
                if self.turns==1:return ProviderTurn('wait','',[ProviderCall('function','wait','browser_wait_for_handoff',{'seconds':300})],{},[])
                while not finish.is_set():await asyncio.sleep(.05)
                return ProviderTurn('finish','Done',[],{},[])
        adapter=FixtureAdapter()
        state=AppState(passcode=None,adapter_factory=lambda *_:adapter)
        owner=state.identity.setup_owner('a11y-owner','keyboard-browser-fixture-password-123')
        project=state.projects.register(str(root),name='Keyboard browser fixture')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No network fixture',base_url='http://127.0.0.1:9999/v1',model='fixture',capabilities=['shell'],api_key='fixture')
        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'<html><title>Keyboard page</title><h1>Keyboard fixture page</h1><label>Draft<input id="draft" aria-label="Draft"></label><button>Preview draft</button></html>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*_):pass
        site=ThreadingHTTPServer(('127.0.0.1',0),Site);threading.Thread(target=site.serve_forever,daemon=True).start()
        origin=f'http://127.0.0.1:{site.server_port}'
        from termx.browser.network import NetworkPolicy
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        state.browser.network=NetworkPolicy([origin])
        session=state.workspace.create_session(owner,title='Keyboard browser task',project_id=project['id'],cwd=str(root),provider_id='fixture',mode='agent')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));host_loops=[]
        async def serve():host_loops.append(asyncio.get_running_loop());await host.serve(sockets=[listener])
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start()
        deadline=monotonic()+20
        while not host.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                for zoom in (100,200):
                    scale=zoom/100
                    context=browser.new_context(viewport={'width':int(1440/scale),'height':int(1000/scale)},device_scale_factor=scale,reduced_motion='reduce')
                    for theme in ('dark','light'):
                        if signin_only:context.clear_cookies()
                        page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                        def seek(locator,label):
                            locator.wait_for(state='visible',timeout=30000)
                            ready=monotonic()+30
                            while not locator.is_enabled():
                                if monotonic()>ready:raise AssertionError('Keyboard control stayed disabled: '+label)
                                page.wait_for_timeout(50)
                            for _ in range(180):
                                if locator.evaluate('e=>e===document.activeElement'):break
                                page.keyboard.press('Tab')
                            else:raise AssertionError('Keyboard cannot reach '+label)
                            focus=locator.evaluate("""e=>{const s=getComputedStyle(e),b=e.getBoundingClientRect();let clip={left:Math.max(0,b.left),right:Math.min(innerWidth,b.right),top:Math.max(0,b.top),bottom:Math.min(innerHeight,b.bottom)},parent=e.parentElement;while(parent){const st=getComputedStyle(parent),r=parent.getBoundingClientRect();if(/hidden|auto|scroll|clip/.test(st.overflowX)){clip.left=Math.max(clip.left,r.left);clip.right=Math.min(clip.right,r.right)}if(/hidden|auto|scroll|clip/.test(st.overflowY)){clip.top=Math.max(clip.top,r.top);clip.bottom=Math.min(clip.bottom,r.bottom)}parent=parent.parentElement}const hit=document.elementFromPoint((clip.left+clip.right)/2,(clip.top+clip.bottom)/2);let a=e,bg='';while(a){const c=getComputedStyle(a).backgroundColor;if(c!=='rgba(0, 0, 0, 0)'&&c!=='transparent'){bg=c;break}a=a.parentElement}return {label:e.getAttribute('aria-label')||e.textContent?.trim(),outline:s.outlineStyle,outline_width:s.outlineWidth,foreground:e.placeholder&&!e.value?getComputedStyle(e,'::placeholder').color:s.color,background:bg,width:b.width,height:b.height,visible_width:Math.max(0,clip.right-clip.left),visible_height:Math.max(0,clip.bottom-clip.top),uncovered:hit===e||e.contains(hit),onscreen:b.right>0&&b.left<innerWidth&&b.bottom>0&&b.top<innerHeight}}""")
                            assert focus['outline']!='none' and focus['outline_width']!='0px' and focus['width']>=12 and focus['height']>=12 and focus['onscreen'] and focus['visible_width']>=12 and focus['visible_height']>=12 and focus['uncovered'],focus
                            from workspace_accessibility_e2e import contrast
                            def hex_color(value):return '#'+''.join(f'{int(part):02x}' for part in re.findall(r'\d+',value)[:3])
                            focus['computed_text_contrast']=contrast(hex_color(focus['foreground']),hex_color(focus['background']))
                            assert focus['computed_text_contrast']>=4.5,focus
                            report['keyboard'].append({'theme':theme,'zoom_percent':zoom,'target':label,'focus':focus})
                        def button(name):
                            target=page.get_by_role('button',name=name,exact=True);seek(target,name);page.keyboard.press('Enter')
                        def type_label(name,value):
                            target=page.get_by_label(name,exact=True);seek(target,name);page.keyboard.press('ControlOrMeta+A');page.keyboard.type(value)
                        def audit(label):
                            page.add_script_tag(path=str(axe));assert page.evaluate('axe.version')=='4.10.3'
                            result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                            record={'theme':theme,'zoom_percent':zoom,'state':label,'reduced_motion':page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'impact':v['impact'],'nodes':[{'target':n['target'],'summary':n.get('failureSummary')} for n in v['nodes']]} for v in result['violations']],'incomplete':[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['incomplete']]}
                            if label in {'observe','takeover','private','resume'}:
                                record['browser_page_height']=page.locator('.browser-page').evaluate('e=>e.getBoundingClientRect().height')
                                assert record['browser_page_height']>=160,record
                            report['states'].append(record);page.screenshot(path=str(destination/f'{label}-{theme}-{zoom}.png'))
                            assert record['reduced_motion'] and not record['overflow'],record
                        page.goto(f'http://127.0.0.1:{port}/?session={session["id"]}')
                        page.wait_for_function('()=>!!document.querySelector("input[autocomplete=username],textarea[aria-label=Message]")',timeout=30000)
                        if page.get_by_label('Username',exact=True).count():
                            audit('sign-in')
                            type_label('Username','a11y-owner');type_label('Password','keyboard-browser-fixture-password-123');button('Continue with password')
                        page.wait_for_function('()=>{const e=document.querySelector("textarea[aria-label=Message]");return !!e&&!e.disabled}',timeout=60000)
                        if signin_only:
                            page.close()
                            continue
                        if (page.locator('html').get_attribute('data-theme') or 'dark')!=theme:button('Appearance')
                        if not state.agent_store.list_tasks():
                            type_label('Message','Keyboard browser task');button('Send message');button('Allow once')
                        page.keyboard.press('Control+3')
                        if not state.browser.records.list('profile'):
                            button('Create a browser profile');type_label('New profile name','Keyboard profile');button('Create profile')
                            type_label('Page address',origin);button('Go')
                        page.get_by_text('Live browser connected',exact=True).wait_for(timeout=30000)
                        assert page.locator('.browser-live-view').evaluate('e=>e.naturalWidth')==1440
                        button('Let agent use');audit('handoff');button('Hand over & observe');page.get_by_role('button',name='Take over',exact=True).wait_for()
                        button('Ask about page');page.get_by_role('region',name='Page context preview').wait_for();audit('observe');button('Close')
                        button('Take over');page.get_by_role('button',name='Let agent use',exact=True).wait_for();audit('takeover')
                        button('Page controls');page.get_by_role('region',name='Page context preview').wait_for()
                        button('Focus input');remote=page.get_by_label('Type into the focused browser page field. Press Escape to return to page controls.',exact=True)
                        seek(remote,'Managed page input bridge')
                        page.screenshot(path=str(destination/f'keyboard-page-{theme}-{zoom}.png'))
                        assert remote.evaluate('e=>e===document.activeElement&&e.getBoundingClientRect().height>=24')
                        page.keyboard.press('ControlOrMeta+A');page.keyboard.type('Keyboard remote draft');page.keyboard.press('Escape');assert page.get_by_role('button',name='Focus input',exact=True).evaluate('e=>e===document.activeElement')
                        deadline=monotonic()+10
                        async def actual_input():return await next(iter(state.browser._pages.values())).locator('#draft').input_value()
                        while asyncio.run_coroutine_threadsafe(actual_input(),host_loops[0]).result(timeout=5)!='Keyboard remote draft':
                            if deadline<monotonic():raise AssertionError('Keyboard input did not reach real managed page')
                            page.wait_for_timeout(50)
                        report['keyboard'].append({'theme':theme,'zoom_percent':zoom,'target':'managed page input','actual_input_verified':True,'escape_restored_controls':True})
                        button('Close')
                        button('Private login');page.get_by_text('Capture paused',exact=True).wait_for();assert page.get_by_role('button',name='Ask about page',exact=True).is_disabled();audit('private')
                        button('Control page with keyboard');seek(remote,'Private page input bridge');assert remote.evaluate('e=>e===document.activeElement&&e.getBoundingClientRect().height>=24');page.keyboard.press('Tab');page.keyboard.press('Escape');assert page.get_by_role('button',name='Control page with keyboard',exact=True).evaluate('e=>e===document.activeElement')
                        report['keyboard'].append({'theme':theme,'zoom_percent':zoom,'target':'private managed page keyboard','no_context_observation':True,'escape_restored_controls':True})
                        button('Resume agent');button('Hand over & observe');page.get_by_role('button',name='Take over',exact=True).wait_for();audit('resume');button('Take over')
                        button('Record safe task steps');page.get_by_role('button',name='Stop recording',exact=True).wait_for()
                        page.keyboard.press('Control+1')
                        page.get_by_role('group',name='Active capture controls').wait_for(timeout=10000)
                        assert page.get_by_text('1 recording',exact=True).is_visible()
                        audit('capture-in-chat')
                        button('Open capture controls');page.get_by_text('Live browser connected',exact=True).wait_for()
                        button('Stop capture');page.get_by_role('group',name='Active capture controls').wait_for(state='hidden',timeout=10000)
                        page.get_by_role('button',name='Record safe task steps',exact=True).wait_for(timeout=10000)
                        assert not state.browser.records.list('tab')[0]['recording']
                        if not page.get_by_role('button',name='Managers',exact=True).is_visible():page.keyboard.press('Control+b')
                        button('Managers');
                        if page.locator('.sidebar-dismiss').is_visible():page.keyboard.press('Control+b')
                        page.get_by_role('button',name='Memory',exact=True).wait_for();button('Memory')
                        type_label('Fact or decision','Keyboard proof memory');type_label('Source or reason','Isolated accessibility fixture');button('Save memory');page.get_by_text('Memory saved',exact=True).wait_for();assert any(row['content']=='Keyboard proof memory' for row in state.workspace.store.list('memory',owner.id));audit('manager-memory')
                        button('Models & engines');summary=page.locator('summary').filter(has_text='Add provider account');seek(summary,'Add provider account');page.keyboard.press('Enter');type_label('Account ID','keyboard-fixture-account');type_label('Display name','Keyboard fixture account');audit('manager-account')
                        assert page.get_by_label('API key',exact=True).get_attribute('type')=='password'
                        page.close()
                    context.close()
                browser.close()
                report['fixture_provider_turns']=adapter.turns
                report['task_status_before_cleanup']=state.agent_store.list_tasks()[0]['status'] if state.agent_store.list_tasks() else None
                assert signin_only or (adapter.turns==2 and report['task_status_before_cleanup']=='running')
        finally:
            finish.set();host.should_exit=True;worker.join(timeout=15);listener.close();site.shutdown();site.server_close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
    assert not report['errors'],report['errors']
    failures=[r for r in report['states'] if r['violations']]
    assert not failures,json.dumps(failures,indent=2)
    return report

if __name__=='__main__':
    import sys
    print(json.dumps(run(sys.argv[1],sys.argv[2]),indent=2))
