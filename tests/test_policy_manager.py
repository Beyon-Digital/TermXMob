"""Manager-level remembered-approval flows.

These run the real AgentManager + CallScheduler + store, not unit doubles:
an approval resolved with `remember=...` must suppress the next equivalent
prompt, persistent denies must auto-refuse in-band, and task-scoped trust
must die with its task — all with `policy.matched`/`approval.auto_resolved`
audit events, never a silent bypass.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from termx.agent.manager import AgentManager
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.store import AgentStore

from test_agent import (  # noqa: E402  (pytest puts tests/ on sys.path)
    FakeAdapter,
    ScriptedAdapter,
    _fn,
    build_manager,
    wait_for_event,
    wait_for_pending_approval,
    wait_for_status,
)


class TwoCommandAdapter(FakeAdapter):
    """Turn 1: run `first`; turn 2: run `second`; turn 3: done."""

    def __init__(self, first: str, second: str) -> None:
        super().__init__()
        self._pair = (first, second)

    async def turn(self, **kwargs: Any) -> ProviderTurn:
        self.turns += 1
        if self.turns in (1, 2):
            command = self._pair[self.turns - 1]
            call = ProviderCall(
                type="function",
                call_id=f"call-{self.turns}",
                name="run_shell",
                arguments={"command": command, "purpose": "t"},
            )
            return ProviderTurn(
                response_id=f"r{self.turns}",
                text="running",
                calls=[call],
                usage={},
                output_items=[
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    }
                ],
            )
        return ProviderTurn(
            response_id="r-final",
            text="done",
            calls=[],
            usage={},
            output_items=[{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
        )


def test_remembered_project_approval_suppresses_second_prompt(tmp_path: Path) -> None:
    """Approving `git push origin main` with remember=project means the next
    equivalent push in the same project auto-resolves without pausing."""

    async def run() -> None:
        adapter = TwoCommandAdapter("git push origin main", "git push origin main")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Publish twice", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")

        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        # Approval v2 payload carries intent + remember options.
        payload = pending["payload"]
        assert payload["policy_fingerprint"]
        assert payload["policy_intent"]["tool"] == "run_shell"
        assert payload["approval_kind"] == "action"
        assert "project" in payload["remember_options"]
        assert payload["sandbox_profile"] == "agent"

        await manager.resolve_approval(
            task["id"], pending["id"], "approved", remember="project"
        )
        completed = await wait_for_status(store, task["id"], "completed")
        # Second identical call never produced another approval.
        pending_approvals = [
            a for a in completed["approvals"] if a["kind"] == "tool" and a["status"] == "pending"
        ]
        assert not pending_approvals
        tool_approvals = [
            a for a in completed["approvals"] if a["kind"] == "tool"
        ]
        assert len(tool_approvals) == 1

        # Durable rule + audit events exist.
        rules = store.list_policy_rules(effect="allow")
        assert len(rules) == 1
        assert rules[0]["scope_type"] == "project"
        assert rules[0]["tool"] == "run_shell"
        events = [e["type"] for e in store.events(task["id"])]
        assert "policy.created" in events
        assert "policy.matched" in events
        assert "approval.auto_resolved" in events
        resolved = [e for e in store.events(task["id"]) if e["type"] == "approval.auto_resolved"]
        assert resolved[-1]["payload"]["decision"] == "approved"
        assert resolved[-1]["payload"]["source"] == "allow"
        assert resolved[-1]["payload"]["rule_id"] == rules[0]["id"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_changed_command_prompts_again_after_remember(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = TwoCommandAdapter("git push origin main", "git push upstream main")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Publish", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        await manager.resolve_approval(
            task["id"], pending["id"], "approved", remember="project"
        )
        # Different remote → different fingerprint → fresh approval ask.
        _, second = await wait_for_pending_approval(store, task["id"], "tool")
        assert second["id"] != pending["id"]
        await manager.resolve_approval(task["id"], second["id"], "approved")
        await wait_for_status(store, task["id"], "completed")
        await manager.close()
        store.close()

    asyncio.run(run())


def test_persistent_deny_auto_blocks_later_calls(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = TwoCommandAdapter("git push origin main", "git push origin main")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Publish", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        # deny + remember=project → persistent deny rule, task cancelled.
        await manager.resolve_approval(
            task["id"], pending["id"], "denied", remember="project"
        )
        await wait_for_status(store, task["id"], "cancelled")

        rules = store.list_policy_rules(effect="deny")
        assert len(rules) == 1
        assert rules[0]["scope_type"] == "project"

        # A NEW task in the same project hits the deny without ever asking —
        # the refusal lands in-band as a ToolOutcome so the run continues.
        adapter.turns = 0
        adapter.command = "git push origin main"
        task2 = await manager.create_task(prompt="Publish again", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task2["id"], task2["approvals"][0]["id"], "approved")
        completed = await wait_for_status(store, task2["id"], "completed")
        refusals = [
            e for e in store.events(task2["id"])
            if e["type"] == "approval.auto_resolved" and e["payload"]["decision"] == "denied"
        ]
        assert refusals, "persistent deny must auto-refuse"
        refused_finish = [
            e for e in store.events(task2["id"])
            if e["type"] == "tool.finished" and e["payload"].get("result", {}).get("auto_resolved") == "denied"
        ]
        assert refused_finish, "refused outcome must be in-band, task keeps running"
        assert not [a for a in completed["approvals"] if a["kind"] == "tool"]
        await manager.close()
        store.close()

    asyncio.run(run())


def test_task_scoped_rule_expires_at_task_end(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = FakeAdapter("git push origin main")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Clean", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        await manager.resolve_approval(
            task["id"], pending["id"], "approved", remember="task"
        )
        await wait_for_status(store, task["id"], "completed")
        rules = store.list_policy_rules()
        assert len(rules) == 1
        assert rules[0]["scope_type"] == "task"
        # Task finished → its rules are expired, not just unmatched.
        assert rules[0]["expires_at"] is not None
        await manager.close()
        store.close()

    asyncio.run(run())


def test_remember_once_or_missing_writes_no_rule(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = FakeAdapter("git push origin main")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Publish", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        await manager.resolve_approval(task["id"], pending["id"], "approved", remember="once")
        await wait_for_status(store, task["id"], "completed")
        assert not store.list_policy_rules()
        await manager.close()
        store.close()

    asyncio.run(run())


def test_remember_relayed_through_escalated_child(tmp_path: Path) -> None:
    """A remember=project on a parent-escalated child approval must write the
    rule from the child's context — so a task-scoped rule can't leak upward."""

    async def run() -> None:
        adapter = ScriptedAdapter(
            [
                [_fn("p1", "spawn_subagent", task="Publish it", agent="publisher")],
                [_fn("c1", "run_shell", command="git push origin main")],
            ]
        )
        manager, store = build_manager(tmp_path, adapter)
        parent = await manager.create_task(
            prompt="Delegate", cwd=str(tmp_path), provider_id="fake"
        )
        await manager.resolve_approval(parent["id"], parent["approvals"][0]["id"], "approved")
        _, delegate = await wait_for_pending_approval(store, parent["id"], "tool")
        await manager.resolve_approval(parent["id"], delegate["id"], "approved")
        _, escalated = await wait_for_pending_approval(store, parent["id"], "tool")
        child_id = escalated["payload"]["child_id"]
        # remember=project on the parent-escalated approval writes the rule.
        await manager.resolve_approval(
            parent["id"], escalated["id"], "approved", remember="project"
        )
        await wait_for_status(store, parent["id"], "completed", timeout=8)
        await wait_for_status(store, child_id, "completed", timeout=8)
        rules = store.list_policy_rules()
        assert rules, "remembered rule must be written for the escalated child call"
        assert rules[0]["scope_type"] == "project"
        assert rules[0]["tool"] == "run_shell"
        await manager.close()
        store.close()

    asyncio.run(run())


def test_second_resolution_does_not_duplicate_rule(tmp_path: Path) -> None:
    """Crash/retry safety: resolving the same approval twice can never write
    the remembered rule twice (the second resolve is rejected outright)."""

    async def run() -> None:
        adapter = FakeAdapter("git push origin main")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Publish", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        await manager.resolve_approval(
            task["id"], pending["id"], "approved", remember="project"
        )
        await wait_for_status(store, task["id"], "completed")
        with pytest.raises(ValueError):
            await manager.resolve_approval(
                task["id"], pending["id"], "approved", remember="project"
            )
        assert len(store.list_policy_rules()) == 1
        await manager.close()
        store.close()

    asyncio.run(run())


def test_deny_once_writes_no_persistent_rule(tmp_path: Path) -> None:
    async def run() -> None:
        adapter = FakeAdapter("git push origin main")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Publish", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        await manager.resolve_approval(task["id"], pending["id"], "denied")
        await wait_for_status(store, task["id"], "cancelled")
        assert not store.list_policy_rules()
        await manager.close()
        store.close()

    asyncio.run(run())


def test_approval_v2_payload_shape(tmp_path: Path) -> None:
    """v2 fields are additive: v1 clients ignore them, v2 clients get intent,
    fingerprint, approval kind, capabilities and remember options."""

    async def run() -> None:
        adapter = FakeAdapter("sudo systemctl restart sshd")
        manager, store = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Restart", cwd=str(tmp_path), provider_id="fake")
        await manager.resolve_approval(task["id"], task["approvals"][0]["id"], "approved")
        _, pending = await wait_for_pending_approval(store, task["id"], "tool")
        payload = pending["payload"]
        assert payload["title"] == "Privilege elevation"
        assert payload["policy_intent"]["risk_class"] == "privilege"
        assert "privilege.elevate" in payload["required_capabilities"]
        assert payload["sandbox_profile"] == "agent"
        assert payload["approval_kind"] == "action"  # host backend grants all
        await manager.resolve_approval(task["id"], pending["id"], "denied")
        await wait_for_status(store, task["id"], "cancelled")
        await manager.close()
        store.close()

    asyncio.run(run())
