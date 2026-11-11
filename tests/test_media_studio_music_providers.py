"""Real catalog/admission/receipts with synthetic HTTP; never paid generation."""
import asyncio
import base64
import json
from pathlib import Path
from time import time

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from termx.media.studio_music import MusicInput
from termx.media.studio_music_catalog import OPENROUTER_MUSIC_MODELS, openrouter_music_models
from termx.media.studio_settings import initial_settings
from test_media_studio import drain, setup_service, state_for

MP3 = (Path(__file__).parent / 'fixtures/music-silence.mp3').read_bytes()
PRO = 'google/lyria-3-pro-preview'
CLIP = 'google/lyria-3-clip-preview'


def inventory():
    return [{'id': model['id'], 'name': model['label'],
             'architecture': {'input_modalities': ['text', 'image'], 'output_modalities': ['text', 'audio']},
             'endpoints': ['/api/v1/chat/completions']} for model in OPENROUTER_MUSIC_MODELS]


def state(*, audio=True, timeout=None):
    provider = {'id': 'or', 'name': 'Shared OpenRouter', 'kind': 'openai-compatible',
                'base_url': 'https://openrouter.ai/api/v1', 'capabilities': ['image', 'video', *(['audio'] if audio else [])],
                'limits': {'request_timeout_s': timeout}}
    return state_for([provider])


def body(**changes):
    return MusicInput(request_id='music-provider-proof', provider_id='or', model=PRO,
                      prompt='A hopeful piano song', acknowledge_billing=True).model_dump() | changes


def sse():
    return ('data: '+json.dumps({'choices': [{'index': 0, 'delta': {'audio': {'data': base64.b64encode(MP3).decode()}}}]})+
            '\n\ndata: '+json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})+
            '\n\ndata: [DONE]\n\n').encode()


def test_live_music_discovery_filters_speech_and_does_not_inherit_auth(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        assert req.method == 'GET' and str(req.url) == 'https://openrouter.ai/api/v1/models?output_modalities=audio'
        assert 'authorization' not in req.headers and 'cookie' not in req.headers
        speech = {'id': 'openai/gpt-audio', 'architecture': {'input_modalities': ['text'], 'output_modalities': ['audio']}}
        return httpx.Response(200, json={'data': [*inventory(), speech]})
    service, client = setup_service(tmp_path, handler, state())
    client.headers['Authorization'] = 'Bearer never-send-discovery'
    client.headers['Cookie'] = 'never-send-discovery'
    service._music_catalog_at = 0
    async def run():
        first = await service.catalog('alice'); second = await service.catalog('alice')
        assert first == second and first['music_catalog_source'] == 'live'
        music = [model for model in first['models'] if model['kind'] == 'audio']
        assert {model['id'] for model in music} == {PRO, CLIP, 'elevenlabs/music_v2_5'}
        assert next(model for model in music if model['id'] == CLIP)['duration_mode'] == 'fixed'
        assert first['accounts'][0]['id'] == 'or' and first['accounts'][0]['configured']
        await client.aclose()
    asyncio.run(run()); assert len(calls) == 1


def test_music_contract_is_not_inferred_from_audio_or_a_name():
    rows = inventory()
    assert len(openrouter_music_models(rows)) == 2
    rows[0]['architecture']['output_modalities'] = ['text']
    rows[1]['endpoints'] = ['/api/v1/responses']
    assert openrouter_music_models(rows) == []
    with pytest.raises(ValueError): openrouter_music_models([inventory()[0], inventory()[0]])


def test_live_model_removal_is_not_masked_by_saved_capabilities(tmp_path):
    service, client = setup_service(tmp_path, lambda req: httpx.Response(200, json={'data': []}), state())
    service._music_catalog_at = 0
    async def run():
        assert not [row for row in (await service.catalog())['models'] if row['adapter'] == 'openrouter' and row['kind'] == 'audio']
        with pytest.raises(HTTPException, match='not supported'):
            await service.create_music('alice', None, body())
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


def test_openrouter_music_owned_receipt_retry_and_refresh_never_replay(tmp_path):
    calls = []
    lyrics = '[Verse]\n' + ('A line with spaces  preserved and words.\n' * 34) + '[Chorus]\n' + ('é' * 220)
    def handler(req):
        calls.append(req)
        assert req.method == 'POST' and str(req.url) == 'https://openrouter.ai/api/v1/chat/completions'
        assert req.headers['authorization'] == 'Bearer fixture-secret'
        payload = json.loads(req.content)
        assert payload['model'] == PRO and payload['stream'] is True and 'voice' not in payload['audio']
        assert lyrics in payload['messages'][0]['content']
        assert '150 seconds' in payload['messages'][0]['content']
        return httpx.Response(200, content=sse(), headers={'content-type': 'text/event-stream'})
    service, client = setup_service(tmp_path, handler, state())
    value = body(mode='custom', prompt='', lyrics=lyrics, styles='gentle acoustic', duration=150, project_id='project-a')
    async def run():
        first, duplicate = await asyncio.gather(service.create_music('alice', 'project-a', value), service.create_music('alice', 'project-a', value))
        assert first['id'] == duplicate['id']; await drain(service)
        final = service.get('alice', first['id'])
        assert final['status'] == 'completed' and final['model'] == PRO
        artifact = final['outputs'][0].split('/')[4]
        assert service.artifacts.binary('alice', artifact) == (MP3, 'audio/mpeg')
        with pytest.raises(HTTPException): service.artifacts.binary('bob', artifact)
        assert await service.refresh('alice', first['id']) == final
        assert await service.create_music('alice', 'project-a', value) == final
        with pytest.raises(HTTPException, match='different arguments'):
            await service.create_music('alice', 'project-a', {**value, 'lyrics': lyrics+'changed'})
        assert service.list('bob', 'project-a') == [] and service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run()); assert len(calls) == 1


def test_models_validate_their_own_duration_and_lyric_constraints():
    MusicInput.model_validate(body(model=CLIP, duration=30))
    with pytest.raises(ValidationError): MusicInput.model_validate(body(model=CLIP, duration=60))
    MusicInput.model_validate(body(duration=184))
    with pytest.raises(ValidationError): MusicInput.model_validate(body(duration=185))
    lyrics = '\n'.join(['x' * 220] * 34)
    MusicInput.model_validate(body(mode='custom', prompt='', lyrics=lyrics, duration=150))
    with pytest.raises(ValidationError): MusicInput.model_validate(body(model='elevenlabs/music_v2_5', mode='custom', prompt='', lyrics=lyrics, duration=60))


@pytest.mark.parametrize('change,audio,expected', [({}, False, 'audio support'), ({'model': 'openai/gpt-audio'}, True, 'not supported')])
def test_unsupported_account_or_model_never_reserves_or_dispatches(tmp_path, change, audio, expected):
    def no_effect(req): raise AssertionError('No generation before supported account and model')
    service, client = setup_service(tmp_path, no_effect, state(audio=audio))
    async def run():
        with pytest.raises(HTTPException, match=expected): await service.create_music('alice', None, body(**change))
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


def test_music_default_matches_account_and_fixed_or_prompt_duration(tmp_path):
    service, client = setup_service(tmp_path, lambda req: (_ for _ in ()).throw(AssertionError('No inference')), state())
    async def run():
        values = initial_settings().public()
        values['defaults'] = {'music': {'provider_id': 'or', 'model': CLIP, 'duration': 30}}
        saved = await service.update_settings('alice', values)
        assert saved['settings']['defaults']['music']['model'] == CLIP
        with pytest.raises(HTTPException, match='duration'):
            await service.update_settings('alice', {**values, 'revision': 1, 'defaults': {'music': {'provider_id': 'or', 'model': CLIP, 'duration': 31}}})
        values.update(revision=1, defaults={'music': {'provider_id': 'or', 'model': PRO, 'duration': 120}})
        assert (await service.update_settings('alice', values))['settings']['revision'] == 2
        await client.aclose()
    asyncio.run(run())


def test_optional_music_cap_cannot_claim_prompt_target_is_a_hard_duration(tmp_path):
    service, client = setup_service(tmp_path, lambda req: (_ for _ in ()).throw(AssertionError('No generation')), state())
    async def run():
        values = initial_settings().public(); values['limits']['max_music_seconds'] = 120
        await service.update_settings('alice', values)
        with pytest.raises(HTTPException, match='music duration'):
            await service.create_music('alice', None, body(duration=60))
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


def test_explicit_provider_deadline_is_forwarded_to_music_and_never_replayed(tmp_path):
    calls = []
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1); yield sse()
    def handler(req):
        calls.append(req); assert req.extensions['timeout']['read'] == .01
        return httpx.Response(200, stream=Slow(), headers={'content-type': 'text/event-stream'})
    service, client = setup_service(tmp_path, handler, state(timeout=.01))
    async def run():
        first = await service.create_music('alice', None, body()); await drain(service)
        assert service.get('alice', first['id'])['status'] == 'unknown'
        assert service.artifacts.list('alice') == []
        await service.create_music('alice', None, body()); assert len(calls) == 1
        await client.aclose()
    asyncio.run(run())
