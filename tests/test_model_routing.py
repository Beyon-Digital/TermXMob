"""Model routing, per-model metadata, fallback, cost and token/cost ceilings."""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from test_agent import (
    FakeAdapter,
    _FanOutAdapter,
    _approve_pending,
    _fn,
    _git_repo,
    build_manager,
    wait_for_pending_approval,
    wait_for_status,
)
from test_auto_mode import _approve_plan, _auto, _events, _shell
from test_plan_mode import PlanAdapter
from termx.agent.metrics import TaskMetrics
from termx.agent.providers import ProviderError, ProviderTurn
from termx.agent.tree_budget import TreeBudget
from termx.config import AgentPrefs

NOW = 1_700_000_000.0


def _info(window=None, out=None, pin=None, pout=None) -> dict[str, Any]:
    return {"context_window": window, "max_output": out, "price_in": pin, "price_out": pout,
            "capabilities": [], "source": "test", "fetched_at": NOW}


def _routed(tmp_path: Path, adapter, routing=None):
    """A manager with provider `fake` (fake-model, big-model) and provider `other` (o-1, o-2)."""
    manager, store = build_manager(tmp_path, adapter)
    manager.save_provider(provider_id="fake", kind="openai-compatible", name="Fake",
                          base_url="http://127.0.0.1:9999/v1", model="fake-model,big-model",
                          capabilities=["shell", "computer"], api_key="k")
    manager.save_provider(provider_id="other", kind="openai-compatible", name="Other",
                          base_url="http://127.0.0.1:9998/v1", model="o-1,o-2",
                          capabilities=["shell"], api_key="k")
    store.set_provider_catalog("fake", {"fake-model": _info(8000, 1000, 1.0, 2.0), "big-model": _info(200000, 8000)},
                               refreshed_at=NOW)
    store.set_provider_catalog("other", {"o-1": _info(32000, 4000, 0.5, 1.5), "o-2": _info(64000)}, refreshed_at=NOW)
    manager._settings = lambda: AgentPrefs(routing=routing or {})
    return manager, store


WORKER = {"worker": {"provider_id": "other", "model": "o-1", "fallbacks": []}}


def test_route_precedence_explicit_then_role_then_provider_default(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter(), {
        "worker": {"provider_id": "other", "model": "o-1", "fallbacks": []},
        "planner": {"provider_id": "fake", "model": "big-model", "fallbacks": [{"provider_id": "other", "model": ""}]},
    })
    # Provider default: first model, with its discovered metadata.
    route = manager.resolve_route("orchestrator", default_provider_id="fake")
    assert (route.provider_id, route.model, route.source) == ("fake", "fake-model", "default")
    assert route.meta["context_window"] == 8000 and route.meta["price_in"] == 1.0
    # Role route beats the provider default.
    route = manager.resolve_route("worker", default_provider_id="fake")
    assert (route.provider_id, route.model, route.source) == ("other", "o-1", "role")
    assert route.meta["max_output"] == 4000
    # Explicit task value beats the role route.
    route = manager.resolve_route("worker", {"model": "o-2"}, default_provider_id="fake")
    assert (route.provider_id, route.model, route.source) == ("other", "o-2", "explicit")
    route = manager.resolve_route("worker", {"provider_id": "fake", "model": "big-model"})
    assert (route.provider_id, route.model, route.source) == ("fake", "big-model", "explicit")
    # An explicit provider alone keeps that provider; the role model applies only when it is the same provider.
    route = manager.resolve_route("planner", {"provider_id": "fake"})
    assert (route.model, route.source) == ("big-model", "role")
    route = manager.resolve_route("worker", {"provider_id": "fake"})
    assert (route.provider_id, route.model, route.source) == ("fake", "fake-model", "explicit")
    # Fallbacks come from the role.
    assert manager.resolve_route("planner", {"provider_id": "fake"}).fallbacks == (("other", ""),)
    # Invalid choices fail clearly.
    with pytest.raises(ValueError, match="not configured"):
        manager.resolve_route("worker", {"model": "nope"}, default_provider_id="fake")
    with pytest.raises(KeyError):
        manager.resolve_route("worker", {"provider_id": "missing"})
    store.close()


def test_overrides_beat_discovered_metadata_in_the_route(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    manager.save_provider(provider_id="fake", kind="openai-compatible", name="Fake",
                          base_url="http://127.0.0.1:9999/v1", model="fake-model,big-model", capabilities=None,
                          model_meta={"fake-model": {"context_window": 16000, "price_in": 0}})
    meta = manager.resolve_route("worker", {"provider_id": "fake"}).meta
    assert meta["context_window"] == 16000 and meta["price_in"] == 0.0
    assert meta["price_out"] == 2.0 and meta["max_output"] == 1000  # discovered values stay
    assert meta["overridden"] == ["context_window", "price_in"]
    with pytest.raises(ValueError):
        manager.save_provider(provider_id="fake", kind="openai-compatible", name="Fake",
                              base_url="http://127.0.0.1:9999/v1", model="fake-model", capabilities=None,
                              model_meta={"fake-model": {"context_window": -5}})
    store.close()


def test_stale_role_route_is_skipped_not_fatal(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter(), {
        "worker": {"provider_id": "gone", "model": "x", "fallbacks": []}})
    route = manager.resolve_route("worker", default_provider_id="fake")
    assert (route.provider_id, route.model, route.source) == ("fake", "fake-model", "default")
    store.close()


def test_empty_selection_offers_every_discovered_model(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    manager.save_provider(provider_id="other", kind="openai-compatible", name="Other",
                          base_url="http://127.0.0.1:9998/v1", model="", capabilities=None)
    assert manager.resolve_route("worker", {"provider_id": "other", "model": "o-2"}).model == "o-2"
    assert manager.resolve_route("worker", {"provider_id": "other"}).model == "o-1"
    store.close()


def test_changing_base_url_clears_the_old_catalog(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    manager.save_provider(provider_id="other", kind="openai-compatible", name="Other",
                          base_url="http://127.0.0.1:9998/v1", model="o-1", capabilities=None, api_key="rotated")
    assert set(store.get_provider("other")["model_catalog"]) == {"o-1", "o-2"}  # key change keeps it
    manager.save_provider(provider_id="other", kind="openai-compatible", name="Other",
                          base_url="http://elsewhere:1/v1", model="o-1", capabilities=None)
    provider = store.get_provider("other")
    assert provider["model_catalog"] == {} and provider["catalog_refreshed_at"] is None
    store.close()


def test_validate_routing_checks_providers_and_models(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    good = {"worker": {"provider_id": "other", "model": "o-2", "fallbacks": [{"provider_id": "fake", "model": ""}]}}
    assert manager.validate_routing(good)["worker"]["fallbacks"] == [{"provider_id": "fake", "model": ""}]
    for bad in ({"worker": {"provider_id": "missing", "model": ""}},
                {"worker": {"provider_id": "other", "model": "nope"}},
                {"janitor": {"provider_id": "other", "model": "o-1"}},
                {"worker": {"provider_id": "other", "fallbacks": [{"provider_id": "missing"}]}}):
        with pytest.raises(ValueError):
            manager.validate_routing(bad)
    store.close()


def test_spawn_subagent_uses_worker_route_then_model_argument_and_never_the_parents_model(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter({
            "Coordinate": ([[_fn("s1", "spawn_subagent", task="child-a"),
                             _fn("s2", "spawn_subagent", task="child-b", model="o-2"),
                             _fn("s3", "spawn_subagent", task="child-c", model="not-a-model")]], 0.0),
            "child-a": ([], 0.0),
            "child-b": ([], 0.0),
        })
        manager, store = _routed(tmp_path, adapter, WORKER)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake", model="big-model")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        for _ in range(3):
            await _approve_pending(manager, store, parent["id"])
        await wait_for_status(store, parent["id"], "completed")
        children = {t["prompt"].splitlines()[0]: t for t in store.list_tasks() if t.get("parent_id") == parent["id"]}
        assert (children["child-a"]["provider_id"], children["child-a"]["model"]) == ("other", "o-1")
        assert (children["child-b"]["provider_id"], children["child-b"]["model"]) == ("other", "o-2")
        assert "child-c" not in children  # the unknown model was refused, no task created
        refused = [e["payload"]["result"] for e in store.events(parent["id"]) if e["type"] == "tool.finished"
                   and e["payload"].get("result", {}).get("ok") is False]
        assert refused and "not configured" in refused[0]["error"]
        assert parent["model"] == "big-model"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_without_a_route_the_child_gets_the_provider_default_model(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = _FanOutAdapter({
            "Coordinate": ([[_fn("s1", "spawn_subagent", task="child-a")]], 0.0),
            "child-a": ([], 0.0),
        })
        manager, store = _routed(tmp_path, adapter)
        parent = await manager.create_task(prompt="Coordinate", cwd=str(tmp_path), provider_id="fake", model="big-model")
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        await _approve_pending(manager, store, parent["id"])
        await wait_for_status(store, parent["id"], "completed")
        child = next(t for t in store.list_tasks() if t.get("parent_id") == parent["id"])
        assert (child["provider_id"], child["model"]) == ("fake", "fake-model")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_output_tokens_are_limited_only_by_explicit_provider_or_model_settings(tmp_path: Path) -> None:
    manager, store = _routed(tmp_path, FakeAdapter())
    manager._adapter_factory = manager._default_adapter
    provider = dict(store.get_provider("fake"))

    def payload_for(model: str) -> dict[str, Any]:
        adapter = manager._adapter({**provider, "model": model})
        return adapter._turn_payload(prompt="x", cwd=str(tmp_path), manifest={}, previous_response_id=None,
                                     input_items=None, read_only=True)

    assert "max_output_tokens" not in payload_for("fake-model")
    assert "max_output_tokens" not in payload_for("big-model")
    # Missing discovered metadata also leaves the request unrestricted.
    store.set_provider_catalog("fake", {"fake-model": _info()}, refreshed_at=NOW)
    provider = dict(store.get_provider("fake"))
    assert "max_output_tokens" not in payload_for("fake-model")
    manager.save_provider(provider_id="fake", kind=provider["kind"], name=provider["name"],
        base_url=provider["base_url"], model=provider["model"], capabilities=provider["capabilities"],
        limits={"max_output_tokens": 75_000}, model_meta={"fake-model": {"max_output": 60_000}})
    provider = dict(store.get_provider("fake"))
    assert payload_for("fake-model")["max_output_tokens"] == 60_000
    assert payload_for("big-model")["max_output_tokens"] == 75_000
    store.close()


# Cost ---------------------------------------------------------------


def test_cost_math_and_unknown_price_is_never_zero() -> None:
    metrics = TaskMetrics()
    cost = metrics.record_provider(5, {"input_tokens": 1_000_000, "output_tokens": 500_000}, (3.0, 15.0))
    assert cost == pytest.approx(10.5)
    assert metrics.snapshot()["cost_usd"] == pytest.approx(10.5)
    # A free model is a known price of zero.
    assert metrics.record_provider(5, {"input_tokens": 10, "output_tokens": 10}, (0.0, 0.0)) == 0.0
    # Unknown price: no cost added, tokens tracked as unpriced.
    assert metrics.record_provider(5, {"input_tokens": 100, "output_tokens": 50}, (None, 15.0)) is None
    assert metrics.record_provider(5, {"input_tokens": 100, "output_tokens": 50}) is None
    snap = metrics.snapshot()
    assert snap["cost_usd"] == pytest.approx(10.5)
    assert snap["unpriced_tokens"] == 300
    assert snap["input_tokens"] == 1_000_000 + 10 + 200
    fresh = TaskMetrics()
    fresh.record_provider(5, {"input_tokens": 10, "output_tokens": 10})
    assert fresh.snapshot()["cost_usd"] is None  # unknown, not 0.0
    assert TaskMetrics(snap).snapshot()["cost_usd"] == pytest.approx(10.5)  # survives persistence


def _tree(tmp_path: Path):
    manager, store = build_manager(tmp_path, FakeAdapter())
    limits = manager._limits(None)
    root = store.create_task(prompt="root", cwd=str(tmp_path), provider_id="fake", model="fake-model",
                             limits=limits, mode="agent")
    kids = [store.create_task(prompt=f"kid{i}", cwd=str(tmp_path), provider_id="fake", model="fake-model",
                              limits=limits, mode="agent", parent_id=root["id"]) for i in range(2)]
    return manager, store, root, kids


def test_tree_rollup_sums_tokens_and_cost_across_two_children(tmp_path: Path) -> None:
    manager, store, root, kids = _tree(tmp_path)
    budget = manager.tree_budget
    assert budget.snapshot(root["id"])["used_cost_usd"] is None  # nothing priced yet
    budget.record_usage(root["id"], 1000, 0.01)
    budget.record_usage(kids[0]["id"], 2000, 0.02)
    budget.record_usage(kids[1]["id"], 500, None)  # unknown price
    for task_id in (root["id"], kids[0]["id"], kids[1]["id"]):
        snap = budget.snapshot(task_id)
        assert snap["used_tokens"] == 3500
        assert snap["used_cost_usd"] == pytest.approx(0.03)
        assert snap["unpriced_tokens"] == 500
    store.close()


def test_tree_budget_migrates_existing_tables(tmp_path: Path) -> None:
    manager, store, root, _kids = _tree(tmp_path)
    store._db.executescript("DROP TABLE agent_tree_budget; DROP TABLE agent_tree_member; DROP TABLE agent_tree_step;"
                            "DROP TABLE agent_tree_lease;"
                            "CREATE TABLE agent_tree_budget(root TEXT PRIMARY KEY,max_steps INTEGER NOT NULL,"
                            "max_seconds REAL NOT NULL,steps INTEGER NOT NULL DEFAULT 0,seconds REAL NOT NULL DEFAULT 0,"
                            "version INTEGER NOT NULL DEFAULT 1);")
    rebuilt = TreeBudget(store)
    rebuilt.record_usage(root["id"], 10, 0.5)
    assert rebuilt.snapshot(root["id"])["used_tokens"] == 10
    store.close()


class UsageAdapter(PlanAdapter):
    """PlanAdapter reporting the same token usage on every turn."""

    def __init__(self, script, usage) -> None:
        super().__init__(script)
        self.usage = usage

    async def turn(self, **kwargs: Any) -> ProviderTurn:
        return dataclasses.replace(await super().turn(**kwargs), usage=self.usage)


def test_task_cost_is_computed_from_the_routed_models_prices(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = UsageAdapter([("Done.", [])], {"input_tokens": 1000, "output_tokens": 2000})
        manager, store = _routed(tmp_path, adapter)
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        usage = next(e["payload"] for e in store.events(task["id"]) if e["type"] == "provider.usage")
        assert usage["cost_usd"] == pytest.approx((1000 * 1.0 + 2000 * 2.0) / 1_000_000)
        assert manager.task_usage(task["id"]) == {"tokens": 3000, "cost_usd": pytest.approx(0.005)}
        assert manager.tree_budget.snapshot(task["id"])["used_tokens"] == 3000
        await manager.close()
        store.close()

    asyncio.run(run())


def test_task_with_unknown_price_reports_null_cost(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = UsageAdapter([("Done.", [])], {"input_tokens": 10, "output_tokens": 20})
        manager, store = _routed(tmp_path, adapter)
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", model="big-model", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        assert manager.task_usage(task["id"]) == {"tokens": 30, "cost_usd": None}
        usage = next(e["payload"] for e in store.events(task["id"]) if e["type"] == "provider.usage")
        assert usage["cost_usd"] is None
        snap = manager.tree_budget.snapshot(task["id"])
        assert snap["used_cost_usd"] is None and snap["unpriced_tokens"] == 30
        await manager.close()
        store.close()

    asyncio.run(run())


# Fallback -----------------------------------------------------------


class RoutedAdapters:
    """adapter_factory handing each provider its own scripted adapter."""

    def __init__(self, **adapters: FakeAdapter) -> None:
        self.adapters = adapters
        self.models_used: list[tuple[str, str]] = []

    def __call__(self, provider: dict[str, Any], _key: str):
        self.models_used.append((provider["id"], provider["model"]))
        return self.adapters[provider["id"]]


class Failing(FakeAdapter):
    def __init__(self, status: int | None = 401, message: str = "denied") -> None:
        super().__init__()
        self.status, self.message, self.calls = status, message, 0

    async def turn(self, **_kwargs: Any) -> ProviderTurn:
        self.calls += 1
        raise ProviderError(self.message, status_code=self.status)


def _fallback_manager(tmp_path: Path, primary, fallback_adapters, fallbacks):
    factory = RoutedAdapters(fake=primary, **fallback_adapters)
    manager, store = _routed(tmp_path, FakeAdapter(), {
        "orchestrator": {"provider_id": "fake", "model": "", "fallbacks": fallbacks}})
    manager._adapter_factory = factory
    return manager, store, factory


def test_fallback_walks_to_the_next_provider_once_and_emits_an_event(tmp_path: Path) -> None:
    async def run() -> None:
        primary = Failing(401)
        backup = UsageAdapter([("From backup.", [])], {"input_tokens": 1_000_000, "output_tokens": 0})
        manager, store, factory = _fallback_manager(
            tmp_path, primary, {"other": backup},
            [{"provider_id": "fake", "model": "fake-model"},  # same target as primary: skipped
             {"provider_id": "gone", "model": ""},            # stale: skipped
             {"provider_id": "other", "model": "o-1"}])
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask")
        done = await wait_for_status(store, task["id"], "completed")
        assert done["result"] == "From backup."
        assert primary.calls == 1  # a 401 is not retried
        events = [e["payload"] for e in store.events(task["id"]) if e["type"] == "provider.fallback"]
        assert events == [{"from": {"provider_id": "fake", "model": "fake-model"},
                           "to": {"provider_id": "other", "model": "o-1"},
                           "reason_class": "non_retryable", "status_code": 401}]
        # Cost uses the model that actually answered: o-1 at $0.5 per 1M input tokens.
        assert manager.task_usage(task["id"])["cost_usd"] == pytest.approx(0.5)
        # The task itself keeps its own route for the next turn.
        assert store.get_task(task["id"])["model"] == "fake-model"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_fallback_runs_after_same_provider_retries_are_exhausted(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr("termx.agent.retry._BASE_DELAY_S", 0.0)
        primary = Failing(503, "overloaded")
        backup = PlanAdapter([("Recovered.", [])])
        manager, store, _factory = _fallback_manager(
            tmp_path, primary, {"other": backup}, [{"provider_id": "other", "model": ""}])
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask")
        await wait_for_status(store, task["id"], "completed")
        assert primary.calls == 3  # all retries used first
        assert len([e for e in store.events(task["id"]) if e["type"] == "provider.retry"]) == 2
        assert len([e for e in store.events(task["id"]) if e["type"] == "provider.fallback"]) == 1
        await manager.close()
        store.close()

    asyncio.run(run())


def test_every_fallback_failing_fails_the_task_with_the_original_error(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store, _factory = _fallback_manager(
            tmp_path, Failing(401, "primary denied"), {"other": Failing(401, "backup denied")},
            [{"provider_id": "other", "model": ""}])
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask")
        failed = await wait_for_status(store, task["id"], "failed")
        assert "primary denied" in failed["error"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_no_fallbacks_configured_fails_as_before(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store, _factory = _fallback_manager(tmp_path, Failing(401), {"other": PlanAdapter([])}, [])
        task = await manager.create_task(prompt="x", cwd=str(tmp_path), provider_id="fake", mode="ask")
        await wait_for_status(store, task["id"], "failed")
        assert not [e for e in store.events(task["id"]) if e["type"] == "provider.fallback"]
        await manager.close()
        store.close()

    asyncio.run(run())


# Auto-mode token and cost ceilings ----------------------------------


async def _auto_start(tmp_path: Path, monkeypatch, adapter, ceilings, model="fake-model"):
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    work = tmp_path / "work"
    work.mkdir()
    manager, store = _routed(work, adapter)
    task = await manager.create_task(prompt="Do the thing", cwd=str(repo), provider_id="fake", model=model,
                                     execution_mode="worktree", auto={"enabled": True, "ceilings": ceilings})
    return manager, store, task


def test_auto_token_usage_exceeds_legacy_ceiling_without_pausing(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        adapter = UsageAdapter([("", [_shell("c1", "rm -rf one")]), ("", [_shell("c2", "rm -rf two")])],
                               {"input_tokens": 700, "output_tokens": 400})
        manager, store, task = await _auto_start(tmp_path, monkeypatch, adapter, {"tokens": 1000})
        await _approve_plan(manager, store, task["id"])
        await wait_for_status(store, task["id"], "completed")
        assert not _events(store, task["id"], "auto.ceiling")
        assert _auto(store, task["id"])["used"]["tokens"] >= 2200
        await manager.close()
        store.close()

    asyncio.run(run())


def test_auto_priced_usage_exceeds_legacy_cost_ceiling_without_pausing(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        # fake-model: $1 in / $2 out per 1M. One turn = $3.
        adapter = UsageAdapter([("", [_shell("c1", "rm -rf one")]), ("", [_shell("c2", "rm -rf two")])],
                               {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        manager, store, task = await _auto_start(tmp_path, monkeypatch, adapter, {"cost_usd": 2.5, "tokens": 1_000_000_000})
        await _approve_plan(manager, store, task["id"])
        await wait_for_status(store, task["id"], "completed")
        assert not _events(store, task["id"], "auto.ceiling")
        assert _auto(store, task["id"])["used"]["cost_usd"] >= 6.0
        await manager.close()
        store.close()

    asyncio.run(run())


def test_cost_ceiling_skips_a_model_with_no_known_price(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        adapter = UsageAdapter([("", [_shell("c1", "rm -rf one")]), ("All done.", [])],
                               {"input_tokens": 5_000_000, "output_tokens": 5_000_000})
        # big-model has no price in the catalog.
        manager, store, task = await _auto_start(tmp_path, monkeypatch, adapter, {"cost_usd": 0.01, "tokens": 1_000_000_000}, model="big-model")
        await _approve_plan(manager, store, task["id"])
        await wait_for_status(store, task["id"], "completed")
        assert not _events(store, task["id"], "auto.ceiling")
        used = _auto(store, task["id"])["used"]
        assert used["cost_usd"] == 0 and used["tokens"] == 20_000_000
        await manager.close()
        store.close()

    asyncio.run(run())


def test_chatgpt_models_feed_the_same_catalog_path(tmp_path: Path) -> None:
    async def run() -> None:
        manager, store = _routed(tmp_path, FakeAdapter())
        store.put_provider("chatgpt-a", kind="chatgpt", name="ChatGPT", base_url="https://chatgpt.example",
                           model="", capabilities=["shell"])

        class Accounts:
            async def models(self, account_id: str):
                return [{"slug": "gpt-5", "display_name": "GPT-5"}]

        manager.chatgpt = Accounts()
        provider = await manager.refresh_provider_models("chatgpt-a")
        assert provider["models"] == ["gpt-5"]
        assert provider["model_catalog"]["gpt-5"]["source"] == "chatgpt"
        assert provider["model_catalog"]["gpt-5"]["context_window"] is None  # unknown, not defaulted
        await manager.close()
        store.close()

    asyncio.run(run())
