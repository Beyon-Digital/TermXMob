"""Capture slice evidence screenshots of the built workspace UI against an isolated host.

Run from the repository root after `pnpm --dir desktop/workspace build`:
    uv run --no-sync python docs/evidence/ide-shell/capture_screens.py OUTPUT_DIR

Viewports 375 x 812 and 1280 x 900, light and dark (set by the system colour scheme, which also
exercises the System default). No agents or providers run and no default configuration store is
touched: everything lives in a temporary directory.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tests'))

VIEWPORTS = {'mobile': (375, 812), 'desktop': (1280, 900)}


def main(destination: str) -> None:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    report = {'states': [], 'errors': [], 'overflow': []}
    with tempfile.TemporaryDirectory(prefix='termx-evidence-') as scratch:
        root = Path(scratch)
        os.environ['TERMX_CONFIG_DIR'] = str(root / 'config')
        os.environ['TERMX_AGENTS_DIR'] = str(root / 'agents')
        os.environ['TERMX_ENGINE_STARTUP_REFRESH'] = '0'
        from termx.app import AppState, create_app
        from termx.agent.secrets import CredentialStore
        (root / 'main.py').write_text('def greet(name):\n    # Evidence fixture\n    return f"Hello {name}"\n')
        state = AppState(passcode=None, credentials=CredentialStore(memory={}))
        owner = state.identity.setup_owner('evidence-owner', 'evidence-fixture-password-123')
        project = state.projects.register(str(root), name='Evidence fixture')
        from workspace_ui_snapshot import snapshot_ui
        app = create_app(state, web_dir=snapshot_ui(root, ROOT / 'desktop/workspace/dist'))
        session = state.workspace.create_session(owner, title='Evidence conversation', project_id=project['id'], cwd=str(root))
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
        threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True).start()
        deadline = monotonic() + 15
        while not server.started:
            if monotonic() > deadline:
                raise RuntimeError('Host did not start')
            sleep(.05)
        base = f'http://127.0.0.1:{port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for scheme in ('light', 'dark'):
                for name, (width, height) in VIEWPORTS.items():
                    context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme, reduced_motion='reduce')
                    page = context.new_page()
                    page.on('pageerror', lambda error: report['errors'].append(str(error)))

                    def shot(label: str) -> None:
                        page.wait_for_timeout(250)
                        page.screenshot(path=str(out / f'{label}-{scheme}-{width}.png'))
                        info = page.evaluate("""()=>{const r=document.documentElement,s=getComputedStyle(r);return {theme:r.dataset.theme,preference:r.dataset.themePreference,dark:r.classList.contains('dark'),
                          overflow:r.scrollWidth>r.clientWidth+1,bg:s.getPropertyValue('--bg').trim(),font:getComputedStyle(document.body).fontFamily.slice(0,40)}}""")
                        report['states'].append({'label': label, 'scheme': scheme, 'viewport': [width, height], **info})
                        if info['overflow']:
                            report['overflow'].append(f'{label}-{scheme}-{width}')

                    page.goto(f'{base}/?session=' + session['id'])
                    page.get_by_label('Username', exact=True).wait_for()
                    shot('sign-in')
                    page.get_by_label('Username', exact=True).fill('evidence-owner')
                    page.get_by_label('Password', exact=True).fill('evidence-fixture-password-123')
                    page.get_by_role('button', name='Continue with password').click()
                    page.wait_for_function("""()=>{const t=document.querySelector('textarea[aria-label="Message"]');return !!t}""", timeout=60000)
                    shot('chat')
                    # Keyboard focus ring on the composer and the next control.
                    page.get_by_role('textbox', name='Message', exact=True).focus()
                    page.keyboard.press('Shift+Tab')
                    shot('focus-visible')
                    page.keyboard.press('Control+2')
                    page.wait_for_timeout(300)
                    shot('workbench')
                    # Settings: a dense form dialog over the workspace.
                    page.goto(f'{base}/?session=' + session['id'])
                    page.get_by_role('button', name='Settings', exact=True).click()
                    page.get_by_role('dialog', name='Settings', exact=True).wait_for()
                    shot('settings')
                    page.goto(f'{base}/?workspace=studio')
                    page.wait_for_timeout(600)
                    shot('studio')
                    page.goto(f'{base}/?workspace=data')
                    page.wait_for_timeout(600)
                    shot('data')
                    context.close()
            browser.close()
        server.should_exit = True
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'states': len(report['states']), 'errors': report['errors'], 'overflow': report['overflow']}))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/evidence/ide-shell/u0')
