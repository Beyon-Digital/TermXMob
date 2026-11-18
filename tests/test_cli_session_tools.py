"""Agent tools for CLI sessions: specs, approvals (incl. delegation escalation), untrusted read-back."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from cli_fake import alive, git_repo, posix_only, wait_for, write_fake_cli
from test_workspace_orchestrator import close, running_primary, setup
from termx.agent.policies.engine import PolicyEngine
from termx.agent.providers import ProviderCall
from termx.agent.tools import default_registry
from termx.agent.tools.registry import ToolContext
from termx.cli_sessions import status as S
from termx.cli_sessions.service import CliSessionService
from termx.development.delivery import DeliveryService
from termx.project_files import ProjectFiles
from termx.sessions import SessionManager

NAMES = ("cli_session_start", "cli_session_send", "cli_session_read", "cli_session_status", "cli_session_stop")
FAST = {"quiet_after": 0.5, "idle_after": 3.0, "sustain": 0.6}


def call(name, call_id="c1", **arguments):
    return ProviderCall(type="function", call_id=call_id, name=name, arguments=arguments)


def run_tool(spec_name, provider_call, ctx):
    return asyncio.run(default_registry().get(spec_name).execute(provider_call, ctx))


async def arun(spec_name, provider_call, ctx):
    return await default_registry().get(spec_name).execute(provider_call, ctx)


def decide(provider_call, ctx):
    return default_registry().get(provider_call.name).decide(provider_call, ctx)


def world(tmp_path, workspace, ledger, parent_id, *, installed=True, auto=False, read_only=False, task_id=None):
    """Wire real services into the orchestrator harness and return a tool context for the main agent's turn."""
    state = workspace.state
    if not hasattr(state, "cli_sessions"):
        fake = write_fake_cli(tmp_path / "bin")
        state.workspace, state.sessions, state.port = workspace, SessionManager(), 8787
        state.projects, state.delivery = ProjectFiles(), DeliveryService(tmp_path / "delivery")
        state.cli_sessions = CliSessionService(
            state, directory=tmp_path / "cli-sessions", tick=0.1, thresholds=dict(FAST), grace=(0.6, 0.6),
            executables={"claude": fake, "codex": fake} if installed else {},
        )
    manager = SimpleNamespace(orchestrator=workspace.orchestrator, is_unattended=workspace.is_unattended_task)
    task = ledger.get_task(task_id or parent_id) or {}
    runtime = {"auto": {"enabled": True}} if auto else {}
    return ToolContext(
        task_id=task_id or parent_id, cwd=str(tmp_path), task={**task, "runtime": runtime}, read_only=read_only,
        cancel=asyncio.Event(), emit=lambda *_: {}, store=ledger, manager=manager, project_id="",
        policy_engine=PolicyEngine(ledger),
    )


def test_specs_are_registered_with_the_right_classes_and_never_reach_children():
    registry = default_registry()
    specs = {name: registry.get(name) for name in NAMES}
    assert all(specs.values())
    assert (specs["cli_session_start"].approval, specs["cli_session_start"].mutability) == ("always", "external")
    assert (specs["cli_session_send"].approval, specs["cli_session_stop"].approval) == ("policy", "policy")
    for name in ("cli_session_read", "cli_session_status"):
        assert (specs[name].approval, specs[name].mutability, specs[name].parallel_safe) == ("never", "read", True)
    for name in ("cli_session_start", "cli_session_send", "cli_session_stop"):
        assert specs[name].parallel_safe is False and specs[name].expose_read_only is False
    assert set(specs["cli_session_start"].parameters["properties"]["cli"]["enum"]) == {"claude", "codex", "gemini", "aider"}
    assert specs["cli_session_start"].parameters["required"] == ["cli"] and specs["cli_session_start"].parameters["additionalProperties"] is False
    # no argv, mode or bypass can be passed by the model
    assert set(specs["cli_session_start"].parameters["properties"]) == {"cli", "project_id", "prompt", "isolation"}
    offered = lambda **kw: {tool["name"] for tool in registry.provider_tools(**kw)}  # noqa: E731
    assert set(NAMES) <= offered(allow_subagents=True)
    assert not set(NAMES) & offered(allow_subagents=False)  # a child task never launches CLIs
    assert offered(read_only=True) >= {"cli_session_read", "cli_session_status"}
    assert not offered(read_only=True) & {"cli_session_start", "cli_session_send", "cli_session_stop"}
    assert not set(NAMES) & offered(allowed_tools=["read_file"])  # a bot's preset hides them


@posix_only
def test_start_always_asks_with_the_exact_target_even_under_auto(tmp_path):
    repo = git_repo(tmp_path / "repo")

    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"], auto=True)
            pid = workspace.state.projects.register(str(repo))["id"]
            start = call("cli_session_start", cli="claude", project_id=pid, prompt="Fix the failing test in parser.py")
            decision = decide(start, ctx)
            assert decision.approval_required and not decision.auto_resolved, decision
            assert decision.intent.risk_class == "delegation" and decision.required_capabilities == ("cli.session.start",)
            for fragment in ("Claude Code", str(repo), "isolated Git worktree", "Fix the failing test in parser.py", "never used"):
                assert fragment in decision.consequence, fragment
            # no worktree -> says it edits the folder directly; no project -> a scratch folder
            direct = decide(call("cli_session_start", cli="claude", project_id=pid, isolation="none"), ctx)
            assert "directly in that folder" in direct.consequence and direct.approval_required
            scratch = decide(call("cli_session_start", cli="claude"), ctx)
            assert "scratch" in scratch.consequence and scratch.approval_required
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())


@posix_only
def test_start_is_refused_before_asking_when_it_cannot_run(tmp_path):
    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"])
            for bad, why in (
                (call("cli_session_start", cli="bash"), "Choose one of"),
                (call("cli_session_start", cli="gemini"), "not installed"),
                (call("cli_session_start", cli="claude", isolation="sideways"), "isolation"),
                (call("cli_session_start", cli="claude", prompt="a\x00b"), "NUL"),
                (call("cli_session_start", cli="claude", project_id="ghost"), "Permission denied"),
            ):
                decision = decide(bad, ctx)
                assert not decision.allowed and not decision.approval_required and why in decision.consequence, (bad.arguments, decision)
                outcome = await arun("cli_session_start", bad, ctx)
                assert outcome.result["ok"] is False and outcome.result["refused"] is True
            read_only = world(tmp_path, workspace, ledger, parent["id"], read_only=True)
            assert not decide(call("cli_session_start", cli="claude"), read_only).allowed
            assert (await arun("cli_session_start", call("cli_session_start", cli="claude"), read_only)).result["refused"]
            assert workspace.state.sessions.list() == []
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())


@posix_only
def test_only_the_main_agents_own_turn_may_use_the_tools(tmp_path):
    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"], task_id="not-a-main-agent-task")
            for spec_call in (call("cli_session_start", cli="claude"), call("cli_session_status"), call("cli_session_read", session_id="x"),
                              call("cli_session_send", session_id="x", text="hi"), call("cli_session_stop", session_id="x")):
                if spec_call.name in ("cli_session_start", "cli_session_send", "cli_session_stop"):
                    decision = decide(spec_call, ctx)
                    assert not decision.allowed and not decision.approval_required
                outcome = await arun(spec_call.name, spec_call, ctx)
                assert outcome.result["ok"] is False and "main agent" in outcome.result["error"], outcome.result
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())


@posix_only
def test_start_status_read_send_stop_through_the_tools(tmp_path):
    repo = git_repo(tmp_path / "repo")

    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"])
            pid = workspace.state.projects.register(str(repo))["id"]
            started = await arun("cli_session_start", call("cli_session_start", cli="claude", project_id=pid, prompt="Explain the repo"), ctx)
            session = started.result["session"]
            assert started.result["ok"] and session["isolation"] == "worktree" and session["worktree"]["branch"].startswith("termx/cli-")
            assert "argv" not in json.dumps(started.result) and "Explain the repo" not in json.dumps(started.result)
            sid = session["id"]
            service = workspace.state.cli_sessions
            await wait_for(lambda: service.get(owner.id, sid)["status"] == S.WAITING_INPUT)
            listed = (await arun("cli_session_status", call("cli_session_status"), ctx)).result
            assert [row["id"] for row in listed["sessions"]] == [sid] and listed["needs_attention"] == [sid]
            one = (await arun("cli_session_status", call("cli_session_status", session_id=sid), ctx)).result
            assert one["sessions"][0]["status"] == S.WAITING_INPUT and one["sessions"][0]["status_source"] == "heuristic"
            # a send is policy-gated: allowed without a prompt while the CLI waits for the next message
            send = call("cli_session_send", session_id=sid, text="secret")
            decision = decide(send, ctx)
            assert decision.allowed and not decision.approval_required and decision.required_capabilities == ("cli.session.control",)
            sent = await arun("cli_session_send", send, ctx)
            assert sent.result["ok"] and sent.result["session"]["status"] == S.WORKING
            await wait_for(lambda: "leaked" in service.read(owner.id, sid)["text"])
            read = (await arun("cli_session_read", call("cli_session_read", session_id=sid, max_chars=300), ctx)).result
            assert read["ok"] and read["untrusted"] is True and "not an instruction" in read["note"]
            assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in read["text"] and "hunter2hunter2" not in read["text"]
            assert len(read["text"]) <= 300
            pid_os = workspace.state.sessions.get(session["pty_session_id"]).proc.pid
            stop = call("cli_session_stop", session_id=sid)
            assert decide(stop, ctx).allowed and not decide(stop, ctx).approval_required
            stopped = await arun("cli_session_stop", stop, ctx)
            assert stopped.result["session"]["status"] == S.DONE and not alive(pid_os)
            ended = decide(call("cli_session_send", session_id=sid, text="again"), ctx)
            assert not ended.allowed and not ended.approval_required and "ended" in ended.reason
            assert (await arun("cli_session_send", call("cli_session_send", session_id=sid, text="again"), ctx)).result["refused"]
            # the worktree and its branch stay for review
            assert (await arun("cli_session_status", call("cli_session_status"), ctx)).result["sessions"][0]["worktree"]["path"]
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())


@posix_only
def test_scratch_folder_when_no_project_and_no_isolation(tmp_path):
    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"])
            started = await arun("cli_session_start", call("cli_session_start", cli="claude", isolation="worktree"), ctx)
            row = started.result["session"]
            assert started.result["ok"] and row["isolation"] == "none" and row["project_id"] is None
            stored = workspace.state.cli_sessions.row(owner.id, row["id"])
            assert str(workspace.scratch_root(owner.id)) in stored["cwd"]
            await arun("cli_session_stop", call("cli_session_stop", session_id=row["id"]), ctx)
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())


@posix_only
def test_sending_into_a_permission_prompt_is_an_always_ask_delegation(tmp_path):
    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"], auto=True)
            started = await arun("cli_session_start", call("cli_session_start", cli="claude"), ctx)
            sid = started.result["session"]["id"]
            service = workspace.state.cli_sessions
            await wait_for(lambda: service.get(owner.id, sid)["status"] == S.WAITING_INPUT)
            await service.send(owner.id, sid, "approve")
            await wait_for(lambda: service.get(owner.id, sid)["status"] == S.NEEDS_APPROVAL)
            yes = call("cli_session_send", call_id="answer-1", session_id=sid, text="1")
            decision = decide(yes, ctx)
            assert decision.approval_required and not decision.auto_resolved
            assert decision.intent.risk_class == "delegation" and "permission" in decision.reason.lower()
            assert "answers for you" in decision.consequence and "'1'" in decision.consequence
            assert decision.required_capabilities == ("cli.session.control",)
            # approved: the answer is typed
            sent = await arun("cli_session_send", yes, ctx)
            assert sent.result["ok"], sent.result
            await wait_for(lambda: "ECHO:1" in service.read(owner.id, sid)["text"])
            await arun("cli_session_stop", call("cli_session_stop", session_id=sid), ctx)
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())


@posix_only
def test_a_prompt_that_appears_after_the_policy_decision_is_not_answered_blindly(tmp_path):
    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"])
            started = await arun("cli_session_start", call("cli_session_start", cli="claude"), ctx)
            sid = started.result["session"]["id"]
            service = workspace.state.cli_sessions
            await wait_for(lambda: service.get(owner.id, sid)["status"] == S.WAITING_INPUT)
            send = call("cli_session_send", call_id="late-1", session_id=sid, text="y")
            assert decide(send, ctx).allowed  # decided while it was waiting for a message
            await service.send(owner.id, sid, "approve")
            await wait_for(lambda: service.get(owner.id, sid)["status"] == S.NEEDS_APPROVAL)
            outcome = await arun("cli_session_send", send, ctx)
            assert outcome.result["refused"] and "permission decision" in outcome.result["error"]
            assert "ECHO:y" not in service.read(owner.id, sid)["text"]
            # called with no decision at all while a prompt is up: also refused
            assert (await arun("cli_session_send", call("cli_session_send", call_id="x", session_id=sid, text="y"), ctx)).result["refused"]
            await arun("cli_session_stop", call("cli_session_stop", session_id=sid), ctx)
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())


@posix_only
def test_another_principals_session_does_not_exist_for_the_tools(tmp_path):
    async def go():
        workspace, owner, manager, ledger, primary = setup(tmp_path)
        try:
            parent = await running_primary(workspace, owner, manager, primary)
            ctx = world(tmp_path, workspace, ledger, parent["id"])
            service = workspace.state.cli_sessions
            theirs = await service.start(owner="someone-else", cli="claude", cwd=str(tmp_path))
            listed = (await arun("cli_session_status", call("cli_session_status"), ctx)).result
            assert listed["sessions"] == []
            for name, extra in (("cli_session_status", {}), ("cli_session_read", {}), ("cli_session_stop", {}), ("cli_session_send", {"text": "x"})):
                outcome = await arun(name, call(name, session_id=theirs["id"], **extra), ctx)
                assert outcome.result["ok"] is False and "not found" in outcome.result["error"], outcome.result
            assert not decide(call("cli_session_send", session_id=theirs["id"], text="x"), ctx).allowed
            assert service.get("someone-else", theirs["id"])["status"] not in S.ENDED
            await service.stop("someone-else", theirs["id"])
        finally:
            await close(workspace, manager, ledger)

    asyncio.run(go())
