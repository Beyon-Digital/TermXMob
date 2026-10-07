"""Opt-in actual local Git/workbench UI proof; temporary repos, no publication.

Run with bundled managed Chromium. The only external request is an explicitly
requested read-only inspection of the existing draft PR when a checkout is given.
"""
from __future__ import annotations
import asyncio,json,os,shutil,socket,subprocess,sys,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright


def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()


def run(destination,existing_checkout=None,pr_number=28):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'provider_queries':0,'external_mutations':0,'fixture_adapter_calls':0,'errors':[],'steps':[]}
    with tempfile.TemporaryDirectory(prefix='termx-delivery-ui-') as temporary:
        scratch=Path(temporary);repo=scratch/'repo';repo.mkdir();remote=scratch/'remote.git';remote.mkdir()
        git(repo,'init','-b','main');git(repo,'config','user.name','Isolated UI fixture');git(repo,'config','user.email','fixture@example.invalid')
        original='alpha=1\n'+''.join('line%02d\n'%i for i in range(2,30))+'tail=old\n'
        (repo/'calc.txt').write_text(original);git(repo,'add','.');git(repo,'commit','-m','Initial fixture')
        git(remote,'init','--bare');git(repo,'remote','add','origin',str(remote));git(repo,'push','--set-upstream','origin','main')
        os.environ.update(TERMX_CONFIG_DIR=str(scratch/'config'),TERMX_AGENTS_DIR=str(scratch/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderTurn
        from test_agent import FakeAdapter
        finish=threading.Event()
        class FixtureAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                report['fixture_adapter_calls']+=1
                while not finish.is_set():await asyncio.sleep(.05)
                return ProviderTurn('fixture','Finished',[],{},[])
        state=AppState(passcode=None);state.agent._adapter=lambda _:FixtureAdapter()
        owner=state.identity.setup_owner('delivery-owner','delivery-ui-password-123')
        project=state.projects.register(str(repo),name='Isolated Git delivery')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='Provider-free fixture',base_url='http://127.0.0.1:9999/v1',model='fixture',capabilities=['shell'],api_key='not-a-provider-credential')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        session=state.workspace.create_session(owner,title='Delivery and preserved state',project_id=project['id'],provider_id='fixture',model='fixture',mode='ask')
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
                browser=playwright.chromium.launch(headless=True)
                context=browser.new_context(viewport={'width':1920,'height':1080},reduced_motion='reduce')
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
                    page.locator('.xterm-rows').get_by_text('PTY_CONTINUITY_1',exact=False).first.wait_for(timeout=15000)
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
                    page.locator('.xterm-rows').get_by_text('WORKTREE_A_TEST_PASS',exact=False).last.wait_for(timeout=15000)
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
                    page.locator('.xterm-rows').get_by_text('WORKTREE_B_TEST_PASS',exact=False).last.wait_for(timeout=15000)
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
                    delivery.get_by_label('Git checkout',exact=True).select_option(b['id']);delivery.get_by_role('button',name='Review worktree cleanup',exact=True).click();delivery.get_by_role('region',name='Exact delivery confirmation').get_by_role('button',name='Confirm this operation',exact=True).click();eventually(lambda:not Path(b['path']).exists())
                    assert git(repo,'show-ref','--verify','refs/heads/codex/ui-two');report['steps'].append('Clean B cleanup confirmed; branch retained; dirty A remains')
                    page.get_by_role('button',name='Files',exact=True).click();page.locator('.file-row').filter(has_text='calc.txt').click()
                    page.wait_for_function('(root)=>document.querySelector(".editor-tab.selected")?.title===root+"/calc.txt"',arg=str(Path(a['path']).resolve()))
                    editor=page.get_by_label('Editor for calc.txt',exact=True);main_unsaved=local+'# main unsaved\n';edit(main_unsaved)
                    with context.expect_page() as detached:
                        page.get_by_role('button',name='Detach workspace',exact=True).click()
                    child=detached.value;child.get_by_label('Message',exact=True).wait_for();child.get_by_role('button',name='Workbench',exact=True).click();child.get_by_label('Execution folder '+str(Path(a['path']).resolve()),exact=True).wait_for()
                    child.get_by_role('button',name='Files',exact=True).click();child.locator('.file-row').filter(has_text='calc.txt').click();child_editor=child.get_by_label('Editor for calc.txt',exact=True);child_editor.wait_for();child_editor.focus();child_editor.press('ControlOrMeta+A');child.keyboard.insert_text(local+'# detached unsaved\n')
                    def persisted_slots():
                        return page.evaluate('''async()=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('termx-workspace-buffer-state',2);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});const tx=db.transaction('slots','readonly');const values=await new Promise(resolve=>{const r=tx.objectStore('slots').getAll();r.onsuccess=()=>resolve(r.result)});db.close();return values}''')
                    eventually(lambda:any(any('# main unsaved' in buffer['content'] for buffer in slot) for slot in persisted_slots()) and any(any('# detached unsaved' in buffer['content'] for buffer in slot) for slot in persisted_slots()))
                    page.on('dialog',lambda dialog:dialog.accept());child.on('dialog',lambda dialog:dialog.accept());page.reload();child.reload()
                    page.get_by_label('Editor for calc.txt',exact=True).wait_for();child.get_by_label('Editor for calc.txt',exact=True).wait_for()
                    assert '# main unsaved' in page.get_by_label('Editor for calc.txt',exact=True).inner_text();assert '# detached unsaved' in child.get_by_label('Editor for calc.txt',exact=True).inner_text();assert (Path(a['path'])/'calc.txt').read_text()==local
                    report['two_window_dirty_reload']={'independent_slots':True,'main_draft_preserved':True,'detached_draft_preserved':True,'disk_unchanged':True};report['steps'].append('Actual detached window: concurrent different dirty buffers independently persisted and restored after both reloads')
                    page.screenshot(path=str(destination/'main-dirty-recovered.png'));child.screenshot(path=str(destination/'detached-dirty-recovered.png'));child.close()
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
    print(json.dumps(report,indent=2))

if __name__=='__main__':run(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None,int(sys.argv[3]) if len(sys.argv)>3 else 28)
