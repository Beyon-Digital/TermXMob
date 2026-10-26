"""Opt-in rendered native/ACP coordination proof with explicit fake boundaries.

The host, Internal Agent tool loop, Gateway, ledgers, approvals and UI are real.
Main inference is deterministic. `fixture-acp` is an in-process EngineAdapter
fixture, not a vendor process or a claim of ACP wire-protocol coverage.
Run only after a serialized grant and a production desktop/workspace build:
  PYTHONPATH=src TERMX_TEST_CHROMIUM=/path/to/chrome python \
    tests/workspace_native_orchestrator_e2e.py OUTPUT_DIRECTORY
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
from time import monotonic, sleep, time
import traceback
from uuid import uuid4

import uvicorn
from playwright.sync_api import expect, sync_playwright
from workspace_ui_snapshot import snapshot_ui
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.engines.types import EngineCapabilities, EngineDescriptor, EngineEvent, EngineSessionBinding


ENGINE = 'fixture-acp'
IDLE_TITLE = 'Existing idle ACP chat'
COORDINATE = 'NATIVE_COORDINATE_CASE'
WORK = 'NATIVE_WORK_CASE'
DIRECT = 'NATIVE_DIRECT_CASE'
HOLD = 'NATIVE_CANCEL_CASE'


class NativeFixture:
    """Normalized adapter boundary only; never starts a vendor/native process."""
    def __init__(self, gateway):
        self.gateway = gateway
        self.sent = []
        self.decisions = []
        self.bindings = {}
        self.workers = {}
        self.dispatches = {}
        self.pending = {}
        self.release_result = threading.Event()
        self.progress = threading.Event()

    def descriptor(self):
        return EngineDescriptor(ENGINE, 'ACP fixture', installed=True, auth_state='not_required',
            transport='in-process-fixture', protocol={'fixture': 'normalized engine boundary'})

    async def probe(self):
        return self.descriptor()

    def capabilities(self):
        return EngineCapabilities(tools_filter='unsupported', approvals='gateway', streaming=True,
            resume='unsupported', steer='unsupported', models=['native-model'])

    async def create_session(self, cfg):
        binding = EngineSessionBinding.new(ENGINE, 'fixture-session-' + uuid4().hex[:8],
                                            cwd=cfg.cwd, status='idle')
        self.bindings[binding.binding_id] = binding
        return binding

    async def configure_session(self, binding, cfg):
        return None

    async def attach(self, binding, cfg=None):
        self.bindings[binding.binding_id] = binding
        return binding

    async def send(self, binding, prompt, attachments=None):
        # Capture the host's attempt at send entry. This fixture has no wire
        # turn ID; its local operation receipt must never masquerade as one.
        dispatch_id = binding.extensions_snapshot['termx_dispatch_id']
        assert isinstance(dispatch_id, str) and dispatch_id
        operation_id = 'fixture-operation-' + uuid4().hex[:8]
        self.sent.append({'binding_id': binding.binding_id, 'native_session_id': binding.native_session_id,
                          'prompt': prompt, 'fixture_operation_id': operation_id, 'host_dispatch_id': dispatch_id})
        binding.status = 'active'
        task = asyncio.create_task(self.drive(binding, prompt, operation_id, dispatch_id))
        self.workers[binding.binding_id] = task
        self.dispatches[binding.binding_id] = dispatch_id
        return None

    async def drive(self, binding, prompt, operation_id, dispatch_id):
        try:
            await asyncio.sleep(.01)
            if binding.extensions_snapshot.get('termx_dispatch_id') != dispatch_id:
                return
            if WORK in prompt:
                token = 'native-approval-' + operation_id
                decision = asyncio.Event()
                self.pending[token] = decision
                await self.gateway.approval_sink(binding.binding_id, token,
                    'item/commandExecution/requestApproval',
                    {'command': 'Read the synthetic fixture only', 'cwd': binding.cwd}, 'tool')
                await decision.wait()
                self.gateway.on_engine_event(binding.binding_id,
                    EngineEvent('engine.message.delta', {'delta': 'Native fixture progress is visible.\n'},
                                native={'dispatch_id': dispatch_id}))
                self.progress.set()
                while not self.release_result.is_set():
                    await asyncio.sleep(.025)
                text = 'Native fixture result returned through the actual gateway.'
            elif DIRECT in prompt:
                text = 'Direct native fixture instruction completed in the same session.'
            elif HOLD in prompt:
                self.gateway.on_engine_event(binding.binding_id,
                    EngineEvent('engine.message.delta', {'delta': 'Native fixture is waiting for an explicit Stop.'},
                                native={'dispatch_id': dispatch_id}))
                await asyncio.Event().wait()
            else:
                raise AssertionError('Unexpected native fixture instruction')
            self.gateway.on_engine_event(binding.binding_id,
                EngineEvent('engine.message.delta', {'delta': text}, native={'dispatch_id': dispatch_id}))
            if binding.extensions_snapshot.get('termx_dispatch_id') == dispatch_id and binding.status not in {'closed', 'lost'}:
                binding.status = 'idle'
            self.gateway.on_engine_event(binding.binding_id,
                EngineEvent('engine.turn.completed', {'status': 'completed'}, native={'dispatch_id': dispatch_id}))
        except asyncio.CancelledError:
            return
        finally:
            # A retired callback cannot clear the newer send's worker slot.
            if (binding.extensions_snapshot.get('termx_dispatch_id') == dispatch_id
                    and self.workers.get(binding.binding_id) is asyncio.current_task()):
                self.workers.pop(binding.binding_id, None)
                self.dispatches.pop(binding.binding_id, None)

    async def respond_approval(self, binding, request_id, decision, remember=False, content=None):
        self.decisions.append({'binding_id': binding.binding_id, 'request_id': request_id, 'decision': decision})
        pending = self.pending.pop(request_id)
        if decision != 'approve':
            await self.cancel(binding)
        else:
            pending.set()

    async def cancel(self, binding):
        worker = self.workers.get(binding.binding_id)
        dispatch_id = self.dispatches.get(binding.binding_id)
        assert dispatch_id is not None
        if worker and not worker.done():
            worker.cancel()
        if binding.extensions_snapshot.get('termx_dispatch_id') == dispatch_id and binding.status not in {'closed', 'lost'}:
            binding.status = 'idle'
        self.gateway.on_engine_event(binding.binding_id,
            EngineEvent('engine.turn.completed', {'status': 'interrupted'}, native={'dispatch_id': dispatch_id}))

    async def steer(self, binding, text):
        return False

    async def close(self, binding):
        worker = self.workers.get(binding.binding_id)
        if worker and not worker.done():
            worker.cancel()
        binding.status = 'closed'

    async def list_sessions(self):
        return [binding.as_dict() for binding in self.bindings.values()]

    async def shutdown(self):
        for worker in self.workers.values():
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)


class MainFixture:
    def __init__(self):
        self.turns = 0

    async def test(self):
        raise AssertionError('No provider connection test is part of this proof')

    async def plan(self, prompt, cwd, manifest):
        return {'summary': 'Coordinate the explicit synthetic engine task and reviewed existing chat.',
                'steps': ['Inspect configured engines', 'Delegate bounded work', 'Review and enroll the existing idle chat'],
                'tools': ['list_agent_engines', 'spawn_engine_agent', 'review_managed_session', 'adopt_managed_session'],
                'risks': []}, 'native-main-plan'

    async def turn(self, **kwargs):
        self.turns += 1
        outputs = {}
        for item in kwargs.get('input_items') or []:
            if item.get('type') == 'function_call_output':
                outputs[item['call_id']] = json.loads(item['output'])
        if 'inspect-engines' not in outputs or 'inspect-sessions' not in outputs:
            calls = [call('inspect-engines', 'list_agent_engines'), call('inspect-sessions', 'list_agent_sessions')]
        elif 'delegate-native' not in outputs:
            assert any(e['id'] == ENGINE for e in outputs['inspect-engines']['engines'])
            calls = [call('delegate-native', 'spawn_engine_agent', engine=ENGINE, agent='Native reviewer',
                          task=WORK, model='native-model', mode='agent')]
        elif 'review-idle' not in outputs:
            idle = next(s for s in outputs['inspect-sessions']['sessions'] if s['title'] == IDLE_TITLE)
            calls = [call('review-idle', 'review_managed_session', session_id=idle['id'])]
        elif 'adopt-idle' not in outputs:
            review = outputs['review-idle']
            calls = [call('adopt-idle', 'adopt_managed_session', session_id=review['session_id'],
                          expected_revision=review['revision'], review_digest=review['review_digest'])]
        else:
            return ProviderTurn('native-main-finished', 'Native work is delegated and the reviewed idle chat is enrolled.', [], {}, [])
        return ProviderTurn('native-main-' + str(self.turns), '', calls, {}, [
            {'type': 'function_call', 'call_id': c.call_id, 'name': c.name, 'arguments': json.dumps(c.arguments)} for c in calls])


def call(identifier, name, **arguments):
    return ProviderCall(type='function', call_id=identifier, name=name, arguments=arguments)


def run(destination):
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=False)
    frames = output / 'frames'
    frames.mkdir()
    report = {'status': 'running', 'checks': [], 'axe': [], 'screenshots': [], 'page_errors': [],
        'external_requests': [], 'cancel_requests': [],
        'fixture_boundary': 'in-process normalized EngineAdapter; no ACP wire/vendor process',
        'real_components': ['AppState', 'Internal Agent tool registry', 'EngineGateway', 'HTTP/GraphQL',
                            'managed authentication', 'persistent ledgers', 'production React UI'],
        'live_provider_calls': 0, 'native_vendor_processes': 0}
    started = monotonic()
    with tempfile.TemporaryDirectory(prefix='termx-native-proof-') as temporary:
        root = Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root / 'config'), TERMX_AGENTS_DIR=str(root / 'agents'),
                          TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState, create_app
        main = MainFixture()
        state = AppState(passcode=None, credentials=CredentialStore(memory={}), adapter_factory=lambda *_: main)
        owner = state.identity.setup_owner('native-proof', 'synthetic-native-password-2026')
        state.agent.save_provider(provider_id='fixture', kind='openai-compatible', name='No-network main fixture',
            base_url='http://127.0.0.1:1/v1', model='fixture', capabilities=['shell', 'functions'],
            api_key='synthetic-fixture-only')
        native = NativeFixture(state.engines)
        state.engines.register(native)
        descriptor = native.descriptor().as_dict()
        descriptor['capabilities'] = native.capabilities().as_dict()
        state.engines.catalogue.entries[ENGINE] = {'descriptor': descriptor,
            'configuration': {'models': ['native-model'], 'default_model': 'native-model'},
            'refreshed_at': time(), 'stale': False, 'refresh_error': None}
        source = Path(os.environ.get('TERMX_UI_PROOF_DIST', str(Path(__file__).resolve().parents[1] / 'desktop/workspace/dist')))
        app = create_app(state, web_dir=snapshot_ui(root, source))
        # WorkspaceService is mounted by create_app, not AppState.__init__.
        idle = state.workspace.create_session(owner, title=IDLE_TITLE, engine=ENGINE, model='native-model', mode='agent')
        report['ui_sha256'] = json.loads((root / 'fixture-ui-snapshot.json').read_text())
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        host = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on', access_log=False, timeout_graceful_shutdown=5))
        worker = threading.Thread(target=lambda: host.run(sockets=[listener]), daemon=True)
        worker.start()
        page = browser = chrome = playwright = None
        deadline = monotonic() + 180

        def stop_owned_browser():
            if chrome is not None and chrome.poll() is None:
                chrome.terminate()
                try:
                    chrome.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    chrome.kill()
                    chrome.wait(timeout=3)

        def wait(predicate, message, seconds=25):
            end = min(deadline, monotonic() + seconds)
            while True:
                value = predicate()
                if value:
                    return value
                if monotonic() >= end:
                    raise AssertionError(message)
                if page:
                    page.wait_for_timeout(40)
                else:
                    sleep(.04)

        def shot(label):
            path = frames / (f'{len(report["screenshots"]) + 1:02d}-' + label + '.png')
            page.screenshot(path=str(path))
            report['screenshots'].append(str(path))
            print(json.dumps({'stage': label, 'url': page.url}), flush=True)

        def audit(label):
            if not page.evaluate('typeof axe !== "undefined"'):
                page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'desktop/workspace/node_modules/axe-core/axe.min.js'))
            result = page.evaluate('''async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target)}))}''')
            overflow = page.evaluate('document.documentElement.scrollWidth>innerWidth')
            report['axe'].append({'stage': label, 'violations': result, 'page_overflow': overflow})
            assert not result and not overflow, report['axe'][-1]

        def approve_visible(label, source=None):
            trigger = (source or page).get_by_role('button', name='Review action', exact=True)
            expect(trigger.first).to_be_visible(timeout=25000)
            trigger.first.click()
            dialog = page.get_by_role('dialog', name='Review action', exact=True)
            expect(dialog).to_be_visible()
            shot(label)
            dialog.get_by_role('button', name='Allow once', exact=True).click()
            expect(dialog).not_to_be_visible(timeout=25000)

        def snapshot():
            return state.workspace.orchestrator.snapshot(owner)

        try:
            wait(lambda: host.started, 'Fixture host did not start')
            playwright = sync_playwright().start()
            executable = os.environ.get('TERMX_TEST_CHROMIUM') or shutil.which('chromium')
            if not executable and sys.platform == 'darwin':
                executable = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            if not executable or not Path(executable).is_file():
                raise RuntimeError('Set TERMX_TEST_CHROMIUM to an existing trusted Chromium executable')
            profile = root / 'browser-profile'
            chrome = subprocess.Popen([executable, '--headless=new', '--no-first-run', '--no-default-browser-check',
                '--disable-background-networking', '--disable-sync', '--disable-component-update', '--disable-extensions',
                '--password-store=basic', '--use-mock-keychain', '--remote-debugging-port=0',
                '--user-data-dir=' + str(profile), 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['owned_browser_pid'] = chrome.pid
            wait(lambda: (profile / 'DevToolsActivePort').is_file(), 'Owned browser did not start')
            port = (profile / 'DevToolsActivePort').read_text().splitlines()[0]
            browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:' + port, timeout=15000)
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
                if response.request.method == 'POST' and response.url.startswith(base + '/') and response.url.endswith('/cancel'):
                    report['cancel_requests'].append({'path': response.url.removeprefix(base), 'status': response.status})
            page.on('response', response_seen)
            page.goto(base)
            page.get_by_label('Username', exact=True).fill('native-proof')
            page.get_by_label(re.compile(r'^Password')).fill('synthetic-native-password-2026')
            page.get_by_role('button', name='Continue with password', exact=True).click()
            composer = page.get_by_role('textbox', name='Message', exact=True)
            expect(composer).to_be_enabled(timeout=30000)
            wait(lambda: snapshot()['session'] is not None, 'Main agent not established')
            primary_id = snapshot()['session']['id']
            composer.fill(COORDINATE)
            page.get_by_role('button', name='Send message', exact=True).click()
            approve_visible('main-plan-approval')
            approve_visible('native-delegation-approval')
            child = wait(lambda: next((c for c in snapshot()['children'] if c['title'] == 'Native reviewer'), None),
                         'Native child did not enroll')
            task_id, child_id = child['task_id'], child['session_id']
            wait(lambda: state.agent_store.approvals(task_id), 'Native permission request did not reach the gateway')
            assert len(native.sent) == 1 and child['engine'] == ENGINE
            assert page.url.endswith('/session/' + primary_id)
            # The real source request is actionable from Main without opening
            # the child, expanding its history, or transferring ownership.
            source = page.get_by_role('article', name='Requests from Native reviewer', exact=True)
            expect(source).to_be_visible(timeout=25000)
            expect(source).to_contain_text('Main agent is coordinating')
            approve_visible('actual-native-source-approval-in-main', source)
            wait(native.progress.is_set, 'Native adapter did not receive its exact approval decision')
            assert len(native.decisions) == 1 and native.decisions[0]['decision'] == 'approve'
            assert page.url.endswith('/session/' + primary_id)
            assert next(c for c in snapshot()['children'] if c['session_id'] == child_id)['control_owner'] == 'orchestrator'
            shot('native-approval-resolved-in-main-without-takeover')
            audit('native-source-decision-in-main')
            disclosure = page.get_by_role('button', name=re.compile('Delegated work'))
            if disclosure.get_attribute('aria-expanded') != 'true':
                disclosure.click()
            region = page.get_by_role('region', name='Main agent coordination', exact=True)
            region.get_by_role('button', name=re.compile('Native reviewer')).click()
            expect(page.get_by_text('Managed by your main agent', exact=True)).to_be_visible()
            page.get_by_role('button', name='Take control', exact=True).click()
            expect(page.get_by_text('You are directing this chat', exact=True)).to_be_visible()
            expect(page.get_by_text('Native fixture progress is visible.', exact=False)).to_be_visible()
            details = page.locator('details[aria-label="Engine details for Native reviewer"]')
            details.locator('summary').click()
            expect(details).to_contain_text('Not measured or capped by TermX')
            expect(details).to_contain_text('Unavailable')
            shot('native-progress-and-honest-capabilities')
            audit('native-progress-capabilities')
            original_native_id = native.sent[0]['native_session_id']
            native.release_result.set()
            wait(lambda: state.agent_store.get_task(task_id)['status'] == 'completed', 'Native result did not settle')
            expect(page.get_by_text('Native fixture result returned through the actual gateway.', exact=False)).to_be_visible()
            composer.fill(DIRECT)
            page.get_by_role('button', name='Send message', exact=True).click()
            expect(page.get_by_text('Direct native fixture instruction completed in the same session.', exact=False)).to_be_visible()
            assert len(native.sent) == 2 and native.sent[1]['native_session_id'] == original_native_id
            page.get_by_role('button', name='Return to main agent', exact=True).click()
            wait(lambda: page.url.endswith('/session/' + primary_id), 'Return to main chat failed')
            approve_visible('reviewed-existing-chat-adoption')
            wait(lambda: any(c['session_id'] == idle['id'] for c in snapshot()['children']), 'Reviewed idle conversation not adopted')
            adopted = next(c for c in snapshot()['children'] if c['session_id'] == idle['id'])
            assert adopted['origin'] == 'adopted' and adopted['task_id'] is None
            assert adopted['status'] == 'idle' and adopted['control_owner'] == 'user'
            assert len(native.sent) == 2
            region = page.get_by_role('region', name='Main agent coordination', exact=True)
            disclosure = region.get_by_role('button', name=re.compile('Delegated work'))
            if disclosure.get_attribute('aria-expanded') != 'true':
                disclosure.click()
            expect(region.get_by_role('button', name=re.compile(IDLE_TITLE))).to_contain_text('No task started')
            region.get_by_role('button', name=re.compile(IDLE_TITLE)).click()
            expect(page.get_by_role('button', name='Give main agent control', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='Stop task', exact=True)).not_to_be_visible()
            details = page.locator('details[aria-label="Engine details for ' + IDLE_TITLE + '"]')
            details.locator('summary').click()
            expect(details).to_contain_text('Host monitoring; existing task limits are retained')
            shot('adopted-idle-chat-user-owned')
            audit('adopted-idle-user-owned')
            page.set_viewport_size({'width': 390, 'height': 844})
            shot('adopted-idle-narrow-capabilities')
            audit('adopted-idle-narrow')
            page.get_by_role('button', name='Give main agent control', exact=True).click()
            wait(lambda: page.url.endswith('/session/' + primary_id), 'Idle ownership handoff did not return main')
            assert not state.workspace.active(idle['id']) and len(native.sent) == 2
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.goto(base + '/session/' + child_id)
            expect(composer).to_be_enabled(timeout=25000)
            page.get_by_role('button', name='Take control', exact=True).click()
            composer.fill(HOLD)
            page.get_by_role('button', name='Send message', exact=True).click()
            wait(lambda: len(native.sent) == 3, 'Direct native hold did not dispatch once')
            latest = state.agent_store.workspace_turns_page(child_id, descending=True, limit=1)[0]['task_id']
            wait(lambda: state.agent_store.get_task(latest)['status'] == 'running', 'Native held task not running')
            expect(page.get_by_text('Native fixture is waiting for an explicit Stop.', exact=False)).to_be_visible()
            page.get_by_role('button', name='Stop task', exact=True).click()
            wait(lambda: state.agent_store.get_task(latest)['status'] == 'cancelled', 'Native Stop did not route to adapter')
            stopped = state.agent_store.get_task(latest)
            assert stopped['result'] == 'Native fixture is waiting for an explicit Stop.'
            assert stopped['error'] == 'Interrupted'
            page.reload()
            expect(composer).to_be_enabled(timeout=25000)
            expect(page.get_by_text('You are directing this chat', exact=True)).to_be_visible()
            assert len(native.sent) == 3
            cancellation = page.locator('.response-cancellation').last
            expect(cancellation).to_have_attribute('role', 'status')
            expect(cancellation).to_contain_text('Task stopped')
            expect(cancellation).to_contain_text(stopped['error'])
            stopped_row = cancellation.locator('xpath=ancestor::*[@data-message-id][1]')
            expect(stopped_row).to_contain_text(stopped['result'])
            expect(stopped_row.locator('.response-error')).to_have_count(0)
            expect(stopped_row.get_by_role('alert')).to_have_count(0)
            expect(stopped_row).not_to_contain_text('The task stopped with an error')
            report['cancellation'] = {'task_id': latest, 'status': stopped['status'],
                'partial_output': stopped['result'], 'reason': stopped['error'],
                'caption_role': 'status', 'response_error_count': 0}
            assert report['cancel_requests'] == [{'path': '/api/workspace/tasks/' + latest + '/cancel', 'status': 200}]
            cancellation.scroll_into_view_if_needed()
            shot('native-cancelled-reloaded')
            audit('native-cancelled-reload')
            page.set_viewport_size({'width': 390, 'height': 844})
            cancellation.scroll_into_view_if_needed()
            shot('native-cancelled-narrow-neutral-caption')
            audit('native-cancelled-narrow')
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.get_by_role('button', name='Return to main agent', exact=True).click()
            wait(lambda: page.url.endswith('/session/' + primary_id), 'Final return did not preserve main')
            expect(composer).to_be_enabled(timeout=25000)
            expect(page.get_by_text('Opening main agent…', exact=True)).not_to_be_visible()
            region = page.get_by_role('region', name='Main agent coordination', exact=True)
            expect(region).to_be_visible()
            disclosure = region.get_by_role('button', name=re.compile('Delegated work'))
            if disclosure.get_attribute('aria-expanded') != 'true':
                disclosure.click()
            expect(region.get_by_role('button', name=re.compile('Native reviewer'))).to_contain_text('Cancelled')
            expect(region.get_by_role('button', name=re.compile(IDLE_TITLE))).to_contain_text('No task started')
            shot('native-and-adopted-status-in-main')
            audit('main-native-and-adopted')
            assert not report['page_errors'] and not report['external_requests']
            report['checks'] = [
                'Actual main tool loop lists engines/sessions and passes plan/delegation gates before Gateway dispatch',
                'Native source permission is reviewed in Main through real approval storage and the exact adapter response, without child navigation or ownership transfer',
                'Native progress/result and honest unsupported/unmeasured capabilities render',
                'Direct control/followup uses the same native session and returns ownership to main',
                'Reviewed adoption keeps existing idle conversation identity, no task and user ownership',
                'Idle handoff starts no native work; native Stop/reload preserves partial output, exact Interrupted reason, neutral status caption and direct ownership',
                'Desktop/narrow native and adopted controls pass WCAG and page-overflow checks with no external requests']
            report['native_dispatches'] = native.sent
            report['native_approval_decisions'] = native.decisions
            report['main_fixture_turns'] = main.turns
            report['status'] = 'passed'
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
            native.release_result.set()
            stop_owned_browser()
            if playwright:
                playwright.stop()
            host.should_exit = True
            worker.join(timeout=15)
            report['cleanup'] = {'browser_stopped': chrome is None or chrome.poll() is not None,
                                 'host_stopped': not worker.is_alive()}
            report['duration_seconds'] = round(monotonic() - started, 2)
            (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
            print('Report: ' + str(output / 'report.json'), flush=True)
    return report


if __name__ == '__main__':
    try:
        run(sys.argv[1])
    except BaseException as error:
        # Preserve a report even if fixture setup fails before the host/browser
        # ownership block starts. Runtime failures already write the full report.
        report_path = Path(sys.argv[1]).resolve() / 'report.json'
        if report_path.parent.is_dir() and not report_path.exists():
            report_path.write_text(json.dumps({'status': 'failed', 'phase': 'fixture-initialization',
                'error': str(error), 'traceback': traceback.format_exc(),
                'live_provider_calls': 0, 'native_vendor_processes': 0}, indent=2) + '\n')
        raise
