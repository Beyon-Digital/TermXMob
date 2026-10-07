"""Actual account-scoped project pinning and compact session configuration UI."""
from __future__ import annotations
import json,os,socket,sys,tempfile,threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright
from workspace_ui_snapshot import snapshot_ui


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'actual-project-pins-and-session-controls','provider_queries':0,'errors':[],'states':[]}
    with tempfile.TemporaryDirectory(prefix='termx-session-controls-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        state=AppState(passcode=None);owner=state.identity.setup_owner('controls-owner','controls-fixture-password-123')
        for name in ('Pinned project','Recent project'):(root/name).mkdir()
        pinned=state.projects.register(str(root/'Pinned project'),name='Pinned project')
        recent=state.projects.register(str(root/'Recent project'),name='Recent project')
        state.agent_store.put_provider('fixture',kind='openai-compatible',name='Fixture account',base_url='http://127.0.0.1:1/v1',model='fixture-a,fixture-b',capabilities=['chat'],secret_configured=False)
        app=create_app(state,web_dir=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist')))))
        session=state.workspace.create_session(owner,title='Configuration proof',project_id=recent['id'],cwd=recent['path'],provider_id='fixture',model='fixture-a')
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+25
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        page=None
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce');page=context.new_page()
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                url=f'http://127.0.0.1:{port}/?session={session["id"]}'
                page.goto(url);page.get_by_label('Username',exact=True).fill('controls-owner');page.get_by_label('Password',exact=True).fill('controls-fixture-password-123');page.get_by_role('button',name='Continue with password').click();page.get_by_role('textbox',name='Message',exact=True).wait_for()
                page.get_by_role('button',name='Pin project Pinned project',exact=True).click();page.get_by_role('button',name='Unpin project Pinned project',exact=True).wait_for()
                assert page.locator('.project-group').all_text_contents()[0]=='Pinned project'
                assert state.workspace.session(owner,session['id'],turns=False)['project_id']==recent['id']
                page.reload();page.get_by_role('button',name='Unpin project Pinned project',exact=True).wait_for();assert page.locator('.project-group').all_text_contents()[0]=='Pinned project'
                other=context.new_page();other.goto(url);other.get_by_role('button',name='Unpin project Pinned project',exact=True).wait_for();other.close()
                report['pins']={'canonical_project_persisted':True,'reload_and_second_window':True,'active_session_target_unchanged':True}
                other=context.new_page();other.goto(url);other.get_by_role('textbox',name='Message',exact=True).wait_for();other.get_by_role('button',name='Session engine and model',exact=True).click()
                page.get_by_role('button',name='Managers',exact=True).click()
                page.get_by_role('button',name='Favorite fixture-b · Fixture account',exact=True).click()
                other.wait_for_function("Array.from(document.querySelectorAll('select[aria-label=\"Session model\"] option')).some(row=>row.textContent==='★ fixture-b')")
                assert state.workspace.session(owner,session['id'],turns=False)['model']=='fixture-a'
                assert not state.agent_store.list_tasks()
                report['manager_favorites']={'shared_with_mounted_session_and_second_window':True,'canonical_model_unchanged':True,'tasks_created':0,'provider_queries':0}
                page.get_by_role('button',name='Back to workspace',exact=True).click();other.close()
                assert page.get_by_role('combobox',name='Session model',exact=True).count()==0
                page.get_by_role('button',name='Session engine and model',exact=True).click();dialog=page.get_by_role('dialog',name='Session engine and model',exact=True)
                with page.expect_response(lambda response:response.request.method=='PATCH' and '/api/workspace/sessions/'+session['id'] in response.url):dialog.get_by_label('Session model',exact=True).select_option('fixture-b')
                page.wait_for_function("document.querySelector('select[aria-label=\"Session model\"]')?.value==='fixture-b'")
                assert state.workspace.session(owner,session['id'],turns=False)['model']=='fixture-b'
                dialog.get_by_text('Execution permissions and source',exact=True).click();dialog.get_by_text('Current account ceiling',exact=True).wait_for();dialog.get_by_text('Administer this host: Permitted by account',exact=True).wait_for()
                report['model']={'only_selected_session_patched':True,'engine_model_single_chip':True,'account_ceiling_source_visible':True,'tasks_created':len(state.agent_store.list_tasks())};assert report['model']['tasks_created']==0
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                for zoom in (100,200):
                    page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom})
                    for theme in ('dark','light'):
                        # Apply theme through the actual Appearance UI between dialogs.
                        dialog.get_by_role('button',name='Close',exact=True).click()
                        if page.locator('html').get_attribute('data-theme')!=theme:
                            page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        if page.get_by_role('button',name='Show sidebar',exact=True).count():page.get_by_role('button',name='Show sidebar',exact=True).click()
                        page.get_by_role('button',name='Managers',exact=True).click()
                        if page.get_by_role('button',name='Close navigation',exact=True).count():page.get_by_role('button',name='Close navigation',exact=True).click()
                        favorite=page.get_by_role('button',name='Remove favorite fixture-b · Fixture account',exact=True);favorite.wait_for();favorite.scroll_into_view_if_needed();favorite.focus()
                        page.add_script_tag(path=str(axe));manager_audit=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                        manager_value={'surface':'model-manager','theme':theme,'zoom':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':item['id'],'targets':[row['target'] for row in item['nodes']]} for item in manager_audit['violations']]};report['states'].append(manager_value);assert not manager_value['overflow'] and not manager_value['violations'],manager_value
                        assert favorite.evaluate("element=>{const box=element.getBoundingClientRect();return element.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2))}"),'Favorite control was covered'
                        page.screenshot(path=str(destination/f'model-favorites-{theme}-{zoom}.png'))
                        page.get_by_role('button',name='Back to workspace',exact=True).click()
                        page.get_by_role('button',name='Session engine and model',exact=True).click();dialog=page.get_by_role('dialog',name='Session engine and model',exact=True)
                        page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                        value={'theme':theme,'zoom':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':item['id'],'targets':[row['target'] for row in item['nodes']]} for item in result['violations']]};report['states'].append(value);assert not value['overflow'] and not value['violations'],value
                        model=dialog.get_by_label('Session model',exact=True);model.scroll_into_view_if_needed();model.focus();assert page.evaluate("document.activeElement.getAttribute('aria-label')")=='Session model'
                        page.screenshot(path=str(destination/f'session-controls-{theme}-{zoom}.png'))
                dialog.get_by_role('button',name='Close',exact=True).click();page.set_viewport_size({'width':1440,'height':1000});page.reload()
                page.get_by_role('textbox',name='Message',exact=True).wait_for()
                if page.get_by_role('button',name='Show sidebar',exact=True).count():page.get_by_role('button',name='Show sidebar',exact=True).click()
                page.get_by_role('button',name='Unpin project Pinned project',exact=True).click();page.get_by_role('button',name='Pin project Pinned project',exact=True).wait_for()
                assert not state.workspace.store.get('project_pins',owner.id)['projects'];assert not report['errors'];report['status']='passed';browser.close()
        except Exception:
            if page:
                try:
                    if not page.is_closed():page.screenshot(path=str(destination/'failure.png'));(destination/'failure.html').write_text(page.content())
                except Exception:pass  # Preserve the original assertion after Playwright cleanup.
            raise
        finally:server.should_exit=True;worker.join(timeout=10);(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
