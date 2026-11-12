"""Opt-in Agent DESIGN proof on immutable UI bytes and a real scratch FastAPI host.

.venv/bin/python -B tests/workspace_agent_design_e2e.py OUTPUT UI_DIST [--baseline]
No real profile, provider, inference, terminal command, native window or paid call.
All conversations and visible messages explicitly identify themselves as fixtures.
"""
from __future__ import annotations
import asyncio, base64, hashlib, io, json, os, re, shutil, socket, sys, tempfile, threading
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit
import httpx, uvicorn
from PIL import Image
from playwright.sync_api import expect, sync_playwright

REPO = Path(os.environ.get('TERMX_AGENT_DESIGN_REPO') or (str(Path(__file__).resolve().parents[1]) if Path(__file__).parent.name == 'tests' else str(Path.cwd())))
sys.path.insert(0, str(REPO / 'tests'))
from workspace_ui_snapshot import snapshot_ui
ANSWER = '''## Local review fixture

This isolated conversation checks that the work stays easy to read. The assistant uses ordinary prose, while the request above stays visually distinct.

- Keep the next action close to the draft.
- Open activity only when the details are useful.
- Preserve unsaved work when changing views.

The local file `design-review.txt` is available for the editor preservation check.

```python
def preserve_draft(draft):
    return draft.strip()
```

This is synthetic QA content. No model generated this answer and no external service was called.
'''
CONSENT = 'I approve provider charges for this generation.'

def phase(report, value, **details):
    print(json.dumps({'phase': value, **details}), flush=True)
    (Path(report['output']) / 'progress.json').write_text(json.dumps({'phase': value, **details})+'\n')

def wait(check, page=None, seconds=20):
    end = monotonic()+seconds
    while not check():
        if monotonic()>end: raise AssertionError('Timed out waiting for fixture state')
        if page: page.wait_for_timeout(50)
        else: sleep(.05)

def settings(page):
    trigger = page.get_by_role('button', name='Workspace actions', exact=True)
    trigger.focus(); page.keyboard.press('Enter')
    page.get_by_role('button', name='Appearance', exact=True).click()
    panel = page.get_by_role('dialog', name='Settings', exact=True)
    expect(panel).to_be_visible()
    return trigger, panel

def close_panel(page, panel, trigger=None):
    page.keyboard.press('Escape'); expect(panel).not_to_be_visible()
    if trigger and trigger.is_visible(): expect(trigger).to_be_focused()

def panel_keyboard(page, panel):
    assert panel.evaluate('e=>e.contains(document.activeElement)'), 'Dialog did not receive focus'
    for key in ('Tab','Shift+Tab','Tab','Tab','Shift+Tab'):
        page.keyboard.press(key)
        assert panel.evaluate('e=>e.contains(document.activeElement)'), 'Dialog keyboard focus escaped'
    box=panel.bounding_box(); vp=page.viewport_size
    assert box and box['x']>=-1 and box['y']>=-1 and box['x']+box['width']<=vp['width']+1 and box['y']+box['height']<=vp['height']+1, box

def geometry(page):
    return page.evaluate('''()=>{
      const visible=e=>!!(e&&e.getBoundingClientRect().width&&e.getBoundingClientRect().height),
      rect=e=>e?e.getBoundingClientRect().toJSON():null,
      info=e=>{const c=getComputedStyle(e);return {rect:rect(e),fontSize:c.fontSize,lineHeight:c.lineHeight,color:c.color,background:c.backgroundColor,border:c.borderColor,radius:c.borderRadius}},
      q=s=>document.querySelector(s);
      return {documentOverflow:document.documentElement.scrollWidth>innerWidth,viewport:{width:innerWidth,height:innerHeight},
        chat:rect(q('.chat-region')),composer:rect(q('.composer')),message:rect(q('textarea[aria-label="Message"]')),
        controls:[...document.querySelectorAll('.composer-controls button')].filter(visible).map(e=>({name:e.getAttribute('aria-label'),...rect(e),focusOutline:getComputedStyle(e).outline})),
        assistant:q('.message-assistant .message-response')?info(q('.message-assistant .message-response')):null,
        assistantContent:q('.message-assistant .message-content')?info(q('.message-assistant .message-content')):null,
        user:q('.message-user .message-content')?info(q('.message-user .message-content')):null,
        sidebar:rect(q('.workspace-sidebar')),pointerCoarse:matchMedia('(pointer: coarse)').matches,reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches};
    }''')

def audit(page, output, report, state, theme, width, axe, full=True):
    phase(report, 'capture', state=state, theme=theme, width=width)
    metrics=geometry(page)
    item={'state':state,'theme':theme,'width':width,**metrics}
    if axe and full:
        if not page.evaluate("typeof window.axe !== 'undefined'"): page.add_script_tag(path=str(axe))
        result=page.evaluate('''async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})''')
        item['axe_version']=page.evaluate('axe.version')
        item['violations']=[{'id':v['id'],'impact':v.get('impact'),'nodes':[{'target':n['target'],'summary':n.get('failureSummary')} for n in v['nodes']]} for v in result['violations']]
        item['incomplete']=[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['incomplete']]
    if report['mode']=='after' and state=='conversation':
        assert metrics['assistant'] and metrics['assistant']['fontSize']=='16px' and metrics['assistant']['lineHeight']=='24px', metrics
        assert metrics['assistant']['rect']['width']<=721, metrics
        assert metrics['assistantContent']['background']=='rgba(0, 0, 0, 0)', metrics
        assert metrics['user']['background']!=metrics['assistantContent']['background'], metrics
    if state=='code-buffer':
        item['workbench_controls']=page.locator('.workbench .explorer-tools,.workbench .editor-tab-actions,.workspace-header').evaluate_all("rows=>rows.map(e=>({class:e.className,rect:e.getBoundingClientRect().toJSON(),buttons:[...e.querySelectorAll('button')].map(b=>({label:b.getAttribute('aria-label')||b.textContent,rect:b.getBoundingClientRect().toJSON(),scrollWidth:b.scrollWidth,clientWidth:b.clientWidth}))}))")
        for row in item['workbench_controls']:
            for button in row['buttons']:
                box=button['rect']
                if box['width'] and box['height']:
                    assert box['x']>=-1 and box['right']<=page.viewport_size['width']+1,button
                    assert button['scrollWidth']<=button['clientWidth']+1,button
    if report['mode']=='after' and state=='streaming':
        caret=page.locator('.message-response.is-streaming').evaluate("e=>{const s=getComputedStyle(e.lastElementChild,'::after');return {content:s.content,animation:s.animationName,width:s.width,height:s.height}}")
        item['streaming_caret']=caret
        assert caret['animation']=='none' and caret['width']=='4px' and caret['height']=='16px', caret
    name=f'{report["mode"]}-{state}-{theme}-{width}.png'
    page.screenshot(path=str(output/name),full_page=True)
    item['screenshot']=name;report['states'].append(item)
    assert not metrics['documentOverflow'], item
    if state in ('welcome','conversation','narrow','short'):
        box=metrics['composer']; assert box and box['x']>=-1 and box['right']<=page.viewport_size['width']+1 and box['bottom']<=page.viewport_size['height']+1, item
        for button in metrics['controls']:
            assert button['x']>=-1 and button['right']<=page.viewport_size['width']+1 and button['bottom']<=page.viewport_size['height']+1, button
    if report['mode']=='after' and metrics['composer'] and metrics['composer']['width']>0 and state in ('welcome','conversation','code-buffer','narrow','short'):
        assert metrics['composer']['bottom']<=metrics['chat']['bottom']+1,item
        for button in metrics['controls']:
            assert button['y']>=metrics['composer']['y']-1 and button['bottom']<=metrics['composer']['bottom']+1,button
            if metrics['pointerCoarse']:assert button['height']>=44,button
    return item

def studio_colors(page):
    return page.get_by_role('region',name='Media studio',exact=True).evaluate('''e=>{
      const properties=['color','backgroundColor','borderTopColor','borderRightColor','borderBottomColor','borderLeftColor'];
      const selectors=[':scope','.studio-stage','.studio-composer','.studio-composer textarea','.studio-composer button','.studio-dock-tools','.studio-dock-modes'];
      return Object.fromEntries(selectors.map(s=>{const n=s===':scope'?e:e.querySelector(s);return [s,n?Object.fromEntries(properties.map(p=>[p,getComputedStyle(n)[p]])):null]}));
    }''')

def switch_studio(page):
    picker=page.get_by_role('group',name='Workspace',exact=True)
    picker.get_by_role('button',name='Studio',exact=True).click()
    studio=page.get_by_role('region',name='Media studio',exact=True);expect(studio).to_be_visible(timeout=30000)
    expect(studio.get_by_role('button',name='Generate image',exact=True)).to_be_disabled()
    expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_be_disabled()
    return picker,studio

def make_tasks(state,owner,project,root):
    from termx.agent.limits import DEFAULT_LIMITS
    state.agent_store.put_provider('offline-design-fixture',kind='openai-compatible',name='Offline design fixture',base_url='http://127.0.0.1:1/v1',model='fixture-review,fixture-plan',capabilities=['chat'],secret_configured=False)
    sessions={}
    for label in ('welcome','conversation','error','approval','streaming'):
        sessions[label]=state.workspace.create_session(owner,title='Design QA fixture · '+label,project_id=project['id'],cwd=str(root),provider_id='offline-design-fixture',model='fixture-review',mode='agent')
    def task(label,prompt,result='',error=''):
        row=state.agent_store.create_task(prompt=prompt,cwd=str(root),provider_id='offline-design-fixture',model='fixture-review',limits=DEFAULT_LIMITS,mode='agent',status='completed')
        state.agent_store.update_task(row['id'],result=result,error=error or None)
        state.workspace.store.create('task',owner.id,{'conversation_id':sessions[label]['id'],'cwd':str(root),'execution_location':str(root),'parent_id':None},project['id'],row['id'])
        state.authorization.claim_principal(owner,'task',row['id'],project_id=project['id'])
        state.agent_store.add_conversation_turn(sessions[label]['id'],prompt=prompt,task_id=row['id'],mode='agent',provider_id='offline-design-fixture',model='fixture-review')
        return row
    completed=task('conversation','Synthetic QA request: review the workspace layout and preserve my local work.',ANSWER)
    call={'call_id':'design-read-fixture','name':'read_file','arguments':{'path':'design-review.txt'}}
    state.agent_store.append_event(completed['id'],'tool.started',{'call':call})
    state.agent_store.append_event(completed['id'],'tool.finished',{'call_id':call['call_id'],'result':{'ok':True,'output':'Synthetic fixture: reviewed design-review.txt locally.'}})
    state.agent_store.append_event(completed['id'],'task.completed',{'result':ANSWER})
    failure=task('error','Synthetic QA request: show a recoverable tool error.','', 'Synthetic offline error: the local review command could not finish. Review the task before retrying.')
    approval=task('approval','Synthetic QA request: hold a local operation for review.')
    streaming=task('streaming','Synthetic QA request: show an in-place response.','This is a synthetic streaming fixture. The response remains in the reading column while it is arriving.')
    return sessions,{'completed':completed,'failure':failure,'approval':approval,'streaming':streaming}

def exercise(page,context,base,sessions,project,state,owner,output,report,theme,width,axe,baseline):
    page.set_default_timeout(20000)
    target=lambda label:base+'/?session='+sessions[label]['id']+'&layout=chat'
    page.goto(target('welcome'))
    page.get_by_label('Username',exact=True).fill('design-fixture')
    page.get_by_label('Password',exact=True).fill('design-fixture-password-123')
    page.get_by_role('button',name='Continue with password',exact=True).click()
    message=page.get_by_role('textbox',name='Message',exact=True);expect(message).to_be_visible(timeout=60000)
    if width<640:
        hide=page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Hide sidebar',exact=True)
        if hide.count() and hide.is_visible():hide.click()
    if not baseline and width<640:
        navigation=page.get_by_role('navigation',name='Workspace navigation',exact=True)
        reveal=navigation.get_by_role('button',name='Show sidebar',exact=True)
        reveal.focus();page.keyboard.press('Enter')
        expect(page.get_by_role('complementary',name='Chats and agents',exact=True)).to_be_visible()
        expect(navigation.get_by_role('button',name='Hide sidebar',exact=True)).to_have_attribute('aria-expanded','true')
        page.keyboard.press('Escape');expect(reveal).to_be_focused()
        expect(page.get_by_role('complementary',name='Chats and agents',exact=True)).not_to_be_visible()
        report['checks'].append({'check':'sidebar-keyboard-escape-return','theme':theme,'width':width})
    if not baseline:
        aliases=page.evaluate("()=>Object.fromEntries(['--background','--accent-soft','--secondary'].map(p=>[p,getComputedStyle(document.documentElement).getPropertyValue(p).trim().toLowerCase()]))")
        expected_aliases=dict(zip(('--background','--accent-soft','--secondary'),('#faf9f7','#f3f1ec','#5e5b54') if theme=='light' else ('#1a1917','#2c2b28','#b5b1a8')))
        assert aliases==expected_aliases,aliases
        report['checks'].append({'check':'agent-theme-aliases','theme':theme,'width':width,'actual':aliases})
    if not baseline or (theme=='dark' and width==1280): audit(page,output,report,'welcome',theme,width,axe,not baseline)
    # Keyboard menu path and visible focus are measured without activating work.
    trigger=page.get_by_role('button',name='Workspace actions',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    menu=page.get_by_role('dialog',name='Workspace actions',exact=True)
    if menu.count():
        expect(menu).to_be_visible();assert menu.evaluate('e=>e.contains(document.activeElement)');page.keyboard.press('Tab');assert menu.evaluate('e=>e.contains(document.activeElement)')
    page.keyboard.press('Escape');expect(trigger).to_be_focused()
    report['checks'].append({'check':'workspace-menu-keyboard','theme':theme,'width':width,'outline':trigger.evaluate('e=>getComputedStyle(e).outline')})
    page.goto(target('conversation'));expect(page.get_by_role('heading',name='Local review fixture')).to_be_visible()
    log=page.get_by_role('log');log.evaluate('e=>e.scrollTop=0')
    if not baseline or (theme=='dark' and width==1280) or (theme=='light' and width==375): audit(page,output,report,'conversation',theme,width,axe,not baseline)
    activity=page.get_by_role('button',name='View activity',exact=False)
    expect(activity).to_have_attribute('data-state','closed')
    activity.focus();page.keyboard.press('Enter')
    tool=page.get_by_role('button',name=re.compile('Read file.*design-review.txt'))
    expect(tool).to_be_visible();expect(tool).to_have_attribute('data-state','closed')
    tool.focus();page.keyboard.press('Enter')
    expect(page.get_by_label('Read file · design-review.txt result')).to_be_visible()
    activity.click()
    report['checks'].append({'check':'collapsed-activity-and-tool-detail-keyboard','theme':theme,'width':width})
    draft=f'Unsent QA draft for {theme} {width}; preserve this text.'
    message.fill(draft)
    pixels=io.BytesIO();Image.new('RGB',(48,32),(176,80,45)).save(pixels,'PNG')
    page.locator('.composer input[type=file]').set_input_files({'name':'design-qa-local.png','mimeType':'image/png','buffer':pixels.getvalue()})
    expect(page.get_by_role('button',name='Remove design-qa-local.png',exact=True)).to_be_visible()
    options=page.get_by_role('button',name='Session options',exact=True)
    options.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Session settings',exact=True);expect(dialog).to_be_visible();panel_keyboard(page,dialog)
    if not baseline: audit(page,output,report,'session-settings',theme,width,axe)
    close_panel(page,dialog,options)
    model=page.get_by_role('button',name='Model and effort',exact=True);model.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Model and effort',exact=True);expect(dialog).to_be_visible();panel_keyboard(page,dialog)
    # This real persisted next-turn selection never starts a task.
    dialog.get_by_label('Session model',exact=True).select_option('fixture-plan')
    expect(dialog.get_by_label('Session model',exact=True)).to_have_value('fixture-plan')
    close_panel(page,dialog,model)
    root_trigger,panel=settings(page);panel_keyboard(page,panel)
    if not baseline or (theme=='dark' and width==1280): audit(page,output,report,'preferences',theme,width,axe,not baseline)
    nav=panel.get_by_role('navigation',name='Settings categories',exact=True)
    models=nav.get_by_role('link',name='Models & engines',exact=True)
    models.focus();page.keyboard.press('Enter')
    expect(models).to_have_attribute('aria-current','page')
    if not baseline:audit(page,output,report,'models-settings',theme,width,axe)
    close_panel(page,panel)
    expect(message).to_have_value(draft);expect(page.get_by_role('button',name='Remove design-qa-local.png',exact=True)).to_be_visible()
    editor=None
    if width==1280 or not baseline:
        message.focus();page.keyboard.press('Control+2')
        row=page.locator('.file-row').filter(has_text='design-review.txt');expect(row).to_be_visible();row.click()
        editor=page.locator('.editor-host .cm-content[contenteditable=true]').first;expect(editor).to_be_visible()
        editor.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.type('def unsaved_design_fixture():\n    return "preserved"\n')
        expect(page.get_by_role('button',name='Save file',exact=True)).to_be_enabled()
        if not baseline or theme=='dark':audit(page,output,report,'code-buffer',theme,width,axe,not baseline)
    picker,studio=switch_studio(page)
    prompt=studio.get_by_label('Prompt',exact=True);prompt.fill('Unsent synthetic Studio draft; never generate.')
    key=f'{theme}-{width}';report['studio_colors'][key]=studio_colors(page)
    expect(studio.get_by_role('button',name='Generate image',exact=True)).to_be_disabled()
    expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_be_disabled()
    picker.get_by_role('button',name='Agent',exact=True).click()
    if editor:
        expect(editor).to_contain_text('unsaved_design_fixture')
        assert Path(project['path'],'design-review.txt').read_text()=='# Synthetic local design QA fixture\nprint("original")\n'
        page.keyboard.press('Control+1')
    expect(message).to_have_value(draft)
    expect(page.get_by_role('button',name='Remove design-qa-local.png',exact=True)).to_be_visible()
    picker.get_by_role('button',name='Studio',exact=True).click();expect(prompt).to_have_value('Unsent synthetic Studio draft; never generate.')
    picker.get_by_role('button',name='Agent',exact=True).click()
    report['checks'].append({'check':'draft-attachment-editor-studio-preservation','theme':theme,'width':width,'editor_checked':bool(editor),'billing_gate_unchanged':True,'generation_requests':0})
    if not baseline and ((theme=='dark' and width==1280) or (theme=='light' and width==375)):
        for label in ('error','approval','streaming'):
            page.goto(target(label));expect(message).to_be_visible()
            if label=='error': expect(page.get_by_role('alert').filter(has_text='Synthetic offline error')).to_be_visible()
            if label=='approval':
                review=page.get_by_role('button',name='Review action',exact=True);expect(review).to_be_visible();review.focus();page.keyboard.press('Enter')
                review_dialog=page.get_by_role('dialog',name='Review action',exact=True);expect(review_dialog).to_be_visible();panel_keyboard(page,review_dialog)
                expect(review_dialog.get_by_role('button',name='Allow once',exact=True)).to_be_enabled()
                close_panel(page,review_dialog,review)
            if label=='streaming':
                expect(page.get_by_text('This is a synthetic streaming fixture.',exact=False)).to_be_visible()
                stream=page.locator('.message-response.is-streaming')
                page.emulate_media(reduced_motion='no-preference')
                first=stream.evaluate("e=>{const s=getComputedStyle(e.lastElementChild,'::after');return {animation:s.animationName,opacity:s.opacity}}")
                page.wait_for_timeout(750)
                second=stream.evaluate("e=>getComputedStyle(e.lastElementChild,'::after').opacity")
                assert first['animation']=='response-caret',first
                report['checks'].append({'check':'streaming-caret-motion','theme':theme,'width':width,'animation':first['animation'],'opacity_samples':[first['opacity'],second],'normal_motion_sampled':True})
                page.emulate_media(reduced_motion='reduce')
            audit(page,output,report,label,theme,width,axe)
        page.goto(target('conversation'));expect(message).to_be_visible()
        for w,h,label in ((320,812,'narrow'),(375,420,'short')):
            page.set_viewport_size({'width':w,'height':h});message.fill('Synthetic narrow layout fixture; never send.')
            audit(page,output,report,label,theme,w,axe)
        page.set_viewport_size({'width':width,'height':900 if width==1280 else 812})

def preview(browser,output,report):
    names=[s['screenshot'] for s in report['states'] if (s['state'],s['theme'],s['width']) in [('conversation','dark',1280),('conversation','light',375),('preferences','light',375),('code-buffer','light',1280)]]
    if not names:return
    cards=''.join('<figure><figcaption>'+n+'</figcaption><img src="data:image/png;base64,'+base64.b64encode((output/n).read_bytes()).decode()+'"></figure>' for n in names)
    html='<!doctype html><html lang="en"><meta charset="utf-8"><title>Agent design browser proof</title><style>body{margin:0;background:#1a1917;color:#f2f0eb;font:14px system-ui;padding:24px}h1{font-size:20px;font-weight:600}main{display:grid;grid-template-columns:2fr 1fr 1fr 2fr;gap:16px}figure{margin:0;min-width:0}figcaption{font-size:12px;margin-bottom:8px}img{width:100%;height:540px;object-fit:contain;object-position:top}</style><h1>Agent design · actual browser captures</h1><p>Synthetic isolated QA data. No model or provider calls.</p><main>'+cards+'</main></html>'
    (output/'preview.html').write_text(html);page=browser.new_page(viewport={'width':1600,'height':700});page.set_content(html);page.screenshot(path=str(output/'preview.png'),full_page=True);page.close()
    report['preview']={'html':'preview.html','screenshot':'preview.png','method':'Actual browser DOM screenshot embedding original browser captures; no image editing.'}

def run(destination,ui_dist,baseline=False):
    output=Path(destination).resolve();output.mkdir(parents=True,exist_ok=False)
    report={'result':'running','mode':'before' if baseline else 'after','output':str(output),'states':[],'checks':[],'errors':[],'external_browser_requests':[],'backend_requests':[],'browser_console':[],'http_errors':[],'failed_browser_requests':[],'provider_mutations':[],'studio_colors':{},'started_at_utc':datetime.now(timezone.utc).isoformat(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'limitations':['Real isolated password auth, FastAPI routes and persisted stores with synthetic QA messages; no external inference.','Automated full-document axe results retain incomplete items; screenshots and browser geometry do not prove screen reader or physical touch behavior.','Native packaging, physical devices, OS keyboard and actual model streaming are outside this browser proof.']}
    root=Path(tempfile.mkdtemp(prefix='termx-agent-design-'));server=worker=listener=client=None
    envs=('TERMX_CONFIG_DIR','TERMX_AGENTS_DIR','TERMX_ENGINE_STARTUP_REFRESH');previous={n:os.environ.get(n) for n in envs}
    try:
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        class NoInference:
            async def test(self,*args,**kwargs):raise AssertionError('Unexpected provider test')
            async def plan(self,*args,**kwargs):raise AssertionError('Unexpected inference')
            async def turn(self,*args,**kwargs):raise AssertionError('Unexpected inference')
        state=AppState(passcode=None,credentials=CredentialStore({}),adapter_factory=lambda *_:NoInference())
        owner=state.identity.setup_owner('design-fixture','design-fixture-password-123')
        project_root=root/'project';project_root.mkdir();(project_root/'design-review.txt').write_text('# Synthetic local design QA fixture\nprint("original")\n')
        project=state.projects.register(str(project_root),name='Synthetic design QA project')
        app=create_app(state,web_dir=snapshot_ui(root,Path(ui_dist)))
        sessions,tasks=make_tasks(state,owner,project,project_root)
        state.workspace.orchestrator.ensure(owner,session_id=sessions['welcome']['id'])
        async def no_catalog_network(_):return {}
        state.agent.catalogue._fetch=no_catalog_network
        def provider(request):
            report['backend_requests'].append({'method':request.method,'path':request.url.path})
            if request.method=='GET' and request.url.path in ('/api/v1/models','/api/v1/images/models','/api/v1/videos/models'):return httpx.Response(503,json={'error':'Synthetic offline discovery fixture'})
            raise AssertionError('Unexpected provider dispatch')
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider));state.studio.openrouter.client=client;state.studio.client=client
        manifest=json.loads((root/'fixture-ui-snapshot.json').read_text());report['ui_dist']=str(Path(ui_dist).resolve());report['ui_manifest_entries']=len(manifest);(output/'ui-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();base='http://127.0.0.1:'+str(listener.getsockname()[1])
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on',access_log=False));worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start();wait(lambda:server.started,seconds=30)
        state.agent_store.update_task(tasks['failure']['id'],status='failed')
        state.agent_store.update_task(tasks['approval']['id'],status='awaiting_approval')
        approval=state.agent_store.create_approval(tasks['approval']['id'],'tool',{'reason':'Synthetic QA operation: read design-review.txt from the scratch project. No execution will be approved.','call':{'call_id':'qa-hold','name':'read_file','arguments':{'path':'design-review.txt'}}})
        state.agent_store.append_event(tasks['approval']['id'],'approval.requested',{'approval':approval})
        state.agent_store.update_task(tasks['streaming']['id'],status='running')
        state.agent_store.append_event(tasks['streaming']['id'],'assistant.delta',{'text':'This is a synthetic streaming fixture. The response remains in the reading column while it is arriving.'})
        axe=REPO/'desktop/workspace/node_modules/axe-core/axe.min.js'
        with sync_playwright() as playwright:
            executable=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or next((str(p) for p in (Path(playwright.chromium.executable_path),Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) if p.is_file()),None)
            browser=playwright.chromium.launch(headless=True,executable_path=executable);report['browser']={'version':browser.version,'executable':executable}
            for width,height,theme in ((1280,900,'dark'),(1280,900,'light'),(375,812,'dark'),(375,812,'light')):
                phase(report,'starting-case',theme=theme,width=width)
                context=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce',has_touch=width<640)
                context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
                origin=urlsplit(base)
                def network(route):
                    request=route.request;url=urlsplit(request.url)
                    if url.scheme in ('http','https') and (url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_browser_requests'].append(request.url);route.abort('blockedbyclient');return
                    if request.method!='GET' and re.match(r'/api/(?:agent/tasks|workspace/tasks|media/studio/jobs)(?:/|$)',url.path):report['provider_mutations'].append({'method':request.method,'path':url.path});route.abort('blockedbyclient');return
                    route.continue_()
                context.route('**/*',network);page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)));page.on('console',lambda msg:report['browser_console'].append({'type':msg.type,'text':msg.text,'location':msg.location}) if msg.type in ('warning','error') else None);page.on('requestfailed',lambda request:report['failed_browser_requests'].append({'method':request.method,'url':request.url,'failure':request.failure}));page.on('response',lambda response:report['http_errors'].append({'method':response.request.method,'path':urlsplit(response.url).path,'status':response.status}) if response.status>=400 else None)
                try:exercise(page,context,base,sessions,project,state,owner,output,report,theme,width,axe if axe.is_file() else None,baseline)
                except BaseException:
                    page.screenshot(path=str(output/'failure.png'),full_page=True);page.locator('input[type=password]').evaluate_all('r=>r.forEach(e=>e.value="")');(output/'failure.html').write_text(page.content());raise
                finally:context.close()
            preview(browser,output,report);browser.close()
        assert not report['errors'] and not report['external_browser_requests'] and not report['provider_mutations'], report
        assert len(state.agent_store.list_tasks())==4,'Unexpected task dispatch'
        assert not [r for r in report['backend_requests'] if r['method']!='GET'],report['backend_requests']
        report['result']='passed'
        baseline_report=REPO/'docs/design-refs/agent-baseline-report.json'
        if not baseline and baseline_report.is_file():
            previous_colors=json.loads(baseline_report.read_text())['studio_colors']
            report['studio_color_comparison']={'unchanged':previous_colors==report['studio_colors'],'baseline':str(baseline_report)}
            assert previous_colors==report['studio_colors'], 'Studio computed colors changed'
        if not baseline:
            report['axe_violation_states']=[{'state':s['state'],'theme':s['theme'],'width':s['width'],'ids':[v['id'] for v in s.get('violations',[])]} for s in report['states'] if s.get('violations')]
            assert not report['axe_violation_states'], report['axe_violation_states']
    except BaseException as error:
        report.update(result='failed',failure=f'{type(error).__name__}: {error}');raise
    finally:
        if server:server.should_exit=True
        if worker:worker.join(timeout=15)
        if listener:listener.close()
        if client:asyncio.run(client.aclose())
        shutil.rmtree(root,ignore_errors=True)
        for n,v in previous.items():
            if v is None:os.environ.pop(n,None)
            else:os.environ[n]=v
        report['finished_at_utc']=datetime.now(timezone.utc).isoformat();(output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    if len(sys.argv) not in (3,4):raise SystemExit('Usage: workspace_agent_design_e2e.py OUTPUT UI_DIST [--baseline]')
    r=run(sys.argv[1],sys.argv[2],len(sys.argv)==4 and sys.argv[3]=='--baseline')
    print(json.dumps({'result':r['result'],'states':len(r['states']),'errors':r['errors'],'axe_violation_states':r.get('axe_violation_states'),'studio_color_comparison':r.get('studio_color_comparison')},indent=2))
