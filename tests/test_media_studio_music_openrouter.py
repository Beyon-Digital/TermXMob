"""Fixture-only OpenRouter music transport: no live or paid requests."""
import asyncio
import base64
import io
import json
import wave
from pathlib import Path

import httpx
import pytest

from termx.media.studio_music import MusicInput
from termx.media.studio_music_openrouter import OpenRouterMusicAdapter
from termx.media.studio_openrouter import OpenRouterError

MP3 = (Path(__file__).parent / 'fixtures' / 'music-silence.mp3').read_bytes()


class Artifacts:
    def __init__(self):
        self.items = []

    def create(self, owner, project, kind, title, *, blob, mime):
        self.items.append({'owner': owner, 'project': project, 'kind': kind, 'title': title, 'blob': blob, 'mime': mime})
        return {'id': 'owned-song'}


class Fragmented(httpx.AsyncByteStream):
    def __init__(self, raw, sizes=(1, 2, 5, 19, 257)):
        self.raw, self.sizes, self.closed = raw, sizes, False

    async def __aiter__(self):
        offset, index = 0, 0
        while offset < len(self.raw):
            size = self.sizes[index % len(self.sizes)]
            yield self.raw[offset:offset + size]
            offset += size
            index += 1

    async def aclose(self):
        self.closed = True


def body(**changes):
    return MusicInput.model_validate({'request_id': 'fixture-request', 'provider_id': 'openrouter-account',
        'model': 'google/lyria-3-pro-preview', 'prompt': 'Warm acoustic song', 'duration': 60,
        'acknowledge_billing': True, **changes})


def event(value, *, ending=b'\n'):
    return b'data: ' + json.dumps(value, ensure_ascii=False).encode() + ending + ending


def audio_event(blob):
    return event({'choices': [{'index': 0, 'delta': {'audio': {'data': base64.b64encode(blob).decode()}}}]})


STOP = event({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
DONE = b'data: [DONE]\n\n'


def music_stream(blob=MP3):
    # Independently encoded chunks deliberately split MPEG frames and contain
    # base64 padding. Concatenating encoded text would corrupt this fixture.
    return (b': keepalive\r\n\r\n' + audio_event(blob[:17]) + audio_event(blob[17:101])
            + audio_event(blob[101:]) + STOP + event({'choices': [], 'usage': {'total_tokens': 0}}) + DONE)


def run_adapter(handler, values=None, *, artifacts=None, before_submit=None, client_options=None, request_timeout_s=None):
    artifacts = artifacts or Artifacts()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), **(client_options or {})) as client:
            result = await OpenRouterMusicAdapter(artifacts, client, request_timeout_s=request_timeout_s).submit(values or body(), 'selected-secret', 'alice', before_submit=before_submit)
            return result, artifacts
    return asyncio.run(run())


def test_fragmented_sse_decodes_independent_audio_chunks_and_saves_owned_mp3():
    calls, order = [], []
    stream = Fragmented(music_stream())
    def handler(request):
        calls.append(request)
        order.append('send')
        assert str(request.url) == 'https://openrouter.ai/api/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer selected-secret'
        assert request.headers['accept'] == 'text/event-stream'
        assert 'cookie' not in request.headers
        assert request.extensions['timeout'] == {'connect': 20, 'read': None, 'write': None, 'pool': None}
        payload = json.loads(request.content)
        assert payload['model'] == 'google/lyria-3-pro-preview'
        assert payload['modalities'] == ['text', 'audio'] and payload['audio'] == {'format': 'mp3'}
        assert payload['stream'] is True and len(payload['messages']) == 1
        assert payload['messages'][0]['role'] == 'user'
        assert 'Warm acoustic song' in payload['messages'][0]['content']
        assert '60 seconds' in payload['messages'][0]['content']
        return httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream; charset=utf-8'})
    result, artifacts = run_adapter(handler, body(project_id='project-a', title='  Evening song  '),
        before_submit=lambda: order.append('guard'),
        client_options={'base_url': 'https://evil.example', 'params': {'secret': 'unrelated'},
                        'headers': {'Authorization': 'unrelated-secret'}, 'cookies': {'session': 'website-secret'},
                        'auth': ('unrelated', 'password'), 'follow_redirects': True})
    assert result == {'status': 'completed', 'outputs': ['/api/media/artifacts/owned-song/export?format=original']}
    assert artifacts.items == [{'owner': 'alice', 'project': 'project-a', 'kind': 'audio',
        'title': 'Evening song', 'blob': MP3, 'mime': 'audio/mpeg'}]
    assert len(calls) == 1 and order == ['guard', 'send'] and stream.closed


def test_custom_lyrics_and_all_reviewed_directions_survive_without_rewriting():
    lyrics = '[Verse]\n Line one with  two spaces.\nLínea dos\n\n[Chorus]\nSing this exactly!\n'
    def handler(request):
        prompt = json.loads(request.content)['messages'][0]['content']
        assert lyrics in prompt and prompt.endswith(lyrics)
        for value in ('Evening song', 'acoustic pop, warm alto', 'distortion, spoken word'):
            assert value in prompt
        assert 'Sing the supplied lyrics exactly' in prompt
        return httpx.Response(200, content=music_stream(), headers={'content-type': 'text/event-stream'})
    run_adapter(handler, body(mode='custom', prompt='', lyrics=lyrics, title='Evening song',
                             styles='acoustic pop, warm alto', negative_styles='distortion, spoken word'))


def test_clip_prompt_uses_documented_fixed_duration_and_instrumental_guidance():
    def handler(request):
        prompt = json.loads(request.content)['messages'][0]['content']
        assert '30-second music clip' in prompt
        assert 'Instrumental music only' in prompt and 'Do not include singing' in prompt
        assert '60 seconds' not in prompt
        return httpx.Response(200, content=music_stream(), headers={'content-type': 'text/event-stream'})
    run_adapter(handler, body(model='google/lyria-3-clip-preview', duration=30, instrumental=True))


def test_complete_pcm_wav_is_saved_with_its_actual_mime():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x00\x01\x00' * 200)
    blob = output.getvalue()
    _, artifacts = run_adapter(lambda _: httpx.Response(200, content=music_stream(blob), headers={'content-type': 'text/event-stream'}))
    assert artifacts.items[0]['blob'] == blob and artifacts.items[0]['mime'] == 'audio/wav'


@pytest.mark.parametrize('raw', [
    b'', audio_event(MP3), audio_event(MP3) + STOP,
    audio_event(MP3) + STOP + b'data: [DONE]',
    audio_event(MP3) + DONE,
    audio_event(MP3) + event({'choices': [{'delta': {}, 'finish_reason': 'length'}]}) + DONE,
    audio_event(MP3) + event({'choices': [{'delta': {}, 'finish_reason': 'content_filter'}]}) + DONE,
    audio_event(MP3) + event({'error': {'code': 500, 'message': 'selected-secret'}}) + DONE,
    audio_event(MP3) + b'event: error\ndata: {"message":"selected-secret"}\n\n',
    b'event: error\n\n',
    b'data: {malformed}\n\n', b'data: []\n\n',
    event({'choices': [{'delta': {'audio': {'data': '!not-base64!'}}}]}) + STOP + DONE,
    event({'choices': [{'delta': {'audio': {'data': 123}}}]}) + STOP + DONE,
    event({'choices': [{'delta': {'audio': []}}]}) + STOP + DONE,
    event({'choices': [{'index': 1, 'delta': {'audio': {'data': base64.b64encode(MP3).decode()}}}]}) + STOP + DONE,
    event({'choices': 'invalid'}), event({'choices': [{'delta': 'invalid'}]}),
    event({'choices': [{'error': {'message': 'selected-secret'}, 'delta': {}}]}),
    audio_event(MP3) + STOP + audio_event(MP3) + DONE,
    audio_event(MP3) + STOP + STOP + DONE,
])
def test_incomplete_malformed_and_provider_error_streams_never_save_or_replay(raw):
    calls, artifacts = [], Artifacts()
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=Fragmented(raw), headers={'content-type': 'text/event-stream'})
    with pytest.raises(OpenRouterError) as failure:
        run_adapter(handler, artifacts=artifacts)
    assert failure.value.uncertain is True and 'selected-secret' not in str(failure.value)
    assert len(calls) == 1 and not artifacts.items


@pytest.mark.parametrize('blob', [b'ID3junk', MP3[:-20], b'{"url":"https://evil.example/song.mp3"}',
                                b'<html>not audio</html>', b'RIFF\x00\x00\x00\x00WAVE', b''])
def test_fake_or_truncated_audio_is_not_mistaken_for_playable_music(blob):
    artifacts, calls = Artifacts(), []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=music_stream(blob), headers={'content-type': 'text/event-stream'})
    with pytest.raises(OpenRouterError) as failure:
        run_adapter(handler, artifacts=artifacts)
    assert failure.value.uncertain is True and not artifacts.items and len(calls) == 1


@pytest.mark.parametrize('response,uncertain', [
    (httpx.Response(401, text='selected-secret'), False), (httpx.Response(402, text='selected-secret'), False),
    (httpx.Response(422, text='selected-secret'), False), (httpx.Response(429, text='selected-secret'), False),
    (httpx.Response(500, text='selected-secret'), True), (httpx.Response(408, text='selected-secret'), True),
    (httpx.Response(499, text='selected-secret'), True),
    (httpx.Response(302, headers={'location': 'https://evil.example/song.mp3'}), True),
    (httpx.Response(200, json={'audio': 'https://evil.example/song.mp3'}), True),
    (httpx.Response(200, content=music_stream(), headers={'content-type': 'text/html'}), True),
])
def test_status_refusals_and_unexpected_formats_do_not_follow_or_leak(response, uncertain):
    calls, artifacts = [], Artifacts()
    def handler(request): calls.append(request); return response
    with pytest.raises(OpenRouterError) as failure:
        run_adapter(handler, artifacts=artifacts, client_options={'follow_redirects': True})
    assert failure.value.uncertain is uncertain and 'selected-secret' not in str(failure.value)
    assert len(calls) == 1 and not artifacts.items


def test_presubmit_guard_stops_the_effect():
    calls = []
    def guard(): raise PermissionError('revoked')
    with pytest.raises(PermissionError, match='revoked'):
        run_adapter(lambda request: calls.append(request), before_submit=guard)
    assert not calls


def test_timeout_has_uncertain_outcome_and_exactly_one_attempt():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('selected-secret', request=request)
    with pytest.raises(OpenRouterError) as failure:
        run_adapter(handler)
    assert failure.value.uncertain is True and 'selected-secret' not in str(failure.value)
    assert len(calls) == 1


def test_explicit_provider_deadline_closes_stream_without_artifact_or_retry():
    class Stalled(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield audio_event(MP3)
            await asyncio.Event().wait()
        async def aclose(self): self.closed = True
    stream, artifacts, calls = Stalled(), Artifacts(), []
    def handler(request):
        calls.append(request)
        assert request.extensions['timeout'] == {'connect': 20, 'read': 0.01, 'write': 0.01, 'pool': 0.01}
        return httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})
    with pytest.raises(OpenRouterError) as failure:
        run_adapter(handler, artifacts=artifacts, request_timeout_s=0.01)
    assert failure.value.uncertain is True and 'selected-secret' not in str(failure.value)
    assert stream.closed and not artifacts.items and len(calls) == 1


def test_default_generation_stays_streaming_until_cancel_without_saving_partial_audio():
    entered, artifacts, calls = asyncio.Event(), Artifacts(), []
    class Stalled(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield audio_event(MP3)
            entered.set()
            await asyncio.Event().wait()
        async def aclose(self): self.closed = True
    stream = Stalled()
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(OpenRouterMusicAdapter(artifacts, client).submit(body(), 'selected-secret', 'alice'))
            await entered.wait()
            completed, pending = await asyncio.wait({task}, timeout=0.03)
            assert not completed and pending == {task}
            assert not artifacts.items
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
    assert len(calls) == 1 and not artifacts.items and stream.closed


def test_decoded_media_limit_closes_stream_without_persisting(monkeypatch):
    monkeypatch.setattr('termx.media.studio_music_openrouter.LIMIT', len(MP3) - 1)
    stream, artifacts = Fragmented(music_stream()), Artifacts()
    with pytest.raises(OpenRouterError):
        run_adapter(lambda _: httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'}), artifacts=artifacts)
    assert stream.closed and not artifacts.items


def test_oversized_unterminated_event_stops_reading(monkeypatch):
    monkeypatch.setattr('termx.media.studio_music_openrouter.LIMIT', 16)
    class Oversized(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: ' + b'x' * (64 * 1024 + 100)
            raise AssertionError('Do not consume an oversized event')
        async def aclose(self): self.closed = True
    stream = Oversized()
    with pytest.raises(OpenRouterError):
        run_adapter(lambda _: httpx.Response(200, stream=stream, headers={'content-type': 'text/event-stream'}))
    assert stream.closed


def test_local_persistence_failure_is_uncertain_without_retrying_provider():
    class BrokenArtifacts(Artifacts):
        def create(self, *args, **kwargs): raise OSError('selected-secret')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=music_stream(), headers={'content-type': 'text/event-stream'})
    with pytest.raises(OpenRouterError) as failure:
        run_adapter(handler, artifacts=BrokenArtifacts())
    assert failure.value.uncertain is True and 'selected-secret' not in str(failure.value)
    assert len(calls) == 1
