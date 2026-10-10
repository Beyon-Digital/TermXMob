import asyncio
from types import SimpleNamespace
import httpx
import pytest
from fastapi import HTTPException
from termx.push import PushDelivery

TOKEN = 'ExpoPushToken[device_12345678]'

@pytest.fixture
def delivery(tmp_path):
    state = SimpleNamespace(
        identity=SimpleNamespace(configured=False, resolve=lambda secret: None, session_by_id=lambda identifier: None),
        auth=SimpleNamespace(passcode='test-only', allows=lambda secret, scope: secret == 'test-only'),
        agent=SimpleNamespace(_listeners=set()),
    )
    service = PushDelivery(state, tmp_path / 'push.db')
    yield service
    asyncio.run(service.close())

def test_registration_rejects_unauthorized_and_malformed_tokens(delivery):
    with pytest.raises(HTTPException): delivery.register('wrong', TOKEN, 'http://localhost:8787')
    with pytest.raises(HTTPException): delivery.register('test-only', 'not-a-token', 'http://localhost:8787')
    with pytest.raises(HTTPException): delivery.register('test-only', TOKEN, 'https://user:secret@host:8787')
    delivery.register('test-only', TOKEN, 'http://localhost:8787')
    assert delivery.db.execute('SELECT owner FROM push_devices').fetchone()[0] != 'test-only'

def test_queue_deduplicates_and_passcode_rotation_revokes_delivery(delivery):
    delivery.register('test-only', TOKEN, 'http://localhost:8787')
    event = {'type': 'task.completed', 'sequence': 7}
    delivery.on_event('task1', event)
    delivery.on_event('task1', event)
    assert delivery.db.execute('SELECT count(*) FROM push_jobs').fetchone()[0] == 1
    delivery.state.auth.passcode = 'rotated'
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('Revoked authority must not send'))) as client:
            await delivery.drain(client)
    asyncio.run(run())
    assert delivery.db.execute('SELECT count(*) FROM push_jobs').fetchone()[0] == 0

def test_ticket_receipt_removes_invalid_token_without_leaking_task_content(delivery):
    delivery.register('test-only', TOKEN, 'http://localhost:8787')
    delivery.on_event('task1', {'type': 'approval.requested', 'sequence': 9, 'payload': {'prompt': 'private source code'}})
    def respond(request):
        assert b'private source code' not in request.content
        if request.url.path.endswith('send'):
            assert b'"taskId":"task1"' in request.content
            return httpx.Response(200, json={'data': {'status': 'ok', 'id': 'receipt1'}})
        return httpx.Response(200, json={'data': {'receipt1': {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await delivery.drain(client)
            delivery.db.execute('UPDATE push_jobs SET due=0')
            delivery.db.commit()
            await delivery.drain(client)
    asyncio.run(run())
    assert delivery.db.execute('SELECT count(*) FROM push_devices').fetchone()[0] == 0

def test_transient_failure_is_persisted_for_retry(delivery):
    delivery.register('test-only', TOKEN, 'http://localhost:8787')
    delivery.on_event('task1', {'type': 'task.failed', 'sequence': 2})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            await delivery.drain(client)
    asyncio.run(run())
    assert delivery.db.execute('SELECT attempts FROM push_jobs').fetchone()[0] == 1
    delivery.unregister('test-only', TOKEN)
    assert delivery.db.execute('SELECT count(*) FROM push_jobs').fetchone()[0] == 0

def test_managed_session_rechecks_resource_authority(delivery):
    principal = SimpleNamespace(id='alice')
    session = SimpleNamespace(session_id='s1', principal=principal)
    delivery.state.identity.configured = True
    delivery.state.identity.resolve = lambda secret: session
    delivery.state.identity.session_by_id = lambda identifier: session
    def deny(*args, **kwargs): raise HTTPException(403, 'resource revoked')
    delivery.state.authorization = SimpleNamespace(require_principal=deny)
    delivery.register('test-only', TOKEN, 'http://localhost:8787')
    delivery.on_event('another-users-task', {'type': 'task.completed', 'sequence': 1})
    assert delivery.db.execute('SELECT count(*) FROM push_jobs').fetchone()[0] == 0
