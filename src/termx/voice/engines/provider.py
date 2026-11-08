"""Cloud or server text-to-speech through the account the owner selected (MediaProvider.speak).

Covers hosted providers and any OpenAI-compatible server, such as a local Kokoro-FastAPI. The server is asked
for raw 24 kHz 16-bit PCM and converted to float32, so it plays like every other engine.
"""
from __future__ import annotations

import array
import sys
from collections.abc import AsyncIterator
from typing import Any

from termx.voice.engines import EngineError, VoiceEngine

SUGGESTED_VOICES = ['alloy', 'ash', 'coral', 'echo', 'fable', 'nova', 'onyx', 'sage', 'shimmer']


def pcm16_to_float32(raw: bytes) -> bytes:
    samples = array.array('h')
    samples.frombytes(raw[:len(raw) - len(raw) % 2])
    if sys.byteorder == 'big':
        samples.byteswap()
    out = array.array('f', (value / 32768 for value in samples))
    if sys.byteorder == 'big':
        out.byteswap()
    return out.tobytes()


class ProviderEngine(VoiceEngine):
    id = 'provider'
    label = 'Cloud or server API'

    def __init__(self, media, agent_store, credentials):
        self.media, self.agent_store, self.credentials = media, agent_store, credentials

    def accounts(self) -> list[dict[str, Any]]:
        return [row for row in self.agent_store.list_providers()
                if 'audio' in (row.get('capabilities') or []) and row.get('secret_configured')
                and row['kind'] in {'openai', 'openai-compatible'}]

    def status(self) -> dict[str, Any]:
        accounts = self.accounts()
        return {'id': self.id, 'label': self.label, 'kind': 'provider',
                'state': 'ready' if accounts else 'unavailable',
                'detail': ('Text is sent to the account you choose.' if accounts
                           else 'Connect an API account with audio support in Models & engines.'),
                'installed': bool(accounts), 'installable': False, 'voices': self.voices(),
                'accounts': [{'id': row['id'], 'name': row['name'], 'base_url': row.get('base_url')} for row in accounts]}

    def voices(self) -> list[dict[str, str]]:
        return [{'id': name, 'label': name.capitalize()} for name in SUGGESTED_VOICES]

    async def synthesize(self, text: str, voice: str, options: dict[str, Any]) -> AsyncIterator[bytes]:
        from fastapi import HTTPException
        try:
            raw = await self.media.speak(options.get('provider'), options.get('model'), text, voice)
        except HTTPException as exc:
            raise EngineError(str(exc.detail)) from exc
        except Exception as exc:
            raise EngineError('The speech account could not be reached.') from exc
        if not raw:
            raise EngineError('The speech account returned no audio')
        yield pcm16_to_float32(raw)
