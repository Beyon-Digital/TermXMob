"""Focused Studio project proof on immutable UI bytes and a scratch authenticated host.

No production profiles, provider credentials, generation, native window actions or BI code.
.venv/bin/python -B tests/workspace_studio_projects_e2e.py OUTPUT UI_DIST [--baseline]
"""
from __future__ import annotations
import asyncio,base64,hashlib,json,os,re,shutil,socket,sys,tempfile,threading
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from time import monotonic,sleep,time
from urllib.parse import urlsplit
import httpx,uvicorn
from playwright.sync_api import expect,sync_playwright

REPO=Path(os.environ.get('TERMX_STUDIO_PROJECTS_REPO')or(str(Path(__file__).resolve().parents[1])if Path(__file__).parent.name=='tests'else str(Path.cwd())))
sys.path.insert(0,str(REPO/'tests'))
from workspace_ui_snapshot import snapshot_ui
PASSWORD='synthetic-studio-project-password-123'
USERNAME='studio-project-fixture'

def wait(check,page=None,seconds=20):
    until=monotonic()+seconds
    while not check():
        if monotonic()>until:raise AssertionError('Timed out waiting for synthetic fixture state')
        if page:page.wait_for_timeout(50)
        else:sleep(.05)

def phase(report,name,**extra):
    value={'phase':name,**extra};print(json.dumps(value),flush=True)
    (Path(report['output'])/'progress.json').write_text(json.dumps(value)+'\n')

def visible_sidebar(page):
    nav=page.get_by_role('navigation',name='Studio projects',exact=True)
    if not nav.is_visible():
        page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Show sidebar',exact=True).click()
    expect(nav).to_be_visible()
    return nav

def studio_section(page,name):
    visible_sidebar(page)
    page.get_by_role('navigation',name='Studio views',exact=True).get_by_role('button',name=name,exact=True).click()

def keyboard_dialog(page,dialog):
    expect(dialog).to_be_visible()
    assert dialog.evaluate('e=>e.contains(document.activeElement)'), 'Dialog has no keyboard focus'
    for key in('Tab','Shift+Tab','Tab','Tab','Shift+Tab'):
        page.keyboard.press(key);assert dialog.evaluate('e=>e.contains(document.activeElement)'), 'Keyboard escaped dialog'

def audit(page,output,report,name,theme,width,axe):
    phase(report,'capture',state=name,theme=theme,width=width)
    page.wait_for_function("()=>[...document.querySelectorAll('[role=dialog],dialog[open]')].every(e=>e.getAnimations().every(a=>a.playState!=='running'||a.effect?.getTiming().iterations===Infinity))",timeout=3000)
    metrics=page.evaluate("""()=>({overflow:document.documentElement.scrollWidth>innerWidth,viewport:{width:innerWidth,height:innerHeight},dialogs:[...document.querySelectorAll('[role=dialog],dialog[open]')].filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>({text:e.textContent.slice(0,180),rect:e.getBoundingClientRect().toJSON()})),alerts:[...document.querySelectorAll('[role=alert]')].filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>e.textContent.trim()),rail:document.querySelector('.workspace-activity-rail')?.getBoundingClientRect().toJSON()})""")
    assert not metrics['overflow'],metrics
    for row in metrics['dialogs']:
        box=row['rect'];assert box['x']>=-1 and box['right']<=width+1 and box['y']>=-1 and box['bottom']<=page.viewport_size['height']+1,box
    item={'state':name,'theme':theme,'width':width,**metrics}
    item['new_project_controls']=page.locator('.studio-project-dialog input,.studio-project-dialog textarea,.studio-project-dialog .button,[role=dialog] .creation-actions .button').evaluate_all("rows=>rows.filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>({tag:e.tagName,name:e.getAttribute('aria-label')||e.textContent.trim(),font:getComputedStyle(e).fontSize,height:e.getBoundingClientRect().height,outline:getComputedStyle(e).outline}))")
    for control in item['new_project_controls']:
        if control['tag']in('INPUT','TEXTAREA'):assert control['font']=='16px',control
        if width<640:assert control['height']>=44,control
    if axe.is_file():
        if not page.evaluate("typeof axe!=='undefined'"):page.add_script_tag(path=str(axe))
        result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
        item['axe_version']=page.evaluate('axe.version')
        item['violations']=[{'id':v['id'],'impact':v.get('impact'),'nodes':[{'target':n['target'],'summary':n.get('failureSummary')}for n in v['nodes']]}for v in result['violations']]
        item['incomplete']=[{'id':v['id'],'targets':[n['target']for n in v['nodes']]}for v in result['incomplete']]
    filename=f'{report["mode"]}-{name}-{theme}-{width}.png';page.screenshot(path=str(output/filename),full_page=True)
    item['screenshot']=filename;report['states'].append(item)
    (output/'partial-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return item

@contextmanager
def isolated_host(ui_dist,report):
    scratch=Path(tempfile.mkdtemp(prefix='termx-studio-projects-'));envs=('TERMX_CONFIG_DIR','TERMX_AGENTS_DIR','TERMX_ENGINE_STARTUP_REFRESH');old={key:os.environ.get(key)for key in envs}
    server=worker=listener=client=None
    try:
        os.environ.update(TERMX_CONFIG_DIR=str(scratch/'config'),TERMX_AGENTS_DIR=str(scratch/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        class NoInference:
            async def test(self,*args,**kwargs):raise AssertionError('No provider testing in Studio project proof')
            async def plan(self,*args,**kwargs):raise AssertionError('No model calls in Studio project proof')
            async def turn(self,*args,**kwargs):raise AssertionError('No model calls in Studio project proof')
        state=AppState(passcode=None,credentials=CredentialStore({}),adapter_factory=lambda *_:NoInference())
        owner=state.identity.setup_owner(USERNAME,PASSWORD)
        folder=scratch/'agent-project';folder.mkdir();(folder/'read-only-fixture.txt').write_text('Synthetic Agent project source; Studio project CRUD must preserve this.\n')
        agent_project=state.projects.register(str(folder),name='Synthetic Agent folder')
        app=create_app(state,web_dir=snapshot_ui(scratch,Path(ui_dist)))
        state.workspace.orchestrator.ensure(owner)
        async def no_catalog(_):return {}
        state.agent.catalogue._fetch=no_catalog
        async def no_dispatch(**kwargs):raise AssertionError('No agent task dispatch in Studio project proof')
        state.agent.create_task=no_dispatch
        def provider(request):
            report['backend_requests'].append({'method':request.method,'path':request.url.path})
            if request.method=='GET'and request.url.path in('/api/v1/models','/api/v1/images/models','/api/v1/videos/models'):return httpx.Response(503,json={'error':'Synthetic offline catalogue; no network discovery'})
            raise AssertionError('Unexpected provider request in Studio project proof')
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider));state.studio.openrouter.client=client;state.studio.client=client
        manifest=json.loads((scratch/'fixture-ui-snapshot.json').read_text());report['ui_manifest_entries']=len(manifest)
        digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
        assert report.get('ui_manifest_digest',digest)==digest,'UI candidate changed between cases'
        report['ui_manifest_digest']=digest
        (Path(report['output'])/'ui-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();base='http://127.0.0.1:'+str(listener.getsockname()[1])
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on',access_log=False));worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start();wait(lambda:server.started,seconds=30)
        yield state,owner,agent_project,base,scratch
        assert not state.agent_store.list_tasks(),'Project management unexpectedly created an agent task'
        assert Path(agent_project['path'],'read-only-fixture.txt').read_text()=='Synthetic Agent project source; Studio project CRUD must preserve this.\n'
    finally:
        if server:server.should_exit=True
        if worker:worker.join(timeout=15);assert not worker.is_alive(),'Scratch host did not stop'
        if listener:listener.close()
        if client:
            failures=[]
            def close_client():
                try:asyncio.run(client.aclose())
                except BaseException as error:failures.append(error)
            cleanup=threading.Thread(target=close_client);cleanup.start();cleanup.join(timeout=5)
            assert not cleanup.is_alive()and not failures,failures
        shutil.rmtree(scratch,ignore_errors=True)
        for key,value in old.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value

def authenticate(page,base):
    page.goto(base+'/?workspace=studio&studio=create')
    page.get_by_label('Username',exact=True).fill(USERNAME);page.get_by_label('Password',exact=True).fill(PASSWORD)
    page.get_by_role('button',name='Continue with password',exact=True).click()
    expect(page.get_by_role('region',name='Media studio',exact=True)).to_be_visible(timeout=60000)
    visible_sidebar(page)

def project_rows(state,owner):
    return state.studio_projects.list(owner.id)

def project_named(state,owner,name):
    rows=[row for row in project_rows(state,owner)if row['name']==name]
    assert len(rows)==1,rows
    return rows[0]

def owner_api(state,base,username=USERNAME):
    response=httpx.post(base+'/auth/login',json={'method':'local-password','username':username,'password':PASSWORD,'transport':'bearer','device_name':'Synthetic project API probe'})
    assert response.status_code==200,response.text
    raw=response.json()['access_token']
    return httpx.Client(base_url=base,headers={'Authorization':'Bearer '+raw}),raw

def managed_api_boundaries(state,owner,agent_project,base,project,report):
    from termx.authorization import ROLES
    admin,raw=owner_api(state,base)
    prefix='/api/media/studio/projects'
    with state.identity._db()as db:grants_before=db.execute('SELECT COUNT(*) FROM project_grants').fetchone()[0]
    other=state.identity.create_local_user('synthetic-other',PASSWORD,list(ROLES['operator']));state.authorization.set_role(other.id,'operator')
    foreign,foreign_raw=owner_api(state,base,'synthetic-other')
    response=foreign.post(prefix,json={'name':'Synthetic other-owner collection','description':'Must remain private from the browser owner.'});assert response.status_code==201,response.text
    foreign_project=response.json()
    foreign_list=foreign.get(prefix);assert foreign_list.status_code==200 and[entry['id']for entry in foreign_list.json()['projects']]==[foreign_project['id']]
    assert project['id']not in [entry['id']for entry in foreign_list.json()['projects']]
    assert foreign.put(prefix+'/'+project['id'],json={'name':'Denied','description':'','revision':project['revision']}).status_code==404
    assert foreign.get('/api/media/studio/jobs?project_id='+project['id']).status_code==404
    assert not state.authorization.can(foreign_raw,'agent-view',project_id=agent_project['id'])
    assert not state.authorization.can(foreign_raw,'agent-view',project_id='unrelated-agent-folder')
    own=state.authorization.resource_owner('studio_project',foreign_project['id']);assert own and own['principal_id']==other.id and own['project_id']is None
    state.identity.set_scopes(other.id,['agent-view','files-read'])
    assert foreign.post(prefix,json={'name':'Scope ceiling must refuse'}).status_code==403
    assert foreign.put(prefix+'/'+foreign_project['id'],json={'name':'Denied','description':'','revision':foreign_project['revision']}).status_code==403
    assert admin.get('/api/media/studio/jobs?project_id=studio_missing').status_code==404
    assert admin.put(prefix+'/'+agent_project['id'],json={'name':'Cannot rename Agent folder here','description':'','revision':1}).status_code==404
    assert httpx.get(base+prefix).status_code==401
    legacy=state.tokens.issue(['agent-view','files-write'],device_name='Synthetic legacy token; no managed identity')
    assert httpx.get(base+prefix,headers={'Authorization':'Bearer '+legacy}).status_code==401
    assert httpx.post(base+prefix,headers={'Authorization':'Bearer '+legacy},json={'name':'Denied legacy collection'}).status_code==401
    with state.identity._db()as db:assert db.execute('SELECT COUNT(*) FROM project_grants').fetchone()[0]==grants_before
    report['checks'].append({'check':'managed-owner-scope-and-Agent-boundaries','other_owner_read_update':404,'live_scope_ceiling_create_update':403,'anonymous_and_unmanaged_legacy':401,'unknown_project':404,'Agent_folder_update':404,'unrelated_Agent_grants_inserted':0})
    foreign.close();admin.close()

def seed_retained_receipt(state,owner,project):
    import io
    from PIL import Image
    image=io.BytesIO();Image.new('RGB',(120,80),(116,119,122)).save(image,'PNG')
    output=state.artifacts.create(owner.id,project['id'],'image','Synthetic retained image',blob=image.getvalue(),mime='image/png')
    document=state.artifacts.create(owner.id,project['id'],'document','Synthetic retained script','This synthetic script must survive archive and restore.')
    source=state.artifacts.create(owner.id,project['id'],'input','Synthetic retained input',blob=b'Synthetic retained source bytes',mime='text/plain')
    identifier='synthetic-project-receipt-'+project['id'];now=time()
    with state.studio.db()as db:
        db.execute("INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,created,updated,outputs) VALUES(?,?,?,?,?,'synthetic','kie','synthetic-image','image',?,'unknown',?,?,?)",(identifier,owner.id,project['id'],'request-'+identifier,'synthetic','Synthetic unresolved receipt; no provider was invoked.',now,now,json.dumps(['/api/media/artifacts/'+output['id']+'/export?format=original'])))
    return {'job':identifier,'output':output,'document':document,'input':source}

def archived_write_and_retention(state,owner,base,project,receipt,report):
    api,_=owner_api(state,base)
    rows=api.get('/api/media/studio/history?project_id='+project['id']);assert rows.status_code==200 and any(row['id']==receipt['job']for row in rows.json()['items'])
    doc=api.get('/api/media/artifacts/'+receipt['document']['id']);assert doc.status_code==200 and doc.json()['content']=='This synthetic script must survive archive and restore.'
    assert state.artifacts.binary(owner.id,receipt['input']['id'])[0]==b'Synthetic retained source bytes'
    assert api.post('/api/media/artifacts',json={'kind':'document','title':'Refused archived write','content':'No artifact should be created','project_id':project['id']}).status_code==409
    current=state.studio_projects.owned(owner.id,project['id']);assert current['archived']
    assert api.put('/api/media/studio/projects/'+project['id'],json={'name':'Refused archived edit','description':'','revision':current['revision']}).status_code==409
    assert api.get('/api/media/studio/jobs?project_id='+project['id']).status_code==200
    report['checks'].append({'check':'archive-keeps-history-assets-and-inputs-refuses-writes','project':project['id'],'history_rows_preserved':1,'document_and_input_bytes_preserved':True,'archived_artifact_write':409,'archived_project_edit':409})
    api.close()

def open_create(page):
    visible_sidebar(page)
    trigger=page.get_by_role('button',name='Create project',exact=True);trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Create project',exact=True);expect(dialog).to_be_visible();keyboard_dialog(page,dialog)
    expect(dialog.get_by_role('textbox',name='Project name',exact=True)).to_be_visible()
    assert dialog.get_by_role('textbox',name=re.compile('folder|path|Git',re.I)).count()==0
    return dialog

def manage_projects(page):
    visible_sidebar(page)
    trigger=page.get_by_role('button',name='Manage projects',exact=True);trigger.focus();page.keyboard.press('Enter')
    dialog=page.get_by_role('dialog',name='Studio projects',exact=True);expect(dialog).to_be_visible();keyboard_dialog(page,dialog)
    return dialog

def select_project(page,name):
    nav=visible_sidebar(page);button=nav.get_by_role('button',name=name,exact=True);button.focus();page.keyboard.press('Enter');return button

def create_project_ui(page,context,state,owner,base,name,description,output,report,theme,width,axe,*,fault=False,capture=False):
    dialog=open_create(page)
    dialog.get_by_role('textbox',name='Project name',exact=True).fill(name)
    dialog.get_by_role('textbox',name='Description',exact=True).fill(description)
    if capture:audit(page,output,report,'projects-create',theme,width,axe)
    pending=[]
    def hold(route):
        if route.request.method=='POST':pending.append(route)
        else:route.fallback()
    if fault:context.route('**/api/media/studio/projects',hold)
    dialog.get_by_role('button',name='Create project',exact=True).click()
    if fault:
        wait(lambda:bool(pending),page=page)
        expect(dialog.get_by_role('textbox',name='Project name',exact=True)).to_be_disabled()
        expect(dialog.get_by_role('button',name='Saving…',exact=True)).to_be_disabled()
        page.keyboard.press('Escape');expect(dialog).to_be_visible()
        assert not [row for row in project_rows(state,owner)if row['name']==name]
        audit(page,output,report,'projects-pending',theme,width,axe)
        pending.pop().fulfill(status=503,json={'detail':'Synthetic project service unavailable. Retry this saved draft.'})
        expect(dialog.get_by_role('alert')).to_contain_text('Synthetic project service unavailable')
        expect(dialog.get_by_role('textbox',name='Project name',exact=True)).to_have_value(name)
        expect(dialog.get_by_role('textbox',name='Description',exact=True)).to_have_value(description)
        audit(page,output,report,'projects-error',theme,width,axe)
        context.unroute('**/api/media/studio/projects',hold)
        dialog.get_by_role('button',name='Create project',exact=True).click()
    expect(dialog).not_to_be_visible();wait(lambda:any(row['name']==name for row in project_rows(state,owner)),page=page)
    result=project_named(state,owner,name)
    expect(visible_sidebar(page).get_by_role('button',name=name,exact=True)).to_have_attribute('aria-pressed','true')
    assert not {'path','cwd','worktree_id'}&result.keys(),result
    return result

def project_management_journey(page,context,state,owner,agent_project,base,output,report,theme,width,axe):
    suffix=f'{width} {theme}';name='Synthetic launch '+suffix;second='Synthetic library '+suffix
    first=create_project_ui(page,context,state,owner,base,name,'Synthetic collection, independent of Agent folders.',output,report,theme,width,axe,fault=width==1280 and theme=='dark',capture=width==1280 and theme=='light')
    other=create_project_ui(page,context,state,owner,base,second,'Synthetic second collection.',output,report,theme,width,axe)
    if width==375:audit(page,output,report,'projects-default',theme,width,axe)
    dialog=manage_projects(page)
    expect(dialog.get_by_role('button',name='Edit '+agent_project['name'],exact=True)).to_have_count(0)
    dialog.get_by_role('button',name='Edit '+name,exact=True).click()
    edit=page.get_by_role('dialog',name='Edit project',exact=True);expect(edit).to_be_visible();keyboard_dialog(page,edit)
    renamed='Synthetic launch collection with a deliberately long bounded name for narrow project management '+suffix
    description='Synthetic description retained with the collection. '*5
    edit.get_by_role('textbox',name='Project name',exact=True).fill(renamed)
    edit.get_by_role('textbox',name='Description',exact=True).fill(description)
    edit.get_by_role('button',name='Save changes',exact=True).click()
    dialog=page.get_by_role('dialog',name='Studio projects',exact=True);expect(dialog).to_be_visible();expect(dialog.get_by_role('status').filter(has_text='Project saved.')).to_be_visible()
    first=project_named(state,owner,renamed);assert first['revision']==2 and first['description']==description.strip()
    page.keyboard.press('Escape');expect(dialog).not_to_be_visible()
    # The unrelated selected collection remains writable; only A's unresolved receipt blocks archive.
    expect(visible_sidebar(page).get_by_role('button',name=second,exact=True)).to_have_attribute('aria-pressed','true')
    receipt=seed_retained_receipt(state,owner,first)
    dialog=manage_projects(page);dialog.get_by_role('button',name='Edit '+renamed,exact=True).click()
    page.get_by_role('dialog',name='Edit project',exact=True).get_by_role('button',name='Archive project',exact=True).click()
    archive=page.get_by_role('dialog',name='Archive project',exact=True);expect(archive).to_be_visible();keyboard_dialog(page,archive)
    confirm=archive.get_by_role('button',name='Archive project',exact=True);expect(confirm).to_be_disabled()
    archive.get_by_role('textbox',name='Project name',exact=True).fill('Incorrect confirmation');expect(confirm).to_be_disabled()
    archive.get_by_role('textbox',name='Project name',exact=True).fill(renamed)
    if width==375 and theme=='dark':audit(page,output,report,'projects-archive-confirm',theme,width,axe)
    confirm.click();expect(archive.get_by_role('alert')).to_contain_text('unconfirmed provider outcome')
    assert not state.studio_projects.owned(owner.id,first['id'])['archived']
    if width==1280 and theme=='dark':audit(page,output,report,'projects-archive-blocked',theme,width,axe)
    with state.studio.db()as db:db.execute("UPDATE studio_jobs SET status='completed',updated=? WHERE id=?",(time(),receipt['job']))
    archive.get_by_role('button',name='Refresh projects',exact=True).click();expect(archive.get_by_role('alert')).to_have_count(0)
    confirm.click();expect(page.get_by_role('dialog',name='Studio projects',exact=True)).to_be_visible()
    archived=state.studio_projects.owned(owner.id,first['id']);assert archived['archived']and archived['revision']==3
    archived_write_and_retention(state,owner,base,first,receipt,report)
    dialog=page.get_by_role('dialog',name='Studio projects',exact=True);dialog.get_by_role('button',name='Show archived',exact=True).click()
    restore=dialog.get_by_role('button',name='Restore '+renamed,exact=True);expect(restore).to_be_visible()
    if width==1280 and theme=='light':audit(page,output,report,'projects-archived',theme,width,axe)
    restore.focus();page.keyboard.press('Enter')
    expect(dialog.get_by_role('button',name='Edit '+renamed,exact=True)).to_be_visible()
    current=state.studio_projects.owned(owner.id,first['id']);assert not current['archived']and current['revision']==4
    page.keyboard.press('Escape');expect(dialog).not_to_be_visible()
    select_project(page,renamed);expect(visible_sidebar(page).get_by_role('button',name=renamed,exact=True)).to_have_attribute('aria-pressed','true')
    if(width==1280 and theme=='light')or(width==375 and theme=='dark'):audit(page,output,report,'projects-restored',theme,width,axe)
    assert state.artifacts.get(owner.id,receipt['document']['id'])['content']=='This synthetic script must survive archive and restore.'
    assert state.studio.list(owner.id,first['id'])[0]['id']==receipt['job']
    if width==1280 and theme=='dark':managed_api_boundaries(state,owner,agent_project,base,current,report)
    report['checks'].append({'check':'creative-project-create-edit-archive-restore','theme':theme,'width':width,'owned_collections_created':2,'unknown_receipt_blocks_archive':True,'named_confirmation_exact':True,'archive_soft':True,'Agent_project_read_only':True,'retained_history_assets_inputs':True,'create_error_retry_no_duplicate':width==1280 and theme=='dark'})
    return current,other

def close_studio_sidebar(page):
    if page.viewport_size['width']<640 and page.get_by_role('navigation',name='Studio projects',exact=True).is_visible():
        page.locator('.workspace-activity-rail').get_by_role('button',name='Hide sidebar',exact=True).click()
        expect(page.get_by_role('navigation',name='Studio projects',exact=True)).not_to_be_visible()

def single_sidebar_toggle(page,report,theme,width):
    nav=visible_sidebar(page)
    assert page.locator('.sidebar-brand button').count()==0,'Redundant brand collapse control remains'
    rail=page.locator('.workspace-activity-rail')
    hide=rail.get_by_role('button',name='Hide sidebar',exact=True);expect(hide).to_be_visible()
    expect(page.get_by_role('button',name='Hide sidebar',exact=True)).to_have_count(1)
    hide.focus();page.keyboard.press('Enter');expect(nav).not_to_be_visible()
    show=rail.get_by_role('button',name='Show sidebar',exact=True);show.focus();page.keyboard.press('Enter');expect(nav).to_be_visible()
    report['checks'].append({'check':'single-retained-rail-toggle-keyboard','theme':theme,'width':width,'brand_toggle_count':0,'visible_toggle_count':1,'keyboard_collapse_expand':True})

def draft_project_guards(page,state,owner,first,other,output,report,theme,width,axe):
    first_name,other_name=first['name'],other['name']
    studio_section(page,'Create');close_studio_sidebar(page)
    prompt=page.locator('.media-studio textarea.studio-prompt');expect(prompt).to_be_visible()
    image_text='Synthetic image draft that must remain in its own collection.';prompt.fill(image_text)
    select_project(page,other_name)
    guard=page.get_by_role('dialog',name='Switch Studio project?',exact=True);expect(guard).to_be_visible();keyboard_dialog(page,guard)
    expect(guard.get_by_role('button',name='Keep editing',exact=True)).to_be_visible()
    if width==375 and theme=='light':audit(page,output,report,'image-draft-guard',theme,width,axe)
    guard.get_by_role('button',name='Keep editing',exact=True).click();expect(guard).not_to_be_visible()
    expect(visible_sidebar(page).get_by_role('button',name=first_name,exact=True)).to_have_attribute('aria-pressed','true')
    expect(prompt).to_have_value(image_text)
    select_project(page,other_name);expect(guard).to_be_visible()
    guard.get_by_role('button',name='Discard drafts and switch',exact=True).click();expect(guard).not_to_be_visible()
    expect(visible_sidebar(page).get_by_role('button',name=other_name,exact=True)).to_have_attribute('aria-pressed','true')
    expect(prompt).to_have_value('')
    select_project(page,first_name);expect(guard).not_to_be_visible();expect(prompt).to_have_value('')
    studio_section(page,'Music');close_studio_sidebar(page)
    music=page.locator('.music-studio');expect(music).to_be_visible()
    simple=music.get_by_role('tab',name='Simple',exact=True);expect(simple).to_have_attribute('aria-selected','true')
    music.get_by_role('textbox',name='Song description',exact=True).fill('Synthetic Music description in the source collection.')
    advanced=music.get_by_role('tab',name='Advanced',exact=True);expect(advanced).to_be_enabled();advanced.click()
    lyrics='[Verse]\nSynthetic lyrics retained only after Keep editing'
    music.get_by_role('textbox',name='Lyrics',exact=True).fill(lyrics)
    music.get_by_role('textbox',name='Music styles',exact=True).fill('Synthetic quiet acoustic arrangement')
    music.get_by_role('textbox',name='Song title',exact=True).fill('Synthetic unsent song')
    select_project(page,other_name);expect(guard).to_be_visible();keyboard_dialog(page,guard)
    if width==375 and theme=='light':audit(page,output,report,'music-draft-guard',theme,width,axe)
    guard.get_by_role('button',name='Keep editing',exact=True).click();expect(guard).not_to_be_visible()
    expect(music.get_by_role('textbox',name='Lyrics',exact=True)).to_have_value(lyrics)
    select_project(page,other_name);expect(guard).to_be_visible();guard.get_by_role('button',name='Discard drafts and switch',exact=True).click();expect(guard).not_to_be_visible()
    expect(music.get_by_role('textbox',name='Song description',exact=True)).to_have_value('')
    select_project(page,first_name);expect(guard).not_to_be_visible()
    expect(music.get_by_role('textbox',name='Song description',exact=True)).to_have_value('')
    close_studio_sidebar(page)
    expect(music.get_by_role('tab',name='Advanced',exact=True)).to_be_enabled();music.get_by_role('tab',name='Advanced',exact=True).click()
    for label in('Lyrics','Music styles','Song title'):expect(music.get_by_role('textbox',name=label,exact=True)).to_have_value('')
    expect(music.get_by_role('button',name='Create',exact=True)).to_be_disabled()
    # Archiving the selected collection must await the same draft decision.
    studio_section(page,'Create');close_studio_sidebar(page);prompt.fill('Synthetic draft protects an attempted selected-project archive.')
    dialog=manage_projects(page);dialog.get_by_role('button',name='Edit '+first_name,exact=True).click()
    page.get_by_role('dialog',name='Edit project',exact=True).get_by_role('button',name='Archive project',exact=True).click()
    archive=page.get_by_role('dialog',name='Archive project',exact=True);archive.get_by_role('textbox',name='Project name',exact=True).fill(first_name)
    revision=state.studio_projects.owned(owner.id,first['id'])['revision']
    archive.get_by_role('button',name='Archive project',exact=True).click();expect(guard).to_be_visible()
    guard.get_by_role('button',name='Keep editing',exact=True).click();expect(guard).not_to_be_visible()
    expect(archive.get_by_role('button',name='Archive project',exact=True)).to_be_enabled()
    current=state.studio_projects.owned(owner.id,first['id']);assert not current['archived']and current['revision']==revision
    page.keyboard.press('Escape');expect(archive).not_to_be_visible()
    close_studio_sidebar(page)
    expect(prompt).to_have_value('Synthetic draft protects an attempted selected-project archive.')
    prompt.fill('')
    report['checks'].append({'check':'Image-Music-and-selected-archive-draft-decisions','theme':theme,'width':width,'keep_preserves_exact_source':True,'discard_clears_all_editors_and_return_does_not_resurrect':True,'Music_simple_lyrics_styles_title':True,'selected_archive_awaits_and_honors_keep':True,'provider_generation':0})

def exercise(page,context,state,owner,agent_project,base,output,report,theme,width,axe,baseline):
    authenticate(page,base)
    nav=visible_sidebar(page);expect(nav.get_by_role('button',name=agent_project['name'],exact=True)).to_be_visible()
    if baseline:
        audit(page,output,report,'studio-projects',theme,width,axe)
        return
    before_agent=[(row['id'],row['path'])for row in state.projects.projects()]
    single_sidebar_toggle(page,report,theme,width)
    first,other=project_management_journey(page,context,state,owner,agent_project,base,output,report,theme,width,axe)
    draft_project_guards(page,state,owner,first,other,output,report,theme,width,axe)
    select_project(page,agent_project['name']);expect(visible_sidebar(page).get_by_role('button',name=agent_project['name'],exact=True)).to_have_attribute('aria-pressed','true')
    select_project(page,first['name'])
    assert [(row['id'],row['path'])for row in state.projects.projects()]==before_agent
    assert len(project_rows(state,owner))==2


def preview(browser,output,report):
    wanted={'desktop':'after-projects-restored-light-1280.png','phone':'after-projects-default-dark-375.png','guard':'after-image-draft-guard-light-375.png'}
    assert all((output/name).is_file()for name in wanted.values()),wanted
    cards={key:'<figure class="'+key+'"><figcaption>'+name+'</figcaption><img src="data:image/png;base64,'+base64.b64encode((output/name).read_bytes()).decode()+'"></figure>'for key,name in wanted.items()}
    html='<!doctype html><html lang="en"><meta charset="utf-8"><title>Studio projects browser proof</title><style>body{margin:0;padding:24px;background:#111;color:#eee;font:14px system-ui}h1{font-size:20px;margin:0 0 12px}p{line-height:1.6}main{display:grid;grid-template-columns:1280px 375px;grid-template-areas:"desktop phone" "notes guard";gap:24px}figure{margin:0;min-width:0}figcaption{margin-bottom:8px}.desktop{grid-area:desktop}.phone{grid-area:phone}.guard{grid-area:guard}img{display:block;width:100%;height:auto}aside{grid-area:notes;max-width:68ch;line-height:1.6}</style><h1>Studio projects · actual browser captures</h1><p>Clearly synthetic collections on isolated scratch hosts. No providers, generation or BI code. Desktop shown at1280px and phone at actual375px.</p><main>'+cards['desktop']+cards['phone']+cards['guard']+'<aside><p>Original PNGs are embedded in an actual browser DOM preview. The report retains exact production asset hashes, project/API checks, keyboard results and accessibility incomplete cases.</p><p>Archive preserves media and history. Agent folders remain selectable context and are not edited through Studio project management.</p></aside></main></html>'
    (output/'preview.html').write_text(html);page=browser.new_page(viewport={'width':1727,'height':1800});page.set_content(html);page.screenshot(path=str(output/'preview.png'),full_page=True);page.close()
    report['preview']={'html':'preview.html','png':'preview.png','originals':wanted,'method':'Unedited browser PNGs embedded into an actual browser DOM; desktop1280 and phone375 display width.'}

def run(output_path,ui_dist,baseline=False):
    output=Path(output_path).resolve();output.mkdir(parents=True,exist_ok=False)
    report={'result':'running','mode':'before'if baseline else'after','output':str(output),'ui_dist':str(Path(ui_dist).resolve()),'states':[],'checks':[],'errors':[],'external_requests':[],'forbidden_mutations':[],'backend_requests':[],'failed_requests':[],'http_errors':[],'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'started_at':datetime.now(timezone.utc).isoformat(),'limitations':['Real isolated FastAPI/password authentication and scratch stores, not the production profile.','No inference, paid generation, native foreground interactions or BI implementation.','Full-document axe retains incomplete cases; browser results do not establish physical-device or complete accessibility conformance.']}
    try:
        with sync_playwright()as playwright:
            chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            browser=playwright.chromium.launch(headless=True,executable_path=chrome);report['browser']=browser.version
            for width,height,theme in((375,812,'dark'),(375,812,'light'),(1280,900,'dark'),(1280,900,'light')):
                phase(report,'case',width=width,theme=theme)
                with isolated_host(ui_dist,report)as(state,owner,agent_project,base,scratch):
                    context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<640,reduced_motion='reduce')
                    context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
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
                    try:exercise(page,context,state,owner,agent_project,base,output,report,theme,width,REPO/'desktop/workspace/node_modules/axe-core/axe.min.js',baseline)
                    except BaseException:
                        page.screenshot(path=str(output/'failure.png'),full_page=True);page.locator('input[type=password]').evaluate_all('rows=>rows.forEach(e=>e.value="")');(output/'failure.html').write_text(page.content());raise
                    finally:context.close()
            if not baseline:preview(browser,output,report)
            browser.close()
        assert not report['errors']and not report['external_requests']and not report['forbidden_mutations'],report
        report['axe_violation_states']=[{'state':s['state'],'theme':s['theme'],'width':s['width'],'violations':s.get('violations')}for s in report['states']if s.get('violations')]
        if not baseline:assert not report['axe_violation_states'],report['axe_violation_states']
        report['result']='passed'
    except BaseException as error:report.update(result='failed',failure=f'{type(error).__name__}: {error}');raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();(output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    if len(sys.argv)not in(3,4):raise SystemExit('Usage: workspace_studio_projects_e2e.py OUTPUT UI_DIST [--baseline]')
    result=run(sys.argv[1],sys.argv[2],len(sys.argv)==4 and sys.argv[3]=='--baseline')
    print(json.dumps({'result':result['result'],'states':len(result['states']),'checks':len(result['checks']),'axe_violation_states':result['axe_violation_states']},indent=2))
