"""Opt-in rendered workspace proof against an isolated real host and real ledger.

Run with managed Playwright Chromium; writes report/screenshots to an explicit
temporary directory. Fixtures never reach the product's default data store.
"""
from __future__ import annotations
import asyncio
import json
import os
import socket
import tempfile
import threading
from pathlib import Path
from time import monotonic,time,sleep
import uvicorn
from playwright.sync_api import sync_playwright

def run(destination):
    with tempfile.TemporaryDirectory(prefix='termx-ui-proof-') as temporary:
        root=Path(temporary);os.environ['TERMX_CONFIG_DIR']=str(root/'config');os.environ['TERMX_AGENTS_DIR']=str(root/'agents');os.environ['TERMX_ENGINE_STARTUP_REFRESH']='0'
        from termx.app import AppState,create_app
        state=AppState(passcode=None);owner=state.identity.setup_owner('fixture-owner','ui-fixture-password-123')
        project=state.projects.register(str(root),name='Workspace fixture')
        app=create_app(state,web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        session=state.workspace.create_session(owner,title='Large conversation fixture',project_id=project['id'],cwd=str(root))
        for index in range(499):state.workspace.create_session(owner,title=f'Conversation fixture {index:03d}',project_id=project['id'],cwd=str(root))
        with state.agent_store._lock:
            state.agent_store._db.executemany('INSERT INTO conversation_turns VALUES(?,?,?,?,?,?,?,?,?)',
                [(f'fixture-turn-{index}',session['id'],index+1,None,f'Fixture message {index}: inspect the workspace state.','ask',None,None,time()) for index in range(10000)])
            state.agent_store._db.commit()
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
        worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);worker.start()
        deadline=monotonic()+15
        while not server.started:
            if monotonic()>deadline:raise RuntimeError('Proof host did not start')
            sleep(.05)
        destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
        report={'fixture':{'sessions':500,'turns':10000},'viewports':[],'errors':[]}
        page=None
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce')
                page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                start=monotonic();page.goto(f'http://127.0.0.1:{port}/?session='+session['id'])
                page.get_by_label('Username',exact=True).fill('fixture-owner');page.get_by_label('Password',exact=True).fill('ui-fixture-password-123')
                page.get_by_role('button',name='Continue with password').click()
                page.get_by_text('Large conversation fixture',exact=True).first.wait_for(timeout=60000)
                composer=page.get_by_role('textbox',name='Message',exact=True)
                composer.wait_for(state='visible',timeout=60000)
                page.wait_for_function('(()=>{const input=document.querySelector("textarea[aria-label=Message]");return input && !input.disabled})()',timeout=60000)
                page.get_by_text('Fixture message 9999: inspect the workspace state.',exact=True).wait_for(timeout=60000)
                page.get_by_text('Conversation fixture 498',exact=True).wait_for(timeout=60000)
                report['authenticated_load_ms']=round((monotonic()-start)*1000)
                report['dom_nodes']=page.locator('*').count()
                for width in (1280,1440,1920,2560,768,390):
                    page.set_viewport_size({'width':width,'height':900});page.wait_for_timeout(150)
                    overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth')
                    report['viewports'].append({'width':width,'page_overflow':overflow})
                    assert not overflow,f'Page overflow at {width}'
                    page.screenshot(path=str(destination/f'workspace-{width}.png'))
                page.set_viewport_size({'width':1440,'height':900})
                begin=monotonic();page.keyboard.press('Control+2');page.wait_for_timeout(250)
                report['layout_switch_ms']=round((monotonic()-begin)*1000)
                page.keyboard.press('Control+1');page.wait_for_timeout(150)
                composer.fill('Preserve this unsent draft')
                page.keyboard.press('Control+3');page.wait_for_timeout(150);page.keyboard.press('Control+1')
                assert page.get_by_role('textbox',name='Message',exact=False).first.input_value()=='Preserve this unsent draft'
                # A second authenticated view must not create a prompt or mutation.
                other=context.new_page();other.goto(f'http://127.0.0.1:{port}/?session='+session['id']);other.get_by_role('textbox',name='Message',exact=True).wait_for(state='visible',timeout=60000);other.wait_for_function('(()=>{const input=document.querySelector("textarea[aria-label=Message]");return input && !input.disabled})()',timeout=60000)
                assert len(state.agent_store.workspace_turns_page(session['id'],after_sequence=9999,limit=201))==1
                report['multiwindow_no_run']=True
                report['memory']=page.evaluate('performance.memory?{usedJSHeapSize:performance.memory.usedJSHeapSize,totalJSHeapSize:performance.memory.totalJSHeapSize}:null')
                context.close();browser.close()
        except Exception:
            if page and not page.is_closed():
                (destination/'failure.html').write_text(page.content())
                page.screenshot(path=str(destination/'failure.png'))
            raise
        finally:
            server.should_exit=True;worker.join(timeout=15);listener.close()
            (destination/'report.json').write_text(json.dumps(report,indent=2))
        return report

if __name__=='__main__':
    import sys
    print(json.dumps(run(sys.argv[1]),indent=2))
