"""Mocked OpenRouter integration tests; never call a paid provider."""
import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from termx.media.service import ArtifactService
from termx.media.studio_openrouter import OpenRouterAdapter, OpenRouterError
import termx.media.studio_openrouter as module


def bitmap():
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(stream, format='PNG')
    return stream.getvalue()


def image_spec():
    return {'id': 'google/gemini-3-pro-image', 'label': 'Nano Banana Pro', 'kind': 'image',
            'aspect_ratios': ['1:1', '16:9'], 'resolutions': ['1K', '2K'], 'durations': [], '_supports_image': True}


def video_spec():
    return {'id': 'google/veo-3.1', 'kind': 'video', 'aspect_ratios': ['16:9'],
            'resolutions': ['720p'], 'durations': [4, 6, 8], '_supports_image': False}


def run_client(tmp_path, handler, operation, *, image_endpoints=True, **client_options):
    def transport(request):
        if image_endpoints and request.method == 'GET' and request.url.path.startswith('/api/v1/images/models/') and request.url.path.endswith('/endpoints'):
            identifier = request.url.path.removeprefix('/api/v1/images/models/').removesuffix('/endpoints')
            assert 'authorization' not in request.headers and 'cookie' not in request.headers
            return httpx.Response(200, json={'id': identifier, 'endpoints': [{
                'provider_tag': 'test-image-provider', 'supported_parameters': {
                    'resolution': {'type': 'enum', 'values': ['1K', '2K']},
                    'aspect_ratio': {'type': 'enum', 'values': ['1:1', '16:9']},
                    'input_references': {'type': 'range', 'min': 0, 'max': 5}}}]})
        return handler(request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), **client_options) as client:
            artifacts = ArtifactService(tmp_path)
            return await operation(OpenRouterAdapter(artifacts, client), artifacts)
    return asyncio.run(run())


def test_dynamic_discovery_capabilities_and_heygen_nulls(tmp_path):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        assert request.url.host == 'openrouter.ai'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert request.extensions['timeout']['read'] <= 8
        if request.url.path == '/api/v1/models':
            return httpx.Response(200, json={'data': [{'id': identifier, 'architecture': {
                'input_modalities': ['text', 'image', 'audio'], 'output_modalities': ['video']}}
                for identifier in ('google/veo-3.1', 'heygen/avatar-iv')]})
        if request.url.path.endswith('/images/models'):
            return httpx.Response(200, json={'data': [{
                'id': 'new/future-image', 'name': 'Future image', 'description': 'Discovered dynamically',
                'architecture': {'input_modalities': ['text', 'image'], 'output_modalities': ['image']},
                'supported_parameters': {'resolution': {'type': 'enum', 'values': ['1K', '2K']},
                    'aspect_ratio': {'type': 'enum', 'values': ['1:1', '16:9']},
                    'input_references': {'type': 'range', 'min': 0, 'max': 2}},
                'endpoints': 'https://untrusted.example/ignored'}]})
        if request.url.path.endswith('/endpoints'):
            return httpx.Response(200, json={'data': {'id': 'heygen/avatar-iv',
                'architecture': {'input_modalities': ['text', 'image', 'audio'], 'output_modalities': ['video']},
                'endpoints': [{'tag': 'heygen'}]}})
        return httpx.Response(200, json={'data': [
            {'id': 'google/veo-3.1', 'name': 'Veo 3.1', 'supported_durations': [8, 4, 6],
             'supported_resolutions': ['720p'], 'supported_aspect_ratios': ['16:9']},
            {'id': 'heygen/avatar-iv', 'name': 'Avatar IV', 'supported_durations': None,
             'allowed_passthrough_parameters': ['motion_prompt'],
             'supported_resolutions': ['720p', '1080p'], 'supported_aspect_ratios': ['16:9', '9:16', '1:1']}]})
    async def operation(adapter, _):
        models = await adapter.discover()
        assert [m['id'] for m in models] == ['new/future-image', 'google/veo-3.1', 'heygen/avatar-iv']
        assert models[0]['adapter'] == 'openrouter'
        assert models[0]['modes'] == ['image', 'cinema'] and models[0]['_supports_image']
        assert models[1]['durations'] == [4, 6, 8] and not models[1]['_supports_image']
        assert models[2]['modes'] == ['lipsync'] and models[2]['requires_image'] and models[2]['requires_audio']
        assert models[2]['durations'] == [] and models[2]['_supports_image']
    run_client(tmp_path, handler, operation, headers={'Authorization': 'must-not-leak'},
               cookies={'session': 'must-not-leak'}, auth=('must-not-leak', 'secret'))
    assert paths == ['/api/v1/models', '/api/v1/images/models', '/api/v1/videos/models', '/api/v1/models/heygen/avatar-iv/endpoints']


@pytest.mark.parametrize('response', [[], {}, {'data': None}, {'data': ['bad']},
    {'data': [{'id': 'bad?url', 'name': 'Bad'}]},
    {'data': [{'id': 'provider/../credits', 'name': 'Traversal'}]},
    {'data': [{'id': 'x/y', 'name': 'Bad', 'supported_parameters': {'resolution': {'type': 'enum', 'values': [42]}}}]}])
def test_malformed_discovery_fails_without_static_fallback(tmp_path, response):
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError):
            await adapter.discover()
    run_client(tmp_path, lambda request: httpx.Response(200, json=response), operation)


def test_image_submission_uses_current_protocol_and_private_artifact(tmp_path):
    image = bitmap()
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'POST' and request.url.path == '/api/v1/images'
        assert request.headers['authorization'] == 'Bearer fixture-key'
        payload = json.loads(request.content)
        assert payload == {'model': 'google/gemini-3-pro-image', 'prompt': 'Blue square', 'n': 1,
                           'aspect_ratio': '16:9', 'resolution': '2K',
                           'input_references': [{'type': 'image_url', 'image_url': {'url': 'https://cdn.example.org/source.png'}}],
                           'provider': {'only': ['test-image-provider'], 'allow_fallbacks': False}}
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(image).decode(), 'media_type': 'image/png'}]})
    async def operation(adapter, artifacts):
        result = await adapter.submit(image_spec(), {'prompt': 'Blue square', 'aspect_ratio': '16:9',
            'resolution': '2K', 'image_url': 'https://cdn.example.org/source.png'}, 'fixture-key', 'alice', 'project-a')
        assert result['status'] == 'completed'
        url = result['outputs'][0]
        assert url.startswith('/api/media/artifacts/') and url.endswith('/export?format=original')
        identifier = url.split('/')[4]
        stored = artifacts.get('alice', identifier)
        assert stored['kind'] == 'image' and stored['project'] == 'project-a'
        assert artifacts.binary('alice', identifier) == (image, 'image/png')
        with pytest.raises(HTTPException):
            artifacts.get('bob', identifier)
        assert 'fixture-key' not in json.dumps(result)
    run_client(tmp_path, handler, operation)
    assert len(calls) == 1


@pytest.mark.parametrize('extra', [{'duration': 5}, {'resolution': '4K'}, {'audio_url': 'https://cdn.example.org/a.mp3'},
    {'video_url': 'https://cdn.example.org/a.mp4'}, {'image_url': 'http://cdn.example.org/a.png'},
    {'image_url': 'https://127.0.0.1/a.png'}, {'image_url': 'https://localhost/a.png'},
    {'image_url': 'https://user:secret@cdn.example.org/a.png'}])
def test_unsupported_settings_reject_before_billing(tmp_path, extra):
    calls = []
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(image_spec(), {'prompt': 'Square', **extra}, 'fixture-key', 'alice', None)
        assert error.value.uncertain is False
    run_client(tmp_path, lambda request: calls.append(request), operation)
    assert calls == []


@pytest.mark.parametrize('output', [
    {'b64_json': 'not-base64'}, {'url': 'https://untrusted.example/output.png'},
    {'b64_json': base64.b64encode(b'<svg onload="alert(1)"></svg>').decode(), 'media_type': 'image/svg+xml'},
    {'b64_json': base64.b64encode(b'not-an-image').decode(), 'media_type': 'image/png'},
    {'b64_json': base64.b64encode(bitmap()).decode(), 'media_type': 'image/jpeg'}])
def test_invalid_or_remote_image_output_is_unknown_not_fetched(tmp_path, output):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'data': [output]})
    async def operation(adapter, artifacts):
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(image_spec(), {'prompt': 'Square'}, 'fixture-key', 'alice', 'p')
        assert error.value.uncertain
        assert artifacts.list('alice') == []
    run_client(tmp_path, handler, operation)
    assert calls == ['https://openrouter.ai/api/v1/images']


def test_image_pixel_limit_prevents_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'PIXEL_LIMIT', 4)
    async def operation(adapter, artifacts):
        with pytest.raises(OpenRouterError, match='pixel limit'):
            await adapter.submit(image_spec(), {'prompt': 'Square'}, 'fixture-key', 'alice', None)
        assert artifacts.list('alice') == []
    run_client(tmp_path, lambda request: httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(bitmap()).decode()}]}), operation)


@pytest.mark.parametrize('status,uncertain', [(400, False), (401, False), (402, False), (403, False),
    (408, True), (429, False), (499, True), (500, True), (502, True)])
def test_http_refusals_and_uncertain_errors_are_sanitized(tmp_path, status, uncertain):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={'error': 'secret-in-provider-body'})
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(image_spec(), {'prompt': 'Square'}, 'fixture-key', 'alice', None)
        assert error.value.uncertain is uncertain
        assert error.value.message == str(error.value)
        assert 'secret-in-provider-body' not in str(error.value)
    run_client(tmp_path, handler, operation)
    assert len(calls) == 1


@pytest.mark.parametrize('kind', ['timeout', 'malformed', 'semantic'])
def test_transport_and_body_errors_never_retry(tmp_path, kind):
    calls = []
    def handler(request):
        calls.append(request)
        if kind == 'timeout':
            raise httpx.ReadTimeout('secret-in-provider-error', request=request)
        if kind == 'semantic':
            return httpx.Response(200, json={'error': {'code': 422, 'message': 'secret-in-provider-body'}})
        return httpx.Response(200, content=b'not-json secret-in-provider-body')
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(image_spec(), {'prompt': 'Square'}, 'fixture-key', 'alice', None)
        assert error.value.uncertain is (kind != 'semantic')
        assert 'secret-in-provider' not in str(error.value)
    run_client(tmp_path, handler, operation)
    assert len(calls) == 1


def test_video_receipt_status_and_fixed_download_path(tmp_path):
    calls = []
    mp4 = b'\x00\x00\x00\x18ftypisom' + b'fixture-video'
    def handler(request):
        calls.append(request.url.path)
        assert request.url.host == 'openrouter.ai'
        assert request.headers['authorization'] == 'Bearer fixture-key'
        if request.method == 'POST':
            assert json.loads(request.content) == {'model': 'google/veo-3.1', 'prompt': 'Moving clouds',
                                                  'duration': 4, 'resolution': '720p', 'aspect_ratio': '16:9'}
            return httpx.Response(202, json={'id': 'safe-job_123', 'status': 'pending', 'polling_url': 'https://untrusted.example/poll'})
        if request.url.path.endswith('/content'):
            assert request.url.query == b'index=0'
            return httpx.Response(200, content=mp4, headers={'Content-Type': 'video/mp4'})
        return httpx.Response(200, json={'id': 'safe-job_123', 'status': 'completed',
            'unsigned_urls': ['https://untrusted.example/leak'], 'urls': ['http://127.0.0.1/admin']})
    async def operation(adapter, artifacts):
        receipt = await adapter.submit(video_spec(), {'prompt': 'Moving clouds', 'duration': 4,
            'resolution': '720p', 'aspect_ratio': '16:9'}, 'fixture-key', 'alice', 'project-b')
        assert receipt == {'status': 'queued', 'task_id': 'safe-job_123'}
        result = await adapter.refresh(receipt['task_id'], 'fixture-key', 'alice', 'project-b')
        assert result['status'] == 'completed'
        identifier = result['outputs'][0].split('/')[4]
        assert artifacts.binary('alice', identifier) == (mp4, 'video/mp4')
        assert artifacts.get('alice', identifier)['project'] == 'project-b'
    run_client(tmp_path, handler, operation)
    assert calls == ['/api/v1/videos', '/api/v1/videos/safe-job_123', '/api/v1/videos/safe-job_123/content']


@pytest.mark.parametrize('direction', ['', 'Speak warmly with subtle hand gestures.'])
def test_heygen_lipsync_exact_references_and_provider_pin(tmp_path, direction):
    calls = []
    spec = {**video_spec(), 'id': 'heygen/avatar-iv', 'durations': [], '_supports_image': True,
            'modes': ['lipsync'], '_contract_verified': True, '_contract': 'avatar',
            '_provider_pin': 'heygen', '_allowed_passthrough': ['motion_prompt'],
            '_supports_audio': True, '_supports_reference_image': True}
    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload == {'model': 'heygen/avatar-iv', 'prompt': '', 'resolution': '720p',
            'input_references': [
                {'type': 'image_url', 'image_url': {'url': 'https://cdn.example.org/portrait.png'}},
                {'type': 'audio_url', 'audio_url': {'url': 'https://cdn.example.org/voice.mp3'}}],
            'provider': {'only': ['heygen'], 'allow_fallbacks': False, **(
                {'options': {'heygen': {'motion_prompt': direction}}} if direction else {})}}
        return httpx.Response(202, json={'id': 'talk-job', 'status': 'pending'})
    async def operation(adapter, _):
        result = await adapter.submit(spec, {'prompt': direction, 'resolution': '720p',
            'image_url': 'https://cdn.example.org/portrait.png', 'audio_url': 'https://cdn.example.org/voice.mp3'},
            'fixture-key', 'alice', 'p')
        assert result == {'status': 'queued', 'task_id': 'talk-job'}
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(spec, {'prompt': '', 'image_url': 'https://cdn.example.org/portrait.png'}, 'fixture-key', 'alice', 'p')
        assert not error.value.uncertain
    run_client(tmp_path, handler, operation)
    assert len(calls) == 1


@pytest.mark.parametrize('status,result', [('pending', 'queued'), ('in_progress', 'running'),
    ('failed', 'failed'), ('cancelled', 'failed'), ('expired', 'failed')])
def test_video_refresh_statuses_and_sanitized_failure(tmp_path, status, result):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'job', 'status': status, 'error': 'sensitive-provider-body'})
    async def operation(adapter, _):
        output = await adapter.refresh('job', 'fixture-key', 'alice', None)
        assert output['status'] == result
        assert 'sensitive-provider-body' not in json.dumps(output)
    run_client(tmp_path, handler, operation)
    assert len(calls) == 1


def test_malformed_video_status_retains_valid_receipt(tmp_path):
    async def operation(adapter, _):
        output = await adapter.submit(video_spec(), {'prompt': 'Clouds'}, 'fixture-key', 'alice', None)
        assert output['status'] == 'unknown' and output['task_id'] == 'receipt-123'
    run_client(tmp_path, lambda request: httpx.Response(202, json={'id': 'receipt-123', 'status': 'unexpected'}), operation)


@pytest.mark.parametrize('identifier', ['../admin', 'job?key=leak', 'https://untrusted.example', 'job%2fother', ''])
def test_invalid_job_id_never_fetches(tmp_path, identifier):
    calls = []
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError):
            await adapter.refresh(identifier, 'fixture-key', 'alice', None)
    run_client(tmp_path, lambda request: calls.append(request), operation)
    assert calls == []


@pytest.mark.parametrize('stage', ['discover', 'image', 'video'])
def test_oversize_stream_is_closed_before_reading_rest(tmp_path, monkeypatch, stage):
    class Oversized(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'a' * 8
            yield b'b' * 8
            raise AssertionError('Must stop at the byte bound')
        async def aclose(self):
            self.closed = True
    monkeypatch.setattr(module, 'DISCOVERY_LIMIT', 12)
    monkeypatch.setattr(module, 'LIMIT', 12)
    # Default args capture DISCOVERY_LIMIT, so discovery uses the explicit
    # method bound here rather than requiring a megabyte fixture.
    stream = Oversized()
    def handler(request):
        if stage == 'video' and not request.url.path.endswith('/content'):
            return httpx.Response(200, json={'id': 'job', 'status': 'completed'})
        return httpx.Response(200, stream=stream)
    async def operation(adapter, artifacts):
        with pytest.raises(OpenRouterError, match='media limit') as error:
            if stage == 'discover':
                await adapter._request('GET', '/images/models', limit=12)
            elif stage == 'image':
                await adapter.submit(image_spec(), {'prompt': 'Square'}, 'fixture-key', 'alice', None)
            else:
                await adapter.refresh('job', 'fixture-key', 'alice', None)
        assert error.value.uncertain and stream.closed
        assert artifacts.list('alice') == []
    run_client(tmp_path, handler, operation)


def test_redirects_are_not_followed_even_with_client_redirects_enabled(tmp_path):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'https://untrusted.example/collect'})
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError):
            await adapter.submit(image_spec(), {'prompt': 'Square'}, 'fixture-key', 'alice', None)
    run_client(tmp_path, handler, operation, follow_redirects=True)
    assert calls == ['https://openrouter.ai/api/v1/images']


def test_discovery_excludes_unimplemented_source_video_operations(tmp_path):
    def handler(request):
        if request.url.path.endswith('/images/models'):
            return httpx.Response(200, json={'data': []})
        if request.url.path == '/api/v1/models':
            return httpx.Response(200, json={'data': [{'id': identifier, 'architecture': {
                'input_modalities': ['text'], 'output_modalities': ['video']}}
                for identifier in ('provider/text-and-edit', 'provider/text-to-video')]})
        return httpx.Response(200, json={'data': [
            {'id': 'runway/upscale-v1', 'name': 'Video upscale'},
            {'id': 'provider/video-edit', 'name': 'Video edit'},
            {'id': 'provider/extend', 'name': 'Extension'},
            {'id': 'provider/motion-control', 'name': 'Motion control'},
            {'id': 'runway/aleph-2', 'name': 'Aleph'},
            {'id': 'tbc/mischief-1', 'name': 'Mischief'},
            {'id': 'provider/image-to-video', 'name': 'Image-only'},
            {'id': 'provider/source-required', 'name': 'Needs video', 'requires_video': True},
            {'id': 'provider/text-and-edit', 'name': 'Text compatible', 'supports_text_to_video': True},
            {'id': 'provider/text-to-video', 'name': 'Prompt video'}]})
    async def operation(adapter, _):
        models = await adapter.discover()
        assert [model['id'] for model in models] == ['provider/text-and-edit', 'provider/text-to-video']
    run_client(tmp_path, handler, operation)


def test_image_discovery_requires_reference_and_excludes_vector_outputs(tmp_path):
    def handler(request):
        if request.url.path.endswith('/videos/models'):
            return httpx.Response(200, json={'data': []})
        return httpx.Response(200, json={'data': [
            {'id': 'krea/k3-edit', 'name': 'Edit image', 'architecture': {'input_modalities': ['text', 'image'], 'output_modalities': ['image']}, 'supported_parameters': {
                'input_references': {'type': 'range', 'min': 1, 'max': 3}}},
            {'id': 'recraft/v4-vector', 'name': 'Vector image'},
            {'id': 'provider/svg-only', 'name': 'Vector output', 'supported_parameters': {
                'output_format': {'type': 'enum', 'values': ['svg']}}},
            {'id': 'provider/multi-image-required', 'name': 'Two sources', 'supported_parameters': {
                'input_references': {'type': 'range', 'min': 2, 'max': 3}}}]})
    async def operation(adapter, _):
        models = await adapter.discover()
        assert len(models) == 1 and models[0]['id'] == 'krea/k3-edit'
        assert models[0]['requires_image'] and models[0]['_supports_image']
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(models[0], {'prompt': 'Make it blue'}, 'fixture-key', 'alice', None)
        assert error.value.uncertain is False
    run_client(tmp_path, handler, operation)


def test_supported_first_frame_uses_frame_images_not_style_references(tmp_path):
    def handler(request):
        if request.url.path.endswith('/images/models'):
            return httpx.Response(200, json={'data': []})
        if request.url.path == '/api/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'google/veo-3.1', 'architecture': {
                'input_modalities': ['text', 'image'], 'output_modalities': ['video']}}]})
        if request.url.path.endswith('/videos/models'):
            return httpx.Response(200, json={'data': [{
                'id': 'google/veo-3.1', 'name': 'Veo', 'supported_frame_images': ['first_frame', 'last_frame']}]})
        payload = json.loads(request.content)
        assert payload == {'model': 'google/veo-3.1', 'prompt': 'Move the clouds', 'frame_images': [{
            'type': 'image_url', 'image_url': {'url': 'https://cdn.example.org/frame.png'}, 'frame_type': 'first_frame'}]}
        return httpx.Response(202, json={'id': 'video-receipt', 'status': 'pending'})
    async def operation(adapter, _):
        model = (await adapter.discover())[0]
        assert model['_supports_image'] and model['_supports_first_frame']
        assert await adapter.submit(model, {'prompt': 'Move the clouds', 'image_url': 'https://cdn.example.org/frame.png'},
                                    'fixture-key', 'alice', None) == {'status': 'queued', 'task_id': 'video-receipt'}
    run_client(tmp_path, handler, operation)


def public_capability_fixture():
    path = Path(__file__).parent / 'fixtures' / 'media_studio_openrouter_capabilities.json'
    return json.loads(path.read_text())


def capability_handler(fixture, calls, mutate=None):
    def handle(request):
        calls.append(request)
        assert request.url.host == 'openrouter.ai'
        if request.method == 'GET':
            path = request.url.path.removeprefix('/api/v1')
            if path == '/models':
                path += '?' + str(request.url.query, 'ascii')
            record = fixture['responses'][path]
            value = json.loads(json.dumps(record['body']))
            if mutate:
                mutate(path, value)
            return httpx.Response(200, json=value)
        return httpx.Response(202, json={'id': 'verified-job', 'status': 'pending'})
    return handle


def test_public_metadata_fixture_proves_advanced_modes_and_limits(tmp_path):
    fixture, calls = public_capability_fixture(), []
    async def operation(adapter, _):
        models = {model['id']: model for model in await adapter.discover()}
        avatar = models['heygen/avatar-iv']
        assert avatar['modes'] == ['lipsync'] and avatar['_contract_verified']
        assert avatar['_provider_pin'] == 'heygen' and avatar['_inline_inputs'] == ['image']
        for identifier in ('black-forest-labs/flux-video-edit', 'runway/aleph-2'):
            spec = models[identifier]
            assert spec['modes'] == ['remix'] and spec['requires_video']
            assert not spec['_supports_image'] and spec['_inline_inputs'] == []
        seedance = models['bytedance/seedance-2.5']
        assert seedance['modes'] == ['video', 'remix']
        assert seedance['image_roles'] == ['first_frame', 'reference']
        assert seedance['_requires_video_for_remix'] and seedance['_supports_video']
        kling = models['kwaivgi/kling-v3.0-pro']
        assert kling['_supports_negative_prompt'] and kling['_provider_pin'] == 'atlas-cloud'
        assert models['krea/k3-edit']['requires_image']
    run_client(tmp_path, capability_handler(fixture, calls), operation)
    assert calls and all(request.method == 'GET' and 'authorization' not in request.headers for request in calls)
    assert fixture['credentials_sent'] is False and fixture['generation_requests_sent'] is False


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_generic_modes_require_actual_input_and_output_modality_proof(tmp_path, kind):
    fixture, calls = public_capability_fixture(), []
    def mutate(path, value):
        if kind == 'image' and path == '/images/models':
            for entry in value['data']:
                entry['architecture']['output_modalities'] = ['text']
        if kind == 'video' and path == '/models?output_modalities=video':
            for entry in value['data']:
                entry['architecture']['output_modalities'] = []
    async def operation(adapter, _):
        models = await adapter.discover()
        if kind == 'image':
            assert not any(model['kind'] == 'image' for model in models)
        else:
            assert not any(model['id'] in {'google/veo-3.1', 'kwaivgi/kling-v3.0-pro'} for model in models)
            # Independent per-endpoint proof remains sufficient for the
            # explicitly documented advanced contracts.
            assert any(model['id'] == 'heygen/avatar-iv' for model in models)
    run_client(tmp_path, capability_handler(fixture, calls, mutate), operation)
    assert all(request.method == 'GET' for request in calls)


@pytest.mark.parametrize('failure', ['wrong-provider', 'missing-audio', 'missing-output', 'missing-passthrough'])
def test_heygen_model_name_cannot_override_missing_live_contract(tmp_path, failure):
    fixture, calls = public_capability_fixture(), []
    def mutate(path, value):
        if path == '/models/heygen/avatar-iv/endpoints':
            if failure == 'wrong-provider': value['data']['endpoints'] = [{'tag': 'unverified'}]
            if failure == 'missing-audio': value['data']['architecture']['input_modalities'] = ['image', 'text']
            if failure == 'missing-output': value['data']['architecture']['output_modalities'] = ['text']
        if failure == 'missing-passthrough' and path == '/videos/models':
            next(item for item in value['data'] if item['id'] == 'heygen/avatar-iv')['allowed_passthrough_parameters'] = []
    async def operation(adapter, _):
        models = await adapter.discover()
        assert 'heygen/avatar-iv' not in {model['id'] for model in models}
        assert any(model['id'] == 'google/gemini-3-pro-image' for model in models)
    run_client(tmp_path, capability_handler(fixture, calls, mutate), operation)
    assert all(request.method == 'GET' for request in calls)


@pytest.mark.parametrize('identifier,provider', [('black-forest-labs/flux-video-edit', 'black-forest-labs'), ('runway/aleph-2', 'runway')])
def test_verified_remix_uses_video_reference_and_pins_actual_provider(tmp_path, identifier, provider):
    fixture, calls = public_capability_fixture(), []
    async def operation(adapter, _):
        spec = next(model for model in await adapter.discover() if model['id'] == identifier)
        result = await adapter.submit(spec, {'operation': 'remix', 'prompt': 'Make the sky violet',
            'video_url': 'https://media.example.org/clip.mp4'}, 'fixture-key', 'alice', 'project')
        assert result['task_id'] == 'verified-job'
        with pytest.raises(OpenRouterError, match='source video'):
            await adapter.submit(spec, {'operation': 'remix', 'prompt': 'Make the sky violet'}, 'fixture-key', 'alice', 'project')
    run_client(tmp_path, capability_handler(fixture, calls), operation)
    posts = [request for request in calls if request.method == 'POST']
    assert len(posts) == 1
    assert json.loads(posts[0].content) == {'model': identifier, 'prompt': 'Make the sky violet',
        'input_references': [{'type': 'video_url', 'video_url': {'url': 'https://media.example.org/clip.mp4'}}],
        'provider': {'only': [provider], 'allow_fallbacks': False}}


def test_seedance_reference_mode_and_first_frame_are_distinct(tmp_path):
    fixture, calls = public_capability_fixture(), []
    async def operation(adapter, _):
        spec = next(model for model in await adapter.discover() if model['id'] == 'bytedance/seedance-2.5')
        args = {'operation': 'remix', 'prompt': 'Keep the subject, change the setting',
                'image_role': 'reference', 'image_url': 'https://media.example.org/portrait.png',
                'video_url': 'https://media.example.org/clip.mp4'}
        await adapter.submit(spec, args, 'fixture-key', 'alice', 'project')
        with pytest.raises(OpenRouterError, match='cannot be combined'):
            await adapter.submit(spec, {**args, 'image_role': 'first_frame'}, 'fixture-key', 'alice', 'project')
    run_client(tmp_path, capability_handler(fixture, calls), operation)
    posts = [request for request in calls if request.method == 'POST']
    assert len(posts) == 1
    payload = json.loads(posts[0].content)
    assert 'frame_images' not in payload
    assert [part['type'] for part in payload['input_references']] == ['image_url', 'video_url']
    assert payload['provider'] == {'only': ['seed'], 'allow_fallbacks': False}


def test_local_image_uses_only_trusted_resolved_reference_and_unsupported_inline_refuses(tmp_path):
    fixture, calls = public_capability_fixture(), []
    encoded = base64.b64encode(bitmap()).decode()
    data_url = 'data:image/png;base64,' + encoded
    async def operation(adapter, _):
        specs = {model['id']: model for model in await adapter.discover()}
        spec = specs['bytedance/seedance-2.5']
        await adapter.submit(spec, {'prompt': 'Move slowly', '_local_references': {
            'image': {'mime': 'image/png', 'data_url': data_url}}}, 'fixture-key', 'alice', 'project')
        with pytest.raises(OpenRouterError, match='public HTTPS'):
            await adapter.submit(spec, {'prompt': 'Move slowly', 'image_url': data_url}, 'fixture-key', 'alice', 'project')
        with pytest.raises(OpenRouterError, match='local audio input is not verified'):
            await adapter.submit(spec, {'prompt': 'Move slowly', '_local_references': {
                'audio': {'mime': 'audio/mpeg', 'data_url': 'data:audio/mpeg;base64,AAAA'}}}, 'fixture-key', 'alice', 'project')
    run_client(tmp_path, capability_handler(fixture, calls), operation)
    posts = [request for request in calls if request.method == 'POST']
    assert len(posts) == 1
    assert json.loads(posts[0].content)['frame_images'][0]['image_url']['url'] == data_url


@pytest.mark.parametrize('identifier,key,nested', [('kwaivgi/kling-v3.0-pro', 'negative_prompt', False), ('google/veo-3.1', 'negativePrompt', True)])
def test_negative_prompt_uses_declared_provider_field_and_nesting(tmp_path, identifier, key, nested):
    fixture, calls = public_capability_fixture(), []
    async def operation(adapter, _):
        spec = next(model for model in await adapter.discover() if model['id'] == identifier)
        await adapter.submit(spec, {'prompt': 'Clear sky', 'negative_prompt': 'fog'}, 'fixture-key', 'alice', 'project')
    run_client(tmp_path, capability_handler(fixture, calls), operation)
    payload = json.loads(next(request for request in calls if request.method == 'POST').content)
    provider = payload['provider']['only'][0]
    expected = {'parameters': {key: 'fog'}} if nested else {key: 'fog'}
    assert payload['provider']['options'] == {provider: expected}


def composition_handler(fixture, calls, mutate=None):
    metadata = capability_handler(fixture, calls, mutate)
    def handle(request):
        if request.method == 'POST':
            calls.append(request)
            assert request.url.path == '/api/v1/images'
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(bitmap()).decode(), 'media_type': 'image/png'}]})
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        return metadata(request)
    return handle


def test_ordered_multi_image_composition_pins_only_count_and_resolution_compatible_provider(tmp_path):
    fixture, calls = public_capability_fixture(), []
    urls = ['https://media.example.org/' + name + '.png' for name in ('person', 'product', 'setting')]
    async def operation(adapter, _):
        spec = next(model for model in await adapter.discover() if model['id'] == 'google/gemini-3-pro-image')
        assert spec['image_reference_limits'] == {'min': 0, 'max': 14, 'app_max': 5}
        assert spec['pricing_evidence'] == ['https://openrouter.ai/api/v1/images/models/google/gemini-3-pro-image/endpoints']
        result = await adapter.submit(spec, {'prompt': 'Compose these in reference order', 'resolution': '4K',
            'image_references': [{'image_url': url} for url in urls]}, 'fixture-key', 'alice', 'project')
        assert result['status'] == 'completed'
    run_client(tmp_path, composition_handler(fixture, calls), operation, image_endpoints=False,
               headers={'Authorization': 'must-not-leak'}, cookies={'session': 'must-not-leak'})
    posts = [request for request in calls if request.method == 'POST']
    assert len(posts) == 1
    payload = json.loads(posts[0].content)
    assert [part['image_url']['url'] for part in payload['input_references']] == urls
    assert payload['provider'] == {'only': ['google-ai-studio/global'], 'allow_fallbacks': False}
    assert posts[0].headers['authorization'] == 'Bearer fixture-key'


def test_owned_and_public_image_composition_preserves_order_without_exposing_artifact_ids(tmp_path):
    fixture, calls = public_capability_fixture(), []
    data_url = 'data:image/png;base64,' + base64.b64encode(bitmap()).decode()
    url = 'https://media.example.org/product.png'
    async def operation(adapter, _):
        spec = next(model for model in await adapter.discover() if model['id'] == 'google/gemini-3-pro-image')
        await adapter.submit(spec, {'prompt': 'Compose', 'image_references': [
            {'image_id': 'owned-first'}, {'image_url': url}, {'image_id': 'owned-last'}],
            '_local_image_references': [{'data_url': data_url, 'mime': 'image/png'}, {'url': url},
                                        {'data_url': data_url, 'mime': 'image/png'}]}, 'fixture-key', 'alice', 'project')
    run_client(tmp_path, composition_handler(fixture, calls), operation, image_endpoints=False)
    post = next(request for request in calls if request.method == 'POST')
    payload = json.loads(post.content)
    assert [part['image_url']['url'] for part in payload['input_references']] == [data_url, url, data_url]
    assert b'owned-first' not in post.content and b'owned-last' not in post.content and b'_local' not in post.content
    assert payload['provider']['allow_fallbacks'] is False


@pytest.mark.parametrize('failure', ['too-many', 'legacy-conflict', 'unresolved-owned', 'wrong-resolved-count', 'one-image-model', 'video-array'])
def test_invalid_composition_is_refused_before_provider_submission(tmp_path, failure):
    calls = []
    spec = {**image_spec(), 'image_reference_limits': {'min': 0, 'max': 14, 'app_max': 5}, '_inline_inputs': ['image']}
    args = {'prompt': 'Compose', 'image_references': [{'image_url': 'https://media.example.org/a.png'}, {'image_url': 'https://media.example.org/b.png'}]}
    if failure == 'too-many': args['image_references'] *= 3
    if failure == 'legacy-conflict': args['image_url'] = 'https://media.example.org/scalar.png'
    if failure == 'unresolved-owned': args['image_references'] = [{'image_id': 'must-be-resolved'}]
    if failure == 'wrong-resolved-count': args['_local_image_references'] = [{'url': 'https://media.example.org/a.png'}]
    if failure == 'one-image-model': spec['image_reference_limits'] = {'min': 0, 'max': 1, 'app_max': 1}
    if failure == 'video-array': spec = video_spec()
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(spec, args, 'fixture-key', 'alice', 'project')
        assert not error.value.uncertain
    run_client(tmp_path, lambda request: calls.append(request), operation, image_endpoints=False)
    assert calls == []


@pytest.mark.parametrize('failure', ['reference-max', 'reference-min', 'resolution', 'provider-tag', 'vector', 'n-min', 'identity'])
def test_no_eligible_image_endpoint_fails_without_charging_or_silent_truncation(tmp_path, failure):
    fixture, calls = public_capability_fixture(), []
    def mutate(path, value):
        if path == '/images/models/google/gemini-3-pro-image/endpoints':
            if failure == 'identity': value['id'] = 'different/model'
            for endpoint in value['endpoints']:
                parameters = endpoint['supported_parameters']
                if failure == 'reference-max': parameters['input_references']['max'] = 1
                if failure == 'reference-min': parameters['input_references']['min'] = 3
                if failure == 'resolution': parameters['resolution']['values'] = ['1K']
                if failure == 'provider-tag': endpoint['provider_tag'] = None
                if failure == 'vector': parameters['output_format'] = {'type': 'enum', 'values': ['svg']}
                if failure == 'n-min': parameters['n']['min'] = 2; parameters['n']['max'] = 3
    async def operation(adapter, _):
        spec = next(model for model in await adapter.discover() if model['id'] == 'google/gemini-3-pro-image')
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(spec, {'prompt': 'Compose', 'resolution': '2K', 'image_references': [
                {'image_url': 'https://media.example.org/a.png'}, {'image_url': 'https://media.example.org/b.png'}]},
                'fixture-key', 'alice', 'project')
        assert not error.value.uncertain and 'No generation was submitted' in str(error.value)
    run_client(tmp_path, composition_handler(fixture, calls, mutate), operation, image_endpoints=False)
    assert all(request.method == 'GET' for request in calls)


@pytest.mark.parametrize('failure', ['timeout', 'unavailable'])
def test_image_endpoint_preflight_failure_is_known_unbilled(tmp_path, failure):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.method == 'GET' and 'authorization' not in request.headers
        if failure == 'timeout': raise httpx.ReadTimeout('fixture-secret', request=request)
        return httpx.Response(503, text='fixture-secret')
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError) as error:
            await adapter.submit(image_spec(), {'prompt': 'Blue square'}, 'fixture-key', 'alice', 'project')
        assert not error.value.uncertain and 'No generation was submitted' in str(error.value)
        assert 'fixture-secret' not in str(error.value)
    run_client(tmp_path, handle, operation, image_endpoints=False)
    assert len(calls) == 1


def test_multi_image_inline_budget_is_aggregate_encoded_bytes(tmp_path, monkeypatch):
    calls = []
    data_url = 'data:image/png;base64,' + base64.b64encode(bitmap()).decode()
    assert module._INLINE_TOTAL_LIMIT == 20 * 1024 * 1024
    monkeypatch.setattr(module, '_INLINE_TOTAL_LIMIT', len(data_url) * 2)
    spec = {**image_spec(), 'image_reference_limits': {'min': 0, 'max': 14, 'app_max': 5}, '_inline_inputs': ['image']}
    async def operation(adapter, _):
        with pytest.raises(OpenRouterError, match='20 MiB encoded') as error:
            await adapter.submit(spec, {'prompt': 'Compose', '_local_image_references': [
                {'data_url': data_url, 'mime': 'image/png'} for _ in range(3)]}, 'fixture-key', 'alice', 'project')
        assert not error.value.uncertain
    run_client(tmp_path, lambda request: calls.append(request), operation, image_endpoints=False)
    assert calls == []
