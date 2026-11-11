"""Durable owner preferences and local, nonbillable Studio admission checks."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from time import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from termx.media.http import mount_media
from termx.media.studio import SNAPSHOT, StudioService
from termx.media.studio_settings import StudioSettingsInput, initial_settings
from test_media_studio import drain, request, setup_service, state_for


def settings(**changes):
    return {**initial_settings().public(), **changes}


def limits(**changes):
    return {**settings()['limits'], **changes}


def no_generation(_):
    raise AssertionError('This operation must not dispatch provider inference')


def seed(service, identifier, *, owner='alice', project='other-project', status='failed', created=None):
    now = time() if created is None else created
    with service.db() as db:
        db.execute('''INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,
            adapter,model,kind,prompt,status,created,updated)
            VALUES(?,?,?,?,?,'kie','kie','nano-banana-pro','image','fixture',?,?,?)''',
            (identifier, owner, project, identifier, 'fixture-digest', status, now, now))


def test_initial_read_has_no_implicit_write_and_settings_are_owned_durable(tmp_path):
    service, client = setup_service(tmp_path, no_generation)
    async def run():
        first = await service.settings('alice')
        assert first['settings'] == settings()
        assert first['usage']['daily_generations'] == first['usage']['active_generations'] == 0
        with service.db() as db:
            assert db.execute('SELECT COUNT(*) FROM studio_settings').fetchone()[0] == 0
        saved = await service.update_settings('alice', settings(enabled_models=['nano-banana-pro'],
            defaults={'image': {'provider_id': 'kie', 'model': 'nano-banana-pro', 'resolution': '2K'}},
            limits=limits(daily_generations=4)))
        assert saved['settings']['revision'] == 1
        assert 'fixture-secret' not in str(saved)
        assert (await service.settings('bob'))['settings'] == settings()
        restarted = StudioService(service.state, service.path.parent, service.artifacts, client=client)
        restarted._catalog = SNAPSHOT['openrouter']
        restarted._catalog_at = time()
        restarted._music_catalog_at = time()
        assert (await restarted.settings('alice'))['settings'] == saved['settings']
        assert (await service.catalog('alice'))['models'][0]['id'] == 'nano-banana-pro'
        assert len((await service.catalog('alice'))['models']) == 1
        assert len(saved['catalog']['models']) > 1
        assert 'settings' not in saved['catalog']
        with pytest.raises(HTTPException, match='changed') as stale:
            await service.update_settings('alice', settings())
        assert stale.value.status_code == 409
        await service.update_settings('alice', settings(revision=1, enabled_models=[]))
        assert (await service.catalog('alice'))['models'] == []
        assert len((await service.settings('alice'))['catalog']['models']) > 1
        await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('changes', [
    {'revision': True}, {'revision': -1}, {'unexpected': 'key'}, {'defaults': {'cinema': {}}},
    {'enabled_models': ['nano-banana-pro', 'nano-banana-pro']}, {'enabled_models': [123]},
    {'limits': {}}, {'limits': limits(daily_generations=True)},
    {'limits': limits(concurrent_generations=-1)}, {'limits': limits(max_video_seconds=1.5)},
    {'defaults': {'image': {'provider_id': 'kie', 'model': 'nano-banana-pro', 'api_key': 'secret'}}},
    {'defaults': {'video': {'provider_id': 'kie', 'model': 'wan/2-5-text-to-video', 'duration': True}}},
])
def test_settings_strict_shape(changes):
    with pytest.raises(ValidationError):
        StudioSettingsInput.model_validate(settings(**changes))


@pytest.mark.parametrize('changes,match', [
    ({'enabled_models': ['unknown-model']}, 'current Studio catalog'),
    ({'defaults': {'image': {'provider_id': 'missing', 'model': 'nano-banana-pro'}}}, 'compatible API account'),
    ({'defaults': {'image': {'provider_id': 'kie', 'model': 'google/gemini-3-pro-image'}}}, 'compatible API account'),
    ({'defaults': {'video': {'provider_id': 'kie', 'model': 'nano-banana-pro'}}}, 'support video'),
    ({'defaults': {'remix': {'provider_id': 'kie', 'model': 'wan/2-5-text-to-video'}}}, 'support remix'),
    ({'defaults': {'image': {'provider_id': 'kie', 'model': 'nano-banana-pro', 'resolution': '8K'}}}, 'resolution'),
    ({'defaults': {'image': {'provider_id': 'kie', 'model': 'nano-banana-pro', 'duration': 5}}}, 'duration'),
    ({'defaults': {'video': {'provider_id': 'kie', 'model': 'wan/2-5-text-to-video', 'aspect_ratio': 'auto'}}}, 'aspect ratio'),
    ({'enabled_models': [], 'defaults': {'image': {'provider_id': 'kie', 'model': 'nano-banana-pro'}}}, 'must be enabled'),
])
def test_invalid_settings_do_not_write_or_dispatch(tmp_path, changes, match):
    service, client = setup_service(tmp_path, no_generation)
    async def run():
        with pytest.raises(HTTPException, match=match):
            await service.update_settings('alice', settings(**changes))
        assert service.settings_store.get('alice').revision == 0
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


def test_defaults_check_account_capability_without_requiring_credentials_or_generation_inputs(tmp_path):
    providers = [{'id': 'kie', 'kind': 'openai-compatible', 'base_url': 'https://api.kie.ai', 'capabilities': ['image']}]
    state = state_for(providers, key=None)
    service, client = setup_service(tmp_path, no_generation, state)
    async def run():
        with pytest.raises(HTTPException, match='video support'):
            await service.update_settings('alice', settings(defaults={
                'video': {'provider_id': 'kie', 'model': 'wan/2-5-text-to-video'}}))
        providers[0]['capabilities'].append('video')
        # A reusable lipsync preset needs no prompt/image/audio until generation.
        saved = await service.update_settings('alice', settings(defaults={
            'lipsync': {'provider_id': 'kie', 'model': 'omnihuman-1-5', 'resolution': '720'}}))
        assert saved['settings']['revision'] == 1
        assert saved['catalog']['accounts'][0]['configured'] is False
        await client.aclose()
    asyncio.run(run())


def test_daily_utc_usage_counts_all_reserved_attempts_across_projects_and_isolates_owners(tmp_path, monkeypatch):
    now = [1_800_000_010.0]
    midnight = int(now[0] // 86400) * 86400
    monkeypatch.setattr('termx.media.studio.time', lambda: now[0])
    service, client = setup_service(tmp_path, no_generation)
    service._catalog_at = now[0]; service._music_catalog_at = now[0]
    for index, status in enumerate(['submitting', 'queued', 'running', 'unknown', 'completed', 'failed']):
        seed(service, f'current-{index}', status=status, created=midnight + index)
    seed(service, 'yesterday', status='unknown', created=midnight - 1)
    seed(service, 'tomorrow', created=midnight + 86400)
    seed(service, 'bob-today', owner='bob', created=midnight + 1)
    async def run():
        envelope = await service.settings('alice')
        assert envelope['usage'] == {'daily_generations': 6, 'active_generations': 5, 'reset_at': midnight + 86400}
        assert (await service.settings('bob'))['usage']['daily_generations'] == 1
        await service.update_settings('alice', settings(limits=limits(daily_generations=6)))
        with pytest.raises(HTTPException, match='midnight UTC') as blocked:
            await service.create('alice', None, request())
        assert blocked.value.status_code == 429
        assert len(service.list('alice', None)) == 0
        now[0] = midnight + 86400
        service._catalog_at = now[0]; service._music_catalog_at = now[0]
        # At the exact next midnight, only the next day's reserved attempt counts.
        assert (await service.settings('alice'))['usage']['daily_generations'] == 1
        await client.aclose()
    asyncio.run(run())


def test_concurrent_unknown_counts_until_terminal_and_rejections_never_reserve(tmp_path):
    calls = []
    service, client = setup_service(tmp_path, lambda req: calls.append(req) or httpx.Response(200,
        json={'code': 200, 'data': {'taskId': 'receipt'}}))
    seed(service, 'unknown-old', status='unknown', created=1)
    async def run():
        await service.update_settings('alice', settings(limits=limits(concurrent_generations=1)))
        with pytest.raises(HTTPException, match='unknown outcomes') as blocked:
            await service.create('alice', None, request())
        assert blocked.value.status_code == 429 and calls == []
        service._update('alice', 'unknown-old', {'status': 'completed'})
        job = await service.create('alice', None, request())
        await drain(service)
        assert service.get('alice', job['id'])['status'] == 'queued'
        assert len(calls) == 1
        assert (await service.settings('alice'))['usage']['daily_generations'] == 1
        await client.aclose()
    asyncio.run(run())


def test_model_disable_and_zero_limits_do_not_block_matching_receipt_replays(tmp_path):
    calls = []
    service, client = setup_service(tmp_path, lambda req: calls.append(req) or httpx.Response(200,
        json={'code': 200, 'data': {'taskId': 'receipt'}}))
    async def run():
        first = await service.create('alice', None, request())
        await drain(service)
        await service.update_settings('alice', settings(enabled_models=[],
            limits=limits(daily_generations=0, concurrent_generations=0, max_video_seconds=0)))
        replay = await service.create('alice', None, request())
        assert replay['id'] == first['id']
        assert (await service.settings('alice'))['usage']['daily_generations'] == 1
        with pytest.raises(HTTPException, match='different arguments'):
            await service.create('alice', None, request(prompt='different'))
        with pytest.raises(HTTPException, match='disabled') as blocked:
            await service.create('alice', None, request(request_id='different-request'))
        assert blocked.value.status_code == 403 and len(calls) == 1
        await service.update_settings('alice', settings(revision=1, limits=limits(daily_generations=0)))
        with pytest.raises(HTTPException, match='daily'):
            await service.create('alice', None, request(request_id='yet-another-request'))
        assert len(calls) == 1
        await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('model,duration,cap,allowed', [
    ('wan/2-5-text-to-video', None, 8, False), ('wan/2-5-text-to-video', 10, 8, False),
    ('wan/2-5-text-to-video', 5, 8, True), ('omnihuman-1-5', None, 60, False),
    ('nano-banana-pro', None, 0, True),
])
def test_video_seconds_cap_requires_proven_duration(tmp_path, model, duration, cap, allowed):
    calls = []
    service, client = setup_service(tmp_path, lambda req: calls.append(req) or httpx.Response(200,
        json={'code': 200, 'data': {'taskId': 'receipt'}}))
    body = request(model=model, duration=duration)
    if model == 'omnihuman-1-5':
        body.update(image_url='https://public.example/image.png', audio_url='https://public.example/audio.mp3')
    async def run():
        await service.update_settings('alice', settings(limits=limits(max_video_seconds=cap)))
        if allowed:
            await service.create('alice', None, body)
            await drain(service)
            assert len(calls) == 1
        else:
            with pytest.raises(HTTPException, match='explicit video duration') as blocked:
                await service.create('alice', None, body)
            assert blocked.value.status_code == 409
            assert service.list('alice', None) == [] and calls == []
        await client.aclose()
    asyncio.run(run())


def test_invalid_or_unauthorized_requests_do_not_consume_daily_attempts(tmp_path):
    service, client = setup_service(tmp_path, no_generation)
    async def run():
        await service.update_settings('alice', settings(limits=limits(daily_generations=1)))
        for changes in ({'acknowledge_billing': False}, {'provider_id': 'missing'}, {'resolution': '8K'}):
            with pytest.raises(HTTPException):
                await service.create('alice', None, request(**changes))
        def revoked():
            raise HTTPException(403, 'revoked')
        with pytest.raises(HTTPException):
            await service.create('alice', None, request(), before_submit=revoked)
        assert (await service.settings('alice'))['usage']['daily_generations'] == 0
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('cap', ['daily_generations', 'concurrent_generations'])
def test_atomic_limit_admission_across_independent_service_instances(tmp_path, cap):
    calls = []
    handler = lambda req: calls.append(req) or httpx.Response(200, json={'code': 200, 'data': {'taskId': 'receipt'}})
    first, first_client = setup_service(tmp_path, handler)
    second, second_client = setup_service(tmp_path, handler)
    asyncio.run(first.update_settings('alice', settings(limits=limits(**{cap: 1}))))
    barrier = Barrier(2)
    def attempt(index):
        service, client = (first, first_client) if index == 0 else (second, second_client)
        async def run():
            barrier.wait(timeout=5)
            try:
                result = await service.create('alice', f'project-{index}', request(
                    request_id=f'parallel-{index}', project_id=f'project-{index}'))
                await drain(service)
                return result['id']
            except HTTPException as exc:
                return exc.status_code
            finally:
                await client.aclose()
        return asyncio.run(run())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(isinstance(result, str) for result in results) == 1 and 429 in results
    assert len(calls) == 1
    assert first.settings_store.snapshot('alice', time())[1]['daily_generations'] == 1


def test_revision_updates_are_atomic_across_settings_writers(tmp_path):
    service, client = setup_service(tmp_path, no_generation)
    barrier = Barrier(2)
    def save(index):
        barrier.wait(timeout=5)
        try:
            return service.settings_store.update('alice', StudioSettingsInput.model_validate(
                settings(limits=limits(daily_generations=index)))).revision
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [1, 409]
    assert service.settings_store.get('alice').revision == 1
    asyncio.run(client.aclose())


@pytest.fixture
def workspace(tmp_path):
    state = state_for()
    grants = {('alice', 'ai-settings', None), ('bob', 'ai-settings', None),
              ('member', 'agent-view', 'project-a'), ('member', 'ai-settings', 'project-a'),
              ('alice', 'agent-view', 'project-a')}
    authority = {'owner': 'alice', 'live': True}
    decisions = []
    def resolve(token):
        if token == 'live-session':
            return SimpleNamespace(principal=SimpleNamespace(id=authority['owner'])) if authority['live'] else None
        if token in {'alice', 'bob', 'member'}:
            return SimpleNamespace(principal=SimpleNamespace(id=token))
    def require(token, scope, project_id=None):
        principal = resolve(token)
        decisions.append((principal.principal.id if principal else None, scope, project_id))
        if principal is None or (principal.principal.id, scope, project_id) not in grants:
            raise HTTPException(403, 'Host-wide settings require administrator authority')
    state.delivery = SimpleNamespace(directory=tmp_path/'delivery')
    state.identity = SimpleNamespace(resolve=resolve)
    state.authorization = SimpleNamespace(require=require)
    app = FastAPI(); mount_media(app, state)
    state.studio._catalog = SNAPSHOT['openrouter']
    state.studio._catalog_at = time()
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, state=state, grants=grants, authority=authority, decisions=decisions)


def test_settings_http_host_authority_owned_data_and_full_catalog(workspace):
    client, service = workspace.client, workspace.state.studio
    alice, bob = {'Authorization': 'Bearer alice'}, {'Authorization': 'Bearer bob'}
    assert client.get('/api/media/studio/settings').status_code == 401
    for method in ('get', 'put'):
        options = {'json': settings()} if method == 'put' else {}
        assert getattr(client, method)('/api/media/studio/settings?project_id=project-a',
            headers={'Authorization': 'Bearer member'}, **options).status_code == 403
    result = client.get('/api/media/studio/settings', headers=alice)
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    assert set(result.json()) == {'settings', 'usage', 'catalog'}
    changed = settings(enabled_models=[], limits=limits(daily_generations=0))
    saved = client.put('/api/media/studio/settings', headers=alice, json=changed)
    assert saved.status_code == 200 and saved.json()['settings']['revision'] == 1
    assert len(saved.json()['catalog']['models']) > 0
    assert client.get('/api/media/studio/settings', headers=bob).json()['settings'] == settings()
    filtered = client.get('/api/media/studio/catalog?project_id=project-a', headers=alice).json()
    assert filtered['models'] == [] and filtered['settings']['revision'] == 1
    assert client.put('/api/media/studio/settings', headers=alice, json=changed).status_code == 409
    assert client.put('/api/media/studio/settings', headers=alice, json={**changed, 'secret': 'x'}).status_code == 422
    assert service.settings_store.get('alice').revision == 1
    assert ('alice', 'ai-settings', None) in workspace.decisions


@pytest.mark.parametrize('method', ['get', 'put'])
@pytest.mark.parametrize('change,code', [('revoked', 403), ('session', 401), ('owner', 403)])
def test_settings_revalidate_actor_after_await_before_data_or_write(workspace, monkeypatch, method, change, code):
    async def changed_catalog():
        await asyncio.sleep(0)
        if change == 'revoked':
            workspace.grants.remove(('alice', 'ai-settings', None))
        elif change == 'session':
            workspace.authority['live'] = False
        else:
            workspace.authority['owner'] = 'bob'
    monkeypatch.setattr(workspace.state.studio, '_load_catalog', changed_catalog)
    options = {'json': settings()} if method == 'put' else {}
    response = getattr(workspace.client, method)('/api/media/studio/settings',
        headers={'Authorization': 'Bearer live-session'}, **options)
    assert response.status_code == code
    assert workspace.state.studio.settings_store.get('alice').revision == 0
    assert workspace.state.studio.settings_store.get('bob').revision == 0
    assert 'fixture-secret' not in response.text


def test_host_authorization_does_not_accept_project_settings_scope(tmp_path):
    from test_authorization import fixture
    identity, authorization, owner, alice, _, login = fixture(tmp_path)
    # The real authorization evaluator applies both host-role and project policy.
    assert authorization.can(login('owner'), 'ai-settings', project_id=None)
    assert not authorization.can(login('alice'), 'ai-settings', project_id=None)
    assert not authorization.can(login('alice'), 'ai-settings', project_id='one')


def test_utc_admission_time_is_taken_after_write_lock(tmp_path, monkeypatch):
    now = [1_800_000_010.0]
    midnight = int(now[0] // 86400) * 86400
    now[0] = midnight + 86400 - 0.1
    monkeypatch.setattr('termx.media.studio.time', lambda: now[0])
    calls = []
    service, client = setup_service(tmp_path, lambda req: calls.append(req) or httpx.Response(200,
        json={'code': 200, 'data': {'taskId': 'receipt'}}))
    service._catalog_at = now[0]; service._music_catalog_at = now[0]
    seed(service, 'previous-day', created=midnight)
    asyncio.run(service.update_settings('alice', settings(limits=limits(daily_generations=1))))
    original_database = service.db
    @contextmanager
    def delayed_lock():
        with original_database() as db:
            class DatabaseAfterMidnight:
                def execute(self, sql, parameters=()):
                    result = db.execute(sql, parameters)
                    if sql == 'BEGIN IMMEDIATE':
                        now[0] = midnight + 86400
                    return result
            yield DatabaseAfterMidnight()
    monkeypatch.setattr(service, 'db', delayed_lock)
    async def run():
        job = await service.create('alice', None, request())
        await drain(service)
        assert job['created'] == midnight + 86400
        assert service.settings_store.snapshot('alice', now[0])[1]['daily_generations'] == 1
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_reservation_rechecks_settings_changed_while_waiting_for_database(tmp_path, monkeypatch):
    import json
    from threading import Event
    service, client = setup_service(tmp_path, no_generation)
    original_database = service.db
    waiting = Event()
    @contextmanager
    def signaled_database():
        with original_database() as db:
            class Database:
                def execute(self, sql, parameters=()):
                    if sql == 'BEGIN IMMEDIATE':
                        waiting.set()
                    return db.execute(sql, parameters)
            yield Database()
    monkeypatch.setattr(service, 'db', signaled_database)
    def attempt():
        async def run():
            try:
                await service.create('alice', None, request())
                raise AssertionError('A newly disabled model must not be reserved')
            except HTTPException as exc:
                return exc.status_code
            finally:
                await client.aclose()
        return asyncio.run(run())
    with ThreadPoolExecutor(max_workers=1) as pool:
        with original_database() as db:
            db.execute('BEGIN IMMEDIATE')
            future = pool.submit(attempt)
            assert waiting.wait(timeout=5)
            saved = settings(revision=1, enabled_models=[])
            db.execute('INSERT INTO studio_settings(owner,revision,settings) VALUES(?,?,?)',
                ('alice', 1, json.dumps(saved)))
        assert future.result(timeout=5) == 403
    assert service.list('alice', None) == []
