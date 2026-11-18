"""Opt-in axe/rendered keyboard proof of the default IDE layout against an isolated real workspace host.

Run: python tests/workspace_accessibility_e2e.py OUTPUT_DIR AXE_CORE_4_10_3_JS
No agents/providers are executed. No default configuration store is accessed.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import socket
import tempfile
import sys
import threading
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import expect, sync_playwright


def contrast(hex_one,hex_two):
    def light(value):
        value=value.strip().lstrip('#')
        if len(value)==3:value=''.join(channel*2 for channel in value)
        channels=[int(value[i:i+2],16)/255 for i in (0,2,4)]
        linear=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in channels]
        return sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))
    first,second=sorted((light(hex_one),light(hex_two)),reverse=True)
    return round((first+.05)/(second+.05),2)


def run(destination, axe_source):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    axe=Path(axe_source)
    if not axe.is_file():raise ValueError('Pass the pinned axe-core 4.10.3 axe.min.js path')
    report={'tool':'axe-core/4.10.3','states':[],'keyboard':[],'errors':[]}
    # Qualify the same installed test interpreter's language-server adapter,
    # not whichever unrelated or absent executable the user's shell selects.
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    report['fixture_adapter_bin']=str(Path(sys.executable).parent)
    with tempfile.TemporaryDirectory(prefix='termx-a11y-proof-') as scratch:
        root=Path(scratch);os.environ['TERMX_CONFIG_DIR']=str(root/'config');os.environ['TERMX_AGENTS_DIR']=str(root/'agents');os.environ['TERMX_ENGINE_STARTUP_REFRESH']='0'
        from termx.app import AppState,create_app
        from termx.agent.secrets import CredentialStore
        (root/'a11y.py').write_text('def greet(name):\n    # Visible syntax contrast fixture\n    return f"Hello {name}"\n')
        state=AppState(passcode=None,credentials=CredentialStore(memory={}));owner=state.identity.setup_owner('accessibility-owner','accessibility-fixture-password-123')
        project=state.projects.register(str(root),name='Accessibility fixture')
        from workspace_ui_snapshot import snapshot_ui
        app=create_app(state,web_dir=snapshot_ui(root,Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        session=state.workspace.create_session(owner,title='Keyboard workspace fixture',project_id=project['id'],cwd=str(root))
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
        worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+15
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Accessibility host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True, executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
                for zoom in (100,200):
                    # A fixed physical 1440×900 display at 200% exposes 720×450 CSS pixels.
                    scale=zoom/100
                    context=browser.new_context(viewport={'width':int(1440/scale),'height':int(900/scale)},device_scale_factor=scale,reduced_motion='reduce')
                    page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    page.goto(f'http://127.0.0.1:{port}/?session='+session['id'])
                    report['production_bundle']=page.evaluate('Array.from(document.scripts).map(s=>s.src).find(s=>s.includes("/assets/index-"))?.split("/").pop()')
                    page.get_by_label('Username',exact=True).fill('accessibility-owner');page.get_by_label('Password',exact=True).fill('accessibility-fixture-password-123')
                    page.get_by_role('button',name='Continue with password').click()
                    # 200% zoom leaves 720 CSS pixels: under the 768px breakpoint, so the layout is the phone projection
                    # (one tab stack, a drawer and a bottom navigation) instead of the tab groups.
                    phone=page.viewport_size['width']<768
                    top=page.get_by_role('banner',name='Window')
                    nav=page.get_by_role('navigation',name='Primary')
                    try:
                        # The IDE layout is the default: the deep link opens the conversation in the Chat tab.
                        if phone:top.wait_for(timeout=60000)
                        else:page.get_by_role('tablist',name='Editor group 1 tabs').wait_for(timeout=60000)
                        page.wait_for_function("""()=>{const t=document.querySelector('textarea[aria-label="Message"]'),b=document.querySelector('button[aria-label="Session options"]');return !!t&&!t.disabled&&!!b&&!b.disabled}""",timeout=60000)
                    except Exception:
                        page.screenshot(path=str(destination / f"startup-failure-{zoom}.png"))
                        (destination / f"startup-failure-{zoom}.html").write_text(page.content(), encoding="utf-8")
                        raise
                    assert page.get_by_role('region',name='conversation panel').count()==0,'the classic dock is not the default'
                    if phone:expect(top.get_by_role('heading')).to_contain_text('Keyboard workspace fixture')
                    else:page.get_by_role('tab',name='Keyboard workspace fixture').wait_for()
                    page.get_by_role('textbox',name='Message',exact=True).wait_for()
                    for theme in ('dark','light'):
                        current=page.locator('html').get_attribute('data-theme') or 'light'
                        if current!=theme and phone:
                            nav.get_by_role('button',name='More').click()
                            page.get_by_role('menuitem',name='Settings',exact=True).click()
                            expect(top.get_by_role('heading')).to_contain_text('Settings')
                            expect(page.get_by_role('heading',name='General',exact=True)).to_be_visible()
                            page.get_by_role('combobox',name='Theme preference',exact=True).select_option(theme)
                            expect(page.locator('html')).to_have_attribute('data-theme',theme)
                            page.get_by_role('button',name=re.compile('Switch tab')).click()
                            page.get_by_role('menuitem',name=re.compile('Keyboard workspace fixture')).click()
                            expect(top.get_by_role('heading')).to_contain_text('Keyboard workspace fixture')
                        elif current!=theme:
                            page.get_by_role('toolbar',name='Activity bar').get_by_role('button',name='Settings').click()
                            settings_tab=page.get_by_role('tab',name='Settings',exact=True)
                            expect(settings_tab).to_have_attribute('aria-selected','true')
                            panel=page.locator('#'+settings_tab.get_attribute('aria-controls'))
                            expect(panel.get_by_role('heading',name='General',exact=True)).to_be_visible()
                            panel.get_by_role('combobox',name='Theme preference',exact=True).select_option(theme)
                            expect(page.locator('html')).to_have_attribute('data-theme',theme)
                            settings_tab.locator('.tab-close').click()
                            expect(page.get_by_role('tab',name='Settings',exact=True)).to_have_count(0)
                        page.add_script_tag(path=str(axe))
                        assert page.evaluate('axe.version')=='4.10.3'
                        # Include WCAG AA contrast, names, landmarks and ARIA in the rendered DOM.
                        result=page.evaluate("""async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})""")
                        violations=[{'id':v['id'],'impact':v['impact'],'description':v['description'],'nodes':[{'target':n['target'],'summary':n.get('failureSummary')} for n in v['nodes']]} for v in result['violations']]
                        state_report={'theme':theme,'zoom_percent':zoom,'css_viewport':page.viewport_size,'device_scale_factor':scale,'reduced_motion':page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':violations,'incomplete_rules':[{'id':v['id'],'nodes':[{'target':n['target'],'summary':n.get('failureSummary'),'html':n.get('html'),'checks':n.get('any')} for n in v['nodes']]} for v in result['incomplete']]}
                        tokens=page.evaluate("""()=>{const s=getComputedStyle(document.documentElement);return Object.fromEntries(['text','text-2','text-3','bg','surface','surface-2','accent','accent-fg','danger'].map(name=>[name,s.getPropertyValue('--'+name).trim()]))}""")
                        state_report['text_contrasts']={f'{fg}/{bg}':contrast(tokens[fg],tokens[bg]) for fg,bg in [('text','bg'),('text-2','surface'),('text-3','bg'),('accent-fg','accent'),('danger','surface-2')]}
                        assert min(state_report['text_contrasts'].values())>=4.5,state_report['text_contrasts']
                        report['states'].append(state_report)
                        assert not page.locator('.error-toast').is_visible(),'A host error is visible in the loaded workspace'
                        page.screenshot(path=str(destination/f'workspace-{theme}-{zoom}.png'))
                        # Traverse actual controls using keyboard and verify a visible focus indicator.
                        page.get_by_role('textbox',name='Message',exact=True).focus()
                        page.keyboard.press('Shift+Tab')
                        focus=page.evaluate("""()=>{const e=document.activeElement,s=getComputedStyle(e);return {tag:e.tagName,name:e.getAttribute('aria-label')||e.textContent?.trim(),outline:s.outlineStyle,width:s.outlineWidth}}""")
                        report['keyboard'].append({'theme':theme,'zoom_percent':zoom,'focus':focus})
                        assert focus['tag']!='BODY' and focus['outline']!='none' and focus['width']!='0px',focus
                        page.get_by_role('textbox',name='Message',exact=True).focus();page.keyboard.press('ControlOrMeta+A');page.keyboard.type('Keyboard-only preserved draft')
                        # Open the file from the Explorer into its own tab (the Chat tab stays mounted behind it).
                        if phone:
                            nav.get_by_role('button',name='Files').click()
                            page.get_by_role('dialog',name='Explorer').get_by_role('treeitem',name=re.compile('a11y.py')).click()
                            expect(top.get_by_role('heading')).to_contain_text('a11y.py')
                        else:
                            # Choosing the shown view again hides the side bar, so open Explorer only when it is not already showing.
                            if not page.get_by_role('tree',name='Files').is_visible():page.get_by_role('toolbar',name='Activity bar').get_by_role('button',name='Explorer').click()
                            page.get_by_role('tree',name='Files').get_by_role('treeitem',name=re.compile('a11y.py')).click()
                            file_tab=page.get_by_role('tab',name=re.compile('a11y.py'))
                            expect(file_tab).to_have_attribute('aria-selected','true')
                        page.locator('.cm-content').get_by_text('def greet(name):',exact=False).wait_for()
                        page.wait_for_timeout(250)
                        workbench=page.evaluate("async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});const editor=document.querySelector('.file-tab-host .cm-scroller'),box=editor.getBoundingClientRect(),style=getComputedStyle(editor);return {editor_viewport:{width:Math.round(box.width),height:Math.round(box.height),font_size:style.fontSize,line_height:style.lineHeight},violations:r.violations.map(x=>({id:x.id,impact:x.impact,description:x.description,nodes:x.nodes.map(n=>({target:n.target,summary:n.failureSummary}))})),overflow:document.documentElement.scrollWidth>innerWidth}}")
                        # A named editor with no practical space can pass axe. Keep
                        # at least six text lines and a usable horizontal viewport.
                        assert workbench['editor_viewport']['height']>=120,workbench['editor_viewport']
                        assert workbench['editor_viewport']['width']>=240,workbench['editor_viewport']
                        editor_actions=page.get_by_role('button',name='Editor actions',exact=True)
                        workbench['editor_actions_visible']=editor_actions.evaluate("e=>{const b=e.getBoundingClientRect(),p=e.closest('.file-tab-bar').getBoundingClientRect();return b.height>=24&&b.left>=p.left&&b.right<=p.right&&b.top>=p.top&&b.bottom<=p.bottom}")
                        assert workbench['editor_actions_visible'],('Editor actions trigger is clipped by its toolbar',editor_actions.evaluate("e=>{const r=x=>{const b=x.getBoundingClientRect();return [b.left,b.top,b.right,b.bottom].map(Math.round)};return {button:r(e),bar:r(e.closest('.file-tab-bar')),zoom:innerWidth}}"))
                        # Language actions are revealed through the keyboard-accessible menu, which stays inside the viewport.
                        editor_actions.focus();page.keyboard.press('Enter')
                        editor_menu=page.get_by_role('menu',name='Editor actions',exact=True)
                        expect(editor_menu).to_be_visible()
                        workbench['menu_visible']=editor_menu.evaluate("e=>{const b=e.getBoundingClientRect();return b.left>=0&&b.top>=0&&b.right<=innerWidth&&b.bottom<=innerHeight}")
                        assert workbench['menu_visible'],('Editor actions menu is clipped by the viewport',editor_menu.bounding_box())
                        page.keyboard.press('Escape')
                        expect(editor_menu).to_be_hidden();expect(editor_actions).to_be_focused()
                        report['states'].append({'theme':theme,'zoom_percent':zoom,'layout':'editor tab','reduced_motion':True,**workbench})
                        page.screenshot(path=str(destination/f'workbench-{theme}-{zoom}.png'))
                        # Back to the conversation: the draft was kept while the editor was in front.
                        if phone:
                            page.get_by_role('button',name=re.compile('Switch tab')).click()
                            page.get_by_role('menuitem',name=re.compile('Keyboard workspace fixture')).click()
                        else:page.get_by_role('tab',name='Keyboard workspace fixture').click()
                        assert page.get_by_role('textbox',name='Message',exact=True).input_value()=='Keyboard-only preserved draft', {'theme':theme,'zoom':zoom,'actual':page.get_by_role('textbox',name='Message',exact=True).input_value()}
                        page.get_by_role('textbox',name='Message',exact=True).fill('')
                        if not phone:file_tab.locator('.tab-close').click()
                    context.close()
                browser.close()
        finally:
            server.should_exit=True;worker.join(timeout=15);listener.close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
    assert not report['errors'],report['errors']
    assert all(not state['overflow'] and state['reduced_motion'] for state in report['states']),report['states']
    failures=[{'theme':state['theme'],'zoom':state['zoom_percent'],'violations':state['violations']} for state in report['states'] if state['violations']]
    assert not failures,json.dumps(failures,indent=2)
    return report

if __name__=='__main__':
    import sys
    print(json.dumps(run(sys.argv[1],sys.argv[2]),indent=2))
