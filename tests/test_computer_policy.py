"""Desktop control goes through the policy engine like every other tool.

The policy decides synchronously, before any agent-desktop process starts. Capability denies, remembered
per-application decisions, the agent's approval mode and the limit on unattended runs all apply, and the application a
ref belongs to comes from the host's own tables, never from text the model wrote.
"""
from __future__ import annotations

import asyncio
import json
import re
from time import time
from types import SimpleNamespace
from typing import Any

import pytest
from agent_desktop_fixtures import app_row, node, tree, window_row
from test_agent import FakeAdapter, build_manager, wait_for_pending_approval, wait_for_status
from test_policies import _RESTRICTED_ENVELOPE, _engine, _rule, store  # noqa: F401  (store is a fixture)

from termx.agent import desktop_data as dd
from termx.agent.policies.fingerprint import computer_fingerprint, computer_label, tool_key
from termx.agent.providers import ProviderCall, ProviderTurn
from termx.agent.secrets import CredentialStore
from termx.agent.tools import default_registry
from termx.agent.tools import desktop as tools
from termx.app import AppState, create_app

SID = "s1abc2def3ghi"
NOTES = "com.apple.Notes"


def call(name: str, arguments: dict[str, Any] | None = None, call_id: str = "c1") -> ProviderCall:
    return ProviderCall(type="function", call_id=call_id, name=name, arguments=arguments or {})


def act(ref: str, action: str = "click", **extra: Any) -> ProviderCall:
    return call("desktop_act", {"ref": ref, "action": action, **extra})


def make_ctx(
    tmp_path, engine, *, agent: dict[str, Any] | None = None, unattended: bool = False, permission: dict | None = None,
    limits: list[str] | None = None, auto: bool = False, read_only: bool = False,
):
    task = {"id": "task-1", "custom_agent_id": agent["id"] if agent else None, "runtime": {"auto": {"enabled": True}} if auto else {}}
    manager = SimpleNamespace(
        is_unattended=(lambda _task_id: True) if unattended else None,
        computer_permission=(lambda _task_id: permission) if unattended else None,
        computer_limits=lambda _task_id: (limits, permission),
    )
    events: list[tuple[str, dict]] = []
    return SimpleNamespace(
        task_id="task-1", cwd=str(tmp_path), task=task, project_id="proj-1", read_only=read_only,
        policy_engine=engine, manager=manager, emit=lambda kind, payload: events.append((kind, payload)) or {},
        events=events, metrics=None, cancel=asyncio.Event(),
    )


_COUNTER = iter(range(1, 10_000))


def arrange(ctx, name="Notes", bundle=NOTES, pid=5001, role="button", label="Save", **flags):
    """Put an application and one control in the host's tables, as desktop_apps and desktop_snapshot would.

    Each call is its own snapshot, so several applications and controls can coexist in one test."""
    state = tools._state(ctx)
    record = state.handles.update(ctx.task_id, [app_row(name, pid, bundle)])[-1]
    sid = f"s1abc{next(_COUNTER):05d}"
    ref = f"@{sid}:e1"
    state.snapshots.upsert(ctx.task_id, sid, record, "w-1", {ref: dd.NodeInfo(role, label, flags.get("sensitive", False), flags.get("consequential", False))})
    return record, ref


def add_ref(ctx, record, role="button", label="Save", **flags):
    """Another control of an application that is already in the tables."""
    sid = f"s1abc{next(_COUNTER):05d}"
    ref = f"@{sid}:e1"
    tools._state(ctx).snapshots.upsert(ctx.task_id, sid, record, "w-1", {ref: dd.NodeInfo(role, label, flags.get("sensitive", False), flags.get("consequential", False))})
    return ref


def decide(ctx, provider_call):
    return default_registry().spec_for(provider_call).decide(provider_call, ctx)


def bound_rule(store, record, **kw):
    base = dict(scope_type="project", scope_id="proj-1", tool="computer", display="computer control",
                fingerprint=tool_key("computer", "app", record.label, record.identifier))
    base.update(kw)
    return _rule(store, **base)


def legacy_rule(store, app="Notes", **kw):
    base = dict(scope_type="project", scope_id="proj-1", tool="computer", fingerprint=computer_fingerprint(app), display="computer control")
    base.update(kw)
    return _rule(store, **base)


@pytest.fixture
def desk(fake_agent_desktop):
    return fake_agent_desktop()


# -- rules, approval mode and sandbox apply to the desktop ----------------------------------------------------


@pytest.mark.parametrize("restricted", [False, True], ids=["host sandbox", "restricted sandbox"])
def test_a_capability_deny_stops_every_desktop_tool(store, tmp_path, desk, restricted):
    agent = store.create_custom_agent(name="ops")
    rule = _rule(store, action_type="capability", effect="deny", scope_type="custom_agent", scope_id=agent["id"], tool="computer",
                 fingerprint="computer.control", capabilities=["computer.control"])
    ctx = make_ctx(tmp_path, _engine(store, envelope=(lambda _p: _RESTRICTED_ENVELOPE) if restricted else None), agent=agent)
    record, ref = arrange(ctx)

    for provider_call in (act(ref), call("desktop_snapshot", {"app_id": record.handle}), call("desktop_apps")):
        decision = decide(ctx, provider_call)
        assert decision.auto_resolved == "deny" and decision.approval_required is False
        assert decision.matched_rule_id == rule["id"]


def test_a_capability_deny_for_one_agent_leaves_another_alone(store, tmp_path, desk):
    denied = store.create_custom_agent(name="denied")
    other = store.create_custom_agent(name="other")
    _rule(store, action_type="capability", effect="deny", scope_type="custom_agent", scope_id=denied["id"], tool="computer",
          fingerprint="computer.control", capabilities=["computer.control"])
    engine = _engine(store)
    ctx_denied, ctx_other = make_ctx(tmp_path, engine, agent=denied), make_ctx(tmp_path, engine, agent=other)
    _, ref_a = arrange(ctx_denied)
    _, ref_b = arrange(ctx_other)

    assert decide(ctx_denied, act(ref_a)).auto_resolved == "deny"
    assert decide(ctx_other, act(ref_b)).auto_resolved != "deny"


def test_a_remembered_deny_under_the_old_name_only_key_still_binds_that_application_only(store, tmp_path, desk):
    legacy_rule(store, "Notes", effect="deny")
    ctx = make_ctx(tmp_path, _engine(store))
    _, notes_ref = arrange(ctx)
    _, mail_ref = arrange(ctx, "Mail", "com.apple.mail", pid=5002)

    assert decide(ctx, act(notes_ref)).auto_resolved == "deny"
    assert decide(ctx, act(mail_ref)).auto_resolved != "deny"


def test_a_remembered_allow_binds_the_application_identifier_not_just_its_name(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    notes, notes_ref = arrange(ctx)
    bound_rule(store, notes, effect="allow")

    assert decide(ctx, act(notes_ref)).auto_resolved == "allow"
    # A different application that calls itself "Notes" does not inherit the allow.
    impostor, impostor_ref = arrange(ctx, "Notes", "com.evil.notes", pid=5003)
    decision = decide(ctx, act(impostor_ref))
    assert decision.auto_resolved != "allow" and decision.intent.fingerprint != decide(ctx, act(notes_ref)).intent.fingerprint
    assert impostor.handle != notes.handle


def test_an_allow_written_under_the_old_name_only_key_no_longer_auto_matches(store, tmp_path, desk):
    legacy_rule(store, "Notes", effect="allow")
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx)

    decision = decide(ctx, act(ref))

    assert decision.auto_resolved != "allow"


def test_an_allow_for_one_application_does_not_cover_another(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    notes, _ = arrange(ctx)
    _, mail_ref = arrange(ctx, "Mail", "com.apple.mail", pid=5002)
    bound_rule(store, notes, effect="allow")

    assert decide(ctx, act(mail_ref)).auto_resolved != "allow"


def test_a_custom_agent_rule_covers_only_that_agent(store, tmp_path, desk):
    first, second = store.create_custom_agent(name="first"), store.create_custom_agent(name="second")
    engine = _engine(store)
    ctx_first, ctx_second = make_ctx(tmp_path, engine, agent=first), make_ctx(tmp_path, engine, agent=second)
    record, ref_first = arrange(ctx_first)
    _, ref_second = arrange(ctx_second)
    bound_rule(store, record, effect="allow", scope_type="custom_agent", scope_id=first["id"])

    assert decide(ctx_first, act(ref_first)).auto_resolved == "allow"
    assert decide(ctx_second, act(ref_second)).auto_resolved != "allow"


@pytest.mark.parametrize("arguments,reason", [
    ({"action": "set_value", "text": "password=hunter2-hunter2"}, "Sensitive text entry"),
    ({"action": "type", "text": "token=abcdef123456"}, "Sensitive text entry"),
    ({"action": "select", "option": "api_key=abcdef1234567"}, "Sensitive text entry"),
])
def test_credential_like_text_is_asked_every_time_and_cannot_be_remembered(store, tmp_path, desk, arguments, reason):
    ctx = make_ctx(tmp_path, _engine(store))
    record, ref = arrange(ctx, role="textfield", label="Notes field")
    bound_rule(store, record, effect="allow")

    decision = decide(ctx, call("desktop_act", {"ref": ref, **arguments}))

    assert decision.approval_required is True and decision.reason == reason
    assert decision.auto_resolved is None and decision.remember_options == ()


def test_a_remembered_deny_still_stops_credential_entry(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    record, ref = arrange(ctx, role="textfield")
    bound_rule(store, record, effect="deny")

    assert decide(ctx, act(ref, "set_value", text="password=hunter2-hunter2")).auto_resolved == "deny"


@pytest.mark.parametrize("what", ["secret", "consequential", "password field", "command app"])
def test_autonomous_and_auto_modes_do_not_approve_an_ask_every_time(store, tmp_path, desk, what):
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    ctx = make_ctx(tmp_path, _engine(store), agent=agent, auto=True)
    if what == "command app":
        record, ref = arrange(ctx, "Terminal", "com.apple.Terminal", role="textfield")
        provider_call = act(ref, "set_value", text="ls")
    elif what == "password field":
        record, ref = arrange(ctx, role="textfield", label="Password", sensitive=True)
        provider_call = act(ref, "set_value", text="plain")
    elif what == "consequential":
        record, ref = arrange(ctx, label="Send", consequential=True)
        provider_call = act(ref)
    else:
        record, ref = arrange(ctx, role="textfield")
        provider_call = act(ref, "type", text="api_key=abcdef1234567")
    bound_rule(store, record, effect="allow")

    decision = decide(ctx, provider_call)

    assert decision.approval_required is True and decision.auto_resolved is None and decision.remember_options == ()


def test_a_routine_action_names_the_capability_and_the_application(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx)

    decision = decide(ctx, act(ref))

    assert "computer.control" in decision.intent.required_capabilities
    assert "Notes" in decision.intent.display and decision.intent.matcher["app"] == "Notes"


def test_an_approval_names_the_action_the_control_and_the_application(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx, label="Send", consequential=True)

    decision = decide(ctx, act(ref))

    assert decision.intent.display == "Click “Send” (button) in Notes (com.apple.Notes)"


def test_a_restricted_sandbox_does_not_turn_desktop_control_into_a_capability_ask(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE))
    _, ref = arrange(ctx)

    decision = decide(ctx, act(ref))

    assert decision.approval_kind != "capability" and decision.allowed is True


def test_application_labels_are_printable_bounded_and_redacted():
    assert computer_label("No\x00te\x1bs\n  Pro") == "No te s Pro"
    assert len(computer_label("x" * 500)) == 80
    assert "hunter2hunter2" not in computer_label("api_key=hunter2hunter2")
    assert computer_label(None) == computer_label("   ") == ""
    assert computer_fingerprint("") != computer_fingerprint("")  # an empty label binds to nothing a rule can name


def test_read_only_modes_never_consult_remembered_rules(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store), read_only=True)
    record, ref = arrange(ctx)
    rule = bound_rule(store, record, effect="allow")

    decision = decide(ctx, act(ref))

    assert decision.auto_resolved == "deny" and "Agent mode" in decision.reason
    assert store.get_policy_rule(rule["id"])["times_used"] == 0


# -- targets come from the host's tables ------------------------------------------------------------------------


def test_refs_the_host_did_not_deliver_are_refused_with_no_process(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    arrange(ctx)
    other = make_ctx(tmp_path, _engine(store))
    other.task_id = "task-2"

    for provider_call in (act("@sdeadbeef12345:e1"), act(f"@{SID}:e99"), call("desktop_snapshot", {"root": "@sdeadbeef12345:e1"}),
                          call("desktop_get", {"ref": f"@{SID}:e99"}), call("desktop_snapshot", {"app_id": "a00000000"})):
        assert decide(ctx, provider_call).auto_resolved == "deny"
    _, ref = arrange(ctx)
    assert decide(other, act(ref)).auto_resolved == "deny"  # another task's ref
    assert desk.spawns() == []


def test_a_ref_pruned_from_the_output_cannot_be_acted_on(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    arrange(ctx)
    cut = dd.render_tree({"complete": True, "tree": tree("s2abc2def3ghi", *[node("button", f"b{i}", f"@s2abc2def3ghi:e{i + 1}") for i in range(30)])}, max_nodes=5)
    state = tools._state(ctx)
    record = next(iter(state.handles._by_task[ctx.task_id].values()))[1]
    state.snapshots.upsert(ctx.task_id, "s2abc2def3ghi", record, None, cut.refs)

    assert decide(ctx, act("@s2abc2def3ghi:e30")).auto_resolved == "deny"
    assert decide(ctx, act("@s2abc2def3ghi:e1")).auto_resolved != "deny"


def test_invalid_requests_are_refused_with_the_exact_problem(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    record, ref = arrange(ctx)
    bad = [
        (call("desktop_act", {"ref": ref, "action": "press"}), "action"),
        (act(ref, "set_value"), "text is required"),
        (act(ref, "click", text="x"), "only for set_value"),
        (act(ref, "select"), "option is required"),
        (call("desktop_act", {"ref": ref, "action": "click", "extra": 1}), "unknown field"),
        (call("desktop_snapshot", {"app_id": record.handle, "root": ref}), "exactly one"),
        (call("desktop_snapshot", {"root": ref, "surface": "menu"}), "root cannot be combined"),
        (call("desktop_snapshot", {"surface": "menu"}), "exactly one"),
        (call("desktop_find", {"app_id": record.handle}), "at least one"),
    ]
    for provider_call, text in bad:
        decision = decide(ctx, provider_call)
        assert decision.auto_resolved == "deny" and text in decision.consequence, (provider_call, decision.consequence)


@pytest.mark.parametrize("platform,action,allowed", [
    ("windows", "type", False), ("windows", "set_value", True), ("windows", "right_click", False),
    ("linux", "scroll", False), ("linux", "focus", False), ("macos", "type", True), ("macos", "scroll", True),
])
def test_the_platform_matrix_is_enforced_before_any_process(store, tmp_path, fake_agent_desktop, platform, action, allowed):
    desk = fake_agent_desktop(platform)
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx, role="textfield")
    extra = {"text": "x"} if action in {"type", "set_value"} else {}

    decision = decide(ctx, act(ref, action, **extra))

    assert (decision.auto_resolved != "deny") is allowed
    if not allowed:
        assert desk.spawns() == []
        if action == "type":
            assert "set_value" in decision.consequence


# -- applications that must not be driven -----------------------------------------------------------------------


def test_termx_and_its_launcher_cannot_be_controlled_even_with_an_allow_and_a_grant(store, tmp_path, desk, monkeypatch):
    monkeypatch.setattr(dd, "ancestor_pids", lambda: frozenset({4242, 4243}))
    grant = {"apps": ["Termx", "Whatever", "Notes"], "max_seconds": 60, "run_id": "run-1"}
    ctx = make_ctx(tmp_path, _engine(store), unattended=True, permission=grant, limits=grant["apps"])
    for name, bundle, pid in (("Termx", "com.jaexxxy.termx", 5001), ("Whatever", "", 4242), ("Whatever", "", 4243)):
        record, ref = arrange(ctx, name, bundle, pid=pid)
        bound_rule(store, record, effect="allow")
        decision = decide(ctx, act(ref))
        assert decision.auto_resolved == "deny" and "own interface" in decision.reason
    assert desk.spawns() == []
    blocked = [payload for kind, payload in ctx.events if kind == "desktop.app_blocked"]
    assert blocked and all(item["reason"] == "self" for item in blocked)


def test_command_applications_ask_every_time_and_are_hidden_from_listings(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    terminal, ref = arrange(ctx, "Terminal", "com.apple.Terminal", pid=5004)
    bound_rule(store, terminal, effect="allow")

    decision = decide(ctx, act(ref))

    assert decision.approval_required is True and "run commands" in decision.reason and decision.remember_options == ()
    assert terminal.app_class() == "command"


def test_an_application_without_an_identifier_or_process_instance_asks_each_time(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    state = tools._state(ctx)
    row = {"name": "Notes", "pid": 7, "presentation": "foreground", "process_instance": "p"}  # no bundle id
    record = state.handles.update(ctx.task_id, [row])[-1]
    ref = f"@{SID}:e1"
    state.snapshots.upsert(ctx.task_id, SID, record, None, {ref: dd.NodeInfo("button", "Save", False, False)})

    decision = decide(ctx, act(ref))

    assert decision.approval_required and "cannot be identified" in decision.reason


def test_two_applications_with_one_name_ask_each_time(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    first, ref = arrange(ctx)
    arrange(ctx, "Notes", "com.evil.notes", pid=5003)
    bound_rule(store, first, effect="allow")

    decision = decide(ctx, act(ref))

    assert decision.approval_required and "cannot be told apart" in decision.consequence


def test_several_copies_of_one_application_are_refused(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    state = tools._state(ctx)
    record = state.handles.update(ctx.task_id, [app_row("Docker", 1, "com.docker"), app_row("Docker", 2, "com.docker")])[-1]

    decision = decide(ctx, call("desktop_snapshot", {"app_id": record.handle}))

    assert decision.auto_resolved == "deny" and "several copies" in decision.consequence
    assert desk.spawns() == []


# -- risk heuristics ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("arguments,expected", [
    ({"action": "click"}, "Consequential control"),
    ({"action": "toggle"}, "Consequential control"),
])
def test_consequential_controls_ask_every_time(store, tmp_path, desk, arguments, expected):
    ctx = make_ctx(tmp_path, _engine(store))
    record, ref = arrange(ctx, label="Send", consequential=True)
    bound_rule(store, record, effect="allow")

    decision = decide(ctx, call("desktop_act", {"ref": ref, **arguments}))

    assert decision.reason == expected and decision.approval_required and decision.auto_resolved is None


def test_unfamiliar_control_types_and_line_breaks_fail_closed(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    record, odd_ref = arrange(ctx, role="mysterywidget", pid=5010)
    field_ref = add_ref(ctx, record, role="textfield")
    area_ref = add_ref(ctx, record, role="textarea")
    bound_rule(store, record, effect="allow")

    assert decide(ctx, act(odd_ref, "set_value", text="x")).reason == "Unfamiliar control"
    assert decide(ctx, act(field_ref, "set_value", text="line one\nline two")).reason == "Form submission"
    assert decide(ctx, act(area_ref, "set_value", text="line one\nline two")).auto_resolved == "allow"
    assert decide(ctx, act(field_ref, "set_value", text="plain")).auto_resolved == "allow"


def test_a_password_field_read_is_refused(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx, role="textfield", label="Password", sensitive=True)

    decision = decide(ctx, call("desktop_get", {"ref": ref}))

    assert decision.auto_resolved == "deny" and "password" in decision.consequence.lower()


# -- application lists and unattended runs ----------------------------------------------------------------------


def test_a_list_names_applications_by_type(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store), limits=["com.apple.Notes"])
    _, ref = arrange(ctx)
    spoof, spoof_ref = arrange(ctx, "com.apple.Notes", "com.evil.x", pid=5003)  # a display name equal to a bundle id
    assert decide(ctx, act(ref)).auto_resolved != "deny"
    assert decide(ctx, act(spoof_ref)).auto_resolved == "deny"
    assert spoof.handle != ""


def test_a_ref_from_an_application_that_left_the_list_is_denied_before_any_process(store, tmp_path, desk):
    limits = ["Notes", "Mail"]
    ctx = make_ctx(tmp_path, _engine(store))
    ctx.manager.computer_limits = lambda _task_id: (limits, None)
    _, notes_ref = arrange(ctx)
    _, mail_ref = arrange(ctx, "Mail", "com.apple.mail", pid=5002)
    assert decide(ctx, act(notes_ref)).auto_resolved != "deny"
    limits[:] = ["Mail"]  # the skill or permission changed after the snapshot

    assert decide(ctx, act(notes_ref)).auto_resolved == "deny"
    assert decide(ctx, act(mail_ref)).auto_resolved != "deny"
    assert desk.spawns() == []
    assert [p for kind, p in ctx.events if kind == "desktop.app_blocked"][0]["reason"] == "limits"


def test_an_empty_list_allows_nothing(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store), limits=[])
    _, ref = arrange(ctx)

    assert decide(ctx, act(ref)).auto_resolved == "deny"


def test_an_unattended_run_cannot_control_the_desktop(store, tmp_path, desk):
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    ctx = make_ctx(tmp_path, _engine(store), agent=agent, unattended=True)
    record, ref = arrange(ctx)
    bound_rule(store, record, effect="allow")

    for provider_call in (act(ref), call("desktop_apps"), call("desktop_snapshot", {"app_id": record.handle})):
        decision = decide(ctx, provider_call)
        assert decision.auto_resolved == "deny" and decision.allowed is False and "unattended" in decision.reason.lower()
    assert desk.spawns() == []


def test_a_grant_opens_only_its_applications_and_never_consequential_steps(store, tmp_path, desk):
    grant = {"apps": ["Notes"], "max_seconds": 60, "run_id": "run-1"}
    ctx = make_ctx(tmp_path, _engine(store), unattended=True, permission=grant, limits=["Notes"])
    notes, ref = arrange(ctx)
    bound_rule(store, notes, effect="allow")
    send_ref = add_ref(ctx, notes, label="Send", consequential=True)
    field_ref = add_ref(ctx, notes, role="textfield", label="Body")
    _, mail_ref = arrange(ctx, "Mail", "com.apple.mail", pid=5002)

    assert decide(ctx, act(ref)).auto_resolved == "allow"
    sent = decide(ctx, act(send_ref))
    assert sent.auto_resolved == "deny" and "Needs a person to confirm" in sent.consequence
    secret = decide(ctx, act(field_ref, "set_value", text="password=hunter2-hunter2"))
    assert secret.auto_resolved == "deny" and "Needs a person to confirm" in secret.consequence
    assert decide(ctx, act(mail_ref)).auto_resolved == "deny"
    assert desk.spawns() == []


def test_an_attended_run_is_not_limited(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx)

    decision = decide(ctx, act(ref))

    assert decision.allowed is True and decision.auto_resolved is None


def test_a_stale_loop_stops_after_five_failures(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    record, ref = arrange(ctx)
    state = tools._state(ctx)
    for _ in range(4):
        state.bump(ctx.task_id, record.handle)
    assert decide(ctx, act(ref)).auto_resolved != "deny"
    state.bump(ctx.task_id, record.handle)
    assert "repeated failures" in decide(ctx, act(ref)).reason
    state.reset(ctx.task_id, record.handle)
    assert decide(ctx, act(ref)).auto_resolved != "deny"


def test_a_takeover_stops_desktop_calls_until_cleared(store, tmp_path, desk):
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx)
    tools._state(ctx).blocked[ctx.task_id] = "The user took over. Ask before using the desktop again."

    decision = decide(ctx, act(ref))

    assert decision.auto_resolved == "deny" and "took over" in decision.consequence


def test_desktop_control_that_is_not_set_up_is_refused_and_hidden(store, tmp_path, fake_agent_desktop):
    desk = fake_agent_desktop()
    desk.client._status.ready = False
    ctx = make_ctx(tmp_path, _engine(store))
    _, ref = arrange(ctx)

    decision = decide(ctx, act(ref))

    assert decision.auto_resolved == "deny" and "not set up" in decision.reason
    names = {tool["name"] for tool in default_registry().provider_tools()}
    assert not names & set(tools.DESKTOP_TOOLS) and "use_computer" not in names


# -- through the manager ---------------------------------------------------------------------------------------


class DesktopAdapter(FakeAdapter):
    """Drives desktop_apps, desktop_snapshot and one desktop_act, reading ids from earlier tool output."""

    def __init__(self, act_args: dict[str, Any] | None = None, *, first: str = "desktop_apps") -> None:
        super().__init__()
        self.act_args = act_args or {"action": "click"}
        self.first = first
        self.schemas: list[set[str]] = []

    @staticmethod
    def outputs(input_items) -> list[dict[str, Any]]:
        found = []
        for item in input_items or []:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                try:
                    found.append(json.loads(item["output"]))
                except ValueError:
                    pass
        return found

    async def turn(self, *, prompt, cwd, manifest, previous_response_id=None, input_items=None, read_only=False,
                   allow_subagents=True, plan_mode=False, **_ignored) -> ProviderTurn:
        from termx.agent.tools import default_registry as registry

        self.schemas.append({tool["name"] for tool in registry().provider_tools(read_only=read_only)})
        self.inputs.append(input_items)
        self.turns += 1
        outputs = self.outputs(input_items)
        if not outputs:
            return self._turn("a1", self.first, {"app_id": "a00000000"} if self.first == "desktop_snapshot" else {})
        last = outputs[-1]
        screen = last.get("screen", "")
        if self.turns == 2 or ("app" not in last and last.get("ok") and "snapshot" not in last):
            handle = re.search(r"\ba[0-9a-f]{8}\b", screen)
            return self._turn("a2", "desktop_snapshot", {"app_id": handle.group(0)} if handle else {"app_id": "a00000000"})
        ref = re.search(r"@s[0-9a-z]+:e\d+", screen)
        if self.turns == 3 and ref:
            return self._turn("a3", "desktop_act", {"ref": ref.group(0), **self.act_args})
        return ProviderTurn("response-end", "Finished.", [], {}, [{"type": "message", "content": [{"type": "output_text", "text": "Finished."}]}])

    @staticmethod
    def _turn(call_id, name, arguments) -> ProviderTurn:
        item = {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}
        return ProviderTurn(f"r-{call_id}", "Looking.", [ProviderCall("function", call_id, name, arguments)], {}, [item])


def notes_app(desk, label="Save", role="button", **node_extra):
    notes = app_row("Notes", 6001, NOTES)
    root = tree(SID, node(role, label, f"@{SID}:e1", available_actions=["Click"], **node_extra))
    desk.arrange_apps((notes, [window_row("w-7", notes)], root))
    return notes


async def approve_plan(manager, store_, task_id):
    _task, plan = await wait_for_pending_approval(store_, task_id, "plan")
    await manager.resolve_approval(task_id, plan["id"], "approved")


def events(task, kind):
    return [event for event in task["events"] if event["type"] == kind]


def test_an_ordinary_task_reads_and_acts_on_another_application(tmp_path, desk):
    notes_app(desk)

    async def run() -> None:
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        task = await manager.create_task(prompt="Click Save in Notes", cwd=str(tmp_path), provider_id="fake")
        await approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        commands = desk.commands()
        assert "snapshot" in commands and commands[-1] == "click"
        assert not [a for a in done["approvals"] if a["kind"] == "tool"]
        finished = events(done, "tool.finished")
        assert all("screen" not in event["payload"]["result"] for event in finished)  # events carry summaries only
        assert any("title" in event["payload"] for event in finished)
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_denied_call_never_reaches_the_cli(tmp_path, desk):
    notes_app(desk)

    async def run() -> None:
        manager, store_ = build_manager(tmp_path, DesktopAdapter(first="desktop_apps"))
        agent = store_.create_custom_agent(name="ops")
        rule = store_.create_policy_rule(effect="deny", scope_type="custom_agent", scope_id=agent["id"], action_type="capability",
                                         tool="computer", fingerprint="computer.control", capabilities=["computer.control"])
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake", custom_agent_id=agent["id"])
        await approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        assert desk.spawns() == []
        resolved = events(done, "approval.auto_resolved")
        assert resolved and resolved[0]["payload"]["rule_id"] == rule["id"]
        assert events(done, "tool.finished")[0]["payload"]["result"]["refused"] is True
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_an_unattended_task_is_refused_before_any_process(tmp_path, desk):
    notes_app(desk)

    async def run() -> None:
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        manager.is_unattended = lambda _task_id: True
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake")
        await approve_plan(manager, store_, task["id"])
        done = await wait_for_status(store_, task["id"], "completed")
        assert desk.spawns() == []
        assert "unattended" in events(done, "tool.finished")[0]["payload"]["result"]["error"].lower()
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_consequential_click_asks_and_a_deny_added_while_waiting_still_stops_it(tmp_path, desk):
    notes_app(desk, label="Send")

    async def run() -> None:
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        task = await manager.create_task(prompt="Send it", cwd=str(tmp_path), provider_id="fake")
        await approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
        assert "Click “Send” (button) in Notes" in approval["payload"]["policy_intent"]["display"]
        _rule(store_, scope_type="project", scope_id=manager._project_id(str(tmp_path)), tool="computer", effect="deny",
              fingerprint=computer_fingerprint("Notes"), display="computer control of Notes")
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        done = await wait_for_status(store_, task["id"], "completed")
        assert "click" not in desk.commands()
        assert events(done, "tool.finished")[-1]["payload"]["result"]["refused"] is True
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_credential_entry_asks_even_after_the_application_was_allowed(tmp_path, desk):
    notes = notes_app(desk, label="Body", role="textfield")

    async def run() -> None:
        manager, store_ = build_manager(tmp_path, DesktopAdapter({"action": "set_value", "text": "token=super-secret-value"}))
        _rule(store_, scope_type="project", scope_id=manager._project_id(str(tmp_path)), tool="computer",
              fingerprint=tool_key("computer", "app", "Notes", NOTES), display="computer control of Notes")
        task = await manager.create_task(prompt="Enter it", cwd=str(tmp_path), provider_id="fake")
        await approve_plan(manager, store_, task["id"])
        _task, approval = await wait_for_pending_approval(store_, task["id"], "tool")
        assert "super-secret-value" not in json.dumps(approval["payload"])
        await manager.resolve_approval(task["id"], approval["id"], "approved")
        done = await wait_for_status(store_, task["id"], "completed")
        assert desk.last_stdin() == "token=super-secret-value"  # delivered once, on stdin only
        assert "super-secret-value" not in json.dumps(done["events"])  # never persisted
        started = [e for e in events(done, "tool.started") if e["payload"]["call"]["name"] == "desktop_act"][0]
        assert started["payload"]["call"]["arguments"]["text"] == "[private input omitted: 24 characters]"
        await manager.close()
        store_.close()

    asyncio.run(run())
    assert notes["name"] == "Notes"


def test_ask_mode_hides_the_tools_and_refuses_a_forced_call(tmp_path, desk):
    notes_app(desk)

    async def run() -> None:
        adapter = DesktopAdapter()
        manager, store_ = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake", mode="ask")
        done = await wait_for_status(store_, task["id"], "completed")
        assert not adapter.schemas[0] & set(tools.DESKTOP_TOOLS)
        assert desk.spawns() == []
        refused = events(done, "tool.finished")[0]["payload"]["result"]
        assert refused["refused"] is True and "read-only" in refused["error"]
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_agent_mode_offers_the_tools_to_a_function_calling_model_without_vision(tmp_path, desk):
    async def run() -> None:
        adapter = DesktopAdapter()
        manager, store_ = build_manager(tmp_path, adapter)
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake")
        await approve_plan(manager, store_, task["id"])
        await wait_for_status(store_, task["id"], "completed")
        assert set(tools.DESKTOP_TOOLS) <= adapter.schemas[0]
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_restricted_browser_task_cannot_use_the_desktop(tmp_path, desk):
    notes_app(desk)
    grants: list[dict[str, Any]] = []

    class BrowserRecords:
        def get(self, kind: str, _identifier: str):
            return {"restricted": True} if kind == "browser-task" else None

        def list(self, _kind: str):
            return grants

    async def run() -> None:
        manager, store_ = build_manager(tmp_path, DesktopAdapter())
        manager.browser = SimpleNamespace(records=BrowserRecords())
        task = await manager.create_task(prompt="Look", cwd=str(tmp_path), provider_id="fake")
        grants.append({"run_id": task["id"], "revoked": False, "expires_at": time() + 600})
        await approve_plan(manager, store_, task["id"])
        failed = await wait_for_status(store_, task["id"], "failed")
        assert "restricted browser task cannot run process or desktop tools" in failed["error"].lower()
        assert desk.spawns() == []
        await manager.close()
        store_.close()

    asyncio.run(run())


def test_a_bot_preset_that_lists_computer_expands_to_the_desktop_tools(tmp_path, desk):
    from termx.agent.tools.registry import expand_tools

    assert set(tools.DESKTOP_TOOLS) <= expand_tools(["computer", "read_file"])
    names = {tool["name"] for tool in default_registry().provider_tools(allowed_tools=["computer"])}
    assert names == set(tools.DESKTOP_TOOLS)
    assert not {tool["name"] for tool in default_registry().provider_tools(desktop=False)} & set(tools.DESKTOP_TOOLS)


def test_the_retired_screenshot_tool_answers_in_band(tmp_path, desk):
    ctx = make_ctx(tmp_path, None)
    spec = default_registry().spec_for(call("use_computer", {"actions": []}))

    outcome = asyncio.run(spec.execute(call("use_computer"), ctx))

    assert outcome.result["refused"] and "desktop_snapshot" in outcome.result["error"]
    assert "use_computer" not in {tool["name"] for tool in default_registry().provider_tools()}


# -- hosted: scheduled runs are unattended ----------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def hosted(tmp_path, fake_agent_desktop):
    desk = fake_agent_desktop()
    notes_app(desk)
    adapter = DesktopAdapter()
    state = AppState(passcode=None, credentials=CredentialStore(memory={}), adapter_factory=lambda *_: adapter)
    create_app(state)
    owner = state.identity.setup_owner("computer-owner", "computer-fixture-password-123")
    folder = tmp_path / "project"
    folder.mkdir()
    project = state.projects.register(str(folder), name="Computer fixture")
    state.agent.save_provider(provider_id="fixture", kind="openai-compatible", name="No-network fixture", base_url="http://127.0.0.1:1/v1",
                              model="fixture", capabilities=["functions"], api_key="fixture-only")
    conversation = state.workspace.create_session(owner, title="Desktop check", project_id=project["id"], cwd=str(folder),
                                                  provider_id="fixture", model="fixture", mode="agent")
    yield SimpleNamespace(state=state, owner=owner, conversation=conversation, desk=desk)
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
    goal = state.automation.goal(owner, conversation_id=conversation["id"], success_criteria="Look at the desktop", max_runs=1, limits=None)
    grant = state.automation.grant(owner, goal_id=goal["id"], expires_at=time() + 3600, max_runs=1, limits=None)
    now = time()
    state.automation.schedule(owner, goal_id=goal["id"], grant_id=grant["id"], prompt="Look at the desktop",
                              spec={"kind": "once", "at": now + 1}, missed="once", enabled=True)
    state.automation.clock = lambda: now + 2
    await state.automation.tick()
    await _eventually(lambda: bool(state.workspace.store.list("schedule_run")) and bool(state.workspace.store.list("schedule_run")[0].get("task_id")))
    task_id = state.workspace.store.list("schedule_run")[0]["task_id"]
    await _eventually(lambda: _finished(state, task_id))

    task = state.agent_store.get_task(task_id, include_events=True)
    assert task["status"] == "completed"
    assert hosted.desk.spawns() == []
    finished = events(task, "tool.finished")[0]["payload"]
    assert finished["result"]["refused"] is True and "unattended" in finished["result"]["error"].lower()


@pytest.mark.anyio
async def test_the_same_conversation_controls_the_desktop_when_someone_is_there(hosted):
    state, owner, conversation = hosted.state, hosted.owner, hosted.conversation

    task = await state.workspace.send(owner, conversation["id"], prompt="Look at the desktop", request_id="attended-desktop")
    _plan_task, plan = await wait_for_pending_approval(state.agent_store, task["id"], "plan")
    await state.agent.resolve_approval(task["id"], plan["id"], "approved")
    await _eventually(lambda: _finished(state, task["id"]))

    assert state.agent_store.get_task(task["id"])["status"] == "completed"
    assert "click" in hosted.desk.commands()


@pytest.mark.anyio
async def test_a_task_is_unattended_through_its_delegation_and_its_children(hosted):
    state, owner = hosted.state, hosted.owner
    make = lambda **kw: state.agent_store.create_task(prompt="p", cwd="/tmp", provider_id="fixture", model="fixture", limits={}, **kw)  # noqa: E731
    scheduled = make()
    child = make(parent_id=scheduled["id"])
    grandchild = make(parent_id=child["id"])
    attended = make()
    state.workspace.store.create("task", owner.id, {"conversation_id": hosted.conversation["id"], "delegation_id": "grant-1"}, None, scheduled["id"])

    assert state.agent.is_unattended(scheduled["id"]) is True
    assert state.agent.is_unattended(child["id"]) is True
    assert state.agent.is_unattended(grandchild["id"]) is True
    assert state.agent.is_unattended(attended["id"]) is False


@pytest.mark.anyio
async def test_an_ancestry_that_cannot_be_followed_counts_as_unattended(hosted, monkeypatch):
    task = hosted.state.agent_store.create_task(prompt="p", cwd="/tmp", provider_id="fixture", model="fixture", limits={})
    monkeypatch.setattr(hosted.state.agent, "get_task", lambda task_id: {"parent_id": task_id}, raising=False)
    monkeypatch.setattr(hosted.state.workspace.agents, "get_task", lambda task_id: {"parent_id": task_id})

    assert hosted.state.workspace.is_unattended_task(task["id"]) is True
