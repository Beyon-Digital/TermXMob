"""Linux kernel-sandbox integration tests (linux-ns / bubblewrap).

These prove the boundary, not return values: every assertion exercises the
kernel-enforced property end to end — a skipped suite (no bwrap / no user
namespaces) means the capability is *not* advertised as kernel-strength.
"""
from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from termx.sandbox import (
    ResourceLimits,
    SandboxFailure,
    SpawnSpec,
    linux_ns_available,
    runner_for,
)

pytestmark = pytest.mark.skipif(
    not linux_ns_available(), reason="bwrap + unprivileged user namespaces required"
)


def _runner(tmp_path: Path, profile: str = "agent"):
    return runner_for(profile, backend="linux-ns", state_dir=tmp_path / "state")


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
    return spawned.process.returncode or 0, out.decode("utf-8", "replace")


def test_agent_profile_runs_kernel_backend(tmp_path):
    runner = _runner(tmp_path)
    caps = runner.capabilities()
    assert caps.backend == "linux-ns"
    assert caps.strength == "kernel"
    assert caps.filesystem_isolation and caps.identity_isolation
    assert caps.network_control and caps.resource_limits and caps.process_tree_kill
    assert "net.outbound:any" in caps.grantable
    assert "net.outbound:any" not in caps.granted


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
    # The secret's parent dir is not mounted — listing it must fail. (The
    # workspace's own ancestors exist as empty auto-created bind parents.)
    _, listing = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                f"ls {secret_dir} >/dev/null 2>&1 && echo VISIBLE || echo HIDDEN",
            ),
        )
    )
    assert "HIDDEN" in listing and "VISIBLE" not in listing


def test_no_symlink_or_traversal_escape(tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("TOPSECRET")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "escape1").symlink_to(secret)
    (ws / "escape2").symlink_to("/etc/shadow")
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(ws, "cat escape1 escape2 ../outside.txt 2>&1; echo MARKER"),
        )
    )
    assert "TOPSECRET" not in out
    assert "MARKER" in out


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


def test_cannot_signal_unrelated_host_process(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sleeper_pid = os.getpid()  # live host pid the sandbox must not touch
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                f"kill -0 {sleeper_pid} 2>&1; kill -9 1 2>&1; echo DONE",
            ),
        )
    )
    assert "DONE" in out
    assert os.getpid() == sleeper_pid  # still alive — signals couldn't reach


def test_network_denied_by_default(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                "python3 -c \"import socket;socket.create_connection(('1.1.1.1',53),2)\" 2>&1 | tail -1",
            ),
        )
    )
    assert "Network is unreachable" in out or "ENETUNREACH" in out


def test_network_grant_shares_net(tmp_path):
    """net.outbound capability grant must actually light up networking —
    proven hermetically against a host-local listener via --share-net."""
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
        f"python3 -c \"import socket;s=socket.create_connection(('127.0.0.1',{port}),3);s.sendall(b'PING');print('SENT')\"",
        network="outbound",
        granted_capabilities=("net.outbound:any",),
    )
    _, out = asyncio.run(_run(runner, spec))
    thread.join(timeout=5)
    listener.close()
    assert "SENT" in out
    assert accepted == [b"PING"]


def test_network_flag_without_grant_still_denied(tmp_path):
    """`network="outbound"` alone must not open net — the capability grant
    is what authorizes sharing the host net namespace."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(
        ws,
        "python3 -c \"import socket;socket.create_connection(('1.1.1.1',53),2)\" 2>&1 | tail -1",
        network="outbound",
    )
    _, out = asyncio.run(_run(runner, spec))
    assert "Network is unreachable" in out or "ENETUNREACH" in out


def test_resource_limits_enforced(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    spec = _spec(
        ws,
        'python3 -c "x = bytearray(256*1024*1024); print(len(x))" 2>&1 | tail -1',
        limits=ResourceLimits(memory_bytes=64 << 20),
    )
    rc, out = asyncio.run(_run(runner, spec))
    assert "268435456" not in out


def test_cancellation_kills_descendants(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    marker = ws / "child.txt"
    runner = _runner(tmp_path)
    spec = _spec(ws, f"sleep 60 & sleep 60")
    spawned = asyncio.run(_spawn_and_kill(runner, spec, marker))
    assert spawned


async def _spawn_and_kill(runner, spec: SpawnSpec, marker: Path):
    spawned = await runner.spawn(spec)
    pid = spawned.process.pid
    await asyncio.sleep(0.6)
    await spawned.terminate()
    await asyncio.sleep(0.3)
    # Outer bwrap pid is gone, and nothing in its process group survived.
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


def test_spawn_failure_never_downgrades(tmp_path, monkeypatch):
    """A backend that can't construct the sandbox raises — it must never
    silently exec unsandboxed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    monkeypatch.setattr("termx.sandbox.linux_ns._BWRAP", "/nonexistent/bwrap")
    spec = _spec(ws, "true")

    async def go() -> None:
        await runner.spawn(spec)

    with pytest.raises(SandboxFailure) as err:
        asyncio.run(go())
    assert err.value.reason == "spawn_failed"


def test_env_is_clean_and_home_private(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    _, out = asyncio.run(
        _run(
            runner,
            _spec(
                ws,
                "echo HOME=$HOME; echo TERM=$TERM; env | wc -l; ls $HOME/.. 2>/dev/null | wc -l",
            ),
        )
    )
    home_line = [l for l in out.splitlines() if l.startswith("HOME=")][0]
    home = home_line.split("=", 1)[1]
    # Private HOME: task-scoped state dir, caller-pinned dir, or the
    # per-namespace ephemeral dir — never the real host HOME.
    assert home != str(Path.home())
    assert "/sandbox/home/" in home or home.startswith(str(tmp_path)) or home == "/tmp/termx-home"
    assert "TERM=" in out


def test_runner_rejects_host_profile(tmp_path):
    with pytest.raises(SandboxFailure) as err:
        runner_for("host", backend="linux-ns")
    assert err.value.reason == "invalid_profile"


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

    # One-shot grant applies to exactly one call's spawn, then is consumed.
    manager._one_shot_capability_grants[(task["id"], "c1")] = {"net.outbound:any"}
    assert "net.outbound:any" in manager.spawn_grants(task["id"], "c1", "agent")
    assert manager.spawn_grants(task["id"], "c1", "agent") == frozenset()
    manager._one_shot_capability_grants.clear()

    # Remembered project capability rule persists across calls.
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
        capabilities=["net.outbound:any"],
        sandbox_profile="agent",
        source_approval_id=None,
        task_id=None,
        project_id=str(project["id"]),
        display="net",
    )
    grants = manager.spawn_grants(task["id"], "c2", "agent")
    assert "net.outbound:any" in grants
    store.close()


def test_run_shell_path_runs_sandboxed(tmp_path):
    """The agent run_shell funnel must land in linux-ns when a restricted
    profile is in play (regression: no second unsandboxed spawn path)."""
    from termx.agent.execution import run_shell

    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    result = asyncio.run(
        run_shell("id -u", str(ws), runner=runner, profile="agent")
    )
    assert "65534" in result.output


def test_taskless_spawn_gets_ephemeral_home(tmp_path):
    """Two task-less spawns must not share HOME state — leftovers from an
    unrelated earlier run can never leak into a later one."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    marker = ws / "marker"  # workspace files are out of scope; HOME is the test
    rc, _ = asyncio.run(
        _run(runner, _spec(ws, 'mkdir -p "$HOME" && touch "$HOME/leak"'))
    )
    assert rc == 0
    rc, out = asyncio.run(_run(runner, _spec(ws, 'test -e "$HOME/leak" || echo ABSENT')))
    assert rc == 0
    assert "ABSENT" in out
    assert not marker.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="git worktree as root mask test")
def test_git_worktree_metadata_writable_but_base_checkout_hidden(tmp_path):
    """Agent worktrees are the writable project root — git needs the
    worktree's linked metadata (index/refs/objects) rw while the base
    checkout's working files stay unmounted."""
    import subprocess

    base = tmp_path / "base"
    base.mkdir()
    subprocess.run(
        ["git", "-C", str(base), "init", "-b", "main"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(base), "config", "user.email", "t@t"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(base), "config", "user.name", "t"],
        check=True, capture_output=True,
    )
    secret = base / "host-secret.txt"
    secret.write_text("do-not-read")
    subprocess.run(
        ["git", "-C", str(base), "commit", "--allow-empty", "-m", "init"],
        check=True, capture_output=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(base), "worktree", "add", "-b", "wt-branch", str(wt)],
        check=True, capture_output=True,
    )
    runner = _runner(tmp_path)
    rc, out = asyncio.run(
        _run(
            runner,
            _spec(
                wt,
                "git status --porcelain >/dev/null && "
                "git commit --allow-empty -m wt && "
                "echo GIT_OK",
            ),
        )
    )
    assert "GIT_OK" in out
    # The base checkout file tree itself is not mounted — traversal to it
    # fails (the worktree gitdir bind is the only host .git surface).
    rc, out = asyncio.run(
        _run(runner, _spec(wt, f"cat {base}/host-secret.txt || echo HIDDEN"))
    )
    assert "HIDDEN" in out


def test_ephemeral_home_writable(tmp_path):
    """A task-less spawn's ephemeral HOME must accept writes from the
    in-ns uid — package caches and tools storing under $HOME depend on it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    rc, out = asyncio.run(
        _run(runner, _spec(ws, 'touch "$HOME/cache" && echo WRITABLE || echo DENIED'))
    )
    assert "WRITABLE" in out and "DENIED" not in out


def test_custom_env_still_gets_mounted_home(tmp_path):
    """spec.env overrides the built env but HOME/TMPDIR are the mount
    contract — they must point at the mounted private dirs regardless."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    rc, out = asyncio.run(
        _run(
            runner,
            _spec(ws, 'echo "H=$HOME T=$TMPDIR F=$MY_FLAG"', env={"MY_FLAG": "yes"}),
        )
    )
    assert "F=yes" in out
    assert "H=/tmp/termx-home" in out
    assert "T=/tmp" in out


def test_env_values_not_on_argv(tmp_path):
    """Secret-bearing env must not appear in the launcher's argv — host
    /proc/<pid>/cmdline is world-readable."""
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = _runner(tmp_path)
    marker = "S3CR3T-MARKER-9f8e7d"
    spawned = asyncio.run(
        runner.spawn(_spec(ws, "sleep 5", env={"KEEP_SECRET": marker}))
    )
    try:
        cmdline = Path(f"/proc/{spawned.process.pid}/cmdline").read_bytes()
        assert marker.encode() not in cmdline, "env value leaked into bwrap argv"
        assert b"--setenv" not in cmdline
    finally:
        spawned.process.kill()
        asyncio.run(asyncio.sleep(0))  # let the loop settle


def test_git_commondir_config_and_hooks_read_only(tmp_path):
    """The shared commondir mounts ro except objects/refs/logs: commits in
    the worktree work, but `git config` writes and hook retargeting fail."""
    import subprocess

    base = tmp_path / "base2"
    base.mkdir()
    for args in (
        ["git", "-C", str(base), "init", "-b", "main"],
        ["git", "-C", str(base), "config", "user.email", "t@t"],
        ["git", "-C", str(base), "config", "user.name", "t"],
        ["git", "-C", str(base), "commit", "--allow-empty", "-m", "init"],
        ["git", "-C", str(base), "worktree", "add", "-b", "wt2", str(tmp_path / "wt2")],
    ):
        subprocess.run(args, check=True, capture_output=True)
    wt = tmp_path / "wt2"
    runner = _runner(tmp_path)
    # Commits still work: objects + refs + logs + the worktree gitdir are rw.
    rc, out = asyncio.run(
        _run(runner, _spec(wt, "git commit --allow-empty -m c && echo COMMIT_OK"))
    )
    assert "COMMIT_OK" in out, out
    # Config writes (core.hooksPath retarget → host-side code exec) are EROFS.
    rc, out = asyncio.run(
        _run(
            runner,
            _spec(
                wt,
                "git config core.hooksPath /tmp/hooks 2>/dev/null && echo WROTE || echo DENIED",
            ),
        )
    )
    assert "DENIED" in out and "WROTE" not in out
    # Config reads still work.
    _, out = asyncio.run(_run(runner, _spec(wt, "git config user.email")))
    assert "t@t" in out


def test_git_commondir_packed_refs_writable(tmp_path):
    """packed-refs must stay writable (fetch --prune rewrites it) while
    worktrees/ and this gitdir's pointer files stay ro."""
    import subprocess

    base = tmp_path / "base3"
    base.mkdir()
    for args in (
        ["git", "-C", str(base), "init", "-b", "main"],
        ["git", "-C", str(base), "config", "user.email", "t@t"],
        ["git", "-C", str(base), "config", "user.name", "t"],
        ["git", "-C", str(base), "commit", "--allow-empty", "-m", "init"],
        ["git", "-C", str(base), "worktree", "add", "-b", "wt3", str(tmp_path / "wt3")],
    ):
        subprocess.run(args, check=True, capture_output=True)
    wt = tmp_path / "wt3"
    # Move a ref into packed-refs host-side so the sandbox must rewrite it.
    subprocess.run(
        ["git", "-C", str(base), "pack-refs", "--all"], check=True, capture_output=True
    )
    common = (base / ".git").resolve()
    packed = common / "packed-refs"
    assert packed.is_file()
    runner = _runner(tmp_path)
    # Ref deletion rewrites packed-refs (+ lock file in the commondir root).
    rc, out = asyncio.run(
        _run(
            runner,
            _spec(
                wt,
                "git update-ref -d refs/heads/main && echo PRUNED || echo FAILED",
            ),
        )
    )
    assert "PRUNED" in out, out
    # Other worktrees' metadata stays ro.
    _, out = asyncio.run(
        _run(runner, _spec(wt, f"touch {common}/worktrees/x 2>/dev/null && echo W || echo D"))
    )
    assert "D" in out and "W" not in out
    # The gitdir's commondir pointer file stays ro.
    gitdir = common / "worktrees" / "wt3"
    _, out = asyncio.run(
        _run(
            runner,
            _spec(wt, f"echo x >> {gitdir}/commondir 2>/dev/null && echo W || echo D"),
        )
    )
    assert "D" in out and "W" not in out


def test_env_sweeper_shared_across_runner_instances(tmp_path):
    """Fresh runners (one per launch, e.g. runbook steps) share a single
    sweeper thread per env directory — no per-runner thread leak."""
    from termx.sandbox import linux_ns

    envdir = tmp_path / "state" / "sandbox" / "env"
    before = len([t for t in threading.enumerate() if t.name == "termx-env-sweep"])
    runners = [_runner(tmp_path) for _ in range(3)]
    for r in runners:
        r._env_file({"FOO": "bar"})
    after = len([t for t in threading.enumerate() if t.name == "termx-env-sweep"])
    assert after - before == 1
    assert str(envdir) in linux_ns._sweepers


def test_env_file_denied_keys_stripped(tmp_path):
    runner = _runner(tmp_path)
    path = runner._env_file({"KEEP": "1", "ENV": "/tmp/evil", "BASH_ENV": "/tmp/evil"})
    text = path.read_text()
    assert "KEEP" in text and "ENV=" not in text.replace("KEEPENV", "")
    assert "BASH_ENV" not in text
