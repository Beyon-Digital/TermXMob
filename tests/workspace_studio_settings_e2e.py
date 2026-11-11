"""Opt-in Studio Settings proof on a real isolated, password-authenticated host.

Run after a frozen production build:
  PYTHONPATH=src .venv/bin/python -B tests/workspace_studio_settings_e2e.py OUTPUT UI_DIST

The UI, settings HTTP API, provider GraphQL mutations, stores, local file inputs,
and pre-dispatch limits are real. Public catalog discovery and the single
intentional generation receipt are explicit fixtures. No paid provider request,
native application, live daemon, or default user profile is used.
"""
from __future__ import annotations

import asyncio
import base64
import io
import hashlib
from datetime import datetime, timezone
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
from urllib.parse import urlsplit
import uuid

import httpx
from PIL import Image
from playwright.sync_api import expect, sync_playwright
import uvicorn

from workspace_ui_snapshot import snapshot_ui


CONSENT = 'I approve provider charges for this generation.'
LIMIT_NAMES = ('daily_generations', 'concurrent_generations', 'max_video_seconds')


def _phase(report, name, **details):
    report['progress'] = {'phase': name, **details}
    print(json.dumps(report['progress']), flush=True)


def _wait(page, check, message, seconds=15):
    deadline = monotonic() + seconds
    while not check():
        if monotonic() > deadline:
            raise AssertionError(message)
        page.wait_for_timeout(50)


def _headers(context, base):
    return {'Content-Type': 'application/json', 'Origin': base,
            'X-Termx-CSRF': next(cookie['value'] for cookie in context.cookies()
                                 if cookie['name'] == 'termx_csrf')}


def _settings(context, base):
    response = context.request.get(base + '/api/media/studio/settings')
    assert response.ok, response.text()
    return response.json()


def _save(context, base, **changes):
    settings = {**_settings(context, base)['settings'], **changes}
    response = context.request.put(base + '/api/media/studio/settings',
        data=settings, headers=_headers(context, base))
    assert response.ok, response.text()
    return response.json()


def _open_settings(page):
    trigger = page.locator('.studio-sidebar-context .sidebar-foot').get_by_role('button', name='Settings', exact=True)
    if not trigger.is_visible():
        page.get_by_role('navigation', name='Workspace navigation', exact=True).get_by_role(
            'button', name='Show sidebar', exact=True).click()
    trigger.focus()
    page.keyboard.press('Enter')
    surface = page.get_by_role('region', name='Studio settings', exact=True)
    expect(surface).to_be_visible()
    expect(surface.get_by_role('tab', name='Providers', exact=True)).to_be_visible()
    return surface


def _tab(page, surface, name):
    tab = surface.get_by_role('tab', name=name, exact=True)
    tab.click()
    expect(tab).to_have_attribute('aria-selected', 'true')
    return tab


def _create(page):
    nav = page.get_by_role('navigation', name='Studio views', exact=True)
    if not nav.is_visible():
        page.get_by_role('navigation', name='Workspace navigation', exact=True).get_by_role(
            'button', name='Show sidebar', exact=True).click()
    nav.get_by_role('button', name='Create', exact=True).click()
    studio = page.get_by_role('region', name='Media studio', exact=True)
    expect(studio).to_be_visible()
    return studio


def _audit(page, surface, output, report, label, theme, width, axe, preserve_scroll=False):
    expect(page.locator('html')).to_have_attribute('data-theme', theme)
    geometry = page.evaluate('''() => ({
      viewport:{width:innerWidth,height:innerHeight},
      page_overflow:document.documentElement.scrollWidth>innerWidth+1,
      reduced_motion:matchMedia('(prefers-reduced-motion:reduce)').matches,
      visible_dialogs:[...document.querySelectorAll('dialog[open]')].map(e=>e.getAttribute('aria-label')||e.innerText.slice(0,100))
    })''')
    geometry['surface_overflow'] = surface.evaluate('e=>e.scrollWidth>e.clientWidth+1')
    assert axe and Path(axe).is_file(), 'axe-core must be installed for this proof'
    if not page.evaluate('typeof axe !== "undefined"'):
        page.add_script_tag(path=str(axe))
    result = page.evaluate('''async()=>{const result=await axe.run(document,{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});
      return JSON.parse(JSON.stringify(result,(key,value)=>value instanceof Error ?
        {name:value.name,message:value.message,stack:value.stack}:value));}''')
    violations = [{'id': row['id'], 'impact': row['impact'],
        'nodes': [{'target': node['target'], 'summary': node.get('failureSummary')}
                  for node in row['nodes']]} for row in result['violations']]
    filename = f'{label}-{theme}-{width}.png'
    if not preserve_scroll: surface.evaluate('e=>{e.scrollTop=0}')
    page.screenshot(path=str(output / filename), full_page=True)
    entry = {'state': label, 'theme': theme, **geometry, 'screenshot': filename,
             'axe_version': page.evaluate('axe.version'), 'violations': violations,
             'incomplete': result['incomplete']}
    report['states'].append(entry)
    _phase(report, 'captured', state=label, theme=theme, width=width)
    assert not geometry['page_overflow'] and not geometry['surface_overflow'], entry
    assert geometry['reduced_motion'], entry
    assert not violations, entry


def _seed_job(state, owner, project, provider, model, status):
    identifier = 'settings-proof-' + uuid.uuid4().hex
    now = time()
    with state.studio.db() as db:
        db.execute('''INSERT INTO studio_jobs(id,owner,project,request_id,digest,
          provider_id,adapter,model,kind,prompt,status,created,updated)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (identifier, owner.id, project['id'], identifier, 'explicit-limit-fixture',
           provider, 'openrouter', model, 'image', 'Explicit local usage fixture', status, now, now))
    return identifier


def _direct_caps(page, context, base, state, owner, project, second_project, provider, models, report):
    original = _settings(context, base)['settings']
    image = next(row for row in models if row['id'] == 'google/gemini-3.1-flash-image')
    other = next(row for row in models if row['id'] == 'google/gemini-3-pro-image')
    video = next(row for row in models if row['id'] == 'google/veo-3.1')
    defaults = {'image': {'provider_id': provider, 'model': image['id']}}
    nulls = dict.fromkeys(LIMIT_NAMES)
    _save(context, base, defaults=defaults, enabled_models=None, limits=nulls)
    before = len(report['backend_provider_requests'])
    def submit(project_id, model=image, **extra):
        payload = {'request_id': 'settings-cap-' + uuid.uuid4().hex,
            'project_id': project_id, 'provider_id': provider, 'model': model['id'],
            'operation': model['kind'], 'prompt': 'Explicit cap rejection test',
            'acknowledge_billing': True, **extra}
        return context.request.post(base + '/api/media/studio/jobs',
            data=payload, headers=_headers(context, base))
    _save(context, base, enabled_models=[image['id'], video['id']])
    rejected = submit(project['id'], other)
    assert rejected.status == 403, rejected.text()
    _save(context, base, limits={**nulls, 'max_video_seconds': 4})
    unknown = submit(project['id'], video)
    too_long = submit(second_project['id'], video, duration=8)
    assert unknown.status == too_long.status == 409, (unknown.text(), too_long.text())
    _seed_job(state, owner, second_project, provider, image['id'], 'failed')
    usage = _settings(context, base)['usage']
    _save(context, base, limits={**nulls, 'daily_generations': usage['daily_generations']})
    daily = submit(project['id'])
    assert daily.status == 429, daily.text()
    _seed_job(state, owner, second_project, provider, image['id'], 'unknown')
    usage = _settings(context, base)['usage']
    _save(context, base, limits={**nulls, 'concurrent_generations': usage['active_generations']})
    active = submit(project['id'])
    assert active.status == 429, active.text()
    assert len(report['backend_provider_requests']) == before, report['backend_provider_requests']
    report['checks'].append({'check': 'direct-real-api-caps-before-provider-dispatch',
        'disabled_model': rejected.status, 'unproven_video_duration': unknown.status,
        'video_too_long': too_long.status, 'cross_project_failed_counts_daily': daily.status,
        'cross_project_unknown_counts_active': active.status,
        'provider_dispatches': 0, 'usage_after_explicit_fixtures': usage})
    _save(context, base, defaults=original['defaults'], enabled_models=original['enabled_models'], limits=original['limits'])


def _exercise(page, context, base, session, project, second_project, owner, state, output, report, theme, width, axe, full):
    page.set_default_timeout(18000)
    origin = urlsplit(base)
    fixture = {'allow_generation': False, 'real_cap': False, 'cap_submissions': [], 'submissions': [], 'receipt': None}
    def network(route):
        request = route.request
        url = urlsplit(request.url)
        if url.scheme in {'http', 'https'} and (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
            report['external_browser_requests'].append({'method': request.method, 'url': request.url})
            route.abort('blockedbyclient'); return
        if request.method == 'POST' and url.path == '/api/media/studio/jobs':
            if fixture['real_cap']:
                fixture['cap_submissions'].append(request.post_data_json)
                route.continue_(); return
            assert fixture['allow_generation'], 'Configuration triggered generation'
            payload = request.post_data_json
            assert payload['acknowledge_billing'] is True
            fixture['submissions'].append(payload)
            report['provider_job_requests'].append({'theme': theme, 'width': width, 'payload': payload})
            now = time()
            fixture['receipt'] = {'id': 'explicit-ui-receipt-' + uuid.uuid4().hex,
                'project_id': payload.get('project_id'), 'provider_id': payload['provider_id'],
                'model': payload['model'], 'kind': 'image', 'prompt': payload['prompt'],
                'status': 'queued', 'created': now, 'updated': now, 'outputs': [], 'parameters': {}}
            route.fulfill(status=202, json=fixture['receipt'])
            return
        if request.method == 'POST' and fixture['receipt'] and url.path == '/api/media/studio/jobs/' + fixture['receipt']['id'] + '/refresh':
            route.fulfill(json=fixture['receipt']); return
        route.continue_()
    context.route('**/*', network)
    page.goto(base + '/?session=' + session['id'])
    page.get_by_label('Username', exact=True).fill('studio-owner')
    page.get_by_label('Password', exact=True).fill('studio-fixture-password-123')
    page.get_by_role('button', name='Continue with password', exact=True).click()
    picker = page.get_by_role('group', name='Workspace', exact=True)
    expect(picker.get_by_role('button', name='Agent', exact=True)).to_have_attribute('aria-pressed', 'true', timeout=60000)
    message = page.get_by_role('textbox', name='Message', exact=True)
    agent_draft = f'Unsent Agent draft remains intact {theme} {width}'
    message.fill(agent_draft)
    # Agent's established settings remain its own modal.
    if full:
        trigger = page.locator('.sidebar-foot').get_by_role('button', name='Settings', exact=True)
        if not trigger.is_visible():
            page.get_by_role('navigation', name='Workspace navigation', exact=True).get_by_role('button', name='Show sidebar', exact=True).click()
        trigger.click()
        settings = page.get_by_role('dialog', name='Settings', exact=True)
        expect(settings).to_be_visible()
        expect(settings.get_by_role('navigation', name='Settings categories', exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(settings).not_to_be_visible()
        expect(message).to_have_value(agent_draft)
    picker.get_by_role('button', name='Studio', exact=True).click()
    studio = page.get_by_role('region', name='Media studio', exact=True)
    expect(studio).to_be_visible()
    expect(studio.get_by_role('checkbox', name=CONSENT, exact=True)).not_to_be_checked()
    prompt_text = f'Unsent Studio draft preserved through settings {theme} {width}'
    studio.get_by_label('Prompt', exact=True).fill(prompt_text)
    surface = _open_settings(page)
    assert not page.get_by_role('dialog', name='Settings', exact=True).is_visible()
    assert not fixture['submissions'] and not report['backend_provider_requests']

    if full:
        initial = _settings(context, base)
        assert initial['settings']['defaults'] == {} and initial['settings']['enabled_models'] is None
        assert initial['settings']['limits'] == dict.fromkeys(LIMIT_NAMES), initial
        surface.get_by_role('button', name='Add provider', exact=True).click()
        form = surface.get_by_role('form', name='Add Studio provider', exact=True)
        form.get_by_label('Provider', exact=True).select_option('openrouter')
        form.get_by_label(re.compile('^Display name')).fill('Studio isolated provider')
        form.get_by_label('API key', exact=True).fill('synthetic-studio-no-network-key')
        form.get_by_role('button', name='Connect provider', exact=True).click()
        _wait(page, lambda: any(row['name'] == 'Studio isolated provider' for row in state.agent_store.list_providers()), 'Real provider GraphQL save did not persist')
        account = next(row for row in state.agent_store.list_providers() if row['name'] == 'Studio isolated provider')
        provider = account['id']
        assert state.credentials.get(provider) == 'synthetic-studio-no-network-key'
        surface.get_by_role('button', name='Edit Studio isolated provider', exact=True).click()
        edit = surface.get_by_role('form', name='Edit Studio provider', exact=True)
        expect(edit.get_by_label('Replace API key', exact=True)).to_have_value('')
        edit.get_by_label(re.compile('^Display name')).fill('Studio isolated provider edited')
        edit.get_by_role('button', name='Save provider changes', exact=True).click()
        _wait(page, lambda: state.agent_store.get_provider(provider)['name'] == 'Studio isolated provider edited', 'Real provider edit did not persist')
        assert state.credentials.get(provider) == 'synthetic-studio-no-network-key'
        metadata = context.request.post(base + '/graphql',
            data={'query': '{agent_providers {id name secret_configured}}'}, headers=_headers(context, base))
        assert metadata.ok and not metadata.json().get('errors')
        assert 'synthetic-studio-no-network-key' not in metadata.text()
        assert 'synthetic-studio-no-network-key' not in json.dumps(_settings(context, base))
        report['checks'].append({'check': 'real-provider-graphql-add-edit', 'blank_key_preserved': True,
            'secret_in_response_or_report': False, 'inference_calls': 0})
    else:
        provider = next(row['id'] for row in state.agent_store.list_providers() if row['name'] == 'Studio isolated provider edited')
    _audit(page, surface, output, report, 'settings-providers', theme, width, axe)

    # Native tabs expose Arrow/Home/End keyboard behavior and retain focus.
    providers_tab = surface.get_by_role('tab', name='Providers', exact=True)
    providers_tab.focus(); page.keyboard.press('ArrowRight')
    expect(surface.get_by_role('tab', name='Models & defaults', exact=True)).to_be_focused()
    page.keyboard.press('End')
    expect(surface.get_by_role('tab', name='Generation limits', exact=True)).to_be_focused()
    page.keyboard.press('Home'); expect(providers_tab).to_be_focused()
    _tab(page, surface, 'Models & defaults')
    catalog = _settings(context, base)['catalog']
    models = catalog['models']
    image = next(row for row in models if row['id'] == 'google/gemini-3.1-flash-image')
    disabled_image = next(row for row in models if row['id'] == 'google/gemini-3-pro-image')
    video = next(row for row in models if row['id'] == 'google/veo-3.1')
    if full:
        surface.locator('.studio-settings-model-list label').filter(has_text=disabled_image['id']).get_by_role('checkbox').uncheck()
        group = surface.get_by_role('group', name='Images', exact=True)
        group.get_by_label('Images default provider', exact=True).select_option(provider)
        group.get_by_label('Images default model', exact=True).select_option(image['id'])
        group.get_by_label('Images aspect ratio', exact=True).select_option('9:16')
        group.get_by_label('Images resolution', exact=True).select_option('2K')
        surface.get_by_role('button', name='Save Studio settings', exact=True).click()
        _wait(page, lambda: _settings(context, base)['settings']['defaults'].get('image', {}).get('model') == image['id'], 'Studio model defaults did not save')
        saved = _settings(context, base)['settings']
        assert saved['defaults']['image'] == {'provider_id': provider, 'model': image['id'], 'aspect_ratio': '9:16', 'resolution': '2K'}, saved
        assert disabled_image['id'] not in saved['enabled_models'], saved
        report['checks'].append({'check': 'real-settings-model-save-readback', 'revision': saved['revision'],
            'default': saved['defaults']['image'], 'disabled_model': disabled_image['id']})
    _audit(page, surface, output, report, 'settings-models', theme, width, axe)
    surface.get_by_role('group', name='Images', exact=True).scroll_into_view_if_needed()
    _audit(page, surface, output, report, 'settings-defaults', theme, width, axe, preserve_scroll=True)
    _tab(page, surface, 'Generation limits')
    if full:
        surface.get_by_role('checkbox', name='Limit daily generations', exact=True).check()
        surface.get_by_role('spinbutton', name='Daily generations limit', exact=True).fill('12')
        surface.get_by_role('checkbox', name='Limit active generations', exact=True).check()
        surface.get_by_role('spinbutton', name='Active generations limit', exact=True).fill('3')
        surface.get_by_role('checkbox', name='Limit video duration', exact=True).check()
        surface.get_by_role('spinbutton', name='Video duration limit', exact=True).fill('8')
        surface.get_by_role('button', name='Save Studio settings', exact=True).click()
        _wait(page, lambda: _settings(context, base)['settings']['limits']['daily_generations'] == 12, 'Limits did not persist')
        saved = _settings(context, base)['settings']
        assert saved['limits'] == {'daily_generations': 12, 'concurrent_generations': 3, 'max_video_seconds': 8}, saved
    _audit(page, surface, output, report, 'settings-limits', theme, width, axe)
    if full:
        # A genuine competing write produces HTTP 409. The visible draft survives.
        surface.get_by_role('spinbutton', name='Daily generations limit', exact=True).fill('13')
        competing = _save(context, base, limits={'daily_generations': 14, 'concurrent_generations': 3, 'max_video_seconds': 8})
        surface.get_by_role('button', name='Save Studio settings', exact=True).click()
        expect(surface.get_by_role('alert')).to_contain_text(re.compile('changed|reload', re.I))
        expect(surface.get_by_role('spinbutton', name='Daily generations limit', exact=True)).to_have_value('13')
        assert _settings(context, base)['settings']['limits']['daily_generations'] == 14
        report['checks'].append({'check': 'real-revision-conflict-draft-safe', 'draft': 13, 'stored': 14,
            'competing_revision': competing['settings']['revision']})
        surface.get_by_role('button', name=re.compile('Reload saved settings|Reload settings', re.I)).click()
        expect(surface.get_by_role('spinbutton', name='Daily generations limit', exact=True)).to_have_value('14')
    if width == 390:
        for viewport, label in (({'width': 320, 'height': 844}, 'settings-limits-reflow'),
                                ({'width': 390, 'height': 500}, 'settings-limits-short')):
            page.set_viewport_size(viewport)
            surface.get_by_role('tab', name='Providers', exact=True).focus()
            page.keyboard.press('End')
            focused_tab = surface.get_by_role('tab', name='Generation limits', exact=True)
            expect(focused_tab).to_be_focused()
            assert focused_tab.evaluate('''e=>{const r=e.getBoundingClientRect(),p=e.parentElement.getBoundingClientRect();return r.left>=p.left-1&&r.right<=p.right+1}'''), 'Focused settings tab must be completely visible'
            _audit(page, surface, output, report, label, theme, viewport['width'], axe)
        page.set_viewport_size({'width': 390, 'height': 844})
    assert not fixture['submissions'] and not report['backend_provider_requests']
    studio = _create(page)
    expect(studio.get_by_label('Prompt', exact=True)).to_have_value(prompt_text)
    expect(studio.get_by_label('Model', exact=True)).to_have_value(image['id'])
    assert disabled_image['id'] not in studio.get_by_label('Model', exact=True).locator('option').evaluate_all('rows=>rows.map(e=>e.value)')
    consent = studio.get_by_role('checkbox', name=CONSENT, exact=True)
    expect(consent).not_to_be_checked()
    if full:
        # A local image uploads to the genuine host; settings navigation must
        # preserve that reference and the unsent prompt without dispatch.
        studio.get_by_role('button', name=re.compile('^Reference\\b')).click()
        refs = page.get_by_role('dialog', name='Reference media', exact=True)
        expect(refs).to_be_visible()
        assert refs.evaluate('e=>e.contains(document.activeElement)')
        page.keyboard.press('Shift+Tab')
        assert refs.evaluate('e=>e.contains(document.activeElement)')
        page.keyboard.press('Tab')
        assert refs.evaluate('e=>e.contains(document.activeElement)')
        pixels = io.BytesIO(); Image.new('RGB', (72, 48), '#4d6874').save(pixels, 'PNG')
        refs.get_by_label('Upload image reference', exact=True).set_input_files(
            {'name': 'studio-settings-local.png', 'mimeType': 'image/png', 'buffer': pixels.getvalue()})
        expect(refs.get_by_text('Ready in this workspace', exact=True)).to_be_visible()
        uploaded = next(row for row in state.artifacts.list(owner.id, project['id']) if row['title'] == 'studio-settings-local.png')
        assert state.artifacts.binary(owner.id, uploaded['id'])[0] == pixels.getvalue()
        page.keyboard.press('Escape')
        _open_settings(page)
        studio = _create(page)
        expect(studio.get_by_label('Prompt', exact=True)).to_have_value(prompt_text)
        studio.get_by_role('button', name=re.compile('^Reference\\b')).click()
        refs = page.get_by_role('dialog', name='Reference media', exact=True)
        expect(refs.locator('.studio-reference-selection')).to_contain_text('studio-settings-local.png')
        assert state.artifacts.binary(owner.id, uploaded['id'])[0] == pixels.getvalue()
        page.keyboard.press('Escape')
        picker.get_by_role('button', name='Agent', exact=True).click()
        expect(message).to_have_value(agent_draft)
        page.get_by_role('button', name='Model and effort', exact=True).click()
        model_dialog = page.get_by_role('dialog', name='Model and effort', exact=True)
        expect(model_dialog.get_by_label('Session provider', exact=True).locator('option[value="' + provider + '"]')).to_have_text('Studio isolated provider edited')
        page.keyboard.press('Escape')
        expect(model_dialog).not_to_be_visible()
        picker.get_by_role('button', name='Studio', exact=True).click()
        expect(studio.get_by_label('Prompt', exact=True)).to_have_value(prompt_text)
        assert not fixture['submissions'] and not report['backend_provider_requests']
        report['checks'].append({'check': 'configuration-has-zero-generations-and-preserves-drafts',
            'billing_checkbox_exact_label_unchanged': CONSENT, 'studio_prompt_preserved': True,
            'owned_local_image_preserved': True, 'agent_draft_preserved': True, 'agent_provider_catalog_refreshed': True,
            'automatic_generations': 0, 'keyboard_tabs': 'ArrowRight/Home/End'})
        # Only this explicit checkbox + Generate click may create the mock receipt.
        expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_disabled()
        fixture['allow_generation'] = True
        consent.check(); studio.get_by_role('button', name='Generate image', exact=True).click()
        _wait(page, lambda: len(fixture['submissions']) == 1, 'Explicit generation receipt was not requested')
        expect(consent).not_to_be_checked()
        assert fixture['submissions'][0]['model'] == image['id']
        fixture['allow_generation'] = False
        _direct_caps(page, context, base, state, owner, project, second_project, provider, models, report)
        # The composer surfaces the authoritative real host rejection. Usage
        # can change between the snapshot and click; the host blocks dispatch.
        usage = _settings(context, base)['usage']
        _save(context, base, limits={'daily_generations': usage['daily_generations'],
            'concurrent_generations': None, 'max_video_seconds': None})
        page.reload(); expect(studio).to_be_visible()
        studio.get_by_label('Prompt', exact=True).fill('Explicit blocked prompt')
        consent.check()
        fixture['real_cap'] = True
        expect(studio.get_by_role('button', name='Generate image', exact=True)).to_be_enabled()
        with page.expect_response(lambda r: '/api/media/studio/jobs' in r.url and r.request.method == 'POST') as blocked:
            studio.get_by_role('button', name='Generate image', exact=True).click()
        assert blocked.value.status == 429
        expect(studio.get_by_role('alert')).to_contain_text('Studio daily accepted generation limit reached')
        expect(studio.get_by_label('Prompt', exact=True)).to_have_value('Explicit blocked prompt')
        assert len(fixture['cap_submissions']) == 1 and not report['backend_provider_requests']
        fixture['real_cap'] = False
        report['checks'].append({'check': 'frontend-authoritative-cap-refusal', 'status': 429, 'prompt_preserved': True, 'provider_dispatches': 0})
        _save(context, base, limits={'daily_generations': 12, 'concurrent_generations': 3, 'max_video_seconds': 8})
    report['checks'].append({'check': 'responsive-settings-navigation', 'theme': theme, 'width': width,
        'dedicated_studio_footer': True, 'disabled_model_not_selectable': True,
        'prompt_preserved': True, 'billing_checkbox_unchanged': True})


def _preview(browser, output, report):
    names = ('settings-defaults-dark-1440.png', 'settings-providers-light-390.png',
             'settings-limits-dark-390.png')
    cards = []
    for name in names:
        data = base64.b64encode((output / name).read_bytes()).decode()
        cards.append(f'<figure><figcaption>{name}</figcaption><img src="data:image/png;base64,{data}"></figure>')
    html = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Studio Settings verification</title><style>body{margin:0;background:#101214;color:#e8eaed;font:13px system-ui;padding:20px}h1{font-size:18px;font-weight:500;margin:0 0 14px}main{display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px}figure{margin:0;min-width:0}figcaption{font-size:11px;color:#b8bec4;padding:0 0 9px}img{display:block;width:100%;height:480px;object-fit:contain;object-position:top;background:#1c2024;border:1px solid #353c43;border-radius:8px;box-sizing:border-box}</style><h1>Studio Settings · verified browser captures</h1><main>' + ''.join(cards) + '</main></html>'
    (output / 'preview.html').write_text(html)
    page = browser.new_page(viewport={'width': 1200, 'height': 570}, reduced_motion='reduce')
    page.set_content(html)
    page.locator('img').first.wait_for(state='visible')
    page.screenshot(path=str(output / 'preview.png'), full_page=True)
    page.close()
    report['preview'] = {'html': 'preview.html', 'screenshot': 'preview.png',
        'method': 'Actual browser DOM screenshot of a CSS grid embedding original browser screenshots; no image editing.'}


def run(destination, ui_dist):
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    axe = repo / 'desktop/workspace/node_modules/axe-core/axe.min.js'
    report = {'proof': 'actual-host-studio-settings', 'result': 'running', 'states': [], 'checks': [],
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'errors': [], 'external_browser_requests': [], 'provider_job_requests': [],
        'backend_provider_requests': [], 'public_discovery_requests': [], 'paid_provider_queries': 0,
        'staged_ui_dist': str(Path(ui_dist).resolve()), 'native_or_physical_devices_tested': False,
        'limitations': ['Synthetic throwaway credentials use a real isolated in-memory CredentialStore; no live keys or user profiles are loaded.',
            'Public provider discovery is an explicit offline 503 fixture. The one intentional generation uses a browser receipt fixture, with no paid request.',
            'Pre-dispatch cap tests use explicitly inserted local failed/unknown job fixtures across two real scratch projects.',
            'Native desktop packaging and physical phone behavior are outside this browser proof.']}
    env_names = ('TERMX_CONFIG_DIR', 'TERMX_AGENTS_DIR', 'TERMX_ENGINE_STARTUP_REFRESH')
    previous = {name: os.environ.get(name) for name in env_names}
    server = worker = listener = client = browser = None
    root = Path(tempfile.mkdtemp(prefix='termx-studio-settings-'))
    try:
        os.environ.update(TERMX_CONFIG_DIR=str(root / 'config'), TERMX_AGENTS_DIR=str(root / 'agents'),
                          TERMX_ENGINE_STARTUP_REFRESH='0')
        from termx.agent.secrets import CredentialStore
        from termx.app import AppState, create_app
        class NoInference:
            async def test(self, *args, **kwargs): raise AssertionError('Settings invoked provider test')
            async def plan(self, *args, **kwargs): raise AssertionError('Settings invoked inference')
            async def turn(self, *args, **kwargs): raise AssertionError('Settings invoked inference')
        state = AppState(passcode=None, credentials=CredentialStore({}), adapter_factory=lambda *_: NoInference())
        owner = state.identity.setup_owner('studio-owner', 'studio-fixture-password-123')
        projects = []
        for name in ('Studio settings fixture', 'Other project usage fixture'):
            folder = root / ('project-' + str(len(projects)))
            folder.mkdir()
            projects.append(state.projects.register(str(folder), name=name))
        project, second_project = projects
        app = create_app(state, web_dir=snapshot_ui(root, Path(ui_dist)))
        session = state.workspace.create_session(owner, title='Studio settings verification',
            project_id=project['id'], cwd=project['path'])
        state.workspace.orchestrator.ensure(owner, session_id=session['id'])
        def provider_request(request):
            if request.method == 'GET' and request.url.path in ('/api/v1/models', '/api/v1/images/models', '/api/v1/videos/models'):
                report['public_discovery_requests'].append(request.url.path)
                return httpx.Response(503, json={'error': 'Explicit offline discovery fixture'})
            report['backend_provider_requests'].append({'method': request.method, 'path': request.url.path})
            raise AssertionError('Unexpected backend provider request')
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider_request))
        state.studio.openrouter.client = client
        state.studio.client = client
        async def no_catalog_network(_):
            return {}
        state.agent.catalogue._fetch = no_catalog_network
        report['UI_asset_snapshot'] = json.loads((root / 'fixture-ui-snapshot.json').read_text())
        (output / 'ui-manifest.json').write_text(json.dumps(report['UI_asset_snapshot'], indent=2) + '\n')
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen()
        base = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on', access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        deadline = monotonic() + 30
        while not server.started:
            if not worker.is_alive() or monotonic() > deadline: raise RuntimeError('Isolated host did not start')
            sleep(.05)
        with sync_playwright() as playwright:
            executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
            if not executable:
                executable = next((str(path) for path in (Path(playwright.chromium.executable_path),
                    Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) if path.is_file()), None)
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
            report['browser'] = {'version': browser.version, 'executable': executable or 'Playwright default'}
            for width, height, theme in ((1440, 1000, 'dark'), (1440, 1000, 'light'), (390, 844, 'dark'), (390, 844, 'light')):
                _phase(report, 'starting-case', theme=theme, width=width)
                context = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='reduce')
                context.add_init_script('localStorage.setItem("termx-shell-theme",' + json.dumps(theme) + ')')
                page = context.new_page()
                page.on('pageerror', lambda error: report['errors'].append(str(error)))
                try:
                    _exercise(page, context, base, session, project, second_project, owner, state,
                        output, report, theme, width, axe, full=width == 1440 and theme == 'dark')
                except Exception:
                    page.screenshot(path=str(output / 'failure.png'), full_page=True)
                    # Secret fields can contain only a synthetic fixture key, but
                    # clear them before persisting DOM diagnostics regardless.
                    page.locator('input[type=password]').evaluate_all('rows=>rows.forEach(e=>e.value="")')
                    (output / 'failure.html').write_text(page.content())
                    raise
                finally:
                    context.close()
            _preview(browser, output, report)
            browser.close(); browser = None
        assert not report['errors'] and not report['external_browser_requests']
        assert not report['backend_provider_requests']
        assert len(report['provider_job_requests']) == 1
        report['result'] = 'passed'
    except Exception as error:
        report.update(result='failed', failure=f'{type(error).__name__}: {error}')
        raise
    finally:
        if browser:
            try: browser.close()
            except Exception: pass  # Playwright also closes owned browsers when its context exits.
        if server: server.should_exit = True
        if worker: worker.join(timeout=20)
        if listener: listener.close()
        if client: asyncio.run(client.aclose())
        shutil.rmtree(root, ignore_errors=True)
        for name, value in previous.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: workspace_studio_settings_e2e.py OUTPUT_DIR UI_DIST')
    result = run(sys.argv[1], sys.argv[2])
    print(json.dumps({'result': result['result'], 'states': len(result['states']),
        'checks': result['checks'], 'errors': result['errors'],
        'paid_provider_queries': result['paid_provider_queries']}, indent=2))
