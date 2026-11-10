"""Text-to-speech engine interface and registry.

Every engine yields mono float32 little-endian PCM at 24 kHz, one or more chunks per piece of text,
so the browser plays all engines the same way.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

SAMPLE_RATE = 24_000


class EngineError(Exception):
    """A failure whose message is safe to show to the user (no secrets, no file contents, no URLs)."""


class VoiceEngine:
    id = ''
    label = ''

    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    def voices(self) -> list[dict[str, str]]:
        raise NotImplementedError

    async def synthesize(self, text: str, voice: str, options: dict[str, Any]) -> AsyncIterator[bytes]:
        raise NotImplementedError
        yield b''  # pragma: no cover - marks this as an async generator

    def close(self) -> None:
        pass


class EngineRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, VoiceEngine] = {}

    def register(self, engine: VoiceEngine) -> None:
        if engine.id in self._engines:
            raise ValueError(f'Voice engine {engine.id} is already registered')
        self._engines[engine.id] = engine

    def replace(self, engine: VoiceEngine) -> None:
        old = self._engines.get(engine.id)
        if old is not None and old is not engine:
            old.close()
        self._engines[engine.id] = engine

    def get(self, engine_id: str) -> VoiceEngine | None:
        return self._engines.get(engine_id)

    def all(self) -> list[VoiceEngine]:
        return list(self._engines.values())

    def close(self) -> None:
        for engine in self._engines.values():
            engine.close()
