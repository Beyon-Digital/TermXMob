"""Public, bounded published media rates. These are never generation quotes."""
from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Annotated, Literal, NotRequired, Protocol, TypedDict, cast

from fastapi import APIRouter, HTTPException, Query, Request, Response

_API = 'https://openrouter.ai/api/v1'
_LIMIT = 1024 * 1024
_TIMEOUT = 4.0
_TTL = 300
_MAX_MODELS = 100
_MAX_RATES = 200
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*){1,3}\Z')
_VARIANT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,79}\Z')
_NUMBER = re.compile(r'[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,2})?\Z')
_VIDEO_SECONDS = re.compile(r'duration_seconds(?:_(with_audio|without_audio)(?:_(480p|720p|1080p|4k))?)?\Z')
_VIDEO_MODE_SECONDS = re.compile(r'(text_to_video|image_to_video)_duration_seconds_(480p|720p|1080p|4k)\Z')
_VIDEO_TOKENS = re.compile(r'video_tokens(?:_(4k|1080p))?(?:_(without_audio|with_video_input))?\Z')
_UNAVAILABLE = 'Published rates are unavailable. Check the provider before generating.'
_PARTIAL = 'Some published pricing fields are unsupported and have been omitted. Actual charges depend on inputs and settings.'


class PublishedRate(TypedDict):
    provider: str
    billable: str
    unit: str
    cost_usd: str
    variant: NotRequired[str]


class PricingGuide(TypedDict):
    model: str
    status: Literal['available', 'unavailable']
    rates: list[PublishedRate]
    source_url: str
    observed_at: str
    message: NotRequired[str]


class PublicTransport(Protocol):
    async def _request(self, method: str, path: str, *, key: str | None = None,
                       limit: int = _LIMIT, timeout: float = _TIMEOUT) -> tuple[bytes, Mapping[str, str]]: ...


class CatalogPort(Protocol):
    openrouter: object
    _catalog: Sequence[object]

    async def _load_catalog(self) -> None: ...


class PricingState(Protocol):
    studio: CatalogPort


@dataclass(frozen=True)
class _CachedGuide:
    expires: float
    guide: PricingGuide


def _map(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _list(value: object, limit: int) -> list[object]:
    if not isinstance(value, list):
        raise ValueError('Invalid public metadata list')
    items = cast(list[object], value)
    if len(items) > limit:
        raise ValueError('Invalid public metadata list')
    return items


def _label(value: object, limit: int = 100) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value.strip()


def _amount(value: object, cents: bool = False) -> str | None:
    # JSON numbers are parsed as Decimal. Never convert through binary float.
    if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
        return None
    source = str(value)
    if len(source) > 64 or not _NUMBER.fullmatch(source):
        return None
    try:
        number = Decimal(source)
        if not number.is_finite() or number < 0 or number > Decimal('1000000000'):
            return None
        if len(number.as_tuple().digits) > 24 or not -18 <= number.adjusted() <= 9:
            return None
        if cents:
            number /= 100
        result = format(number, 'f')
    except (InvalidOperation, ValueError, OverflowError):
        return None
    if '.' in result:
        result = result.rstrip('0').rstrip('.')
    return result if len(result) <= 64 else None


def _reject_constant(_value: str) -> object:
    raise ValueError('Non-finite public metadata')


def _decode(raw: bytes) -> dict[str, object]:
    if len(raw) > _LIMIT:
        raise ValueError('Public metadata exceeds response limit')
    value = cast(object, json.loads(raw, parse_float=Decimal, parse_int=Decimal, parse_constant=_reject_constant))
    result = _map(value)
    if not result or result.get('error'):
        raise ValueError('Invalid public metadata')
    return result


def _image_rates(payload: dict[str, object], model: str) -> tuple[list[PublishedRate], bool]:
    # The Images API returns a direct model object. Accept its explicit data
    # envelope too, but require the same model identity in either representation.
    data = _map(payload['data']) if 'data' in payload else payload
    if data.get('id') != model:
        raise ValueError('Different public metadata model')
    rows: list[PublishedRate] = []
    omitted = False
    for raw_endpoint in _list(data.get('endpoints'), 100):
        endpoint = _map(raw_endpoint)
        if endpoint.get('model_id', model) != model:
            raise ValueError('Different public metadata endpoint model')
        provider = _label(endpoint.get('provider_name')) or _label(endpoint.get('provider_tag')) or _label(endpoint.get('tag'))
        pricing = endpoint.get('pricing')
        if not provider or not isinstance(pricing, list):
            omitted = True
            continue
        for raw_rate in _list(cast(object, pricing), _MAX_RATES):
            rate = _map(raw_rate)
            billable, unit = rate.get('billable'), rate.get('unit')
            cost = _amount(rate.get('cost_usd'))
            variant = rate.get('variant')
            allowed = (billable in ('input_image', 'output_image') and unit in ('image', 'token', 'request')) or (billable in ('input_text', 'output_text') and unit == 'token')
            if set(rate) - {'billable', 'unit', 'cost_usd', 'variant'} or not allowed or cost is None or (variant is not None and (not isinstance(variant, str) or not _VARIANT.fullmatch(variant))):
                omitted = True
                continue
            row: PublishedRate = {'provider': provider, 'billable': cast(str, billable), 'unit': cast(str, unit), 'cost_usd': cost}
            if isinstance(variant, str):
                row['variant'] = variant
            rows.append(row)
            if len(rows) > _MAX_RATES:
                raise ValueError('Too many public rates')
    return rows, omitted


def _video_sku(name: str) -> tuple[str, str, str | None, bool] | None:
    if name == 'cents_per_second_output':
        return 'output_video', 'second', None, True
    if name == 'minimum_cents_per_generation':
        return 'minimum_generation_charge', 'generation', None, True
    if match := _VIDEO_SECONDS.fullmatch(name):
        variant = '_'.join(part for part in match.groups() if part) or None
        return 'output_video', 'second', variant, False
    if match := _VIDEO_MODE_SECONDS.fullmatch(name):
        return 'output_video', 'second', '_'.join(match.groups()), False
    if match := _VIDEO_TOKENS.fullmatch(name):
        variant = '_'.join(part for part in match.groups() if part) or None
        return 'output_video', 'token', variant, False
    return None


def _video_rates(payload: dict[str, object], model: str) -> tuple[list[PublishedRate], bool]:
    models = [_map(item) for item in _list(payload.get('data'), _MAX_MODELS)]
    matching = [item for item in models if item.get('id') == model]
    if len(matching) != 1:
        raise ValueError('Missing or duplicate public metadata model')
    skus = _map(matching[0].get('pricing_skus'))
    if len(skus) > _MAX_RATES:
        raise ValueError('Too many public pricing fields')
    rows: list[PublishedRate] = []
    omitted = not bool(skus)
    for sku, value in skus.items():
        definition = _video_sku(sku)
        if definition is None:
            omitted = True
            continue
        billable, unit, variant, cents = definition
        cost = _amount(value, cents)
        if cost is None:
            omitted = True
            continue
        # The video catalog publishes model-wide SKUs, not provider quotes.
        row: PublishedRate = {'provider': 'OpenRouter (model rates)', 'billable': billable, 'unit': unit, 'cost_usd': cost}
        if variant:
            row['variant'] = variant
        rows.append(row)
    return rows, omitted


class StudioPricingService:
    def __init__(self, transport: PublicTransport) -> None:
        self.transport: PublicTransport = transport
        self._cache: OrderedDict[tuple[str, str], _CachedGuide] = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Task[PricingGuide]] = {}
        self._capacity: asyncio.Semaphore = asyncio.Semaphore(4)

    @staticmethod
    def unavailable(model: str, kind: str) -> PricingGuide:
        path = '/images/models/' + model + '/endpoints' if kind == 'image' else '/videos/models'
        return {'model': model, 'status': 'unavailable', 'rates': [], 'source_url': _API + path,
                'observed_at': datetime.now(timezone.utc).isoformat(), 'message': _UNAVAILABLE}

    async def get(self, model: str, kind: Literal['image', 'video']) -> PricingGuide:
        if not _MODEL.fullmatch(model) or any(part in ('', '.', '..') for part in model.split('/')) or kind not in ('image', 'video'):
            raise ValueError('Invalid studio pricing model')
        key = (model, kind)
        # These short cache operations contain no await, so unrelated models do
        # not queue behind a slow network response. Same-model reads share I/O.
        now = monotonic()
        for expired in [item for item, value in self._cache.items() if value.expires <= now]:
            del self._cache[expired]
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached.guide
        pending = self._inflight.get(key)
        if pending is None:
            if len(self._inflight) >= _MAX_MODELS:
                return self.unavailable(model, kind)
            pending = asyncio.create_task(self._fetch(model, kind))
            self._inflight[key] = pending
        return await asyncio.shield(pending)

    async def _fetch(self, model: str, kind: Literal['image', 'video']) -> PricingGuide:
        key = (model, kind)
        path = '/images/models/' + model + '/endpoints' if kind == 'image' else '/videos/models'
        guide = self.unavailable(model, kind)
        try:
            # Bound the entire fetch, including the small concurrency queue;
            # per-read HTTP timeouts alone do not bound a streaming response.
            async with asyncio.timeout(_TIMEOUT):
                async with self._capacity:
                    raw, _headers = await self.transport._request('GET', path, key=None, limit=_LIMIT, timeout=_TIMEOUT)
            payload = _decode(raw)
            rates, omitted = _image_rates(payload, model) if kind == 'image' else _video_rates(payload, model)
            if rates:
                guide['status'] = 'available'
                guide['rates'] = rates
                if omitted:
                    guide['message'] = _PARTIAL
                else:
                    del guide['message']
                self._cache[key] = _CachedGuide(monotonic() + _TTL, guide)
                while len(self._cache) > _MAX_MODELS:
                    _ = self._cache.popitem(last=False)
        except Exception:
            # Failed metadata stays retryable and never echoes upstream payloads,
            # URLs, headers, or credentials. Cancellation still propagates.
            pass
        finally:
            _ = self._inflight.pop(key, None)
        return guide


def mount_studio_pricing(router: APIRouter, state: object,
                         actor: Callable[[Request, str | None, str], str]) -> StudioPricingService:
    studio = cast(PricingState, state).studio
    service = StudioPricingService(cast(PublicTransport, studio.openrouter))

    @router.get('/studio/pricing')
    async def published_rates(request: Request, response: Response,
                              model: Annotated[str, Query(min_length=3, max_length=200)],
                              project_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None) -> PricingGuide:
        response.headers['Cache-Control'] = 'no-store'
        _ = actor(request, project_id, 'agent-view')
        if not _MODEL.fullmatch(model) or any(part in ('', '.', '..') for part in model.split('/')):
            raise HTTPException(400, 'Choose a supported studio model')
        await studio._load_catalog()
        matches = [_map(item) for item in studio._catalog if _map(item).get('id') == model]
        if len(matches) != 1 or matches[0].get('adapter') != 'openrouter' or matches[0].get('kind') not in ('image', 'video'):
            raise HTTPException(400, 'Choose a supported OpenRouter studio model')
        # Catalog refresh may have awaited public I/O; recheck scope before a
        # cached result is consulted. No account lookup or key is required.
        _ = actor(request, project_id, 'agent-view')
        result = await service.get(model, cast(Literal['image', 'video'], matches[0]['kind']))
        _ = actor(request, project_id, 'agent-view')
        return result

    return service
