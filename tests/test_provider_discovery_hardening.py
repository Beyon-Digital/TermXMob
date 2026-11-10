"""Discovery treats the provider endpoint as untrusted: no key in errors, bounded bodies, no redirects."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from test_provider_discovery import NOW, _store
from termx.agent import discovery
from termx.agent.discovery import ProviderCatalogue, discover_models
from termx.agent.providers import ProviderError

KEY = "sk-SECRET123456789012345"


def _run(handler, base_url: str = "http://proxy:4000/v1", key: str = "k") -> dict[str, Any]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return asyncio.run(discover_models({"base_url": base_url}, key, client=client, now=lambda: NOW))


def test_transport_errors_never_echo_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.LocalProtocolError(f"Illegal header value b'Bearer {KEY}\\nTRAILING'")

    with pytest.raises(ProviderError) as failure:
        _run(handler, key=KEY)
    assert KEY not in str(failure.value)
    assert "LocalProtocolError" in str(failure.value)


def test_catalogue_stores_only_provider_error_text_or_the_exception_name(tmp_path: Path) -> None:
    async def run() -> None:
        store = _store(tmp_path)

        async def leak(_provider: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError(f"Bearer {KEY}")

        provider = await ProviderCatalogue(store, leak).refresh("p")
        assert provider["catalog_error"] == "RuntimeError"
        store.close()

    asyncio.run(run())


def test_oversized_body_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "MAX_BODY_BYTES", 200)
    big = {"data": [{"id": f"model-{i}"} for i in range(100)]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=big)
        return httpx.Response(404)

    with pytest.raises(ProviderError, match="too large"):
        _run(handler)


def test_catalog_is_capped_in_count_and_id_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "MAX_MODELS", 3)
    monkeypatch.setattr(discovery, "MAX_ID_LENGTH", 10)
    body = {"data": [{"id": "x" * 11}] + [{"id": f"m{i}"} for i in range(10)]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body) if request.url.path == "/v1/models" else httpx.Response(404)

    assert list(_run(handler)) == ["m0", "m1", "m2"]


def test_ollama_show_fan_out_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "MAX_SHOW_REQUESTS", 2)
    shows: list[str] = []
    tags = {"models": [{"name": f"m{i}", "model": f"m{i}"} for i in range(5)]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags)
        if request.url.path == "/api/show":
            shows.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(404)

    catalog = _run(handler, base_url="http://localhost:11434/v1")
    assert len(shows) == 2
    assert len(catalog) == 5  # models past the cap are listed with unknown values


def test_redirects_are_not_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "proxy":
            return httpx.Response(302, headers={"location": "http://internal.example/v1/models"})
        return httpx.Response(200, json={"data": [{"id": "stolen"}]})

    with pytest.raises(ProviderError):
        _run(handler)
    assert all("internal.example" not in url for url in seen)
