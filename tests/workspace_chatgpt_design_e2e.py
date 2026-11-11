"""Opt-in ChatGPT reference design proof on immutable UI bytes and a real scratch FastAPI host.

.venv/bin/python -B tests/workspace_chatgpt_design_e2e.py OUTPUT UI_DIST [--baseline]
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

REPO = Path(os.environ.get('TERMX_CHATGPT_DESIGN_REPO') or (str(Path(__file__).resolve().parents[1]) if Path(__file__).parent.name == 'tests' else str(Path.cwd())))
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

def persisted_draft(state,owner,session_id,text,page):
    wait(lambda:state.workspace.session(owner,session_id,turns=False)['draft_text']==text,page=page)


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
      info=e=>{const c=getComputedStyle(e);return {rect:rect(e),fontSize:c.fontSize,lineHeight:c.lineHeight,fontFamily:c.fontFamily,color:c.color,background:c.backgroundColor,border:c.borderColor,radius:c.borderRadius}},
      q=s=>document.querySelector(s);
      return {documentOverflow:document.documentElement.scrollWidth>innerWidth,viewport:{width:innerWidth,height:innerHeight},
        chat:rect(q('.chat-region')),composer:rect(q('.composer')),message:rect(q('textarea[aria-label="Message"]')),
        controls:[...document.querySelectorAll('.composer-controls button')].filter(visible).map(e=>({name:e.getAttribute('aria-label'),...rect(e),focusOutline:getComputedStyle(e).outline})),
        assistant:q('.message-assistant .message-response')?info(q('.message-assistant .message-response')):null,
        assistantContent:q('.message-assistant .message-content')?info(q('.message-assistant .message-content')):null,
        user:q('.message-user .message-content')?info(q('.message-user .message-content')):null,
        sidebar:rect(q('.sidebar')),pointerCoarse:matchMedia('(pointer: coarse)').matches,reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches};
    }''')

def audit(page, output, report, state, theme, width, axe, full=True):
    phase(report, 'capture', state=state, theme=theme, width=width)
    metrics=geometry(page)
    item={'state':state,'theme':theme,'width':width,**metrics}
    item['visible_alerts']=page.locator('[role=alert]').evaluate_all("rows=>rows.filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>e.textContent.trim())")
    item['visible_toasts']=page.locator('.error-toast').evaluate_all("rows=>rows.filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>e.textContent.trim())")
    if report['mode']=='after' and not state.startswith('capture-'):assert not item['visible_toasts'],item['visible_toasts']
    if state in ('preferences','models-settings'):
        item['settings_geometry']=page.get_by_role('dialog',name='Settings',exact=True).evaluate("e=>{const rect=n=>n?n.getBoundingClientRect().toJSON():null;return {sheet:rect(e),categories:rect(e.querySelector('.settings-categories')),content:rect(e.querySelector('.settings-content')),background:getComputedStyle(e).backgroundColor}}")
    if report['mode']=='after' and state=='preferences':
        heading=page.get_by_role('dialog',name='Settings',exact=True).get_by_role('heading',name='General',exact=True)
        size=heading.evaluate('e=>getComputedStyle(e).fontSize')
        item['general_heading_font_size']=size
        assert size==('24px' if width<640 else '28px'),size
        page.locator('.settings-surface .keybinding-row').first.wait_for(state='attached')
        rows=page.locator('.settings-surface .keybinding-row').evaluate_all("rows=>rows.map(e=>{const text=[...e.childNodes].find(n=>n.nodeType===3&&n.textContent.trim()),r=document.createRange();r.selectNodeContents(text);return {label:text.textContent.trim(),direction:getComputedStyle(e).flexDirection,text:r.getBoundingClientRect().toJSON(),control:e.querySelector('input').getBoundingClientRect().toJSON()}})")
        assert rows,'Keyboard shortcut rows must be present'
        item['keyboard_shortcut_geometry']=rows
        if width>=1280:
            for row in rows:
                assert row['direction']=='row' and row['control']['x']>=row['text']['right']+8,row
                assert abs((row['text']['y']+row['text']['height']/2)-(row['control']['y']+row['control']['height']/2))<=3,row
    if report['mode']=='after' and state=='projects':
        search=page.locator('.control-project-search').evaluate("e=>({direction:getComputedStyle(e).flexDirection,rect:e.getBoundingClientRect().toJSON(),icon:e.querySelector('svg').getBoundingClientRect().toJSON(),input:e.querySelector('input').getBoundingClientRect().toJSON()})")
        item['project_search_geometry']=search
        assert search['direction']=='row' and search['input']['x']>search['icon']['x'],search
        assert abs((search['icon']['y']+search['icon']['height']/2)-(search['input']['y']+search['input']['height']/2))<=2,search
    if axe and full:
        if not page.evaluate("typeof window.axe !== 'undefined'"): page.add_script_tag(path=str(axe))
        result=page.evaluate('''async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})''')
        item['axe_version']=page.evaluate('axe.version')
        item['violations']=[{'id':v['id'],'impact':v.get('impact'),'nodes':[{'target':n['target'],'summary':n.get('failureSummary')} for n in v['nodes']]} for v in result['violations']]
        item['incomplete']=[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['incomplete']]
    if report['mode']=='after' and state in ('conversation','configured-conversation'):
        assert metrics['assistant'] and metrics['assistant']['fontSize']==('16px' if width<640 else '15px') and metrics['assistant']['lineHeight']=='24px', metrics
        assert metrics['assistant']['rect']['width']<=737, metrics
        assert metrics['assistantContent']['background']=='rgba(0, 0, 0, 0)', metrics
        assert metrics['user']['background']!=metrics['assistantContent']['background'], metrics
        quiet={'completed_header_visible':page.locator('.conversation-surface-header').is_visible() if page.locator('.conversation-surface-header').count() else False,'context_meter_visible':page.locator('.task-meter').is_visible() if page.locator('.task-meter').count() else False}
        item['normal_chat_chrome']=quiet
        assert not any(quiet.values()),quiet
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
    if state in ('welcome','conversation','configured-conversation','narrow','short'):
        box=metrics['composer']; assert box and box['x']>=-1 and box['right']<=page.viewport_size['width']+1 and box['bottom']<=page.viewport_size['height']+1, item
        for button in metrics['controls']:
            assert button['x']>=-1 and button['right']<=page.viewport_size['width']+1 and button['bottom']<=page.viewport_size['height']+1, button
    if report['mode']=='after' and metrics['composer'] and metrics['composer']['width']>0 and state in ('welcome','conversation','configured-conversation','code-buffer','narrow','short'):
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

def choose_workspace(page, name):
    candidates=page.get_by_role('group',name='Workspace',exact=True).get_by_role('button',name=name,exact=True)
    if not candidates.count() or not candidates.first.is_visible():
        rail=page.get_by_role('navigation',name='Workspace navigation',exact=True)
        candidates=rail.get_by_role('button',name=name,exact=True)
    if not candidates.count() or not candidates.first.is_visible():
        label='Return to Agent' if name=='Agent' else 'Open Studio'
        candidates=page.get_by_role('button',name=label,exact=True)
    assert candidates.count() and candidates.first.is_visible(),'No accessible '+name+' workspace route'
    candidates.first.focus();page.keyboard.press('Enter')

def switch_studio(page):
    choose_workspace(page,'Studio')
    studio=page.get_by_role('region',name='Media studio',exact=True);expect(studio).to_be_visible(timeout=30000)
    expect(studio.get_by_role('button',name='Generate image',exact=True)).to_be_disabled()
    expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_be_disabled()
    return studio

def projects(page):
    target=page.get_by_role('button',name='Projects',exact=True)
    if not target.count() or not target.first.is_visible():
        target=page.get_by_role('button',name='Projects & operations',exact=True)
    if not target.count() or not target.first.is_visible():
        navigation=page.get_by_role('navigation',name='Workspace navigation',exact=True)
        reveal=navigation.get_by_role('button',name='Show sidebar',exact=True)
        if reveal.is_visible():reveal.click()
        target=page.get_by_role('button',name='Projects',exact=True)
        if not target.count() or not target.first.is_visible():target=page.get_by_role('button',name='Projects & operations',exact=True)
    assert target.count() and target.first.is_visible(),'Projects route is unavailable'
    target.first.focus();page.keyboard.press('Enter')
    views=page.get_by_role('navigation',name='Control plane views',exact=True)
    if views.count() and views.is_visible():views.get_by_role('button',name='Projects',exact=True).click()
    expect(page.get_by_role('heading',name='Projects',exact=True)).to_be_visible()


def project_journey(page,project,sessions,output,report,theme,width,axe):
    panel=page.get_by_role('region',name='Desktop control plane',exact=True)
    search=panel.get_by_role('searchbox',name='Search projects',exact=True)
    expect(search).to_be_visible();search.focus()
    search.fill('explicit-unmatched-synthetic-project')
    toggle=panel.get_by_role('button',name=project['name'],exact=True)
    expect(toggle).to_have_count(0)
    expect(panel.get_by_role('button',name='Clear search',exact=True)).to_be_visible()
    capture=(theme=='dark' and width==1920) or (theme=='light' and width==375)
    if capture:audit(page,output,report,'projects-no-results',theme,width,axe)
    panel.get_by_role('button',name='Clear search',exact=True).click();expect(search).to_have_value('')
    expect(toggle).to_have_attribute('aria-expanded','false')
    toggle.focus();page.keyboard.press('Enter');expect(toggle).to_have_attribute('aria-expanded','true')
    conversations=panel.get_by_role('list',name=project['name']+' conversations',exact=True)
    expect(conversations).to_be_visible()
    child=conversations.get_by_role('button',name='Open Design QA fixture · conversation',exact=True)
    expect(child).to_be_visible()
    if capture:audit(page,output,report,'projects-expanded',theme,width,axe)
    child.focus();page.keyboard.press('Enter')
    expect(page.get_by_role('heading',name='Local review fixture',exact=True)).to_be_visible()
    assert ('session='+sessions['conversation']['id'] in page.url) or (urlsplit(page.url).path=='/session/'+sessions['conversation']['id']),page.url
    report['checks'].append({'check':'projects-search-expand-open-chat','theme':theme,'width':width,'no_result_and_clear':True,'opened_exact_seeded_session':True})


def capture_status_checks(page,context,output,report,theme,width,axe,state,owner,session):
    message=page.get_by_role('textbox',name='Message',exact=True)
    persisted_draft(state,owner,session['id'],message.input_value(),page)
    local='Local synthetic draft retained during an explicit external-write fixture.'
    message.fill(local)
    current=state.workspace.session(owner,session['id'],turns=False)
    state.workspace.update_session(owner,session['id'],revision=current['revision'],changes={'draft_text':'External synthetic capture-conflict fixture'})
    toast=page.locator('.error-toast').filter(has_text='This draft changed in another window')
    expect(toast).to_be_visible();expect(message).to_have_value(local)
    assert state.workspace.session(owner,session['id'],turns=False)['draft_text']=='External synthetic capture-conflict fixture'
    report['checks'].append({'check':'expected-conflict-toast-local-draft-preserved','theme':theme,'width':width,'fixture':'Explicit scratch-store external write; guard must preserve local text and not overwrite external text.'})
    fixture={'mode':'control'}
    def status(route):
        assert route.request.method=='GET','Capture probe must never mutate the host'
        if fixture['mode']=='unavailable':route.fulfill(status=503,json={'detail':'Explicit synthetic capture-status unavailable fixture'});return
        row={'id':'synthetic-capture-only','kind':'computer' if fixture['mode']=='control' else 'window','state':fixture['mode'],'stop_allowed':True}
        if fixture['mode']=='private':row['expired']=True
        route.fulfill(json={'captures':[row]})
    context.route('**/api/desktop/recording/status',status)
    for mode in ('control','private','unavailable'):
        fixture['mode']=mode
        page.evaluate("window.dispatchEvent(new Event('termx-capture-changed'))")
        label='1 computer control' if mode=='control' else 'Private consent ended' if mode=='private' else 'Capture status unavailable'
        group=page.get_by_role('group',name='Active capture controls',exact=True)
        expect(group.get_by_text(label,exact=mode!='private')).to_be_visible()
        expect(page.locator('.status-bar')).to_be_visible()
        if mode!='unavailable':
            for button in (group.get_by_role('button',name='Open capture controls',exact=True),group.get_by_role('button',name='Stop capture',exact=True)):
                button.focus();expect(button).to_be_focused()
                assert button.evaluate("e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}"),'Capture target is covered by another element'
                box=button.bounding_box();vp=page.viewport_size
                assert box and box['height']>=24 and box['x']>=-1 and box['y']>=-1 and box['x']+box['width']<=vp['width']+1 and box['y']+box['height']<=vp['height']+1,box
        expect(toast).to_be_visible()
        audit(page,output,report,'capture-'+mode,theme,width,axe)
    report['checks'].append({'check':'capture-status-visibility-and-keyboard','theme':theme,'width':width,'fixture':'Explicit browser status-response fixtures; no real captures created or control operations clicked.','states':['control','private-expired','unavailable']})
    context.unroute('**/api/desktop/recording/status',status)


def make_tasks(state,owner,project,root):
    from termx.agent.limits import DEFAULT_LIMITS
    state.agent_store.put_provider('offline-design-fixture',kind='openai-compatible',name='Offline design fixture',base_url='http://127.0.0.1:1/v1',model='fixture-review,fixture-plan',capabilities=['chat'],secret_configured=False)
    state.agent.save_provider(provider_id='configured-ui-fixture',kind='openai-compatible',name='Synthetic configured UI provider',base_url='http://127.0.0.1:1/v1',model='fixture-configured',capabilities=['shell'],api_key='synthetic-ui-only-not-a-provider-key')
    sessions={}
    for label in ('welcome','conversation','error','approval','streaming','configured'):
        sessions[label]=state.workspace.create_session(owner,title='Design QA fixture · '+label,project_id=project['id'],cwd=str(root),provider_id='configured-ui-fixture' if label=='configured' else 'offline-design-fixture',model='fixture-configured' if label=='configured' else 'fixture-review',mode='agent')
    def task(label,prompt,result='',error=''):
        row=state.agent_store.create_task(prompt=prompt,cwd=str(root),provider_id=sessions[label]['provider_id'],model=sessions[label]['model'],limits=DEFAULT_LIMITS,mode='agent',status='completed')
        state.agent_store.update_task(row['id'],result=result,error=error or None)
        state.workspace.store.create('task',owner.id,{'conversation_id':sessions[label]['id'],'cwd':str(root),'execution_location':str(root),'parent_id':None},project['id'],row['id'])
        state.authorization.claim_principal(owner,'task',row['id'],project_id=project['id'])
        state.agent_store.add_conversation_turn(sessions[label]['id'],prompt=prompt,task_id=row['id'],mode='agent',provider_id=sessions[label]['provider_id'],model=sessions[label]['model'])
        return row
    task('configured','Synthetic configured-provider UI presentation; never submit or invoke a provider.',ANSWER)
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
        shell=page.evaluate("""()=>{const info=s=>{const e=document.querySelector(s),c=e?getComputedStyle(e):null;return e?{rect:e.getBoundingClientRect().toJSON(),background:c.backgroundColor,radius:c.borderRadius}:null};return {rail:info('.workspace-activity-rail'),sidebar:info('.sidebar'),canvas:info('.workspace-main'),composer:info('.composer')}}""")
        assert shell['rail'] and any(abs(shell['rail']['rect']['width']-value)<=1 for value in ((52,56) if width<640 else (52,))),shell
        if width>=1280:assert shell['sidebar'] and abs(shell['sidebar']['rect']['width']-240)<=1,shell
        if not page.locator('.capture-status').count():assert not page.locator('.status-bar').is_visible(),'Normal chat footer must recede without capture status'
        aliases=page.evaluate("()=>Object.fromEntries(['--background','--accent-soft','--secondary','--font-sans'].map(p=>[p,getComputedStyle(document.documentElement).getPropertyValue(p).trim().toLowerCase()]))")
        expected=dict(zip(('--background','--accent-soft','--secondary'),('#ffffff','#f0f0f0','#5e5e5e') if theme=='light' else ('#111111','#303030','#b4b4b4')))
        assert all(aliases[k]==v for k,v in expected.items()),aliases
        assert aliases['--font-sans'].startswith('-apple-system'),aliases
        report['checks'].append({'check':'neutral-reference-tokens-native-font','theme':theme,'width':width,'actual':aliases})
        report['checks'].append({'check':'reference-frame-proportions','theme':theme,'width':width,'actual':shell})
    if not baseline or (theme=='dark' and width==1920): audit(page,output,report,'welcome',theme,width,axe,not baseline)
    # Keyboard menu path and visible focus are measured without activating work.
    trigger=page.get_by_role('button',name='Workspace actions',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    menu=page.get_by_role('dialog',name='Workspace actions',exact=True)
    if menu.count():
        expect(menu).to_be_visible();assert menu.evaluate('e=>e.contains(document.activeElement)');page.keyboard.press('Tab');assert menu.evaluate('e=>e.contains(document.activeElement)')
    if not baseline or (theme=='dark' and width==1920):audit(page,output,report,'workspace-menu',theme,width,axe,not baseline)
    page.keyboard.press('Escape');expect(trigger).to_be_focused()
    report['checks'].append({'check':'workspace-menu-keyboard','theme':theme,'width':width,'outline':trigger.evaluate('e=>getComputedStyle(e).outline')})
    if not baseline or (theme=='dark' and width==1920):
        projects(page)
        audit(page,output,report,'projects',theme,width,axe,not baseline)
        if not baseline:project_journey(page,project,sessions,output,report,theme,width,axe)
    page.goto(target('conversation'));expect(page.get_by_role('heading',name='Local review fixture')).to_be_visible()
    log=page.get_by_role('log');log.evaluate('e=>e.scrollTop=0')
    if not baseline or (theme=='dark' and width==1920) or (theme=='light' and width==375): audit(page,output,report,'conversation',theme,width,axe,not baseline)
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
    persisted_draft(state,owner,sessions['conversation']['id'],draft,page)
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
    if not baseline or (theme=='dark' and width==1920): audit(page,output,report,'preferences',theme,width,axe,not baseline)
    theme_control=panel.get_by_role('combobox',name='Theme preference',exact=True)
    if not baseline:
        expect(theme_control).to_be_visible();theme_control.focus()
        expect(theme_control).to_be_focused()
        opposite='light' if theme=='dark' else 'dark'
        theme_control.select_option(opposite);expect(page.locator('html')).to_have_attribute('data-theme',opposite)
        theme_control.select_option(theme);expect(page.locator('html')).to_have_attribute('data-theme',theme)
        report['checks'].append({'check':'native-theme-selector','theme':theme,'width':width,'round_trip':True})
    category=panel.get_by_role('combobox',name='Settings category',exact=True)
    if category.count() and category.is_visible():
        category.focus();category.select_option('models');expect(category).to_have_value('models')
    else:
        nav=panel.get_by_role('navigation',name='Settings categories',exact=True)
        models=nav.get_by_role('link',name='Models & engines',exact=True)
        models.focus();page.keyboard.press('Enter');expect(models).to_have_attribute('aria-current','page')
    if not baseline:audit(page,output,report,'models-settings',theme,width,axe)
    close_panel(page,panel)
    expect(message).to_have_value(draft);expect(page.get_by_role('button',name='Remove design-qa-local.png',exact=True)).to_be_visible()
    editor=None
    if width>=1280 or not baseline:
        message.focus();page.keyboard.press('Control+2')
        row=page.locator('.file-row').filter(has_text='design-review.txt');expect(row).to_be_visible();row.click()
        editor=page.locator('.editor-host .cm-content[contenteditable=true]').first;expect(editor).to_be_visible()
        editor.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.type('def unsaved_design_fixture():\n    return "preserved"\n')
        expect(page.get_by_role('button',name='Save file',exact=True)).to_be_enabled()
        if not baseline or theme=='dark':audit(page,output,report,'code-buffer',theme,width,axe,not baseline)
    studio=switch_studio(page)
    prompt=studio.get_by_label('Prompt',exact=True);prompt.fill('Unsent synthetic Studio draft; never generate.')
    key=f'{theme}-{width}';report['studio_colors'][key]=studio_colors(page)
    expect(studio.get_by_role('button',name='Generate image',exact=True)).to_be_disabled()
    expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_be_disabled()
    choose_workspace(page,'Agent')
    if editor:
        expect(editor).to_contain_text('unsaved_design_fixture')
        assert Path(project['path'],'design-review.txt').read_text()=='# Synthetic local design QA fixture\nprint("original")\n'
        page.keyboard.press('Control+1')
    expect(message).to_have_value(draft)
    expect(page.get_by_role('button',name='Remove design-qa-local.png',exact=True)).to_be_visible()
    choose_workspace(page,'Studio');expect(prompt).to_have_value('Unsent synthetic Studio draft; never generate.')
    choose_workspace(page,'Agent')
    report['checks'].append({'check':'draft-attachment-editor-studio-preservation','theme':theme,'width':width,'editor_checked':bool(editor),'billing_gate_unchanged':True,'generation_requests':0})
    if not baseline and ((theme=='dark' and width==1920) or (theme=='light' and width in (1280,375))):
        page.goto(target('configured'));expect(page.get_by_role('heading',name='Local review fixture',exact=True)).to_be_visible()
        configured_message=page.get_by_role('textbox',name='Message',exact=True)
        configured_message.fill('Synthetic configured UI check; never send.')
        expect(page.get_by_role('button',name='Send message',exact=True)).to_be_enabled()
        persisted_draft(state,owner,sessions['configured']['id'],configured_message.input_value(),page)
        configured_message.fill('');persisted_draft(state,owner,sessions['configured']['id'],'',page)
        expect(page.get_by_text('Connect this chat',exact=True)).not_to_be_visible()
        page.get_by_role('log').evaluate('e=>e.scrollTop=0')
        audit(page,output,report,'configured-conversation',theme,width,axe)
        report['checks'].append({'check':'configured-provider-ui-only','theme':theme,'width':width,'dummy_credential_memory_only':True,'send_enabled_for_draft':True,'inference_dispatches':0})
    if not baseline and ((theme=='dark' and width==1920) or (theme=='light' and width==375)):
        for label in ('error','approval','streaming'):
            page.goto(target(label));expect(message).to_be_visible()
            if label=='error': expect(page.get_by_role('alert').filter(has_text='Synthetic offline error')).to_be_visible()
            if label=='approval':
                review=page.get_by_role('button',name='Review action',exact=True);expect(review).to_be_visible();review.focus();page.keyboard.press('Enter')
                review_dialog=page.get_by_role('dialog',name='Review action',exact=True);expect(review_dialog).to_be_visible();panel_keyboard(page,review_dialog)
                expect(review_dialog.get_by_role('button',name='Allow once',exact=True)).to_be_enabled()
                close_panel(page,review_dialog,review)
            if label=='streaming':
                expect(page.locator('.message-response.is-streaming')).to_contain_text('This is a synthetic streaming fixture.')
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
            persisted_draft(state,owner,sessions['conversation']['id'],message.input_value(),page)
            audit(page,output,report,label,theme,w,axe)
        page.set_viewport_size({'width':width,'height':1080 if width==1920 else 900 if width==1280 else 812})
        capture_status_checks(page,context,output,report,theme,width,axe,state,owner,sessions['conversation'])

def preview(browser,output,report):
    wanted={'chat':'after-configured-conversation-dark-1920.png','settings':'after-preferences-dark-1920.png','phone':'after-configured-conversation-light-375.png'}
    if not all((output/name).is_file()for name in wanted.values()):return
    cards={key:'<figure class="'+key+'"><figcaption>'+name+'</figcaption><img src="data:image/png;base64,'+base64.b64encode((output/name).read_bytes()).decode()+'"></figure>' for key,name in wanted.items()}
    html='<!doctype html><html lang="en"><meta charset="utf-8"><title>ChatGPT reference browser proof</title><style>body{margin:0;background:#111;color:#f4f4f4;font:14px system-ui;padding:24px}h1{font-size:20px;font-weight:600;margin:0 0 12px}p{line-height:1.6}main{display:grid;grid-template-columns:1280px 375px;grid-template-areas:"chat phone" "settings notes";gap:24px}figure{margin:0;min-width:0}figcaption{font-size:14px;margin-bottom:8px}.chat{grid-area:chat}.settings{grid-area:settings}.phone{grid-area:phone}img{display:block;width:100%;height:auto;object-fit:contain;object-position:top}aside{grid-area:notes;line-height:1.6;color:#b4b4b4}</style><h1>ChatGPT reference · actual browser captures</h1><p>Synthetic isolated QA data; desktop chat uses a configured dummy provider in memory only. No model or provider calls. Desktop captures shown at 1280px wide; phone capture at actual 375px.</p><main>'+cards['chat']+cards['phone']+cards['settings']+'<aside><p>These are unedited browser captures embedded in an actual browser DOM preview.</p><p>Open individual original PNGs for the full 1920 × 1080 desktop images. The full report retains layout geometry, keyboard results, accessibility incomplete checks, and exact Studio color comparison.</p></aside></main></html>'
    (output/'preview.html').write_text(html);page=browser.new_page(viewport={'width':1727,'height':1800});page.set_content(html);page.screenshot(path=str(output/'preview.png'),full_page=True);page.close()
    report['preview']={'html':'preview.html','screenshot':'preview.png','method':'Actual browser DOM screenshot embedding original browser captures; desktop width1280 and phone width375, no image editing.','originals':wanted}


def run(destination,ui_dist,baseline=False):
    output=Path(destination).resolve();output.mkdir(parents=True,exist_ok=False)
    report={'result':'running','mode':'before' if baseline else 'after','output':str(output),'states':[],'checks':[],'errors':[],'external_browser_requests':[],'backend_requests':[],'browser_console':[],'http_errors':[],'failed_browser_requests':[],'provider_mutations':[],'studio_colors':{},'started_at_utc':datetime.now(timezone.utc).isoformat(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'limitations':['Real isolated password auth, FastAPI routes and persisted stores with synthetic QA messages; no external inference.','Automated full-document axe results retain incomplete items; screenshots and browser geometry do not prove screen reader or physical touch behavior.','Native packaging, physical devices, OS keyboard and actual model streaming are outside this browser proof.']}
    root=Path(tempfile.mkdtemp(prefix='termx-chatgpt-design-'));server=worker=listener=client=None
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
            for width,height,theme in ((1920,1080,'dark'),(1920,1080,'light'),(1280,900,'dark'),(1280,900,'light'),(375,812,'dark'),(375,812,'light')):
                phase(report,'starting-case',theme=theme,width=width)
                context=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce',has_touch=width<640)
                context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
                origin=urlsplit(base)
                def network(route):
                    request=route.request;url=urlsplit(request.url)
                    if url.scheme in ('http','https') and (url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_browser_requests'].append(request.url);route.abort('blockedbyclient');return
                    if request.method!='GET' and re.match(r'/api/(?:agent/tasks|workspace/tasks|media/studio/jobs|desktop/recording)(?:/|$)',url.path):report['provider_mutations'].append({'method':request.method,'path':url.path});route.abort('blockedbyclient');return
                    route.continue_()
                context.route('**/*',network);page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)));page.on('console',lambda msg:report['browser_console'].append({'type':msg.type,'text':msg.text,'location':msg.location}) if msg.type in ('warning','error') else None);page.on('requestfailed',lambda request:report['failed_browser_requests'].append({'method':request.method,'url':request.url,'failure':request.failure}));page.on('response',lambda response:report['http_errors'].append({'method':response.request.method,'path':urlsplit(response.url).path,'status':response.status}) if response.status>=400 else None)
                try:exercise(page,context,base,sessions,project,state,owner,output,report,theme,width,axe if axe.is_file() else None,baseline)
                except BaseException:
                    page.screenshot(path=str(output/'failure.png'),full_page=True);page.locator('input[type=password]').evaluate_all('r=>r.forEach(e=>e.value="")');(output/'failure.html').write_text(page.content());raise
                finally:context.close()
            preview(browser,output,report);browser.close()
        assert not report['errors'] and not report['external_browser_requests'] and not report['provider_mutations'], report
        assert len(state.agent_store.list_tasks())==5,'Unexpected task dispatch'
        assert not [r for r in report['backend_requests'] if r['method']!='GET'],report['backend_requests']
        report['result']='passed'
        baseline_report=REPO/'docs/evidence/chatgpt-ui/baseline/report.json'
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
    if len(sys.argv) not in (3,4):raise SystemExit('Usage: workspace_chatgpt_design_e2e.py OUTPUT UI_DIST [--baseline]')
    r=run(sys.argv[1],sys.argv[2],len(sys.argv)==4 and sys.argv[3]=='--baseline')
    print(json.dumps({'result':r['result'],'states':len(r['states']),'errors':r['errors'],'axe_violation_states':r.get('axe_violation_states'),'studio_color_comparison':r.get('studio_color_comparison')},indent=2))
