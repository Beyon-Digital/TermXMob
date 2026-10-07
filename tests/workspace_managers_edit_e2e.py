"""Actual memory/preset editor host proof. No provider requests or paid work."""
from __future__ import annotations
import json,os,socket,sys,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright
from workspace_ui_snapshot import snapshot_ui

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'actual-memory-preset-editors','paid_provider_queries':0,'errors':[],'states':[]}
    with tempfile.TemporaryDirectory(prefix='termx-manager-edit-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        state=AppState(passcode=None);owner=state.identity.setup_owner('manager-owner','manager-fixture-password-123')
        app=create_app(state,web_dir=snapshot_ui(root,Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))
        memory=state.workspace.save_memory(owner,content='Original decision',provenance='Initial review',excluded=True)
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+25
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/');page.get_by_label('Username',exact=True).fill('manager-owner');page.get_by_label('Password',exact=True).fill('manager-fixture-password-123');page.get_by_role('button',name='Continue with password').click();page.get_by_label('Message',exact=True).wait_for()
                def manager(name):
                    page.get_by_role('button',name='Commands',exact=True).click();page.get_by_role('button',name=name,exact=True).click();page.get_by_role('region',name='Workspace managers').wait_for()
                manager('Memory');page.get_by_role('button',name='Edit memory',exact=True).click();form=page.get_by_role('form',name='Edit memory')
                form.get_by_label('Fact or decision').fill('Updated decision');form.get_by_label('Source or reason').fill('Second review');form.get_by_label('Retention in days').fill('7');form.get_by_role('button',name='Save memory changes').click();page.get_by_text('Memory updated',exact=True).wait_for()
                updated=state.workspace.store.get('memory',memory['id']);assert updated['content']=='Updated decision' and updated['provenance']=='Second review' and updated['excluded'] and updated['retention_days']==7 and updated['revision']>memory['revision']
                page.get_by_role('button',name='Edit memory',exact=True).click();form=page.get_by_role('form',name='Edit memory');form.get_by_label('Fact or decision').fill('Unsaved conflicting edit')
                state.workspace.save_memory(owner,identifier=memory['id'],revision=updated['revision'],content='Concurrent accepted version',provenance='Other window',excluded=True,retention_days=7)
                form.get_by_role('button',name='Save memory changes').click();form.get_by_role('alert').wait_for();assert form.get_by_label('Fact or decision').input_value()=='Unsaved conflicting edit';assert state.workspace.store.get('memory',memory['id'])['content']=='Concurrent accepted version'
                report['memory']={'excluded_preserved':True,'original_scope_preserved':True,'retention_days':7,'revision_conflict_keeps_draft':True}
                form.get_by_role('button',name='Cancel memory edit').click();page.get_by_role('button',name='Agents',exact=True).click();form=page.get_by_role('form',name='Create agent preset')
                form.get_by_label('Name',exact=True).fill('Rendered review preset');form.get_by_label('Instructions',exact=True).fill('Inspect before changing');form.get_by_label('Allowed tool IDs (one per line)',exact=True).fill('read_file');form.get_by_label('Maximum steps',exact=True).fill('12');form.get_by_role('button',name='Save preset',exact=True).click();page.get_by_text('Preset saved',exact=True).wait_for();page.get_by_text('Loading current settings…',exact=True).wait_for(state='hidden');report['created_preset_metadata']=[{key:row.get(key) for key in ('id','tools','tools_mode','file_revision')} for row in state.agent_store.list_custom_agents()]
                page.get_by_role('button',name='Edit preset',exact=True).click();form=page.get_by_role('form',name='Edit agent preset');form.wait_for();page.screenshot(path=str(destination/'preset-open-debug.png'));form.get_by_label('Allowed tool IDs (one per line)',exact=True).fill('read_file\nsearch_files');form.get_by_label('Maximum steps',exact=True).fill('9');form.get_by_role('button',name='Save preset changes',exact=True).click();page.get_by_text('Preset updated for future runs',exact=True).wait_for()
                preset=next(row for row in state.agent_store.list_custom_agents() if row['name']=='Rendered review preset');assert preset['tools']==['read_file','search_files'] and preset['limits']['max_steps']==9 and preset['tools_mode']=='explicit'
                report['preset']={'explicit_tools':preset['tools'],'max_steps':9,'source_revision_present':bool(preset['file_revision']),'tasks_created':len(state.agent_store.list_tasks())}
                page.get_by_role('button',name='Edit preset',exact=True).click();axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                for zoom in (100,200):
                    page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom})
                    for theme in ('dark','light'):
                        if page.locator('html').get_attribute('data-theme')!=theme:
                            page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                        entry={'theme':theme,'zoom_percent':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['violations']]};report['states'].append(entry);assert not entry['overflow'] and not entry['violations'],entry
                        form=page.get_by_role('form',name='Edit agent preset');form.get_by_role('button',name='Save preset changes').scroll_into_view_if_needed();form.get_by_role('button',name='Save preset changes').focus();assert page.evaluate('document.activeElement.textContent')=='Save preset changes';page.screenshot(path=str(destination/f'managers-{theme}-{zoom}.png'))
                report['production_bundle']=page.evaluate('Array.from(document.scripts).map(s=>s.src).find(s=>s.includes("/assets/index-"))?.split("/").pop()');assert not report['errors'];browser.close()
        finally:server.should_exit=True;worker.join(timeout=10);(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
