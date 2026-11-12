"""Published rates preserve units and scope; metadata never authorizes spending."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from termx.media import studio_pricing
from termx.media.studio_pricing import StudioPricingService, mount_studio_pricing

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/media_studio_openrouter_capabilities.json').read_text())['responses']
IMAGE = 'google/gemini-3-pro-image'
VIDEO = 'black-forest-labs/flux-video-edit'


def fixture_body(path):
    return FIXTURE[path]['body']


class Transport:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload
        self.error = None
        self.on_request = None

    async def _request(self, method, path, **options):
        self.calls.append((method, path, options))
        if self.on_request:
            self.on_request()
        if self.error:
            raise self.error
        payload = self.payload if self.payload is not None else fixture_body(path)
        return json.dumps(payload).encode(), {}


@pytest.fixture
def workspace():
    transport = Transport()
    grants = {('alice', 'project-a'), ('alice', 'project-b'), ('alice', None), ('bob', 'project-a')}
    decisions = []
    catalog = [{'id': name, 'adapter': 'openrouter', 'kind': kind} for name, kind in [
        (IMAGE, 'image'), ('krea/k3-edit', 'image'), (VIDEO, 'video'), ('heygen/avatar-iv', 'video'),
        ('bytedance/seedance-2.5', 'video'), ('google/veo-3.1', 'video')]]
    loads = []
    async def load_catalog():
        loads.append(True)
    studio = SimpleNamespace(openrouter=transport, _catalog=catalog, _load_catalog=load_catalog)
    def actor(request, project, scope):
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        decisions.append((token, project, scope))
        if token not in ('alice', 'bob'):
            raise HTTPException(401, 'Sign in required')
        if (token, project) not in grants:
            raise HTTPException(403, 'Project access revoked')
        assert scope == 'agent-view'
        return token
    app = FastAPI()
    router = APIRouter(prefix='/api/media')
    service = mount_studio_pricing(router, SimpleNamespace(studio=studio), actor)
    app.include_router(router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, transport=transport, grants=grants, decisions=decisions,
                              catalog=catalog, loads=loads, service=service)


def get(workspace, model=IMAGE, project='project-a', owner='alice'):
    params = {'model': model}
    if project is not None:
        params['project_id'] = project
    return workspace.client.get('/api/media/studio/pricing', params=params,
                                headers={'Authorization': 'Bearer ' + owner} if owner else {})


def test_image_token_rates_are_not_per_image_quotes(workspace):
    response = get(workspace)
    guide = response.json()
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert set(guide) == {'model', 'status', 'rates', 'source_url', 'observed_at'}
    assert guide['status'] == 'available' and guide['model'] == IMAGE
    assert guide['source_url'] == 'https://openrouter.ai/api/v1/images/models/' + IMAGE + '/endpoints'
    assert len(guide['rates']) == 4
    assert {row['provider'] for row in guide['rates']} == {'Google AI Studio', 'Google Vertex'}
    assert {row['unit'] for row in guide['rates']} == {'token'}
    assert [row['cost_usd'] for row in guide['rates'] if row['billable'] == 'output_image'] == ['0.00012', '0.00012']
    assert all(isinstance(row['cost_usd'], str) for row in guide['rates'])
    method, path, options = workspace.transport.calls[0]
    assert method == 'GET' and path.startswith('/images/models/')
    assert options == {'key': None, 'limit': 1024 * 1024, 'timeout': 4.0}
    assert 'estimate' not in response.text and 'free' not in response.text


def test_image_preserves_additive_input_rates_variants_and_surcharges(workspace):
    guide = get(workspace, 'krea/k3-edit').json()
    assert [(r['billable'], r['unit'], r['cost_usd'], r.get('variant')) for r in guide['rates']] == [
        ('output_image', 'image', '0.03', None), ('output_image', 'image', '0.06', '2k'),
        ('input_image', 'image', '0.02', None), ('input_image', 'image', '0.07', '2k'),
        ('output_image', 'request', '0.02', 'high_thinking_surcharge')]
    assert all(row['provider'] == 'Krea' for row in guide['rates'])


@pytest.mark.parametrize('model,expected', [
    (VIDEO, [('second', '0.03', None)]),
    ('heygen/avatar-iv', [('second', '0.05', None)]),
    ('bytedance/seedance-2.5', [('token', '0.0000107', None), ('token', '0.0000107', 'without_audio'), ('token', '0.0000064', 'with_video_input')]),
    ('google/veo-3.1', [('second', '0.4', 'with_audio'), ('second', '0.6', 'with_audio_4k'), ('second', '0.2', 'without_audio'), ('second', '0.4', 'without_audio_4k')]),
])
def test_video_sku_units_and_cents_are_authoritative(workspace, model, expected):
    guide = get(workspace, model).json()
    assert guide['status'] == 'available'
    assert [(r['unit'], r['cost_usd'], r.get('variant')) for r in guide['rates']] == expected
    assert guide['source_url'] == 'https://openrouter.ai/api/v1/videos/models'
    assert workspace.transport.calls[0][1] == '/videos/models'
    assert all(row['provider'] == 'OpenRouter (model rates)' for row in guide['rates'])
    assert 'total' not in guide and 'estimated_total' not in guide


def test_general_chat_placeholders_never_imply_free_video(workspace):
    workspace.transport.payload = fixture_body('/models/' + VIDEO + '/endpoints')
    guide = get(workspace, VIDEO).json()
    assert guide['status'] == 'unavailable' and guide['rates'] == []
    assert 'free' not in json.dumps(guide).lower()


def test_unknown_skus_are_omitted_without_minimum_across_units(workspace):
    workspace.transport.payload = {'data': [{'id': VIDEO, 'pricing_skus': {
        'cents_per_second_output': '3', 'video_tokens': '0.0000107', 'unpublished_magic_rate': '0',
        'minimum_cents_per_generation': '56'}}]}
    guide = get(workspace, VIDEO).json()
    assert guide['status'] == 'available' and 'omitted' in guide['message']
    assert [(r['unit'], r['cost_usd']) for r in guide['rates']] == [('second', '0.03'), ('token', '0.0000107'), ('generation', '0.56')]
    assert guide['rates'][2]['billable'] == 'minimum_generation_charge'


@pytest.mark.parametrize('cost', ['-1', 'NaN', 'Infinity', '-Infinity', True, None, '9'*65, '1e99', '1e-99', '0.1234567890123456789012345', {'value': 2}])
def test_invalid_costs_fail_closed(workspace, cost):
    workspace.transport.payload = {'data': [{'id': VIDEO, 'pricing_skus': {'duration_seconds': cost}}]}
    guide = get(workspace, VIDEO).json()
    assert guide['status'] == 'unavailable' and guide['rates'] == []


def test_unknown_image_units_and_conditional_fields_are_not_reinterpreted(workspace):
    workspace.transport.payload = {'id': IMAGE, 'endpoints': [{'provider_tag': 'google-vertex/global', 'pricing': [
        {'billable': 'output_image', 'unit': 'pixel', 'cost_usd': '0.03'},
        {'billable': 'output_image', 'unit': 'image', 'cost_usd': '0.06', 'unknown_condition': '4k'},
        {'billable': 'output_image', 'unit': 'token', 'cost_usd': '0.00012'},
    ]}]}
    guide = get(workspace).json()
    assert len(guide['rates']) == 1 and guide['rates'][0]['unit'] == 'token'
    assert guide['rates'][0]['provider'] == 'google-vertex/global'
    assert 'omitted' in guide['message']


def test_auth_and_catalog_are_rechecked_before_cached_metadata(workspace):
    assert get(workspace).status_code == 200
    assert get(workspace).status_code == 200 and len(workspace.transport.calls) == 1
    assert len(workspace.loads) == 2
    workspace.grants.remove(('alice', 'project-a'))
    assert get(workspace).status_code == 403
    assert get(workspace, owner='').status_code == 401
    assert get(workspace, project='project-b').status_code == 200
    assert get(workspace, owner='bob').status_code == 200
    workspace.catalog.clear()
    assert get(workspace, owner='bob').status_code == 400
    assert len(workspace.transport.calls) == 1
    assert all(row[2] == 'agent-view' for row in workspace.decisions)


@pytest.mark.parametrize('model', ['google//model', 'google/../model', 'google/./model', '/google/model', 'google/model/', 'google/model?secret=x', 'google/model#x', 'https://evil.test/model', 'unknown/model'])
def test_invalid_or_unknown_models_never_reach_transport(workspace, model):
    assert get(workspace, model).status_code == 400
    assert workspace.transport.calls == []


def test_kie_and_other_kinds_do_not_call_provider(workspace):
    workspace.catalog.extend([{'id': 'kie/veo', 'adapter': 'kie', 'kind': 'video'}, {'id': 'openrouter/chat', 'adapter': 'openrouter', 'kind': 'chat'}])
    assert get(workspace, 'kie/veo').status_code == 400
    assert get(workspace, 'openrouter/chat').status_code == 400
    assert not workspace.transport.calls


def test_revocation_during_metadata_fetch_does_not_return_a_rate(workspace):
    workspace.transport.on_request = lambda: workspace.grants.remove(('alice', 'project-a'))
    assert get(workspace).status_code == 403
    assert len(workspace.transport.calls) == 1


@pytest.mark.parametrize('payload', [None, [], {'id': 'other/model', 'endpoints': []}, {'data': {'id': IMAGE, 'endpoints': []}}, {'id': IMAGE, 'endpoints': [{'model_id': 'other/model'}]}, {'error': {'message': 'provider-secret'}}])
def test_bad_public_payloads_are_sanitized(workspace, payload):
    workspace.transport.payload = payload
    if payload is None:
        workspace.transport.error = RuntimeError('provider-secret https://secret.invalid')
    response = get(workspace)
    assert response.status_code == 200 and response.json()['status'] == 'unavailable'
    assert response.json()['rates'] == []
    assert 'provider-secret' not in response.text and 'secret.invalid' not in response.text


def test_cache_expires_after_300_seconds_and_is_bounded(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(studio_pricing, 'monotonic', lambda: clock[0])
    transport = Transport(fixture_body('/images/models/' + IMAGE + '/endpoints'))
    service = StudioPricingService(transport)
    async def run():
        await service.get(IMAGE, 'image')
        clock[0] = 399.9
        await service.get(IMAGE, 'image')
        assert len(transport.calls) == 1
        clock[0] = 400.0
        await service.get(IMAGE, 'image')
        assert len(transport.calls) == 2
        for number in range(101):
            model = f'example/model-{number}'
            transport.payload = {'id': model, 'endpoints': [{'provider_name': 'Example', 'pricing': [{'billable': 'output_image', 'unit': 'image', 'cost_usd': '0.03'}]}]}
            await service.get(model, 'image')
        assert len(service._cache) == 100
        assert (IMAGE, 'image') not in service._cache
    asyncio.run(run())


def test_response_size_nonfinite_json_and_large_lists_are_bounded(workspace):
    async def oversized(*args, **kwargs):
        return b'x' * (1024 * 1024 + 1), {}
    workspace.transport._request = oversized
    assert get(workspace).json()['status'] == 'unavailable'
    async def nonfinite(*args, **kwargs):
        return b'{"id":"google/gemini-3-pro-image","endpoints":[],"x":NaN}', {}
    workspace.transport._request = nonfinite
    workspace.service._cache.clear()
    assert get(workspace).json()['status'] == 'unavailable'
    workspace.transport._request = Transport({'data': [{'id': 'example/model'}] * 101})._request
    assert get(workspace, VIDEO).json()['status'] == 'unavailable'


def test_failed_metadata_can_be_retried_without_waiting_for_cache(workspace):
    workspace.transport.error = RuntimeError('temporary failure')
    assert get(workspace).json()['status'] == 'unavailable'
    workspace.transport.error = None
    assert get(workspace).json()['status'] == 'available'
    assert len(workspace.transport.calls) == 2


def test_slow_model_does_not_block_another_model_and_same_model_shares_io():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        class SlowTransport(Transport):
            async def _request(self, method, path, **options):
                if IMAGE in path:
                    started.set()
                    await release.wait()
                return await super()._request(method, path, **options)
        transport = SlowTransport()
        service = StudioPricingService(transport)
        first = asyncio.create_task(service.get(IMAGE, 'image'))
        await started.wait()
        shared = asyncio.create_task(service.get(IMAGE, 'image'))
        other = await asyncio.wait_for(service.get('krea/k3-edit', 'image'), 0.5)
        assert other['status'] == 'available'
        assert not first.done() and not shared.done()
        release.set()
        a, b = await asyncio.gather(first, shared)
        assert a == b and a['status'] == 'available'
        assert len(transport.calls) == 2
    asyncio.run(run())


def test_total_network_budget_is_bounded_and_cancelled_callers_do_not_cancel_shared_read(monkeypatch):
    async def run():
        started = asyncio.Event()
        class HangingTransport(Transport):
            async def _request(self, *args, **kwargs):
                started.set()
                await asyncio.Event().wait()
        service = StudioPricingService(HangingTransport())
        first = asyncio.create_task(service.get(IMAGE, 'image'))
        await started.wait()
        shared = asyncio.create_task(service.get(IMAGE, 'image'))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        guide = await asyncio.wait_for(shared, 0.5)
        assert guide['status'] == 'unavailable'
        assert not service._inflight and not service._cache
    monkeypatch.setattr(studio_pricing, '_TIMEOUT', 0.05)
    asyncio.run(run())
