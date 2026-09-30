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
  credentials were provisioned (``%ProgramData%\\termx\\sandbox-user.cred``,
  a DPAPI machine-scope blob holding the generated password — e.g. written
  by an elevated ``termx sandbox provision`` step): the child then runs via
  ``CreateProcessWithLogonW`` as that user, gets DACL grants instead of
  integrity labels, and reports ``identity_isolation=True``,
  ``strength='restricted-user'``.
* ``strength='kernel'`` in token mode: the enforced boundary is kernel
  security machinery (mandatory integrity labels + job objects), not a
  wrapper script. ``'restricted-user'`` is reserved for the separate-user
  mode, matching the strength enum's identity semantics.

Residual boundaries worth stating plainly:

* The writable domain is **per-principal, not per-spawn**. Low-integrity
  labels persist on disk, so every low-IL task under this account shares one
  writable domain: a later task's child can write a *previous* task's
  labeled workspace even when that path is absent from its own
  ``writable_roots``. The boundary enforced here is sandboxed-domain vs.
  host filesystem, not task vs. task — Windows has no per-process ACL
  principal short of a distinct token identity, so cross-task file
  isolation is not provided by this backend.
* ``windows-user`` mode trades the integrity boundary for an identity
  boundary: ``CreateProcessWithLogonW`` cannot carry a pre-built restricted
  token, so the child runs at *medium* IL as ``termx-sandbox``. Its write
  boundary is DACL-only — host-owned objects are denied (the dominant
  leak), but anything world-writable (e.g. ``%PUBLIC%``) remains writable
  outside approved roots. The dedicated user's home/tmp live under
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
    import ctypes
    import ctypes.wintypes as wt

    si = shim._STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    pi = shim._PROCESS_INFORMATION()
    cmd = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe") + " /c exit 0"
    if not shim._advapi32.CreateProcessWithTokenW(
        token, shim._LOGON_WITH_PROFILE, None, cmd,
        shim._CREATE_SUSPENDED | shim._CREATE_UNICODE_ENVIRONMENT, None, None,
        ctypes.byref(si), ctypes.byref(pi),
    ):
        raise shim._last_error()
    resumed = False
    try:
        job = shim._kernel32.CreateJobObjectW(None, None)
        if not job:
            raise shim._last_error()
        info = shim._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            shim._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | shim._JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        info.BasicLimitInformation.ActiveProcessLimit = 8
        if not shim._kernel32.SetInformationJobObject(
            job, shim._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise shim._last_error()
        if not shim._kernel32.AssignProcessToJobObject(job, pi.hProcess):
            raise shim._last_error()
        shim._kernel32.ResumeThread(pi.hThread)
        resumed = True
        shim._kernel32.WaitForSingleObject(pi.hProcess, 15000)
        code = wt.DWORD()
        shim._kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        shim._kernel32.CloseHandle(job)
        return code.value
    finally:
        if not resumed:
            # A failed setup must not strand the suspended canary process.
            shim._kernel32.TerminateProcess(pi.hProcess, 1)
        shim._kernel32.CloseHandle(pi.hProcess)
        shim._kernel32.CloseHandle(pi.hThread)


def _sandbox_user_provisioned() -> bool:
    """Dedicated restricted user present and credentialed.

    Contract: local account ``termx-sandbox`` exists AND
    ``%ProgramData%\\termx\\sandbox-user.cred`` holds a DPAPI machine-scope
    blob of its generated password (UTF-8), written by an install-time
    elevated ``termx sandbox provision`` step. The password is loaded inside
    the shim — it never enters a request file.
    """
    if sys.platform != "win32":
        return False
    try:
        from termx.sandbox import _win_shim as shim
        return shim.sandbox_user_credentials() is not None
    except (ImportError, RuntimeError, OSError):
        return False


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
        self._labeled: "set[str]" = set()
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
            self._apply_fs_boundary(spec, home, tmp)
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
            raise SandboxFailure("spawn_failed", f"windows sandbox spawn failed: {exc}") from exc
        return StreamedProcess(process, spec, backend=self._backend_name)

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

    def _apply_fs_boundary(self, spec: SpawnSpec, home: Path, tmp: Path) -> None:
        """Make approved roots reachable by the restricted child — once each.

        Token mode: relabel writable roots low-integrity so the low-IL child
        can write there while the rest of the filesystem stays medium and
        write-denied. Read-only roots need no grant (readable by default,
        write-denied by mandatory no-write-up).

        User mode: DACL-grant the sandbox user's SID writable access on
        writable roots and read access on read-only roots; everything the
        host user owns stays DACL-protected from the foreign identity.
        """
        writable = [Path(p).resolve() for p in (spec.workspace_root, *spec.writable_roots)]
        writable += [home, tmp]
        ro = [Path(p).resolve() for p in spec.read_only_roots]
        for path in dict.fromkeys(writable + ro):
            key = str(path)
            if key in self._labeled:
                continue
            if self._user_mode:
                access = "F" if path in writable else "R"
                self._icacls([key, "/grant", f"{_SANDBOX_USER}:(OI)(CI)({access})", "/T"])
            else:
                if path in writable:
                    self._icacls([key, "/setintegritylevel", "(OI)(CI)L", "/T"])
            self._labeled.add(key)

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
