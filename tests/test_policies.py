"""Policy engine v2: fingerprinting, durable rules, precedence, scopes.

These tests prove the security invariants the whole feature depends on —
not just return values:

- remembered approvals match only equivalent actions (never a broader one);
- denies always beat allows, at any scope;
- task-scoped trust dies with its task and never leaks to sibling tasks;
- project/custom-agent rules never leak into unrelated scopes;
- a remembered *execution* allow can never punch through a missing sandbox
  capability — capability elevation is its own approval.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from termx.agent.policies.engine import PolicyEngine
from termx.agent.policies.fingerprint import (
    fingerprint_command,
    runbook_fingerprint,
    shell_intent,
    tool_key,
)
from termx.agent.policies.models import PolicyIntent, rule_public
from termx.agent.policy import PolicyDecision
from termx.agent.providers import ProviderCall
from termx.agent.store import AgentStore


@pytest.fixture
def store(tmp_path):
    s = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    yield s
    s.close()


def _ctx(tmp_path, *, task_id: str = "task-1", project_id: str = "proj-1", custom_agent_id=None):
    task = {"custom_agent_id": custom_agent_id} if custom_agent_id else {}
    return SimpleNamespace(
        task_id=task_id,
        cwd=str(tmp_path),
        task=task,
        project_id=project_id,
    )


def _call(command: str, *, call_id: str = "c1", name: str = "run_shell") -> ProviderCall:
    return ProviderCall(
        type="function", call_id=call_id, name=name,
        arguments={"command": command, "purpose": "t"},
    )


def _shell_decision(engine, command, ctx) -> PolicyDecision:
    base = PolicyDecision(False, True, "Needs approval", "Runs a shell command")
    return engine.decide_shell(_call(command), ctx, command, base)


def _rule(store, **kw) -> dict:
    base = dict(
        effect="allow",
        scope_type="project",
        scope_id="proj-1",
        action_type="tool",
        tool="run_shell",
        fingerprint="fp",
        display="fp",
    )
    base.update(kw)
    return store.create_policy_rule(**base)


# ---------------------------------------------------------------------------
# Fingerprinting — remembered rules must never over-approximate.
# ---------------------------------------------------------------------------


def test_fingerprint_binds_interpreter_to_script_path(tmp_path):
    script = tmp_path / "scripts"
    script.mkdir()
    (script / "a.py").write_text("print(1)")
    (script / "b.py").write_text("print(2)")

    fp_a, kind_a = fingerprint_command("python scripts/a.py", str(tmp_path))
    fp_b, _ = fingerprint_command("python scripts/b.py", str(tmp_path))
    fp_again, _ = fingerprint_command("python   scripts/a.py", str(tmp_path))
    assert kind_a == "conservative"
    assert fp_a != fp_b  # different script = different intent
    assert fp_a == fp_again  # whitespace normalization holds


def test_fingerprint_inline_interpreter_is_exact():
    fp1, kind = fingerprint_command("python -c 'import os; print(1)'", "/tmp")
    fp2, _ = fingerprint_command("python -c 'import os; print(2)'", "/tmp")
    assert kind == "exact"
    assert fp1 != fp2


def test_fingerprint_compound_commands_are_exact(tmp_path):
    for cmd in (
        "rm -rf a && ls",
        "echo hi | grep h",
        "ls; pwd",
        "cat $(which python)",
        "find . -name '*.py' > out.txt",
    ):
        _, kind = fingerprint_command(cmd, str(tmp_path))
        assert kind == "exact", cmd


def test_fingerprint_hierarchical_programs_bind_subcommand(tmp_path):
    push, kind = fingerprint_command("git push origin main", str(tmp_path))
    push_again, _ = fingerprint_command("git push origin main --force-with-lease", str(tmp_path))
    fetch, _ = fingerprint_command("git fetch origin", str(tmp_path))
    assert kind == "conservative"
    # Flag variants of the same subcommand match; a different subcommand doesn't.
    assert push == push_again
    assert push != fetch


def test_fingerprint_generic_binds_first_target(tmp_path):
    a, _ = fingerprint_command("rm -rf build/", str(tmp_path))
    b, _ = fingerprint_command("rm -rf dist/", str(tmp_path))
    assert a != b


def test_fingerprint_strips_env_assignments(tmp_path):
    with_env, _ = fingerprint_command("FOO=1 BAR=2 pytest tests/", str(tmp_path))
    without, _ = fingerprint_command("pytest tests/", str(tmp_path))
    assert with_env == without


def test_fingerprint_unparseable_command_is_exact(tmp_path):
    _, kind = fingerprint_command("echo 'unclosed", str(tmp_path))
    assert kind == "exact"


def test_runbook_fingerprint_binds_runbook_identity():
    assert runbook_fingerprint("deploy") != runbook_fingerprint("deploy-2")
    assert runbook_fingerprint("deploy") == runbook_fingerprint("deploy")


# ---------------------------------------------------------------------------
# Store layer — durable policy_rules.
# ---------------------------------------------------------------------------


def test_policy_rule_crud(store):
    rule = _rule(store, fingerprint="fp-x", display="pnpm install")
    fetched = store.get_policy_rule(rule["id"])
    assert fetched["effect"] == "allow"
    assert fetched["scope_type"] == "project"
    assert fetched["fingerprint"] == "fp-x"
    assert fetched["times_used"] == 0
    assert fetched["revoked_at"] is None

    public = rule_public(fetched)
    assert "matcher_json" not in public
    assert public["fingerprint"] == "fp-x"


def test_policy_rule_validation(store):
    with pytest.raises(ValueError):
        _rule(store, effect="maybe")
    with pytest.raises(ValueError):
        _rule(store, scope_type="galaxy")
    with pytest.raises(ValueError):
        _rule(store, action_type="blob")
    with pytest.raises(ValueError):
        _rule(store, fingerprint_kind="fuzzy")
    # non-host rules need a scope_id
    with pytest.raises(ValueError):
        _rule(store, scope_type="project", scope_id=None)
    # host rules are global
    host = _rule(store, scope_type="host", scope_id=None)
    assert host["scope_type"] == "host"


def test_policy_rule_filters(store):
    _rule(store, fingerprint="a", scope_type="project", scope_id="p1")
    _rule(store, fingerprint="b", scope_type="task", scope_id="t1", effect="deny")
    assert len(store.list_policy_rules(scope_type="project")) == 1
    assert len(store.list_policy_rules(effect="deny")) == 1
    assert len(store.list_policy_rules(scope_id="t1")) == 1


def test_matching_policy_rules_scope_filtering(store):
    shared_fp = "fp-shared"
    _rule(store, fingerprint=shared_fp, scope_type="project", scope_id="proj-1")
    _rule(store, fingerprint=shared_fp, scope_type="project", scope_id="proj-2")
    _rule(store, fingerprint=shared_fp, scope_type="task", scope_id="task-9")
    _rule(store, fingerprint=shared_fp, scope_type="custom_agent", scope_id="ca-1")

    # Task-1 in proj-1: sees proj-1 rule only (not proj-2, not other task's,
    # not the custom-agent rule).
    matched = store.matching_policy_rules(
        fingerprint=shared_fp, scopes=[("task", "task-1"), ("project", "proj-1")]
    )
    assert len(matched) == 1
    assert matched[0]["scope_type"] == "project"

    # Host-scope rules always match.
    _rule(store, fingerprint=shared_fp, scope_type="host", scope_id=None)
    matched = store.matching_policy_rules(fingerprint=shared_fp, scopes=[])
    assert [r["scope_type"] for r in matched] == ["host"]


def test_matching_policy_rules_skips_revoked_and_expired(store):
    _rule(store, fingerprint="fp-live")
    dead = _rule(store, fingerprint="fp-dead")
    store.revoke_policy_rule(dead["id"])
    expired = _rule(store, fingerprint="fp-old", expires_at=time.time() - 10)
    matched = store.matching_policy_rules(
        fingerprint="fp-live", scopes=[("project", "proj-1")]
    )
    assert len(matched) == 1
    assert not store.matching_policy_rules(
        fingerprint="fp-dead", scopes=[("project", "proj-1")]
    )
    assert not store.matching_policy_rules(
        fingerprint="fp-old", scopes=[("project", "proj-1")]
    )


def test_expire_task_policy_rules(store):
    task_rule = _rule(store, fingerprint="fp", scope_type="task", scope_id="task-7", task_id="task-7")
    project_rule = _rule(store, fingerprint="fp")
    assert store.expire_task_policy_rules("task-7") == 1
    assert store.get_policy_rule(task_rule["id"])["expires_at"] is not None
    assert store.get_policy_rule(project_rule["id"])["expires_at"] is None
    # expired task rule no longer matches; the live project rule still does
    matched = store.matching_policy_rules(
        fingerprint="fp", scopes=[("task", "task-7"), ("project", "proj-1")]
    )
    assert [r["id"] for r in matched] == [project_rule["id"]]


def test_touch_and_update_policy_rule(store):
    rule = _rule(store, fingerprint="fp")
    store.touch_policy_rule(rule["id"])
    store.touch_policy_rule(rule["id"])
    updated = store.get_policy_rule(rule["id"])
    assert updated["times_used"] == 2
    assert updated["last_used_at"] is not None

    changed = store.update_policy_rule(rule["id"], display="new label", effect="deny", capabilities=["net.outbound:any"])
    assert changed["display"] == "new label"
    assert changed["effect"] == "deny"
    assert changed["capabilities"] == ["net.outbound:any"]
    with pytest.raises(ValueError):
        store.update_policy_rule(rule["id"], scope_type="host")
    with pytest.raises(KeyError):
        store.update_policy_rule("nonexistent", display="x")


def test_capability_rules_scoped(store):
    _rule(
        store, fingerprint="cap", action_type="capability",
        capabilities=["net.outbound:any"], scope_type="project", scope_id="proj-1",
    )
    _rule(
        store, fingerprint="cap", action_type="capability",
        capabilities=["docker.socket"], scope_type="task", scope_id="task-5",
    )
    got = store.capability_rules(scopes=[("project", "proj-1")])
    assert [r["capabilities"] for r in got] == [["net.outbound:any"]]
    got = store.capability_rules(scopes=[("task", "task-5")])
    assert [r["capabilities"] for r in got] == [["docker.socket"]]


def test_custom_agent_mode_columns(store):
    agent = store.create_custom_agent(
        name="a",
        approval_mode="autonomous",
        sandbox_profile="workspace",
    )
    assert agent["approval_mode"] == "autonomous"
    assert agent["sandbox_profile"] == "workspace"
    assert store.get_custom_agent(agent["id"])["approval_mode"] == "autonomous"

    updated = store.update_custom_agent(agent["id"], approval_mode="remember")
    assert updated["approval_mode"] == "remember"

    with pytest.raises(ValueError):
        store.create_custom_agent(name="bad", approval_mode="yolo")
    with pytest.raises(ValueError):
        store.create_custom_agent(name="bad2", sandbox_profile="root")


def test_task_custom_agent_id(store):
    agent = store.create_custom_agent(name="a")
    task = store.create_task(
        prompt="p", cwd="/tmp", provider_id="p", model="m", limits={},
        custom_agent_id=agent["id"],
    )
    assert store.get_task(task["id"])["custom_agent_id"] == agent["id"]
    plain = store.create_task(prompt="p", cwd="/tmp", provider_id="p", model="m", limits={})
    assert store.get_task(plain["id"])["custom_agent_id"] is None


# ---------------------------------------------------------------------------
# Engine precedence — the order is the security contract.
# ---------------------------------------------------------------------------


def _engine(store, envelope=None):
    return PolicyEngine(store, envelope=envelope)


def test_remembered_allow_auto_resolves(store, tmp_path):
    engine = _engine(store)
    ctx = _ctx(tmp_path)
    fp, _ = fingerprint_command("pnpm install", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="project", scope_id="proj-1")

    decision = _shell_decision(engine, "pnpm install", ctx)
    assert decision.allowed is True
    assert decision.approval_required is False
    assert decision.auto_resolved == "allow"
    assert decision.matched_rule_id is not None
    rule = store.get_policy_rule(decision.matched_rule_id)
    assert rule["times_used"] == 1  # touch recorded


def test_remembered_deny_beats_remembered_allow(store, tmp_path):
    engine = _engine(store)
    ctx = _ctx(tmp_path)
    fp, _ = fingerprint_command("git push origin main", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="project", scope_id="proj-1", effect="allow")
    _rule(store, fingerprint=fp, scope_type="task", scope_id="task-1", effect="deny")

    decision = _shell_decision(engine, "git push origin main", ctx)
    assert decision.auto_resolved == "deny"
    assert decision.allowed is False
    assert decision.approval_required is False


def test_changed_command_prompts_again(store, tmp_path):
    """The remembered approval for `pnpm install` must not bless a riskier
    or simply different command — fingerprints stay narrow."""
    engine = _engine(store)
    ctx = _ctx(tmp_path)
    fp, _ = fingerprint_command("pnpm install", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="project", scope_id="proj-1")

    for command in ("pnpm install evil-pkg", "pnpm publish", "pnpm install && rm -rf /", "pnpm up"):
        decision = _shell_decision(engine, command, ctx)
        assert decision.auto_resolved != "allow", command
        assert decision.approval_required is True, command


def test_project_rule_does_not_leak_into_other_project(store, tmp_path):
    engine = _engine(store)
    fp, _ = fingerprint_command("pnpm install", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="project", scope_id="proj-1")

    other = _ctx(tmp_path, project_id="proj-2")
    decision = _shell_decision(engine, "pnpm install", other)
    assert decision.auto_resolved is None
    assert decision.approval_required is True


def test_task_rule_does_not_leak_into_sibling_task(store, tmp_path):
    engine = _engine(store)
    fp, _ = fingerprint_command("pnpm test", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="task", scope_id="task-1", task_id="task-1")

    sibling = _ctx(tmp_path, task_id="task-2")
    decision = _shell_decision(engine, "pnpm test", sibling)
    assert decision.auto_resolved is None
    assert decision.approval_required is True


def test_custom_agent_rule_applies_only_to_that_agent(store, tmp_path):
    store.create_custom_agent(name="bot", approval_mode="remember")
    agent_id = next(iter(store.list_custom_agents()))["id"]
    engine = _engine(store)
    fp, _ = fingerprint_command("pnpm lint", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="custom_agent", scope_id=agent_id)

    mine = _ctx(tmp_path, custom_agent_id=agent_id)
    theirs = _ctx(tmp_path, custom_agent_id="ca-other")
    assert _shell_decision(engine, "pnpm lint", mine).auto_resolved == "allow"
    assert _shell_decision(engine, "pnpm lint", theirs).auto_resolved is None


def test_deny_wins_over_allow_at_any_scope(store, tmp_path):
    """A narrow deny outranks a broad allow — explicit denials always win."""
    engine = _engine(store)
    ctx = _ctx(tmp_path)
    fp, _ = fingerprint_command("npm publish", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="project", scope_id="proj-1", effect="allow")
    _rule(store, fingerprint=fp, scope_type="task", scope_id="task-1", effect="deny")
    decision = _shell_decision(engine, "npm publish", ctx)
    assert decision.auto_resolved == "deny"


# ---------------------------------------------------------------------------
# Capability boundary — remembered approvals never conjure sandbox powers.
# ---------------------------------------------------------------------------

_RESTRICTED_ENVELOPE = frozenset(
    {
        "process.execute:any",
        "process.children:any",
        "fs.workspace:any",
    }
)


def test_missing_grantable_capability_asks_separately(store, tmp_path):
    engine = _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE)
    ctx = _ctx(tmp_path)
    # `curl` requires net.outbound — not in the envelope.
    decision = _shell_decision(engine, "curl https://example.com/x", ctx)
    assert decision.approval_kind == "capability"
    assert decision.approval_required is True
    assert "net.outbound" in decision.required_capabilities[0]


def test_remembered_allow_cannot_punch_through_missing_capability(store, tmp_path):
    """THE core invariant: an execution-side remembered rule never overrides a
    sandbox capability gap."""
    engine = _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE)
    ctx = _ctx(tmp_path)
    fp, _ = fingerprint_command("curl https://example.com/x", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="project", scope_id="proj-1", effect="allow")

    decision = _shell_decision(engine, "curl https://example.com/x", ctx)
    # Execution allow exists, but capability is still missing → capability ask.
    assert decision.approval_kind == "capability"
    assert decision.approval_required is True
    assert decision.auto_resolved != "allow"


def test_remembered_capability_fills_gap_then_execution_rule_applies(store, tmp_path):
    engine = _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE)
    ctx = _ctx(tmp_path)
    fp, _ = fingerprint_command("curl https://example.com/x", str(tmp_path))
    _rule(
        store, fingerprint="cap", action_type="capability",
        capabilities=["net.outbound:any"], scope_type="project", scope_id="proj-1",
    )
    _rule(store, fingerprint=fp, scope_type="project", scope_id="proj-1", effect="allow")

    decision = _shell_decision(engine, "curl https://example.com/x", ctx)
    assert decision.auto_resolved == "allow"


def test_ungrantable_capability_is_hard_deny_not_ask(store, tmp_path):
    engine = _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE)
    ctx = _ctx(tmp_path)
    for command in ("sudo systemctl restart x", "kill -9 1", "docker run alpine"):
        decision = _shell_decision(engine, command, ctx)
        assert decision.approval_required is False, command
        assert decision.auto_resolved == "deny", command
        assert "Capability unavailable" in decision.reason


def test_capability_ask_does_not_imply_execution_approval(store, tmp_path):
    """Granting `net.outbound` does not silently approve the command that
    needed it — the action itself still has to pass policy."""
    engine = _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE)
    ctx = _ctx(tmp_path)
    _rule(
        store, fingerprint="cap", action_type="capability",
        capabilities=["net.outbound:any"], scope_type="project", scope_id="proj-1",
    )
    # No execution rule for curl → falls through to base ask.
    decision = _shell_decision(engine, "curl https://example.com/x", ctx)
    assert decision.approval_required is True
    assert decision.approval_kind == "action"
    assert decision.auto_resolved is None


# ---------------------------------------------------------------------------
# Autonomous mode — bounded auto-approval inside the envelope.
# ---------------------------------------------------------------------------


def test_autonomous_mode_auto_approves_safe_ops(store, tmp_path):
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    engine = _engine(store)
    ctx = _ctx(tmp_path, custom_agent_id=agent["id"])

    base = PolicyDecision(False, True, "Needs approval", "runs x")
    call = _call("pnpm test", name="run_shell")
    decision = engine.decide_shell(call, ctx, "pnpm test", base)
    assert decision.auto_resolved == "autonomous"
    assert decision.approval_required is False


def test_autonomous_mode_never_approves_sensitive_or_publication(store, tmp_path):
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    engine = _engine(store)
    ctx = _ctx(tmp_path, custom_agent_id=agent["id"])

    base = PolicyDecision(False, True, "External publication", "Publishes to origin")
    call = _call("git push origin main")
    decision = engine.decide_shell(call, ctx, "git push origin main", base)
    # `git push` is a publication-class risk — autonomous must still ask.
    assert decision.auto_resolved != "autonomous"
    assert decision.approval_required is True


def test_autonomous_still_asks_for_capability_elevation(store, tmp_path):
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    engine = _engine(store, envelope=lambda _p: _RESTRICTED_ENVELOPE)
    ctx = _ctx(tmp_path, custom_agent_id=agent["id"])
    decision = _shell_decision(engine, "curl https://x", ctx)
    assert decision.approval_kind == "capability"
    assert decision.auto_resolved != "autonomous"


def test_autonomous_denies_still_win(store, tmp_path):
    agent = store.create_custom_agent(name="auto", approval_mode="autonomous")
    engine = _engine(store)
    ctx = _ctx(tmp_path, custom_agent_id=agent["id"])
    fp, _ = fingerprint_command("pnpm test", str(tmp_path))
    _rule(store, fingerprint=fp, scope_type="host", scope_id=None, effect="deny")
    decision = _shell_decision(engine, "pnpm test", ctx)
    assert decision.auto_resolved == "deny"


def test_remember_mode_does_not_auto_approve(store, tmp_path):
    agent = store.create_custom_agent(name="bot", approval_mode="remember")
    engine = _engine(store)
    ctx = _ctx(tmp_path, custom_agent_id=agent["id"])
    decision = _shell_decision(engine, "pnpm test", ctx)
    assert decision.auto_resolved is None
    assert decision.approval_required is True
    assert "custom_agent" in decision.remember_options


# ---------------------------------------------------------------------------
# record_resolution — durable rules from approvals.
# ---------------------------------------------------------------------------


def test_record_resolution_writes_scoped_rules(store, tmp_path):
    engine = _engine(store)
    intent = PolicyIntent(
        action_type="tool", tool="run_shell", fingerprint="fp1",
        fingerprint_kind="conservative", display="pnpm install",
        cwd=str(tmp_path), project_id="proj-1", task_id="task-1",
    )
    rule = engine.record_resolution(
        intent, decision="approved", remember="project",
        project_id="proj-1", source_approval_id="a1",
    )
    assert rule["effect"] == "allow"
    assert rule["scope_type"] == "project"
    assert rule["scope_id"] == "proj-1"
    assert rule["source_approval_id"] == "a1"

    deny = engine.record_resolution(
        intent, decision="denied", remember="task",
        project_id="proj-1", source_approval_id="a2",
    )
    assert deny["effect"] == "deny"
    assert deny["scope_type"] == "task"
    assert deny["task_id"] == "task-1"


def test_record_resolution_rejects_scope_without_anchor(store, tmp_path):
    engine = _engine(store)
    intent = PolicyIntent(
        action_type="tool", tool="run_shell", fingerprint="fp1",
        fingerprint_kind="exact", display="x", cwd=str(tmp_path),
        project_id="", task_id="task-1",
    )
    with pytest.raises(ValueError):
        engine.record_resolution(
            intent, decision="approved", remember="custom_agent",
            project_id="", source_approval_id="a1",
        )
    # once / no remember writes nothing
    assert engine.record_resolution(
        intent, decision="approved", remember="once",
        project_id="p", source_approval_id="a",
    ) is None
    assert engine.record_resolution(
        intent, decision="approved", remember=None,
        project_id="p", source_approval_id="a",
    ) is None


def test_capability_resolution_writes_capability_rule(store, tmp_path):
    engine = _engine(store)
    intent = PolicyIntent(
        action_type="tool", tool="run_shell", fingerprint="fp-cap",
        fingerprint_kind="exact", display="curl", cwd=str(tmp_path),
        project_id="proj-1", task_id="task-1",
        required_capabilities=("net.outbound:any",),
    )
    rule = engine.record_resolution(
        intent, decision="approved", remember="project",
        project_id="proj-1", source_approval_id="a1", approval_kind="capability",
    )
    assert rule["action_type"] == "capability"
    assert rule["capabilities"] == ["net.outbound:any"]


# ---------------------------------------------------------------------------
# rule_public — API payload shape.
# ---------------------------------------------------------------------------


def test_rule_public_strips_internal_columns(store):
    rule = _rule(store, fingerprint="fp", matcher={"command": "pnpm install"})
    public = rule_public(rule)
    for key in ("matcher_json", "capabilities_json"):
        assert key not in public
    assert public["matcher"] == {"command": "pnpm install"}
    assert public["source_approval_id"] is None  # audit linkage is public-safe
