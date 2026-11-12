"""Mocked paid-effect, persistence and resource-boundary coverage for Studio."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from termx.media.http import mount_media
from termx.media.service import ArtifactService
from termx.media.studio import SNAPSHOT, StudioInput, StudioService, account_adapter, public_https_url


def state_for(providers=None, key='fixture-secret'):
    providers = providers if providers is not None else [{'id': 'kie', 'name': 'My Kie', 'kind': 'openai-compatible',
        'base_url': 'https://api.kie.ai/api/v1', 'capabilities': ['image', 'video']}]
    return SimpleNamespace(agent_store=SimpleNamespace(get_provider=lambda identifier: next((p for p in providers if p['id'] == identifier), None),
        list_providers=lambda: providers), credentials=SimpleNamespace(get=lambda _: key))


def setup_service(tmp_path, handler, state=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    artifacts = ArtifactService(tmp_path / 'artifacts')
    service = StudioService(state or state_for(), tmp_path / 'studio', artifacts, client=client)
    service._catalog = SNAPSHOT['openrouter']
    service._catalog_at = __import__('time').time()
    service._music_catalog_at = __import__('time').time()
    return service, client


def request(**changes):
    return {**StudioInput(request_id='unique-request', provider_id='kie', model='nano-banana-pro',
        prompt='A painted orange fox', acknowledge_billing=True).model_dump(), **changes}


async def drain(service):
    while service.tasks:
        await asyncio.gather(*service.tasks)


@pytest.mark.parametrize('url,expected', [
    ('https://api.kie.ai', 'kie'), ('https://api.kie.ai/v1/', 'kie'),
    ('https://openrouter.ai/api/v1', 'openrouter'), ('https://openrouter.ai:443/v1', 'openrouter'),
    ('http://api.kie.ai/api/v1', None), ('https://api.kie.ai.evil.example', None),
    ('https://api.kie.ai/api/v1/jobs', None), ('https://api.kie.ai:8443', None),
    ('https://user:pass@api.kie.ai', None), ('https://api.kie.ai?api_key=secret', None),
])
def test_exact_account_dispatch_without_secret_redirection(url, expected):
    assert account_adapter({'kind': 'openai-compatible', 'base_url': url}) == expected
    assert account_adapter({'kind': 'openai', 'base_url': url}) is None


@pytest.mark.parametrize('url', ['http://media.example/file.png', 'https://127.0.0.1/a', 'https://192.168.1.1/a',
    'https://[::1]/a', 'https://localhost/a', 'https://server.local/a', 'https://user:pass@public.example/a',
    'https://2130706433/a', 'data:image/png;base64,AA', 'https://public.example/a#fragment', 'https://public.example/a\nb'])
def test_private_and_unsafe_reference_links_rejected(url):
    assert not public_https_url(url)


def test_input_acknowledgement_is_explicit_and_bounds_are_strict():
    with pytest.raises(ValidationError): StudioInput.model_validate(request(acknowledge_billing='true'))
    with pytest.raises(ValidationError): StudioInput.model_validate(request(duration=True))
    with pytest.raises(ValidationError): StudioInput.model_validate(request(unexpected='secret'))


def test_persisted_receipts_idempotency_and_owner_project_partition(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        assert str(req.url) == 'https://api.kie.ai/api/v1/jobs/createTask'
        assert req.headers['authorization'] == 'Bearer fixture-secret'
        assert json.loads(req.content) == {'model': 'nano-banana-pro', 'input': {'prompt': 'A painted orange fox', 'output_format': 'png'}}
        return httpx.Response(200, json={'code': 200, 'data': {'taskId': 'remote-123'}})
    service, client = setup_service(tmp_path, handler)
    async def run():
        first, duplicate = await asyncio.gather(service.create('alice', None, request()), service.create('alice', None, request()))
        assert first['id'] == duplicate['id'] and first['status'] == 'submitting'
        await drain(service)
        queued = service.get('alice', first['id'])
        assert queued['status'] == 'queued' and queued['outputs'] == []
        assert await service.create('alice', None, request()) == queued
        with pytest.raises(HTTPException, match='different arguments'):
            await service.create('alice', 'project', request(project_id='project'))
        with pytest.raises(HTTPException): service.get('bob', first['id'])
        assert service.list('bob', None) == [] and service.list('alice', 'project') == []
        assert len(service.list('alice', None)) == 1
        restarted = StudioService(service.state, service.path.parent, service.artifacts, client=client)
        assert await restarted.create('alice', None, request()) == queued
        assert set(queued) == {'id','project_id','provider_id','model','kind','prompt','status','created','updated','outputs','parameters'}
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 1
    raw = service.path.read_bytes()
    assert b'fixture-secret' not in raw


@pytest.mark.parametrize('response,status', [
    (httpx.Response(200, json={'code': 505, 'msg': 'fixture-secret'}), 'failed'),
    (httpx.Response(401, text='fixture-secret'), 'failed'),
    (httpx.Response(500, text='fixture-secret'), 'unknown'),
    (httpx.Response(408, text='fixture-secret'), 'unknown'),
    (httpx.Response(499, text='fixture-secret'), 'unknown'),
    (httpx.Response(200, text='not json fixture-secret'), 'unknown'),
    (httpx.Response(200, json={'code': 200, 'data': {}}), 'unknown'),
    (httpx.Response(200, json={'code': True, 'data': {'taskId':'bad-boolean'}}), 'unknown'),
    (httpx.Response(200, json={'code': 200.0, 'data': {'taskId':'bad-float'}}), 'unknown'),
    (httpx.Response(200, json=['fixture-secret']), 'unknown'),
])
def test_refused_or_uncertain_submission_never_replayed(tmp_path, response, status):
    calls = []
    def handler(req):
        calls.append(req)
        return response
    service, client = setup_service(tmp_path, handler)
    async def run():
        job = await service.create('alice', None, request())
        await drain(service)
        final = service.get('alice', job['id'])
        assert final['status'] == status and 'fixture-secret' not in final.get('error', '')
        assert await service.create('alice', None, request()) == final
        assert await service.refresh('alice', job['id']) == final
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_timeout_restart_cancel_and_concurrent_duplicate_never_bill_twice(tmp_path):
    calls = []
    entered = asyncio.Event()
    hold = asyncio.Event()
    async def handler(req):
        calls.append(req)
        entered.set()
        await hold.wait()
        raise httpx.ReadTimeout('fixture-secret', request=req)
    service, client = setup_service(tmp_path, handler)
    async def run():
        job = await service.create('alice', None, request())
        await entered.wait()
        assert (await service.create('alice', None, request()))['status'] == 'submitting'
        for task in list(service.tasks): task.cancel()
        await asyncio.gather(*service.tasks, return_exceptions=True)
        assert service.get('alice', job['id'])['status'] == 'unknown'
        restarted = StudioService(service.state, service.path.parent, service.artifacts, client=client)
        assert (await restarted.create('alice', None, request()))['status'] == 'unknown'
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize('changes,key,match', [
    ({'acknowledge_billing': False}, 'fixture', 'Confirm paid usage'),
    ({'provider_id': 'missing'}, 'fixture', 'stored compatible'),
    ({'model': 'not-supported'}, 'fixture', 'not supported'),
    ({'resolution': 'EXTRA'}, 'fixture', 'resolution'),
    ({'image_url': 'https://127.0.0.1/a'}, 'fixture', 'public HTTPS'),
    ({}, None, 'Connect this API account'),
])
def test_validation_precedes_effect_reservation(tmp_path, changes, key, match):
    def handler(req): raise AssertionError('Invalid submission must not call a provider')
    service, client = setup_service(tmp_path, handler, state_for(key=key))
    async def run():
        with pytest.raises(HTTPException, match=match): await service.create('alice', None, request(**changes))
        assert service.list('alice', None) == []
        await client.aclose()
    asyncio.run(run())


def test_model_kind_capabilities_and_native_payloads(tmp_path):
    providers = [{'id':'kie','kind':'openai-compatible','base_url':'https://api.kie.ai','capabilities':['image']}]
    service, client = setup_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()), state_for(providers))
    async def run():
        with pytest.raises(HTTPException, match='video support'):
            await service.create('alice', None, request(model='wan/2-5-text-to-video'))
        catalog = await service.catalog()
        assert catalog['accounts'] == [{'id':'kie','name':'kie','configured':True,'adapter':'kie','capabilities':['image']}]
        assert all('_supports_image' not in row for row in catalog['models'])
        await client.aclose()
    asyncio.run(run())
    video = next(m for m in SNAPSHOT['kie'] if m['id'] == 'wan/2-5-text-to-video')
    assert service.kie_payload(video, request(model=video['id'], duration=5))['input']['duration'] == '5'
    talk = next(m for m in SNAPSHOT['kie'] if m['id'] == 'omnihuman-1-5')
    body = request(model=talk['id'], prompt='', image_url='https://public.example/portrait.png', audio_url='https://public.example/audio.mp3', resolution='720')
    service.validate(talk, body)
    assert service.kie_payload(talk, body)['input'] == {'image_url':body['image_url'], 'audio_url':body['audio_url'], 'output_resolution':'720'}


def test_refresh_persists_safe_outputs_and_never_fetches_provider_urls(tmp_path):
    calls, states = [], ['generating', 'success']
    def handler(req):
        calls.append(req)
        if req.method == 'POST': return httpx.Response(200, json={'code':200, 'data':{'taskId':'receipt'}})
        assert req.url.params['taskId'] == 'receipt'
        return httpx.Response(200, json={'code':200, 'data':{'state':states.pop(0), 'resultJson':json.dumps({'resultUrls':['https://cdn.example/output.png']})}})
    service, client = setup_service(tmp_path, handler)
    async def run():
        job = await service.create('alice', None, request())
        await drain(service)
        await service.refresh('alice', job['id']); await drain(service)
        assert service.get('alice', job['id'])['status'] == 'running'
        await service.refresh('alice', job['id']); await drain(service)
        final = service.get('alice', job['id'])
        assert final['status'] == 'completed' and final['outputs'] == ['https://cdn.example/output.png']
        assert await service.refresh('alice', job['id']) == final
        await client.aclose()
    asyncio.run(run())
    assert len(calls) == 3 and all(req.url.host == 'api.kie.ai' for req in calls)


def test_unsafe_output_and_temporary_status_errors_remain_reconcilable(tmp_path):
    sequence = [httpx.Response(200,json={'code':200,'data':{'taskId':'receipt'}}),
        httpx.Response(503,text='fixture-secret'),
        httpx.Response(200,json={'code':200,'data':{'state':'success','resultJson':json.dumps({'resultUrls':['javascript:alert(1)']})}}),
        httpx.Response(200,json={'code':200,'data':{'state':'fail','failMsg':'fixture-secret'}})]
    service, client = setup_service(tmp_path, lambda _: sequence.pop(0))
    async def run():
        job = await service.create('alice', None, request()); await drain(service)
        for _ in range(2):
            await service.refresh('alice', job['id']); await drain(service)
            current = service.get('alice', job['id'])
            assert current['status'] == 'queued' and current['outputs'] == [] and 'fixture-secret' not in current['error']
        await service.refresh('alice', job['id']); await drain(service)
        assert service.get('alice', job['id'])['status'] == 'failed'
        await client.aclose()
    asyncio.run(run())


def test_routes_require_identity_owner_and_live_project_authority(tmp_path, monkeypatch):
    state = state_for()
    state.delivery = SimpleNamespace(directory=tmp_path/'delivery')
    state.identity = SimpleNamespace(resolve=lambda token: SimpleNamespace(principal=SimpleNamespace(id=token)) if token in {'alice','bob'} else None)
    allowed = {'project-a'}
    decisions = []
    def authorize(token, scope, project_id=None):
        decisions.append((token,scope,project_id))
        if project_id not in allowed: raise HTTPException(403, 'project denied')
    state.authorization = SimpleNamespace(require=authorize)
    app = FastAPI(); mount_media(app, state)
    async def discover(): return SNAPSHOT['openrouter']
    monkeypatch.setattr(state.studio.openrouter, 'discover', discover)
    # Pre-existing owned jobs make the HTTP checks independent of any provider.
    with state.studio.db() as db:
        db.execute("INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,created,updated) VALUES('owned','alice','project-a','request','digest','kie','kie','nano-banana-pro','image','hello','unknown',1,1)")
        db.execute("INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,created,updated) VALUES('other','alice','project-b','request-b','digest','kie','kie','nano-banana-pro','image','private','unknown',1,1)")
    with TestClient(app) as client:
        assert client.get('/api/media/studio/catalog?project_id=project-a').status_code == 401
        alice, bob = {'Authorization':'Bearer alice'}, {'Authorization':'Bearer bob'}
        assert client.get('/api/media/studio/catalog?project_id=project-a', headers=alice).status_code == 200
        jobs = client.get('/api/media/studio/jobs?project_id=project-a',headers=alice)
        assert jobs.status_code == 200 and [j['id'] for j in jobs.json()] == ['owned']
        assert client.get('/api/media/studio/jobs?project_id=project-b',headers=alice).status_code == 403
        assert client.post('/api/media/studio/jobs/owned/refresh',headers=bob).status_code == 404
        assert client.post('/api/media/studio/jobs/other/refresh',headers=alice).status_code == 403
        assert client.post('/api/media/studio/jobs/owned/refresh',headers=alice).status_code == 200
        allowed.clear()
        assert client.post('/api/media/studio/jobs/owned/refresh',headers=alice).status_code == 403
        assert client.post('/api/media/studio/jobs',headers=alice,json=request(project_id='project-a')).status_code == 403
    assert ('alice','agent-run','project-a') in decisions


def test_catalog_live_discovery_is_cached_and_snapshot_is_explicit(tmp_path):
    from termx.media.studio_openrouter import OpenRouterError
    service, client = setup_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError('No provider generation')))
    calls = []
    live = {**SNAPSHOT['openrouter'][0], 'id':'vendor/current-image', 'label':'Current image'}
    async def discover():
        calls.append('read-only-discovery')
        return [live]
    service.openrouter.discover = discover
    service._catalog_at = 0
    async def run():
        catalog = await service.catalog()
        assert catalog['catalog_source'] == 'live'
        assert 'vendor/current-image' in [m['id'] for m in catalog['models']]
        assert 'google/veo-3.1' not in [m['id'] for m in catalog['models']]
        assert 'fixture-secret' not in json.dumps(catalog)
        await service.catalog()
        assert len(calls) == 1
        async def unavailable(): raise OpenRouterError('do not leak provider body')
        service.openrouter.discover = unavailable
        service._catalog_at = 0
        fallback = await service.catalog()
        assert fallback['catalog_source'] == 'snapshot'
        assert 'do not leak' not in fallback['catalog_error']
        assert 'heygen/avatar-iv' not in [m['id'] for m in fallback['models']]
        await client.aclose()
    asyncio.run(run())


def test_bounded_provider_status_closes_stream_and_preserves_unknown(tmp_path):
    class Oversized(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'a' * 700000
            yield b'b' * 400000
            raise AssertionError('Stop at the response bound')
        async def aclose(self): self.closed = True
    stream = Oversized()
    service, client = setup_service(tmp_path, lambda _: httpx.Response(200, stream=stream))
    async def run():
        job = await service.create('alice', None, request())
        await drain(service)
        assert stream.closed and service.get('alice', job['id'])['status'] == 'unknown'
        assert (await service.create('alice', None, request()))['id'] == job['id']
        await client.aclose()
    asyncio.run(run())


def seed_history(service, count=155, owner='alice', project='project-a', prefix='job', start=0):
    with service.db() as db:
        for index in range(start, start + count):
            db.execute('''INSERT INTO studio_jobs(id,owner,project,request_id,digest,provider_id,adapter,model,kind,prompt,status,created,updated)
                VALUES(?,?,?,?,?,'kie','kie','nano-banana-pro',?,?,?,?,?)''',
                (f'{prefix}-{index:04d}', owner, project, f'{prefix}-request-{index}', 'digest',
                 'image' if index % 2 == 0 else 'video', 'Literal %_needle' if index == start else f'Painted fox {index}',
                 'completed' if index % 3 == 0 else 'queued', 1000 + index // 5, 2000))


def test_history_more_than_100_ties_and_insertions_have_no_gaps_or_duplicates(tmp_path):
    service, client = setup_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()))
    seed_history(service)
    seed_history(service, 12, owner='bob', prefix='bob')
    seed_history(service, 13, project='project-b', prefix='other')
    seed_history(service, 14, project=None, prefix='personal')
    expected = [f'job-{i:04d}' for i in range(154, -1, -1)]
    first = service.history('alice', 'project-a', limit=17)
    assert len(service.list('alice', 'project-a')) == 100
    assert [j['id'] for j in first['items']] == expected[:17]
    # Inserts at the head, at a timestamp tie and below the current cursor all
    # belong to a new history view, regardless of how their timestamp sorts.
    seed_history(service, 1, prefix='new-head', start=1000)
    seed_history(service, 1, prefix='new-old', start=0)
    seed_history(service, 1, prefix='new-tied', start=135)
    seen = [j['id'] for j in first['items']]
    cursor = first['next_cursor']
    while cursor:
        page = service.history('alice', 'project-a', limit=17, cursor=cursor)
        seen.extend(j['id'] for j in page['items'])
        cursor = page['next_cursor']
    assert seen == expected and len(set(seen)) == 155
    assert len(service.history('alice', None)['items']) == 14
    assert service.history('nobody', 'project-a') == {'items': [], 'next_cursor': None}
    asyncio.run(client.aclose())


def test_history_filters_search_old_rows_and_cursor_scope(tmp_path):
    service, client = setup_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()))
    seed_history(service, 180)
    seed_history(service, 2, owner='bob', prefix='bob')
    seed_history(service, 2, project='project-b', prefix='other')
    page = service.history('alice', 'project-a', limit=9, kind='image', status='completed', q='fox')
    assert len(page['items']) == 9 and page['next_cursor']
    assert all(j['kind'] == 'image' and j['status'] == 'completed' and 'fox' in j['prompt'] for j in page['items'])
    # A filter applies over all stored rows, including records older than the
    # compatibility polling endpoint's 100-job window. % and _ stay literal.
    literal = service.history('alice', 'project-a', q='%_needle')
    assert [j['id'] for j in literal['items']] == ['job-0000']
    assert service.history('alice', 'project-a', q="' OR 1=1 --")['items'] == []
    for owner, project, changes in [('bob', 'project-a', {}), ('alice','project-b', {}),
            ('alice','project-a', {'kind':'video'}), ('alice','project-a', {'status':'queued'}),
            ('alice','project-a', {'q':'different'})]:
        with pytest.raises(HTTPException, match='different history view'):
            service.history(owner, project, limit=9, cursor=page['next_cursor'], **{'kind':'image','status':'completed','q':'fox',**changes})
    # Provider status is mutable; it must not invalidate the ordering anchor.
    anchor = page['items'][-1]['id']
    with service.db() as db: db.execute("UPDATE studio_jobs SET status='failed' WHERE id=?", (anchor,))
    assert service.history('alice', 'project-a', limit=9, cursor=page['next_cursor'], kind='image', status='completed', q='fox')['items']
    asyncio.run(client.aclose())


@pytest.mark.parametrize('changes', [{'limit':0}, {'limit':101}, {'limit':True}, {'kind':'document'}, {'status':'unexpected'},
    {'q':'x'*201}, {'q':42}, {'q':'line\nbreak'}, {'cursor':''}, {'cursor':'?'}, {'cursor':'a'*1025}, {'cursor':'bm90LWpzb24'}])
def test_history_bounds_reject_before_query_publication(tmp_path, changes):
    service, client = setup_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()))
    with pytest.raises(HTTPException): service.history('alice', 'project-a', **changes)
    asyncio.run(client.aclose())


def test_history_cursor_anchor_is_reauthorized_and_schema_is_strict(tmp_path):
    import base64
    service, client = setup_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError()))
    seed_history(service, 5)
    seed_history(service, 1, owner='bob', prefix='bob')
    page = service.history('alice', 'project-a', limit=2)
    def unpack(value): return json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
    def pack(value): return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    original = unpack(page['next_cursor'])
    for update in [{'v':2}, {'v':True}, {'a':'bob-0000'}, {'a':'absent'}, {'w':True}, {'w':2**63}, {'extra':'field'}]:
        with pytest.raises(HTTPException): service.history('alice', 'project-a', cursor=pack({**original,**update}))
    asyncio.run(client.aclose())


def test_history_http_identity_project_revocation_and_input_bounds(tmp_path):
    state = state_for()
    state.delivery = SimpleNamespace(directory=tmp_path/'delivery')
    state.identity = SimpleNamespace(resolve=lambda token: SimpleNamespace(principal=SimpleNamespace(id=token)) if token in {'alice','bob'} else None)
    allowed = {'project-a'}
    def authorize(token, scope, project_id=None):
        if project_id not in allowed: raise HTTPException(403, 'project denied')
    state.authorization = SimpleNamespace(require=authorize)
    app = FastAPI(); mount_media(app, state)
    seed_history(state.studio, 120)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer alice'}
        assert client.get('/api/media/studio/history?project_id=project-a').status_code == 401
        first = client.get('/api/media/studio/history?project_id=project-a&limit=25', headers=headers)
        assert first.status_code == 200 and len(first.json()['items']) == 25 and first.json()['next_cursor']
        active=client.get('/api/media/studio/history?project_id=project-a&status=active',headers=headers)
        assert active.status_code==200 and all(j['status']=='queued' for j in active.json()['items'])
        for query in ['limit=101','q='+'x'*201,'kind=document','status=unexpected','cursor='+'a'*1025]:
            assert client.get('/api/media/studio/history?project_id=project-a&'+query,headers=headers).status_code == 422
        cursor=first.json()['next_cursor']
        assert client.get('/api/media/studio/history',params={'project_id':'project-a','cursor':cursor},headers={'Authorization':'Bearer bob'}).status_code == 400
        allowed.clear()
        assert client.get('/api/media/studio/history',params={'project_id':'project-a','cursor':cursor},headers=headers).status_code == 403


def image_bytes(color='red'):
    from PIL import Image
    import io
    output=io.BytesIO(); Image.new('RGB',(12,12),color).save(output,format='PNG'); return output.getvalue()


def openrouter_reference_service(tmp_path):
    provider={'id':'router','name':'OpenRouter','kind':'openai-compatible','base_url':'https://openrouter.ai/api/v1','capabilities':['image','video']}
    service, client = setup_service(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError('No external provider calls')), state_for([provider]))
    spec={**SNAPSHOT['openrouter'][0], '_inline_inputs':['image']}
    service._catalog=[spec]
    captured=[]
    async def submit(model, body, key, owner, project):
        captured.append((model,body,owner,project))
        return {'status':'queued','task_id':'mock-receipt'}
    service.openrouter.submit=submit
    return service,client,spec,captured


def test_owned_image_reference_is_validated_pinned_and_never_persisted_inline(tmp_path):
    import base64
    service,client,spec,captured=openrouter_reference_service(tmp_path)
    source=service.artifacts.create('alice','project-a','input','Portrait',blob=image_bytes(),mime='image/png')
    args=request(provider_id='router',model=spec['id'],project_id='project-a',image_id=source['id'],operation='image',resolution='1K')
    async def run():
        job=await service.create('alice','project-a',args)
        service.artifacts.revise('alice',source['id'],1,blob=image_bytes('blue'),mime='image/png')
        await drain(service)
        assert len(captured)==1
        local=captured[0][1]['_local_references']['image']
        assert local['mime']=='image/png' and base64.b64decode(local['data_url'].split(',')[1])==image_bytes()
        final=service.get('alice',job['id'])
        assert final['parameters']=={'operation':'image','resolution':'1K','image_id':source['id'],'has_image_reference':True}
        assert await service.create('alice','project-a',args)==final
        history=service.history('alice','project-a')
        assert 'data:image' not in json.dumps(history) and 'fixture-secret' not in json.dumps(history)
        assert b'data:image' not in service.path.read_bytes()
        await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('case,code', [('owner',404),('project',403),('mime',415),('not-binary',400),('invalid',400),('truncated-jpeg',400),('oversize',413),('inline-limit',413),('unsupported-adapter',400)])
def test_owned_reference_rejections_precede_credentials_and_reservation(tmp_path,monkeypatch,case,code):
    import termx.media.studio as module
    service,client,spec,captured=openrouter_reference_service(tmp_path)
    owner='bob' if case=='owner' else 'alice'
    project='other' if case=='project' else 'project-a'
    if case=='not-binary':
        item=service.artifacts.create(owner,project,'document','Not binary','Hello')
        # Correct declared MIME does not turn a text artifact into binary.
        with service.artifacts.db() as db: db.execute("UPDATE versions SET mime='image/png' WHERE artifact=?",(item['id'],))
    else:
        raw=b'bad image' if case=='invalid' else b'x'*(module.INLINE_IMAGE_LIMIT+1) if case=='oversize' else image_bytes()
        mime='audio/mpeg' if case=='mime' else 'image/png'
        if case=='truncated-jpeg':
            from PIL import Image
            import io
            stream=io.BytesIO();Image.new('RGB',(100,100),'red').save(stream,format='JPEG')
            raw=stream.getvalue()[:-2];mime='image/jpeg'
        item=service.artifacts.create(owner,project,'input','Reference',blob=raw,mime=mime)
    if case=='inline-limit': monkeypatch.setattr(module,'INLINE_TOTAL_LIMIT',10)
    if case=='unsupported-adapter': spec['_inline_inputs']=[]
    def no_key(_): raise AssertionError('Invalid references must be rejected before reading account credentials')
    service.state.credentials.get=no_key
    # catalog() also computes configured state; retain its cached model metadata
    # while avoiding the unrelated account-picker read during this boundary test.
    async def catalog(): return {'models':[spec],'accounts':[]}
    service.catalog=catalog
    async def run():
        with pytest.raises(HTTPException) as error:
            await service.create('alice','project-a',request(provider_id='router',model=spec['id'],project_id='project-a',image_id=item['id']))
        assert error.value.status_code==code and service.list('alice','project-a')==[] and captured==[]
        await client.aclose()
    asyncio.run(run())


def test_reference_exclusivity_internal_fields_and_url_only_audio_video(tmp_path):
    with pytest.raises(ValidationError): StudioInput.model_validate(request(image_id='image',image_url='https://cdn.example/p.png'))
    with pytest.raises(ValidationError): StudioInput.model_validate(request(_local_references={'image':{'data_url':'data:secret'}}))
    service,client,spec,captured=openrouter_reference_service(tmp_path)
    spec.update(kind='video',modes=['lipsync'],requires_image=True,requires_audio=True)
    image=service.artifacts.create('alice',None,'input','Image',blob=image_bytes(),mime='image/png')
    audio=service.artifacts.create('alice',None,'input','Audio',blob=b'ID3fixture',mime='audio/mpeg')
    async def run():
        with pytest.raises(HTTPException,match='public HTTPS audio URL'):
            await service.create('alice',None,request(provider_id='router',model=spec['id'],prompt='',image_id=image['id'],audio_id=audio['id']))
        assert service.list('alice',None)==[] and not captured
        await client.aclose()
    asyncio.run(run())


def test_saved_parameters_remove_signed_queries_and_legacy_schema_migrates(tmp_path):
    import hashlib,sqlite3
    service,client,spec,captured=openrouter_reference_service(tmp_path)
    args=request(provider_id='router',model=spec['id'],image_url='https://cdn.example/input.png?token=private',aspect_ratio='1:1')
    async def run():
        job=await service.create('alice',None,args); await drain(service)
        stored=service.get('alice',job['id'])
        assert stored['parameters']=={'aspect_ratio':'1:1','has_image_reference':True}
        assert 'private' not in json.dumps(service.history('alice',None))
        await client.aclose()
    asyncio.run(run())
    legacy=tmp_path/'legacy';legacy.mkdir()
    db=sqlite3.connect(legacy/'studio.sqlite3')
    db.execute('''CREATE TABLE studio_jobs(id TEXT PRIMARY KEY,owner TEXT,project TEXT,request_id TEXT,digest TEXT,provider_id TEXT,adapter TEXT,model TEXT,kind TEXT,prompt TEXT,status TEXT,task_id TEXT,created REAL,updated REAL,outputs TEXT,error TEXT,UNIQUE(owner,request_id))''')
    old={k:v for k,v in request().items() if k not in {'operation','image_role','negative_prompt','image_id','audio_id','video_id','image_references'}}
    digest=hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()
    db.execute("INSERT INTO studio_jobs VALUES('legacy','alice',NULL,'unique-request',?,'kie','kie','nano-banana-pro','image','A painted orange fox','queued','receipt',1,1,'[]',NULL)",(digest,))
    db.commit();db.close()
    migrated=StudioService(state_for(),legacy,service.artifacts)
    async def check():
        restored=await migrated.create('alice',None,request())
        assert restored['id']=='legacy' and restored['parameters']=={}
        assert migrated.history('alice',None)['items'][0]['id']=='legacy'
    asyncio.run(check())


def test_history_active_and_attention_groups_page_over_all_owned_history(tmp_path):
    service,client=setup_service(tmp_path,lambda _: (_ for _ in ()).throw(AssertionError()))
    seed_history(service,180)
    with service.db() as db:
        for identifier,status in [('job-0000','failed'),('job-0001','unknown'),('job-0002','submitting'),('job-0003','running')]:
            db.execute('UPDATE studio_jobs SET status=? WHERE id=?',(status,identifier))
    active=service.history('alice','project-a',status='active',limit=13)
    all_active=list(active['items'])
    cursor=active['next_cursor']
    while cursor:
        page=service.history('alice','project-a',status='active',limit=13,cursor=cursor)
        all_active.extend(page['items']);cursor=page['next_cursor']
    assert len(all_active)==120 and len({j['id'] for j in all_active})==120
    assert {j['status'] for j in all_active}=={'submitting','queued','running'}
    assert {j['id'] for j in service.history('alice','project-a',status='attention')['items']}=={'job-0000','job-0001'}
    with pytest.raises(HTTPException):service.history('alice','project-a',status='queued',cursor=active['next_cursor'])
    asyncio.run(client.aclose())


def test_upload_to_owned_inline_generation_to_history_and_authenticated_export(tmp_path):
    import base64
    from time import time
    provider={'id':'router','name':'OpenRouter','kind':'openai-compatible','base_url':'https://openrouter.ai/api/v1','capabilities':['image','video']}
    state=state_for([provider])
    state.delivery=SimpleNamespace(directory=tmp_path/'delivery')
    state.identity=SimpleNamespace(resolve=lambda token:SimpleNamespace(principal=SimpleNamespace(id=token)) if token in {'alice','bob'} else None)
    def authorize(token,scope,project_id=None):
        if project_id!='project-a':raise HTTPException(403,'project denied')
    state.authorization=SimpleNamespace(require=authorize)
    app=FastAPI();mount_media(app,state)
    spec=SNAPSHOT['openrouter'][0]
    state.studio._catalog=[spec];state.studio._catalog_at=time()
    source=image_bytes();output=image_bytes('green');calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='GET':return image_endpoint_response(req)
        assert req.url.host=='openrouter.ai' and req.url.path=='/api/v1/images' and req.method=='POST'
        assert req.headers['authorization']=='Bearer fixture-secret'
        payload=json.loads(req.content)
        inline=payload['input_references'][0]['image_url']['url']
        assert base64.b64decode(inline.split(',')[1])==source
        assert 'image_id' not in payload and '_local_references' not in payload
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(output).decode(),'media_type':'image/png'}]})
    provider_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    state.studio.openrouter.client=provider_client
    with TestClient(app) as client:
        headers={'Authorization':'Bearer alice'}
        uploaded=client.post('/api/media/inputs',params={'project_id':'project-a','name':'Portrait.png'},headers={**headers,'Content-Type':'image/png'},content=source)
        assert uploaded.status_code==200
        args=request(provider_id='router',model=spec['id'],project_id='project-a',image_id=uploaded.json()['id'],operation='image')
        started=client.post('/api/media/studio/jobs',headers=headers,json=args)
        assert started.status_code==200
        client.portal.call(drain,state.studio)
        history=client.get('/api/media/studio/history?project_id=project-a',headers=headers).json()
        job=history['items'][0]
        assert job['status']=='completed' and job['parameters']['image_id']==uploaded.json()['id']
        assert 'data:image' not in json.dumps(history) and 'fixture-secret' not in json.dumps(history)
        exported=client.get(job['outputs'][0],headers=headers)
        assert exported.status_code==200 and exported.content==output
        assert client.get(job['outputs'][0],headers={'Authorization':'Bearer bob'}).status_code==404
        assert client.post('/api/media/studio/jobs',headers={'Authorization':'Bearer bob'},json=args).status_code==404
        client.portal.call(provider_client.aclose)
    assert len(calls)==2 and sum(req.method=='POST' for req in calls)==1


def test_advanced_capabilities_require_declared_source_roles_before_effect():
    spec={'id':'vendor/verified-video','kind':'video','modes':['video','remix'],'aspect_ratios':[], 'resolutions':[], 'durations':[],
          '_supports_image':True,'_supports_audio':True,'_supports_video':True,'_supports_first_frame':True,
          '_supports_reference_image':True,'_requires_video_for_remix':True,'_supports_negative_prompt':True,'_inline_inputs':['image']}
    caps=StudioService.public_capabilities(spec)
    assert all(caps[field] is True for field in ['supports_audio','supports_video','requires_video_for_remix','supports_negative_prompt'])
    with pytest.raises(HTTPException,match='source video'):
        StudioService.validate(spec,request(operation='remix'))
    with pytest.raises(HTTPException,match='First-frame'):
        StudioService.validate(spec,request(operation='video',image_role='first_frame',image_url='https://cdn.example/image.png',audio_url='https://cdn.example/voice.mp3'))
    StudioService.validate(spec,request(operation='remix',video_url='https://cdn.example/source.mp4',image_role='reference',image_url='https://cdn.example/image.png',negative_prompt='fog'))


def image_endpoint_response(req):
    assert req.method=='GET' and req.url.host=='openrouter.ai'
    assert req.url.path.startswith('/api/v1/images/models/') and req.url.path.endswith('/endpoints')
    assert 'authorization' not in req.headers and 'cookie' not in req.headers
    identifier=req.url.path.removeprefix('/api/v1/images/models/').removesuffix('/endpoints')
    return httpx.Response(200,json={'id':identifier,'endpoints':[{'provider_tag':'verified-image-fixture',
        'supported_parameters':{'input_references':{'type':'range','min':0,'max':5},
            'n':{'type':'range','min':1,'max':1},'output_format':{'type':'enum','values':['png']},
            'resolution':{'type':'enum','values':['1K','2K','4K']},'aspect_ratio':{'type':'enum','values':['1:1','16:9']}}}]})


def composition_service(tmp_path):
    service,client,spec,captured=openrouter_reference_service(tmp_path)
    spec['image_reference_limits']={'min':0,'max':14,'app_max':5}
    return service,client,spec,captured


@pytest.mark.parametrize('references,legacy', [([],{}),([{'image_url':'https://cdn.example/a.png'}]*6,{}),([{}],{}),
    ([{'image_id':'owned','image_url':'https://cdn.example/a.png'}],{}),
    ([{'image_url':'https://cdn.example/a.png','unexpected':'field'}],{}),
    ([{'data_url':'data:image/png;base64,AAAA'}],{}),
    ([{'image_id':'owned'}],{'image_id':'legacy'}),([{'image_id':'owned'}],{'image_url':'https://cdn.example/a.png'})])
def test_composition_request_schema_is_strict_and_exclusive(references,legacy):
    with pytest.raises(ValidationError):StudioInput.model_validate(request(image_references=references,**legacy))


def test_composition_mixed_sources_order_parameters_and_replay_are_durable(tmp_path):
    import base64
    service,client,spec,captured=composition_service(tmp_path)
    first=service.artifacts.create('alice','project-a','input','First',blob=image_bytes(),mime='image/png')
    last=service.artifacts.create('alice','project-a','input','Last',blob=image_bytes('blue'),mime='image/png')
    refs=[{'image_id':first['id']},{'image_url':'https://cdn.example/public.png'},
          {'image_url':'https://cdn.example/signed.png?token=private-signature'},{'image_id':last['id']}]
    args=request(provider_id='router',model=spec['id'],project_id='project-a',image_references=refs,operation='image')
    async def run():
        job=await service.create('alice','project-a',args)
        service.artifacts.revise('alice',first['id'],1,blob=image_bytes('green'),mime='image/png')
        await drain(service)
        assert len(captured)==1
        resolved=captured[0][1]['_local_image_references']
        assert base64.b64decode(resolved[0]['data_url'].split(',')[1])==image_bytes()
        assert resolved[1]=={'url':refs[1]['image_url']} and resolved[2]=={'url':refs[2]['image_url']}
        assert base64.b64decode(resolved[3]['data_url'].split(',')[1])==image_bytes('blue')
        stored=service.get('alice',job['id'])
        assert stored['parameters']['image_references']==[refs[0],refs[1],{'has_image_reference':True},refs[3]]
        assert stored['parameters']['has_image_reference'] is True
        history=json.dumps(service.history('alice','project-a'))
        assert 'private-signature' not in history and 'data:image' not in history and 'fixture-secret' not in history
        assert await service.create('alice','project-a',args)==stored
        with pytest.raises(HTTPException,match='different arguments'):
            await service.create('alice','project-a',{**args,'image_references':list(reversed(refs))})
        await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('case,code', [('owner',404),('project',403),('invalid-url',400),('unsupported-count',400),
    ('required-two',400),('wrong-kind',400),('aggregate-budget',413),('mime',415)])
def test_composition_all_references_validate_before_account_key_or_reservation(tmp_path,monkeypatch,case,code):
    import termx.media.studio as module
    service,client,spec,captured=composition_service(tmp_path)
    first=service.artifacts.create('alice','project-a','input','First',blob=image_bytes(),mime='image/png')
    other=service.artifacts.create('bob' if case=='owner' else 'alice','other' if case=='project' else 'project-a',
        'input','Second',blob=image_bytes(),mime='audio/mpeg' if case=='mime' else 'image/png')
    refs=[{'image_id':first['id']},{'image_id':other['id']}]
    if case=='invalid-url':refs[1]={'image_url':'https://127.0.0.1/private.png'}
    if case=='unsupported-count':spec['image_reference_limits']={'min':0,'max':1,'app_max':1}
    if case=='required-two':spec['image_reference_limits']={'min':2,'max':5,'app_max':5};refs=refs[:1]
    if case=='wrong-kind':spec.update(kind='video',modes=['video'])
    if case=='aggregate-budget':monkeypatch.setattr(module,'INLINE_TOTAL_LIMIT',len('data:image/png;base64,')+4*((len(image_bytes())+2)//3))
    def no_key(_):raise AssertionError('Reference validation precedes key lookup')
    service.state.credentials.get=no_key
    async def run():
        with pytest.raises(HTTPException) as error:
            await service.create('alice','project-a',request(provider_id='router',model=spec['id'],project_id='project-a',image_references=refs))
        assert error.value.status_code==code and service.list('alice','project-a')==[] and captured==[]
        await client.aclose()
    asyncio.run(run())


def test_composition_real_adapter_preserves_all_inputs_and_pins_verified_provider(tmp_path):
    import base64
    from termx.media.studio_openrouter import OpenRouterAdapter
    service,client,spec,_=composition_service(tmp_path)
    service.openrouter=OpenRouterAdapter(service.artifacts,client)
    first=service.artifacts.create('alice','project-a','input','First',blob=image_bytes(),mime='image/png')
    refs=[{'image_id':first['id']},{'image_url':'https://cdn.example/second.png'},{'image_id':first['id']}]
    calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='GET':return image_endpoint_response(req)
        assert req.url.path=='/api/v1/images' and req.headers['authorization']=='Bearer fixture-secret'
        payload=json.loads(req.content)
        urls=[entry['image_url']['url'] for entry in payload['input_references']]
        assert len(urls)==3 and urls[1]==refs[1]['image_url'] and urls[0]==urls[2]
        assert base64.b64decode(urls[0].split(',')[1])==image_bytes()
        assert payload['provider']=={'only':['verified-image-fixture'],'allow_fallbacks':False}
        assert b'image_id' not in req.content and b'_local' not in req.content
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(image_bytes('blue')).decode(),'media_type':'image/png'}]})
    client._transport=httpx.MockTransport(handler)
    async def run():
        job=await service.create('alice','project-a',request(provider_id='router',model=spec['id'],project_id='project-a',image_references=refs))
        await drain(service)
        final=service.get('alice',job['id'])
        assert final['status']=='completed' and len(final['outputs'])==1
        assert await service.create('alice','project-a',request(provider_id='router',model=spec['id'],project_id='project-a',image_references=refs))==final
        await client.aclose()
    asyncio.run(run())
    assert [req.method for req in calls]==['GET','POST']


def authority_workspace(tmp_path, provider='router'):
    from time import time
    row={'id':provider,'name':'Fixture account','kind':'openai-compatible',
         'base_url':'https://api.kie.ai/api/v1' if provider=='kie' else 'https://openrouter.ai/api/v1',
         'capabilities':['image','video']}
    state=state_for([row])
    state.delivery=SimpleNamespace(directory=tmp_path/'delivery')
    authority={'owner':'alice','live':True,'allowed':True}
    keys=[]
    def identity(token):
        if token=='managed-session-fixture' and authority['live']:
            return SimpleNamespace(principal=SimpleNamespace(id=authority['owner']))
    def require(token,scope,project_id=None):
        if not authority['allowed'] or project_id!='project-a':
            raise HTTPException(403,'Private authorization detail must not enter a job')
    def key(identifier):
        keys.append(identifier)
        return 'fixture-secret'
    state.identity=SimpleNamespace(resolve=identity)
    state.authorization=SimpleNamespace(require=require)
    state.credentials.get=key
    app=FastAPI();mount_media(app,state)
    spec=SNAPSHOT['kie'][0] if provider=='kie' else SNAPSHOT['openrouter'][0]
    state.studio._catalog=[spec];state.studio._catalog_at=time()
    return app,state,authority,keys,spec


def change_authority(authority, change):
    if change=='project':authority['allowed']=False
    elif change=='session':authority['live']=False
    elif change=='owner':authority['owner']='bob'
    else:raise AssertionError(change)


@pytest.mark.parametrize('change,code',[('project',403),('session',401),('owner',403)])
def test_creation_revalidates_actor_after_catalog_before_credentials_or_reservation(tmp_path,change,code):
    app,state,authority,keys,spec=authority_workspace(tmp_path)
    async def catalog():
        await asyncio.sleep(0)
        change_authority(authority,change)
    state.studio._load_catalog=catalog
    with TestClient(app) as client:
        response=client.post('/api/media/studio/jobs',headers={'Authorization':'Bearer managed-session-fixture'},
            json=request(provider_id='router',model=spec['id'],project_id='project-a'))
        assert response.status_code==code
        assert keys==[] and state.studio.list('alice','project-a')==[]
        assert state.studio.tasks==set()


@pytest.mark.parametrize('change',['project','session','owner'])
def test_composition_revalidates_actor_after_endpoint_preflight_before_paid_post(tmp_path,change):
    app,state,authority,keys,spec=authority_workspace(tmp_path)
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.method=='GET','A revoked actor must never reach a paid provider POST'
        assert 'authorization' not in req.headers
        change_authority(authority,change)
        return image_endpoint_response(req)
    provider=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    state.studio.openrouter.client=provider
    args=request(provider_id='router',model=spec['id'],project_id='project-a',image_url='https://cdn.example/reference.png')
    with TestClient(app) as client:
        response=client.post('/api/media/studio/jobs',headers={'Authorization':'Bearer managed-session-fixture'},json=args)
        assert response.status_code==200
        client.portal.call(drain,state.studio)
        job=state.studio.get('alice',response.json()['id'])
        assert job['status']=='failed' and job['outputs']==[]
        assert job['error']=='Authorization changed before provider submission. No generation was submitted.'
        assert keys==['router'] and len(calls)==1
        safe=json.dumps(job)
        for private in ['fixture-secret','managed-session-fixture','Private authorization detail']:
            assert private not in safe and private.encode() not in state.studio.path.read_bytes()
        # The durable failed receipt is never sent again by an internal retry.
        assert client.portal.call(state.studio.create,'alice','project-a',args)==job
        client.portal.call(provider.aclose)
    assert [call.method for call in calls]==['GET']


@pytest.mark.parametrize('provider',['kie','router-video'])
@pytest.mark.parametrize('change',['project','session','owner'])
def test_queued_kie_and_video_submissions_recheck_authority_at_effect_boundary(tmp_path,provider,change):
    account='kie' if provider=='kie' else 'router'
    app,state,authority,keys,spec=authority_workspace(tmp_path,account)
    if provider=='router-video':
        spec=next(model for model in SNAPSHOT['openrouter'] if model['kind']=='video')
        state.studio._catalog=[spec]
    calls=[]
    def handler(req):
        calls.append(req)
        raise AssertionError('No provider operation is permitted after actor revocation')
    transport=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    state.studio.client=transport;state.studio.openrouter.client=transport
    original=state.studio._spawn
    def after_reservation(coroutine):
        change_authority(authority,change)
        original(coroutine)
    state.studio._spawn=after_reservation
    with TestClient(app) as client:
        response=client.post('/api/media/studio/jobs',headers={'Authorization':'Bearer managed-session-fixture'},
            json=request(provider_id=account,model=spec['id'],project_id='project-a'))
        assert response.status_code==200
        client.portal.call(drain,state.studio)
        job=state.studio.get('alice',response.json()['id'])
        assert job['status']=='failed' and 'No generation was submitted' in job['error']
        assert calls==[] and keys==[account]
        client.portal.call(transport.aclose)


@pytest.mark.parametrize('surface',['catalog','refresh'])
@pytest.mark.parametrize('change,code',[('project',403),('session',401),('owner',403)])
def test_awaited_catalog_and_refresh_responses_recheck_same_actor(tmp_path,surface,change,code):
    app,state,authority,keys,spec=authority_workspace(tmp_path)
    headers={'Authorization':'Bearer managed-session-fixture'}
    if surface=='catalog':
        async def catalog():
            await asyncio.sleep(0)
            change_authority(authority,change)
            return {'accounts':[{'name':'sensitive-account-name'}],'models':[]}
        state.studio.catalog=catalog
        url='/api/media/studio/catalog?project_id=project-a'
    else:
        seed_history(state.studio,1)
        async def refresh(owner,identifier):
            job=state.studio.get(owner,identifier)
            await asyncio.sleep(0)
            change_authority(authority,change)
            return {**job,'prompt':'sensitive-job-prompt'}
        state.studio.refresh=refresh
        url='/api/media/studio/jobs/job-0000/refresh'
    with TestClient(app) as client:
        result=client.get(url,headers=headers) if surface=='catalog' else client.post(url,headers=headers)
        assert result.status_code==code
        assert 'sensitive-account-name' not in result.text and 'sensitive-job-prompt' not in result.text
        assert keys==[]
