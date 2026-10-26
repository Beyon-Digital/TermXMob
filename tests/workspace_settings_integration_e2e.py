"""Real-host settings integration proof, with synthetic accounts and data.

Build desktop/workspace first, then run with a fresh output directory:
  TERMX_TEST_CHROMIUM=/path/to/chrome PYTHONPATH=src python tests/workspace_settings_integration_e2e.py OUTPUT

Production React, password/JWT, HTTP/GraphQL, SQLite, skills and MCP stdio are
real. The inference adapter raises if invoked. OS service mutation requests are
blocked by the test browser and make the proof fail. TLS configuration is saved
only in the private fixture profile; no listener, CA or service is installed.
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
from urllib.parse import urlsplit

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright
from termx.agent.secrets import CredentialStore
from workspace_ui_snapshot import snapshot_ui


class NoInference:
    def __init__(self):
        self.calls = 0
        self.tests = 0

    async def test(self):
        self.tests += 1
        raise AssertionError('Settings proof must not send a provider test')

    async def plan(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('Settings proof must not invoke inference')

    async def turn(self, **kwargs):
        self.calls += 1
        raise AssertionError('Settings proof must not invoke inference')


def run(destination):
    output = Path(destination).resolve()
    frames = output / 'frames'
    frames.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'checks': [], 'screenshots': [], 'axe': [],
              'page_errors': [], 'external_requests': [], 'requests': [],
              'forbidden_service_mutations': [], 'live_provider_calls': 0,
              'native_tauri_tested': False,
              'fixture_boundary': 'Synthetic identity/projects/provider key; real host/UI/stores, no-network inference guard, actual local MCP stdio fixture. Host service status and hostname are read-only real host metadata; service mutation requests are blocked.'}
    started = monotonic()
    with tempfile.TemporaryDirectory(prefix='termx-settings-integrated-') as temporary:
        root = Path(temporary).resolve()
        os.environ.update(TERMX_CONFIG_DIR=str(root / 'config'), TERMX_AGENTS_DIR=str(root / 'agents'),
                          TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState, create_app
        inference = NoInference()
        state = AppState(passcode=None, credentials=CredentialStore(memory={}),
                         adapter_factory=lambda *_: inference)
        owner = state.identity.setup_owner('settings-proof', 'synthetic-settings-password-2026')
        ui = snapshot_ui(root, Path(os.environ.get('TERMX_UI_PROOF_DIST', str(Path(__file__).resolve().parents[1] / 'desktop/workspace/dist'))))
        report['ui_sha256'] = json.loads((root / 'fixture-ui-snapshot.json').read_text())
        app = create_app(state, web_dir=ui)
        projects = []
        for slug, name in [('alpha', 'Alpha settings project'), ('beta', 'Beta settings project'), ('foreign', 'Outside connection allowlist')]:
            folder = root / slug
            folder.mkdir()
            projects.append(state.projects.register(str(folder), name=name))
        alpha, beta, foreign = projects
        memory = state.workspace.save_memory(owner, content='Keep fixture settings explicit', provenance='Synthetic integration proof')
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0)); listener.listen()
        base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        host = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on', access_log=False,
                                            timeout_graceful_shutdown=5))
        worker = threading.Thread(target=lambda: host.run(sockets=[listener]), daemon=True)
        worker.start()
        deadline = monotonic() + 30
        while not host.started:
            if monotonic() > deadline:
                host.should_exit = True; worker.join(timeout=5); listener.close()
                raise RuntimeError('Isolated settings host startup timed out')
            sleep(.05)
        page = browser = context = playwright = chrome = None
        try:
            playwright = sync_playwright().start()
            executable = os.environ.get('TERMX_TEST_CHROMIUM') or shutil.which('chromium')
            if not executable and sys.platform == 'darwin':
                executable = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            if not executable or not Path(executable).is_file():
                raise RuntimeError('Set TERMX_TEST_CHROMIUM to a trusted installed Chromium')
            profile = root / 'owned-browser'
            chrome = subprocess.Popen([executable, '--headless=new', '--no-first-run', '--no-default-browser-check',
                '--disable-background-networking', '--disable-sync', '--disable-component-update',
                '--disable-extensions', '--disable-default-apps', '--password-store=basic', '--use-mock-keychain',
                '--remote-debugging-port=0', '--user-data-dir=' + str(profile), 'about:blank'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['owned_browser_pid'] = chrome.pid
            port_file = profile / 'DevToolsActivePort'
            deadline = monotonic() + 30
            while not port_file.exists():
                if chrome.poll() is not None or monotonic() > deadline:
                    raise RuntimeError('Owned browser did not expose its debug endpoint')
                sleep(.05)
            browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:' + port_file.read_text().splitlines()[0], timeout=15000)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            def network(route):
                request = route.request
                if not request.url.startswith(base + '/'):
                    report['external_requests'].append(request.url); route.abort(); return
                path = urlsplit(request.url).path
                if path.startswith('/api/host/service/') and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                    report['forbidden_service_mutations'].append({'method': request.method, 'path': path})
                    route.abort(); return
                route.continue_()
            context.route('**/*', network)
            page = context.new_page(); page.set_default_timeout(20000)
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            def requested(request):
                path = urlsplit(request.url).path
                if not request.url.startswith(base + '/') or request.method in {'GET', 'HEAD', 'OPTIONS'}:
                    return
                row = {'method': request.method, 'path': path}
                if path == '/graphql':
                    try:
                        value = request.post_data_json
                        query = value.get('query', '')
                        row['operation'] = re.search(r'\b(?:mutation|query)\b[^{}]*\{\s*(\w+)', query).group(1) if re.search(r'\b(?:mutation|query)\b[^{}]*\{\s*(\w+)', query) else 'query'
                        variables = value.get('variables') or {}
                        if 'project' in variables: row['project'] = variables['project']
                        if row['operation'] == 'call_mcp_tool':
                            row.update(connection=variables.get('id'), tool=variables.get('tool'), arguments=(variables.get('input') or {}).get('arguments'))
                    except (ValueError, AttributeError):
                        row['operation'] = 'unparsed'
                report['requests'].append(row)
            page.on('request', requested)
            def wait(check, message, seconds=30):
                until = monotonic() + seconds
                while not check():
                    if monotonic() > until: raise AssertionError(message)
                    page.wait_for_timeout(50)
            def shot(name):
                path = frames / (f'{len(report["screenshots"])+1:02d}-' + name + '.png')
                page.screenshot(path=str(path)); report['screenshots'].append(str(path))
                print(json.dumps({'stage': name, 'url': page.url}), flush=True)
            def audit(stage):
                if not page.evaluate('typeof axe !== "undefined"'):
                    page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'desktop/workspace/node_modules/axe-core/axe.min.js'))
                violations = page.evaluate("""async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target),details:v.nodes.map(n=>n.failureSummary)}))}""")
                overflow = page.evaluate('document.documentElement.scrollWidth>innerWidth')
                report['axe'].append({'stage': stage, 'violations': violations, 'page_overflow': overflow})
                assert not violations and not overflow, report['axe'][-1]
            def mutations(operation):
                return [row for row in report['requests'] if row.get('operation') == operation]
            def posts(path, method='POST'):
                return [row for row in report['requests'] if row['path'] == path and row['method'] == method]

            page.goto(base)
            page.get_by_label('Username', exact=True).fill('settings-proof')
            page.get_by_label(re.compile('^Password')).fill('synthetic-settings-password-2026')
            page.get_by_role('button', name='Continue with password', exact=True).click()
            composer = page.get_by_role('textbox', name='Message', exact=True)
            expect(composer).to_be_enabled(timeout=30000)
            wait(lambda: '/session/' in page.url, 'Main did not become the primary route')
            main_id = page.url.rsplit('/', 1)[-1]
            draft = 'Preserve Main while managing explicit settings'
            composer.fill(draft)
            wait(lambda: state.workspace.session(owner, main_id, turns=False)['draft_text'] == draft, 'Main draft did not persist')
            page.get_by_role('button', name='Settings', exact=True).click()
            settings = page.get_by_role('dialog', name='Settings', exact=True)
            expect(settings.get_by_role('heading', name='General', exact=True)).to_be_visible()
            expect(settings.get_by_text('settings-proof', exact=True).first).to_be_visible()
            expect(settings.get_by_role('combobox', name='Settings scope', exact=True)).not_to_be_visible()
            nav = settings.get_by_role('navigation', name='Settings categories', exact=True)
            def category(identifier, label):
                if page.viewport_size['width'] <= 700:
                    nav.get_by_role('combobox', name='Settings category', exact=True).select_option(identifier)
                else:
                    nav.get_by_role('button', name=label, exact=True).click()
                wait(lambda: urlsplit(page.url).path == '/manage/' + identifier, 'Settings route did not select ' + identifier)
            shot('general-account-host'); audit('general-account-host')
            report['checks'].append('Real configured-owner login opens the durable Main; General uses account/host context and preserves the underlying Main draft')

            category('models', 'Models & engines')
            settings.get_by_text('Add provider account', exact=True).click()
            form = settings.get_by_role('form', name='Add provider account', exact=True)
            form.get_by_label('Provider', exact=True).select_option('custom')
            form.get_by_label('Display name', exact=True).fill('Settings no-network provider')
            form.get_by_label('Provider API address', exact=True).fill('http://127.0.0.1:1/v1')
            form.get_by_label('API key', exact=True).fill('synthetic-not-a-real-provider-key')
            form.get_by_label('Default model for future sessions', exact=True).fill('fixture')
            form.get_by_role('button', name='Save account', exact=True).click()
            expect(settings.get_by_text('Provider account saved', exact=True)).to_be_visible()
            before = len(mutations('test_agent_provider'))
            settings.get_by_role('button', name='Test connection', exact=True).click()
            review = page.get_by_role('dialog', name='Test Settings no-network provider?', exact=True)
            expect(review).to_contain_text('Provider charges may apply')
            review.get_by_role('button', name='Cancel test', exact=True).click()
            assert len(mutations('test_agent_provider')) == before and inference.tests == 0
            settings.get_by_role('button', name='Close', exact=True).click()
            expect(composer).to_have_value(draft)
            page.get_by_role('button', name='Use for this chat', exact=True).click()
            wait(lambda: state.workspace.session(owner, main_id, turns=False).get('provider_id') is not None, 'Explicit Main account/model binding did not save')
            assert inference.calls == inference.tests == 0
            page.get_by_role('button', name='Settings', exact=True).click()
            expect(settings.get_by_role('heading', name='General', exact=True)).to_be_visible()
            report['checks'].append('Actual account form and explicit Main account/model binding preserve the draft; billable test review cancellation sends no inference')

            category('memory', 'Memory')
            expect(settings.get_by_role('combobox', name='Settings scope', exact=True)).to_be_visible()
            settings.get_by_role('button', name='Edit memory', exact=True).click()
            editor = settings.get_by_role('form', name='Edit memory', exact=True)
            changed_memory = 'Keep this unsaved fixture decision'
            editor.get_by_label('Fact or decision', exact=True).fill(changed_memory)
            page.once('dialog', lambda dialog: dialog.dismiss())
            nav.get_by_role('button', name='Hooks', exact=True).click()
            expect(editor.get_by_label('Fact or decision', exact=True)).to_have_value(changed_memory)
            assert urlsplit(page.url).path == '/manage/memory'
            page.once('dialog', lambda dialog: dialog.dismiss()); page.keyboard.press('Escape')
            expect(settings).to_be_visible()
            editor.get_by_role('button', name='Save memory changes', exact=True).click()
            wait(lambda: state.workspace.store.get('memory', memory['id'])['content'] == changed_memory, 'Memory save did not reach actual store')
            report['checks'].append('Memory retains explicit project scope; unsaved edits survive refused category/escape discard and actual save clears the form receipt')

            category('extensions', 'Plugins & skills')
            expect(settings.get_by_label('Skill folder name', exact=True)).not_to_be_visible()
            settings.get_by_text('Add a skill', exact=True).click()
            source_name = 'settings-fixture-skill'
            markdown = '---\nname: settings-fixture-skill\ndescription: Inspect a synthetic fixture without writing files.\n---\nRead the selected fixture and explain it.\n'
            settings.get_by_label('Skill folder name', exact=True).fill(source_name)
            settings.get_by_label('Skill instructions', exact=True).fill(markdown)
            settings.get_by_role('button', name='Validate skill', exact=True).click()
            expect(settings.get_by_text('Skill metadata and instructions validated', exact=True)).to_be_visible()
            before = posts('/api/workspace/skill-sources/source', 'PUT')
            page.once('dialog', lambda dialog: dialog.dismiss()); nav.get_by_role('button', name='Hooks', exact=True).click()
            expect(settings.get_by_label('Skill instructions', exact=True)).to_have_value(markdown)
            assert len(posts('/api/workspace/skill-sources/source', 'PUT')) == len(before)
            settings.get_by_role('button', name='Save source', exact=True).click()
            expect(settings.get_by_text('Skill source saved', exact=True)).to_be_visible()
            actual = state.skill_sources.read(owner, scope='user', name=source_name)
            assert actual['markdown'] == markdown
            # A second real identity retains readable project inventory without a write grant.
            state.skill_sources.save(owner, scope='project', project_id=alpha['id'], name='read-only-fixture', markdown=markdown, expected_digest=None)
            member = state.identity.create_local_user('readonly-settings', 'synthetic-readonly-password-2026', ['files-read', 'files-write', 'agent-view'])
            state.authorization.set_role(member.id, 'operator')
            state.authorization.grant_project(member.id, alpha['id'], ['files-read', 'agent-view'])
            credentials = asyncio.run(state.identity.login('local-password', {'username': 'readonly-settings', 'password': 'synthetic-readonly-password-2026'}, peer='settings-fixture'))
            with httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + credentials.access_token}, timeout=15) as client:
                response = client.get('/api/workspace/skill-sources', params={'project_id': alpha['id']})
                assert response.status_code == 200, response.text
                roots = response.json()['roots']; assert len(roots) == 1 and roots[0]['editable'] is False
                denied = client.put('/api/workspace/skill-sources/source', json={'scope': 'project', 'project_id': alpha['id'], 'name': 'denied-write', 'markdown': markdown, 'expected_digest': None})
                assert denied.status_code in {403, 404}, denied.text
            report['checks'].append('Actual skill validation does not save; explicit save writes managed SKILL.md; a read-only project member receives editable=false and its HTTP write is denied')

            category('access', 'Access & sessions')
            expect(settings.get_by_role('heading', name='Local network HTTPS', exact=True)).not_to_be_visible()
            settings.get_by_text('Host connectivity', exact=True).click()
            tls = settings.get_by_role('region', name='Local network TLS', exact=True)
            expect(tls.get_by_role('heading', name='Local network HTTPS', exact=True)).to_be_visible()
            before = len(posts('/auth/lan-tls', 'PUT'))
            tls.get_by_text('Network settings', exact=True).click()
            tls.get_by_label('HTTPS port', exact=True).fill('8899')
            tls.get_by_label('Bind address', exact=True).fill('127.0.0.1')
            tls.get_by_label('Additional .local names or private addresses', exact=True).fill('settings-fixture.local')
            tls.get_by_label('Enable LAN HTTPS after restart', exact=True).check()
            dialogs = []
            def refuse_tls(dialog):
                dialogs.append(dialog.message); dialog.dismiss()
            page.once('dialog', refuse_tls)
            tls.get_by_role('button', name='Save LAN HTTPS settings', exact=True).click()
            assert dialogs and '127.0.0.1:8899' in dialogs[-1] and 'settings-fixture.local' in dialogs[-1]
            assert len(posts('/auth/lan-tls', 'PUT')) == before
            tls.get_by_label('Enable LAN HTTPS after restart', exact=True).uncheck()
            tls.get_by_role('button', name='Save LAN HTTPS settings', exact=True).click()
            expect(tls.get_by_text('Saved. Restart the host to apply LAN exposure changes. No trust root was installed.', exact=True)).to_be_visible()
            config = json.loads((root / 'config/lan-tls-settings.json').read_text())
            assert config['port'] == 8899 and config['enabled'] is False
            assert not report['forbidden_service_mutations']
            shot('host-and-tls-progressive-review'); audit('host-tls')
            report['checks'].append('Host controls mount only after disclosure; exact LAN enable review cancels with zero PUT; disabled TLS profile saves without listener, CA install or OS service mutation')

            category('hooks', 'Hooks')
            expect(settings.get_by_role('combobox', name='Settings scope', exact=True)).not_to_be_visible()
            hooks = settings.get_by_role('region', name='Lifecycle hooks', exact=True)
            expect(hooks.get_by_role('heading', name='Lifecycle hooks', exact=True)).to_be_visible()
            hooks.get_by_text('Add a hook', exact=True).click()
            hook_form = hooks.get_by_role('form', name='Create lifecycle hook', exact=True)
            expect(hook_form.get_by_role('combobox', name='Run in project', exact=True)).to_have_value('')
            hook_form.get_by_role('combobox', name='Run in project', exact=True).select_option(alpha['id'])
            hook_form.get_by_label('Executable and arguments (one per line)', exact=True).fill(json.dumps([sys.executable, '-c', 'print("synthetic hook")']))
            hook_form.get_by_role('button', name='Review disabled hook', exact=True).click()
            hook_review = page.get_by_role('dialog', name='Save this disabled hook?', exact=True)
            expect(hook_review).to_contain_text(alpha['name']); expect(hook_review).to_contain_text(alpha['path'])
            before = len(posts('/api/workspace/hooks'))
            hook_review.get_by_role('button', name='Cancel review', exact=True).click()
            assert len(posts('/api/workspace/hooks')) == before
            hook_form.get_by_role('button', name='Review disabled hook', exact=True).click()
            hook_review.get_by_role('button', name='Save disabled hook', exact=True).click()
            wait(lambda: len(posts('/api/workspace/hooks')) == before + 1, 'Hook creation did not dispatch exactly once')
            card = hooks.get_by_role('article', name='Hook in ' + alpha['name'], exact=True)
            expect(card).to_contain_text('Disabled')
            card.get_by_role('button', name='Enable', exact=True).click()
            enable = page.get_by_role('dialog', name='Enable this lifecycle hook?', exact=True)
            expect(enable).to_contain_text('chat tool approvals do not approve each hook invocation')
            enable.get_by_role('button', name='Cancel review', exact=True).click()
            assert not [row for row in report['requests'] if row['path'].startswith('/api/workspace/hooks/') and row['method'] == 'PATCH']
            assert not state.workspace.store.list('hook_run')
            shot('hook-explicit-target-disabled'); audit('hook-target')
            report['checks'].append('Lifecycle hook requires an explicit project; create review cancellation sends nothing, confirmed save creates one disabled record, enable cancellation executes no command')

            category('connections', 'Tools & connections')
            expect(settings.get_by_role('combobox', name='Settings scope', exact=True)).not_to_be_visible()
            settings.get_by_text('Add a tool connection', exact=True).click()
            form = settings.get_by_role('form', name='Add tool connection', exact=True)
            form.get_by_text('Advanced · internal connection key', exact=True).click()
            form.get_by_label('Connection key (generated automatically)', exact=True).fill('ui-settings-tools')
            form.get_by_label('Connection name', exact=True).fill('Synthetic scoped tools')
            form.get_by_role('combobox', name='Connection transport', exact=True).select_option('stdio')
            form.get_by_label('Executable and arguments (one per line)', exact=True).fill(json.dumps([sys.executable, str(Path(__file__).parent.resolve() / 'fixtures/fake_mcp_server.py')]))
            form.get_by_role('radio', name='Only selected projects', exact=True).check()
            form.get_by_role('checkbox', name=alpha['name'], exact=True).check(); form.get_by_role('checkbox', name=beta['name'], exact=True).check()
            form.get_by_label('Approved tool names', exact=True).fill('add')
            form.get_by_role('button', name='Save untrusted connection', exact=True).click()
            card = settings.get_by_role('article').filter(has=page.get_by_role('heading', name='Synthetic scoped tools', exact=True))
            expect(card).to_be_visible(); assert state.mcp_pool.status() == {}
            selector = card.get_by_role('combobox', name='Connection target for Synthetic scoped tools', exact=True)
            expect(selector).to_have_value(''); expect(card.get_by_role('button', name='Connect', exact=True)).to_be_disabled()
            assert foreign['id'] not in selector.locator('option').evaluate_all('(nodes)=>nodes.map(node=>node.value)')
            before_connect = len(mutations('connect_mcp_connection')); before_call = len(mutations('call_mcp_tool'))
            card.get_by_role('button', name='Inspect tools and permissions', exact=True).click()
            inspection = settings.get_by_role('region', name='Connection permission inspection', exact=True)
            expect(inspection).to_contain_text('It does not connect or run a tool')
            assert len(mutations('connect_mcp_connection')) == before_connect and len(mutations('call_mcp_tool')) == before_call
            inspection.get_by_role('button', name='Close connection inspection', exact=True).click()
            card.get_by_role('button', name='Trust reviewed connection', exact=True).click()
            expect(card.get_by_role('button', name='Connect', exact=True)).to_be_disabled()
            selector.select_option(alpha['id']); card.get_by_role('button', name='Connect', exact=True).click()
            expect(card.get_by_role('button', name='Disconnect', exact=True)).to_be_visible(timeout=30000)
            # A refreshed inventory reauthorizes the explicit target; select it
            # again if the host snapshot replaced the connection state.
            selector.select_option(alpha['id'])
            card.get_by_role('button', name='Inspect tools and permissions', exact=True).click()
            inspection.get_by_role('combobox', name='Tool to inspect', exact=True).select_option('add')
            expect(inspection.get_by_role('button', name='Call selected tool', exact=True)).not_to_be_visible()
            inspection.get_by_text('Advanced · run this tool', exact=True).click()
            inspection.get_by_role('textbox', name='Tool arguments', exact=True).fill('{"a":7,"b":5}')
            inspection.get_by_role('button', name='Call selected tool', exact=True).click()
            review = inspection.get_by_role('region', name='Review tool call', exact=True)
            expect(review).to_contain_text(alpha['name']); expect(review).to_contain_text(alpha['id'])
            assert len(mutations('call_mcp_tool')) == before_call
            review.get_by_role('button', name='Cancel tool call', exact=True).click()
            assert len(mutations('call_mcp_tool')) == before_call
            inspection.get_by_role('button', name='Call selected tool', exact=True).click()
            review.get_by_role('button', name='Confirm tool call', exact=True).click()
            result = inspection.get_by_role('region', name='Tool result', exact=True)
            expect(result).to_contain_text('12', timeout=30000)
            calls = mutations('call_mcp_tool'); assert len(calls) == before_call + 1 and calls[-1]['project'] == alpha['id']
            report['actual_stdio_result'] = json.loads(result.inner_text())
            assert state.mcp_registry().get('ui-settings-tools').approved_tools == ['add']
            # Same authenticated owner, but a connection-disallowed project: host must deny.
            forbidden = context.request.post(base + '/graphql', data={'query': 'mutation($id:String!,$tool:String!,$input:McpToolCallInput!,$project:String){call_mcp_tool(conn_id:$id,tool:$tool,input:$input,project_id:$project)}', 'variables': {'id': calls[-1]['connection'], 'tool': 'add', 'input': {'arguments': {'a':900,'b':1}}, 'project': foreign['id']}}, headers={'Content-Type':'application/json','Origin':base,'X-Termx-CSRF':next(cookie['value'] for cookie in context.cookies() if cookie['name']=='termx_csrf')})
            assert forbidden.status == 200 and forbidden.json().get('errors'), forbidden.text()
            report['connection_disallowed_project_denied'] = True
            report['checks'].append('MCP saves untrusted without connecting; only allowlisted targets appear; inspection is read-only; advanced exact review cancels without call then executes one actual stdio add on its selected project; foreign project is denied')
            shot('mcp-actual-scoped-tool-result'); audit('mcp-actual-result')

            for width, height in [(1440,1000),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                for theme in ['dark','light']:
                    category('general','General')
                    settings.get_by_role('combobox',name='Theme preference',exact=True).select_option(theme)
                    assert page.locator('html').get_attribute('data-theme') == theme
                    if width <= 700:
                        selector = nav.get_by_role('combobox',name='Settings category',exact=True)
                        assert selector.bounding_box()['height'] >= 44
                    for identifier, label in [('general','General'),('models','Models & engines'),('extensions','Plugins & skills'),('connections','Tools & connections'),('hooks','Hooks')]:
                        category(identifier,label)
                        expect(settings.get_by_role('heading',name=label if identifier not in {'connections','hooks'} else 'Tool connections' if identifier=='connections' else 'Lifecycle hooks',exact=True).first).to_be_visible()
                        stage=identifier+'-'+theme+'-'+str(width)
                        shot(stage); audit(stage)
                    for key in ['Tab','Shift+Tab']*4:
                        page.keyboard.press(key); assert settings.evaluate('(node)=>node.contains(document.activeElement)')
            report['checks'].append('Grouped desktop and 44px mobile category navigation, theme changes and actual settings content fit at 1440/390px in both themes with zero axe violations and trapped modal focus')
            page.set_viewport_size({'width':1440,'height':1000})
            settings.get_by_role('button',name='Close',exact=True).click()
            expect(composer).to_have_value(draft)
            assert page.url.endswith('/session/'+main_id)
            page.reload(); expect(composer).to_have_value(draft,timeout=30000)
            assert page.url.endswith('/session/'+main_id) and len(state.workspace.store.list('orchestrator',owner.id))==1
            assert inference.calls == inference.tests == 0
            assert not report['forbidden_service_mutations'] and not report['page_errors'] and not report['external_requests']
            report['checks'].append('Closing settings and reloading restores the same single Main and draft; no inference, external navigation, implicit service mutation or duplicate Main occurred')
            report['status']='passed'
        except BaseException as error:
            report.update(status='failed',error=str(error),traceback=traceback.format_exc())
            if page:
                try:
                    page.screenshot(path=str(frames/'failure.png'));report['visible_text']=page.locator('body').inner_text()
                except Exception: pass
            raise
        finally:
            report['inference_requests']={'turns':inference.calls,'provider_tests':inference.tests}
            if context:
                try:context.close()
                except Exception:pass
            if browser:
                try:browser.close()
                except Exception:pass
            if playwright:
                try:playwright.stop()
                except Exception:pass
            if chrome and chrome.poll() is None:
                chrome.terminate()
                try:chrome.wait(timeout=5)
                except subprocess.TimeoutExpired:chrome.kill();chrome.wait(timeout=3)
            host.should_exit=True;worker.join(timeout=15)
            report['cleanup']={'browser_stopped':chrome is None or chrome.poll() is not None,'host_stopped':not worker.is_alive()}
            cleanup_failed = report['status']=='passed' and not all(report['cleanup'].values())
            if cleanup_failed: report.update(status='failed',error='An owned fixture host or browser did not stop')
            report['duration_seconds']=round(monotonic()-started,2)
            (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
            print('Report: '+str(output/'report.json'),flush=True)
            if cleanup_failed: raise RuntimeError(report['error'])
    return report


if __name__=='__main__':
    try:
        run(sys.argv[1])
    except BaseException as error:
        # Initialization failures also have a concrete artifact instead of
        # leaving a parent to infer whether a fixture ever started.
        path = Path(sys.argv[1]).resolve() / 'report.json'
        if path.parent.is_dir() and not path.exists():
            path.write_text(json.dumps({'status':'failed','phase':'fixture-initialization',
                'error':str(error),'traceback':traceback.format_exc(),
                'live_provider_calls':0,'native_tauri_tested':False},indent=2)+'\n')
        raise
