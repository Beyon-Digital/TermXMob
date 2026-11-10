"""Model discovery: ask each provider what it serves instead of typing it in.

``discover_models`` returns ``{model_id: ModelInfo}``. ``ModelInfo`` keys are
``context_window``, ``max_output``, ``price_in``, ``price_out`` (USD per 1M
tokens), ``capabilities``, ``source`` and ``fetched_at``. A value the provider
does not report is ``None``; it is never defaulted to zero.

``ProviderCatalogue`` owns the refresh lifecycle (single flight, bounded
concurrency, timeout, keep the previous catalog on failure). Reads never
touch the network.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from termx.agent.providers import ProviderError

DISCOVERY_TIMEOUT_S = 15.0
_SHOW_CONCURRENCY = 8
# The endpoint is untrusted: bound what one refresh can read, store and fan out to.
MAX_BODY_BYTES = 5_000_000
MAX_MODELS = 2000
MAX_ID_LENGTH = 200
MAX_SHOW_REQUESTS = 200
_CAPABILITY_ALIASES = {
    "tools": "tools",
    "tool_use": "tools",
    "tool_choice": "tools",
    "vision": "vision",
    "image": "vision",
    "thinking": "reasoning",
    "reasoning": "reasoning",
}
# Sanity ceilings: a remote catalog or an override must not produce inf or absurd values.
MAX_TOKENS = 100_000_000
MAX_PRICE_PER_MILLION = 1_000_000.0
OVERRIDE_FIELDS = ("context_window", "max_output", "price_in", "price_out", "capabilities")


def model_info(source: str, fetched_at: float | None, **values: Any) -> dict[str, Any]:
    return {
        "context_window": _positive_int(values.get("context_window")),
        "max_output": _positive_int(values.get("max_output")),
        "price_in": _price(values.get("price_in")),
        "price_out": _price(values.get("price_out")),
        "capabilities": _capabilities(values.get("capabilities")),
        "source": source,
        "fetched_at": fetched_at,
    }


def merge_meta(discovered: dict[str, Any] | None, override: dict[str, Any] | None) -> dict[str, Any]:
    """Discovered values with the user's overrides on top; ``overridden`` names the fields they set."""
    meta = dict(discovered) if discovered else model_info("unknown", None)
    overridden: list[str] = []
    for key in OVERRIDE_FIELDS:
        value = (override or {}).get(key)
        if value is None:
            continue
        meta[key] = value
        overridden.append(key)
    meta["overridden"] = overridden
    return meta


def catalog_key(model: str, catalog: dict[str, dict[str, Any]]) -> str | None:
    """The catalog entry ``model`` names. Ollama accepts ``llama3`` for a listed ``llama3:latest``."""
    if model in catalog:
        return model
    if any(info.get("source") == "ollama" for info in catalog.values()):
        bare = model.removesuffix(":latest")
        for key in (bare, f"{bare}:latest"):
            if key in catalog:
                return key
    return None


def model_listed(model: str, catalog: dict[str, dict[str, Any]]) -> bool:
    return catalog_key(model, catalog) is not None


def clean_overrides(value: Any) -> dict[str, dict[str, Any]]:
    """Validate user overrides; unknown or malformed values raise ValueError."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("model overrides must be an object keyed by model id")
    cleaned: dict[str, dict[str, Any]] = {}
    for model, fields in value.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(fields, dict):
            raise ValueError("each model override must be an object keyed by model id")
        unknown = fields.keys() - set(OVERRIDE_FIELDS)
        if unknown:
            raise ValueError(f"unknown model override fields: {', '.join(sorted(unknown))}")
        item: dict[str, Any] = {}
        for key in ("context_window", "max_output"):
            if fields.get(key) is not None:
                parsed = _positive_int(fields[key])
                if parsed is None:
                    raise ValueError(f"{key} must be a positive integer")
                item[key] = parsed
        for key in ("price_in", "price_out"):
            if fields.get(key) is not None:
                parsed = _price(fields[key])
                if parsed is None:
                    raise ValueError(f"{key} must be a non-negative number (USD per 1M tokens)")
                item[key] = parsed
        if fields.get("capabilities") is not None:
            if not isinstance(fields["capabilities"], list):
                raise ValueError("capabilities must be a list")
            item["capabilities"] = _capabilities(fields["capabilities"])
        if item:
            cleaned[model.strip()] = item
    return cleaned


# Parsers ---------------------------------------------------------------


def parse_openrouter(body: Any, fetched_at: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in _data_list(body):
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        top = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
        pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
        params = item.get("supported_parameters") if isinstance(item.get("supported_parameters"), list) else []
        arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        capabilities = list(params)
        if "image" in (arch.get("input_modalities") or []):
            capabilities.append("vision")
        out[model_id] = model_info(
            "openrouter",
            fetched_at,
            context_window=item.get("context_length") or top.get("context_length"),
            max_output=top.get("max_completion_tokens"),
            price_in=_per_million(pricing.get("prompt")),
            price_out=_per_million(pricing.get("completion")),
            capabilities=capabilities,
        )
    return out


def parse_ollama(tags: Any, shows: dict[str, Any], fetched_at: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    models = tags.get("models") if isinstance(tags, dict) else None
    for item in models if isinstance(models, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("model") or item.get("name")
        if not isinstance(name, str) or not name:
            continue
        show = shows.get(name) if isinstance(shows.get(name), dict) else {}
        info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
        arch = info.get("general.architecture")
        window = info.get(f"{arch}.context_length") if isinstance(arch, str) else None
        if window is None:
            window = next((v for k, v in info.items() if k.endswith(".context_length")), None)
        out[name] = model_info(
            "ollama", fetched_at, context_window=window, capabilities=show.get("capabilities")
        )
    return out


def parse_vllm(body: Any, fetched_at: float) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: model_info("vllm", fetched_at, context_window=item.get("max_model_len"))
        for item in _data_list(body)
        if isinstance(item.get("id"), str) and item["id"]
    }


def parse_lmstudio(body: Any, fetched_at: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in _data_list(body):
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id or item.get("type") in {"embeddings", "embedding"}:
            continue
        capabilities = list(item.get("capabilities") or [])
        if item.get("type") == "vlm":
            capabilities.append("vision")
        out[model_id] = model_info(
            "lmstudio", fetched_at, context_window=item.get("max_context_length"), capabilities=capabilities
        )
    return out


def parse_openai(body: Any, fetched_at: float) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: model_info("openai", fetched_at)
        for item in _data_list(body)
        if isinstance(item.get("id"), str) and item["id"]
    }


# Fetching ---------------------------------------------------------------


async def discover_models(
    provider: dict[str, Any],
    api_key: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = DISCOVERY_TIMEOUT_S,
    now: Callable[[], float] = time.time,
) -> dict[str, dict[str, Any]]:
    """Detect the provider's flavour from its URL and a probe, then list its models.

    Raises ``ProviderError`` (with ``status_code`` for HTTP failures) when the
    final listing request fails, so callers can tell a bad key from an
    unreachable host.
    """
    base = str(provider["base_url"]).rstrip("/")
    headers = {"User-Agent": "termx-agent/1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    owned = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)
    try:
        return _bounded(await _discover(client, base, headers, now()))
    except httpx.RequestError as exc:
        # Never echo str(exc): transport errors can quote the Authorization header.
        raise ProviderError(f"Could not reach provider ({type(exc).__name__})", network=True) from exc
    finally:
        if owned:
            await client.aclose()


async def _discover(
    client: httpx.AsyncClient, base: str, headers: dict[str, str], fetched_at: float
) -> dict[str, dict[str, Any]]:
    host = (urlsplit(base).hostname or "").lower()
    root = base[:-3] if base.endswith("/v1") else base
    if host.endswith("openrouter.ai"):
        return parse_openrouter(await _get_json(client, f"{base}/models", headers), fetched_at)
    if host == "api.openai.com":
        return parse_openai(await _get_json(client, f"{base}/models", headers), fetched_at)

    tags = await _probe(client, f"{root}/api/tags", headers)
    if isinstance(tags, dict) and isinstance(tags.get("models"), list):
        names = [m.get("model") or m.get("name") for m in tags["models"] if isinstance(m, dict)]
        names = [n for n in names if isinstance(n, str) and n][:MAX_SHOW_REQUESTS]
        gate = asyncio.Semaphore(_SHOW_CONCURRENCY)

        async def show(name: str) -> tuple[str, Any]:
            async with gate:
                try:
                    status, body = await _fetch(client, "POST", f"{root}/api/show", headers, {"model": name})
                    return name, json.loads(body) if 200 <= status < 300 else None
                except (httpx.RequestError, ValueError, ProviderError):
                    return name, None

        shows = dict(await asyncio.gather(*(show(n) for n in names)))
        return parse_ollama(tags, shows, fetched_at)

    lmstudio = await _probe(client, f"{root}/api/v0/models", headers)
    if isinstance(lmstudio, dict) and isinstance(lmstudio.get("data"), list):
        return parse_lmstudio(lmstudio, fetched_at)

    body = await _get_json(client, f"{base}/models", headers)
    if any("max_model_len" in item for item in _data_list(body)):
        return parse_vllm(body, fetched_at)
    return parse_openai(body, fetched_at)


async def _probe(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any:
    """GET that returns None for any failure: a probe only decides which flavour this is."""
    try:
        status, body = await _fetch(client, "GET", url, headers)
        return json.loads(body) if 200 <= status < 300 else None
    except (httpx.RequestError, ValueError, ProviderError):
        return None


async def _fetch(
    client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], payload: Any = None
) -> tuple[int, bytes]:
    """One request with a hard cap on the body size and no redirects."""
    async with client.stream(method, url, headers=headers, json=payload, follow_redirects=False) as response:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise ProviderError("The provider's model list is too large")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise ProviderError("The provider's model list is too large")
            chunks.append(chunk)
    return response.status_code, b"".join(chunks)


def _bounded(catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    usable = ((k, v) for k, v in catalog.items() if len(k) <= MAX_ID_LENGTH)
    return dict(list(usable)[:MAX_MODELS])


async def _get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any:
    status, body = await _fetch(client, "GET", url, headers)
    if not 200 <= status < 300:
        reason = (
            "The provider rejected the API key" if status in {401, 403}
            else f"The provider could not list models (HTTP {status})"
        )
        raise ProviderError(reason, status_code=status)
    try:
        return json.loads(body)
    except ValueError as exc:
        raise ProviderError("The provider returned an invalid model list") from exc


# Refresh lifecycle --------------------------------------------------------


class ProviderCatalogue:
    """Explicit, single-flight catalog refresh. Modelled on ``EngineCatalogue``."""

    def __init__(
        self,
        store: Any,
        fetch: Callable[[dict[str, Any]], Awaitable[dict[str, dict[str, Any]]]],
        *,
        timeout_s: float = DISCOVERY_TIMEOUT_S,
    ) -> None:
        self.store = store
        self._fetch = fetch
        self.timeout_s = timeout_s
        self.jobs: dict[str, asyncio.Task] = {}
        self.startup: asyncio.Task | None = None
        self._generation: dict[str, int] = {}
        # Discovery makes network calls; bound a startup sweep over many providers.
        self._semaphore = asyncio.Semaphore(4)

    def start(self) -> None:
        if self.startup is None:
            self.startup = asyncio.create_task(self.refresh_all())

    def invalidate(self, provider_id: str) -> None:
        """Config or key changed: an in-flight refresh against the old settings must not be saved."""
        self._generation[provider_id] = self._generation.get(provider_id, 0) + 1
        self.jobs.pop(provider_id, None)

    async def refresh_all(self) -> None:
        # A provider deleted mid-sweep raises KeyError; it must not abort the others.
        await asyncio.gather(
            *(self.refresh(p["id"]) for p in self.store.list_providers()), return_exceptions=True
        )

    async def refresh(self, provider_id: str) -> dict[str, Any]:
        """Refresh one provider and return it. Network failures set ``catalog_error`` and keep the old catalog."""
        if self.store.get_provider(provider_id) is None:
            raise KeyError(provider_id)
        task = self.jobs.get(provider_id)
        if task is None:
            task = asyncio.create_task(self._run(provider_id))
            self.jobs[provider_id] = task
        try:
            await asyncio.shield(task)
        finally:
            if task.done() and self.jobs.get(provider_id) is task:
                self.jobs.pop(provider_id, None)
        provider = self.store.get_provider(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        return provider

    async def _run(self, provider_id: str) -> None:
        generation = self._generation.get(provider_id, 0)
        provider = self.store.get_provider(provider_id)
        if provider is None:
            return
        catalog: dict[str, dict[str, Any]] | None = None
        error: str | None = None
        try:
            async with self._semaphore:
                catalog = await asyncio.wait_for(self._fetch(provider), self.timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a refresh never raises
            # Only our own fixed ProviderError text is stored; anything else could quote a credential.
            error = str(exc) if isinstance(exc, ProviderError) and str(exc) else type(exc).__name__
        if self._generation.get(provider_id, 0) != generation:
            return
        self.store.set_provider_catalog(provider_id, catalog, error=error, refreshed_at=time.time())

    async def stop(self) -> None:
        tasks = [*self.jobs.values(), *([self.startup] if self.startup else [])]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.jobs.clear()


# Helpers --------------------------------------------------------------


def _data_list(body: Any) -> list[dict[str, Any]]:
    data = body.get("data") if isinstance(body, dict) else None
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 < number <= MAX_TOKENS else None


def _price(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= MAX_PRICE_PER_MILLION else None


def _per_million(value: Any) -> float | None:
    """OpenRouter quotes USD per token as a string; negative means variable pricing."""
    number = _price(value)
    return None if number is None else _price(round(number * 1_000_000, 6))


def _capabilities(value: Any) -> list[str]:
    found: list[str] = []
    for item in value if isinstance(value, list) else []:
        mapped = _CAPABILITY_ALIASES.get(str(item).lower())
        if mapped and mapped not in found:
            found.append(mapped)
    return found
