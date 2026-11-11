"""Bounded Projects/control-plane proof; real scratch auth with synthetic offline receipts.
Run from repo: .venv/bin/python -B tests/workspace_projects_controlplane_e2e.py OUTPUT UI_DIST [--baseline]
No production profile, provider credentials, native window, terminal or inference dispatch.
"""
from __future__ import annotations
import base64,hashlib,json,os,re,sys
import httpx
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs
REPO=Path.cwd();sys.path.insert(0,str(REPO/'tests'))
import workspace_studio_projects_e2e as fixture
from playwright.sync_api import sync_playwright,expect


def seed(state,owner,project,scratch):
    from termx.agent.limits import DEFAULT_LIMITS
    projects=[project]
    for name,relative in [('TermX release automation','release/desktop/release-candidate-with-a-long-folder-name/quality-and-delivery'),('TermX documentation','docs'),('TermX empty project','empty'),('Generated working copy','scratch/'+'a'*32+'/'+'b'*32)]:
        path=scratch/relative;path.mkdir(parents=True,exist_ok=True);projects.append(state.projects.register(str(path),name=name))
    state.agent_store.put_provider('offline-projects-qa',kind='openai-compatible',name='Synthetic offline QA',base_url='http://127.0.0.1:1/v1',model='synthetic-review',capabilities=['chat'],secret_configured=False)
    receipts=[];sessions={}
    for index,(status,title) in enumerate([('running','Review desktop release readiness'),('failed','Recover the interrupted package build'),('completed','Verify project navigation and preserve local work'),('awaiting_approval','Review the pending workspace change')]):
        p=projects[index%3];session=state.workspace.create_session(owner,title=title,project_id=p['id'],cwd=p['path'],provider_id='offline-projects-qa',model='synthetic-review',mode='ask');sessions[status]=session
        task=state.agent_store.create_task(prompt='Synthetic offline receipt: '+title,cwd=p['path'],provider_id='offline-projects-qa',model='synthetic-review',limits=DEFAULT_LIMITS,mode='ask',status='completed')
        state.agent_store.update_task(task['id'],status=status,result='Synthetic local fixture. No model or tool executed.' if status=='completed' else None,error='Synthetic offline build failure. No command executed.' if status=='failed' else None)
        state.workspace.store.create('task',owner.id,{'conversation_id':session['id'],'cwd':p['path'],'execution_location':p['path'],'parent_id':None},p['id'],task['id']);state.authorization.claim_principal(owner,'task',task['id'],project_id=p['id'])
        state.agent_store.add_conversation_turn(session['id'],prompt='Synthetic offline receipt: '+title,task_id=task['id'],mode='ask',provider_id='offline-projects-qa',model='synthetic-review');receipts.append(task['id'])
    return projects,sessions,receipts


def visible(page):
    nav=page.get_by_role('navigation',name='Workspace navigation',exact=True)
    hide=nav.get_by_role('button',name='Hide sidebar',exact=True)
    if page.viewport_size['width']<640 and hide.is_visible():hide.click()


def audit(page,out,report,name,theme,width):
    page.wait_for_timeout(40)
    metrics=page.evaluate('''()=>{const visible=e=>{const r=e.getBoundingClientRect();return r.width&&r.height&&!e.closest('[inert]')},q=s=>document.querySelector(s),rect=e=>e?.getBoundingClientRect().toJSON();return{viewport:{width:innerWidth,height:innerHeight},documentOverflow:document.documentElement.scrollWidth>innerWidth,focus:{tag:document.activeElement?.tagName,name:document.activeElement?.getAttribute('aria-label'),outline:getComputedStyle(document.activeElement).outline},panel:rect(q('.control-plane')),inspector:rect(q('.control-inspector')),table:q('.control-table-wrap')?{rect:rect(q('.control-table-wrap')),scroll:q('.control-table-wrap').scrollWidth,client:q('.control-table-wrap').clientWidth}:null,dialogs:[...document.querySelectorAll('[role=dialog]')].filter(visible).map(e=>({name:e.getAttribute('aria-labelledby')?q('#'+e.getAttribute('aria-labelledby'))?.textContent:'',rect:rect(e),containsFocus:e.contains(document.activeElement)})),buttons:[...document.querySelectorAll('.control-plane button,[role=dialog] button')].filter(visible).map(e=>({name:e.getAttribute('aria-label')||e.textContent.trim(),rect:rect(e),font:getComputedStyle(e).fontSize,radius:getComputedStyle(e).borderRadius,primary:e.classList.contains('button-primary')})),controlBorder:q('.control-plane')?getComputedStyle(q('.control-plane')).borderWidth:null,themeTokens:{workspace:document.documentElement.dataset.appWorkspace,text:getComputedStyle(document.documentElement).getPropertyValue('--text'),border:getComputedStyle(document.documentElement).getPropertyValue('--border')},horizontalScrollContainers:[...document.querySelectorAll('.control-plane *,[role=dialog] *')].filter(visible).filter(e=>['auto','scroll'].includes(getComputedStyle(e).overflowX)&&e.scrollWidth>e.clientWidth+1).map(e=>({tag:e.tagName,class:e.className,scroll:e.scrollWidth,client:e.clientWidth})),borders:[...document.querySelectorAll('.control-plane,.control-project-row,.control-project-group,.control-activity-row,.dialog-content,.workspace-command-menu,.session-model-popover')].filter(visible).map(e=>({class:e.className,width:getComputedStyle(e).borderWidth,style:getComputedStyle(e).borderStyle,color:getComputedStyle(e).borderColor,shadow:getComputedStyle(e).boxShadow})),overflowing:[...document.querySelectorAll('.control-plane *,[role=dialog] *')].filter(visible).filter(e=>{const r=e.getBoundingClientRect();return r.x<0||r.right>innerWidth}).slice(0,20).map(e=>({tag:e.tagName,class:e.className,text:e.textContent.slice(0,60),rect:rect(e)}))}}''')
    row={'state':name,'theme':theme,'width':width,**metrics}
    axe=REPO/'desktop/workspace/node_modules/axe-core/axe.min.js'
    if axe.is_file():
        if not page.evaluate("typeof axe!=='undefined'"):page.add_script_tag(path=str(axe))
        result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
        row['axe']={key:[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result[key]]for key in('violations','incomplete')}
    filename=f'{report["mode"]}-{name}-{theme}-{width}.png';page.screenshot(path=str(out/filename),full_page=True);row['screenshot']=filename;report['states'].append(row)
    panel=page.locator('.control-plane')
    if panel.is_visible():panel.screenshot(path=str(out/f'{report["mode"]}-{name}-crop-{theme}-{width}.png'))
    (out/'partial-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'capture':filename}),flush=True)
    if report['mode']=='after':
        assert not metrics['documentOverflow'],metrics
        assert not row.get('axe',{}).get('violations'),row.get('axe')
        assert not metrics['horizontalScrollContainers'],metrics['horizontalScrollContainers']
        for dialog in metrics['dialogs']:
            box=dialog['rect'];assert box['x']>=-1 and box['y']>=-1 and box['right']<=width+1 and box['bottom']<=page.viewport_size['height']+1,box
            assert dialog['containsFocus'],dialog
    return row


def open_page(page,base,name):
    page.evaluate('url=>{history.pushState(null,"",url);dispatchEvent(new PopStateEvent("popstate"))}',base+'/manage/'+name+'?workspace=agent')
    expect(page.get_by_role('region',name='Projects workspace',exact=True)).to_be_visible(timeout=60000)
    visible(page)


def keyboard_modal(page,dialog):
    expect(dialog).to_be_visible()
    assert dialog.evaluate('e=>e.contains(document.activeElement)'), 'Modal did not receive focus'
    for key in ('Tab','Tab','Shift+Tab','Shift+Tab','Tab'):
        page.keyboard.press(key)
        assert dialog.evaluate('e=>e.contains(document.activeElement)'), 'Modal focus escaped'


def modal_close(page,dialog,trigger,key='Escape'):
    if key=='Escape':page.keyboard.press('Escape')
    else:dialog.get_by_role('button',name='Close',exact=True).click()
    dialog.wait_for(state='detached')
    expect(trigger).to_be_focused(timeout=3000)


def pending_project(page,context,scratch,out,report,theme,width):
    trigger=page.get_by_role('region',name='Projects workspace',exact=True).get_by_role('button',name='Add project',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Add project',exact=True)
    path=dialog.get_by_role('textbox',name='Folder path',exact=True)
    name=dialog.locator('input[name="project-name"]')
    draft=str(scratch/'synthetic-pending-project');path.fill(draft);name.fill('Synthetic pending project')
    pending=[]
    def hold(route):
        if route.request.method=='POST'and 'register_project'in(route.request.post_data or''):pending.append(route)
        else:route.fallback()
    context.route('**/graphql',hold)
    dialog.get_by_role('button',name='Add project',exact=True).click();fixture.wait(lambda:bool(pending),page)
    expect(dialog.get_by_role('button',name='Adding…',exact=True)).to_be_disabled()
    expect(path).to_be_disabled();expect(name).to_be_disabled()
    page.keyboard.press('Escape');expect(dialog).to_be_visible()
    close=dialog.get_by_role('button',name='Close',exact=True)
    if close.count():expect(close).to_be_disabled()
    page.mouse.click(2,2);expect(dialog).to_be_visible()
    # Keep at least one focusable control in the pending modal.
    assert dialog.evaluate('e=>e.contains(document.activeElement)'), 'Pending modal lost focus'
    audit(page,out,report,'add-project-pending',theme,width)
    pending.pop().fulfill(status=503,json={'detail':'Synthetic project service unavailable. Retry the saved draft.'})
    expect(dialog.get_by_role('alert')).to_contain_text('Synthetic project service unavailable')
    expect(path).to_have_value(draft);expect(name).to_have_value('Synthetic pending project')
    expect(path).to_be_enabled();expect(dialog.get_by_role('button',name='Add project',exact=True)).to_be_enabled()
    audit(page,out,report,'add-project-error',theme,width)
    modal_close(page,dialog,trigger)
    context.unroute('**/graphql',hold)
    report['checks'].append({'check':'pending-registration-protection-and-error-draft','theme':theme,'width':width,'Escape_and_outside_blocked':True,'close_disabled':True,'host_registration_calls':0})


def auth_boundaries(state,owner,project,sessions,base,report):
    # Explicit independent local identities, not browser test-auth shortcuts.
    assert httpx.get(base+'/api/workspace/sessions').status_code==401
    other=state.identity.create_local_user('synthetic-project-viewer',fixture.PASSWORD,['agent-view','files-read'])
    state.authorization.set_role(other.id,'viewer')
    login=httpx.post(base+'/auth/login',json={'method':'local-password','username':'synthetic-project-viewer','password':fixture.PASSWORD,'transport':'bearer','device_name':'Synthetic read boundary probe'})
    assert login.status_code==200
    with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+login.json()['access_token']})as api:
        listing=api.get('/api/workspace/sessions');assert listing.status_code==200 and not listing.json()['sessions'],listing.text
        denied=api.get('/api/workspace/sessions/'+sessions['failed']['id']);assert denied.status_code in(403,404),denied.text
        catalogue=api.post('/graphql',json={'query':'query{projects{id name path}}'});assert catalogue.status_code==200 and catalogue.json().get('data',{}).get('projects')==[],catalogue.text
        state.identity.set_scopes(other.id,['files-read'])
        assert api.get('/api/workspace/sessions').status_code==403
    report['checks'].append({'check':'real-auth-owner-resource-and-live-scope-boundaries','anonymous':401,'other_identity_session_collection_empty':True,'other_identity_project_catalog_empty':True,'foreign_session_read':denied.status_code,'revoked_agent_view':403})


def register_scratch_project(page,context,state,scratch,out,report,theme,width):
    folder=scratch/'recoverable-registration';folder.mkdir();(folder/'qa-read-only.txt').write_text('Synthetic project registration receipt. No terminal or agent execution.\n')
    state._qa_allowed_project_paths={str(folder)}
    panel=page.get_by_role('region',name='Projects workspace',exact=True)
    panel.get_by_role('button',name='Add project',exact=True).click()
    dialog=page.get_by_role('dialog',name='Add project',exact=True)
    dialog.get_by_role('textbox',name='Folder path',exact=True).fill(str(folder))
    dialog.locator('input[name="project-name"]').fill('Synthetic registered folder')
    dialog.get_by_role('button',name='Add project',exact=True).click()
    dialog.wait_for(state='detached')
    workbench=page.get_by_role('region',name='Code workbench',exact=True)
    expect(workbench).to_be_visible(timeout=15000)
    expect(workbench.locator('.explorer-root')).to_have_text('Synthetic registered folder')
    page.wait_for_timeout(500)
    expect(workbench).to_be_visible();expect(workbench.locator('.explorer-root')).to_have_text('Synthetic registered folder')
    assert not panel.is_visible()
    registered=next(p for p in state.projects.projects()if p['path']==str(folder.resolve()))
    assert registered['name']=='Synthetic registered folder'
    assert(folder/'qa-read-only.txt').read_text()=='Synthetic project registration receipt. No terminal or agent execution.\n'
    assert parse_qs(urlsplit(page.url).query).get('project')==[registered['id']],page.url
    page.reload();expect(workbench).to_be_visible(timeout=15000)
    expect(workbench.locator('.explorer-root')).to_have_text('Synthetic registered folder')
    audit(page,out,report,'registered-project-workbench',theme,width)
    report['checks'].append({'check':'actual-scratch-registration-opens-correct-workbench','theme':theme,'width':width,'registered_project_id':registered['id'],'name':registered['name'],'url_path':urlsplit(page.url).path,'url_query':urlsplit(page.url).query,'stayed_after_500ms':True,'source_file_unchanged':True})
    state._qa_allowed_project_paths=set()


def configuration_failure_probe(page,context,out,report,theme,width):
    def fail_configuration(route):
        query=(route.request.post_data_json or{}).get('query','')
        if 'engines{'in query and 'agent_providers'in query:
            route.fulfill(status=503,json={'detail':'Synthetic configuration catalogue unavailable'})
        else:route.fallback()
    context.route('**/graphql',fail_configuration)
    panel=page.get_by_role('region',name='Projects workspace',exact=True)
    panel.get_by_role('button',name='Refresh',exact=True).click()
    toast=page.locator('.error-toast');expect(toast).to_contain_text('Synthetic configuration catalogue unavailable')
    expect(panel.get_by_role('button',name='Synthetic Agent folder',exact=True)).to_be_visible()
    expect(panel.get_by_role('searchbox',name='Search projects',exact=True)).to_be_enabled()
    audit(page,out,report,'configuration-unavailable-projects-usable',theme,width)
    toast.get_by_role('button',name='Dismiss',exact=True).click()
    context.unroute('**/graphql',fail_configuration)
    refresh=panel.get_by_role('button',name='Refresh',exact=True);expect(refresh).to_be_enabled();refresh.click()
    expect(refresh).to_be_enabled()
    report['checks'].append({'check':'configuration-failure-preserves-project-catalogue-and-search','theme':theme,'width':width,'fixture':'Synthetic503 for engine/provider read only; real project read stays healthy','project_catalogue_preserved':True})


def menu_probe(page,base,out,report,theme,width):
    trigger=page.get_by_role('button',name='Workspace actions',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    menu=page.get_by_role('dialog',name='Workspace actions',exact=True)
    expect(menu).to_be_visible()
    for key in('ArrowDown','End','Home','ArrowUp'):
        page.keyboard.press(key);assert menu.evaluate('e=>e.contains(document.activeElement)')
    page.keyboard.press('Home')
    audit(page,out,report,'workspace-actions-menu',theme,width)
    box=menu.bounding_box();assert box and box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=page.viewport_size['height']+1,box
    page.keyboard.press('Escape');menu.wait_for(state='detached');expect(trigger).to_be_focused()
    trigger.click();expect(menu).to_be_visible()
    menu.get_by_role('button',name='Activity',exact=True).click()
    expect(menu).not_to_be_visible();expect(page.get_by_role('heading',name='Activity',exact=True)).to_be_visible()
    page.go_back();expect(page.get_by_role('heading',name='Projects',exact=True)).to_be_visible()
    report['checks'].append({'check':'Workspace-actions-anchored-keyboard-Escape-and-actual-navigation','theme':theme,'width':width,'bounds':box,'Escape_returns_trigger':True,'actual_Activity_button_used':True})


def models_probe(page,state,owner,base,out,report,theme,width):
    main=state.workspace.orchestrator.snapshot(owner)['session']
    target=base+'/?session='+main['id']+'&layout=chat&workspace=agent'
    page.goto(target);expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible(timeout=15000);visible(page)
    trigger=page.get_by_role('button',name='Model and effort',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    popup=page.get_by_role('dialog',name='Model and effort',exact=True)
    expect(popup).to_be_visible()
    expect(popup.get_by_role('listbox',name='Session model',exact=True)).to_have_count(1)
    expect(popup.get_by_role('option')).to_have_count(0)
    expect(popup.get_by_role('button',name='Manage connections',exact=True)).to_be_visible()
    assert popup.get_by_role('slider').count()==0
    audit(page,out,report,'main-models-no-configured-account',theme,width)
    page.keyboard.press('Escape');popup.wait_for(state='detached');expect(trigger).to_be_focused()
    # Provider flags/catalogues below are explicit offline fixture metadata; no secret is stored.
    accounts=[('synthetic-catalogue-account','Synthetic catalogue account','',{'synthetic/catalogue-model':{}}),('synthetic-model-account','Synthetic explicit account','synthetic/explicit-model',{})]
    for identifier,name,models,catalogue in accounts:
        state.agent_store.put_provider(identifier,kind='openai-compatible',name=name,base_url='http://127.0.0.1:1/v1',model=models,capabilities=['chat'],secret_configured=True)
        if catalogue:state.agent_store.set_provider_catalog(identifier,catalogue)
    page.reload();expect(trigger).to_be_visible(timeout=15000);trigger.click()
    expect(popup.get_by_role('listbox',name='Session model',exact=True)).to_be_visible()
    for label in('synthetic/catalogue-model · Synthetic catalogue account','synthetic/explicit-model · Synthetic explicit account'):
        expect(popup.get_by_role('option',name=label,exact=True)).to_be_visible()
    assert popup.get_by_role('slider').count()==0
    audit(page,out,report,'main-models-explicit-accounts',theme,width)
    page.keyboard.press('Escape');popup.wait_for(state='detached');expect(trigger).to_be_focused()
    current=state.workspace.session(owner,main['id'],turns=False)
    assert not current.get('provider_id')and not current.get('model'),current
    state.workspace.update_session(owner,main['id'],revision=current['revision'],changes={'provider_id':'synthetic-catalogue-account','model':'synthetic/catalogue-model','reasoning_config':{}})
    page.reload();expect(trigger).to_be_visible(timeout=15000);trigger.click()
    expect(popup.get_by_role('option',name='synthetic/catalogue-model',exact=True)).to_be_visible()
    expect(popup.get_by_role('button',name='Choose account',exact=True)).to_contain_text('Synthetic catalogue account')
    audit(page,out,report,'main-models-catalogue-fallback',theme,width)
    page.evaluate('url=>{history.pushState(null,"",url);dispatchEvent(new PopStateEvent("popstate"))}',base+'/?workspace=studio')
    expect(popup).not_to_be_visible()
    report['checks'].append({'check':'main-internal-models-direct-read-only-and-scope-close','theme':theme,'width':width,'no_account_recovery':True,'unbound_exact_provider_labels':True,'catalogue_only_bound_model_visible':True,'model_choice_clicks':0,'browser_model_patch_calls':0,'provider_flags_synthetic_no_credentials':True})


def motion_probe(page,report):
    page.emulate_media(reduced_motion='no-preference')
    trigger=page.get_by_role('button',name='Inspect Recover the interrupted package build',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Recover the interrupted package build',exact=True)
    expect(dialog).to_be_visible()
    normal=dialog.evaluate('e=>({animation:getComputedStyle(e).animationName,duration:getComputedStyle(e).animationDuration,keyframes:e.getAnimations().flatMap(a=>a.effect?.getKeyframes()||[])})')
    assert normal['animation']!='none',normal
    assert all(0.12<=float(t.rstrip('s'))<=0.2 for t in normal['duration'].split(',')),normal
    modal_close(page,dialog,trigger)
    page.emulate_media(reduced_motion='reduce');trigger.click();expect(dialog).to_be_visible()
    reduced=dialog.evaluate('e=>({animation:getComputedStyle(e).animationName,transition:getComputedStyle(e).transitionDuration})')
    assert reduced['animation']=='none',reduced
    modal_close(page,dialog,trigger)
    report['checks'].append({'check':'activity-modal-motion','normal':normal,'reduced':reduced})


def exercise(page,context,state,owner,project,projects,sessions,base,scratch,out,report,theme,width):
    page.goto(base+'/manage/projects?workspace=agent')
    page.get_by_label('Username',exact=True).fill(fixture.USERNAME)
    page.get_by_label('Password',exact=True).fill(fixture.PASSWORD)
    page.get_by_role('button',name='Continue with password',exact=True).click()
    expect(page.get_by_role('heading',name='Projects',exact=True)).to_be_visible(timeout=60000)
    visible(page)
    panel=page.get_by_role('region',name='Projects workspace',exact=True)
    views=page.get_by_role('navigation',name='Workspace views',exact=True)
    for label in('Projects','Activity','Overview'):expect(views.get_by_role('button',name=label,exact=True)).to_be_visible()
    # Revealing/collapsing navigation must preserve the page, including on touch.
    rail=page.get_by_role('navigation',name='Workspace navigation',exact=True)
    rail.get_by_role('button',name='Show sidebar',exact=True).click()
    expect(page.get_by_role('heading',name='Projects',exact=True)).to_be_visible()
    assert urlsplit(page.url).path=='/manage/projects'
    rail.get_by_role('button',name='Hide sidebar',exact=True).click()
    report['checks'].append({'check':'sidebar-preserves-current-projects-route','theme':theme,'width':width})
    # Generated scratch/worktree folder disclosures are read filters only.
    generated=panel.get_by_role('button',name='Generated working copy',exact=True)
    expect(generated).to_have_count(0)
    show=panel.get_by_role('button',name='Show workspaces',exact=True);show.click();expect(generated).to_be_visible()
    panel.get_by_role('button',name='Hide workspaces',exact=True).click();expect(generated).to_have_count(0)
    audit(page,out,report,'projects',theme,width)
    trigger=panel.get_by_role('button',name=project['name'],exact=True)
    trigger.focus();page.keyboard.press('Enter');expect(trigger).to_have_attribute('aria-expanded','true')
    audit(page,out,report,'projects-expanded',theme,width)
    search=panel.get_by_role('searchbox',name='Search projects',exact=True)
    search.fill('Generated working copy');expect(generated).to_be_visible()
    search.fill('unmatched-synthetic-project');expect(panel.get_by_role('button',name='Clear search',exact=True)).to_be_visible()
    audit(page,out,report,'projects-no-results',theme,width)
    panel.get_by_role('button',name='Clear search',exact=True).click();expect(search).to_have_value('')
    create=panel.get_by_role('button',name='Add project',exact=True)
    page.keyboard.press('Tab');create.focus()
    focus=create.evaluate('e=>({outline:getComputedStyle(e).outline,width:getComputedStyle(e).outlineWidth,offset:getComputedStyle(e).outlineOffset,rect:e.getBoundingClientRect().toJSON(),font:getComputedStyle(e).fontSize,style:getComputedStyle(e).outlineStyle})')
    assert float(focus['width'].rstrip('px'))>=2 and focus['style']!='none',focus
    if width<640:assert focus['rect']['height']>=44,focus
    report['checks'].append({'check':'Add-project-keyboard-focus-and-target','theme':theme,'width':width,'actual':focus})
    page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Add project',exact=True)
    keyboard_modal(page,dialog);audit(page,out,report,'add-project',theme,width)
    path=dialog.get_by_role('textbox',name='Folder path',exact=True)
    path.fill('relative/folder');dialog.get_by_role('button',name='Add project',exact=True).click()
    expect(dialog.get_by_role('alert')).to_be_visible();audit(page,out,report,'add-project-invalid',theme,width)
    modal_close(page,dialog,create)
    report['checks'].append({'check':'project-dialog-keyboard-validation','theme':theme,'width':width,'contained_focus':True,'Escape_return_to_trigger':True,'registration_calls':0})
    if(width==1280 and theme=='dark')or(width==375 and theme=='light'):
        pending_project(page,context,scratch,out,report,theme,width)
    # Existing project opens instead of silently renaming or registering it.
    create.click();dialog=page.get_by_role('dialog',name='Add project',exact=True)
    dialog.get_by_role('textbox',name='Folder path',exact=True).fill(project['path'])
    expect(dialog.get_by_role('button',name='Open project',exact=True)).to_be_visible()
    modal_close(page,dialog,create)
    views.get_by_role('button',name='Overview',exact=True).click()
    expect(page.get_by_role('heading',name='Overview',exact=True)).to_be_visible()
    audit(page,out,report,'overview',theme,width)
    views.get_by_role('button',name='Activity',exact=True).click()
    expect(page.get_by_role('heading',name='Activity',exact=True)).to_be_visible()
    audit(page,out,report,'activity',theme,width)
    target=page.get_by_role('button',name='Inspect Recover the interrupted package build',exact=True)
    target.focus();page.keyboard.press('Enter')
    inspector=page.get_by_role('dialog',name='Recover the interrupted package build',exact=True)
    keyboard_modal(page,inspector);expect(inspector).to_contain_text('Synthetic offline build failure')
    content=inspector.get_by_role('region',name='Activity details content',exact=True)
    content.focus()
    scroll_metrics=content.evaluate('e=>({client:e.clientHeight,total:e.scrollHeight,top:e.scrollTop,outline:getComputedStyle(e).outline,style:getComputedStyle(e).outlineStyle,offset:getComputedStyle(e).outlineOffset})')
    assert scroll_metrics['style']!='none',scroll_metrics
    page.keyboard.press('End')
    if scroll_metrics['total']>scroll_metrics['client']+1:fixture.wait(lambda:content.evaluate('e=>e.scrollTop')>scroll_metrics['top'],page,seconds=3)
    scroll_metrics['after_End']=content.evaluate('e=>e.scrollTop')
    page.keyboard.press('Home');fixture.wait(lambda:content.evaluate('e=>e.scrollTop')==0,page,seconds=3)
    inspector.get_by_role('button',name='Close',exact=True).focus()
    report['checks'].append({'check':'Activity-details-keyboard-scroll-and-inset-focus','theme':theme,'width':width,'actual':scroll_metrics})
    audit(page,out,report,'activity-details',theme,width)
    scroll=inspector.evaluate('e=>({client:e.clientHeight,total:e.scrollHeight})')
    if scroll['total']>scroll['client']+1:
        inspector.evaluate('e=>e.scrollTop=e.scrollHeight')
        expect(inspector.get_by_role('button',name='Open chat',exact=True)).to_be_in_viewport()
    modal_close(page,inspector,target,key='Close')
    # Filters are available without hover; keyboard selection remains meaningful.
    status=page.get_by_role('combobox',name='Filter activity by status',exact=True)
    status.select_option('completed');expect(target).to_have_count(0)
    expect(page.get_by_role('button',name='Inspect Verify project navigation and preserve local work',exact=True)).to_be_visible()
    status.select_option('all')
    project_filter=page.get_by_role('combobox',name='Filter activity by project',exact=True)
    project_filter.select_option(projects[1]['id']);expect(target).to_be_visible()
    expect(page.get_by_role('button',name='Inspect Review desktop release readiness',exact=True)).to_have_count(0)
    project_filter.select_option('')
    query=page.get_by_role('searchbox',name='Search activity',exact=True)
    query.fill('unmatched-synthetic-run');expect(target).to_have_count(0)
    audit(page,out,report,'activity-no-results',theme,width);query.fill('')
    target.focus();page.keyboard.press('Enter');expect(inspector).to_be_visible()
    inspector.get_by_role('button',name='Open chat',exact=True).click()
    expect(panel).not_to_be_visible();expect(page.get_by_role('textbox',name='Message',exact=True)).to_be_visible()
    opened=page.url;assert sessions['failed']['id'] in opened,opened
    page.go_back();expect(page.get_by_role('heading',name='Activity',exact=True)).to_be_visible()
    expect(page.get_by_role('dialog')).to_have_count(0)
    report['checks'].append({'check':'inspect-open-exact-session-browser-back','theme':theme,'width':width,'session':sessions['failed']['id'],'opened_url':urlsplit(opened).path,'back_path':urlsplit(page.url).path,'Close_returns_focus':True})
    if width==1280 and theme=='dark':motion_probe(page,report)
    views.get_by_role('button',name='Projects',exact=True).click()
    empty=page.get_by_role('button',name='TermX empty project',exact=True);empty.click()
    expect(panel).to_contain_text('No conversations in this project yet.')
    audit(page,out,report,'empty-project',theme,width)
    # Direct manager routes remain stable after a full reload and back navigation.
    page.reload();expect(page.get_by_role('heading',name='Projects',exact=True)).to_be_visible()
    visible(page);assert urlsplit(page.url).path=='/manage/projects'
    if width==1280 and theme=='dark':auth_boundaries(state,owner,project,sessions,base,report)
    if(width==1280 and theme=='dark')or(width==375 and theme=='light'):
        configuration_failure_probe(page,context,out,report,theme,width)
        menu_probe(page,base,out,report,theme,width)
        register_scratch_project(page,context,state,scratch,out,report,theme,width)
        models_probe(page,state,owner,base,out,report,theme,width)

def style_delta_exercise(page,context,state,owner,project,projects,sessions,base,scratch,out,report,theme,width):
    page.goto(base+'/manage/projects?workspace=agent')
    page.get_by_label('Username',exact=True).fill(fixture.USERNAME)
    page.get_by_label('Password',exact=True).fill(fixture.PASSWORD)
    page.get_by_role('button',name='Continue with password',exact=True).click()
    expect(page.get_by_role('heading',name='Projects',exact=True)).to_be_visible(timeout=60000)
    visible(page)
    panel=page.get_by_role('region',name='Projects workspace',exact=True)
    audit(page,out,report,'projects',theme,width)
    trigger=panel.get_by_role('button',name='Add project',exact=True)
    trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Add project',exact=True)
    keyboard_modal(page,dialog)
    add_border=dialog.evaluate('e=>({width:getComputedStyle(e).borderWidth,style:getComputedStyle(e).borderStyle})')
    assert add_border['width']=='0px',add_border
    audit(page,out,report,'add-project',theme,width)
    modal_close(page,dialog,trigger)
    page.get_by_role('navigation',name='Workspace views',exact=True).get_by_role('button',name='Activity',exact=True).click()
    inspect=page.get_by_role('button',name='Inspect Recover the interrupted package build',exact=True)
    inspect.focus();page.keyboard.press('Enter')
    detail=page.get_by_role('dialog',name='Recover the interrupted package build',exact=True)
    expect(detail.get_by_role('button',name='Close',exact=True)).to_be_focused()
    keyboard_modal(page,detail)
    content=detail.get_by_role('region',name='Activity details content',exact=True)
    content.focus();scroll=content.evaluate('e=>({client:e.clientHeight,total:e.scrollHeight,top:e.scrollTop,outline:getComputedStyle(e).outline,style:getComputedStyle(e).outlineStyle})')
    assert scroll['style']!='none',scroll
    page.keyboard.press('End')
    if scroll['total']>scroll['client']+1:fixture.wait(lambda:content.evaluate('e=>e.scrollTop')>=scroll['total']-scroll['client']-1,page,seconds=3)
    scroll['after_End']=content.evaluate('e=>e.scrollTop')
    page.keyboard.press('Home');fixture.wait(lambda:content.evaluate('e=>e.scrollTop')==0,page,seconds=3)
    detail.get_by_role('button',name='Close',exact=True).focus()
    detail_border=detail.evaluate('e=>({width:getComputedStyle(e).borderWidth,style:getComputedStyle(e).borderStyle})')
    assert detail_border['width']=='0px',detail_border
    audit(page,out,report,'activity-details',theme,width)
    modal_close(page,detail,inspect)
    report['checks'].append({'check':'CSS-delta-dialog-frame-focus-geometry-and-keyboard-scroll','theme':theme,'width':width,'Add_border':add_border,'Activity_border':detail_border,'focus_contained':True,'Escape_restores_both_triggers':True,'actual_scroll':scroll})


def run(destination,ui_dist,baseline=False,style_delta=False):
    if style_delta:
        basis_root=REPO/'docs/evidence/projects-control-plane/full-v2'
        if not all((basis_root/name).is_file() for name in ('source-manifest.json','report.json')):
            raise FileNotFoundError('--style-delta requires the optional historical full-v2 source-manifest.json and report.json, which are not shipped. Run without --style-delta for current-source checks.')
    out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'mode':'before'if baseline else'after','result':'running','output':str(out),'ui_dist':str(Path(ui_dist).resolve()),'started_at':datetime.now(timezone.utc).isoformat(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fixture_helper_sha256':hashlib.sha256(Path(fixture.__file__).read_bytes()).hexdigest(),'states':[],'checks':[],'errors':[],'external_requests':[],'forbidden_mutations':[],'owned_mutations':[],'backend_requests':[],'requests':[],'limits':['Scratch authenticated host and real stores; project titles, directories, sessions and task states are explicitly synthetic offline fixtures.','No native app/profile, engine or provider inference, terminal action, production data or device proof.','Axe is a bounded automated check; incomplete findings require human review.']}
    try:
        with sync_playwright()as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');report['browser']=browser.version
            source_manifest={str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest()for root in(REPO/'desktop/workspace/src',REPO/'src/termx')for p in sorted(root.rglob('*'))if p.is_file()and p.suffix in('.ts','.tsx','.css','.py')}
            if style_delta:
                basis_root=REPO/'docs/evidence/projects-control-plane/full-v2'
                basis=json.loads((basis_root/'source-manifest.json').read_text());basis_report=json.loads((basis_root/'report.json').read_text())
                assert basis_report['result']=='passed'and len(basis_report['states'])==56
                assert set(basis)==set(source_manifest),'Source files changed outside the documented CSS delta'
                changed=[{'path':name,'before':basis[name],'after':value}for name,value in source_manifest.items()if basis[name]!=value]
                assert [row['path']for row in changed]==['desktop/workspace/src/features/control-plane.css'],changed
                report['proof_type']='CSS-only delta on full-v2 functional proof'
                report['functional_basis']={'report':'../full-v2/report.json','captures':56,'checks':len(basis_report['checks']),'ui_manifest_digest':basis_report['ui_manifest_digest'],'source_manifest_digest':basis_report['source_manifest_sha256'],'report_file_sha256':hashlib.sha256((basis_root/'report.json').read_bytes()).hexdigest()}
                report['changed_sources']=changed
            (out/'source-manifest.json').write_text(json.dumps(source_manifest,indent=2)+'\n')
            report['source_manifest_sha256']=hashlib.sha256(json.dumps(source_manifest,sort_keys=True).encode()).hexdigest()
            for width,height,theme in((1280,900,'dark'),(1280,900,'light'),(375,812,'dark'),(375,812,'light')):
                with fixture.isolated_host(ui_dist,report)as(state,owner,project,base,scratch):
                    projects,sessions,receipts=seed(state,owner,project,scratch);report.setdefault('fixture_cases',[]).append({'theme':theme,'width':width,'project_names':[p['name']for p in projects],'statuses':list(sessions),'receipts':receipts})
                    context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<640,reduced_motion='reduce');context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
                    origin=urlsplit(base)
                    def guard(route):
                        req=route.request;url=urlsplit(req.url)
                        if url.scheme in('http','https')and(url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_requests'].append(req.url);route.abort();return
                        body=req.post_data_json if req.method=='POST'and(url.path=='/graphql')else{}
                        project_input=(body or{}).get('variables',{}).get('input',{})
                        allowed_project=url.path=='/graphql'and 'register_project'in(body or{}).get('query','')and project_input.get('path')in getattr(state,'_qa_allowed_project_paths',set())
                        if allowed_project:report['owned_mutations'].append({'method':req.method,'path':url.path,'operation':'register_project','input':project_input})
                        if req.method not in('GET','HEAD')and url.path!='/auth/login'and not allowed_project and not(url.path=='/graphql'and not re.search(r'\bmutation\b',(body or{}).get('query',''))):
                            report['forbidden_mutations'].append({'method':req.method,'path':url.path,'query':(body or{}).get('query','')});route.abort();return
                        report['requests'].append({'method':req.method,'path':url.path});route.continue_()
                    context.route('**/*',guard);page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    try:(style_delta_exercise if style_delta else exercise)(page,context,state,owner,project,projects,sessions,base,scratch,out,report,theme,width)
                    except BaseException:
                        page.screenshot(path=str(out/'failure.png'),full_page=True);raise
                    finally:
                        context.close()
                        assert {r['id']for r in state.agent_store.list_tasks()}==set(receipts),'Unexpected task dispatch'
                        for identifier in receipts:state.agent_store.update_task(identifier,status='completed');state.agent_store.delete_task(identifier)
            browser.close();report['cleanup']={'browser_closed':not browser.is_connected(),'scratch_hosts_closed':True,'synthetic_tasks_removed':True};report['result']='passed'
            assert all(hashlib.sha256((REPO/name).read_bytes()).hexdigest()==value for name,value in source_manifest.items()),'Source changed during final proof; rerun with frozen source'
        assert not report['errors']and not report['external_requests']and not report['forbidden_mutations'],report
    except BaseException as error:report.update(result='failed',failure=type(error).__name__+': '+str(error));raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':report['result'],'report':str(out/'report.json')}),flush=True)

if __name__=='__main__':run(sys.argv[1],sys.argv[2],'--baseline'in sys.argv,'--style-delta'in sys.argv)
