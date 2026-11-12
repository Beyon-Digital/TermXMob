"""OpenRouter visual media transport with bounded, locally stored outputs.

Discovery and generation use the dedicated public Image and Video APIs. Only
fixed API paths are fetched; provider-supplied polling and download URLs are
never followed. The caller owns authorization, billing consent, and durable
submission/idempotency state.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from termx.media.service import ArtifactService, LIMIT

BASE_URL = 'https://openrouter.ai/api/v1'
DISCOVERY_LIMIT = 1024 * 1024
PIXEL_LIMIT = 32_000_000
_TASK_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z')
_IMAGE_MIMES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}
_PROVIDER_TAG = re.compile(r'[a-z0-9][a-z0-9._/-]{0,100}\Z')
_IMAGE_REFERENCE_DOC = 'https://openrouter.ai/docs/guides/overview/multimodal/image-generation'
_VIDEO_REFERENCE_DOC = 'https://github.com/OpenRouterTeam/skills/blob/main/skills/openrouter-video/SKILL.md'
_VIDEO_SCHEMA_DOC = 'https://openrouter.ai/docs/api/api-reference/video-generation/submit-a-video-generation-request'
_IMAGE_SCHEMA_DOC = 'https://openrouter.ai/docs/api/api-reference/images/generate-an-image'
_INLINE_TOTAL_LIMIT = 20 * 1024 * 1024
_APP_IMAGE_REFERENCES = 5


@dataclass(frozen=True)
class VideoContract:
    """Documented request semantics, still requiring live endpoint verification."""

    name: str
    providers: tuple[str, ...]
    inputs: frozenset[str]
    passthrough: frozenset[str]
    documentation: str


_VIDEO_CONTRACTS = {
    'heygen/avatar-iv': VideoContract('avatar', ('heygen',), frozenset({'image', 'audio'}),
        frozenset({'motion_prompt'}), 'https://openrouter.ai/heygen/avatar-iv'),
    'black-forest-labs/flux-video-edit': VideoContract('edit', ('black-forest-labs',),
        frozenset({'text', 'video'}), frozenset({'safety_tolerance'}),
        'https://openrouter.ai/black-forest-labs/flux-video-edit'),
    'runway/aleph-2': VideoContract('edit', ('runway',), frozenset({'text', 'video'}),
        frozenset({'keyframes'}), 'https://openrouter.ai/runway/aleph-2'),
    'bytedance/seedance-2.5': VideoContract('references', ('seed', 'byteplus'),
        frozenset({'text', 'image', 'audio', 'video'}), frozenset({'watermark'}),
        'https://openrouter.ai/blog/insights/seedance-2-5-review/'),
    'bytedance/seedance-2.0': VideoContract('references', ('seed', 'byteplus'),
        frozenset({'text', 'image', 'audio', 'video'}), frozenset({'watermark'}),
        'https://openrouter.ai/bytedance/seedance-2.0'),
    'bytedance/seedance-2.0-fast': VideoContract('references', ('seed', 'byteplus'),
        frozenset({'text', 'image', 'audio', 'video'}), frozenset({'watermark'}),
        'https://openrouter.ai/bytedance/seedance-2.0-fast'),
    'bytedance/seedance-2.0-mini': VideoContract('references', ('seed', 'byteplus'),
        frozenset({'text', 'image', 'audio', 'video'}), frozenset({'watermark'}),
        'https://openrouter.ai/bytedance/seedance-2.0-mini'),
}


def _model_id(value: object) -> bool:
    return (isinstance(value, str) and _MODEL_ID.fullmatch(value) is not None
            and all(part not in {'', '.', '..'} for part in value.split('/')))


class OpenRouterError(Exception):
    def __init__(self, message: str, uncertain: bool = True):
        super().__init__(message)
        self.message = message
        self.uncertain = uncertain


def _invalid(message: str = 'OpenRouter returned an invalid response') -> OpenRouterError:
    return OpenRouterError(message)


def _status_error(status: int) -> OpenRouterError:
    if status in {401, 403}:
        return OpenRouterError('OpenRouter did not accept this API account or model access. Check the account in Models & engines.', False)
    if status == 402:
        return OpenRouterError('OpenRouter requires sufficient API credits for this request.', False)
    if status == 429:
        return OpenRouterError('OpenRouter is limiting requests. Check the account quota before submitting another job.', False)
    if status in {408, 499}:
        return OpenRouterError('OpenRouter timed out or was cancelled without confirming the request outcome. Check this job before submitting another one.')
    if 400 <= status < 500:
        return OpenRouterError('OpenRouter rejected the request. Check the selected model and its supported settings.', False)
    return OpenRouterError('OpenRouter did not confirm the request outcome. Check this job before submitting another one.')


def _json(raw: bytes | bytearray, *, job: bool = False) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
    if not isinstance(value, dict):
        raise _invalid()
    has_receipt = job and isinstance(value.get('id'), str) and _TASK_ID.fullmatch(value['id']) and 'status' in value
    if value.get('error') and not has_receipt:
        error = value['error']
        code = error.get('code') if isinstance(error, dict) else None
        if isinstance(code, int) and not isinstance(code, bool) and 400 <= code < 600:
            raise _status_error(code)
        known_refusal = code in {'invalid_request_error', 'content_policy_violation', 'insufficient_quota'} if isinstance(code, str) else False
        raise OpenRouterError('OpenRouter could not complete the request. Check the account and supported model settings.', not known_refusal)
    return cast(dict[str, Any], value)


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(item, str) or not item or len(item) > 100 for item in value):
        raise _invalid('OpenRouter returned invalid model capabilities')
    return list(dict.fromkeys(cast(list[str], value)))


def _enum(parameters: dict[str, Any], name: str) -> list[str]:
    value = parameters.get(name)
    if value is None:
        return []
    if not isinstance(value, dict) or value.get('type') != 'enum':
        raise _invalid('OpenRouter returned invalid model capabilities')
    return _strings(value.get('values'))


def _reference(url: object) -> str:
    """Only explicit public HTTPS references are passed through to the provider."""
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url) or '\\' in url:
        raise OpenRouterError('A reference must be a public HTTPS URL.', False)
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid')) or '.' not in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError()
    except ValueError as exc:
        raise OpenRouterError('A reference must be a public HTTPS URL.', False) from exc
    return url


class OpenRouterAdapter:
    def __init__(self, artifacts: ArtifactService, client: httpx.AsyncClient | None = None) -> None:
        self.artifacts = artifacts
        self.client = client

    async def _request(self, method: str, path: str, *, key: str | None = None,
                       payload: dict[str, Any] | None = None, limit: int = DISCOVERY_LIMIT,
                       timeout: float = 300) -> tuple[bytes, httpx.Headers]:
        # A supplied test/client configuration cannot enable redirects or inject
        # credentials into public discovery through default auth/headers.
        client = self.client or httpx.AsyncClient(timeout=300, follow_redirects=False)
        headers = {'Accept': 'video/mp4' if '/content?' in path else 'application/json'}
        response = None
        try:
            request = client.build_request(method, BASE_URL + path, headers=headers,
                                           json=payload, timeout=timeout)
            request.headers.pop('authorization', None)
            request.headers.pop('cookie', None)
            if key:
                request.headers['Authorization'] = f'Bearer {key}'
            response = await client.send(request, auth=None, follow_redirects=False, stream=True)
            if not 200 <= response.status_code < 300:
                raise _status_error(response.status_code)
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > limit:
                    raise OpenRouterError('OpenRouter response exceeds the media limit.')
                raw.extend(chunk)
            return bytes(raw), response.headers
        except httpx.RequestError as exc:
            raise OpenRouterError('OpenRouter did not confirm the request outcome. Check this job before submitting another one.') from exc
        finally:
            if response is not None:
                await response.aclose()
            if self.client is None:
                await client.aclose()

    async def discover(self) -> list[dict[str, Any]]:
        try:
            async with asyncio.timeout(8):
                return await self._discover()
        except TimeoutError as exc:
            raise OpenRouterError('OpenRouter model discovery timed out.') from exc

    async def discover_music(self) -> list[dict[str, Any]]:
        from termx.media.studio_music_catalog import openrouter_music_models
        try:
            async with asyncio.timeout(8):
                raw, _ = await self._request('GET', '/models?output_modalities=audio', timeout=8)
                return openrouter_music_models(_json(raw).get('data'))
        except (TimeoutError, ValueError) as exc:
            raise OpenRouterError('OpenRouter music discovery could not be confirmed.') from exc

    async def _discover(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        video_architectures: dict[str, dict[str, Any]] = {}
        try:
            raw, _ = await self._request('GET', '/models?output_modalities=video', timeout=4)
            inventory = _json(raw).get('data')
            if not isinstance(inventory, list) or len(inventory) > 1000:
                raise _invalid('OpenRouter returned an invalid video model inventory')
            for entry in inventory:
                if isinstance(entry, dict) and isinstance(entry.get('id'), str) and isinstance(entry.get('architecture'), dict):
                    video_architectures[entry['id']] = entry['architecture']
        except OpenRouterError:
            # Image discovery and independently verified special video modes
            # can still work if this public inventory is temporarily absent.
            pass
        for kind in ('image', 'video'):
            raw, _ = await self._request('GET', f'/{kind}s/models', timeout=8)
            value = _json(raw)
            entries = value.get('data')
            if not isinstance(entries, list) or len(entries) > 1000:
                raise _invalid('OpenRouter returned an invalid model catalog')
            for item in entries:
                if not isinstance(item, dict) or not _model_id(item.get('id')):
                    raise _invalid('OpenRouter returned an invalid model catalog')
                # Dedicated edit/upscale/motion variants need source-specific
                # payloads this adapter does not implement. Do not advertise
                # them as prompt-only video generation just because listed.
                explicit_text = item.get('supports_text_to_video') is True or item.get('supports_text_only') is True
                source_only = re.search(r'(^|[/_.-])(edit|upscale|upscaler|extend|extension|motion[-_]control|animate|aleph|mischief|video[-_]to[-_]video|image[-_]to[-_]video|reference[-_]to[-_]video)([/_.-]|$)', item['id'], re.IGNORECASE)
                contract = _VIDEO_CONTRACTS.get(item['id']) if kind == 'video' else None
                if kind == 'video' and not contract and (item.get('requires_video') is True or (source_only and not explicit_text)):
                    continue
                if not isinstance(item.get('name'), str) or not item['name'].strip():
                    raise _invalid('OpenRouter returned an invalid model catalog')
                description = item.get('description', '')
                if not isinstance(description, str):
                    raise _invalid('OpenRouter returned an invalid model catalog')
                architecture = item.get('architecture', {}) if kind == 'image' else video_architectures.get(item['id'], {})
                if not isinstance(architecture, dict):
                    raise _invalid('OpenRouter returned invalid model capabilities')
                parameters = item.get('supported_parameters', {})
                if not isinstance(parameters, dict):
                    raise _invalid('OpenRouter returned invalid model capabilities')
                references = parameters.get('input_references', {})
                if not isinstance(references, dict):
                    raise _invalid('OpenRouter returned invalid model capabilities')
                maximum = references.get('max', 0)
                minimum = references.get('min', 0)
                if not isinstance(maximum, int) or isinstance(maximum, bool) or not 0 <= maximum <= 100:
                    raise _invalid('OpenRouter returned invalid model capabilities')
                if not isinstance(minimum, int) or isinstance(minimum, bool) or not 0 <= minimum <= maximum:
                    raise _invalid('OpenRouter returned invalid model capabilities')
                # Composition accepts at most five ordered references. Raster
                # outputs remain mandatory; unsupported minimums are excluded.
                if kind == 'image':
                    formats = _enum(parameters, 'output_format')
                    vector_only = re.search(r'(^|[/_.-])vector(?:ize|ization)?([/_.-]|$)', item['id'], re.IGNORECASE)
                    if minimum > _APP_IMAGE_REFERENCES or vector_only or (formats and not set(formats) & {'png', 'jpeg', 'jpg', 'webp'}):
                        continue
                frames = item.get('supported_frame_images') or []
                if not isinstance(frames, list) or any(not isinstance(frame, str) for frame in frames):
                    raise _invalid('OpenRouter returned invalid frame image capabilities')
                first_frame = kind == 'video' and 'first_frame' in frames
                inputs = architecture.get('input_modalities', [])
                if not isinstance(inputs, list) or any(not isinstance(v, str) for v in inputs):
                    raise _invalid('OpenRouter returned invalid model capabilities')
                if kind == 'image':
                    aspects, resolutions, durations = _enum(parameters, 'aspect_ratio'), _enum(parameters, 'resolution'), []
                    outputs = architecture.get('output_modalities', [])
                    if not isinstance(outputs, list) or 'image' not in outputs or 'text' not in inputs:
                        continue
                else:
                    outputs = architecture.get('output_modalities', [])
                    if not contract and (not isinstance(outputs, list) or 'video' not in outputs or 'text' not in inputs):
                        continue
                    aspects = _strings(item.get('supported_aspect_ratios') or [])
                    resolutions = _strings(item.get('supported_resolutions') or [])
                    durations = item.get('supported_durations') or []
                    if not isinstance(durations, list) or len(durations) > 300 or any(type(v) is not int or not 1 <= v <= 600 for v in durations):
                        raise _invalid('OpenRouter returned invalid model durations')
                    durations = sorted(set(durations))
                model: dict[str, Any] = {'id': item['id'], 'label': item['name'][:200],
                               'description': description[:2000], 'kind': kind,
                               'modes': ['image', 'cinema'] if kind == 'image' else ['video'],
                               'aspect_ratios': aspects, 'resolutions': resolutions, 'durations': durations,
                               'adapter': 'openrouter',
                               '_supports_image': (maximum > 0 and (not inputs or 'image' in inputs)) if kind == 'image' else first_frame}
                model['image_roles'] = ['first_frame'] if first_frame else []
                model['_basic_verified'] = kind == 'image' or ('video' in outputs and 'text' in inputs)
                model['_inline_inputs'] = ['image'] if model['_supports_image'] else []
                model['capability_evidence'] = [BASE_URL + f'/{kind}s/models']
                if kind == 'image':
                    # The normalized Image API caps the array at sixteen even
                    # when a model's native catalog advertises a larger limit.
                    effective_maximum = min(maximum, 16)
                    model['image_reference_limits'] = {'min': minimum, 'max': effective_maximum,
                        'app_max': min(_APP_IMAGE_REFERENCES, effective_maximum)}
                    model['capability_evidence'] += [BASE_URL + '/images/models/' + model['id'] + '/endpoints', _IMAGE_SCHEMA_DOC]
                    model['pricing_evidence'] = [BASE_URL + '/images/models/' + model['id'] + '/endpoints']
                if kind == 'video' and architecture:
                    model['capability_evidence'].append(BASE_URL + '/models?output_modalities=video')
                model['_allowed_passthrough'] = _strings(item.get('allowed_passthrough_parameters') or [])
                if kind == 'image' and model['_supports_image']:
                    model['capability_evidence'].append(_IMAGE_REFERENCE_DOC)
                elif first_frame:
                    model['capability_evidence'].append(_VIDEO_REFERENCE_DOC)
                if kind == 'image' and minimum:
                    model['requires_image'] = True
                if first_frame:
                    model['_supports_first_frame'] = True
                models.append(model)
        if len({(m['kind'], m['id']) for m in models}) != len(models):
            raise _invalid('OpenRouter returned duplicate model entries')
        # Model names alone never enable audio/video references or a new mode.
        # These paths require both a documented contract and matching current
        # architecture, provider identity, and passthrough metadata.
        semaphore = asyncio.Semaphore(4)

        async def enrich(model: dict[str, Any]) -> dict[str, Any] | None:
            contract = _VIDEO_CONTRACTS.get(model['id'])
            allowed = set(model['_allowed_passthrough'])
            negative_key = 'negative_prompt' if 'negative_prompt' in allowed else 'negativePrompt' if 'negativePrompt' in allowed else None
            if model['kind'] != 'video' or (not contract and not negative_key):
                return model
            try:
                async with semaphore:
                    raw, _ = await self._request('GET', '/models/' + model['id'] + '/endpoints', timeout=4)
                proof = _json(raw).get('data')
                if not isinstance(proof, dict) or proof.get('id') != model['id']:
                    raise _invalid('OpenRouter endpoint identity could not be verified')
                architecture = proof.get('architecture')
                endpoints = proof.get('endpoints')
                if not isinstance(architecture, dict) or not isinstance(endpoints, list):
                    raise _invalid('OpenRouter endpoint capabilities could not be verified')
                inputs = set(_strings(architecture.get('input_modalities') or []))
                outputs = set(_strings(architecture.get('output_modalities') or []))
                tags = {entry.get('tag') for entry in endpoints if isinstance(entry, dict) and
                        isinstance(entry.get('tag'), str) and _PROVIDER_TAG.fullmatch(entry['tag'])}
                if 'video' not in outputs:
                    raise _invalid('OpenRouter endpoint does not declare video output')
                if contract:
                    providers = [tag for tag in contract.providers if tag in tags]
                    if not providers or not contract.inputs <= inputs or not contract.passthrough <= allowed:
                        raise _invalid('OpenRouter no longer declares this operation contract')
                    model.update(_contract_verified=True, _contract=contract.name,
                                 _provider_pin=providers[0], _contract_inputs=sorted(inputs))
                    model['capability_evidence'] += [BASE_URL + '/models/' + model['id'] + '/endpoints', contract.documentation, _VIDEO_SCHEMA_DOC]
                    if contract.name == 'avatar':
                        model.update(modes=['lipsync'], requires_image=True, requires_audio=True,
                                     durations=[], _supports_image=True, _supports_audio=True,
                                     image_roles=['reference'], _supports_reference_image=True,
                                     _inline_inputs=['image'])
                    elif contract.name == 'edit':
                        # Aleph keyframes are deliberately not conflated with
                        # ordinary reference images: only video+prompt is wired.
                        model.update(modes=['remix'], requires_video=True, _supports_video=True,
                                     _supports_image=False, image_roles=[], _inline_inputs=[])
                    elif contract.name == 'references':
                        model.update(modes=['video', 'remix'], _supports_reference_image=True,
                                     _supports_image=True, _supports_audio=True, _supports_video=True,
                                     _requires_video_for_remix=True, _inline_inputs=['image'])
                        model['image_roles'] = (['first_frame'] if model.get('_supports_first_frame') else []) + ['reference']
                # Unioned model options cannot establish which of several
                # endpoints implements a negative prompt. Pin the sole proven
                # endpoint, or the already verified contract's provider.
                provider = model.get('_provider_pin') or (next(iter(tags)) if len(tags) == 1 else None)
                if negative_key and provider and 'text' in inputs:
                    model.update(_supports_negative_prompt=True, _negative_key=negative_key,
                                 _provider_pin=provider)
                return model
            except (OpenRouterError, ValueError, TypeError):
                # A failed advanced proof cannot erase working basic discovery.
                # Source-only modes have no safe prompt-only fallback.
                return None if not model['_basic_verified'] or (contract and contract.name in {'avatar', 'edit'}) else model

        verified = await asyncio.gather(*(enrich(model) for model in models))
        return [model for model in verified if model is not None]

    @staticmethod
    def _settings(spec: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field, choices in (('aspect_ratio', 'aspect_ratios'), ('resolution', 'resolutions'), ('duration', 'durations')):
            value = body.get(field)
            if value is None:
                continue
            if field == 'duration' and type(value) is not int:
                raise OpenRouterError('Choose a supported duration for this model.', False)
            if value not in spec.get(choices, []):
                raise OpenRouterError(f'Choose a supported {field.replace("_", " ")} for this model.', False)
            result[field] = value
        for kind in ('audio', 'video'):
            if body.get(kind + '_url') and not spec.get('_supports_' + kind):
                raise OpenRouterError(f'This model does not declare {kind} reference support.', False)
        return result

    @staticmethod
    def _inline_image_reference(spec: dict[str, Any], entry: object) -> str:
        if 'image' not in spec.get('_inline_inputs', []):
            raise OpenRouterError('This model needs a public HTTPS image URL; local image input is not verified.', False)
        if not isinstance(entry, dict) or entry.get('mime') not in _IMAGE_MIMES.values():
            raise OpenRouterError('The local media reference has an unsupported format.', False)
        url = entry.get('data_url')
        prefix = 'data:' + entry['mime'] + ';base64,'
        if not isinstance(url, str) or not url.startswith(prefix) or len(url) > 12 * 1024 * 1024:
            raise OpenRouterError('The local image reference is invalid or too large.', False)
        try:
            decoded = base64.b64decode(url[len(prefix):], validate=True)
            if not decoded or len(decoded) > 8 * 1024 * 1024:
                raise ValueError()
        except (ValueError, binascii.Error) as exc:
            raise OpenRouterError('The local image reference is invalid or too large.', False) from exc
        return url

    @staticmethod
    def _input_reference(spec: dict[str, Any], body: dict[str, Any], kind: str) -> str | None:
        """Only the service may resolve an owned artifact to an inline reference."""
        local = body.get('_local_references', {})
        if not isinstance(local, dict):
            raise OpenRouterError('The local media reference could not be read.', False)
        if kind not in local:
            value = body.get(kind + '_url')
            return _reference(value) if value else None
        if kind != 'image':
            raise OpenRouterError(f'This model needs a public HTTPS {kind} URL; local {kind} input is not verified.', False)
        return OpenRouterAdapter._inline_image_reference(spec, local[kind])

    @staticmethod
    def _image_references(spec: dict[str, Any], body: dict[str, Any]) -> list[str]:
        requested = body.get('image_references')
        if requested is not None and (body.get('image_url') or body.get('image_id')):
            raise OpenRouterError('Choose either ordered image references or the legacy image field.', False)
        entries = body.get('_local_image_references')
        if entries is None and requested is not None:
            if not isinstance(requested, list):
                raise OpenRouterError('Image references must be an ordered list.', False)
            entries = []
            for item in requested:
                if not isinstance(item, dict) or set(item) != {'image_url'}:
                    raise OpenRouterError('Owned image references must be resolved by this workspace before generation.', False)
                entries.append({'url': item['image_url']})
        if entries is None:
            if body.get('image_id') and 'image' not in body.get('_local_references', {}):
                raise OpenRouterError('Owned image references must be resolved by this workspace before generation.', False)
            image = OpenRouterAdapter._input_reference(spec, body, 'image')
            images = [image] if image else []
        else:
            if not isinstance(entries, list) or not 1 <= len(entries) <= _APP_IMAGE_REFERENCES:
                raise OpenRouterError('Choose between one and five ordered image references.', False)
            if isinstance(requested, list) and len(entries) != len(requested):
                raise OpenRouterError('The workspace did not resolve every ordered image reference.', False)
            images = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise OpenRouterError('The image reference could not be read.', False)
                if set(entry) == {'url'}:
                    images.append(_reference(entry['url']))
                elif set(entry) == {'data_url', 'mime'}:
                    images.append(OpenRouterAdapter._inline_image_reference(spec, entry))
                else:
                    raise OpenRouterError('The image reference format is unsupported.', False)
        limits = spec.get('image_reference_limits', {})
        minimum = limits.get('min', 1 if spec.get('requires_image') else 0)
        maximum = min(_APP_IMAGE_REFERENCES, limits.get('app_max', limits.get('max', 1)))
        if not minimum <= len(images) <= maximum or (images and not spec.get('_supports_image')):
            raise OpenRouterError('The selected model does not support this number of image references.', False)
        if sum(len(image) for image in images if image.startswith('data:')) > _INLINE_TOTAL_LIMIT:
            raise OpenRouterError('Inline image references exceed the 20 MiB encoded limit.', False)
        return images

    async def _image_providers(self, spec: dict[str, Any], payload: dict[str, Any], count: int) -> list[str]:
        """A model-level union cannot prove one endpoint accepts the request."""
        try:
            async with asyncio.timeout(8):
                raw, _ = await self._request('GET', '/images/models/' + spec['id'] + '/endpoints', timeout=8)
            response = _json(raw)
            endpoints = response.get('endpoints')
            if response.get('id') != spec['id'] or not isinstance(endpoints, list) or len(endpoints) > 100:
                raise _invalid()
            providers: list[str] = []
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    continue
                tag, parameters = endpoint.get('provider_tag'), endpoint.get('supported_parameters')
                if not isinstance(tag, str) or not _PROVIDER_TAG.fullmatch(tag) or not isinstance(parameters, dict):
                    continue
                references = parameters.get('input_references', {'type': 'range', 'min': 0, 'max': 0})
                if not isinstance(references, dict) or references.get('type') != 'range':
                    continue
                low, high = references.get('min'), references.get('max')
                if type(low) is not int or type(high) is not int or not 0 <= low <= count <= high <= 100:
                    continue
                try:
                    if any(payload.get(field) is not None and payload[field] not in _enum(parameters, field)
                           for field in ('resolution', 'aspect_ratio')):
                        continue
                    formats = _enum(parameters, 'output_format')
                    if formats and not set(formats) & {'png', 'jpeg', 'jpg', 'webp'}:
                        continue
                except OpenRouterError:
                    continue
                outputs = parameters.get('n')
                if outputs is not None and (not isinstance(outputs, dict) or outputs.get('type') != 'range'
                    or type(outputs.get('min')) is not int or type(outputs.get('max')) is not int
                    or not outputs['min'] <= 1 <= outputs['max']):
                    continue
                if tag not in providers:
                    providers.append(tag)
            if not providers:
                raise OpenRouterError('No verified OpenRouter provider accepts this image count and output settings. No generation was submitted.', False)
            return providers
        except (OpenRouterError, TimeoutError) as exc:
            if isinstance(exc, OpenRouterError) and not exc.uncertain:
                raise
            raise OpenRouterError('OpenRouter image provider capabilities could not be verified. No generation was submitted; try again after checking availability.', False) from exc

    async def submit(self, spec: dict[str, Any], body: dict[str, Any], key: str,
                     owner: str, project: str | None, *,
                     before_submit: Callable[[], None] | None = None) -> dict[str, Any]:
        if not _model_id(spec.get('id')) or spec.get('kind') not in {'image', 'video'}:
            raise OpenRouterError('Select a discovered OpenRouter visual model.', False)
        if not isinstance(key, str) or not key.strip():
            raise OpenRouterError('Connect the selected OpenRouter API account on the host.', False)
        payload: dict[str, Any] = {'model': spec['id'], 'prompt': body.get('prompt', ''), **self._settings(spec, body)}
        contract = spec.get('_contract') if spec.get('_contract_verified') else None
        lipsync = contract == 'avatar'
        operation = body.get('operation') or ('lipsync' if lipsync else 'remix' if contract == 'edit' else spec['kind'])
        if operation in {'lipsync', 'remix'} and not contract:
            raise OpenRouterError('This advanced operation has no verified provider contract.', False)
        if operation not in spec.get('modes', [spec['kind']]):
            raise OpenRouterError('This model does not declare this studio operation.', False)
        if not isinstance(payload['prompt'], str) or (not lipsync and not payload['prompt'].strip()):
            raise OpenRouterError('Enter a prompt for this model.', False)
        if spec['kind'] != 'image' and (body.get('image_references') is not None or body.get('_local_image_references') is not None):
            raise OpenRouterError('Ordered image composition is supported only for image generation.', False)
        image_references = self._image_references(spec, body) if spec['kind'] == 'image' else []
        image = image_references[0] if image_references else self._input_reference(spec, body, 'image') if spec['kind'] == 'video' else None
        audio = self._input_reference(spec, body, 'audio')
        video = self._input_reference(spec, body, 'video')
        if spec.get('requires_image') and not image:
            raise OpenRouterError('This model requires a reference image HTTPS URL.', False)
        if (spec.get('requires_video') or operation == 'remix') and not video:
            raise OpenRouterError('Video remix requires a source video public HTTPS URL.', False)
        if image:
            if not spec.get('_supports_image'):
                raise OpenRouterError('This model does not declare image reference support.', False)
            reference = {'type': 'image_url', 'image_url': {'url': image}}
            role = body.get('image_role') or ('reference' if audio or video or lipsync else 'first_frame')
            if body.get('image_role') and (spec['kind'] != 'video' or role not in spec.get('image_roles', [])):
                raise OpenRouterError('This model does not declare this image role.', False)
            if role == 'first_frame' and (audio or video):
                raise OpenRouterError('First-frame mode cannot be combined with audio or video references.', False)
            if spec['kind'] == 'video' and role == 'first_frame' and spec.get('_supports_first_frame') and not lipsync:
                payload['frame_images'] = [{**reference, 'frame_type': 'first_frame'}]
            else:
                if spec['kind'] == 'video' and not spec.get('_supports_reference_image'):
                    raise OpenRouterError('This model does not declare image reference mode.', False)
                payload['input_references'] = [reference]
        elif body.get('image_role'):
            raise OpenRouterError('Choose an image before setting its role.', False)
        for kind, value in (('audio', audio), ('video', video)):
            if value:
                if not spec.get('_supports_' + kind) or not spec.get('_contract_verified'):
                    raise OpenRouterError(f'This model has no verified {kind} reference contract.', False)
                payload.setdefault('input_references', []).append({'type': kind + '_url', kind + '_url': {'url': value}})
        provider_value = spec.get('_provider_pin')
        provider = provider_value if isinstance(provider_value, str) else None
        provider_config: dict[str, Any] = {}
        if provider:
            provider_config = {'only': [provider], 'allow_fallbacks': False}
        direction = payload['prompt'].strip() if lipsync else ''
        options: dict[str, Any] = {}
        if direction:
            if not provider or 'motion_prompt' not in spec.get('_allowed_passthrough', []):
                raise OpenRouterError('This endpoint does not declare performance direction support.', False)
            options['motion_prompt'] = direction
        negative = body.get('negative_prompt')
        if negative:
            key_name = spec.get('_negative_key')
            if not isinstance(negative, str) or len(negative) > 2000 or not spec.get('_supports_negative_prompt') or not provider or not isinstance(key_name, str) or key_name not in spec.get('_allowed_passthrough', []):
                raise OpenRouterError('This endpoint does not declare negative prompt support.', False)
            options[key_name] = negative.strip()
        if options:
            if spec.get('_negative_key') == 'negativePrompt' and str(spec['id']).startswith('google/veo-'):
                options = {'parameters': options}
            provider_config['options'] = {provider: options}
        if provider_config:
            payload['provider'] = provider_config
        if lipsync:
            if not image or not audio:
                raise OpenRouterError('Avatar IV requires a portrait image and an audio HTTPS URL.', False)
            # With supplied audio, the UI prompt directs the performance; it
            # must not become an alternative text-to-speech script.
            payload['prompt'] = ''
        if spec['kind'] == 'image':
            payload['n'] = 1
            if image_references:
                payload['input_references'] = [{'type': 'image_url', 'image_url': {'url': value}} for value in image_references]
            providers = await self._image_providers(spec, payload, len(image_references))
            payload['provider'] = {'only': providers, 'allow_fallbacks': False}
            # Discovery may await public I/O. Recheck the original actor at the
            # actual paid-effect boundary; a denial must propagate unchanged.
            if before_submit is not None:
                before_submit()
            raw, _ = await self._request('POST', '/images', key=key, payload=payload, limit=LIMIT)
            images = _json(raw).get('data')
            if not isinstance(images, list) or not 1 <= len(images) <= 10:
                raise _invalid('OpenRouter did not return a generated image. Check this job before submitting another one.')
            validated = [self._bitmap(image) for image in images]
            if sum(len(blob) for blob, _ in validated) > LIMIT:
                raise OpenRouterError('OpenRouter images exceed the media limit.')
            outputs = [self._store(owner, project, 'image', payload['prompt'], blob, mime) for blob, mime in validated]
            return {'status': 'completed', 'outputs': outputs}
        if before_submit is not None:
            before_submit()
        raw, _ = await self._request('POST', '/videos', key=key, payload=payload)
        response = _json(raw, job=True)
        task_id = self._task_id(response.get('id'))
        status = response.get('status')
        if status not in {'pending', 'in_progress'}:
            # Keep a confirmed ID even when an unusually fast job is terminal;
            # refresh performs the sole bounded content retrieval path.
            if status == 'completed':
                return {'status': 'queued', 'task_id': task_id}
            if status in {'failed', 'cancelled', 'expired'}:
                return {'status': 'failed', 'task_id': task_id, 'error': 'OpenRouter could not complete this video.'}
            return {'status': 'unknown', 'task_id': task_id, 'error': 'OpenRouter returned an unknown video status. Refresh this job before submitting another one.'}
        return {'status': 'queued' if status == 'pending' else 'running', 'task_id': task_id}

    @staticmethod
    def _task_id(task_id: object) -> str:
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise _invalid('OpenRouter did not return a valid job identifier.')
        return task_id

    @staticmethod
    def _bitmap(value: object) -> tuple[bytes, str]:
        if not isinstance(value, dict) or not isinstance(value.get('b64_json'), str):
            raise _invalid('OpenRouter returned an invalid image.')
        encoded = value['b64_json']
        if len(encoded) > ((LIMIT + 2) // 3) * 4:
            raise OpenRouterError('OpenRouter image exceeds the media limit.')
        declared = value.get('media_type')
        if declared is not None and declared not in _IMAGE_MIMES.values():
            raise _invalid('OpenRouter returned an unsupported image format.')
        try:
            blob = base64.b64decode(encoded, validate=True)
            if not blob or len(blob) > LIMIT:
                raise ValueError()
            with Image.open(io.BytesIO(blob), formats=list(_IMAGE_MIMES)) as image:
                if image.width * image.height > PIXEL_LIMIT or getattr(image, 'n_frames', 1) != 1:
                    raise ValueError()
                image_format = image.format
                if image_format is None:
                    raise ValueError()
                mime = _IMAGE_MIMES[image_format]
                if declared is not None and declared != mime:
                    raise ValueError()
                image.load()
        except (ValueError, KeyError, OSError, binascii.Error, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise _invalid('OpenRouter returned an invalid image or one above the pixel limit.') from exc
        return blob, mime

    def _store(self, owner: str, project: str | None, kind: str, title: str,
               blob: bytes, mime: str) -> str:
        try:
            item = self.artifacts.create(owner, project, kind, title[:200], blob=blob, mime=mime)
        except Exception as exc:
            raise OpenRouterError('Generation completed but its output could not be stored. Check this job before submitting another one.') from exc
        return f'/api/media/artifacts/{item["id"]}/export?format=original'

    async def refresh(self, task_id: str, key: str, owner: str, project: str | None) -> dict[str, Any]:
        task_id = self._task_id(task_id)
        raw, _ = await self._request('GET', '/videos/' + task_id, key=key)
        response = _json(raw, job=True)
        if response.get('id', task_id) != task_id:
            raise _invalid('OpenRouter returned a different job identifier.')
        status = response.get('status')
        if status in {'pending', 'in_progress'}:
            return {'status': 'queued' if status == 'pending' else 'running', 'task_id': task_id}
        if status in {'failed', 'cancelled', 'expired'}:
            return {'status': 'failed', 'task_id': task_id, 'error': 'OpenRouter could not complete this video.'}
        if status != 'completed':
            raise _invalid('OpenRouter returned an unknown video status.')
        # Construct this authenticated path ourselves. Never fetch polling_url,
        # unsigned_urls, or signed urls supplied in a provider response.
        blob, headers = await self._request('GET', '/videos/' + task_id + '/content?index=0', key=key, limit=LIMIT)
        mime = headers.get('content-type', '').partition(';')[0].strip().lower()
        if mime not in {'', 'application/octet-stream', 'video/mp4'} or len(blob) < 12 or blob[4:8] != b'ftyp':
            raise _invalid('OpenRouter returned an invalid MP4 video.')
        output = self._store(owner, project, 'video', 'Generated video', blob, 'video/mp4')
        return {'status': 'completed', 'task_id': task_id, 'outputs': [output]}
