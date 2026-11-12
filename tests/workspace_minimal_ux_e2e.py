"""Opt-in minimal workspace UX proof on a real isolated, authenticated host.

Build desktop/workspace/dist, then run:
  .venv/bin/python -B tests/workspace_minimal_ux_e2e.py OUTPUT_DIR UI_DIST

The baseline harness snapshots all build assets, supplies a scratch config/project
and random loopback port, signs in through the real UI, and blocks paid discovery.
Only configured catalog metadata and generation receipts use browser fixtures.
Real artifact exports retain the host's authentication and byte verification.
Never targets a native/live host, production profile, or configured provider.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import sys
from time import time
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from playwright.sync_api import expect

import workspace_studio_e2e as baseline
import workspace_ui_snapshot as snapshots

_ui_dist = None
_progress_path = None
_progress_case = {}


def _phase(phase, **details):
    entry = {**_progress_case, 'phase': phase, **details}
    if _progress_path is not None: _progress_path.write_text(json.dumps(entry) + '\n')
    print(json.dumps(entry), flush=True)


def _section(page, name):
    _phase('opening-section', section=name)
    nav = page.get_by_role('navigation', name='Studio views', exact=True)
    if not nav.is_visible():
        page.get_by_role('navigation', name='Workspace navigation', exact=True).get_by_role('button', name='Show sidebar', exact=True).click()
    nav.get_by_role('button', name=name, exact=True).click()


def _project(page, name):
    _phase('selecting-fixture-project', project=name)
    nav = page.get_by_role('navigation', name='Studio projects', exact=True)
    was_open = nav.is_visible()
    if not was_open:
        page.get_by_role('navigation', name='Workspace navigation', exact=True).get_by_role('button', name='Show sidebar', exact=True).click()
    nav.get_by_role('button', name=name, exact=True).click()
    if not was_open: page.keyboard.press('Escape')


def _mode(page, studio, name):
    _phase('selecting-mode', mode=name)
    _section(page, 'Create')
    if name in ('Image', 'Video'):
        target = studio.get_by_role('tab', name=name, exact=True)
        target.click(); expect(target).to_have_attribute('aria-selected', 'true')
    else:
        studio.get_by_role('button', name='Tools', exact=True).click()
        tools = page.get_by_role('dialog', name='Studio tools', exact=True)
        expect(tools).to_be_visible()
        tools.get_by_role('button', name=re.compile('^' + re.escape(name) + r'\b')).click()
        expect(tools).not_to_be_visible()
        opened = page.get_by_role('dialog')
        if opened.is_visible(): page.keyboard.press('Escape')


def _panel(page, studio, trigger_name, dialog_name):
    _phase('opening-panel', dialog=dialog_name)
    trigger = studio.get_by_role('button', name=re.compile('^' + re.escape(trigger_name) + r'\b'))
    trigger.focus(); page.keyboard.press('Enter')
    panel = page.get_by_role('dialog', name=dialog_name, exact=True)
    expect(panel).to_be_visible()
    assert panel.evaluate('e=>e.contains(document.activeElement)'), dialog_name
    page.keyboard.press('Shift+Tab')
    assert panel.evaluate('e=>e.contains(document.activeElement)'), 'Focus escaped ' + dialog_name
    page.keyboard.press('Tab')
    assert panel.evaluate('e=>e.contains(document.activeElement)'), 'Focus escaped ' + dialog_name
    return trigger, panel


def _close_panel(page, trigger, panel):
    page.keyboard.press('Escape')
    expect(panel).not_to_be_visible()
    expect(trigger).to_be_focused()
    expect(trigger).to_be_in_viewport()
    _phase('panel-focus-returned')


def _option_focus(page, studio, destination, label, theme, width):
    """Tab through each enabled option and require full horizontal visibility."""
    row = studio.locator('.studio-dock-tools')
    tiles = row.locator(':scope > .studio-tool-tile:not(:disabled)')
    assert tiles.count(), 'The composer must expose enabled option tiles'
    # Enter the first option with a real Tab key, then traverse in DOM order.
    tiles.first.focus()
    page.keyboard.press('Shift+Tab')
    page.keyboard.press('Tab')
    results = []
    for index in range(tiles.count()):
        tile = tiles.nth(index)
        expect(tile).to_be_focused()
        geometry = tile.evaluate(r"""e => {
          const r=e.getBoundingClientRect(), p=e.parentElement.getBoundingClientRect();
          return {label:e.innerText.replace(/\s+/g,' ').trim(), left:r.left, right:r.right,
            rowLeft:p.left, rowRight:p.right, width:r.width, height:r.height,
            overflow:document.documentElement.scrollWidth > innerWidth};
        }""")
        assert geometry['left'] >= geometry['rowLeft'] - 1, geometry
        assert geometry['right'] <= geometry['rowRight'] + 1, geometry
        assert not geometry['overflow'], geometry
        results.append(geometry)
        if index + 1 < tiles.count(): page.keyboard.press('Tab')
    screenshot = f'after-studio-{label}-options-focus-{theme}-{width}.png'
    page.screenshot(path=str(destination / screenshot), full_page=True)
    return {'viewport': page.viewport_size, 'tiles': results, 'screenshot': screenshot}


def _audit(page, surface, destination, report, label, theme, width, axe):
    """Whole-document automated checks plus visible-shell geometry and capture."""
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    option_focus = []
    if surface == 'studio' and label in ('image', 'video', 'reflow-image'):
        studio = page.get_by_role('region', name='Media studio', exact=True)
        option_focus.append(_option_focus(page, studio, destination, label, theme, width))
        if label == 'video' and width == 390:
            page.set_viewport_size({'width': 320, 'height': 844})
            option_focus.append(_option_focus(page, studio, destination, label, theme, 320))
            page.set_viewport_size({'width': 390, 'height': 844})
            option_focus.append(_option_focus(page, studio, destination, label, theme, width))
    metrics = page.evaluate('''() => {
      const visible = e => !!(e.getBoundingClientRect().width && e.getBoundingClientRect().height);
      const name = e => e.getAttribute('aria-label') || e.innerText || e.title;
      const rect = e => e?.getBoundingClientRect().toJSON();
      return {overflow:document.documentElement.scrollWidth > innerWidth,
        visibleButtons:[...document.querySelectorAll('button')].filter(visible).map(name).filter(Boolean),
        visibleInputs:[...document.querySelectorAll('input,textarea,select')].filter(visible).map(e=>e.getAttribute('aria-label')||e.placeholder||e.name),
        visiblePanes:[...document.querySelectorAll('.dock-pane')].filter(visible).map(e=>e.getAttribute('data-panel')),
        studioRect:rect(document.querySelector('.media-studio')),
        chatRect:rect(document.querySelector('.chat-region')),
        promptRect:rect(document.querySelector('.media-studio textarea')),
        reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches};
    }''')
    entry = {'surface': surface, 'state': label, 'theme': theme,
             'viewport': page.viewport_size, 'option_focus': option_focus, **metrics}
    if surface in ('studio', 'assets'):
        studio = page.get_by_role('region', name='Media studio', exact=True)
        entry['surfaceOverflow'] = studio.evaluate('e=>e.scrollWidth > e.clientWidth+1')
        canvas = studio.get_by_role('region', name='Generation results', exact=True)
        dock = studio.get_by_role('form', name='Generation composer', exact=True)
        if canvas.is_visible() and dock.is_visible():
            entry['canvas_dock'] = studio.evaluate("""e=>{
                const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
                const surface=rect(e),stage=rect(e.querySelector('.studio-stage')),canvas=rect(e.querySelector('[aria-label="Generation results"]')),
                  dock=rect(e.querySelector('form[aria-label="Generation composer"]'));
                return {surface,stage,canvas,dock,canvas_area_ratio:canvas.width*canvas.height/(surface.width*surface.height),
                  center_offset:Math.abs(dock.x+dock.width/2-canvas.x-canvas.width/2),bottom_gap:stage.bottom-dock.bottom};
            }""")
    if axe:
        if not page.evaluate("typeof window.axe !== 'undefined'"):
            page.add_script_tag(path=str(axe))
        result = page.evaluate('''async()=>await axe.run(document,{
          runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})''')
        entry['axe_version'] = page.evaluate('axe.version')
        entry['violations'] = [{'id': item['id'], 'impact': item.get('impact'),
            'nodes': [{'target': node['target'], 'summary': node.get('failureSummary')}
                      for node in item['nodes']]} for item in result['violations']]
        entry['incomplete'] = [{'id': item['id'], 'targets': [n['target'] for n in item['nodes']]}
                               for item in result['incomplete']]
    else:
        entry['axe_skipped'] = 'axe-core is not installed'
    filename = f'after-{surface}-{label}-{theme}-{width}.png'
    page.screenshot(path=str(destination / filename), full_page=True)
    entry['screenshot'] = filename
    report['states'].append(entry)
    _phase('captured-state', states=len(report['states']), surface=surface, state=label, screenshot=filename)
    assert not entry['overflow'], entry
    assert not entry.get('surfaceOverflow'), entry
    assert entry['reducedMotion'], entry
    if label in ('unconfigured-image', 'image') and entry.get('canvas_dock'):
        g = entry['canvas_dock']
        assert g['center_offset'] <= 2, g
        assert 0 <= g['bottom_gap'] <= 40, g
        assert g['canvas']['width'] >= g['stage']['width'] - 1, g
        assert g['dock']['width'] <= min(g['stage']['width'], 960) + 1, g
    assert not entry.get('violations'), entry


def _exercise(page, context, base, session, project, owner, state, destination, report, theme, width, axe):
    global _progress_path, _progress_case
    _progress_path = destination / 'progress.json'
    _progress_case = {'theme': theme, 'width': width}
    _phase('starting-case')
    page.set_default_timeout(15000)
    report.update(proof='actual-host-minimal-workspace-ux',
                  axe_scope='Full document WCAG A/AA/2.1AA; actual keyboard and visible geometry checked separately.',
                  staged_ui_dist=str(_ui_dist))
    project_id = project['id']
    fixture = {'enabled': False, 'catalog': None, 'jobs': [], 'complete': False,
               'submissions': [], 'refreshes': [], 'fail_catalog': False, 'fail_history': False,
               'saving_subject': False}
    origin = urlsplit(base)

    def handle(route):
        request = route.request
        url = urlsplit(request.url)
        if url.scheme in ('http', 'https') and (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
            report['external_browser_requests'].append({'method': request.method, 'url': request.url})
            route.abort('blockedbyclient'); return
        if not url.path.startswith('/api/media/studio/'):
            route.continue_(); return
        if request.method == 'POST' and url.path == '/api/media/studio/subjects':
            assert fixture['saving_subject'] and request.post_data_json['project_id'] == project_id
            route.continue_(); return
        if not fixture['enabled']:
            assert request.method == 'GET', ('Unexpected unconfigured mutation', request.url)
            route.continue_(); return
        if request.method == 'GET' and url.path == '/api/media/studio/catalog':
            if fixture['fail_catalog']:
                route.fulfill(status=503, json={'detail': 'Explicit catalog error fixture'})
            else:
                route.fulfill(json=fixture['catalog'])
            return
        if request.method == 'GET' and url.path == '/api/media/studio/jobs':
            route.fulfill(json=fixture['jobs'] if parse_qs(url.query).get('project_id') == [project_id] else []); return
        if request.method == 'GET' and url.path == '/api/media/studio/history':
            if fixture['fail_history']:
                route.fulfill(status=503, json={'detail': 'Explicit history error fixture'}); return
            query = parse_qs(url.query)
            rows = fixture['jobs'] if query.get('project_id') == [project_id] else []
            kind, status, search = query.get('kind', [''])[0], query.get('status', [''])[0], query.get('q', [''])[0].lower()
            if kind: rows = [row for row in rows if row['kind'] == kind]
            if status:
                allowed = ['failed', 'unknown'] if status == 'attention' else ['submitting', 'queued', 'running'] if status == 'active' else [status]
                rows = [row for row in rows if row['status'] in allowed]
            if search: rows = [row for row in rows if search in row['prompt'].lower() or search in row['model'].lower()]
            route.fulfill(json={'items': rows, 'next_cursor': None}); return
        if request.method == 'POST' and url.path == '/api/media/studio/jobs':
            payload = request.post_data_json
            assert payload['project_id'] == project_id and payload['acknowledge_billing'] is True, payload
            fixture['submissions'].append(payload)
            report['provider_job_requests'].append({'theme': theme, 'width': width, 'payload': payload})
            job = {'id': f'minimal-fixture-{theme}-{width}', 'project_id': project_id,
                   'provider_id': payload['provider_id'], 'model': payload['model'],
                   'kind': 'image', 'prompt': payload['prompt'], 'status': 'queued',
                   'created': time(), 'updated': time(), 'outputs': []}
            fixture['jobs'].insert(0, job)
            route.fulfill(status=202, json=job); return
        if request.method == 'POST' and re.fullmatch(r'/api/media/studio/jobs/[A-Za-z0-9_-]+/refresh', url.path):
            identifier = url.path.split('/')[-2]
            fixture['refreshes'].append(identifier)
            job = next(item for item in fixture['jobs'] if item['id'] == identifier)
            if job['status'] == 'queued' and fixture['complete']:
                job.update(status='completed', outputs=[fixture['output_url']], updated=time())
            route.fulfill(json=job); return
        # Reads for optional reference/pricing panels may use the real scratch
        # host; no unhandled Studio mutation is allowed to reach its adapters.
        if request.method == 'GET': route.continue_(); return
        raise AssertionError(f'Unexpected studio mutation: {request.method} {request.url}')

    context.route('**/*', handle)
    page.goto(f'{base}/?session={session["id"]}&layout=chat')
    page.get_by_label('Username', exact=True).fill('studio-owner')
    page.get_by_label('Password', exact=True).fill('studio-fixture-password-123')
    page.get_by_role('button', name='Continue with password', exact=True).click()
    picker = page.get_by_role('group', name='Workspace', exact=True)
    agent_button = picker.get_by_role('button', name='Agent', exact=True)
    studio_button = picker.get_by_role('button', name='Studio', exact=True)
    expect(agent_button).to_have_attribute('aria-pressed', 'true', timeout=60000)
    message = page.get_by_role('textbox', name='Message', exact=True)
    expect(message).to_be_visible()
    expect(page.get_by_role('button', name='Send message', exact=True)).to_be_disabled()
    ideas = page.locator('.chat-welcome-ideas')
    expect(ideas).not_to_have_attribute('open', '')
    ideas.locator('summary').focus(); page.keyboard.press('Enter')
    expect(page.get_by_role('button', name=re.compile('^Make a plan'))).to_be_visible()
    ideas.locator('summary').click()
    expect(page.get_by_role('button', name=re.compile('^Make a plan'))).not_to_be_visible()
    _audit(page, 'agent', destination, report, 'default', theme, width, axe)
    draft = f'Unsent isolated Agent draft {theme} {width}; do not submit.'
    message.fill(draft)
    options = page.get_by_role('button', name='Session options', exact=True)
    options.focus(); page.keyboard.press('Enter')
    expect(page.get_by_role('dialog', name='Session settings', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog', name='Session settings', exact=True)).not_to_be_visible()
    expect(options).to_be_focused()
    editor = None
    original_file = None
    if width == 1440:
        _phase('preparing-unsaved-agent-file')
        original_file = Path(project['path']) / f'minimal-ux-preservation-{theme}.txt'
        original_file.write_text('Original scratch file; never save the test buffer.\n')
        page.keyboard.press('Control+2')
        page.locator('.file-row').filter(has_text=original_file.name).click()
        editor = page.locator('.editor-host .cm-content[contenteditable=true]').first
        expect(editor).to_be_visible()
        editor.click(); page.keyboard.press('ControlOrMeta+A')
        page.keyboard.type('Unsaved scratch edit preserved across Agent and Studio.')
        expect(page.get_by_role('button', name='Save file', exact=True)).to_be_enabled()
    dock_before = page.locator('.workspace-dock-host .dock-pane').evaluate_all("rows=>rows.map(e=>({panel:e.dataset.panel,area:e.style.gridArea,display:e.style.display}))")
    studio_button.focus(); page.keyboard.press('Enter')
    studio = page.get_by_role('region', name='Media studio', exact=True)
    studio.wait_for(state='visible', timeout=60000)
    expect(studio_button).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('.workspace-dock-host')).not_to_be_visible()
    expect(message).not_to_be_visible()
    expect(page.get_by_role('button', name='New chat', exact=True)).not_to_be_visible()
    assert 'studio' in page.url, page.url
    assert page.locator('.workspace-dock-host').evaluate("e=>!!e.closest('[inert]')"), 'Inactive Agent subtree must be inert'
    expect(page.get_by_role('tablist', name='Media workspace views', exact=True)).not_to_be_visible()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    expect(studio.get_by_label('Provider account', exact=True)).not_to_be_visible()
    consent = studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)
    expect(consent).to_be_disabled()
    expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_disabled()
    expect(studio.get_by_text('Connect a compatible provider', exact=True)).to_be_visible()
    catalog_response = context.request.get(base + '/api/media/studio/catalog?project_id=' + project_id)
    assert catalog_response.ok, catalog_response.text()
    real_catalog = catalog_response.json()
    assert not real_catalog['accounts'] and real_catalog['models'] and real_catalog['catalog_source'] == 'snapshot', real_catalog
    expect(studio.get_by_text(re.compile('Saved model catalog'))).to_be_visible()
    _audit(page, 'studio', destination, report, 'unconfigured-image', theme, width, axe)
    settings_trigger, settings = _panel(page, studio, 'Generation settings', 'Generation settings')
    expect(settings.get_by_label('Provider account', exact=True)).to_be_disabled()
    _audit(page, 'studio', destination, report, 'generation-settings-panel', theme, width, axe)
    _close_panel(page, settings_trigger, settings)
    studio_draft = 'Unsent isolated Studio draft; preserve through workspace and asset views.'
    studio.get_by_label('Prompt', exact=True).fill(studio_draft)
    _section(page, 'Library')
    expect(studio.get_by_text('Your next idea starts here.', exact=True)).to_be_visible()
    _section(page, 'Saved assets')
    new_set = page.get_by_role('group', name='New reference set', exact=True)
    subject_name = f'Preserved real local file {theme} {width}'
    subject_description = 'Explicit local draft; byte content must survive ordinary navigation.'
    local_image = io.BytesIO(); Image.new('RGB', (320, 200), '#394263').save(local_image, format='PNG')
    local_filename = f'local-subject-{theme}-{width}.png'
    new_set.get_by_label('Name', exact=True).fill(subject_name)
    new_set.get_by_label(re.compile('^Description')).fill(subject_description)
    new_set.get_by_label('Reference set photos', exact=True).set_input_files(
        {'name': local_filename, 'mimeType': 'image/png', 'buffer': local_image.getvalue()})
    local_preview = new_set.get_by_role('img', name=local_filename, exact=True)
    expect(local_preview).to_have_js_property('naturalWidth', 320)
    old_local_preview = local_preview.get_attribute('src')
    _audit(page, 'assets', destination, report, 'local-file-draft', theme, width, axe)
    _section(page, 'Create')
    page.wait_for_function('''async url=>{try{await fetch(url);return false}catch{return true}}''', arg=old_local_preview, timeout=5000)
    expect(studio.get_by_label('Prompt', exact=True)).to_have_value(studio_draft)
    studio_button.focus(); page.keyboard.press('ArrowLeft')
    expect(agent_button).to_be_focused(); expect(agent_button).to_have_attribute('aria-pressed', 'true')
    expect(message).to_have_value(draft)
    dock_after = page.locator('.workspace-dock-host .dock-pane').evaluate_all("rows=>rows.map(e=>({panel:e.dataset.panel,area:e.style.gridArea,display:e.style.display}))")
    assert dock_after == dock_before, (dock_before, dock_after)
    if editor is not None:
        expect(editor).to_contain_text('Unsaved scratch edit preserved across Agent and Studio.')
        assert original_file.read_text() == 'Original scratch file; never save the test buffer.\n'
    agent_button.focus(); page.keyboard.press('End')
    expect(studio_button).to_be_focused(); expect(studio_button).to_have_attribute('aria-pressed', 'true')
    page.keyboard.press('Home'); expect(agent_button).to_be_focused()
    page.keyboard.press('ArrowRight'); expect(studio_button).to_be_focused()
    _section(page, 'Saved assets')
    expect(new_set.get_by_label('Name', exact=True)).to_have_value(subject_name)
    expect(new_set.get_by_label(re.compile('^Description'))).to_have_value(subject_description)
    expect(local_preview).to_have_js_property('naturalWidth', 320)
    assert local_preview.get_attribute('src') != old_local_preview, 'Hidden File preview must be revoked and recreated'
    _phase('saving-preserved-file-to-scratch')
    fixture['saving_subject'] = True
    new_set.get_by_role('button', name='Save reference set', exact=True).click()
    saved_row = studio.get_by_role('article', name=subject_name, exact=True)
    expect(saved_row).to_be_visible()
    fixture['saving_subject'] = False
    saved_subject = next(row for row in state.studio_subjects.list(owner.id, project_id) if row['name'] == subject_name)
    saved_image_id = saved_subject['image_ids'][0]
    subject_export = context.request.get(base + f'/api/media/artifacts/{saved_image_id}/export?format=original')
    assert subject_export.ok and subject_export.body() == local_image.getvalue(), 'The preserved File bytes changed'
    _section(page, 'Create')
    report['checks'].append({'theme': theme, 'width': width, 'check': 'workspace-separation',
        'agent_draft_preserved': True, 'agent_dock_preserved': True,
        'unsaved_file_buffer_preserved': editor is not None, 'scratch_file_saved': False, 'agent_hidden_and_inert_in_studio':
        page.locator('.workspace-dock-host').evaluate("e=>!!e.closest('[inert]')"),
        'studio_has_no_visible_agent_composer': not message.is_visible(),
        'keyboard_enter_arrows_home_end': True, 'session_dialog_escape_focus': True,
        'library_roundtrip_studio_draft_preserved': True,
        'no_account_saved_assets_name_description_and_file_bytes_preserved': True,
        'hidden_blob_preview_revoked_and_recreated': True,
        'real_no_account_catalog': len(real_catalog['models'])})

    fixture['catalog'] = {**real_catalog, 'accounts': [{'id': 'studio-fixture',
        'name': 'Explicit synthetic UI account', 'adapter': 'openrouter',
        'configured': True, 'capabilities': ['image', 'video']}], 'catalog_source': 'snapshot'}
    if not any('lipsync' in model['modes'] and model['adapter'] == 'openrouter' for model in real_catalog['models']):
        fixture['catalog']['models'] = [*real_catalog['models'], {'id': 'heygen/avatar-iv',
            'label': 'Explicit synthetic lip sync model', 'adapter': 'openrouter', 'kind': 'video',
            'modes': ['lipsync'], 'description': 'Synthetic UI contract; never submitted.',
            'aspect_ratios': ['16:9', '9:16'], 'resolutions': ['720p', '1080p'],
            'durations': [], 'requires_image': True, 'requires_audio': True}]
    image_model = next(model for model in real_catalog['models'] if model['adapter'] == 'openrouter' and 'image' in model['modes'])
    image = io.BytesIO(); Image.new('RGB', (320, 200), '#27553d').save(image, format='PNG')
    artifact = state.artifacts.create(owner.id, project_id, 'image', 'Explicit minimal UI output fixture',
                                      blob=image.getvalue(), mime='image/png')
    fixture['output_url'] = f'/api/media/artifacts/{artifact["id"]}/export?format=original'
    fixture['enabled'] = True
    page.reload(); studio.wait_for(state='visible'); _project(page, project['name'])
    expect(studio.get_by_label('Model', exact=True)).to_have_value(image_model['id'])
    expect(page.get_by_role('dialog')).not_to_be_visible()
    for tab_name, label in [('Image', 'image'), ('Video', 'video'), ('Cinema', 'cinema'), ('Lip sync', 'lipsync'), ('Library', 'library')]:
        if label == 'library':
            _section(page, 'Library')
            expect(studio.get_by_text('Your next idea starts here.', exact=True)).to_be_visible()
        else:
            _mode(page, studio, tab_name)
        if label == 'image':
            style_trigger, style_panel = _panel(page, studio, 'Style', 'Prompt style')
            expect(style_panel.get_by_role('group', name='Prompt presets', exact=True)).to_be_visible()
            _close_panel(page, style_trigger, style_panel)
        if label == 'video':
            for trigger_name, dialog_name in [('Film setup', 'Film setup'), ('Camera', 'Camera movement'), ('Color', 'Color direction'), ('Lighting', 'Lighting direction')]:
                trigger, panel = _panel(page, studio, trigger_name, dialog_name)
                _close_panel(page, trigger, panel)
        if label == 'cinema':
            expect(studio.get_by_label('Your scene', exact=True)).to_be_visible()
            camera_trigger, camera = _panel(page, studio, 'Camera', 'Camera & lens')
            for field in ('Camera', 'Lens', 'Focal length', 'Aperture'):
                expect(camera.get_by_label(field, exact=True)).to_be_visible()
            _audit(page, 'studio', destination, report, 'camera-panel', theme, width, axe)
            _close_panel(page, camera_trigger, camera)
        if label == 'lipsync':
            reference_trigger, references = _panel(page, studio, 'Reference', 'Reference media')
            expect(references.get_by_label('Image URL', exact=True)).to_have_attribute('required', '')
            expect(references.get_by_label('Audio URL', exact=True)).to_have_attribute('required', '')
            upload_source = references.get_by_role('tab', name='Upload or URL', exact=True)
            upload_source.focus(); page.keyboard.press('ArrowRight')
            expect(references.get_by_role('tab', name='Saved sets', exact=True)).to_be_focused()
            page.keyboard.press('End')
            expect(references.get_by_role('tab', name='Recent images', exact=True)).to_be_focused()
            page.keyboard.press('Home'); expect(upload_source).to_be_focused()
            _audit(page, 'studio', destination, report, 'required-reference-panel', theme, width, axe)
            _close_panel(page, reference_trigger, references)
            expect(studio.get_by_role('button', name='Generate lip sync', exact=True)).to_be_disabled()
        _audit(page, 'studio', destination, report, label, theme, width, axe)
    _mode(page, studio, 'Image')
    image_tab = studio.get_by_role('tab', name='Image', exact=True)
    image_tab.focus(); page.keyboard.press('ArrowRight')
    expect(studio.get_by_role('tab', name='Video', exact=True)).to_be_focused()
    page.keyboard.press('Home'); expect(image_tab).to_be_focused()
    _section(page, 'Saved assets')
    saved_row.get_by_role('button', name='Use selected photo from ' + subject_name, exact=True).click()
    _section(page, 'Create')
    expect(studio.get_by_role('group', name='Selected reference media', exact=True)).to_contain_text(subject_name)
    studio_button.focus(); page.keyboard.press('ArrowLeft'); page.keyboard.press('ArrowRight')
    expect(studio.get_by_role('group', name='Selected reference media', exact=True)).to_contain_text(subject_name)
    prompt = f'Explicit synthetic Studio request {theme} {width}.'
    studio.get_by_label('Prompt', exact=True).fill(prompt)
    generate = studio.get_by_role('button', name='Generate image', exact=True)
    expect(generate).to_be_disabled(); consent.check(); expect(generate).to_be_enabled()
    _phase('submitting-mocked-generation'); generate.click()
    queued = studio.get_by_role('article', name=image_model['label'] + ' · Queued', exact=True)
    expect(queued).to_be_visible(); expect(consent).not_to_be_checked()
    expect(queued.locator('img,video')).to_have_count(0)
    assert len(fixture['submissions']) == 1
    assert fixture['submissions'][0]['prompt'] == prompt
    sent = fixture['submissions'][0]
    assert saved_image_id in ([sent.get('image_id')] + [row.get('image_id') for row in sent.get('image_references', [])]), sent
    _audit(page, 'studio', destination, report, 'queued', theme, width, axe)
    fixture['complete'] = True
    queued.get_by_role('button', name='Check status', exact=True).click()
    completed = studio.get_by_role('article', name=image_model['label'] + ' · Completed', exact=True)
    expect(completed).to_be_visible()
    preview = completed.locator('img'); preview.scroll_into_view_if_needed()
    expect(preview).to_have_js_property('complete', True)
    assert preview.evaluate('e=>e.naturalWidth') == 320
    with page.expect_download() as download:
        completed.get_by_role('link', name=re.compile('^Download Generation')).click()
    exported = destination / f'authenticated-output-{theme}-{width}.png'
    download.value.save_as(exported)
    assert exported.read_bytes() == image.getvalue()
    _audit(page, 'studio', destination, report, 'completed', theme, width, axe)
    base_job = fixture['jobs'][0]
    fixture['jobs'].extend([
        {**base_job, 'id': f'failed-{theme}-{width}', 'status': 'failed', 'outputs': [],
         'prompt': 'Explicit failed fixture', 'error': 'The provider rejected this synthetic request.'},
        {**base_job, 'id': f'unknown-{theme}-{width}', 'status': 'unknown', 'outputs': [],
         'prompt': 'Explicit uncertain fixture', 'error': 'Provider receipt not confirmed; do not resubmit.'}])
    _section(page, 'Library')
    studio.get_by_role('button', name='Needs attention', exact=True).click()
    failed = studio.get_by_role('article', name=image_model['label'] + ' · Failed', exact=True)
    unknown = studio.get_by_role('article', name=image_model['label'] + ' · Status unknown', exact=True)
    expect(failed).to_be_visible(); expect(unknown).to_be_visible()
    for card in (failed, unknown): expect(card.locator('img,video,a')).to_have_count(0)
    unknown.get_by_role('button', name='Check status', exact=True).click()
    assert len(fixture['submissions']) == 1, 'Status check must never resubmit'
    _audit(page, 'studio', destination, report, 'needs-attention', theme, width, axe)
    studio.get_by_label('Search generations', exact=True).fill('No matching explicit fixture')
    expect(studio.get_by_text('No matching generations', exact=True)).to_be_visible()
    _audit(page, 'studio', destination, report, 'no-search-results', theme, width, axe)
    _mode(page, studio, 'Image')
    fixture['fail_catalog'] = True
    page.reload(); studio.wait_for(state='visible'); _project(page, project['name'])
    expect(studio.get_by_text('Studio could not load', exact=True)).to_be_visible()
    _audit(page, 'studio', destination, report, 'catalog-error', theme, width, axe)
    fixture['fail_catalog'] = False
    studio.get_by_role('button', name='Try again', exact=True).click()
    expect(studio.get_by_label('Model', exact=True)).to_have_value(image_model['id'])
    expect(studio.get_by_text('Studio could not load', exact=True)).not_to_be_visible()
    if width == 390:
        # Reflow boundary is additional to the representative phone screenshots.
        page.set_viewport_size({'width': 320, 'height': 844})
        _audit(page, 'studio', destination, report, 'reflow-image', theme, 320, axe)
        page.set_viewport_size({'width': 390, 'height': 420})
        studio.get_by_role('button', name='Generate image', exact=True).scroll_into_view_if_needed()
        expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_in_viewport()
        consent.scroll_into_view_if_needed()
        expect(consent).to_be_in_viewport()
        expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_in_viewport()
        _audit(page, 'studio', destination, report, 'short-viewport', theme, 390, axe)
        page.set_viewport_size({'width': 320, 'height': 844})
        picker.get_by_role('button', name='Agent', exact=True).click()
        expect(message).to_have_value(draft)
        _audit(page, 'agent', destination, report, 'reflow-draft', theme, 320, axe)
        setup = page.get_by_role('region', name='Connect this chat', exact=True)
        expect(setup.get_by_role('button', name='Set up connection', exact=True)).to_be_in_viewport()
        page.set_viewport_size({'width': 390, 'height': 420})
        expect(message).to_be_in_viewport()
        expect(setup.get_by_role('button', name='Set up connection', exact=True)).to_be_in_viewport()
        _audit(page, 'agent', destination, report, 'short-viewport-draft', theme, 390, axe)
    if width == 1440:
        page.set_viewport_size({'width': 720, 'height': 500})
        studio.get_by_role('button', name='Generate image', exact=True).scroll_into_view_if_needed()
        expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_in_viewport()
        consent.scroll_into_view_if_needed()
        expect(consent).to_be_in_viewport()
        expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_in_viewport()
        _audit(page, 'studio', destination, report, 'css-reflow-720x500', theme, 720, axe)
    report['checks'].append({'theme': theme, 'width': width, 'check': 'focused-studio-journey',
        'modes': ['image', 'video', 'cinema', 'lipsync', 'library'], 'option_dialogs_closed_by_default': True,
        'required_lipsync_references_discoverable': True, 'keyboard_mode_navigation': True,
        'explicit_billing_required_and_reset': True, 'mock_submissions': len(fixture['submissions']),
        'real_authenticated_export_bytes': exported.stat().st_size,
        'failed_unknown_have_no_success_outputs': True, 'unknown_check_never_resubmits': True,
        'empty_search_and_recoverable_catalog_error': True})


def run(destination, ui_dist):
    global _ui_dist
    _ui_dist = Path(ui_dist).resolve()
    if not (_ui_dist / 'index.html').is_file():
        raise RuntimeError(f'The explicit staged UI build is missing: {_ui_dist}')
    original_exercise, original_snapshot = baseline._exercise, snapshots.snapshot_ui
    try:
        # baseline.run imports this helper at call time. Override only its input
        # while retaining its complete immutable copy and per-asset hash manifest.
        snapshots.snapshot_ui = lambda root, _historical_source: original_snapshot(root, _ui_dist)
        baseline._exercise = _exercise
        return baseline.run(destination)
    finally:
        baseline._exercise = original_exercise
        snapshots.snapshot_ui = original_snapshot
        _ui_dist = None


if __name__ == '__main__':
    if len(sys.argv) != 3: raise SystemExit('Usage: workspace_minimal_ux_e2e.py OUTPUT_DIR UI_DIST')
    result = run(sys.argv[1], sys.argv[2])
    print(json.dumps({'result': result['result'], 'staged_ui_dist': result['staged_ui_dist'],
        'states': len(result['states']), 'checks': result['checks'], 'errors': result['errors'],
        'external_browser_requests': result['external_browser_requests'],
        'paid_provider_queries': result['paid_provider_queries']}, indent=2))
