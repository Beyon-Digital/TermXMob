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
import shutil
import subprocess
import sys
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
        argv = self.spawn_argv(spec)
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
        return StreamedProcess(process, spec, backend="linux-ns")

    def spawn_argv(self, spec: SpawnSpec) -> list[str]:
        """Resolved sandbox argv for embedding under an external pty/session.

        The PTY layer (terminals.py) owns the controlling terminal and spawn;
        this returns the fully wrapped bwrap+rlimits argv so the same kernel
        isolation applies to interactive workspace terminals as to agent
        spawns. Validation and env construction match ``spawn`` exactly.
        """
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
        env = spec.env or build_environment(
            spec.profile, home=str(home) if home else _EPHEMERAL_HOME, tmp_dir="/tmp"
        )
        return self._argv(spec, env, home)

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

    def _argv(self, spec: SpawnSpec, env: dict[str, str], home: Path | None) -> list[str]:
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
        ]
        # --new-session detaches the child into a fresh session — under an
        # external pty the terminal layer must keep job control in the outer
        # session (tcsetpgrp/ctrl-C), so interactive spawns skip it. The
        # namespace tree still dies with --die-with-parent and the outer
        # killpg covers cancellation.
        if not spec.pty:
            argv.append("--new-session")
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
            argv += ["--dir", _EPHEMERAL_HOME]
            seen.add(_EPHEMERAL_HOME)
        workspace = str(Path(spec.workspace_root).resolve())
        argv += ["--bind", workspace, workspace]
        seen.add(workspace)
        # Linked-git-worktree metadata: the worktree's `.git` file points at
        # <base>/.git/worktrees/<name>; git needs that dir (rw — index/refs)
        # and its commondir object store (rw — new objects). The base
        # checkout's *files* stay unmounted entirely.
        for meta in _git_metadata_roots(Path(workspace)):
            resolved = str(meta)
            if resolved not in seen:
                seen.add(resolved)
                argv += ["--bind", resolved, resolved]
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
        for key, value in env.items():
            argv += ["--setenv", key, value]
        argv.append("--")
        cmd = list(spec.argv) if spec.argv is not None else ["/bin/sh", "-c", spec.shell or ""]
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


def _git_metadata_roots(workspace: Path) -> list[Path]:
    """Git metadata a linked worktree needs beyond the workspace bind.

    A worktree's `.git` is a text file (`gitdir: <abs>`) pointing into the
    base repository's `.git/worktrees/<name>`; that dir's `commondir` in
    turn points at the shared object store. Both are bound rw — git refuses
    to operate otherwise — while the base checkout's working files stay
    unmounted entirely.
    """
    dotgit = workspace / ".git"
    if not dotgit.is_file():
        return []
    try:
        text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        target = next(
            (ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("gitdir:")),
            "",
        )
        if not target:
            return []
        gitdir = Path(target)
        if not gitdir.is_absolute():
            gitdir = workspace / gitdir
        gitdir = gitdir.resolve(strict=False)
        if not gitdir.is_dir():
            return []
        roots = [gitdir]
        commondir_file = gitdir / "commondir"
        if commondir_file.is_file():
            common = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
            if common:
                common_path = Path(common)
                if not common_path.is_absolute():
                    common_path = gitdir / common_path
                resolved = common_path.resolve(strict=False)
                if resolved.is_dir() and resolved not in roots:
                    roots.append(resolved)
        return roots
    except OSError:
        return []


def _default_state_dir() -> Path:
    from termx.config import config_dir

    return config_dir()


__all__ = ["LinuxNamespaceRunner", "linux_ns_available"]
