"""Real keyboard-only browser/manager workflow and axe; no paid inference."""
from __future__ import annotations
import asyncio,hashlib,json,os,re,shutil,socket,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import uvicorn
from playwright.sync_api import sync_playwright

def run(destination,axe_source):
    signin_only=os.environ.get('TERMX_A11Y_SIGNIN_ONLY')=='1'
    features_only=os.environ.get('TERMX_A11Y_BROWSER_FEATURES_ONLY')=='1'
    computer_only=os.environ.get('TERMX_A11Y_COMPUTER_ONLY')=='1'
    safety_only=os.environ.get('TERMX_A11Y_SAFETY_ONLY')=='1'
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    axe=Path(axe_source);assert axe.is_file()
    report={'tool':'axe-core/4.10.3','states':[],'keyboard':[],'errors':[],'paid_provider_queries':0,'limitations':['Rendered DOM and host-semantic browser controls are audited; pixels inside a remotely rendered third-party page and screen-reader speech require separate human evaluation.']}
    with tempfile.TemporaryDirectory(prefix='termx-browser-a11y-') as temporary:
        root=Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderCall,ProviderTurn
        from termx.agent.secrets import CredentialStore
        from test_agent import FakeAdapter
        finish=threading.Event()
        class FixtureAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                self.turns+=1
                if self.turns==1:return ProviderTurn('wait','',[ProviderCall('function','wait','browser_wait_for_handoff',{'seconds':300})],{},[])
                while not finish.is_set():await asyncio.sleep(.05)
                return ProviderTurn('finish','Done',[],{},[])
        adapter=FixtureAdapter()
        state=AppState(passcode=None,credentials=CredentialStore({}),adapter_factory=lambda *_:adapter)
        computer_events=[];computer_refused=threading.Event()
        if computer_only:
            import io
            from PIL import Image
            from termx.desktop import session as computer_port
            from termx.desktop.input import InputError
            image=io.BytesIO();Image.new('RGB',(1280,720),'#284437').save(image,format='JPEG');computer_frame=image.getvalue()
            computer_port.grab_jpeg=lambda *_:computer_frame
            computer_port.list_displays=lambda:[]
            computer_port.permission_snapshot=lambda:{'screen_recording':'granted','accessibility':'granted'}
            def fixture_input(event,*_):
                if computer_refused.is_set() and event.get('type')!='release_all':raise InputError('Fixture input adapter refused control')
                computer_events.append(event)
            computer_port.apply_event=fixture_input
            report['limitations'].append('Computer frames and input ports are explicit fixtures; this proves rendered keyboard/ACK/Stop authority and denial UX, not OS permission or physical display capture.')
        owner=state.identity.setup_owner('a11y-owner','keyboard-browser-fixture-password-123')
        project=state.projects.register(str(root),name='Keyboard browser fixture')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No network fixture',base_url='http://127.0.0.1:9999/v1',model='fixture',capabilities=['shell'],api_key='fixture')
        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'<html><title>Keyboard page</title><h1>Keyboard fixture page</h1><label>Draft<input id="draft" aria-label="Draft"></label><button onclick="console.log(&quot;Preview clicked&quot;);fetch(&quot;/diagnostic&quot;)">Preview draft</button></html>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*_):pass
        site=ThreadingHTTPServer(('127.0.0.1',0),Site);threading.Thread(target=site.serve_forever,daemon=True).start()
        origin=f'http://127.0.0.1:{site.server_port}'
        from termx.browser.network import NetworkPolicy
        production=Path(__file__).resolve().parents[1]/'desktop/workspace/dist'
        frozen=root/'production-ui';shutil.copytree(production,frozen)
        entry=(frozen/'index.html').read_text()
        report['production_bundle']={'index_sha256':hashlib.sha256(entry.encode()).hexdigest(),'entry_assets':re.findall(r'(?:src|href)="([^"]+/assets/[^"]+|/assets/[^"]+)"',entry),'source':'Exact production dist frozen before fixture startup; no source transform or concurrent build asset swaps'}
        app=create_app(state,web_dir=frozen)
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
                zooms=(int(os.environ['TERMX_A11Y_ZOOM']),) if os.environ.get('TERMX_A11Y_ZOOM') else (100,200)
                themes=(os.environ['TERMX_A11Y_THEME'],) if os.environ.get('TERMX_A11Y_THEME') else ('dark','light')
                for zoom in zooms:
                    scale=zoom/100
                    context=browser.new_context(viewport={'width':int(1440/scale),'height':int(1000/scale)},device_scale_factor=scale,reduced_motion='reduce')
                    for theme in themes:
                        if signin_only:context.clear_cookies()
                        page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                        def seek(locator,label):
                            try:locator.wait_for(state='visible',timeout=30000)
                            except Exception:
                                page.screenshot(path=str(destination/f'failed-{theme}-{zoom}.png'))
                                report['failure_context']={'label':label,'body':page.locator('body').inner_text(),'tabs':state.browser.records.list('tab')}
                                raise
                            ready=monotonic()+30
                            while not locator.is_enabled():
                                if monotonic()>ready:
                                    page.screenshot(path=str(destination/f'disabled-{theme}-{zoom}.png'))
                                    report['failure_context']={'label':label,'body':page.locator('body').inner_text(),'fields':page.locator('input,select').evaluate_all("es=>es.map(e=>({label:e.getAttribute('aria-label'),value:e.type==='password'?'<redacted>':e.value}))")}
                                    raise AssertionError('Keyboard control stayed disabled: '+label)
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
                            assert record['reduced_motion'] and not record['overflow'] and not record['violations'],record
                            assert not any(v['id']=='aria-prohibited-attr' for v in record['incomplete']),record
                            if label in {'developer-inspector','computer-expired-private-recovery','computer-global-control-status'}:
                                footer=page.locator('.status-bar button').evaluate_all("es=>es.map(e=>{const r=e.getBoundingClientRect();return {name:e.getAttribute('aria-label')||e.textContent.trim(),left:r.left,right:r.right,top:r.top,bottom:r.bottom,inside:r.left>=-1&&r.right<=innerWidth+1&&r.top>=-1&&r.bottom<=innerHeight+1}})")
                                record['footer_controls']=footer
                                assert footer and all(row['inside'] for row in footer),footer
                        page.goto(f'http://127.0.0.1:{port}/?session={session["id"]}')
                        page.wait_for_function('()=>!!document.querySelector("input[autocomplete=username],textarea[aria-label=Message]")',timeout=30000)
                        if page.get_by_label('Username',exact=True).count():
                            audit('sign-in')
                            type_label('Username','a11y-owner');type_label('Password','keyboard-browser-fixture-password-123');button('Continue with password')
                        page.wait_for_function('()=>{const e=document.querySelector("textarea[aria-label=Message]");return !!e&&!e.disabled}',timeout=60000)
                        if os.environ.get('TERMX_A11Y_GEOMETRY_CSS'):
                            css=Path(os.environ['TERMX_A11Y_GEOMETRY_CSS']).read_text()
                            page.add_style_tag(content=css)
                            report['diagnostic_css_injection']={'sha256':hashlib.sha256(css.encode()).hexdigest(),'purpose':'Narrow geometry diagnosis only; not final production proof'}
                        if signin_only:
                            page.close()
                            continue
                        if (page.locator('html').get_attribute('data-theme') or 'dark')!=theme:
                            button('Appearance');seek(page.locator('input[name=theme-preference]:checked'),'Theme preference')
                            desired=page.get_by_role('radio',name=theme.title(),exact=True)
                            for _ in range(3):
                                if desired.evaluate('e=>e===document.activeElement'):break
                                page.keyboard.press('ArrowRight')
                            seek(desired,theme+' theme');page.keyboard.press('Space');button('Close')
                        if safety_only:
                            button('Action review');button('Configure')
                            checkbox=page.get_by_role('checkbox',name='Include an administrator pricing snapshot',exact=True)
                            seek(checkbox,'Include administrator pricing snapshot');page.keyboard.press('Space')
                            for label,value in [('Currency (3 uppercase letters)','USD'),('Input rate per million tokens','2'),('Cached input rate per million tokens','0.5'),('Output rate per million tokens','8'),('Pricing source','Explicit test fixture, not a market price'),('Pricing snapshot version','fixture-price-v1')]:
                                type_label(label,value)
                            seek(page.get_by_label('Pricing effective date',exact=True),'Pricing effective date')
                            assert page.get_by_role('button',name='Activate qualified reviewer',exact=True).is_disabled()
                            audit('safety-pricing-form')
                            page.close()
                            continue
                        if computer_only:
                            page.keyboard.press('Control+5');button('Watch this machine');page.get_by_alt_text('Live remote computer',exact=True).wait_for(timeout=15000)
                            assert page.get_by_alt_text('Live remote computer',exact=True).evaluate('e=>e.naturalWidth')==1280
                            button('Take control');page.get_by_role('button',name='Return to watch',exact=True).wait_for(timeout=15000)
                            canvas=page.get_by_role('region',name='Remote computer, keyboard control enabled. Tab leaves the canvas; Escape returns to controls.',exact=True)
                            seek(canvas,'Computer remote input canvas');before=len(computer_events);page.keyboard.press('Tab');assert not canvas.evaluate('e=>e===document.activeElement');assert not any(row.get('key')=='Tab' for row in computer_events[before:])
                            seek(canvas,'Computer Escape exit');page.keyboard.press('Escape');assert page.get_by_role('button',name='Return to watch',exact=True).evaluate('e=>e===document.activeElement');assert not any(row.get('key')=='Escape' for row in computer_events[before:]);audit('computer-acknowledged-control')
                            button('Send Tab');deadline=monotonic()+5
                            while not any(row.get('key')=='Tab' for row in computer_events):
                                assert monotonic()<deadline;page.wait_for_timeout(50)
                            button('Pause capture');page.get_by_role('status').filter(has_text='Capture paused').wait_for();button('Resume capture');page.get_by_role('status').filter(has_text='You are in control').wait_for()
                            computer_refused.set();button('Send Escape');page.get_by_role('region',name='Computer workspace',exact=True).get_by_role('alert').wait_for(timeout=10000);page.get_by_role('button',name='Take control',exact=True).wait_for();assert page.get_by_role('button',name='Send Tab',exact=True).is_disabled();audit('computer-control-denied')
                            computer_refused.clear();button('Take control');page.get_by_role('button',name='Return to watch',exact=True).wait_for()
                            # Move focus outside protected remote input before a workspace layout command.
                            seek(page.get_by_role('button',name='Appearance',exact=True),'Workspace controls outside Computer');page.keyboard.press('Control+1')
                            page.get_by_role('group',name='Active capture controls').wait_for(timeout=10000);assert page.get_by_text('1 computer control',exact=True).is_visible();audit('computer-global-control-status');button('Stop capture')
                            page.get_by_role('group',name='Active capture controls').wait_for(state='hidden',timeout=10000);page.keyboard.press('Control+5');page.get_by_role('button',name='Watch this machine',exact=True).wait_for();assert not state.desktop.viewers(owner.id);audit('computer-global-stopped')
                            # A separate session's expired private window preview
                            # must remain a visible, owner-recoverable model pause.
                            from test_window_recording import Port
                            from termx.desktop.recording import assert_agent_capture_allowed
                            state.window_recording.port=Port()
                            private_session=asyncio.run_coroutine_threadsafe(state.identity.login('local-password',{'username':'a11y-owner','password':'keyboard-browser-fixture-password-123'},peer='127.0.0.1'),host_loops[0]).result(timeout=10)
                            private=state.window_recording.start(owner.id,private_session.session_id,owner.policy_version,'1')
                            state.authorization.claim(private_session.access_token,'window-capture',private['id'])
                            state.window_recording.configure(private['id'],owner.id,private=True)
                            state.identity.revoke(private_session.session_id,owner.id)
                            page.get_by_role('group',name='Active capture controls').get_by_text('Private consent ended',exact=False).wait_for(timeout=10000)
                            audit('computer-expired-private-recovery');button('Stop capture')
                            page.get_by_role('group',name='Active capture controls').wait_for(state='hidden',timeout=10000);assert_agent_capture_allowed()
                            report.setdefault('computer_features',[]).append({'theme':theme,'zoom_percent':zoom,'host_ack_before_control':True,'tab_leaves_canvas':True,'escape_restores_controls':True,'explicit_remote_tab':True,'denied_control_cleared':True,'global_status_outside_layout':True,'global_stop_closed_owned_viewer':True})
                            page.close();continue
                        if not features_only and not state.agent_store.list_tasks():
                            type_label('Message','Keyboard browser task');button('Send message');button('Allow once')
                        page.keyboard.press('Control+3')
                        if not state.browser.records.list('profile'):
                            button('Create a browser profile');type_label('New profile name','Keyboard profile');button('Create profile')
                            type_label('Page address',origin);button('Go')
                        page.get_by_text('Live browser connected',exact=True).wait_for(timeout=30000)
                        assert page.locator('.browser-live-view').evaluate('e=>e.naturalWidth')==1440
                        if features_only:
                            type_label('Page address',origin);button('Go')
                            page.wait_for_timeout(350)
                            button('Page controls')
                            select=page.get_by_label('Element to annotate',exact=True);seek(select,'Exact annotation element');select.select_option(index=1);assert select.input_value()
                            type_label('Page annotation',f'Element note {theme} {zoom}')
                            check=page.get_by_role('checkbox',name='Include a masked screenshot reference',exact=True);seek(check,'Masked annotation image opt-in');page.keyboard.press('Space');button('Annotate')
                            page.get_by_text(f'Element note {theme} {zoom}',exact=True).wait_for(timeout=15000)
                            element_article=page.locator('.browser-context article').filter(has=page.get_by_text(f'Element note {theme} {zoom}',exact=True));summary=element_article.locator('summary').filter(has_text='Review masked screenshot');seek(summary,'Review permitted screenshot');page.keyboard.press('Enter')
                            image=page.get_by_alt_text('Masked snapshot for annotation: '+f'Element note {theme} {zoom}',exact=True);image.wait_for();page.wait_for_function('label=>Array.from(document.querySelectorAll(".browser-annotation-image")).some(e=>e.alt===label&&e.naturalWidth===1440)',arg='Masked snapshot for annotation: '+f'Element note {theme} {zoom}')
                            target=page.get_by_label('Comment target',exact=True);seek(target,'Region annotation target');target.select_option('region');assert target.input_value()=='region'
                            type_label('Page annotation',f'Region note {theme} {zoom}');button('Annotate');page.get_by_text(f'Region note {theme} {zoom}',exact=True).wait_for()
                            reference=page.locator('.browser-context article').filter(has=page.get_by_text(f'Region note {theme} {zoom}',exact=True)).locator('summary').filter(has_text='Exact frame and target reference');seek(reference,'Open exact annotation reference');page.keyboard.press('Enter');seek(page.locator('.browser-context article').filter(has=page.get_by_text(f'Region note {theme} {zoom}',exact=True)).get_by_label('Exact annotation frame and target',exact=True),'Scroll exact annotation reference')
                            audit('element-region-annotations')
                            target=element_article.get_by_role('button',name='Capture masked after image',exact=True);seek(target,'Explicit after image capture');page.keyboard.press('Enter')
                            comparison=element_article.locator('summary').filter(has_text='Before / after');comparison.wait_for(timeout=15000);seek(comparison,'Open before after comparison');page.keyboard.press('Enter')
                            seek(element_article.get_by_role('region',name='before target reference',exact=True),'Scroll frozen before reference');seek(element_article.get_by_role('region',name='after target reference',exact=True),'Scroll fresh after reference')
                            page.wait_for_function("label=>Array.from(document.querySelectorAll('.browser-annotation-image')).some(e=>e.alt===label&&e.naturalWidth===1440)",arg='after masked snapshot for '+f'Element note {theme} {zoom}')
                            target=element_article.get_by_role('button',name='Resolve annotation',exact=True);seek(target,'Resolve exact annotation');page.keyboard.press('Enter');element_article.get_by_role('button',name='Reopen annotation',exact=True).wait_for();audit('annotation-before-after-resolved')
                            target=element_article.get_by_role('button',name='Reopen annotation',exact=True);seek(target,'Reopen exact annotation');page.keyboard.press('Enter');element_article.get_by_role('button',name='Resolve annotation',exact=True).wait_for()
                            notes=state.browser.records.list('annotation');assert any(n.get('element') and n.get('screenshot') and n['comment']==f'Element note {theme} {zoom}' for n in notes) and any(n.get('region') and n['comment']==f'Region note {theme} {zoom}' for n in notes)
                            button('Focus input');remote=page.get_by_label('Type into the focused browser page field. Press Escape to return to page controls.',exact=True)
                            seek(remote,'Remote shortcut guard');page.keyboard.press('Control+1');assert page.get_by_role('region',name='Built-in browser workspace').is_visible();page.keyboard.press('Escape')
                            button('Attach this context');page.keyboard.press('Control+1');page.get_by_role('region',name='Selected browser context',exact=True).wait_for(timeout=15000)
                            context_detail=page.get_by_role('region',name='Selected browser context',exact=True).locator('summary').last;seek(context_detail,'Annotated chat context');page.keyboard.press('Enter');seek(page.get_by_role('region',name='Selected browser context',exact=True).get_by_label('Exact selected context',exact=True).last,'Scroll exact selected context');assert f'Region note {theme} {zoom}' in page.get_by_role('region',name='Selected browser context',exact=True).locator('pre').last.inner_text()
                            audit('annotated-chat-context');page.keyboard.press('Control+3')
                            button('Developer inspector');button('Enable developer observation');button('Page controls');button('Focus button');page.wait_for_timeout(200);button('Close')
                            selector=page.get_by_label('Inspector view',exact=True)
                            for index,view in enumerate(('DOM','Console','Network','Performance')):
                                seek(selector,'Inspector '+view);selector.select_option(view.lower());assert selector.input_value()==view.lower()
                                button('Refresh inspector');page.get_by_role('region',name='Developer browser inspector').locator('pre').wait_for()
                                page.wait_for_function("view=>Array.from(document.querySelectorAll('.browser-context pre')).some(e=>{try{return Object.hasOwn(JSON.parse(e.textContent),view)}catch{return false}})",arg=view.lower())
                                seek(page.get_by_label('Developer diagnostic data',exact=True),'Scroll '+view+' diagnostic data')
                            bounds=page.get_by_role('region',name='Developer browser inspector',exact=True).evaluate("e=>({left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width,viewport:innerWidth,preWidth:e.querySelector('pre')?.getBoundingClientRect().width,scrollLeft:e.closest('.browser-workspace').scrollLeft})")
                            if not (bounds['left']>=-1 and bounds['right']<=bounds['viewport']+1 and bounds['preWidth']<=bounds['width'] and bounds['scrollLeft']==0):
                                page.screenshot(path=str(destination/f'inspector-geometry-failure-{theme}-{zoom}.png'))
                                report['inspector_geometry_failure']=page.get_by_role('region',name='Developer browser inspector',exact=True).evaluate("e=>{const result=[];while(e){const r=e.getBoundingClientRect(),s=getComputedStyle(e);result.push({class:e.className,tag:e.tagName,left:r.left,right:r.right,width:r.width,minWidth:s.minWidth,display:s.display,flex:s.flex,overflowX:s.overflowX});e=e.parentElement}return result}")
                                raise AssertionError(bounds)
                            record=page.locator('.browser-inspector-controls').evaluate("e=>Array.from(e.querySelectorAll('button,select')).map(n=>{const b=n.getBoundingClientRect(),r=e.getBoundingClientRect();return {text:n.textContent,left:b.left,right:b.right,width:b.width,inside:b.left>=r.left-1&&b.right<=r.right+1}})")
                            assert all(row['inside'] for row in record),record
                            audit('developer-inspector');button('Stop developer observation');button('Close inspector')
                            count=page.get_by_role('tab').count();seek(page.get_by_label('Page address',exact=True),'Scoped browser tab commands');page.keyboard.press('Control+Alt+t');page.wait_for_function('n=>document.querySelectorAll(".browser-tabs [role=tab]").length===n',arg=count+1)
                            seek(page.get_by_label('Page address',exact=True),'Close own browser tab');page.keyboard.press('Control+Alt+w');page.wait_for_function('n=>document.querySelectorAll(".browser-tabs [role=tab]").length===n',arg=count)
                            seek(page.get_by_label('Page address',exact=True),'Reopen own browser tab');page.keyboard.press('Control+Alt+Shift+t');page.wait_for_function('n=>document.querySelectorAll(".browser-tabs [role=tab]").length===n',arg=count+1)
                            assert not any(t.get('grant_id') for t in state.browser.records.list('tab'))
                            button('History');page.get_by_role('region',name='Browser history').wait_for();audit('profile-history');button('Clear this profile history')
                            page.wait_for_function("()=>{const e=document.querySelector('[aria-label=\"Browser history\"]');return !!e&&e.querySelectorAll('p').length===0}")
                            assert not state.browser.records.list('history');assert state.browser.records.list('profile');button('Close history')
                            report.setdefault('browser_features',[]).append({'theme':theme,'zoom_percent':zoom,'element_and_region_comments':True,'masked_image_reference_rendered':True,'before_after_images_rendered':True,'resolve_reopen_preserved_reference':True,'labelled_reference_regions_valid':True,'inspector_controls_inside_container':record,'annotated_chat_context':True,'inspector_views':4,'remote_shortcut_stayed_in_page':True,'scoped_new_close_reopen':True,'history_clear_kept_profile':True,'agent_grants':0,'native_select_method':'Playwright form selection; real Tab/focus checks retained. Headless macOS does not synthesize native popup key selection.'})
                            page.close();continue
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
                assert signin_only or safety_only or features_only or computer_only or (adapter.turns==2 and report['task_status_before_cleanup']=='running')
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
