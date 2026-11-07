"""Edge cases for provider testing, value sanity and the startup sweep."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from test_provider_discovery import NOW, _adapter, _client, _store
from termx.agent.discovery import ProviderCatalogue, clean_overrides, parse_openrouter
from termx.agent.providers import OpenAIResponsesAdapter, ProviderError


def test_spawn_model_of_the_parents_provider_is_found_when_a_worker_route_points_elsewhere(tmp_path: Path) -> None:
    from test_agent import _FanOutAdapter, _approve_pending, _fn, wait_for_status
    from test_model_routing import WORKER, _routed

    async def run() -> None:
        adapter = _FanOutAdapter({
            "Coordinate": ([[_fn("s1", "spawn_subagent", task="child-a", model="big-model")]], 0.0),
            "child-a": ([], 0.0),
        })
        manager, store = _routed(tmp_path, adapter, WORKER)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake", model="fake-model")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])
        await wait_for_status(store, parent["id"], "completed")
        child = next(t for t in store.list_tasks() if t.get("parent_id") == parent["id"])
        assert (child["provider_id"], child["model"]) == ("fake", "big-model")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_provider_test_does_not_bill_on_outage_rate_limit_or_bad_body() -> None:
    for status in (503, 429):
        seen: list[httpx.Request] = []
        with pytest.raises(ProviderError) as failure:
            asyncio.run(_adapter({("GET", "/v1/models"): status}, seen=seen).test())
        assert failure.value.status_code == status
        assert all(r.method == "GET" for r in seen)  # no POST /responses


def test_provider_test_accepts_an_ollama_model_without_its_latest_tag() -> None:
    adapter = OpenAIResponsesAdapter(
        base_url="http://localhost:11434/v1", model="llama3", api_key="", capabilities=["shell"]
    )
    adapter.client = _client({("GET", "/api/tags"): {"models": [{"name": "llama3:latest", "model": "llama3:latest"}]}})
    assert asyncio.run(adapter.test()) == "Connected. 1 model available."


def test_non_finite_and_absurd_values_are_rejected() -> None:
    assert clean_overrides({"m": {"price_in": 1.5}}) == {"m": {"price_in": 1.5}}
    for bad in (float("inf"), float("nan"), 1e12):
        with pytest.raises(ValueError):
            clean_overrides({"m": {"price_in": bad}})
    for bad in (10**30, float("inf")):
        with pytest.raises(ValueError):
            clean_overrides({"m": {"context_window": bad}})
    body = {"data": [{"id": "m", "pricing": {"prompt": "1e999", "completion": "0.000001"}}]}
    info = parse_openrouter(body, NOW)["m"]
    assert info["price_in"] is None and info["price_out"] == 1.0


def test_startup_sweep_survives_a_provider_deleted_mid_refresh(tmp_path: Path) -> None:
    async def run() -> None:
        store = _store(tmp_path)
        store.put_provider(
            "q", kind="openai-compatible", name="Q", base_url="http://x/v1", model="", capabilities=["shell"]
        )

        async def fetch(provider: dict[str, Any]) -> dict[str, Any]:
            return {}

        catalogue = ProviderCatalogue(store, fetch)
        original = store.get_provider
        # "q" is listed, then disappears before its refresh looks it up.
        store.get_provider = lambda pid: None if pid == "q" else original(pid)  # type: ignore[method-assign]
        await catalogue.refresh_all()  # must not raise
        with pytest.raises(KeyError):
            await catalogue.refresh("q")
        store.close()

    asyncio.run(run())
