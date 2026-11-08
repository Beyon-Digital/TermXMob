"""The desktop goes through the policy engine like every other tool.

``computer`` used to have its own decider that looked only for credential-like
text. Capability denies, remembered per-application decisions, the agent's
approval mode and the limit on unattended runs never reached it, so a denied or
scheduled run drove the desktop with no question asked.
"""
from __future__ import annotations

import asyncio
import json
from time import time
from types import SimpleNamespace
from typing import Any

import pytest

from test_agent import FakeAdapter, FakeComputer, build_manager, wait_for_pending_approval, wait_for_status
from test_policies import _RESTRICTED_ENVELOPE, _engine, _rule, store  # noqa: F401  (store is a fixture)
from termx.agent.computer import ComputerController
from termx.agent.manager import AgentManager
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.agent.tools import default_registry
from termx.app import AppState, create_app

CLICK = [{"type": "click", "x": 24, "y": 32}]
FORM = [{"type": "type", "text": "hello"}, {"type": "keypress", "keys": ["ENTER"]}]
CREDENTIAL = [{"type": "paste_text", "text": "password=hunter2-hunter2"}]
SAFETY_CHECK = [{"code": "malicious_instructions", "message": "The page asks the agent to leave this site"}]


def _fingerprint(app: str | None) -> str:
    from termx.agent.policies.fingerprint import computer_fingerprint

    return computer_fingerprint(app)


class Desktop(FakeComputer):
    """FakeComputer that names the application in front, as the real controller does."""

    def __init__(self, app: str | None = "Notes") -> None:
        super().__init__()
        self.app = app

    def target_app(self) -> str | None:
        return self.app


def _computer_call(
    actions: list[dict[str, Any]],
    *,
    call_id: str = "computer-1",
    name: str = "use_computer",
    safety_checks: list[dict[str, Any]] | None = None,
) -> ProviderCall:
    return ProviderCall(
        type="computer", call_id=call_id, name=name, actions=actions, safety_checks=safety_checks or []
    )


def _ctx(
    tmp_path,
    engine,
    *,
    app: str | None = "Notes",
    agent: dict[str, Any] | None = None,
    unattended: bool = False,
    auto: bool = False,
    read_only: bool = False,
):
    task = {
        "id": "task-1",
        "custom_agent_id": agent["id"] if agent else None,
        "runtime": {"auto": {"enabled": True}} if auto else {},
    }
    manager = SimpleNamespace(
        _decide_computer=AgentManager._decide_computer,
        _computer=Desktop(app),
        is_unattended=(lambda _task_id: True) if unattended else None,
        computer_permission=None,
    )
    return SimpleNamespace(
        task_id="task-1",
        cwd=str(tmp_path),
        task=task,
        project_id="proj-1",
        read_only=read_only,
        policy_engine=engine,
        manager=manager,
    )


def _decide(ctx, call: ProviderCall):
    return default_registry().spec_for(call).decide(call, ctx)


def _computer_rule(store, **kw) -> dict[str, Any]:
    """A remembered rule for the computer tool, bound to an application."""
    app = kw.pop("app", "Notes")
    base = dict(
        scope_type="project",
        scope_id="proj-1",
        tool="computer",
        fingerprint=_fingerprint(app),
        display="computer control",
    )
    base.update(kw)
    return _rule(store, **base)


# ---------------------------------------------------------------------------
# Rules, approval mode and sandbox apply to the desktop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("restricted", [False, True], ids=["host sandbox", "restricted sandbox"])
def test_a_capability_deny_stops_computer_control(store, tmp_path, restricted):
    agent = store.create_custom_agent(name="ops")
    rule = _rule(
        store,
        action_type="capability",
        effect="deny",
        scope_type="custom_agent",
        scope_id=agent["id"],
        tool="computer",
        fingerprint="computer.control",
        capabilities=["computer.control"],
    )
    engine = _engine(store, envelope=(lambda _p: _RESTRICTED_ENVELOPE) if restricted else None)

    decision = _decide(_ctx(tmp_path, engine, agent=agent), _computer_call(CLICK))

    assert decision.auto_resolved == "deny"
    assert decision.approval_required is False
    assert decision.matched_rule_id == rule["id"]


def test_a_capability_deny_for_one_agent_leaves_another_alone(store, tmp_path):
    denied = store.create_custom_agent(name="denied")
    other = store.create_custom_agent(name="other")
    _rule(
        store,
        action_type="capability",
        effect="deny",
        scope_type="custom_agent",
        scope_id=denied["id"],
        tool="computer",
        fingerprint="computer.control",
        capabilities=["computer.control"],
    )

    decision = _decide(_ctx(tmp_path, _engine(store), agent=other), _computer_call(CLICK))

    assert decision.allowed is True
    assert decision.auto_resolved is None
    assert decision.approval_required is False


def test_a_remembered_deny_applies_to_that_application_only(store, tmp_path):
    _computer_rule(store, effect="deny", app="Safari")
    engine = _engine(store)

    safari = _decide(_ctx(tmp_path, engine, app="Safari"), _computer_call(CLICK))
    notes = _decide(_ctx(tmp_path, engine, app="Notes"), _computer_call(CLICK))

    assert safari.auto_resolved == "deny"
    assert notes.auto_resolved is None
    assert notes.approval_required is False


def test_a_remembered_allow_covers_form_submission_in_that_application_only(store, tmp_path):
    _computer_rule(store, effect="allow", app="Notes")
    engine = _engine(store)

    notes = _decide(_ctx(tmp_path, engine, app="Notes"), _computer_call(FORM))
    safari = _decide(_ctx(tmp_path, engine, app="Safari"), _computer_call(FORM))

    assert notes.auto_resolved == "allow"
    assert notes.approval_required is False
    assert safari.auto_resolved is None
    assert safari.approval_required is True


def test_a_custom_agent_rule_covers_only_that_agent(store, tmp_path):
    ops = store.create_custom_agent(name="ops")
    other = store.create_custom_agent(name="other")
    _computer_rule(store, effect="allow", app="Notes", scope_type="custom_agent", scope_id=ops["id"])
    engine = _engine(store)

    covered = _decide(_ctx(tmp_path, engine, agent=ops), _computer_call(FORM))
    uncovered = _decide(_ctx(tmp_path, engine, agent=other), _computer_call(FORM))

    assert covered.auto_resolved == "allow"
    assert uncovered.approval_required is True
    assert uncovered.auto_resolved is None


@pytest.mark.parametrize(
    "call",
    [_computer_call(CREDENTIAL), _computer_call(CLICK, safety_checks=SAFETY_CHECK)],
    ids=["credential-like text", "provider safety check"],
)
def test_credentials_and_provider_safety_checks_are_asked_every_time(store, tmp_path, call):
    _computer_rule(store, effect="allow", app="Notes")

    decision = _decide(_ctx(tmp_path, _engine(store), app="Notes"), call)

    assert decision.approval_required is True
    assert decision.auto_resolved is None
    assert decision.remember_options == ()


def test_a_remembered_deny_still_stops_credential_entry(store, tmp_path):
    _computer_rule(store, effect="deny", app="Notes")

    decision = _decide(_ctx(tmp_path, _engine(store), app="Notes"), _computer_call(CREDENTIAL))

    assert decision.auto_resolved == "deny"


@pytest.mark.parametrize("actions", [FORM, CREDENTIAL], ids=["form submission", "credential-like text"])
def test_autonomous_and_auto_modes_do_not_approve_a_computer_ask(store, tmp_path, actions):
    # The desktop is not inside a sandbox or a worktree, so neither mode may approve for the user.
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    engine = _engine(store)

    autonomous = _decide(_ctx(tmp_path, engine, agent=agent), _computer_call(actions))
    auto = _decide(_ctx(tmp_path, engine, auto=True), _computer_call(actions))

    for decision in (autonomous, auto):
        assert decision.approval_required is True
        assert decision.auto_resolved is None


def test_a_routine_action_names_the_capability_and_the_application(store, tmp_path):
    agent = store.create_custom_agent(name="ops", approval_mode="remember")

    decision = _decide(_ctx(tmp_path, _engine(store), app="Notes", agent=agent), _computer_call(CLICK))

    assert decision.allowed is True
    assert decision.approval_required is False
    assert decision.intent.tool == "computer"
    assert decision.intent.required_capabilities == ("computer.control",)
    assert decision.intent.custom_agent_id == agent["id"]
    assert decision.intent.display == "computer control of Notes"
    assert decision.intent.matcher == {"app": "Notes"}


def test_a_restricted_sandbox_does_not_turn_computer_control_into_a_capability_ask(store, tmp_path):
    # Input is sent by the host process, not inside a sandboxed spawn, so the sandbox's
    # capability list neither grants nor limits it.
    engine = _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE)

    decision = _decide(_ctx(tmp_path, engine), _computer_call(CLICK))

    assert decision.allowed is True
    assert decision.approval_required is False
    assert decision.approval_kind == "action"


def test_native_and_function_calls_share_one_identity(store, tmp_path):
    ctx = _ctx(tmp_path, _engine(store), app="Notes")

    native = _decide(ctx, _computer_call(CLICK, name=""))
    function = _decide(ctx, _computer_call(CLICK, name="use_computer"))

    assert native.intent.tool == function.intent.tool == "computer"
    assert native.intent.fingerprint == function.intent.fingerprint == _fingerprint("Notes")


def test_an_unnamed_application_falls_back_to_the_whole_desktop(store, tmp_path):
    decision = _decide(_ctx(tmp_path, _engine(store), app=None), _computer_call(CLICK))

    assert decision.intent.fingerprint == _fingerprint(None)
    assert decision.intent.display == "computer control of the desktop"
    assert _fingerprint(None) != _fingerprint("Notes")


def test_an_unidentified_target_on_macos_asks_and_no_rule_vouches_for_it(store, tmp_path, monkeypatch):
    # macOS can name the front application, so a failed lookup is not "the whole desktop".
    monkeypatch.setattr("sys.platform", "darwin")
    _computer_rule(store, app=None)
    _computer_rule(store, app="Notes", effect="deny")

    decision = _decide(_ctx(tmp_path, _engine(store), app=None), _computer_call(CLICK))

    assert decision.approval_required is True
    assert decision.auto_resolved is None
    assert decision.reason == "Unknown target application"
    assert decision.remember_options == ()


def test_a_form_in_the_whole_desktop_is_asked_every_time(store, tmp_path, monkeypatch):
    # Without an application name a remembered allow would cover every application.
    monkeypatch.setattr("sys.platform", "linux")
    _computer_rule(store, app=None)

    decision = _decide(_ctx(tmp_path, _engine(store), app=None), _computer_call(FORM))

    assert decision.approval_required is True
    assert decision.auto_resolved is None
    assert decision.remember_options == ()
    assert _decide(_ctx(tmp_path, _engine(store), app=None), _computer_call(CLICK)).approval_required is False


def test_a_remembered_deny_still_stops_an_ask_every_time_call(store, tmp_path):
    rule = _computer_rule(store, effect="deny")

    decision = _decide(_ctx(tmp_path, _engine(store), app="Notes"), _computer_call(CREDENTIAL))

    assert decision.auto_resolved == "deny"
    assert decision.matched_rule_id == rule["id"]


def test_application_names_are_printable_bounded_and_redacted():
    from termx.agent.policies.fingerprint import computer_label

    assert computer_label("No\x00te\x1bs\n  Pro") == "No te s Pro"
    assert len(computer_label("x" * 500)) == 80
    assert "hunter2hunter2" not in computer_label("api_key=hunter2hunter2")
    assert computer_label(None) == computer_label("   ") == ""


def test_read_only_modes_never_consult_remembered_rules(store, tmp_path):
    # Ask and Plan refuse the desktop before any decision is used; a rule must not count as used.
    rule = _computer_rule(store, effect="allow", app="Notes")

    _decide(_ctx(tmp_path, _engine(store), read_only=True), _computer_call(FORM))

    assert store.get_policy_rule(rule["id"])["times_used"] == 0


# ---------------------------------------------------------------------------
# Unattended runs
# ---------------------------------------------------------------------------


def test_an_unattended_run_cannot_control_the_computer(store, tmp_path):
    # Not even a remembered allow or an autonomous agent: nobody is there to watch or stop it.
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    _computer_rule(store, effect="allow", app="Notes")

    decision = _decide(_ctx(tmp_path, _engine(store), agent=agent, unattended=True), _computer_call(CLICK))

    assert decision.auto_resolved == "deny"
    assert decision.approval_required is False
    assert decision.allowed is False
    assert "unattended" in decision.reason.lower()


def test_an_attended_run_is_not_limited(store, tmp_path):
    decision = _decide(_ctx(tmp_path, _engine(store), unattended=False), _computer_call(CLICK))

    assert decision.allowed is True
    assert decision.auto_resolved is None


# ---------------------------------------------------------------------------
# Naming the application in front
# ---------------------------------------------------------------------------


def test_frontmost_app_is_the_owner_of_the_front_window(monkeypatch):
    from termx.desktop import windows

    monkeypatch.setattr(windows, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        windows.WindowCapture,
        "list",
        lambda self: [{"id": "2", "app": "Notes"}, {"id": "1", "app": "Safari"}],
    )

    assert windows.frontmost_app() == "Notes"


def test_frontmost_app_is_unknown_without_a_window_list(monkeypatch):
    from termx.desktop import windows

    monkeypatch.setattr(windows, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(windows.WindowCapture, "list", lambda self: [])
    assert windows.frontmost_app() is None

    def refuse(self):
        raise AssertionError("only macOS names the front window")

    monkeypatch.setattr(windows, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(windows.WindowCapture, "list", refuse)
    assert windows.frontmost_app() is None


def test_the_controller_names_the_target_without_ever_raising(monkeypatch):
    def unavailable():
        raise OSError("no window server")

    monkeypatch.setattr("termx.agent.computer.frontmost_app", unavailable)
    assert ComputerController().target_app() is None

    monkeypatch.setattr("termx.agent.computer.frontmost_app", lambda: "Notes")
    assert ComputerController().target_app() == "Notes"


# ---------------------------------------------------------------------------
# Through the manager: what reaches the desktop
# ---------------------------------------------------------------------------


class DesktopAdapter(FakeAdapter):
    """Asks for one computer action, then reports back once it has an answer."""

    def __init__(self, actions: list[dict[str, Any]], *, native: bool = False) -> None:
        super().__init__()
        self.actions = actions
        self.native = native

    async def turn(
        self,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        previous_response_id: str | None = None,
        input_items: list[dict[str, Any]] | None = None,
        allow_computer: bool = False,
        read_only: bool = False,
        allow_subagents: bool = True,
        plan_mode: bool = False,
    ) -> ProviderTurn:
        self.inputs.append(input_items)
        self.turns += 1
        answered = any(
            isinstance(item, dict)
            and item.get("call_id") == "computer-1"
            and str(item.get("type", "")).endswith("call_output")
            for item in input_items or []
        )
        if answered:
            return ProviderTurn(
                "response-2", "Finished.", [], {},
                [{"type": "message", "content": [{"type": "output_text", "text": "Finished."}]}],
            )
        call = _computer_call(self.actions, name="" if self.native else "use_computer")
        item = (
            {"type": "computer_call", "call_id": call.call_id, "action": self.actions[0]}
            if self.native
            else {
                "type": "function_call",
                "call_id": call.call_id,
                "name": "use_computer",
                "arguments": json.dumps({"actions": self.actions}),
            }
        )
        return ProviderTurn("response-1", "Looking at the screen.", [call], {}, [item])


def _manager(tmp_path, actions, *, native: bool = False, app: str | None = "Notes"):
    manager, store_ = build_manager(tmp_path, DesktopAdapter(actions, native=native))
    desktop = Desktop(app)
    manager._computer = desktop  # type: ignore[assignment]
    return manager, store_, desktop


async def _approve_plan(manager, store_, task_id: str) -> None:
    _task, plan = await wait_for_pending_approval(store_, task_id, "plan")
    await manager.resolve_approval(task_id, plan["id"], "approved")


def _events(task: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    return [event for event in task["events"] if event["type"] == event_type]


def test_an_ordinary_task_still_controls_the_desktop(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, CLICK)
        task = await manager.create_task(prompt="Look at the desktop", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        assert desktop.actions == [CLICK]
        assert not [a for a in done["approvals"] if a["kind"] == "tool"]
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_denied_call_never_reaches_the_desktop(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, CLICK)
        agent = store_.create_custom_agent(name="ops")
        rule = store_.create_policy_rule(
            effect="deny",
            scope_type="custom_agent",
            scope_id=agent["id"],
            action_type="capability",
            tool="computer",
            fingerprint="computer.control",
            capabilities=["computer.control"],
        )
        task = await manager.create_task(
            prompt="Look at the desktop", cwd=str(tmp_path), provider_id="fake", custom_agent_id=agent["id"]
        )
        await _approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        assert desktop.actions == []
        resolved = _events(done, "approval.auto_resolved")
        assert resolved and resolved[0]["payload"]["decision"] == "denied"
        assert resolved[0]["payload"]["rule_id"] == rule["id"]
        finished = _events(done, "tool.finished")[0]["payload"]
        assert finished["result"]["refused"] is True
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_denied_native_call_fails_the_task_instead_of_sending_a_malformed_answer(tmp_path):
    # A native computer_call can only be answered with a screenshot, which a refusal must not take.
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, CLICK, native=True)
        agent = store_.create_custom_agent(name="ops")
        store_.create_policy_rule(
            effect="deny",
            scope_type="custom_agent",
            scope_id=agent["id"],
            action_type="capability",
            tool="computer",
            fingerprint="computer.control",
            capabilities=["computer.control"],
        )
        task = await manager.create_task(
            prompt="Look at the desktop", cwd=str(tmp_path), provider_id="fake", custom_agent_id=agent["id"]
        )
        await _approve_plan(manager, store_, task["id"])
        failed = await wait_for_status(store_, task["id"], "failed")
        assert desktop.actions == []
        assert "denied" in failed["error"].lower()
        assert not _events(failed, "tool.finished")
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_an_unattended_task_is_refused_before_any_input(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, CLICK)
        manager.is_unattended = lambda _task_id: True
        task = await manager.create_task(prompt="Look at the desktop", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        assert desktop.actions == []
        finished = _events(done, "tool.finished")[0]["payload"]
        assert finished["result"]["refused"] is True
        assert "unattended" in finished["result"]["error"].lower()
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_form_submission_asks_once_and_a_remembered_allow_covers_the_next_task(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, FORM)
        first = await manager.create_task(prompt="Fill in the form", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, first["id"])
        _task, approval = await wait_for_pending_approval(store_, first["id"], "tool")
        payload = approval["payload"]
        assert payload["title"] == "Form submission"
        assert payload["policy_intent"]["display"] == "computer control of Notes"
        assert payload["policy_intent"]["required_capabilities"] == ["computer.control"]
        assert "project" in payload["remember_options"]
        assert desktop.actions == []

        await manager.resolve_approval(first["id"], approval["id"], "approved", remember="project")
        done = await wait_for_status(store_, first["id"], "completed")
        assert desktop.actions == [FORM]
        created = _events(done, "policy.created")
        assert created and created[0]["payload"]["rule"]["fingerprint"] == _fingerprint("Notes")

        second = await manager.create_task(prompt="Fill in the form again", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, second["id"])
        done = await wait_for_status(store_, second["id"], "completed")
        assert desktop.actions == [FORM, FORM]
        assert not [a for a in done["approvals"] if a["kind"] == "tool"]
        matched = _events(done, "policy.matched")
        assert matched and matched[0]["payload"]["decision"] == "allow"

        # The same form in another application is a new question.
        desktop.app = "Safari"
        third = await manager.create_task(prompt="Fill in the form in Safari", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, third["id"])
        await wait_for_pending_approval(store_, third["id"], "tool")
        assert desktop.actions == [FORM, FORM]
        manager.cancel(third["id"])
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_credential_entry_asks_even_after_the_application_was_allowed(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, CREDENTIAL)
        project_id = manager._project_id(str(tmp_path))
        _rule(
            store_,
            scope_type="project",
            scope_id=project_id,
            tool="computer",
            fingerprint=_fingerprint("Notes"),
            display="computer control of Notes",
        )
        task = await manager.create_task(prompt="Sign in", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
        assert approval["payload"]["remember_options"] == []
        assert desktop.actions == []
        assert "hunter2" not in json.dumps(approval["payload"])
        manager.cancel(task["id"])
        await manager.close()
        store_.close()

    asyncio.run(run())


class ShiftingDesktop(Desktop):
    """The application in front changes after each look, like a user switching windows."""

    def __init__(self, *apps: str) -> None:
        super().__init__(apps[0])
        self.queue = list(apps)

    def target_app(self) -> str | None:
        return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]


def test_a_deny_for_the_application_in_front_at_execution_stops_the_call(tmp_path):
    # The batch is scheduled while Notes is in front; Terminal is in front by the time it runs.
    async def run() -> None:
        manager, store_, _desktop = _manager(tmp_path, CLICK)
        desktop = ShiftingDesktop("Notes", "Terminal")
        manager._computer = desktop  # type: ignore[assignment]
        project_id = manager._project_id(str(tmp_path))
        _rule(
            store_,
            scope_type="project",
            scope_id=project_id,
            tool="computer",
            effect="deny",
            fingerprint=_fingerprint("Terminal"),
            display="computer control of Terminal",
        )
        task = await manager.create_task(prompt="Look at the desktop", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        assert desktop.actions == []
        assert _events(done, "tool.finished")[0]["payload"]["result"]["refused"] is True
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_deny_added_while_an_approval_waited_still_stops_the_call(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, FORM)
        task = await manager.create_task(prompt="Fill in the form", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
        _rule(
            store_,
            scope_type="project",
            scope_id=manager._project_id(str(tmp_path)),
            tool="computer",
            effect="deny",
            fingerprint=_fingerprint("Notes"),
            display="computer control of Notes",
        )
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        done = await wait_for_status(store_, task["id"], "completed")
        assert desktop.actions == []
        assert _events(done, "tool.finished")[0]["payload"]["result"]["refused"] is True
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_an_approval_does_not_carry_over_to_another_application(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, FORM)
        task = await manager.create_task(prompt="Fill in the form", cwd=str(tmp_path), provider_id="fake")
        await _approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
        assert approval["payload"]["policy_intent"]["display"] == "computer control of Notes"
        desktop.app = "Safari"  # the user switched windows before approving
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        _task, again = await wait_for_pending_approval(store_, task["id"], "tool")
        assert again["id"] != approval["id"]
        assert again["payload"]["policy_intent"]["display"] == "computer control of Safari"
        assert desktop.actions == []
        manager.cancel(task["id"])
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_ask_mode_still_refuses_the_desktop_whatever_the_rules_say(tmp_path):
    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, CLICK)
        project_id = manager._project_id(str(tmp_path))
        _rule(
            store_,
            scope_type="project",
            scope_id=project_id,
            tool="computer",
            fingerprint=_fingerprint("Notes"),
            display="computer control of Notes",
        )
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake", mode="ask")
        failed = await wait_for_status(store_, task["id"], "failed")
        assert "ask mode cannot use the computer" in failed["error"].lower()
        assert desktop.actions == []
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_restricted_browser_task_still_cannot_use_the_desktop(tmp_path):
    grants: list[dict[str, Any]] = []

    class BrowserRecords:
        def get(self, kind: str, _identifier: str):
            return {"restricted": True} if kind == "browser-task" else None

        def list(self, _kind: str):
            return grants

    async def run() -> None:
        manager, store_, desktop = _manager(tmp_path, CLICK)
        manager.browser = SimpleNamespace(records=BrowserRecords())
        project_id = manager._project_id(str(tmp_path))
        _rule(
            store_,
            scope_type="project",
            scope_id=project_id,
            tool="computer",
            fingerprint=_fingerprint("Notes"),
            display="computer control of Notes",
        )
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake")
        grants.append({"run_id": task["id"], "revoked": False, "expires_at": time() + 600})
        await _approve_plan(manager, store_, task["id"])
        failed = await wait_for_status(store_, task["id"], "failed")
        assert "restricted browser task cannot run process/computer tools" in failed["error"].lower()
        assert desktop.actions == []
        await manager.close()
        store_.close()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Scheduled runs are unattended
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def hosted(tmp_path):
    adapter = DesktopAdapter(CLICK)
    state = AppState(
        passcode=None, credentials=CredentialStore(memory={}), adapter_factory=lambda *_: adapter
    )
    create_app(state)
    desktop = Desktop("Notes")
    state.agent._computer = desktop  # type: ignore[assignment]
    owner = state.identity.setup_owner("computer-owner", "computer-fixture-password-123")
    folder = tmp_path / "project"
    folder.mkdir()
    project = state.projects.register(str(folder), name="Computer fixture")
    state.agent.save_provider(
        provider_id="fixture",
        kind="openai-compatible",
        name="No-network fixture",
        base_url="http://127.0.0.1:1/v1",
        model="fixture",
        capabilities=["shell", "computer"],
        api_key="fixture-only",
    )
    conversation = state.workspace.create_session(
        owner,
        title="Desktop check",
        project_id=project["id"],
        cwd=str(folder),
        provider_id="fixture",
        model="fixture",
        mode="agent",
    )
    yield SimpleNamespace(state=state, owner=owner, conversation=conversation, desktop=desktop)
    await state.automation.close()
    await state.workspace.close()
    await state.agent.close()
    state.workspace.store.close()
    state.agent_store.close()


async def _eventually(predicate, timeout: float = 10.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def _finished(state, task_id: str) -> bool:
    return state.agent_store.get_task(task_id)["status"] in {"completed", "failed", "cancelled"}


@pytest.mark.anyio
async def test_a_scheduled_run_cannot_control_the_desktop(hosted):
    state, owner, conversation = hosted.state, hosted.owner, hosted.conversation
    goal = state.automation.goal(
        owner, conversation_id=conversation["id"], success_criteria="Look at the desktop", max_runs=1, limits=None
    )
    grant = state.automation.grant(
        owner, goal_id=goal["id"], expires_at=time() + 3600, max_runs=1, limits=None
    )
    now = time()
    state.automation.schedule(
        owner,
        goal_id=goal["id"],
        grant_id=grant["id"],
        prompt="Look at the desktop",
        spec={"kind": "once", "at": now + 1},
        missed="once",
        enabled=True,
    )
    state.automation.clock = lambda: now + 2
    await state.automation.tick()
    await _eventually(
        lambda: bool(state.workspace.store.list("schedule_run"))
        and bool(state.workspace.store.list("schedule_run")[0].get("task_id"))
    )
    task_id = state.workspace.store.list("schedule_run")[0]["task_id"]
    await _eventually(lambda: _finished(state, task_id))

    task = state.agent_store.get_task(task_id, include_events=True)
    assert task["status"] == "completed"
    assert hosted.desktop.actions == []
    finished = _events(task, "tool.finished")[0]["payload"]
    assert finished["result"]["refused"] is True
    assert "unattended" in finished["result"]["error"].lower()


@pytest.mark.anyio
async def test_the_same_conversation_controls_the_desktop_when_someone_is_there(hosted):
    state, owner, conversation = hosted.state, hosted.owner, hosted.conversation

    task = await state.workspace.send(
        owner, conversation["id"], prompt="Look at the desktop", request_id="attended-desktop"
    )
    _plan_task, plan = await wait_for_pending_approval(state.agent_store, task["id"], "plan")
    await state.agent.resolve_approval(task["id"], plan["id"], "approved")
    await _eventually(lambda: _finished(state, task["id"]))

    assert state.agent_store.get_task(task["id"])["status"] == "completed"
    assert hosted.desktop.actions == [CLICK]


@pytest.mark.anyio
async def test_a_task_is_unattended_through_its_delegation_and_its_children(hosted):
    state, owner = hosted.state, hosted.owner
    make = lambda **kw: state.agent_store.create_task(  # noqa: E731
        prompt="p", cwd="/tmp", provider_id="fixture", model="fixture", limits={}, **kw
    )
    scheduled = make()
    child = make(parent_id=scheduled["id"])
    grandchild = make(parent_id=child["id"])
    attended = make()
    state.workspace.store.create(
        "task", owner.id, {"conversation_id": hosted.conversation["id"], "delegation_id": "grant-1"}, None, scheduled["id"]
    )

    assert state.agent.is_unattended(scheduled["id"]) is True
    assert state.agent.is_unattended(child["id"]) is True
    assert state.agent.is_unattended(grandchild["id"]) is True
    assert state.agent.is_unattended(attended["id"]) is False


@pytest.mark.anyio
async def test_an_ancestry_that_cannot_be_followed_counts_as_unattended(hosted, monkeypatch):
    task = hosted.state.agent_store.create_task(
        prompt="p", cwd="/tmp", provider_id="fixture", model="fixture", limits={}
    )
    monkeypatch.setattr(hosted.state.agent, "get_task", lambda task_id: {"parent_id": task_id}, raising=False)
    monkeypatch.setattr(hosted.state.workspace.agents, "get_task", lambda task_id: {"parent_id": task_id})

    assert hosted.state.workspace.is_unattended_task(task["id"]) is True
