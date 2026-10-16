"""Bounded real-host responsive settings proof. Only temporary identities/state.

python tests/workspace_settings_tls_e2e.py OUTPUT_DIR
Uses an installed Chromium; never bypasses TLS, installs a CA, or contacts AI.
"""
from __future__ import annotations
import json, os, socket, sys, tempfile, threading
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright


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
                browser=playwright.chromium.launch(executable_path=os.environ.get('TERMX_TEST_CHROMIUM','/usr/bin/chromium'),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/')
                page.get_by_label('Username',exact=True).fill('settings-fixture');page.get_by_label('Password',exact=True).fill('settings-fixture-password-123');page.get_by_role('button',name='Continue with password').click()
                page.get_by_role('button',name='Settings',exact=True).wait_for(timeout=20000)
                sidebar=page.get_by_role('complementary',name='Projects and conversations')
                assert sidebar.get_by_role('button',name='Settings',exact=True).count()==1
                assert sidebar.get_by_role('button',name='Access & sessions',exact=True).count()==0
                assert sidebar.get_by_role('button',name='Home / Overview',exact=True).count()==1
                page.screenshot(path=str(output/'drawer-desktop.png'))
                opener=page.get_by_role('button',name='Settings',exact=True);opener.click()
                dialog=page.get_by_role('dialog',name='Settings',exact=True);dialog.wait_for()
                nav=dialog.get_by_role('navigation',name='Settings categories');assert nav.get_by_role('button').count()==14
                nav.get_by_role('button',name='Access & sessions',exact=True).click();dialog.get_by_role('heading',name='Local network HTTPS',exact=True).wait_for()
                assert page.url.endswith('/manage/access')
                # Stage and undo configuration on a throwaway profile. The proof
                # never restarts or exposes a LAN listener or opens firewall rules.
                dialog.get_by_label('HTTPS port',exact=True).fill('8899');dialog.get_by_role('button',name='Save LAN HTTPS settings',exact=True).click();dialog.get_by_text('Saved. Restart the host to apply LAN exposure changes. No trust root was installed.',exact=True).wait_for()
                assert json.loads((root/'config/lan-tls-settings.json').read_text())['port']==8899
                report['checks'].append('TLS configuration saved to isolated profile; no listener/trust installed')
                for width,height in [(1440,1000),(768,900),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height})
                    for theme in ['dark','light']:
                        nav.get_by_role('button',name='Appearance',exact=True).click()
                        dialog.get_by_role('radio',name=theme.title(),exact=True).check()
                        assert page.locator('html').get_attribute('data-theme')==theme
                        page.wait_for_timeout(100)
                        # Take a real category view, without mock layout or hidden controls.
                        nav.get_by_role('button',name='Memory',exact=True).click()
                        dialog.get_by_text('Use verified TLS for LAN connections',exact=True).wait_for()
                        bounds=dialog.bounding_box();assert bounds and bounds['x']>=-1 and bounds['x']+bounds['width']<=width+1
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        category_layout=nav.evaluate('(node)=>({scroll:node.scrollWidth,width:node.clientWidth,display:getComputedStyle(node).display})')
                        if width==390:assert category_layout['scroll']>category_layout['width'] and category_layout['display']=='flex'
                        # Focus remains inside the modal across forward/backward tab cycles.
                        for key in ['Tab','Shift+Tab']*8:
                            page.keyboard.press(key);assert dialog.evaluate('(node)=>node.contains(document.activeElement)')
                        page.screenshot(path=str(output/f'settings-{width}-{theme}.png'))
                        report['viewports'].append({'width':width,'height':height,'theme':theme,'bounds':bounds,'category_layout':category_layout,'page_overflow':False,'focus_trapped':True})
                page.set_viewport_size({'width':1440,'height':1000})
                nav.get_by_role('button',name='Memory',exact=True).click();dialog.get_by_role('button',name='Edit memory',exact=True).click()
                form=dialog.get_by_role('form',name='Edit memory');form.get_by_label('Fact or decision').fill('Keep this unsaved security decision')
                page.once('dialog',lambda prompt:prompt.dismiss());nav.get_by_role('button',name='Hooks',exact=True).click();assert form.get_by_label('Fact or decision').input_value()=='Keep this unsaved security decision'
                page.once('dialog',lambda prompt:prompt.dismiss());page.keyboard.press('Escape');assert dialog.is_visible()
                form.get_by_role('button',name='Save memory changes',exact=True).click();dialog.get_by_text('Memory updated',exact=True).wait_for()
                assert state.workspace.store.get('memory',memory['id'])['content']=='Keep this unsaved security decision'
                dialog.get_by_role('button',name='Delete',exact=True).click();review=page.get_by_role('dialog',name='Delete?',exact=True);review.wait_for();review.get_by_role('button',name='Cancel',exact=True).click();assert state.workspace.store.get('memory',memory['id'])
                # All categories remain reachable through one registry.
                for category in ['Models & engines','Cloud accounts','Agents','Plugins & skills','Tools & connections','Memory','Hooks','Automations','Runners','Access & sessions','Appearance','Keyboard shortcuts','Safety & Auto Review','General']:
                    nav.get_by_role('button',name=category,exact=True).click();assert nav.get_by_role('button',name=category,exact=True).get_attribute('aria-current')=='page'
                nav.get_by_role('button',name='Memory',exact=True).click()
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                page.add_script_tag(path=str(axe))
                for width in [1440,390]:
                    page.set_viewport_size({'width':width,'height':900});result=page.evaluate('async()=>{const result=await axe.run(document.querySelector(".settings-surface"),{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa"]}});return result.violations.map(v=>({id:v.id,impact:v.impact,targets:v.nodes.map(n=>n.target)}))}')
                    report['axe'].append({'width':width,'violations':result});assert not result,result
                page.set_viewport_size({'width':1440,'height':1000});dialog.get_by_role('button',name='Close',exact=True).click();assert not dialog.is_visible();assert page.get_by_role('button',name='Show sidebar',exact=True).evaluate('(node)=>node===document.activeElement')
                report['checks'] += ['one sticky Settings entry; no drawer category duplication','all 14 categories reachable','memory edit/save and draft guards','destructive cancellation leaves record intact','Escape close and focus return','focus trap at all tested widths','shareable manager routes']
                assert not report['errors'],report['errors']
                browser.close()
        finally:
            server.should_exit=True;worker.join(timeout=15)
            (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':run(sys.argv[1])
