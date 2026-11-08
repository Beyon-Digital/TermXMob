"""Live voice chat: browser socket, host relay, fake OpenAI-Realtime-compatible upstream. No provider is contacted."""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from termx.agent.secrets import CredentialStore
from termx.app import AppState, create_app
from termx.voice import live
from termx.workspace.store import Conflict

PASSWORD = 'strong-password-fixture'
KEY = 'sk-live-fixture-key-123456'
PCM = b'\x01\x00' * 480


def login(state, name='owner'):
    return asyncio.run(state.identity.login('local-password', {'username': name, 'password': PASSWORD}, peer='local', device_name=name))


class FakeUpstream:
    """A scripted realtime server. `script(event, push)` runs on every JSON message the host sends."""

    def __init__(self, script=None):
        self.sent: list[dict] = []
        self.script = script or (lambda event, push: None)
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.loop = None

    def push(self, event: dict | None):
        """Queue a server event (None closes the socket). Safe from the test thread and from the script."""
        self.loop.call_soon_threadsafe(self.inbox.put_nowait, None if event is None else json.dumps(event))

    async def send(self, message):
        self.loop = asyncio.get_running_loop()
        event = json.loads(message)
        self.sent.append(event)
        self.script(event, self.push)

    async def recv(self):
        self.loop = asyncio.get_running_loop()
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError('closed')
        return item

    async def close(self):
        self.closed = True
        self.inbox.put_nowait(None)  # runs on the loop

    def types(self):
        return [event['type'] for event in self.sent]


class Fixture:
    def __init__(self, tmp_path, monkeypatch, script=None):
        monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
        self.state = AppState(credentials=CredentialStore(memory={'rt': KEY}))
        self.owner = self.state.identity.setup_owner('owner', PASSWORD)
        self.client = TestClient(create_app(self.state))
        self.credentials = login(self.state)
        self.token = self.credentials.access_token
        self.headers = {'Authorization': 'Bearer ' + self.token}
        self.state.agent_store.put_provider('rt', kind='openai-compatible', name='Realtime', base_url='https://api.rt.example/v1',
                                            model='rt-model', capabilities=['realtime'], secret_configured=True)
        self.upstream = FakeUpstream(script)
        self.connections: list[tuple[str, dict]] = []
        self.sends: list[dict] = []

        async def connect(url, headers):
            self.connections.append((url, dict(headers)))
            return self.upstream

        self.state.voice_live.connect = connect
        self.state.voice_live.tick = 0.02
        self.session = self.state.workspace.create_session(self.owner, title='Talking')
        self.conversation = self.session['id']
        self.task_result = {'status': 'completed', 'result': 'Tests pass.'}

        async def send(principal, identifier, **kwargs):
            self.sends.append({'principal': principal.id, 'identifier': identifier, **kwargs})
            if isinstance(self.task_result, Exception):
                raise self.task_result
            return {'id': 'task-1'}

        self.state.workspace.send = send
        self.state.agent_store.get_task = lambda task_id, **_: {'id': task_id, **self.task_result} if task_id == 'task-1' else None

    def configure(self, consent=True, **extra):
        changes = {'live_provider': 'rt', 'live_model': 'rt-realtime', 'live_voice': 'verse', 'consent_live': consent, **extra}
        self.state.voice.save_settings(self.owner.id, changes)

    def open(self, conversation=None, headers=None, query=''):
        url = f'/api/voice/live?conversation_id={conversation or self.conversation}{query}'
        return self.client.websocket_connect(url, headers=self.headers if headers is None else headers)


def drain(ws, until, limit=60):
    """Messages from the host until `until(message)` is true. Binary frames come back as bytes."""
    seen = []
    for _ in range(limit):
        message = ws.receive()
        if message['type'] == 'websocket.close':
            seen.append({'type': '__closed__', 'code': message.get('code')})
            return seen
        item = message['bytes'] if message.get('bytes') is not None else json.loads(message['text'])
        seen.append(item)
        if until(item):
            return seen
    raise AssertionError(f'never saw the expected message: {seen}')


def settled(upstream, seconds=2.0):
    """True once the host has closed the provider socket; teardown runs just after the browser socket closes."""
    deadline = time.monotonic() + seconds
    while not upstream.closed and time.monotonic() < deadline:
        time.sleep(0.02)
    return upstream.closed


def is_type(name):
    return lambda item: isinstance(item, dict) and item.get('type') == name


def refusal(app, **kwargs):
    """(reason, close code) for a socket the host refuses. The host accepts first so the browser can read the reason."""
    try:
        with app.open(**kwargs) as ws:
            messages = drain(ws, lambda item: isinstance(item, dict) and item.get('type') in {'ended', '__closed__'})
            if messages[-1]['type'] == 'ended':
                return messages[-1]['reason'], ws.receive().get('code')
            return None, messages[-1]['code']
    except WebSocketDisconnect as closed:
        return None, closed.code


def ready(ws):
    return drain(ws, lambda item: isinstance(item, dict) and item.get('type') == 'state' and item.get('state') == 'listening')


@pytest.fixture
def app(tmp_path, monkeypatch):
    return Fixture(tmp_path, monkeypatch)


# --- capability and settings --------------------------------------------------------------------

def test_realtime_is_a_declarable_provider_capability(app):
    saved = app.state.agent.save_provider(provider_id='rt2', kind='openai-compatible', name='RT2', base_url='https://x.example/v1',
                                          model='m', capabilities=['realtime', 'functions'], api_key=None)
    assert 'realtime' in saved['capabilities']


def test_live_settings_require_a_realtime_account_and_stamp_consent(app):
    with pytest.raises(Exception) as refused:
        app.state.voice.save_settings(app.owner.id, {'live_provider': 'missing'})
    assert refused.value.status_code == 400
    app.state.agent_store.put_provider('plain', kind='openai-compatible', name='Plain', base_url='https://p.example/v1', model='m',
                                       capabilities=['audio'], secret_configured=True)
    with pytest.raises(Exception) as refused:
        app.state.voice.save_settings(app.owner.id, {'live_provider': 'plain'})
    assert refused.value.status_code == 400
    app.configure()
    settings = app.state.voice.settings(app.owner.id)
    assert settings['live_provider'] == 'rt' and settings['consent']['live']['id'] == 'rt' and settings['consent']['live']['at'] > 0
    # Switching account drops the acknowledgement; the host never trusts a client timestamp.
    app.state.agent_store.put_provider('rt3', kind='openai-compatible', name='RT3', base_url='https://r3.example/v1', model='m',
                                       capabilities=['realtime'], secret_configured=True)
    app.state.voice.save_settings(app.owner.id, {'live_provider': 'rt3'})
    assert 'live' not in app.state.voice.settings(app.owner.id)['consent']


def test_status_lists_live_accounts_without_secrets(app):
    app.configure()
    body = app.client.get('/api/voice/status', headers=app.headers).json()
    assert body['live']['configured'] is True and body['live']['accounts'][0]['id'] == 'rt'
    assert KEY not in json.dumps(body)
    graphql = app.client.post('/graphql', headers=app.headers, json={'query': '{voice_status{live settings}}'}).json()
    assert not graphql.get('errors'), graphql
    assert graphql['data']['voice_status']['live']['configured'] is True


# --- socket authentication ----------------------------------------------------------------------

def test_an_unauthenticated_socket_is_closed_with_4401(app):
    app.configure()
    for headers in ({}, {'Authorization': 'Bearer nope'}):
        reason, code = refusal(app, headers=headers)
        assert code == 4401 and reason in {None, 'unauthorized'}
    assert app.connections == []


def test_the_browser_cookie_signs_the_socket_in(app):
    app.configure()
    with app.client.websocket_connect(f'/api/voice/live?conversation_id={app.conversation}',
                                      headers={'Cookie': 'termx_access=' + app.token, 'Origin': 'http://testserver'}) as ws:
        ready(ws)


def test_an_unknown_or_foreign_conversation_is_closed_with_4404_after_saying_why(app):
    app.configure()
    assert refusal(app, conversation='does-not-exist') == ('not_found', 4404)
    app.state.identity.create_principal('Other', ['agent-view', 'agent-run', 'agent-control'])
    foreign = 'foreign-1'
    app.state.workspace.store.create('conversation', 'someone-else', {'title': 'Theirs'}, identifier=foreign)
    assert refusal(app, conversation=foreign) == ('not_found', 4404)
    assert app.connections == []


def test_a_credential_the_host_denies_agent_run_is_closed_with_4403(app, monkeypatch):
    app.configure()

    def deny(credential, action, **resource):
        raise HTTPException(403, 'denied')

    monkeypatch.setattr(app.state.authorization, 'require', deny)
    reason, code = refusal(app)
    assert code == 4403 and reason in {None, 'forbidden'}
    assert app.connections == []


def test_a_missing_conversation_id_is_refused(app):
    app.configure()
    with app.client.websocket_connect('/api/voice/live', headers=app.headers) as ws:
        messages = drain(ws, is_type('ended'))
        assert messages[-1]['reason'] == 'not_found' and ws.receive().get('code') == 4404


# --- readiness gates ----------------------------------------------------------------------------

def test_without_billing_acknowledgement_nothing_is_dialled(app):
    app.configure(consent=False)
    with app.open() as ws:
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'consent_required'
    assert app.connections == []


def test_without_a_realtime_account_the_session_explains_what_to_do(app):
    with app.open() as ws:
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'not_configured' and 'Voice settings' in messages[-1]['detail']
    assert app.connections == []


def test_a_missing_key_never_reaches_the_provider(app):
    app.configure()
    app.state.credentials.delete('rt')
    with app.open() as ws:
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'not_configured'
    assert app.connections == []


# --- the full flow ------------------------------------------------------------------------------

def ask_script(event, push):
    """The first audio makes the model hear the user and ask the agent; the tool output makes it speak."""
    if event['type'] == 'input_audio_buffer.append' and not getattr(ask_script, 'started', False):
        ask_script.started = True
        push({'type': 'input_audio_buffer.speech_started'})
        push({'type': 'input_audio_buffer.speech_stopped'})
        push({'type': 'conversation.item.input_audio_transcription.completed', 'transcript': 'run the tests'})
        push({'type': 'response.created', 'response': {'id': 'r1'}})
        push({'type': 'response.function_call_arguments.done', 'call_id': 'call_1', 'name': 'ask_agent',
              'arguments': json.dumps({'request': 'run the tests'})})
        push({'type': 'response.done', 'response': {'id': 'r1'}})
    if event['type'] == 'conversation.item.create' and event['item'].get('type') == 'function_call_output':
        push({'type': 'response.created', 'response': {'id': 'r2'}})
        push({'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\x02\x00' * 100).decode()})
        push({'type': 'response.output_audio_transcript.done', 'transcript': 'The tests pass.'})
        push({'type': 'response.done', 'response': {'id': 'r2'}})


@pytest.fixture
def flow(tmp_path, monkeypatch):
    ask_script.started = False
    fixture = Fixture(tmp_path, monkeypatch, ask_script)
    fixture.configure(live_transcribe_model='rt-transcribe')
    return fixture


def test_full_flow_from_microphone_to_spoken_answer(flow):
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        messages = drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
    kinds = [item['type'] if isinstance(item, dict) else 'audio' for item in messages]
    assert 'audio' in kinds
    assert {'type': 'transcript', 'role': 'user', 'text': 'run the tests'} in messages
    assert any(isinstance(item, dict) and item.get('text') == 'The tests pass.' for item in messages)
    # The provider got the key from the host credential store, never from the browser.
    url, headers = flow.connections[0]
    assert url.startswith('wss://api.rt.example/v1/realtime?') and 'model=rt-realtime' in url
    assert headers['Authorization'] == 'Bearer ' + KEY
    assert KEY not in json.dumps(messages, default=str)
    # Session setup: one tool, server VAD, PCM16 at 24 kHz, the chosen voice.
    update = next(event for event in flow.upstream.sent if event['type'] == 'session.update')['session']
    assert [tool['name'] for tool in update['tools']] == ['ask_agent']
    assert update['audio']['input']['turn_detection']['type'] == 'server_vad'
    assert update['audio']['input']['format'] == {'type': 'audio/pcm', 'rate': 24000}
    assert update['audio']['output']['voice'] == 'verse'
    assert update['audio']['input']['transcription'] == {'model': 'rt-transcribe'}
    # Browser audio is forwarded as base64 PCM16.
    appended = next(event for event in flow.upstream.sent if event['type'] == 'input_audio_buffer.append')
    assert base64.b64decode(appended['audio']) == PCM
    # ask_agent goes through the same send() as typed chat, as the signed-in owner.
    assert len(flow.sends) == 1
    sent = flow.sends[0]
    assert sent['prompt'] == 'run the tests' and sent['identifier'] == flow.conversation and sent['principal'] == flow.owner.id
    assert sent['managed_session_id'] == flow.credentials.session_id and sent['request_id']
    # The model gets the agent's answer as tool output, flagged as data, then is asked to speak.
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    assert output['call_id'] == 'call_1'
    payload = json.loads(output['output'])
    assert payload['status'] == 'completed' and payload['result'] == 'Tests pass.' and 'data' in payload['note']
    assert flow.upstream.types().index('response.create') > flow.upstream.types().index('conversation.item.create')
    assert settled(flow.upstream)


def test_audio_is_not_retained_even_when_keep_audio_is_on(flow, monkeypatch):
    flow.state.voice.save_settings(flow.owner.id, {'keep_audio': True})

    def forbidden(*args, **kwargs):
        raise AssertionError('live voice must not store audio')

    monkeypatch.setattr(flow.state.artifacts, 'create', forbidden)
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')


def test_the_model_has_no_tool_but_ask_agent(flow):
    flow.upstream.script = lambda event, push: (event['type'] == 'input_audio_buffer.append' and push({
        'type': 'response.function_call_arguments.done', 'call_id': 'c9', 'name': 'run_shell', 'arguments': '{"command":"rm -rf /"}'}))
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        time.sleep(0.3)
    assert flow.sends == []
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    assert json.loads(output['output'])['status'] == 'error'


def test_a_refused_send_is_reported_to_the_model_without_internals(flow):
    flow.task_result = PermissionError('Permission denied /private/path ' + KEY)
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    payload = json.loads(output['output'])
    assert payload['status'] == 'refused'
    assert KEY not in output['output'] and '/private' not in output['output']


def test_a_busy_conversation_is_reported_not_queued(flow):
    flow.task_result = Conflict('Conversation already has an active turn')
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    assert json.loads(output['output'])['status'] == 'busy'


def test_a_turn_waiting_for_approval_is_reported_so_the_owner_can_approve_it(flow):
    flow.task_result = {'status': 'awaiting_approval'}
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    payload = json.loads(output['output'])
    assert payload['status'] == 'awaiting_approval' and 'approve' in payload['message'].lower()


# --- barge-in, mute, interrupt ------------------------------------------------------------------

def speaking_script(event, push):
    if event['type'] == 'input_audio_buffer.append' and event['audio'] == base64.b64encode(PCM).decode():
        push({'type': 'response.created', 'response': {'id': 'r1'}})
        push({'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\x02\x00' * 50).decode()})


def test_barge_in_cancels_the_response_and_flushes_playback(app):
    app.configure()
    app.upstream.script = speaking_script
    with app.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, bytes))
        app.upstream.push({'type': 'input_audio_buffer.speech_started'})
        messages = drain(ws, is_type('flush'))
        # Audio the provider had already queued for the cancelled response is dropped.
        app.upstream.push({'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\x03\x00' * 50).decode()})
        app.upstream.push({'type': 'response.created', 'response': {'id': 'r2'}})
        app.upstream.push({'type': 'response.output_audio.delta', 'delta': base64.b64encode(b'\x04\x00' * 50).decode()})
        after = drain(ws, lambda item: isinstance(item, bytes))
    assert 'response.cancel' in app.upstream.types()
    assert not any(isinstance(item, bytes) for item in messages)
    assert after[-1] == b'\x04\x00' * 50 and sum(isinstance(item, bytes) for item in after) == 1


def test_interrupt_button_cancels_like_barge_in(app):
    app.configure()
    app.upstream.script = speaking_script
    with app.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, bytes))
        ws.send_json({'type': 'interrupt'})
        drain(ws, is_type('flush'))
    assert 'response.cancel' in app.upstream.types()


def test_barge_in_without_a_running_response_sends_no_cancel(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        app.upstream.push({'type': 'input_audio_buffer.speech_started'})
        drain(ws, is_type('flush'))
    assert 'response.cancel' not in app.upstream.types()


def test_muted_microphone_audio_is_not_forwarded(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        ws.send_json({'type': 'mute', 'muted': True})
        ws.send_bytes(PCM)
        ws.send_json({'type': 'mute', 'muted': False})
        ws.send_bytes(PCM)
        time.sleep(0.2)
    assert app.upstream.types().count('input_audio_buffer.append') == 1


def test_oversized_frames_and_garbage_are_ignored(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        ws.send_bytes(b'\x00' * (200 * 1024))
        ws.send_bytes(b'\x01')  # odd length is not PCM16
        ws.send_text('not json')
        ws.send_json({'type': 'nonsense'})
        ws.send_bytes(PCM)
        time.sleep(0.2)
    assert app.upstream.types().count('input_audio_buffer.append') == 1


# --- limits -------------------------------------------------------------------------------------

def test_a_session_ends_after_the_time_limit(app):
    app.configure()
    app.state.voice_live.max_seconds = 0.3
    with app.open() as ws:
        ready(ws)
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'max_duration'
    assert settled(app.upstream)


def test_a_quiet_session_ends_after_the_idle_limit(app):
    app.configure()
    app.state.voice_live.idle_seconds = 0.3
    with app.open() as ws:
        ready(ws)
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'idle'


def test_the_session_outlives_the_five_minute_access_token(tmp_path, monkeypatch):
    from termx import identity as identity_module

    # A two second token stands in for the five minute one. The sign-in itself stays valid.
    monkeypatch.setattr(identity_module, 'ACCESS_TTL', 2)
    short = Fixture(tmp_path, monkeypatch)
    short.configure()
    short.state.voice_live.idle_seconds = 4.0
    with short.open() as ws:
        ready(ws)
        messages = drain(ws, lambda item: isinstance(item, dict) and item.get('type') in {'ended', '__closed__'})
    last = messages[-1]
    assert last.get('type') == 'ended' and last['reason'] == 'idle', last


def test_speech_keeps_a_session_from_going_idle(app):
    app.configure()
    app.state.voice_live.idle_seconds = 0.4
    with app.open() as ws:
        ready(ws)
        for _ in range(4):
            time.sleep(0.2)
            app.upstream.push({'type': 'input_audio_buffer.speech_started'})
            drain(ws, is_type('flush'))
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'idle'


def test_microphone_silence_alone_does_not_count_as_activity(app):
    app.configure()
    app.state.voice_live.idle_seconds = 0.4
    with app.open() as ws:
        ready(ws)
        for _ in range(8):
            ws.send_bytes(PCM)
            time.sleep(0.1)
        assert drain(ws, is_type('ended'))[-1]['reason'] == 'idle'


def test_only_one_live_session_per_owner(app):
    app.configure()
    with app.open() as first:
        ready(first)
        with app.open() as second:
            messages = drain(second, is_type('ended'))
            assert messages[-1]['reason'] == 'busy'
        # The first session is untouched by the refused second one.
        first.send_json({'type': 'ping'})
        first.send_bytes(PCM)
    assert len(app.connections) == 1
    with app.open() as again:  # the slot is free once the first ended
        ready(again)


def test_the_user_can_end_the_session_and_the_reason_is_kept_out_of_the_provider(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        ws.send_json({'type': 'end', 'reason': 'hidden'})
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'hidden'
    assert settled(app.upstream)


def test_unknown_end_reasons_are_normalised(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        ws.send_json({'type': 'end', 'reason': '<script>'})
        assert drain(ws, is_type('ended'))[-1]['reason'] == 'user'


# --- revocation and lock ------------------------------------------------------------------------

def ended_by_host(messages):
    """The host (or the socket guard in front of it, within a quarter second) ended the session; the last frame says how."""
    last = messages[-1]
    return last.get('reason') if last.get('type') == 'ended' else ('closed', last['code'])


def test_revoking_the_signed_in_session_ends_the_live_session(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        app.state.identity.revoke(app.credentials.session_id, app.owner.id)
        outcome = ended_by_host(drain(ws, lambda item: isinstance(item, dict) and item.get('type') in {'ended', '__closed__'}))
    assert outcome in {'revoked', ('closed', 4401), ('closed', 4403)}
    assert settled(app.upstream)


def test_locking_the_session_ends_the_live_session(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        with app.state.identity._db() as db:
            db.execute('UPDATE sessions SET locked=1 WHERE id=?', (app.credentials.session_id,))
        outcome = ended_by_host(drain(ws, lambda item: isinstance(item, dict) and item.get('type') in {'ended', '__closed__'}))
    assert outcome in {'locked', ('closed', 4401), ('closed', 4403)}
    assert settled(app.upstream)


def test_losing_the_conversation_ends_the_live_session(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        original = app.state.workspace.record

        def gone(*args, **kwargs):
            raise KeyError(app.conversation)

        app.state.workspace.record = gone
        try:
            messages = drain(ws, is_type('ended'))
        finally:
            app.state.workspace.record = original
    assert messages[-1]['reason'] == 'revoked'


def test_a_conversation_the_owner_may_no_longer_run_is_refused_at_connect(flow):
    original = flow.state.workspace.record

    def refuse(*args, **kwargs):
        raise PermissionError('revoked')

    flow.state.workspace.record = refuse
    try:
        outcome = refusal(flow)
    finally:
        flow.state.workspace.record = original
    assert outcome == ('forbidden', 4403) and flow.sends == [] and flow.connections == []


# --- upstream failures --------------------------------------------------------------------------

def test_a_provider_that_cannot_be_reached_ends_the_session_with_a_safe_message(app):
    app.configure()

    async def broken(url, headers):
        raise OSError(f'connect failed {url} Bearer {headers["Authorization"]}')

    app.state.voice_live.connect = broken
    with app.open() as ws:
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'upstream_failed'
    text = json.dumps(messages)
    assert KEY not in text and 'api.rt.example' not in text


def test_the_provider_closing_ends_the_session(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        app.upstream.push(None)
        assert drain(ws, is_type('ended'))[-1]['reason'] == 'upstream_closed'


def test_provider_errors_become_fixed_sentences_and_never_echo_provider_text(app):
    app.configure()
    with app.open() as ws:
        ready(ws)
        app.upstream.push({'type': 'error', 'error': {'type': 'invalid_request_error', 'code': 'invalid_api_key',
                                                      'message': 'Incorrect API key provided: sk-live-SECRET'}})
        auth = drain(ws, is_type('error'))[-1]
        app.upstream.push({'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'slow down ' + 'x' * 1000}})
        limited = drain(ws, is_type('error'))[-1]
        app.upstream.push({'type': 'error', 'error': {'message': 'internal path /srv/secret ' + KEY}})
        other = drain(ws, is_type('error'))[-1]
        ws.send_bytes(PCM)
        time.sleep(0.1)
    for message in (auth, limited, other):
        assert 'sk-' not in message['detail'] and 'secret' not in message['detail'].lower() and KEY not in message['detail']
        assert len(message['detail']) < 160
    assert len({auth['detail'], limited['detail'], other['detail']}) == 3
    assert app.upstream.types().count('input_audio_buffer.append') == 1


def test_a_plain_http_account_is_only_allowed_on_loopback(app):
    app.configure()
    app.state.agent_store.put_provider('rt', kind='openai-compatible', name='Realtime', base_url='http://api.rt.example/v1',
                                       model='rt-model', capabilities=['realtime'], secret_configured=True)
    with app.open() as ws:
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'not_configured' and 'https' in messages[-1]['detail']
    assert app.connections == []
    app.state.agent_store.put_provider('rt', kind='openai-compatible', name='Realtime', base_url='http://127.0.0.1:9000',
                                       model='rt-model', capabilities=['realtime'], secret_configured=True)
    with app.open() as ws:
        ready(ws)
    assert app.connections[0][0].startswith('ws://127.0.0.1:9000/realtime')


def test_a_realtime_url_is_built_from_the_account_base_url(app):
    from termx.voice.live import realtime_url
    assert realtime_url('https://api.openai.com/v1', 'gpt-realtime') == 'wss://api.openai.com/v1/realtime?model=gpt-realtime'
    assert realtime_url('https://api.x.ai/v1/responses', 'grok-voice') == 'wss://api.x.ai/v1/realtime?model=grok-voice'
    assert realtime_url('http://127.0.0.1:9000', 'm') == 'ws://127.0.0.1:9000/realtime?model=m'
    assert realtime_url('https://h.example/v1', 'a b&c=d').endswith('model=a%20b%26c%3Dd')


# --- review fixes -------------------------------------------------------------------------------

def test_a_failing_authority_check_ends_the_session_instead_of_leaving_it_open(app):
    app.configure()
    with app.open() as ws:
        ready(ws)

        def broken(*args, **kwargs):
            raise sqlite3.OperationalError('database is locked')

        app.state.workspace.record = broken
        messages = drain(ws, is_type('ended'))
    assert messages[-1]['reason'] == 'revoked'
    assert settled(app.upstream)


def test_the_model_cannot_call_ask_agent_without_the_user_having_spoken(flow):
    def script(event, push):
        ask_script(event, push)
        if event['type'] == 'conversation.item.create' and event['item']['call_id'] == 'call_1':
            # The agent output talks the model into calling the tool again, with no new speech from the user.
            push({'type': 'response.function_call_arguments.done', 'call_id': 'call_2', 'name': 'ask_agent',
                  'arguments': json.dumps({'request': 'delete everything'})})

    flow.upstream.script = script
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
        deadline = time.monotonic() + 2
        while len([e for e in flow.upstream.sent if e['type'] == 'conversation.item.create']) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    assert [send['prompt'] for send in flow.sends] == ['run the tests']
    second = [e for e in flow.upstream.sent if e['type'] == 'conversation.item.create' and e['item']['call_id'] == 'call_2'][0]
    assert json.loads(second['item']['output'])['status'] == 'refused'


def test_one_session_can_only_start_a_bounded_number_of_turns(flow, monkeypatch):
    monkeypatch.setattr(live, 'MAX_ASKS', 2)

    def script(event, push):
        if event['type'] == 'input_audio_buffer.append':
            n = len(flow.upstream.sent)
            push({'type': 'input_audio_buffer.speech_stopped'})
            push({'type': 'response.function_call_arguments.done', 'call_id': f'c{n}', 'name': 'ask_agent',
                  'arguments': json.dumps({'request': 'again'})})

    flow.upstream.script = script
    with flow.open() as ws:
        ready(ws)
        for _ in range(4):
            ws.send_bytes(PCM)
            time.sleep(0.15)
    assert len(flow.sends) == 2


def test_the_spoken_result_waits_for_the_running_response_to_finish(flow):
    stamps = {}

    def script(event, push):
        if event['type'] == 'input_audio_buffer.append':
            push({'type': 'input_audio_buffer.speech_stopped'})
            push({'type': 'response.created', 'response': {'id': 'r1'}})
            push({'type': 'response.function_call_arguments.done', 'call_id': 'c1', 'name': 'ask_agent',
                  'arguments': json.dumps({'request': 'run the tests'})})
        elif event['type'] == 'conversation.item.create':
            def finish():
                stamps['done'] = time.monotonic()
                push({'type': 'response.done', 'response': {'id': 'r1'}})
            flow.upstream.loop.call_later(0.3, finish)
        elif event['type'] == 'response.create':
            stamps['create'] = time.monotonic()

    flow.upstream.script = script
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        deadline = time.monotonic() + 3
        while 'create' not in stamps and time.monotonic() < deadline:
            time.sleep(0.02)
    assert 'create' in stamps and stamps['create'] >= stamps['done']


@pytest.mark.parametrize('status', ['paused', 'recovery_confirmation_required', 'awaiting_approval'])
def test_a_turn_that_needs_the_owner_is_reported_at_once(flow, status):
    flow.task_result = {'status': status}
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    payload = json.loads(output['output'])
    assert payload['status'] == status and 'app' in payload['message'].lower()


def test_an_agent_that_never_asks_for_approval_cannot_be_started_by_voice(flow):
    flow.state.workspace.agents.get_custom_agent = lambda identifier: {'id': identifier, 'approval_mode': 'autonomous'}
    flow.state.workspace.session = lambda *args, **kwargs: {'custom_agent_id': 'free-agent'}
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
    assert flow.sends == []
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    payload = json.loads(output['output'])
    assert payload['status'] == 'refused' and 'type' in payload['message'].lower()


def test_the_agents_reply_is_redacted_before_it_goes_to_the_provider(flow):
    flow.task_result = {'status': 'completed', 'result': f'Done. The token is api_key={KEY} and password=hunter2hunter2.'}
    with flow.open() as ws:
        ready(ws)
        ws.send_bytes(PCM)
        drain(ws, lambda item: isinstance(item, dict) and item.get('role') == 'assistant')
    output = next(event for event in flow.upstream.sent if event['type'] == 'conversation.item.create')['item']
    assert KEY not in output['output'] and 'hunter2hunter2' not in output['output']
    assert json.loads(output['output'])['status'] == 'completed'
