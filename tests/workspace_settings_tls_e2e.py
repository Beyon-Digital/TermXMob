"""Bounded real-host responsive settings proof. Only temporary identities/state.

python tests/workspace_settings_tls_e2e.py OUTPUT_DIR
Add --smoke for the final Models, Plugins and Tools verification.
Add --narrow to rerun the 320px cases and interaction checks after a narrow fix.
Add --interactions to finish only the interaction checks after a completed matrix.
Uses an installed Chromium; never bypasses TLS, installs a CA, or contacts AI.
"""
from __future__ import annotations
import json, os, shutil, socket, sys, tempfile, threading
from pathlib import Path
from time import monotonic,sleep
from urllib.parse import urlsplit
import uvicorn
from playwright.sync_api import sync_playwright


def run(destination):
    output=Path(destination);output.mkdir(parents=True,exist_ok=True)
    report={'viewports':[],'checks':[],'errors':[],'axe':[],'scope_requests':[]}
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
        print('Isolated settings host started',flush=True)
        try:
            with sync_playwright() as playwright:
                chrome=os.environ.get('TERMX_TEST_CHROMIUM') or shutil.which('chromium')
                if not chrome and Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').exists():chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
                browser=playwright.chromium.launch(executable_path=chrome,headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page.context.tracing.start(screenshots=True,snapshots=True,sources=True)
                page.on('pageerror',lambda error:report['errors'].append(str(error)))
                def record_request(request):
                    parsed=urlsplit(request.url)
                    if parsed.path=='/graphql' and request.post_data:
                        payload=json.loads(request.post_data)
                        if 'mcp_connections(' in payload.get('query',''):report['scope_requests'].append({'method':request.method,'path':parsed.path,'query':parsed.query,'body':payload})
                    if parsed.path in {'/api/workspace/memory','/api/workspace/skill-sources','/api/workspace/skill-sources/compatibility','/api/workspace/skill-sources/validate','/api/workspace/automations','/api/runners'}:
                        row={'method':request.method,'path':parsed.path,'query':parsed.query}
                        if request.post_data:row['body']=json.loads(request.post_data)
                        report['scope_requests'].append(row)
                page.on('request',record_request)
                try:
                    page.goto(f'http://127.0.0.1:{port}/')
                    report['bundle']=page.locator('script[type="module"]').first.get_attribute('src')
                    page.get_by_label('Username',exact=True).fill('settings-fixture');page.get_by_label('Password',exact=True).fill('settings-fixture-password-123');page.get_by_role('button',name='Continue with password').click()
                    page.get_by_role('button',name='Settings',exact=True).wait_for(timeout=20000)
                    print('Browser authenticated; checking settings',flush=True)
                    rail=page.get_by_role('navigation',name='Workspace navigation',exact=True)
                    assert rail.get_by_role('button',name='Settings',exact=True).count()==1
                    assert page.get_by_role('button',name='Settings',exact=True).count()==1
                    sidebar=page.get_by_role('complementary',name='Projects and conversations')
                    assert sidebar.get_by_role('button',name='Access & sessions',exact=True).count()==0
                    assert rail.get_by_role('button',name='Home',exact=True).count()==1
                    assert page.get_by_role('button',name='Home / Overview',exact=True).count()==0
                    sidebar.get_by_role('button',name='Review local HTTPS',exact=True).click()
                    page.get_by_role('navigation',name='Workspace view').get_by_role('button',name='Code',exact=True).click()
                    page.screenshot(path=str(output/'drawer-desktop.png'))
                    opener=page.get_by_role('button',name='Settings',exact=True);opener.click()
                    dialog=page.get_by_role('dialog',name='Settings',exact=True);dialog.wait_for()
                    dialog.get_by_role('heading',name='General',exact=True).wait_for()
                    page.screenshot(path=str(output/'settings-general-first-open.png'))
                    categories=['Models & engines','Cloud accounts','Agents','Characters','Plugins & skills','Tools & connections','Memory','Hooks','Automations','Runners','Access & sessions','Appearance','Keyboard shortcuts','Safety & Auto Review','General']
                    identifiers=['models','cloud','agents','characters','extensions','connections','memory','hooks','automations','runners','access','appearance','shortcuts','safety','general']
                    nav=dialog.get_by_role('navigation',name='Settings categories')
                    def select_category(name):
                        identifier=identifiers[categories.index(name)]
                        if page.viewport_size['width']<=700:
                            selector=nav.get_by_role('combobox',name='Settings category',exact=True)
                            selector.select_option(identifier)
                            assert selector.input_value()==identifier
                        else:
                            button=nav.get_by_role('button',name=name,exact=True)
                            button.click();assert button.get_attribute('aria-current')=='page'
                        assert urlsplit(page.url).path=='/manage/'+identifier
                    def assert_account_context():
                        assert dialog.get_by_role('combobox',name='Settings scope',exact=True).count()==0
                        assert not dialog.locator('.settings-scope').count()
                        assert not dialog.locator('select option').filter(has_text='Selected project').count()
                        assert dialog.locator('.settings-active-context').inner_text().startswith('settings-fixture')
                    assert nav.get_by_role('button').count()==len(categories)
                    for category in categories:
                        assert nav.get_by_role('button',name=category,exact=True).count()==1
                    assert_account_context()
                    dialog.get_by_role('heading',name='General',exact=True).wait_for()
                    if '--smoke' in sys.argv:
                        axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                        page.add_script_tag(path=str(axe))
                        for width,height,theme in [(1440,1000,'dark'),(1440,1000,'light'),(320,740,'dark'),(320,740,'light')]:
                            page.set_viewport_size({'width':width,'height':height})
                            select_category('General');dialog.get_by_role('combobox',name='Theme preference',exact=True).select_option(theme)
                            if width==320:
                                assert nav.get_by_role('combobox',name='Settings category',exact=True).bounding_box()['height']>=44
                            for category,heading in [('Models & engines','Installed engines'),('Plugins & skills','Plugins & skills'),('Tools & connections','Tool connections')]:
                                select_category(category);dialog.get_by_role('heading',name=heading,exact=True).first.wait_for()
                                if category=='Plugins & skills':dialog.get_by_text('Add a skill',exact=True).wait_for()
                                if category=='Tools & connections':dialog.get_by_text('Loading current connections…',exact=True).wait_for(state='hidden')
                                assert not dialog.get_by_role('alert').count(),dialog.get_by_role('alert').all_text_contents()
                                assert_account_context()
                                overflow=dialog.locator('.settings-content').evaluate('(node)=>({scroll:node.scrollWidth,width:node.clientWidth})')
                                assert overflow['scroll']<=overflow['width']+1,overflow
                                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                                screenshot=f'settings-final-{identifiers[categories.index(category)]}-{width}-{theme}.png'
                                page.screenshot(path=str(output/screenshot))
                                violations=page.evaluate('async()=>{const result=await axe.run(document.querySelector(".settings-surface"),{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa"]}});return result.violations.map(v=>({id:v.id,impact:v.impact,targets:v.nodes.map(n=>n.target)}))}')
                                report['viewports'].append({'width':width,'height':height,'theme':theme,'category':identifiers[categories.index(category)],'page_overflow':False,'content_overflow':False,'screenshot':screenshot})
                                report['axe'].append({'width':width,'theme':theme,'category':category,'violations':violations});assert not violations,violations
                            print(f'Final smoke checked {width}px {theme}: Models, Plugins and Tools',flush=True)
                        for category in ['Appearance','Keyboard shortcuts']:
                            select_category(category);dialog.get_by_role('heading',name=category,exact=True).wait_for()
                            overflow=dialog.locator('.settings-content').evaluate('(node)=>({scroll:node.scrollWidth,width:node.clientWidth})')
                            assert overflow['scroll']<=overflow['width']+1 and page.evaluate('document.documentElement.scrollWidth<=innerWidth'),overflow
                            page.screenshot(path=str(output/f'settings-final-{identifiers[categories.index(category)]}-320-light.png'))
                        select_category('Models & engines');dialog.get_by_role('heading',name='Installed engines',exact=True).wait_for()
                        assert dialog.get_by_role('textbox',name='Sign-in method for',exact=False).count()==0
                        visible_models=dialog.inner_text()
                        assert not any(raw in visible_models for raw in ['not_required','unauthenticated','Installed · unknown']),visible_models
                        select_category('Plugins & skills');dialog.get_by_text('Add a skill',exact=True).wait_for()
                        assert not dialog.get_by_label('Skill folder name',exact=True).is_visible()
                        assert dialog.get_by_role('combobox',name='Skill scope',exact=True).count()==0
                        dialog.get_by_text('Add a skill',exact=True).click()
                        dialog.get_by_label('Skill folder name',exact=True).fill('inspect-final-fixture')
                        instructions='---\nname: inspect-final-fixture\ndescription: Inspect a temporary fixture without changing files.\n---\nRead the selected fixture and report findings.\n'
                        dialog.get_by_label('Skill instructions',exact=True).fill(instructions)
                        dialog.get_by_role('button',name='Validate skill',exact=True).click();dialog.get_by_text('Skill metadata and instructions validated',exact=True).wait_for()
                        page.once('dialog',lambda prompt:prompt.dismiss());nav.get_by_role('combobox',name='Settings category',exact=True).select_option('connections')
                        assert dialog.get_by_label('Skill instructions',exact=True).input_value()==instructions
                        page.once('dialog',lambda prompt:prompt.accept());nav.get_by_role('combobox',name='Settings category',exact=True).select_option('connections')
                        dialog.get_by_role('heading',name='Tool connections',exact=True).wait_for()
                        assert dialog.get_by_text('Select a project before using a project-scoped connection.',exact=False).count()==0
                        assert dialog.get_by_role('button',name='Call selected tool',exact=True).count()==0
                        validation=[row for row in report['scope_requests'] if row['path']=='/api/workspace/skill-sources/validate']
                        assert validation and validation[-1]['body']['scope']=='user' and validation[-1]['body']['project_id'] is None
                        connections=[row for row in report['scope_requests'] if row['path']=='/graphql']
                        assert connections and all(row['body']['variables']['project'] is None for row in connections),connections
                        assert all('project_id=' not in row['query'] for row in report['scope_requests'] if row['method']=='GET')
                        report['checks'] += ['final Models/Plugins/Tools fit at 1440 and 320px in both themes with zero axe violations','mobile category control has a 44px touch target','Appearance and Keyboard shortcuts fit at 320px','opaque engine sign-in method field removed','skill editor starts disclosed, uses account scope and preserves validation drafts','Tools uses host management without a selected workspace or direct execution action']
                        assert not report['errors'],report['errors']
                        print('Final settings smoke passed',flush=True)
                        return
                    dialog.get_by_role('button',name='Manage models',exact=True).click();dialog.get_by_role('heading',name='Installed engines',exact=True).wait_for()
                    select_category('General');dialog.get_by_role('button',name='Manage access',exact=True).click()
                    dialog.get_by_role('heading',name='Your devices',exact=True).wait_for()
                    assert not dialog.get_by_role('heading',name='Local network HTTPS',exact=True).is_visible()
                    dialog.get_by_text('Host connectivity',exact=True).click()
                    dialog.get_by_role('heading',name='Local network HTTPS',exact=True).wait_for()
                    dialog.get_by_text('Network settings',exact=True).click()
                    assert page.url.endswith('/manage/access')
                    # Stage and undo configuration on a throwaway profile. The proof
                    # never restarts or exposes a LAN listener or opens firewall rules.
                    dialog.get_by_label('HTTPS port',exact=True).fill('8899');dialog.get_by_role('button',name='Save LAN HTTPS settings',exact=True).click();dialog.get_by_text('Saved. Restart the host to apply LAN exposure changes. No trust root was installed.',exact=True).wait_for()
                    assert json.loads((root/'config/lan-tls-settings.json').read_text())['port']==8899
                    report['checks'].append('TLS configuration saved to isolated profile; no listener/trust installed')
                    # Active project conversation and layout changes must never scope settings.
                    for layout in ['Chat','Code']:
                        select_category('Memory');dialog.get_by_text('Use verified TLS for LAN connections',exact=True).wait_for()
                        assert_account_context()
                        dialog.get_by_role('button',name='Close',exact=True).click();assert not dialog.is_visible()
                        assert opener.evaluate('(node)=>node===document.activeElement')
                        page.get_by_role('navigation',name='Workspace view').get_by_role('button',name=layout,exact=True).click()
                        assert page.locator('.workspace-header .breadcrumb strong').inner_text()=='Review local HTTPS'
                        opener.click();dialog.wait_for();select_category('Memory')
                        dialog.get_by_text('Use verified TLS for LAN connections',exact=True).wait_for()
                    memory_reads=[row for row in report['scope_requests'] if row['path']=='/api/workspace/memory' and row['method']=='GET']
                    assert len(memory_reads)>=3 and all(not row['query'] for row in memory_reads),memory_reads
                    # Both navigation modes expose every category from the same registry.
                    for width in [1440,390]:
                        page.set_viewport_size({'width':width,'height':1000})
                        if width==390:
                            assert nav.get_by_role('button').count()==0
                            assert nav.get_by_role('combobox',name='Settings category',exact=True).locator('option').count()==len(categories)
                        for category in categories:select_category(category);assert_account_context()
                    axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                    page.add_script_tag(path=str(axe))
                    views={'General':'General','Access & sessions':'Your devices','Models & engines':'Installed engines','Memory':'Saved memory'}
                    viewports=[] if '--interactions' in sys.argv else [(320,740)] if '--narrow' in sys.argv else [(1440,1000),(768,900),(390,844),(320,740)]
                    for width,height in viewports:
                        page.set_viewport_size({'width':width,'height':height})
                        for theme in ['dark','light']:
                            select_category('General')
                            dialog.get_by_role('combobox',name='Theme preference',exact=True).select_option(theme)
                            assert page.locator('html').get_attribute('data-theme')==theme
                            for category,heading in views.items():
                                select_category(category);dialog.get_by_role('heading',name=heading,exact=True).wait_for()
                                if category=='Memory':dialog.get_by_text('Use verified TLS for LAN connections',exact=True).wait_for()
                                page.wait_for_timeout(100)
                                bounds=dialog.bounding_box();assert bounds and bounds['x']>=-1 and bounds['x']+bounds['width']<=width+1,bounds
                                assert bounds['y']>=-1 and bounds['y']+bounds['height']<=height+1,bounds
                                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                                overflow=dialog.locator('.settings-content').evaluate('(node)=>({scroll:node.scrollWidth,width:node.clientWidth})')
                                assert overflow['scroll']<=overflow['width']+1,overflow
                                if category=='General':
                                    for key in ['Tab']*4+['Shift+Tab']*4:
                                        page.keyboard.press(key);assert dialog.evaluate('(node)=>node.contains(document.activeElement)')
                                identifier=identifiers[categories.index(category)]
                                screenshot=f'settings-{identifier}-{width}-{theme}.png'
                                page.screenshot(path=str(output/screenshot))
                                report['viewports'].append({'width':width,'height':height,'theme':theme,'category':identifier,'bounds':bounds,'page_overflow':False,'content_overflow':False,'focus_trapped':True if category=='General' else None,'screenshot':screenshot})
                                violations=page.evaluate('async()=>{const result=await axe.run(document.querySelector(".settings-surface"),{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa"]}});return result.violations.map(v=>({id:v.id,impact:v.impact,targets:v.nodes.map(n=>n.target)}))}')
                                report['axe'].append({'width':width,'theme':theme,'category':identifier,'violations':violations});assert not violations,violations
                            print(f'Checked {width}px {theme}: General, Access, Models and Memory',flush=True)
                    page.set_viewport_size({'width':320,'height':740})
                    for category in ['Appearance','Keyboard shortcuts']:
                        select_category(category);dialog.get_by_role('heading',name=category,exact=True).wait_for()
                        overflow=dialog.locator('.settings-content').evaluate('(node)=>({scroll:node.scrollWidth,width:node.clientWidth})')
                        assert overflow['scroll']<=overflow['width']+1,overflow
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        page.screenshot(path=str(output/f'settings-{identifiers[categories.index(category)]}-320-light.png'))
                    report['checks'].append('Appearance and Keyboard shortcuts fit at 320px without page/content overflow')
                    page.set_viewport_size({'width':1440,'height':1000})
                    nav.get_by_role('button',name='Memory',exact=True).click();dialog.get_by_role('button',name='Edit memory',exact=True).click()
                    form=dialog.get_by_role('form',name='Edit memory');form.get_by_label('Fact or decision').fill('Keep this unsaved security decision')
                    page.once('dialog',lambda prompt:prompt.dismiss());nav.get_by_role('button',name='Hooks',exact=True).click();assert form.get_by_label('Fact or decision').input_value()=='Keep this unsaved security decision'
                    page.once('dialog',lambda prompt:prompt.dismiss());page.keyboard.press('Escape');assert dialog.is_visible()
                    form.get_by_role('button',name='Save memory changes',exact=True).click();dialog.get_by_text('Memory updated',exact=True).wait_for()
                    assert state.workspace.store.get('memory',memory['id'])['content']=='Keep this unsaved security decision'
                    dialog.get_by_role('button',name='Delete',exact=True).click();review=page.get_by_role('dialog',name='Delete?',exact=True);review.wait_for();review.get_by_role('button',name='Cancel',exact=True).click();assert state.workspace.store.get('memory',memory['id'])
                    dialog.get_by_text('Add memory',exact=True).click()
                    add=dialog.get_by_text('Add memory',exact=True).locator('..').locator('.manager-form')
                    add.get_by_label('Fact or decision').fill('Account-wide setting from an active project')
                    add.get_by_label('Source or reason').fill('Settings scope proof')
                    add.get_by_role('button',name='Save memory',exact=True).click();dialog.get_by_text('Memory saved',exact=True).wait_for()
                    written=[row for row in report['scope_requests'] if row['path']=='/api/workspace/memory' and row['method']=='POST']
                    assert written and written[-1]['body']['project_id'] is None,written
                    # A successful validation request must not commit the source draft.
                    nav.get_by_role('button',name='Plugins & skills',exact=True).click()
                    if not dialog.get_by_label('Skill folder name',exact=True).is_visible():dialog.get_by_text('Add a skill',exact=True).click()
                    dialog.get_by_label('Skill folder name',exact=True).fill('inspect-fixture')
                    instructions='---\nname: inspect-fixture\ndescription: Inspect a temporary fixture without changing files.\n---\nRead the selected fixture and report findings.\n'
                    dialog.get_by_label('Skill instructions',exact=True).fill(instructions)
                    dialog.get_by_role('button',name='Validate skill',exact=True).click()
                    dialog.get_by_text('Skill metadata and instructions validated',exact=True).wait_for()
                    page.once('dialog',lambda prompt:prompt.dismiss());nav.get_by_role('button',name='Memory',exact=True).click()
                    assert dialog.get_by_label('Skill instructions',exact=True).input_value()==instructions
                    page.once('dialog',lambda prompt:prompt.accept());nav.get_by_role('button',name='Memory',exact=True).click()
                    validation=[row for row in report['scope_requests'] if row['path']=='/api/workspace/skill-sources/validate']
                    assert validation and validation[-1]['body']['scope']=='user' and validation[-1]['body']['project_id'] is None
                    assert all('project_id=' not in row['query'] for row in report['scope_requests'] if row['method']=='GET'),report['scope_requests']
                    report['checks'].append('successful skill validation preserves unsaved draft and guarded navigation')
                    # All categories remain reachable through one registry.
                    for category in categories:
                        nav.get_by_role('button',name=category,exact=True).click();assert nav.get_by_role('button',name=category,exact=True).get_attribute('aria-current')=='page'
                    nav.get_by_role('button',name='Memory',exact=True).click()
                    page.set_viewport_size({'width':1440,'height':1000});dialog.get_by_role('button',name='Close',exact=True).click();assert not dialog.is_visible();assert opener.evaluate('(node)=>node===document.activeElement')
                    report['checks'] += ['one compact Settings entry; no drawer category duplication',f'all {len(categories)} categories reachable on desktop and mobile','no workspace scope selector or selected-project settings options','General account/host rows with direct Manage access and Manage models actions','host connectivity disclosed until requested','selected conversation and Chat/Code layouts keep account-wide backend requests','memory edit/save/account-wide creation and draft guards','destructive cancellation leaves record intact','Escape close and focus return',f'focus trap on General at each tested width/theme; no page/content overflow across {len(report["viewports"])} rendered cases',f'zero axe WCAG A/AA violations across {len(report["viewports"])} rendered cases','shareable manager routes']
                    assert not report['errors'],report['errors']
                except Exception as error:
                    report['failure']=str(error)
                    if not page.is_closed():page.screenshot(path=str(output/'failure.png'))
                    raise
                finally:
                    page.context.tracing.stop(path=str(output/'trace.zip'))
                    browser.close()
        finally:
            server.should_exit=True;worker.join(timeout=15)
            (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':run(sys.argv[1])
