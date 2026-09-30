"""Windows sandbox integration tests (restricted token + Job Object).

These prove the boundary, not return values: every assertion exercises the
kernel-enforced property end to end — a skipped suite means the
restricted-token/Job primitives are not available on this host, in which
case the backend is never advertised.

Deliberately honest deltas from the Linux suite:

* No network-denial test — there is no unprivileged Windows primitive that
  isolates outbound networking (WFAS rules need admin). The backend reports
  ``network_control=False`` and *ignores* ``spec.network``; pretending
  otherwise would be worse than sharing the host net.
* Host-private files stay *readable* (same user, DACL allows) — the enforced
  boundary is writes, so the read test is replaced by a write-denial test.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from termx.sandbox import (
    ResourceLimits,
    SandboxFailure,
    SpawnSpec,
    runner_for,
    windows_backend_available,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not windows_backend_available(),
    reason="restricted-token + Job Object primitives require Windows",
)


def _runner(tmp_path: Path, profile: str = "agent"):
    return runner_for(profile, backend="windows", state_dir=tmp_path / "state")


def _spec(workspace: Path, command: str, profile: str = "agent", **kw) -> SpawnSpec:
    return SpawnSpec(
        profile=profile,
        shell=command,
        cwd=str(workspace),
        workspace_root=str(workspace),
        writable_roots=[str(workspace)],
        **kw,
    )


async def _run(runner, spec: SpawnSpec) -> tuple[int, str]:
    spawned = await runner.spawn(spec)
    out = await asyncio.wait_for(spawned.process.stdout.read(), timeout=45)
    await asyncio.wait_for(spawned.wait(), timeout=15)
    return spawned.process.returncode or 0, out.decode("utf-8", "replace")


def test_agent_profile_runs_windows_backend(tmp_path):
    runner = _runner(tmp_path)
    caps = runner.capabilities()
    # windows-token (restricted token) or windows-user (provisioned account)
    assert caps.backend in ("windows-token", "windows-user")
    assert caps.strength in ("kernel", "restricted-user")
    assert caps.filesystem_isolation and caps.resource_limits
    assert caps.process_tree_kill
    # No unprivileged outbound-net primitive exists on Windows — reported
    # honestly instead of pretending.
    assert caps.network_control is False
    assert "net.outbound:any" not in caps.grantable
    # identity_isolation only in the provisioned dedicated-user mode.
    assert caps.identity_isolation == (caps.backend == "windows-user")


def test_workspace_read_write(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "seed.txt").write_text("seed-content")
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(runner, _spec(ws, "type seed.txt & echo new > created.txt & echo OK"))
    )
    assert "seed-content" in out
    assert "OK" in out
    assert (ws / "created.txt").read_text().strip().startswith("new")


def test_child_really_runs_low_integrity(tmp_path):
    """The spawned process token must carry Low Mandatory Level — the actual
    enforcement hook for the whole filesystem boundary."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(runner, _spec(ws, 'whoami /groups | findstr /c:"Mandatory Label"'))
    )
    assert "Low Mandatory Level" in out


def test_cannot_write_outside_approved_roots(tmp_path):
    host_private = tmp_path / "host-private"
    host_private.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                f'echo x > "{host_private}\\evil.txt" 2>nul && echo WROTE || echo DENIED',
            ),
        )
    )
    assert "DENIED" in out and "WROTE" not in out
    assert not (host_private / "evil.txt").exists()


def test_no_dotdot_write_escape(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(ws, r'echo x > ..\escape.txt 2>nul && echo WROTE || echo DENIED'),
        )
    )
    assert "DENIED" in out and "WROTE" not in out
    assert not (tmp_path / "escape.txt").exists()


def test_read_only_root_readable_not_writable(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "file.txt").write_text("original")
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(
        ws,
        f'type "{checkout}\\file.txt" & echo change >> "{checkout}\\file.txt" 2>nul '
        "&& echo RO_VIOLATION || echo RO_OK & echo w > wt.txt && echo WT_OK",
        read_only_roots=[str(checkout)],
    )
    _, out = asyncio.run(_run(runner, spec))
    assert "original" in out  # reading an approved ro root works
    assert "RO_OK" in out and "RO_VIOLATION" not in out
    assert "WT_OK" in out
    assert (checkout / "file.txt").read_text() == "original"
    assert (ws / "wt.txt").read_text().strip().startswith("w")


def test_no_inherited_host_secret_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_HOST_SECRET", "abc123")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIAFAKE")
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(runner, _spec(ws, "set"))
    )
    assert "FAKE_HOST_SECRET" not in out
    assert "AWS_SECRET_ACCESS_KEY" not in out
    assert "abc123" not in out and "AKIAFAKE" not in out


def test_env_is_clean_and_home_private(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(_run(runner, _spec(ws, "echo %USERPROFILE% & echo %TERM%")))
    home_line = [l for l in out.splitlines() if "USERPROFILE" not in l and l.strip()][0]
    assert "sandbox" in home_line.replace("/", "\\") or "sandbox" in home_line
    assert "xterm" in out


def test_cannot_tamper_with_unrelated_host_process(tmp_path):
    """A low-integrity token cannot OpenProcess a medium-integrity host
    process for *modification* rights — the mandatory-label no-write-up
    check denies VM_WRITE/CREATE_THREAD/DUP_HANDLE/SET_INFO, i.e. the whole
    memory-tamper and injection surface.

    PROCESS_TERMINATE is NOT in the integrity write-up mask on Windows, so
    in token mode (same identity) a low-IL child can still kill its host
    siblings — inherent to single-identity mode. User mode (a different
    account) closes that too, and is asserted when provisioned."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    user_mode = runner.capabilities().backend == "windows-user"
    host_pid = os.getpid()
    script = (
        "import ctypes,sys,ctypes.wintypes as wt;"
        "k=ctypes.WinDLL('kernel32',use_last_error=True);"
        "k.OpenProcess.restype=wt.HANDLE;k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];"
        "pid=int(sys.argv[1]);"
        "checks=[(0x20,'VM_WRITE'),(0x2,'CREATE_THREAD'),(0x400,'DUP_HANDLE'),"
        "(0x40,'SET_INFO'),(0x800,'SUSPEND_RESUME'),(0x8,'VM_OPERATION'),(0x1,'TERMINATE')];"
        "[print(n,'OK' if k.OpenProcess(m,False,pid) else 'DENIED') for m,n in checks]"
    )
    spec = SpawnSpec(
        profile="agent",
        argv=(sys.executable, "-c", script, str(host_pid)),
        cwd=str(ws),
        workspace_root=str(ws),
        writable_roots=[str(ws)],
    )
    _, out = asyncio.run(_run(runner, spec))
    denied = {"VM_WRITE", "CREATE_THREAD", "DUP_HANDLE", "SET_INFO",
              "SUSPEND_RESUME", "VM_OPERATION"}
    if user_mode:
        denied.add("TERMINATE")  # different account: no kill rights on host procs
    for right in denied:
        assert f"{right} DENIED" in out, right
        assert f"{right} OK" not in out


def test_large_output_fully_delivered(tmp_path):
    """A child that writes a big tail then exits must lose nothing — the shim
    drains its pipes to EOF, not a fixed timeout."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    nbytes = 2_000_000
    spec = SpawnSpec(
        profile="agent",
        argv=(
            sys.executable, "-c",
            f"import sys;sys.stdout.write('x'*{nbytes})",
        ),
        cwd=str(ws),
        workspace_root=str(ws),
        writable_roots=[str(ws)],
    )
    _, out = asyncio.run(_run(runner, spec))
    assert len(out.strip()) == nbytes


def test_argv_spawn_and_exit_code_relay(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = SpawnSpec(
        profile="agent",
        argv=(sys.executable, "-c", "import sys;sys.stdout.write('EXITED');sys.exit(7)"),
        cwd=str(ws),
        workspace_root=str(ws),
        writable_roots=[str(ws)],
    )

    async def go():
        spawned = await runner.spawn(spec)
        out = await asyncio.wait_for(spawned.process.stdout.read(), timeout=45)
        return await spawned.wait(), out

    code, out = asyncio.run(go())
    assert code == 7  # the child's real exit code crosses the shim
    assert b"EXITED" in out


def test_cancellation_kills_process_tree(tmp_path):
    """terminate() -> shim dies -> job handle closes -> KILL_ON_JOB_CLOSE
    reaps the whole restricted tree, descendants included."""
    ws = tmp_path / "ws"
    ws.mkdir()
    heartbeat = ws / "beat.txt"
    runner = _runner(tmp_path)
    child = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c',"
        "'import time,sys,pathlib;p=pathlib.Path(sys.argv[1]);"
        "[p.write_text(str(time.time())) or time.sleep(0.1) for _ in iter(int,1)]',"
        "sys.argv[1]]);"
        "time.sleep(60)"
    )
    spec = SpawnSpec(
        profile="agent",
        argv=(sys.executable, "-c", child, str(heartbeat)),
        cwd=str(ws),
        workspace_root=str(ws),
        writable_roots=[str(ws)],
    )

    async def go():
        spawned = await runner.spawn(spec)
        for _ in range(100):
            if heartbeat.exists():
                break
            await asyncio.sleep(0.1)
        assert heartbeat.exists(), "grandchild never started beating"
        await spawned.terminate()
        # A surviving grandchild would keep rewriting this file. Compare two
        # reads taken after the kill — a kill mid-write can leave a torn file,
        # so a single read is not a reliable "stopped" signal.
        await asyncio.sleep(0.6)
        first = heartbeat.read_text()
        await asyncio.sleep(0.6)
        assert heartbeat.read_text() == first

    asyncio.run(go())


def test_job_active_process_limit_enforced(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(
        ws,
        "whoami >nul && echo RAN || echo LIMITED",
        limits=ResourceLimits(pids=1),  # the cmd itself already fills the job
    )
    _, out = asyncio.run(_run(runner, spec))
    assert "LIMITED" in out
    assert "RAN" not in out


def test_job_memory_limit_enforced(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = SpawnSpec(
        profile="agent",
        argv=(
            sys.executable, "-c",
            "try:\n"
            " x=bytearray(512*1024*1024);x[:]=b'1'*len(x)\n"
            "except MemoryError:\n"
            " print('LIMIT_HIT')\n"
            "else:\n"
            " print('ALLOC_OK')",
        ),
        cwd=str(ws),
        workspace_root=str(ws),
        writable_roots=[str(ws)],
        limits=ResourceLimits(memory_bytes=128 << 20),
    )
    _, out = asyncio.run(_run(runner, spec))
    assert "LIMIT_HIT" in out
    assert "ALLOC_OK" not in [l.strip() for l in out.splitlines()]


def test_network_control_honestly_absent(tmp_path):
    """spec.network is ignored — outbound isolation is unenforceable
    unprivileged, so the child shares the host network even for
    ``network='none'`` and ``network_control`` reports False rather than
    pretending a boundary exists."""
    import socket as sk
    import threading

    listener = sk.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted: list[bytes] = []

    def serve() -> None:
        try:
            conn, _ = listener.accept()
            accepted.append(conn.recv(16))
            conn.close()
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    script = (
        "import socket;"
        f"s=socket.create_connection(('127.0.0.1',{port}),3);"
        "s.sendall(b'PING');print('SENT')"
    )
    spec = SpawnSpec(
        profile="agent",
        argv=(sys.executable, "-c", script),
        cwd=str(ws),
        workspace_root=str(ws),
        writable_roots=[str(ws)],
        network="none",  # requested denial — documented as not enforceable
    )
    _, out = asyncio.run(_run(runner, spec))
    thread.join(timeout=5)
    listener.close()
    assert "SENT" in out and accepted == [b"PING"]
    assert runner.capabilities().network_control is False


def test_spawn_failure_never_downgrades(tmp_path, monkeypatch):
    """A backend that can't construct the spawn raises — it must never
    silently exec unsandboxed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    monkeypatch.setattr(
        runner, "_shim_argv",
        lambda _p: ["C:\\definitely\\not\\a\\python.exe", "-m", "nope"],
    )
    spec = _spec(ws, "echo hi")

    async def go() -> None:
        await runner.spawn(spec)

    with pytest.raises(SandboxFailure) as err:
        asyncio.run(go())
    assert err.value.reason == "spawn_failed"


def test_boundary_setup_failure_raises(tmp_path, monkeypatch):
    """If the filesystem boundary cannot be applied the spawn raises
    ``boundary_setup`` rather than running unbounded."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    monkeypatch.setattr("termx.sandbox.windows_runner._ICACLS", None)
    spec = _spec(ws, "echo hi")

    async def go() -> None:
        await runner.spawn(spec)

    with pytest.raises(SandboxFailure) as err:
        asyncio.run(go())
    assert err.value.reason == "boundary_setup"


def test_runner_rejects_host_profile(tmp_path):
    with pytest.raises(SandboxFailure) as err:
        runner_for("host", backend="windows")
    assert err.value.reason == "invalid_profile"


def test_runner_rejects_foreign_profile(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path, profile="workspace")

    async def go() -> None:
        await runner.spawn(_spec(ws, "echo hi", profile="agent"))

    with pytest.raises(SandboxFailure) as err:
        asyncio.run(go())
    assert err.value.reason == "invalid_profile"


def test_default_backend_selection_on_windows(monkeypatch):
    monkeypatch.delenv("TERMX_SANDBOX_BACKEND", raising=False)
    caps = runner_for("agent").capabilities()
    assert caps.backend in ("windows-token", "windows-user")
    monkeypatch.setenv("TERMX_SANDBOX_BACKEND", "windows")
    assert runner_for("agent").capabilities().backend in (
        "windows-token", "windows-user")
    monkeypatch.setenv("TERMX_SANDBOX_BACKEND", "host")
    assert runner_for("agent").capabilities().backend == "host"


def test_run_shell_path_runs_sandboxed(tmp_path):
    """The agent run_shell funnel must land in the windows backend when a
    restricted profile is in play (regression: no unsandboxed spawn path)."""
    from termx.agent.execution import run_shell

    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    result = asyncio.run(
        run_shell(
            'whoami /groups | findstr /c:"Low Mandatory Level"',
            str(ws), runner=runner, profile="agent",
        )
    )
    assert "Low Mandatory Level" in result.output


def test_user_mode_runs_as_restricted_identity(tmp_path):
    """Dedicated-user mode: the child is the termx-sandbox account at Low
    Mandatory Level — identity AND integrity boundaries both real."""
    runner = _runner(tmp_path)
    caps = runner.capabilities()
    if caps.backend != "windows-user":
        pytest.skip("termx-sandbox account not provisioned on this host")
    assert caps.strength == "restricted-user"
    assert caps.identity_isolation
    ws = tmp_path / "ws"
    ws.mkdir()
    _, out = asyncio.run(
        _run(runner, _spec(
            ws, 'whoami & whoami /groups | findstr /c:"Mandatory Label"')))
    assert "termx-sandbox" in out
    assert "Low Mandatory Level" in out


def test_workspace_labels_restored_after_spawn(tmp_path):
    """A spawn's low-IL label + DACL grant must be revoked when the last
    holder exits — otherwise a later low-IL task under the same principal
    could write a workspace that was never in its SpawnSpec."""
    ws_a = tmp_path / "ws-a"
    ws_a.mkdir()
    ws_b = tmp_path / "ws-b"
    ws_b.mkdir()
    runner = _runner(tmp_path)

    async def run_once(spec):
        spawned = await runner.spawn(spec)
        out = await asyncio.wait_for(spawned.process.stdout.read(), timeout=45)
        await asyncio.wait_for(spawned.wait(), timeout=15)
        return out.decode("utf-8", "replace")

    async def go():
        out_a = await run_once(_spec(ws_a, "echo seed > a.txt & echo A_OK"))
        assert "A_OK" in out_a
        # The release is fire-and-forget on the runner's loop: it must
        # finish its icacls restore before the next spawn is a fair test.
        for _ in range(600):
            if not runner._release_tasks:
                break
            await asyncio.sleep(0.05)
        assert not runner._release_tasks, "label restore never ran"
        return await run_once(_spec(
            ws_b,
            f'echo x > "{ws_a}\\intruder.txt" 2>nul && echo WROTE || echo DENIED'))

    out_b = asyncio.run(go())
    assert "DENIED" in out_b and "WROTE" not in out_b
    assert not (ws_a / "intruder.txt").exists()


def test_exit_then_spawn_same_workspace_stays_writable(tmp_path):
    """A last-release restore must serialize with a new spawn's apply on the
    same path — the queued medium-IL reset cannot land after the fresh
    low-IL apply and leave the live task unable to write."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)

    async def run_once(spec):
        spawned = await runner.spawn(spec)
        out = await asyncio.wait_for(spawned.process.stdout.read(), timeout=45)
        await asyncio.wait_for(spawned.wait(), timeout=15)
        return out.decode("utf-8", "replace")

    async def go():
        await run_once(_spec(ws, "echo seed > a.txt & echo A_OK"))
        # Spawn B immediately — A's release restore may still be in flight;
        # B's apply must still win the ordering either way.
        out_b = await run_once(_spec(ws, "echo live > b.txt 2>nul && echo B_OK || echo B_DENIED"))
        for _ in range(600):
            if not runner._release_tasks:
                break
            await asyncio.sleep(0.05)
        return out_b

    out_b = asyncio.run(go())
    assert "B_OK" in out_b and "B_DENIED" not in out_b
    assert (ws / "b.txt").read_text().strip().startswith("live")


def test_spawn_grants_combines_remembered_and_one_shot(tmp_path):
    """Manager.spawn_grants: remembered capability rules ∪ one-shot
    capability approvals — the spawn-time capability negotiation."""
    from termx.agent.manager import AgentManager
    from termx.agent.secrets import CredentialStore
    from termx.agent.store import AgentStore

    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    manager = AgentManager(store, CredentialStore(memory={}), desktop=None)
    task = store.create_task(
        prompt="t", cwd=str(tmp_path), provider_id="p", model="m", limits={}
    )
    assert manager.spawn_grants(task["id"], "c1", "agent") == frozenset()

    manager._one_shot_capability_grants[(task["id"], "c1")] = {"external.submit"}
    assert "external.submit" in manager.spawn_grants(task["id"], "c1", "agent")
    assert manager.spawn_grants(task["id"], "c1", "agent") == frozenset()
    manager._one_shot_capability_grants.clear()

    project = manager._files_service().register(str(tmp_path))
    store.create_policy_rule(
        effect="allow",
        scope_type="project",
        scope_id=str(project["id"]),
        action_type="capability",
        tool="*",
        fingerprint="",
        fingerprint_kind="exact",
        matcher={},
        # external.submit is in this backend's grantable set — remembered
        # capability rules only count where the backend can provide them.
        capabilities=["external.submit"],
        sandbox_profile="agent",
        source_approval_id=None,
        task_id=None,
        project_id=str(project["id"]),
        display="ext",
    )
    grants = manager.spawn_grants(task["id"], "c2", "agent")
    assert "external.submit" in grants
    store.close()
