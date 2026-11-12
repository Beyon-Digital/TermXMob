"""Bounded Agent specs/effort proof on isolated auth and immutable UI bytes."""
from __future__ import annotations
import base64,hashlib,json,os,re,sys
from pathlib import Path
from datetime import datetime,timezone
from time import time
from urllib.parse import urlsplit
REPO=Path.cwd();sys.path.insert(0,str(REPO/'tests'))
import workspace_studio_projects_e2e as fixture
from playwright.sync_api import sync_playwright,expect

OLD_COPY=('Take control to direct this agent yourself. Its progress still returns to your main chat.','This existing chat is included in updates. You stay in control until you give the main agent control.','The main agent can see progress, but waits for you to return control before steering this chat.')

def seed(state,owner,project):
    from termx.engines.types import EngineDescriptor
    levels=['low','medium','high','xhigh','max','ultra'];models=['synthetic-codex-model','synthetic-second-model']
    option={'id':'reasoning_effort','name':'Reasoning','category':'thought_level','type':'select','currentValue':'medium','options':[{'value':v,'name':v.title()}for v in levels]}
    for identifier in state.engines.engines():
        descriptor=EngineDescriptor(id=identifier,label='Synthetic '+identifier.title(),installed=identifier=='codex',auth_state='not_required',auth_detail='Offline capability receipt',transport='stdio-jsonl').as_dict()
        descriptor['capabilities']=state.engines.adapter(identifier).capabilities().as_dict()
        config={'models':models,'default_model':models[0],'config_options':[option],'model_configurations':{model:{'config_options':[option]}for model in models}}if identifier=='codex'else{}
        state.engines.catalogue.entries[identifier]={'descriptor':descriptor,'configuration':config,'refreshed_at':time(),'stale':False,'refresh_error':None}
    primary=state.workspace.orchestrator.snapshot(owner)['session'];children=[]
    for index,title in enumerate(('Synthetic delegated reviewer','Synthetic sibling reviewer')):
        row=state.workspace.create_session(owner,title=title,project_id=project['id'],cwd=project['path'],engine='codex',model=models[index],mode='ask',reasoning_config={'reasoning_effort':'ultra'if index==0 else'high'})
        state.workspace.store.create('delegated_session',owner.id,{'parent_session_id':primary['id'],'parent_task_id':None,'task_id':None,'control_owner':'orchestrator','origin':'adopted','engine':'codex','cwd':project['path']},project['id'],row['id']);children.append(row)
    snapshot=state.workspace.orchestrator.snapshot(owner)
    assert len(snapshot['children'])==2 and not state.agent_store.list_tasks()
    return children,snapshot

def facts_from_snapshot(child):
    limits=child['capability_limits'];labels={'native':'Native engine support','gateway':'TermX gateway','os':'Operating system','advisory':'Advisory only','unsupported':'Unavailable','unverified':'Not verified'}
    display=lambda value:labels.get(value,value.replace('_',' '))
    facts={'Engine':'Codex'}
    if limits.get('time_budget'):facts['Time allowance']='Shared host time budget'if limits['time_budget']=='shared-host'else'Host monitoring; existing task limits are retained'if limits['time_budget']=='host-monitoring'else display(limits['time_budget'])
    if limits.get('tool_steps'):facts['Work allowance']='Shared host tool-step budget'if limits['tool_steps']=='shared-host'else'Native turn limit; individual tool calls are not counted'if limits['tool_steps']=='native-max-turns'else'Tool steps are not measured by TermX'if limits['tool_steps']=='unmeasured'else display(limits['tool_steps'])
    for key,label in(('tokens','Token usage'),('cost','Cost')):
        if limits.get(key):facts[label]='Not measured or capped by TermX'if limits[key]=='unmeasured'else display(limits[key])
    for key,label in(('steer','Steering'),('resume','Resume'),('approvals','Approvals'),('tools_filter','Tool restrictions')):
        if limits.get(key):facts[label]=display(limits[key])
    deadline=limits.get('elapsed_deadline_seconds')
    if deadline:facts['Elapsed-time safety ceiling']=(str(deadline//3600)+' hours'if deadline%3600==0 else str(deadline)+' seconds')+' while the main agent is in control'
    return facts

def capture(page,out,report,name,theme,width,scope=None):
    metrics=page.evaluate("()=>({viewport:{width:innerWidth,height:innerHeight},horizontalOverflow:document.documentElement.scrollWidth>innerWidth,focus:{tag:document.activeElement?.tagName,name:document.activeElement?.getAttribute('aria-label')}})")
    assert not metrics['horizontalOverflow'],metrics
    row={'state':name,'theme':theme,'width':width,**metrics}
    if scope:
        box=scope.bounding_box();assert box and box['x']>=-1 and box['y']>=-1 and box['x']+box['width']<=width+1 and box['y']+box['height']<=page.viewport_size['height']+1,box
        row['popup']=box
        axe=REPO/'desktop/workspace/node_modules/axe-core/axe.min.js'
        if axe.is_file():
            if not page.evaluate("typeof axe!=='undefined'"):page.add_script_tag(path=str(axe))
            result=scope.evaluate("async e=>await axe.run(e,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
            row['axe']={key:[{'id':x['id'],'targets':[n['target']for n in x['nodes']]}for x in result[key]]for key in('violations','incomplete')}
    filename=f'{name}-{theme}-{width}.png';page.screenshot(path=str(out/filename),full_page=True);row['screenshot']=filename;report['states'].append(row)
    (out/'partial-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'capture':filename}),flush=True)


def open_specs(page,child):
    banner=page.locator('.orchestrator-child-banner');expect(banner).to_be_visible()
    trigger=banner.get_by_role('button',name='Engine details for '+child['title'],exact=True);trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Engine capabilities & limits',exact=True);expect(dialog).to_be_visible();expect(dialog.get_by_role('button',name='Close',exact=True)).to_be_focused();return banner,trigger,dialog

def exercise(page,context,state,owner,project,base,out,report,theme,width):
    sessions,snapshot=seed(state,owner,project);first=sessions[0];child=next(row for row in snapshot['children']if row['session_id']==first['id']);expected=facts_from_snapshot(child)
    context.add_init_script('localStorage.setItem("termx-coordination-sidebar:'+owner.id+'","closed")')
    target=lambda session:base+'/?session='+session['id']+'&layout=chat&workspace=agent'
    page.goto(target(first));page.get_by_label('Username',exact=True).fill(fixture.USERNAME);page.get_by_label('Password',exact=True).fill(fixture.PASSWORD);page.get_by_role('button',name='Continue with password',exact=True).click()
    expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible(timeout=60000)
    hide=page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Hide sidebar',exact=True)
    if width<640 and hide.is_visible():hide.click()
    banner=page.locator('.orchestrator-child-banner');expect(banner.get_by_role('button',name='Take control',exact=True)).to_be_visible()
    assert all(copy not in banner.inner_text()for copy in OLD_COPY)
    assert banner.locator('details,dl,.orchestrator-engine-specs').count()==0
    assert banner.locator(':scope > p:not(.orchestrator-engine-label)').count()==0
    capture(page,out,report,'specs-closed',theme,width)
    banner,trigger,dialog=open_specs(page,first)
    expect(dialog).to_contain_text(first['title']);dl=dialog.get_by_label('Engine specifications',exact=True)
    actual=dl.evaluate("e=>Object.fromEntries([...e.querySelectorAll('dt')].map(dt=>[dt.textContent.trim(),dt.nextElementSibling?.textContent.trim()]))")
    assert actual==expected,(actual,expected)
    assert banner.locator('dl').count()==0
    for key in('Tab','Tab','Shift+Tab'):
        page.keyboard.press(key);assert dialog.evaluate('e=>e.contains(document.activeElement)')
    dl.focus();before=dl.evaluate('e=>({scroll:e.scrollTop,client:e.clientHeight,total:e.scrollHeight})');page.keyboard.press('End');after=dl.evaluate('e=>e.scrollTop')
    if before['total']>before['client']+1:
        fixture.wait(lambda:dl.evaluate('e=>e.scrollTop')>before['scroll'],page,seconds=3);after=dl.evaluate('e=>e.scrollTop')
    dl.evaluate('e=>e.scrollTop=0')
    capture(page,out,report,'specs-open',theme,width,dialog)
    page.keyboard.press('Escape');dialog.wait_for(state='detached');expect(trigger).to_be_focused()
    report['checks'].append({'check':'banner-modal-values-keyboard-scroll','theme':theme,'width':width,'expected':expected,'actual':actual,'scroll':before,'after_End':after,'Escape_return':True,'initial_focus':'Close','removed_paragraphs':True,'inline_specs':False})
    # Effort is bounded to this same owned idle native session; never send a turn.
    effort_check(page,context,state,owner,first,sessions[1],target,out,report,theme,width)
    if width==1280 and theme=='dark':
        page.goto(target(first));expect(page.locator('.orchestrator-child-banner')).to_be_visible();_,_,dialog=open_specs(page,first)
        page.evaluate('url=>{history.pushState({},"",url);dispatchEvent(new PopStateEvent("popstate"))}',target(sessions[1]))
        expect(page.locator('.orchestrator-child-banner').get_by_role('button',name='Engine details for '+sessions[1]['title'],exact=True)).to_be_visible();expect(dialog).not_to_be_visible()
        _,sibling_trigger,sibling=open_specs(page,sessions[1]);expect(sibling).to_contain_text(sessions[1]['title']);assert first['title']not in sibling.inner_text();page.keyboard.press('Escape');sibling.wait_for(state='detached');expect(sibling_trigger).to_be_focused()
        report['checks'].append({'check':'actual-popstate-sibling-closes-stale-specs','closed_before_reopen':True,'fresh_child_title':sessions[1]['title']})
    if(width==1280 and theme=='dark')or(width==375 and theme=='light'):read_icon_check(page,state,owner,project,sessions[0],target,out,report,theme,width)
    assert {row['id']for row in state.agent_store.list_tasks()}==set(getattr(state,'_qa_read_tasks',[]))


def read_icon_check(page,state,owner,project,session,target,out,report,theme,width):
    from termx.agent.limits import DEFAULT_LIMITS
    state.agent_store.put_provider('offline-read-fixture',kind='openai-compatible',name='Synthetic offline reply',base_url='http://127.0.0.1:1/v1',model='synthetic-reply',capabilities=['chat'],secret_configured=False)
    task=state.agent_store.create_task(prompt='Show the compact response controls.',cwd=project['path'],provider_id='offline-read-fixture',model='synthetic-reply',limits=DEFAULT_LIMITS,mode='ask',status='completed')
    state.agent_store.update_task(task['id'],result='An offline reply verifies the compact response controls.')
    state.workspace.store.create('task',owner.id,{'conversation_id':session['id'],'cwd':project['path'],'execution_location':project['path'],'parent_id':None},project['id'],task['id']);state.authorization.claim_principal(owner,'task',task['id'],project_id=project['id'])
    state.agent_store.add_conversation_turn(session['id'],prompt='Show the compact response controls.',task_id=task['id'],mode='ask',provider_id='offline-read-fixture',model='synthetic-reply');state._qa_read_tasks=[task['id']]
    page.goto(target(session));expect(page.get_by_role('log')).to_contain_text('An offline reply verifies the compact response controls.')
    bar=page.get_by_role('group',name='Response actions',exact=True);expect(bar).to_be_visible()
    read=bar.get_by_role('button',name='Read aloud',exact=True);copy=bar.get_by_role('button',name='Copy answer',exact=True);expect(read).to_be_visible();expect(copy).to_be_visible()
    value=read.evaluate('e=>({text:e.textContent,svg:e.querySelectorAll("svg").length,label:e.getAttribute("aria-label"),title:e.title,classes:e.className,disabled:e.disabled,parent:e.parentElement.getAttribute("aria-label")})')
    assert not value['text'].strip()and value['svg']>=1 and value['label']=='Read aloud'and value['title']and value['parent']=='Response actions',value
    assert 'button-icon'in value['classes']and'button-ghost'in value['classes'],value
    assert bar.get_by_role('button',name='Stop reading',exact=True).count()==0
    capture(page,out,report,'read-icon',theme,width)
    report['checks'].append({'check':'icon-only-Read-next-to-Copy-with-accessible-tooltip','theme':theme,'width':width,'actual':value,'Read_or_audio_action_clicked':False,'owned_offline_task':task['id']})


def effort_check(page,context,state,owner,first,sibling,target,out,report,theme,width):
    trigger=page.get_by_role('button',name='Model and effort',exact=True);trigger.focus();page.keyboard.press('Enter')
    popup=page.get_by_role('dialog',name='Model and effort',exact=True);expect(popup).to_be_visible()
    slider=popup.get_by_role('slider',name='Reasoning',exact=True);expect(slider).to_be_enabled();expect(slider).to_have_value('6');expect(slider).to_have_attribute('aria-valuetext','Ultra')
    expect(popup.locator('.effort-current-label')).to_have_text('Ultra');expect(popup.get_by_role('button',name='Choose model',exact=True)).to_contain_text(first['model']);expect(popup.get_by_role('button',name='Reset effort',exact=True)).to_be_visible()
    assert popup.locator('.reasoning-scale,p.control-description,.model-connection-notice').count()==0
    box=popup.bounding_box();assert abs(box['width']-256)<=2 and box['height']<=150,box
    gradient=popup.locator('.effort-fill').evaluate('e=>getComputedStyle(e).backgroundImage');assert 'linear-gradient'in gradient,gradient
    capture(page,out,report,'effort-open',theme,width,popup)
    patch_before=len(report['effort_patches']);samples=[];boundaries=[]
    if width==1280 and theme=='dark':
        page.emulate_media(reduced_motion='no-preference');slider.focus()
        # Rehearse native range boundaries as drafts, then commit the final
        # advertised position once on ArrowLeft keyup. No turns are submitted.
        page.keyboard.down('Home');expect(slider).to_have_value('0');boundaries.append({'key':'Home','value':slider.input_value()})
        page.keyboard.down('End');expect(slider).to_have_value('6');boundaries.append({'key':'End','value':slider.input_value()})
        page.wait_for_timeout(180);fill=popup.locator('.effort-fill');samples.append(fill.bounding_box()['width'])
        page.keyboard.down('ArrowLeft');expect(slider).to_have_value('5');boundaries.append({'key':'ArrowLeft','value':slider.input_value()})
        for delay in(0,50,150):
            if delay:page.wait_for_timeout(delay)
            samples.append(fill.bounding_box()['width'])
        assert max(samples)-min(samples)>1,samples
        assert len(report['effort_patches'])==patch_before,'Draft keydowns dispatched before explicit keyup'
        page.keyboard.up('ArrowLeft');page.keyboard.up('Home');page.keyboard.up('End')
        fixture.wait(lambda:state.workspace.session(owner,first['id'],turns=False)['reasoning_config'].get('reasoning_effort')=='max',page)
        expect(slider).to_be_enabled();expect(slider).to_have_attribute('aria-valuetext','Max')
        assert len(report['effort_patches'])==patch_before+1,report['effort_patches']
        body=report['effort_patches'][-1]['body'];assert body['changes']=={'reasoning_config':{'reasoning_effort':'max'}},body
        page.emulate_media(reduced_motion='reduce')
    reduced=popup.locator('.effort-fill').evaluate('e=>({transition:getComputedStyle(e).transitionDuration,animation:getComputedStyle(e).animationDuration})')
    assert all(float(value.rstrip('s'))==0 for value in reduced['transition'].split(',')),reduced
    page.keyboard.press('Escape');popup.wait_for(state='detached');expect(trigger).to_be_focused()
    report['checks'].append({'check':'compact-effort-read-keyboard-and-motion','theme':theme,'width':width,'popup':box,'gradient':gradient,'boundaries':boundaries,'normal_motion_fill_width_samples':samples,'reduced_motion':reduced,'actual_patches':len(report['effort_patches'])-patch_before,'Escape_return':True})
    if width==1280 and theme=='dark':
        trigger.click();expect(popup).to_be_visible()
        hidden=target(first).replace('workspace=agent','workspace=studio')
        page.evaluate('url=>{history.pushState({},"",url);dispatchEvent(new PopStateEvent("popstate"))}',hidden)
        expect(popup).not_to_be_visible()
        page.evaluate('url=>{history.pushState({},"",url);dispatchEvent(new PopStateEvent("popstate"))}',target(first))
        expect(trigger).to_be_visible();expect(popup).not_to_be_visible()
        trigger.click();expect(popup).to_be_visible()
        page.evaluate('url=>{history.pushState({},"",url);dispatchEvent(new PopStateEvent("popstate"))}',target(sibling))
        expect(page.locator('.orchestrator-child-banner').get_by_role('button',name='Engine details for '+sibling['title'],exact=True)).to_be_visible();expect(popup).not_to_be_visible()
        fresh=page.get_by_role('button',name='Model and effort',exact=True);fresh.click();expect(popup).to_be_visible();expect(popup.locator('.effort-current-label')).to_have_text('High');expect(popup.get_by_role('button',name='Choose model',exact=True)).to_contain_text(sibling['model']);page.keyboard.press('Escape');popup.wait_for(state='detached')
        report['checks'].append({'check':'effort-hidden-and-session-scope-close','workspace_hidden_unmount':True,'resume_stays_closed':True,'sibling_reopen_uses_actual_model_and_High_effort':True,'unexpected_patches':len(report['effort_patches'])-patch_before-1})


def run(destination,ui_dist):
    out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'result':'running','output':str(out),'ui_dist':str(Path(ui_dist).resolve()),'states':[],'checks':[],'errors':[],'external_requests':[],'forbidden_mutations':[],'effort_patches':[],'audio_calls':[],'backend_requests':[],'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fixture_helper_sha256':hashlib.sha256(Path(fixture.__file__).read_bytes()).hexdigest(),'started_at':datetime.now(timezone.utc).isoformat(),'limits':['Bounded popup/browser proof only, not physical-device, screen-reader or full-app accessibility conformance.','Real scratch host/auth/stores, synthetic offline engine catalogue; no inference/native engine dispatch or production profile.']}
    try:
        with sync_playwright()as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');report['browser']=browser.version
            for width,height,theme in((1280,900,'dark'),(1280,900,'light'),(375,812,'dark'),(375,812,'light')):
                with fixture.isolated_host(ui_dist,report)as(state,owner,project,base,scratch):
                    context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<640,reduced_motion='reduce');context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
                    context.add_init_script("window.__qaAudioCalls=[];if(window.speechSynthesis)window.speechSynthesis.speak=()=>{window.__qaAudioCalls.push('speechSynthesis.speak');throw Error('No speech in isolated UI proof')};HTMLMediaElement.prototype.play=function(){window.__qaAudioCalls.push('media.play');return Promise.reject(Error('No audio in isolated UI proof'))}")
                    origin=urlsplit(base)
                    def guard(route):
                        request=route.request;url=urlsplit(request.url)
                        if url.scheme in('http','https')and(url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_requests'].append(request.url);route.abort();return
                        if request.method not in('GET','HEAD')and url.path!='/auth/login':
                            query=(request.post_data_json or{}).get('query','')if url.path=='/graphql'else''
                            if url.path=='/graphql'and not str(query).lstrip().startswith('mutation'):route.continue_();return
                            data=request.post_data_json or{}
                            if request.method=='PATCH'and re.fullmatch('/api/workspace/sessions/[^/]+',url.path)and set(data.get('changes',{}))=={'reasoning_config'}:
                                report['effort_patches'].append({'path':url.path,'body':data});route.continue_();return
                            report['forbidden_mutations'].append({'method':request.method,'path':url.path});route.abort();return
                        route.continue_()
                    context.route('**/*',guard);page=context.new_page();page.set_default_timeout(10000);page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    try:exercise(page,context,state,owner,project,base,out,report,theme,width)
                    except BaseException:
                        page.screenshot(path=str(out/'failure.png'),full_page=True);(out/'failure.html').write_text(page.content());raise
                    finally:
                        calls=page.evaluate('window.__qaAudioCalls||[]');report['audio_calls'].extend(calls);context.close()
                        for identifier in getattr(state,'_qa_read_tasks',[]):state.agent_store.delete_task(identifier);state.workspace.store.delete('task',identifier)
                        assert not calls,calls
            browser.close()
        assert not report['errors']and not report['external_requests']and not report['forbidden_mutations'],report
        assert len(report['states'])==14
        assert len(report['effort_patches'])==1,report['effort_patches']
        report['result']='passed'
    except BaseException as error:report.update(result='failed',failure=f'{type(error).__name__}: {error}');raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
if __name__=='__main__':
    result=run(sys.argv[1],sys.argv[2]);print(json.dumps({'result':result['result'],'states':len(result['states']),'checks':len(result['checks'])}))
