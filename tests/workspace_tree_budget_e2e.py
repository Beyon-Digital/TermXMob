"""Opt-in actual hosted pooled-budget UI and committed-grant restart proof.

Only isolated, controlled provider adapters execute. No paid inference, native
engine or external browser navigation is involved. Children use actual spawn tools and parent watchers. Setup plan/delegation
approvals are explicit fixture decisions; budget HTTP grants are exercised in UI.
"""
from __future__ import annotations
import asyncio,json,logging,os,socket,sys,tempfile,threading,traceback
from collections import defaultdict
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright,expect
from termx.agent.providers import ProviderCall,ProviderTurn
from termx.agent.secrets import CredentialStore
from workspace_ui_snapshot import snapshot_ui

class Adapter:
    def __init__(self):self.turns=defaultdict(int);self.manager=None;self.parent_id=None
    async def test(self):return 'Controlled fixture only'
    async def plan(self,prompt,cwd,manifest):return {'summary':prompt,'steps':['Delegate two read-only inspections'],'tools':['spawn_subagent'],'risks':[]},'fixture-plan'
    async def turn(self,**kwargs):
        prompt=kwargs['prompt'];self.turns[prompt]+=1
        if prompt=='Coordinate actual spawned children':
            if self.turns[prompt]==1:calls=[ProviderCall('function','spawn-'+name,'spawn_subagent',{'task':name,'mode':'ask','agent':name}) for name in ('Child A','Child B')]
            elif self.turns[prompt]==2:calls=[ProviderCall('function','wait','await_subagents',{})]
            else:calls=[]
        elif self.turns[prompt]==1:
            if prompt.startswith('Child '):
                while self.manager.tree_budget.snapshot(self.parent_id)['used_steps']<3:await asyncio.sleep(.01)
            calls=[ProviderCall('function',prompt+'-'+str(i),'read_file',{'path':'read.txt'}) for i in range(4 if prompt.startswith('Child ') else 2)]
        else:calls=[]
        return ProviderTurn(prompt+str(self.turns[prompt]),'Completed exact saved batch: '+prompt if not calls else '',calls,{},[])

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'actual-host-shared-tree-budget-renewal-and-restart','paid_queries':0,'native_engine_spawns':0,'errors':[],'server_exceptions':[],'states':[],'keyboard_checks':[],'approval_http':[],'phase':'workflow'}
    with tempfile.TemporaryDirectory(prefix='termx-tree-budget-ui-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        memory={};adapter=Adapter();state=AppState(passcode=None,credentials=CredentialStore(memory=memory),adapter_factory=lambda *_:adapter)
        adapter.manager=state.agent
        owner=state.identity.setup_owner('budget-owner','budget-proof-password-123')
        folder=root/'project';folder.mkdir();(folder/'read.txt').write_text('Only the enrolled fixture file\n')
        project=state.projects.register(str(folder),name='Shared budget proof')
        state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='No-network budget fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['shell'],api_key='fixture-not-a-provider-key')
        ui=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))));report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        app=create_app(state,web_dir=ui)
        session=state.workspace.create_session(owner,title='Shared parent and children',project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture',mode='agent')
        receipt_session=state.workspace.create_session(owner,title='Approved grant restart receipt',project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture',mode='ask')
        class Capture(logging.Handler):
            def emit(self,record):
                if record.exc_info:
                    report['server_exceptions'].append({'phase':report['phase'],'type':type(record.exc_info[1]).__name__,'frames':[{'file':Path(frame.filename).name,'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(record.exc_info[2])[-3:]]})
        capture=Capture();port=0;holder={}
        def start(application):
            nonlocal port
            listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',port));listener.listen();port=listener.getsockname()[1]
            host=uvicorn.Server(uvicorn.Config(application,log_level='error',lifespan='on'));logging.getLogger('uvicorn.error').addHandler(capture)
            async def serve():holder['loop']=asyncio.get_running_loop();await host.serve(sockets=[listener])
            worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start();deadline=monotonic()+40
            while not host.started:
                if monotonic()>deadline:raise RuntimeError('Isolated budget host did not start')
                sleep(.05)
            return host,worker
        host,worker=start(app)
        def execute(coro):return asyncio.run_coroutine_threadsafe(coro,holder['loop']).result(40)
        def wait(predicate):
            deadline=monotonic()+40
            while not predicate():
                if monotonic()>deadline:raise AssertionError('Actual budget runtime did not settle')
                sleep(.05)
        def bind(tid,row,prompt,mode,sid):
            state.workspace.store.create('task',owner.id,{'conversation_id':row['id'],'cwd':str(folder)},project['id'],tid)
            state.authorization.claim_principal(owner,'task',tid,project_id=project['id'])
            state.agent_store.add_conversation_turn(row['id'],prompt=prompt,task_id=tid,mode=mode,provider_id='fixture',model='fixture')
            state.browser.records.put('agent-task-authority',tid,{'id':tid,'conversation_id':row['id'],'principal_id':owner.id,'session_id':sid,'project_id':project['id'],'policy_version':owner.policy_version})
        page=None
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},device_scale_factor=1,reduced_motion='reduce');page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.on('response',lambda response:report['approval_http'].append({'path':response.url.split(str(port),1)[-1],'status':response.status,'body':response.request.post_data_json}) if response.request.method=='POST' and '/approvals/' in response.url and response.url.endswith('/resolve') else None)
                address=f'http://127.0.0.1:{port}/?session={session["id"]}'
                page.goto(address);page.get_by_label('Username',exact=True).fill('budget-owner');page.get_by_label('Password',exact=True).fill('budget-proof-password-123');page.get_by_role('button',name='Continue with password',exact=True).click();expect(page.get_by_label('Message',exact=True)).to_be_enabled(timeout=60000)
                access=next(cookie['value'] for cookie in context.cookies() if cookie['name']=='termx_access');sid=state.identity.resolve(access).session_id
                async def seed_tree():
                    parent=await state.workspace.send(owner,session['id'],prompt='Coordinate actual spawned children',request_id='budget-render-parent',limits={'max_steps':8,'max_seconds':120},managed_session_id=sid)
                    adapter.parent_id=parent['id']
                    await state.agent.resolve_approval(parent['id'],parent['approvals'][0]['id'],'approved')
                    # Only setup decisions use the trusted controlled fixture.
                    # The actual budget decision below traverses the hosted UI.
                    for _ in range(500):
                        pending=[a for a in state.agent_store.approvals(parent['id']) if a['status']=='pending']
                        for approval in pending:
                            if approval['kind']=='tool':await state.agent.resolve_approval(parent['id'],approval['id'],'approved')
                        if any(a['kind']=='budget' and a['payload'].get('child_id') for a in pending):break
                        await asyncio.sleep(.01)
                    return parent,state.agent_store.children(parent['id'])
                parent,children=execute(seed_tree());wait(lambda:sorted(state.agent_store.get_task(child['id'])['status'] for child in children)==['awaiting_approval','completed'])
                paused=next(state.agent_store.get_task(child['id']) for child in children if state.agent_store.get_task(child['id'])['status']=='awaiting_approval');approval=next(a for a in state.agent_store.approvals(parent['id']) if a['kind']=='budget' and a['status']=='pending')
                assert approval['payload']['child_id']==paused['id'] and approval['payload']['child_approval_id'] in [a['id'] for a in state.agent_store.approvals(paused['id'])]
                wait(lambda:state.agent.tree_budget.snapshot(parent['id'])['active_workers']==0)
                before=state.agent.tree_budget.snapshot(parent['id']);assert before['used_steps']==7 and before['max_steps']==8 and before['active_workers']==0 and paused['limits']['max_steps']==8
                sleep(2);after_wait=state.agent.tree_budget.snapshot(parent['id']);assert before['used_execution_seconds']==after_wait['used_execution_seconds'];report['human_wait']={'elapsed_wall_seconds':2,'before':before,'after':after_wait,'unchanged':True}
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                def keyboard(target,label):
                    for count in range(100):
                        page.keyboard.press('Tab')
                        if target.evaluate('(el)=>el===document.activeElement'):
                            result=target.evaluate('(el)=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2),s=getComputedStyle(el);return {visible:r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight,uncovered:[[r.x+r.width/2,r.y+Math.min(8,r.height/4)],[r.x+r.width/2,r.bottom-Math.min(8,r.height/4)],[r.x+r.width/2,r.y+r.height/2]].every(([x,y])=>{const node=document.elementFromPoint(x,y);return node===el||el.contains(node)}),outline:s.outlineStyle,width:parseFloat(s.outlineWidth),offset:parseFloat(s.outlineOffset),ring_visible:(()=>{const e=Math.max(0,parseFloat(s.outlineWidth)+parseFloat(s.outlineOffset));if(r.top-e<0||r.bottom+e>innerHeight||r.left-e<0||r.right+e>innerWidth)return false;for(let a=el.parentElement;a;a=a.parentElement){const st=getComputedStyle(a),b=a.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(st.overflowY)&&(r.top-e<b.top||r.bottom+e>b.bottom))return false;if(/auto|scroll|hidden|clip/.test(st.overflowX)&&(r.left-e<b.left||r.right+e>b.right))return false}return true})()}}')
                            if not (result['visible'] and result['uncovered'] and result['outline']!='none' and result['width']>=2 and result['ring_visible']):page.screenshot(path=str(destination/'failure-focus.png'))
                            assert result['visible'] and result['uncovered'] and result['outline']!='none' and result['width']>=2 and result['ring_visible'],(label,result);report['keyboard_checks'].append({'target':label,'tabs':count+1,**result});return
                    raise AssertionError('Keyboard could not reach '+label)
                def audit(theme,zoom,phase):
                    page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})");value={'theme':theme,'zoom':zoom,'phase':phase,'viewport':page.evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})'),'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['violations']]};report['states'].append(value);assert not value['overflow'] and not value['violations'],value;page.screenshot(path=str(destination/f'budget-{theme}-{zoom}-{phase}.png'))
                message=page.get_by_label('Message',exact=True);message.fill('Unsent draft survives budget review');wait(lambda:state.workspace.session(owner,session['id'],turns=False)['draft_text']=='Unsent draft survives budget review')
                for zoom in (100,200):
                    for theme in ('dark','light'):
                        page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom});page.goto(address);expect(page.get_by_label('Message',exact=True)).to_have_value('Unsent draft survives budget review',timeout=60000)
                        if page.locator('html').get_attribute('data-theme')!=theme:
                            page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        budget=page.get_by_role('region',name='Budget extension',exact=True);budget.wait_for();review=budget.get_by_role('button',name='Review budget extension',exact=True);keyboard(review,'Review actual child budget');page.keyboard.press('Enter');grant=page.get_by_role('dialog',name='Shared task tree budget exhausted',exact=True);grant.wait_for();expect(grant).to_contain_text('grant version 1');expect(grant).to_contain_text('7 / 8 reserved calls');keyboard(grant.get_by_label('Extended max_steps',exact=True),'Shared total call ceiling');audit(theme,zoom,'paused-grant-fields');keyboard(grant.get_by_role('button',name='Approve budget extension',exact=True),'Explicit versioned extension');audit(theme,zoom,'paused-grant-actions');keyboard(grant.get_by_role('button',name='Close',exact=True),'Close grant without execution');page.keyboard.press('Enter');expect(review).to_be_focused();report.setdefault('dialog_focus_returns',[]).append({'theme':theme,'zoom':zoom,'target':'Review budget extension','restored':True});expect(message).to_have_value('Unsent draft survives budget review');keyboard(page.get_by_role('button',name='Appearance',exact=True),'Visible footer appearance focus')
                        page.get_by_role('button',name='Supervise',exact=True).first.click();dialog=page.get_by_role('dialog',name='Task supervision',exact=True);dialog.wait_for();dialog.get_by_role('button',name=__import__('re').compile('^'+paused['prompt'])).click();shared=dialog.get_by_role('region',name='Shared task tree budget',exact=True);expect(shared).to_contain_text('7 / 8 reserved tool calls');expect(shared).to_contain_text('summed active execution seconds');expect(dialog.locator('label').filter(has_text='Individual task ceiling')).to_be_visible();assert '"max_steps": 8' in dialog.locator('pre').first.inner_text();keyboard(dialog.get_by_label('Steer selected task',exact=True),'Selected child controls and individual ceiling');audit(theme,zoom,'shared-vs-individual');keyboard(dialog.get_by_role('button',name='Close',exact=True),'Close task supervision');page.keyboard.press('Enter')
                budget.get_by_role('button',name='Review budget extension',exact=True).click();grant=page.get_by_role('dialog',name='Shared task tree budget exhausted',exact=True);grant.get_by_label('Extended max_steps',exact=True).fill('12');grant.get_by_label('Extended max_seconds',exact=True).fill('120');keyboard(grant.get_by_role('button',name='Approve budget extension',exact=True),'Approve one shared grant');page.keyboard.press('Enter');page.keyboard.press('Enter');wait(lambda:state.agent_store.get_task(parent['id'])['status']=='completed');wait(lambda:state.agent.tree_budget.snapshot(parent['id'])['active_workers']==0);expect(budget).not_to_be_visible(timeout=60000);renewed=state.agent.tree_budget.snapshot(parent['id']);assert renewed['version']==2 and renewed['max_steps']==12 and renewed['used_steps']==11;assert len(report['approval_http'])==1 and report['approval_http'][0]['status']==200;expect(message).to_have_value('Unsent draft survives budget review');report['fixture_provider_turns']=dict(adapter.turns);report['normal_renewal']={'before':before,'after':renewed,'actual_spawned_children':2,'parent_wrapper_approval':approval['id'],'tool_effects':sum(sum(e['type']=='tool.finished' for e in state.agent_store.events(child['id'])) for child in children),'unsent_draft_preserved':True};assert report['normal_renewal']['tool_effects']==8
                async def seed_receipt():
                    return await state.agent.create_task(prompt='Restart receipt',cwd=str(folder),provider_id='fixture',model='fixture',mode='ask',limits={'max_steps':1,'max_seconds':120},on_created=lambda tid:bind(tid,receipt_session,'Restart receipt','ask',sid))
                receipt=execute(seed_receipt());wait(lambda:state.agent_store.get_task(receipt['id'])['status']=='awaiting_approval');old=next(a for a in state.agent_store.approvals(receipt['id']) if a['kind']=='budget' and a['status']=='pending')
                # Simulate the exact durable boundary: decision/grant committed,
                # process stops before launching the approved worker.
                state.agent.tree_budget.approve(receipt['id'],old['id'],{'max_steps':3,'max_seconds':120},version=1);report['phase']='receipt-restart';host.should_exit=True;worker.join(20);assert not worker.is_alive()
                state=AppState(passcode=None,credentials=CredentialStore(memory=memory),adapter_factory=lambda *_:adapter);app=create_app(state,web_dir=ui);host,worker=start(app);owner=state.identity.principal_by_id(owner.id)
                resumed=next(a for a in state.agent_store.approvals(receipt['id']) if a['kind']=='budget' and a['status']=='pending');assert resumed['payload']['reuse_grant'] is True;checkpoint=state.agent.tree_budget.snapshot(receipt['id']);assert checkpoint['version']==2
                page.goto(f'http://127.0.0.1:{port}/?session={receipt_session["id"]}');resume=page.get_by_role('region',name='Budget extension',exact=True);resume.wait_for(timeout=60000);keyboard(resume.get_by_role('button',name='Review approved continuation',exact=True),'Review saved grant continuation');page.keyboard.press('Enter');continuation=page.get_by_role('dialog',name='Continue within the approved shared budget',exact=True);continuation.wait_for();expect(continuation.locator('input[type=number]')).to_have_count(0);keyboard(continuation.get_by_role('button',name='Continue within approved budget',exact=True),'Continue exact persisted grant');audit('light',200,'restart-receipt');page.keyboard.press('Enter');page.keyboard.press('Enter');wait(lambda:state.agent_store.get_task(receipt['id'])['status']=='completed');expect(resume).not_to_be_visible(timeout=60000);continued=state.agent.tree_budget.snapshot(receipt['id']);assert continued['version']==checkpoint['version']==2 and continued['max_steps']==3 and continued['max_execution_seconds']==120 and continued['used_steps']==2;assert len(report['approval_http'])==2 and all(row['status']==200 for row in report['approval_http']);assert 'limits' not in report['approval_http'][-1]['body'];assert sum(e['type']=='tool.finished' for e in state.agent_store.events(receipt['id']))==2;report['restart_receipt']={'actual_host_restarts':1,'committed_boundary':'approval and allowance committed; worker not launched','before_continue':checkpoint,'after_continue':continued,'no_second_grant':True,'exact_saved_effects':2};assert not report['errors'];report['status']='passed';report['phase']='teardown';context.close();browser.close()
        except BaseException:
            report['status']='failed'
            if page:
                try:page.screenshot(path=str(destination/'failure.png'));report['visible_text']=page.locator('body').inner_text()
                except Exception:pass
            raise
        finally:host.should_exit=True;worker.join(20);logging.getLogger('uvicorn.error').removeHandler(capture);(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
if __name__=='__main__':
    result=run(sys.argv[1]);print(json.dumps({key:result[key] for key in ('status','errors','approval_http','server_exceptions')},indent=2))
