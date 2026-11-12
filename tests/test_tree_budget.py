"""Durable task-tree usage accounting without task execution quotas."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import asyncio
import sqlite3

import pytest

from termx.agent.store import AgentStore
from termx.agent.tree_budget import TreeBudget


@pytest.fixture
def tree(tmp_path):
    store = AgentStore(tmp_path / "agents.db", tmp_path / "artifacts")
    clock = [100.0]
    limits = {"max_steps": 3, "max_seconds": 10, "shell_timeout_s": 10,
              "max_parallel_subagents": 3, "max_subagents_total": 8}
    parent = store.create_task(prompt="parent", cwd=str(tmp_path), provider_id="fixture",
                              model="fixture", limits=limits)
    children = [store.create_task(prompt=f"child{i}", cwd=str(tmp_path), provider_id="fixture",
                    model="fixture", limits=limits, parent_id=parent["id"]) for i in range(2)]
    budget = TreeBudget(store, clock=lambda: clock[0])
    budget.bind(parent["id"])
    for child in children:
        budget.bind(child["id"])
    yield SimpleNamespace(store=store, budget=budget, clock=clock, parent=parent, children=children)
    store.close()


def test_concurrent_parent_children_record_every_call_without_a_shared_quota(tree):
    ids = [tree.parent["id"], *[child["id"] for child in tree.children]]
    with ThreadPoolExecutor(max_workers=9) as executor:
        results = list(executor.map(lambda item: tree.budget.reserve(item[0], [f"call-{item[1]}"]),
                                    [(ids[i % 3], i) for i in range(300)]))
    assert results == [None] * 300
    for task in ids:
        snapshot = tree.budget.snapshot(task)
        assert snapshot["used_steps"] == 300
        assert snapshot["max_steps"] is None and snapshot["reason"] is None


def test_call_reservations_are_idempotent_and_child_positions_are_independent(tree):
    parent, child = tree.parent["id"], tree.children[0]["id"]
    assert tree.budget.reserve(parent, ["call-0", "call-1"]) is None
    assert tree.budget.reserve(parent, ["call-0", "call-1"]) is None
    assert tree.budget.reserve(child, ["call-0", "call-1"]) is None
    assert tree.budget.snapshot(parent)["used_steps"] == 4
    assert tree.budget.reserve(parent, ["call-2"]) is None
    assert tree.budget.snapshot(parent)["used_steps"] == 5


def test_time_is_sum_of_active_workers_without_a_four_hour_deadline(tree):
    parent, child = tree.parent["id"], tree.children[0]["id"]
    tree.budget.start(parent)
    tree.budget.start(child)
    tree.clock[0] += 18_000
    snapshot = tree.budget.snapshot(parent)
    assert snapshot["used_execution_seconds"] == 36_000
    assert snapshot["max_execution_seconds"] is None and snapshot["reason"] is None
    tree.budget.stop(parent)
    tree.budget.stop(child)
    tree.clock[0] += 100
    assert tree.budget.snapshot(child)["used_execution_seconds"] == 36_000
    tree.budget.start(child)
    tree.clock[0] += 4
    assert tree.budget.reserve(child, ["later"]) is None
    assert tree.budget.snapshot(parent)["used_execution_seconds"] == 36_004


def test_restart_preserves_usage_and_legacy_columns_without_manufacturing_exhaustion(tree):
    parent = tree.parent["id"]
    tree.budget.reserve(parent, ["completed"])
    tree.budget.record_usage(parent, 100, 0.5)
    tree.budget.start(tree.children[0]["id"])
    tree.clock[0] += 2
    assert tree.budget.snapshot(parent)["used_execution_seconds"] == 2
    tree.store._db.execute("UPDATE agent_tree_budget SET max_steps=1,max_seconds=1 WHERE root=?", (parent,))
    tree.store._db.commit()
    tree.clock[0] += 18_000
    recovered = TreeBudget(tree.store, clock=lambda: tree.clock[0])
    snapshot = recovered.snapshot(parent)
    assert snapshot["used_steps"] == 1 and snapshot["used_execution_seconds"] == 2
    assert snapshot["used_tokens"] == 100 and snapshot["used_cost_usd"] == 0.5
    assert snapshot["reason"] is None and snapshot["active_workers"] == 0
    stored = tree.store._db.execute("SELECT max_steps,max_seconds FROM agent_tree_budget WHERE root=?", (parent,)).fetchone()
    assert tuple(stored) == (1, 1)
    assert recovered.reserve(parent, ["after-restart"]) is None
    assert recovered.snapshot(parent)["used_steps"] == 2


def test_independent_store_connections_record_calls_atomically(tree, tmp_path):
    other = AgentStore(tmp_path / "agents.db", tmp_path / "artifacts")
    try:
        other_budget = TreeBudget(other, clock=lambda: tree.clock[0])
        parent = tree.parent["id"]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda i: (tree.budget if i % 2 else other_budget).reserve(parent, [f"call-{i}"]), range(40)))
        assert results == [None] * 40
        assert tree.budget.snapshot(parent)["used_steps"] == 40
    finally:
        other.close()


def test_invalid_renewal_and_cyclic_ancestry_leave_no_open_transaction(tree):
    parent = tree.parent["id"]
    tree.budget.reserve(parent, ["reserved"])
    with pytest.raises(ValueError, match="changed"):
        tree.budget.renew(parent, {}, version=999)
    with pytest.raises(ValueError, match="integer"):
        tree.budget.renew(parent, {"max_seconds": True}, version=1)
    assert not tree.store._db.in_transaction
    assert tree.budget.snapshot(parent)["version"] == 1
    child = tree.store.create_task(prompt="cycle", cwd=parent, provider_id="fixture", model="fixture", limits={})
    tree.store._db.execute("UPDATE tasks SET parent_id=? WHERE id=?", (child["id"], child["id"]))
    tree.store._db.commit()
    with pytest.raises(ValueError, match="cycle"):
        tree.budget.bind(child["id"])
    assert not tree.store._db.in_transaction


def test_legacy_renewal_retains_accumulated_usage(tree):
    parent = tree.parent["id"]
    tree.budget.start(parent)
    tree.clock[0] += 5
    tree.budget.reserve(parent, ["reserved"])
    tree.budget.record_usage(parent, 100, 0.25)
    snapshot = tree.budget.renew(parent, {"max_steps": 1, "max_seconds": 1}, version=1)
    assert snapshot["used_steps"] == 1 and snapshot["used_execution_seconds"] == 5
    assert snapshot["used_tokens"] == 100 and snapshot["used_cost_usd"] == 0.25
    assert snapshot["version"] == 2 and snapshot["reason"] is None


def test_budget_approval_and_receipt_roll_back_together(tree):
    parent = tree.parent["id"]
    approval = tree.store.create_approval(parent, "budget", {"tree_budget": tree.budget.snapshot(parent)})
    tree.store._db.execute("CREATE TRIGGER reject_grant BEFORE UPDATE ON approvals BEGIN SELECT RAISE(ABORT,'fixture decision write failed'); END")
    tree.store._db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="decision write failed"):
        tree.budget.approve(parent, approval["id"], {}, version=1)
    assert tree.budget.snapshot(parent)["version"] == 1
    assert tree.store.get_approval(approval["id"])["status"] == "pending"
    assert not (tree.store.get_task(parent).get("runtime") or {}).get("tree_budget_resume")
    assert not tree.store._db.in_transaction


def test_cancelled_provider_releases_lease_and_keeps_usage(tmp_path):
    from test_agent import build_manager, wait_for_status
    from termx.agent.providers import ProviderTurn

    class Adapter:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = False

        async def turn(self, **kwargs):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return ProviderTurn("unused", "", [], {})

    async def run():
        adapter = Adapter()
        manager, store = build_manager(tmp_path, adapter)
        clock = [100.0]
        manager.tree_budget.clock = lambda: clock[0]
        try:
            task = await manager.create_task(prompt="Cancel pending provider", cwd=str(tmp_path), provider_id="fake", mode="ask")
            await asyncio.wait_for(adapter.started.wait(), 2)
            manager.tree_budget.reserve(task["id"], ["already-reserved"])
            manager.tree_budget.record_usage(task["id"], 20, 0.5)
            clock[0] += 18_000
            before = manager.tree_budget.snapshot(task["id"])
            assert before["used_execution_seconds"] == 18_000 and before["reason"] is None
            manager.cancel(task["id"])
            await wait_for_status(store, task["id"], "cancelled")
            after = manager.tree_budget.snapshot(task["id"])
            assert adapter.cancelled and after["active_workers"] == 0
            assert after["used_steps"] == 1 and after["used_tokens"] == 20
            assert after["used_cost_usd"] == 0.5
            assert not [a for a in store.approvals(task["id"]) if a["kind"] == "budget"]
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())


def test_concurrent_child_work_continues_beyond_legacy_shared_settings(tmp_path):
    from collections import defaultdict
    from test_agent import build_manager, wait_for_status
    from termx.agent.providers import ProviderCall, ProviderTurn

    class Adapter:
        def __init__(self):
            self.count = defaultdict(int)
            self.children = 0
            self.ready = asyncio.Event()
            self.parent = asyncio.Event()

        async def turn(self, **kwargs):
            prompt = kwargs["prompt"]
            self.count[prompt] += 1
            if prompt == "Parent":
                await self.parent.wait()
                return ProviderTurn("parent", "Parent finished", [], {})
            if self.count[prompt] == 1:
                self.children += 1
                if self.children == 2:
                    self.ready.set()
                await self.ready.wait()
                return ProviderTurn(prompt, "", [ProviderCall("function", prompt + "-" + str(i), "read_file", {"path": "read.txt"}) for i in range(2)], {})
            return ProviderTurn(prompt + "-done", "Child finished", [], {})

    async def run():
        (tmp_path / "read.txt").write_text("Scoped fixture")
        adapter = Adapter()
        manager, store = build_manager(tmp_path, adapter)
        try:
            parent = await manager.create_task(prompt="Parent", cwd=str(tmp_path), provider_id="fake", mode="ask", limits={"max_steps": 1, "max_seconds": 1})
            children = [await manager.create_task(prompt=name, cwd=str(tmp_path), provider_id="fake", mode="ask", parent_id=parent["id"], limits={"max_steps": 1, "max_seconds": 1}) for name in ("Child A", "Child B")]
            for child in children:
                await wait_for_status(store, child["id"], "completed")
            assert manager.tree_budget.snapshot(parent["id"])["used_steps"] == 4
            assert all(not store.approvals(child["id"]) for child in children)
            adapter.parent.set()
            await wait_for_status(store, parent["id"], "completed")
        finally:
            await manager.close()
            store.close()
    asyncio.run(run())
