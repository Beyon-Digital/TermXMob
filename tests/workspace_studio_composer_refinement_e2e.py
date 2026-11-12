"""Bounded real-host Studio composer/focus proof; synthetic catalog, no dispatch.
Run from repo: .venv/bin/python -B SCRIPT OUTPUT UI_DIST [--baseline]
"""
from __future__ import annotations
import hashlib,json,os,re,sys,base64
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime,timezone
REPO=Path(os.environ.get('TERMX_COMPOSER_REPO')or Path.cwd())
sys.path.insert(0,str(REPO/'tests'))
import workspace_studio_projects_e2e as p
from playwright.sync_api import sync_playwright,expect

FOCUS_JS="""e=>{const r=x=>x.getBoundingClientRect().toJSON();const chain=[];for(let n=e;n&&n!==document.documentElement;n=n.parentElement){const s=getComputedStyle(n),o=parseFloat(s.outlineWidth)||0,off=parseFloat(s.outlineOffset)||0;chain.push({tag:n.tagName,class:n.className,rect:r(n),outline:s.outline,offset:off,expansion:Math.max(0,o+off),ring:parseFloat(s.opacity)>0&&o>0&&s.outlineStyle!=='none'&&s.outlineColor!=='rgba(0, 0, 0, 0)',shadow:s.boxShadow,overflowX:s.overflowX,overflowY:s.overflowY});if(n.matches('dialog:modal'))break;}return{hitTarget:e.matches('input[type=file]')&&e.closest('label')?r(e.closest('label')):r(e),hitTargetKind:e.matches('input[type=file]')&&e.closest('label')?'implicit clickable file-input label':'control',name:e.getAttribute('aria-label')||e.textContent?.trim().slice(0,80)||e.id,focusVisible:e.matches(':focus-visible'),active:e===document.activeElement,chain};}"""

def focus_sample(page,element,label,report,strict):
    element.focus();page.keyboard.press('Tab');page.keyboard.press('Shift+Tab')
    expect(element).to_be_focused()
    value=element.evaluate(FOCUS_JS);value['label']=label
    rings=[row for row in value['chain']if row['ring']]
    value['ring_count']=len(rings);value['clipping']=[]
    for index,row in enumerate(value['chain']):
        if not row['ring']:continue
        box=row['rect'];extra=row['expansion']
        if box['x']-extra<-.5 or box['right']+extra>page.viewport_size['width']+.5:value['clipping'].append('viewport')
        for parent in value['chain'][index+1:]:
            b=parent['rect']
            if parent['overflowX']in('hidden','clip','auto','scroll')and(box['x']-extra<b['x']-.5 or box['right']+extra>b['right']+.5):value['clipping'].append(parent['class'])
            if parent['overflowY']in('hidden','clip','auto','scroll')and(box['y']-extra<b['y']-.5 or box['bottom']+extra>b['bottom']+.5):value['clipping'].append(parent['class'])
    report['focus'].append(value)
    if strict:
        if page.viewport_size['width']<640:
            rect=value['hitTarget'];assert rect['height']>=44 and rect['width']>=44,('Coarse target below44px',label,rect)
        assert value['focusVisible'],value
        assert len(rings)==1,'Expected one painted focus ring: '+json.dumps(value)
        assert all(row['expansion']==0 for row in rings),'Focus marker must stay inset: '+json.dumps(value)
        assert not value['clipping'],'Focus marker is clipped/overflowing: '+json.dumps(value)
    return value

def audit(page,out,report,name,theme,width,axe):
    item=p.audit(page,out,report,name,theme,width,axe)
    item['composer']=page.locator('.studio-dock-main').evaluate_all("rows=>rows.filter(e=>e.getBoundingClientRect().width).map(e=>({rect:e.getBoundingClientRect().toJSON(),text:e.textContent,buttons:[...e.querySelectorAll('button')].filter(x=>x.getBoundingClientRect().width&&x.getBoundingClientRect().height).map(x=>({name:x.getAttribute('aria-label')||x.textContent.trim(),rect:x.getBoundingClientRect().toJSON()})),selects:[...e.querySelectorAll('select')].filter(x=>x.getBoundingClientRect().width).map(x=>({name:x.getAttribute('aria-label')||x.id,rect:x.getBoundingClientRect().toJSON()}))}))")
    item['focus_current']=page.locator(':focus').evaluate_all('rows=>rows.map(e=>({tag:e.tagName,class:e.className,text:e.getAttribute("aria-label")||e.textContent.slice(0,70)}))')
    return item

def catalog_fixture(context,base):
    response=context.request.get(base+'/api/media/studio/catalog');assert response.ok,response.text()
    raw=response.json()
    raw.update(accounts=[{'id':'synthetic-offline-account','name':'Synthetic offline account','adapter':'openrouter','configured':True,'capabilities':['image','video']},{'id':'synthetic-music-account','name':'Synthetic Music account','adapter':'elevenlabs','configured':True,'capabilities':['audio']}],models=[
    {'id':'synthetic/still','label':'Synthetic still','kind':'image','adapter':'openrouter','modes':['image','cinema'],'description':'Offline QA metadata; no provider exists','aspect_ratios':['1:1','16:9'],'resolutions':['1K','2K'],'durations':[],'supports_image':True,'inline_inputs':['image']},
    {'id':'synthetic/video','label':'Synthetic video','kind':'video','adapter':'openrouter','modes':['video'],'description':'Offline QA metadata; no provider exists','aspect_ratios':['16:9'],'resolutions':['720p','1080p'],'durations':[5,10],'supports_image':True,'inline_inputs':['image']},
    {'id':'elevenlabs/music_v2_5','label':'Eleven Music 2.5','kind':'audio','adapter':'elevenlabs','modes':['music'],'aspect_ratios':[],'resolutions':[],'durations':[30,60],'duration_min':3,'duration_max':600,'custom_duration_max':120,'supported_music_modes':['simple','custom'],'supports_custom_lyrics':True,'supports_instrumental':True,'supports_negative_styles':True}],catalog_source='live')
    def route(r):
        if r.request.method=='GET':r.fulfill(json=raw)
        else:r.fallback()
    context.route('**/api/media/studio/catalog*',route)


def exercise(page,context,state,owner,agent_project,base,out,report,theme,width,axe,baseline):
    p.authenticate(page,base)
    if not baseline and width==1280 and theme=='light':
        p.close_studio_sidebar(page);expect(page.locator('.media-studio').get_by_role('button',name='Generate image',exact=True)).to_be_disabled()
        simple_copy_check(page,report,'Unconfigured Image',theme,width);audit(page,out,report,'unconfigured-default',theme,width,axe)
    catalog_fixture(context,base)
    api,_=p.owner_api(state,base)
    first=api.post('/api/media/studio/projects',json={'name':'Synthetic focus collection','description':'Isolated visual fixture.'}).json()
    other=api.post('/api/media/studio/projects',json={'name':'Synthetic alternate collection'}).json()
    page.reload();expect(page.get_by_role('region',name='Media studio',exact=True)).to_be_visible(timeout=30000)
    p.select_project(page,first['name']);expect(p.visible_sidebar(page).get_by_role('button',name=first['name'],exact=True)).to_have_attribute('aria-pressed','true');p.close_studio_sidebar(page)
    studio=page.get_by_role('region',name='Media studio',exact=True)
    prompt=studio.locator('textarea.studio-prompt');expect(prompt).to_be_visible();prompt.fill('A quiet coastal house at sunrise, warm natural light.')
    page.locator('.studio-empty-stage h3,.studio-empty h3').first.click()if page.locator('.studio-empty-stage h3,.studio-empty h3').count()else page.locator('.studio-canvas').click(position={'x':20,'y':30})
    audit(page,out,report,'image-default',theme,width,axe)
    focus_sample(page,prompt,'Image prompt',report,not baseline)
    audit(page,out,report,'image-keyboard-focus',theme,width,axe)
    if not baseline:focus_sample(page,studio.get_by_role('button',name='Refresh',exact=True),'Recent generations Refresh',report,True)
    p.visible_sidebar(page)
    project_button=page.get_by_role('navigation',name='Studio projects',exact=True).get_by_role('button',name=first['name'],exact=True)
    focus_sample(page,project_button,'Selected Studio project row',report,not baseline)
    if baseline:
        audit(page,out,report,'sidebar-keyboard-focus',theme,width,axe);api.close();return
    p.close_studio_sidebar(page)
    simple_copy_check(page,report,'Image',theme,width)
    assert_compact_composer(page,report,'image',theme,width)
    studio.get_by_role('tab',name='Video',exact=True).click()
    prompt.fill('A slow dolly shot through a quiet coastal house at sunrise.')
    for button in studio.locator('.studio-tool-tile').all():
        if button.is_enabled():focus_sample(page,button,'Video '+str(button.get_attribute('aria-label')),report,True)
    audit(page,out,report,'video-options-keyboard',theme,width,axe)
    assert_compact_composer(page,report,'video',theme,width)
    simple_copy_check(page,report,'Video',theme,width)
    trigger=studio.get_by_role('button',name='Generation settings',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Generation settings',exact=True);expect(dialog).to_be_visible();p.keyboard_dialog(page,dialog)
    field=dialog.locator('select:not(:disabled),input:not(:disabled),button:not(:disabled)').first
    focus_sample(page,field,'Generation settings first control',report,True)
    if width==375:audit(page,out,report,'settings-keyboard-focus',theme,width,axe)
    page.keyboard.press('Escape');expect(dialog).not_to_be_visible();expect(trigger).to_be_focused()
    tools=studio.get_by_role('button',name='Tools',exact=True);tools.focus();page.keyboard.press('Enter')
    tools_dialog=page.get_by_role('dialog',name='Studio tools',exact=True);expect(tools_dialog).to_be_visible();p.keyboard_dialog(page,tools_dialog)
    expect(tools_dialog.get_by_role('button',name=re.compile('Cinema'))).to_be_visible()
    page.keyboard.press('Escape');expect(tools_dialog).not_to_be_visible();expect(tools).to_be_focused()
    if width==375 and theme=='light':affected_panels_check(page,studio,out,report,theme,axe)
    studio.get_by_role('tab',name='Image',exact=True).click()
    project_guard_check(page,first,other,report,theme,width,'image')
    p.studio_section(page,'Music');p.close_studio_sidebar(page)
    music=page.locator('.music-studio');expect(music).to_be_visible()
    song=music.get_by_role('textbox',name='Song description',exact=True);song.fill('Synthetic quiet acoustic theme')
    focus_sample(page,song,'Music Song description',report,True)
    search=music.get_by_role('searchbox',name='Search My songs',exact=True)
    focus_sample(page,search,'Music library search',report,True)
    simple_copy_check(page,report,'Music',theme,width)
    audit(page,out,report,'music-keyboard-focus',theme,width,axe)
    project_guard_check(page,first,other,report,theme,width,'music')
    p.visible_sidebar(page)
    create=page.get_by_role('button',name='Create project',exact=True);create.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Create project',exact=True);expect(dialog).to_be_visible();p.keyboard_dialog(page,dialog)
    focus_sample(page,dialog.get_by_role('textbox',name='Project name',exact=True),'Project dialog Project name',report,True)
    if width==1280:audit(page,out,report,'project-dialog-keyboard-focus',theme,width,axe)
    page.keyboard.press('Escape');expect(dialog).not_to_be_visible()
    if width==1280 and theme=='dark':p.managed_api_boundaries(state,owner,agent_project,base,first,report)
    if (width==1280 and theme=='dark')or(width==375 and theme=='light'):
        p.studio_section(page,'Create');p.close_studio_sidebar(page)
        prompt=studio.locator('textarea.studio-prompt');prompt.fill('Synthetic exact direct-submit request; rejected before host dispatch.')
        direct_submit_check(page,context,studio,first,report,theme,width)
        audit(page,out,report,'direct-submit-error-draft',theme,width,axe)
    if width==375 and theme=='light':
        p.studio_section(page,'Create');p.close_studio_sidebar(page)
        studio.get_by_role('tab',name='Video',exact=True).click()
        page.set_viewport_size({'width':320,'height':812})
        for button in studio.locator('.studio-tool-tile').all():
            if button.is_enabled():focus_sample(page,button,'320px Video '+str(button.get_attribute('aria-label')),report,True)
        assert_compact_composer(page,report,'video',theme,320)
        audit(page,out,report,'narrow-video-focus',theme,320,axe)
        page.set_viewport_size({'width':375,'height':420})
        focus_sample(page,prompt,'Short-screen prompt',report,True)
        audit(page,out,report,'short-prompt-focus',theme,375,axe)
        page.set_viewport_size({'width':375,'height':812})
    if theme=='light':
        p.studio_section(page,'Create');p.close_studio_sidebar(page)
        studio.get_by_role('button',name='Tools',exact=True).click()
        page.get_by_role('dialog',name='Studio tools',exact=True).get_by_role('button',name=re.compile('^Content factory')).click()
        factory=page.get_by_role('region',name='Content factory',exact=True);expect(factory).to_be_visible()
        focus_sample(page,factory.get_by_role('textbox',name='What are we making?',exact=True),'Factory brief',report,True)
        simple_copy_check(page,report,'Content factory',theme,width)
        audit(page,out,report,'factory-keyboard-focus',theme,width,axe)
    api.close()


def affected_panels_check(page,studio,out,report,theme,axe):
    samples=[]
    for width in (375,320):
        page.set_viewport_size({'width':width,'height':812});studio.get_by_role('tab',name='Image',exact=True).click()
        def inspect(title,variant=''):
            dialog=page.get_by_role('dialog',name=title,exact=True);expect(dialog).to_be_visible();row={'width':width,'title':title,'variant':variant,'controls':[]}
            for target in dialog.locator('button,input:not([type=file]):not([type=hidden]),select,textarea,summary,.studio-upload-label').all():
                if not target.is_visible():continue
                box=target.bounding_box();name=target.get_attribute('aria-label')or target.text_content().strip()[:70]
                assert box['height']>=44 and box['width']>=44,('Affected panel coarse target below44px',title,name,box)
                row['controls'].append({'name':name,'rect':box})
                if target.evaluate('e=>e.tagName')=='LABEL':
                    file=target.locator('input[type=file]')
                    if file.count()and file.is_enabled():focus_sample(page,file,str(width)+' '+title+' Upload image file',report,True)
                    continue
                if not target.is_enabled()or target.get_attribute('tabindex')=='-1':continue
                focus_sample(page,target,str(width)+' '+title+' '+name,report,True)
            samples.append(row)
        def open_panel(trigger,title):
            trigger.click();inspect(title)
            if width==375 and title in('Reference media','Camera & lens'):audit(page,out,report,'panel-'+('reference'if title=='Reference media'else'camera')+'-focus',theme,width,axe)
            page.keyboard.press('Escape');expect(page.get_by_role('dialog',name=title,exact=True)).not_to_be_visible()
        focus_sample(page,studio.get_by_role('button',name='Tools',exact=True),str(width)+' Header Tools',report,True)
        open_panel(studio.get_by_role('button',name='Generation settings',exact=True),'Generation settings')
        studio.get_by_role('button',name='Reference',exact=True).click();inspect('Reference media','Upload or URL')
        if width==375:audit(page,out,report,'panel-reference-focus',theme,width,axe)
        for tab in('Saved sets','Recent images'):
            page.get_by_role('dialog',name='Reference media',exact=True).get_by_role('tab',name=tab,exact=True).click()
            if tab=='Saved sets':expect(page.locator('.studio-subjects')).to_be_visible()
            inspect('Reference media',tab)
        reference=page.get_by_role('dialog',name='Reference media',exact=True)
        reference.get_by_role('tab',name='Upload or URL',exact=True).click();reference.get_by_role('textbox',name='Image URL',exact=True).fill('https://fixture.example.com/reference.png')
        page.keyboard.press('Escape')
        focus_sample(page,studio.get_by_role('group',name='Selected reference media',exact=True).get_by_role('button'),str(width)+' Selected reference disclosure',report,True)
        studio.get_by_role('button',name='Reference',exact=True).click();page.get_by_role('dialog',name='Reference media',exact=True).get_by_role('textbox',name='Image URL',exact=True).fill('');page.keyboard.press('Escape')
        open_panel(studio.get_by_role('button',name='Camera',exact=True),'Camera & lens')
        studio.get_by_role('tab',name='Image',exact=True).click();open_panel(studio.get_by_role('button',name='Style',exact=True),'Prompt style')
        studio.get_by_role('tab',name='Video',exact=True).click()
        for name,title in(('Film setup','Film setup'),('Camera','Camera movement'),('Color','Color direction'),('Lighting','Lighting direction')):open_panel(studio.get_by_role('button',name=name,exact=True),title)
        open_panel(studio.get_by_role('button',name='Tools',exact=True),'Studio tools')
    page.set_viewport_size({'width':375,'height':812})
    report['checks'].append({'check':'affected-panels-keyboard-and-coarse-targets','panels':samples,'rectangles':sum(len(row['controls'])for row in samples),'native_summary_in_actual_Tab_cycle':True,'file_upload_label_bounds_checked':True,'reference_URL_staged_then_cleared_without_fetch':True})


def simple_copy_check(page,report,section,theme,width):
    text=page.locator('body').inner_text()
    banned=re.findall(r'[^\n]*(?:provider charges|acknowledge billing|bills this request|authorize.{0,25}charges|provider rates apply|review.{0,30}charges)[^\n]*',text,re.I)
    assert not banned,('Repeated billing copy remains',section,banned)
    report['checks'].append({'check':'no-repeated-charge-copy','section':section,'theme':theme,'width':width,'matches':banned})


def assert_compact_composer(page,report,mode,theme,width):
    studio=page.locator('.media-studio');dock=studio.locator('.studio-dock-main')
    tiles=studio.locator('.studio-tool-tile');assert tiles.count()==(3 if mode=='image'else 5)
    expected=['Reference','Camera','Style']if mode=='image'else['Reference','Film setup','Camera','Color','Lighting']
    assert [row.get_attribute('aria-label')for row in tiles.all()]==expected
    for tile in tiles.all():
        box=tile.bounding_box();assert box and box['height']==(46 if width<640 else 48),('Reference option tile height changed',box,width)
    data=dock.evaluate("""e=>{const rect=e.getBoundingClientRect();return{rect:rect.toJSON(),prompt:e.querySelector('textarea')?.getBoundingClientRect().toJSON(),tools:e.querySelector('.studio-dock-tools')?.getBoundingClientRect().toJSON(),footer:e.querySelector('.studio-dock-bottom')?.getBoundingClientRect().toJSON(),extraText:[...e.querySelectorAll('p')].filter(x=>x.getBoundingClientRect().height).map(x=>x.textContent),fields:[...e.querySelectorAll('select,button')].filter(x=>x.getBoundingClientRect().width&&x.getBoundingClientRect().height).map(x=>({name:x.getAttribute('aria-label')||x.textContent.trim(),box:x.getBoundingClientRect().toJSON()}))}}""")
    assert data['tools']and data['footer']and data['prompt'],data
    assert data['tools']['bottom']<=data['prompt']['y']+1,('Liked tiles must remain above prompt',data)
    assert data['prompt']['bottom']<=data['footer']['y']+1,('Single footer must follow prompt',data)
    assert studio.get_by_role('button',name='Rates',exact=True).count()==0
    assert not data['extraText'],data['extraText']
    assert page.locator('.studio-generation-review').count()==0
    if width<640:
        for field in data['fields']:
            assert field['box']['height']>=44,('Phone control smaller than44px',field)
        assert data['rect']['x']>=0 and data['rect']['right']<=width,data
    report['checks'].append({'check':'restored-Higgsfield-hierarchy-single-footer','mode':mode,'theme':theme,'width':width,**data})


def project_guard_check(page,first,other,report,theme,width,kind):
    closed_focus=[]
    if kind=='image':field=page.locator('.media-studio textarea.studio-prompt')
    else:field=page.locator('.music-studio').get_by_role('textbox',name='Song description',exact=True)
    expect(qnav:=p.visible_sidebar(page).get_by_role('button',name=first['name'],exact=True)).to_have_attribute('aria-pressed','true')
    p.close_studio_sidebar(page)
    text='Synthetic '+kind+' draft remains in source collection.';field.fill(text)
    p.select_project(page,other['name']);guard=page.get_by_role('dialog',name='Switch Studio project?',exact=True);expect(guard).to_be_visible();p.keyboard_dialog(page,guard)
    focus_sample(page,guard.get_by_role('button',name='Keep editing',exact=True),kind+' project guard Keep editing',report,True)
    guard.get_by_role('button',name='Keep editing',exact=True).click();expect(guard).not_to_be_visible();page.locator('.studio-project-switch-dialog').wait_for(state='detached');closed_focus.append(page.evaluate("async()=>{await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const e=document.activeElement;return{tag:e?.tagName,name:e?.getAttribute('aria-label')||e?.textContent?.trim().slice(0,70),insideClosedLayer:!!e?.closest('[inert],[aria-hidden=true]')}}")) ;expect(field).to_have_value(text)
    p.select_project(page,other['name']);expect(guard).to_be_visible();guard.get_by_role('button',name='Discard drafts and switch',exact=True).click();expect(guard).not_to_be_visible();page.locator('.studio-project-switch-dialog').wait_for(state='detached');closed_focus.append(page.evaluate("async()=>{await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const e=document.activeElement;return{tag:e?.tagName,name:e?.getAttribute('aria-label')||e?.textContent?.trim().slice(0,70),insideClosedLayer:!!e?.closest('[inert],[aria-hidden=true]')}}"))
    expect(p.visible_sidebar(page).get_by_role('button',name=other['name'],exact=True)).to_have_attribute('aria-pressed','true');expect(field).to_have_value('')
    p.visible_sidebar(page).get_by_role('button',name=first['name'],exact=True).click();expect(p.visible_sidebar(page).get_by_role('button',name=first['name'],exact=True)).to_have_attribute('aria-pressed','true');p.close_studio_sidebar(page);expect(field).to_have_value('')
    report['checks'].append({'check':'project-draft-guard-Keep-and-Discard','kind':kind,'theme':theme,'width':width,'Keep_preserves':True,'Discard_clears_return_does_not_resurrect':True,'actual_focus_after_completed_modal_exit':closed_focus})


def direct_submit_check(page,context,studio,project,report,theme,width):
    samples=[]
    def rejected(route):
        if route.request.method=='POST':
            body=route.request.post_data_json;samples.append(body)
            route.fulfill(status=503,json={'detail':'Synthetic isolated dispatch rejection. No provider was invoked.'})
        else:route.fallback()
    context.route('**/api/media/studio/jobs',rejected)
    button=studio.get_by_role('button',name='Generate image',exact=True);expect(button).to_be_enabled();button.click()
    p.wait(lambda:len(samples)==1,page)
    expect(studio.get_by_role('alert').filter(has_text='Synthetic isolated dispatch rejection')).to_be_visible()
    expect(studio.locator('textarea.studio-prompt')).to_have_value('Synthetic exact direct-submit request; rejected before host dispatch.')
    assert page.get_by_role('dialog',name='Review generation',exact=True).count()==0
    request=samples[0];assert request['project_id']==project['id']and request['acknowledge_billing']is True,request
    assert request['provider_id']=='synthetic-offline-account'and request['model']=='synthetic/still',request
    report['checks'].append({'check':'explicit-Generate-direct-submit-mocked-before-host','theme':theme,'width':width,'requests':samples,'host_dispatches':0,'real_paid_calls':0,'visible_error_and_exact_draft_preserved':True})
    context.unroute('**/api/media/studio/jobs',rejected)


def preview(browser,out,report):
    images={'desktop':'after-image-default-dark-1280.png','phone':'after-video-options-keyboard-light-375.png','music':'after-music-keyboard-focus-light-1280.png','dialog':'after-settings-keyboard-focus-dark-375.png'}
    assert all((out/name).is_file()for name in images.values()),images
    cards={key:'<figure class="'+key+'"><figcaption>'+name+'</figcaption><img src="data:image/png;base64,'+base64.b64encode((out/name).read_bytes()).decode()+'"></figure>'for key,name in images.items()}
    html='<!doctype html><html lang="en"><meta charset="utf-8"><title>Studio composer refinement proof</title><style>body{margin:0;padding:24px;background:#111;color:#eee;font:14px system-ui}h1{font-size:20px;margin:0 0 12px}p{line-height:1.6}main{display:grid;grid-template-columns:1280px 375px;grid-template-areas:"desktop phone" "music dialog";gap:24px}figure{margin:0;min-width:0}figcaption{margin-bottom:8px}.desktop{grid-area:desktop}.phone{grid-area:phone}.music{grid-area:music}.dialog{grid-area:dialog}img{display:block;width:100%;height:auto}</style><h1>Studio composer and contained keyboard focus</h1><p>Actual isolated browser captures with clearly synthetic catalog metadata. No real generation or production profile. Desktop1280px and phone375px displayed at actual width.</p><main>'+cards['desktop']+cards['phone']+cards['music']+cards['dialog']+'</main></html>'
    (out/'preview.html').write_text(html);page=browser.new_page(viewport={'width':1727,'height':1800});page.set_content(html);page.screenshot(path=str(out/'preview.png'),full_page=True);page.close()
    report['preview']={'html':'preview.html','png':'preview.png','originals':images,'method':'Original browser PNGs embedded unchanged in actual browser DOM; actual1280/375 display widths.'}


def run(destination,ui_dist,baseline=False):
    out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'result':'running','mode':'before'if baseline else'after','output':str(out),'ui_dist':str(Path(ui_dist).resolve()),'states':[],'checks':[],'focus':[],'errors':[],'external_requests':[],'forbidden_mutations':[],'backend_requests':[],'failed_requests':[],'http_errors':[],'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fixture_helper_sha256':hashlib.sha256(Path(p.__file__).read_bytes()).hexdigest(),'started_at':datetime.now(timezone.utc).isoformat(),'limitations':['Scratch real FastAPI/password authentication only; synthetic offline catalog, no credentials or generation.','No production-profile, native-window, physical-device or screen-reader validation.','Full-document axe incomplete cases are retained.']}
    try:
        with sync_playwright()as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');report['browser']=browser.version
            cases=((1280,900,'dark'),(1280,900,'light'),(375,812,'dark'),(375,812,'light'))
            chosen=next((arg.split('=',1)[1]for arg in sys.argv if arg.startswith('--case=')),None)
            if chosen:cases=tuple(row for row in cases if str(row[0])+'-'+row[2]==chosen);assert len(cases)==1,chosen
            report['case_filter']=chosen
            for width,height,theme in cases:
                p.phase(report,'case',width=width,theme=theme)
                with p.isolated_host(ui_dist,report)as(state,owner,agent_project,base,scratch):
                    context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<640,reduced_motion='reduce');context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
                    origin=urlsplit(base)
                    def guard(route):
                        request=route.request;url=urlsplit(request.url)
                        if url.scheme in('http','https')and(url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_requests'].append(request.url);route.abort();return
                        if request.method!='GET'and(re.match(r'/api/media/studio/(?:music/)?jobs(?:/|$)',url.path)or re.match(r'/api/(?:agent/tasks|workspace/sessions/[^/]+/turns)(?:/|$)',url.path)):
                            report['forbidden_mutations'].append({'method':request.method,'path':url.path});route.abort();return
                        if url.path=='/graphql'and request.method=='POST'and str((request.post_data_json or{}).get('query','')).lstrip().startswith('mutation'):
                            report['forbidden_mutations'].append({'method':'POST','path':'/graphql mutation'});route.abort();return
                        route.continue_()
                    context.route('**/*',guard);page=context.new_page();page.set_default_timeout(15000)
                    page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    page.on('requestfailed',lambda request:report['failed_requests'].append({'method':request.method,'url':request.url,'failure':request.failure}))
                    page.on('response',lambda response:report['http_errors'].append({'path':urlsplit(response.url).path,'status':response.status})if response.status>=400 else None)
                    try:exercise(page,context,state,owner,agent_project,base,out,report,theme,width,REPO/'desktop/workspace/node_modules/axe-core/axe.min.js',baseline)
                    except BaseException:
                        page.screenshot(path=str(out/'failure.png'),full_page=True);(out/'failure.html').write_text(page.content());raise
                    finally:context.close()
            if not baseline and not chosen:preview(browser,out,report)
            browser.close()
        assert not report['errors']and not report['external_requests']and not report['forbidden_mutations'],report
        report['axe_violation_states']=[{'state':s['state'],'theme':s['theme'],'width':s['width'],'violations':s.get('violations')}for s in report['states']if s.get('violations')]
        if not baseline:assert not report['axe_violation_states'],report['axe_violation_states']
        report['result']='passed'
    except BaseException as error:report.update(result='failed',failure=f'{type(error).__name__}: {error}');raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
def parallel_run(destination,ui_dist):
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False)
    cases=['1280-dark','1280-light','375-dark','375-light']
    def child(case):
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),str(out/case),str(Path(ui_dist).resolve()),'--case='+case],cwd=REPO,capture_output=True,text=True)
        (out/(case+'.log')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,case+' failed; see '+str(out/(case+'.log'))
        report=json.loads((out/case/'report.json').read_text());assert report['result']=='passed';return report
    with ThreadPoolExecutor(max_workers=4)as pool:reports=list(pool.map(child,cases))
    report=dict(reports[0]);report.update(output=str(out),case_filter=None,execution='Four independent fresh-host processes, joined before aggregation; final preview runs after all child browsers/hosts stop.',case_reports=[str(out/case/'report.json')for case in cases])
    for key in('states','checks','focus','errors','external_requests','forbidden_mutations','backend_requests','failed_requests','http_errors','axe_violation_states'):report[key]=[item for child_report in reports for item in child_report[key]]
    for row in reports:assert row['ui_manifest_digest']==report['ui_manifest_digest']and row['runner_sha256']==report['runner_sha256']and row['fixture_helper_sha256']==report['fixture_helper_sha256']
    manifest=json.loads((out/cases[0]/'ui-manifest.json').read_text())
    for case in cases:
        assert json.loads((out/case/'ui-manifest.json').read_text())==manifest
        for item in json.loads((out/case/'report.json').read_text())['states']:
            assert not(out/item['screenshot']).exists();__import__('shutil').copy2(out/case/item['screenshot'],out/item['screenshot'])
    (out/'ui-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    assert not report['errors']and not report['external_requests']and not report['forbidden_mutations']and not report['axe_violation_states']
    with sync_playwright()as pw:
        browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');preview(browser,out,report);browser.close()
    report['finished_at']=datetime.now(timezone.utc).isoformat();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report

if __name__=='__main__':
    result=parallel_run(sys.argv[1],sys.argv[2])if'--parallel'in sys.argv else run(sys.argv[1],sys.argv[2],'--baseline'in sys.argv)
    print(json.dumps({'result':result['result'],'states':len(result['states']),'focus_samples':len(result['focus'])}))
