"""Stream documented OpenRouter music audio into owned local artifacts.

Audio-output chat completions use SSE ``delta.audio.data`` base64 chunks:
https://openrouter.ai/docs/guides/overview/multimodal/audio
The caller owns account selection, consent, model admission and durable receipts.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import Callable

import httpx

from termx.media.service import LIMIT
from termx.media.studio_music import MusicArtifacts, MusicInput, valid_mp3
from termx.media.studio_openrouter import OpenRouterError, _status_error

_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'


def music_prompt(body: MusicInput) -> str:
    """Preserve reviewed text; Lyria controls are natural-language guidance."""
    parts = ['Compose and perform an original piece of music.']
    if body.title.strip():
        parts.append('Title: ' + body.title)
    if body.prompt.strip():
        parts.append('Song description:\n' + body.prompt)
    if body.styles.strip():
        parts.append('Styles and musical directions:\n' + body.styles)
    if body.negative_styles.strip():
        parts.append('Avoid these styles and elements:\n' + body.negative_styles)
    if body.model == 'google/lyria-3-clip-preview':
        parts.append('Create a 30-second music clip.')
    else:
        parts.append(f'Aim for a duration of {body.duration} seconds with a complete ending.')
    if body.instrumental:
        parts.append('Instrumental music only. Do not include singing, speech or vocal sounds.')
    elif body.mode == 'custom':
        parts.append('Sing the supplied lyrics exactly, preserving their words and section directions.\n'
                     'Lyrics:\n' + body.lyrics)
    else:
        parts.append('Include suitable original sung lyrics if the description calls for vocals.')
    return '\n\n'.join(parts)


def _invalid() -> OpenRouterError:
    return OpenRouterError('OpenRouter did not return complete valid music audio. Inspect the provider account; this request will not be resubmitted.', uncertain=True)


def _valid_wav(blob: bytes) -> bool:
    """Accept complete RIFF/WAVE PCM audio, including bounded ancillary chunks."""
    if (len(blob) < 44 or blob[:4] != b'RIFF' or blob[8:12] != b'WAVE'
            or int.from_bytes(blob[4:8], 'little') != len(blob) - 8):
        return False
    offset, alignment, data_size = 12, 0, 0
    while offset < len(blob):
        if offset + 8 > len(blob):
            return False
        tag = blob[offset:offset + 4]
        size = int.from_bytes(blob[offset + 4:offset + 8], 'little')
        start, end = offset + 8, offset + 8 + size
        offset = end + (size % 2)
        if offset > len(blob):
            return False
        if tag == b'fmt ':
            if alignment or size < 16:
                return False
            encoding = int.from_bytes(blob[start:start + 2], 'little')
            channels = int.from_bytes(blob[start + 2:start + 4], 'little')
            rate = int.from_bytes(blob[start + 4:start + 8], 'little')
            byte_rate = int.from_bytes(blob[start + 8:start + 12], 'little')
            alignment = int.from_bytes(blob[start + 12:start + 14], 'little')
            bits = int.from_bytes(blob[start + 14:start + 16], 'little')
            if (encoding != 1 or not 1 <= channels <= 8 or not 1 <= rate <= 384000
                    or bits not in {8, 16, 24, 32} or alignment != channels * bits // 8
                    or byte_rate != rate * alignment):
                return False
        elif tag == b'data':
            if not alignment or data_size or not size or size % alignment:
                return False
            data_size = size
    return offset == len(blob) and data_size > 0


class _AudioStream:
    def __init__(self) -> None:
        self.audio = bytearray()
        self.done = False
        self.finished = False

    def event(self, data: bytes, event_type: bytes) -> None:
        if event_type == b'error' or self.done:
            raise _invalid()
        if data == b'[DONE]':
            if not self.finished or not self.audio:
                raise _invalid()
            self.done = True
            return
        try:
            value = json.loads(data.decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise _invalid() from exc
        if not isinstance(value, dict) or value.get('error') is not None:
            raise _invalid()
        choices = value.get('choices')
        if not isinstance(choices, list) or len(choices) > 1:
            raise _invalid()
        for choice in choices:
            if (not isinstance(choice, dict) or choice.get('index', 0) != 0
                    or choice.get('error') is not None):
                raise _invalid()
            delta = choice.get('delta', {})
            if not isinstance(delta, dict) or delta.get('error') is not None:
                raise _invalid()
            audio = delta.get('audio')
            if audio is not None:
                if not isinstance(audio, dict):
                    raise _invalid()
                encoded = audio.get('data', '')
                if not isinstance(encoded, str) or (encoded and self.finished):
                    raise _invalid()
                if len(encoded) > ((LIMIT - len(self.audio) + 2) // 3) * 4:
                    raise _invalid()
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise _invalid() from exc
                if len(self.audio) + len(decoded) > LIMIT:
                    raise _invalid()
                self.audio.extend(decoded)
            reason = choice.get('finish_reason')
            if reason is not None:
                if reason != 'stop' or self.finished:
                    raise _invalid()
                self.finished = True


async def _read_audio(response: httpx.Response) -> tuple[bytes, str]:
    # These bound transport and parser memory, not permitted generations or
    # provider execution. Encoded binary needs more space than the media limit.
    wire_limit = LIMIT * 2 + 1024 * 1024
    event_limit = ((LIMIT + 2) // 3) * 4 + 64 * 1024
    wire_size, event_size = 0, 0
    pending = bytearray()
    data: list[bytes] = []
    event_type = b''
    stream = _AudioStream()
    async for chunk in response.aiter_bytes():
        wire_size += len(chunk)
        if wire_size > wire_limit:
            raise _invalid()
        pending.extend(chunk)
        while (end := pending.find(b'\n')) >= 0:
            line = bytes(pending[:end]).removesuffix(b'\r')
            del pending[:end + 1]
            if not line:
                if data:
                    stream.event(b'\n'.join(data), event_type)
                elif event_type == b'error':
                    raise _invalid()
                data, event_type, event_size = [], b'', 0
                if stream.done:
                    blob = bytes(stream.audio)
                    if valid_mp3(blob):
                        return blob, 'audio/mpeg'
                    if _valid_wav(blob):
                        return blob, 'audio/wav'
                    raise _invalid()
            elif not line.startswith(b':'):
                event_size += len(line)
                if event_size > event_limit:
                    raise _invalid()
                field, _, value = line.partition(b':')
                value = value.removeprefix(b' ')
                if field == b'data':
                    data.append(value)
                elif field == b'event':
                    event_type = value
        if len(pending) + event_size > event_limit:
            raise _invalid()
    # EOF without the final event delimiter and [DONE] is not confirmation.
    raise _invalid()


class OpenRouterMusicAdapter:
    def __init__(self, artifacts: MusicArtifacts, client: httpx.AsyncClient | None = None, *,
                 request_timeout_s: float | None = None) -> None:
        self.artifacts = artifacts
        self.client = client
        self.request_timeout_s = request_timeout_s

    async def submit(self, body: MusicInput, key: str, owner: str, *,
                     before_submit: Callable[[], None] | None = None) -> dict[str, object]:
        client = self.client or httpx.AsyncClient()
        response: httpx.Response | None = None
        try:
            # Build directly: supplied clients cannot add default credentials,
            # cookies or query parameters, or choose a different API origin.
            request = httpx.Request('POST', _ENDPOINT,
                headers={'Authorization': f'Bearer {key}', 'Accept': 'text/event-stream'},
                json={'model': body.model, 'messages': [{'role': 'user', 'content': music_prompt(body)}],
                      'modalities': ['text', 'audio'], 'audio': {'format': 'mp3'}, 'stream': True},
                extensions={'timeout': httpx.Timeout(self.request_timeout_s, connect=20).as_dict()})
            # Active generation has no deadline unless the selected provider
            # explicitly configures one. The connect handshake stays bounded.
            async with asyncio.timeout(self.request_timeout_s):
                if before_submit is not None:
                    before_submit()
                response = await client.send(request, auth=None, follow_redirects=False, stream=True)
                if not 200 <= response.status_code < 300:
                    raise _status_error(response.status_code)
                if response.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'text/event-stream':
                    raise _invalid()
                blob, mime = await _read_audio(response)
            try:
                item = self.artifacts.create(owner, body.project_id, 'audio', body.title.strip() or 'Generated music',
                                             blob=blob, mime=mime)
            except Exception as exc:
                raise OpenRouterError('Music was generated but could not be saved on this host. Inspect this job and the provider account before creating another song.', uncertain=True) from exc
            return {'status': 'completed', 'outputs': [f'/api/media/artifacts/{item["id"]}/export?format=original']}
        except (httpx.RequestError, TimeoutError) as exc:
            raise OpenRouterError('Music submission could not be confirmed. Inspect the provider account; this request will not be resubmitted.', uncertain=True) from exc
        finally:
            if response is not None:
                await response.aclose()
            if self.client is None:
                await client.aclose()
