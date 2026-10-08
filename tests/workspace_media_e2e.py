"""Opt-in actual artifact UI/version/export proof; no provider requests or billing."""
from __future__ import annotations
import asyncio,json,os,socket,sys,tempfile,threading,zipfile
from pathlib import Path
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright


def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'actual-artifact-ui','paid_provider_queries':0,'errors':[],'artifacts':[],'states':[]}
    with tempfile.TemporaryDirectory(prefix='termx-media-ui-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState,create_app
        state=AppState(passcode=None);owner=state.identity.setup_owner('media-owner','media-proof-password-123')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
        async def serve():await host.serve(sockets=[listener])
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start();deadline=monotonic()+25
        while not host.started:
            if monotonic()>deadline:raise RuntimeError('Fixture host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True,reduced_motion='reduce')
                page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/');page.get_by_label('Username',exact=True).fill('media-owner');page.get_by_label('Password',exact=True).fill('media-proof-password-123');page.get_by_role('button',name='Continue with password').click()
                page.get_by_label('Message',exact=True).wait_for();page.get_by_role('button',name='Media & artifacts',exact=True).click()
                pane=page.get_by_role('region',name='Media and artifacts')
                for kind,format in [('document','docx'),('table','xlsx'),('deck','pptx'),('chart','svg'),('code','txt')]:
                    title='Actual '+kind;summary=pane.locator('summary').filter(has_text='Create an artifact')
                    if not pane.get_by_role('button',name='Create artifact',exact=True).is_visible():summary.click()
                    form=pane.locator('form').first;form.get_by_label('Title',exact=True).fill(title);form.get_by_label('Kind',exact=True).select_option(kind);form.get_by_role('button',name='Create artifact',exact=True).click()
                    pane.get_by_role('heading',name=title,exact=True).wait_for()
                    if kind in ('document','code'):pane.get_by_label('Artifact content',exact=True).fill('Version two exported content')
                    elif kind=='table':
                        pane.get_by_label('Column 1 heading',exact=True).fill('Result');pane.get_by_label('Row 1, column 1',exact=True).fill('42');pane.get_by_role('button',name='Add row',exact=True).click();pane.get_by_label('Row 2, column 1',exact=True).fill('73')
                    elif kind=='deck':
                        pane.get_by_label('Slide 1 title',exact=True).fill('Actual title');pane.get_by_label('Slide 1 body',exact=True).fill('Versioned slide body');pane.get_by_label('Speaker notes',exact=True).fill('Private fixture speaker notes')
                    else:
                        pane.get_by_label('Series 1 label',exact=True).fill('Result');pane.get_by_label('Series 1 value',exact=True).fill('42')
                    pane.get_by_role('button',name='Save new version',exact=True).click();page.wait_for_function("document.querySelector('select[aria-label=\"Inspect version\"]')?.value==='2'")
                    with page.expect_download() as pending:pane.get_by_role('button',name='Export '+format,exact=True).click()
                    export=destination/(kind+'.'+format);pending.value.save_as(export)
                    if format in ('docx','xlsx','pptx'):
                        with zipfile.ZipFile(export) as archive:
                            assert '[Content_Types].xml' in archive.namelist();text=''.join(archive.read(name).decode() for name in archive.namelist() if name.endswith('.xml'))
                            assert ('42' if kind=='table' else 'Actual title' if kind=='deck' else 'Version two exported content') in text
                    else:assert ('Result' if kind=='chart' else 'Version two exported content') in export.read_text()
                    pane.get_by_label('Inspect version',exact=True).select_option('1');page.wait_for_timeout(200)
                    if kind in ('document','code'):assert pane.get_by_label('Artifact content',exact=True).input_value()==''
                    elif kind=='table':assert pane.get_by_label('Row 1, column 1',exact=True).input_value()==''
                    elif kind=='deck':assert pane.get_by_label('Slide 1 title',exact=True).input_value()==''
                    else:assert pane.get_by_label('Series 1 value',exact=True).input_value()=='0'
                    report['artifacts'].append({'kind':kind,'saved_version':2,'original_version_unchanged':True,'export':format,'bytes':export.stat().st_size})
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                for zoom in (100,200):
                    page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom})
                    for theme in ('dark','light'):
                        if (page.locator('html').get_attribute('data-theme') or 'dark')!=theme:
                            page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                        entry={'theme':theme,'zoom_percent':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'nodes':[{'target':n['target'],'summary':n.get('failureSummary')} for n in v['nodes']]} for v in result['violations']]};report['states'].append(entry);page.screenshot(path=str(destination/f'media-{theme}-{zoom}.png'));assert not entry['overflow'] and not entry['violations'],entry
                page.emulate_media(color_scheme='dark');page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name='System',exact=True).check()
                page.wait_for_function("document.documentElement.dataset.theme==='dark' && document.documentElement.dataset.themePreference==='system'")
                appearance=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                assert not appearance['violations'],appearance['violations'];page.screenshot(path=str(destination/'appearance-system-dark-200.png'))
                page.get_by_role('button',name='Close',exact=True).click();page.emulate_media(color_scheme='light');page.wait_for_function("document.documentElement.dataset.theme==='light' && document.documentElement.dataset.themePreference==='system'")
                page.reload();page.get_by_label('Message',exact=True).wait_for();page.wait_for_function("document.documentElement.dataset.theme==='light' && document.documentElement.dataset.themePreference==='system'")
                report['system_appearance']={'live_dark_and_light':True,'preference_survives_reload':True,'dialog_axe_violations':0}
                page.add_script_tag(path=str(axe)) # Reload clears the injected accessibility runtime.
                # Search settings, keyboard configuration and views using the
                # actual loaded palette. Native button focus/Enter is preserved.
                page.get_by_role('button',name='Commands',exact=True).click()
                search=page.get_by_role('searchbox',name='Search commands',exact=True)
                search.fill('settings theme');search.press('ArrowDown');page.keyboard.press('Enter')
                page.get_by_role('dialog',name='Appearance',exact=True).wait_for()
                appearance=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                assert not appearance['violations'],appearance['violations']
                page.get_by_role('button',name='Close',exact=True).click()
                page.get_by_role('button',name='Commands',exact=True).click();search=page.get_by_role('searchbox',name='Search commands',exact=True)
                search.fill('settings shortcuts');search.press('Enter')
                page.get_by_role('dialog',name='Keyboard shortcuts',exact=True).wait_for()
                page.get_by_role('button',name='Close',exact=True).click()
                page.get_by_role('button',name='Commands',exact=True).click();search=page.get_by_role('searchbox',name='Search commands',exact=True)
                search.fill('no such command fixture');page.get_by_role('status').filter(has_text='No matching commands').wait_for();search.press('Enter')
                assert page.get_by_role('dialog',name='Workspace commands',exact=True).is_visible()
                search.fill('view workbench');page.screenshot(path=str(destination/'search-workbench-200.png'));search.press('Enter')
                page.get_by_role('region',name='Code workbench',exact=True).wait_for()
                report['command_search']={'settings_open_by_arrow_and_enter':True,'keyboard_settings_open_by_enter':True,'no_match_does_not_execute':True,'view_open_by_enter':True,'appearance_axe_violations':0}
                assert not report['errors'];browser.close()
        finally:
            host.should_exit=True;worker.join(20);(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':run(sys.argv[1])
