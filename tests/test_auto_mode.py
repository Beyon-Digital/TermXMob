"""Auto mode: task-level autonomous approval inside a worktree, with run ceilings."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_agent import _git_repo, wait_for_pending_approval, wait_for_status
from test_plan_mode import PlanAdapter, _call
from test_policies import _engine, _rule, _shell_decision, store  # noqa: F401  (store is a fixture)
from test_agent import build_manager
from termx.agent.limits import auto_summary, resolve_auto_ceilings
from termx.agent.policies.fingerprint import fingerprint_command


def _shell(call_id: str, command: str):
    return _call("run_shell", call_id, command=command, purpose="test")


async def _start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script,
    *,
    auto: dict[str, Any] | None = None,
    worktree: bool = True,
    mode: str | None = None,
    reviewed: bool = False,
):
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    work = tmp_path / "work"
    work.mkdir()
    adapter = PlanAdapter(script)
    manager, store_ = build_manager(work, adapter)
    extra: dict[str, Any] = {}
    if reviewed:
        # The workspace puts every tool call through the action-review gate.
        from termx.browser.service import BrowserService

        manager.browser = BrowserService(tmp_path / "browser", session_valid=lambda *_: True)
        extra["on_created"] = lambda id: manager.browser.records.put(
            "agent-task-authority", id,
            {"id": id, "principal_id": "owner", "session_id": "sid", "project_id": "project", "policy_version": 1},
        )
    task = await manager.create_task(
        prompt="Do the thing",
        cwd=str(repo),
        provider_id="fake",
        execution_mode="worktree" if worktree else None,
        auto=auto,
        **({"mode": mode} if mode else {}),
        **extra,
    )
    return manager, store_, adapter, task


async def _approve_plan(manager, store_, task_id: str) -> None:
    _task, approval = await wait_for_pending_approval(store_, task_id, "plan")
    await manager.resolve_approval(task_id, approval["id"], "approved")


def _auto(store_, task_id: str) -> dict[str, Any] | None:
    return auto_summary(store_.get_task(task_id)["runtime"])


def _events(store_, task_id: str, event_type: str) -> list[dict[str, Any]]:
    return [e for e in store_.events(task_id) if e["type"] == event_type]


def test_auto_approves_low_risk_calls_and_audits_each(tmp_path, monkeypatch):
    async def run() -> None:
        script = [("", [_shell("c1", "rm -rf build-one")]), ("All done.", [])]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True}
        )
        await _approve_plan(manager, store_, task["id"])
        await wait_for_status(store_, task["id"], "completed")
        decisions = _events(store_, task["id"], "auto.decision")
        assert len(decisions) == 1
        payload = decisions[0]["payload"]
        assert payload["tool"] == "run_shell"
        assert payload["risk"] == "consequential"
        assert payload["rule"] and payload["index"] == 0
        assert not [a for a in store_.approvals(task["id"]) if a["kind"] == "tool"]
        await manager.close()
        store_.close()

    asyncio.run(run())


@pytest.mark.parametrize("reviewed", [False, True])
def test_auto_counts_each_auto_approved_call_after_the_run_finishes(tmp_path, monkeypatch, reviewed):
    async def run() -> None:
        script = [
            ("", [_shell("c1", "rm -rf build-one"), _shell("c2", "rm -rf build-two")]),
            ("", [_shell("c3", "rm -rf build-three")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True}, reviewed=reviewed
        )
        await _approve_plan(manager, store_, task["id"])
        await wait_for_status(store_, task["id"], "completed")
        assert len(_events(store_, task["id"], "auto.decision")) == 3
        # The counter lives in the persisted task runtime, so a reload or restart reads the same number.
        assert _auto(store_, task["id"])["used"]["tool_calls"] == 3
        assert store_.get_task(task["id"])["runtime"]["auto"]["calls"] == 3
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_auto_approves_through_the_action_review_gate(tmp_path, monkeypatch):
    async def run() -> None:
        script = [
            ("", [_call("write_file", "c1", path="notes.txt", content="hi\n"), _shell("c2", "echo hi")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True}, reviewed=True
        )
        await _approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        decisions = _events(store_, task["id"], "auto.decision")
        assert [d["payload"]["tool"] for d in decisions] == ["write_file", "run_shell"]
        assert not [a for a in store_.approvals(task["id"]) if a["kind"] == "tool"]
        assert done["runtime"]["auto"]["enabled"] is True
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_auto_ceiling_still_counts_through_the_action_review_gate(tmp_path, monkeypatch):
    async def run() -> None:
        script = [
            ("", [_shell("c1", "echo one")]),
            ("", [_shell("c2", "echo two")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True, "ceilings": {"tool_calls": 1}}, reviewed=True
        )
        await _approve_plan(manager, store_, task["id"])
        _paused, approval = await wait_for_pending_approval(store_, task["id"], "budget")
        assert approval["payload"]["reason"] == "auto_tool_calls"
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_auto_through_the_gate_still_asks_for_blocked_risks_and_when_switched_off(tmp_path, monkeypatch):
    async def run() -> None:
        for sub, auto, command in (("a", {"enabled": True}, "git push origin main"), ("b", None, "echo hi")):
            base = tmp_path / sub
            base.mkdir()
            manager, store_, _adapter, task = await _start(
                base, monkeypatch, [("", [_shell("c1", command)])], auto=auto, reviewed=True
            )
            await _approve_plan(manager, store_, task["id"])
            _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
            assert approval["status"] == "pending"
            assert not _events(store_, task["id"], "auto.decision")
            await manager.close()
            store_.close()

    asyncio.run(run())


def test_auto_still_asks_for_blocked_risks(tmp_path, monkeypatch):
    async def run() -> None:
        script = [("", [_shell("c1", "git push origin main")])]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True}
        )
        await _approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
        assert approval["status"] == "pending"
        assert not _events(store_, task["id"], "auto.decision")
        await manager.close()
        store_.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ~/x",
        "rm -rf ~/Documents",
        "rm -rf /",
        "rm -rf ..",
        "cd .. && rm -rf *",
        "rm -rf $HOME/x",
        "rm -rf ${HOME}",
        "git reset --hard",
        "git clean -fdx",
        "git branch -D main",
        "git update-ref -d refs/heads/main",
        "git gc --prune=now",
        "git -C /tmp/other status",
        "cat /etc/hosts",
    ],
)
def test_auto_still_asks_for_commands_that_escape_the_worktree(store, tmp_path, command):  # noqa: F811
    engine = _engine(store)
    ctx = SimpleNamespace(
        task_id="task-1",
        cwd=str(tmp_path),
        project_id="proj-1",
        task={"runtime": {"auto": {"enabled": True}}},
    )
    decision = _shell_decision(engine, command, ctx)
    assert decision.approval_required is True, command
    assert decision.auto_resolved is None, command


@pytest.mark.parametrize("command", ["rm -rf build-one", "pnpm test", "git status", "rm -rf ./dist"])
def test_auto_keeps_approving_contained_commands(store, tmp_path, command):  # noqa: F811
    engine = _engine(store)
    ctx = SimpleNamespace(
        task_id="task-1",
        cwd=str(tmp_path),
        project_id="proj-1",
        task={"runtime": {"auto": {"enabled": True}}},
    )
    assert _shell_decision(engine, command, ctx).auto_resolved == "autonomous"


@pytest.mark.parametrize(
    "hidden",
    [
        _call("run_runbook", "c1", runbook_id="missing"),
        _call("plan_nodes", "c1", nodes=[{"title": "x", "prompt": "y"}]),
    ],
)
def test_plan_mode_with_auto_refuses_tools_hidden_from_the_read_only_schema(tmp_path, monkeypatch, hidden):
    async def run() -> None:
        script = [("", [hidden]), ("", [_call("exit_plan", "c2", plan_markdown="1. Edit"),])]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True}, mode="plan"
        )
        await wait_for_pending_approval(store_, task["id"], "plan")
        finished = _events(store_, task["id"], "tool.finished")
        assert finished and finished[0]["payload"]["result"].get("refused") is True
        assert not _events(store_, task["id"], "auto.decision")
        assert not [a for a in store_.approvals(task["id"]) if a["kind"] == "tool"]
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_hard_deny_beats_task_auto(store, tmp_path):  # noqa: F811
    engine = _engine(store)
    ctx = SimpleNamespace(
        task_id="task-1",
        cwd=str(tmp_path),
        project_id="proj-1",
        task={"runtime": {"auto": {"enabled": True}}},
    )
    free = _shell_decision(engine, "pnpm test", ctx)
    assert free.auto_resolved == "autonomous"
    fp, _ = fingerprint_command("pnpm test", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="host", scope_id=None, effect="deny")
    assert _shell_decision(engine, "pnpm test", ctx).auto_resolved == "deny"


def test_disabled_flag_does_not_auto_approve(store, tmp_path):  # noqa: F811
    engine = _engine(store)
    ctx = SimpleNamespace(
        task_id="task-1",
        cwd=str(tmp_path),
        project_id="proj-1",
        task={"runtime": {"auto": {"enabled": False}}},
    )
    assert _shell_decision(engine, "pnpm test", ctx).approval_required is True


def test_call_ceiling_pauses_with_budget_approval_then_resumes(tmp_path, monkeypatch):
    async def run() -> None:
        script = [
            ("", [_shell("c1", "rm -rf build-one")]),
            ("", [_shell("c2", "rm -rf build-two")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True, "ceilings": {"tool_calls": 1}}
        )
        await _approve_plan(manager, store_, task["id"])
        paused, approval = await wait_for_pending_approval(store_, task["id"], "budget")
        assert approval["payload"]["reason"] == "auto_tool_calls"
        assert approval["payload"]["auto_ceiling"] == {"ceiling": "tool_calls", "limit": 1, "used": 1}
        ceiling = _events(store_, task["id"], "auto.ceiling")
        assert len(ceiling) == 1
        assert ceiling[0]["payload"]["ceiling"] == "tool_calls"
        assert ceiling[0]["payload"]["limit"] == 1
        assert paused["status"] == "awaiting_approval"
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        # The renewed allowance is one call too, so the second call exhausts it again.
        _task, again = await wait_for_pending_approval(store_, task["id"], "budget")
        assert again["id"] != approval["id"]
        await manager.resolve_approval(task["id"], again["id"], "approved")
        done = await wait_for_status(store_, task["id"], "completed")
        assert len(_events(store_, task["id"], "auto.decision")) == 2
        assert len(_events(store_, task["id"], "auto.ceiling")) == 2
        assert done["runtime"]["auto"]["enabled"] is True
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_ceiling_inside_one_turn_parks_the_remaining_calls(tmp_path, monkeypatch):
    async def run() -> None:
        script = [
            ("", [_shell("c1", "rm -rf build-one"), _shell("c2", "rm -rf build-two")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True, "ceilings": {"tool_calls": 1}}
        )
        await _approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "budget")
        assert len(_events(store_, task["id"], "auto.decision")) == 1
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        await wait_for_status(store_, task["id"], "completed")
        assert len(_events(store_, task["id"], "auto.decision")) == 2
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_turn_ceiling_pauses(tmp_path, monkeypatch):
    async def run() -> None:
        script = [("", [_shell("c1", "rm -rf build-one")]), ("All done.", [])]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True, "ceilings": {"steps": 1}}
        )
        await _approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "budget")
        assert approval["payload"]["reason"] == "auto_steps"
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_toggle_mid_task_applies_to_the_next_call(tmp_path, monkeypatch):
    async def run() -> None:
        script = [
            ("", [_shell("c1", "rm -rf build-one")]),
            ("", [_shell("c2", "rm -rf build-two")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(tmp_path, monkeypatch, script)
        await _approve_plan(manager, store_, task["id"])
        _task, first = await wait_for_pending_approval(store_, task["id"], "tool")
        manager.set_task_auto(task["id"], enabled=True)
        assert _auto(store_, task["id"])["enabled"] is True
        await manager.resolve_approval(task["id"], first["id"], "approved")
        await wait_for_status(store_, task["id"], "completed")
        tool_approvals = [a for a in store_.approvals(task["id"]) if a["kind"] == "tool"]
        assert len(tool_approvals) == 1
        assert len(_events(store_, task["id"], "auto.decision")) == 1
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_toggle_off_goes_back_to_asking(tmp_path, monkeypatch):
    async def run() -> None:
        script = [
            ("", [_shell("c1", "git push --dry-run origin main"), _shell("c2", "rm -rf build-two")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True}
        )
        await _approve_plan(manager, store_, task["id"])
        _task, first = await wait_for_pending_approval(store_, task["id"], "tool")
        manager.set_task_auto(task["id"], enabled=False)
        assert _auto(store_, task["id"])["enabled"] is False
        await manager.resolve_approval(task["id"], first["id"], "approved")
        # c2 would have been auto-approved a moment ago; now it asks.
        _task, second = await wait_for_pending_approval(store_, task["id"], "tool")
        assert second["id"] != first["id"]
        assert not _events(store_, task["id"], "auto.decision")
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_toggle_off_mid_batch_applies_to_the_remaining_calls(tmp_path, monkeypatch):
    async def run() -> None:
        script = [
            ("", [_shell("c1", "rm -rf build-one"), _shell("c2", "rm -rf build-two")]),
            ("All done.", []),
        ]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script, auto={"enabled": True}
        )
        invoke = manager._invoke_tool
        calls = 0

        async def switch_off_after_first(entry, ctx):
            nonlocal calls
            calls += 1
            if calls == 1:
                manager.set_task_auto(task["id"], enabled=False)
            return await invoke(entry, ctx)

        monkeypatch.setattr(manager, "_invoke_tool", switch_off_after_first)
        await _approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
        assert approval["payload"]["call"]["call_id"] == "c2"
        assert len(_events(store_, task["id"], "auto.decision")) == 1
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_auto_requires_a_worktree(tmp_path, monkeypatch):
    async def run() -> None:
        monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_repo(repo)
        manager, store_ = build_manager(tmp_path, PlanAdapter([]))
        with pytest.raises(ValueError, match="worktree"):
            await manager.create_task(
                prompt="x", cwd=str(repo), provider_id="fake", auto={"enabled": True}
            )
        direct = await manager.create_task(prompt="x", cwd=str(repo), provider_id="fake")
        with pytest.raises(ValueError, match="worktree"):
            manager.set_task_auto(direct["id"], enabled=True)
        with pytest.raises(ValueError, match="Ask mode"):
            await manager.create_task(
                prompt="x", cwd=str(repo), provider_id="fake", mode="ask",
                execution_mode="worktree", auto={"enabled": True},
            )
        # Turning it off never needs a worktree.
        manager.set_task_auto(direct["id"], enabled=False)
        assert _auto(store_, direct["id"])["enabled"] is False
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_token_and_cost_ceilings_ignore_turns_that_report_no_usage(tmp_path, monkeypatch):
    async def run() -> None:
        script = [("", [_shell("c1", "rm -rf build-one")]), ("All done.", [])]
        manager, store_, _adapter, task = await _start(
            tmp_path, monkeypatch, script,
            auto={"enabled": True, "ceilings": {"tokens": 1, "cost_usd": 0.01}},
        )
        stored = _auto(store_, task["id"])
        assert stored["ceilings"]["tokens"] == 1
        assert stored["ceilings"]["cost_usd"] == 0.01
        await _approve_plan(manager, store_, task["id"])
        await wait_for_status(store_, task["id"], "completed")
        assert not _events(store_, task["id"], "auto.ceiling")
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_ceilings_are_validated():
    assert resolve_auto_ceilings(None)["tool_calls"] == 100
    assert resolve_auto_ceilings({"seconds": 60})["seconds"] == 60
    for bad in ({"tool_calls": 0}, {"seconds": "x"}, {"bogus": 1}, {"cost_usd": -1}, {"steps": True}):
        with pytest.raises(ValueError):
            resolve_auto_ceilings(bad)


def test_isolated_session_worktree_counts_as_isolation_for_auto(tmp_path, monkeypatch):
    """A conversation bound to an enrolled worktree passes isolated=True; Auto then needs no per-task checkout."""

    async def run() -> None:
        monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_repo(repo)
        manager, store_ = build_manager(tmp_path, PlanAdapter([]))
        task = await manager.create_task(
            prompt="x", cwd=str(repo), provider_id="fake", isolated=True,
            auto={"enabled": True, "ceilings": {"tool_calls": 7}},
        )
        summary = _auto(store_, task["id"])
        assert summary["enabled"] is True and summary["ceilings"]["tool_calls"] == 7
        assert store_.get_task(task["id"])["runtime"]["isolated"] is True
        # Switching it off and on again mid-task keeps working because the flag survives.
        manager.set_task_auto(task["id"], enabled=False)
        manager.set_task_auto(task["id"], enabled=True)
        assert _auto(store_, task["id"])["enabled"] is True
        # Without the flag the same request is still refused.
        with pytest.raises(ValueError, match="worktree"):
            await manager.create_task(prompt="x", cwd=str(repo), provider_id="fake", auto={"enabled": True})
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_turn_input_accepts_auto_and_rejects_unknown_fields():
    from pydantic import ValidationError

    from termx.workspace.http import TurnInput

    body = TurnInput(prompt="go", request_id="request-0001", auto={"enabled": True, "ceilings": {"steps": 5}})
    assert body.model_dump()["auto"] == {"enabled": True, "ceilings": {"steps": 5}}
    assert TurnInput(prompt="go", request_id="request-0001").model_dump()["auto"] is None
    with pytest.raises(ValidationError):
        TurnInput(prompt="go", request_id="request-0001", auto={"enabled": True, "mode": "x"})
