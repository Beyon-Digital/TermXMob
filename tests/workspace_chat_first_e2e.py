"""Rendered chat-first proof against a real isolated host and agent runtime.

Only model inference is replaced by DeterministicAdapter. Password onboarding,
HttpOnly sessions, persistent conversations, approvals, child spawning, control
handoff, cancellation, drafts and workspace surfaces use the actual host/UI.
No external provider, real credentials or native Tauri bridge is exercised.

Build desktop/workspace, then run with the existing test Python environment:
  TERMX_TEST_CHROMIUM=/path/to/chrome python tests/workspace_chat_first_e2e.py OUTPUT
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from time import monotonic, sleep
import traceback

import uvicorn
from playwright.sync_api import expect, sync_playwright
from workspace_ui_snapshot import snapshot_ui
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore


class DeterministicAdapter:
    """An explicit no-network inference fixture; orchestration remains real."""
    def __init__(self):
        self.calls = []
        self.test_requests = 0
        self.turns = {}
        self.release_child = threading.Event()
        self.release_late_child = threading.Event()
        self.hold_started = threading.Event()
        self.project_evidence = []

    async def test(self):
        self.test_requests += 1
        raise AssertionError('A connection test must require a final explicit action')

    async def plan(self, prompt, cwd, manifest):
        if 'Coordinate two synthetic projects' in prompt:
            return {'summary': 'Review two explicitly enrolled project roots', 'steps': ['List authorized projects', 'Delegate separate read-only checks', 'Return both source results'], 'tools': ['list_agent_projects', 'spawn_project_agent', 'await_subagents'], 'risks': []}, 'multi-project-plan'
        return {'summary': prompt, 'steps': ['Delegate the synthetic read-only check', 'Consolidate its result'],
                'tools': ['spawn_subagent'], 'risks': []}, 'fixture-plan'

    async def turn(self, **kwargs):
        prompt = kwargs['prompt']
        self.calls.append(prompt)
        self.turns[prompt] = self.turns.get(prompt, 0) + 1
        count = self.turns[prompt]
        def outputs():
            return [json.loads(item['output']) for item in kwargs.get('input_items') or []
                    if item.get('type') == 'function_call_output' and isinstance(item.get('output'), str)]
        def tool_turn(identifier, calls):
            return ProviderTurn(identifier, '', calls, {}, [{'type': 'function_call',
                'call_id': call.call_id, 'name': call.name, 'arguments': json.dumps(call.arguments)} for call in calls])
        if 'Verify project evidence for ' in prompt:
            slug = 'alpha-project' if 'alpha-project' in prompt else 'beta-project'
            if count == 1:
                return tool_turn(slug + '-read', [ProviderCall(type='function', call_id=slug + '-read-once',
                    name='read_file', arguments={'path': 'sample.txt'})])
            source = next(value for value in reversed(outputs()) if value.get('path') == 'sample.txt')
            assert source.get('ok') is True and isinstance(source.get('content'), str), source
            expected = 'ALPHA_PRIVATE_SYNTHETIC_EVIDENCE' if slug == 'alpha-project' else 'BETA_PRIVATE_SYNTHETIC_EVIDENCE'
            other = 'BETA_PRIVATE_SYNTHETIC_EVIDENCE' if slug == 'alpha-project' else 'ALPHA_PRIVATE_SYNTHETIC_EVIDENCE'
            assert source['content'].strip() == expected and other not in json.dumps(kwargs.get('input_items')), source
            self.project_evidence.append({'cwd': kwargs['cwd'], 'content': source['content'].strip(), 'project': slug})
            return ProviderTurn(slug + '-verified', slug + ': ' + source['content'].strip(), [], {}, [])
        if 'Coordinate two synthetic projects' in prompt:
            if count == 1:
                return tool_turn('multi-list', [ProviderCall(type='function', call_id='multi-project-list-once',
                    name='list_agent_projects', arguments={})])
            if count == 2:
                projects = next(value['projects'] for value in reversed(outputs()) if 'projects' in value)
                targets = [project for project in projects if project['name'] in {'alpha-project', 'beta-project'}]
                assert len(targets) == 2, projects
                return tool_turn('multi-spawn', [ProviderCall(type='function', call_id='multi-spawn-' + project['id'],
                    name='spawn_project_agent', arguments={'project_id': project['id'], 'agent': project['name'] + ' reviewer',
                    'task': 'Verify project evidence for ' + project['name'] + ' by reading sample.txt.', 'mode': 'ask'}) for project in targets])
            if count == 3:
                return tool_turn('multi-await', [ProviderCall(type='function', call_id='multi-project-await-once',
                    name='await_subagents', arguments={})])
            results = next(value['children'] for value in reversed(outputs()) if 'children' in value)
            assert len(results) == 2 and all(child['status'] == 'completed' for child in results.values()), results
            return ProviderTurn('multi-consolidated', 'Two project review results:\n' + '\n'.join(
                sorted(child['result'] for child in results.values())), [], {}, [])
        if 'Hold synthetic coordinator' in prompt:
            self.hold_started.set()
            await asyncio.Event().wait()
        if 'Inspect late synthetic sample' in prompt:
            while not self.release_late_child.is_set():
                await asyncio.sleep(.05)
            return ProviderTurn('late-child-finished', 'Late synthetic result arrived after the main turn finished.', [], {}, [])
        if 'Inspect synthetic sample' in prompt:
            while not self.release_child.is_set():
                await asyncio.sleep(.05)
            return ProviderTurn('child-finished', 'Synthetic sample verified. No external service was contacted.', [], {}, [])
        if 'Direct synthetic follow-up' in prompt:
            return ProviderTurn('direct-finished', 'Direct child instruction received and completed.', [], {}, [])
        if 'Coordinate a later synthetic check' in prompt:
            if count == 1:
                call = ProviderCall(type='function', call_id='late-synthetic-spawn-once', name='spawn_subagent',
                    arguments={'agent': 'Late reviewer', 'task': 'Inspect late synthetic sample', 'mode': 'ask'})
                return ProviderTurn('late-parent-spawn', '', [call], {}, [{'type': 'function_call',
                    'call_id': call.call_id, 'name': call.name, 'arguments': json.dumps(call.arguments)}])
            return ProviderTurn('late-parent-finished', 'The late reviewer is running. Its update will return here.', [], {}, [])
        if 'Coordinate the synthetic check' in prompt:
            if count == 1:
                call = ProviderCall(type='function', call_id='synthetic-spawn-once', name='spawn_subagent',
                    arguments={'agent': 'Sample reviewer', 'task': 'Inspect synthetic sample', 'mode': 'ask'})
            elif count == 2:
                call = ProviderCall(type='function', call_id='synthetic-await-once', name='await_subagents', arguments={})
            else:
                return ProviderTurn('parent-finished', 'The sample reviewer finished. The synthetic check passed.', [], {}, [])
            return ProviderTurn('parent-step-' + str(count), '', [call], {}, [{'type': 'function_call',
                'call_id': call.call_id, 'name': call.name, 'arguments': json.dumps(call.arguments)}])
        return ProviderTurn('fixture-response', 'Synthetic response completed.', [], {}, [])


def run(destination):
    output = Path(destination).resolve()
    frames = output / 'frames'
    frames.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'checks': [], 'screenshots': [], 'axe': [], 'page_errors': [],
              'external_requests': [], 'cancel_requests': [], 'http_errors': [], 'refresh_responses': [],
              'fixture': 'Real HTTP/auth/stores/agent runtime; deterministic no-network inference boundary',
              'live_provider_calls': 0, 'native_tauri_tested': False}
    with tempfile.TemporaryDirectory(prefix='termx-chat-first-') as temporary:
        root = Path(temporary).resolve()
        os.environ.update(TERMX_CONFIG_DIR=str(root / 'config'), TERMX_AGENTS_DIR=str(root / 'agents'),
                          TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState, create_app
        adapter = DeterministicAdapter()
        credential_memory = {}
        state = AppState(passcode=None, credentials=CredentialStore(memory=credential_memory), adapter_factory=lambda *_: adapter)
        source = Path(os.environ.get('TERMX_UI_PROOF_DIST', str(Path(__file__).resolve().parents[1] / 'desktop/workspace/dist')))
        ui = snapshot_ui(root, source)
        report['ui_sha256'] = json.loads((root / 'fixture-ui-snapshot.json').read_text())
        app = create_app(state, web_dir=ui)
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on', access_log=False,
                                              timeout_graceful_shutdown=5))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        deadline = monotonic() + 30
        while not server.started:
            if monotonic() > deadline:
                raise RuntimeError('Synthetic host startup timed out')
            sleep(.05)
        page = context = browser = chrome_process = playwright = None
        def stop_owned_browser():
            if chrome_process is None or chrome_process.poll() is not None:
                return
            # Only the exact process launched below is eligible for cleanup.
            # Never enumerate or kill an existing user's browser/process tree.
            chrome_process.terminate()
            try:
                chrome_process.wait(timeout=5)
                report['browser_cleanup'] = 'owned process terminated'
            except subprocess.TimeoutExpired:
                chrome_process.kill()
                chrome_process.wait(timeout=3)
                report['browser_cleanup'] = 'owned process killed after graceful deadline'
        try:
            playwright = sync_playwright().start()
            executable = os.environ.get('TERMX_TEST_CHROMIUM') or shutil.which('chromium')
            if not executable and sys.platform == 'darwin':
                executable = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            if not executable or not Path(executable).is_file():
                raise RuntimeError('Set TERMX_TEST_CHROMIUM to an installed trusted Chromium executable')
            browser_profile = root / 'test-browser-profile'
            chrome_process = subprocess.Popen([executable, '--headless=new', '--no-first-run',
                '--no-default-browser-check', '--disable-background-networking', '--disable-sync',
                '--disable-component-update', '--disable-extensions', '--disable-default-apps',
                '--password-store=basic', '--use-mock-keychain', '--remote-debugging-port=0',
                '--user-data-dir=' + str(browser_profile), 'about:blank'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['owned_browser_pid'] = chrome_process.pid
            port_file = browser_profile / 'DevToolsActivePort'
            deadline = monotonic() + 30
            while not port_file.is_file():
                if chrome_process.poll() is not None:
                    raise RuntimeError('Owned test browser exited before its debug endpoint was ready')
                if monotonic() > deadline:
                    raise RuntimeError('Owned test browser startup timed out')
                sleep(.05)
            debug_port = port_file.read_text().splitlines()[0]
            browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:' + debug_port, timeout=15000)
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
            def network(route):
                if route.request.url.startswith(base + '/'):
                    route.continue_()
                else:
                    report['external_requests'].append(route.request.url)
                    route.abort()
            context.route('**/*', network)
            page = context.new_page()
            page.set_default_timeout(20000)
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            def response_seen(response):
                path = response.url.removeprefix(base)
                if response.request.method == 'POST' and path.endswith('/cancel'):
                    report['cancel_requests'].append({'path': path, 'status': response.status})
                if path == '/auth/refresh':
                    report['refresh_responses'].append(response.status)
                if response.status >= 400:
                    report['http_errors'].append({'path': path, 'status': response.status})
            page.on('response', response_seen)
            def wait(check, message, timeout=30):
                deadline = monotonic() + timeout
                while not check():
                    if monotonic() > deadline:
                        raise AssertionError(message)
                    page.wait_for_timeout(50)
            def shot(name, target=None):
                target = target or page
                path = frames / (f'{len(report["screenshots"]) + 1:02d}-' + name + '.png')
                target.screenshot(path=str(path))
                report['screenshots'].append(str(path))
                print(json.dumps({'stage': name, 'url': target.url}), flush=True)
            def audit(name):
                if not page.evaluate('typeof axe !== "undefined"'):
                    page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'desktop/workspace/node_modules/axe-core/axe.min.js'))
                result = page.evaluate('''async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target),details:v.nodes.map(n=>n.failureSummary)}))}''')
                geometry = page.evaluate('({page:document.documentElement.scrollWidth>innerWidth,viewport:innerWidth})')
                report['axe'].append({'stage': name, 'violations': result, 'geometry': geometry})
                assert not geometry['page'], geometry
                assert not result, result
            def approve_pending():
                button = page.get_by_role('button', name='Review action', exact=True)
                expect(button.first).to_be_visible(timeout=30000)
                button.first.click()
                dialog = page.get_by_role('dialog', name='Review action', exact=True)
                expect(dialog).to_be_visible()
                shot('03-approval-required')
                dialog.get_by_role('button', name='Allow once', exact=True).click()
                expect(dialog).not_to_be_visible(timeout=30000)

            # Real first-owner password setup; all credentials are synthetic.
            page.goto(base)
            expect(page.get_by_role('heading', name='Set up your workspace', exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_text('invalid CSRF token', exact=True)).not_to_be_visible()
            page.get_by_label('Username', exact=True).fill('chat-first-proof')
            page.get_by_label(re.compile(r'^Password')).fill('synthetic-password-chat-first-2026')
            shot('01-first-owner-setup')
            page.get_by_role('button', name='Create owner account', exact=True).click()
            composer = page.get_by_role('textbox', name='Message', exact=True)
            expect(composer).to_be_enabled(timeout=30000)
            wait(lambda: '/session/' in page.url, 'Root did not open the main conversation')
            primary_id = page.url.rsplit('/', 1)[-1]
            owner = state.identity.resolve(next(cookie['value'] for cookie in context.cookies() if cookie['name'] == 'termx_access')).principal
            assert state.workspace.orchestrator.binding(owner.id)['conversation_id'] == primary_id
            expect(page.get_by_role('button', name='Set up connection', exact=True)).to_be_visible()
            assert not adapter.calls
            shot('02-main-agent-needs-connection')
            audit('main-agent-no-provider')
            report['checks'].append('First owner setup opens one durable main agent without provider calls; connection guidance visible')

            fresh_context = browser.new_context(viewport={'width': 1024, 'height': 768}, reduced_motion='reduce')
            fresh_context.route('**/*', network)
            fresh_page = fresh_context.new_page()
            fresh_page.set_default_timeout(20000)
            fresh_page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            fresh_page.on('response', response_seen)
            fresh_page.goto(base)
            expect(fresh_page.get_by_role('heading', name='Connect to your workspace', exact=True)).to_be_visible(timeout=30000)
            expect(fresh_page.get_by_text('invalid CSRF token', exact=True)).not_to_be_visible()
            assert not report['refresh_responses'], 'A fresh signed-out context attempted an empty refresh mutation'
            shot('02b-configured-password-sign-in', fresh_page)
            fresh_page.get_by_label('Username', exact=True).fill('chat-first-proof')
            fresh_page.get_by_label(re.compile(r'^Password')).fill('synthetic-password-chat-first-2026')
            fresh_page.get_by_role('button', name='Continue with password', exact=True).click()
            expect(fresh_page.get_by_role('textbox', name='Message', exact=True)).to_be_enabled(timeout=30000)
            wait(lambda: fresh_page.url.endswith('/session/' + primary_id), 'Configured password login did not return the same main agent')
            fresh_context.close()
            report['checks'].append('Fresh configured-host password login has no false CSRF error or empty refresh and opens the same durable main agent')

            composer.fill('Preserve this unsent main draft')
            page.get_by_role('button', name='New chat', exact=True).click()
            new_chat = page.get_by_role('dialog', name='New conversation', exact=True)
            expect(new_chat).to_be_visible()
            expect(new_chat.get_by_label('Add a project folder', exact=True)).not_to_be_visible()
            new_chat.get_by_label('Conversation title', exact=True).fill('Focused synthetic chat')
            new_chat.get_by_role('button', name='Create conversation', exact=True).click()
            wait(lambda: '/session/' in page.url and not page.url.endswith(primary_id), 'Focused chat was not opened')
            expect(composer).to_be_enabled()
            expect(composer).to_have_value('')
            composer.fill('An independent synthetic draft')
            page.get_by_role('button', name=re.compile(r'^Main agent')).first.click()
            wait(lambda: page.url.endswith('/session/' + primary_id), 'Main agent did not reopen')
            expect(composer).to_have_value('Preserve this unsent main draft')
            report['checks'].append('Focused chat creation keeps project/agent options optional and preserves separate drafts when switching to main')
            wait(lambda: state.workspace.session(owner, primary_id, turns=False)['draft_text'] == 'Preserve this unsent main draft',
                 'Main draft did not persist to the real host')
            page.reload()
            expect(composer).to_have_value('Preserve this unsent main draft', timeout=30000)
            assert page.url.endswith(primary_id)
            assert len(state.workspace.store.list('orchestrator', owner.id)) == 1
            report['checks'].append('Reload preserves main binding and draft without duplicate primary creation')
            # Exercise the real first-account form and explicit main-chat binding.
            # The key is synthetic and only the no-network adapter can use it.
            expect(page.get_by_role('button', name='Send message', exact=True)).to_be_disabled()
            composer.press('Enter')
            assert not adapter.calls and not state.agent_store.workspace_turns_page(primary_id, limit=1)
            page.get_by_role('button', name='Set up connection', exact=True).click()
            settings = page.get_by_role('dialog', name='Settings', exact=True)
            expect(settings).to_be_visible()
            settings.get_by_text('Add provider account', exact=True).click()
            form = settings.get_by_role('form', name='Add provider account', exact=True)
            form.get_by_label('Provider', exact=True).select_option('custom')
            form.get_by_label('Display name', exact=True).fill('Synthetic no-network provider')
            form.get_by_label('Provider API address', exact=True).fill('http://127.0.0.1:1/v1')
            form.get_by_label('API key', exact=True).fill('fixture-not-a-real-key')
            form.get_by_label('Default model for future sessions', exact=True).fill('fixture')
            shot('03a-first-account-form-accessible')
            audit('first-provider-account-form')
            form.get_by_role('button', name='Save account', exact=True).click()
            expect(settings.get_by_text('Provider account saved', exact=True)).to_be_visible(timeout=30000)
            settings.get_by_role('button', name='Close', exact=True).click()
            expect(settings).not_to_be_visible()
            expect(composer).to_have_value('Preserve this unsent main draft')
            assert state.workspace.session(owner, primary_id, turns=False).get('provider_id') is None
            page.get_by_role('button', name='Use for this chat', exact=True).click()
            wait(lambda: state.workspace.session(owner, primary_id, turns=False).get('model') == 'fixture',
                 'Explicit connection did not bind this main chat')
            expect(page.get_by_role('button', name='Send message', exact=True)).to_be_enabled()
            expect(composer).to_have_value('Preserve this unsent main draft')
            assert page.url.endswith('/session/' + primary_id) and not adapter.calls
            shot('03b-main-first-account-connected-draft-preserved')
            audit('main-first-connection')
            report['checks'].append('Real account form and explicit Use for this chat connect the existing main without inference, retargeting history, changing its ID or losing its draft; unconfigured send creates no failed receipt')
            composer.fill('Coordinate the synthetic check')
            page.get_by_role('button', name='Send message', exact=True).click()
            wait(lambda: bool(state.agent_store.workspace_turns_page(primary_id, limit=1)), 'Main turn was not persisted')
            parent_id = state.agent_store.workspace_turns_page(primary_id, limit=1)[0]['task_id']
            wait(lambda: state.agent_store.get_task(parent_id)['status'] == 'awaiting_approval', 'Plan approval was not required')
            assert not state.agent_store.children(parent_id)
            approve_pending()
            wait(lambda: any(a['status'] == 'pending' and a['kind'] == 'tool' for a in state.agent_store.approvals(parent_id)), 'Delegation tool approval absent')
            assert not state.agent_store.children(parent_id)
            approve_pending()
            wait(lambda: len(state.agent_store.children(parent_id)) == 1, 'Actual runtime did not create a child')
            children = state.agent_store.children(parent_id)
            child_task = children[0]
            snapshot = state.workspace.orchestrator.snapshot(owner)
            assert len(snapshot['children']) == 1
            child_id = snapshot['children'][0]['session_id']
            expect(page.get_by_role('button', name=re.compile('Delegated work'))).to_be_visible()
            disclosure = page.get_by_role('button', name=re.compile('Delegated work'))
            if disclosure.get_attribute('aria-expanded') != 'true':
                disclosure.click()
            child_link = page.get_by_role('region', name='Main agent coordination', exact=True).get_by_role('button', name=re.compile('Sample reviewer'))
            expect(child_link).to_be_visible(timeout=30000)
            shot('04-live-delegated-child')
            child_link.click()
            wait(lambda: page.url.endswith('/session/' + child_id), 'Child conversation did not open')
            expect(page.get_by_text('Managed by your main agent', exact=True)).to_be_visible()
            page.get_by_role('button', name='Take control', exact=True).click()
            expect(page.get_by_text('You are directing this chat', exact=True)).to_be_visible()
            assert state.workspace.orchestrator.relation(child_id)['control_owner'] == 'user'
            shot('05-direct-child-control')
            page.reload()
            expect(page.get_by_text('You are directing this chat', exact=True)).to_be_visible(timeout=30000)
            assert len(state.agent_store.children(parent_id)) == 1
            adapter.release_child.set()
            wait(lambda: state.agent_store.get_task(child_task['id'])['status'] == 'completed', 'Child did not finish')
            wait(lambda: state.agent_store.get_task(parent_id)['status'] == 'completed', 'Main did not consolidate')
            expect(composer).to_be_enabled()
            composer.fill('Direct synthetic follow-up')
            page.get_by_role('button', name='Send message', exact=True).click()
            expect(page.get_by_text('Direct child instruction received and completed.', exact=False)).to_be_visible(timeout=30000)
            assert state.workspace.orchestrator.relation(child_id)['control_owner'] == 'user'
            page.get_by_role('button', name='Return to main agent', exact=True).click()
            wait(lambda: page.url.endswith('/session/' + primary_id), 'Ownership return did not restore main chat')
            assert state.workspace.orchestrator.relation(child_id)['control_owner'] == 'orchestrator'
            expect(page.get_by_text('The sample reviewer finished. The synthetic check passed.', exact=False)).to_be_visible(timeout=30000)
            shot('06-consolidated-result')
            audit('consolidated-main-result')
            report['checks'].append('Actual runtime spawns one linked child after two real approvals; direct child control survives reload, accepts followup, returns ownership and consolidates result')
            activity = state.workspace.orchestrator.snapshot(owner)['activity']
            assert any(row['kind'] == 'task.completed' for row in activity)
            assert len({row['id'] for row in activity}) == len(activity)
            report['checks'].append('Persisted completion feed contains deduplicated real child events')

            # A child finishing after the main turn must report back without
            # waking an unbounded model loop or requiring a chat switch.
            composer.fill('Coordinate a later synthetic check')
            page.get_by_role('button', name='Send message', exact=True).click()
            def late_turn():
                return next((turn for turn in state.agent_store.workspace_turns_page(primary_id, descending=True, limit=20)
                    if turn['prompt'] == 'Coordinate a later synthetic check'
                    and state.agent_store.get_task(turn['task_id']).get('engine') != 'coordination'), None)
            wait(late_turn, 'Late coordinator execution was not stored')
            late_parent_id = late_turn()['task_id']
            wait(lambda: state.agent_store.get_task(late_parent_id)['status'] == 'awaiting_approval', 'Late plan approval absent')
            approve_pending()
            wait(lambda: any(a['status'] == 'pending' and a['kind'] == 'tool' for a in state.agent_store.approvals(late_parent_id)), 'Late delegation approval absent')
            approve_pending()
            wait(lambda: state.agent_store.get_task(late_parent_id)['status'] == 'completed', 'Late coordinator did not finish while child worked')
            late_child = state.agent_store.children(late_parent_id)[0]
            assert state.agent_store.get_task(late_child['id'])['status'] == 'running'
            before_late_finish = len(adapter.calls)
            adapter.release_late_child.set()
            wait(lambda: state.agent_store.get_task(late_child['id'])['status'] == 'completed', 'Late child did not finish')
            expect(page.get_by_text('Late synthetic result arrived after the main turn finished.', exact=False).last).to_be_visible(timeout=30000)
            expect(page.get_by_role('note', name='Delegated agent report', exact=True).last).to_be_visible()
            assert page.url.endswith('/session/' + primary_id)
            assert len(adapter.calls) == before_late_finish
            shot('06b-proactive-late-child-result')
            report['checks'].append('Late child completion returns a source-labeled report in main transcript without another model call or chat switch')

            # Actual task cancellation remains separate from changing chats.
            composer.fill('Hold synthetic coordinator')
            page.get_by_role('button', name='Send message', exact=True).click()
            def held_turn():
                return next((turn for turn in state.agent_store.workspace_turns_page(primary_id, descending=True, limit=20)
                    if turn['prompt'] == 'Hold synthetic coordinator'
                    and state.agent_store.get_task(turn['task_id']).get('engine') != 'coordination'), None)
            wait(held_turn, 'Held main request was not persisted as an execution turn')
            latest = held_turn()['task_id']
            wait(lambda: state.agent_store.get_task(latest)['status'] == 'awaiting_approval', 'Second plan gate absent')
            approve_pending()
            wait(adapter.hold_started.is_set, 'Held task did not start')
            page.get_by_role('button', name='Stop task', exact=True).click()
            wait(lambda: state.agent_store.get_task(latest)['status'] == 'cancelled', 'Stop did not cancel actual task')
            assert len([r for r in report['cancel_requests'] if latest in r['path']]) == 1
            report['checks'].append('Stop sends one cancellation request and stops the actual runtime task')

            composer.fill('Retain main draft while inspecting tools')
            page.get_by_role('button', name='Workspace actions', exact=True).click()
            page.get_by_role('button', name='Browser', exact=True).click()
            expect(composer).to_have_value('Retain main draft while inspecting tools')
            shot('07-contextual-browser-and-draft')
            page.get_by_role('button', name='Close Browser tab', exact=True).click()
            expect(composer).to_have_value('Retain main draft while inspecting tools')
            report['checks'].append('Opening contextual Browser retains the current main conversation draft')
            page.get_by_role('button', name='Settings', exact=True).click()
            settings = page.get_by_role('dialog', name='Settings', exact=True)
            expect(settings).to_be_visible()
            settings.get_by_role('button', name='Models & engines', exact=True).click()
            expect(settings.get_by_role('button', name='Test connection', exact=True)).to_be_visible(timeout=30000)
            settings.get_by_role('button', name='Test connection', exact=True).click()
            test_dialog = page.get_by_role('dialog').last
            expect(test_dialog).to_contain_text('Provider charges may apply.')
            shot('08-provider-test-disclosure')
            page.keyboard.press('Escape')
            assert adapter.test_requests == 0
            page.keyboard.press('Escape')
            report['checks'].append('Provider test requires explicit inference disclosure; review dismissed without executing the fixture adapter test')
            for width, height in [(1440, 900), (1024, 768), (390, 844)]:
                page.set_viewport_size({'width': width, 'height': height})
                for theme in ['dark', 'light']:
                    page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
                    audit('chat-' + theme + '-' + str(width))
                    shot('09-chat-' + theme + '-' + str(width))
            page.set_viewport_size({'width': 390, 'height': 844})
            show = page.get_by_role('button', name='Show sidebar', exact=True)
            show.click()
            expect(page.get_by_role('button', name='Close navigation', exact=True)).to_be_visible()
            page.keyboard.press('Escape')
            expect(show).to_be_focused()
            # Enroll two actual roots through chat UI; main delegates using real
            # authorized IDs and production read_file, never injected child results.
            page.set_viewport_size({'width': 1440, 'height': 900})
            if page.get_by_role('button', name='Show sidebar', exact=True).is_visible():
                page.get_by_role('button', name='Show sidebar', exact=True).click()
            project_roots = {}
            for slug, marker in [('alpha-project', 'ALPHA_PRIVATE_SYNTHETIC_EVIDENCE'),
                                 ('beta-project', 'BETA_PRIVATE_SYNTHETIC_EVIDENCE')]:
                project_root = root / 'synthetic-projects' / slug
                project_root.mkdir(parents=True)
                (project_root / 'sample.txt').write_text(marker + '\n')
                page.get_by_role('button', name='New chat', exact=True).click()
                creation = page.get_by_role('dialog', name='New conversation', exact=True)
                creation.get_by_label('Conversation title', exact=True).fill(slug + ' context')
                creation.get_by_text('Project & agent options', exact=True).click()
                expect(creation.get_by_role('combobox', name='Project', exact=True)).to_have_value('')
                expect(creation.get_by_label('Add a project folder', exact=True)).to_have_value('')
                creation.get_by_label('Add a project folder', exact=True).fill(str(project_root))
                creation.get_by_role('button', name='Create conversation', exact=True).click()
                wait(lambda: '/session/' in page.url and not page.url.endswith(primary_id), 'Project chat did not open')
                enrolled_id = page.url.rsplit('/', 1)[-1]
                enrolled = state.workspace.session(owner, enrolled_id, turns=False)
                assert Path(enrolled['cwd']).resolve() == project_root.resolve() and enrolled['project_id'], {'requested': str(project_root), 'actual': enrolled['cwd'], 'project_id': enrolled['project_id']}
                project_roots[enrolled['project_id']] = str(project_root)
                assert not state.agent_store.workspace_turns_page(enrolled_id, limit=1)
                page.get_by_role('button', name=re.compile(r'^Main agent')).first.click()
                wait(lambda: page.url.endswith('/session/' + primary_id), 'Main did not reopen after project enrollment')
            composer.fill('Coordinate two synthetic projects')
            page.get_by_role('button', name='Send message', exact=True).click()
            def multi_turn():
                return next((turn for turn in state.agent_store.workspace_turns_page(primary_id, descending=True, limit=30)
                    if turn['prompt'] == 'Coordinate two synthetic projects'
                    and state.agent_store.get_task(turn['task_id']).get('engine') != 'coordination'), None)
            wait(multi_turn, 'Multi-project main turn was not persisted')
            multi_parent = multi_turn()['task_id']
            approval_deadline = monotonic() + 90
            approved_ids = set()
            while state.agent_store.get_task(multi_parent)['status'] not in {'completed', 'failed', 'cancelled'}:
                if monotonic() > approval_deadline:
                    raise AssertionError('Multi-project approvals/execution did not finish within the bounded fixture deadline')
                pending = [item for item in state.agent_store.approvals(multi_parent) if item['status'] == 'pending']
                if pending:
                    approved_ids.update(item['id'] for item in pending)
                    approve_pending()
                else:
                    page.wait_for_timeout(100)
            assert state.agent_store.get_task(multi_parent)['status'] == 'completed'
            project_children = state.agent_store.children(multi_parent)
            assert len(project_children) == 2 and len(approved_ids) >= 2
            assert len(adapter.project_evidence) == 2
            for child_task_row in project_children:
                metadata = state.workspace.store.get('task', child_task_row['id'])
                assert metadata['project_id'] in project_roots
                assert child_task_row['cwd'] == project_roots[metadata['project_id']]
                assert child_task_row['status'] == 'completed'
                assert sum(event['type'] == 'tool.finished' and event['payload'].get('call_id') == Path(child_task_row['cwd']).name + '-read-once'
                           for event in state.agent_store.events(child_task_row['id'])) == 1
            report['multi_project_backend'] = {'parent': {key: state.agent_store.get_task(multi_parent).get(key)
                for key in ('id', 'status', 'result', 'error', 'engine', 'updated_at')},
                'children': [{'id': value['id'], 'cwd': value['cwd'], 'status': value['status']} for value in project_children],
                'actual_file_reads': adapter.project_evidence}
            print(json.dumps({'stage': 'multi-project-backend-completed', 'parent_task_id': multi_parent,
                'child_count': len(project_children)}), flush=True)
            expect(page.get_by_text('Two project review results:', exact=False).last).to_be_visible(timeout=30000)
            assert page.url.endswith('/session/' + primary_id)
            assert all(marker in state.agent_store.get_task(multi_parent)['result'] for marker in
                       ['ALPHA_PRIVATE_SYNTHETIC_EVIDENCE', 'BETA_PRIVATE_SYNTHETIC_EVIDENCE'])
            shot('09b-two-projects-consolidated-in-main')
            audit('two-projects-consolidated-main')
            report['multi_project'] = {'parent_task_id': multi_parent, 'child_count': 2,
                'actual_file_reads': adapter.project_evidence, 'explicit_approvals': len(approved_ids),
                'main_route_preserved': True, 'context_roots_distinct': len(set(project_roots.values())) == 2}
            report['checks'].append('UI enrolls two synthetic project roots; real authorized project tools spawn separate read-only children, each reads only its own file once and main consolidates actual results without chat switching')
            composer.fill('Retain main draft while inspecting tools')
            wait(lambda: state.workspace.session(owner, primary_id, turns=False)['draft_text'] == 'Retain main draft while inspecting tools',
                 'Final main draft did not persist before restart')
            assert any(cookie['name'] == 'termx_refresh' for cookie in context.cookies())
            before_refresh_calls = len(adapter.calls)
            report['refresh_responses'].clear()
            context.clear_cookies(name='termx_access')
            page.reload()
            expect(composer).to_have_value('Retain main draft while inspecting tools', timeout=30000)
            assert 200 in report['refresh_responses'], report['refresh_responses']
            assert page.url.endswith('/session/' + primary_id)
            assert len(adapter.calls) == before_refresh_calls
            me = page.request.get(base + '/auth/me')
            assert me.ok and me.json()['principal']['id'] == owner.id
            report['checks'].append('Access-cookie loss recovers through actual refresh while preserving account, main chat and draft without inference')
            wait(lambda: not any(not task.done() for task in list(state.agent._workers.values())),
                 'The fixture still had active execution workers before restart')
            before_restart = state.workspace.orchestrator.snapshot(owner)
            before_restart_ids = [row['id'] for row in before_restart['activity']]
            before_restart_calls = len(adapter.calls)
            before_restart_children = {row['session_id']: (row['status'], row['result'], row['control_owner'])
                                       for row in before_restart['children']}
            old_session_id = state.identity.resolve(next(cookie['value'] for cookie in context.cookies()
                if cookie['name'] == 'termx_access')).session_id
            # Pause all renderer polling before stopping only this proof's
            # host. No existing system app or shared listener is touched.
            page.goto('about:blank')
            server.should_exit = True
            worker.join(timeout=15)
            assert not worker.is_alive(), 'Owned fixture host did not stop within the restart deadline'
            listener.close()
            state = AppState(passcode=None, credentials=CredentialStore(memory=credential_memory), adapter_factory=lambda *_: adapter)
            app = create_app(state, web_dir=ui)
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', int(base.rsplit(':', 1)[1])))
            listener.listen()
            server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on', access_log=False,
                                                  timeout_graceful_shutdown=5))
            worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
            worker.start()
            wait(lambda: server.started, 'Owned fixture host did not restart on its same loopback port')
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.goto(base + '/session/' + primary_id)
            expect(composer).to_have_value('Retain main draft while inspecting tools', timeout=30000)
            after_restart = state.workspace.orchestrator.snapshot(owner)
            assert after_restart['session']['id'] == primary_id
            assert {row['session_id']: (row['status'], row['result'], row['control_owner'])
                    for row in after_restart['children']} == before_restart_children
            assert [row['id'] for row in after_restart['activity']] == before_restart_ids
            assert len(set(before_restart_ids)) == len(before_restart_ids)
            assert state.identity.resolve(next(cookie['value'] for cookie in context.cookies()
                if cookie['name'] == 'termx_access')).session_id == old_session_id
            assert len(adapter.calls) == before_restart_calls
            expect(page.get_by_text('Late synthetic result arrived after the main turn finished.', exact=False).last).to_be_visible()
            shot('10-real-host-restart-restored-main')
            audit('host-restart-restored-main')
            report['restart'] = {'same_loopback_port': True, 'managed_session_preserved': True,
                'primary_preserved': True, 'child_results_and_ownership_preserved': True,
                'activity_ids_preserved': len(before_restart_ids), 'new_inference_calls': 0,
                'draft_preserved': True}
            report['checks'].append('Actual host shutdown/recreation on the same private port preserves auth, primary, child results/ownership, deduplicated activity and draft without replay or inference')
            assert not report['page_errors'], report['page_errors']
            assert not report['external_requests'], report['external_requests']
            report['tasks'] = {'parent': parent_id, 'child_task': child_task['id'], 'child_session': child_id,
                'primary_session': primary_id, 'late_parent': late_parent_id, 'late_child': late_child['id']}
            report['inference_fixture_calls'] = len(adapter.calls)
            report['provider_test_requests'] = adapter.test_requests
            report['checks'].append('Desktop, short and narrow layouts pass axe WCAG checks with no page overflow; Escape returns sidebar toggle focus')
            report['status'] = 'passed'
            # A system Chrome/driver shutdown handshake can stall on this
            # Mac. Stop only our known child with a bounded deadline.
            stop_owned_browser()
        except BaseException as error:
            report['status'] = 'failed'
            report['error'] = str(error)
            report['traceback'] = traceback.format_exc()
            if page and not page.is_closed():
                try:
                    page.screenshot(path=str(frames / 'failure.png'))
                    report['visible_text'] = page.locator('body').inner_text()
                except Exception:
                    pass
            raise
        finally:
            stop_owned_browser()
            if playwright is not None:
                playwright.stop()
            adapter.release_child.set()
            adapter.release_late_child.set()
            server.should_exit = True
            worker.join(timeout=15)
            report['host_stopped'] = not worker.is_alive()
            (output / 'report.json').write_text(json.dumps(report, indent=2))
            print('Report: ' + str(output / 'report.json'), flush=True)
    return report


if __name__ == '__main__':
    run(sys.argv[1])
