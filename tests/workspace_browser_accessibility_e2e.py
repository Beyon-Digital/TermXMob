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
    paste_only=os.environ.get('TERMX_A11Y_PASTE_ONLY')=='1'
    features_only=os.environ.get('TERMX_A11Y_BROWSER_FEATURES_ONLY')=='1' or paste_only
    docking_only=os.environ.get('TERMX_A11Y_COMPUTER_DOCKING_ONLY')=='1'
    computer_only=os.environ.get('TERMX_A11Y_COMPUTER_ONLY')=='1' or docking_only
    safety_only=os.environ.get('TERMX_A11Y_SAFETY_ONLY')=='1'
    rules_only=os.environ.get('TERMX_A11Y_SAFETY_RULES_ONLY')=='1'
    layouts_only=os.environ.get('TERMX_A11Y_LAYOUTS_ONLY')=='1'
    lock_only=os.environ.get('TERMX_A11Y_LOCK_ONLY')=='1'
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    axe=Path(axe_source);assert axe.is_file()
    report={'tool':'axe-core/4.10.3','focus_geometry_method':'Native IntersectionObserver clipped intersection plus elementFromPoint; fixed portal contents are bounded by their actual containing block, not an unrelated zero-height body box','states':[],'keyboard':[],'errors':[],'paid_provider_queries':0,'limitations':['Rendered DOM and host-semantic browser controls are audited; pixels inside a remotely rendered third-party page and screen-reader speech require separate human evaluation.']}
    with tempfile.TemporaryDirectory(prefix='termx-browser-a11y-') as temporary:
        root=Path(temporary)
        (root/'layout-fixture.txt').write_text('Saved code buffer\n')
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderCall,ProviderTurn
        from termx.agent.secrets import CredentialStore
        from test_agent import FakeAdapter
        finish=threading.Event();host_stop_received=threading.Event()
        class FixtureAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                self.turns+=1
                if self.turns==1:return ProviderTurn('wait','',[ProviderCall('function','wait','browser_wait_for_handoff',{'seconds':300})],{},[])
                while not finish.is_set():await asyncio.sleep(.05)
                return ProviderTurn('finish','Done',[],{},[])
        adapter=FixtureAdapter()
        state=AppState(passcode=None,credentials=CredentialStore({}),adapter_factory=lambda *_:adapter)
        state.request_shutdown=lambda:host_stop_received.set()
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
        report['production_assets']=[{'path':str(p.relative_to(frozen)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(frozen.rglob('*')) if p.is_file()]
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
                            else:
                                page.screenshot(path=str(destination/f'unreachable-{theme}-{zoom}.png'));report['failure_context']={'label':label,'body':page.locator('body').inner_text(),'focus':page.evaluate('()=>({tag:document.activeElement?.tagName,name:document.activeElement?.getAttribute("aria-label"),text:document.activeElement?.textContent?.slice(0,200)})')};raise AssertionError('Keyboard cannot reach '+label)
                            focus=locator.evaluate("""async e=>{const s=getComputedStyle(e),b=e.getBoundingClientRect();const clip=await new Promise(resolve=>{const observer=new IntersectionObserver(entries=>{observer.disconnect();const r=entries[0].intersectionRect;resolve({left:r.left,right:r.right,top:r.top,bottom:r.bottom})});observer.observe(e)});const hit=document.elementFromPoint((clip.left+clip.right)/2,(clip.top+clip.bottom)/2);let a=e,bg='';while(a){const c=getComputedStyle(a).backgroundColor;if(c!=='rgba(0, 0, 0, 0)'&&c!=='transparent'){bg=c;break}a=a.parentElement}return {label:e.getAttribute('aria-label')||e.textContent?.trim(),outline:s.outlineStyle,outline_width:s.outlineWidth,foreground:e.placeholder&&!e.value?getComputedStyle(e,'::placeholder').color:s.color,background:bg,width:b.width,height:b.height,visible_width:Math.max(0,clip.right-clip.left),visible_height:Math.max(0,clip.bottom-clip.top),uncovered:hit===e||e.contains(hit),onscreen:b.right>0&&b.left<innerWidth&&b.bottom>0&&b.top<innerHeight}}""")
                            if not(focus['visible_width']>=12 and focus['visible_height']>=12 and focus['uncovered']):
                                page.screenshot(path=str(destination/f'focus-geometry-{theme}-{zoom}.png'))
                                report['failure_geometry']={'label':label,'focus':focus,'ancestors':locator.evaluate("e=>{let rows=[];while(e){const r=e.getBoundingClientRect(),s=getComputedStyle(e);rows.push({tag:e.tagName,class:e.className,position:s.position,transform:s.transform,overflowX:s.overflowX,overflowY:s.overflowY,left:r.left,right:r.right,top:r.top,bottom:r.bottom});e=e.parentElement}return rows}")}
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
                            resolved_theme=page.locator('html').get_attribute('data-theme');assert label=='sign-in' or resolved_theme==theme
                            record={'theme':resolved_theme,'requested_theme':theme,'zoom_percent':zoom,'state':label,'reduced_motion':page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'impact':v['impact'],'nodes':[{'target':n['target'],'summary':n.get('failureSummary')} for n in v['nodes']]} for v in result['violations']],'incomplete':[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['incomplete']]}
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
                        page.goto(f'http://127.0.0.1:{port}/?session={session["id"]}'+('&layout=computer' if docking_only else ''))
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
                        if lock_only:
                            # Complement the dark100 dirty-task/PTY/artifact flow
                            # with loaded lock/unlock forms in every theme/zoom.
                            # Host setup is explicit; no inference/OS capture.
                            type_label('Message','Lock matrix retained draft')
                            managed=context.request.get(f'http://127.0.0.1:{port}/auth/me').json()
                            csrf=page.evaluate("decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('termx_csrf=')).slice('termx_csrf='.length))")
                            headers={'X-Termx-CSRF':csrf,'Origin':f'http://127.0.0.1:{port}'}
                            profile=context.request.post(f'http://127.0.0.1:{port}/api/browser/profiles',headers=headers,data={'project_id':project['id'],'name':'Lock matrix '+theme+str(zoom),'ephemeral':True});assert profile.ok,profile.text()
                            tab=context.request.post(f'http://127.0.0.1:{port}/api/browser/tabs',headers=headers,data={'profile_id':profile.json()['id'],'url':origin});assert tab.ok,tab.text()
                            seek(page.get_by_role('button',name='Appearance',exact=True),'Lock matrix browser origin');page.keyboard.press('Control+3');picker=page.get_by_label('Browser profile',exact=True);seek(picker,'Exact owned Lock matrix profile');picker.select_option(profile.json()['id']);page.get_by_role('img',name=re.compile('^Live rendered browser page')).wait_for(timeout=30000)
                            button('Lock workspace');page.get_by_role('dialog',name='Workspace locked',exact=True).wait_for(timeout=15000)
                            assert page.locator('.workspace-root').evaluate("e=>e.inert&&getComputedStyle(e).visibility==='hidden'")
                            assert context.request.get(f'http://127.0.0.1:{port}/api/browser/tabs').status==423
                            assert state.browser.records.get('tab',tab.json()['id'])['state']=='private'
                            audit('lock-matrix-loaded')
                            type_label('Password','wrong-fixture-password');button('Unlock workspace');page.get_by_role('alert').last.wait_for();assert page.get_by_label('Password',exact=True).input_value()=='';audit('lock-matrix-refused')
                            page.reload();page.get_by_role('dialog',name='Workspace locked',exact=True).wait_for(timeout=30000);assert not page.get_by_role('heading',name='Connect to your workspace',exact=True).count();audit('lock-matrix-reloaded')
                            type_label('Password','keyboard-browser-fixture-password-123');button('Unlock workspace');page.get_by_role('button',name='Lock workspace',exact=True).wait_for(timeout=30000)
                            restored=context.request.get(f'http://127.0.0.1:{port}/auth/me').json();assert restored['session_id']==managed['session_id'] and restored['principal']['id']==managed['principal']['id']
                            seek(page.get_by_role('button',name='Appearance',exact=True),'Lock matrix explicit resume origin');page.keyboard.press('Control+3');page.get_by_role('button',name='Resume browser view',exact=True).wait_for();assert not page.get_by_role('img',name=re.compile('^Live rendered browser page')).count();button('Resume browser view');page.get_by_role('img',name=re.compile('^Live rendered browser page')).wait_for(timeout=30000);assert state.browser.records.get('tab',tab.json()['id'])['state']=='private';audit('lock-matrix-explicit-view')
                            seek(page.get_by_role('button',name='Appearance',exact=True),'Lock matrix retained composer origin');page.keyboard.press('Control+1');page.wait_for_function("()=>document.querySelector('textarea[aria-label=Message]')?.value==='Lock matrix retained draft'",timeout=15000)
                            button('Commands');type_label('Search commands','Stop this host');seek(page.get_by_role('button',name='Stop this host',exact=True),'Lock matrix inspect host');page.keyboard.press('Enter');page.get_by_role('dialog',name='Stop this host',exact=True).wait_for();assert page.get_by_role('button',name='Stop host now',exact=True).is_disabled();audit('lock-matrix-host-stop-inspect');button('Close')
                            # Isolated host shutdown cleans these temporary profiles.
                            # Profile-deletion latency is not a Lock UI assertion.
                            report.setdefault('lock_matrix',[]).append({'theme':theme,'zoom_percent':zoom,'same_sid':True,'protected_locked423':True,'wrong_password_cleared':True,'reload_keeps_lock':True,'explicit_view_resume_only':True,'retained_draft':True,'no_agent_grant_or_os_capture':True})
                            page.close();continue
                        if layouts_only:
                            # Real production App plus actual worker, file and PTY.
                            # The fixture provider never sends a paid/network request.
                            if not state.agent_store.list_tasks():
                                type_label('Message','Keep this task running during layout changes');button('Send message');button('Allow once')
                            deadline=monotonic()+30
                            while not state.agent_store.list_tasks() or state.agent_store.list_tasks()[0]['status']!='running':
                                assert monotonic()<deadline,state.agent_store.list_tasks();page.wait_for_timeout(50)
                            task=state.agent_store.list_tasks()[0]
                            seek(page.get_by_role('button',name='Appearance',exact=True),'Workspace shortcut origin');page.keyboard.press('Control+2')
                            button('Commands');type_label('Search commands','Quick file open');seek(page.get_by_role('button',name=re.compile('^Quick file open')),'Quick file command');page.keyboard.press('Enter')
                            type_label('Find file by name','layout-fixture.txt');result=page.get_by_role('option',name='layout-fixture.txt',exact=True);result.wait_for();assert result.get_attribute('aria-selected')=='true';assert page.get_by_label('Find file by name',exact=True).get_attribute('aria-activedescendant')==result.get_attribute('id');page.keyboard.press('Enter')
                            editor=page.locator('.cm-content[contenteditable=true]').first;seek(editor,'Actual editable file buffer');page.keyboard.press('ControlOrMeta+A');page.keyboard.type('Preserved unsaved code buffer')
                            button('New terminal');page.wait_for_function("()=>!!document.querySelector('.terminal-region')?.dataset.connectedSession",timeout=30000)
                            terminal=page.locator('.terminal-region').get_attribute('data-connected-session');assert terminal
                            page.keyboard.press('Shift+Tab') # Documented escape retains plain terminal Tab.
                            dock_browser=page.get_by_label('Dock browser',exact=True)
                            # Hidden-pane selector is not a user control; make the
                            # browser visible through its supported focus command.
                            page.keyboard.press('Control+Shift+p');type_label('Search commands','Focus browser');seek(page.get_by_role('button',name=re.compile('^Focus browser')),'Focus browser command');page.keyboard.press('Enter');page.get_by_role('dialog',name='Workspace commands',exact=True).wait_for(state='hidden')
                            seek(page.get_by_label('Dock browser',exact=True),'Browser dock placement');page.get_by_label('Dock browser',exact=True).select_option('bottom')
                            seek(page.get_by_role('button',name='Appearance',exact=True),'Chat layout origin');page.keyboard.press('Control+1');type_label('Message','Preserved unsent conversation draft')
                            button('Media & artifacts');summary=page.get_by_text('Create an artifact',exact=True);seek(summary,'Create artifact section');page.keyboard.press('Enter');type_label('Title','Layout artifact');button('Create artifact')
                            type_label('Artifact content','Preserved unsaved artifact edit')
                            seek(page.get_by_label('Dock editor',exact=True),'Editor dock placement') if page.get_by_label('Dock editor',exact=True).is_visible() else None
                            # The named layout retains only geometry. Editor/file,
                            # task and terminal authority remain their existing state.
                            button('Layouts');type_label('Layout name','Artifact inspection');button('Save current layout');audit('named-layout-saved');button('Close')
                            seek(page.get_by_role('button',name='Appearance',exact=True),'Workbench restore origin');page.keyboard.press('Control+2')
                            assert page.get_by_label('Dock browser',exact=True).input_value()=='bottom'
                            assert page.locator('.cm-content[contenteditable=true]').first.inner_text().strip()=='Preserved unsaved code buffer'
                            assert page.locator('.terminal-region').get_attribute('data-connected-session')==terminal
                            button('Layouts');button('Restore Artifact inspection');page.get_by_label('Artifact content',exact=True).wait_for();assert page.get_by_label('Artifact content',exact=True).input_value()=='Preserved unsaved artifact edit'
                            audit('artifact-retained-layout')
                            button('Layouts');button('Reset current layout preset');assert page.get_by_role('button',name='Restore Artifact inspection',exact=True).is_visible();button('Close')
                            button('Layouts');button('Restore Artifact inspection')
                            page.reload();page.get_by_role('button',name='Layouts',exact=True).wait_for(timeout=60000);button('Layouts');button('Restore Artifact inspection')
                            preserved=page.get_by_text('Unsaved artifact drafts',exact=True);seek(preserved,'Preserved artifact drafts');page.keyboard.press('Enter');button('Restore draft Layout artifact');assert page.get_by_label('Artifact content',exact=True).input_value()=='Preserved unsaved artifact edit'
                            button('Layouts');button('Restore Artifact inspection');assert page.get_by_label('Artifact content',exact=True).input_value()=='Preserved unsaved artifact edit'
                            # Two actual same-owner windows preserve conflicting
                            # edits; return ACK follows main durable persistence.
                            with page.expect_popup() as opened:button('Detach artifacts')
                            popup=opened.value;popup.on('pageerror',lambda error:report['errors'].append('Detached: '+str(error)));main=page;page=popup
                            page.get_by_text('Unsaved artifact drafts',exact=True).wait_for(timeout=60000);seek(page.get_by_text('Unsaved artifact drafts',exact=True),'Detached artifact draft');page.keyboard.press('Enter');button('Restore draft Layout artifact');type_label('Artifact content','Detached artifact edit')
                            page=main;page.bring_to_front();type_label('Artifact content','Main artifact edit');page=popup;page.bring_to_front()
                            button('Return to main workspace');popup.wait_for_event('close',timeout=45000);page=main
                            page.get_by_text('Unsaved artifact drafts',exact=True).wait_for();
                            if not page.get_by_role('button',name='Restore draft Layout artifact (returned copy)',exact=True).is_visible():seek(page.get_by_text('Unsaved artifact drafts',exact=True),'Returned conflict copies');page.keyboard.press('Enter')
                            button('Restore draft Layout artifact (returned copy)');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value==='Detached artifact edit'",timeout=15000)
                            button('Restore draft Layout artifact');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value==='Main artifact edit'",timeout=15000)
                            artifact=state.artifacts.list(owner.id,project['id'])[0];assert state.artifacts.get(owner.id,artifact['id'])['content']==''
                            assert task['id']==state.agent_store.list_tasks()[0]['id'] and state.agent_store.get_task(task['id'])['status']=='running'
                            audit('artifact-return-conflict-copies')
                            button('Layouts');button('Remove Artifact inspection');assert not page.get_by_role('button',name='Restore Artifact inspection',exact=True).count();audit('named-layout-removed');button('Close')
                            report.setdefault('layout_artifact_features',[]).append({'theme':theme,'zoom_percent':zoom,'canonical_task_running':task['id'],'real_pty_preserved_across_views':terminal,'dirty_file_and_conversation_preserved':True,'named_save_restore_reset_reload_remove':True,'artifact_independent_detach':True,'two_conflicting_artifact_drafts_preserved_after_ack':True,'no_implicit_artifact_save':True,'native_picker_method':'Tab/focus then select_option','fixture_limits':'No paid calls; native installed detachment is not exercised'})
                            # Inspection cannot replace or silently clear a dirty
                            # editor. Historical content is only copied by an
                            # explicit action into its own durable draft.
                            button('Save new version');page.wait_for_function("()=>document.querySelector('select[aria-label=\"Inspect version\"]')?.value==='2'",timeout=15000)
                            type_label('Artifact content','Dirty current edit after saved v2')
                            seek(page.get_by_label('Inspect version',exact=True),'Read-only historical version picker');page.get_by_label('Inspect version',exact=True).select_option('1')
                            page.get_by_role('region',name='Saved artifact version',exact=True).wait_for();assert page.get_by_label('Artifact content',exact=True).input_value()=='Dirty current edit after saved v2'
                            seek(page.get_by_role('region',name='Saved artifact content version 1',exact=True),'Read-only historical content');audit('artifact-history-keeps-current-edit')
                            button('Copy saved version to a separate draft');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value===''",timeout=15000)
                            type_label('Artifact content','Historical copy edit')
                            button('Restore draft Layout artifact');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value==='Dirty current edit after saved v2'",timeout=15000)
                            button('Restore draft Layout artifact (restored v1)');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value==='Historical copy edit'",timeout=15000)
                            button('Restore draft Layout artifact');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value==='Dirty current edit after saved v2'",timeout=15000)
                            assert state.artifacts.get(owner.id,artifact['id'])['version']==2 and state.artifacts.get(owner.id,artifact['id'])['content']=='Main artifact edit'
                            audit('artifact-history-separate-copy');report['artifact_history']={'explicit_save_version':2,'inspected_version':1,'read_only_preview_preserves_current_dirty_edit':True,'historical_copy_has_distinct_persistent_draft':True,'original_and_copy_independently_restorable':True,'server_content_unchanged_by_inspection_or_copy':True}
                            if os.environ.get('TERMX_A11Y_LOCK_WITH_LAYOUTS')=='1':
                                # Same real enrolled SID, durable drafts and worker.
                                managed=context.request.get(f'http://127.0.0.1:{port}/auth/me').json()
                                csrf=page.evaluate("decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('termx_csrf=')).slice('termx_csrf='.length))")
                                profile=context.request.post(f'http://127.0.0.1:{port}/api/browser/profiles',headers={'X-Termx-CSRF':csrf,'Origin':f'http://127.0.0.1:{port}'},data={'project_id':project['id'],'name':'Lock fixture','ephemeral':True});assert profile.ok,profile.text()
                                tab=context.request.post(f'http://127.0.0.1:{port}/api/browser/tabs',headers={'X-Termx-CSRF':csrf,'Origin':f'http://127.0.0.1:{port}'},data={'profile_id':profile.json()['id'],'url':origin});assert tab.ok,tab.text()
                                tab_id=tab.json()['id']
                                seek(page.get_by_role('button',name='Appearance',exact=True),'Browser lock origin');page.keyboard.press('Control+3');page.get_by_role('img',name=re.compile('^Live rendered browser page')).wait_for(timeout=30000)
                                button('Lock workspace');page.get_by_role('dialog',name='Workspace locked',exact=True).wait_for(timeout=15000)
                                assert page.locator('.workspace-root').evaluate("e=>e.inert&&getComputedStyle(e).visibility==='hidden'")
                                assert context.request.get(f'http://127.0.0.1:{port}/api/browser/tabs').status==423
                                locked_tab=state.browser.records.get('tab',tab_id);assert locked_tab['state']=='private' and not locked_tab.get('grant_id')
                                audit('workspace-locked-retained')
                                type_label('Password','incorrect-fixture-password');button('Unlock workspace');page.get_by_role('alert').last.wait_for();assert page.get_by_role('dialog',name='Workspace locked',exact=True).is_visible();assert page.get_by_label('Password',exact=True).input_value()==''
                                audit('workspace-unlock-refused')
                                page.reload();page.get_by_role('dialog',name='Workspace locked',exact=True).wait_for(timeout=30000);assert not page.get_by_role('heading',name='Connect to your workspace',exact=True).count();audit('workspace-locked-reload')
                                type_label('Password','keyboard-browser-fixture-password-123');button('Unlock workspace');page.get_by_role('button',name='Lock workspace',exact=True).wait_for(timeout=30000)
                                restored=context.request.get(f'http://127.0.0.1:{port}/auth/me').json();assert restored['session_id']==managed['session_id'] and restored['principal']['id']==managed['principal']['id']
                                seek(page.get_by_role('button',name='Appearance',exact=True),'Browser after unlock');page.keyboard.press('Control+3')
                                page.get_by_role('button',name='Resume browser view',exact=True).wait_for();assert not page.get_by_role('img',name=re.compile('^Live rendered browser page')).count();button('Resume browser view');page.get_by_role('img',name=re.compile('^Live rendered browser page')).wait_for(timeout=30000);assert state.browser.records.get('tab',tab_id)['state']=='private';audit('workspace-unlocked-explicit-view')
                                button('Media & artifacts');page.get_by_text('Unsaved artifact drafts',exact=True).wait_for();seek(page.get_by_text('Unsaved artifact drafts',exact=True),'Artifact drafts after same-SID unlock');page.keyboard.press('Enter');button('Restore draft Layout artifact');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value==='Dirty current edit after saved v2'",timeout=15000);button('Restore draft Layout artifact (restored v1)');page.wait_for_function("()=>document.querySelector('textarea[aria-label=\"Artifact content\"]')?.value==='Historical copy edit'",timeout=15000);audit('workspace-unlocked-artifact-copies')
                                seek(page.get_by_role('button',name='Appearance',exact=True),'Retained chat origin');page.keyboard.press('Control+1');page.wait_for_function("()=>document.querySelector('textarea[aria-label=Message]')?.value==='Preserved unsent conversation draft'",timeout=15000)
                                seek(page.get_by_role('button',name='Appearance',exact=True),'Retained editor origin');page.keyboard.press('Control+2');page.wait_for_function("()=>document.querySelector('.cm-content')?.textContent==='Preserved unsaved code buffer'",timeout=15000)
                                try:page.wait_for_function("expected=>document.querySelector('.terminal-region')?.dataset.connectedSession===expected",arg=terminal,timeout=30000)
                                except Exception:
                                    page.screenshot(path=str(destination/f'pty-unlock-recovery-{theme}-{zoom}.png'))
                                    report['pty_unlock_failure']={'expected':terminal,'ui':page.locator('.terminal-region').evaluate("e=>({connected:e.dataset.connectedSession,selected:e.querySelector('select')?.value,labels:e.querySelector('.terminal-toolbar')?.innerText,options:Array.from(e.querySelectorAll('option')).map(o=>({id:o.value,label:o.textContent}))})"),'current_task_status':state.agent_store.get_task(task['id'])['status']}
                                    raise
                                assert state.agent_store.get_task(task['id'])['status']=='running'
                                button('Commands');type_label('Search commands','Stop this host');seek(page.get_by_role('button',name='Stop this host',exact=True),'Inspect host shutdown');page.keyboard.press('Enter');page.get_by_role('dialog',name='Stop this host',exact=True).wait_for();stop=page.get_by_role('button',name='Stop host now',exact=True);assert stop.is_disabled();audit('host-stop-inspected')
                                seek(page.get_by_role('checkbox'),'Acknowledge exact host shutdown');page.keyboard.press('Space');button('Stop host now');page.get_by_role('status').filter(has_text='The host accepted shutdown').wait_for();assert host_stop_received.wait(3);audit('host-stop-accepted-fixture')
                                report['lock_lifecycle']={'same_managed_sid':True,'private_tab_grant_revoked':True,'workspace_retained_hidden':True,'wrong_password_keeps_lock':True,'locked_reload_does_not_sign_out':True,'no_automatic_browser_or_computer_resume':True,'explicit_view_resume_keeps_private_barrier':True,'dirty_file_chat_artifact_retained':True,'same_pty':terminal,'same_running_task':task['id'],'host_stop_explicit_current_id_ack':True,'host_stop_callback':'Explicit isolated event callback; no real external host/process shutdown'}
                            page.close();continue
                        if rules_only:
                            from termx.auto_review import ActionEnvelope,canonical_hash
                            managed=context.request.get(f'http://127.0.0.1:{port}/auth/me').json()
                            task=state.agent_store.create_task(prompt='Explicit fixture task for remembered rule editor; no provider worker',cwd=str(root),provider_id='fixture',model='fixture',limits={},status='running')
                            authority={'id':task['id'],'principal_id':owner.id,'session_id':managed['session_id'],'project_id':project['id'],'policy_version':owner.policy_version}
                            state.browser.records.put('agent-task-authority',task['id'],authority)
                            envelope=ActionEnvelope('rule-fixture-'+task['id'],owner.id,managed['session_id'],project['id'],task['id'],'agent.read_file',canonical_hash({'path':'explicit-fixture.txt'}),str(root),'observe','task:'+task['id'],owner.policy_version)
                            rule=state.browser.review.rule(envelope,'BLOCK',expires_at=__import__('time').time()+3600)
                            button('Action review')
                            inspect=page.get_by_text('Inspect exact remembered scope',exact=True);seek(inspect,'Inspect exact remembered scope');page.keyboard.press('Enter')
                            seek(page.get_by_role('region',name='Immutable remembered rule scope',exact=True),'Scrollable exact remembered scope')
                            assert page.get_by_role('region',name='Immutable remembered rule scope',exact=True).inner_text()==json.dumps(rule['scope'],indent=2)
                            audit('safety-remembered-scope')
                            button('Edit remembered rule')
                            decision=page.get_by_label('Remembered decision',exact=True);seek(decision,'Bounded remembered decision');decision.select_option('ALLOW')
                            type_label('Expires after seconds','600')
                            button('Save remembered rule');page.get_by_text('Remembered rule updated. Scope and host restrictions are unchanged.',exact=True).wait_for(timeout=10000)
                            updated=state.browser.records.get('review-rule',rule['id'])
                            assert updated['scope']==rule['scope'] and updated['decision']=='ALLOW' and updated['revision']==2 and __import__('time').time()+590<updated['expires_at']<__import__('time').time()+610
                            audit('safety-remembered-edited')
                            button('Revoke');page.get_by_text('No remembered permissions.',exact=True).wait_for()
                            assert not state.browser.records.list('review-rule')
                            audit('safety-remembered-revoked')
                            state.agent_store.update_task(task['id'],status='completed')
                            report.setdefault('remembered_rule_editor',[]).append({'theme':theme,'zoom_percent':zoom,'exact_scope_inspected':True,'same_live_session_and_task':True,'revision_checked_decision_and_expiry_only':True,'rule_revoked':True,'fixture_scope':'Real managed session and canonical running fixture task, seeded scoped rule; no provider worker or website operation','decision_picker':'Actual Tab/focus plus select_option for native picker'})
                            button('Close');page.close();continue
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
                            if docking_only:
                                original=state.desktop.viewers(owner.id)
                                assert len(original)==1 and original[0]['state']=='control',original
                                computer_panel=page.get_by_role('region',name='computer panel',exact=True)
                                assert computer_panel.is_visible() and not page.get_by_role('region',name='browser panel',exact=True).is_visible()
                                assert computer_panel.locator('.dock-pane-header').get_by_role('button',name='Computer',exact=True).is_visible()
                                seek(page.get_by_label('Dock computer',exact=True),'Computer dock control outside remote capture')
                                page.keyboard.press('Control+k');page.keyboard.press('z')
                                page.wait_for_function('()=>document.querySelector(".dock-computer").style.gridArea==="main"&&getComputedStyle(document.querySelector(".dock-conversation")).display==="none"')
                                assert page.get_by_alt_text('Live remote computer',exact=True).is_visible()
                                assert state.desktop.viewers(owner.id)==original
                                audit('computer-focus-preserves-canvas')
                                page.keyboard.press('Escape')
                                page.wait_for_function('()=>document.querySelector(".dock-conversation").style.gridArea==="right"')
                                assert page.get_by_role('button',name='Return to watch',exact=True).is_visible()
                                assert state.desktop.viewers(owner.id)==original
                                audit('computer-focus-restored')
                                if zoom==100:
                                    seek(page.get_by_label('Dock computer',exact=True),'Independent Computer bottom placement');page.get_by_label('Dock computer',exact=True).select_option('bottom')
                                    button('Commands');type_label('Search commands','Focus browser')
                                    seek(page.get_by_role('button',name=re.compile(r'^Focus browser')),'Focus browser command');page.keyboard.press('Enter')
                                    page.get_by_role('region',name='browser panel',exact=True).wait_for()
                                    # Keep both real surfaces wide enough for their controls.
                                    seek(page.get_by_label('Dock browser',exact=True),'Independent browser dock placement')
                                    page.get_by_label('Dock browser',exact=True).select_option('main')
                                    seek(page.get_by_label('Dock conversation',exact=True),'Conversation tab group placement')
                                    page.get_by_label('Dock conversation',exact=True).select_option('hidden')
                                    if not state.browser.records.list('profile'):
                                        button('Create a browser profile');type_label('New profile name','Independent browser profile');button('Create profile')
                                    type_label('Page address',origin);button('Go')
                                    page.get_by_text('Live browser connected',exact=True).wait_for(timeout=30000)
                                    assert page.get_by_alt_text('Live remote computer',exact=True).is_visible()
                                    assert page.locator('.browser-live-view').is_visible()
                                    for frame in [page.get_by_alt_text('Live remote computer',exact=True),page.locator('.browser-live-view')]:
                                        assert frame.evaluate('e=>{const r=e.getBoundingClientRect(),p=e.closest(".dock-pane-content").getBoundingClientRect();return Math.min(r.bottom,p.bottom,innerHeight)-Math.max(r.top,p.top,0)>60}')
                                    assert state.desktop.viewers(owner.id)==original
                                    assert computer_panel.locator('.dock-pane-header').get_by_role('button',name='Computer',exact=True).is_visible()
                                    audit('independent-browser-computer-canvases')
                                with page.expect_popup() as detached:
                                    button('Detach computer')
                                popup=detached.value
                                popup.wait_for_url(re.compile(r'.*[?&]layout=computer(?:&|$).*'),timeout=30000)
                                popup.get_by_role('region',name='computer panel',exact=True).wait_for(timeout=30000)
                                assert not popup.get_by_role('region',name='browser panel',exact=True).is_visible()
                                assert popup.get_by_role('button',name='Watch this machine',exact=True).is_visible()
                                assert not popup.get_by_role('button',name='Return to watch',exact=True).count()
                                assert state.desktop.viewers(owner.id)==original
                                popup.screenshot(path=str(destination/f'computer-detached-exact-canvas-{theme}-{zoom}.png'))
                                popup.close()
                                button('Stop watching')
                                deadline=monotonic()+10
                                while state.desktop.viewers(owner.id):
                                    assert monotonic()<deadline;page.wait_for_timeout(50)
                                report.setdefault('computer_docking',[]).append({'theme':theme,'zoom_percent':zoom,'same_controlled_viewer_during_focus_restore':True,'independent_simultaneous_live_canvases':zoom==100,'exact_computer_popup_without_control_inheritance':True,'dock_picker_method':'Actual Tab/focus plus Playwright select_option for native picker; no DOM mutation'})
                                page.close();continue
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
                        if paste_only:
                            button('Page controls');button('Focus input');remote=page.get_by_label('Type into the focused browser page field. Press Escape to return to page controls.',exact=True);seek(remote,'Explicit paste target')
                            remote.evaluate("e=>{const data=new DataTransfer();data.setData('text/plain','Explicit clipboard event fixture');e.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}))}")
                            browser_tab=state.browser.records.list('tab')[0]
                            async def page_value():return await state.browser._pages[browser_tab['id']].locator('#draft').input_value()
                            deadline=monotonic()+10
                            while asyncio.run_coroutine_threadsafe(page_value(),host_loops[0]).result(timeout=10)!='Explicit clipboard event fixture':
                                assert monotonic()<deadline;page.wait_for_timeout(50)
                            page.keyboard.press('Escape');audit('explicit-browser-paste')
                            report['paste_features']={'explicit_dom_clipboard_event_to_real_managed_page':True,'text':'Explicit clipboard event fixture','background_clipboard_reads':0,'host_os_clipboard_used':False,'limit':'Synthetic explicit paste event uses isolated browser DataTransfer; OS/native clipboard integration remains separate.'}
                            page.close();continue
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
                assert signin_only or safety_only or rules_only or layouts_only or lock_only or features_only or computer_only or (adapter.turns==2 and report['task_status_before_cleanup']=='running')
        finally:
            finish.set();host.should_exit=True;worker.join(timeout=15);listener.close();site.shutdown();site.server_close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
    assert not report['errors'],report['errors']
    failures=[r for r in report['states'] if r['violations']]
    assert not failures,json.dumps(failures,indent=2)
    return report

if __name__=='__main__':
    import sys
    try:print(json.dumps(run(sys.argv[1],sys.argv[2]),indent=2))
    except Exception as error:
        # Playwright request call logs contain fixture cookies. Keep sanitized
        # failure type/message; owned role/geometry snapshots are in report.json.
        raise RuntimeError(type(error).__name__+': '+str(error).split('Call log:')[0].strip()) from None
