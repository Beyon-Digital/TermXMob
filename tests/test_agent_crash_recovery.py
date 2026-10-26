"""Host restart safety using only a scratch SQLite ledger and no model calls."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from termx.agent.manager import AgentManager
from termx.agent.secrets import CredentialStore
from termx.agent.store import AgentStore


def _task(store: AgentStore, *, status: str = "running", **kwargs: Any) -> dict[str, Any]:
    return store.create_task(
        prompt="synthetic interrupted work", cwd=str(store.path.parent),
        provider_id="fixture", model="fixture", mode="agent", status=status,
        limits={"max_steps": 24, "max_seconds": 900, "shell_timeout_s": 120},
        **kwargs,
    )


def _checkpoint(store: AgentStore, task_id: str, state: str = "committed") -> dict[str, Any]:
    return store.create_checkpoint(
        task_id, kind="execution", side_effect_state=state, pending_call_id="effect-1",
        payload={
            "call": {"type": "function", "call_id": "effect-1", "name": "write_file",
                     "arguments": {"path": "fixture.txt", "content": "fixture"}},
            "history": [], "remaining_calls": [], "step": 0,
        },
    )


def _unexpected_adapter(*args: Any) -> Any:
    raise AssertionError("Crash recovery must not contact a provider in this test")


@pytest.fixture
def ledger(tmp_path: Path):
    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    managers: list[AgentManager] = []

    def restart() -> AgentManager:
        manager = AgentManager(store, CredentialStore(memory={}), desktop=None,
                               adapter_factory=_unexpected_adapter)
        managers.append(manager)
        return manager

    yield store, restart
    for manager in managers:
        asyncio.run(manager.close())
    store.close()


@pytest.mark.parametrize("older_checkpoint", [False, True])
def test_lost_tool_request_closes_same_approval_without_replay(ledger, older_checkpoint: bool) -> None:
    store, restart = ledger
    task = _task(store, status="awaiting_approval")
    if older_checkpoint:
        _checkpoint(store, task["id"])
    approval = store.create_approval(task["id"], "tool", {
        "call": {"call_id": "private-lost", "name": "write_file", "arguments": "[redacted]"},
    })
    manager = restart()
    current = store.get_task(task["id"])
    assert current["status"] == "failed"
    assert "fresh instruction" in current["error"]
    assert store.get_approval(approval["id"])["status"] == "denied"
    assert manager.pending_recoveries() == []
    assert manager._workers == {}
    events = store.events(task["id"])
    settled = [event for event in events if event["type"] == "approval.resolved"]
    assert len(settled) == 1
    assert settled[0]["payload"]["approval"]["id"] == approval["id"]
    assert settled[0]["payload"]["reason"] == "host_restart_private_request_lost"
    with pytest.raises(ValueError):
        asyncio.run(manager.resolve_approval(task["id"], approval["id"], "approved"))
    # A second restart must not duplicate the decision or resurrect the task.
    restart()
    assert store.events(task["id"]) == events
    assert not (Path(task["cwd"]) / "fixture.txt").exists()


def test_lost_tool_request_cannot_hide_behind_durable_plan_gate(ledger) -> None:
    store, restart = ledger
    task = _task(store, status="awaiting_approval")
    plan = store.create_approval(task["id"], "plan", {"summary": "synthetic plan"})
    tool = store.create_approval(task["id"], "tool", {"call": {"call_id": "missing-private"}})
    restart()
    assert store.get_task(task["id"])["status"] == "failed"
    assert store.get_approval(plan["id"])["status"] == "denied"
    assert store.get_approval(tool["id"])["status"] == "denied"


def test_second_crash_during_approval_settlement_cannot_enable_old_checkpoint(ledger, monkeypatch) -> None:
    store, restart = ledger
    manager = restart()
    task = _task(store, status="awaiting_approval")
    checkpoint = _checkpoint(store, task["id"])
    approval = store.create_approval(task["id"], "tool", {"call": {"call_id": "private-lost"}})
    original = store.resolve_approval

    def settle_then_crash(approval_id: str, decision: str):
        original(approval_id, decision)
        raise RuntimeError("synthetic second host crash")

    monkeypatch.setattr(store, "resolve_approval", settle_then_crash)
    with pytest.raises(RuntimeError, match="second host crash"):
        manager._recover_interrupted_tasks()
    assert store.get_approval(approval["id"])["status"] == "denied"
    assert store.get_task(task["id"])["status"] == "awaiting_approval"
    assert store.get_task(task["id"])["runtime"]["restart_lost_tool_approvals"] == [approval["id"]]
    monkeypatch.setattr(store, "resolve_approval", original)
    manager._recover_interrupted_tasks()
    assert store.get_task(task["id"])["status"] == "failed"
    assert manager.pending_recoveries() == []
    assert store.get_checkpoint(checkpoint["id"])["side_effect_state"] == "committed"
    assert manager._workers == {}


@pytest.mark.parametrize("kind", ["plan", "budget"])
def test_reconstructable_plan_and_budget_boundaries_stay_pending(ledger, kind: str) -> None:
    store, restart = ledger
    task = _task(store, status="awaiting_approval")
    approval = store.create_approval(task["id"], kind, {"summary": "synthetic safe boundary"})
    manager = restart()
    assert store.get_task(task["id"])["status"] == "awaiting_approval"
    assert store.get_approval(approval["id"])["status"] == "pending"
    assert manager._workers == {}


def test_parent_wrapper_mirrors_lost_child_request_with_same_binding(ledger) -> None:
    store, restart = ledger
    parent = _task(store, status="awaiting_approval")
    _checkpoint(store, parent["id"])
    child = _task(store, parent_id=parent["id"], status="awaiting_approval")
    inner = store.create_approval(child["id"], "tool", {"call": {"call_id": "child-private-lost"}})
    wrapper = store.create_approval(parent["id"], "tool", {
        "child_id": child["id"], "child_approval_id": inner["id"],
    })
    manager = restart()
    assert store.get_task(child["id"])["status"] == "failed"
    assert store.get_approval(inner["id"])["status"] == "denied"
    assert store.get_approval(wrapper["id"])["status"] == "denied"
    assert store.get_task(parent["id"])["status"] == "recovering"
    assert manager.pending_recoveries() == [parent["id"]]


def test_parent_wrapper_cannot_reconstruct_lost_native_process_approval(ledger) -> None:
    store, restart = ledger
    parent = _task(store, status="awaiting_approval")
    _checkpoint(store, parent["id"])
    child = _task(store, parent_id=parent["id"], status="awaiting_approval", engine="codex")
    inner = store.create_approval(child["id"], "tool", {"call": {"call_id": "native-action"}})
    wrapper = store.create_approval(parent["id"], "tool", {
        "child_id": child["id"], "child_approval_id": inner["id"],
    })
    manager = restart()
    # The gateway, rather than the internal manager, owns the inner decision.
    assert store.get_approval(inner["id"])["status"] == "pending"
    assert store.get_approval(wrapper["id"])["status"] == "denied"
    assert wrapper["id"] not in manager._pending_approval_calls
    assert store.get_task(child["id"])["status"] == "awaiting_approval"


def test_nested_wrappers_settle_leaf_to_root_in_one_restart(ledger) -> None:
    store, restart = ledger
    root = _task(store, status="awaiting_approval")
    middle = _task(store, parent_id=root["id"], status="awaiting_approval")
    leaf = _task(store, parent_id=middle["id"], status="awaiting_approval")
    _checkpoint(store, root["id"])
    _checkpoint(store, middle["id"])
    inner = store.create_approval(leaf["id"], "tool", {"call": {"call_id": "leaf-private-lost"}})
    middle_wrapper = store.create_approval(middle["id"], "tool", {
        "child_id": leaf["id"], "child_approval_id": inner["id"],
    })
    root_wrapper = store.create_approval(root["id"], "tool", {
        "child_id": middle["id"], "child_approval_id": middle_wrapper["id"],
    })
    # Force root-first storage order, the previous nested-wrapper failure.
    store.update_task(root["id"], updated_at=200.0)
    store.update_task(middle["id"], updated_at=100.0)
    store.update_task(leaf["id"], updated_at=50.0)
    manager = restart()
    assert store.get_task(leaf["id"])["status"] == "failed"
    assert store.get_task(root["id"])["status"] == "recovering"
    assert store.get_task(middle["id"])["status"] == "recovering"
    for approval in (inner, middle_wrapper, root_wrapper):
        assert store.get_approval(approval["id"])["status"] == "denied"
        assert approval["id"] not in manager._pending_approval_calls


def test_foreign_child_binding_cannot_reconstruct_execution_authority(ledger) -> None:
    store, restart = ledger
    parent = _task(store, status="awaiting_approval")
    foreign = _task(store, status="awaiting_approval", engine="codex")
    inner = store.create_approval(foreign["id"], "tool", {"call": {"call_id": "foreign"}})
    wrapper = store.create_approval(parent["id"], "tool", {
        "child_id": foreign["id"], "child_approval_id": inner["id"],
    })
    manager = restart()
    assert store.get_task(parent["id"])["status"] == "failed"
    assert store.get_approval(wrapper["id"])["status"] == "denied"
    assert store.get_approval(inner["id"])["status"] == "pending"
    assert manager._pending_approval_calls == {}


def test_wrapper_for_terminal_child_does_not_remain_actionable(ledger) -> None:
    store, restart = ledger
    parent = _task(store, status="awaiting_approval")
    _checkpoint(store, parent["id"])
    child = _task(store, parent_id=parent["id"], status="cancelled", engine="codex")
    inner = store.create_approval(child["id"], "tool", {"call": {"call_id": "cancelled-native"}})
    wrapper = store.create_approval(parent["id"], "tool", {
        "child_id": child["id"], "child_approval_id": inner["id"],
    })
    manager = restart()
    assert store.get_approval(wrapper["id"])["status"] == "denied"
    assert store.get_task(child["id"])["status"] == "cancelled"
    assert wrapper["id"] not in manager._pending_approval_calls


@pytest.mark.parametrize("checkpoint_state", [None, "committed", "running"])
def test_restored_child_budget_wrapper_cannot_fabricate_parent_worker(ledger, monkeypatch, checkpoint_state) -> None:
    store, restart = ledger
    parent = _task(store, status="awaiting_approval")
    if checkpoint_state:
        _checkpoint(store, parent["id"], checkpoint_state)
    child = _task(store, parent_id=parent["id"], status="awaiting_approval")
    inner = store.create_approval(child["id"], "budget", {"suggested_limits": {"max_steps": 48}})
    wrapper = store.create_approval(parent["id"], "budget", {
        "child_id": child["id"], "child_approval_id": inner["id"],
    })
    manager = restart()
    expected = {None: "failed", "committed": "recovering", "running": "recovery_confirmation_required"}[checkpoint_state]
    assert store.get_task(parent["id"])["status"] == expected
    assert (parent["id"] in manager.pending_recoveries()) is (checkpoint_state == "committed")
    driven: list[str] = []

    async def drive(task_id: str, **kwargs):
        driven.append(task_id)
        store.update_task(task_id, status="completed")

    monkeypatch.setattr(manager, "_drive", drive)

    async def resolve() -> None:
        await manager.resolve_approval(parent["id"], wrapper["id"], "approved")
        assert store.get_task(parent["id"])["status"] == expected
        assert parent["id"] not in manager._workers
        assert store.get_approval(inner["id"])["status"] == "approved"
        assert store.get_approval(wrapper["id"])["status"] == "approved"
        await asyncio.gather(*manager._workers.values())

    asyncio.run(resolve())
    assert driven == [child["id"]]


def test_own_budget_gate_keeps_restored_parent_paused_after_child_decision(ledger, monkeypatch) -> None:
    store, restart = ledger
    parent = _task(store, status="awaiting_approval")
    own = store.create_approval(parent["id"], "budget", {"suggested_limits": {"max_steps": 48}})
    child = _task(store, parent_id=parent["id"], status="awaiting_approval")
    inner = store.create_approval(child["id"], "budget", {"suggested_limits": {"max_steps": 48}})
    wrapper = store.create_approval(parent["id"], "budget", {
        "child_id": child["id"], "child_approval_id": inner["id"],
    })
    manager = restart()

    async def drive(task_id: str, **kwargs):
        store.update_task(task_id, status="completed")

    monkeypatch.setattr(manager, "_drive", drive)

    async def resolve() -> None:
        await manager.resolve_approval(parent["id"], wrapper["id"], "approved")
        assert store.get_task(parent["id"])["status"] == "awaiting_approval"
        assert store.get_approval(own["id"])["status"] == "pending"
        assert parent["id"] not in manager._workers
        await asyncio.gather(*manager._workers.values())

    asyncio.run(resolve())


@pytest.mark.parametrize("status", ["cancelling", "cancelled"])
def test_cancel_intent_survives_restart_with_resumable_checkpoint(ledger, status: str) -> None:
    store, restart = ledger
    task = _task(store, status=status)
    checkpoint = _checkpoint(store, task["id"], "prepared")
    manager = restart()
    assert store.get_task(task["id"])["status"] == "cancelled"
    assert store.get_checkpoint(checkpoint["id"])["side_effect_state"] == "prepared"
    assert manager.pending_recoveries() == []
    assert manager._workers == {}


def test_old_active_task_is_not_hidden_by_500_recent_terminal_tasks(ledger) -> None:
    store, restart = ledger
    old = _task(store)
    store.update_task(old["id"], updated_at=1.0)
    for _ in range(501):
        _task(store, status="completed")
    manager = restart()
    assert store.get_task(old["id"])["status"] == "failed"
    assert manager._workers == {}


def test_recovery_queue_reads_all_active_pages(ledger) -> None:
    store, restart = ledger
    manager = restart()
    expected = {_task(store, status="recovering")["id"] for _ in range(105)}
    native = _task(store, status="recovering", engine="codex")
    assert set(manager.pending_recoveries()) == expected
    assert native["id"] not in manager.pending_recoveries()


def test_duplicate_confirm_rejected_before_constructing_second_resume(ledger, monkeypatch) -> None:
    store, restart = ledger
    task = _task(store)
    _checkpoint(store, task["id"], "running")
    manager = restart()

    async def run() -> None:
        release = asyncio.Event()
        invocations: list[str] = []

        def resume(task_id: str, checkpoint: dict[str, Any], decision: str):
            invocations.append(task_id)

            async def wait_for_release():
                await release.wait()

            return wait_for_release()

        monkeypatch.setattr(manager, "_resume_execution", resume)
        blocked = await manager.recover_task(task["id"], confirm=False)
        assert blocked["status"] == "recovery_confirmation_required"
        assert invocations == []
        await manager.recover_task(task["id"], confirm=True)
        try:
            with pytest.raises(ValueError, match="already running"):
                await manager.recover_task(task["id"], confirm=True)
            assert invocations == [task["id"]]
        finally:
            release.set()
            await asyncio.gather(*manager._workers.values())

    asyncio.run(run())


def test_confirmed_missing_result_dispatches_call_instead_of_dropping_it(ledger, monkeypatch) -> None:
    store, restart = ledger
    task = _task(store)
    checkpoint = _checkpoint(store, task["id"], "completed_uncommitted")
    manager = restart()
    executed: list[str] = []

    async def run_calls(task_id, calls, history, step, started_at, **kwargs):
        executed.extend(call.call_id for call in calls)
        store.update_task(task_id, status="awaiting_approval")
        return True, history, step

    monkeypatch.setattr(manager, "_run_calls", run_calls)
    asyncio.run(manager._resume_execution(task["id"], checkpoint, "confirm_required"))
    assert executed == ["effect-1"]
    assert store.get_task(task["id"])["status"] == "awaiting_approval"
