"""Linux kernel sandbox backend — bubblewrap namespaces + rlimits.

Mechanism (all kernel-enforced, no regex boundary):

* user namespace mapping the spawned process to uid/gid 65534
* mount namespace showing only: /usr (ro), usr/lib/bin/sbin/lib64
  symlinks, a minimal /etc, /proc, a private /tmp, a private HOME, and
  the approved roots (workspace rw, writable_roots rw, read_only_roots ro)
* PID namespace — sandboxed processes cannot see or signal host processes;
  killing the namespace init reaps the whole tree
* IPC/UTS/cgroup namespaces
* network namespace unless the spawn carries a granted net.outbound
  capability (``--share-net``); loopback-only is not offered — spec
  ``network="localhost"`` is rejected rather than silently widened
* ``--die-with-parent`` — a Termx crash reaps the sandbox instead of
  orphaning its processes
* ``setpriv --no-new-privs``-equivalent: bwrap always applies
  NO_NEW_PRIVS; setuid/file-cap binaries cannot raise privilege inside
* ``prlimit`` rlimits (pids, address space, fds, cpu) per spawned tree

The backend refuses ``network="localhost"`` (not enforceable without an
elevated netns helper — advertised grantable set stays honest) and never
falls back to unsandboxed execution: a spawn failure raises
``SandboxFailure``.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from termx.sandbox.environment import build_environment
from termx.sandbox.models import (
    ResourceLimits,
    SandboxCapabilities,
    SandboxFailure,
    SpawnSpec,
)
from termx.sandbox.runner import StreamedProcess

_BWRAP = shutil.which("bwrap")
_PRLIMIT = shutil.which("prlimit")
_available: bool | None = None

_SANDBOX_UID = "65534"
_SANDBOX_GID = "65534"

# Kernel-level caps the backend advertises. `net.outbound` is *grantable* —
# remembered capability rules (or one-shot approvals) translate to
# --share-net at spawn time. Everything else stays absent: no docker socket,
# no host credentials, no privileged-escalation surface inside the ns.
_GRANTED = frozenset(
    {"process.execute:any", "process.children:any", "fs.workspace:any"}
)
_GRANTABLE = frozenset(
    {"net.outbound:any", "git.publish", "package.publish", "external.submit"}
)

# Minimal /etc surface: name resolution + identities + CA roots (needed for
# TLS once net.outbound is granted). Guarded by existence — distros differ.
_ETC_FILES = ("resolv.conf", "hosts", "nsswitch.conf", "passwd", "group")
_ETC_DIRS = ("ssl", "pki", "ca-certificates")

# Task-less spawns get an ephemeral HOME inside the per-ns tmpfs — it dies
# with the namespace and can never expose an unrelated run's leftovers.
_EPHEMERAL_HOME = "/tmp/termx-home"

# Child env is delivered through a sourced file, never `--setenv` argv —
# argv is world-readable via /proc/<pid>/cmdline, while the envfile is
# 0600 under the 0700 state dir and swept once consumed.
_ENVFILE_NS = "/tmp/termx-env"
_ENVFILE_MAX_AGE_S = 120
# How often the background sweeper runs (files expire by age, not by a
# per-spawn timer — a timer can't tell a mounted bind from a stalled one).
_ENVFILE_SWEEP_S = 30
# Env keys that are code-execution vectors when set, never data: an inner
# shell honoring them would run a script it can read inside the sandbox.
_ENV_DENYLIST = frozenset({"ENV", "BASH_ENV", "SHELLOPTS", "PS4", "CDPATH"})

# One daemon sweeper per env directory per process — runner instances are
# created per launch (runner_for), so a per-runner thread would leak.
_sweepers: set[str] = set()
_sweepers_lock = threading.Lock()


def _ensure_sweeper(envdir: Path) -> None:
    """Recurring daemon sweep of expired env files for this directory.

    A per-spawn timer can't know when bwrap's mount phase finished, so
    files expire only by age: bwrap's mount phase is <1s in practice, and
    a launcher stalled past the ~2min expiry window fails visibly rather
    than silently losing env. One thread per directory across all runner
    instances; daemon, so it exits with the process.
    """
    key = str(envdir)
    with _sweepers_lock:
        if key in _sweepers:
            return
        _sweepers.add(key)

        def loop() -> None:
            while True:
                time.sleep(_ENVFILE_SWEEP_S)
                LinuxNamespaceRunner._sweep_env_files(envdir)

        threading.Thread(target=loop, daemon=True, name="termx-env-sweep").start()


async def _unlink_when_mounted(pid: int, envfile: Path) -> None:
    """Delete the env file once its bind is confirmed in the child's ns.

    /proc/<pid>/mountinfo exposing the env path proves bwrap's mount
    phase finished — the host copy is then dead weight (credentials
    linger ~1s instead of the full age window). If the child dies
    first, the file is useless; if the mount never lands within the
    cap, the age sweep still owns cleanup.
    """
    mountinfo = Path(f"/proc/{pid}/mountinfo")
    for _ in range(90):
        try:
            text = mountinfo.read_text(encoding="utf-8", errors="replace")
        except (OSError, FileNotFoundError):
            break  # process gone — file is dead either way
        if _ENVFILE_NS in text:
            break
        await asyncio.sleep(0.5)
    else:
        return
    try:
        envfile.unlink()
    except OSError:
        pass


# prlimit defaults for every restricted spawn (per-tree bounds; pids counts
# the real uid so it is kept generous enough to avoid false failures).
_DEFAULT_LIMITS = ResourceLimits(pids=1024, memory_bytes=8 << 30)


def linux_ns_available() -> bool:
    """True when bwrap + unprivileged user namespaces work on this host."""
    global _available
    if _available is not None:
        return _available
    if sys.platform != "linux" or _BWRAP is None:
        _available = False
        return _available
    try:
        probe = subprocess.run(
            [_BWRAP, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", "true"],
            capture_output=True,
            timeout=10,
        )
        _available = probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _available = False
    return _available


def _net_granted(spec: SpawnSpec) -> bool:
    if spec.network != "outbound":
        return False
    return any(c.startswith("net.outbound") for c in spec.granted_capabilities)


class LinuxNamespaceRunner:
    """SandboxRunner enforcing restricted execution with kernel namespaces."""

    def __init__(self, *, profile: str, state_dir: "str | Path | None" = None) -> None:
        if profile not in ("workspace", "agent"):
            raise SandboxFailure(
                "invalid_profile", "linux-ns backend only serves restricted profiles"
            )
        if not linux_ns_available():
            raise SandboxFailure(
                "backend_unavailable",
                "linux-ns requires bwrap and unprivileged user namespaces",
            )
        self._profile = profile
        base = Path(state_dir) if state_dir is not None else _default_state_dir()
        self._state_dir = base / "sandbox"

    @property
    def profile(self) -> str:
        return self._profile

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            backend="linux-ns",
            profile=self._profile,
            strength="kernel",
            granted=_GRANTED,
            grantable=_GRANTABLE,
            network_control=True,
            filesystem_isolation=True,
            identity_isolation=True,
            resource_limits=True,
            process_tree_kill=True,
        )

    async def spawn(self, spec: SpawnSpec) -> StreamedProcess:
        spec.validate()
        if spec.profile != self._profile:
            raise SandboxFailure(
                "invalid_profile",
                f"runner for profile {self._profile!r} cannot spawn {spec.profile!r}",
            )
        if spec.network == "localhost":
            raise SandboxFailure(
                "unsupported_network",
                "loopback-only networking is not enforceable by linux-ns",
            )
        home = self._sandbox_home(spec)
        if home is not None:
            home.mkdir(parents=True, exist_ok=True)
        env = dict(spec.env) if spec.env else build_environment(
            spec.profile, home=str(home) if home else _EPHEMERAL_HOME, tmp_dir="/tmp"
        )
        # HOME/TMPDIR are part of the mount contract — a custom env must
        # still point at the mounted private dirs.
        env["HOME"] = str(home) if home else _EPHEMERAL_HOME
        env["TMPDIR"] = "/tmp"
        envfile = self._env_file(env)
        argv = self._argv(spec, env, home, envfile)
        try:
            # env={}: the bwrap launcher itself must not inherit host secrets —
            # only the inner child (inside the ns, via --setenv) needs env.
            process = await asyncio.create_subprocess_exec(
                *argv,
                env={},
                stdin=spec.stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxFailure("spawn_failed", f"linux-ns spawn failed: {exc}") from exc
        # Drop the credential-bearing envfile as soon as the bind is
        # confirmed inside the child's mount ns (the age sweep stays as
        # the bound for stalled or argv-embedded launches).
        asyncio.get_running_loop().create_task(
            _unlink_when_mounted(process.pid, envfile)
        )
        return StreamedProcess(process, spec, backend="linux-ns")

    def _env_file(self, env: dict[str, str]) -> Path:
        envdir = self._state_dir / "env"
        envdir.mkdir(parents=True, exist_ok=True)
        envdir.chmod(0o700)
        self._sweep_env_files(envdir)
        path = envdir / f"{uuid.uuid4().hex}.env"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for key, value in env.items():
                if key in _ENV_DENYLIST:
                    continue
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                    continue
                fh.write(f"{key}={shlex.quote(value)}\n")
        _ensure_sweeper(envdir)
        return path

    @staticmethod
    def _sweep_env_files(envdir: Path) -> None:
        # By the time a file is old its child has sourced it (the preamble
        # runs at exec); stale leftovers would only linger on a crashed
        # spawn, so age-sweeping loses nothing live.
        cutoff = time.time() - _ENVFILE_MAX_AGE_S
        try:
            for entry in envdir.iterdir():
                try:
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        entry.unlink()
                except OSError:
                    continue
        except OSError:
            pass

    # -- internals ------------------------------------------------------

    def _sandbox_home(self, spec: SpawnSpec) -> Path | None:
        """Persistent private HOME path, or None for an ephemeral one.

        Task-scoped (or caller-pinned) homes persist on disk; task-less
        spawns get a fresh dir inside the per-ns tmpfs so files from an
        unrelated earlier run can never leak into this one.
        """
        if spec.home:
            return Path(spec.home)
        if spec.task_id:
            return self._state_dir / "home" / _safe_name(spec.task_id)
        return None

    def _argv(
        self, spec: SpawnSpec, env: dict[str, str], home: Path | None, envfile: Path
    ) -> list[str]:
        argv = [_BWRAP or "bwrap"]
        argv += [
            "--unshare-user",
            "--uid",
            _SANDBOX_UID,
            "--gid",
            _SANDBOX_GID,
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-cgroup",
            "--die-with-parent",
            "--new-session",
        ]
        argv += ["--share-net"] if _net_granted(spec) else ["--unshare-net"]
        # Root filesystem: toolchain read-only, no host HOME, minimal /etc.
        argv += ["--ro-bind", "/usr", "/usr"]
        for link, target in (
            ("bin", "usr/bin"),
            ("sbin", "usr/sbin"),
            ("lib", "usr/lib"),
            ("lib64", "usr/lib64"),
        ):
            argv += ["--symlink", target, f"/{link}"]
        argv += ["--dir", "/etc"]
        for name in _ETC_FILES:
            src = Path("/etc") / name
            if src.is_file():
                argv += ["--ro-bind", str(src), f"/etc/{name}"]
        for name in _ETC_DIRS:
            src = Path("/etc") / name
            if src.is_dir():
                argv += ["--ro-bind", str(src), f"/etc/{name}"]
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        seen: set[str] = set()
        if home is not None:
            argv += ["--bind", str(home), str(home)]
            seen.add(str(home))
        else:
            # Ephemeral HOME inside the per-ns tmpfs — nothing persists to a
            # shared dir, so an unrelated earlier run's files can't leak in.
            # --chmod makes it writable by the in-ns uid. 0777 on purpose:
            # bwrap may create --dir during mount setup as ns-root, and on
            # those versions 0700 would deny the child entirely — this is a
            # per-ns tmpfs seen only by this task's own uid, so the wide
            # mode costs nothing.
            argv += ["--dir", _EPHEMERAL_HOME, "--chmod", "0777", _EPHEMERAL_HOME]
            seen.add(_EPHEMERAL_HOME)
        argv += ["--ro-bind", str(envfile), _ENVFILE_NS]
        seen.add(_ENVFILE_NS)
        workspace = str(Path(spec.workspace_root).resolve())
        argv += ["--bind", workspace, workspace]
        seen.add(workspace)
        # Linked-git-worktree metadata: the worktree's `.git` file points at
        # <base>/.git/worktrees/<name>. The shared commondir mounts rw
        # (packed-refs + lock files need it) with ro overlays for config,
        # hooks, other worktrees' dirs, and this gitdir's pointer files —
        # the sandbox cannot retarget hooks or rewrite repo config (which
        # would run host-side outside the sandbox).
        for git_path, mode in _git_mounts(Path(workspace)):
            resolved = str(git_path)
            if resolved not in seen:
                seen.add(resolved)
                argv += [mode, resolved, resolved]
        for root in spec.writable_roots:
            resolved = str(Path(root).resolve())
            if resolved not in seen:
                seen.add(resolved)
                argv += ["--bind", resolved, resolved]
        for root in spec.read_only_roots:
            resolved = str(Path(root).resolve())
            if resolved not in seen:
                seen.add(resolved)
                argv += ["--ro-bind", resolved, resolved]
        argv += ["--chdir", spec.cwd or workspace]
        argv.append("--clearenv")
        argv.append("--")
        cmd = list(spec.argv) if spec.argv is not None else ["/bin/sh", "-c", spec.shell or ""]
        # Env arrives via the ro-bound file: the preamble sources it then
        # execs, so values never appear in argv (world-readable cmdline).
        # (An in-ns umount here is dead code — bwrap drops CAP_SYS_ADMIN.)
        cmd = [
            "/bin/sh",
            "-c",
            f'set -a; . "{_ENVFILE_NS}"; set +a; exec "$@"',
            "sh",
            *cmd,
        ]
        limits = spec.limits if spec.limits != ResourceLimits() else _DEFAULT_LIMITS
        argv += self._limit_argv(limits, spec) + cmd
        return argv

    @staticmethod
    def _limit_argv(limits: ResourceLimits, spec: SpawnSpec) -> list[str]:
        if _PRLIMIT is None:
            return []
        opts: list[str] = []
        if limits.pids:
            opts.append(f"--nproc={limits.pids}")
        if limits.memory_bytes:
            opts.append(f"--as={limits.memory_bytes}")
        # fd bound defaults modestly; wall/cpu time still enforced by callers.
        opts.append("--nofile=1024")
        cpu_s = limits.cpu_s or (math.ceil(spec.limits.wall_s) if spec.limits.wall_s else None)
        if cpu_s:
            opts.append(f"--cpu={int(cpu_s)}")
        return [_PRLIMIT, *opts, "--"] if opts else []


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:80] or "agent"


def _git_mounts(workspace: Path) -> list[tuple[Path, str]]:
    """Ordered (path, bwrap bind flag) ops for a linked worktree's git metadata.

    A worktree's `.git` is a text file (`gitdir: <abs>`) pointing into the
    base repository's `.git/worktrees/<name>`; that dir's `commondir` in
    turn points at the shared object store. The commondir mounts rw —
    ref maintenance (fetch --prune on packed-refs, lock files) needs the
    directory writable — with ro overlays for what must never change:
    `config` and `hooks` (a retargeted hook executes host-side, outside
    the sandbox), `worktrees/` (other tasks' metadata), and inside the
    rw gitdir the `config.worktree`/`commondir`/`gitdir` pointer files.
    """
    dotgit = workspace / ".git"
    empty: list[tuple[Path, str]] = []
    if not dotgit.is_file():
        return empty
    try:
        text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        target = next(
            (ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("gitdir:")),
            "",
        )
        if not target:
            return empty
        gitdir = Path(target)
        if not gitdir.is_absolute():
            gitdir = workspace / gitdir
        gitdir = gitdir.resolve(strict=False)
        if not gitdir.is_dir():
            return empty
        commondir_file = gitdir / "commondir"
        if not commondir_file.is_file():
            # Standalone gitdir (e.g. submodule-less main worktree): the
            # repo IS the checkout — no shared state to protect.
            return [(gitdir, "--bind")]
        common = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
        if not common:
            return empty
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = gitdir / common_path
        common_path = common_path.resolve(strict=False)
        if not common_path.is_dir():
            return empty
        # Mount order matters — later binds shadow earlier ones:
        #   1. common rw base   (refs/objects/packed-refs + lock files)
        #   2. ro overlays      (config, hooks, other worktrees' dirs)
        #   3. gitdir rw        (this worktree's index/HEAD/FETCH_HEAD)
        #   4. ro pointer files (config.worktree, commondir, gitdir)
        ops: list[tuple[Path, str]] = [(common_path, "--bind")]
        for sub in ("config", "hooks", "worktrees"):
            child = common_path / sub
            if child.exists():
                ops.append((child, "--ro-bind"))
        ops.append((gitdir, "--bind"))
        for pointer in ("config.worktree", "commondir", "gitdir"):
            child = gitdir / pointer
            if child.exists():
                ops.append((child, "--ro-bind"))
        return ops
    except OSError:
        return empty


def _default_state_dir() -> Path:
    from termx.config import config_dir

    return config_dir()


__all__ = ["LinuxNamespaceRunner", "linux_ns_available"]
