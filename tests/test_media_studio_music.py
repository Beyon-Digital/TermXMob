"""Fixture-only music effects, exact lyrics, ownership and admission checks."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from termx.media.http import mount_media
from termx.media.studio import StudioService, account_adapter
from termx.media.studio_music import MUSIC_MODEL_ID, MusicInput
from termx.media.studio_settings import initial_settings
from test_media_studio import drain, request, setup_service, state_for

MP3 = (Path(__file__).parent / 'fixtures' / 'music-silence.mp3').read_bytes()


def music_state(*, key='fixture-secret', capabilities=None, base_url='https://api.elevenlabs.io'):
    return state_for([{'id': 'music-account', 'name': 'My music API', 'kind': 'openai-compatible',
        'base_url': base_url, 'capabilities': ['audio'] if capabilities is None else capabilities}], key=key)


def music_request(**changes):
    return {**MusicInput(request_id='music-request', provider_id='music-account', prompt='Warm acoustic song',
        acknowledge_billing=True).model_dump(), **changes}


def music_service(tmp_path, handler, state=None):
    return setup_service(tmp_path, handler, state or music_state())


@pytest.mark.parametrize('url,expected', [
    ('https://api.elevenlabs.io', 'elevenlabs'), ('https://api.elevenlabs.io/v1/', 'elevenlabs'),
    ('https://api.elevenlabs.io:443/v1', 'elevenlabs'), ('http://api.elevenlabs.io', None),
    ('https://api.elevenlabs.io.evil.example', None), ('https://api.elevenlabs.io/v1/music', None),
    ('https://api.elevenlabs.io:8443', None), ('https://user:pass@api.elevenlabs.io', None),
    ('https://api.elevenlabs.io?key=secret', None), ('https://platform.suno.com', None),
])
def test_music_account_uses_documented_exact_origin(url, expected):
    assert account_adapter({'kind': 'openai-compatible', 'base_url': url}) == expected


@pytest.mark.parametrize('changes', [
    {'duration': True}, {'duration': 2}, {'duration': 601}, {'duration': 3.5},
    {'instrumental': 'true'}, {'acknowledge_billing': 'true'}, {'mode': 'unknown'}, {'unexpected': 'token'},
    {'prompt': ''}, {'lyrics': 'Use custom mode'}, {'prompt': 'x' * 4101},
    {'styles': 'x' * 1001}, {'negative_styles': 'x' * 1001}, {'title': 'x' * 201},
    {'prompt': 'x' * 4100, 'styles': 'pop'},
    {'mode': 'custom', 'prompt': '', 'lyrics': ''},
    {'mode': 'custom', 'prompt': '', 'lyrics': 'word', 'duration': 121},
    {'mode': 'custom', 'prompt': 'not forwarded', 'lyrics': 'word'},
    {'mode': 'custom', 'instrumental': True, 'lyrics': 'word'},
    {'mode': 'custom', 'instrumental': True, 'prompt': '', 'styles': ''},
    {'mode': 'custom', 'prompt': '', 'lyrics': 'x' * 12001},
    {'mode': 'custom', 'prompt': '', 'lyrics': 'word', 'styles': ','.join(['pop'] * 51)},
])
def test_music_inputs_fail_before_reservation_or_effect(changes):
    with pytest.raises(ValidationError):
        MusicInput.model_validate(music_request(**changes))


def test_simple_instrumental_payload_owned_binary_and_idempotent_receipt(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        assert req.method == 'POST' and req.url.path == '/v1/music'
        assert req.url.host == 'api.elevenlabs.io' and req.url.params['output_format'] == 'mp3_48000_192'
        assert req.headers['xi-api-key'] == 'fixture-secret' and 'authorization' not in req.headers
        assert json.loads(req.content) == {'model_id': 'music_v2_5', 'prompt': 'Warm acoustic song',
            'music_length_ms': 60000, 'force_instrumental': True}
        return httpx.Response(200, content=MP3, headers={'content-type': 'audio/mpeg', 'song-id': 'provider-id'})
    service, client = music_service(tmp_path, handler)
    values = music_request(instrumental=True, title='Evening song', project_id='project-a')
    async def run():
        first, duplicate = await asyncio.gather(service.create_music('alice', 'project-a', values),
            service.create_music('alice', 'project-a', values))
        assert first['id'] == duplicate['id'] and first['kind'] == 'audio'
        await drain(service)
        final = service.get('alice', first['id'])
        assert final['status'] == 'completed' and final['request_id'] == 'music-request'
        assert final['parameters']['title'] == 'Evening song' and final['parameters']['instrumental'] is True
        assert len(final['outputs']) == 1 and final['outputs'][0].endswith('/export?format=original')
        identifier = final['outputs'][0].split('/')[4]
        assert service.artifacts.get('alice', identifier)['project'] == 'project-a'
        blob, mime = service.artifacts.binary('alice', identifier)
        assert blob == MP3 and mime == 'audio/mpeg'
        with pytest.raises(HTTPException): service.artifacts.get('bob', identifier)
        assert await service.create_music('alice', 'project-a', values) == final
        assert await service.refresh('alice', final['id']) == final
        restarted = StudioService(service.state, service.path.parent, service.artifacts, client=client)
        assert await restarted.create_music('alice', 'project-a', values) == final
        with pytest.raises(HTTPException, match='different arguments'):
            await service.create_music('alice', 'project-a', {**values, 'title': 'Changed title'})
        assert service.list('alice', None) == [] and service.list('bob', 'project-a') == []
        assert service.history('alice', 'project-a', kind='audio', q='Evening')['items'] == [final]
        assert b'fixture-secret' not in service.path.read_bytes()
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_exact_custom_lyrics_and_section_tags_are_not_rewritten_or_generated(tmp_path):
    lyrics = '[Verse]\n Line one with  two spaces.\nLínea dos\n\n[Chorus]\nSing this exactly!\n'
    calls = []
    def handler(req):
        calls.append(req)
        payload = json.loads(req.content)
        chunks = payload['composition_plan']['chunks']
        assert payload['model_id'] == 'music_v2_5' and len(chunks) == 2
        assert ''.join(chunk['text'] for chunk in chunks) == lyrics
        assert [chunk['duration_ms'] for chunk in chunks] == [45000, 45000]
        assert chunks[0]['text'].startswith('[Verse]') and chunks[1]['text'].startswith('[Chorus]')
        for chunk in chunks:
            assert chunk['positive_styles'] == ['acoustic pop', 'warm alto']
            assert chunk['negative_styles'] == ['distortion', 'spoken word']
            assert chunk['context_adherence'] == 'high'
        assert 'prompt' not in payload and 'force_instrumental' not in payload
        return httpx.Response(200, content=MP3, headers={'content-type': 'audio/mpeg'})
    service, client = music_service(tmp_path, handler)
    values = music_request(mode='custom', prompt='', lyrics=lyrics, styles='acoustic pop, warm alto',
        negative_styles='distortion, spoken word', duration=90)
    async def run():
        job = await service.create_music('alice', None, values)
        await drain(service)
        final = service.get('alice', job['id'])
        assert final['parameters']['lyrics'] == lyrics and final['parameters']['music_mode'] == 'custom'
        assert service.history('alice', None, kind='audio', q='Línea dos')['items'] == [final]
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_custom_instrumental_guarantees_instrumental_with_documented_prompt_flag():
    body = MusicInput.model_validate(music_request(mode='custom', prompt='', styles='ambient, soft piano',
        negative_styles='vocals', instrumental=True))
    assert body.payload() == {'model_id': 'music_v2_5', 'prompt': 'Styles: ambient, soft piano\n\nAvoid these styles: vocals',
        'music_length_ms': 60000, 'force_instrumental': True}


@pytest.mark.parametrize('response,status', [
    (httpx.Response(401, text='fixture-secret'), 'failed'), (httpx.Response(422, text='fixture-secret'), 'failed'),
    (httpx.Response(429, text='fixture-secret'), 'failed'), (httpx.Response(500, text='fixture-secret'), 'unknown'),
    (httpx.Response(408, text='fixture-secret'), 'unknown'), (httpx.Response(499, text='fixture-secret'), 'unknown'),
    (httpx.Response(302, headers={'location': 'https://evil.example/api'}), 'unknown'),
    (httpx.Response(200, json={'secret': 'fixture-secret'}), 'unknown'), (httpx.Response(200, content=b''), 'unknown'),
])
def test_refusal_invalid_audio_and_uncertain_outcome_never_replay_or_leak(tmp_path, response, status):
    calls = []
    def handler(req): calls.append(req); return response
    service, client = music_service(tmp_path, handler)
    async def run():
        job = await service.create_music('alice', None, music_request())
        await drain(service)
        final = service.get('alice', job['id'])
        assert final['status'] == status and 'fixture-secret' not in json.dumps(final)
        assert await service.create_music('alice', None, music_request()) == final
        assert await service.refresh('alice', job['id']) == final
        assert service.artifacts.list('alice') == []
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_timeout_cancellation_and_restart_preserve_unknown_receipt(tmp_path):
    calls, entered, hold = [], asyncio.Event(), asyncio.Event()
    async def handler(req):
        calls.append(req); entered.set(); await hold.wait()
        raise httpx.ReadTimeout('fixture-secret', request=req)
    service, client = music_service(tmp_path, handler)
    async def run():
        job = await service.create_music('alice', None, music_request())
        await entered.wait()
        await service.close()
        assert service.get('alice', job['id'])['status'] == 'unknown'
        restarted = StudioService(service.state, service.path.parent, service.artifacts, client=client)
        assert (await restarted.create_music('alice', None, music_request()))['status'] == 'unknown'
        assert len(calls) == 1
        await client.aclose()
    asyncio.run(run())


def test_stream_limit_stops_reading_and_does_not_persist_audio(tmp_path, monkeypatch):
    class Oversized(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield MP3
            yield b'x' * 40
            raise AssertionError('Stop consuming an oversized song')
        async def aclose(self): self.closed = True
    stream = Oversized()
    monkeypatch.setattr('termx.media.studio_music.LIMIT', len(MP3) + 10)
    service, client = music_service(tmp_path, lambda _: httpx.Response(200, stream=stream, headers={'content-type': 'audio/mpeg'}))
    async def run():
        job = await service.create_music('alice', None, music_request())
        await drain(service)
        assert stream.closed and service.get('alice', job['id'])['status'] == 'unknown'
        assert service.artifacts.list('alice') == []
        await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('changes,state,match', [
    ({'acknowledge_billing': False}, None, 'Confirm paid usage'),
    ({'model': 'suno/v5'}, None, 'not supported'),
    ({'project_id': 'other'}, None, 'Project does not match'),
    ({}, music_state(key=None), 'Connect this API account'),
    ({}, music_state(capabilities=['image']), 'audio support'),
    ({}, music_state(base_url='https://api.elevenlabs.io.evil.example'), 'compatible API account'),
])
def test_account_project_and_charge_checks_precede_reservation(tmp_path, changes, state, match):
    service, client = music_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()), state)
    async def run():
        with pytest.raises(HTTPException, match=match):
            await service.create_music('alice', None, music_request(**changes))
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


def test_music_settings_duration_cap_and_shared_atomic_daily_concurrent_limits(tmp_path):
    calls, entered, hold = [], asyncio.Event(), asyncio.Event()
    async def handler(req):
        calls.append(req); entered.set(); await hold.wait()
        return httpx.Response(200, content=MP3, headers={'content-type': 'audio/mpeg'})
    service, client = music_service(tmp_path, handler)
    async def run():
        initial = initial_settings().public()
        saved = await service.update_settings('alice', {**initial, 'defaults': {'music': {
            'provider_id': 'music-account', 'model': MUSIC_MODEL_ID, 'duration': 60}},
            'limits': {**initial['limits'], 'daily_generations': 1, 'concurrent_generations': 1, 'max_music_seconds': 60}})
        assert saved['settings']['limits']['max_music_seconds'] == 60
        with pytest.raises(HTTPException, match='music seconds limit'):
            await service.create_music('alice', None, music_request(duration=61))
        assert service.list('alice', None) == []
        first = await service.create_music('alice', None, music_request())
        await entered.wait()
        with pytest.raises(HTTPException) as blocked:
            await service.create_music('alice', None, music_request(request_id='second-request', project_id='other'))
        # Project must match independently; actual quota is owner-wide.
        assert blocked.value.status_code == 400
        with pytest.raises(HTTPException, match='daily accepted generation limit'):
            await service.create_music('alice', 'other', music_request(request_id='second-request', project_id='other'))
        duplicate = await service.create_music('alice', None, music_request())
        assert duplicate['id'] == first['id']
        hold.set(); await drain(service)
        assert (await service.settings('alice'))['usage']['daily_generations'] == 1
        assert len(calls) == 1
        await client.aclose()
    asyncio.run(run())


def test_music_admission_respects_disabled_models_and_concurrent_limits(tmp_path):
    service, client = music_service(tmp_path, lambda _: httpx.Response(200, content=MP3, headers={'content-type': 'audio/mpeg'}))
    async def run():
        initial = initial_settings().public()
        await service.update_settings('alice', {**initial, 'enabled_models': []})
        with pytest.raises(HTTPException, match='disabled'):
            await service.create_music('alice', None, music_request())
        await service.update_settings('alice', {**initial, 'revision': 1,
            'limits': {**initial['limits'], 'concurrent_generations': 0}})
        with pytest.raises(HTTPException, match='concurrent'):
            await service.create_music('alice', None, music_request())
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


def test_live_authorization_guard_rechecks_immediately_before_paid_effect(tmp_path):
    service, client = music_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError('No paid POST')))
    checks = []
    def authorize():
        checks.append('check')
        if len(checks) > 1: raise HTTPException(403, 'fixture-secret')
    async def run():
        job = await service.create_music('alice', None, music_request(), before_submit=authorize)
        await drain(service)
        final = service.get('alice', job['id'])
        assert final['status'] == 'failed' and 'No music was submitted' in final['error']
        assert 'fixture-secret' not in json.dumps(final) and len(checks) == 2
        await client.aclose()
    asyncio.run(run())


def test_visual_generation_rejects_audio_model_and_music_catalog_is_honest(tmp_path):
    service, client = music_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()))
    async def run():
        catalog = await service.catalog('alice')
        model = next(model for model in catalog['models'] if model['id'] == MUSIC_MODEL_ID)
        assert model['kind'] == 'audio' and model['modes'] == ['music']
        assert model['supports_custom_lyrics'] and model['custom_duration_max'] == 120
        assert catalog['accounts'] == [{'id': 'music-account', 'name': 'My music API', 'adapter': 'elevenlabs',
            'configured': True, 'capabilities': ['audio']}]
        assert not any(model['id'].startswith('suno') for model in catalog['models'])
        with pytest.raises(HTTPException, match='dedicated music generation endpoint'):
            await service.create('alice', None, request(model=MUSIC_MODEL_ID, provider_id='music-account'))
        await client.aclose()
    asyncio.run(run())


def test_http_music_routes_require_identity_run_scope_and_owned_export(tmp_path, monkeypatch):
    state = music_state()
    state.delivery = SimpleNamespace(directory=tmp_path / 'delivery')
    state.identity = SimpleNamespace(resolve=lambda token: SimpleNamespace(principal=SimpleNamespace(id=token)) if token in {'alice', 'bob'} else None)
    allowed = {'project-a'}
    decisions = []
    def authorize(token, scope, project_id=None):
        decisions.append((token, scope, project_id))
        if project_id not in allowed: raise HTTPException(403, 'project denied')
    state.authorization = SimpleNamespace(require=authorize)
    app = FastAPI(); mount_media(app, state)
    state.studio._catalog_at = __import__('time').time()
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=MP3, headers={'content-type': 'audio/mpeg'})))
    state.studio.client = mock_client
    values = music_request(project_id='project-a')
    with TestClient(app) as client:
        alice, bob = {'Authorization': 'Bearer alice'}, {'Authorization': 'Bearer bob'}
        assert client.post('/api/media/studio/music/jobs', json=values).status_code == 401
        assert client.post('/api/media/studio/music/jobs', headers=alice, json={**values, 'duration': True}).status_code == 422
        response = client.post('/api/media/studio/music/jobs', headers=alice, json=values)
        assert response.status_code == 200
        identifier = response.json()['id']
        # Read through the same TestClient event loop to let the queued effect finish.
        client.portal.call(drain, state.studio)
        jobs = client.get('/api/media/studio/history?project_id=project-a&kind=audio', headers=alice).json()['items']
        assert len(jobs) == 1 and jobs[0]['status'] == 'completed'
        output = jobs[0]['outputs'][0]
        assert client.get(output, headers=alice).content == MP3
        assert client.get(output, headers=bob).status_code == 404
        assert client.post(f'/api/media/studio/jobs/{identifier}/refresh', headers=bob).status_code == 404
        allowed.clear()
        assert client.get(output, headers=alice).status_code == 403
        assert client.post('/api/media/studio/music/jobs', headers=alice, json=values).status_code == 403
        client.portal.call(mock_client.aclose)
    assert ('alice', 'agent-run', 'project-a') in decisions


@pytest.mark.parametrize('lyrics,duration', [
    ('\n'.join(['word'] * 31), 60), ('x' * 201, 60),
    ('[' + 'x' * 101 + ']\nword', 60), ('[]\nword', 60),
    ('[Verse]\nword\n[Chorus]\nword', 5),
    ('\n'.join('[Section]\nword' for _ in range(31)), 120),
])
def test_exact_lyrics_provider_bounds_fail_without_truncation(lyrics, duration):
    with pytest.raises(ValidationError):
        MusicInput.model_validate(music_request(mode='custom', prompt='', lyrics=lyrics, duration=duration))


@pytest.mark.parametrize('blob,mime', [
    (b'ID3junk', 'text/html'), (b'ID3junk', 'audio/mpeg'),
    (b'\xff\xff\xff\xff', 'application/json'), (b'\xff\xff\xff\xff', 'audio/mpeg'),
    (MP3, 'text/html'), (MP3[:-20], 'audio/mpeg'),
    (b'ID3\x04\x00\x00\x7f\x7f\x7f\x7f', 'audio/mpeg'),
])
def test_corrupt_or_non_audio_success_cannot_create_completed_song(tmp_path, blob, mime):
    service, client = music_service(tmp_path, lambda _: httpx.Response(200, content=blob, headers={'content-type': mime}))
    async def run():
        job = await service.create_music('alice', None, music_request())
        await drain(service)
        assert service.get('alice', job['id'])['status'] == 'unknown'
        assert service.artifacts.list('alice') == []
        await client.aclose()
    asyncio.run(run())


def test_music_default_accepts_continuous_documented_duration_range(tmp_path):
    service, client = music_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()))
    async def run():
        initial = initial_settings().public()
        for revision, duration in enumerate((3, 10, 600)):
            saved = await service.update_settings('alice', {**initial, 'revision': revision, 'defaults': {'music': {
                'provider_id': 'music-account', 'model': MUSIC_MODEL_ID, 'duration': duration}}})
            assert saved['settings']['defaults']['music']['duration'] == duration
        await client.aclose()
    asyncio.run(run())



def test_music_song_receipt_cannot_route_music_key_to_other_provider(tmp_path):
    service, client = music_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError('Never poll another provider with the music key')))
    with service.db() as db:
        db.execute("""INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,task_id,created,updated)
            VALUES('receipt-job','alice',NULL,'receipt-request','digest','music-account','elevenlabs',?,'audio','test','unknown','provider-song-id',1,1)""", (MUSIC_MODEL_ID,))
    async def run():
        current = await service.refresh('alice', 'receipt-job')
        assert current['status'] == 'unknown' and not service.tasks
        await client.aclose()
    asyncio.run(run())
