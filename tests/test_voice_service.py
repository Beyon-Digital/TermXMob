"""Voice service: engines, Woof download and integrity, consent gating, streaming and HTTP."""
from __future__ import annotations

import array
import asyncio
import base64
import hashlib
import json
import struct
import sys
import threading
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from termx.agent.secrets import CredentialStore
from termx.app import AppState, create_app
from termx.voice.engines import EngineError
from termx.voice.engines import woof as woof_module
from termx.voice.engines.woof import MANIFEST, WoofEngine, WoofIntegrityError, WoofManifest
from termx.voice.service import split_sentences

PASSWORD = 'strong-password-fixture'
REVISION = 'a' * 40
FAKE_PACKAGE = {
    'underdog_woof/__init__.py': (
        'import array, threading\n'
        'from .helper import scale\n'
        'SR = 24000\n'
        'VOICES = ("juno", "mira")\n'
        'CALLS = []\n'
        'class Woof:\n'
        '    def __init__(self, weights, voice="juno", **kwargs):\n'
        '        self.weights, self.voice = weights, voice\n'
        '    def __call__(self, text, seed=None):\n'
        '        CALLS.append((text, self.voice, threading.get_ident()))\n'
        '        return array.array("f", [scale(0.25)] * (len(text) * 10))\n'
    ).encode(),
    'underdog_woof/helper.py': b'def scale(value):\n    return value * 2\n',
}
FAKE_FILES = {
    'LICENSE': b'Fake non-commercial license text',
    'NOTICE': b'Fake notice text',
    'woof_int8.npz': b'fake weights',
    **FAKE_PACKAGE,
}


def make_manifest(files=None, revision=REVISION):
    files = files or FAKE_FILES
    return WoofManifest(repo='Fixture/Woof', revision=revision,
                        files={path: (hashlib.sha256(data).hexdigest(), len(data)) for path, data in files.items()})


def serve(files=None, requests=None, replace=None):
    files = dict(files or FAKE_FILES)
    files.update(replace or {})
    def handler(request):
        if requests is not None:
            requests.append(request.url.path)
        prefix = f'/Fixture/Woof/resolve/{REVISION}/'
        assert request.url.path.startswith(prefix), request.url.path
        path = request.url.path.removeprefix(prefix)
        if path not in files:
            return httpx.Response(404)
        return httpx.Response(200, content=files[path])
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def numpy_present(monkeypatch):
    # The fake package needs no numpy; the real one is exercised by hand (see the spike notes).
    monkeypatch.setattr(woof_module, 'numpy_available', lambda: True)
    yield
    for name in [name for name in sys.modules if name.startswith('_termx_woof_')]:
        sys.modules.pop(name)


def engine_for(tmp_path, files=None, requests=None, replace=None, **options):
    manifest = make_manifest(files)
    return WoofEngine(tmp_path / 'voice', manifest=manifest, client_factory=lambda: serve(files, requests, replace), **options)


def license_hash():
    return hashlib.sha256(FAKE_FILES['LICENSE']).hexdigest()


def collect(engine, text, voice='juno'):
    async def run():
        return [chunk async for chunk in engine.synthesize(text, voice, {})]
    return asyncio.run(run())


# --- pinned manifest ---------------------------------------------------------------------------

def test_pinned_manifest_names_a_commit_and_hashes_every_file():
    assert MANIFEST.repo == 'ConwayResearch/Underdog-Woof'
    assert len(MANIFEST.revision) == 40 and set(MANIFEST.revision) <= set('0123456789abcdef')
    assert MANIFEST.installable
    for path, (digest, size) in MANIFEST.files.items():
        assert len(digest) == 64 and set(digest) <= set('0123456789abcdef'), path
        assert size > 0 and not path.startswith('/') and '..' not in path.split('/'), path
    assert {'LICENSE', 'NOTICE', 'woof_int8.npz', 'underdog_woof/__init__.py'} <= set(MANIFEST.files)


def test_empty_manifest_reports_not_installable_and_refuses_download(tmp_path):
    engine = WoofEngine(tmp_path / 'voice', manifest=WoofManifest(repo='x/y', revision='', files={}))
    status = engine.status()
    assert status['state'] == 'unavailable' and not status['installable']
    assert 'no pinned' in status['detail'].lower()
    with pytest.raises(EngineError):
        asyncio.run(engine.install('0' * 64))


# --- license first, then download --------------------------------------------------------------

def test_license_and_notice_are_shown_verified_and_unmodified(tmp_path):
    engine = engine_for(tmp_path)
    texts = asyncio.run(engine.license_texts())
    assert texts['license']['text'] == FAKE_FILES['LICENSE'].decode()
    assert texts['notice']['text'] == FAKE_FILES['NOTICE'].decode()
    assert texts['license']['sha256'] == license_hash()
    assert texts['attribution'] == {'text': 'Voice by Underdog Woof', 'url': 'https://underdog.ai'}


def test_a_tampered_license_is_refused(tmp_path):
    engine = engine_for(tmp_path, replace={'LICENSE': b'Altered license'})
    with pytest.raises(WoofIntegrityError):
        asyncio.run(engine.license_texts())


def test_download_needs_the_accepted_license_and_fetches_no_model_file_without_it(tmp_path):
    requests = []
    engine = engine_for(tmp_path, requests=requests)
    with pytest.raises(EngineError, match='license'):
        asyncio.run(engine.install('f' * 64))
    assert all(path.endswith(('/LICENSE', '/NOTICE')) for path in requests)
    assert not engine.status()['installed']


def test_install_verifies_every_file_and_keeps_license_files_unmodified(tmp_path):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    status = engine.status()
    assert status['installed'] and status['state'] == 'ready' and status['voices'] == [
        {'id': 'juno', 'label': 'Juno'}, {'id': 'mira', 'label': 'Mira'}]
    model = tmp_path / 'voice' / 'woof' / REVISION
    for path, data in FAKE_FILES.items():
        assert (model / path).read_bytes() == data
    assert not list((tmp_path / 'voice' / 'woof').glob('partial*'))


def test_a_file_failing_its_hash_installs_nothing(tmp_path):
    engine = engine_for(tmp_path, replace={'woof_int8.npz': b'poisoned weights'})
    with pytest.raises(WoofIntegrityError) as caught:
        asyncio.run(engine.install(license_hash()))
    assert 'poisoned' not in str(caught.value)
    assert not engine.status()['installed']
    assert not any((tmp_path / 'voice' / 'woof').glob('*'))


def test_same_size_but_different_bytes_fails_the_checksum(tmp_path):
    engine = engine_for(tmp_path, replace={'woof_int8.npz': b'fake weighTS'})
    with pytest.raises(WoofIntegrityError, match='checksum'):
        asyncio.run(engine.install(license_hash()))
    assert not engine.status()['installed'] and engine.status()['state'] == 'error'
    assert not any((tmp_path / 'voice' / 'woof').glob('*'))


def test_an_oversized_download_is_cut_off(tmp_path):
    engine = engine_for(tmp_path, replace={'woof_int8.npz': b'x' * 5000})
    with pytest.raises(WoofIntegrityError, match='size'):
        asyncio.run(engine.install(license_hash()))
    assert not engine.status()['installed']


def test_remove_deletes_the_model_and_unloads(tmp_path):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    collect(engine, 'Hello there.')
    assert engine.loaded
    engine.remove()
    assert not engine.loaded and not engine.status()['installed']
    assert not (tmp_path / 'voice' / 'woof').exists() or not any((tmp_path / 'voice' / 'woof').iterdir())


# --- loading, synthesis, idle unload -----------------------------------------------------------

def test_synthesis_is_lazy_threaded_and_returns_float32_chunks(tmp_path):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    assert not engine.loaded and not [name for name in sys.modules if name.startswith('_termx_woof_')]
    chunks = collect(engine, 'Hello.')
    assert engine.loaded and len(chunks) == 1
    samples = array.array('f')
    samples.frombytes(chunks[0])
    assert len(samples) == 60 and samples[0] == pytest.approx(0.5)
    module = next(module for name, module in sys.modules.items() if name.startswith('_termx_woof_'))
    assert module.CALLS[0][2] != threading.get_ident()
    assert module.CALLS[0][1] == 'juno'


def test_an_unknown_voice_is_refused(tmp_path):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    with pytest.raises(EngineError, match='voice'):
        collect(engine, 'Hello.', voice='../etc')


def test_the_model_unloads_after_idle_and_reloads_on_demand(tmp_path):
    now = [100.0]
    engine = engine_for(tmp_path, idle_seconds=60, clock=lambda: now[0], schedule_unload=False)
    asyncio.run(engine.install(license_hash()))
    collect(engine, 'Hello.')
    now[0] += 30
    assert not engine.unload_if_idle() and engine.loaded
    now[0] += 31
    assert engine.unload_if_idle() and not engine.loaded
    assert not [name for name in sys.modules if name.startswith('_termx_woof_')]
    assert len(collect(engine, 'Again.')) == 1 and engine.loaded


def test_files_changed_after_install_are_refused_at_load(tmp_path):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    helper = tmp_path / 'voice' / 'woof' / REVISION / 'underdog_woof' / 'helper.py'
    helper.write_text('raise SystemExit("owned")\n')
    with pytest.raises(WoofIntegrityError):
        collect(engine, 'Hello.')
    assert not engine.loaded


def test_unexpected_code_beside_the_model_is_refused(tmp_path):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    (tmp_path / 'voice' / 'woof' / REVISION / 'underdog_woof' / 'extra.py').write_text('print("x")\n')
    with pytest.raises(WoofIntegrityError, match='unexpected'):
        collect(engine, 'Hello.')


def test_a_link_in_place_of_a_model_file_is_refused(tmp_path):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    target = tmp_path / 'voice' / 'woof' / REVISION / 'woof_int8.npz'
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.write_bytes(FAKE_FILES['woof_int8.npz'])
    target.unlink()
    target.symlink_to(elsewhere)
    with pytest.raises(WoofIntegrityError):
        collect(engine, 'Hello.')


def test_missing_numpy_is_reported_not_crashed(tmp_path, monkeypatch):
    engine = engine_for(tmp_path)
    asyncio.run(engine.install(license_hash()))
    monkeypatch.setattr(woof_module, 'numpy_available', lambda: False)
    status = engine.status()
    assert status['state'] == 'unavailable' and 'numpy' in status['detail'].lower()
    with pytest.raises(EngineError, match='numpy'):
        collect(engine, 'Hello.')


# --- sentence splitting ------------------------------------------------------------------------

def test_sentences_split_for_streaming_and_long_runs_are_bounded():
    assert split_sentences('One. Two! Three?\nFour') == ['One.', 'Two!', 'Three?', 'Four']
    assert split_sentences('   ') == []
    long = ('word ' * 300).strip()
    parts = split_sentences(long)
    assert all(len(part) <= 400 for part in parts) and ' '.join(parts) == long


# --- full app: HTTP, GraphQL, consent, providers ----------------------------------------------

def login(state, name='owner'):
    return asyncio.run(state.identity.login('local-password', {'username': name, 'password': PASSWORD}, peer='local', device_name=name)).access_token


class Fixture:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'config'))
        self.state = AppState(credentials=CredentialStore(memory={'speech': 'fixture-key'}))
        self.owner = self.state.identity.setup_owner('owner', PASSWORD)
        self.client = TestClient(create_app(self.state))
        self.token = login(self.state)
        self.headers = {'Authorization': 'Bearer ' + self.token}
        self.requests = []
        self.provider_calls = []
        self.state.agent_store.put_provider('speech', kind='openai-compatible', name='Speech', base_url='https://speech.example/v1', model='tts-1', capabilities=['audio'], secret_configured=True)
        self.state.media.client = httpx.AsyncClient(transport=httpx.MockTransport(self.provider))

    def provider(self, request):
        self.provider_calls.append(request)
        if request.url.path.endswith('/audio/speech'):
            samples = struct.pack('<4h', 0, 16384, -16384, 32767)
            return httpx.Response(200, content=samples)
        if request.url.path.endswith('/audio/transcriptions'):
            return httpx.Response(200, json={'text': 'hello from audio'})
        return httpx.Response(404)

    def use_fake_woof(self, tmp_path, **options):
        engine = engine_for(tmp_path, **options)
        self.state.voice.registry.replace(engine)
        self.woof = engine
        return engine

    def gql(self, query, variables=None):
        return self.client.post('/graphql', headers=self.headers, json={'query': query, 'variables': variables or {}}).json()

    def save(self, **fields):
        body = self.gql('mutation($input:VoiceSettingsInput!){save_voice_settings(input:$input){settings}}', {'input': fields})
        assert not body.get('errors'), body
        return body['data']['save_voice_settings']['settings']

    def speak(self, text='Hello there. Second one.', **extra):
        response = self.client.post('/api/voice/speak', headers=self.headers, json={'text': text, **extra})
        return response, [json.loads(line) for line in response.text.splitlines() if line.strip()] if response.status_code == 200 else []


@pytest.fixture
def app(tmp_path, monkeypatch):
    return Fixture(tmp_path, monkeypatch)


def test_endpoints_require_a_managed_session(app):
    for method, path in (('get', '/api/voice/status'), ('post', '/api/voice/speak'), ('post', '/api/voice/transcribe')):
        assert getattr(app.client, method)(path, **({'json': {'text': 'hi'}} if path.endswith('speak') else {})).status_code == 401


def test_status_reports_engines_settings_and_the_machine_capability(app):
    body = app.client.get('/api/voice/status', headers=app.headers).json()
    assert {engine['id'] for engine in body['engines']} == {'provider', 'woof'}
    assert body['settings']['engine'] == 'browser' and body['settings']['keep_audio'] is False
    assert body['settings']['auto_speak'] is False and body['settings']['cloud_fallback'] is False
    provider = next(engine for engine in body['engines'] if engine['id'] == 'provider')
    assert provider['state'] == 'ready'
    health = app.gql('{health{capabilities}}')['data']['health']['capabilities']
    assert health['voice'] is True


def test_voice_status_graphql_and_secrets_never_appear(app):
    body = app.gql('{voice_status{available settings engines stt}}')
    assert not body.get('errors'), body
    assert 'fixture-key' not in json.dumps(body)
    assert body['data']['voice_status']['settings']['engine'] == 'browser'


def test_settings_are_validated_and_stored_per_owner(app):
    saved = app.save(engine='provider', provider='speech', model='tts-1', transcribe_model='whisper-1', voice='alloy', auto_speak=True)
    assert saved['engine'] == 'provider' and saved['auto_speak'] is True and saved['keep_audio'] is False
    bad = app.gql('mutation($input:VoiceSettingsInput!){save_voice_settings(input:$input){settings}}', {'input': {'engine': 'cloud9'}})
    assert bad['errors'][0]['extensions']['http_status'] == 400
    bad = app.gql('mutation($input:VoiceSettingsInput!){save_voice_settings(input:$input){settings}}', {'input': {'provider': 'missing'}})
    assert bad['errors'][0]['extensions']['http_status'] == 400
    bad = app.gql('mutation($input:VoiceSettingsInput!){save_voice_settings(input:$input){settings}}', {'input': {'voice': '../../x'}})
    assert bad['errors'][0]['extensions']['http_status'] == 400
    assert app.state.workspace.store.list('voice_settings', 'someone-else') == []


def test_consent_is_stamped_by_the_host_and_cleared_when_the_provider_changes(app):
    saved = app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    assert saved['consent']['provider']['id'] == 'speech' and saved['consent']['provider']['at'] > 0
    app.state.agent_store.put_provider('other', kind='openai-compatible', name='Other', base_url='https://other.example/v1', model='m', capabilities=['audio'], secret_configured=True)
    changed = app.save(provider='other')
    assert changed['consent'].get('provider') is None


def test_provider_speech_needs_consent_then_streams_per_sentence(app):
    app.save(engine='provider', provider='speech', model='tts-1', voice='alloy')
    response, _ = app.speak()
    assert response.status_code == 409 and not app.provider_calls
    app.save(consent_provider=True)
    response, events = app.speak()
    assert response.status_code == 200 and response.headers['content-type'].startswith('application/x-ndjson')
    assert [event['event'] for event in events] == ['start', 'chunk', 'chunk', 'end']
    assert events[0]['engine'] == 'provider' and events[0]['sample_rate'] == 24000 and 'attribution' not in events[0]
    pcm = array.array('f')
    pcm.frombytes(base64.b64decode(events[1]['pcm']))
    assert list(pcm) == pytest.approx([0.0, 0.5, -0.5, 32767 / 32768])
    assert events[1]['text'] == 'Hello there.' and events[2]['text'] == 'Second one.'
    request = app.provider_calls[0]
    assert json.loads(request.content) == {'model': 'tts-1', 'input': 'Hello there.', 'voice': 'alloy', 'response_format': 'pcm'}
    assert request.headers['authorization'] == 'Bearer fixture-key'


def test_speaking_stores_no_artifact(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    app.speak()
    assert app.state.artifacts.list('anyone') == [] and app.state.artifacts.list(app.owner.id) == []


def test_woof_needs_the_accepted_license_before_it_speaks_and_credits_the_voice(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof')
    asyncio.run(app.woof.install(license_hash()))
    app.save(engine='woof', voice='mira')
    response, _ = app.speak()
    assert response.status_code == 409 and 'license' in response.json()['detail'].lower()
    saved = app.save(accept_woof_license=license_hash())
    assert saved['consent']['woof']['at'] > 0
    response, events = app.speak('Hello.')
    assert response.status_code == 200
    assert events[0]['engine'] == 'woof' and events[0]['attribution'] == {'text': 'Voice by Underdog Woof', 'url': 'https://underdog.ai'}
    assert events[0]['voice'] == 'mira'
    assert not app.provider_calls


def test_accepting_the_wrong_license_hash_is_refused(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof')
    body = app.gql('mutation($input:VoiceSettingsInput!){save_voice_settings(input:$input){settings}}', {'input': {'accept_woof_license': 'f' * 64}})
    assert body['errors'][0]['extensions']['http_status'] == 409


def test_woof_text_never_goes_to_the_cloud_unless_fallback_is_on(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof')  # not installed
    app.save(engine='woof', provider='speech', model='tts-1', accept_woof_license=license_hash(), consent_provider=True)
    response, _ = app.speak()
    assert response.status_code == 503 and not app.provider_calls
    app.save(cloud_fallback=True)
    response, events = app.speak('Hello.')
    assert response.status_code == 200 and events[0]['engine'] == 'provider' and 'attribution' not in events[0]
    assert len(app.provider_calls) == 1


def test_fallback_without_provider_consent_is_not_used(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof')
    app.save(engine='woof', provider='speech', model='tts-1', accept_woof_license=license_hash(), cloud_fallback=True)
    response, _ = app.speak()
    assert response.status_code == 503 and not app.provider_calls


def test_preview_can_name_an_engine_without_changing_the_saved_choice(app):
    app.save(provider='speech', model='tts-1', consent_provider=True)
    response, events = app.speak('Preview.', engine='provider', voice='nova')
    assert response.status_code == 200 and events[0]['engine'] == 'provider'
    assert json.loads(app.provider_calls[0].content)['voice'] == 'nova'
    assert app.client.get('/api/voice/status', headers=app.headers).json()['settings']['engine'] == 'browser'


def test_speech_text_is_bounded(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    response, _ = app.speak('x' * 8001)
    assert response.status_code == 413
    response, _ = app.speak('   ')
    assert response.status_code == 400


def test_engine_failure_after_the_first_chunk_ends_the_stream_with_an_error_event(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(200, content=struct.pack('<2h', 1, 2)) if len(calls) == 1 else httpx.Response(500)
    app.state.media.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    response, events = app.speak()
    assert [event['event'] for event in events] == ['start', 'chunk', 'error']
    assert 'fixture-key' not in response.text


def test_too_many_concurrent_streams_are_refused(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    app.state.voice.active[app.owner.id] = 2
    response, _ = app.speak()
    assert response.status_code == 429


def test_transcription_stores_nothing_by_default_and_sends_the_audio_once(app):
    app.save(provider='speech', transcribe_model='whisper-1', consent_provider=True)
    response = app.client.post('/api/voice/transcribe', headers={**app.headers, 'Content-Type': 'audio/webm'}, content=b'fake-audio')
    assert response.status_code == 200 and response.json() == {'text': 'hello from audio', 'artifact_id': None}
    assert app.state.artifacts.list(app.owner.id) == []
    request = app.provider_calls[0]
    assert request.url.path == '/v1/audio/transcriptions' and b'whisper-1' in request.content and b'fake-audio' in request.content


def test_keep_audio_stores_the_recording_as_an_input(app):
    app.save(provider='speech', transcribe_model='whisper-1', consent_provider=True, keep_audio=True)
    response = app.client.post('/api/voice/transcribe', headers={**app.headers, 'Content-Type': 'audio/webm'}, content=b'fake-audio')
    kept = response.json()['artifact_id']
    assert kept and app.state.artifacts.binary(app.owner.id, kept)[0] == b'fake-audio'


def test_transcription_needs_consent_audio_type_and_size_limit(app):
    app.save(provider='speech', transcribe_model='whisper-1')
    audio = {**app.headers, 'Content-Type': 'audio/webm'}
    assert app.client.post('/api/voice/transcribe', headers=audio, content=b'a').status_code == 409
    app.save(consent_provider=True)
    assert app.client.post('/api/voice/transcribe', headers={**app.headers, 'Content-Type': 'text/plain'}, content=b'a').status_code == 415
    assert app.client.post('/api/voice/transcribe', headers=audio, content=b'').status_code == 400
    assert app.client.post('/api/voice/transcribe', headers=audio, content=b'x' * (10 * 1024 * 1024 + 1)).status_code == 413
    assert not app.provider_calls


def test_scopes_gate_voice_actions_live(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof')
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    app.state.identity.set_scopes(app.owner.id, ['agent-view'])
    assert app.client.get('/api/voice/status', headers=app.headers).status_code == 200
    assert app.speak()[0].status_code == 403
    assert app.client.post('/api/voice/engines/woof/download', headers=app.headers, json={'license_sha256': license_hash()}).status_code == 403
    assert app.client.delete('/api/voice/engines/woof', headers=app.headers).status_code == 403


def test_download_endpoint_installs_after_the_license_was_shown(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof')
    shown = app.client.get('/api/voice/engines/woof/license', headers=app.headers).json()
    assert shown['license']['text'] == FAKE_FILES['LICENSE'].decode() and shown['notice']['text'] == FAKE_FILES['NOTICE'].decode()
    bad = app.client.post('/api/voice/engines/woof/download', headers=app.headers, json={'license_sha256': 'f' * 64})
    assert bad.status_code == 409
    ok = app.client.post('/api/voice/engines/woof/download', headers=app.headers, json={'license_sha256': shown['license']['sha256']})
    assert ok.status_code == 202
    status = app.client.get('/api/voice/status', headers=app.headers).json()
    woof = next(engine for engine in status['engines'] if engine['id'] == 'woof')
    assert woof['installed'] and woof['state'] == 'ready'
    assert status['settings']['consent']['woof']['sha256'] == shown['license']['sha256']
    assert app.client.delete('/api/voice/engines/woof', headers=app.headers).status_code == 200
    woof = next(engine for engine in app.client.get('/api/voice/status', headers=app.headers).json()['engines'] if engine['id'] == 'woof')
    assert not woof['installed']


def test_a_failed_download_surfaces_a_safe_error(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof', replace={'woof_int8.npz': b'poisoned weights'})
    shown = app.client.get('/api/voice/engines/woof/license', headers=app.headers).json()
    app.client.post('/api/voice/engines/woof/download', headers=app.headers, json={'license_sha256': shown['license']['sha256']})
    woof = next(engine for engine in app.client.get('/api/voice/status', headers=app.headers).json()['engines'] if engine['id'] == 'woof')
    assert woof['state'] == 'error' and 'poisoned' not in woof['detail'] and not woof['installed']


def test_woof_is_never_reachable_without_the_owner_session(app, tmp_path):
    app.use_fake_woof(tmp_path / 'woof')
    asyncio.run(app.woof.install(license_hash()))
    anonymous = TestClient(app.client.app)
    assert anonymous.post('/api/voice/speak', json={'text': 'hi', 'engine': 'woof'}).status_code == 401


def test_each_owner_has_their_own_settings_record(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True, keep_audio=True)
    rows = app.state.workspace.store.list('voice_settings')
    assert [row['owner'] for row in rows] == [app.owner.id]
    assert rows[0]['keep_audio'] is True
    second = app.state.identity.create_principal('Second', ['agent-view', 'agent-run', 'agent-control'])
    assert app.state.voice.settings(second.id)['keep_audio'] is False


# --- review fixes ------------------------------------------------------------------------------

def test_without_numpy_woof_cannot_be_downloaded_and_says_why(app, tmp_path, monkeypatch):
    app.use_fake_woof(tmp_path / 'woof')
    monkeypatch.setattr(woof_module, 'numpy_available', lambda: False)
    status = app.woof.status()
    assert status['state'] == 'unavailable' and not status['installable'] and 'numpy' in status['detail'].lower()
    shown = app.client.get('/api/voice/engines/woof/license', headers=app.headers).json()
    refused = app.client.post('/api/voice/engines/woof/download', headers=app.headers, json={'license_sha256': shown['license']['sha256']})
    assert refused.status_code == 409 and 'numpy' in refused.json()['detail'].lower()
    assert not app.woof.installed


def test_saving_again_keeps_the_original_consent_time(app, tmp_path):
    first = app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    again = app.save(voice='nova', consent_provider=True)
    assert again['consent']['provider']['at'] == first['consent']['provider']['at']
    app.use_fake_woof(tmp_path / 'woof')
    accepted = app.save(accept_woof_license=license_hash())
    repeated = app.save(accept_woof_license=license_hash())
    assert repeated['consent']['woof']['at'] == accepted['consent']['woof']['at']


def test_first_saves_from_several_threads_do_not_collide(app):
    errors = []
    def save():
        try:
            app.state.voice.save_settings('fresh-owner', {'auto_speak': True})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=save) for _ in range(8)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert not errors and app.state.voice.settings('fresh-owner')['auto_speak'] is True


def test_an_unexpected_provider_failure_ends_the_stream_with_an_error_event(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    async def broken(*args, **kwargs):
        raise httpx.InvalidURL('bad url with secret fixture-key')
    app.state.media.speak = broken
    response, events = app.speak()
    assert response.status_code == 200
    assert [event['event'] for event in events] == ['error']
    assert 'fixture-key' not in response.text


def test_shutdown_closes_the_voice_engines(app):
    closed = []
    class Probe(woof_module.VoiceEngine):
        id = 'probe'
        def close(self):
            closed.append(self.id)
    app.state.voice.registry.register(Probe())
    from termx.lifecycle import shutdown_state
    asyncio.run(shutdown_state(app.state))
    assert closed == ['probe']


def test_the_stream_limit_counts_jobs_that_were_prepared_but_not_yet_read(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    service = app.state.voice
    first = service.prepare(app.owner.id, 'One.')
    service.prepare(app.owner.id, 'Two.')
    with pytest.raises(HTTPException) as refused:
        service.prepare(app.owner.id, 'Three.')
    assert refused.value.status_code == 429
    first.release()
    first.release()  # releasing twice frees one slot only
    assert service.active[app.owner.id] == 1
    service.prepare(app.owner.id, 'Four.')


def test_a_finished_stream_frees_its_slot(app):
    app.save(engine='provider', provider='speech', model='tts-1', consent_provider=True)
    for _ in range(4):
        assert app.speak()[0].status_code == 200
    assert app.state.voice.active.get(app.owner.id, 0) == 0


@pytest.mark.parametrize('target', ['http://127.0.0.1/x', 'https://evil.example/x', 'https://huggingface.co.evil.example/x'])
def test_downloads_do_not_follow_redirects_off_the_hugging_face_hosts(target):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'location': target}) if request.url.host == 'huggingface.co' else httpx.Response(200, content=b'x')
    client = woof_module._default_client(transport=httpx.MockTransport(handler))
    async def run():
        async with client:
            await client.get('https://huggingface.co/a')
    with pytest.raises(EngineError):
        asyncio.run(run())
    assert seen == ['https://huggingface.co/a']


def test_downloads_follow_redirects_to_the_hugging_face_cdn():
    def handler(request):
        if request.url.host == 'huggingface.co':
            return httpx.Response(302, headers={'location': 'https://cdn-lfs.hf.co/file'})
        return httpx.Response(200, content=b'ok')
    client = woof_module._default_client(transport=httpx.MockTransport(handler))
    async def run():
        async with client:
            return (await client.get('https://huggingface.co/a')).content
    assert asyncio.run(run()) == b'ok'


def test_weights_are_checked_again_when_the_voice_changes_while_loaded(tmp_path):
    engine = engine_for(tmp_path, idle_seconds=600, schedule_unload=False)
    asyncio.run(engine.install(license_hash()))
    collect(engine, 'Hello.', voice='juno')
    (engine.dir / 'woof_int8.npz').write_bytes(b'FAKE WEIGHTS')
    with pytest.raises(WoofIntegrityError):
        collect(engine, 'Hello.', voice='mira')
    assert not engine.loaded
