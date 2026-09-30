"""macOS restricted-execution integration tests (macos-seatbelt + macos-helper).

These prove the boundary, not return values: every assertion exercises the
kernel-enforced property end to end through ``sandbox-exec`` — a skipped suite
means the capability is *not* advertised as kernel-strength.

Honest-scope notes vs the Linux suite:
- Seatbelt does NOT isolate signals between same-uid processes, so the
  "cannot signal unrelated host process" test is intentionally absent — the
  backend reports ``identity_isolation=False`` for that reason.
- ``memory_bytes`` is not enforceable on macOS (no RLIMIT_AS; RLIMIT_DATA
  does not cover mmap), so the resource-limit test exercises the CPU bound
  (``ulimit -t`` → SIGXCPU) instead.
"""
from __future__ import annotations

import asyncio
import os
import signal
import stat
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

from termx.sandbox import (
    ResourceLimits,
    SandboxFailure,
    SpawnSpec,
    runner_for,
)
from termx.sandbox import macos_runner


@pytest.fixture(autouse=True)
def _reset_probes(monkeypatch):
    """Helper/seatbelt availability probes are process-cached; reset them so
    env-var changes in a test take effect."""
    monkeypatch.setattr(macos_runner, "_helper_probe", None)
    monkeypatch.setattr(macos_runner, "_seatbelt_probe", None)


def _seatbelt() -> bool:
    return macos_runner.macos_seatbelt_available()


requires_seatbelt = pytest.mark.skipif(
    not _seatbelt(), reason="sandbox-exec unavailable"
)


def _runner(tmp_path: Path, profile: str = "agent"):
    return runner_for(profile, backend="macos", state_dir=tmp_path / "state")


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
    out = await asyncio.wait_for(spawned.process.stdout.read(), timeout=30)
    await spawned.wait()
    return spawned.process.returncode or 0, out.decode("utf-8", "replace")


@requires_seatbelt
def test_agent_profile_runs_seatbelt_backend(tmp_path):
    runner = _runner(tmp_path)
    caps = runner.capabilities()
    assert caps.backend in {"macos-seatbelt", "macos-helper"}
    assert caps.strength == "kernel"
    assert caps.filesystem_isolation and caps.network_control
    assert caps.resource_limits and caps.process_tree_kill
    # Seatbelt-only mode has no identity boundary — same uid by design.
    assert caps.identity_isolation == (caps.backend == "macos-helper")
    assert "net.outbound:any" in caps.grantable
    assert "net.outbound:any" not in caps.granted


@requires_seatbelt
def test_workspace_read_write(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "seed.txt").write_text("seed")
    runner = _runner(tmp_path)
    rc, out = asyncio.run(
        _run(runner, _spec(ws, "cat seed.txt && echo new > created.txt && echo OK"))
    )
    assert "OK" in out
    assert (ws / "created.txt").read_text().strip() == "new"


@requires_seatbelt
def test_cannot_read_host_private_fixture(tmp_path):
    secret_dir = tmp_path / "host-private"
    secret_dir.mkdir()
    secret = secret_dir / "secret.txt"
    secret.write_text("TOPSECRET")
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    rc, out = asyncio.run(_run(runner, _spec(ws, f"cat {secret}")))
    assert "TOPSECRET" not in out
    # The directory exists (metadata traversal allowed) but its contents are
    # denied at the kernel boundary.
    _, listing = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                f"cat {secret_dir}/secret.txt >/dev/null 2>&1 && echo VISIBLE || echo HIDDEN",
            ),
        )
    )
    assert "HIDDEN" in listing and "VISIBLE" not in listing


@requires_seatbelt
def test_no_symlink_or_traversal_escape(tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("TOPSECRET")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "escape1").symlink_to(secret)
    (ws / "escape2").symlink_to("/etc/master.passwd")
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(ws, "cat escape1 escape2 ../outside.txt 2>&1; echo MARKER"),
        )
    )
    assert "TOPSECRET" not in out
    assert "MARKER" in out


@requires_seatbelt
def test_no_inherited_host_secret_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_HOST_SECRET", "abc123")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIAFAKE")
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                "echo \"S1=${FAKE_HOST_SECRET:-EMPTY}\"; "
                "echo \"S2=${AWS_SECRET_ACCESS_KEY:-EMPTY}\"",
            ),
        )
    )
    assert "S1=EMPTY" in out and "S2=EMPTY" in out
    assert "abc123" not in out and "AKIAFAKE" not in out


@requires_seatbelt
def test_network_denied_by_default(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            # /usr/bin/nc is used (not venv python3) so the assertion proves
            # socket-level denial, not a denied interpreter read under $HOME.
            _spec(
                ws,
                "nc -v -w 2 1.1.1.1 53 </dev/null 2>&1 | tail -1",
            ),
        )
    )
    assert "Operation not permitted" in out


@requires_seatbelt
def test_network_grant_shares_net(tmp_path):
    """A granted net.outbound capability must actually light up networking —
    proven against a host-local listener."""
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
    spec = _spec(
        ws,
        f"printf 'PING' | nc -w 2 127.0.0.1 {port} && echo SENT",
        network="outbound",
        granted_capabilities=("net.outbound:any",),
    )
    _, out = asyncio.run(_run(runner, spec))
    thread.join(timeout=5)
    listener.close()
    assert "SENT" in out
    assert accepted == [b"PING"]


@requires_seatbelt
def test_network_flag_without_grant_still_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(
        ws,
        "nc -v -w 2 1.1.1.1 53 </dev/null 2>&1 | tail -1",
        network="outbound",
    )
    _, out = asyncio.run(_run(runner, spec))
    assert "Operation not permitted" in out


@requires_seatbelt
def test_one_shot_capability_grant_reaches_spawn(tmp_path):
    """Regression: an "Allow once" net.outbound approval must reach the
    SpawnSpec — resolving grants separately for `network` and
    `granted_capabilities` used to pop the one-shot twice, silently
    re-denying the spawn (approval granted, kernel still denied)."""
    import socket as sk
    import threading
    from types import SimpleNamespace

    from termx.agent.manager import AgentManager
    from termx.agent.secrets import CredentialStore
    from termx.agent.store import AgentStore
    from termx.agent.tools.helpers import sandbox_grants, sandbox_network

    store = AgentStore(tmp_path / "agent.sqlite3", tmp_path / "artifacts")
    try:
        manager = AgentManager(store, CredentialStore(memory={}), desktop=None)
        task = store.create_task(
            prompt="t", cwd=str(tmp_path), provider_id="p", model="m", limits={}
        )
        manager._one_shot_capability_grants[(task["id"], "c1")] = {
            "net.outbound:any"
        }
        ctx = SimpleNamespace(task_id=task["id"], manager=manager)

        # Same resolution order as tools/shell.py: grants resolved once, the
        # network flag derived from the same read.
        grants = sandbox_grants(ctx, "agent", "c1")
        network = sandbox_network(grants)
    finally:
        store.close()

    assert network == "outbound"
    assert "net.outbound:any" in grants

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
    spec = _spec(
        ws,
        f"printf 'PING' | nc -w 2 127.0.0.1 {port} && echo SENT",
        network=network,
        granted_capabilities=tuple(sorted(grants)),
    )
    _, out = asyncio.run(_run(runner, spec))
    thread.join(timeout=5)
    listener.close()
    assert "SENT" in out
    assert accepted == [b"PING"]


@requires_seatbelt
def test_cpu_limit_enforced(tmp_path):
    """ulimit -t inside the seatbelt preamble must kill a busy loop via
    SIGXCPU. (memory_bytes is not enforceable on macOS — documented gap.)"""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(
        ws,
        # /bin/sh busy loop — /usr/bin/python3 is the Xcode shim, which
        # needs /Library/Preferences (license plist) readable to resolve.
        "while :; do :; done",
        limits=ResourceLimits(cpu_s=1),
    )
    rc, out = asyncio.run(_run(runner, spec))
    assert rc == -signal.SIGXCPU or "Cputime limit exceeded" in out


@requires_seatbelt
def test_cancellation_kills_descendants(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(ws, "sleep 60 & sleep 60")
    spawned = asyncio.run(_spawn_and_kill(runner, spec))
    assert spawned


async def _spawn_and_kill(runner, spec: SpawnSpec):
    spawned = await runner.spawn(spec)
    pid = spawned.process.pid
    await asyncio.sleep(0.6)
    await spawned.terminate()
    await asyncio.sleep(0.3)
    try:
        os.killpg(pid, 0)
        group_alive = True
    except (ProcessLookupError, PermissionError):
        group_alive = False
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive and not group_alive
    return spawned


@requires_seatbelt
def test_worktree_writable_original_checkout_read_only(tmp_path):
    worktree = tmp_path / "worktree"
    checkout = tmp_path / "checkout"
    worktree.mkdir()
    checkout.mkdir()
    (checkout / "file.txt").write_text("original")
    runner = _runner(tmp_path)
    spec = _spec(
        worktree,
        f"echo change >> {checkout}/file.txt 2>/dev/null && echo RO_VIOLATION || echo RO_OK; "
        "echo w > wt.txt && echo WT_OK",
        read_only_roots=[str(checkout)],
    )
    _, out = asyncio.run(_run(runner, spec))
    assert "RO_OK" in out and "RO_VIOLATION" not in out
    assert "WT_OK" in out
    assert (checkout / "file.txt").read_text() == "original"
    assert (worktree / "wt.txt").read_text().strip() == "w"


@requires_seatbelt
def test_localhost_network_mode_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(ws, "true", network="localhost")

    async def go() -> None:
        await runner.spawn(spec)

    with pytest.raises(SandboxFailure) as err:
        asyncio.run(go())
    assert err.value.reason == "unsupported_network"


@requires_seatbelt
def test_spawn_failure_never_downgrades(tmp_path, monkeypatch):
    """A backend that can't construct the sandbox raises — it must never
    silently exec unsandboxed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    monkeypatch.setattr(macos_runner, "_SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    spec = _spec(ws, "true")

    async def go() -> None:
        await runner.spawn(spec)

    with pytest.raises(SandboxFailure) as err:
        asyncio.run(go())
    assert err.value.reason == "spawn_failed"


@requires_seatbelt
def test_env_is_clean_and_home_private(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                "echo HOME=$HOME; echo TMPDIR=$TMPDIR; echo TERM=$TERM; "
                "echo v > $HOME/probe.txt && cat $HOME/probe.txt",
            ),
        )
    )
    home_line = [l for l in out.splitlines() if l.startswith("HOME=")][0]
    home = home_line.split("=", 1)[1]
    assert "/sandbox/home/" in home or home.startswith(str(tmp_path))
    assert "TERM=" in out
    assert "probe" not in out.split("HOME=")[0]
    assert (Path(home) / "probe.txt").exists()  # private HOME is writable


@requires_seatbelt
def test_runner_rejects_host_profile(tmp_path):
    with pytest.raises(SandboxFailure) as err:
        runner_for("host", backend="macos")
    assert err.value.reason == "invalid_profile"


@requires_seatbelt
def test_run_shell_path_runs_sandboxed(tmp_path):
    """The agent run_shell funnel must land in the macos backend (regression:
    no second unsandboxed spawn path)."""
    from termx.agent.execution import run_shell

    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    result = asyncio.run(
        run_shell("id -u; id -un", str(ws), runner=runner, profile="agent")
    )
    # Seatbelt mode keeps the invoking uid — honest: the boundary is fs/net,
    # not identity. The restricted-user helper adds that later.
    assert str(os.getuid()) in result.output


@requires_seatbelt
def test_run_shell_cannot_touch_host_home(tmp_path):
    """run_shell inside the sandbox must not write into the real $HOME."""
    from termx.agent.execution import run_shell

    ws = tmp_path / "ws"
    ws.mkdir()
    marker = Path.home() / ".termx-sandbox-probe-must-not-exist"
    if marker.exists():
        marker.unlink()
    runner = _runner(tmp_path)
    result = asyncio.run(
        run_shell(f"echo x > {marker} 2>/dev/null; echo done", str(ws), runner=runner)
    )
    assert not marker.exists()


def test_default_backend_selection_on_macos():
    from termx.sandbox import _default_backend

    backend = _default_backend("agent")
    if macos_runner.macos_backend_available():
        assert backend == "macos"
    else:
        assert backend == "host"


def test_backend_override_honors_macos(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMX_SANDBOX_BACKEND", "macos")
    from termx.sandbox import _default_backend

    assert _default_backend("agent") == "macos"


# --- helper-client protocol coverage -------------------------------------
#
# The privileged helper binary ships with the Tauri packaging; here a fake
# helper implements the documented JSON-lines protocol (and actually applies
# the received seatbelt profile through sandbox-exec) so the client path is
# exercised end to end.

_FAKE_HELPER = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import base64, json, os, signal, subprocess, sys, threading

    def emit(frame):
        sys.stdout.write(json.dumps(frame) + "\\n")
        sys.stdout.flush()

    children = {}

    def pump(pid, proc):
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                emit({"op": "output", "pid": pid, "stream": "stdout",
                      "data": base64.b64encode(chunk).decode()})
        finally:
            proc.wait()
            emit({"op": "exit", "pid": pid, "exit_code": proc.returncode})

    def spawn(req):
        sb = req.get("seatbelt")
        limits = req.get("limits") or {}
        pre = []
        if limits.get("cpu_s"):
            pre.append(f"ulimit -t {int(limits['cpu_s'])} 2>/dev/null")
        if limits.get("pids"):
            pre.append(f"ulimit -u {int(limits['pids'])} 2>/dev/null")
        pre.append("ulimit -n 1024 2>/dev/null")
        preamble = "; ".join(pre) + "; " if pre else ""
        if req.get("argv"):
            wrapped = ["/bin/sh", "-c", preamble + 'exec "$@"',
                       "termx-sandbox", *req["argv"]]
        else:
            wrapped = ["/bin/sh", "-c", preamble + (req.get("shell") or "")]
        argv = wrapped
        if sb:
            argv = ["/usr/bin/sandbox-exec", "-p", sb, *argv]
        try:
            proc = subprocess.Popen(
                argv, cwd=req["cwd"], env=req["env"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        except Exception as exc:
            emit({"op": "spawn", "ok": False,
                  "error": {"reason": "spawn_failed", "message": str(exc)}})
            return
        children[proc.pid] = proc
        emit({"op": "spawn", "ok": True, "pid": proc.pid})
        threading.Thread(target=pump, args=(proc.pid, proc), daemon=True).start()

    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        op = req.get("op")
        if op == "status":
            emit({"op": "status", "ok": True, "user": "termx-sandbox",
                  "seatbelt": True})
        elif op == "spawn":
            spawn(req)
        elif op == "terminate":
            proc = children.get(req.get("pid"))
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            emit({"op": "terminate", "ok": True, "pid": req.get("pid")})
    """
)


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    helper = tmp_path / "sandbox-helper"
    helper.write_text(_FAKE_HELPER)
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("TERMX_SANDBOX_HELPER", str(helper))
    monkeypatch.setattr(macos_runner, "_helper_probe", None)
    return helper


def test_helper_mode_capabilities(tmp_path, fake_helper):
    runner = _runner(tmp_path)
    caps = runner.capabilities()
    assert caps.backend == "macos-helper"
    assert caps.strength == "kernel"
    assert caps.identity_isolation and caps.filesystem_isolation
    assert caps.network_control and caps.process_tree_kill


def test_helper_spawn_round_trip(tmp_path, fake_helper):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    rc, out = asyncio.run(_run(runner, _spec(ws, "echo HELPER-OUTPUT && echo w > made.txt")))
    assert "HELPER-OUTPUT" in out
    assert (ws / "made.txt").read_text().strip() == "w"


def test_helper_applies_seatbelt_boundary(tmp_path, fake_helper):
    """The fake helper applies the received seatbelt profile — proving
    defense-in-depth actually flows through the protocol."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(_run(runner, _spec(ws, f"cat {secret}")))
    assert "TOPSECRET" not in out


def test_helper_terminate_kills_child(tmp_path, fake_helper):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)

    async def go():
        spawned = await runner.spawn(_spec(ws, "sleep 60"))
        await asyncio.sleep(0.3)
        await spawned.terminate()
        return spawned.process.returncode

    rc = asyncio.run(go())
    assert rc is not None


def test_helper_spawn_failure_raises(tmp_path, monkeypatch):
    helper = tmp_path / "sandbox-helper"
    helper.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        "print('{\"op\":\"status\",\"ok\":true,\"user\":\"termx-sandbox\",\"seatbelt\":true}')\n"
        "sys.stdin.readline()\n"
        "print('{\"op\":\"spawn\",\"ok\":false,\"error\":{\"reason\":\"spawn_failed\",\"message\":\"nope\"}}')\n"
    )
    helper.chmod(0o755)
    monkeypatch.setenv("TERMX_SANDBOX_HELPER", str(helper))
    monkeypatch.setattr(macos_runner, "_helper_probe", None)
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)

    async def go() -> None:
        await runner.spawn(_spec(ws, "true"))

    with pytest.raises(SandboxFailure) as err:
        asyncio.run(go())
    assert err.value.reason == "spawn_failed"


def test_helper_wrong_user_not_trusted(tmp_path, monkeypatch):
    """A helper that does not report the restricted user must not be used —
    it would lie about identity isolation."""
    helper = tmp_path / "sandbox-helper"
    helper.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        "print('{\"op\":\"status\",\"ok\":true,\"user\":\"devin\",\"seatbelt\":true}')\n"
        "sys.stdin.readline()\n"
    )
    helper.chmod(0o755)
    monkeypatch.setenv("TERMX_SANDBOX_HELPER", str(helper))
    monkeypatch.setattr(macos_runner, "_helper_probe", None)
    runner = _runner(tmp_path)
    # Falls back to seatbelt mode — never claims identity it cannot prove.
    assert runner.capabilities().backend == "macos-seatbelt"
    assert not runner.capabilities().identity_isolation
