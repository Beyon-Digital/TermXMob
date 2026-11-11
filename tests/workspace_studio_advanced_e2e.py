"""Actual-host advanced studio proof; no credentials or paid provider requests.

Run after building the workspace:
  uv run --extra media python -B tests/workspace_studio_advanced_e2e.py OUTPUT_DIR

History, image upload, authorization and binary exports use the real scratch host.
Only configured catalog metadata and generation receipts are browser fixtures.
Set TERMX_STUDIO_SUBJECTS_ONLY=1 for the focused real reference-set workflow.
Set TERMX_STUDIO_FOCUS=composition or lazy for ordered-reference/pricing or IO proofs.
"""
from __future__ import annotations

import io
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import time
from urllib.parse import parse_qs, urlencode, urlsplit

from PIL import Image
import httpx
from playwright.sync_api import expect

import workspace_studio_e2e as baseline

_focused_clients = []


def seed_history(state, owner, project):
    """Seed more than one legacy page, including an old owned image handoff."""
    project_id = project['id']
    with state.studio.db() as database:
        database.execute("DELETE FROM studio_jobs WHERE owner=? AND project IS ? AND id LIKE 'advanced-%'", (owner.id, project_id))
        existing = database.execute('SELECT COUNT(*) FROM studio_jobs WHERE owner=? AND project IS ?',
                                    (owner.id, project_id)).fetchone()[0]
    if existing:
        return state._advanced_studio_fixture
    image = io.BytesIO()
    Image.new('RGB', (320, 200), '#315b43').save(image, format='PNG')
    artifact = state.artifacts.create(owner.id, project_id, 'image', 'Owned handoff image',
                                      blob=image.getvalue(), mime='image/png')
    output = f'/api/media/artifacts/{artifact["id"]}/export?format=original'
    rows = []
    for index in range(124):
        kind = 'video' if index % 3 == 0 else 'image'
        model = 'google/veo-3.1' if kind == 'video' else 'google/gemini-3-pro-image'
        status = 'unknown' if index in (33, 44) else 'failed' if index % 10 == 0 else 'completed'
        prompt = 'Source handoff fixture' if index == 7 else 'Literal_%_needle' if index == 2 else f'Archive scene {index:03d}'
        parameters = {'operation': kind, 'aspect_ratio': '16:9',
                      'resolution': '720p' if kind == 'video' else '2K'}
        rows.append((f'archive-{index:03d}', owner.id, project_id, f'archive-request-{index:03d}',
                     f'fixture-digest-{index}', 'studio-fixture', 'openrouter', model, kind,
                     prompt, status, None, 1700000000.0 + index, 1700000000.0 + index,
                     json.dumps([output] if index == 7 else []),
                     'Explicit uncertain fixture; do not resubmit.' if status == 'unknown' else None,
                     json.dumps(parameters)))
    # These intentionally inaccessible rows prove real history scope filtering.
    rows.append(('foreign-owner', 'another-fixture-owner', project_id, 'foreign-owner-request', 'foreign',
                 'studio-fixture', 'openrouter', 'google/gemini-3-pro-image', 'image', 'PRIVATE FOREIGN ROW',
                 'completed', None, 1900000001.0, 1900000001.0, '[]', None, '{}'))
    rows.append(('foreign-project', owner.id, 'another-project', 'foreign-project-request', 'foreign',
                 'studio-fixture', 'openrouter', 'google/gemini-3-pro-image', 'image', 'PRIVATE OTHER PROJECT',
                 'completed', None, 1900000002.0, 1900000002.0, '[]', None, '{}'))
    with state.studio.db() as database:
        database.executemany('''INSERT INTO studio_jobs
            (id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,task_id,
             created,updated,outputs,error,parameters) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
    state._advanced_studio_fixture = {'artifact': artifact, 'output': output, 'image': image.getvalue(),
                                      'count': 124, 'rows': rows[:124]}
    return state._advanced_studio_fixture


def history_contract(context, base, project_id, fixture, report):
    def get(**extra):
        response = context.request.get(base + '/api/media/studio/history?' + urlencode(
            {'project_id': project_id, **extra}))
        assert response.ok, response.text()
        return response.json()
    identifiers = []
    cursor = None
    pages = []
    while True:
        page = get(limit=31, **({'cursor': cursor} if cursor else {}))
        assert set(page) == {'items', 'next_cursor'}, page
        pages.append(len(page['items']))
        identifiers.extend(row['id'] for row in page['items'])
        cursor = page['next_cursor']
        if not cursor:
            break
        assert len(pages) < 8, 'History cursor did not terminate'
    assert len(identifiers) == len(set(identifiers)) == fixture['count'], identifiers
    assert identifiers == [f'archive-{index:03d}' for index in reversed(range(124))], identifiers
    assert get(q='Source handoff fixture')['items'][0]['id'] == 'archive-007'
    assert [row['id'] for row in get(q='Literal_%_needle')['items']] == ['archive-002']
    assert all(row['kind'] == 'video' for row in get(kind='video')['items'])
    attention = get(status='attention', limit=100)['items']
    assert attention and all(row['status'] in ('failed', 'unknown') for row in attention)
    assert not get(status='active')['items']
    report['advanced_history_api'] = {'owned_rows': len(identifiers), 'page_sizes': pages,
        'no_duplicate_or_foreign_rows': True, 'old_search_beyond_100': True,
        'literal_search': True, 'media_and_status_filters': True}


def exercise(page, context, base, session, project, owner, state, destination, report, theme, width, axe):
    report['proof'] = 'actual-host-studio-advanced'
    report['limitations'] = [
        'History, image upload and image export use the actual isolated host; configured catalog capabilities and generation receipts are explicit browser fixtures.',
        'Synthetic capability metadata proves UI gating and request serialization, not live model availability or paid generation.',
        'Factory drafting combines deterministic shot templates; no provider writes the scene scripts.',
        'Native desktop behavior is verified separately, outside this browser proof.',
    ]
    fixture = seed_history(state, owner, project)
    project_id = project['id']
    mock = {'catalog': None, 'submissions': [], 'batch': False, 'batch_count': 0, 'refreshes': [], 'errors': [], 'uploads': 0}
    origin = urlsplit(base)

    def handle(route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.scheme in ('http', 'https') and (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            report['external_browser_requests'].append({'method': request.method, 'url': request.url})
            route.abort('blockedbyclient')
            return
        if parsed.path == '/api/media/studio/catalog' and mock['catalog']:
            route.fulfill(json=mock['catalog'])
            return
        if request.method == 'POST' and parsed.path == '/api/media/studio/jobs':
            payload = request.post_data_json
            assert payload['project_id'] == project_id and payload['acknowledge_billing'] is True, payload
            assert payload['provider_id'] == 'studio-fixture', payload
            assert re.fullmatch(r'[A-Za-z0-9_-]{8,128}', payload['request_id']), payload
            mock['submissions'].append(payload)
            report['provider_job_requests'].append({'theme': theme, 'width': width, 'payload': payload})
            model = next(model for model in mock['catalog']['models'] if model['id'] == payload['model'])
            if mock['batch']:
                mock['batch_count'] += 1
            status = 'unknown' if mock['batch'] and mock['batch_count'] == 2 else 'queued' if mock['batch'] else 'completed'
            identifier = f'advanced-{theme}-{width}-{len(mock["submissions"])}'
            now = time()
            outputs = [fixture['output']] if model['kind'] == 'image' else []
            parameters = state.studio.parameters(payload)
            with state.studio.db() as database:
                database.execute('''INSERT INTO studio_jobs
                    (id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,task_id,
                     created,updated,outputs,error,parameters) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identifier, owner.id, project_id, payload['request_id'], 'browser-fixture',
                     payload['provider_id'], 'openrouter', payload['model'], model['kind'], payload['prompt'], status,
                     None, now, now, json.dumps(outputs), 'Explicit uncertain batch fixture' if status == 'unknown' else None,
                     json.dumps(parameters)))
            route.fulfill(status=202, json=state.studio.get(owner.id, identifier))
            return
        if request.method == 'POST' and re.fullmatch(r'/api/media/studio/jobs/[A-Za-z0-9_-]+/refresh', parsed.path):
            identifier = parsed.path.split('/')[-2]
            mock['refreshes'].append(identifier)
            route.fulfill(json=state.studio.get(owner.id, identifier))
            return
        if request.method == 'POST' and parsed.path == '/api/media/inputs':
            mock['uploads'] += 1
        # All history, authentication, upload and binary reads remain genuine.
        route.continue_()

    def guarded(route):
        try:
            handle(route)
        except Exception as error:
            mock['errors'].append(f'{type(error).__name__}: {error}')
            route.fulfill(status=500, json={'detail': mock['errors'][-1]})

    context.route('**/*', guarded)
    target = f'{base}/?session={session["id"]}&layout=artifacts'
    page.goto(target)
    page.get_by_label('Username', exact=True).fill('studio-owner')
    page.get_by_label('Password', exact=True).fill('studio-fixture-password-123')
    page.get_by_role('button', name='Continue with password', exact=True).click()
    studio = page.get_by_role('region', name='Media studio', exact=True)
    expect(studio.locator('.studio-scope')).to_have_text('Project workspace', timeout=60000)
    history_contract(context, base, project_id, fixture, report)
    response = context.request.get(base + '/api/media/studio/catalog?' + urlencode({'project_id': project_id}))
    assert response.ok, response.text()
    catalog = response.json()
    image_model = next(model for model in catalog['models'] if model['id'] == 'google/gemini-3-pro-image' and model['adapter'] == 'openrouter')
    video_model = next(model for model in catalog['models'] if model['id'] == 'google/veo-3.1' and model['adapter'] == 'openrouter')
    image_model.update(supports_image=True, inline_inputs=['image'])
    video_model.update(supports_image=True, inline_inputs=['image'], image_roles=['first_frame', 'reference'], supports_negative_prompt=True)
    # Capability-gated controls use deliberately synthetic public discovery metadata.
    remix_model = {'id': 'runway/aleph-2', 'label': 'Verified remix contract fixture', 'kind': 'video',
        'adapter': 'openrouter', 'modes': ['remix'], 'description': 'Explicit verified-contract UI fixture.',
        'aspect_ratios': ['16:9'], 'resolutions': ['720p'], 'durations': [],
        'requires_video': True, 'supports_video': True, 'inline_inputs': []}
    mock['catalog'] = {'models': [image_model, video_model], 'accounts': [
        {'id': 'studio-fixture', 'name': 'Advanced fixture OpenRouter', 'configured': True,
         'adapter': 'openrouter', 'capabilities': ['image', 'video']}], 'catalog_source': 'snapshot'}
    page.goto(target)
    expect(studio.locator('.studio-scope')).to_have_text('Project workspace')
    expect(studio.get_by_label('Provider account', exact=True)).to_have_value('studio-fixture')

    def audit(label):
        baseline._audit(page, studio, destination, report, 'advanced-' + label, theme, width, axe)
        entry = report['states'][-1]
        if width == 390 and studio.locator('form').count() and studio.locator('.studio-canvas .studio-job').count():
            entry['mobile_composer_reachable_without_long_history_scroll'] = (
                entry['prompt_dock']['top'] <= page.viewport_size['height'] + 100)
            assert entry['mobile_composer_reachable_without_long_history_scroll'], entry

    if os.environ.get('TERMX_STUDIO_SUBJECTS_ONLY') == '1':
        trigger = studio.get_by_role('button', name='Saved subjects & products', exact=True)
        expect(trigger).to_be_visible()
        trigger.click()
        create = studio.get_by_role('group', name='New reference set', exact=True)
        expect(create).to_be_visible()
        subject_name = f'Reference product {theme} {width}'
        create.get_by_label('Name', exact=True).fill(subject_name)
        create.get_by_label(re.compile('^Description')).fill('A handmade ceramic product with two reference angles.')
        photos = create.get_by_label('Reference set photos', exact=True)
        photos.set_input_files([{'name': f'excess-{index}.png', 'mimeType': 'image/png', 'buffer': fixture['image']}
                                for index in range(6)])
        expect(studio.get_by_role('alert')).to_contain_text('between 1 and 5 photos')
        assert mock['uploads'] == 0
        photos.set_input_files([{'name': f'product-angle-{index}.png', 'mimeType': 'image/png', 'buffer': fixture['image']}
                                for index in (1, 2)])
        create.get_by_role('button', name='Save reference set', exact=True).click()
        row = studio.get_by_role('article', name=subject_name, exact=True)
        expect(row).to_be_visible()
        assert mock['uploads'] == 2 and not mock['submissions'], 'Saving references must upload only, with no generation'
        response = context.request.get(base + '/api/media/studio/subjects?' + urlencode({'project_id': project_id}))
        assert response.ok, response.text()
        saved = next(item for item in response.json() if item['name'] == subject_name)
        assert saved['project_id'] == project_id and len(set(saved['image_ids'])) == 2, saved
        expect(row.locator('img')).to_have_count(2)
        row.screenshot(path=str(destination / f'subject-reference-card-{theme}-{width}.png'))
        audit('subjects-saved')
        search = studio.get_by_label('Find a saved set', exact=True)
        search.fill('no-matching-reference-set')
        expect(studio.get_by_text('No reference sets match your search.', exact=True)).to_be_visible()
        search.fill(subject_name)
        expect(row).to_be_visible()
        row.get_by_role('button', name='Photo 2 for ' + subject_name, exact=True).click()
        row.get_by_role('button', name='Use selected photo from ' + subject_name, exact=True).click()
        expect(studio.locator('.studio-reference-selection')).to_contain_text(subject_name)
        expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).not_to_be_checked()
        assert not mock['submissions'], 'Selecting a subject must remain a draft'
        studio.get_by_label('Prompt', exact=True).fill('An editorial product portrait using the selected reference angle.')
        studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True).check()
        studio.locator('form').get_by_role('button', name='Generate image', exact=True).click()
        expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).not_to_be_checked()
        assert len(mock['submissions']) == 1, mock['submissions']
        payload = mock['submissions'][0]
        assert payload['image_id'] == saved['image_ids'][1] and 'image_url' not in payload, payload
        assert not any(key in payload for key in ('image_ids', 'subject_id', 'description')), payload
        audit('subjects-selected')
        row.get_by_role('button', name='Remove ' + subject_name, exact=True).click()
        confirm = row.get_by_role('group', name='Confirm removal of ' + subject_name, exact=True)
        confirm.get_by_role('button', name='Cancel', exact=True).click()
        expect(row).to_be_visible()
        row.get_by_role('button', name='Remove ' + subject_name, exact=True).click()
        confirm.get_by_role('button', name='Remove set', exact=True).click()
        expect(row).to_have_count(0)
        remaining = context.request.get(base + '/api/media/studio/subjects?' + urlencode({'project_id': project_id}))
        assert remaining.ok and saved['id'] not in [item['id'] for item in remaining.json()]
        for image_id in saved['image_ids']:
            exported = context.request.get(base + f'/api/media/artifacts/{image_id}/export?format=original')
            assert exported.ok and exported.body() == fixture['image'], 'Removing a reference set must keep its image files'
        audit('subjects-removed')
        report['checks'].append({'theme': theme, 'width': width, 'check': 'real-subjects-flow',
            'excess_photos_rejected_before_upload': True, 'real_uploaded_photos': 2,
            'real_saved_project_reference_set': True, 'search_and_photo_selection': True,
            'selection_is_draft_only': True, 'explicit_submission_sends_only_selected_owned_id': True,
            'cancel_then_confirm_removal': True, 'removed_set_keeps_uploaded_images': True})
        assert not mock['errors'], mock['errors']
        return

    def library(search=''):
        studio.get_by_role('tab', name='Library', exact=True).click()
        all_filter = studio.get_by_role('button', name='All', exact=True)
        status = studio.get_by_label('Generation status', exact=True)
        if all_filter.get_attribute('aria-pressed') != 'true' or status.input_value():
            all_filter.click()
        field = studio.get_by_label('Search generations', exact=True)
        if field.input_value() != search:
            field.fill(search)
        expect(studio.locator('.studio-loading').filter(has_text='Loading history')).to_have_count(0)
        return studio.get_by_role('article')

    cards = library()
    expect(cards).to_have_count(40)
    counts = [40]
    while studio.get_by_role('button', name='Load more generations', exact=True).count():
        before = cards.count()
        studio.get_by_role('button', name='Load more generations', exact=True).click()
        expect(cards).not_to_have_count(before)
        counts.append(cards.count())
        assert len(counts) < 8, counts
    expect(cards).to_have_count(124)
    prompts = studio.locator('.studio-job-prompt').all_text_contents()
    assert len(prompts) == len(set(prompts)) == 124, prompts
    assert not any('PRIVATE' in prompt for prompt in prompts)
    audit('history-124')
    search = studio.get_by_label('Search generations', exact=True)
    search.fill('Archive scene 12')
    search.fill('Source handoff fixture')
    expect(cards).to_have_count(1)
    expect(cards.first.locator('.studio-job-prompt')).to_have_text('Source handoff fixture')
    audit('history-search')
    search.fill('')
    studio.get_by_label('Generation status', exact=True).select_option('unknown')
    expect(cards).to_have_count(2)
    expect(cards.first.locator('.studio-job-status')).to_have_text('Status unknown')
    audit('history-unknown')

    def source_card():
        rows = library('Source handoff fixture')
        expect(rows).to_have_count(1)
        expect(rows.first.locator('.studio-job-prompt')).to_have_text('Source handoff fixture')
        return rows.first

    source_card().get_by_role('button', name='Reuse settings', exact=True).click()
    expect(studio.get_by_role('tab', name='Image', exact=True)).to_have_attribute('aria-selected', 'true')
    expect(studio.get_by_label('Prompt', exact=True)).to_have_value('Source handoff fixture')
    expect(studio.get_by_label('Aspect ratio', exact=True)).to_have_value('16:9')
    expect(studio.get_by_label('Resolution', exact=True)).to_have_value('2K')
    expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).not_to_be_checked()
    assert not mock['submissions'], 'Reuse must create only a draft'
    source_card().get_by_role('button', name='Edit image', exact=True).click()
    expect(studio.get_by_text('Ready in this workspace', exact=True)).to_be_visible()
    expect(studio.get_by_label('Image URL', exact=True)).to_be_disabled()
    assert not mock['submissions'], 'Image handoff must not generate automatically'

    def submit(prompt, button):
        studio.get_by_label('Prompt', exact=True).fill(prompt)
        studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True).check()
        prior = len(mock['submissions'])
        studio.locator('form').get_by_role('button', name=button, exact=True).click()
        expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).not_to_be_checked()
        assert len(mock['submissions']) == prior + 1, mock['submissions']
        return mock['submissions'][-1]

    edited = submit('Edit the owned image, preserving its subject.', 'Generate image')
    assert edited['image_id'] == fixture['artifact']['id'] and 'image_url' not in edited, edited
    source_card().get_by_role('button', name='Animate', exact=True).click()
    expect(studio.get_by_role('tab', name='Video', exact=True)).to_have_attribute('aria-selected', 'true')
    expect(studio.get_by_label('Image reference role', exact=True)).to_have_value('first_frame')
    studio.get_by_label('Negative prompt', exact=True).fill('Avoid flicker and distorted hands.')
    animated = submit('Animate the owned image with a slow camera move.', 'Generate video')
    assert animated['image_id'] == fixture['artifact']['id'] and animated['image_role'] == 'first_frame', animated
    assert animated['negative_prompt'] == 'Avoid flicker and distorted hands.' and animated['operation'] == 'video', animated
    audit('animation-owned-image')

    studio.get_by_role('tab', name='Image', exact=True).click()
    studio.get_by_label('Upload image reference', exact=True).set_input_files(
        {'name': 'too-large.png', 'mimeType': 'image/png', 'buffer': bytes(8 * 1024 * 1024 + 1)})
    expect(studio.get_by_role('alert')).to_contain_text('between 1 byte and 8 MiB')
    assert mock['uploads'] == 0, 'An oversized image must be rejected before upload'
    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/media/inputs' and response.request.method == 'POST') as uploaded:
        studio.get_by_label('Upload image reference', exact=True).set_input_files(
            {'name': 'advanced-local-reference.png', 'mimeType': 'image/png', 'buffer': fixture['image']})
    assert uploaded.value.ok, uploaded.value.text()
    assert mock['uploads'] == 1
    expect(studio.locator('.studio-reference-selection')).to_contain_text('advanced-local-reference.png')
    expect(studio.get_by_text('Ready in this workspace', exact=True)).to_be_visible()
    # The UI has parsed the real upload receipt. Verify its identity directly in
    # the owned host ledger, then compare the submitted ID and exported bytes.
    owned_upload = next(row for row in state.artifacts.list(owner.id, project_id)
                        if row['title'] == 'advanced-local-reference.png')
    uploaded_row = state.artifacts.get(owner.id, owned_upload['id'])
    assert uploaded_row['project'] == project_id and uploaded_row['mime'] == 'image/png', uploaded_row
    local = submit('Use the local image as an editorial reference.', 'Generate image')
    assert local['image_id'] == uploaded_row['id'] and 'image_url' not in local, local
    binary = context.request.get(base + f'/api/media/artifacts/{uploaded_row["id"]}/export?format=original')
    assert binary.ok and binary.body() == fixture['image'], 'Owned upload/export bytes changed'
    audit('local-image')

    # No remix model is inferred from ordinary video generation metadata.
    studio.get_by_role('tab', name='Video', exact=True).click()
    studio.get_by_role('group', name='Video tools', exact=True).get_by_role('button', name='Remix video', exact=True).click()
    expect(studio.get_by_label('Model', exact=True)).to_be_disabled()
    expect(studio.get_by_role('button', name='Generate remix', exact=True)).to_be_disabled()
    audit('remix-unavailable')
    mock['catalog']['models'].append(remix_model)
    page.goto(target)
    expect(studio.locator('.studio-scope')).to_have_text('Project workspace')
    studio.get_by_role('tab', name='Video', exact=True).click()
    studio.get_by_role('group', name='Video tools', exact=True).get_by_role('button', name='Remix video', exact=True).click()
    expect(studio.get_by_label('Model', exact=True)).to_have_value(remix_model['id'])
    expect(studio.get_by_label('Video URL', exact=True)).to_have_attribute('required', '')
    expect(studio.get_by_label('Upload image reference', exact=True)).to_have_count(0)
    studio.get_by_label('Video URL', exact=True).fill('https://media.example.com/source.mp4')
    remix = submit('Restyle the source video as a watercolour film.', 'Generate remix')
    assert remix['operation'] == 'remix' and remix['video_url'] == 'https://media.example.com/source.mp4', remix
    assert 'image_id' not in remix, remix
    audit('remix-verified')

    studio.get_by_role('group', name='Video tools', exact=True).get_by_role('button', name='Content factory', exact=True).click()
    factory = studio.get_by_role('region', name='Content factory', exact=True)
    expect(factory).to_be_visible()
    factory.get_by_label('What are we making?', exact=True).fill('A six-shot campaign for handmade ceramics.')
    factory.get_by_label('Scene count', exact=True).fill('6')
    before_draft = len(mock['submissions'])
    factory.get_by_role('button', name='Build scene drafts', exact=True).click()
    expect(factory.get_by_role('article')).to_have_count(6)
    assert len(mock['submissions']) == before_draft, 'Drafting must not call a provider'
    factory.get_by_label('Scene 1 prompt', exact=True).fill('Edited first scene: reveal the handmade bowl.')
    expect(factory.get_by_role('button', name='Queue 6 scenes', exact=True)).to_be_disabled()
    audit('factory-drafts')
    mock['batch'] = True
    factory.get_by_role('checkbox', name=re.compile('^I approve provider charges for 6 scenes')).check()
    factory.get_by_role('button', name='Queue 6 scenes', exact=True).click()
    expect(factory.get_by_role('alert')).to_contain_text('batch paused')
    expect(factory.get_by_role('button', name='Queue 4 scenes', exact=True)).to_be_disabled()
    assert mock['batch_count'] == 2 and len(mock['submissions']) == before_draft + 2, mock
    assert mock['submissions'][-2]['prompt'] == 'Edited first scene: reveal the handmade bowl.'
    expect(factory.get_by_label('Scene 3 prompt', exact=True)).to_be_enabled()
    expect(factory.get_by_label('Scene 2 prompt', exact=True)).to_be_disabled()
    warning_geometry = factory.get_by_role('alert').evaluate("""async element => {
        const box = element.getBoundingClientRect();
        const queue = [...element.closest('.factory-delivery').querySelectorAll('button')]
            .find(button => /^Queue /.test(button.textContent.trim())).getBoundingClientRect();
        const intersection = await new Promise(resolve => {
            const observer = new IntersectionObserver(entries => {
                observer.disconnect(); resolve(entries[0].intersectionRect.height);
            }); observer.observe(element);
        });
        return {top: Math.round(box.top), bottom: Math.round(box.bottom),
            height: box.height, visible_height: intersection,
            gap_to_queue: Math.round(queue.top - box.bottom)};
    }""")
    assert warning_geometry['visible_height'] >= warning_geometry['height'] * .9, warning_geometry
    assert 0 <= warning_geometry['gap_to_queue'] <= 240, warning_geometry
    page.screenshot(path=str(destination / f'factory-paused-at-queue-{theme}-{width}.png'), full_page=True)
    audit('factory-paused')
    report['states'][-1]['paused_warning_at_queue'] = warning_geometry
    assert not mock['errors'], mock['errors']
    report['checks'].append({'theme': theme, 'width': width, 'check': 'advanced-studio-flow',
        'real_history_rows': 124, 'loaded_counts': counts, 'search_old_row_and_status': True,
        'reuse_is_draft_only': True, 'edit_and_animate_owned_image_ids': True,
        'local_upload_and_authenticated_export': True, 'remix_metadata_gated': True,
        'oversized_image_rejected_before_upload': True,
        'factory_editable_template_scenes': 6, 'factory_drafting_provider_calls': 0,
        'batch_stopped_after_unknown_receipt': True, 'batch_submissions': mock['batch_count'],
        'generation_submissions': len(mock['submissions'])})


def focused_exercise(page, context, base, session, project, owner, state, destination, report, theme, width, axe):
    """Composition/pricing and actual IntersectionObserver proofs, isolated from prior modes."""
    mode = os.environ['TERMX_STUDIO_FOCUS']
    report['proof'] = 'actual-host-studio-' + mode
    fixture = seed_history(state, owner, project)
    project_id = project['id']
    with state.studio.db() as db:
        db.execute('DELETE FROM studio_jobs WHERE owner=? AND project IS ?', (owner.id, project_id))
    trace = {'catalog': None, 'submissions': [], 'errors': [], 'pricing_reads': 0,
             'public_reads': [], 'override_pricing': False, 'pricing_fail': True,
             'hold_history': False, 'history_response': None, 'binary': [],
             'hold_binary': False, 'pending_binary': [], 'active_binary': 0, 'max_binary': 0}
    origin = urlsplit(base)

    def handle(route):
        request, parsed = route.request, urlsplit(route.request.url)
        if parsed.scheme in ('http', 'https') and (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            report['external_browser_requests'].append(request.url); route.abort('blockedbyclient'); return
        if parsed.path == '/api/media/studio/catalog' and trace['catalog']:
            route.fulfill(json=trace['catalog']); return
        if parsed.path == '/api/media/studio/pricing':
            trace['pricing_reads'] += 1
        if parsed.path == '/api/media/studio/history' and trace['hold_history'] and parse_qs(parsed.query).get('cursor'):
            trace['history_response'] = (route, route.fetch()); return
        if re.fullmatch(r'/api/media/artifacts/[A-Za-z0-9_-]+/export', parsed.path):
            trace['binary'].append(parsed.path)
            if trace['hold_binary']:
                trace['active_binary'] += 1
                trace['max_binary'] = max(trace['max_binary'], trace['active_binary'])
                trace['pending_binary'].append((route, route.fetch())); return
        if request.method == 'POST' and parsed.path == '/api/media/studio/jobs':
            body = request.post_data_json
            assert body['project_id'] == project_id and body['acknowledge_billing'] is True
            trace['submissions'].append(body)
            report['provider_job_requests'].append({'theme': theme, 'width': width, 'payload': body})
            identifier, now = f'focused-{theme}-{width}-{len(trace["submissions"])}', time()
            kind = next(row['kind'] for row in trace['catalog']['models'] if row['id'] == body['model'])
            with state.studio.db() as db:
                db.execute('''INSERT INTO studio_jobs
                    (id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,task_id,
                     created,updated,outputs,error,parameters) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identifier, owner.id, project_id, body['request_id'], 'browser-fixture', body['provider_id'],
                     'openrouter', body['model'], kind, body['prompt'], 'completed', None, now, now, '[]', None,
                     json.dumps(state.studio.parameters(body))))
            route.fulfill(status=202, json=state.studio.get(owner.id, identifier)); return
        if request.method == 'POST' and parsed.path.endswith('/refresh'):
            route.fulfill(json=state.studio.get(owner.id, parsed.path.split('/')[-2])); return
        route.continue_()

    def guarded(route):
        try: handle(route)
        except Exception as error:
            trace['errors'].append(str(error)); route.fulfill(status=500, json={'detail': str(error)})

    context.route('**/*', guarded)
    target = f'{base}/?session={session["id"]}&layout=artifacts'
    page.goto(target)
    page.get_by_label('Username', exact=True).fill('studio-owner')
    page.get_by_label('Password', exact=True).fill('studio-fixture-password-123')
    page.get_by_role('button', name='Continue with password', exact=True).click()
    studio = page.get_by_role('region', name='Media studio', exact=True)
    expect(studio.locator('.studio-scope')).to_have_text('Project workspace', timeout=60000)
    expect(studio.get_by_text('Connect a compatible provider', exact=True)).to_be_visible()
    fixture_path = Path(__file__).parent / 'fixtures/media_studio_openrouter_capabilities.json'
    public_fixture = json.loads(fixture_path.read_text())
    report['recorded_public_capabilities'] = {'path': 'tests/fixtures/media_studio_openrouter_capabilities.json',
        'sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(), 'retrieved_at': public_fixture['retrieved_at']}

    def public_only(request):
        assert request.method == 'GET' and request.url.host == 'openrouter.ai'
        assert not request.headers.get('authorization'), 'Public metadata must not receive a key'
        path = request.url.path.removeprefix('/api/v1')
        if path == '/models': path += '?' + request.url.query.decode()
        trace['public_reads'].append(path)
        if trace['override_pricing']:
            if path.startswith('/images/models/'):
                if trace['pricing_fail']: return httpx.Response(503, json={'error': 'Offline pricing fixture'})
                model_id = path.removeprefix('/images/models/').removesuffix('/endpoints')
                return httpx.Response(200, json={'id': model_id, 'endpoints': [{'provider_name': 'Public rate fixture',
                    'model_id': model_id, 'pricing': [
                        {'billable': 'output_image', 'unit': 'image', 'cost_usd': '0.123', 'variant': '4K'},
                        {'billable': 'input_text', 'unit': 'token', 'cost_usd': '0.000002'}]}]})
            if path == '/videos/models':
                return httpx.Response(200, json={'data': [{'id': 'google/veo-3.1', 'pricing_skus': {
                    'cents_per_second_output': '35', 'video_tokens_1080p': '0.00007',
                    'minimum_cents_per_generation': '20'}}]})
        return httpx.Response(200, json=public_fixture['responses'][path]['body'])

    client = httpx.AsyncClient(transport=httpx.MockTransport(public_only))
    _focused_clients.append(client)
    state.studio.openrouter.client = client
    try:
        state.studio._catalog_at = 0
        catalog_response = context.request.get(base + '/api/media/studio/catalog?' + urlencode({'project_id': project_id}))
        assert catalog_response.ok, catalog_response.text()
        catalog = catalog_response.json()
        image = copy.deepcopy(next(row for row in catalog['models'] if row['id'] == 'google/gemini-3-pro-image'))
        video = copy.deepcopy(next(row for row in catalog['models'] if row['id'] == 'google/veo-3.1'))
        assert image['image_reference_limits']['app_max'] == 5, image
        single = {**copy.deepcopy(image), 'id': 'fixture/single-image', 'label': 'Single-image capability fixture',
                  'image_reference_limits': {'min': 0, 'max': 1, 'app_max': 1}}
        trace['catalog'] = {'models': [image, single, video], 'accounts': [
            {'id': 'studio-fixture', 'name': 'Focused fixture account', 'adapter': 'openrouter',
             'configured': True, 'capabilities': ['image', 'video']}], 'catalog_source': 'live'}
        trace['override_pricing'] = True
        state.studio_pricing._cache.clear()

        def audit(label):
            baseline._audit(page, studio, destination, report, mode + '-' + label, theme, width, axe)

        if mode == 'lazy':
            # Newer40 no-output jobs keep the older40 binary outputs offscreen.
            with state.studio.db() as db:
                for index in range(80):
                    output = []
                    if index < 40:
                        artifact = state.artifacts.create(owner.id, project_id, 'image', f'Lazy image {index}',
                                                          blob=fixture['image'], mime='image/png')
                        output = [f'/api/media/artifacts/{artifact["id"]}/export?format=original']
                    db.execute('''INSERT INTO studio_jobs
                        (id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,task_id,
                         created,updated,outputs,error,parameters) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (f'lazy-{index:03d}', owner.id, project_id, f'lazy-request-{index}', 'fixture', 'studio-fixture',
                         'openrouter', image['id'], 'image', f'Lazy image {index:03d}', 'completed', None,
                         1800000000 + index, 1800000000 + index, json.dumps(output), None, '{}'))
            page.goto(target)
            expect(studio.get_by_label('Provider account', exact=True)).to_have_value('studio-fixture')
            studio.get_by_role('tab', name='Library', exact=True).click()
            expect(studio.get_by_role('article')).to_have_count(40)
            trace['hold_history'] = True
            studio.get_by_role('button', name='Load more generations', exact=True).click()
            for _ in range(100):
                if trace['history_response'] is not None: break
                page.wait_for_timeout(50)
            assert trace['history_response'] is not None
            studio.locator('.studio-body,.studio-canvas').evaluate_all('elements => elements.forEach(element => element.scrollTop = 0)')
            pending_route, response = trace['history_response']
            pending_route.fulfill(response=response)
            expect(studio.get_by_role('article')).to_have_count(80)
            page.wait_for_timeout(150)
            assert not trace['binary'], trace['binary']
            audit('40-offscreen-zero-fetch')
            offscreen = studio.get_by_role('article').filter(has=page.locator('.studio-output')).first
            # Activate the actual Download UI before intersection, without an IO stub.
            with page.expect_download() as pending:
                offscreen.get_by_role('link', name=re.compile('^Download Generation')).evaluate('element => element.click()')
            output_file = destination / f'lazy-explicit-export-{theme}-{width}.png'
            pending.value.save_as(output_file)
            assert output_file.read_bytes() == fixture['image']
            assert len(trace['binary']) == 1, trace['binary']
            trace['binary'].clear(); trace['hold_binary'] = True
            offscreen.scroll_into_view_if_needed()
            page.wait_for_timeout(150)
            assert trace['pending_binary'] and trace['max_binary'] <= 3
            # Release genuine host responses while recording outstanding browser transfers.
            for _ in range(12):
                while trace['pending_binary']:
                    route, response = trace['pending_binary'].pop(0)
                    trace['active_binary'] -= 1
                    route.fulfill(response=response)
                page.wait_for_timeout(30)
                if not trace['pending_binary']: break
            trace['hold_binary'] = False
            assert 0 < len(trace['binary']) < 40 and trace['max_binary'] <= 3, trace
            audit('viewport-bounded-fetch')
            report['checks'].append({'theme': theme, 'width': width, 'check': 'lazy-gallery',
                'offscreen_outputs': 40, 'offscreen_binary_requests': 0,
                'explicit_export_before_intersection': True, 'visible_preview_requests': len(trace['binary']),
                'maximum_outstanding_preview_transfers': trace['max_binary']})
            return

        page.goto(target)
        expect(studio.get_by_label('Model', exact=True)).to_have_value(image['id'])
        assert trace['pricing_reads'] == 0
        studio.get_by_label('Prompt', exact=True).fill('Compose these references into an editorial image.')
        composition = studio.get_by_role('group', name='Ordered image references', exact=True)
        identifiers = []
        for index in range(5):
            field = 'Upload image reference' if index == 0 else 'Upload additional image reference'
            filename = f'composition-{theme}-{width}-{index}.png'
            studio.get_by_label(field, exact=True).set_input_files({'name': filename, 'mimeType': 'image/png', 'buffer': fixture['image']})
            expect(composition.get_by_role('listitem')).to_have_count(index + 1)
            uploaded = next(row for row in state.artifacts.list(owner.id, project_id) if row['title'] == filename)
            identifiers.append(uploaded['id'])
            if index:
                if index == 1:
                    composition.get_by_role('button', name='Move image 2 up', exact=True).click()
                    identifiers.reverse()
                studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True).check()
                studio.locator('form').get_by_role('button', name='Generate image', exact=True).click()
                expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).not_to_be_checked()
                payload = trace['submissions'][-1]
                assert payload['image_references'] == [{'image_id': identifier} for identifier in identifiers], payload
                assert 'image_id' not in payload and 'image_url' not in payload
        expect(studio.get_by_label('Upload additional image reference', exact=True)).to_be_disabled()
        expect(studio.get_by_label('Additional image URL', exact=True)).to_be_disabled()
        audit('five-ordered-images')
        before = len(trace['submissions'])
        studio.get_by_label('Model', exact=True).select_option(single['id'])
        expect(composition.get_by_role('listitem')).to_have_count(5)
        studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True).check()
        studio.locator('form').get_by_role('button', name='Generate image', exact=True).click()
        expect(studio.get_by_role('alert')).to_contain_text('image references')
        assert len(trace['submissions']) == before
        audit('single-model-blocks-extras')
        studio.get_by_label('Model', exact=True).select_option(image['id'])
        csrf = page.evaluate("decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('termx_csrf=')).slice(11))")
        subject_ids = identifiers[:3]
        subject_name = f'Composition set {theme} {width}'
        saved = context.request.post(base + '/api/media/studio/subjects', headers={'X-Termx-CSRF': csrf, 'Origin': base},
            data={'project_id': project_id, 'name': subject_name, 'description': 'Ordered reference fixture', 'image_ids': subject_ids})
        assert saved.ok, saved.text()
        studio.get_by_role('button', name='Saved subjects & products', exact=True).click()
        studio.get_by_role('article', name=subject_name, exact=True).get_by_role('button', name='Use reference set from ' + subject_name, exact=True).click()
        assert len(trace['submissions']) == before
        expect(composition.get_by_role('listitem')).to_have_count(3)
        studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True).check()
        studio.locator('form').get_by_role('button', name='Generate image', exact=True).click()
        expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).not_to_be_checked()
        assert trace['submissions'][-1]['image_references'] == [{'image_id': identifier} for identifier in subject_ids]
        audit('saved-set-order')

        # Sanitize a signed URL through the real persisted parameter boundary, then reuse it.
        signed = [{'image_id': subject_ids[0]}, {'image_url': 'https://media.example.org/temporary.png?token=fixture-only'}, {'image_id': subject_ids[2]}]
        with state.studio.db() as db:
            db.execute('''INSERT INTO studio_jobs
                (id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,task_id,
                 created,updated,outputs,error,parameters) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('signed-reentry', owner.id, project_id, 'signed-reentry-request', 'fixture', 'studio-fixture', 'openrouter',
                 image['id'], 'image', 'Signed composition reentry fixture', 'completed', None, time(), time(), '[]', None,
                 json.dumps(state.studio.parameters({'operation': 'image', 'image_references': signed}))))
        studio.get_by_role('tab', name='Library', exact=True).click()
        studio.get_by_label('Search generations', exact=True).fill('Signed composition reentry fixture')
        row = studio.get_by_role('article').filter(has_text='Signed composition reentry fixture')
        expect(row).to_have_count(1)
        row.get_by_role('button', name='Reuse settings', exact=True).click()
        expect(studio.get_by_label('Missing image reference 2', exact=True)).to_be_visible()
        assert 'token=fixture-only' not in studio.inner_text()
        before_missing = len(trace['submissions'])
        studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True).check()
        studio.locator('form').get_by_role('button', name='Generate image', exact=True).click()
        expect(studio.get_by_role('alert')).to_contain_text('missing temporary image reference')
        assert len(trace['submissions']) == before_missing
        studio.get_by_label('Missing image reference 2', exact=True).fill('https://media.example.org/reentered.png')
        studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True).check()
        studio.locator('form').get_by_role('button', name='Generate image', exact=True).click()
        expect(studio.get_by_role('checkbox', name='I approve provider charges for this generation.', exact=True)).not_to_be_checked()
        assert trace['submissions'][-1]['image_references'][1] == {'image_url': 'https://media.example.org/reentered.png'}
        audit('signed-slot-reentry')

        # Rates are parsed by the genuine authorized server endpoint, from public GET fixtures only.
        assert trace['pricing_reads'] == 0
        unauthenticated = httpx.get(base + '/api/media/studio/pricing', params={'model': image['id'], 'project_id': project_id}, trust_env=False)
        assert unauthenticated.status_code == 401
        rates = studio.get_by_role('button', name='Published rates', exact=True)
        rates.click()
        expect(studio.get_by_text('Published rates are unavailable. Check the provider before generating.', exact=True)).to_be_visible()
        trace['pricing_fail'] = False
        rates.click(); rates.click()
        panel = studio.get_by_role('region', name='Published unit rates', exact=True)
        expect(panel.locator('.pricing-rates li')).to_have_count(2)
        expect(panel.locator('.pricing-rates li').filter(has_text='USD 0.123')).to_contain_text('per image')
        expect(panel.locator('.pricing-rates li').filter(has_text='USD 0.000002')).to_contain_text('per token')
        expect(panel).to_contain_text('4K')
        assert not re.search(r'\bfree\b|\btotal\b', panel.inner_text(), re.I)
        assert not re.search(r'\bfree\b', page.locator('.conversation-home').inner_text(), re.I)
        panel.screenshot(path=str(destination / f'published-image-rates-{theme}-{width}.png'))
        audit('published-image-units')
        studio.get_by_role('tab', name='Video', exact=True).click()
        expect(studio.get_by_label('Model', exact=True)).to_have_value(video['id'])
        studio.get_by_role('button', name='Published rates', exact=True).click()
        panel = studio.get_by_role('region', name='Published unit rates', exact=True)
        expect(panel.locator('.pricing-rates li')).to_have_count(3)
        expect(panel.locator('.pricing-rates li').filter(has_text='USD 0.35')).to_contain_text('per second')
        expect(panel.locator('.pricing-rates li').filter(has_text='USD 0.00007')).to_contain_text('per token')
        expect(panel.locator('.pricing-rates li').filter(has_text='USD 0.2')).to_contain_text('per generation')
        expect(panel).to_contain_text('1080p')
        panel.screenshot(path=str(destination / f'published-video-rates-{theme}-{width}.png'))
        audit('published-video-units')
        assert not trace['errors'], trace['errors']
        report['checks'].append({'theme': theme, 'width': width, 'check': 'composition-and-pricing',
            'ordered_counts': [2, 3, 4, 5], 'legacy_scalars_omitted': True, 'reorder_preserved': True,
            'single_image_model_preserves_and_blocks_extras': True, 'saved_set_order': True,
            'signed_url_slot_requires_reentry': True, 'pricing_reads_before_expand': 0,
            'pricing_scope_401': True, 'unavailable_metadata_recovers': True,
            'image_token_second_and_cents_units': True, 'public_metadata_calls_have_no_credentials': True,
            'no_free_chat_placeholder_or_generation_total': True})
    finally:
        for route, response in trace['pending_binary']:
            try: route.fulfill(response=response)
            except Exception: pass


def run(destination):
    # Reuse the baseline's actual AppState, real login, immutable build snapshot,
    # four viewport/theme combinations, offline discovery transport, and cleanup.
    original = baseline._exercise
    try:
        baseline._exercise = focused_exercise if os.environ.get('TERMX_STUDIO_FOCUS') else exercise
        return baseline.run(destination)
    finally:
        baseline._exercise = original
        for client in _focused_clients:
            asyncio.run(client.aclose())
        _focused_clients.clear()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: workspace_studio_advanced_e2e.py OUTPUT_DIR')
    print(json.dumps(run(sys.argv[1]), indent=2))
