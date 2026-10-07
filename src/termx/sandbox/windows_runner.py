"""Windows sandbox backend — restricted token + Job Object.

Kernel-enforced isolation on an unprivileged Windows host, stdlib ctypes only:

* The runner hands each spawn to ``termx.sandbox._win_shim`` — a normal
  ``asyncio.create_subprocess_exec`` child, so the caller-facing surface stays
  a real ``asyncio.subprocess.Process`` (pipes, wait, terminate).
* The shim duplicates the Termx process token, strips its privileges
  (``CreateRestrictedToken`` + ``DISABLE_MAX_PRIVILEGE``) and pins it to
  **low integrity** (``S-1-16-4096`` via ``TokenIntegrityLevel``), then spawns
  the target suspended through ``CreateProcessWithTokenW`` (Secondary Logon
  service — needs no special privilege; ``CreateProcessAsUser`` is retained
  as the privileged fallback), assigns it to a Job Object, and resumes it.
* Filesystem boundary: approved writable roots are relabeled **low integrity**
  (``icacls <path> /setintegritylevel (OI)(CI)L /T``). Everything else stays
  at the implicit medium label, and mandatory no-write-up then denies all
  writes outside approved roots — kernel-enforced, not a path filter.
  Granting the low-IL SID in the DACL (``/grant *S-1-16-4096``) does NOT
  bypass the mandatory check — verified on Windows Server 2022 — which is why
  the label route is used. Read-only roots need no grant: default objects
  stay readable (no NO_READ_UP) but writable-denied.
* Process-tree kill: the Job Object carries
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``; terminating the shim closes its
  job handle and the kernel reaps the whole restricted tree. Job limits
  (``ActiveProcessLimit``, ``ProcessMemoryLimit``/``JobMemoryLimit``,
  ``PerJobUserTimeLimit``) map ``ResourceLimits``.
* Environment: ``build_environment(profile)`` — never ``os.environ`` —
  plus the non-secret OS vars Windows processes expect (``SystemRoot``,
  ``ComSpec``, ``PATHEXT``).

Honesty notes (also surfaced through ``capabilities()``):

* ``network_control=False``. There is no unprivileged primitive that denies
  outbound networking on Windows (WFAS rules and WFP callouts need admin /
  install-time provisioning), so ``spec.network`` is *ignored* — the child
  shares the host network regardless of grants. Advertising the control we
  cannot enforce would be a lie; the grantable set therefore omits
  ``net.outbound:*``.
* ``identity_isolation=False`` in token mode: the child runs as the same
  user at lower integrity — it cannot write medium-integrity objects or
  open higher-integrity processes for write access, but it can still read
  what the user's DACL allows. A stronger **dedicated restricted user** mode
  activates automatically when a ``termx-sandbox`` local account exists and
  its credential both exists and verifies (``LogonUser`` probe at runner
  construction; creds in ``%ProgramData%\\termx\\sandbox-user.cred``, a
  DPAPI machine-scope blob holding the generated password — e.g. written
  by an elevated ``termx sandbox provision`` step): the shim then logs that
  user on (``LogonUser`` INTERACTIVE) and applies the *same*
  restricted-token pipeline — privileges stripped, low integrity, job
  object — so the child is a low-IL ``termx-sandbox``. Write denial is then
  two independent layers (mandatory no-write-up *and* foreign-identity
  DACL), and the backend reports ``identity_isolation=True``,
  ``strength='restricted-user'``. Spawning an other-user token via
  ``CreateProcessWithTokenW`` requires the host to hold
  ``SE_IMPERSONATE_NAME`` (services / elevated hosts do; a standard-user
  host may not) — a failure raises ``spawn_failed``, never downgrades.
* ``strength='kernel'`` in token mode: the enforced boundary is kernel
  security machinery (mandatory integrity labels + job objects), not a
  wrapper script. ``'restricted-user'`` is reserved for the separate-user
  mode, matching the strength enum's identity semantics.

Residual boundaries worth stating plainly:

* The writable domain is **per-principal within a runner's lifetime**.
  Low-integrity labels and (user-mode) DACL grants are refcounted per spawn
  and restored when the last holder's shim exits — files a child created
  keep their low label until that restore walks the tree. If the runner
  itself dies between a spawn's exit and the restore (SIGKILL, power
  loss), labels/grants on that task's roots stay on disk: a later low-IL
  task could then write them without owning them — stale residue, not a
  live-boundary gap.
* The dedicated user's home/tmp live under
  ``%ProgramData%\\termx\\sandbox-state`` so ancestor traversal works
  without grants inside the host's private profile tree.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
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

_ICACLS = shutil.which("icacls")
_available: "bool | None" = None

# Same vocabulary as linux-ns where enforceable. `net.outbound:any` is
# intentionally absent from grantable: nothing here can enforce its denial.
_GRANTED = frozenset(
    {"process.execute:any", "process.children:any", "fs.workspace:any"}
)
_GRANTABLE = frozenset({"git.publish", "package.publish", "external.submit"})

_DEFAULT_LIMITS = ResourceLimits(pids=256)

# OS vars a Windows process needs to be functional; none are secrets. They
# ride on top of the allowlisted build_environment output.
_OS_ENV = ("SystemRoot", "windir", "ComSpec", "PATHEXT", "OS", "PROCESSOR_ARCHITECTURE")

_SANDBOX_USER = "termx-sandbox"

# Filesystem-boundary bookkeeping, shared across every runner instance in
# this process: per path, the live spawn refs — each recording whether it
# needs write access and whether it runs as the dedicated sandbox user —
# and the icacls ops currently applied. Apply and release both recompute
# the required op set under this one lock, so a queued last-release restore
# can never land after a fresh apply on the same path, a read-only holder
# never blocks a writable ref's low-IL label, and a token-mode hold never
# hides a user-mode ref's DACL grant. (Cross-process runners can't be
# coordinated unprivileged — documented limitation; the bookkeeping only
# covers in-process concurrency.)
_LABEL_LOCK = threading.Lock()
_LABEL_HELD: "dict[str, list[tuple[str, bool]]]" = {}
_LABEL_OPS: "dict[str, set[str]]" = {}


def windows_backend_available() -> bool:
    """True when the restricted-token + job primitives actually work here.

    Probed once per process: build a restricted low-IL token and spawn a
    no-op through ``CreateProcessWithTokenW`` (Secondary Logon). A negative
    probe (or non-win32) is cached permanently.
    """
    global _available
    if _available is not None:
        return _available
    if sys.platform != "win32" or _ICACLS is None:
        _available = False
        return _available
    try:
        from termx.sandbox import _win_shim as shim
    except (ImportError, RuntimeError):
        _available = False
        return _available
    try:
        token = shim.build_restricted_token()
        code = _canary_spawn(shim, token)
        shim._kernel32.CloseHandle(token)
        _available = code == 0
    except OSError:
        _available = False
    return _available


def _canary_spawn(shim, token: int) -> int:
    """Spawn ``cmd /c exit 0`` under the restricted token inside a Job Object.

    Exercises the whole unprivileged primitive chain the backend needs —
    token build, ``CreateProcessWithTokenW`` suspended spawn, job create +
    limit + assign, resume — so ``windows_backend_available()`` is true only
    when a real sandboxed spawn can complete end to end.
    """
    return shim.canary_spawn(token)


_user_available: "bool | None" = None
_USER_PROBE_LOCK = threading.Lock()


def _sandbox_user_provisioned() -> bool:
    """Dedicated restricted user present, credentialed, and launchable.

    Contract: local account ``termx-sandbox`` exists AND
    ``%ProgramData%\\termx\\sandbox-user.cred`` holds a DPAPI machine-scope
    blob of its generated password (UTF-8), written by an install-time
    elevated ``termx sandbox provision`` step. The password is loaded inside
    the shim — it never enters a request file. The probe ends in a real
    canary spawn under the restricted user token, so a host without
    SE_IMPERSONATE_NAME/SE_ASSIGNPRIMARYTOKEN never selects a mode whose
    spawn paths all fail. Probed once per process; the probe is serialized
    and its result published only after completion, so a runner constructed
    mid-probe waits for the verified answer instead of latching a
    pessimistic default and diverging from its peers' identity mode.
    """
    global _user_available
    if _user_available is not None:
        return _user_available
    with _USER_PROBE_LOCK:
        if _user_available is not None:
            return _user_available
        result = False
        if sys.platform == "win32":
            try:
                from termx.sandbox import _win_shim as shim

                result = shim.probe_sandbox_user()
            except (ImportError, RuntimeError, OSError):
                pass
        _user_available = result
    return _user_available


class WindowsSandboxRunner:
    """SandboxRunner enforcing restricted execution via token+job."""

    def __init__(self, *, profile: str, state_dir: "str | Path | None" = None) -> None:
        if profile not in ("workspace", "agent"):
            raise SandboxFailure(
                "invalid_profile", "windows backend only serves restricted profiles"
            )
        if not windows_backend_available():
            raise SandboxFailure(
                "backend_unavailable",
                "windows backend requires restricted-token spawn (Secondary Logon) and icacls",
            )
        self._profile = profile
        base = Path(state_dir) if state_dir is not None else _default_state_dir()
        self._state_dir = base / "sandbox"
        self._release_tasks: "set[asyncio.Task]" = set()
        # Dedicated restricted user (stronger identity boundary) when the
        # admin-provisioned account + DPAPI credential exist.
        self._user_mode = _sandbox_user_provisioned()

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def _backend_name(self) -> str:
        return "windows-user" if self._user_mode else "windows-token"

    def capabilities(self) -> SandboxCapabilities:
        user_mode = self._user_mode
        return SandboxCapabilities(
            backend=self._backend_name,
            profile=self._profile,
            strength="restricted-user" if user_mode else "kernel",
            granted=_GRANTED,
            grantable=_GRANTABLE,
            network_control=False,
            filesystem_isolation=True,
            identity_isolation=user_mode,
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
        home = self._sandbox_home(spec)
        tmp = self._sandbox_tmp(spec)
        home.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True, exist_ok=True)
        env = spec.env or build_environment(
            spec.profile, home=str(home), tmp_dir=str(tmp)
        )
        env = self._os_env(env)

        try:
            # icacls recursion can take seconds on a large tree — keep it
            # off the event loop.
            held = await asyncio.to_thread(
                self._apply_fs_boundary, spec, home, tmp
            )
        except OSError as exc:
            raise SandboxFailure(
                "boundary_setup", f"windows sandbox: filesystem boundary failed: {exc}"
            ) from exc

        request = {
            "command": self._command_line(spec),
            "cwd": str(Path(spec.cwd or spec.workspace_root).resolve()),
            "env": env,
            "job": self._job_limits(spec.limits),
            "mode": "user" if self._user_mode else "token",
        }
        req_dir = self._state_dir / "requests"
        req_dir.mkdir(parents=True, exist_ok=True)
        req_path = req_dir / f"spawn-{uuid.uuid4().hex}.json"
        req_path.write_text(json.dumps(request), encoding="utf-8")

        shim_argv = self._shim_argv(req_path)
        shim_env = self._shim_env()
        try:
            process = await asyncio.create_subprocess_exec(
                *shim_argv,
                stdin=spec.stdin if spec.stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(Path(spec.workspace_root).resolve()),
                env=shim_env,
            )
        except OSError as exc:
            try:
                req_path.unlink()
            except OSError:
                pass
            await asyncio.to_thread(self._drop_held, held)
            raise SandboxFailure("spawn_failed", f"windows sandbox spawn failed: {exc}") from exc
        task = asyncio.create_task(self._release_after(process, held))
        self._release_tasks.add(task)
        task.add_done_callback(self._release_tasks.discard)
        return StreamedProcess(process, spec, backend=self._backend_name)

    def spawn_terminal(self, spec: SpawnSpec, rows: int, cols: int):
        """Create an actual restricted ConPTY, retaining roots until job exit.

        Called from the terminal service's worker thread. Windows cannot enforce
        a network-denied PTY, so only the explicitly network-enabled human
        workspace profile is supported; no unrestricted fallback is permitted.
        """
        spec.validate()
        if spec.profile != self._profile or spec.profile != 'workspace' or not spec.pty:
            raise SandboxFailure('invalid_profile', 'Windows PTYs require the workspace terminal profile')
        if spec.network != 'outbound' or 'net.outbound:any' not in spec.granted_capabilities:
            raise SandboxFailure('network_unavailable', 'Windows cannot enforce network-denied terminals')
        from termx.sandbox._win_conpty import RestrictedConPTY
        home, temporary = self._sandbox_home(spec), self._sandbox_tmp(spec)
        home.mkdir(parents=True, exist_ok=True); temporary.mkdir(parents=True, exist_ok=True)
        env = dict(spec.env or build_environment(spec.profile, home=str(home), tmp_dir=str(temporary)))
        env.update({'TMP':str(temporary), 'TEMP':str(temporary), 'HOME':str(home), 'USERPROFILE':str(home)})
        env = self._os_env(env)
        held = self._apply_fs_boundary(spec, home, temporary)
        return RestrictedConPTY(command=self._command_line(spec), cwd=str(Path(spec.cwd or spec.workspace_root).resolve()),
            env=env, job_limits=self._job_limits(spec.limits), user_mode=self._user_mode, rows=rows, cols=cols,
            release=lambda:self._drop_held(held))

    # -- internals ------------------------------------------------------

    def _shim_argv(self, req_path: Path) -> list[str]:
        return [sys.executable, "-m", "termx.sandbox._win_shim", str(req_path)]

    def _shim_env(self) -> dict[str, str]:
        """Minimal env for the shim interpreter — OS plumbing only, never the
        restricted spec env and never the full host environ."""
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1"}
        for name in _OS_ENV:
            if name in os.environ:
                env[name] = os.environ[name]
        pythonpath = os.environ.get("PYTHONPATH")
        if pythonpath:
            env["PYTHONPATH"] = pythonpath
        return env

    def _os_env(self, env: dict[str, str]) -> dict[str, str]:
        out = dict(env)
        for name in _OS_ENV:
            if name in os.environ and name not in out:
                out[name] = os.environ[name]
        return out

    def _sandbox_home(self, spec: SpawnSpec) -> Path:
        if spec.home:
            return Path(spec.home)
        scope = spec.task_id or self._profile
        return self._mode_state_root() / "home" / _safe_name(scope)

    def _sandbox_tmp(self, spec: SpawnSpec) -> Path:
        scope = spec.task_id or self._profile
        return self._mode_state_root() / "tmp" / _safe_name(scope)

    def _mode_state_root(self) -> Path:
        if self._user_mode:
            # %ProgramData% is traversable by a foreign account: the dedicated
            # user's USERPROFILE/TEMP equivalents must not live under the host
            # user's private state tree, where ancestor DACLs would deny the
            # child access to its own home/tmp even with leaf grants.
            return (
                Path(os.environ.get("ProgramData", r"C:\ProgramData"))
                / "termx"
                / "sandbox-state"
            )
        return self._state_dir

    def _command_line(self, spec: SpawnSpec) -> str:
        if spec.argv is not None:
            return subprocess.list2cmdline(list(spec.argv))
        comspec = os.environ.get("ComSpec") or str(
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
        )
        return f'"{comspec}" /c {spec.shell}'

    def _job_limits(self, limits: ResourceLimits) -> dict:
        eff = limits if limits != ResourceLimits() else _DEFAULT_LIMITS
        job: dict[str, int | float] = {}
        if eff.pids:
            job["active_process_limit"] = int(eff.pids)
        if eff.memory_bytes:
            job["process_memory_bytes"] = int(eff.memory_bytes)
            job["job_memory_bytes"] = int(eff.memory_bytes)
        if eff.cpu_s:
            job["job_time_100ns"] = int(eff.cpu_s * 10_000_000)
        return job

    def _apply_fs_boundary(
        self, spec: SpawnSpec, home: Path, tmp: Path
    ) -> "list[tuple[str, str, bool]]":
        """Make approved roots reachable by the restricted child.

        Writable roots are relabeled low-integrity in BOTH modes (the child
        is always a low-IL token — dedicated-user mode strips privileges and
        forces low IL on the LogonUser token too), so mandatory no-write-up
        denies every write outside the approved set. User mode additionally
        needs DACL grants: the foreign ``termx-sandbox`` identity has no
        default rights on the host user's objects.

        Each ref records (kind, user_mode); the ops a path carries are
        recomputed from its live refs, so a read-only holder never blocks
        a later writable ref's label and a token-mode hold never hides a
        user-mode ref's grant. The last release restores everything
        (medium IL, grants removed) so a stale low label can never widen a
        later task's write domain. Returns this spawn's held refs.
        """
        writable = [Path(p).resolve() for p in (spec.workspace_root, *spec.writable_roots)]
        writable += [home, tmp]
        ro = [Path(p).resolve() for p in spec.read_only_roots]
        held: list[tuple[str, str, bool]] = []
        try:
            # The icacls calls stay inside the lock: a queued restore cannot
            # land after a fresh apply on the same path and clobber it.
            with _LABEL_LOCK:
                for path in dict.fromkeys(writable + ro):
                    key = str(path)
                    kind = "w" if path in writable else "r"
                    _LABEL_HELD.setdefault(key, []).append((kind, self._user_mode))
                    held.append((key, kind, self._user_mode))
                    self._reconcile_locked(key)
        except Exception:
            with _LABEL_LOCK:
                for key, kind, umode in held:
                    entries = _LABEL_HELD.get(key)
                    if not entries:
                        continue
                    try:
                        entries.remove((kind, umode))
                    except ValueError:
                        continue
                    try:
                        self._reconcile_locked(key)
                    except OSError:
                        pass
                    if not entries:
                        _LABEL_HELD.pop(key, None)
                        _LABEL_OPS.pop(key, None)
            raise
        return held

    @staticmethod
    def _needed_ops(entries: "list[tuple[str, bool]]") -> "set[str]":
        """Ops a path's live refs require: the low-IL label while any
        writable ref lives, and a ``termx-sandbox`` DACL grant — full while
        any writable *user-mode* ref lives, read-only otherwise — while any
        dedicated-user ref lives."""
        ops: set[str] = set()
        if any(kind == "w" for kind, _ in entries):
            ops.add("il")
        if any(user for _, user in entries):
            ops.add(
                "grantF"
                if any(kind == "w" and user for kind, user in entries)
                else "grantR"
            )
        return ops

    def _reconcile_locked(self, key: str) -> None:
        """Drive a path's applied ops to the set its live refs require.

        Caller must hold ``_LABEL_LOCK``. icacls ``/grant`` replaces the
        trustee's ACE, so a grant upgrade/downgrade is one call; grant
        removal and the medium-IL restore run only when no live ref needs
        them.
        """
        applied = _LABEL_OPS.setdefault(key, set())
        needed = self._needed_ops(_LABEL_HELD.get(key, []))
        want_grant = needed & {"grantF", "grantR"}
        if want_grant != (applied & {"grantF", "grantR"}):
            if want_grant:
                access = "F" if "grantF" in want_grant else "R"
                self._icacls(
                    [key, "/grant", f"{_SANDBOX_USER}:(OI)(CI)({access})", "/T"]
                )
            else:
                self._icacls([key, "/remove:g", _SANDBOX_USER, "/T"])
            applied.difference_update({"grantF", "grantR"})
            applied.update(want_grant)
        if ("il" in needed) != ("il" in applied):
            self._icacls(
                [key, "/setintegritylevel",
                 "(OI)(CI)L" if "il" in needed else "(OI)(CI)M", "/T"]
            )
            if "il" in needed:
                applied.add("il")
            else:
                applied.discard("il")

    async def _release_after(self, process, held: "list[str]") -> None:
        """Restore the fs boundary once the shim exits (normal or killed)."""
        try:
            await process.wait()
        finally:
            await asyncio.to_thread(self._drop_held, held)

    def _drop_held(self, held: "list[tuple[str, str, bool]]") -> None:
        """Release this spawn's refs and recompute each path's op set.

        A last release restores everything; a writable→read-only
        transition restores just the medium label (low-IL readers can
        still read medium objects); a user→token transition removes just
        the DACL grant. Runs inside the shared lock so a queued restore
        can never interleave with a fresh apply on the same path.
        """
        with _LABEL_LOCK:
            for key, kind, umode in held:
                entries = _LABEL_HELD.get(key)
                if not entries:
                    continue
                try:
                    entries.remove((kind, umode))
                except ValueError:
                    continue
                try:
                    self._reconcile_locked(key)
                except OSError:
                    # Path gone or already reset — a failed restore leaves
                    # a stale label on an existing tree; it widens writes
                    # for later low-IL tasks on that path only (documented
                    # residual).
                    pass
                if not entries:
                    _LABEL_HELD.pop(key, None)
                    _LABEL_OPS.pop(key, None)

    @staticmethod
    def _icacls(args: list[str]) -> None:
        if _ICACLS is None:
            raise OSError("icacls not found")
        proc = subprocess.run(
            [_ICACLS, *args], capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            raise OSError(
                f"icacls {' '.join(args)} failed ({proc.returncode}): "
                f"{(proc.stdout + proc.stderr).strip()[:300]}"
            )


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:80] or "agent"


def _default_state_dir() -> Path:
    from termx.config import config_dir

    return config_dir()


__all__ = ["WindowsSandboxRunner", "windows_backend_available"]
