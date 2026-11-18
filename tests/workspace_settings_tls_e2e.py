"""Bounded real-host responsive settings proof. Only temporary identities/state.

python tests/workspace_settings_tls_e2e.py OUTPUT_DIR
Uses an installed Chromium; never bypasses TLS, installs a CA, or contacts AI.
"""
from __future__ import annotations
import json, os, re, shutil, socket, sys, tempfile, threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import expect, sync_playwright


def run(destination):
    output=Path(destination);output.mkdir(parents=True,exist_ok=True)
    report={'viewports':[],'checks':[],'errors':[],'axe':[]}
    with tempfile.TemporaryDirectory(prefix='termx-settings-proof-') as scratch:
        root=Path(scratch);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        state=AppState(passcode=None,credentials=CredentialStore(memory={}))
        owner=state.identity.setup_owner('settings-fixture','settings-fixture-password-123')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        project=state.projects.register(str(root),name='TermX workspace')
        for title in ['Review local HTTPS','Navigation redesign','Certificate lifecycle']:
            state.workspace.create_session(owner,title=title,project_id=project['id'],cwd=str(root))
        memory=state.workspace.save_memory(owner,content='Use verified TLS for LAN connections',provenance='Security review')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on',access_log=False,proxy_headers=False))
        worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+20
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('TERMX_TEST_CHROMIUM') or shutil.which('chromium'),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/')
                page.get_by_label('Username',exact=True).fill('settings-fixture');page.get_by_label('Password',exact=True).fill('settings-fixture-password-123');page.get_by_role('button',name='Continue with password').click()
                # The IDE layout is the default: Settings is a tab, opened from the activity bar.
                page.get_by_role('tablist',name='Editor group 1 tabs').wait_for(timeout=60000)
                sidebar=page.get_by_role('complementary',name='Agents',exact=True)
                activity_bar=page.get_by_role('toolbar',name='Activity bar',exact=True)
                # One persistent Settings entry lives in the activity bar.
                opener=activity_bar.get_by_role('button',name='Settings',exact=True)
                assert opener.count()==1
                assert page.get_by_role('region',name='conversation panel').count()==0,'the classic dock is not the default'
                assert sidebar.get_by_role('button',name='Settings',exact=True).count()==0
                assert sidebar.get_by_role('button',name='Access & sessions',exact=True).count()==0
                assert sidebar.get_by_role('button',name='Main agent').count()==1
                page.screenshot(path=str(output/'drawer-desktop.png'))
                opener.click()
                settings_tab=page.get_by_role('tab',name=re.compile(r'^Settings'))
                expect(settings_tab).to_have_attribute('aria-selected','true')
                dialog=page.get_by_role('region',name='Settings',exact=True);dialog.wait_for()
                categories=['General','Characters','Models & engines','Voice','Cloud accounts','Agents','Bots','Plugins & skills','Tools & connections','Memory','Hooks','Automations','Runners','Access & sessions','Safety & auto review']
                nav=dialog.get_by_role('navigation',name='Settings categories')
                mobile_category=dialog.get_by_role('combobox',name='Settings category',exact=True)
                def choose_category(label):
                    if mobile_category.is_visible():mobile_category.select_option(label=label)
                    else:nav.get_by_role('link',name=label,exact=True).click()
                expect(nav.get_by_role('link')).to_have_count(len(categories))  # retries: the list can re-render while settings load
                for category in categories:
                    expect(nav.get_by_role('link',name=category,exact=True)).to_have_count(1)
                nav.get_by_role('link',name='Access & sessions',exact=True).click()
                # LAN HTTPS sits in the collapsed "Host connectivity" disclosure, and the port in its "Network settings" disclosure.
                dialog.get_by_text('Host connectivity',exact=True).click();dialog.get_by_role('heading',name='Local network HTTPS',exact=True).wait_for()
                dialog.get_by_text('Network settings',exact=True).click()
                # Stage and undo configuration on a throwaway profile. The proof
                # never restarts or exposes a LAN listener or opens firewall rules.
                dialog.get_by_label('HTTPS port',exact=True).fill('8899');dialog.get_by_role('button',name='Save LAN HTTPS settings',exact=True).click();dialog.get_by_text('Saved. Restart the host to apply LAN exposure changes. No trust root was installed.',exact=True).wait_for()
                assert json.loads((root/'config/lan-tls-settings.json').read_text())['port']==8899
                report['checks'].append('TLS configuration saved to isolated profile; no listener/trust installed')
                for width,height in [(1440,1000),(768,900),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height})
                    for theme in ['dark','light']:
                        choose_category('General')
                        dialog.get_by_role('combobox',name='Theme preference',exact=True).select_option(theme)
                        assert page.locator('html').get_attribute('data-theme')==theme
                        page.wait_for_timeout(100)
                        # Take a real category view, without mock layout or hidden controls.
                        choose_category('Memory')
                        dialog.get_by_text('Use verified TLS for LAN connections',exact=True).wait_for()
                        bounds=dialog.bounding_box();assert bounds and bounds['x']>=-1 and bounds['x']+bounds['width']<=width+1
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        category_control=mobile_category if width<=768 else nav
                        category_layout=category_control.evaluate('(node)=>({scroll:node.scrollWidth,width:node.clientWidth,height:node.getBoundingClientRect().height,display:getComputedStyle(node).display})')
                        if width<=768:
                            expect(mobile_category).to_be_visible();expect(mobile_category).to_have_value('memory')
                            assert mobile_category.get_by_role('option').count()==len(categories)
                            assert category_layout['height']>=44 and category_layout['scroll']<=category_layout['width']
                        # A tab is not a modal: focus moves through real controls and is never lost to the page body.
                        for key in ['Tab','Shift+Tab']*8:
                            page.keyboard.press(key);assert page.evaluate('document.activeElement!==document.body')
                        page.screenshot(path=str(output/f'settings-{width}-{theme}.png'))
                        report['viewports'].append({'width':width,'height':height,'theme':theme,'bounds':bounds,'category_layout':category_layout,'page_overflow':False,'focus_kept':True})
                page.set_viewport_size({'width':1440,'height':1000})
                nav.get_by_role('link',name='Memory',exact=True).click();dialog.get_by_role('button',name='Edit memory',exact=True).click()
                form=dialog.get_by_role('form',name='Edit memory');form.get_by_label('Fact or decision').fill('Keep this unsaved security decision')
                # Unsaved edits are guarded by an in-page confirmation (not a native prompt); the footer reports the draft.
                discard_prompt=page.get_by_role('dialog',name='Discard unsaved changes?',exact=True)
                expect(dialog.get_by_role('status').filter(has_text='Unsaved changes')).to_be_visible()
                nav.get_by_role('link',name='Hooks',exact=True).click();discard_prompt.wait_for();discard_prompt.get_by_role('button',name='Keep editing',exact=True).click();expect(discard_prompt).to_be_hidden();assert form.get_by_label('Fact or decision').input_value()=='Keep this unsaved security decision'
                # Closing the Settings tab with unsaved edits asks first, and keeping them leaves the tab and the draft.
                settings_tab.locator('.tab-close').click();discard_prompt.wait_for();discard_prompt.get_by_role('button',name='Keep editing',exact=True).click();expect(discard_prompt).to_be_hidden();assert dialog.is_visible()
                assert form.get_by_label('Fact or decision').input_value()=='Keep this unsaved security decision'
                form.get_by_role('button',name='Save memory changes',exact=True).click();dialog.get_by_text('Memory updated',exact=True).wait_for()
                assert state.workspace.store.get('memory',memory['id'])['content']=='Keep this unsaved security decision'
                dialog.get_by_role('button',name='Delete',exact=True).click();review=page.get_by_role('dialog',name='Delete?',exact=True);review.wait_for();review.get_by_role('button',name='Cancel',exact=True).click();assert state.workspace.store.get('memory',memory['id'])
                # A successful validation request must not commit the source draft.
                nav.get_by_role('link',name='Plugins & skills',exact=True).click()
                dialog.get_by_text('Add a skill',exact=True).click()
                dialog.get_by_label('Skill folder name',exact=True).fill('inspect-fixture')
                instructions='---\nname: inspect-fixture\ndescription: Inspect a temporary fixture without changing files.\n---\nRead the selected fixture and report findings.\n'
                dialog.get_by_label('Skill instructions',exact=True).fill(instructions)
                dialog.get_by_role('button',name='Validate skill',exact=True).click()
                dialog.get_by_text('Skill metadata and instructions validated',exact=True).wait_for()
                nav.get_by_role('link',name='Memory',exact=True).click();discard_prompt.wait_for();discard_prompt.get_by_role('button',name='Keep editing',exact=True).click();expect(discard_prompt).to_be_hidden()
                assert dialog.get_by_label('Skill instructions',exact=True).input_value()==instructions
                nav.get_by_role('link',name='Memory',exact=True).click();discard_prompt.wait_for();discard_prompt.get_by_role('button',name='Discard changes',exact=True).click();expect(discard_prompt).to_be_hidden()
                report['checks'].append('successful skill validation preserves unsaved draft and guarded navigation')
                # All categories remain reachable through one registry.
                for category in categories:
                    nav.get_by_role('link',name=category,exact=True).click();assert nav.get_by_role('link',name=category,exact=True).get_attribute('aria-current')=='page'
                nav.get_by_role('link',name='Memory',exact=True).click()
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                page.add_script_tag(path=str(axe))
                for width in [1440,390]:
                    page.set_viewport_size({'width':width,'height':900});result=page.evaluate('async()=>{const result=await axe.run(document.querySelector(".settings-surface-page"),{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa"]}});return result.violations.map(v=>({id:v.id,impact:v.impact,targets:v.nodes.map(n=>n.target)}))}')
                    report['axe'].append({'width':width,'violations':result});assert not result,result
                page.set_viewport_size({'width':1440,'height':1000});settings_tab.locator('.tab-close').click()
                expect(dialog).to_be_hidden()
                expect(settings_tab).to_have_count(0)
                # A shareable manager route still lands on its page: /manage/access opens the Settings tab on that category.
                page.goto(f'http://127.0.0.1:{port}/manage/access')
                page.get_by_role('tab',name='Settings',exact=True).wait_for(timeout=60000)
                expect(page.get_by_role('navigation',name='Settings categories').get_by_role('link',name='Access & sessions',exact=True)).to_have_attribute('aria-current','page')
                report['checks'] += ['one sticky Settings entry; no drawer category duplication',f'all {len(categories)} categories reachable, including Characters','memory edit/save and draft guards','destructive cancellation leaves record intact','closing the Settings tab with unsaved edits asks first','focus never lost to the page body at all tested widths','a /manage/access deep link opens the Settings tab on its category']
                assert not report['errors'],report['errors']
                browser.close()
        finally:
            server.should_exit=True;worker.join(timeout=15)
            (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':run(sys.argv[1])
