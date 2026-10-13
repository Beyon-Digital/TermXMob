"""Opt-in hosted desktop swarm proof against the real isolated host runtime.

A no-network adapter holds read-only workers in-flight. Agent workers use the
actual snapshot isolation and delegated plan approval path; no paid provider is contacted.
"""
from __future__ import annotations
import asyncio,json,os,socket,sys,tempfile,threading,traceback
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright,expect
from termx.agent.providers import ProviderTurn
from termx.agent.secrets import CredentialStore
from workspace_ui_snapshot import snapshot_ui

class Adapter:
    def __init__(self):self.release=threading.Event();self.started=[]
    async def test(self):return 'No-network swarm fixture'
    async def plan(self,prompt,cwd,manifest):
        return {'summary':prompt,'steps':['Inspect the enrolled project'],'tools':[],'risks':[]},'swarm-plan'
    async def turn(self,**kwargs):
        self.started.append(kwargs['prompt'])
        while not self.release.is_set():await asyncio.sleep(.05)
        return ProviderTurn('swarm-fixture','Fixture finished',[],{},[])

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'hosted-desktop-actual-swarm-api','paid_queries':0,'errors':[],'http':[],'states':[]}
    with tempfile.TemporaryDirectory(prefix='termx-swarm-ui-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        adapter=Adapter();state=AppState(passcode=None,credentials=CredentialStore(memory={}),adapter_factory=lambda *_:adapter)
        owner=state.identity.setup_owner('swarm-owner','swarm-fixture-password-123');folder=root/'project';folder.mkdir();(folder/'read.txt').write_text('Enrolled fixture only\n')
        project=state.projects.register(str(folder),name='Swarm proof')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No-network swarm fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['shell'],api_key='fixture-not-a-provider-key')
        ui=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))));report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        app=create_app(state,web_dir=ui);session=state.workspace.create_session(owner,title='Actual swarm proof',project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture',mode='agent')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1];holder={}
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
        async def serve():holder['loop']=asyncio.get_running_loop();await host.serve(sockets=[listener])
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start()
        def wait(predicate,message='Runtime did not settle'):
            deadline=monotonic()+40
            while not predicate():
                if monotonic()>deadline:raise AssertionError(message)
                sleep(.05)
        wait(lambda:host.started,'Host did not start')
        def execute(coro):return asyncio.run_coroutine_threadsafe(coro,holder['loop']).result(40)
        page=None
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.on('response',lambda response:report['http'].append({'path':response.url.split(str(port),1)[-1],'status':response.status,'body':response.request.post_data_json}) if response.request.method=='POST' and '/tasks/' in response.url else None)
                page.goto(f'http://127.0.0.1:{port}/?session={session["id"]}');page.get_by_label('Username',exact=True).fill('swarm-owner');page.get_by_label('Password',exact=True).fill('swarm-fixture-password-123');page.get_by_role('button',name='Continue with password',exact=True).click();expect(page.get_by_label('Message',exact=True)).to_be_enabled(timeout=60000)
                access=next(cookie['value'] for cookie in context.cookies() if cookie['name']=='termx_access');sid=state.identity.resolve(access).session_id
                parent=execute(state.workspace.send(owner,session['id'],prompt='Coordinate this agent team',request_id='swarm-browser-root',managed_session_id=sid));assert parent['status']=='awaiting_approval'
                page.get_by_role('button',name='Supervise',exact=True).first.click();dialog=page.get_by_role('dialog',name='Task supervision',exact=True);dialog.wait_for();dialog.get_by_role('button',name='Build agent team',exact=True).click()
                expect(dialog.get_by_role('button',name='Create team',exact=True)).to_be_disabled();expect(dialog.get_by_label('Worker 1 access',exact=True)).to_have_value('ask')
                dialog.get_by_label('Worker 1 task',exact=True).fill('Inspect accessibility');dialog.get_by_label('Worker 2 name',exact=True).fill('Editor');dialog.get_by_label('Worker 2 task',exact=True).fill('Fix keyboard navigation');dialog.get_by_label('Worker 2 access',exact=True).select_option('agent')
                dialog.get_by_role('button',name='Create team',exact=True).click();dialog.get_by_text('Team created. Select a worker to inspect or steer it.',exact=True).wait_for(timeout=30000);page.screenshot(path=str(destination/'swarm-builder.png'));dialog.get_by_role('button',name='Close team builder',exact=True).click()
                children=state.agent_store.children(parent['id']);assert len(children)==2;reader=next(child for child in children if child['mode']=='ask');editor=next(child for child in children if child['mode']=='agent');assert reader['cwd']==str(folder);assert editor['cwd']!=str(folder);assert Path(editor['cwd'],'read.txt').read_text()=='Enrolled fixture only\n'
                wait(lambda:all(state.agent_store.get_task(child['id'])['status']=='running' for child in children))
                tree=dialog.get_by_role('navigation',name='Task tree',exact=True);tree.get_by_role('button',name=__import__('re').compile('Researcher · Inspect accessibility')).click();expect(dialog.get_by_text('read-only workspace · edits require review',exact=True)).to_be_visible();expect(dialog.locator('code').filter(has_text=str(folder))).to_be_visible()
                dialog.get_by_label('Steer selected task',exact=True).fill('Check focus order');dialog.get_by_role('button',name='Steer task',exact=True).click();wait(lambda:any(event['type']=='task.steered' and event['payload']['message']=='Check focus order' for event in state.agent_store.events(reader['id'])));expect(dialog.get_by_label('Steer selected task',exact=True)).to_have_value('')
                dialog.get_by_role('button',name='Cancel task and descendants',exact=True).click();wait(lambda:state.agent_store.get_task(reader['id'])['status']=='cancelled');assert state.agent_store.get_task(parent['id'])['status']=='awaiting_approval','Finishing a child must preserve the coordinator pending plan approval'
                tree.get_by_role('button',name=__import__('re').compile('Editor · Fix keyboard navigation')).click();expect(dialog.get_by_text('snapshot workspace · edits require review',exact=True)).to_be_visible();expect(dialog.locator('code').filter(has_text=editor['cwd'])).to_be_visible()
                # Geometry and accessible input labels on actual expanded swarm UI.
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                page.add_script_tag(path=str(axe))
                def audit(label):
                    results=page.evaluate('async()=>{const summarize=r=>r.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target),details:v.nodes.map(n=>n.failureSummary)}));return {wcag:summarize(await axe.run(document,{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa"]}})),landmarks:summarize(await axe.run(document,{runOnly:{type:"rule",values:["landmark-unique","region"]}}))}}')
                    report['states'].append({'label':label,'axe':results})
                for width in [1440,720,390]:
                    page.set_viewport_size({'width':width,'height':900})
                    for theme in ['dark','light']:
                        page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme);audit('supervision-'+theme+'-'+str(width))
                        geometry=dialog.evaluate('(el)=>({documentOverflow:document.documentElement.scrollWidth>innerWidth,dialogOverflow:el.scrollWidth>el.clientWidth+1,width:innerWidth})');assert not geometry['documentOverflow'] and not geometry['dialogOverflow'],geometry;report['states'][-1]['geometry']=geometry;page.screenshot(path=str(destination/f'swarm-{theme}-{width}.png'))
                page.evaluate('document.documentElement.dataset.theme="dark"')
                tree.get_by_role('button',name=__import__('re').compile('^Coordinate this agent team')).click();
                with page.expect_response(lambda response:response.url.endswith('/tasks/'+parent['id']+'/cancel') and response.request.method=='POST') as parent_cancel:
                    dialog.get_by_role('button',name='Cancel task and descendants',exact=True).click()
                assert parent_cancel.value.status==200
                wait(lambda:state.agent_store.get_task(parent['id'])['status']=='cancelled');wait(lambda:state.agent_store.get_task(editor['id'])['status']=='cancelled','Parent cancellation left isolated editor active');assert all(state.agent_store.get_task(child['id'])['status']=='cancelled' for child in children)
                # Compact sheet must expose real keyboard focus and restore the trigger on Escape.
                dialog.get_by_role('button',name='Close',exact=True).click();trigger=page.get_by_role('button',name='Session options',exact=True);trigger.click();settings=page.get_by_role('dialog',name='Session settings',exact=True);settings.wait_for();expect(settings).to_have_class(__import__('re').compile('settings-sheet'))
                for theme in ['dark','light']:
                    page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme);page.screenshot(path=str(destination/f'settings-{theme}-390.png'));audit('settings-'+theme+'-390')
                close=settings.get_by_role('button',name='Close',exact=True)
                for _ in range(80):
                    page.keyboard.press('Tab')
                    if close.evaluate('(el)=>el===document.activeElement'):break
                expect(close).to_be_focused();focus=close.evaluate('(el)=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2),style=getComputedStyle(el);return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,visible:r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth,uncovered:hit===el||el.contains(hit),outline:style.outlineStyle,outlineWidth:style.outlineWidth}}');assert focus['visible'] and focus['uncovered'],focus;report['settings_close_focus']=focus;page.screenshot(path=str(destination/'settings-close-keyboard-focus.png'))
                page.keyboard.press('Escape');expect(settings).not_to_be_visible();report['settings_focus_after_escape']=page.evaluate('document.activeElement?.outerHTML.slice(0,500)');expect(trigger).to_be_focused();report['settings_escape_restores_focus']=True;page.evaluate('document.documentElement.dataset.theme="dark"')
                violations=[row for row in report['states'] if row['axe']['wcag'] or row['axe']['landmarks']];assert not violations,violations
                report['tasks']={'parent':parent['id'],'reader':reader['id'],'editor':editor['id'],'reader_mode':reader['mode'],'editor_mode':editor['mode'],'isolated_editor':True,'all_cancelled':True};assert not report['errors'];assert any(row['path'].endswith('/swarm') and row['status']==200 for row in report['http']);report['status']='passed';context.close();browser.close()
        except BaseException as error:
            report['error']=str(error);report['traceback']=traceback.format_exc()
            if 'parent' in locals():report['terminal_task_states']={row['id']:state.agent_store.get_task(row['id'])['status'] for row in [parent,*state.agent_store.children(parent['id'])]}
            report['status']='failed'
            if page:
                try:page.screenshot(path=str(destination/'failure.png'));report['visible_text']=page.locator('body').inner_text()
                except Exception:pass
            raise
        finally:
            adapter.release.set();host.should_exit=True;worker.join(15);(destination/'report.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':run(sys.argv[1] if len(sys.argv)>1 else 'scratch/swarm-proof')
