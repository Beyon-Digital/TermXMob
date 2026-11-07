"""Hardening around discovered model metadata and fallback routing."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from test_agent import FakeAdapter, wait_for_pending_approval, wait_for_status
from test_model_routing import Failing, RoutedAdapters, _info, _routed
from test_plan_mode import PlanAdapter, _call
from termx.agent.discovery import catalog_key, model_info


def test_catalog_key_follows_the_ollama_alias_rule() -> None:
    catalog = {"llama3:latest": model_info("ollama", 1.0), "qwen:7b": model_info("ollama", 1.0)}
    assert catalog_key("llama3", catalog) == "llama3:latest"
    assert catalog_key("llama3:latest", catalog) == "llama3:latest"
    assert catalog_key("qwen:7b", catalog) == "qwen:7b"
    assert catalog_key("mistral", catalog) is None
    # Aliases only apply to Ollama catalogs.
    assert catalog_key("gpt", {"gpt:latest": model_info("openai", 1.0)}) is None


def test_model_meta_finds_an_ollama_model_selected_without_its_latest_tag(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    store.set_provider_catalog(
        "other", {"llama3:latest": model_info("ollama", 1.0, context_window=8192)}, refreshed_at=1.0
    )
    provider = store.get_provider("other")
    assert manager.model_meta(provider, "llama3")["context_window"] == 8192
    store.close()


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"max_output": 100_000_000, "context_window": 1_000_000, "overridden": []}, 32_000),
        ({"max_output": 8000, "context_window": 8000, "overridden": []}, 4000),
        ({"max_output": 4000, "context_window": 200_000, "overridden": []}, 4000),
        ({"max_output": 50_000, "context_window": 8000, "overridden": ["max_output"]}, 50_000),
        ({"max_output": None, "context_window": 8000, "overridden": []}, None),
    ],
)
def test_discovered_max_output_is_clamped_but_a_user_override_is_not(tmp_path: Path, meta, expected) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    provider = {**store.get_provider("fake"), "meta": meta}
    assert manager._default_adapter(provider, "k").max_output_tokens == expected
    store.close()


def test_fallback_without_tool_support_is_skipped_for_agent_tasks(tmp_path: Path) -> None:
    async def run() -> None:
        primary, backup = Failing(401, "primary denied"), PlanAdapter([("Plain chat reply.", [])])
        manager, store = _routed(tmp_path, FakeAdapter(), {
            "orchestrator": {"provider_id": "fake", "model": "", "fallbacks": [{"provider_id": "other", "model": ""}]}})
        manager.save_provider(provider_id="other", kind="openai-compatible", name="Other",
                              base_url="http://127.0.0.1:9998/v1", model="o-1,o-2",
                              capabilities=["image"], api_key="k")
        manager._adapter_factory = RoutedAdapters(fake=primary, other=backup)
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="agent")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        failed = await wait_for_status(store, task["id"], "failed")
        assert "primary denied" in failed["error"]
        assert backup.turns == 0
        skipped = [e["payload"] for e in store.events(task["id"]) if e["type"] == "provider.fallback_skipped"]
        assert skipped and skipped[0]["provider_id"] == "other"
        await manager.close()
        store.close()

    asyncio.run(run())


ROUTES = {
    "planner": {"provider_id": "other", "model": "o-1", "fallbacks": []},
    "orchestrator": {"provider_id": "fake", "model": "big-model", "fallbacks": []},
}


def _plan_then_approve(tmp_path: Path, **create: Any) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        adapter = PlanAdapter([("", [_call("exit_plan", "c1", plan_markdown="1. Edit")]), ("Done.", [])])
        manager, store = _routed(tmp_path, adapter, ROUTES)
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), mode="plan", **create)
        _task, approval = await wait_for_pending_approval(store, task["id"], "plan")
        planned = (store.get_task(task["id"])["provider_id"], store.get_task(task["id"])["model"])
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        after = store.get_task(task["id"])
        await manager.close()
        store.close()
        return {"planned": planned, "after": (after["provider_id"], after["model"])}

    return asyncio.run(run())


def test_approving_a_plan_moves_implementation_to_the_orchestrator_route(tmp_path: Path) -> None:
    result = _plan_then_approve(tmp_path, provider_id="")
    assert result["planned"] == ("other", "o-1")
    assert result["after"] == ("fake", "big-model")


def test_approving_a_plan_keeps_a_model_the_user_chose(tmp_path: Path) -> None:
    result = _plan_then_approve(tmp_path, provider_id="other", model="o-2")
    assert result["planned"] == result["after"] == ("other", "o-2")


def test_routing_rejects_a_tool_less_fallback_for_agent_roles(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    manager.save_provider(provider_id="other", kind="openai-compatible", name="Other",
                          base_url="http://127.0.0.1:9998/v1", model="o-1,o-2",
                          capabilities=["image"], api_key="k")
    route = {"provider_id": "fake", "model": "", "fallbacks": [{"provider_id": "other", "model": ""}]}
    for role in ("orchestrator", "worker"):
        with pytest.raises(ValueError, match="cannot call tools"):
            manager.validate_routing({role: route})
    assert manager.validate_routing({"planner": route})["planner"]["fallbacks"]
    store.close()
