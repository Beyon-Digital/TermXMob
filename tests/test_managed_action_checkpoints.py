"""Managed execution boundaries using actual exact approval and filesystem tools.

SQLite backup captures the live, durable boundary before the original coroutine
is cleaned up. Reopening that backup tests restart classification without faking
checkpoint/review rows. The separate process-crash harness proves SIGKILL.
Only provider inference is scripted; the review broker and write_file are real.
"""
import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from termx.browser.service import BrowserService
from test_agent import (
    _FanOutAdapter,
    _fn,
    build_manager,
    wait_for_pending_approval,
    wait_for_status,
)


def _backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as original, sqlite3.connect(destination) as copied:
        original.backup(copied)


def _bind(service: BrowserService, task_id: str) -> None:
    service.records.put("agent-task-authority", task_id, {
        "id": task_id, "principal_id": "owner", "session_id": "sid",
        "project_id": "project", "policy_version": 1,
    })


async def _approve_exact_write(manager, store, service, cwd):
    task = await manager.create_task(
        prompt="Managed write", cwd=str(cwd), provider_id="fake",
        on_created=lambda identifier: _bind(service, identifier),
    )
    await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
    _, approval = await wait_for_pending_approval(store, task["id"], "tool")
    assert approval["payload"]["browser_review"]["status"] == "needs_user"
    assert store.latest_checkpoint(task["id"], kind="execution") is None
    await manager.resolve_approval(task["id"], approval["id"], "approved")
    return task


@pytest.mark.parametrize("boundary", ["prepared", "running", "completed_uncommitted"])
def test_real_managed_write_checkpoint_and_exact_receipt_restart_boundary(tmp_path, boundary):
    async def run():
        original_root = tmp_path / "original"
        original_root.mkdir()
        adapter = _FanOutAdapter({"Managed write": ([[_fn(
            "exact-write", "write_file", path="once.txt", content="One actual write\n", expected_revision=None,
        )]], 0.0)})
        manager, store = build_manager(original_root, adapter)
        service = BrowserService(original_root / "browser", session_valid=lambda *_: True)
        manager.browser = service
        entered, release = asyncio.Event(), asyncio.Event()
        attempts = []
        spec = manager._tools.get("write_file")
        assert spec is not None
        original_execute = service.review.execute
        restored = restored_store = restored_service = None

        async def real_write(call, ctx):
            attempts.append(call.call_id)
            result = await spec.execute(call, ctx)
            assert result.result["ok"] is True
            if boundary == "running":
                entered.set()
                await release.wait()
            return result

        async def gated_execute(*args, operation, **kwargs):
            if boundary == "prepared":
                entered.set()
                await release.wait()

            async def after_result():
                result = await operation()
                if boundary == "completed_uncommitted":
                    entered.set()
                    await release.wait()
                return result

            return await original_execute(*args, operation=after_result, **kwargs)

        manager._tools.register(replace(spec, execute=real_write))
        service.review.execute = gated_execute
        try:
            task = await _approve_exact_write(manager, store, service, original_root)
            await asyncio.wait_for(entered.wait(), 5)
            checkpoint = store.latest_checkpoint(task["id"], kind="execution")
            assert checkpoint["side_effect_state"] == boundary
            assert checkpoint["pending_call_id"] == "exact-write"
            binding = checkpoint["payload"]["action_review"]
            assert set(binding) == {"id", "fingerprint"}
            receipt = service.records.get("review", binding["id"])
            assert receipt["fingerprint"] == binding["fingerprint"]
            assert receipt["status"] == ("permitted" if boundary == "prepared" else "executing")
            assert ("permit" in receipt) == (boundary == "prepared")
            assert attempts == ([] if boundary == "prepared" else ["exact-write"])
            if boundary == "prepared":
                assert not (original_root / "once.txt").exists()
            else:
                assert (original_root / "once.txt").read_text() == "One actual write\n"
            if boundary == "completed_uncommitted":
                assert checkpoint["result"]["finished"]["result"]["ok"] is True
                assert not [event for event in store.events(task["id"]) if event["type"] == "tool.finished"]

            # Real SQLite snapshot, including an actual consumed receipt. The
            # blocked original cannot run while the independent copy is read.
            restored_root = tmp_path / "reopened"
            _backup(store.path, restored_root / "agent.sqlite3")
            _backup(service.records.path, restored_root / "browser" / "browser.sqlite3")
            restored, restored_store = build_manager(
                restored_root, _FanOutAdapter({"Managed write": ([], 0.0), "Fresh instruction": ([[_fn(
                    "inspect-state", "read_file", path="once.txt",
                )]], 0.0)}),
            )
            restored_service = BrowserService(restored_root / "browser", session_valid=lambda *_: True)
            restored.browser = restored_service
            invalidated = restored_service.records.get("review", binding["id"])
            assert invalidated["status"] == "invalidated"
            if boundary == "completed_uncommitted":
                assert restored_store.get_task(task["id"])["status"] == "recovering"
                await restored.recover_task(task["id"])
                _, budget = await wait_for_pending_approval(restored_store, task["id"], "budget")
                assert budget["payload"]["reason"] == "shared_max_seconds"
                allowance = budget["payload"]["tree_budget"]
                assert allowance["used_execution_seconds"] >= allowance["max_execution_seconds"]
                assert restored_store.latest_checkpoint(task["id"], kind="execution")["side_effect_state"] == "committed"
                finished = [event for event in restored_store.events(task["id"]) if event["type"] == "tool.finished"]
                assert len(finished) == 1 and finished[0]["payload"]["result"]["ok"] is True
                assert attempts == ["exact-write"]
                # An uncertain live lease exhausts the old time allowance at
                # startup. Result reuse cannot silently reset that boundary:
                # approve a separate, bounded fresh budget through the real API.
                await restored.resolve_approval(task["id"], budget["id"], "approved", limits={
                    "max_steps": allowance["used_steps"] + 2, "max_seconds": 30,
                })
                await wait_for_status(restored_store, task["id"], "completed")
                renewed = restored.tree_budget.snapshot(task["id"])
                assert renewed["version"] == allowance["version"] + 1
                assert renewed["used_steps"] == allowance["used_steps"]
                assert renewed["max_execution_seconds"] == 30
            else:
                failed = restored_store.get_task(task["id"])
                assert failed["status"] == "failed"
                assert failed["runtime"]["recovery_requires_fresh_instruction"] == {
                    "checkpoint_id": checkpoint["id"], "review_id": binding["id"],
                }
                assert "fresh instruction" in failed["error"] and "cannot be replayed" in failed["error"]
                event_count = len(restored_store.events(task["id"]))
                for confirm in (False, True):
                    denied = await restored.recover_task(task["id"], confirm=confirm)
                    assert denied["status"] == "failed"
                    assert task["id"] not in restored._workers
                assert len(restored_store.events(task["id"])) == event_count
                assert restored.pending_recoveries() == []
                # Terminal interrupted turn does not prevent a fresh reviewed
                # task. No old call, permit, or old provider turn is replayed.
                fresh = await restored.create_task(
                    prompt="Fresh instruction", cwd=str(original_root), provider_id="fake",
                    on_created=lambda identifier: _bind(restored_service, identifier),
                )
                await restored.resolve_approval(fresh["id"], fresh["approvals"][0]["id"], "approved")
                await wait_for_status(restored_store, fresh["id"], "completed")
                inspected = next(event["payload"]["result"] for event in restored_store.events(fresh["id"])
                    if event["type"] == "tool.finished")
                if boundary == "running":
                    assert inspected["ok"] is True and inspected["content"] == "One actual write\n"
                else:
                    assert inspected["ok"] is False

            assert attempts == ([] if boundary == "prepared" else ["exact-write"])
            receipt_after = restored_service.records.get("review", binding["id"])
            assert receipt_after["status"] == "invalidated"
            assert receipt_after.get("permit") == invalidated.get("permit"), "Recovery must never issue a new permit"
        finally:
            # Cancellation is cleanup of the original held coroutine, not the
            # restart proof: the independent backup predates this cancellation.
            for worker in list(manager._workers.values()):
                worker.cancel()
            await manager.close()
            manager._tools.register(spec)
            await service.close()
            store.close()
            if restored is not None:
                await restored.close()
            if restored_service is not None:
                await restored_service.close()
            if restored_store is not None:
                restored_store.close()

    asyncio.run(run())


def test_revoked_exact_managed_write_never_enters_execution_checkpoint(tmp_path):
    async def run():
        adapter = _FanOutAdapter({"Managed write": ([[_fn(
            "exact-write", "write_file", path="revoked.txt", content="Must not run\n", expected_revision=None,
        )]], 0.0)})
        manager, store = build_manager(tmp_path, adapter)
        live = True
        service = BrowserService(tmp_path / "browser", session_valid=lambda *_: live)
        manager.browser = service
        try:
            task = await manager.create_task(
                prompt="Managed write", cwd=str(tmp_path), provider_id="fake",
                on_created=lambda identifier: _bind(service, identifier),
            )
            await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
            _, approval = await wait_for_pending_approval(store, task["id"], "tool")
            live = False
            await manager.resolve_approval(task["id"], approval["id"], "approved")
            await wait_for_status(store, task["id"], "completed")
            assert not (tmp_path / "revoked.txt").exists()
            assert store.latest_checkpoint(task["id"], kind="execution") is None
            assert not any(row["status"] in {"permitted", "executing", "completed"} for row in service.records.list("review"))
            finished = [event for event in store.events(task["id"]) if event["type"] == "tool.finished"]
            assert len(finished) == 1 and finished[0]["payload"]["result"]["refused"] is True
        finally:
            await manager.close()
            await service.close()
            store.close()

    asyncio.run(run())
