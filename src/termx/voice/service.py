"""Per-owner voice settings, consent gates, sentence streaming and transcription.

Consent is stamped here, on the host, never accepted from the client as a timestamp. Text and audio leave the
device only for an engine the owner has consented to; Woof text never reaches a cloud account unless the owner
turned on cloud fallback and consented to that account.
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator
import threading
from time import time
from typing import Any

from fastapi import HTTPException

from termx.audit import log_event
from termx.voice.engines import SAMPLE_RATE, EngineError, EngineRegistry
from termx.voice.engines.woof import ATTRIBUTION, WoofEngine

MAX_SPEECH_CHARS = 8000
MAX_SENTENCE_CHARS = 400
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_STREAMS_PER_OWNER = 2
ENGINES = {'browser', 'woof', 'provider'}
VOICE_PATTERN = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
MODEL_PATTERN = re.compile(r'^[A-Za-z0-9_.:/-]{1,128}$')
AUDIO_TYPES = {'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/m4a'}
DEFAULTS: dict[str, Any] = {
    'engine': 'browser', 'provider': None, 'model': '', 'transcribe_model': '', 'voice': '',
    'auto_send': False, 'auto_speak': False, 'cloud_fallback': False, 'keep_audio': False, 'consent': {},
    'live_provider': None, 'live_model': '', 'live_voice': '', 'live_transcribe_model': '',
}
LIVE_KINDS = {'openai', 'openai-compatible'}  # providers that speak the OpenAI Realtime wire protocol
_END = re.compile(r'(?<=[.!?…])["\')\]]*\s+|\n+')


def is_live_account(row: dict[str, Any] | None) -> bool:
    """The one rule for a live voice account: an OpenAI-protocol provider that declares the realtime capability."""
    return bool(row) and row['kind'] in LIVE_KINDS and 'realtime' in (row.get('capabilities') or [])


def split_sentences(text: str) -> list[str]:
    """Pieces small enough to synthesize and play while the next one is made."""
    pieces: list[str] = []
    for sentence in (part.strip() for part in _END.split(text)):
        while len(sentence) > MAX_SENTENCE_CHARS:
            cut = max(sentence.rfind(' ', 0, MAX_SENTENCE_CHARS), sentence.rfind(',', 0, MAX_SENTENCE_CHARS))
            cut = cut if cut > 0 else MAX_SENTENCE_CHARS
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].lstrip(', ')
        if sentence:
            pieces.append(sentence)
    return [piece for piece in pieces if piece]


class SpeechJob:
    """A validated plan: engines to try in order, each with its own voice and options."""

    def __init__(self, service: 'VoiceService', owner: str, text: str, plan: list[tuple[Any, str, dict[str, Any]]]):
        self.service, self.owner, self.sentences, self.plan = service, owner, split_sentences(text), plan
        self.released = False
        service.active[owner] = service.active.get(owner, 0) + 1  # held from preparation until release()

    def release(self) -> None:
        """Free this job's slot. Safe to call more than once and for a job that was never read."""
        if not self.released:
            self.released = True
            self.service.active[self.owner] = max(0, self.service.active.get(self.owner, 1) - 1)

    async def stream(self) -> AsyncIterator[str]:
        started = False
        failure = 'No voice engine could speak this text.'
        try:
            for engine, voice, options in self.plan:
                try:
                    for index, sentence in enumerate(self.sentences):
                        async for pcm in engine.synthesize(sentence, voice, options):
                            if not started:
                                started = True
                                start = {'event': 'start', 'engine': engine.id, 'voice': voice, 'sample_rate': SAMPLE_RATE}
                                if engine.id == 'woof':
                                    start['attribution'] = ATTRIBUTION
                                yield json.dumps(start) + '\n'
                            yield json.dumps({'event': 'chunk', 'index': index, 'text': sentence,
                                              'pcm': base64.b64encode(pcm).decode('ascii')}) + '\n'
                    yield json.dumps({'event': 'end'}) + '\n'
                    log_event('voice_speak', engine=engine.id, characters=sum(map(len, self.sentences)))
                    return
                except EngineError as exc:
                    failure = str(exc)
                    if started:
                        break
            yield json.dumps({'event': 'error', 'detail': failure}) + '\n'
        finally:
            self.release()


class VoiceService:
    def __init__(self, state: Any, registry: EngineRegistry, store: Any):
        self.state, self.registry, self.store = state, registry, store
        self.active: dict[str, int] = {}
        self._save_lock = threading.Lock()

    # -- settings ---------------------------------------------------------------------------------

    def settings(self, owner: str) -> dict[str, Any]:
        row = self.store.get('voice_settings', owner)
        body = {key: (row or {}).get(key, default) for key, default in DEFAULTS.items()}
        body['consent'] = dict(body['consent'] or {})
        return body

    def save_settings(self, owner: str, changes: dict[str, Any]) -> dict[str, Any]:
        with self._save_lock:
            return self._save_settings(owner, changes)

    def _save_settings(self, owner: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.settings(owner)
        merged = dict(current)
        for key in ('engine', 'provider', 'model', 'transcribe_model', 'voice', 'auto_send', 'auto_speak', 'cloud_fallback', 'keep_audio',
                    'live_provider', 'live_model', 'live_voice', 'live_transcribe_model'):
            if changes.get(key) is not None:
                merged[key] = changes[key]
        if merged['engine'] not in ENGINES:
            raise HTTPException(400, 'Choose browser, woof or provider as the voice engine')
        for key in ('model', 'transcribe_model', 'live_model', 'live_transcribe_model'):
            merged[key] = str(merged[key]).strip()[:128]
        for key in ('live_model', 'live_transcribe_model'):
            if merged[key] and not MODEL_PATTERN.match(merged[key]):
                raise HTTPException(400, 'Model names use letters, numbers, dots, dashes, colons, slashes and underscores')
        merged['live_voice'] = str(merged['live_voice'] or '').strip()
        for key in ('voice', 'live_voice'):
            if merged[key] and not VOICE_PATTERN.match(str(merged[key])):
                raise HTTPException(400, 'Voice names use letters, numbers, dots, dashes and underscores')
        provider = merged['provider'] or None
        if provider is not None and provider != current['provider']:
            known = self.state.agent_store.get_provider(str(provider))
            if not known or 'audio' not in (known.get('capabilities') or []):
                raise HTTPException(400, 'Choose an API account that declares audio support')
        merged['provider'] = provider
        live = merged['live_provider'] or None
        if live is not None and live != current['live_provider']:
            if not is_live_account(self.state.agent_store.get_provider(str(live))):
                raise HTTPException(400, 'Choose an API account that declares realtime support')
        merged['live_provider'] = live
        consent = dict(current['consent'])
        if live != current['live_provider'] or (consent.get('live') or {}).get('id') != live:
            consent.pop('live', None)  # an acknowledgement belongs to one account
        if changes.get('consent_live') is True and live:
            if (consent.get('live') or {}).get('id') != live:
                consent['live'] = {'id': live, 'at': time()}
        elif changes.get('consent_live') is False:
            consent.pop('live', None)
        if provider != current['provider'] or (consent.get('provider') or {}).get('id') != provider:
            consent.pop('provider', None)
        if changes.get('consent_provider') is True and provider:
            if (consent.get('provider') or {}).get('id') != provider:  # keep the time the owner first agreed
                consent['provider'] = {'id': provider, 'at': time()}
        elif changes.get('consent_provider') is False:
            consent.pop('provider', None)
        accepted = changes.get('accept_woof_license')
        if accepted:
            woof = self.registry.get('woof')
            if not isinstance(woof, WoofEngine) or accepted != woof.license_sha256:
                raise HTTPException(409, 'The Woof license text changed; read and accept it again')
            if (consent.get('woof') or {}).get('sha256') != accepted:
                consent['woof'] = {'sha256': accepted, 'at': time()}
        merged['consent'] = consent
        body = {key: merged[key] for key in DEFAULTS}
        if self.store.get('voice_settings', owner):
            self.store.update('voice_settings', owner, body)
        else:
            self.store.create('voice_settings', owner, body, identifier=owner)
        self.store.log(owner, 'voice_settings', owner, 'saved', {'engine': body['engine'], 'provider': body['provider'],
                                                                  'keep_audio': body['keep_audio']})
        return self.settings(owner)

    # -- status -----------------------------------------------------------------------------------

    def engines(self) -> list[dict[str, Any]]:
        return [engine.status() for engine in self.registry.all()]

    def available(self) -> bool:
        return any(row['state'] == 'ready' for row in self.engines())

    def status(self, owner: str) -> dict[str, Any]:
        settings = self.settings(owner)
        return {'available': self.available(), 'settings': settings, 'engines': self.engines(), 'live': self.live_status(settings),
                'stt': {'configured': bool(settings['provider'] and settings['transcribe_model']
                                           and (settings['consent'].get('provider') or {}).get('id') == settings['provider']),
                        'provider': settings['provider']}}

    def live_accounts(self) -> list[dict[str, Any]]:
        return [row for row in self.state.agent_store.list_providers() if is_live_account(row) and row.get('secret_configured')]

    def live_status(self, settings: dict[str, Any]) -> dict[str, Any]:
        accounts = self.live_accounts()
        chosen = settings['live_provider']
        return {'available': bool(accounts), 'accounts': [{'id': row['id'], 'name': row['name'], 'base_url': row.get('base_url')}
                                                           for row in accounts],
                'configured': bool(chosen and settings['live_model'] and any(row['id'] == chosen for row in accounts)),
                'acknowledged': (settings['consent'].get('live') or {}).get('id') == chosen and bool(chosen)}

    # -- speech -----------------------------------------------------------------------------------

    def _provider_option(self, settings: dict[str, Any], voice: str | None) -> tuple[Any, str, dict[str, Any]] | None:
        engine = self.registry.get('provider')
        consent = settings['consent'].get('provider') or {}
        if engine is None or not settings['provider'] or not settings['model'] or consent.get('id') != settings['provider']:
            return None
        chosen = voice or (settings['voice'] if settings['engine'] == 'provider' else '') or 'alloy'
        return engine, chosen, {'provider': settings['provider'], 'model': settings['model']}

    def prepare(self, owner: str, text: str, *, engine: str | None = None, voice: str | None = None) -> SpeechJob:
        text = (text or '').strip()
        if not text:
            raise HTTPException(400, 'There is nothing to read aloud')
        if len(text) > MAX_SPEECH_CHARS:
            raise HTTPException(413, f'Speech text is limited to {MAX_SPEECH_CHARS} characters')
        if self.active.get(owner, 0) >= MAX_STREAMS_PER_OWNER:
            raise HTTPException(429, 'Two voice replies are already playing; wait for one to finish')
        settings = self.settings(owner)
        chosen = engine or settings['engine']
        if chosen not in ENGINES:
            raise HTTPException(400, 'Choose browser, woof or provider as the voice engine')
        if voice is not None and not VOICE_PATTERN.match(voice):
            raise HTTPException(400, 'Voice names use letters, numbers, dots, dashes and underscores')
        plan: list[tuple[Any, str, dict[str, Any]]] = []
        if chosen == 'browser':
            raise HTTPException(409, 'The browser voice plays on this device; the host is not involved')
        if chosen == 'provider':
            option = self._provider_option(settings, voice)
            if option is None:
                raise HTTPException(409, 'Choose an API account, a speech model and agree to send text to it first')
            plan.append(option)
        elif chosen == 'woof':
            woof = self.registry.get('woof')
            consent = settings['consent'].get('woof') or {}
            if not isinstance(woof, WoofEngine) or consent.get('sha256') != woof.license_sha256:
                raise HTTPException(409, 'Accept the Woof license in Voice settings first')
            woof_voice = (voice or settings['voice'] or 'juno').lower()
            known = [item['id'] for item in woof.voices()]
            if voice is not None and woof_voice not in known:
                # A saved voice may belong to another engine and falls back; a voice asked for by name must exist.
                raise HTTPException(400, f"Woof has no voice named '{voice}'. Choose {' or '.join(known)}.")
            plan.append((woof, woof_voice if woof_voice in known else 'juno', {}))
            if engine is None and settings['cloud_fallback']:
                option = self._provider_option(settings, None)
                if option is not None:
                    plan.append(option)
            if woof.status()['state'] != 'ready' and len(plan) == 1:
                raise HTTPException(503, 'Woof is not ready; download it in Voice settings')
        return SpeechJob(self, owner, text, plan)

    # -- transcription ----------------------------------------------------------------------------

    async def transcribe(self, owner: str, audio: bytes, mime: str) -> dict[str, Any]:
        settings = self.settings(owner)
        consent = settings['consent'].get('provider') or {}
        if not settings['provider'] or not settings['transcribe_model'] or consent.get('id') != settings['provider']:
            raise HTTPException(409, 'Choose an API account, a transcription model and agree to send audio to it first')
        if mime not in AUDIO_TYPES:
            raise HTTPException(415, 'Send a webm, ogg, mp4, mpeg or wav recording')
        if not audio:
            raise HTTPException(400, 'The recording is empty')
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, 'Recording exceeds 10 MB; make a shorter one')
        text = (await self.state.media.transcribe_audio(settings['provider'], settings['transcribe_model'], audio, mime)).strip()
        artifact_id = None
        if settings['keep_audio']:
            artifact_id = self.state.artifacts.create(owner, None, 'input', 'Voice input', blob=audio, mime=mime)['id']
        log_event('voice_transcribe', provider=settings['provider'], audio_bytes=len(audio), kept=bool(artifact_id))
        return {'text': text[:20_000], 'artifact_id': artifact_id}
