"""Provider discovery: parsers per flavour, catalog storage, refresh lifecycle, free provider test."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from termx.agent.discovery import (
    ProviderCatalogue,
    clean_overrides,
    discover_models,
    merge_meta,
    parse_lmstudio,
    parse_ollama,
    parse_openai,
    parse_openrouter,
    parse_vllm,
)
from termx.agent.providers import OpenAIResponsesAdapter, ProviderError
from termx.agent.store import AgentStore

FIXTURES = Path(__file__).parent / "fixtures" / "discovery"
NOW = 1_700_000_000.0


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_openrouter_parser_reads_window_output_price_and_capabilities() -> None:
    models = parse_openrouter(fixture("openrouter_models.json"), NOW)
    sonnet = models["anthropic/claude-sonnet-4.5"]
    assert sonnet["context_window"] == 200000
    assert sonnet["max_output"] == 64000
    assert sonnet["price_in"] == 3.0
    assert sonnet["price_out"] == 15.0
    assert sonnet["capabilities"] == ["tools", "reasoning", "vision"]
    assert sonnet["source"] == "openrouter" and sonnet["fetched_at"] == NOW
    # Variable pricing (-1) and a missing output limit stay unknown, never zero.
    auto = models["openrouter/auto"]
    assert auto["price_in"] is None and auto["price_out"] is None and auto["max_output"] is None
    # A free model really costs zero.
    free = models["meta-llama/llama-3.3-70b-instruct:free"]
    assert free["price_in"] == 0.0 and free["price_out"] == 0.0


def test_ollama_parser_uses_architecture_context_length_and_capabilities() -> None:
    shows = {
        "qwen2.5-coder:7b": fixture("ollama_show_qwen.json"),
        "llava:latest": fixture("ollama_show_llava.json"),
    }
    models = parse_ollama(fixture("ollama_tags.json"), shows, NOW)
    assert models["qwen2.5-coder:7b"]["context_window"] == 32768
    assert models["qwen2.5-coder:7b"]["capabilities"] == ["tools"]
    assert models["llava:latest"]["context_window"] == 4096
    assert models["llava:latest"]["capabilities"] == ["vision"]
    assert models["llava:latest"]["price_in"] is None
    # A model whose /api/show failed is still listed, with unknown values.
    missing = parse_ollama(fixture("ollama_tags.json"), {}, NOW)
    assert missing["llava:latest"]["context_window"] is None


def test_vllm_lmstudio_and_openai_parsers() -> None:
    vllm = parse_vllm(fixture("vllm_models.json"), NOW)
    assert vllm["Qwen/Qwen2.5-Coder-32B-Instruct"]["context_window"] == 32768
    assert vllm["Qwen/Qwen2.5-Coder-32B-Instruct"]["source"] == "vllm"

    lmstudio = parse_lmstudio(fixture("lmstudio_models.json"), NOW)
    assert set(lmstudio) == {"qwen2.5-coder-14b-instruct", "qwen2-vl-7b"}  # embeddings skipped
    assert lmstudio["qwen2.5-coder-14b-instruct"]["context_window"] == 32768
    assert lmstudio["qwen2.5-coder-14b-instruct"]["capabilities"] == ["tools"]
    assert lmstudio["qwen2-vl-7b"]["capabilities"] == ["vision"]

    openai = parse_openai(fixture("openai_models.json"), NOW)
    assert set(openai) == {"gpt-5", "gpt-5-mini"}
    assert openai["gpt-5"]["context_window"] is None
    assert openai["gpt-5"]["price_in"] is None and openai["gpt-5"]["capabilities"] == []


def _client(routes: dict[str, Any], seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        route = routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(route, int):
            return httpx.Response(route, json={"error": {"message": "nope"}})
        return httpx.Response(200, json=route)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _discover(base_url: str, routes: dict[str, Any], key: str = "k", seen=None):
    provider = {"base_url": base_url}
    return asyncio.run(discover_models(provider, key, client=_client(routes, seen), now=lambda: NOW))


def test_discover_detects_ollama_and_posts_show_per_model() -> None:
    seen: list[httpx.Request] = []
    routes = {
        ("GET", "/api/tags"): fixture("ollama_tags.json"),
        ("POST", "/api/show"): fixture("ollama_show_qwen.json"),
    }
    models = _discover("http://localhost:11434/v1", routes, seen=seen)
    assert set(models) == {"qwen2.5-coder:7b", "llava:latest"}
    shows = [r for r in seen if r.url.path == "/api/show"]
    assert sorted(json.loads(r.content)["model"] for r in shows) == ["llava:latest", "qwen2.5-coder:7b"]
    assert models["qwen2.5-coder:7b"]["context_window"] == 32768


def test_discover_detects_lmstudio_vllm_and_plain_openai() -> None:
    lm = _discover("http://localhost:1234/v1", {("GET", "/api/v0/models"): fixture("lmstudio_models.json")})
    assert lm["qwen2.5-coder-14b-instruct"]["source"] == "lmstudio"
    vllm = _discover("http://gpu:8000/v1", {("GET", "/v1/models"): fixture("vllm_models.json")})
    assert vllm["Qwen/Qwen2.5-Coder-32B-Instruct"]["context_window"] == 32768
    plain = _discover("http://proxy:4000/v1", {("GET", "/v1/models"): fixture("openai_models.json")})
    assert plain["gpt-5"]["source"] == "openai" and plain["gpt-5"]["context_window"] is None


def test_discover_uses_hosted_flavours_without_probing_local_paths() -> None:
    seen: list[httpx.Request] = []
    models = _discover(
        "https://openrouter.ai/api/v1", {("GET", "/api/v1/models"): fixture("openrouter_models.json")}, seen=seen
    )
    assert "anthropic/claude-sonnet-4.5" in models
    assert [r.url.path for r in seen] == ["/api/v1/models"]
    assert seen[0].headers["authorization"] == "Bearer k"
    seen.clear()
    _discover("https://api.openai.com/v1", {("GET", "/v1/models"): fixture("openai_models.json")}, seen=seen)
    assert [r.url.path for r in seen] == ["/v1/models"]


def test_discover_failure_carries_the_status() -> None:
    with pytest.raises(ProviderError) as bad_key:
        _discover("https://api.openai.com/v1", {("GET", "/v1/models"): 401})
    assert bad_key.value.status_code == 401
    assert "API key" in str(bad_key.value)


def test_overrides_beat_discovered_values_and_only_for_the_fields_set() -> None:
    discovered = parse_openrouter(fixture("openrouter_models.json"), NOW)["anthropic/claude-sonnet-4.5"]
    meta = merge_meta(discovered, {"context_window": 1_000_000, "price_in": 1.5})
    assert meta["context_window"] == 1_000_000
    assert meta["price_in"] == 1.5
    assert meta["price_out"] == 15.0
    assert meta["overridden"] == ["context_window", "price_in"]
    assert merge_meta(None, None)["context_window"] is None


def test_override_validation() -> None:
    assert clean_overrides({"m": {"context_window": "8192", "price_in": 0}}) == {
        "m": {"context_window": 8192, "price_in": 0.0}
    }
    for bad in ({"m": {"context_window": 0}}, {"m": {"bogus": 1}}, {"m": {"price_out": -1}}, ["x"]):
        with pytest.raises(ValueError):
            clean_overrides(bad)


def _store(tmp_path: Path) -> AgentStore:
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    store.put_provider(
        "p", kind="openai-compatible", name="P", base_url="http://localhost:11434/v1",
        model="", capabilities=["shell"],
    )
    return store


def test_store_catalog_roundtrip_empty_selection_means_all_discovered(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_provider("p")["models"] == []
    catalog = parse_openai(fixture("openai_models.json"), NOW)
    store.set_provider_catalog("p", catalog, refreshed_at=NOW)
    provider = store.get_provider("p")
    assert provider["models"] == ["gpt-5", "gpt-5-mini"]
    assert provider["selected_models"] == []
    assert provider["catalog_refreshed_at"] == NOW and provider["catalog_error"] is None
    # Re-saving the provider (a key rotation, say) keeps the catalog and overrides.
    store.put_provider("p", kind="openai-compatible", name="P", base_url="http://localhost:11434/v1",
                       model="gpt-5", capabilities=["shell"], model_overrides={"gpt-5": {"max_output": 4000}})
    provider = store.get_provider("p")
    assert provider["models"] == ["gpt-5"]
    assert set(provider["model_catalog"]) == {"gpt-5", "gpt-5-mini"}
    assert provider["model_overrides"] == {"gpt-5": {"max_output": 4000}}


def test_failed_refresh_keeps_previous_catalog_and_records_error(tmp_path: Path) -> None:
    async def run() -> None:
        store = _store(tmp_path)
        store.set_provider_catalog("p", parse_openai(fixture("openai_models.json"), NOW), refreshed_at=NOW)
        outcomes: list[Any] = [ProviderError("Could not reach provider", network=True)]

        async def fetch(_provider: dict[str, Any]) -> dict[str, Any]:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        catalogue = ProviderCatalogue(store, fetch)
        provider = await catalogue.refresh("p")  # must not raise
        assert set(provider["model_catalog"]) == {"gpt-5", "gpt-5-mini"}
        assert "Could not reach" in provider["catalog_error"]
        assert provider["catalog_refreshed_at"] == NOW
        # A later success replaces the catalog and clears the error.
        outcomes.append(parse_vllm(fixture("vllm_models.json"), NOW))
        provider = await catalogue.refresh("p")
        assert list(provider["model_catalog"]) == ["Qwen/Qwen2.5-Coder-32B-Instruct"]
        assert provider["catalog_error"] is None
        store.close()

    asyncio.run(run())


def test_refresh_times_out_without_raising_and_is_single_flight(tmp_path: Path) -> None:
    async def run() -> None:
        store = _store(tmp_path)
        calls = 0
        release = asyncio.Event()

        async def slow(_provider: dict[str, Any]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            await release.wait()
            return parse_openai(fixture("openai_models.json"), NOW)

        catalogue = ProviderCatalogue(store, slow, timeout_s=5)
        first = asyncio.create_task(catalogue.refresh("p"))
        second = asyncio.create_task(catalogue.refresh("p"))
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.gather(first, second)
        assert calls == 1

        async def hang(_provider: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(10)
            return {}

        timed = ProviderCatalogue(store, hang, timeout_s=0.05)
        provider = await timed.refresh("p")
        assert provider["catalog_error"]
        assert set(provider["model_catalog"]) == {"gpt-5", "gpt-5-mini"}
        with pytest.raises(KeyError):
            await timed.refresh("missing")
        store.close()

    asyncio.run(run())


def test_invalidate_discards_an_in_flight_refresh(tmp_path: Path) -> None:
    async def run() -> None:
        store = _store(tmp_path)
        release = asyncio.Event()

        async def fetch(_provider: dict[str, Any]) -> dict[str, Any]:
            await release.wait()
            return parse_openai(fixture("openai_models.json"), NOW)

        catalogue = ProviderCatalogue(store, fetch)
        pending = asyncio.create_task(catalogue.refresh("p"))
        await asyncio.sleep(0.05)
        catalogue.invalidate("p")  # base URL or key changed mid-flight
        release.set()
        await pending
        assert store.get_provider("p")["model_catalog"] == {}
        store.close()

    asyncio.run(run())


def _adapter(routes: dict[str, Any], model: str = "gpt-5", seen=None) -> OpenAIResponsesAdapter:
    adapter = OpenAIResponsesAdapter(
        base_url="https://api.openai.com/v1", model=model, api_key="k", capabilities=["shell"]
    )
    adapter.client = _client(routes, seen)
    return adapter


def test_free_provider_test_does_not_bill_and_reports_model_count() -> None:
    seen: list[httpx.Request] = []
    adapter = _adapter({("GET", "/v1/models"): fixture("openai_models.json")}, seen=seen)
    message = asyncio.run(adapter.test())
    assert message == "Connected. 2 models available."
    assert all(r.method == "GET" for r in seen)  # no POST /responses


def test_free_provider_test_tells_bad_key_from_bad_model() -> None:
    with pytest.raises(ProviderError) as key:
        asyncio.run(_adapter({("GET", "/v1/models"): 401}).test())
    assert key.value.status_code == 401 and "API key" in str(key.value)
    with pytest.raises(ProviderError) as model:
        asyncio.run(_adapter({("GET", "/v1/models"): fixture("openai_models.json")}, model="gpt-9").test())
    assert model.value.status_code == 404 and "gpt-9" in str(model.value)


def test_provider_test_falls_back_to_one_billed_call_only_when_listing_is_unsupported() -> None:
    seen: list[httpx.Request] = []
    adapter = _adapter(
        {("POST", "/v1/responses"): {"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}},
        seen=seen,
    )
    assert asyncio.run(adapter.test()) == "OK"
    assert [r.method for r in seen] == ["GET", "POST"]
    # Explicitly asking for the billed check skips the listing.
    seen.clear()
    assert asyncio.run(adapter.test(billed=True)) == "OK"
    assert [r.method for r in seen] == ["POST"]
