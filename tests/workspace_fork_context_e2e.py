"""Opt-in actual hosted cross-engine transfer UI; no provider/native execution.

The isolated Gateway has an explicitly declared fixture adapter. Preview and
commit use real workspace authorization, canonical turns, real UTF-8 project
files and durable linked conversations. Native subscription/resume is not claimed.
"""
from __future__ import annotations
import asyncio,json,logging,os,socket,sys,tempfile,threading,traceback
from pathlib import Path
from time import monotonic,sleep
from urllib.parse import urlparse
import uvicorn
from playwright.sync_api import sync_playwright,expect
from termx.agent.secrets import CredentialStore
from termx.engines.types import EngineDescriptor,EngineCapabilities
from workspace_ui_snapshot import snapshot_ui

class TransferFixture:
    def descriptor(self):return EngineDescriptor(id='fixture-transfer',label='Fixture transfer engine',installed=True,auth_state='not_required',transport='fixture-no-execution')
    def capabilities(self):return EngineCapabilities(models=['fixture-target'],fork='unsupported',notes={'fixture':'Context transfer only; native execution/resume not tested'})
    async def shutdown(self):pass


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'server_exceptions':[],'phase':'workflow','proof':'actual-host-reviewed-cross-engine-context-fork','provider_queries':0,'native_engine_spawns':0,'errors':[],'states':[],'keyboard_checks':[],'commit_requests':0}
    with tempfile.TemporaryDirectory(prefix='termx-fork-context-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        state=AppState(passcode=None,credentials=CredentialStore(memory={}))
        owner=state.identity.setup_owner('fork-owner','fork-fixture-password-123')
        folder=root/'project';folder.mkdir();(folder/'selected.txt').write_text('Exact reviewed project file\n',encoding='utf-8');(folder/'excluded.txt').write_text('Never transfer this file\n',encoding='utf-8')
        project=state.projects.register(str(folder),name='Fork project')
        adapter=TransferFixture();state.engines.register(adapter)
        descriptor=adapter.descriptor().as_dict();descriptor['capabilities']=adapter.capabilities().as_dict()
        state.engines.catalogue.entries['fixture-transfer']={'descriptor':descriptor,'configuration':{'models':['fixture-target']},'refreshed_at':1,'stale':False,'refresh_error':None}
        state.agent_store.put_provider('fixture',kind='openai-compatible',name='No-query fixture account',base_url='http://127.0.0.1:1/v1',model='fixture-source',capabilities=['chat'],secret_configured=False)
        app=create_app(state,web_dir=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist')))))
        session=state.workspace.create_session(owner,title='Original fork context',project_id=project['id'],cwd=str(folder),provider_id='fixture',model='fixture-source',mode='ask')
        for prompt,result in [('Selected message','Selected answer'),('Excluded message','Excluded answer')]:
            task=state.agent_store.create_task(prompt=prompt,cwd=str(folder),provider_id='fixture',model='fixture-source',limits={},mode='ask')
            state.agent_store.update_task(task['id'],status='completed',result=result)
            state.workspace.store.create('task',owner.id,{'conversation_id':session['id'],'cwd':str(folder)},project['id'],task['id'])
            state.authorization.claim_principal(owner,'task',task['id'],project_id=project['id'])
            state.agent_store.add_conversation_turn(session['id'],prompt=prompt,task_id=task['id'],mode='ask',provider_id='fixture',model='fixture-source')
        original=state.workspace.session(owner,session['id']);original_turns=[t['id'] for t in original['turns']]
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        class Capture(logging.Handler):
            def emit(self,record):
                if record.exc_info:
                    error=record.exc_info[1];report['server_exceptions'].append({'phase':report['phase'],'type':type(error).__name__,'frames':[{'file':Path(frame.filename).name,'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(record.exc_info[2])[-3:]]})
        capture=Capture()
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));logging.getLogger('uvicorn.error').addHandler(capture);worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start();deadline=monotonic()+30
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Fork fixture host did not start')
            sleep(.05)
        page=None
        def wait(check):
            until=monotonic()+30
            while not check():
                if monotonic()>until:raise AssertionError('Actual fork durable state did not settle')
                sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.on('request',lambda request:report.__setitem__('commit_requests',report['commit_requests']+1) if request.method=='POST' and '/fork-previews/' in request.url and request.url.endswith('/commit') else None)
                address=f'http://127.0.0.1:{port}/?session={session["id"]}'
                page.goto(address);page.get_by_label('Username',exact=True).fill('fork-owner');page.get_by_label('Password',exact=True).fill('fork-fixture-password-123');page.get_by_role('button',name='Continue with password',exact=True).click();message=page.get_by_label('Message',exact=True);expect(message).to_be_enabled(timeout=60000);message.fill('Original unsent draft remains');wait(lambda:state.workspace.session(owner,session['id'],turns=False)['draft_text']=='Original unsent draft remains')
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                def keyboard(target,label):
                    for count in range(80):
                        page.keyboard.press('Tab')
                        if target.evaluate('(el)=>el===document.activeElement'):
                            bounds=target.evaluate('(el)=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2),style=getComputedStyle(el);return {visible:r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight,uncovered:hit===el||el.contains(hit),outline:style.outlineStyle,outline_width:parseFloat(style.outlineWidth)}}')
                            assert bounds['visible'] and bounds['uncovered'] and bounds['outline']!='none' and bounds['outline_width']>=2,(label,bounds)
                            report['keyboard_checks'].append({'target':label,'tabs':count+1,**bounds});return
                    raise AssertionError('Keyboard could not reach '+label)
                def audit(theme,zoom,phase):
                    page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})");value={'theme':theme,'zoom':zoom,'phase':phase,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['violations']]};report['states'].append(value);assert not value['overflow'] and not value['violations'],value;page.screenshot(path=str(destination/f'fork-{theme}-{zoom}-{phase}.png'))
                for zoom in (100,200):
                    for theme in ('dark','light'):
                        page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom});page.goto(address);message=page.get_by_label('Message',exact=True);expect(message).to_have_value('Original unsent draft remains',timeout=60000)
                        if page.locator('html').get_attribute('data-theme')!=theme:
                            page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        page.get_by_role('button',name='Session engine and model',exact=True).click();engine=page.get_by_label('Session engine',exact=True);expect(engine.locator('option[value="fixture-transfer"]')).to_have_count(1);engine.select_option('fixture-transfer')
                        dialog=page.get_by_role('dialog',name='Continue with fixture-transfer',exact=True);dialog.wait_for();chosen=dialog.get_by_role('checkbox',name='Selected message · includes reply',exact=True);excluded=dialog.get_by_role('checkbox',name='Excluded message · includes reply',exact=True)
                        keyboard(excluded,'Excluded turn');page.keyboard.press('Space');expect(excluded).not_to_be_checked();expect(chosen).to_be_checked()
                        files=dialog.get_by_label('Project files to transfer',exact=True);keyboard(files,'Project files to transfer');files.fill('selected.txt')
                        summary=dialog.get_by_label('Transfer summary',exact=True);keyboard(summary,'Transfer summary');summary.fill('Exact reviewed summary '+theme+' '+str(zoom))
                        preview_button=dialog.get_by_role('button',name='Preview transferred context',exact=True);keyboard(preview_button,'Preview transferred context');page.keyboard.press('Enter');preview=dialog.get_by_role('region',name='Context transfer preview',exact=True);preview.wait_for();transfer=json.loads(preview.inner_text());assert [x['content'] for x in transfer['messages']]==['Selected message','Selected answer'];assert transfer['files']==[{'path':'selected.txt','content':'Exact reviewed project file\n','sha256':__import__('hashlib').sha256(b'Exact reviewed project file\n').hexdigest()}];assert transfer['summary']=='Exact reviewed summary '+theme+' '+str(zoom);assert transfer['engine']=='fixture-transfer' and transfer['excluded']==['native_session','hidden_memory','credentials','native_resume'];keyboard(preview,'Context transfer preview');audit(theme,zoom,'preview')
                        edit=dialog.get_by_role('button',name='Edit transferred context',exact=True);keyboard(edit,'Edit transferred context');page.keyboard.press('Enter');expect(files).to_have_value('selected.txt');expect(summary).to_have_value(transfer['summary']);expect(excluded).not_to_be_checked();expect(chosen).to_be_checked();audit(theme,zoom,'edit')
                        keyboard(preview_button,'Re-preview chosen context');page.keyboard.press('Enter');preview.wait_for();assert json.loads(preview.inner_text())==transfer
                        before=report['commit_requests'];commit=dialog.get_by_role('button',name='Create linked fork',exact=True);keyboard(commit,'Create linked fork');page.keyboard.press('Enter');page.keyboard.press('Enter');expect(dialog).not_to_be_visible();wait(lambda:len([r for r in state.workspace.store.list('conversation') if r.get('linked_from')==session['id']])==len(report.get('forks',[]))+1)
                        children=[r for r in state.workspace.store.list('conversation') if r.get('linked_from')==session['id']];child=next(r for r in children if r['id'] not in report.get('forks',[]));report.setdefault('forks',[]).append(child['id']);assert report['commit_requests']==before+1;assert child['transfer']==transfer and child['engine']=='fixture-transfer';assert state.workspace.session(owner,child['id'])['turns']==[]
                        after=state.workspace.session(owner,session['id']);assert [t['id'] for t in after['turns']]==original_turns and after['engine']=='internal' and after['model']=='fixture-source' and after['provider_id']=='fixture' and after['draft_text']=='Original unsent draft remains' and after['transfer'] is None;assert len(state.agent_store.list_tasks())==2
                assert not report['errors'];report['native_select_method']='Explicit select_option after accessible control and exact installed fixture option observed; native picker key synthesis not claimed';report['original_unchanged']={'engine_provider_model':True,'turns_and_tasks':True,'unsent_draft':True,'no_transfer_in_source':True};report['status']='passed';report['phase']='teardown';context.close();browser.close()
        except BaseException:
            report['status']='failed'
            if page:
                try:page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                except Exception:pass
            raise
        finally:server.should_exit=True;worker.join(15);logging.getLogger('uvicorn.error').removeHandler(capture);(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
if __name__=='__main__':
    result=run(sys.argv[1]);print(json.dumps({key:result[key] for key in ['status','commit_requests','errors','server_exceptions']},indent=2))
