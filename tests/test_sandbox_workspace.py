"""PR C shared pieces — workspace terminal profile, pty spawn_argv surface,
runbook human/workspace profiles, provisioning lifecycle reporting.

The platform backends each carry their own boundary suites; this file pins
the profile-agnostic contract (linux-only tests skip where the backend is
unavailable, mirroring test_sandbox_linux.py).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from _gql import data, err_status

from termx.sandbox import SpawnSpec
from termx.sandbox.host import HostSandboxRunner
from termx.sandbox.provision import helper_path, provision_status


def _spec(tmp_path: Path, **overrides) -> SpawnSpec:
    base = dict(
        profile="workspace",
        argv=("echo", "hi"),
        cwd=str(tmp_path),
        workspace_root=str(tmp_path),
        writable_roots=(str(tmp_path),),
    )
    base.update(overrides)
    return SpawnSpec(**base)


def test_host_spawn_argv_passthrough(tmp_path: Path) -> None:
    runner = HostSandboxRunner(profile="workspace")
    spec = _spec(tmp_path)
    assert runner.spawn_argv(spec) == ["echo", "hi"]
    shell_spec = _spec(tmp_path, argv=None, shell="echo hi")
    argv = runner.spawn_argv(shell_spec)
    if os.name == "nt":
        assert argv[-1] == "echo hi" and argv[-2] == "/c"
    else:
        assert argv == ["/bin/sh", "-c", "echo hi"]


def test_provision_status_shape() -> None:
    status = provision_status()
    assert status["platform"] == sys.platform
    assert isinstance(status["checks"], list) and status["checks"]
    for check in status["checks"]:
        assert check["id"] and isinstance(check["ok"], bool)
        assert "detail" in check and "elevated" in check
        assert isinstance(check["required"], bool)
    assert isinstance(status["helper"]["installed"], bool)
    assert isinstance(status["ok"], bool)
    assert isinstance(status["fully_provisioned"], bool)
    assert isinstance(status["upgrades_pending"], list)


def test_provision_ok_requires_all_required_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ok` is only true when every primitive the backend needs passes —
    a missing optional upgrade must not flip it either way."""
    import termx.sandbox.provision as prov

    checks = [
        {"id": "a", "ok": True, "detail": "", "repair": None, "elevated": False, "required": True},
        {"id": "b", "ok": False, "detail": "", "repair": "x", "elevated": True, "required": True},
        {"id": "c", "ok": False, "detail": "", "repair": "y", "elevated": True, "required": False},
    ]
    monkeypatch.setattr(prov, "_linux_checks", lambda: checks)
    monkeypatch.setattr(sys, "platform", "linux")
    status = prov.provision_status()
    assert status["ok"] is False  # a required primitive missing
    assert status["fully_provisioned"] is False
    assert status["upgrades_pending"] == ["c"]

    checks[1]["ok"] = True
    status = prov.provision_status()
    assert status["ok"] is True  # required all pass; pending upgrade stays visible
    assert status["fully_provisioned"] is False
    assert status["upgrades_pending"] == ["c"]


def test_helper_path_honors_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TERMX_SANDBOX_HELPER", str(tmp_path / "custom-helper"))
    assert helper_path() == tmp_path / "custom-helper"


# -- linux-ns backed behavioral coverage -------------------------------------

linux_ns = pytest.importorskip("termx.sandbox.linux_ns")
linux_ns_skip = pytest.mark.skipif(
    not linux_ns.linux_ns_available(), reason="bwrap/userns unavailable"
)


@linux_ns_skip
def test_spawn_argv_pty_drops_new_session(tmp_path: Path) -> None:
    runner = linux_ns.LinuxNamespaceRunner(profile="workspace", state_dir=tmp_path / "state")
    pty_spec = _spec(tmp_path, pty=True)
    non_pty = _spec(tmp_path)
    assert "--new-session" not in runner.spawn_argv(pty_spec)
    assert "--new-session" in runner.spawn_argv(non_pty)


@linux_ns_skip
def test_workspace_terminal_launch_wires_sandbox(tmp_path: Path) -> None:
    from termx.sessions import Session

    session = Session(
        id="term01",
        title="ws",
        created_at=time.time(),
        cols=80,
        rows=24,
        argv=["/bin/bash", "-l"],
        cwd=str(tmp_path),
        shell="/bin/bash",
        sandbox_profile="workspace",
    )
    argv, env = session._restricted_launch()
    assert argv[0].endswith("bwrap")
    # workspace root bound rw; shell-integration dir mounted ro; net allowed
    # for the human's project shell; private HOME exported.
    joined = " ".join(argv)
    assert "--bind" in argv and str(tmp_path) in argv
    assert "--share-net" in argv
    assert "sandbox-home" in env["HOME"]
    assert "sandbox-home" in joined
    ro_marks = [i for i, a in enumerate(argv) if a == "--ro-bind"]
    assert any("termx-shell" in argv[i + 1] for i in ro_marks)


@linux_ns_skip
def test_restricted_launch_fails_closed_on_host_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A restricted terminal must never silently resolve to the unrestricted
    host backend — no kernel backend available = honest TerminalError."""
    from termx.sandbox.host import HostSandboxRunner
    from termx.sessions import Session
    from termx.terminals import TerminalError

    monkeypatch.setattr(
        "termx.sandbox.runner_for",
        lambda profile, **kw: HostSandboxRunner(profile=profile),
    )
    session = Session(
        id="termhost",
        title="ws",
        created_at=time.time(),
        cols=80,
        rows=24,
        argv=["/bin/bash"],
        cwd=str(tmp_path),
        shell="/bin/bash",
        sandbox_profile="workspace",
    )
    with pytest.raises(TerminalError):
        session._restricted_launch()


@linux_ns_skip
def test_agent_profile_terminal_gets_no_network(tmp_path: Path) -> None:
    """Only the workspace profile grants ambient outbound network — an
    `agent`-profile terminal (even if reached outside the API) stays
    deny-by-default like every other agent spawn."""
    from termx.sessions import Session

    session = Session(
        id="termagent",
        title="ws",
        created_at=time.time(),
        cols=80,
        rows=24,
        argv=["/bin/bash"],
        cwd=str(tmp_path),
        shell="/bin/bash",
        sandbox_profile="agent",
    )
    argv, _env = session._restricted_launch()
    assert "--unshare-net" in argv
    assert "--share-net" not in argv


@linux_ns_skip
def test_workspace_terminal_end_to_end(tmp_path: Path) -> None:
    """A real pty inside linux-ns: marker output flows back, the private HOME
    is exported, and job control stays with the outer session."""
    from termx.sessions import Session
    from termx.terminals import PosixTerminal

    session = Session(
        id="term02",
        title="ws",
        created_at=time.time(),
        cols=80,
        rows=24,
        argv=["/bin/bash"],
        cwd=str(tmp_path),
        shell="/bin/bash",
        sandbox_profile="workspace",
    )
    argv, env = session._restricted_launch()
    terminal = PosixTerminal(argv, str(tmp_path), env, 24, 80)
    try:
        terminal.write(b'printf "HOME_IS_%s\\n" "$HOME"; echo TERMX_MARK_42\n')
        out = b""
        deadline = time.time() + 15
        while b"HOME_IS_/" not in out and time.time() < deadline:
            chunk = terminal.read(0.25)
            if chunk:
                out += chunk
        home_line = next(
            (ln for ln in out.splitlines() if ln.startswith(b"HOME_IS_/")), b""
        )
        assert home_line.startswith(b"HOME_IS_/"), out[-400:]
        # private HOME under config sandbox-home — never the real $HOME
        assert b"sandbox-home" in home_line, home_line
        assert b"TERMX_MARK_42" in out, out[-400:]
        terminal.write(b"exit\n")
        deadline = time.time() + 10
        while terminal.proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert terminal.proc.poll() is not None
    finally:
        if terminal.proc.poll() is None:
            terminal.proc.kill()


@linux_ns_skip
def test_runbook_run_workspace_profile(tmp_path: Path) -> None:
    """Human-invoked runbook run with profile=workspace executes each step
    through the restricted backend (validated via SpawnSpec route)."""
    from fastapi.testclient import TestClient

    from termx.agent.secrets import CredentialStore
    from termx.agent.store import AgentStore
    from termx.app import AppState, create_app

    state = AppState(
        passcode="secret",
        agent_store=AgentStore(tmp_path / "rb.sqlite3", tmp_path / "rb-artifacts"),
        credentials=CredentialStore(memory={}),
    )
    headers = {"x-termx-passcode": "secret"}
    with TestClient(create_app(state, web_dir=None)) as client:
        runbook = data(
            client,
            'mutation($input: RunbookInput!) { create_runbook(input: $input) { id } }',
            "create_runbook",
            {"input": {"name": "ws", "steps": [{"kind": "shell", "command": "echo ok"}]}},
            headers=headers,
        )
        run = (
            'mutation($id: String!, $profile: String!) '
            "{ run_runbook(runbook_id: $id, profile: $profile) { id status } }"
        )
        # human runbooks are host|workspace only
        assert err_status(client, run, {"id": runbook["id"], "profile": "agent"}, headers) == 422
        resp = data(
            client, run, "run_runbook",
            {"id": runbook["id"], "profile": "workspace"}, headers=headers,
        )
        run_id = resp["id"]
        deadline = time.time() + 20
        run = None
        while time.time() < deadline:
            run = state.agent_store.get_runbook_run(run_id)
            if run and run["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "completed", run


def test_runbook_run_spawn_failure_marks_failed(tmp_path: Path) -> None:
    """A step that can't even spawn (backend raises) must record the run
    failed — never silently report completed."""
    import asyncio

    from termx.agent.store import AgentStore
    from termx.runbooks import RunbookRunner
    from termx.sandbox import SandboxCapabilities, SandboxFailure

    class _Boom:
        profile = "workspace"

        def capabilities(self):  # noqa: ANN202 - test double
            return SandboxCapabilities(backend="boom", profile="workspace")

        async def spawn(self, spec):  # noqa: ANN001, ANN202 - test double
            raise SandboxFailure("backend_unavailable", "nope")

    store = AgentStore(tmp_path / "rb.sqlite3", tmp_path / "rb-artifacts")
    runbook = store.create_runbook(
        name="boom", steps=[{"kind": "shell", "command": "echo ok"}]
    )
    runner = RunbookRunner(store, runner_for=lambda _p, **_kw: _Boom())

    async def go() -> dict:
        run = runner.start(runbook, str(tmp_path), profile="workspace")
        await asyncio.wait_for(runner._tasks[run["id"]], timeout=10)
        return run

    run = asyncio.run(go())
    final = store.get_runbook_run(run["id"])
    assert final is not None and final["status"] == "failed"
    assert final["error"]
    store.close()


@linux_ns_skip
def test_sessions_api_workspace_profile(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from termx.app import AppState, create_app

    state = AppState()
    with TestClient(create_app(state, web_dir=None)) as client:
        create = (
            'mutation($input: CreateSessionInput!) '
            "{ create_session(input: $input) { id } }"
        )
        assert err_status(client, create, {"input": {"sandbox_profile": "nope"}}) == 422
        # interactive terminals are host|workspace
        assert err_status(client, create, {"input": {"sandbox_profile": "agent"}}) == 422
        snap = data(
            client, create, "create_session",
            {"input": {"sandbox_profile": "workspace", "cwd": str(tmp_path)}},
        )
        assert snap["id"]
        state.sessions.kill(snap["id"])
