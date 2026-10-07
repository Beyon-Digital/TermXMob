"""Opt-in real hosted prompt queue/interrupt/jump proof on an immutable UI.

The canonical AgentManager uses a no-network fixture provider. No shell effect,
external page, provider credential or paid inference is used. Consent deadlines
are shortened only in the isolated fixture to exercise real expiry/review UI.
"""
from __future__ import annotations
import asyncio,json,os,socket,sys,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright,expect
from termx.agent.providers import ProviderTurn
from termx.agent.secrets import CredentialStore

class Adapter:
    def __init__(self):self.prompts=[];self.releases={'Initial active task':threading.Event(),'Interrupt gate':threading.Event(),'Cancellation gate':threading.Event()}
    async def test(self):return 'Fixture only'
    async def plan(self,prompt,cwd,manifest):return {'summary':prompt,'steps':[],'tools':[],'risks':[]},'fixture-plan'
    async def turn(self,**kwargs):
        prompt=kwargs['prompt'];self.prompts.append(prompt)
        gate=self.releases.get(prompt)
        while gate and not gate.is_set():await asyncio.sleep(.05)
        return ProviderTurn(response_id='fixture-'+str(len(self.prompts)),text='Completed: '+prompt,calls=[],usage={},output_items=[])


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'actual-canonical-prompt-queue-interrupt-jump','paid_queries':0,'external_network_requests':0,'errors':[],'states':[],'steps':[]}
    with tempfile.TemporaryDirectory(prefix='termx-queue-ui-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        project_root=root/'project';project_root.mkdir()
        from termx.app import AppState,create_app
        from workspace_ui_snapshot import snapshot_ui
        adapter=Adapter();state=AppState(passcode=None,credentials=CredentialStore(memory={}),adapter_factory=lambda *_:adapter)
        owner=state.identity.setup_owner('queue-owner','queue-proof-password-123');project=state.projects.register(str(project_root),name='Queue proof')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No network fixture',base_url='http://127.0.0.1:9999/v1',model='fixture',capabilities=['shell'],api_key='not-a-provider-key')
        app=create_app(state,web_dir=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist')))))
        session=state.workspace.create_session(owner,title='Durable queue proof',project_id=project['id'],cwd=str(project_root),provider_id='fixture',model='fixture',mode='ask')
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        for i in range(80):
            task=state.agent_store.create_task(prompt='History '+str(i),cwd=str(project_root),provider_id='fixture',model='fixture',limits={},mode='ask')
            state.agent_store.update_task(task['id'],status='completed',result='Historical answer '+str(i))
            state.workspace.store.create('task',owner.id,{'conversation_id':session['id'],'cwd':str(project_root)},project['id'],task['id']);state.authorization.claim_principal(owner,'task',task['id'],project_id=project['id'])
            state.agent_store.add_conversation_turn(session['id'],prompt='History '+str(i),task_id=task['id'],mode='ask',provider_id='fixture',model='fixture')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));worker=threading.Thread(target=lambda:asyncio.run(host.serve(sockets=[listener])),daemon=True);worker.start();deadline=monotonic()+25
        while not host.started:
            if monotonic()>deadline:raise RuntimeError('Queue host did not start')
            sleep(.05)
        def wait(predicate):
            deadline=monotonic()+30
            while not predicate():
                if monotonic()>deadline:raise AssertionError('Durable queue runtime did not reach the asserted state')
                sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                address=f'http://127.0.0.1:{port}/?session={session["id"]}'
                page.goto(address);page.get_by_label('Username',exact=True).fill('queue-owner');page.get_by_label('Password',exact=True).fill('queue-proof-password-123');page.get_by_role('button',name='Continue with password').click();page.wait_for_function('document.querySelector(".breadcrumb strong")?.textContent==="Durable queue proof"',timeout=60000)
                message=page.get_by_label('Message',exact=True);message.fill('Initial active task');page.get_by_role('button',name='Send message',exact=True).click();page.get_by_role('button',name='Queue next',exact=True).wait_for();wait(lambda:adapter.prompts==['Initial active task'])
                for text in ('Queued exact A','Queued exact B'):
                    message.fill(text);page.get_by_role('button',name='Queue next',exact=True).click();expect(message).to_have_value('')
                wait(lambda:len(state.workspace.store.list('prompt_queue'))==2);assert adapter.prompts==['Initial active task'];page.get_by_text('2 queued follow-ups',exact=True).wait_for()
                message.fill('Unrelated unsent draft after queue');sleep(.8);log=page.get_by_role('log',name='Conversation messages');log.hover();page.mouse.wheel(0,-15000);page.get_by_role('button',name='Jump to latest',exact=True).wait_for();old_offset=log.evaluate('(el)=>el.scrollTop');old_anchor=log.evaluate('(el)=>Array.from(el.querySelectorAll("[data-index]")).find(row=>row.getBoundingClientRect().bottom>el.getBoundingClientRect().top)?.getAttribute("data-index")');assert log.evaluate('(el)=>el.scrollHeight-el.scrollTop-el.clientHeight')>350;wait(lambda:abs(state.workspace.session(owner,session['id'],turns=False)['scroll']-old_offset)<2)
                page.close();page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)));page.goto(address);message=page.get_by_label('Message',exact=True);expect(message).to_have_value('Unrelated unsent draft after queue',timeout=60000);page.get_by_text('2 queued follow-ups',exact=True).wait_for();assert adapter.prompts==['Initial active task']
                adapter.releases['Initial active task'].set();wait(lambda:adapter.prompts==['Initial active task','Queued exact A','Queued exact B']);wait(lambda:all(row['status']=='dispatched' for row in state.workspace.store.list('prompt_queue')))
                expect(message).to_have_value('Unrelated unsent draft after queue');assert state.workspace.session(owner,session['id'],turns=False)['draft_text']=='Unrelated unsent draft after queue';page.get_by_role('button',name='Jump to latest',exact=True).wait_for();preserved_offset=page.get_by_role('log',name='Conversation messages').evaluate('(el)=>el.scrollTop');new_anchor=page.get_by_role('log',name='Conversation messages').evaluate('(el)=>Array.from(el.querySelectorAll("[data-index]")).find(row=>row.getBoundingClientRect().bottom>el.getBoundingClientRect().top)?.getAttribute("data-index")');report['reading_position']={'old_offset':old_offset,'restored_offset':preserved_offset,'old_anchor':old_anchor,'restored_anchor':new_anchor};assert new_anchor==old_anchor,report['reading_position']
                page.get_by_role('button',name='Jump to latest',exact=True).click();page.get_by_text('Completed: Queued exact B',exact=True).wait_for();page.get_by_text('Follow-up history · 2',exact=True).click();page.get_by_role('button',name='Inspect follow-up task').first.wait_for();print('Verified queue phase',flush=True);report['steps'].append('Two real queued turns survive page close, execute once in order through AgentManager, preserve later draft and old reading position; Jump returns to actual latest answer')
                message.fill('Interrupt gate');page.get_by_role('button',name='Send message',exact=True).click();page.get_by_role('button',name='Queue next',exact=True).wait_for();wait(lambda:'Interrupt gate' in adapter.prompts);active=state.agent_store.active_conversation_tasks(session['id'])[0]
                message.fill('Interrupt replacement');page.get_by_role('button',name='Interrupt current task',exact=True).click();assert len(state.workspace.store.list('prompt_queue'))==2;page.get_by_role('button',name='Stop current task and queue draft',exact=True).click();wait(lambda:'Interrupt replacement' in adapter.prompts);wait(lambda:state.agent_store.get_task(active['id'])['status']=='cancelled');print('Verified queue phase',flush=True);report['steps'].append('Consequence confirmation cancels the exact active canonical task and starts the replacement once as a new queued turn')
                message.fill('Cancellation gate');page.get_by_role('button',name='Send message',exact=True).click();page.get_by_role('button',name='Queue next',exact=True).wait_for();wait(lambda:'Cancellation gate' in adapter.prompts);message.fill('Never execute cancelled prompt');page.get_by_role('button',name='Queue next',exact=True).click();expect(message).to_have_value('');item=next(row for row in state.workspace.store.list('prompt_queue') if row['prompt']=='Never execute cancelled prompt');state.workspace.store.update('prompt_queue',item['id'],{**item,'expires_at':0},item['revision']);page.get_by_text('1 queued follow-up',exact=True).click();page.get_by_role('button',name='Review before continuing',exact=True).wait_for();page.get_by_role('button',name='Review before continuing',exact=True).click();page.get_by_role('region',name='Current queued settings').wait_for();assert 'Never execute cancelled prompt' not in adapter.prompts;page.get_by_role('button',name='Continue with these settings').click();page.get_by_role('button',name='Cancel queued follow-up').click();wait(lambda:state.workspace.store.get('prompt_queue',item['id'])['status']=='cancelled');print('Verified queue phase',flush=True);report['steps'].append('Expired follow-up blocks until explicit old/current target review; cancellation remains inspectable in bounded history without executing')
                message.fill('Queue geometry prompt');page.get_by_role('button',name='Queue next',exact=True).click();expect(message).to_have_value('');axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                for theme,width in [('dark',1440),('light',1440),('dark',720),('light',720)]:
                    page.set_viewport_size({'width':width,'height':1000 if width==1440 else 500});page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click();page.get_by_text('1 queued follow-up',exact=True).wait_for();message.fill('Unsent geometry text');queue=page.get_by_role('button',name='Queue next',exact=True);queue.focus();geometry=queue.evaluate('(el)=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {top:r.top,bottom:r.bottom,height:r.height,viewport:innerHeight,uncovered:hit===el||el.contains(hit)}}');assert geometry['uncovered'] and geometry['bottom']<=geometry['viewport'],geometry;expect(queue).to_be_focused();page.add_script_tag(path=str(axe));result=page.evaluate('async()=>await axe.run(document,{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa"]}})');violations=[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['violations']];assert not violations,violations;assert not page.evaluate('document.documentElement.scrollWidth>innerWidth');page.screenshot(path=str(destination/f'queue-{theme}-{width}.png'));report['states'].append({'theme':theme,'width':width,'queue_button':geometry,'axe_violations':violations})
                assert 'Never execute cancelled prompt' not in adapter.prompts;assert not report['errors'];report['fixture_provider_turns']=adapter.prompts;report['status']='passed';context.close();browser.close()
        except BaseException:
            report['status']='failed'
            if 'page' in locals():
                try:page.screenshot(path=str(destination/'failure.png'));report['visible_text']=page.locator('body').inner_text()
                except Exception:pass
            raise
        finally:
            for gate in adapter.releases.values():gate.set()
            host.should_exit=True;worker.join(15);(destination/'report.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':run(sys.argv[1])
