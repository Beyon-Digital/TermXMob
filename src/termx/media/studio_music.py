"""Documented ElevenLabs music composition with durable, non-replayed receipts.

Only the host's explicitly selected API account is used. Website sessions and
third-party Suno wrappers are intentionally outside this adapter contract.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import ClassVar, Literal, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from termx.media.service import LIMIT
from termx.media.studio_openrouter import OpenRouterError

MUSIC_MODEL_ID = 'elevenlabs/music_v2_5'
MUSIC_DOCUMENTATION = [
    'https://elevenlabs.io/docs/api-reference/music/compose',
    'https://elevenlabs.io/docs/eleven-api/guides/how-to/music/composition-plans',
]
MUSIC_MODELS: list[dict[str, object]] = [{
    'id': MUSIC_MODEL_ID, 'label': 'Eleven Music 2.5', 'kind': 'audio', 'adapter': 'elevenlabs',
    'description': 'Songs or guaranteed instrumentals from a description; exact custom lyrics from a composition plan.',
    'modes': ['music'], 'aspect_ratios': [], 'resolutions': [],
    'durations': [15, 30, 60, 90, 120, 180, 240, 300, 600],
    'duration_min': 3, 'duration_max': 600, 'custom_duration_max': 120,
    'supported_music_modes': ['simple', 'custom'], 'supports_custom_lyrics': True,
    'supports_instrumental': True, 'supports_negative_styles': True,
    'duration_mode': 'exact', 'lyrics_constraints': 'sections',
    'prompt_max_length': 4100, 'lyrics_max_length': 12000,
    'documentation': MUSIC_DOCUMENTATION,
}]


class MusicInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', strict=True)
    request_id: str = Field(min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(default=MUSIC_MODEL_ID, min_length=1, max_length=200)
    mode: Literal['simple', 'custom'] = 'simple'
    prompt: str = Field(default='', max_length=4100)
    lyrics: str = Field(default='', max_length=12000)
    styles: str = Field(default='', max_length=1000)
    negative_styles: str = Field(default='', max_length=1000)
    title: str = Field(default='', max_length=200)
    instrumental: StrictBool = False
    duration: int = Field(default=60, ge=3, le=600)
    acknowledge_billing: StrictBool = False

    @model_validator(mode='after')
    def composition_inputs(self) -> 'MusicInput':
        if self.mode == 'simple':
            if not self.prompt.strip():
                raise ValueError('Describe the song you want to create')
            if self.lyrics.strip():
                raise ValueError('Choose Custom mode to use exact lyrics')
        else:
            if self.model == MUSIC_MODEL_ID and self.duration > 120:
                raise ValueError('Custom music supports 3 to 120 seconds')
            if self.instrumental:
                if self.lyrics.strip():
                    raise ValueError('Remove lyrics when creating an instrumental')
                if not (self.styles.strip() or self.prompt.strip()):
                    raise ValueError('Describe the instrumental style')
            elif not self.lyrics.strip():
                raise ValueError('Enter exact lyrics or choose Instrumental')
            # A composition plan has no separate global prompt. Reject rather
            # than silently dropping instructions the person reviewed.
            elif self.prompt.strip():
                raise ValueError('Use Styles for custom music directions; leave Song description empty')
        if self.mode == 'simple' or self.instrumental:
            if len(self.provider_prompt()) > 4100:
                raise ValueError('Song description and styles together must fit within 4100 characters')
        elif self.model == MUSIC_MODEL_ID:
            if len(self.style_list(self.styles)) > 50 or len(self.style_list(self.negative_styles)) > 50:
                raise ValueError('Choose at most 50 styles and 50 excluded styles')
            _ = self.composition_chunks()
        from termx.media.studio_music_catalog import OPENROUTER_MUSIC_MODELS
        spec = next((row for row in OPENROUTER_MUSIC_MODELS if row['id'] == self.model), None)
        if spec is not None and not cast(int, spec['duration_min']) <= self.duration <= cast(int, spec['duration_max']):
            raise ValueError('Choose a duration supported by the selected music model')
        return self

    @staticmethod
    def style_list(value: str) -> list[str]:
        return [style.strip() for style in value.split(',') if style.strip()]

    def provider_prompt(self) -> str:
        parts = [self.prompt] if self.prompt else []
        if self.styles.strip():
            parts.append('Styles: ' + self.styles)
        if self.negative_styles.strip():
            parts.append('Avoid these styles: ' + self.negative_styles)
        return '\n\n'.join(parts)

    def payload(self) -> dict[str, object]:
        if self.mode == 'custom' and not self.instrumental:
            return {'model_id': 'music_v2_5', 'composition_plan': {'chunks': self.composition_chunks()}}
        return {'model_id': 'music_v2_5', 'prompt': self.provider_prompt(),
                'music_length_ms': self.duration * 1000, 'force_instrumental': self.instrumental}

    def composition_chunks(self) -> list[dict[str, object]]:
        # Every original character belongs to exactly one chunk. Section tags
        # begin provider chunks, so they guide structure instead of being added
        # as sung words. Never trim, truncate or invent reviewed lyric lines.
        markers = list(re.finditer(r'(?m)^\[([^\]\r\n]*)\]', self.lyrics))
        for marker in markers:
            if not marker.group(1).strip() or len(marker.group(1)) > 100:
                raise ValueError('Section names must contain 1 to 100 characters')
        boundaries = [0] + [marker.start() for marker in markers if marker.start() > 0] + [len(self.lyrics)]
        sections = [self.lyrics[start:end] for start, end in zip(boundaries, boundaries[1:])]
        if len(sections) > 30:
            raise ValueError('Custom music supports at most 30 sections')
        if self.duration * 1000 < len(sections) * 3000:
            raise ValueError('Allow at least 3 seconds for each lyric section')
        for section in sections:
            lines = section.splitlines()
            if len(lines) > 30 or any(len(line) > 200 for line in lines):
                raise ValueError('Each lyric section supports at most 30 lines and 200 characters per line')
        duration, remainder = divmod(self.duration * 1000, len(sections))
        return [{'text': section, 'duration_ms': duration + (1 if index < remainder else 0),
                 'positive_styles': self.style_list(self.styles),
                 'negative_styles': self.style_list(self.negative_styles), 'context_adherence': 'high'}
                for index, section in enumerate(sections)]

    def parameters(self) -> dict[str, object]:
        return {'operation': 'music', 'music_mode': self.mode, 'lyrics': self.lyrics,
                'styles': self.styles, 'negative_styles': self.negative_styles,
                'title': self.title, 'instrumental': self.instrumental, 'duration': self.duration}


def valid_mp3(blob: bytes) -> bool:
    """Require complete Layer III frames, with bounded optional ID3 metadata.

    This checks container structure rather than decoding untrusted media or
    treating the ID3 metadata marker alone as proof of a valid audio stream.
    """
    offset = 0
    if blob.startswith(b'ID3'):
        if len(blob) < 10 or blob[3] not in (2, 3, 4) or blob[4] == 255 or any(byte & 128 for byte in blob[6:10]):
            return False
        tag_size = sum(byte << shift for byte, shift in zip(blob[6:10], (21, 14, 7, 0)))
        offset = 10 + tag_size + (10 if blob[3] == 4 and blob[5] & 16 else 0)
    frames = 0
    sample_format: tuple[int, int] | None = None
    bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
    low_bitrates = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    while offset < len(blob):
        if len(blob) - offset == 128 and blob[offset:offset + 3] == b'TAG':
            offset += 128
            break
        if offset + 4 > len(blob):
            return False
        header = int.from_bytes(blob[offset:offset + 4], 'big')
        version, layer = (header >> 19) & 3, (header >> 17) & 3
        bitrate_index, rate_index = (header >> 12) & 15, (header >> 10) & 3
        if (header >> 21 != 2047 or version == 1 or layer != 1 or bitrate_index in (0, 15)
                or rate_index == 3 or header & 3 == 2):
            return False
        rate = (44100, 48000, 32000)[rate_index] // (1 if version == 3 else (2 if version == 2 else 4))
        current_format = (version, rate)
        if sample_format is not None and sample_format != current_format:
            return False
        sample_format = current_format
        bitrate = (bitrates if version == 3 else low_bitrates)[bitrate_index]
        frame_size = (144000 if version == 3 else 72000) * bitrate // rate + ((header >> 9) & 1)
        if frame_size < 24 or offset + frame_size > len(blob):
            return False
        offset += frame_size
        frames += 1
    return offset == len(blob) and frames >= 3


class MusicArtifacts(Protocol):
    def create(self, owner: str, project: str | None, kind: str, title: str, *,
               blob: bytes, mime: str) -> Mapping[str, object]: ...


class ElevenLabsMusicAdapter:
    def __init__(self, artifacts: MusicArtifacts, client: httpx.AsyncClient | None = None) -> None:
        self.artifacts: MusicArtifacts = artifacts
        self.client: httpx.AsyncClient | None = client

    async def submit(self, body: MusicInput, key: str, owner: str, *,
                     before_submit: Callable[[], None] | None = None) -> dict[str, object]:
        own_client = self.client is None
        client = self.client or httpx.AsyncClient()
        try:
            if before_submit is not None:
                before_submit()
            async with client.stream('POST', 'https://api.elevenlabs.io/v1/music',
                    params={'output_format': 'mp3_48000_192'},
                    headers={'xi-api-key': key, 'Accept': 'audio/mpeg'},
                    json=body.payload(), timeout=httpx.Timeout(660, connect=20), follow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    uncertain = response.status_code >= 500 or response.status_code in {408, 499} or response.status_code < 400
                    raise OpenRouterError('ElevenLabs rejected or could not confirm this music request. Check the selected API account, music access and quota.', uncertain=uncertain)
                mime = cast(str, response.headers.get('content-type', '')).split(';', 1)[0].strip().lower()
                if mime not in {'audio/mpeg', 'audio/mp3'}:
                    raise OpenRouterError('ElevenLabs returned an unexpected audio format. Inspect the provider account; this request will not be resubmitted.', uncertain=True)
                size = 0
                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > LIMIT:
                        raise OpenRouterError('Music was generated but its audio exceeds the 100 MiB media limit. Inspect the provider account before creating another song.', uncertain=True)
                    chunks.append(chunk)
                blob = b''.join(chunks)
                # The requested codec is MP3. Do not persist an HTML/JSON error
                # page or an arbitrary provider content type as playable audio.
                if not valid_mp3(blob):
                    raise OpenRouterError('ElevenLabs returned no valid MP3 audio. Inspect the provider account; this request will not be resubmitted.', uncertain=True)
            try:
                item = self.artifacts.create(owner, body.project_id, 'audio', body.title.strip() or 'Generated music', blob=blob, mime='audio/mpeg')
            except Exception as exc:
                raise OpenRouterError('Music was generated but could not be saved on this host. Inspect this job and the provider account before creating another song.', uncertain=True) from exc
            return {'status': 'completed', 'outputs': [f'/api/media/artifacts/{item["id"]}/export?format=original']}
        except httpx.RequestError as exc:
            raise OpenRouterError('Music submission could not be confirmed. Inspect the provider account; this request will not be resubmitted.', uncertain=True) from exc
        finally:
            if own_client:
                await client.aclose()

