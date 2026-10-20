"""Real-host chat, password-session, and responsive-control regression proof.

Build desktop/workspace first, then run:
  TERMX_TEST_CHROMIUM=/path/to/chromium python tests/workspace_chat_recovery_e2e.py OUTPUT_DIR

All identities, providers, task records, and capabilities are temporary fixtures.
No agent process is started and no inference/provider request is made. The only
simulated engine data is an explicit, offline model-capability catalogue; HTTP,
GraphQL, authentication, session mutations, and saved task history stay real.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep, time
from urllib.parse import urlparse

import uvicorn
from playwright.sync_api import expect, sync_playwright
from workspace_ui_snapshot import snapshot_ui


ANSWER = '''## Session recovery is ready

The workspace keeps your **local account** connected while an agent recovers.

- A tool failure belongs to its task.
- Agent settings stay fixed during a run.
- Follow-ups remain in the queue.

| Check | Result |
| --- | --- |
| Password session | Preserved |
| Agent recovery | Verified |

```python
def keep_session(error):
    return error.status != 401
```

Open **View activity** to inspect the tools and delegated agents.
'''


def wait_until(check, message, timeout=10, page=None):
    deadline = monotonic() + timeout
    while not check():
        if monotonic() >= deadline:
            raise AssertionError(message)
        # Browser route callbacks need the Playwright dispatcher to keep
        # running while a real HTTP mutation updates the fixture stores.
        if page is None: sleep(.04)
        else: page.wait_for_timeout(40)


def run(destination):
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'checks': [], 'viewports': [], 'axe': [], 'errors': [], 'task_reads': [],
              'provider_requests': [], 'fixture': 'real password auth and stores; offline capabilities only'}
    with tempfile.TemporaryDirectory(prefix='termx-chat-proof-') as temporary:
        root = Path(temporary)
        os.environ.update(TERMX_CONFIG_DIR=str(root/'config'), TERMX_AGENTS_DIR=str(root/'agents'), TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState, create_app
        from termx.agent.secrets import CredentialStore
        from termx.agent.limits import DEFAULT_LIMITS
        state = AppState(passcode=None, credentials=CredentialStore(memory={}))
        owner = state.identity.setup_owner('chat-fixture', 'chat-fixture-password-123')
        project = state.projects.register(str(root), name='TermX workspace')
        state.agent_store.put_provider('offline-fixture', kind='openai-compatible', name='Offline fixture account',
                                      base_url='http://127.0.0.1:1/v1', model='fixture-fast,fixture-deep',
                                      capabilities=['chat'], secret_configured=False)
        # A recorded capability response, not a replacement engine or a request
        # interceptor. Real reasoning validation and PATCH handlers consume it.
        effort = {'id': 'reasoning_effort', 'name': 'Reasoning effort', 'category': 'thought_level', 'type': 'select',
                  'currentValue': 'medium', 'options': [{'value': value, 'name': value.title()} for value in ['low', 'medium', 'high']]}
        configuration = {'models': ['fixture-fast', 'fixture-deep'], 'default_model': 'fixture-fast',
                         'config_options': [effort], 'model_configurations': {model: {'config_options': [effort]} for model in ['fixture-fast', 'fixture-deep']}}
        descriptor = state.engines.adapter('codex').descriptor().as_dict()
        # Advertise the offline catalogue as selectable. Nothing launches this
        # engine: the proof only edits persisted next-turn configuration.
        descriptor['installed'] = True
        descriptor['capabilities'] = state.engines.adapter('codex').capabilities().as_dict()
        descriptor['capabilities']['models'] = configuration['models']
        state.engines.catalogue.entries['codex'] = {'descriptor': descriptor, 'configuration': configuration,
                                                   'refreshed_at': time(), 'stale': False, 'refresh_error': None}
        app = create_app(state, web_dir=snapshot_ui(root, Path(__file__).resolve().parents[1]/'desktop/workspace/dist'))
        conversation = state.workspace.create_session(owner, title='Keep the conversation going', project_id=project['id'],
                                                      cwd=str(root), provider_id='offline-fixture', model='fixture-fast', mode='agent')
        native = state.workspace.create_session(owner, title='Reasoning control fixture', project_id=project['id'],
                                                cwd=str(root), engine='codex', model='fixture-fast', mode='ask')
        active_conversation = state.workspace.create_session(owner, title='Agent in progress', project_id=project['id'],
                                                             cwd=str(root), provider_id='offline-fixture', model='fixture-fast', mode='agent')

        def task(session, prompt, result='', parent=None, name=None):
            row = state.agent_store.create_task(prompt=prompt, cwd=str(root), provider_id='offline-fixture', model='fixture-fast',
                                                limits=DEFAULT_LIMITS, mode='agent', parent_id=parent, status='completed')
            state.agent_store.update_task(row['id'], result=result)
            state.workspace.store.create('task', owner.id, {'conversation_id': session['id'], 'cwd': str(root),
                                         'execution_location': str(root), 'parent_id': parent, 'agent_label': name, 'delegated_task': prompt}, project['id'], row['id'])
            state.authorization.claim_principal(owner, 'task', row['id'], project_id=project['id'])
            if not parent:
                state.agent_store.add_conversation_turn(session['id'], prompt=prompt, task_id=row['id'], mode='agent',
                                                       provider_id='offline-fixture', model='fixture-fast')
            return row

        completed = task(conversation, 'Review the session recovery and make the chat easier to use.', ANSWER)
        child = task(conversation, 'Review local-password session renewal.', 'Session renewal and task errors remain separate.', completed['id'], 'Auth reviewer')
        grandchild = task(conversation, 'Check the session guard.', 'The guard preserves an authenticated account.', child['id'], 'Guard reviewer')
        for row, path in [(completed, 'workspace/session.py'), (child, 'identity/session.py'), (grandchild, 'identity/guard.py')]:
            call = 'read-'+row['id']
            state.agent_store.append_event(row['id'], 'tool.started', {'call': {'call_id': call, 'name': 'read_file', 'arguments': {'path': path}}})
            state.agent_store.append_event(row['id'], 'tool.finished', {'call_id': call, 'result': {'ok': True, 'output': 'Reviewed '+path}})
        for parent, delegated, name in [(completed, child, 'Auth reviewer'), (child, grandchild, 'Guard reviewer')]:
            state.agent_store.append_event(parent['id'], 'subagent.started', {'child_id': delegated['id'], 'agent': name, 'task': delegated['prompt']})
            state.agent_store.append_event(parent['id'], 'subagent.finished', {'child_id': delegated['id'], 'agent': name, 'status': 'completed', 'result': state.agent_store.get_task(delegated['id'])['result']})
        state.agent_store.append_event(completed['id'], 'engine.tool.started', {'toolCallId': 'acp-inspect', 'title': 'Inspect ACP session', 'rawInput': {'scope': 'conversation'}, 'status': 'in_progress'})
        state.agent_store.append_event(completed['id'], 'engine.tool.progress', {'toolCallId': 'acp-inspect', 'status': 'completed', 'rawOutput': {'text': 'Session binding is intact.'}})
        state.agent_store.append_event(completed['id'], 'task.completed', {'result': ANSWER})
        task(native, 'Keep reasoning choices simple.', 'Choose a model, then use the effort slider for the next turn.')
        running = task(active_conversation, 'Inspect the agent while it is working.')
        report['asset_count'] = len(json.loads((root/'fixture-ui-snapshot.json').read_text()))
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on', access_log=False, proxy_headers=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True); worker.start()
        wait_until(lambda: server.started, 'Fixture host did not start', 25)
        # Created after lifecycle recovery so this is a presentation fixture,
        # never a real engine process that recovery might attempt to resume.
        state.agent_store.update_task(running['id'], status='running', result='')
        state.agent_store.append_event(running['id'], 'tool.started', {'call': {'call_id': 'pending-check', 'name': 'run_check', 'arguments': {'command': 'fixture check'}}})
        base = f'http://127.0.0.1:{port}'
        page = browser = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('TERMX_TEST_CHROMIUM') or shutil.which('chromium'), headless=True)
                try:
                    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                    def network(route):
                        if route.request.url.startswith(base+'/'):
                            route.continue_()
                        else:
                            report['provider_requests'].append(route.request.url)
                            route.abort()
                    context.route('**/*', network)
                    page = context.new_page(); page.set_default_timeout(12000)
                    page.on('pageerror', lambda error: report['errors'].append(str(error)))
                    def request_seen(request):
                        path = urlparse(request.url).path
                        if request.method == 'GET' and path == '/api/workspace/tasks/'+completed['id']:
                            report['task_reads'].append(path)
                    page.on('request', request_seen)
                    refresh_responses = []
                    page.on('response', lambda response: refresh_responses.append(response.status) if urlparse(response.url).path == '/auth/refresh' else None)
                    page.goto(base+'/?session='+conversation['id'])
                    page.get_by_label('Username', exact=True).fill('chat-fixture')
                    page.get_by_label('Password', exact=True).fill('chat-fixture-password-123')
                    page.get_by_role('button', name='Continue with password', exact=True).click()
                    composer = page.get_by_role('textbox', name='Message', exact=True)
                    expect(composer).to_be_enabled(timeout=25000)
                    page.get_by_role('navigation', name='Workspace view').get_by_role('button', name='Chat', exact=True).click()
                    expect(page.get_by_role('heading', name='Session recovery is ready')).to_be_visible()
                    page.get_by_role('log').evaluate('(node)=>node.scrollTop=0')
                    page.screenshot(path=str(output/'first-desktop-dark.png'))
                    print(json.dumps({'screenshot': str(output/'first-desktop-dark.png'), 'stage': 'real password login and saved response rendered'}), flush=True)
                    assert not report['task_reads'], 'Completed activity fetched before it was expanded'
                    expect(page.locator('.message-response table')).to_have_count(1)
                    expect(page.locator('.message-response [data-streamdown="code-block"]')).to_have_count(1)
                    expect(page.locator('.message-response pre')).to_contain_text('def keep_session(error):')
                    activity = page.get_by_role('button', name='View activity', exact=False)
                    activity.click()
                    expect(page.get_by_role('heading', name='Agent team')).to_be_visible()
                    assert len(report['task_reads']) == 1
                    page.get_by_role('button', name='Auth reviewer', exact=False).click()
                    expect(page.get_by_text('Session renewal and task errors remain separate.', exact=True)).to_be_visible()
                    page.get_by_role('button', name='Guard reviewer', exact=False).click()
                    expect(page.get_by_text('The guard preserves an authenticated account.', exact=True)).to_be_visible()
                    page.screenshot(path=str(output/'saved-nested-activity-dark.png'))
                    page.get_by_role('button', name='Read file · identity/guard.py', exact=False).click()
                    expect(page.get_by_label('Read file · identity/guard.py result')).to_be_visible()
                    activity.click()
                    report['checks'].append('Completed history loads once on expansion; nested persisted agents and tool results render')

                    # Real model, mode and run-style mutations; no inference dispatch.
                    page.get_by_role('button', name='Model and effort', exact=True).click()
                    dialog = page.get_by_role('dialog', name='Model and effort', exact=True)
                    dialog.get_by_label('Session model', exact=True).select_option('fixture-deep')
                    expect(dialog.get_by_label('Session model', exact=True)).to_have_value('fixture-deep')
                    assert state.workspace.session(owner, conversation['id'], turns=False)['model'] == 'fixture-deep'
                    dialog.get_by_role('button', name='Close', exact=True).click()
                    page.get_by_role('button', name='Session mode', exact=True).click()
                    page.screenshot(path=str(output/'mode-desktop-dark.png'))
                    page.get_by_role('dialog', name='Choose a mode', exact=True).get_by_text('Ask', exact=True).click()
                    expect(page.get_by_role('button', name='Session mode', exact=True)).to_contain_text('Ask')
                    assert state.workspace.session(owner, conversation['id'], turns=False)['mode'] == 'ask'
                    page.get_by_role('button', name='Session options', exact=True).click()
                    settings = page.get_by_role('dialog', name='Session settings', exact=True)
                    settings.get_by_text('Extended', exact=True).click()
                    expect(settings.get_by_role('radio', name='Extended', exact=False)).to_be_checked()
                    assert state.workspace.session(owner, conversation['id'], turns=False)['run_limits']['max_steps'] == 1024
                    settings.get_by_label('Session engine', exact=True).select_option('codex')
                    fork = page.get_by_role('dialog', name='Continue with codex', exact=True)
                    expect(fork).to_be_visible(); fork.get_by_role('button', name='Close', exact=True).click()
                    assert state.workspace.session(owner, conversation['id'], turns=False)['engine'] == 'internal'
                    report['checks'].append('Model, mode and profile choices persist; engine switch opens reviewable linked-fork flow')

                    # Actual appearance settings, never an injected class or stylesheet.
                    def set_theme(theme):
                        if page.locator('html').get_attribute('data-theme') == theme:
                            return
                        page.get_by_role('button', name='Workspace actions', exact=True).click()
                        page.get_by_role('button', name='Appearance', exact=True).click()
                        preferences = page.get_by_role('dialog', name='Settings', exact=True)
                        preferences.get_by_role('navigation', name='Settings categories').get_by_role('button', name='Appearance', exact=True).click()
                        preferences.get_by_role('radio', name=theme.title(), exact=True).check()
                        preferences.get_by_role('button', name='Close', exact=True).click()
                        expect(page.locator('html')).to_have_attribute('data-theme', theme)

                    axe = Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                    def audit(surface, selector, width, theme):
                        page.add_script_tag(path=str(axe))
                        failures = page.evaluate('''async({selector})=>{const r=await axe.run(document.querySelector(selector),{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa"]}});return r.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))}''', {'selector': selector})
                        report['axe'].append({'surface': surface, 'width': width, 'theme': theme, 'violations': failures})
                        assert not failures, failures

                    def bounds(width, height):
                        values = page.locator('.chat-region').evaluate('''node=>{const rect=n=>{const b=n.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,right:b.right,bottom:b.bottom}};const controls=[...node.querySelectorAll('.composer-controls button')].map(n=>({name:n.getAttribute('aria-label'),...rect(n),covered:!n.contains(document.elementFromPoint(n.getBoundingClientRect().x+n.clientWidth/2,n.getBoundingClientRect().y+n.clientHeight/2))}));return {region:rect(node),composer:rect(node.querySelector('.composer')),draftBody:rect(node.querySelector('.composer-draft-body')||node.querySelector('textarea[aria-label="Message"]')),controlRow:rect(node.querySelector('.composer-controls')),toolbar:rect(node.querySelector('.conversation-surface-header')),controls,overflow:node.querySelector('.composer-controls').scrollWidth>node.querySelector('.composer-controls').clientWidth,pageOverflow:document.documentElement.scrollWidth>innerWidth}}''')
                        assert not values['overflow'] and not values['pageOverflow'], values
                        for box in [values['composer'], values['toolbar'], *values['controls']]:
                            assert box['x'] >= -1 and box['right'] <= width+1 and box['y'] >= -1 and box['bottom'] <= height+1, box
                        assert not any(control['covered'] for control in values['controls']), values
                        assert values['draftBody']['height'] >= 24 and values['draftBody']['bottom'] <= values['controlRow']['y'], values
                        assert values['controlRow']['bottom'] <= values['composer']['bottom']-6, values
                        assert values['composer']['bottom'] <= values['region']['bottom'], values
                        return values

                    for width, height in [(1440, 1000), (390, 844), (320, 740), (390, 420)]:
                        page.set_viewport_size({'width': width, 'height': height})
                        for theme in ['dark', 'light']:
                            set_theme(theme)
                            hide = page.get_by_role('navigation', name='Workspace navigation', exact=True).get_by_role('button', name='Hide sidebar', exact=True)
                            if width <= 850 and hide.count() and hide.is_visible(): hide.click()
                            composer.fill('Keep this draft while changing models and settings.\nThe next message stays right here.')
                            values = bounds(width, height)
                            page.screenshot(path=str(output/f'chat-{width}x{height}-{theme}.png'))
                            report['viewports'].append({'width': width, 'height': height, 'theme': theme, **values})
                            if (width, height) in [(1440, 1000), (390, 844)]: audit('chat', '.chat-region', width, theme)
                            page.get_by_role('button', name='Session options', exact=True).click()
                            settings = page.get_by_role('dialog', name='Session settings', exact=True)
                            box = settings.bounding_box()
                            assert box and box['x'] >= -1 and box['x']+box['width'] <= width+1 and box['y'] >= -1 and box['y']+box['height'] <= height+1, box
                            for key in ['Tab', 'Shift+Tab']*3:
                                page.keyboard.press(key)
                                assert settings.evaluate('(node)=>node.contains(document.activeElement)')
                            page.screenshot(path=str(output/f'settings-{width}x{height}-{theme}.png'))
                            if (width, height) in [(1440, 1000), (390, 844)]: audit('session settings', '.session-settings-dialog', width, theme)
                            page.keyboard.press('Escape'); expect(settings).not_to_be_visible()
                            expect(page.get_by_role('button', name='Session options', exact=True)).to_be_focused()
                            assert composer.input_value().startswith('Keep this draft')
                            page.get_by_role('button', name='Model and effort', exact=True).click()
                            model_dialog = page.get_by_role('dialog', name='Model and effort', exact=True)
                            box = model_dialog.bounding_box()
                            assert box and box['x'] >= -1 and box['x']+box['width'] <= width+1 and box['y'] >= -1 and box['y']+box['height'] <= height+1, box
                            page.screenshot(path=str(output/f'model-{width}x{height}-{theme}.png'))
                            if (width, height) in [(1440, 1000), (390, 844)]: audit('model settings', '.session-model-settings', width, theme)
                            page.keyboard.press('Escape')
                            print(json.dumps({'stage': 'responsive controls verified', 'width': width, 'height': height, 'theme': theme}), flush=True)
                    report['checks'].append('Composer and dialogs fit desktop, 390px, 320px and short keyboard-like viewports in both themes')

                    # Remove only the access cookie. Keep the real refresh family,
                    # CSRF token and saved draft, then exercise ordinary app startup.
                    preserved_draft = composer.input_value()
                    wait_until(lambda: state.workspace.session(owner, conversation['id'], turns=False)['draft_text'] == preserved_draft, 'Fixture draft did not persist before refresh', page=page)
                    assert any(cookie['name'] == 'termx_refresh' for cookie in context.cookies())
                    refresh_responses.clear()
                    context.clear_cookies(name='termx_access')
                    assert not any(cookie['name'] == 'termx_access' for cookie in context.cookies())
                    page.reload()
                    composer = page.get_by_role('textbox', name='Message', exact=True)
                    expect(composer).to_be_enabled(timeout=20000)
                    expect(composer).to_have_value(preserved_draft)
                    expect(page.locator('.breadcrumb')).to_contain_text('Keep the conversation going')
                    assert 200 in refresh_responses, refresh_responses
                    me = page.request.get(base+'/auth/me')
                    assert me.ok and me.json()['principal']['id'] == owner.id
                    report['refresh_recovery'] = {'refresh_statuses': refresh_responses, 'same_principal': True, 'same_conversation': True, 'draft_preserved': True}
                    report['checks'].append('Expired access cookie refreshes through real auth flow; account, conversation and draft survive reload')

                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.goto(base+'/?session='+native['id'])
                    expect(page.get_by_role('textbox', name='Message', exact=True)).to_be_enabled()
                    page.get_by_role('button', name='Model and effort', exact=True).click()
                    dialog = page.get_by_role('dialog', name='Model and effort', exact=True)
                    slider = dialog.get_by_role('slider', name='Reasoning effort', exact=True)
                    expect(slider).to_be_enabled()
                    # A concurrent metadata save advances the revision while the
                    # native model dialog remains open. The slider must refresh it.
                    row = state.workspace.session(owner, native['id'], turns=False)
                    state.workspace.update_session(owner, native['id'], revision=row['revision'], changes={'draft_text': 'Preserved native draft'})
                    slider.focus(); page.keyboard.press('End')
                    expect(slider).to_have_attribute('aria-valuetext', 'High')
                    wait_until(lambda: state.workspace.session(owner, native['id'], turns=False).get('reasoning_config', {}).get('reasoning_effort') == 'high', 'Effort was not persisted with the current revision', page=page)
                    expect(slider).to_be_enabled()
                    expect(slider).to_have_attribute('aria-valuetext', 'High')
                    audit('effort slider', '.session-model-settings', 390, 'light')
                    page.screenshot(path=str(output/'native-effort-390-light.png'))
                    page.keyboard.press('Escape')
                    report['checks'].append('Advertised native effort slider persists using a refreshed canonical revision')

                    page.goto(base+'/?session='+active_conversation['id'])
                    expect(page.get_by_role('button', name='Stop task', exact=True)).to_be_visible()
                    expect(page.get_by_text('Preparing a response', exact=True)).to_be_visible()
                    expect(page.locator('.response-skeleton')).to_have_count(1)
                    page.screenshot(path=str(output/'working-loader-390-light.png'))
                    page.get_by_role('button', name='Session options', exact=True).click()
                    settings = page.get_by_role('dialog', name='Session settings', exact=True)
                    expect(settings.get_by_label('Session engine', exact=True)).to_be_disabled()
                    for radio in settings.get_by_role('radio').all(): expect(radio).to_be_disabled()
                    page.keyboard.press('Escape')
                    report['checks'].append('Active task loader and stop action render; execution settings remain immutable')
                    assert not report['errors'], report['errors']
                    assert not report['provider_requests'], report['provider_requests']
                    assert len(state.agent_store.list_tasks()) == 5, 'The proof unexpectedly dispatched another task'
                    report['checks'].append('Local password session remains authenticated; no inference or new task dispatch')
                    report['status'] = 'passed'
                    for name in ['failure.png','failure.html']:(output/name).unlink(missing_ok=True)
                except BaseException:
                    if page and not page.is_closed():
                        page.screenshot(path=str(output/'failure.png'))
                        (output/'failure.html').write_text(page.content())
                    raise
                browser.close()
        except BaseException as error:
            report['status'] = 'failed'
            report['failure'] = str(error)
            if page:
                try:
                    if not page.is_closed():
                        page.screenshot(path=str(output/'failure.png'))
                        (output/'failure.html').write_text(page.content())
                except Exception:
                    pass
            raise
        finally:
            server.should_exit = True; worker.join(timeout=15)
            (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    result=run(sys.argv[1])
    print(json.dumps({'status':result['status'],'checks':result['checks'],'viewport_checks':len(result['viewports']),
                      'axe_audits':len(result['axe']),'refresh_recovery':result.get('refresh_recovery'),
                      'page_errors':result['errors'],'external_requests':result['provider_requests']},indent=2))
