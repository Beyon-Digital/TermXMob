"""Exercise the bundled updater page with a deterministic native bridge.

The real native state transitions are covered by updater::tests in Rust CI.
This browser proof covers presentation, IPC actions, reopen, and accessibility.
"""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright, expect


def run(destination, axe_path):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(SimpleHTTPRequestHandler, directory=str(root / 'desktop/bootstrap')))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    report = []
    bridge = """(() => {
      window.calls = [];
      window.snapshot = {phase:'checking', currentVersion:'0.2.7', version:null, downloaded:0, total:null, notes:null, error:null, windows:false};
      window.__TAURI_INTERNALS__ = {invoke: async (command, args) => {
        if (command === 'desktop_update_state') return {...window.snapshot};
        if (command !== 'desktop_update_action') throw Error('Unexpected native command');
        window.calls.push(args.action);
        if (window.rejectAction) throw Error('Native update action failed');
        const phase = {check:'checking',download:'downloading',install:'installing',restart:'restarting'}[args.action];
        window.snapshot.phase = phase;
      }};
    })()"""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for theme in ('dark', 'light'):
                context = browser.new_context(viewport={'width': 560, 'height': 640}, color_scheme=theme, reduced_motion='reduce')
                context.add_init_script(bridge)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/updater.html')
                expect(page.get_by_role('heading', name='Checking for updates…')).to_be_visible()

                def state(phase, **values):
                    page.evaluate('s => Object.assign(window.snapshot, s)', {'phase': phase, **values})
                    page.wait_for_timeout(600)

                for phase in ('checking', 'current', 'available', 'downloading', 'verifying', 'downloaded', 'installing', 'ready', 'restarting', 'error'):
                    state(phase, version='0.2.8', downloaded=1048576, total=4194304, notes='<img src=x onerror=alert(1)>\nRelease improvements', error='Network unavailable' if phase == 'error' else None)
                    if phase == 'downloading':
                        expect(page.locator('#bytes')).to_contain_text('25%')
                        assert page.locator('progress').get_attribute('value') == '1048576'
                    expect(page.locator('#notes')).to_contain_text('<img')
                    assert page.locator('#notes img').count() == 0
                    page.evaluate(Path(axe_path).read_text())
                    violations = page.evaluate("async () => (await axe.run(document, {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})).violations")
                    assert not violations, (theme, phase, violations)
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                    report.append({'theme': theme, 'phase': phase, 'violations': violations})
                    page.screenshot(path=str(destination / f'{theme}-{phase}.png'))

                state('available', error=None)
                page.get_by_role('button', name='Download update').click()
                expect(page.get_by_role('heading', name='Downloading update…')).to_be_visible()
                assert page.evaluate('window.calls') == ['download']
                state('downloading', total=None)
                assert page.locator('progress').get_attribute('value') is None
                expect(page.locator('#bytes')).to_contain_text('total size not provided')
                # Reopening rehydrates the native snapshot instead of repeating the action.
                saved = page.evaluate('window.snapshot')
                page.reload()
                page.evaluate('s => {window.snapshot=s; window.calls=[]}', saved)
                expect(page.get_by_role('heading', name='Downloading update…')).to_be_visible()
                assert page.evaluate('window.calls') == []
                state('downloaded', windows=True)
                expect(page.locator('#description')).to_contain_text('close Termx')
                page.get_by_role('button', name='Install update', exact=True).click()
                expect(page.get_by_role('heading', name='Installing update…')).to_be_visible()
                state('ready', windows=False)
                page.get_by_role('button', name='Restart Termx', exact=True).click()
                expect(page.get_by_role('heading', name='Restarting Termx…')).to_be_visible()
                assert page.evaluate('window.calls') == ['install', 'restart']
                state('error', error='Signature verification failed')
                expect(page.get_by_role('alert')).to_contain_text('Signature verification failed')
                page.get_by_role('button', name='Try again').click()
                assert page.evaluate('window.calls').pop() == 'check'
                state('current', error=None)
                page.evaluate('window.rejectAction=true')
                page.get_by_role('button', name='Check again').click()
                expect(page.get_by_role('alert')).to_contain_text('Native update action failed')
                page.wait_for_timeout(600)
                expect(page.get_by_role('alert')).to_contain_text('Native update action failed')
                page.set_viewport_size({'width': 320, 'height': 420})
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                page.get_by_role('button').focus()
                page.keyboard.press('Shift+Tab')
                page.keyboard.press('Tab')
                expect(page.get_by_role('button')).to_be_focused()
                assert page.get_by_role('button').evaluate("e => getComputedStyle(e).outlineStyle") != 'none'
                assert not errors, errors
                context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        (destination / 'report.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    import sys
    print(json.dumps(run(sys.argv[1], sys.argv[2]), indent=2))
