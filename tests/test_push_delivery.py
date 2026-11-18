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


# -- generalised kinds: cli.session.attention, orchestrator.report, host.recovered ------------------------------------

import hashlib
import json
import sqlite3

ALICE_TOKEN = 'ExpoPushToken[alice_device_01]'
BOB_TOKEN = 'ExpoPushToken[bob___device_02]'
ROOT_TOKEN = 'ExpoPushToken[root___device_03]'
LEGACY_TOKEN = 'ExpoPushToken[legacy_device_4]'


def legacy_state():
    return SimpleNamespace(identity=SimpleNamespace(configured=False, resolve=lambda secret: None, session_by_id=lambda identifier: None),
        auth=SimpleNamespace(passcode='test-only', allows=lambda secret, scope: secret == 'test-only'), agent=SimpleNamespace(_listeners=set()))


@pytest.fixture
def fleet(tmp_path):
    """A managed host with an operator, another operator and an administrator."""
    people = {name: SimpleNamespace(id=name) for name in ('alice', 'bob', 'root')}
    sessions = {f's-{name}': SimpleNamespace(session_id=f's-{name}', principal=people[name]) for name in people}
    roles = {'alice': 'operator', 'bob': 'operator', 'root': 'admin'}
    owned = {('terminal', 'pty-1'): 'alice', ('conversation', 'main-1'): 'alice', ('terminal', 'pty-2'): 'alice', ('terminal', 'pty-3'): 'alice'}
    revoked = set()

    def require_principal(principal, scope, *, project_id=None, resource_kind=None, resource_id=None):
        if principal.id in revoked:
            raise HTTPException(403, 'revoked')
        if scope == 'host-admin' and roles[principal.id] != 'admin':
            raise HTTPException(403, 'admin only')
        if resource_kind and owned.get((resource_kind, resource_id)) != principal.id:
            raise HTTPException(403, 'not yours')

    state = SimpleNamespace(
        identity=SimpleNamespace(configured=True, resolve=lambda secret: sessions.get(secret), session_by_id=lambda identifier: sessions.get(identifier)),
        auth=SimpleNamespace(passcode=None, allows=lambda secret, scope: secret in sessions),
        authorization=SimpleNamespace(require_principal=require_principal),
        agent=SimpleNamespace(_listeners=set()),
    )
    service = PushDelivery(state, tmp_path / 'push.db')
    for name, token in (('alice', ALICE_TOKEN), ('bob', BOB_TOKEN), ('root', ROOT_TOKEN)):
        service.register(f's-{name}', token, 'http://localhost:8787')
    service.revoked, service.owned = revoked, owned
    yield service
    asyncio.run(service.close())


def jobs(service, kind=None):
    rows = service.db.execute('SELECT token,task,title,body,kind,audience FROM push_jobs ORDER BY created, token').fetchall()
    return [row for row in rows if kind is None or row[4] == kind]


def sent_requests(service):
    sent = []

    def respond(request):
        if request.url.path.endswith('send'):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'data': {'status': 'ok', 'id': 'r' + str(len(sent))}})
        return httpx.Response(200, json={'data': {}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await service.drain(client)
    asyncio.run(run())
    return sent


def test_enqueue_reaches_only_devices_whose_principal_may_see_the_resource(fleet):
    assert fleet.enqueue('cli.session.attention', 'pty-1', 'CLI session needs approval', 'Claude Code is waiting for a permission decision.', audience='alice') == 1
    assert [(row[0], row[4]) for row in jobs(fleet)] == [(ALICE_TOKEN, 'cli.session.attention')]
    assert fleet.enqueue('orchestrator.report', 'main-1', 'Main agent finished its plan', '2 completed.', audience='alice', key='x') == 1
    assert fleet.enqueue('orchestrator.report', 'main-1', 'Main agent finished its plan', '2 completed.', audience='bob') == 0  # not bob's
    # a host-wide notice needs host administration: the operator's device is not told
    assert fleet.enqueue('host.recovered', 'host', 'TermX restarted and recovered work', '1 CLI session re-attached.') == 1
    assert [row[0] for row in jobs(fleet, 'host.recovered')] == [ROOT_TOKEN]
    with pytest.raises(ValueError):
        fleet.enqueue('task.completed', 'x', 'nope')


def test_nothing_is_queued_for_a_host_with_no_registered_device(tmp_path):
    service = PushDelivery(legacy_state(), tmp_path / 'push.db')
    assert service.enqueue('host.recovered', 'host', 'TermX restarted and recovered work', '1 task recovering.') == 0
    assert jobs(service) == []
    asyncio.run(service.close())


def test_the_task_payload_is_unchanged_and_new_kinds_carry_only_status_text(fleet):
    real = fleet.state.authorization.require_principal
    fleet.state.authorization.require_principal = lambda *a, **k: None
    fleet.on_event('task-1', {'type': 'task.completed', 'sequence': 4})
    fleet.state.authorization.require_principal = real
    fleet.enqueue('cli.session.attention', 'pty-1', 'CLI session is waiting for you', 'Claude Code is waiting for input.', audience='alice')
    fleet.state.authorization.require_principal = lambda *a, **k: None  # authority holds when the worker sends
    sent = sent_requests(fleet)
    task = next(item for item in sent if 'taskId' in item['data'])
    assert set(task) == {'to', 'title', 'body', 'sound', 'channelId', 'data'} and task['body'] == 'Open TermX to review this task.'
    assert set(task['data']) == {'host', 'taskId', 'eventId'} and task['data']['taskId'] == 'task-1' and task['title'] == 'Task completed'
    attention = next(item for item in sent if item['data'].get('kind') == 'cli.session.attention')
    assert attention['to'] == ALICE_TOKEN and attention['title'] == 'CLI session is waiting for you' and attention['body'] == 'Claude Code is waiting for input.'
    assert set(attention['data']) == {'host', 'kind', 'resourceId', 'eventId'} and attention['data']['resourceId'] == 'pty-1'
    assert 'taskId' not in attention['data']


def test_a_resource_is_pushed_once_per_interval_and_a_device_has_an_hourly_cap(fleet):
    from termx.push import DEVICE_HOURLY_CAP

    assert fleet.enqueue('cli.session.attention', 'pty-1', 'a', 'b', audience='alice') == 1
    assert fleet.enqueue('cli.session.attention', 'pty-1', 'a', 'b', audience='alice') == 0  # the same session flapping: no storm
    assert fleet.enqueue('cli.session.attention', 'pty-1', 'a', 'b', audience='alice', key='other') == 0
    fleet.db.execute("UPDATE push_jobs SET created=created-61, id=id||'-aged'")  # as if a minute went by
    fleet.db.commit()
    assert fleet.enqueue('cli.session.attention', 'pty-1', 'a', 'b', audience='alice') == 1  # the interval passed
    for index in range(DEVICE_HOURLY_CAP + 5):
        fleet.owned[('terminal', f'many-{index}')] = 'alice'
        fleet.enqueue('cli.session.attention', f'many-{index}', 'a', 'b', audience='alice')
    assert fleet.db.execute("SELECT count(*) FROM push_jobs WHERE token=? AND kind<>'task'", (ALICE_TOKEN,)).fetchone()[0] == DEVICE_HOURLY_CAP
    # task pushes are not throttled by any of this
    fleet.state.authorization.require_principal = lambda *a, **k: None
    for sequence in range(30):
        fleet.on_event('task-9', {'type': 'task.failed', 'sequence': sequence})
    assert fleet.db.execute("SELECT count(*) FROM push_jobs WHERE token=? AND kind='task'", (ALICE_TOKEN,)).fetchone()[0] == 30


def test_authority_is_checked_again_when_a_queued_push_would_be_sent(fleet):
    fleet.enqueue('cli.session.attention', 'pty-1', 'CLI session needs approval', 'Claude Code is waiting for a permission decision.', audience='alice')
    fleet.enqueue('host.recovered', 'host', 'TermX restarted and recovered work', '1 task recovering.')
    fleet.revoked.update({'alice', 'root'})  # access revoked between the event and the delivery
    assert sent_requests(fleet) == []
    assert jobs(fleet) == []


def test_the_legacy_passcode_owner_hears_host_events_and_its_own_records_only(tmp_path):
    service = PushDelivery(legacy_state(), tmp_path / 'push.db')
    service.register('test-only', LEGACY_TOKEN, 'http://localhost:8787')
    assert service.enqueue('host.recovered', 'host', 'TermX restarted and recovered work', '1 task recovering.') == 1
    assert service.enqueue('cli.session.attention', 'pty-1', 'CLI session is waiting for you', 'x', audience='host') == 1
    assert service.enqueue('cli.session.attention', 'pty-2', 'CLI session is waiting for you', 'x', audience='alice') == 0  # a managed user's record
    assert service.enqueue('orchestrator.report', 'main-9', 'Main agent finished its plan', 'x', audience='alice') == 0
    asyncio.run(service.close())


def test_titles_and_bodies_are_clipped_status_lines(fleet):
    fleet.enqueue('host.recovered', 'host', 'T' * 500, 'B' * 500)
    (row,) = jobs(fleet, 'host.recovered')
    assert len(row[2]) <= 140 and len(row[3]) <= 140


def test_a_store_from_before_the_new_kinds_is_upgraded_in_place_and_its_jobs_still_send(tmp_path):
    path = tmp_path / 'push.db'
    old = sqlite3.connect(path)
    old.executescript('''
        CREATE TABLE push_devices(token TEXT PRIMARY KEY, owner TEXT NOT NULL, host TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE push_jobs(id TEXT PRIMARY KEY, token TEXT NOT NULL, task TEXT NOT NULL, title TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, created REAL NOT NULL, receipt TEXT);
    ''')
    owner = 'legacy:' + hashlib.sha256(b'test-only').hexdigest()
    old.execute('INSERT INTO push_devices VALUES(?,?,?,?)', (TOKEN, owner, 'http://localhost:8787', 1.0))
    old.execute('INSERT INTO push_jobs(id,token,task,title,due,created) VALUES(?,?,?,?,?,?)', ('j1', TOKEN, 'task-old', 'Task completed', 0, 9e12))
    old.commit()
    old.close()
    service = PushDelivery(legacy_state(), path)
    assert service.db.execute('SELECT kind, body FROM push_jobs').fetchone() == ('task', None)
    (sent,) = sent_requests(service)
    assert sent['data'] == {'host': 'http://localhost:8787', 'taskId': 'task-old', 'eventId': 'j1'} and sent['body'] == 'Open TermX to review this task.'
    asyncio.run(service.close())
