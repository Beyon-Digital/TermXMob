"""Real desktop UI -> real host -> qualified Docker runner, with a fixture provider.

Build desktop/workspace and termx-runner:workspace first. No paid provider calls.
Run: TERMX_CHROMIUM_EXECUTABLE=/usr/bin/chromium .venv/bin/python
     tests/workspace_runner_e2e.py /tmp/termx-runner-browser-proof
"""
import io
import json
import os
from pathlib import Path
import socket
import tarfile
import tempfile
import threading
from time import monotonic, sleep

import uvicorn
from playwright.sync_api import sync_playwright, expect


def run(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = {'steps': [], 'errors': [], 'provider': 'local deterministic fixture; no billing'}
    with tempfile.TemporaryDirectory(prefix='termx-runner-browser-') as temporary:
        root = Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'), TERMX_AGENTS_DIR=str(root/'agents'), TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState, create_app
        from termx.agent.secrets import CredentialStore
        from termx.agent.providers import ProviderTurn
        state = AppState(passcode=None, credentials=CredentialStore(memory={'fixture': 'private-browser-fixture'}))
        owner = state.identity.setup_owner('runner-fixture', 'runner-fixture-password-123')
        folder = root/'project'
        folder.mkdir()
        project = state.projects.register(str(folder), 'Runner browser fixture')
        state.agent_store.put_provider('fixture', kind='openai-compatible', name='Fixture API', base_url='https://fixture.invalid', model='fixture-model', capabilities=['shell'], secret_configured=True)
        class Provider:
            async def turn(self, **kwargs):
                assert kwargs['cwd'] == '/workspace'
                return ProviderTurn('fixture-turn', 'Verified reply from the isolated runner.', [], {})
        state.agent._adapter = lambda _: Provider()
        # Exercise real viewer sockets without requesting physical screen access.
        from termx.desktop import session as display_port
        from termx.desktop.capture import CaptureError
        display_port.permission_snapshot = lambda: {'screen_recording':'granted', 'accessibility':'granted'}
        display_port.list_displays = lambda: []
        def no_physical_frame(*args): raise CaptureError('Fixture display; lifecycle transport only')
        display_port.grab_jpeg = no_physical_frame
        app = create_app(state, web_dir=Path(__file__).resolve().parents[1]/'desktop/workspace/dist')
        session = state.workspace.create_session(owner, title='Runner attachment fixture', project_id=project['id'], cwd=str(folder), engine='internal', provider_id='fixture', model='fixture-model', mode='ask')
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        deadline = monotonic()+20
        while not server.started:
            if monotonic() > deadline: raise RuntimeError('Fixture host did not start')
            sleep(.05)
        runner = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('TERMX_CHROMIUM_EXECUTABLE'), headless=True)
                context = browser.new_context(viewport={'width':1440, 'height':1100}, accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(30000)
                page.on('pageerror', lambda error: report['errors'].append(str(error)))
                page.goto(origin)
                page.get_by_label('Username', exact=True).fill('runner-fixture')
                page.get_by_label('Password', exact=True).fill('runner-fixture-password-123')
                page.get_by_role('button', name='Continue with password').click()
                page.get_by_role('navigation', name='Control plane navigation').wait_for()
                page.get_by_role('button', name='Open workspace', exact=True).click()
                page.get_by_role('navigation', name='Workspace layout').get_by_role('button', name='Computer', exact=True).click()
                page.get_by_role('button', name='Watch this machine').click()
                captures = "async()=> (await (await fetch('/api/desktop/recording/status')).json()).captures.filter(row=>row.kind==='computer').length"
                page.wait_for_function('async()=> (await ('+captures+')()) > 0')
                page.get_by_role('button', name='Control plane', exact=True).click()
                page.wait_for_function('async()=> (await ('+captures+')()) === 0')
                page.get_by_role('button', name='Open workspace', exact=True).click()
                page.get_by_role('button', name='Watch this machine').click()
                page.wait_for_function('async()=> (await ('+captures+')()) > 0')
                page.evaluate("window.dispatchEvent(new Event('blur'))")
                page.wait_for_function('async()=> (await ('+captures+')()) === 0')
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.get_by_role('button', name='Watch this machine')).to_be_enabled()
                page.get_by_role('button', name='Control plane', exact=True).click()
                if page.get_by_role('button', name='Dismiss', exact=True).count():
                    page.get_by_role('button', name='Dismiss', exact=True).click()
                report['steps'].append('Real desktop viewer sockets disappear from host capture status when the panel hides and window blurs; no automatic restart')
                page.get_by_role('button', name='Runners', exact=True).click()
                page.get_by_role('button', name='Enroll runner', exact=True).click()
                page.get_by_role('combobox', name='Runner project').select_option(project['id'])
                page.get_by_label('Container image', exact=True).fill(os.environ.get('TERMX_RUNNER_IMAGE', 'termx-runner:workspace'))
                page.get_by_role('button', name='Create scoped runner').click()
                page.get_by_role('button', name='View activity', exact=True).wait_for(timeout=60000)
                expect(page.locator('.runner-record .status-badge')).to_have_text('ready', timeout=60000)
                runner = state.runners.list(owner.id)[0]
                assert runner['project'] == project['id'] and runner['status'] == 'ready'
                report['steps'].append('Rendered global enrollment selects the project and provisions a real isolated Docker container')
                archive = root/'input.tar'
                with tarfile.open(archive, 'w') as tar:
                    raw = b'actual browser upload'
                    item = tarfile.TarInfo('input.txt'); item.size = len(raw)
                    tar.addfile(item, io.BytesIO(raw))
                page.get_by_label('Copy workspace tar archive').set_input_files(str(archive))
                page.get_by_role('button', name='Run on this runner').wait_for()
                expect(page.get_by_role('button', name='Run on this runner')).to_be_enabled()
                page.get_by_label('Command arguments').fill(json.dumps(['sh', '-c', 'cat input.txt > result.txt; printf runner-browser-ok']))
                page.get_by_role('button', name='Run on this runner').click()
                expect(page.get_by_label('Runner output')).to_contain_text('runner-browser-ok', timeout=30000)
                page.get_by_label('Result file path').fill('result.txt')
                with page.expect_download() as downloaded:
                    page.get_by_role('button', name='Download results', exact=True).click()
                result = root/'result.tar'; downloaded.value.save_as(result)
                with tarfile.open(result) as tar:
                    assert tar.extractfile('result.txt').read() == b'actual browser upload'
                report['steps'].append('Actual file input uploads a tar; rendered job submits; downloaded artifact matches source bytes')
                page.get_by_role('button', name='Runner attachment fixture', exact=True).click()
                page.get_by_role('button', name='Session options').click()
                target = page.get_by_role('combobox', name='Session execution location')
                expect(target.locator(f'option[value="{runner["id"]}"]')).to_have_count(1)
                target.select_option(runner['id'])
                expect(target).to_have_value(runner['id'])
                page.keyboard.press('Escape')
                page.get_by_role('textbox', name='Message', exact=True).fill('Verify the attached isolated execution target.')
                page.get_by_role('button', name='Send message', exact=True).click()
                page.get_by_text('Verified reply from the isolated runner.', exact=True).wait_for(timeout=60000)
                canonical = state.workspace.session(owner, session['id'])
                assert canonical['runner_id'] == runner['id']
                assert canonical['turns'][-1]['task']['status'] == 'completed'
                report['steps'].append('Actual session-options runner selector attaches the container and sends a canonical agent turn through the host broker')
                page.reload()
                page.get_by_role('button', name='Session options').click()
                target = page.get_by_role('combobox', name='Session execution location')
                expect(target).to_have_value(runner['id'])
                target.select_option('')
                expect(target).to_have_value('')
                page.keyboard.press('Escape')
                page.get_by_role('button', name='Runners', exact=True).click()
                page.get_by_role('button', name='Tear down', exact=True).click()
                expect(page.locator('.runner-record .status-badge')).to_have_text('removed', timeout=30000)
                expect(page.get_by_role('button', name='Tear down', exact=True)).to_be_disabled()
                assert state.runners.row(owner.id, runner['id'])['status'] == 'removed'
                assert not state.workspace.session(owner, session['id'])['runner_id']
                report['steps'].append('Reload preserves attachment; explicit detach restores host execution; rendered teardown removes the container')
                page.screenshot(path=str(destination/'runner-teardown.png'))
                assert not report['errors'], report['errors']
                browser.close()
        finally:
            if runner:
                # The normal path tears down in the UI; failures still remove this fixture's container.
                import subprocess
                subprocess.run(['docker', 'rm', '-f', 'termx-runner-'+runner['id']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            server.should_exit = True
            worker.join(timeout=20)
            listener.close()
            (destination/'report.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    import sys
    print(json.dumps(run(sys.argv[1]), indent=2))
