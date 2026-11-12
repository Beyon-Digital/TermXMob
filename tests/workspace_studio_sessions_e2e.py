"""Opt-in Studio, session lifecycle and content design proof on immutable UI bytes and a real scratch FastAPI host.

.venv/bin/python -B tests/workspace_studio_sessions_e2e.py OUTPUT UI_DIST [--baseline]
No real profile, provider, inference, terminal command, native window or paid call.
All conversations and visible messages explicitly identify themselves as fixtures.
"""
from __future__ import annotations
import asyncio, base64, hashlib, io, json, os, re, shutil, socket, sys, tempfile, threading
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep, time
from urllib.parse import urlsplit
import httpx, uvicorn
from PIL import Image
from playwright.sync_api import expect, sync_playwright

REPO = Path(os.environ.get('TERMX_STUDIO_SESSIONS_REPO') or (str(Path(__file__).resolve().parents[1]) if Path(__file__).parent.name == 'tests' else str(Path.cwd())))
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
    # Static contrast/geometry captures represent settled screens; motion is sampled separately.
    page.wait_for_function("""()=>[...document.querySelectorAll('dialog[open]:not([data-state=closed])')].every(e=>e.getAnimations().every(a=>a.playState!=='running'||a.effect?.getTiming().iterations===Infinity))""",timeout=3000)
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
    if state.startswith('studio-'):
        region=page.get_by_role('region',name='Media studio',exact=True)
        if region.count()and region.is_visible():
            item['studio_geometry']=region.evaluate("e=>({rect:e.getBoundingClientRect().toJSON(),overflow:e.scrollWidth>e.clientWidth+1,composer:e.querySelector('.studio-composer')?.getBoundingClientRect().toJSON()})")
            if item['studio_geometry']['overflow']:
                item['studio_geometry']['overflowing']=region.evaluate("e=>[...e.querySelectorAll('*')].map(n=>({tag:n.tagName,classes:String(n.className),text:n.textContent?.trim().slice(0,70),rect:n.getBoundingClientRect().toJSON(),clientWidth:n.clientWidth,scrollWidth:n.scrollWidth})).filter(n=>n.rect.width&&n.rect.height&&(n.rect.right>e.getBoundingClientRect().right+1||n.scrollWidth>n.clientWidth+1)).sort((a,b)=>b.rect.right-a.rect.right).slice(0,30)")
                report.setdefault('layout_failures',[]).append({'state':state,'theme':theme,'width':width,**item['studio_geometry']})
            assert not item['studio_geometry']['overflow'],item['studio_geometry']
    if state.startswith('music-'):
        item['music_geometry']=page.locator('.music-studio[aria-label="Music studio"]').evaluate('e=>({rect:e.getBoundingClientRect().toJSON(),scrollWidth:e.scrollWidth,clientWidth:e.clientWidth,composer:e.querySelector(".music-composer").getBoundingClientRect().toJSON(),create:e.querySelector(".music-create").getBoundingClientRect().toJSON()})')
        assert item['music_geometry']['scrollWidth']<=item['music_geometry']['clientWidth']+1,item['music_geometry']
        details=page.locator('.music-studio').evaluate("""e=>{const m=e.querySelector('[aria-label="Music model"]'),c=getComputedStyle(m),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');ctx.font=c.font;const text=m.options[m.selectedIndex]?.textContent||'';return {model:{text,rect:m.getBoundingClientRect().toJSON(),textWidth:ctx.measureText(text).width,usable:m.clientWidth-parseFloat(c.paddingLeft)-parseFloat(c.paddingRight)-20,font:c.font},targets:[...e.querySelectorAll('button,input[type=range]')].filter(n=>n.getBoundingClientRect().width&&n.getBoundingClientRect().height).map(n=>({name:n.getAttribute('aria-label')||n.textContent.trim(),rect:n.getBoundingClientRect().toJSON()}))}}""")
        item['music_geometry'].update(details)
        if width<=375:assert details['model']['usable']>=details['model']['textWidth']-1,details['model']
        if metrics['pointerCoarse']:
            for target in details['targets']:assert target['rect']['height']>=44 and target['rect']['width']>=44,target
    if report['mode']=='after' and state in ('conversation','configured-conversation'):
        assert metrics['assistant'] and metrics['assistant']['fontSize']==('16px' if metrics['chat']['width']<=640 else '15px') and metrics['assistant']['lineHeight']=='24px', metrics
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
    if _baseline:expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_be_disabled()
    else:expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_have_count(0)
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


def studio_section(page,name):
    nav=page.get_by_role('navigation',name='Studio views',exact=True)
    if not nav.is_visible():
        page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Show sidebar',exact=True).click()
    nav.get_by_role('button',name=name,exact=True).click()

def studio_tool(page,name):
    studio_section(page,'Create')
    studio=page.get_by_role('region',name='Media studio',exact=True)
    studio.get_by_role('button',name='Tools',exact=True).click()
    panel=page.get_by_role('dialog',name='Studio tools',exact=True)
    expect(panel).to_be_visible()
    panel.get_by_role('button',name=re.compile('^'+re.escape(name)+r'\b')).click()
    return studio

def music_unconfigured_checks(page,context,base,project,output,report,theme,width,axe):
    original=page.get_by_role('textbox',name='Message',exact=True).input_value()
    raw=context.request.get(base+'/api/media/studio/catalog?project_id='+project['id']);assert raw.ok
    catalog=raw.json()
    assert any(row.get('kind')=='audio'and'music'in row.get('modes',[])for row in catalog['models']),'Actual host must expose supported music metadata before credentials'
    assert not any(row.get('adapter')=='elevenlabs'and row.get('configured')for row in catalog['accounts'])
    choose_workspace(page,'Studio');studio_section(page,'Music')
    music=page.locator('.music-studio[aria-label="Music studio"]');expect(music).to_be_visible()
    form=music.get_by_role('form',name='Music generation',exact=True)
    expect(form.get_by_role('button',name='Create',exact=True)).to_be_disabled()
    expect(form.get_by_role('button',name='Configure music provider',exact=True)).to_be_visible()
    audit(page,output,report,'music-unconfigured',theme,width,axe)
    advanced=form.get_by_role('tab',name='Advanced',exact=True);expect(advanced).to_be_enabled();advanced.click()
    form.get_by_role('textbox',name='Lyrics',exact=True).fill('[Verse]\nSynthetic draft before provider setup')
    form.get_by_role('textbox',name='Music styles',exact=True).fill('Synthetic soft acoustic direction')
    expect(form.get_by_role('button',name='Create',exact=True)).to_be_disabled()
    expect(page.get_by_role('dialog',name='Review music generation',exact=True)).not_to_be_visible()
    audit(page,output,report,'music-unconfigured-advanced',theme,width,axe)
    studio_section(page,'Create');choose_workspace(page,'Agent')
    expect(page.get_by_role('textbox',name='Message',exact=True)).to_have_value(original)
    report['checks'].append({'check':'music-real-first-launch-before-credentials','theme':theme,'width':width,'actual_host_catalog':True,'configured_music_accounts':0,'simple_and_advanced_explorable':True,'create_disabled':True,'catalogue_fixtures':False,'paid_requests':0})

def studio_design_checks(page,context,base,sessions,project,state,owner,output,report,theme,width,axe):
    original=page.get_by_role('textbox',name='Message',exact=True).input_value()
    choose_workspace(page,'Studio')
    studio=page.get_by_role('region',name='Media studio',exact=True);expect(studio).to_be_visible()
    projects_nav=page.get_by_role('navigation',name='Studio projects',exact=True)
    if not projects_nav.is_visible():page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Show sidebar',exact=True).click()
    projects_nav.get_by_role('button',name=project['name'],exact=True).click()
    raw=context.request.get(base+'/api/media/studio/catalog?project_id='+project['id']);assert raw.ok
    catalog={**raw.json(),'accounts':[
      {'id':'qa-openrouter','name':'Synthetic Studio account','adapter':'openrouter','configured':True,'capabilities':['image','video']},
      {'id':'qa-kie','name':'Synthetic lip sync account','adapter':'kie','configured':True,'capabilities':['video']},
      {'id':'qa-elevenlabs','name':'Synthetic music account','adapter':'elevenlabs','configured':True,'capabilities':['audio']}],
      'models':[
      {'id':'qa/still','label':'Synthetic still model','kind':'image','adapter':'openrouter','modes':['image','cinema'],'description':'Explicit offline UI fixture','aspect_ratios':['1:1','16:9'],'resolutions':['1K','2K'],'durations':[],'supports_image':True,'inline_inputs':['image']},
      {'id':'qa/video','label':'Synthetic video model','kind':'video','adapter':'openrouter','modes':['video'],'description':'Explicit offline UI fixture','aspect_ratios':['16:9'],'resolutions':['720p'],'durations':[5,10],'supports_image':True,'inline_inputs':['image']},
      {'id':'qa/lipsync','label':'Synthetic lip sync model','kind':'video','adapter':'kie','modes':['lipsync'],'description':'Explicit offline UI fixture','aspect_ratios':['16:9'],'resolutions':['720p'],'durations':[],'requires_image':True,'requires_audio':True,'supports_image':True,'inline_inputs':['image']},
      {'id':'elevenlabs/music_v2_5','label':'Eleven Music 2.5','kind':'audio','adapter':'elevenlabs','modes':['music'],'description':'Explicit offline music fixture','aspect_ratios':[],'resolutions':[],'durations':[15,30,60,120],'duration_min':3,'duration_max':600,'custom_duration_max':120,'supported_music_modes':['simple','custom'],'supports_custom_lyrics':True,'supports_instrumental':True,'supports_negative_styles':True}], 'catalog_source':'snapshot'}
    job={'id':'qa-finished-image','project_id':project['id'],'provider_id':'qa-openrouter','model':'qa/still','kind':'image','prompt':'Explicit synthetic gallery image; no generation occurred.','status':'completed','created':time(),'updated':time(),'outputs':[state._qa['studio_image_url']]}
    fixture_jobs=[]
    settings_response=context.request.get(base+'/api/media/studio/settings');assert settings_response.ok
    settings_fixture={**settings_response.json(),'catalog':catalog}
    def route_studio(route):
        request=route.request;url=urlsplit(request.url)
        assert request.method=='GET','No Studio generation or batch authorization may reach the host'
        if url.path.endswith('/catalog'):route.fulfill(json=catalog)
        elif url.path.endswith('/jobs'):route.fulfill(json=fixture_jobs)
        elif url.path.endswith('/history'):route.fulfill(json={'items':fixture_jobs,'next_cursor':None})
        elif url.path.endswith('/settings'):route.fulfill(json=settings_fixture)
        else:route.fallback()
    context.route('**/api/media/studio/*',route_studio)
    page.reload();expect(studio).to_be_visible(timeout=30000)
    projects_nav=page.get_by_role('navigation',name='Studio projects',exact=True)
    if not projects_nav.is_visible():page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Show sidebar',exact=True).click()
    projects_nav.get_by_role('button',name=project['name'],exact=True).click()
    studio_section(page,'Create')
    prompt=studio.locator('textarea.studio-prompt')
    expect(prompt).to_be_visible();prompt.fill('Synthetic isolated Studio draft; never authorize generation.')
    expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_have_count(0)
    generate=studio.get_by_role('button',name='Generate image',exact=True)
    expect(generate).to_be_enabled()
    audit(page,output,report,'studio-image',theme,width,axe)
    normal_motion=theme=='dark'and width==1920
    if normal_motion:page.emulate_media(reduced_motion='no-preference')
    generate.focus();page.keyboard.press('Enter')
    review=page.get_by_role('dialog',name='Review generation',exact=True);expect(review).to_be_visible();panel_keyboard(page,review)
    expect(review).to_contain_text('Synthetic isolated Studio draft; never authorize generation.')
    expect(review.get_by_role('button',name='Generate image with Synthetic still model',exact=True)).to_be_enabled()
    audit(page,output,report,'studio-generation-review',theme,width,axe)
    review.get_by_role('button',name='Keep editing',exact=True).click()
    native=page.locator('dialog.studio-generation-review')
    if native.count():
        assert native.get_attribute('inert')is not None and native.get_attribute('aria-hidden')=='true'
    expect(review).not_to_be_visible()
    expect(generate).to_be_focused()
    page.emulate_media(reduced_motion='reduce')
    expect(prompt).to_have_value('Synthetic isolated Studio draft; never authorize generation.')
    # Cancel before a workspace switch; open-review hide/lock fencing is covered by the component suite.
    generate.click();expect(review).to_be_visible();page.keyboard.press('Escape');expect(review).not_to_be_visible();choose_workspace(page,'Agent')
    choose_workspace(page,'Studio');expect(review).not_to_be_visible();expect(prompt).to_have_value('Synthetic isolated Studio draft; never authorize generation.')
    studio.get_by_role('tab',name='Video',exact=True).click()
    prompt.fill('Synthetic isolated video draft; never authorize generation.')
    expect(studio.get_by_role('button',name='Generate video',exact=True)).to_be_enabled()
    audit(page,output,report,'studio-video',theme,width,axe)
    special=(theme=='dark' and width==1920)or(theme=='light' and width==375)
    if special:
        studio_tool(page,'Cinema')
        if page.get_by_role('dialog').count():page.keyboard.press('Escape')
        prompt.fill('Synthetic cinematic still draft; no provider call.')
        expect(studio.get_by_role('button',name='Generate still image',exact=True)).to_be_enabled()
        audit(page,output,report,'studio-cinema',theme,width,axe)
        studio_tool(page,'Lip sync')
        refs=page.get_by_role('dialog',name='Reference media',exact=True);expect(refs).to_be_visible()
        expect(refs.get_by_label('Image URL',exact=True)).to_have_attribute('required','')
        expect(refs.get_by_label('Audio URL',exact=True)).to_have_attribute('required','')
        page.keyboard.press('Escape')
        studio.get_by_role('button',name='Generate lip sync',exact=True).click()
        expect(page.get_by_role('dialog',name='Review generation',exact=True)).not_to_be_visible()
        expect(page.get_by_role('dialog',name='Reference media',exact=True).get_by_role('alert')).to_be_visible()
        audit(page,output,report,'studio-lipsync-required',theme,width,axe)
        page.keyboard.press('Escape')
        studio_tool(page,'Content factory')
        factory=studio.get_by_role('region',name='Content factory',exact=True);expect(factory).to_be_visible()
        factory.get_by_label('What are we making?',exact=True).fill('Explicit synthetic QA brief for three local scene drafts.')
        factory.get_by_role('button',name='Build scene drafts',exact=True).click()
        expect(factory.get_by_role('checkbox')).to_have_count(0)
        factory.get_by_role('button',name='Review 3 scenes',exact=True).click()
        batch=page.get_by_role('dialog',name='Review scene batch',exact=True);expect(batch).to_be_visible();panel_keyboard(page,batch)
        expect(batch.get_by_role('button',name='Queue 3 scenes with Synthetic video model',exact=True)).to_be_enabled()
        audit(page,output,report,'studio-batch-review',theme,width,axe)
        batch.get_by_role('button',name='Cancel batch',exact=True).click();expect(batch).not_to_be_visible()
    fixture_jobs.append(job)
    studio_section(page,'Library')
    studio.get_by_role('group',name='Filter library',exact=True).get_by_role('button',name='All',exact=True).click()
    view=studio.get_by_role('button',name='View generation qa-finished-image',exact=True);expect(view).to_be_visible();view.click()
    gallery=page.get_by_role('dialog',name='Generation preview',exact=True);expect(gallery).to_be_visible();panel_keyboard(page,gallery)
    expect(gallery.locator('img').first).to_be_visible()
    wait(lambda:gallery.locator('img').first.evaluate('e=>e.naturalWidth>0'),page=page)
    audit(page,output,report,'studio-gallery-preview',theme,width,axe)
    page.keyboard.press('Escape');expect(gallery).not_to_be_visible()
    # These actions stage a draft and cannot create a job.
    studio.get_by_role('button',name='Regenerate',exact=True).first.click()
    expect(prompt).to_have_value(job['prompt'])
    studio_section(page,'Library')
    animate=studio.get_by_role('button',name='Animate',exact=True)
    if animate.count() and animate.first.is_visible():
        animate.first.click();expect(studio.get_by_role('tab',name='Video',exact=True)).to_have_attribute('aria-selected','true')
    settings_trigger=page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Settings',exact=True)
    if not settings_trigger.is_visible():page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Show sidebar',exact=True).click()
    settings_trigger.click();settings_surface=page.get_by_role('region',name='Studio settings',exact=True);expect(settings_surface).to_be_visible()
    settings_surface.get_by_role('tab',name='Providers',exact=True).click()
    audit(page,output,report,'studio-settings',theme,width,axe)
    settings_surface.get_by_role('button',name='Add provider',exact=True).click()
    provider_editor=settings_surface.get_by_role('form',name='Add Studio provider',exact=True)
    provider_editor.get_by_role('combobox',name='Provider',exact=True).select_option('elevenlabs')
    expect(provider_editor.get_by_role('checkbox',name='Allow music generation',exact=True)).to_be_checked()
    expect(provider_editor.get_by_role('checkbox',name='Allow image generation',exact=True)).to_have_count(0)
    audit(page,output,report,'studio-music-provider-option',theme,width,axe)
    provider_editor.get_by_role('button',name='Cancel',exact=True).click();expect(provider_editor).not_to_be_visible()
    for title in ('Models & defaults','Generation limits'):
        tab=settings_surface.get_by_role('tab',name=title,exact=True);tab.focus();page.keyboard.press('Enter');expect(tab).to_have_attribute('aria-selected','true')
    settings_surface.get_by_role('tab',name='Models & defaults',exact=True).click()
    music_provider=settings_surface.get_by_role('combobox',name='Music default provider',exact=True)
    music_provider.select_option('qa-elevenlabs')
    expect(settings_surface.get_by_role('combobox',name='Music default model',exact=True)).to_have_value('elevenlabs/music_v2_5')
    duration=settings_surface.get_by_role('spinbutton',name='Music duration',exact=True)
    expect(duration).to_have_attribute('min','3');expect(duration).to_have_attribute('max','600');duration.fill('30')
    audit(page,output,report,'studio-music-settings-default',theme,width,axe)
    music_provider.select_option('')
    settings_surface.get_by_role('tab',name='Generation limits',exact=True).click()
    limit=settings_surface.get_by_role('checkbox',name='Limit music duration',exact=True);limit.check()
    cap=settings_surface.get_by_role('spinbutton',name='Music duration limit',exact=True);cap.fill('120')
    audit(page,output,report,'studio-music-settings-cap',theme,width,axe)
    limit.uncheck();expect(settings_surface.get_by_role('button',name='Save Studio settings',exact=True)).to_be_disabled()
    report['checks'].append({'check':'music-provider-option-default-and-local-cap-drafts','theme':theme,'width':width,'elevenlabs_option':True,'audio_capability':True,'music_default_and_duration':True,'duration_limit_control':True,'settings_and_provider_saves':0})
    studio_section(page,'Create')
    if special:
        for w,h,label in ((320,812,'studio-narrow'),(375,420,'studio-short')):
            page.set_viewport_size({'width':w,'height':h})
            audit(page,output,report,label,theme,w,axe)
            charge=studio.locator('.studio-charge-note');charge.scroll_into_view_if_needed()
            generate_button=studio.locator('.studio-generate');expect(generate_button).to_be_visible()
            controls=studio.evaluate("e=>{const g=e.querySelector('.studio-generate'),n=e.querySelector('.studio-charge-note'),r=g.getBoundingClientRect(),p=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {generate:r.toJSON(),charge:n.getBoundingClientRect().toJSON(),hit:g===p||g.contains(p)}}")
            assert controls['generate']['bottom']<=controls['charge']['top']+1 and controls['hit'],controls
            audit(page,output,report,label+'-controls',theme,w,axe)
            report['checks'].append({'check':'narrow-studio-controls-reachable-and-charge-separated','theme':theme,'width':w,**controls})
        page.set_viewport_size({'width':width,'height':1080 if width==1920 else 812})
    report['checks'].append({'check':'studio-reviewed-billing-and-staged-actions','theme':theme,'width':width,'review_cancelled':True,'cancelled_review_did_not_resume_after_workspace_switch':True,'checkboxes_removed':True,'provider_jobs':0,'fixture':'Explicit offline catalogue and completed gallery receipt; owned image bytes served by actual scratch host.'})
    music_design_checks(page,context,base,project,state,owner,fixture_jobs,output,report,theme,width,axe)
    choose_workspace(page,'Agent')
    expect(page.get_by_role('textbox',name='Message',exact=True)).to_have_value(original)
    context.unroute('**/api/media/studio/*',route_studio)

def music_design_checks(page,context,base,project,state,owner,fixture_jobs,output,report,theme,width,axe):
    studio_section(page,'Music')
    music=page.locator('.music-studio[aria-label="Music studio"]')
    expect(music).to_be_visible()
    form=music.get_by_role('form',name='Music generation',exact=True)
    simple=form.get_by_role('tab',name='Simple',exact=True)
    expect(simple).to_have_attribute('aria-selected','true')
    form.get_by_role('textbox',name='Song description',exact=True).fill('Synthetic QA song description: calm piano and warm strings; never submit to a provider.')
    instrumental=form.get_by_role('button',name=re.compile('^Instrumental'))
    instrumental.click();expect(instrumental).to_have_attribute('aria-pressed','true')
    create=form.get_by_role('button',name='Create',exact=True);expect(create).to_be_enabled()
    expect(form.get_by_role('checkbox')).to_have_count(0)
    audit(page,output,report,'music-simple',theme,width,axe)
    create.focus();page.keyboard.press('Enter')
    review=page.get_by_role('dialog',name='Review music generation',exact=True)
    expect(review).to_be_visible();panel_keyboard(page,review)
    expect(review).to_contain_text('Synthetic QA song description')
    expect(review).to_contain_text('Instrumental')
    final=review.get_by_role('button',name='Create music with Eleven Music 2.5',exact=True);expect(final).to_be_enabled()
    audit(page,output,report,'music-simple-review',theme,width,axe)
    review.get_by_role('button',name='Keep editing',exact=True).click();expect(review).not_to_be_visible();expect(create).to_be_focused()
    simple.focus();page.keyboard.press('ArrowRight')
    advanced=form.get_by_role('tab',name='Advanced',exact=True);expect(advanced).to_be_focused();expect(advanced).to_have_attribute('aria-selected','true')
    lyrics='[Verse]\nSynthetic lines for a silent QA fixture\nNo provider created this song\n\n[Chorus]\nKeep the original work safe'
    form.get_by_role('textbox',name='Lyrics',exact=True).fill(lyrics)
    form.get_by_role('textbox',name='Music styles',exact=True).fill('Synthetic acoustic piano, slow tempo, warm strings')
    form.get_by_role('textbox',name='Song title',exact=True).fill('Synthetic QA draft')
    more=form.get_by_role('button',name='More options',exact=True);more.click()
    form.get_by_role('spinbutton',name='Music duration',exact=True).fill('30')
    form.get_by_role('textbox',name='Exclude music styles',exact=True).fill('Synthetic exclusion: heavy distortion')
    more.click();music.locator('.music-compose-scroll').evaluate('e=>e.scrollTop=0')
    audit(page,output,report,'music-advanced',theme,width,axe)
    create.click();expect(review).to_be_visible();panel_keyboard(page,review)
    expect(review).to_contain_text(lyrics);expect(review).to_contain_text('Synthetic QA draft');expect(review).to_contain_text('30 seconds');expect(review).to_contain_text('Synthetic exclusion: heavy distortion')
    expect(final).to_be_enabled()
    audit(page,output,report,'music-advanced-review',theme,width,axe)
    page.keyboard.press('Escape');expect(review).not_to_be_visible()
    special=(theme=='dark'and width==1920)or(theme=='light'and width==375)
    if special:
        form.get_by_role('button',name='Expand lyrics editor',exact=True).click()
        editor=page.get_by_role('dialog',name='Lyrics editor',exact=True);expect(editor).to_be_visible();panel_keyboard(page,editor)
        expect(editor.get_by_role('textbox',name='Full-screen lyrics',exact=True)).to_have_value(lyrics)
        audit(page,output,report,'music-lyrics-editor',theme,width,axe)
        editor.get_by_role('button',name='Done',exact=True).click();expect(editor).not_to_be_visible()
    song={'id':'qa-finished-song','request_id':'qa-completed-synthetic-song','project_id':project['id'],'provider_id':'qa-elevenlabs','model':'elevenlabs/music_v2_5','kind':'audio','prompt':'Explicit synthetic song receipt; no provider generated this silent audio.','status':'completed','created':time(),'updated':time(),'parameters':{'operation':'music','music_mode':'custom','duration':12,'title':'Synthetic silent fixture','lyrics':lyrics,'styles':'Synthetic acoustic piano fixture','negative_styles':'','instrumental':False},'outputs':[state._qa['studio_audio_url']]}
    fixture_jobs.append(song)
    music.get_by_role('button',name='Refresh My songs',exact=True).click()
    library=music.get_by_role('region',name='My songs',exact=True)
    row=library.get_by_role('article',name='Synthetic silent fixture · Completed',exact=True);expect(row).to_be_visible()
    search=library.get_by_role('searchbox',name='Search My songs',exact=True)
    search.fill('no-synthetic-song-matches');expect(library.get_by_role('heading',name='No matching songs',exact=True)).to_be_visible()
    if special:audit(page,output,report,'music-search-empty',theme,width,axe)
    search.fill('');expect(row).to_be_visible()
    library.get_by_role('combobox',name='Sort My songs',exact=True).select_option('title')
    row.get_by_role('button',name='Use prompt',exact=True).click()
    expect(form.get_by_role('textbox',name='Song title',exact=True)).to_have_value('Synthetic silent fixture')
    expect(review).not_to_be_visible()
    if special:
        row.get_by_role('button',name='Play Synthetic silent fixture',exact=True).click()
        player=music.get_by_role('region',name='Music player',exact=True);expect(player).to_be_visible()
        audio=player.locator('audio');wait(lambda:audio.evaluate('e=>Number.isFinite(e.duration)&&e.duration>0'),page=page)
        wait(lambda:audio.evaluate('e=>e.currentTime>0.02'),page=page)
        player.get_by_role('button',name='Pause music',exact=True).click();assert audio.evaluate('e=>e.paused')
        seek=player.get_by_role('slider',name='Playback position',exact=True);seek.focus();page.keyboard.press('Home');page.keyboard.press('ArrowRight');page.keyboard.press('ArrowRight')
        volume=player.get_by_role('slider',name='Music volume',exact=True)
        volume_exercised=bool(volume.count()and volume.is_visible())
        if volume_exercised:
            volume.focus();page.keyboard.press('Home');assert audio.evaluate('e=>e.volume')==0
        else:assert width<1100,'A desktop Music volume control is missing'
        blob=audio.get_attribute('src');assert blob.startswith('blob:')
        audit(page,output,report,'music-player',theme,width,axe)
        choose_workspace(page,'Agent');expect(player).to_have_count(0)
        assert not page.evaluate('async url=>{try{await fetch(url);return true}catch{return false}}',blob),'Music player blob retained after workspace hide'
        choose_workspace(page,'Studio');expect(music).to_be_visible()
        expect(form.get_by_role('textbox',name='Song title',exact=True)).to_have_value('Synthetic silent fixture')
        for w,h,label in((320,812,'music-narrow'),(375,420,'music-short')):
            page.set_viewport_size({'width':w,'height':h});music.evaluate('e=>e.scrollTop=0');audit(page,output,report,label,theme,w,axe)
        page.set_viewport_size({'width':width,'height':1080 if width==1920 else 812})
    report['checks'].append({'check':'music-independent-drafts-review-library','theme':theme,'width':width,'simple_and_custom_exact_review_cancelled':True,'final_authorization_uninvoked':True,'lyrics_editor_checked':special,'search_sort_reuse':True,'silent_owned_audio_player_play_pause_seek_and_hide_revoke':special,'volume_control_exercised':special and width>=1100,'paid_requests':0,'fixture':'Explicit offline ElevenLabs catalogue and completed receipt; a real owned 12-second silent WAV is served by scratch auth routes.'})

def prepare_extra_fixtures(state,owner,project,sessions,tasks,report):
    import subprocess,wave,struct
    from termx.agent.limits import DEFAULT_LIMITS
    root=Path(project['path']).parent
    repo=root/'lifecycle-repo';repo.mkdir()
    def git(*args):return subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True).stdout
    git('init','-b','main');git('config','user.name','Synthetic QA');git('config','user.email','qa@example.invalid')
    (repo/'base.txt').write_text('Committed synthetic lifecycle fixture\n');git('add','base.txt');git('commit','-m','Synthetic lifecycle fixture')
    lifecycle=state.projects.register(str(repo),name='Synthetic lifecycle Git project')
    state.agent.save_provider(provider_id='lifecycle-ui-fixture',kind='openai-compatible',name='Synthetic lifecycle dispatch stub',base_url='http://127.0.0.1:1/v1',model='fixture-lifecycle',capabilities=['shell'],api_key='synthetic-lifecycle-memory-only')
    qa=state._qa;qa.update(lifecycle_project=lifecycle,git_head=git('rev-parse','HEAD').strip(),children=[],mutable_statuses=[],stub_calls=[])
    silent=io.BytesIO()
    with wave.open(silent,'wb')as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(struct.pack('<h',0)*(8000*12))
    receipt=state.artifacts.create(owner.id,project['id'],'audio','Synthetic owned silent Music fixture',blob=silent.getvalue(),mime='audio/wav')
    qa['studio_audio_url']='/api/media/artifacts/'+receipt['id']+'/export?format=original'
    def seed(session,prompt,result,provider='configured-ui-fixture'):
        task=state.agent_store.create_task(prompt=prompt,cwd=session['cwd'],provider_id=provider,model='fixture-configured',limits=DEFAULT_LIMITS,mode='ask',status='completed')
        state.agent_store.update_task(task['id'],result=result)
        state.workspace.store.create('task',owner.id,{'conversation_id':session['id'],'cwd':session['cwd'],'execution_location':session['cwd'],'parent_id':None},project['id'],task['id'])
        state.authorization.claim_principal(owner,'task',task['id'],project_id=project['id'])
        state.agent_store.add_conversation_turn(session['id'],prompt=prompt,task_id=task['id'],mode='ask',provider_id=provider,model='fixture-configured')
        state.agent_store.append_event(task['id'],'task.completed',{'result':result})
        return task
    typed=state.workspace.create_session(owner,title='Synthetic typed content fixture',project_id=project['id'],cwd=project['path'],provider_id='configured-ui-fixture',model='fixture-configured',mode='ask')
    result='''## Synthetic typed content

These files and links are local QA fixtures. No provider generated this content.

[Reference documentation](https://example.invalid/docs)

![Remote image stays a link](https://example.invalid/never-fetch.png)

[Unsafe navigation](javascript:alert(1))
'''
    typed_task=seed(typed,'Show synthetic typed media, files, links and tool results.',result)
    image=io.BytesIO();Image.new('RGB',(240,136),(115,120,125)).save(image,'PNG')
    video=root/'typed-video.mp4'
    subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-loglevel','error','-f','lavfi','-i','color=c=gray:s=160x90:r=10','-t','0.6','-c:v','libx264','-pix_fmt','yuv420p','-an','-movflags','+faststart',str(video)],check=True,capture_output=True)
    audio=io.BytesIO()
    with wave.open(audio,'wb')as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000);wav.writeframes(struct.pack('<h',0)*4800)
    assets=[]
    for kind,mime,data,name in [('media','image/png',image.getvalue(),'synthetic-image.png'),('media','video/mp4',video.read_bytes(),'synthetic-video.mp4'),('media','audio/wav',audio.getvalue(),'synthetic-audio.wav'),('media','application/pdf',b'%PDF-1.4\n% Synthetic download-only fixture\n','synthetic-document.pdf'),('media','image/png',b'<html><script>window.__typedXss=1</script></html>','invalid-signature.png')]:
        row=state.agent_store.save_artifact(typed_task['id'],kind,mime,data);assets.append(row)
        state.agent_store.append_event(typed_task['id'],'agent.media',{'artifact':{k:row[k]for k in('id','task_id','kind','mime','size')},'name':name,'caption':'Explicit synthetic '+name,'call_id':'share-'+row['id']})
    upload=state.agent_store.save_artifact(typed_task['id'],'upload','image/png',image.getvalue())
    state.agent_store.append_event(typed_task['id'],'user.media',{'artifacts':[{k:upload[k]for k in('id','task_id','kind','mime','size')}]})
    (Path(project['path'])/'typed-created.txt').write_text('Synthetic typed file context\n')
    for call_id,name,args,value in [('typed-read','read_file',{'path':'typed-created.txt'},{'ok':True,'path':'typed-created.txt','start_line':1,'content':'Synthetic typed file context\n'}),('typed-write','write_file',{'path':'typed-created.txt'},{'ok':True,'path':'typed-created.txt','size':29}),('typed-patch','apply_patch',{'patch':'explicit fixture'},{'ok':True,'changes':[{'path':'typed-created.txt','added':1,'removed':0,'preview':['--- a/typed-created.txt','+++ b/typed-created.txt','+Synthetic typed file context']} ]})]:
        state.agent_store.append_event(typed_task['id'],'tool.started',{'call':{'call_id':call_id,'name':name,'arguments':args}})
        state.agent_store.append_event(typed_task['id'],'tool.finished',{'call_id':call_id,'result':value})
    foreign=state.agent_store.create_task(prompt='Foreign synthetic task',cwd=project['path'],provider_id='offline-design-fixture',model='fixture-review',mode='ask',limits=DEFAULT_LIMITS,status='completed')
    state.workspace.store.create('task','foreign-synthetic-owner',{'conversation_id':typed['id'],'cwd':project['path']},project['id'],foreign['id'])
    foreign_file=state.agent_store.save_artifact(foreign['id'],'media','image/png',image.getvalue())
    qa.update(typed_session=typed,typed_task=typed_task,typed_assets=assets,typed_image=image.getvalue(),foreign_task=foreign,foreign_artifact=foreign_file)
    for index in range(14):
        status='awaiting_approval'if index==0 else'running'if index==1 else'failed'if index==2 else'completed'
        child=state.workspace.create_session(owner,title='Synthetic worker '+str(index+1).zfill(2),project_id=project['id'],cwd=project['path'],provider_id='configured-ui-fixture',model='fixture-configured',mode='ask')
        child_task=seed(child,'Synthetic delegated check '+str(index+1),'Synthetic child result '+str(index+1))
        if status!='completed':qa['mutable_statuses'].append((child_task['id'],status))
        if index==0:
            qa['child_approval_task']=child_task['id']
        qa['children'].append({'session_id':child['id'],'task_id':child_task['id'],'title':child['title'],'status':status,'engine':'internal','origin':'delegated','capability_limits':{},'control_owner':'orchestrator','pending_approvals':1 if index==0 else 0,'revision':1,'updated_at':time()-index*70,'parent_session_id':sessions['welcome']['id']})
    async def stub_create(**kwargs):
        assert kwargs.get('provider_id')=='lifecycle-ui-fixture'and kwargs.get('prompt','').startswith(('Implement isolated lifecycle fixture','Continue isolated lifecycle fixture')),kwargs.get('provider_id')
        cwd=Path(kwargs['cwd']);assert cwd!=repo and kwargs.get('isolated')is True
        (cwd/'qa-result.txt').write_text('Synthetic stub output in enrolled worktree only\n')
        row=state.agent_store.create_task(prompt=kwargs['prompt'],cwd=str(cwd),provider_id=kwargs['provider_id'],model=kwargs.get('model')or'fixture-lifecycle',limits=kwargs['limits'],mode=kwargs['mode'],status='completed')
        state.agent_store.update_task(row['id'],result='Synthetic lifecycle reply; no model was invoked.')
        state.agent_store.append_event(row['id'],'task.completed',{'result':'Synthetic lifecycle reply; no model was invoked.'})
        callback=kwargs.get('on_created')
        if callback:callback(row['id'])
        qa['stub_calls'].append({'task_id':row['id'],'cwd':str(cwd),'isolated':kwargs.get('isolated'),'prompt':kwargs['prompt']})
        return state.agent_store.get_task(row['id'])
    state.agent.create_task=stub_create
    report['seeded_task_count']=len(state.agent_store.list_tasks())

def typed_content_checks(page,context,base,state,owner,output,report,theme,width,axe):
    qa=state._qa;session=qa['typed_session'];page.goto(base+'/?session='+session['id']+'&layout=chat')
    expect(page.get_by_role('heading',name='Synthetic typed content',exact=True)).to_be_visible()
    shared=page.locator('section.content-attachments[aria-label="Shared files"]');expect(shared).to_be_visible()
    expect(shared.locator('.content-asset-image img').first).to_be_visible()
    expect(shared.locator('video[controls]')).to_have_count(1);expect(shared.locator('audio[controls]')).to_have_count(1)
    expect(shared.locator('.content-asset-pdf')).to_have_count(1)
    expect(shared.get_by_role('alert').filter(has_text='A preview is unavailable')).to_be_visible()
    assert not page.evaluate('Boolean(window.__typedXss)')
    assert page.locator('.message-response iframe,.message-response img[src^="http"],a[href^="javascript:"]').count()==0
    expect(page.locator('.content-link-preview[href="https://example.invalid/docs"]')).to_be_visible()
    expect(page.locator('.content-link-preview[href="https://example.invalid/never-fetch.png"]')).to_be_visible()
    image_url=shared.locator('.content-asset-image img').first.get_attribute('src');assert image_url.startswith('blob:')
    owned='/api/workspace/tasks/'+qa['typed_task']['id']+'/artifacts/'+qa['typed_assets'][0]['id']
    response=context.request.get(base+owned);assert response.ok and response.body()==qa['typed_image']
    denied=context.request.get(base+'/api/workspace/tasks/'+qa['foreign_task']['id']+'/artifacts/'+qa['foreign_artifact']['id']);assert denied.status in(403,404)
    assert httpx.get(base+owned).status_code==401
    shared.scroll_into_view_if_needed()
    audit(page,output,report,'typed-media',theme,width,axe)
    expect(page.locator('.response-content-actions').get_by_role('button',name='Open typed-created.txt',exact=True)).to_have_count(0)
    activity=page.get_by_role('button',name='View activity',exact=False);activity.click()
    expect(page.locator('.response-content-actions').get_by_role('button',name='Open typed-created.txt',exact=True)).to_be_visible()
    read=page.get_by_role('button',name=re.compile('Read file.*typed-created.txt'));read.click()
    expect(page.get_by_role('region',name='typed-created.txt content',exact=True)).to_contain_text('Synthetic typed file context')
    expect(page.get_by_role('button',name='Open file typed-created.txt:1',exact=True)).to_be_visible()
    audit(page,output,report,'typed-content',theme,width,axe)
    raw=page.locator('.tool-action-content details').filter(has=page.locator('summary').filter(has_text='Raw details')).first
    if raw.count():
        page.emulate_media(reduced_motion='no-preference')
        summary=raw.locator('summary');summary.focus();page.keyboard.press('Enter');expect(raw).to_contain_text('typed-read')
        summary.focus();page.keyboard.press('Enter');assert raw.get_attribute('open')is None
        page.keyboard.press('Tab');assert not raw.evaluate('e=>e.contains(document.activeElement)'), 'Closed native details retained a keyboard target'
        page.emulate_media(reduced_motion='reduce')
        report['checks'].append({'check':'native-details-closed-exit-tab','theme':theme,'width':width,'closed_content_skipped':True})
    choose_workspace(page,'Studio');expect(shared).not_to_be_visible()
    expect(shared.locator('.content-asset-image img')).to_have_count(0)
    assert not page.evaluate('async url=>{try{await fetch(url);return true}catch{return false}}',image_url),'Hidden content blob was not revoked'
    choose_workspace(page,'Agent');expect(shared.locator('.content-asset-image img').first).to_be_visible()
    report['checks'].append({'check':'typed-owned-content-and-safety','theme':theme,'width':width,'owned_binary_bytes':True,'foreign_denied':denied.status,'unauthenticated_binary':401,'invalid_signature_fallback':True,'no_remote_fetch_or_active_html':True,'hide_revokes_blob':True,'fixture':'Owned PNG, MP4, silent WAV, PDF and invalid-signature artifacts; real binary/auth routes, no external asset fetch.'})

def subagents_checks(page,context,base,sessions,tasks,project,state,owner,output,report,theme,width,axe):
    qa=state._qa
    node_rows=state.workspace.orchestrator.graph.plan(owner,[{'key':'inspect','title':'Synthetic inspect plan','prompt':'Inspect fixture only','role':'reviewer'},{'key':'verify','title':'Synthetic verify plan','prompt':'Verify fixture only','depends_on':['inspect']}],call_id='qa-plan-'+theme+str(width))
    snapshot_reads=[]
    def snapshot(route):
        assert route.request.method=='GET'
        snapshot_reads.append(time())
        route.fulfill(json={'session':state.workspace.session(owner,sessions['welcome']['id'],turns=False),'children':qa['children'],'activity':[],'cursor':1})
    context.route('**/api/workspace/orchestrator',snapshot)
    page.evaluate('(key)=>localStorage.setItem(key,"closed")','termx-coordination-sidebar:'+owner.id)
    page.goto(base+'/?session='+sessions['welcome']['id']+'&layout=chat')
    toggle=page.get_by_role('button',name='Subagents',exact=True)
    expect(toggle).to_be_visible();expect(toggle).to_have_attribute('aria-expanded','false')
    aside=page.get_by_role('complementary',name='Subagents',exact=True)
    review=page.get_by_role('button',name='Review action',exact=True);expect(review).to_be_visible()
    assert page.locator('#workspace-subagents').get_attribute('inert')is not None
    page.emulate_media(reduced_motion='no-preference')
    toggle.click()
    samples=[]
    for delay in(0,50,80,100):
        if delay:page.wait_for_timeout(delay)
        samples.append(page.locator('#workspace-subagents').evaluate('e=>{const c=getComputedStyle(e);return {time:performance.now(),opacity:Number(c.opacity),transform:c.transform,width:e.getBoundingClientRect().width,animations:e.getAnimations().map(a=>a.playState)}}'))
    assert max(x['opacity']for x in samples)-min(x['opacity']for x in samples)>.05,samples
    assert samples[-1]['opacity']>.95,samples
    expect(aside.get_by_role('region',name='Active subagents',exact=True)).to_be_visible()
    done=aside.get_by_role('region',name='Done subagents',exact=True)
    expect(done.get_by_role('button',name=re.compile('^Open Synthetic worker'))).to_have_count(10)
    more=done.get_by_role('button',name='Show more done subagents',exact=True);more.focus();page.keyboard.press('Enter')
    expect(done.get_by_role('button',name=re.compile('^Open Synthetic worker'))).to_have_count(12)
    assert aside.locator('time[datetime]').count()==14
    assert page.locator('.orchestrator-status[data-presentation=sidebar]').count()==1
    audit(page,output,report,'subagents-main',theme,width,axe)
    plans=aside.get_by_role('button',name=re.compile('^Plans'));plans.focus();page.keyboard.press('Enter')
    expect(aside.get_by_role('region',name='Task board',exact=True)).to_be_visible()
    expect(aside.get_by_role('listitem',name='Synthetic inspect plan',exact=True)).to_be_visible()
    audit(page,output,report,'subagents-plans',theme,width,axe)
    plans.click()
    content=aside.locator('.coordination-plan-content')
    if content.count():assert content.get_attribute('inert')is not None
    plans.click();expect(aside.get_by_role('region',name='Task board',exact=True)).to_be_visible()
    assert aside.locator('.coordination-plan-content').get_attribute('inert')is None
    plans.click()
    worker=qa['children'][1]
    aside.get_by_role('button',name='Open '+worker['title'],exact=True).click()
    expect(page.locator('.orchestrator-child-banner')).to_contain_text('Managed by your main agent')
    assert worker['session_id']in page.url
    if width<640 and aside.is_visible():
        aside.get_by_role('button',name='Close subagents',exact=True).click();expect(aside).not_to_be_visible()
    page.locator('.orchestrator-child-banner').get_by_role('button',name='Main agent',exact=True).click()
    expect(review).to_be_visible()
    if not aside.is_visible():toggle.click();expect(aside).to_be_visible()
    aside.get_by_role('button',name='Close subagents',exact=True).focus();page.keyboard.press('Escape')
    expect(toggle).to_be_focused();expect(toggle).to_have_attribute('aria-expanded','false')
    assert page.locator('#workspace-subagents').get_attribute('inert')is not None
    assert page.locator('#workspace-subagents').get_attribute('aria-hidden')=='true'
    page.wait_for_timeout(180);assert page.locator('#workspace-subagents').get_attribute('hidden')is not None
    expect(review).to_be_visible()
    page.emulate_media(reduced_motion='reduce');toggle.click();expect(aside).to_be_visible()
    reduced=aside.evaluate('e=>({opacity:Number(getComputedStyle(e).opacity),animations:e.getAnimations().length,width:e.getBoundingClientRect().width})')
    assert reduced['opacity']==1 and reduced['animations']==0,reduced
    aside.get_by_role('button',name='Close subagents',exact=True).click();expect(aside).not_to_be_visible()
    context.unroute('**/api/workspace/orchestrator',snapshot)
    for row in node_rows:state.workspace.store.delete('orchestrator_node',row['id'])
    report['checks'].append({'check':'subagent-main-motion-and-management','theme':theme,'width':width,'normal_motion_samples':samples,'reduced_motion_actual':reduced,'snapshot_reads':len(snapshot_reads),'single_observer_dom':True,'critical_approval_visible_closed':True,'done_show_more':12,'child_navigation_exact':True,'plans_keyboard_and_rapid_reopen':True,'fixture':'Explicit Main snapshot over real owned sessions/tasks; actual plan-board stores.'})
    # Ordinary coding-thread children use the real authorized task tree and inspector.
    if not qa.get('ordinary_child'):
        from termx.agent.limits import DEFAULT_LIMITS
        child=state.agent_store.create_task(prompt='Synthetic ordinary task child',cwd=project['path'],provider_id='configured-ui-fixture',model='fixture-configured',limits=DEFAULT_LIMITS,mode='ask',parent_id=tasks['completed']['id'],status='running')
        state.workspace.store.create('task',owner.id,{'conversation_id':sessions['conversation']['id'],'cwd':project['path'],'execution_location':project['path'],'parent_id':tasks['completed']['id'],'agent_label':'Ordinary QA reviewer','delegated_task':'Synthetic ordinary task child'},project['id'],child['id'])
        state.authorization.claim_principal(owner,'task',child['id'],project_id=project['id'])
        qa['ordinary_child']=child
        report['seeded_task_count']+=1
    page.goto(base+'/?session='+sessions['conversation']['id']+'&layout=chat')
    expect(page.get_by_role('heading',name='Local review fixture',exact=True)).to_be_visible()
    toggle=page.get_by_role('button',name='Subagents',exact=True)
    if toggle.get_attribute('aria-expanded')!='true':toggle.click()
    aside=page.get_by_role('complementary',name='Subagents',exact=True)
    ordinary=aside.get_by_role('button',name='Open Ordinary QA reviewer',exact=True);expect(ordinary).to_be_visible()
    audit(page,output,report,'subagents-ordinary',theme,width,axe)
    current_url=page.url;ordinary.click()
    inspector=page.get_by_role('dialog',name='Task supervision',exact=True);expect(inspector).to_be_visible();panel_keyboard(page,inspector)
    expect(inspector).to_contain_text('Synthetic ordinary task child');assert page.url==current_url
    page.keyboard.press('Escape');expect(inspector).not_to_be_visible()
    # Popstate can retain A's loaded snapshot while B's real request is pending.
    # Delay its GraphQL node request at the browser transport boundary, without changing either response.
    delayed=[];destination=qa['typed_session']['id']
    delayed_pattern='**/graphql';destination_node=base64.b64encode(('WorkspaceSessionNode:'+destination).encode()).decode()
    def hold_session(route):
        payload=route.request.post_data_json or {}
        if destination_node in (payload.get('variables')or{}).get('ids',[]):delayed.append(route)
        else:route.fallback()
    context.route(delayed_pattern,hold_session)
    page.evaluate("url=>{history.pushState(null,'',url);window.dispatchEvent(new PopStateEvent('popstate'))}", '/?session='+destination+'&layout=chat')
    wait(lambda:bool(delayed),page=page)
    expect(aside.get_by_role('button',name='Open Ordinary QA reviewer',exact=True,include_hidden=True)).to_have_count(0)
    audit(page,output,report,'subagents-delayed-popstate',theme,width,axe)
    for request in delayed:request.continue_()
    context.unroute(delayed_pattern,hold_session)
    expect(page.get_by_role('heading',name='Synthetic typed content',exact=True)).to_be_visible()
    expect(aside.get_by_role('button',name='Open Ordinary QA reviewer',exact=True,include_hidden=True)).to_have_count(0)
    report['checks'].append({'check':'subagent-selected-snapshot-pending-popstate','theme':theme,'width':width,'prior_child_absent_during_delayed_next_snapshot':True,'delayed_real_session_graphql_requests':len(delayed)})
    close=page.get_by_role('button',name='Close subagents',exact=True)
    if close.count()and close.is_visible():close.click();expect(aside).not_to_be_visible()
    report['checks'].append({'check':'subagent-ordinary-authorized-task-tree','theme':theme,'width':width,'ordinary_authorized_tree_inspected':True,'inspector_keeps_conversation':True,'fixture':'Ordinary child uses real authorized host task tree and existing Task supervision dialog.'})

def lifecycle_checks(page,context,base,sessions,state,owner,output,report,theme,width,axe):
    import subprocess
    qa=state._qa;project=qa['lifecycle_project'];repo=Path(project['path'])
    def git(*args):return subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=True).stdout
    if not(repo/'.env').exists():
        (repo/'base.txt').write_text('Staged synthetic source edit\n');git('add','base.txt');(repo/'base.txt').write_text('Unstaged synthetic source edit\n');(repo/'.env').write_text('SYNTHETIC_ONLY=not-a-real-secret\n')
    before=git('status','--porcelain=v1','-z');index=(repo/'.git/index').read_bytes()
    old={row['id']for row in state.workspace.sessions(owner)}
    navigation=page.get_by_role('navigation',name='Workspace navigation',exact=True)
    direct=page.locator('.sidebar').get_by_role('button',name='New chat',exact=True)
    if not direct.is_visible():
        reveal=navigation.get_by_role('button',name='Show sidebar',exact=True)
        if reveal.is_visible():reveal.click()
    direct.click()
    wait(lambda:len({row['id']for row in state.workspace.sessions(owner)}-old)==1,page=page)
    new=next(row for row in state.workspace.sessions(owner)if row['id']not in old)
    expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_focused()
    assert new['title']=='' and new['title_origin']=='pending',new
    expect(page.get_by_role('dialog',name='New chat with options',exact=True)).not_to_be_visible()
    audit(page,output,report,'direct-untitled-chat',theme,width,axe)
    before_ids={row['id']for row in state.workspace.sessions(owner)}
    page.get_by_role('button',name='Workspace actions',exact=True).click();page.get_by_role('button',name='New chat with options',exact=True).click()
    options=page.get_by_role('dialog',name='New chat with options',exact=True);expect(options).to_be_visible()
    expect(options.get_by_label('Conversation title',exact=True)).to_have_count(0)
    options.get_by_label('Project',exact=True).select_option(project['id'])
    options.get_by_role('button',name='Create chat',exact=True).click();expect(options).not_to_be_visible()
    wait(lambda:len({row['id']for row in state.workspace.sessions(owner)}-before_ids)==1,page=page)
    row=next(row for row in state.workspace.sessions(owner)if row['id']not in before_ids)
    model=page.get_by_role('button',name='Model and effort',exact=True);model.click()
    settings=page.get_by_role('dialog',name='Model and effort',exact=True)
    settings.get_by_label('Session provider',exact=True).select_option('lifecycle-ui-fixture')
    expect(settings.get_by_label('Session model',exact=True)).to_have_value('fixture-lifecycle')
    page.keyboard.press('Escape');expect(settings).not_to_be_visible()
    report.setdefault('allowed_lifecycle_sessions',[]).append(row['id'])
    count=len(state.delivery.worktrees(project['id']))
    prompt='Implement isolated lifecycle fixture'
    composer=page.get_by_role('textbox',name='Message',exact=True);composer.fill(prompt)
    page.get_by_role('button',name='Send message',exact=True).click()
    expect(page.locator('.message-response').filter(has_text='Synthetic lifecycle reply; no model was invoked.')).to_be_visible()
    current=state.workspace.session(owner,row['id'],turns=False)
    assert current['title']==prompt and current['title_origin']=='auto',current
    assert current['worktree_id']and current['checkout_state']=='isolated'and current['cwd']!=str(repo),current
    assert current['source_cwd']==str(repo)and current['source_dirty']is True and current['worktree_base_ref']==qa['git_head'],current
    assert len(state.delivery.worktrees(project['id']))==count+1
    assert not(Path(current['cwd'])/'.env').exists()
    assert git('status','--porcelain=v1','-z')==before and(repo/'.git/index').read_bytes()==index
    assert not(repo/'qa-result.txt').exists()and(Path(current['cwd'])/'qa-result.txt').is_file()
    audit(page,output,report,'first-prompt-title-worktree',theme,width,axe)
    review=page.get_by_role('button',name='Review 1 changed file',exact=True);expect(review).to_be_visible();review.click()
    expect(page.get_by_text('qa-result.txt',exact=True).first).to_be_visible()
    audit(page,output,report,'content-review-cta',theme,width,axe)
    if width<850:
        chat_tab=page.get_by_role('tablist',name='Workspace tabs',exact=True).get_by_role('tab',name='Chat',exact=True)
        chat_tab.focus();page.keyboard.press('Enter');expect(chat_tab).to_have_attribute('aria-selected','true')
    else:page.keyboard.press('Control+1')
    expect(composer).to_be_visible();composer.fill('Continue isolated lifecycle fixture')
    page.get_by_role('button',name='Send message',exact=True).click()
    wait(lambda:len([item for item in qa['stub_calls']if item['cwd']==current['cwd']])==2,page=page)
    reused=state.workspace.session(owner,row['id'],turns=False)
    assert reused['title']==prompt and reused['worktree_id']==current['worktree_id']and reused['cwd']==current['cwd']
    assert len(state.delivery.worktrees(project['id']))==count+1
    report['checks'].append({'check':'direct-untitled-first-send-worktree-title-reuse','theme':theme,'width':width,'source_git_unchanged':True,'untracked_env_not_copied':True,'title_local_first_prompt':True,'same_enrolled_worktree_reused':True,'stub_dispatches':2,'real_provider_calls':0,'fixture':'Real scratch Git repository and workspace HTTP send; only AgentManager.create_task is a whitelisted deterministic completed-task stub.'})

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
    tasks=state._qa_tasks
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
    close_subagents=page.get_by_role('button',name='Close subagents',exact=True)
    if close_subagents.count()and close_subagents.is_visible():close_subagents.click()
    log=page.get_by_role('log');log.evaluate('e=>e.scrollTop=0')
    if not baseline:
        expect(page.get_by_role('checkbox',name='Send after dictation',exact=True)).to_have_count(0)
        expect(page.get_by_role('checkbox',name='Read replies aloud',exact=True)).to_have_count(0)
        read=page.locator('.message-assistant .response-voice-actions').get_by_role('button',name='Read aloud',exact=True)
        expect(read).to_be_visible()
        report['checks'].append({'check':'explicit-response-voice-only','theme':theme,'width':width,'read_action_present':True,'auto_voice_checkboxes_absent':True,'speech_or_microphone_invoked':False})
    if not baseline or (theme=='dark' and width==1920) or (theme=='light' and width==375): audit(page,output,report,'conversation',theme,width,axe,not baseline)
    activity=page.get_by_role('button',name='View activity',exact=False)
    expect(activity).to_have_attribute('data-state','closed')
    activity.focus();page.keyboard.press('Enter')
    tool=page.get_by_role('button',name=re.compile('Read file.*design-review.txt'))
    expect(tool).to_be_visible();expect(tool).to_have_attribute('data-state','closed')
    tool.focus();page.keyboard.press('Enter')
    expect(page.get_by_label('Read file · design-review.txt result')).to_be_visible()
    trigger=page.locator('.message-assistant .run-activity-trigger');trigger.click();expect(trigger).to_have_attribute('data-state','closed')
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
    audit(page,output,report,'studio-unconfigured',theme,width,axe,not baseline)
    expect(studio.get_by_role('button',name='Generate image',exact=True)).to_be_disabled()
    if _baseline:expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_be_disabled()
    else:expect(studio.get_by_role('checkbox',name=CONSENT,exact=True)).to_have_count(0)
    choose_workspace(page,'Agent')
    if editor:
        expect(editor).to_contain_text('unsaved_design_fixture')
        assert Path(project['path'],'design-review.txt').read_text()=='# Synthetic local design QA fixture\nprint("original")\n'
        page.keyboard.press('Control+1')
    expect(message).to_have_value(draft)
    expect(page.get_by_role('button',name='Remove design-qa-local.png',exact=True)).to_be_visible()
    choose_workspace(page,'Studio');expect(prompt).to_have_value('Unsent synthetic Studio draft; never generate.')
    choose_workspace(page,'Agent')
    report['checks'].append({'check':'draft-attachment-editor-studio-preservation','theme':theme,'width':width,'editor_checked':bool(editor),'paid_dispatches':0,'generation_requests':0})
    if not baseline:
        music_unconfigured_checks(page,context,base,project,output,report,theme,width,axe)
        studio_design_checks(page,context,base,sessions,project,state,owner,output,report,theme,width,axe)
        if (theme=='dark' and width==1920)or(theme=='light' and width==375):
            subagents_checks(page,context,base,sessions,tasks,project,state,owner,output,report,theme,width,axe)
            typed_content_checks(page,context,base,state,owner,output,report,theme,width,axe)
            lifecycle_checks(page,context,base,sessions,state,owner,output,report,theme,width,axe)
            page.goto(target('conversation'));expect(message).to_be_visible()
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
    wanted={'studio':'after-studio-image-dark-1920.png','music':'after-music-advanced-dark-1920.png','subagents':'after-subagents-main-dark-1920.png','phone':'after-music-simple-light-375.png'}
    if not all((output/name).is_file()for name in wanted.values()):return
    cards={key:'<figure class="'+key+'"><figcaption>'+name+'</figcaption><img src="data:image/png;base64,'+base64.b64encode((output/name).read_bytes()).decode()+'"></figure>' for key,name in wanted.items()}
    html='<!doctype html><html lang="en"><meta charset="utf-8"><title>Studio and sessions browser proof</title><style>body{margin:0;background:#111;color:#f4f4f4;font:14px system-ui;padding:24px}h1{font-size:20px;font-weight:600;margin:0 0 12px}p{line-height:1.6}main{display:grid;grid-template-columns:1280px 375px;grid-template-areas:"studio phone" "music notes" "subagents notes";gap:24px}figure{margin:0;min-width:0}figcaption{font-size:14px;margin-bottom:8px}.studio{grid-area:studio}.music{grid-area:music}.subagents{grid-area:subagents}.phone{grid-area:phone}img{display:block;width:100%;height:auto;object-fit:contain;object-position:top}aside{grid-area:notes;line-height:1.6;color:#b4b4b4}</style><h1>Studio, Music and sessions · actual browser captures</h1><p>Synthetic isolated QA data and explicit offline Studio catalogue; no inference or paid generation. Desktop captures shown at 1280px wide; phone capture at actual 375px.</p><main>'+cards['studio']+cards['phone']+cards['music']+cards['subagents']+'<aside><p>These are original browser captures embedded in an actual browser DOM preview.</p><p>Open the original PNGs for full 1920 × 1080 desktop images. The report retains layout, keyboard, motion samples, authenticated media and Git checkout results, and accessibility incomplete cases.</p></aside></main></html>'
    (output/'preview.html').write_text(html);page=browser.new_page(viewport={'width':1727,'height':1800});page.set_content(html);page.screenshot(path=str(output/'preview.png'),full_page=True);page.close()
    report['preview']={'html':'preview.html','screenshot':'preview.png','method':'Actual browser DOM screenshot embedding original browser captures; desktop width1280 and phone width375, no image editing.','originals':wanted}


def run(destination,ui_dist,baseline=False):
    global _baseline
    _baseline=baseline
    output=Path(destination).resolve();output.mkdir(parents=True,exist_ok=False)
    report={'result':'running','mode':'before' if baseline else 'after','output':str(output),'states':[],'checks':[],'errors':[],'external_browser_requests':[],'backend_requests':[],'browser_console':[],'http_errors':[],'failed_browser_requests':[],'provider_mutations':[],'studio_colors':{},'started_at_utc':datetime.now(timezone.utc).isoformat(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'limitations':['Real isolated password auth, FastAPI routes and persisted stores with synthetic QA messages; no external inference.','Automated full-document axe results retain incomplete items; screenshots and browser geometry do not prove screen reader or physical touch behavior.','Native packaging, physical devices, OS keyboard and actual model streaming are outside this browser proof.']}
    root=Path(tempfile.mkdtemp(prefix='termx-studio-sessions-'));server=worker=listener=client=None
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
        samples=io.BytesIO();Image.new('RGB',(320,180),(105,105,105)).save(samples,'PNG')
        media=state.artifacts.create(owner.id,project['id'],'image','Synthetic owned Studio output',blob=samples.getvalue(),mime='image/png')
        state._qa={'studio_image_url':'/api/media/artifacts/'+media['id']+'/export?format=original'}
        sessions,tasks=make_tasks(state,owner,project,project_root)
        state.workspace.orchestrator.ensure(owner,session_id=sessions['welcome']['id'])
        prepare_extra_fixtures(state,owner,project,sessions,tasks,report)
        state._qa_tasks=tasks
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
        for identifier,status in state._qa['mutable_statuses']:state.agent_store.update_task(identifier,status=status)
        state.agent_store.create_approval(state._qa['child_approval_task'],'tool',{'reason':'Synthetic local review operation; never approve in browser proof.','call':{'call_id':'qa-child-hold','name':'read_file','arguments':{'path':'design-review.txt'}}})
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
            for width,height,theme in ((375,812,'light'),(1920,1080,'dark'),(1920,1080,'light'),(1280,900,'dark'),(1280,900,'light'),(375,812,'dark')):
                phase(report,'starting-case',theme=theme,width=width)
                context=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce',has_touch=width<640)
                context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
                context.add_init_script("""(()=>{window.__qaVoiceInvocations=[];if(window.speechSynthesis){window.speechSynthesis.speak=()=>{window.__qaVoiceInvocations.push('speechSynthesis.speak');throw Error('Isolated QA never plays speech')}}if(navigator.mediaDevices){Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:()=>{window.__qaVoiceInvocations.push('getUserMedia');return Promise.reject(Error('Isolated QA never records microphone'))}})}})()""")
                origin=urlsplit(base)
                def network(route):
                    request=route.request;url=urlsplit(request.url)
                    if url.scheme in ('http','https') and (url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_browser_requests'].append(request.url);route.abort('blockedbyclient');return
                    turn=re.fullmatch(r'/api/workspace/sessions/([^/]+)/turns',url.path)
                    if request.method=='POST'and turn and turn.group(1)not in report.get('allowed_lifecycle_sessions',[]):report['provider_mutations'].append({'method':request.method,'path':url.path});route.abort('blockedbyclient');return
                    if url.path=='/graphql'and request.method=='POST'and str((request.post_data_json or {}).get('query','')).lstrip().startswith('mutation'):report['provider_mutations'].append({'method':request.method,'path':'/graphql mutation'});route.abort('blockedbyclient');return
                    if request.method!='GET' and re.match(r'/api/(?:agent/tasks|workspace/tasks|media/studio/jobs|desktop/recording)(?:/|$)',url.path):report['provider_mutations'].append({'method':request.method,'path':url.path});route.abort('blockedbyclient');return
                    route.continue_()
                context.route('**/*',network);page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)));page.on('console',lambda msg:report['browser_console'].append({'type':msg.type,'text':msg.text,'location':msg.location}) if msg.type in ('warning','error') else None);page.on('requestfailed',lambda request:report['failed_browser_requests'].append({'method':request.method,'url':request.url,'failure':request.failure}));page.on('response',lambda response:report['http_errors'].append({'method':response.request.method,'path':urlsplit(response.url).path,'status':response.status}) if response.status>=400 else None)
                try:exercise(page,context,base,sessions,project,state,owner,output,report,theme,width,axe if axe.is_file() else None,baseline)
                except BaseException:
                    page.screenshot(path=str(output/'failure.png'),full_page=True);page.locator('input[type=password]').evaluate_all('r=>r.forEach(e=>e.value="")');(output/'failure.html').write_text(page.content());raise
                finally:
                    attempts=page.evaluate('window.__qaVoiceInvocations||[]')
                    report.setdefault('voice_platform_invocations',[]).extend(attempts)
                    context.close()
                    assert not attempts,attempts
            preview(browser,output,report);browser.close()
        assert not report['errors'] and not report['external_browser_requests'] and not report['provider_mutations'], report
        report['lifecycle_stub_dispatches']=state._qa['stub_calls']
        assert len(state.agent_store.list_tasks())==report['seeded_task_count']+len(state._qa['stub_calls']),'Unexpected task dispatch'
        assert not [r for r in report['backend_requests'] if r['method']!='GET'],report['backend_requests']
        report['result']='passed'
        report['studio_design']='Authorized neutral Studio refresh; prior exact-color parity is not an acceptance condition.'
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
    if len(sys.argv) not in (3,4):raise SystemExit('Usage: workspace_studio_sessions_e2e.py OUTPUT UI_DIST [--baseline]')
    r=run(sys.argv[1],sys.argv[2],len(sys.argv)==4 and sys.argv[3]=='--baseline')
    print(json.dumps({'result':r['result'],'states':len(r['states']),'checks':len(r['checks']),'errors':r['errors'],'axe_violation_states':r.get('axe_violation_states'),'stub_dispatches':len(r.get('lifecycle_stub_dispatches',[]))},indent=2))
