"""Opt-in behavioural proof of the default IDE layout against a real scratch FastAPI host.

Run from the repository root after `pnpm --dir desktop/workspace build`:
    python tests/workspace_ide_design_e2e.py OUTPUT_DIR [AXE_CORE_4_10_3_JS]

This replaces the four chat-first design scripts (ChatGPT design, chat-first, minimal UX, agent design). It keeps what is
still true of the product and drops the pixel and old-layout assertions: the empty first-run state, one primary action per
view, the composer (growth, send gating, drafts and attachments kept across views and tabs), keyboard paths (command
center, side bar, dialogs, roving focus, focus-visible), collapsed activity, the streaming caret under reduced motion and
normal motion, an inline error with its recovery, a held approval, capture status, Studio and Data as views with tabs and
the Projects tab, plus responsive widths (1280, 375, 320 and a short 375x420) with no horizontal overflow and axe clean.

No real profile, provider, inference, terminal command, native window or paid call: every conversation is a fixture.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from workspace_ide_design_checks import ANSWER, OWNER, PASSWORD, Case, capture, composer, conversation, empty_state, keyboard_paths, responsive, states, views_and_tabs, wait  # noqa: E402


def run(destination: str, axe_path: str | None) -> None:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    axe_file = Path(axe_path) if axe_path else ROOT / 'desktop/workspace/node_modules/axe-core/axe.min.js'
    axe_source = axe_file.read_text()
    report: dict = {'checks': [], 'errors': [], 'axe': {}, 'external_requests': [], 'mutations': []}
    root = Path(tempfile.mkdtemp(prefix='termx-ide-design-'))
    names = ('TERMX_CONFIG_DIR', 'TERMX_AGENTS_DIR', 'TERMX_ENGINE_STARTUP_REFRESH')
    previous = {name: os.environ.get(name) for name in names}
    server = worker = listener = None
    try:
        os.environ.update(TERMX_CONFIG_DIR=str(root / 'config'), TERMX_AGENTS_DIR=str(root / 'agents'), TERMX_ENGINE_STARTUP_REFRESH='0')
        os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
        from termx.agent.limits import DEFAULT_LIMITS
        from termx.agent.secrets import CredentialStore
        from termx.app import AppState, create_app
        from workspace_ui_snapshot import snapshot_ui

        class NoInference:
            async def test(self, *args, **kwargs):
                raise AssertionError('Unexpected provider test')

            async def plan(self, *args, **kwargs):
                raise AssertionError('Unexpected inference')

            async def turn(self, *args, **kwargs):
                raise AssertionError('Unexpected inference')

        state = AppState(passcode=None, credentials=CredentialStore({}), adapter_factory=lambda *_: NoInference())
        owner = state.identity.setup_owner(OWNER, PASSWORD)
        checkout = root / 'project'
        checkout.mkdir()
        (checkout / 'design-review.txt').write_text('# Synthetic local design QA fixture\nprint("original")\n')
        project = state.projects.register(str(checkout), name='Synthetic design QA project')
        app = create_app(state, web_dir=snapshot_ui(root, ROOT / 'desktop/workspace/dist'))
        state.agent_store.put_provider('offline-design-fixture', kind='openai-compatible', name='Offline design fixture', base_url='http://127.0.0.1:1/v1', model='fixture-review,fixture-plan', capabilities=['chat'], secret_configured=False)
        state.agent.save_provider(provider_id='configured-ui-fixture', kind='openai-compatible', name='Synthetic configured UI provider', base_url='http://127.0.0.1:1/v1', model='fixture-configured', capabilities=['shell'], api_key='synthetic-ui-only-not-a-provider-key')
        sessions = {}
        for label in ('welcome', 'configured', 'conversation', 'error', 'approval', 'streaming'):
            configured = label == 'configured'
            sessions[label] = state.workspace.create_session(owner, title='Design QA fixture · ' + label, project_id=project['id'], cwd=str(checkout), provider_id='configured-ui-fixture' if configured else 'offline-design-fixture', model='fixture-configured' if configured else 'fixture-review', mode='agent')

        def task(label: str, prompt: str, result: str = '', error: str = ''):
            row = state.agent_store.create_task(prompt=prompt, cwd=str(checkout), provider_id='offline-design-fixture', model='fixture-review', limits=DEFAULT_LIMITS, mode='agent', status='completed')
            state.agent_store.update_task(row['id'], result=result, error=error or None)
            state.workspace.store.create('task', owner.id, {'conversation_id': sessions[label]['id'], 'cwd': str(checkout), 'execution_location': str(checkout), 'parent_id': None}, project['id'], row['id'])
            state.authorization.claim_principal(owner, 'task', row['id'], project_id=project['id'])
            state.agent_store.add_conversation_turn(sessions[label]['id'], prompt=prompt, task_id=row['id'], mode='agent', provider_id='offline-design-fixture', model='fixture-review')
            return row

        completed = task('conversation', 'Synthetic QA request: review the workspace layout and preserve my local work.', ANSWER)
        call = {'call_id': 'design-read-fixture', 'name': 'read_file', 'arguments': {'path': 'design-review.txt'}}
        state.agent_store.append_event(completed['id'], 'tool.started', {'call': call})
        state.agent_store.append_event(completed['id'], 'tool.finished', {'call_id': call['call_id'], 'result': {'ok': True, 'output': 'Synthetic fixture: reviewed design-review.txt locally.'}})
        state.agent_store.append_event(completed['id'], 'task.completed', {'result': ANSWER})
        failure = task('error', 'Synthetic QA request: show a recoverable tool error.', '', 'Synthetic offline error: the local review command could not finish. Review the task before retrying.')
        approval_task = task('approval', 'Synthetic QA request: hold a local operation for review.')
        streaming = task('streaming', 'Synthetic QA request: show an in-place response.', 'This is a synthetic streaming fixture. The response remains in the reading column while it is arriving.')
        state.workspace.orchestrator.ensure(owner, session_id=sessions['welcome']['id'])

        async def no_catalog_network(_):
            return {}

        state.agent.catalogue._fetch = no_catalog_network
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on', access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        wait(lambda: server.started, seconds=30)
        state.agent_store.update_task(failure['id'], status='failed')
        state.agent_store.update_task(approval_task['id'], status='awaiting_approval')
        approval = state.agent_store.create_approval(approval_task['id'], 'tool', {'reason': 'Synthetic QA operation: read design-review.txt from the scratch project. No execution will be approved.', 'call': {'call_id': 'qa-hold', 'name': 'read_file', 'arguments': {'path': 'design-review.txt'}}})
        state.agent_store.append_event(approval_task['id'], 'approval.requested', {'approval': approval})
        state.agent_store.update_task(streaming['id'], status='running')
        state.agent_store.append_event(streaming['id'], 'assistant.delta', {'text': 'This is a synthetic streaming fixture. The response remains in the reading column while it is arriving.'})

        origin = urlsplit(base)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for theme in ('light', 'dark'):
                for width, height in ((1280, 900), (375, 812)):
                    context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=theme, reduced_motion='reduce', has_touch=width < 640)
                    context.add_init_script('localStorage.setItem("termx-shell-theme", ' + json.dumps(theme) + ')')

                    def network(route):
                        request = route.request
                        url = urlsplit(request.url)
                        if url.scheme in ('http', 'https') and (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
                            report['external_requests'].append(request.url)
                            route.abort('blockedbyclient')
                            return
                        if request.method != 'GET' and re.match(r'/api/(?:agent/tasks|workspace/tasks|media/studio/jobs|desktop/recording)(?:/|$)', url.path):
                            report['mutations'].append({'method': request.method, 'path': url.path})
                            route.abort('blockedbyclient')
                            return
                        route.continue_()

                    context.route('**/*', network)
                    page = context.new_page()
                    page.set_default_timeout(20000)
                    page.on('pageerror', lambda error: report['errors'].append(str(error)))
                    case = Case(page, base, report, state, owner, sessions, project, axe_source, theme, width, height, out)
                    try:
                        for step in (empty_state, composer, keyboard_paths, conversation, states, capture, views_and_tabs, responsive):
                            step(case)
                    except BaseException:
                        page.screenshot(path=str(out / 'failure.png'), full_page=True)
                        raise
                    finally:
                        context.close()
            browser.close()
        assert not report['errors'], report['errors']
        assert not report['external_requests'], report['external_requests']
        assert not report['mutations'], report['mutations']
        assert (checkout / 'design-review.txt').read_text() == '# Synthetic local design QA fixture\nprint("original")\n', 'the fixture file was written'
        assert len(state.agent_store.list_tasks()) == 4, 'Unexpected task dispatch'
    finally:
        if server:
            server.should_exit = True
        if worker:
            worker.join(timeout=15)
        if listener:
            listener.close()
        shutil.rmtree(root, ignore_errors=True)
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (out / 'workspace_ide_design_e2e.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'checks': report['checks'], 'errors': 0, 'axe_violations': 0}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
