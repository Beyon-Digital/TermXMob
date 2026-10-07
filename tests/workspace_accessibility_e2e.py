"""Opt-in axe/rendered keyboard proof against an isolated real workspace host.

Run: python tests/workspace_accessibility_e2e.py OUTPUT_DIR AXE_CORE_4_10_3_JS
No agents/providers are executed. No default configuration store is accessed.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import tempfile
import sys
import threading
from time import monotonic,sleep
import uvicorn
from playwright.sync_api import sync_playwright


def contrast(hex_one,hex_two):
    def light(value):
        value=value.strip().lstrip('#');channels=[int(value[i:i+2],16)/255 for i in (0,2,4)]
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
        (root/'a11y.py').write_text('def greet(name):\n    # Visible syntax contrast fixture\n    return f"Hello {name}"\n')
        state=AppState(passcode=None);owner=state.identity.setup_owner('accessibility-owner','accessibility-fixture-password-123')
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
                browser=playwright.chromium.launch(headless=True)
                for zoom in (100,200):
                    # A fixed physical 1440×900 display at 200% exposes 720×450 CSS pixels.
                    scale=zoom/100
                    context=browser.new_context(viewport={'width':int(1440/scale),'height':int(900/scale)},device_scale_factor=scale,reduced_motion='reduce')
                    page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    page.goto(f'http://127.0.0.1:{port}/?session='+session['id'])
                    report['production_bundle']=page.evaluate('Array.from(document.scripts).map(s=>s.src).find(s=>s.includes("/assets/index-"))?.split("/").pop()')
                    page.get_by_label('Username',exact=True).fill('accessibility-owner');page.get_by_label('Password',exact=True).fill('accessibility-fixture-password-123')
                    page.get_by_role('button',name='Continue with password').click()
                    page.wait_for_function("""()=>{const t=document.querySelector('textarea[aria-label="Message"]'),b=document.querySelector('button[aria-label="Rename or move conversation"]');return !!t&&!t.disabled&&!!b&&!b.disabled}""",timeout=60000)
                    page.get_by_text('Keyboard workspace fixture',exact=True).last.wait_for(state='attached')
                    page.get_by_role('textbox',name='Message',exact=True).wait_for()
                    for theme in ('dark','light'):
                        current=page.locator('html').get_attribute('data-theme') or 'dark'
                        if current!=theme:
                            page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        page.add_script_tag(path=str(axe))
                        assert page.evaluate('axe.version')=='4.10.3'
                        # Include WCAG AA contrast, names, landmarks and ARIA in the rendered DOM.
                        result=page.evaluate("""async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})""")
                        violations=[{'id':v['id'],'impact':v['impact'],'description':v['description'],'nodes':[{'target':n['target'],'summary':n.get('failureSummary')} for n in v['nodes']]} for v in result['violations']]
                        state_report={'theme':theme,'zoom_percent':zoom,'css_viewport':page.viewport_size,'device_scale_factor':scale,'reduced_motion':page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':violations,'incomplete_rules':[{'id':v['id'],'nodes':[{'target':n['target'],'summary':n.get('failureSummary'),'html':n.get('html'),'checks':n.get('any')} for n in v['nodes']]} for v in result['incomplete']]}
                        tokens=page.evaluate("""()=>{const s=getComputedStyle(document.documentElement);return Object.fromEntries(['foreground','secondary','background','surface','muted','accent','accent-soft','destructive'].map(name=>[name,s.getPropertyValue('--'+name).trim()]))}""")
                        state_report['text_contrasts']={f'{fg}/{bg}':contrast(tokens[fg],tokens[bg]) for fg,bg in [('foreground','background'),('secondary','surface'),('accent','accent-soft'),('destructive','muted')]}
                        assert min(state_report['text_contrasts'].values())>=4.5,state_report['text_contrasts']
                        report['states'].append(state_report)
                        assert not page.locator('.error-toast').is_visible(),'A host error is visible in the loaded workspace'
                        page.screenshot(path=str(destination/f'workspace-{theme}-{zoom}.png'))
                        page.keyboard.press('Control+1')
                        # Traverse actual controls using keyboard and verify a visible focus indicator.
                        page.get_by_role('textbox',name='Message',exact=True).focus()
                        page.keyboard.press('Shift+Tab')
                        focus=page.evaluate("""()=>{const e=document.activeElement,s=getComputedStyle(e);return {tag:e.tagName,name:e.getAttribute('aria-label')||e.textContent?.trim(),outline:s.outlineStyle,width:s.outlineWidth}}""")
                        report['keyboard'].append({'theme':theme,'zoom_percent':zoom,'focus':focus})
                        assert focus['tag']!='BODY' and focus['outline']!='none' and focus['width']!='0px',focus
                        page.get_by_role('textbox',name='Message',exact=True).focus();page.keyboard.press('ControlOrMeta+A');page.keyboard.type('Keyboard-only preserved draft')
                        page.keyboard.press('Control+2')
                        page.locator('.file-row').filter(has_text='a11y.py').click()
                        page.locator('.cm-content').get_by_text('def greet(name):',exact=False).wait_for()
                        page.wait_for_timeout(250)
                        workbench=page.evaluate("async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});const editor=document.querySelector('.editor-host .cm-scroller'),box=editor.getBoundingClientRect(),style=getComputedStyle(editor);return {editor_viewport:{width:Math.round(box.width),height:Math.round(box.height),font_size:style.fontSize,line_height:style.lineHeight},violations:r.violations.map(x=>({id:x.id,impact:x.impact,description:x.description,nodes:x.nodes.map(n=>({target:n.target,summary:n.failureSummary}))})),overflow:document.documentElement.scrollWidth>innerWidth}}")
                        # A named editor with no practical space can pass axe. Keep
                        # at least six text lines and a usable horizontal viewport.
                        assert workbench['editor_viewport']['height']>=120,workbench['editor_viewport']
                        assert workbench['editor_viewport']['width']>=240,workbench['editor_viewport']
                        workbench['split_selector_visible']=page.get_by_role('combobox',name='Editor split layout').evaluate("e=>{const b=e.getBoundingClientRect(),p=e.closest('.editor-tabs').getBoundingClientRect();return b.height>=24&&b.top>=p.top&&b.bottom<=p.bottom}")
                        assert workbench['split_selector_visible'],'Editor split selector is clipped by its toolbar'
                        report['states'].append({'theme':theme,'zoom_percent':zoom,'layout':'workbench','reduced_motion':True,**workbench})
                        page.screenshot(path=str(destination/f'workbench-{theme}-{zoom}.png'))
                        page.keyboard.press('Control+1')
                        assert page.get_by_role('textbox',name='Message',exact=True).input_value()=='Keyboard-only preserved draft', {'theme':theme,'zoom':zoom,'actual':page.get_by_role('textbox',name='Message',exact=True).input_value()}
                        page.get_by_role('textbox',name='Message',exact=True).fill('')
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
