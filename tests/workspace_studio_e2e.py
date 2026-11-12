"""Opt-in rendered media-studio proof against an isolated real workspace host.

Run: uv run --extra media python tests/workspace_studio_e2e.py OUTPUT_DIR
Build desktop/workspace/dist first. Provider jobs are browser fixtures; no paid
provider requests, native interactions, or default configuration stores are used.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import threading
from time import monotonic, sleep, time
from urllib.parse import parse_qs, urlsplit

import uvicorn
import httpx
from playwright.sync_api import expect, sync_playwright


def _exercise(page, context, base, session, project, owner, state, destination, report, theme, width, axe):
    """Real auth/host/catalog/export plus explicitly mocked configured-provider jobs."""
    from PIL import Image

    project_id = project['id']
    fixture = {'enabled': False, 'catalog': None, 'jobs': [], 'complete': False,
               'submissions': [], 'refreshes': [], 'requests': []}
    origin = urlsplit(base)

    def handle(route):
        request = route.request
        url = urlsplit(request.url)
        if url.scheme in ('http', 'https') and (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
            report['external_browser_requests'].append({'method': request.method, 'url': request.url})
            route.abort('blockedbyclient')
            return
        if not url.path.startswith('/api/media/studio/'):
            route.continue_()
            return
        fixture['requests'].append({'method': request.method, 'path': url.path})
        if not fixture['enabled']:
            # Only read the real no-account catalog/history. Never let a browser
            # accidentally reach a real submission or provider refresh handler.
            assert request.method == 'GET', ('Unexpected unconfigured mutation', request.url)
            route.continue_()
            return
        if request.method == 'GET' and url.path in ('/api/media/studio/catalog', '/api/media/studio/jobs'):
            scope = parse_qs(url.query).get('project_id')
            # App restores the selected conversation asynchronously after reload;
            # its brief personal-workspace read must not receive project jobs.
            assert scope in (None, [project_id]), request.url
            value = fixture['catalog'] if url.path.endswith('/catalog') else fixture['jobs'] if scope else []
            route.fulfill(json=value)
            return
        if request.method == 'GET' and url.path == '/api/media/studio/history':
            query = parse_qs(url.query)
            rows = fixture['jobs'] if query.get('project_id') == [project_id] else []
            kind, status, search = query.get('kind', [''])[0], query.get('status', [''])[0], query.get('q', [''])[0].lower()
            if kind:
                rows = [row for row in rows if row['kind'] == kind]
            if status:
                allowed = ['failed', 'unknown'] if status == 'attention' else ['submitting', 'queued', 'running'] if status == 'active' else [status]
                rows = [row for row in rows if row['status'] in allowed]
            if search:
                rows = [row for row in rows if search in row['prompt'].lower() or search in row['model'].lower()]
            route.fulfill(json={'items': rows, 'next_cursor': None})
            return
        if request.method == 'POST' and url.path == '/api/media/studio/jobs':
            payload = request.post_data_json
            assert payload['project_id'] == project_id, payload
            fixture['submissions'].append(payload)
            report['provider_job_requests'].append({'theme': theme, 'width': width, 'payload': payload})
            now = time()
            job = {'id': f'fixture-job-{theme}-{width}', 'project_id': project_id,
                   'provider_id': payload['provider_id'], 'model': payload['model'],
                   'kind': 'image', 'prompt': payload['prompt'], 'status': 'queued',
                   'created': now, 'updated': now, 'outputs': []}
            fixture['jobs'].insert(0, job)
            route.fulfill(status=202, json=job)
            return
        if request.method == 'POST' and re.fullmatch(r'/api/media/studio/jobs/[A-Za-z0-9_-]+/refresh', url.path):
            identifier = url.path.split('/')[-2]
            fixture['refreshes'].append(identifier)
            job = next(item for item in fixture['jobs'] if item['id'] == identifier)
            if job['status'] == 'queued' and fixture['complete']:
                job.update(status='completed', outputs=[fixture['output_url']], updated=time())
            route.fulfill(json=job)
            return
        raise AssertionError(f'Unexpected studio fixture request: {request.method} {request.url}')

    context.route('**/*', handle)
    target = f'{base}/?session={session["id"]}&layout=artifacts'
    page.goto(target)
    page.get_by_label('Username', exact=True).fill('studio-owner')
    page.get_by_label('Password', exact=True).fill('studio-fixture-password-123')
    page.get_by_role('button', name='Continue with password', exact=True).click()
    studio = page.get_by_role('region', name='Media studio', exact=True)
    studio.wait_for(state='visible', timeout=60000)
    expect(studio.locator('.studio-scope')).to_have_text('Project workspace')
    expect(studio.get_by_text('Connect a compatible provider', exact=True)).to_be_visible()
    expect(studio.get_by_label('Provider account', exact=True)).to_be_disabled()
    expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_disabled()
    expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).to_be_disabled()
    response = context.request.get(base + '/api/media/studio/catalog?project_id=' + project_id)
    assert response.ok, response.text()
    real_catalog = response.json()
    assert real_catalog['accounts'] == [], real_catalog
    assert real_catalog['models'], 'The documented offline catalog should remain available'
    assert real_catalog['catalog_source'] == 'snapshot', real_catalog
    expect(studio.get_by_text(re.compile('Saved model catalog'))).to_be_visible()
    report['checks'].append({'theme': theme, 'width': width, 'check': 'real-no-account-state',
                             'accounts': 0, 'model_count': len(real_catalog['models']),
                             'catalog_source': real_catalog['catalog_source']})
    _audit(page, studio, destination, report, 'unconfigured-image', theme, width, axe)

    # Verify the new surface leaves the existing artifact editor reachable.
    page.get_by_role('tab', name='Artifacts & documents', exact=True).click()
    expect(page.get_by_role('region', name='Media and artifacts', exact=True)).to_be_visible()
    page.get_by_role('tab', name='Media studio', exact=True).click()
    expect(studio).to_be_visible()

    # These are the real backend's documented model shapes, with one explicitly
    # synthetic configured account. No credential is ever registered on the host.
    fixture['catalog'] = {**real_catalog, 'accounts': [
        {'id': 'studio-fixture', 'name': 'Studio fixture OpenRouter', 'adapter': 'openrouter',
         'configured': True, 'capabilities': ['image', 'video']}], 'catalog_source': 'snapshot'}
    # The offline backend deliberately omits a live-only OpenRouter lip-sync
    # model. Supply its public UI contract explicitly; this is never submitted.
    if not any(model['adapter'] == 'openrouter' and 'lipsync' in model['modes']
               for model in fixture['catalog']['models']):
        fixture['catalog']['models'] = [*real_catalog['models'], {
            'id': 'heygen/avatar-iv', 'label': 'HeyGen Avatar IV fixture',
            'adapter': 'openrouter', 'kind': 'video', 'modes': ['lipsync'],
            'description': 'Explicit synthetic live-catalog UI fixture; no provider calls.',
            'aspect_ratios': ['16:9', '9:16'], 'resolutions': ['720p', '1080p'],
            'durations': [], 'requires_image': True, 'requires_audio': True}]
    image_models = [model for model in real_catalog['models']
                    if model['adapter'] == 'openrouter' and 'image' in model['modes']]
    assert image_models, real_catalog
    for mode in ('image', 'video', 'cinema', 'lipsync'):
        assert any(model['adapter'] == 'openrouter' and mode in model['modes']
                   for model in fixture['catalog']['models']), ('Missing fixture mode', mode)
    image_model = image_models[0]
    def output_control(label):
        return studio.locator('.studio-output-settings label').filter(
            has_text=re.compile('^' + re.escape(label))).get_by_role('combobox')
    image = io.BytesIO()
    Image.new('RGB', (320, 200), '#27553d').save(image, format='PNG')
    artifact = state.artifacts.create(
        owner.id,
        project_id, 'image', 'Studio fixture output', blob=image.getvalue(), mime='image/png')
    fixture['output_url'] = f'/api/media/artifacts/{artifact["id"]}/export?format=original'
    fixture['enabled'] = True
    page.goto(target)
    studio.wait_for(state='visible')
    expect(studio.locator('.studio-scope')).to_have_text('Project workspace')
    expect(studio.get_by_label('Provider account', exact=True)).to_have_value('studio-fixture')
    expect(studio.get_by_label('Model', exact=True)).to_have_value(image_model['id'])
    expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_disabled()

    for tab_name, heading, label in (
        ('Image', 'Image studio', 'image'), ('Video', 'Video studio', 'video'),
        (re.compile(r'^Cinema\b'), 'Cinema studio', 'cinema'),
        ('Lip sync', 'Lip sync studio', 'lipsync'), ('Library', None, 'library')):
        tab = studio.get_by_role('tab', name=tab_name, exact=not isinstance(tab_name, re.Pattern))
        tab.click()
        expect(tab).to_have_attribute('aria-selected', 'true')
        if heading:
            expect(studio.get_by_role('heading', name=re.compile('^' + re.escape(heading)))).to_be_visible()
        else:
            expect(studio.get_by_role('heading', name=re.compile('^Generation library'))).to_be_visible()
            expect(studio.get_by_text('Your next idea starts here.', exact=True)).to_be_visible()
        if label == 'video':
            expect(output_control('Duration')).to_be_visible()
        if label == 'cinema':
            expect(studio.get_by_label('Your scene', exact=True)).to_be_visible()
            studio.locator('summary').filter(has_text='Camera & lens').click()
            for field in ('Camera', 'Lens', 'Focal length', 'Aperture'):
                expect(studio.get_by_label(field, exact=True)).to_be_visible()
            studio.get_by_label('Focal length', exact=True).select_option('85')
            studio.get_by_label('Aperture', exact=True).select_option('f/4')
            expect(studio.get_by_role('button', name='Generate still image', exact=True)).to_be_disabled()
        if label == 'lipsync':
            expect(studio.get_by_label('Image URL', exact=True)).to_have_attribute('required', '')
            expect(studio.get_by_label('Audio URL', exact=True)).to_have_attribute('required', '')
        _audit(page, studio, destination, report, label, theme, width, axe)

    # The modes support native tablist keyboard navigation, not just mouse clicks.
    image_tab = studio.get_by_role('tab', name='Image', exact=True)
    image_tab.click()
    image_tab.focus()
    page.keyboard.press('ArrowRight')
    video_tab = studio.get_by_role('tab', name='Video', exact=True)
    expect(video_tab).to_be_focused()
    expect(video_tab).to_have_attribute('aria-selected', 'true')
    image_tab.click()
    prompt = f'Isolated studio request fixture at {width}px in {theme} theme.'
    studio.get_by_label('Prompt', exact=True).fill(prompt)
    consent = studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)
    generate = studio.get_by_role('button', name='Generate image', exact=True)
    expect(generate).to_be_disabled()
    ratio = output_control('Aspect ratio').input_value()
    resolution = output_control('Resolution').input_value()
    consent.check()
    expect(generate).to_be_enabled()
    generate.click()
    queued = studio.get_by_role('article', name=image_model['label'] + ' · Queued', exact=True)
    expect(queued).to_be_visible()
    expect(consent).not_to_be_checked()
    assert len(fixture['submissions']) == 1, fixture['submissions']
    payload = fixture['submissions'][0]
    assert re.fullmatch(r'[A-Za-z0-9_-]{8,128}', payload['request_id']), payload
    assert payload == {**payload, 'project_id': project_id, 'provider_id': 'studio-fixture',
                       'model': image_model['id'], 'prompt': prompt, 'aspect_ratio': ratio,
                       'resolution': resolution, 'operation': 'image', 'acknowledge_billing': True}, payload
    assert set(payload) == {'request_id', 'project_id', 'provider_id', 'model', 'prompt',
                            'aspect_ratio', 'resolution', 'operation', 'acknowledge_billing'}, payload
    expect(queued.locator('img,video')).to_have_count(0)
    _audit(page, studio, destination, report, 'queued', theme, width, axe)
    fixture['complete'] = True
    queued.get_by_role('button', name='Check status', exact=True).click()
    completed = studio.get_by_role('article', name=image_model['label'] + ' · Completed', exact=True)
    expect(completed).to_be_visible()
    preview = completed.locator('img')
    preview.scroll_into_view_if_needed()
    expect(preview).to_be_visible()
    expect(preview).to_have_js_property('complete', True)
    assert preview.evaluate('image => image.naturalWidth') == 320
    with page.expect_download() as pending:
        completed.get_by_role('link', name=re.compile('^Download Generation ')).click()
    export = destination / f'studio-output-{theme}-{width}.png'
    pending.value.save_as(export)
    assert export.read_bytes() == image.getvalue(), 'The authenticated artifact export changed the fixture bytes'
    _audit(page, studio, destination, report, 'completed', theme, width, axe)

    # Failed and unknown are separate factual outcomes; neither may show a
    # success preview or an automatic generation retry.
    base_job = fixture['jobs'][0]
    fixture['jobs'].extend([
        {**base_job, 'id': f'fixture-failed-{theme}-{width}', 'status': 'failed', 'outputs': [],
         'prompt': 'Explicit failed fixture', 'error': 'The provider rejected this fixture request.'},
        {**base_job, 'id': f'fixture-unknown-{theme}-{width}', 'status': 'unknown', 'outputs': [],
         'prompt': 'Explicit uncertain fixture', 'error': 'A provider receipt was not confirmed. Do not resubmit.'},
    ])
    studio.get_by_role('tab', name='Library', exact=True).click()
    studio.get_by_role('button', name='Refresh', exact=True).click()
    failed = studio.get_by_role('article', name=image_model['label'] + ' · Failed', exact=True)
    unknown = studio.get_by_role('article', name=image_model['label'] + ' · Status unknown', exact=True)
    expect(failed).to_be_visible()
    expect(unknown).to_be_visible()
    expect(failed.get_by_text('The provider could not complete this generation.', exact=True)).to_be_visible()
    expect(unknown.get_by_text('The provider has not confirmed a final result. Check status before starting another job.', exact=True)).to_be_visible()
    for card in (failed, unknown):
        expect(card.locator('img,video')).to_have_count(0)
        expect(card.get_by_role('link')).to_have_count(0)
    studio.get_by_role('button', name='Needs attention', exact=True).click()
    expect(studio.get_by_role('article')).to_have_count(2)
    unknown.get_by_role('button', name='Check status', exact=True).click()
    expect(unknown).to_be_visible()
    assert len(fixture['submissions']) == 1, 'Checking uncertain status must not resubmit generation'
    _audit(page, studio, destination, report, 'needs-attention', theme, width, axe)
    report['checks'].append({'theme': theme, 'width': width, 'check': 'configured-studio-flow',
                             'modes': ['image', 'video', 'cinema', 'lipsync', 'library'],
                             'artifacts_view_reachable': True, 'keyboard_mode_navigation': True,
                             'explicit_billing_required': True, 'generation_submissions': 1,
                             'queued_then_completed': True, 'authenticated_output_bytes': export.stat().st_size,
                             'failed_unknown_have_no_outputs': True, 'unknown_check_does_not_resubmit': True,
                             'refreshes': fixture['refreshes']})


def _audit(page, studio, destination, report, label, theme, width, axe):
    """Capture actual rendered geometry and WCAG checks, including contrast."""
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    studio.wait_for(state='visible')
    studio.locator('.studio-body').evaluate('element => { element.scrollTop = 0; }')
    entry = {
        'state': label,
        'theme': theme,
        'viewport': page.viewport_size,
        'overflow': page.evaluate('document.documentElement.scrollWidth > innerWidth'),
        'studio_overflow': studio.evaluate('e => e.scrollWidth > e.clientWidth + 1'),
        'reduced_motion': page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),
    }
    if studio.locator('form').count():
        entry['prompt_dock'] = studio.locator('form').evaluate("""element => {
            const box = element.getBoundingClientRect();
            const panel = element.closest('.media-studio').getBoundingClientRect();
            return {height: Math.round(box.height), top: Math.round(box.top),
                bottom: Math.round(box.bottom), panel_bottom: Math.round(panel.bottom),
                visible_height: Math.max(0, Math.round(Math.min(box.bottom, panel.bottom, innerHeight)
                    - Math.max(box.top, panel.top, 0)))};
        }""")
    if axe:
        if not page.evaluate("typeof window.axe !== 'undefined'"):
            page.add_script_tag(path=str(axe))
        result = studio.evaluate("""async element => await axe.run(element, {
            runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}
        })""")
        entry['axe_version'] = page.evaluate('axe.version')
        entry['violations'] = [
            {'id': item['id'], 'impact': item.get('impact'),
             'nodes': [{'target': node['target'], 'summary': node.get('failureSummary')}
                       for node in item['nodes']]}
            for item in result['violations']
        ]
        entry['incomplete'] = [
            {'id': item['id'], 'targets': [node['target'] for node in item['nodes']]}
            for item in result['incomplete']
        ]
    else:
        entry['axe_skipped'] = 'axe-core is not installed in desktop/workspace/node_modules'
    name = f'studio-{label}-{theme}-{width}.png'
    page.screenshot(path=str(destination / name), full_page=True)
    entry['screenshot'] = name
    if width == 390 and studio.locator('form').count():
        studio.locator('form').get_by_role('button', name=re.compile('^Generate ')).scroll_into_view_if_needed()
        control_name = f'studio-{label}-controls-{theme}-{width}.png'
        page.screenshot(path=str(destination / control_name), full_page=True)
        entry['controls_screenshot'] = control_name
        studio.locator('.studio-body').evaluate('element => { element.scrollTop = 0; }')
    report['states'].append(entry)
    assert not entry['overflow'], entry
    assert not entry['studio_overflow'], entry
    assert entry['reduced_motion'], entry
    assert not entry.get('violations'), entry


def run(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    axe_path = repo / 'desktop/workspace/node_modules/axe-core/axe.min.js'
    axe = axe_path if axe_path.is_file() else None
    report = {
        'proof': 'actual-host-media-studio',
        'axe_scope': 'Visible Media studio region; full document horizontal overflow is checked separately.',
        'paid_provider_queries': 0,
        'states': [],
        'checks': [],
        'errors': [],
        'external_browser_requests': [],
        'provider_job_requests': [],
        'public_discovery_requests': [],
        'limitations': [
            'Configured provider catalog and job lifecycle use explicit browser route fixtures; '
            'this is rendered UI and request-contract evidence, not a real paid generation.',
            'Native desktop interactions and physical-device behavior are outside this proof.',
        ],
    }
    page = None
    server = None
    worker = None
    listener = None
    provider_client = None
    root = None
    env_names = ('TERMX_CONFIG_DIR', 'TERMX_AGENTS_DIR', 'TERMX_ENGINE_STARTUP_REFRESH')
    previous_env = {name: os.environ.get(name) for name in env_names}
    try:
        root = Path(tempfile.mkdtemp(prefix='termx-studio-ui-'))
        os.environ.update(TERMX_CONFIG_DIR=str(root / 'config'),
                          TERMX_AGENTS_DIR=str(root / 'agents'),
                          TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.app import AppState, create_app
        from termx.agent.secrets import CredentialStore
        from workspace_ui_snapshot import snapshot_ui

        state = AppState(passcode=None, credentials=CredentialStore({}))
        owner = state.identity.setup_owner('studio-owner', 'studio-fixture-password-123')
        project = state.projects.register(str(root), name='Studio UI fixture')
        app = create_app(state, web_dir=snapshot_ui(root, repo / 'desktop/workspace/dist'))
        session = state.workspace.create_session(
            owner, title='Studio verification', project_id=project['id'], cwd=str(root))
        state.workspace.orchestrator.ensure(owner, session_id=session['id'])
        # Exercise the genuine no-account API and its documented fallback without
        # depending on external network availability. Any paid backend path is a bug.
        def discovery_only(request):
            if request.method != 'GET' or request.url.path not in ('/api/v1/models', '/api/v1/images/models', '/api/v1/videos/models'):
                raise AssertionError(f'Unexpected backend provider request: {request.method} {request.url.path}')
            report['public_discovery_requests'].append(request.url.path)
            return httpx.Response(503, json={'error': 'Explicit offline discovery fixture'})
        provider_client = httpx.AsyncClient(transport=httpx.MockTransport(discovery_only))
        state.studio.openrouter.client = provider_client
        report['UI_asset_snapshot'] = json.loads((root / 'fixture-ui-snapshot.json').read_text())
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        deadline = monotonic() + 25
        while not server.started:
            if not worker.is_alive():
                raise RuntimeError('The isolated studio host exited before starting')
            if monotonic() > deadline:
                raise RuntimeError('The isolated studio host did not start')
            sleep(.05)
        with sync_playwright() as playwright:
            executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
            if not executable:
                candidates = (Path(playwright.chromium.executable_path),
                              Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))
                executable = next((str(path) for path in candidates if path.is_file()), None)
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
            report['browser'] = {'version': browser.version, 'executable': executable or 'Playwright default'}
            for width, height in ((1440, 1000), (390, 844)):
                for theme in ('dark', 'light'):
                    context = browser.new_context(
                        viewport={'width': width, 'height': height}, reduced_motion='reduce')
                    context.add_init_script(
                        f"localStorage.setItem('termx-shell-theme', {json.dumps(theme)})")
                    page = context.new_page()
                    page.on('pageerror', lambda error: report['errors'].append(str(error)))
                    # _exercise installs same-origin-only browser network protection and
                    # fixtures only the studio catalog/job endpoints after the real empty state.
                    try:
                        _exercise(page, context, base, session, project, owner, state,
                                  destination, report, theme, width, axe)
                    except Exception:
                        page.screenshot(path=str(destination / 'failure.png'), full_page=True)
                        (destination / 'failure.html').write_text(page.content(), encoding='utf-8')
                        raise
                    finally:
                        context.close()
                        page = None
            browser.close()
        assert not report['errors'], report['errors']
        assert not report['external_browser_requests'], report['external_browser_requests']
        report['result'] = 'passed'
        server.should_exit = True
        worker.join(timeout=20)
        listener.close()
        server = None
        worker = None
        listener = None
    except Exception as error:
        report['result'] = 'failed'
        report['failure'] = f'{type(error).__name__}: {error}'
        if page and not page.is_closed():
            try:
                page.screenshot(path=str(destination / 'failure.png'), full_page=True)
                (destination / 'failure.html').write_text(page.content(), encoding='utf-8')
            except Exception as capture_error:
                report['failure_capture_error'] = str(capture_error)
        raise
    finally:
        if server:
            server.should_exit = True
        if worker:
            worker.join(timeout=20)
        if listener:
            listener.close()
        if provider_client:
            asyncio.run(provider_client.aclose())
        if root:
            shutil.rmtree(root, ignore_errors=True)
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: workspace_studio_e2e.py OUTPUT_DIR')
    print(json.dumps(run(sys.argv[1]), indent=2))
