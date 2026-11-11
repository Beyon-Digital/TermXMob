"""Bounded effort/reference proof: immutable UI bytes, real owned scratch auth/stores.

Usage: .venv/bin/python -B tests/workspace_effort_reference_e2e.py OUTPUT UI_DIST
No production profile, inference, provider testing, control, speech, or native app credentials.
"""
from __future__ import annotations
import hashlib,json,re,sys
from pathlib import Path
from datetime import datetime,timezone
from time import time
from urllib.parse import urlsplit
REPO=Path.cwd();sys.path.insert(0,str(REPO/'tests'))
import workspace_studio_projects_e2e as fixture
from playwright.sync_api import sync_playwright,expect

MODELS=['synthetic-codex-model','synthetic-second-model','synthetic-adaptive-model',*[f'synthetic-{name}-model'for name in('fourth','fifth','sixth','seventh','eighth')]]
LEVELS=['low','medium','high','xhigh','max','ultra']
def option(values):return {'id':'reasoning_effort','name':'Reasoning','category':'thought_level','type':'select','currentValue':values[0],'options':[{'value':v,'name':v.title()}for v in values]}
def seed(state,owner,project):
 from termx.engines.types import EngineDescriptor
 standard=option(LEVELS);adaptive=option(['light','balanced','deep'])
 for identifier in state.engines.engines():
  descriptor=EngineDescriptor(id=identifier,label='Synthetic '+identifier.title(),installed=identifier=='codex',auth_state='not_required',auth_detail='Offline capability receipt',transport='stdio-jsonl').as_dict();descriptor['capabilities']=state.engines.adapter(identifier).capabilities().as_dict()
  config={'models':MODELS,'default_model':MODELS[0],'config_options':[standard],'model_configurations':{model:{'config_options':[adaptive if index==2 else standard]}for index,model in enumerate(MODELS)}}if identifier=='codex'else{}
  state.engines.catalogue.entries[identifier]={'descriptor':descriptor,'configuration':config,'refreshed_at':time(),'stale':False,'refresh_error':None}
 primary=state.workspace.orchestrator.snapshot(owner)['session'];sessions=[]
 for index,title in enumerate(('Synthetic effort reviewer','Synthetic sibling reviewer')):
  row=state.workspace.create_session(owner,title=title,project_id=project['id'],cwd=project['path'],engine='codex',model=MODELS[index],mode='ask',reasoning_config={'reasoning_effort':'ultra'if index==0 else'high'})
  state.workspace.store.create('delegated_session',owner.id,{'parent_session_id':primary['id'],'parent_task_id':None,'task_id':None,'control_owner':'orchestrator','origin':'adopted','engine':'codex','cwd':project['path']},project['id'],row['id']);sessions.append(row)
 return sessions

def rect_inside(box,parent,tolerance=1):return box['x']>=parent['x']-tolerance and box['y']>=parent['y']-tolerance and box['x']+box['width']<=parent['x']+parent['width']+tolerance and box['y']+box['height']<=parent['y']+parent['height']+tolerance

def metrics(scope):
 return scope.evaluate("""e=>{const css=x=>{const s=getComputedStyle(x);return {border:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth],outline:s.outline,outlineWidth:s.outlineWidth,outlineStyle:s.outlineStyle,outlineOffset:s.outlineOffset,boxShadow:s.boxShadow,animation:s.animationName,animationDuration:s.animationDuration,transition:s.transitionDuration,background:s.backgroundImage,backgroundColor:s.backgroundColor,color:s.color,radius:s.borderRadius,overflow:s.overflow}};const box=x=>x?.getBoundingClientRect().toJSON();const track=e.querySelector('.effort-track'),thumb=e.querySelector('.effort-thumb'),range=e.querySelector('.effort-range');return {popup:box(e),css:css(e),focus:{tag:document.activeElement?.tagName,label:document.activeElement?.getAttribute('aria-label'),visible:document.activeElement?.matches(':focus-visible'),inside:e.contains(document.activeElement)},track:box(track),thumb:box(thumb),range:range&&css(range),currentLabelColor:e.querySelector('.effort-current-label')&&getComputedStyle(e.querySelector('.effort-current-label')).color,chipEffortColor:document.querySelector('.composer-model-chip .model-effort-label')&&getComputedStyle(document.querySelector('.composer-model-chip .model-effort-label')).color,menu:e.querySelector('[role=listbox]')&&{clientHeight:e.querySelector('[role=listbox]').clientHeight,scrollHeight:e.querySelector('[role=listbox]').scrollHeight,font:getComputedStyle(e.querySelector('[role=option]')).fontSize,weight:getComputedStyle(e.querySelector('[role=option]')).fontWeight},motionStyles:e.querySelector('.effort-thumb-position')&&css(e.querySelector('.effort-thumb-position')),particles:[...e.querySelectorAll('.effort-particles,.effort-particle,.effort-sparkles')].map(x=>({className:x.getAttribute('class'),box:box(x),css:css(x),trackContains:!!track?.contains(x)})),descendantBorders:[...e.querySelectorAll('button,[role=listbox]')].map(x=>({label:x.getAttribute('aria-label')||x.textContent.trim(),border:css(x).border})),animations:e.getAnimations({subtree:true}).map(x=>({playState:x.playState,iterations:x.effect?.getTiming().iterations,duration:x.effect?.getTiming().duration}))}}""")

def capture(page,out,report,name,theme,width,scope=None,axe=True):
 row={'state':name,'theme':theme,'width':width,'viewport':page.viewport_size,'horizontalOverflow':page.evaluate('document.documentElement.scrollWidth>innerWidth')};assert not row['horizontalOverflow'],row
 if scope:
  row.update(metrics(scope));assert rect_inside(row['popup'],{'x':0,'y':0,**page.viewport_size}),row['popup']
  if axe:
   path=REPO/'desktop/workspace/node_modules/axe-core/axe.min.js'
   if path.is_file():
    if not page.evaluate("typeof axe!=='undefined'"):page.add_script_tag(path=str(path))
    result=scope.evaluate("async e=>await axe.run(e,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
    row['axe']={key:[{'id':v['id'],'impact':v.get('impact'),'nodes':[{'target':n['target'],'summary':n.get('failureSummary')}for n in v['nodes']]}for v in result[key]]for key in('violations','incomplete')}
    assert not row['axe']['violations'],row['axe']['violations']
 filename=f'{name}-{theme}-{width}.png';page.screenshot(path=str(out/filename),full_page=True);row['screenshot']=filename;
 if scope:
  crop=f'{name}-{theme}-{width}-popup.png';scope.screenshot(path=str(out/crop));row['popupScreenshot']=crop
 report['states'].append(row);(out/'partial-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'capture':filename}),flush=True);return row

def borderless(row):
 assert all(float(v.removesuffix('px'))==0 for v in row['css']['border']),row['css']
 assert float(row['css']['outlineWidth'].removesuffix('px'))==0 or row['css']['outlineStyle']=='none',row['css']
 for control in row['descendantBorders']:assert all(float(v.removesuffix('px'))==0 for v in control['border']),control

def normal_popup(page):return page.get_by_role('dialog',name='Model and effort',exact=True)
def open_popup(page,keyboard=False):
 trigger=page.get_by_role('button',name='Model and effort',exact=True)
 if keyboard:trigger.focus();page.keyboard.press('Enter')
 else:trigger.click()
 popup=normal_popup(page);expect(popup).to_be_visible();return trigger,popup

def model_menu(page,popup):
 popup.get_by_role('button',name='Choose model',exact=True).click();menu=popup.get_by_role('listbox',name='Session model',exact=True);expect(menu).to_be_visible();return menu

def authenticate(page,context,state,owner,first,base,width):
 context.add_init_script('localStorage.setItem("termx-coordination-sidebar:'+owner.id+'","closed")')
 target=lambda session:base+'/?session='+session['id']+'&layout=chat&workspace=agent'
 page.goto(target(first));page.get_by_label('Username',exact=True).fill(fixture.USERNAME);page.get_by_label('Password',exact=True).fill(fixture.PASSWORD);page.get_by_role('button',name='Continue with password',exact=True).click();expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible(timeout=60000);expect(page.get_by_role('button',name='Model and effort',exact=True)).to_have_attribute('title',first['model']);expect(page.locator('.orchestrator-child-banner')).to_be_visible();page.locator('.composer').first.evaluate('e=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
 hide=page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Hide sidebar',exact=True)
 if width<640 and hide.is_visible():hide.click()
 return target

def range_checks(page,popup,report,width,theme):
 slider=popup.get_by_role('slider',name='Reasoning',exact=True);expect(slider).to_be_enabled();expect(slider).to_have_value('6');expect(slider).to_have_attribute('aria-valuetext','Ultra')
 expect(popup.locator('.effort-current-label')).to_have_text('Ultra');m=metrics(popup)
 assert abs(m['popup']['width']-256)<=1 and (95<=m['popup']['height']<=97 if width==1280 else m['popup']['height']<=144),m['popup']
 assert abs(m['track']['height']-24)<=1 and abs(m['thumb']['height']-28)<=1 and abs(m['thumb']['width']-28)<=1,m
 assert abs(m['thumb']['y']+14-m['track']['y']-12)<=1,m
 assert rect_inside(m['thumb'],m['popup']),m
 positioned_alignment(page,popup,page.get_by_role('button',name='Model and effort',exact=True))
 assert m['particles'] and all(x['trackContains']and rect_inside(x['box'],m['track'])for x in m['particles']),m['particles']
 assert all(not(x['iterations']==float('inf'))for x in m['animations']),m['animations']
 assert all(x['css']['animation']=='none'for x in m['particles']),m['particles']
 assert not m['focus']['visible'],m['focus'];assert m['range']['outlineStyle']=='none' or m['range']['outlineWidth']=='0px',m['range']
 report['checks'].append({'check':'reference-geometry-contained-static-particles-pointer-focus','theme':theme,'width':width,'metrics':m})
 return slider

def deep_checks(page,context,state,owner,project,first,sibling,target,out,report,gates):
 theme='dark';width=1280;page.reload();expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible();expect(page.get_by_role('button',name='Model and effort',exact=True)).to_have_attribute('title',first['model']);popup=normal_popup(page)
 # Keyboard opening preserves a visible, inset range focus indicator.
 gates['hold_reasoning']=True;trigger,popup=open_popup(page,keyboard=True);fixture.wait(lambda:bool(gates['reasoning']),page);header=popup.get_by_role('button',name='Choose model',exact=True);expect(header).to_be_focused();header_node=header.element_handle();capture(page,out,report,'loading',theme,width,popup,axe=False)
 for route in gates['reasoning']:route.continue_()
 gates['reasoning'].clear();gates['hold_reasoning']=False;slider=popup.get_by_role('slider',name='Reasoning',exact=True);expect(slider).to_be_enabled();assert header_node.evaluate('e=>e.isConnected&&e===document.activeElement'),'Loading completion replaced the keyboard-focused header';
 for _ in range(5):
  if slider.evaluate('e=>e===document.activeElement'):break
  page.keyboard.press('Tab')
 expect(slider).to_be_focused();m=metrics(popup);assert m['focus']['visible']and m['range']['outlineStyle']!='none'and float(m['range']['outlineWidth'].removesuffix('px'))>=2,m
 assert float(m['range']['outlineOffset'].removesuffix('px'))<=0,m['range'];capture(page,out,report,'keyboard-focus',theme,width,popup)
 # Draft endpoints align with native input positions and never save before keyup.
 count=len(report['patches']);samples=[]
 for key,value in(('Home','0'),('End','6'),('ArrowLeft','5')):
  page.keyboard.down(key);expect(slider).to_have_value(value);samples.append({'key':key,'value':value,'metrics':metrics(popup)})
 assert len(report['patches'])==count,report['patches']
 for sample in samples:
  t=sample['metrics']['track'];h=sample['metrics']['thumb'];assert abs(h['y']+h['height']/2-t['y']-t['height']/2)<=1,sample
  if sample['value']=='0':assert abs(h['x']-t['x'])<=1,sample
  if sample['value']=='6':assert abs(h['x']+h['width']-t['x']-t['width'])<=1,sample
 gates['hold_patch']=True;page.keyboard.up('ArrowLeft');fixture.wait(lambda:bool(gates['patches']),page);expect(slider).to_be_disabled();capture(page,out,report,'saving-disabled',theme,width,popup,axe=False)
 for route in gates['patches']:route.continue_()
 gates['patches'].clear();gates['hold_patch']=False;page.keyboard.up('Home');page.keyboard.up('End');fixture.wait(lambda:state.workspace.session(owner,first['id'],turns=False)['reasoning_config'].get('reasoning_effort')=='max',page);expect(slider).to_be_enabled();expect(slider).to_have_attribute('aria-valuetext','Max')
 assert len(report['patches'])==count+1 and report['patches'][-1]['body']['changes']=={'reasoning_config':{'reasoning_effort':'max'}},report['patches']
 report['checks'].append({'check':'keyboard-range-drafts-and-real-authenticated-scratch-save','samples':samples,'patch':report['patches'][-1],'saving_disabled':True})
 # Normal motion is sampled from the actual wrapper/thumb transforms. No loop.
 page.emulate_media(reduced_motion='no-preference');page.keyboard.press('Escape');popup.wait_for(state='detached');trigger.click();expect(popup).to_be_visible();slider=popup.get_by_role('slider',name='Reasoning',exact=True);slider.focus();frames=[]
 page.keyboard.down('Home')
 for index,delay in enumerate((0,40,60,100)):
  if delay:page.wait_for_timeout(delay)
  frames.append({'elapsed_ms':sum((0,40,60,100)[:index+1]),'metrics':metrics(popup)});page.screenshot(path=str(out/f'motion-frame-{index}.png'),full_page=True)
 # Restore draft without committing another actual write.
 page.keyboard.down('End');page.keyboard.down('ArrowLeft');page.keyboard.up('Home');page.keyboard.up('End');page.keyboard.up('ArrowLeft');expect(slider).to_be_enabled()
 assert max(x['metrics']['thumb']['x']for x in frames)-min(x['metrics']['thumb']['x']for x in frames)>1,frames
 assert not any(a['iterations']==float('inf')for x in frames for a in x['metrics']['animations']),frames
 page.emulate_media(reduced_motion='reduce');reduced=metrics(popup);assert reduced['motionStyles']['transition']in('0s','0s, 0s')and not reduced['animations'],reduced
 report['checks'].append({'check':'normal-motion-sampled-and-reduced-motion-static','frames':frames,'reduced':reduced})
 # Pointer model view has real rows/checks and nested Escape returns to effort.
 menu=model_menu(page,popup);expect(menu.get_by_role('option',selected=True)).to_contain_text(first['model']);assert menu.locator('select,input').count()==0
 current=menu.get_by_role('option',selected=True);assert current.locator('svg').count()>=1,current.inner_html()
 page.keyboard.press('Escape');expect(menu).not_to_be_visible();expect(slider).to_be_visible();page.keyboard.press('Escape');popup.wait_for(state='detached');expect(trigger).to_be_focused()
 # Keyboard model rows activate a catalogue-advertised model and reload its three effort values.
 _,popup=open_popup(page,keyboard=True);popup.get_by_role('button',name='Choose model',exact=True).focus();page.keyboard.press('Enter');menu=popup.get_by_role('listbox',name='Session model',exact=True);expect(menu).to_be_visible();page.keyboard.press('Home');first_focus=page.evaluate('document.activeElement?.textContent');page.keyboard.press('End');last_focus=page.evaluate('document.activeElement?.textContent');assert 'Default'in first_focus and MODELS[-1]in last_focus,(first_focus,last_focus)
 page.keyboard.press('Home');page.keyboard.press('ArrowDown');page.keyboard.press('ArrowDown');page.keyboard.press('ArrowDown');page.keyboard.press('Enter');fixture.wait(lambda:state.workspace.session(owner,first['id'],turns=False)['model']==MODELS[2],page);expect(popup.get_by_role('slider',name='Reasoning',exact=True)).to_have_attribute('max','3');expect(popup.locator('.effort-current-label')).to_have_text('Default');capture(page,out,report,'dynamic-model-catalogue',theme,width,popup)
 model_patch=next(x for x in report['patches']if x['body']['changes'].get('model')==MODELS[2]);assert model_patch['body']['changes']=={'model':MODELS[2],'reasoning_config':{}},model_patch
 menu=model_menu(page,popup);expect(menu.get_by_role('option',selected=True)).to_contain_text(MODELS[2]);page.keyboard.press('Escape');page.keyboard.press('Escape');popup.wait_for(state='detached')
 report['checks'].append({'check':'keyboard-model-list-real-save-current-check-dynamic-catalogue','Home_focus':first_focus,'End_focus':last_focus,'loading_header_identity_and_focus_preserved':True,'patch':model_patch,'advertised_effort_max':3})
 # Visibility, sibling and outside-pointer dismissal do not leak a stale selection.
 trigger,popup=open_popup(page);hidden=target(first).replace('workspace=agent','workspace=studio');page.evaluate('url=>{history.pushState({},"",url);dispatchEvent(new PopStateEvent("popstate"))}',hidden);expect(popup).not_to_be_visible();page.evaluate('url=>{history.pushState({},"",url);dispatchEvent(new PopStateEvent("popstate"))}',target(first));expect(trigger).to_be_visible();expect(popup).not_to_be_visible();trigger.click();expect(popup).to_be_visible();page.evaluate('url=>{history.pushState({},"",url);dispatchEvent(new PopStateEvent("popstate"))}',target(sibling));expect(popup).not_to_be_visible();trigger,popup=open_popup(page);expect(popup.locator('.effort-current-label')).to_have_text('High');expect(popup.get_by_role('button',name='Choose model',exact=True)).to_contain_text(sibling['model']);page.get_by_role('textbox',name='Message',exact=True).click();expect(popup).not_to_be_visible()
 report['checks'].append({'check':'workspace-visibility-sibling-outside-dismissal','closed_on_hide':True,'closed_after_resume':True,'fresh_sibling_model':sibling['model'],'fresh_sibling_effort':'High'})
 state_checks(page,state,owner,project,sibling,target,out,report,gates)

def state_checks(page,state,owner,project,session,target,out,report,gates):
 entry=state.engines.catalogue.entries['codex'];original=json.loads(json.dumps(entry['configuration']))
 entry['stale']=True;entry['refresh_error']='Synthetic offline catalogue refresh failure'
 page.reload();expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible();expect(page.get_by_role('button',name='Model and effort',exact=True)).to_have_attribute('title',session['model']);_,popup=open_popup(page);expect(popup.get_by_role('status')).to_contain_text(re.compile('unavailable|could not|failed',re.I));assert popup.get_by_role('slider').count()==0;capture(page,out,report,'catalogue-error','dark',1280,popup);page.keyboard.press('Escape');popup.wait_for(state='detached')
 entry['stale']=False;entry['refresh_error']=None;entry['configuration']={**original,'config_options':[],'model_configurations':{model:{'config_options':[]}for model in MODELS}}
 page.reload();expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible();expect(page.get_by_role('button',name='Model and effort',exact=True)).to_have_attribute('title',session['model']);_,popup=open_popup(page);expect(popup.locator('.effort-range-unavailable')).to_be_visible();expect(popup.get_by_role('button',name='Reset effort',exact=True)).to_be_disabled();assert popup.get_by_role('slider').count()==0;capture(page,out,report,'effort-unavailable','dark',1280,popup);page.keyboard.press('Escape');popup.wait_for(state='detached');entry['configuration']=original
 # A stored running task exercises the genuine running UI without starting any process.
 from termx.agent.limits import DEFAULT_LIMITS
 state.agent_store.put_provider('offline-running-fixture',kind='openai-compatible',name='Synthetic offline running receipt',base_url='http://127.0.0.1:1/v1',model='synthetic-reply',capabilities=['chat'],secret_configured=False)
 task=state.agent_store.create_task(prompt='Synthetic running-state receipt; no dispatch.',cwd=project['path'],provider_id='offline-running-fixture',model='synthetic-reply',limits=DEFAULT_LIMITS,mode='ask',status='running')
 try:
  state.workspace.store.create('task',owner.id,{'conversation_id':session['id'],'cwd':project['path'],'execution_location':project['path'],'parent_id':None},project['id'],task['id']);state.authorization.claim_principal(owner,'task',task['id'],project_id=project['id']);state.agent_store.add_conversation_turn(session['id'],prompt='Synthetic running-state receipt; no dispatch.',task_id=task['id'],mode='ask',provider_id='offline-running-fixture',model='synthetic-reply')
  page.reload();expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible();expect(page.get_by_role('button',name='Model and effort',exact=True)).to_have_attribute('title',session['model']);_,popup=open_popup(page);expect(popup.get_by_role('slider')).to_be_disabled();menu=model_menu(page,popup);assert all(menu.get_by_role('option').nth(i).is_disabled()for i in range(menu.get_by_role('option').count()));capture(page,out,report,'running-disabled','dark',1280,popup);page.keyboard.press('Escape');page.keyboard.press('Escape')
 finally:state.agent_store.update_task(task['id'],status='completed');state.agent_store.delete_task(task['id']);state.workspace.store.delete('task',task['id'])
 report['checks'].append({'check':'distinct-loading-catalogue-error-unavailable-saving-and-running-disabled','loading':'held real authenticated GET','catalogue_error':'real backend stale/refresh_error receipt','unavailable':'real backend empty options receipt','saving':'held real authenticated PATCH then committed','running':'owned stored running receipt; no process or dispatch'})

def exercise(page,context,state,owner,project,base,out,report,theme,width,gates):
 first,sibling=seed(state,owner,project);target=authenticate(page,context,state,owner,first,base,width);trigger=page.get_by_role('button',name='Model and effort',exact=True);before={'trigger':trigger.bounding_box(),'composer':page.locator('.composer').first.bounding_box(),'text':trigger.inner_text()};trigger,popup=open_popup(page);expect(trigger).to_have_text('Select effort');after={'trigger':trigger.bounding_box(),'composer':page.locator('.composer').first.bounding_box(),'text':trigger.inner_text()};assert before['composer']==after['composer'],(before,after);report['checks'].append({'check':'compact-context-trigger-composer-stable','theme':theme,'width':width,'before':before,'after':after});slider=range_checks(page,popup,report,width,theme);page.mouse.move(100,100);row=capture(page,out,report,'effort-open',theme,width,popup);borderless(row)
 menu=model_menu(page,popup);expect(trigger).to_contain_text(first['model']);expect(trigger).to_contain_text('Ultra');expect(popup).to_contain_text('Select model');expect(menu).to_contain_text('Recommended set of models');expect(menu.get_by_role('option',selected=True)).to_contain_text(first['model']);assert menu.locator('select,input').count()==0;assert menu.get_by_role('option',selected=True).locator('svg').count()>=1
 page.mouse.move(100,100);row=capture(page,out,report,'model-menu',theme,width,popup);borderless(row);assert row['chipEffortColor']==row['currentLabelColor'],(row['chipEffortColor'],row['currentLabelColor']);
 if width==1280:assert row['menu']['scrollHeight']<=row['menu']['clientHeight']+1,row['menu']
 assert abs(row['popup']['width']-256)<=1;assert not row['focus']['visible'],row['focus'];page.keyboard.press('Escape');expect(menu).not_to_be_visible();page.keyboard.press('Escape');popup.wait_for(state='detached')
 if width==1280 and theme=='dark':deep_checks(page,context,state,owner,project,first,sibling,target,out,report,gates)
 assert not state.agent_store.list_tasks()

def run(destination,ui_dist):
 out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False)
 report={'result':'running','output':str(out),'ui_dist':str(Path(ui_dist).resolve()),'states':[],'checks':[],'errors':[],'external_requests':[],'forbidden_mutations':[],'patches':[],'audio_calls':[],'backend_requests':[],'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fixture_helper_sha256':hashlib.sha256(Path(fixture.__file__).read_bytes()).hexdigest(),'source_sha256':{str(path.relative_to(REPO)):hashlib.sha256(path.read_bytes()).hexdigest()for path in[REPO/'desktop/workspace/src/features/SessionModelControls.tsx',REPO/'desktop/workspace/src/features/ReasoningPicker.tsx',REPO/'desktop/workspace/src/features/session-model-controls.css']},'started_at':datetime.now(timezone.utc).isoformat(),'limits':['Bounded reference popup/browser proof; physical device, screen reader, native rendered popup and full-app accessibility conformance are not established here.','Real owned scratch hosts/auth/stores and immutable compiled UI assets; synthetic offline engine catalogue. No inference, provider testing, control, speech, production profile or test credentials in native app.']}
 try:
  with sync_playwright()as pw:
   browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');report['browser']=browser.version
   try:
    for width,height,theme in((1280,900,'dark'),(1280,900,'light'),(375,812,'dark'),(375,812,'light')):
     with fixture.isolated_host(ui_dist,report)as(state,owner,project,base,scratch):
      context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<640,reduced_motion='reduce');context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')');context.add_init_script("window.__qaAudioCalls=[];if(window.speechSynthesis)window.speechSynthesis.speak=()=>{window.__qaAudioCalls.push('speechSynthesis.speak');throw Error('No speech in effort proof')};HTMLMediaElement.prototype.play=function(){window.__qaAudioCalls.push('media.play');return Promise.reject(Error('No audio in effort proof'))}")
      gates={'hold_reasoning':False,'hold_patch':False,'reasoning':[],'patches':[]};origin=urlsplit(base)
      def guard(route):
       request=route.request;url=urlsplit(request.url)
       if url.scheme in('http','https')and(url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_requests'].append(request.url);route.abort();return
       if request.method=='GET'and url.path.endswith('/reasoning')and gates['hold_reasoning']:gates['reasoning'].append(route);return
       if request.method not in('GET','HEAD')and url.path!='/auth/login':
        data=request.post_data_json or{}
        if url.path=='/graphql'and not str(data.get('query','')).lstrip().startswith('mutation'):route.continue_();return
        if request.method=='PATCH'and re.fullmatch('/api/workspace/sessions/[^/]+',url.path)and set(data.get('changes',{}))in({'reasoning_config'},{'model','reasoning_config'}):
         report['patches'].append({'path':url.path,'body':data})
         if gates['hold_patch']:gates['patches'].append(route);return
         route.continue_();return
        report['forbidden_mutations'].append({'method':request.method,'path':url.path});route.abort();return
       route.continue_()
      context.route('**/*',guard);page=context.new_page();page.set_default_timeout(10000);page.on('pageerror',lambda error:report['errors'].append(str(error)))
      try:exercise(page,context,state,owner,project,base,out,report,theme,width,gates)
      except BaseException:
       page.screenshot(path=str(out/'failure.png'),full_page=True);(out/'failure.html').write_text(page.content());raise
      finally:
       calls=page.evaluate('window.__qaAudioCalls||[]');report['audio_calls'].extend(calls)
       for pending in gates['reasoning']+gates['patches']:
        try:pending.abort()
        except Exception:pass
       context.close();assert not calls,calls
   finally:browser.close()
  assert not any(report[key]for key in('errors','external_requests','forbidden_mutations','audio_calls','backend_requests')),report
  report['cleanup']={'scratch_hosts_stopped':4,'browser_closed':True,'fixture_assertions':'Each server thread joined, local listener and mock client closed, temporary config/profile removed.'}
  assert len(report['states'])>=14 and len(report['patches'])==2,(len(report['states']),report['patches']);report['result']='passed';report['exit_status']=0
 except BaseException as error:report.update(result='failed',exit_status=1,failure=f'{type(error).__name__}: {error}');raise
 finally:report['finished_at']=datetime.now(timezone.utc).isoformat();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 return report

def positioned_alignment(page,popup,trigger):
 value=popup.evaluate('''(e,t)=>{const p=e.getBoundingClientRect(),b=t.getBoundingClientRect(),width=Math.min(256,innerWidth-32),bottom=Math.max(8,Math.min(innerHeight-88,innerHeight-b.top+8)),left=Math.max(16,Math.min(b.right-width,innerWidth-width-16));return {popup:p.toJSON(),trigger:b.toJSON(),viewport:{width:innerWidth,height:innerHeight},expected:{left,bottom},gap:b.top-p.bottom,rightDelta:p.right-b.right,horizontalClamped:Math.abs(left-(b.right-width))>.5}}''',trigger.element_handle())
 assert abs(value['popup']['x']-value['expected']['left'])<=1 and abs(value['viewport']['height']-value['popup']['bottom']-value['expected']['bottom'])<=1,value
 assert value['popup']['x']>=15 and value['popup']['right']<=value['viewport']['width']-15 and value['popup']['y']>=-1 and value['popup']['bottom']<=value['viewport']['height']+1,value
 if not value['horizontalClamped']:assert abs(value['rightDelta'])<=1,value
 if value['expected']['bottom']<value['viewport']['height']-88:assert abs(value['gap']-8)<=1,value
 return value

def wait_positioned(page,popup,trigger):
 def ready():
  try:positioned_alignment(page,popup,trigger);return True
  except AssertionError:return False
 fixture.wait(ready,page,seconds=5);return positioned_alignment(page,popup,trigger)

def positioned_run(destination,ui_dist):
 import difflib
 prior=REPO/'docs/evidence/effort-reference-refinement/final-v2-complete/report.json'
 if not prior.is_file():
  raise FileNotFoundError('--position-only requires the optional historical final-v2-complete/report.json, which is not shipped. Run without --position-only for current-source checks.')
 out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False);previous=json.loads(prior.read_text());source_paths=[REPO/path for path in previous['source_sha256']];source_hashes={str(path.relative_to(REPO)):hashlib.sha256(path.read_bytes()).hexdigest()for path in source_paths}
 control=REPO/'desktop/workspace/src/features/SessionModelControls.tsx';after=control.read_text();before=after.replace('const anchor=trigger.current;','const anchor=trigger.current?.closest<HTMLElement>(\'.composer\')??trigger.current;').replace('bounds.right-width','bounds.right-12-width');assert hashlib.sha256(before.encode()).hexdigest()==previous['source_sha256'][str(control.relative_to(REPO))],'The source change is not only the two approved anchor expressions'
 for path,digest in previous['source_sha256'].items():
  if path!=str(control.relative_to(REPO)):assert source_hashes[path]==digest,(path,source_hashes[path],digest)
 (out/'source-placement.diff').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='v2/SessionModelControls.tsx',tofile='v3/SessionModelControls.tsx')))
 report={'result':'running','verification_scope':'Placement-only v3 proof. Prior full functional v2 proof retained.','prior_functional_report':str(prior),'prior_functional_ui_manifest_digest':previous['ui_manifest_digest'],'source_diff_verified_placement_only':True,'source_sha256':source_hashes,'prior_source_sha256':previous['source_sha256'],'output':str(out),'ui_dist':str(Path(ui_dist).resolve()),'states':[],'checks':[],'errors':[],'external_requests':[],'forbidden_mutations':[],'patches':[],'audio_calls':[],'backend_requests':[],'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fixture_helper_sha256':hashlib.sha256(Path(fixture.__file__).read_bytes()).hexdigest(),'started_at':datetime.now(timezone.utc).isoformat(),'limits':['Real authenticated owned scratch hosts and immutable compiled candidate; synthetic offline engine catalogue. No production profile, inference, provider testing, control, speech, paid calls or native app test credentials.','Eight bounded placement captures; functional keyboard/save/motion/state proof is final-v2-complete, whose non-placement implementation bytes match this candidate. Physical-device, screen-reader, full-app accessibility, and native rendering remain separate.']}
 try:
  with sync_playwright()as pw:
   browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');report['browser']=browser.version
   try:
    for width,height,theme in((1280,900,'dark'),(1280,900,'light'),(375,812,'dark'),(375,812,'light')):
     with fixture.isolated_host(ui_dist,report)as(state,owner,project,base,scratch):
      first,_=seed(state,owner,project);context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<640,reduced_motion='reduce');context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')');context.add_init_script("window.__qaAudioCalls=[];if(window.speechSynthesis)window.speechSynthesis.speak=()=>{window.__qaAudioCalls.push('speech');throw Error('No speech in position proof')};HTMLMediaElement.prototype.play=function(){window.__qaAudioCalls.push('media');return Promise.reject(Error('No audio in position proof'))}")
      origin=urlsplit(base)
      def guard(route):
       request=route.request;url=urlsplit(request.url)
       if url.scheme in('http','https')and(url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_requests'].append(request.url);route.abort();return
       if request.method not in('GET','HEAD')and url.path!='/auth/login':
        data=request.post_data_json or{}
        if url.path!='/graphql'or str(data.get('query','')).lstrip().startswith('mutation'):report['forbidden_mutations'].append({'method':request.method,'path':url.path});route.abort();return
       route.continue_()
      context.route('**/*',guard);page=context.new_page();page.set_default_timeout(10000);page.on('pageerror',lambda error:report['errors'].append(str(error)))
      try:
       authenticate(page,context,state,owner,first,base,width);trigger=page.get_by_role('button',name='Model and effort',exact=True);composer=page.locator('.composer').first;original_composer=composer.bounding_box();trigger,popup=open_popup(page);expect(popup.get_by_role('slider',name='Reasoning',exact=True)).to_be_enabled();effort=wait_positioned(page,popup,trigger);assert composer.bounding_box()==original_composer,(original_composer,composer.bounding_box());page.mouse.move(100,100);row=capture(page,out,report,'effort-open',theme,width,popup);row['alignment']=effort;borderless(row)
       menu=model_menu(page,popup);model=wait_positioned(page,popup,trigger);assert composer.bounding_box()==original_composer,(original_composer,composer.bounding_box());expect(menu.get_by_role('option',selected=True)).to_contain_text(first['model']);assert menu.locator('select,input').count()==0;page.mouse.move(100,100);row=capture(page,out,report,'model-menu',theme,width,popup);row['alignment']=model;borderless(row);assert row['chipEffortColor']==row['currentLabelColor'];report['checks'].append({'check':'actual-button-anchored-effort-and-model-with-viewport-clamp-and-stable-composer','theme':theme,'width':width,'effort':effort,'model':model,'composer':original_composer})
       if width==1280 and theme=='dark':
        resize=[];scroll={}
        for new_width,new_height in((960,720),(480,400)):
         page.set_viewport_size({'width':new_width,'height':new_height});expect(trigger).to_be_visible();resize.append(wait_positioned(page,popup,trigger))
        menu_box=menu.bounding_box();scroll_before=menu.evaluate('e=>({top:e.scrollTop,client:e.clientHeight,total:e.scrollHeight})')
        if scroll_before['total']>scroll_before['client']+1:
         page.mouse.move(menu_box['x']+menu_box['width']/2,menu_box['y']+menu_box['height']/2);page.mouse.wheel(0,120);fixture.wait(lambda:menu.evaluate('e=>e.scrollTop')>scroll_before['top'],page,seconds=5);scroll={'performed':True,'before':scroll_before,'after':menu.evaluate('e=>({top:e.scrollTop,client:e.clientHeight,total:e.scrollHeight})'),'alignment':wait_positioned(page,popup,trigger)}
        else:scroll={'performed':False,'reason':'The empty fixture and model list have no scroll overflow at the resized viewport.','metrics':scroll_before}
        page.set_viewport_size({'width':width,'height':height});resize.append(wait_positioned(page,popup,trigger));report['checks'].append({'check':'live-trigger-anchor-after-resize-and-real-wheel-scroll','resize':resize,'scroll':scroll})
       page.keyboard.press('Escape');expect(menu).not_to_be_visible();page.keyboard.press('Escape');popup.wait_for(state='detached')
      except BaseException:
       page.screenshot(path=str(out/'failure.png'),full_page=True);(out/'failure.html').write_text(page.content());raise
      finally:
       calls=page.evaluate('window.__qaAudioCalls||[]');report['audio_calls'].extend(calls);context.close();assert not calls,calls
   finally:browser.close()
  assert not any(report[key]for key in('errors','external_requests','forbidden_mutations','audio_calls','backend_requests','patches')),report
  assert len(report['states'])==8 and len(report['checks'])==5,(len(report['states']),len(report['checks']));report.update(result='passed',exit_status=0,cleanup={'scratch_hosts_stopped':4,'browser_closed':True,'temporary_profiles_removed':True})
 except BaseException as error:report.update(result='failed',exit_status=1,failure=f'{type(error).__name__}: {error}');raise
 finally:report['finished_at']=datetime.now(timezone.utc).isoformat();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 return report

if __name__=='__main__':
 result=(positioned_run if '--position-only'in sys.argv[3:]else run)(sys.argv[1],sys.argv[2]);print(json.dumps({'result':result['result'],'states':len(result['states']),'checks':len(result['checks']),'exit_status':result['exit_status']}))
