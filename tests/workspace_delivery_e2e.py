"""Opt-in actual local Git/workbench UI proof; temporary repos, no publication.

Run with bundled managed Chromium. The only external request is an explicitly
requested read-only inspection of the existing draft PR when a checkout is given.
"""
from __future__ import annotations
import asyncio,json,os,re,shutil,socket,subprocess,sys,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright


def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()


def prove_window_buffers(page,context,destination,report,root,local,eventually):
    page.get_by_role('button',name='Files',exact=True).click();page.locator('.file-row').filter(has_text='calc.txt').click()
    page.wait_for_function('(root)=>document.querySelector(".editor-tab.selected")?.title===root+"/calc.txt"',arg=str(Path(root).resolve()))
    editor=page.get_by_label('Editor for calc.txt',exact=True);main_unsaved=local+'# main unsaved\n';editor.focus();editor.press('ControlOrMeta+A');page.keyboard.insert_text(main_unsaved)
    with context.expect_page() as detached:
        page.get_by_role('button',name='Detach workspace',exact=True).click()
    child=detached.value;child.get_by_label('Message',exact=True).wait_for();child.get_by_label('Workspace layout',exact=True).get_by_role('button',name='Workbench',exact=True).click();child.get_by_label('Execution folder '+str(Path(root).resolve()),exact=True).wait_for()
    child.get_by_role('button',name='Files',exact=True).click();child.locator('.file-row').filter(has_text='calc.txt').click();child_editor=child.get_by_label('Editor for calc.txt',exact=True);child_editor.wait_for();child_editor.focus();child_editor.press('ControlOrMeta+A');child.keyboard.insert_text(local+'# detached unsaved\n')
    def persisted_slots():
        return page.evaluate('''async()=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('termx-workspace-buffer-state',2);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});const tx=db.transaction('slots','readonly');const values=await new Promise(resolve=>{const r=tx.objectStore('slots').getAll();r.onsuccess=()=>resolve(r.result)});db.close();return values}''')
    try:
        eventually(lambda:any(any('# main unsaved' in buffer['content'] for buffer in slot) for slot in persisted_slots()) and any(any('# detached unsaved' in buffer['content'] for buffer in slot) for slot in persisted_slots()))
    except AssertionError:
        report['window_persistence_diagnostic']={'main_name':page.evaluate('window.name'),'child_name':child.evaluate('window.name'),'slots':persisted_slots(),'main_editor':page.get_by_label('Editor for calc.txt',exact=True).inner_text(),'child_editor':child_editor.inner_text(),'child_alerts':child.locator('[role=alert]').all_text_contents()};raise
    page.on('dialog',lambda dialog:dialog.accept());child.on('dialog',lambda dialog:dialog.accept());page.reload();child.reload()
    page.get_by_label('Editor for calc.txt',exact=True).wait_for();child.get_by_label('Editor for calc.txt',exact=True).wait_for()
    assert '# main unsaved' in page.get_by_label('Editor for calc.txt',exact=True).inner_text();assert '# detached unsaved' in child.get_by_label('Editor for calc.txt',exact=True).inner_text();assert (Path(root)/'calc.txt').read_text()==local
    report['two_window_dirty_reload']={'independent_slots':True,'main_dirty_buffer_preserved':True,'detached_dirty_buffer_preserved':True,'disk_unchanged':True};report['steps'].append('Actual detached window: concurrent different dirty buffers independently persisted and restored after both reloads')
    page.screenshot(path=str(destination/'main-dirty-recovered.png'));child.screenshot(path=str(destination/'detached-dirty-recovered.png'));child.close()

def prove_window_lifecycle(page,context,destination,report,repo,original,eventually,state,session):
    page.get_by_role('button',name='Files',exact=True).click();page.locator('.file-row').filter(has_text='calc.txt').click()
    editor=page.get_by_label('Editor for calc.txt',exact=True);editor.wait_for();editor.focus();editor.press('ControlOrMeta+A');page.keyboard.insert_text(original+'# MAIN_UNSAVED\n')
    page.get_by_label('Message',exact=True).fill('Provider-free task during window handoff');page.get_by_role('button',name='Send message',exact=True).click()
    eventually(lambda:bool(state.agent_store.active_conversation_tasks(session['id'])))
    task_id=state.agent_store.active_conversation_tasks(session['id'])[0]['id'];eventually(lambda:state.agent_store.get_task(task_id)['status']=='running')
    page.get_by_role('button',name='New terminal',exact=True).click();page.wait_for_function('()=>!!document.querySelector(".terminal-region")?.dataset.connectedSession');terminal_id=page.get_by_label('Terminal session',exact=True).input_value()
    with context.expect_page() as popup:page.get_by_role('button',name='Detach editor',exact=True).click()
    child=popup.value;child.on('pageerror',lambda error:report['errors'].append(str(error)));child.on('dialog',lambda dialog:dialog.accept())
    child.get_by_label('Execution folder '+str(repo.resolve()),exact=True).wait_for();child.get_by_role('button',name='Files',exact=True).click();child.locator('.file-row').filter(has_text='calc.txt').click()
    child_editor=child.get_by_label('Editor for calc.txt',exact=True);child_editor.wait_for();child_editor.focus();child_editor.press('ControlOrMeta+A');child.keyboard.insert_text(original+'# CHILD_UNSAVED\n')
    name=child.evaluate('window.name');assert name.startswith('termx-workspace-');assert child.get_by_role('button',name='Return to main workspace',exact=True).is_visible()
    # Local image bytes must survive even when another window changes the shared text.
    child.locator('input[type=file]').set_input_files({'name':'pending-window.png','mimeType':'image/png','buffer':b'controlled local image attachment'})
    child.get_by_text('pending-window.png',exact=True).wait_for()
    child.get_by_label('Message',exact=True).fill('Detached draft survives closed window')
    def journals():return page.evaluate('''async()=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('termx-workspace-window-journal',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});const rows=await new Promise(resolve=>{const r=db.transaction('drafts','readonly').objectStore('drafts').getAll();r.onsuccess=()=>resolve(r.result)});db.close();return rows}''')
    eventually(lambda:any(row['slot']==name and row['draft']['text']=='Detached draft survives closed window' and row['draft'].get('attachments') for row in journals()))
    page.get_by_label('Message',exact=True).fill('Main conflicting unsent draft')
    owner=state.identity.principal_by_id(session['owner'])
    eventually(lambda:any(row['slot']==page.evaluate('window.name') and row['draft']['text']=='Main conflicting unsent draft' for row in journals()))
    # Recovery of an active named window focuses it without navigating dirty content.
    before=len(context.pages);page.get_by_role('button',name='Recover unsaved windows',exact=True).click();page.get_by_role('button',name='Reopen window',exact=True).click();page.get_by_role('dialog',name='Recover unsaved windows').wait_for(state='hidden');assert len(context.pages)==before and not child.is_closed();assert '# CHILD_UNSAVED' in child_editor.inner_text();report['active_named_window_focused_without_navigation']=True
    child.close();page.get_by_role('button',name='Recover unsaved windows',exact=True).click()
    with context.expect_page() as recovered:page.get_by_role('button',name='Reopen window',exact=True).click()
    restored=recovered.value;restored.on('dialog',lambda dialog:dialog.accept());restored.on('pageerror',lambda error:report['errors'].append(str(error)))
    restored.get_by_label('Editor for calc.txt',exact=True).wait_for();restored.get_by_text('pending-window.png',exact=True).wait_for()
    assert restored.evaluate('window.name')==name
    assert restored.get_by_label('Message',exact=True).input_value()=='Detached draft survives closed window'
    assert '# CHILD_UNSAVED' in restored.get_by_label('Editor for calc.txt',exact=True).inner_text()
    assert '# MAIN_UNSAVED' in editor.inner_text() and (repo/'calc.txt').read_text()==original
    restored.get_by_role('button',name='Return to main workspace',exact=True).click();restored.get_by_role('alert').filter(has_text='draft').wait_for()
    assert not restored.is_closed();assert page.get_by_label('Message',exact=True).input_value()=='Main conflicting unsent draft'
    report['conflicting_draft_return_refused']=True
    restored.get_by_role('button',name='Dismiss',exact=True).click()
    page.get_by_label('Message',exact=True).fill('Detached draft survives closed window')
    eventually(lambda:state.workspace.session(owner,session['id'])['draft_text']=='Detached draft survives closed window')
    try:
        with restored.expect_event('close',timeout=15000):restored.get_by_role('button',name='Return to main workspace',exact=True).click()
    except BaseException:
        report['redock_failure']={'main_alerts':page.get_by_role('alert').all_text_contents(),'source_alerts':restored.get_by_role('alert').all_text_contents(),'journals':journals()};restored.screenshot(path=str(destination/'redock-source-failure.png'));raise
    page.get_by_text('pending-window.png',exact=True).wait_for();page.locator('.editor-tabs').get_by_role('button',name=re.compile(r'^calc\.txt · recovered copy(?: ●)?$')).wait_for()
    assert page.get_by_label('Message',exact=True).input_value()=='Detached draft survives closed window'
    main_slot=page.evaluate('window.name');eventually(lambda:any(row['slot']==main_slot and any(item['name']=='pending-window.png' and __import__('base64').b64decode(item['data'])==b'controlled local image attachment' for item in row['draft'].get('attachments',[])) for row in journals()))
    page.locator('.editor-tabs').get_by_role('button',name=re.compile(r'^calc\.txt · recovered copy(?: ●)?$')).click();assert '# CHILD_UNSAVED' in page.get_by_label('Editor for calc.txt',exact=True).inner_text()
    page.locator('.editor-tabs').get_by_role('button',name=re.compile(r'^calc\.txt(?: ●)?$')).click();assert '# MAIN_UNSAVED' in page.get_by_label('Editor for calc.txt',exact=True).inner_text()
    assert state.agent_store.get_task(task_id)['status']=='running';assert page.get_by_label('Terminal session',exact=True).input_value()==terminal_id
    report['acknowledged_browser_return']={'source_closed_after_accept':True,'main_draft_preserved':True,'image_bytes_preserved':True,'conflicting_buffers_both_visible':True,'canonical_task_running':True,'same_terminal_id':terminal_id,'disk_unchanged':(repo/'calc.txt').read_text()==original}
    # A toolbar popup with image context but no text or dirty buffers must still
    # appear in recovery. This is independent of the dirty-file predicate.
    page.get_by_label('Workspace layout',exact=True).get_by_role('button',name='Chat',exact=True).click()
    with context.expect_page() as context_popup:page.get_by_role('button',name='Detach workspace',exact=True).click()
    other=context_popup.value;other.on('dialog',lambda dialog:dialog.accept());other.get_by_label('Message',exact=True).wait_for()
    other.get_by_label('Message',exact=True).fill('');other.locator('input[type=file]').set_input_files({'name':'context-only.png','mimeType':'image/png','buffer':b'context-only image bytes'})
    other.get_by_text('context-only.png',exact=True).wait_for();slot=other.evaluate('window.name')
    eventually(lambda:any(row['slot']==slot and row['draft']['text']=='' and row['draft'].get('attachments') for row in journals()))
    other.close();page.get_by_role('button',name='Recover unsaved windows',exact=True).click()
    with context.expect_page() as context_reopened:page.get_by_role('button',name='Reopen window',exact=True).click()
    image_only=context_reopened.value;image_only.on('dialog',lambda dialog:dialog.accept());image_only.get_by_text('context-only.png',exact=True).wait_for()
    assert image_only.get_by_label('Message',exact=True).input_value()=='';assert image_only.evaluate('window.name')==slot
    report['context_only_closed_window_recovery']=True;report['toolbar_and_panel_detach']=True
    report['steps'].append('Actual panel and toolbar popups: close/reopen preserved draft/images; acknowledged return kept conflicting dirty files, active task and PTY; different main draft kept source open')
    page.screenshot(path=str(destination/'browser-return-main.png'));image_only.screenshot(path=str(destination/'context-only-recovered.png'));image_only.close()

def prove_quick_file(page,destination,report,repo,eventually,state,session):
    actor=state.identity.principal_by_id(session['owner']);pid=session['project_id']
    tree=state.delivery.effect(pid,str(repo),'worktree-create',{'branch':'codex/quick-file-proof','base':'main'})
    root=Path(tree['path']);(root/'isolated-only.txt').write_text('Actual isolated quick file source\n')
    (repo/'main-only.txt').write_text('Other checkout source\n')
    current=state.workspace.session(actor,session['id'],turns=False)
    state.workspace.update_session(actor,session['id'],revision=current['revision'],changes={'worktree_id':tree['id']})
    page.reload();page.get_by_label('Message',exact=True).wait_for();page.get_by_label('Workspace layout',exact=True).get_by_role('button',name='Workbench',exact=True).click()
    page.get_by_label('Execution folder '+str(root.resolve()),exact=True).wait_for()
    page.get_by_role('button',name='Files',exact=True).click();page.locator('.file-row').filter(has_text='calc.txt').click()
    editor=page.get_by_label('Editor for calc.txt',exact=True);editor.wait_for();editor.focus();editor.press('ControlOrMeta+A');page.keyboard.insert_text('Unsaved original retained across quick file open\n')
    page.get_by_role('button',name='Commands',exact=True).click()
    search=page.get_by_label('Search commands',exact=True);search.fill('quick file')
    page.get_by_role('button',name=re.compile(r'^Quick file open')).click()
    picker=page.get_by_role('dialog',name='Quick file open');query=picker.get_by_label('Find file by name',exact=True);query.fill('only')
    picker.get_by_role('option',name='isolated-only.txt',exact=True).wait_for()
    assert picker.get_by_role('option',name='main-only.txt',exact=True).count()==0
    for zoom in (100,200):
        page.set_viewport_size({'width':1440 if zoom==100 else 720,'height':900 if zoom==100 else 450})
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'))
        result=page.evaluate('''async()=>await axe.run(document.querySelector('[role=dialog]'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})''')
        assert not result['violations'],result['violations']
        option=picker.get_by_role('option',name='isolated-only.txt',exact=True)
        assert query.get_attribute('aria-activedescendant')==option.get_attribute('id') and option.get_attribute('aria-selected')=='true'
        bounds=query.bounding_box();assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=page.viewport_size['height']
        report.setdefault('quick_file_accessibility',[]).append({'zoom':zoom,'axe_violations':0,'named_combobox_selected_option':True,'visible_query':True})
    page.set_viewport_size({'width':1440,'height':900})
    query.press('Enter');page.get_by_label('Editor for isolated-only.txt',exact=True).wait_for()
    assert page.get_by_label('Editor for isolated-only.txt',exact=True).inner_text().strip()=='Actual isolated quick file source'
    page.locator('.editor-tabs').get_by_role('button',name='calc.txt ●',exact=True).click()
    assert editor.inner_text().strip()=='Unsaved original retained across quick file open'
    # The actual remappable chord also reaches the picker, without intercepting
    # terminal or remote input, and the visible menu remains its browser fallback.
    page.get_by_role('button',name='Quick file open',exact=True).focus();page.keyboard.press('ControlOrMeta+p')
    picker.wait_for();query.fill('isolated');picker.get_by_role('option',name='isolated-only.txt',exact=True).wait_for();query.press('Enter')
    page.get_by_label('Editor for isolated-only.txt',exact=True).wait_for()
    report['quick_file']={'searchable_command_palette':True,'actual_mod_p':True,'filename_only_current_worktree':True,'dirty_buffer_retained':True,'host_disk_unchanged':(root/'calc.txt').read_text().startswith('alpha=1')}
    report['steps'].append('Actual searchable Commands and ModP opened only the enrolled checkout file and retained unsaved editor content')
    page.screenshot(path=str(destination/'quick-file-worktree.png'))

def prove_session_presets(page,destination,report,eventually,state,owner,session):
    override=os.environ.get('TERMX_COMPOSER_STYLE_OVERRIDE')
    if override:page.add_style_tag(path=override);report['stylesheet_iteration_override']=override
    profile=state.agent_store.create_custom_agent(name='UI source guide',instructions='Frozen UI source instructions',provider_id='fixture',model='fixture',tools=['read_file'])
    state.authorization.claim_principal(owner,'custom_agent',profile['id'])
    page.get_by_role('button',name='New chat',exact=True).click()
    dialog=page.get_by_role('dialog',name='New conversation',exact=True)
    dialog.get_by_label('Conversation title',exact=True).fill('Preset-connected conversation')
    picker=dialog.get_by_label('New conversation agent preset',exact=True)
    picker.get_by_role('option',name='UI source guide',exact=True).wait_for(state='attached')
    picker.select_option(profile['id']);assert picker.input_value()==profile['id']
    dialog.get_by_role('button',name='Create conversation',exact=True).click()
    eventually(lambda:any(row['title']=='Preset-connected conversation' for row in state.agent_store.list_conversations()))
    created=next(row for row in state.agent_store.list_conversations() if row['title']=='Preset-connected conversation')
    selected=page.get_by_label('Session agent preset',exact=True)
    page.locator('details.session-tools').filter(has=selected).locator('summary').click()
    eventually(lambda:selected.input_value()==profile['id'])
    page.get_by_label('Message',exact=True).fill('Provider-free preset task')
    page.get_by_role('button',name='Send message',exact=True).click()
    eventually(lambda:bool(state.agent_store.active_conversation_tasks(created['id'])))
    task=state.agent_store.active_conversation_tasks(created['id'])[0]
    eventually(lambda:state.agent_store.get_task(task['id'])['status']=='running')
    eventually(lambda:selected.is_disabled());assert state.agent_store.task_agent(task)['instructions']==profile['instructions']
    state.agent_store.update_custom_agent(profile['id'],instructions='Edited library source',tools=['run_shell'])
    assert state.agent_store.task_agent(task)['instructions']==profile['instructions']
    page.get_by_role('button',name='Stop task',exact=True).click()
    eventually(lambda:state.agent_store.get_task(task['id'])['status']=='cancelled')
    eventually(lambda:not selected.is_disabled())
    page.get_by_role('button',name='Refresh preset binding',exact=True).click()
    eventually(lambda:state.workspace.store.get('conversation',created['id'])['custom_agent_revision']!=state.agent_store.task_agent(task)['workspace_revision'])
    selected.select_option('');eventually(lambda:state.agent_store.get_conversation(created['id'])['custom_agent_id'] is None)
    assert state.agent_store.get_conversation(session['id'])['custom_agent_id'] is None
    geometry=[]
    voice=page.locator('.voice-controls>details')
    for zoom in (100,200):
        page.set_viewport_size({'width':1440 if zoom==100 else 720,'height':900 if zoom==100 else 450})
        for expanded in (False,True):
            voice.evaluate('(element,open)=>element.open=open',expanded)
            page.add_script_tag(path=str(Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'))
            result=page.evaluate("async()=>await axe.run(document.querySelector('.chat-region'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
            assert not result['violations'],result['violations']
            page.screenshot(path=str(destination/('session-presets-'+str(zoom)+('-voice' if expanded else '')+'.png')))
            bounds_by_control={}
            for control in (page.get_by_label('Message',exact=True),page.get_by_role('button',name='Send message',exact=True)):
                bounds=control.bounding_box();assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=page.viewport_size['height'],bounds
                bounds_by_control[control.get_attribute('aria-label')]=bounds
            selected.focus();selected.scroll_into_view_if_needed();assert selected.is_visible()
            selected_clip=selected.evaluate("e=>{const a=e.getBoundingClientRect(),b=e.closest('.composer-settings').getBoundingClientRect();return {top:a.top,bottom:a.bottom,parentTop:b.top,parentBottom:b.bottom,height:b.height,visible:a.top>=b.top-1&&a.bottom<=b.bottom+1}}");assert selected_clip['visible'],selected_clip
            if expanded:
                account=page.get_by_label('Voice account',exact=True);account.focus();account.scroll_into_view_if_needed();assert account.is_visible()
                voice_bounds=account.bounding_box();assert voice_bounds and voice_bounds['y']>=0 and voice_bounds['y']+voice_bounds['height']<=page.viewport_size['height'],voice_bounds
                voice_clip=account.evaluate("e=>{const a=e.getBoundingClientRect(),b=e.closest('details').getBoundingClientRect();return {top:a.top,bottom:a.bottom,parentTop:b.top,parentBottom:b.bottom,height:b.height,visible:a.top>=b.top-1&&a.bottom<=b.bottom+1}}");assert voice_clip['visible'],voice_clip
                bounds_by_control['Voice account']=voice_bounds
                page.screenshot(path=str(destination/('session-presets-'+str(zoom)+'-voice-focused.png')))
            geometry.append({'zoom':zoom,'voice_expanded':expanded,'controls':bounds_by_control})
    page.get_by_role('button',name='Record dictation',exact=True).click()
    stop=page.get_by_role('button',name='Stop microphone',exact=True);stop.wait_for()
    voice.evaluate('(element)=>element.open=false')
    stop_bounds=stop.bounding_box();assert stop_bounds and stop_bounds['y']>=0 and stop_bounds['y']+stop_bounds['height']<=page.viewport_size['height'],stop_bounds
    assert stop.is_visible();stop.click();stop.wait_for(state='hidden')
    report['persistent_microphone_stop']={'visible_after_fold_collapse':True,'zoom':200,'browser_input':'synthetic Chromium device; no physical microphone'}
    report['composer_geometry']=geometry
    page.set_viewport_size({'width':1440,'height':900});page.screenshot(path=str(destination/'session-presets.png'))
    report['preset_flow']={'new_session_picker':True,'actual_owned_session_binding':True,'active_selector_disabled':True,'task_snapshot_frozen_after_library_edit':True,'explicit_refresh_after_stop':True,'clear_only_selected_session':True,'other_session_unchanged':True,'axe_100_200_violations':0,'native_select_method':'actual form select_option + exact selected value; OS native dropdown popup is not synthesized'}
    report['steps'].append('Actual new-conversation preset selection, immutable active run, stop/explicit renewal and session-only clear')

def run(destination,existing_checkout=None,pr_number=28,windows_only=False):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'host_platform':sys.platform,'proof':'session-agent-presets' if windows_only=='preset' else 'quick-file-open' if windows_only=='quick' else 'browser-window-lifecycle' if windows_only=='lifecycle' else 'two-window-buffers' if windows_only else 'git-delivery-and-windows','provider_queries':0,'external_mutations':0,'fixture_adapter_calls':0,'errors':[],'steps':[]}
    with tempfile.TemporaryDirectory(prefix='termx-delivery-ui-') as temporary:
        scratch=Path(temporary);repo=scratch/'repo';repo.mkdir();remote=scratch/'remote.git';remote.mkdir()
        git(repo,'init','-b','main');git(repo,'config','user.name','Isolated UI fixture');git(repo,'config','user.email','fixture@example.invalid')
        original='alpha=1\n'+''.join('line%02d\n'%i for i in range(2,30))+'tail=old\n'
        (repo/'calc.txt').write_text(original);git(repo,'add','.');git(repo,'commit','-m','Initial fixture')
        git(remote,'init','--bare');git(repo,'remote','add','origin',str(remote));git(repo,'push','--set-upstream','origin','main')
        os.environ.update(TERMX_CONFIG_DIR=str(scratch/'config'),TERMX_AGENTS_DIR=str(scratch/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderTurn
        from termx.agent.secrets import CredentialStore
        from test_agent import FakeAdapter
        finish=threading.Event()
        class FixtureAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                report['fixture_adapter_calls']+=1
                while not finish.is_set():await asyncio.sleep(.05)
                return ProviderTurn('fixture','Finished',[],{},[])
        state=AppState(passcode=None,credentials=CredentialStore({}));state.agent._adapter=lambda _:FixtureAdapter()
        owner=state.identity.setup_owner('delivery-owner','delivery-ui-password-123')
        project=state.projects.register(str(repo),name='Isolated Git delivery')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='Provider-free fixture',base_url='http://127.0.0.1:9999/v1',model='fixture',capabilities=['shell'],api_key='not-a-provider-credential')
        app=create_app(state,web_dir=Path(os.environ.get('TERMX_WORKSPACE_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))))
        session=state.workspace.create_session(owner,title='Delivery and preserved state',project_id=project['id'],cwd=project['path'],provider_id='fixture',model='fixture',mode='ask')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));loops=[]
        async def serve():loops.append(asyncio.get_running_loop());await server.serve(sockets=[listener])
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start();deadline=monotonic()+25
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        page=None
        def eventually(check,seconds=20):
            deadline=monotonic()+seconds
            while not check():
                if monotonic()>deadline:raise AssertionError('Fixture state did not converge')
                sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'] if windows_only=='preset' else [])
                context=browser.new_context(viewport={'width':1920,'height':1080},reduced_motion='reduce')
                if windows_only=='preset':context.grant_permissions(['microphone'],origin=f'http://127.0.0.1:{port}')
                page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                def record_socket(socket):
                    if '/pty' not in socket.url:return
                    def sent(payload):
                        try:
                            value=json.loads(payload)
                            if value.get('type')=='input':report.setdefault('pty_inputs',[]).append(value['data'])
                        except (ValueError,TypeError):pass
                    socket.on('framesent',sent)
                page.on('websocket',record_socket)
                try:
                    page.goto(f'http://127.0.0.1:{port}/?session={session["id"]}')
                    page.get_by_label('Username',exact=True).fill('delivery-owner');page.get_by_label('Password',exact=True).fill('delivery-ui-password-123');page.get_by_role('button',name='Continue with password').click()
                    page.wait_for_function('(()=>{const e=document.querySelector("textarea[aria-label=Message]");return !!e&&!e.disabled})()',timeout=60000)
                    if windows_only=='preset':
                        prove_session_presets(page,destination,report,eventually,state,owner,session);assert not report['errors'];browser.close();return
                    if windows_only:
                        page.keyboard.press('Control+2');page.get_by_label('Execution folder '+str(repo.resolve()),exact=True).wait_for();(prove_quick_file(page,destination,report,repo,eventually,state,session) if windows_only=='quick' else prove_window_lifecycle(page,context,destination,report,repo,original,eventually,state,session) if windows_only=='lifecycle' else prove_window_buffers(page,context,destination,report,repo,original,eventually));assert not report['errors'];browser.close();return
                    page.keyboard.press('Control+2');page.get_by_role('button',name='Git changes',exact=True).click()
                    delivery=page.get_by_role('region',name='Git delivery')
                    delivery.get_by_text('Branches & parallel worktrees',exact=True).click()
                    for branch in ('codex/ui-one','codex/ui-two'):
                        delivery.get_by_label('Branch name',exact=True).fill(branch)
                        delivery.get_by_role('button',name='Create isolated worktree',exact=True).click()
                        eventually(lambda:len(state.delivery.worktrees(project['id']))==(1 if branch.endswith('one') else 2))
                    trees={row['branch']:row for row in state.delivery.worktrees(project['id'])};a=trees['codex/ui-one'];b=trees['codex/ui-two']
                    report['steps'].append('Created two actual isolated worktrees through UI')
                    page.get_by_role('button',name='Refresh checkouts',exact=True).click();page.get_by_label('Session checkout',exact=True).select_option(a['id'])
                    eventually(lambda:state.workspace.store.get('conversation',session['id']).get('worktree_id')==a['id']);page.get_by_label('Execution folder '+str(Path(a['path']).resolve()),exact=True).wait_for()
                    page.get_by_role('button',name='Files',exact=True).click();page.locator('.file-row').filter(has_text='calc.txt').click()
                    editor=page.get_by_label('Editor for calc.txt',exact=True);editor.wait_for()
                    def edit(text):
                        editor.focus();editor.press('ControlOrMeta+A');page.keyboard.insert_text(text)
                    def save(expected,target):
                        page.get_by_role('button',name='Save file',exact=True).click()
                        try:eventually(lambda:(Path(target)/'calc.txt').read_text()==expected)
                        except AssertionError:
                            report['save_mismatch']={'expected':repr(expected),'actual':repr((Path(target)/'calc.txt').read_text())};raise
                    desired_a=original.replace('alpha=1','alpha=2').replace('tail=old','tail=one')
                    edit(desired_a)
                    composer=page.get_by_label('Message',exact=True);composer.fill('Keep this provider-free fixture task active');page.get_by_role('button',name='Send message',exact=True).click()
                    eventually(lambda:bool(state.agent_store.active_conversation_tasks(session['id'])))
                    task=state.agent_store.active_conversation_tasks(session['id'])[0];eventually(lambda:state.agent_store.get_task(task['id'])['status']=='running')
                    composer.fill('Unsent next instruction survives layout moves')
                    page.get_by_role('button',name='New terminal',exact=True).click();terminal_select=page.get_by_label('Terminal session',exact=True)
                    page.wait_for_function(r'()=>!!document.querySelector(".terminal-toolbar select")?.value')
                    terminal_id=terminal_select.input_value()
                    page.get_by_text('Connected',exact=True).last.wait_for()
                    page.wait_for_function("()=>document.querySelector('.xterm-rows')?.innerText.includes(' % ')")
                    def command(value):
                        field=page.locator('.xterm-helper-textarea');field.focus();page.keyboard.type(value,delay=3);field.press('Enter')
                    command('echo PTY_CONTINUITY_1; sleep 30')
                    page.locator('.xterm-rows').get_by_text('PTY_CONTINUITY_1',exact=True).first.wait_for(timeout=15000)
                    page.keyboard.press('Control+1');sleep(.6)
                    assert composer.input_value()=='Unsent next instruction survives layout moves'
                    assert state.agent_store.get_task(task['id'])['status']=='running'
                    page.keyboard.press('Control+2')
                    assert 'alpha=2' in editor.inner_text() and 'tail=one' in editor.inner_text()
                    assert (Path(a['path'])/'calc.txt').read_text()==original
                    assert terminal_select.input_value()==terminal_id and state.agent_store.get_task(task['id'])['status']=='running'
                    assert composer.input_value()=='Unsent next instruction survives layout moves'
                    report['layout_preservation']={'task_id':task['id'],'task_status':'running','draft':True,'dirty_buffer':True,'terminal_id':terminal_id,'same_terminal':True}
                    page.get_by_role('button',name='Interrupt',exact=True).click();save(desired_a,a['path'])
                    command('python3 -c "from pathlib import Path; assert Path(\'calc.txt\').read_text().startswith(\'alpha=2\'); print(\'WORKTREE_A_TEST_PASS\')"')
                    page.locator('.xterm-rows').get_by_text('WORKTREE_A_TEST_PASS',exact=True).last.wait_for(timeout=15000)
                    command('python3 -c "assert 1 == 2, \'EXPECTED_FAILED_LOCAL_CHECK\'"')
                    page.locator('.xterm-rows').get_by_text('AssertionError: EXPECTED_FAILED_LOCAL_CHECK',exact=False).last.wait_for(timeout=15000)
                    report['steps'].append('Actual editor save + terminal passing/failing local checks in worktree A')
                    page.get_by_role('button',name='Git changes',exact=True).click();delivery.get_by_role('button',name='Refresh',exact=True).click()
                    delivery.locator('.git-row>button').filter(has_text='calc.txt').click()
                    patch=delivery.get_by_label('Selected Git patch',exact=True).input_value();lines=patch.splitlines();hunks=[i for i,line in enumerate(lines) if line.startswith('@@')];assert len(hunks)==2
                    first='\n'.join(lines[:hunks[1]])+'\n';delivery.get_by_label('Selected Git patch',exact=True).fill(first);delivery.get_by_role('button',name='Stage selected patch',exact=True).click()
                    eventually(lambda:'alpha=2' in git(a['path'],'diff','--cached'))
                    assert 'tail=one' not in git(a['path'],'diff','--cached')
                    delivery.locator('.git-row>button').filter(has_text='calc.txt').click();delivery.get_by_role('button',name='Unstage selected patch',exact=True).click();eventually(lambda:not git(a['path'],'diff','--cached'))
                    delivery.get_by_role('button',name='Stage',exact=True).click();eventually(lambda:'tail=one' in git(a['path'],'diff','--cached'))
                    delivery.get_by_label('Commit message',exact=True).fill('UI fixture one');delivery.get_by_role('button',name='Commit staged changes',exact=True).click();eventually(lambda:git(a['path'],'log','-1','--format=%s')=='UI fixture one')
                    def push(branch,path):
                        before=git(path,'rev-parse','HEAD');delivery.get_by_role('button',name='Review push',exact=True).click();confirmation=delivery.get_by_role('region',name='Exact delivery confirmation');confirmation.wait_for()
                        assert branch in confirmation.inner_text();assert before in confirmation.inner_text()
                        def pushed():
                            result=subprocess.run(['git','-C',str(remote),'rev-parse',branch],capture_output=True,text=True)
                            return result.returncode==0 and result.stdout.strip()==before
                        confirmation.get_by_role('button',name='Confirm this operation',exact=True).click();eventually(pushed)
                    push('codex/ui-one',a['path']);report['steps'].append('Selective hunk stage/unstage, whole-file stage, commit and exact-confirmed push A')
                    page.get_by_role('button',name='Stop task',exact=True).click();eventually(lambda:state.agent_store.get_task(task['id'])['status']=='cancelled')
                    page.get_by_label('Session checkout',exact=True).select_option(b['id']);eventually(lambda:state.workspace.store.get('conversation',session['id']).get('worktree_id')==b['id']);page.get_by_label('Execution folder '+str(Path(b['path']).resolve()),exact=True).wait_for()
                    page.get_by_role('button',name='Files',exact=True).click();page.locator('.file-row').filter(has_text='calc.txt').click();editor=page.get_by_label('Editor for calc.txt',exact=True);page.locator('.editor-tab.selected').filter(has=page.get_by_role('button',name='calc.txt',exact=True)).filter(has_not_text='●').wait_for();page.wait_for_function('(root)=>document.querySelector(".editor-tab.selected")?.title===root+"/calc.txt"',arg=str(Path(b['path']).resolve()))
                    desired_b=original.replace('alpha=1','alpha=3').replace('tail=old','tail=two');edit(desired_b);save(desired_b,b['path'])
                    page.get_by_role('button',name='New terminal',exact=True).click();page.wait_for_function('(old)=>!!document.querySelector(".terminal-toolbar select")?.value&&document.querySelector(".terminal-toolbar select").value!==old',arg=terminal_id);page.wait_for_function('()=>document.querySelector(".terminal-region")?.dataset.connectedSession===document.querySelector(".terminal-toolbar select")?.value');page.wait_for_function("()=>document.querySelector('.xterm-rows')?.innerText.includes(' % ')");command('python3 -c "from pathlib import Path; assert Path(\'calc.txt\').read_text().startswith(\'alpha=3\'); print(\'WORKTREE_B_TEST_PASS\')"')
                    page.locator('.xterm-rows').get_by_text('WORKTREE_B_TEST_PASS',exact=True).last.wait_for(timeout=15000)
                    page.get_by_role('button',name='Git changes',exact=True).click();delivery.get_by_role('button',name='Refresh',exact=True).click();delivery.get_by_role('button',name='Stage',exact=True).click()
                    delivery.get_by_label('Commit message',exact=True).fill('UI fixture two');delivery.get_by_role('button',name='Commit staged changes',exact=True).click();eventually(lambda:git(b['path'],'log','-1','--format=%s')=='UI fixture two');push('codex/ui-two',b['path'])
                    assert (repo/'calc.txt').read_text()==original;report['steps'].append('Independent editor/test/stage/commit/push B; parent source untouched')
                    # An actual remote writer creates divergence; no synthetic Git API.
                    other=scratch/'remote-writer';subprocess.run(['git','clone','--branch','codex/ui-one',str(remote),str(other)],check=True,capture_output=True)
                    git(other,'config','user.name','Independent remote fixture');git(other,'config','user.email','remote@example.invalid');(other/'calc.txt').write_text(desired_a.replace('tail=one','tail=remote'));git(other,'add','.');git(other,'commit','-m','Remote divergent change');git(other,'push')
                    page.get_by_label('Session checkout',exact=True).select_option(a['id']);eventually(lambda:state.workspace.store.get('conversation',session['id']).get('worktree_id')==a['id']);page.get_by_label('Execution folder '+str(Path(a['path']).resolve()),exact=True).wait_for()
                    page.get_by_role('button',name='Files',exact=True).click();page.locator('.file-row').filter(has_text='calc.txt').click();editor=page.get_by_label('Editor for calc.txt',exact=True);page.wait_for_function('(root)=>document.querySelector(".editor-tab.selected")?.title===root+"/calc.txt"',arg=str(Path(a['path']).resolve()))
                    local=desired_a.replace('alpha=2','alpha=22');edit(local);save(local,a['path'])
                    page.get_by_role('button',name='Git changes',exact=True).click();delivery.get_by_role('button',name='Refresh',exact=True).click();delivery.get_by_role('button',name='Stage',exact=True).click();delivery.get_by_label('Commit message',exact=True).fill('Local divergent change');delivery.get_by_role('button',name='Commit staged changes',exact=True).click();eventually(lambda:git(a['path'],'log','-1','--format=%s')=='Local divergent change')
                    terminal_select.select_option(terminal_id);page.locator('.terminal-region[data-connected-session="'+terminal_id+'"]').wait_for();command('python3 -c "from pathlib import Path; Path(\'keep.txt\').write_text(\'preserved dirty user file\'); print(\'DIRTY_CREATED\')"');eventually(lambda:(Path(a['path'])/'keep.txt').exists())
                    head=git(a['path'],'rev-parse','HEAD');delivery.get_by_role('button',name='Pull fast forward',exact=True).click();delivery.get_by_role('alert').filter(has_text='fast-forward').wait_for(timeout=15000)
                    assert git(a['path'],'rev-parse','HEAD')==head and (Path(a['path'])/'calc.txt').read_text()==local
                    delivery.get_by_role('button',name='Review worktree cleanup',exact=True).click();delivery.get_by_role('region',name='Exact delivery confirmation').get_by_role('button',name='Confirm this operation',exact=True).click();delivery.get_by_role('alert').filter(has_text='Dirty worktree retained').wait_for(timeout=15000)
                    assert (Path(a['path'])/'keep.txt').read_text()=='preserved dirty user file';report['steps'].append('Diverged pull and dirty cleanup visibly refused; HEAD/source/dirty file preserved')
                    page.screenshot(path=str(destination/'dirty-cleanup-refusal.png'))
                    delivery.get_by_role('region',name='Exact delivery confirmation').get_by_role('button',name='Cancel',exact=True).click()
                    delivery.get_by_label('Git checkout',exact=True).select_option(b['id']);delivery.get_by_role('button',name='Refresh',exact=True).click();delivery.get_by_role('button',name='Review worktree cleanup',exact=True).click();delivery.get_by_role('region',name='Exact delivery confirmation').get_by_role('button',name='Confirm this operation',exact=True).click();eventually(lambda:not Path(b['path']).exists())
                    page.wait_for_function('()=>document.querySelector(\'select[aria-label="Git checkout"]\')?.value===\'\'');delivery.get_by_text('No uncommitted changes.',exact=True).wait_for();assert delivery.get_by_role('alert').count()==0;assert state.workspace.store.get('conversation',session['id'])['worktree_id']==a['id'];report['cleanup_delivery_target_recovered']=True
                    assert git(repo,'show-ref','--verify','refs/heads/codex/ui-two');report['steps'].append('Clean B cleanup confirmed; branch retained; dirty A remains')
                    prove_window_buffers(page,context,destination,report,a['path'],local,eventually)
                    if existing_checkout and shutil.which('gh'):
                        pr_project=state.projects.register(str(Path(existing_checkout).resolve()),name='Existing draft PR read-only')
                        pr_session=state.workspace.create_session(owner,title='Existing draft PR inspection',project_id=pr_project['id'])
                        page.goto(f'http://127.0.0.1:{port}/?session={pr_session["id"]}&layout=workbench');page.get_by_role('button',name='Git changes',exact=True).click();delivery=page.get_by_role('region',name='Git delivery')
                        delivery.get_by_text('Pull request & checks',exact=True).click();delivery.get_by_label('Pull request number',exact=True).fill(str(pr_number));delivery.get_by_role('button',name='Inspect PR & checks',exact=True).click()
                        article=delivery.locator('article');article.wait_for(timeout=60000);pull=state.delivery.github.read(str(existing_checkout),pr_number)
                        report['existing_pr']={'url':pull['url'],'number':pull['number'],'state':pull['state'],'checks':[{key:check.get(key) for key in ('name','context','state','status','conclusion')} for check in pull.get('statusCheckRollup',[])]}
                        report['steps'].append('Read-only actual draft PR/checks inspected through UI; no publication/comment');page.screenshot(path=str(destination/'actual-pr-checks.png'))
                    report['parent_unchanged']=(repo/'calc.txt').read_text()==original;report['two_worktree_pushes']=True;report['selective_hunks']=True;report['dirty_cleanup_preserved']=True;report['diverged_pull_preserved']=True
                    assert not report['errors'];browser.close()
                except BaseException:
                    page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                    raise

        except BaseException as error:
            report['failure']=str(error)
            if page:
                try:page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                except BaseException:pass
            raise
        finally:
            finish.set();(destination/'report.json').write_text(json.dumps(report,indent=2));server.should_exit=True;worker.join(timeout=20)
    print(json.dumps({key:value for key,value in report.items() if key!='pty_inputs'},indent=2))

if __name__=='__main__':run(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None,int(sys.argv[3]) if len(sys.argv)>3 else 28,windows_only=sys.argv[4] if len(sys.argv)>4 and sys.argv[4] in {'buffers','lifecycle','quick','preset'} else False)
