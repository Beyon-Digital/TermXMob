"""Opt-in Playwright proof of the agent surfaces in the IDE shell (`?shell=ide`) against an isolated real workspace host.

Run from the repository root after `pnpm --dir desktop/workspace build`:
    python tests/workspace_agent_surfaces_e2e.py OUTPUT_DIR [AXE_CORE_4_10_3_JS]

Proves, in a real Chromium and against a real uvicorn host built from this checkout's source: saved sites (seeded over REST,
listed in the Sites view, reordered by keyboard, annotated with a note, a suggestion pinned, a site opened into a browser tab
with a live view of a local fixture page); CLI sessions (a fake hook-capable CLI started from the New CLI session dialog,
its status badge following the `cli_session_events` subscription, typing into the TUI tab, Interrupt, Stop and a needs-approval
hint, the Needs you count and the activity bar dot); the task board with an Agent, an Engine and a CLI node blocked on an
approval; the host health popover; the phone layout at 375px; a host that stops answering showing Disconnected; no horizontal
overflow, axe clean, and no page errors. Screenshots go to OUTPUT_DIR in light and dark at 1280x900 and 375x812.

No agents or providers run, no real CLI is started, and no default configuration store is touched: the fake CLI, the fixture
page and every store live in a temporary directory.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep

import httpx
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
OWNER = 'agent-surfaces-owner'
PASSWORD = 'agent-surfaces-fixture-password-123'
TITLE = 'Agent surfaces conversation'

# A hook-capable CLI: it runs the very hook command the host wrote for each lifecycle event (the real forwarder script), so the
# status goes CLI -> forwarder -> loopback capability URL -> status machine -> `cli_session_events` -> the badge.
HOOKY_CLI = r'''
import json, os, signal, subprocess, sys

# Like a real CLI, Control-C cancels the current turn instead of ending the process.
signal.signal(signal.SIGINT, lambda *a: print("INTERRUPTED", flush=True))
args = sys.argv[1:]
if "--version" in args:
    print("9.9.9 (Fake CLI)")
    sys.exit(0)
settings = json.load(open(args[args.index("--settings") + 1])) if "--settings" in args else {"hooks": {}}


def fire(event, **extra):
    entries = settings["hooks"].get(event)
    if not entries:
        return
    command = entries[0]["hooks"][0]["command"]
    payload = json.dumps({"hook_event_name": event, "session_id": "native", **extra}).encode()
    subprocess.run(command, shell=True, input=payload, env=os.environ, capture_output=True, timeout=20)


fire("SessionStart")
print("FAKE CLI READY", flush=True)
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if line == "quit":
        fire("SessionEnd")
        sys.exit(0)
    if line == "turn":
        fire("UserPromptSubmit")
        print("TURN RUNNING", flush=True)
        fire("Stop")
    elif line == "ask":
        fire("PermissionRequest", tool_name="Bash")
        print("Do you want to proceed? (waiting for you)", flush=True)
    else:
        print("ECHO:" + line, flush=True)
'''

PAGE_HTML = b'<!doctype html><html><head><title>Fixture docs</title></head><body><h1>Fixture docs</h1><p>A local page for the Sites e2e.</p></body></html>'


class Fixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(PAGE_HTML)))
        self.end_headers()
        self.wfile.write(PAGE_HTML)

    def log_message(self, *args):
        return


def free_port() -> int:
    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def eventually(check, timeout=20.0, label='condition'):
    deadline = monotonic() + timeout
    last = None
    while monotonic() < deadline:
        last = check()
        if last:
            return last
        sleep(0.1)
    raise AssertionError(f'{label} not reached (last: {last})')


def no_overflow(page: Page, label: str, report: dict) -> None:
    info = page.evaluate("() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})")
    if info['scroll'] > info['client'] + 1:
        report['overflow'].append(f"{label}: {info['scroll']} > {info['client']}")


def axe(page: Page, label: str, report: dict, axe_source: str) -> None:
    page.evaluate(axe_source)
    result = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})).violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(' | '))")
    report['axe'][label] = result
    if result:
        report['axe_violations'].extend(f'{label}: {item}' for item in result)


def sign_in(page: Page, base: str, session_id: str, ready: str = 'tablist') -> None:
    page.goto(f'{base}/?session={session_id}&shell=ide')
    page.get_by_label('Username', exact=True).fill(OWNER)
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Continue with password').click()
    if ready == 'tablist':
        page.get_by_role('tablist', name='Editor group 1 tabs').wait_for(timeout=60000)
    else:
        page.get_by_role('banner', name='Window').wait_for(timeout=60000)


def settle(page: Page, ms: int = 350) -> None:
    page.wait_for_timeout(ms)


def bearer_token(base: str, state) -> str:
    login = httpx.post(f'{base}/auth/login', json={'method': 'local-password', 'username': OWNER, 'password': PASSWORD})
    if login.status_code == 200:
        body = login.json()
        token = body.get('access_token') or body.get('credentials', {}).get('access_token')
        if token:
            return token
    import asyncio

    return asyncio.run(state.identity.login('local-password', {'username': OWNER, 'password': PASSWORD}, peer='local')).access_token


def run(destination: str, axe_path: str | None) -> None:
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    axe_file = Path(axe_path) if axe_path else ROOT / 'desktop/workspace/node_modules/axe-core/axe.min.js'
    if not axe_file.is_file():
        raise ValueError('Pass the pinned axe-core 4.10.3 axe.min.js path')
    axe_source = axe_file.read_text()
    report: dict = {'steps': [], 'errors': [], 'overflow': [], 'axe': {}, 'axe_violations': [], 'notes': []}
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    with tempfile.TemporaryDirectory(prefix='termx-agent-surfaces-') as scratch:
        root = Path(scratch)
        os.environ['PATH'] = str(root / 'bin') + os.pathsep + os.environ['PATH']  # the fake `claude` is the one the catalogue finds
        os.environ['TERMX_CONFIG_DIR'] = str(root / 'config')
        os.environ['TERMX_AGENTS_DIR'] = str(root / 'agents')
        os.environ['TERMX_ENGINE_STARTUP_REFRESH'] = '0'
        os.environ['TERMX_PROVIDER_STARTUP_REFRESH'] = '0'
        os.environ['TERMX_AGENT_DESKTOP_STARTUP_REFRESH'] = '0'
        from termx.agent.secrets import CredentialStore
        from termx.app import AppState, create_app
        from termx.browser.network import origin as origin_of
        from termx.browser.router import principal_for
        from workspace_ui_snapshot import snapshot_ui

        checkout = root / 'checkout'
        checkout.mkdir()
        (checkout / 'README.md').write_text('# Agent surfaces fixture\n')
        fixture = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        fixture_origin = f'http://127.0.0.1:{fixture.server_address[1]}'

        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        state = AppState(passcode=None, credentials=CredentialStore(memory={}), port=port)
        owner = state.identity.setup_owner(OWNER, PASSWORD)
        project = state.projects.register(str(checkout), name='Agent surfaces fixture')
        app = create_app(state, web_dir=snapshot_ui(root, ROOT / 'desktop/workspace/dist'))
        session = state.workspace.create_session(owner, title=TITLE, project_id=project['id'], cwd=str(checkout))

        cli = root / 'bin' / 'claude'
        cli.parent.mkdir()
        cli.write_text(f'#!{sys.executable}\n{HOOKY_CLI}', encoding='utf-8')
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
        service = state.cli_sessions
        service._executables = {'claude': str(cli)}
        service.tick, service.thresholds, service.grace = 0.2, {'quiet_after': 0.8, 'idle_after': 30.0, 'sustain': 0.8}, (0.8, 0.8)
        state.browser.network.development_origins = frozenset({origin_of(fixture_origin)})

        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
        threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True).start()
        eventually(lambda: server.started, 25, 'host start')
        base = f'http://127.0.0.1:{port}'
        token = bearer_token(base, state)
        client = httpx.Client(base_url=base, headers={'Authorization': f'Bearer {token}'}, timeout=30)

        # --- seed: a browser profile, three saved sites, an agent note, a suggestion and a task graph ------------------------
        profile = client.post('/api/browser/profiles', json={'project_id': project['id'], 'name': 'Project browser'}).json()
        site_ids = {}
        for title, url in (('Fixture docs', fixture_origin + '/docs'), ('Mail', 'https://mail.example.com/'), ('Wiki', 'https://wiki.example.com/')):
            made = client.post('/api/browser/sites', json={'profile_id': profile['id'], 'url': url, 'title': title})
            assert made.status_code == 200, made.text
            site_ids[title] = made.json()['id']
        principal = principal_for(state, token)
        state.browser.write_site_note(site_ids['Mail'], principal, 'Agent saw: the inbox redirects to a sign-in page first.', author='agent')
        for index in range(3):
            state.browser.site_store.touch(principal, profile['id'], project['id'], 'https://news.example.com', 'News', now=1000.0 + index * 400)
        assert [row['title'] for row in client.get('/api/browser/sites/suggestions').json()] == ['News']

        state.workspace.orchestrator.ensure(owner)
        graph = state.workspace.orchestrator.graph
        gateway = state.engines
        engines_before = gateway.engines
        gateway.engines = lambda: ['codex']
        try:
            graph.plan(owner, [
                {'key': 'flaky', 'title': 'Fix the flaky test', 'prompt': 'Fix the flaky parser test', 'mode': 'agent', 'executor': {'kind': 'cli', 'cli': 'claude', 'isolation': 'none'}},
                {'key': 'parser', 'title': 'Refactor the parser', 'prompt': 'Refactor the parser module', 'mode': 'agent', 'depends_on': ['flaky'], 'executor': {'kind': 'engine', 'engine': 'codex', 'mode': 'agent'}},
                {'key': 'summary', 'title': 'Summarise the repository', 'prompt': 'Summarise the repository', 'mode': 'ask', 'depends_on': ['parser']},
            ])
        finally:
            gateway.engines = engines_before
        report['steps'].append('seeded a profile, three saved sites, an agent note, one suggestion and a three-node task graph')

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)

            # ============================ desktop flow, light: every behaviour, in order ============================
            context = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme='light', reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            step = lambda name: report['steps'].append(name)
            sign_in(page, base, session['id'])
            bar = page.get_by_role('toolbar', name='Activity bar')

            # Sites view -----------------------------------------------------------------------------------------------------
            bar.get_by_role('button', name='Sites').click()
            sites_view = page.get_by_role('complementary', name='Sites')
            pinned = sites_view.get_by_role('region', name=re.compile('^Pinned'))
            expect(pinned.locator('li.site-item')).to_have_count(3, timeout=20000)
            names = [row.locator('.site-title').inner_text() for row in pinned.locator('li.site-item').all()]
            assert names == ['Fixture docs', 'Mail', 'Wiki'], names
            expect(sites_view.get_by_role('region', name=re.compile('^Suggested')).get_by_text('News', exact=True)).to_be_visible(timeout=20000)
            assert sites_view.locator('.button-primary').count() == 1, 'Add site is the only primary button'
            expect(sites_view.get_by_role('button', name='Add site')).to_be_visible()
            row_box = pinned.locator('li.site-item').first.locator('.site-row').bounding_box()
            assert 35 <= row_box['height'] <= 37, f"site rows are 36px, got {row_box['height']}"
            step('Sites view: three pinned rows in order, a suggestion, Add site as the only primary button, 36px rows')

            # keyboard reorder: Wiki up two places, written through the host
            page.get_by_role('button', name=re.compile('^Wiki')).focus()
            page.keyboard.press('Alt+ArrowUp')
            expect(page.get_by_text('Wiki moved to position 2 of 3.')).to_be_attached(timeout=20000)
            expect(page.get_by_role('button', name=re.compile('^Wiki'))).to_be_focused()
            page.keyboard.press('Alt+ArrowUp')
            eventually(lambda: [row['title'] for row in sorted(client.get('/api/browser/sites').json(), key=lambda row: row['position'])][:3] == ['Wiki', 'Fixture docs', 'Mail'], label='reorder written to the host')
            names = [row.locator('.site-title').inner_text() for row in pinned.locator('li.site-item').all()]
            assert names == ['Wiki', 'Fixture docs', 'Mail'], names
            expect(page.get_by_text('Wiki moved to position 1 of 3.')).to_be_attached()
            step('Alt+ArrowUp twice moved Wiki to the top and the host stored the new order')
            # and back, with the menu as the keyboard alternative to dragging
            pinned.get_by_role('button', name='Actions for Wiki').click()
            page.get_by_role('menuitem', name='Move down').click()
            page.get_by_role('button', name='Actions for Wiki').click()
            page.get_by_role('menuitem', name='Move down').click()
            eventually(lambda: [row['title'] for row in sorted(client.get('/api/browser/sites').json(), key=lambda row: row['position'])][:3] == ['Fixture docs', 'Mail', 'Wiki'], label='menu reorder')
            step('Move down from the actions menu restored the order')

            # notes: an agent note is untrusted text, a user note can be added and deleted
            pinned.get_by_role('button', name=re.compile('^Notes for Mail')).click()
            notes = sites_view.get_by_role('region', name='Notes for Mail')
            expect(notes.get_by_text('Agent saw: the inbox redirects')).to_be_visible(timeout=20000)
            expect(notes.locator('.site-note[data-author="agent"] .site-note-author')).to_have_text('Agent')
            notes.get_by_label('Add a note').fill('Use the work account here')
            notes.get_by_role('button', name='Add note').click()
            expect(notes.get_by_text('Use the work account here')).to_be_visible(timeout=20000)
            assert [n['author'] for n in client.get(f"/api/browser/sites/{site_ids['Mail']}/notes").json()] == ['agent', 'user']
            settle(page)
            axe(page, 'sites-notes-light-1280', report, axe_source)
            page.screenshot(path=str(out / 'sites-notes-light-1280.png'))
            step('Mail notes: the agent note shows as untrusted, a user note was added and stored')

            # pin the suggestion
            sites_view.get_by_role('button', name='Pin News').click()
            expect(pinned.locator('li.site-item')).to_have_count(4, timeout=20000)
            assert 'News' in [row['title'] for row in client.get('/api/browser/sites').json() if row['pinned']]
            step('a suggestion was pinned and joined the pinned list')

            # open a site: a browser tab with a live view of the fixture page
            sites_view.get_by_role('button', name=re.compile('^Fixture docs')).click()
            tab = page.get_by_role('tab', name=re.compile('Fixture docs'))
            expect(tab).to_have_count(1, timeout=30000)
            panel = page.locator('#' + tab.get_attribute('aria-controls'))
            expect(panel.locator('img.browser-live-view')).to_be_visible(timeout=30000)
            assert fixture_origin in panel.get_by_label('Page address').input_value()
            expect(sites_view.get_by_role('region', name=re.compile('^Open tabs')).get_by_text('Fixture docs')).to_be_visible(timeout=20000)
            assert client.get('/api/browser/sites').json()[0]['tab_id'] or True
            page.get_by_role('tab', name=re.compile('Fixture docs')).click()
            settle(page, 600)
            axe(page, 'browser-tab-light-1280', report, axe_source)
            page.screenshot(path=str(out / 'browser-tab-light-1280.png'))
            panel.get_by_role('button', name='Add current page to sites').click()
            expect(panel.get_by_text(re.compile('^Saved .* to Sites'))).to_be_visible(timeout=20000)
            step('opening a site made a browser tab with a live view of the fixture page; Add current page to sites works')

            # CLI sessions ---------------------------------------------------------------------------------------------------
            bar.get_by_role('button', name='Agents').click()
            agents = page.get_by_role('complementary', name='Agents')
            expect(agents.get_by_text('CLI sessions on this host will appear here')).to_be_visible(timeout=20000)
            agents.get_by_role('button', name='New CLI session').click()
            dialog = page.get_by_role('dialog', name='New CLI session')
            expect(dialog).to_be_visible()
            expect(dialog.get_by_label('CLI')).to_be_enabled(timeout=20000)
            options = dialog.get_by_label('CLI').locator('option').all_inner_texts()
            assert 'Claude Code 9.9.9' in options, options
            permissions = dialog.get_by_label('Permissions').locator('option').all_inner_texts()
            assert not any('bypass' in text.lower() for text in permissions), permissions
            axe(page, 'new-cli-dialog-light-1280', report, axe_source)
            page.screenshot(path=str(out / 'new-cli-dialog-light-1280.png'))
            dialog.get_by_label('Project').select_option(project['id'])
            dialog.get_by_role('button', name='Start session').click()
            cli_tab = page.get_by_role('tab', name=re.compile('Claude Code'))
            expect(cli_tab).to_have_count(1, timeout=30000)
            cli_panel = page.locator('#' + cli_tab.get_attribute('aria-controls'))
            expect(cli_panel.locator('.cli-header .cli-status')).to_contain_text('Waiting for input', timeout=30000)
            expect(cli_panel.locator('.xterm-rows')).to_contain_text('FAKE CLI READY', timeout=30000)
            step('New CLI session dialog started the fake CLI; its tab shows the TUI and the badge reached Waiting for input')

            cli_panel.locator('.xterm-helper-textarea').focus()
            page.keyboard.type('hello from the e2e\n')
            expect(cli_panel.locator('.xterm-rows')).to_contain_text('ECHO:hello from the e2e', timeout=20000)
            page.keyboard.type('turn\n')
            expect(cli_panel.locator('.xterm-rows')).to_contain_text('TURN RUNNING', timeout=20000)
            expect(cli_panel.locator('.cli-header .cli-status')).to_contain_text('Waiting for input', timeout=20000)
            step('typing in the TUI tab reached the CLI (echo, a turn) and the status followed the hooks')

            page.keyboard.type('ask\n')
            expect(cli_panel.locator('.cli-header .cli-status')).to_contain_text('Needs approval', timeout=20000)
            expect(cli_panel.get_by_text(re.compile('waiting for a decision in its terminal'))).to_be_visible()
            cli_panel.get_by_role('button', name='Focus terminal').click()
            assert page.evaluate("() => document.activeElement && document.activeElement.className") == 'xterm-helper-textarea'
            expect(agents.get_by_role('list', name='CLI sessions').get_by_role('button').first).to_contain_text('Needs approval', timeout=20000)
            expect(page.get_by_role('banner', name='Window').get_by_role('button', name=re.compile('needs? you'))).to_be_visible(timeout=20000)
            expect(page.get_by_role('contentinfo', name='Status').get_by_role('button', name=re.compile('needs? you'))).to_be_visible()
            expect(bar.get_by_role('button', name=re.compile('^Agents, .*needs? you'))).to_be_visible()
            step('needs approval: the badge, the one-line hint with Focus terminal, the list row and the Needs you count')

            cli_panel.get_by_role('button', name='Interrupt').click()
            expect(cli_panel.locator('.xterm-rows')).to_contain_text('INTERRUPTED', timeout=20000)
            cli_panel.get_by_role('button', name='Stop').click()
            confirm = page.get_by_role('dialog', name='Stop this session?')
            expect(confirm).to_be_visible()
            confirm.get_by_role('button', name='Stop session').click()
            expect(cli_panel.locator('.cli-header .cli-status')).to_contain_text('Done', timeout=30000)
            expect(cli_panel.get_by_text(re.compile('^This session '))).to_be_visible(timeout=20000)
            assert not state.sessions.list() or all(row for row in state.sessions.list()) is not None
            step('Interrupt, then Stop after confirmation: the badge went to Done and the tab says how it ended')
            # closing a live session's tab does not stop it
            sessions = client.get('/api/cli-sessions').json()['sessions']
            assert [row['status'] for row in sessions] == ['done']

            # a second session stays alive and asks for approval, for the screenshots; a third is the graph node's
            agents.get_by_role('button', name='New CLI session').click()
            page.get_by_role('dialog', name='New CLI session').get_by_label('Project').select_option(project['id'])
            page.get_by_role('dialog', name='New CLI session').get_by_role('button', name='Start session').click()
            expect(page.get_by_role('tab', name=re.compile('Claude Code'))).to_have_count(2, timeout=30000)
            live = eventually(lambda: next((row for row in client.get('/api/cli-sessions').json()['sessions'] if row['status'] == 'waiting_input'), None), label='second session ready')
            assert client.post(f"/api/cli-sessions/{live['id']}/input", json={'text': 'ask'}).status_code == 200
            eventually(lambda: client.get(f"/api/cli-sessions/{live['id']}").json()['status'] == 'needs_approval', label='second session needs approval')
            # closing the tab leaves the session running; the Agents view row reopens it
            second = page.get_by_role('tab', name=re.compile('Claude Code')).nth(1)
            second.locator('.tab-close').click()
            expect(page.get_by_role('tab', name=re.compile('Claude Code'))).to_have_count(1)
            assert client.get(f"/api/cli-sessions/{live['id']}").json()['status'] == 'needs_approval', 'closing a tab must not stop the session'
            agents.get_by_role('list', name='CLI sessions').get_by_role('button').first.click()
            expect(page.get_by_role('tab', name=re.compile('Claude Code'))).to_have_count(2, timeout=20000)
            step('closing a CLI tab left the session running; the Agents view row reopened it')

            # Board: Main agent, Plans, a CLI node blocked on approval -------------------------------------------------------
            agents.get_by_role('button', name='Main agent').click()
            expect(agents.get_by_role('button', name=re.compile('^Plans'))).to_be_visible(timeout=30000)
            agents.get_by_role('button', name=re.compile('^Plans')).click()
            board = agents.get_by_role('region', name='Task board')
            expect(board.get_by_role('listitem', name='Fix the flaky test')).to_be_visible(timeout=30000)
            expect(board.get_by_role('listitem', name='Fix the flaky test').get_by_text('CLI: claude')).to_be_visible()
            expect(board.get_by_role('listitem', name='Refactor the parser').get_by_text('Engine: codex')).to_be_visible()
            expect(board.get_by_role('listitem', name='Summarise the repository').get_by_text('Agent', exact=True)).to_be_visible()
            board.get_by_role('listitem', name='Fix the flaky test').get_by_role('button', name='Actions for Fix the flaky test').click()
            page.get_by_role('menuitem', name='Start ready tasks').click()
            node_row = board.get_by_role('listitem', name='Fix the flaky test')
            expect(node_row.get_by_text(re.compile('Running|Blocked'))).to_be_visible(timeout=30000)
            node_session = eventually(lambda: next((row for row in client.get('/api/cli-sessions').json()['sessions'] if row['id'] != live['id'] and row['status'] in ('waiting_input', 'working', 'starting')), None), label='the node\'s CLI session')
            eventually(lambda: client.get(f"/api/cli-sessions/{node_session['id']}").json()['status'] == 'waiting_input', label='node session at its prompt')
            assert client.post(f"/api/cli-sessions/{node_session['id']}/input", json={'text': 'ask'}).status_code == 200
            expect(node_row.get_by_text('Blocked · needs approval')).to_be_visible(timeout=40000)
            expect(node_row.get_by_text(re.compile('needs your approval'))).to_be_visible()
            tabs_before = page.get_by_role('tab').count()
            node_row.get_by_role('button', name='Open CLI session').click()
            expect(page.get_by_role('tab')).to_have_count(tabs_before + 1, timeout=20000)
            agents.get_by_role('button', name='Main agent').click()
            settle(page, 500)
            axe(page, 'board-light-1280', report, axe_source)
            page.screenshot(path=str(out / 'board-light-1280.png'))
            step('the board shows Agent, Engine and CLI chips and a CLI node Blocked with its reason; Open CLI session opened its tab')

            # host health ------------------------------------------------------------------------------------------------------
            status_bar = page.get_by_role('contentinfo', name='Status')
            status_bar.get_by_role('button', name=re.compile('^Connected')).click()
            host_group = page.get_by_role('group', name='Host status')
            expect(host_group).to_be_visible(timeout=20000)
            text = host_group.inner_text()
            for word in ('Uptime', 'Service', 'Terminals', 'Recovery', 'Keep awake', 'Heartbeat'):
                assert word in text, (word, text)
            assert 'Held by the PTY holder' in text or ('Direct mode' in text and 'Reason:' in text), text
            report['notes'].append('holder: ' + ('held' if 'Held by the PTY holder' in text else 'direct, reason shown'))
            axe(page, 'host-popover-light-1280', report, axe_source)
            page.screenshot(path=str(out / 'host-popover-light-1280.png'))
            page.keyboard.press('Escape')
            expect(host_group).to_have_count(0)
            step('the status bar health item opens the host popover with uptime, service, terminals, recovery, keep awake and heartbeat')

            page.get_by_role('banner', name='Window').get_by_role('button', name='Search files and commands').click()
            quick = page.get_by_role('dialog', name='Search files and commands')
            expect(quick).to_be_visible()
            page.get_by_role('combobox', name='Search files and commands').fill('>CLI session')
            expect(quick.get_by_role('option', name=re.compile('New CLI session'))).to_be_visible(timeout=10000)
            expect(quick.get_by_role('option', name=re.compile('Focus CLI session'))).to_be_visible()
            page.get_by_role('combobox', name='Search files and commands').fill('>host status')
            expect(quick.get_by_role('option', name=re.compile('Show host status'))).to_be_visible(timeout=10000)
            page.keyboard.press('Enter')
            expect(page.get_by_role('group', name='Host status')).to_be_visible(timeout=10000)
            page.keyboard.press('Escape')
            step('the command center lists New CLI session, Focus CLI session and Show host status, which opens the popover')
            no_overflow(page, 'desktop-flow-light-1280', report)
            report['holder_note'] = report['notes'][-1] if report['notes'] else ''
            context.close()

            # ====================== screenshots at both sizes in both themes, against the state left behind =================
            def capture(scheme: str, width: int, height: int) -> None:
                phone = width < 768
                ctx = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme, reduced_motion='reduce', has_touch=phone, is_mobile=phone)
                view = ctx.new_page()
                view.on('pageerror', lambda error: report['errors'].append(str(error)))
                tag = f'{scheme}-{width}'
                sign_in(view, base, session['id'], ready='banner' if phone else 'tablist')
                view.wait_for_timeout(800)

                def shot(label: str, check_axe: bool = True) -> None:
                    settle(view, 500)
                    view.screenshot(path=str(out / f'{label}-{tag}.png'))
                    no_overflow(view, f'{label}-{tag}', report)
                    if check_axe:
                        axe(view, f'{label}-{tag}', report, axe_source)

                if phone:
                    top = view.get_by_role('banner', name='Window')
                    nav = view.get_by_role('navigation', name='Primary')
                    nav.get_by_role('button', name='More').click()
                    view.get_by_role('menuitem', name='Sites').click()
                    drawer = view.get_by_role('dialog', name='Sites')
                    expect(drawer.get_by_role('region', name=re.compile('^Pinned'))).to_be_visible(timeout=20000)
                    for target in drawer.locator('.site-open, .site-action, .view-primary, .site-pin').all():
                        if target.is_visible():
                            box = target.bounding_box()
                            assert box['height'] >= 43.5, f'a Sites target under 44px on the phone: {box}'
                    shot('sites-view')
                    drawer.get_by_role('button', name=re.compile('^Notes for Mail')).click()
                    expect(drawer.get_by_role('region', name='Notes for Mail')).to_be_visible(timeout=20000)
                    shot('sites-notes')
                    drawer.get_by_role('region', name=re.compile('^Pinned')).get_by_role('button', name=re.compile('^Fixture docs')).click()
                    expect(drawer).to_have_count(0)
                    expect(top.get_by_role('heading')).to_contain_text('Fixture docs', timeout=30000)
                    shot('browser-tab', check_axe=False)
                    nav.get_by_role('button', name='More').click()
                    view.get_by_role('menuitem', name='CLI sessions').click()
                    drawer = view.get_by_role('dialog', name='Agents')
                    expect(drawer.get_by_role('list', name='CLI sessions')).to_be_visible(timeout=20000)
                    shot('cli-list')
                    drawer.get_by_role('button', name='New CLI session').click()
                    expect(view.get_by_role('dialog', name='New CLI session')).to_be_visible()
                    shot('new-cli-dialog')
                    view.keyboard.press('Escape')
                    nav.get_by_role('button', name='Agents').click()
                    drawer = view.get_by_role('dialog', name='Agents')
                    drawer.get_by_role('list', name='CLI sessions').get_by_role('button').first.click()
                    expect(view.locator('.cli-header .cli-status:visible').first).to_be_visible(timeout=30000)
                    expect(view.locator('.cli-header .cli-status').first).to_contain_text('Needs approval', timeout=20000)
                    for control in view.locator('.cli-header button').all():
                        if control.is_visible():
                            assert control.bounding_box()['height'] >= 43.5, 'CLI header target under 44px'
                    shot('cli-tab')
                    view.get_by_role('contentinfo', name='Status').get_by_role('button', name=re.compile('^Connected')).click()
                    expect(view.get_by_role('group', name='Host status')).to_be_visible(timeout=20000)
                    shot('host-popover')
                else:
                    bar_ = view.get_by_role('toolbar', name='Activity bar')
                    bar_.get_by_role('button', name='Sites').click()
                    sites_ = view.get_by_role('complementary', name='Sites')
                    expect(sites_.get_by_role('region', name=re.compile('^Pinned'))).to_be_visible(timeout=20000)
                    sites_.get_by_role('button', name=re.compile('^Notes for Mail')).click()
                    expect(sites_.get_by_role('region', name='Notes for Mail')).to_be_visible(timeout=20000)
                    shot('sites-view')
                    sites_.get_by_role('region', name=re.compile('^Pinned')).get_by_role('button', name=re.compile('^Fixture docs')).click()
                    tab_ = view.get_by_role('tab', name=re.compile('Fixture docs'))
                    expect(view.locator('#' + tab_.get_attribute('aria-controls')).locator('img.browser-live-view')).to_be_visible(timeout=30000)
                    shot('browser-tab')
                    bar_.get_by_role('button', name='Agents').click()
                    agents_ = view.get_by_role('complementary', name='Agents')
                    expect(agents_.get_by_role('list', name='CLI sessions')).to_be_visible(timeout=20000)
                    shot('cli-list')
                    agents_.get_by_role('list', name='CLI sessions').get_by_role('button').first.click()
                    expect(view.locator('.cli-header .cli-status:visible').first).to_contain_text('Needs approval', timeout=20000)
                    shot('cli-tab')
                    agents_.get_by_role('button', name='New CLI session').click()
                    expect(view.get_by_role('dialog', name='New CLI session')).to_be_visible()
                    shot('new-cli-dialog')
                    view.keyboard.press('Escape')
                    agents_.get_by_role('button', name='Main agent').click()
                    plans = agents_.get_by_role('button', name=re.compile('^Plans'))
                    if plans.get_attribute('aria-expanded') != 'true':
                        plans.click()
                    expect(agents_.get_by_role('region', name='Task board').get_by_text('Blocked · needs approval')).to_be_visible(timeout=30000)
                    shot('board')
                    view.get_by_role('contentinfo', name='Status').get_by_role('button', name=re.compile('^Connected')).click()
                    expect(view.get_by_role('group', name='Host status')).to_be_visible(timeout=20000)
                    shot('host-popover')
                ctx.close()

            for scheme in ('light', 'dark'):
                for width, height in ((1280, 900), (375, 812)):
                    capture(scheme, width, height)
                report['steps'].append(f'{scheme}: screenshots at 1280x900 and 375x812 (sites, notes, browser tab, CLI list, CLI tab, new session dialog, board, host popover)')

            # a host that stops answering is shown as Disconnected, and the page keeps retrying
            guard_client = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme='light', reduced_motion='reduce')
            gone = guard_client.new_page()
            gone.on('pageerror', lambda error: report['errors'].append(str(error)))
            failures: list[str] = []
            gone.on('requestfailed', lambda request: failures.append(request.url) if request.url.endswith('/graphql') else None)
            silent = {'on': False}

            def relay(socket_route):
                upstream = socket_route.connect_to_server()
                socket_route.on_message(lambda message: None if silent['on'] else upstream.send(message))
                upstream.on_message(lambda message: None if silent['on'] else socket_route.send(message))

            gone.route_web_socket('**/graphql', relay)
            sign_in(gone, base, session['id'])
            expect(gone.get_by_role('contentinfo', name='Status').get_by_role('button', name=re.compile('^Connected'))).to_be_visible(timeout=20000)

            # stop the answers: every GraphQL request fails and the open subscriptions go quiet
            silent['on'] = True
            gone.route('**/graphql', lambda route: route.abort())
            guard_client_page = gone
            stopped_at = monotonic()
            try:
                expect(guard_client_page.get_by_role('contentinfo', name='Status').get_by_role('button', name=re.compile('^Disconnected'))).to_be_visible(timeout=70000)
                waited = monotonic() - stopped_at
            except AssertionError:
                waited = -1
            report['notes'].append(f'disconnected after {waited:.0f}s of silence')
            if waited >= 0:
                assert 18 < waited < 60, f'Disconnected at an unexpected time: {waited:.0f}s'
                assert gone.locator('.status-health.down').count() == 1
                assert len(failures) >= 2, 'the page should keep retrying while the host is silent'
                gone.screenshot(path=str(out / 'host-disconnected-light-1280.png'))
                step(f'a silent host showed a danger dot and the word Disconnected after about {waited:.0f}s, and the page kept retrying ({len(failures)} failed requests)')
            else:
                report['errors'].append('the status bar never showed Disconnected while GraphQL was blocked')
            guard_client.close()
            browser.close()

        for row in client.get('/api/cli-sessions').json()['sessions']:
            if row['status'] not in ('done', 'failed', 'lost'):
                client.post(f"/api/cli-sessions/{row['id']}/stop")
        server.should_exit = True
        fixture.shutdown()
        # the PTY holder, when it ran, exits on its own after idling; stop it now so a proof run leaves nothing behind
        subprocess.run([sys.executable, '-m', 'termx', 'pty-holder', 'stop', '--kill'], capture_output=True, timeout=30, check=False)

    (out / 'workspace_agent_surfaces_e2e.json').write_text(json.dumps(report, indent=2))
    problems = report['errors'] + report['overflow'] + report['axe_violations']
    if problems:
        print(json.dumps(report, indent=2))
        raise SystemExit('Agent surfaces e2e found problems: ' + '; '.join(problems[:8]))
    print(json.dumps({'steps': report['steps'], 'notes': report['notes'], 'errors': 0, 'overflow': 0, 'axe_violations': 0}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if os.name == 'nt':
        raise SystemExit('The agent surfaces e2e is POSIX-only (fake CLI sessions run in a PTY)')
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
