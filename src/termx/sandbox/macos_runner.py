"""macOS restricted-execution backend — Seatbelt + optional restricted-user helper.

Two modes, selected at runner construction (never silently downgraded):

``macos-seatbelt`` (always available on macOS, no root)
    Spawns via ``sandbox-exec -p '<profile>' <cmd>``. Seatbelt is a
    kernel-enforced MAC policy: the generated profile denies by default and
    allows file reads only on system/toolchain roots plus the approved
    ``SpawnSpec`` roots, file writes only inside ``workspace_root``,
    ``writable_roots`` and the private sandbox HOME/tmp, and
    ``(allow network*)`` only when the spec carries a granted ``net.outbound``
    capability. ``(deny network*)`` otherwise — loopback-only is NOT
    enforceable with seatbelt, so ``network="localhost"`` raises
    ``unsupported_network`` rather than silently widening.

    Caveat: ``sandbox-exec`` is officially deprecated by Apple (still present
    and functional on macOS 14/15/26). It is therefore acceptable here only as
    the kernel fs/net boundary — it is NOT the identity story. Processes run
    as the invoking user (``identity_isolation=False``): same uid means the
    sandboxed process CAN signal unrelated host processes, and DAC-level
    protections outside seatbelt are unchanged. Treat the seatbelt boundary as
    filesystem/network containment, not a user boundary.

    Resource limits: encoded via an ``/bin/sh -c 'ulimit …; exec …'``
    preamble (``-n`` fds, ``-t`` cpu seconds, ``-u`` nproc). macOS has no
    RLIMIT_AS and RLIMIT_DATA does not cover mmap, so ``memory_bytes`` cannot
    be enforced in this mode — that gap is documented rather than faked.
    ``resource_limits=True`` refers to the fd/cpu/nproc bounds that do apply.

``macos-helper`` (preferred boundary, when provisioned)
    A privileged helper binary — shipped by the Tauri packaging (PR C) —
    executes spawns under a restricted non-login macOS user account
    (``termx-sandbox``) so the DAC uid boundary adds real identity isolation.
    The helper is located via ``$TERMX_SANDBOX_HELPER`` (a developer seam —
    anything able to set env on the daemon is already host-level) or the
    well-known path ``/Library/Application Support/termx/sandbox-helper``.
    The well-known path is only trusted when the binary is **root-owned and
    not group/other-writable**: a user-writable file could attest
    ``termx-sandbox`` + ``seatbelt`` without enforcing either boundary.
    Detection is then a live probe: the runner sends ``{"op": "status"}``
    and requires ``user == termx-sandbox`` and ``seatbelt: true`` in the
    reply so defense-in-depth applies on top of the uid boundary.

Helper protocol — JSON Lines over the helper's stdin/stdout. One helper
invocation per runner handles a spawn session; implementers may also run it
as a persistent broker (the protocol is identical):

    -> {"op": "status"}
    <- {"op": "status", "ok": true, "user": "termx-sandbox", "seatbelt": true}

    -> {"op": "spawn", "argv": [...], "shell": <str|null>, "cwd": <str>,
         "env": {...}, "workspace_root": <str>, "writable_roots": [...],
         "read_only_roots": [...], "network": "none"|"outbound",
         "home_dir": <str>, "tmp_dir": <str>,
         "limits": {"cpu_s": …, "memory_bytes": …, "pids": …, "wall_s": …,
                    "output_bytes": …},
         "seatbelt": "<generated seatbelt profile text>",
         "task_id": <str|null>, "purpose": <str>}
    <- {"op": "spawn", "ok": true, "pid": <child pid>}
       {"op": "spawn", "ok": false, "error": {"reason": …, "message": …}}
    <- {"op": "output", "pid": N, "stream": "stdout"|"stderr",
        "data": "<base64>"}            (zero or more, streamed)
    <- {"op": "exit", "pid": N, "exit_code": <int>}   (terminal; helper may exit)

    -> {"op": "terminate", "pid": N}
    <- {"op": "terminate", "ok": true, "pid": N}   then the exit frame above.

Helper obligations (the client cannot enforce these — they are the contract
the privileged binary must meet):

- Apply the supplied seatbelt profile around the child (sandbox_init/exec)
  in addition to dropping to the restricted user, so the kernel boundary
  holds even if the uid boundary is configured wrongly.
- Provision ``home_dir``/``tmp_dir`` owned by the restricted user before
  spawn: the client creates them under the daemon state dir, which a
  different uid cannot write. The helper must likewise give the restricted
  user access to ``workspace_root``/``writable_roots`` (chown or ACL),
  never widening access beyond the approved roots.
- Bound every ``output`` frame to ``_MAX_FRAME_BYTES`` of raw data so one
  frame never exceeds the client's line buffer.
- Kill the child's whole process group on ``terminate``, AND reap all its
  spawned children when the session ends: when the helper's stdin reaches
  EOF or the helper itself exits/dies, every child it started must die
  too (e.g. kill-on-close process group or a reaper). The daemon cannot
  signal a different-uid child, so a helper that exits without reaping
  orphans the sandboxed tree.
- Keep stdout protocol-only; diagnostics belong on stderr.
"""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from termx.sandbox.environment import build_environment
from termx.sandbox.models import (
    ResourceLimits,
    SandboxCapabilities,
    SandboxFailure,
    SpawnSpec,
)
from termx.sandbox.runner import StreamedProcess

_SANDBOX_EXEC = shutil.which("sandbox-exec")
_HELPER_ENV = "TERMX_SANDBOX_HELPER"
# System-wide install location (signed pkg / install-repair lifecycle), NOT
# ~/Library: a helper under the user's home can always be rewritten by the
# very user it claims to drop below.
_HELPER_DEFAULT = Path("/Library/Application Support/termx/sandbox-helper")
_HELPER_USER = "termx-sandbox"
# Helper protocol contract: a single output frame carries at most this many
# bytes of raw child output (the client reads frames with a 2x headroom).
_MAX_FRAME_BYTES = 1 << 21  # 2 MiB

# Same capability vocabulary as linux-ns: every restricted spawn may execute
# processes/children inside the workspace; network publish-class capabilities
# are grantable via remembered rules / one-shot approvals.
_GRANTED = frozenset(
    {"process.execute:any", "process.children:any", "fs.workspace:any"}
)
_GRANTABLE = frozenset(
    {"net.outbound:any", "git.publish", "package.publish", "external.submit"}
)

# Read surface every sandboxed process needs to start and run a toolchain:
# dyld, libSystem, shells, interpreters, /etc name resolution, /dev. Reads are
# metadata-global (stat/getcwd traversal works without leaking file contents);
# *data* reads stay confined to this list plus the approved roots. All entries
# are canonicalized — seatbelt matches kernel paths, so both the user-facing
# spelling (/etc) and the resolved path (/private/etc) are emitted.
# Deliberately narrow: /Users, /private/var/folders (per-user TMPDIR trees —
# that is where host-private files live) and similar are excluded so only
# explicit approved roots become readable. Symlinked system paths are listed
# with both spellings since seatbelt filters match the lookup path.
_SYSTEM_READ_ROOTS = (
    "/System",
    # /Library is narrowed to toolchain/Apple-OS content — a blanket grant
    # exposed host databases and per-machine app data (e.g. /Library/
    # Application Support) outside approved roots. /Library/Apple holds
    # Apple's own system content, including the firmlinked PrivateFrameworks
    # (CoreDevice, MobileDevice) the /usr/bin developer shims dlopen.
    "/Library/Apple",
    "/Library/Developer",
    "/Library/Frameworks",
    "/Library/Fonts",
    "/Library/Perl",
    "/Library/Python",
    "/Library/Ruby",
    "/Library/Java",
    "/usr",
    "/bin",
    "/sbin",
    "/opt",
    "/dev",
    "/Applications",
    "/etc",
    "/private/etc",
    "/cores",
)
_SYSTEM_READ_LITERALS = (
    # /usr/bin developer shims (python3, xcodebuild, ...) read the Xcode
    # license plist before resolving a toolchain. Literal-granting just
    # that plist keeps the rest of /Library/Preferences closed.
    "/Library/Preferences/com.apple.dt.Xcode.plist",
    "/Library/Preferences/com.apple.dt.XcodeHelper.plist",
)
_SYSTEM_EXEC_ROOTS = ("/usr", "/bin", "/sbin", "/opt", "/Applications", "/System")
_DEV_WRITE = ('/dev/null', '/dev/tty', '/dev/ptmx', '/dev/dtracehelper')

# Per-tree rlimit defaults mirroring linux-ns where macOS can express them.
_DEFAULT_LIMITS = ResourceLimits(pids=1024)

_seatbelt_probe: "bool | None" = None
_helper_probe: "dict[str, Any] | None" = None


def _helper_path() -> Path | None:
    override = os.environ.get(_HELPER_ENV, "").strip()
    if override:
        # Developer seam: trusted by definition — a party that can set env
        # on the daemon already controls the host side of the boundary.
        path = Path(override)
        if path.is_file() and os.access(path, os.X_OK):
            return path
        return None
    path = _HELPER_DEFAULT
    if not (path.is_file() and os.access(path, os.X_OK)):
        return None
    # Self-attestation is only meaningful from a tamper-proof binary: the
    # well-known helper AND every ancestor directory must be root-owned and
    # not group/other-writable, or any local process could rename a parent
    # and substitute a self-attesting binary.
    try:
        cur = path.resolve()
        while True:
            stat_result = cur.stat()
            if stat_result.st_uid != 0 or (stat_result.st_mode & 0o022):
                return None
            if cur.parent == cur:
                break
            cur = cur.parent
    except OSError:
        return None
    return path


def _probe_helper() -> dict[str, Any] | None:
    """Handshake with the helper binary. Returns the parsed status reply."""
    global _helper_probe
    if _helper_probe is not None:
        return _helper_probe
    _helper_probe = _probe_helper_now()
    return _helper_probe


def _probe_helper_now() -> dict[str, Any] | None:
    path = _helper_path()
    if path is None:
        return None
    try:
        proc = subprocess.run(
            [str(path)],
            input=json.dumps({"op": "status"}) + "\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        if frame.get("op") == "status":
            return frame if frame.get("ok") else None
    return None


def macos_helper_available() -> bool:
    """True when a provisioned helper answers status as the restricted user
    AND advertises seatbelt enforcement inside the child."""
    reply = _probe_helper()
    return bool(
        reply
        and reply.get("user") == _HELPER_USER
        and reply.get("seatbelt") is True
    )


def macos_seatbelt_available() -> bool:
    """True when sandbox-exec exists and accepts a trivial profile."""
    global _seatbelt_probe
    if _seatbelt_probe is not None:
        return _seatbelt_probe
    if sys.platform != "darwin" or _SANDBOX_EXEC is None:
        _seatbelt_probe = False
        return _seatbelt_probe
    try:
        probe = subprocess.run(
            [_SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            timeout=10,
        )
        _seatbelt_probe = probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _seatbelt_probe = False
    return _seatbelt_probe


def macos_backend_available() -> bool:
    return macos_helper_available() or macos_seatbelt_available()


def _net_granted(spec: SpawnSpec) -> bool:
    if spec.network != "outbound":
        return False
    return any(c.startswith("net.outbound") for c in spec.granted_capabilities)


def _canon(path: str) -> str:
    return str(Path(path).resolve())


def _spellings(path: str) -> list[str]:
    """Emit both the given spelling and its canonical form — seatbelt path
    filters match the resolved vnode path, but symlink parents (e.g. /etc ->
    /private/etc) also need their lookup spelling allowed."""
    canon = _canon(path)
    return [path, canon] if canon != path else [canon]


def _sb_escape(path: str) -> str:
    if any(c in path for c in ('"', "\\", "\n", "\r")):
        raise SandboxFailure(
            "invalid_spawn", f"path {path!r} is not expressible in a seatbelt profile"
        )
    return path


def _subpaths(paths: "list[str]") -> str:
    return " ".join(f'(subpath "{_sb_escape(p)}")' for p in dict.fromkeys(paths))


def _seatbelt_profile(spec: SpawnSpec, home: Path, tmp_dir: Path) -> str:
    """Compile the deny-default seatbelt profile for one spawn.

    Reads: system roots + approved roots. Writes: workspace, writable roots,
    private HOME/tmp, and a few /dev endpoints. Exec: system trees + approved
    roots (agents legitimately run binaries they just built in-workspace).
    """
    write_roots: list[str] = []
    read_roots: list[str] = []
    exec_roots: list[str] = list(_SYSTEM_EXEC_ROOTS)

    for raw in (spec.workspace_root, *spec.writable_roots):
        if raw:
            for p in _spellings(raw):
                write_roots.append(p)
                read_roots.append(p)
                exec_roots.append(p)
    for raw in spec.read_only_roots:
        if raw:
            for p in _spellings(raw):
                read_roots.append(p)
                exec_roots.append(p)
    for p in _spellings(str(home)) + _spellings(str(tmp_dir)):
        write_roots.append(p)
        read_roots.append(p)
        exec_roots.append(p)

    read_all = list(_SYSTEM_READ_ROOTS) + read_roots
    lines = [
        "(version 1)",
        "(deny default)",
        f"(allow process-exec {_subpaths(exec_roots)})",
        "(allow process-fork)",
        # Same-uid sandbox: signal isolation is NOT provided — a same-uid
        # process can always be signaled/signaling; document, don't claim.
        "(allow signal)",
        "(allow sysctl-read)",
        "(allow process-info*)",
        # dyld, TCC-backed frameworks and the shell need Mach services to
        # start at all; same-uid lookups do not cross an identity boundary.
        "(allow mach-lookup)",
        "(allow ipc-posix-shm)",
        # stat() on arbitrary paths is leaked by design (needed for getcwd
        # and PATH traversal through unreadable ancestors). Metadata !=
        # content: file-read-data and directory listing stay confined, so
        # filenames aren't enumerable where data reads are denied.
        "(allow file-read-metadata)",
        '(allow file-read* (literal "/"))',
        f"(allow file-read* {_subpaths(read_all)} "
        + " ".join(f'(literal "{p}")' for p in _SYSTEM_READ_LITERALS)
        + ")",
        f"(allow file-write* {_subpaths(write_roots)})",
        "(allow file-write* "
        + " ".join(f'(literal "{p}")' for p in _DEV_WRITE)
        + ' (regex #"^/dev/ttys[0-9]+$"))',
        "(allow file-ioctl (subpath \"/dev\"))",
    ]
    if _net_granted(spec):
        lines.append("(allow network*)")
    else:
        lines.append("(deny network*)")
    return "".join(lines)


def _limit_preamble(spec: SpawnSpec) -> str:
    """sh preamble enforcing the ResourceLimits macOS can express.

    ``ulimit -v``/``-m`` are rejected by macOS (no RLIMIT_AS, no enforced RSS)
    so ``memory_bytes`` is deliberately not emitted — honest gap. wall_s maps
    to CPU seconds, matching the linux-ns prlimit translation.
    """
    limits = spec.limits if spec.limits != ResourceLimits() else _DEFAULT_LIMITS
    cmds: list[str] = []
    cmds.append("ulimit -n 1024")
    if limits.pids:
        cmds.append(f"ulimit -u {int(limits.pids)}")
    cpu_s = limits.cpu_s or (math.ceil(limits.wall_s) if limits.wall_s else None)
    if cpu_s:
        cmds.append(f"ulimit -t {int(cpu_s)}")
    # A rejected ulimit must never abort the spawn — it only weakens a bound.
    return "; ".join(f"{c} 2>/dev/null" for c in cmds) + "; " if cmds else ""


def _seatbelt_argv(spec: SpawnSpec, home: Path, tmp_dir: Path) -> list[str]:
    profile = _seatbelt_profile(spec, home, tmp_dir)
    preamble = _limit_preamble(spec)
    if spec.argv is not None:
        inner = preamble + 'exec "$@"'
        wrapped = ["/bin/sh", "-c", inner, "termx-sandbox", *spec.argv]
    else:
        inner = preamble + (spec.shell or "")
        wrapped = ["/bin/sh", "-c", inner]
    return [(_SANDBOX_EXEC or "sandbox-exec"), "-p", profile, *wrapped]


def _helper_limits(spec: SpawnSpec) -> dict[str, Any]:
    limits = spec.limits
    return {
        "cpu_s": limits.cpu_s,
        "memory_bytes": limits.memory_bytes,
        "pids": limits.pids,
        "wall_s": limits.wall_s,
        "output_bytes": limits.output_bytes,
    }


class _HelperChild:
    """asyncio.subprocess.Process-shaped facade over the helper protocol.

    ``stdout`` is a merged stream of the child's stdout+stderr output frames,
    matching the PIPE|STDOUT shape StreamedProcess callers rely on.
    """

    def __init__(self, helper: "asyncio.subprocess.Process", pid: int) -> None:
        self._helper = helper
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self._exited = asyncio.Event()

    def feed_output(self, data: bytes) -> None:
        self.stdout.feed_data(data)

    def feed_exit(self, code: int | None) -> None:
        if self.returncode is None:
            self.returncode = code if code is not None else -1
            self.stdout.feed_eof()
            self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode if self.returncode is not None else -1

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        """asyncio-subprocess shape: stdout/stderr are one merged channel, so
        stderr is always empty. The helper owns the child's stdin — input is
        accepted for interface parity but unsupported."""
        data = await self.stdout.read()
        await self.wait()
        return data, b""

    def _request_stop(self) -> None:
        """Best-effort terminate op over the helper channel. Callers reach
        for ``.process.kill()`` after a killpg fails cross-uid — the only
        kill that can work here goes over the wire, synchronously."""
        if self.returncode is not None:
            return
        try:
            if self._helper.stdin is not None:
                self._helper.stdin.write(
                    json.dumps({"op": "terminate", "pid": self.pid}).encode()
                    + b"\n"
                )
        except (OSError, AttributeError):
            pass

    def kill(self) -> None:
        self._request_stop()

    def terminate(self) -> None:
        self._request_stop()


class _HelperProcess:
    """SandboxProcess for helper-mode spawns (terminate goes over the wire,
    not killpg — the child runs under a different uid)."""

    def __init__(
        self,
        helper: "asyncio.subprocess.Process",
        child: _HelperChild,
        spec: SpawnSpec,
    ) -> None:
        self.process = child
        self._helper = helper
        self._spec = spec
        self._pump: asyncio.Task | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def start(self) -> asyncio.Task:
        self._pump = asyncio.ensure_future(self._read_frames())
        return self._pump

    async def _read_frames(self) -> None:
        out = self._helper.stdout
        child = self.process
        if out is None:
            child.feed_exit(-1)
            return
        try:
            while True:
                line = await out.readline()
                if not line:
                    break
                try:
                    frame = json.loads(line)
                except ValueError:
                    # Protocol violation: treat as raw child output defensively.
                    child.feed_output(line)
                    continue
                op = frame.get("op")
                if op == "output" and frame.get("pid") == child.pid:
                    data = frame.get("data", "")
                    try:
                        child.feed_output(base64.b64decode(data))
                    except ValueError:
                        pass
                elif op == "exit" and frame.get("pid") == child.pid:
                    child.feed_exit(frame.get("exit_code"))
                    # Session complete: stdin EOF tells a per-spawn helper it
                    # may exit; a broker stays up harmlessly either way.
                    self._close_stdin()
                    break
        finally:
            if child.returncode is None:
                child.feed_exit(-1)

    def _close_stdin(self) -> None:
        try:
            if self._helper.stdin is not None:
                self._helper.stdin.close()
        except (OSError, AttributeError):
            pass

    async def wait(self) -> int:
        code = await self.process.wait()
        self._close_stdin()
        try:
            await asyncio.wait_for(self._helper.wait(), timeout=3.0)
        except (asyncio.TimeoutError, Exception):
            if self._helper.returncode is None:
                self._helper.kill()
        return code

    async def terminate(self) -> None:
        """Ask the helper to kill the child tree; reap the helper either way."""
        helper = self._helper
        if self.process.returncode is None:
            try:
                if helper.stdin is not None:
                    helper.stdin.write(
                        json.dumps(
                            {"op": "terminate", "pid": self.process.pid}
                        ).encode()
                        + b"\n"
                    )
                    await helper.stdin.drain()
            except (OSError, AttributeError):
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
        if helper.returncode is None:
            helper.kill()
            try:
                await helper.wait()
            except Exception:
                pass
        if self.process.returncode is None:
            self.process.feed_exit(-1)

    def metadata(self) -> dict[str, Any]:
        return {
            "backend": "macos-helper",
            "profile": self._spec.profile,
            "pid": self.pid,
            "purpose": self._spec.purpose,
            "network": self._spec.network,
        }


class MacOSRunner:
    """SandboxRunner for restricted profiles on macOS.

    Mode selection at construction: helper (restricted user + seatbelt inside
    the helper) wins when provisioned; otherwise Seatbelt-only. Neither
    available → ``backend_unavailable`` — never silent host execution.
    """

    def __init__(self, *, profile: str, state_dir: "str | Path | None" = None) -> None:
        if profile not in ("workspace", "agent"):
            raise SandboxFailure(
                "invalid_profile", "macos backend only serves restricted profiles"
            )
        self._helper_status = _probe_helper()
        self._helper = (
            self._helper_status is not None
            and self._helper_status.get("user") == _HELPER_USER
            and self._helper_status.get("seatbelt") is True
        )
        if not self._helper and not macos_seatbelt_available():
            raise SandboxFailure(
                "backend_unavailable",
                "macos backend requires sandbox-exec or a provisioned sandbox helper",
            )
        self._profile = profile
        base = Path(state_dir) if state_dir is not None else _default_state_dir()
        self._state_dir = base / "sandbox"

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def mode(self) -> str:
        return "macos-helper" if self._helper else "macos-seatbelt"

    def capabilities(self) -> SandboxCapabilities:
        if self._helper:
            # Restricted-user identity + seatbelt applied inside the helper:
            # the only mode that can honestly claim identity isolation.
            return SandboxCapabilities(
                backend="macos-helper",
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
        return SandboxCapabilities(
            backend="macos-seatbelt",
            profile=self._profile,
            strength="kernel",
            granted=_GRANTED,
            grantable=_GRANTABLE,
            network_control=True,
            filesystem_isolation=True,
            identity_isolation=False,  # same uid — signals and DAC unchanged
            resource_limits=True,  # ulimit fds/cpu/nproc; memory unenforceable
            process_tree_kill=True,
        )

    async def spawn(self, spec: SpawnSpec) -> StreamedProcess | _HelperProcess:
        spec.validate()
        if spec.profile != self._profile:
            raise SandboxFailure(
                "invalid_profile",
                f"runner for profile {self._profile!r} cannot spawn {spec.profile!r}",
            )
        # Seatbelt cannot express loopback-only networking, and the helper
        # contract inherits the same mechanism — reject rather than widen.
        if spec.network == "localhost":
            raise SandboxFailure(
                "unsupported_network",
                "loopback-only networking is not enforceable by the macos backend",
            )
        home = self._sandbox_home(spec)
        tmp_dir = self._sandbox_tmp(spec)
        home.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        if self._helper:
            # The helper spawns as `termx-sandbox` — our-uid 0700 dirs are
            # unwritable to it. Grant an ACL entry so the restricted user
            # can use HOME/TMPDIR without opening the dirs to every local
            # account (the helper also chowns per protocol contract).
            self._grant_restricted_acl(home)
            self._grant_restricted_acl(tmp_dir)
        env = spec.env or build_environment(
            spec.profile, home=str(home), tmp_dir=str(tmp_dir)
        )
        env["PATH"] = self._sandbox_path(env.get("PATH", ""), spec, home, tmp_dir)
        if self._helper:
            return await self._spawn_helper(spec, env, home, tmp_dir)
        return await self._spawn_seatbelt(spec, env, home, tmp_dir)

    # -- seatbelt -------------------------------------------------------

    @staticmethod
    def _grant_restricted_acl(path: Path) -> None:
        """Best-effort `chmod +a` grant for the restricted helper user."""
        try:
            subprocess.run(
                [
                    "chmod",
                    "+a",
                    f"{_HELPER_USER} allow read,write,execute,delete,append",
                    str(path),
                ],
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    async def _spawn_seatbelt(
        self, spec: SpawnSpec, env: dict[str, str], home: Path, tmp_dir: Path
    ) -> StreamedProcess:
        argv = _seatbelt_argv(spec, home, tmp_dir)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=spec.cwd or spec.workspace_root,
                env=env,
                stdin=spec.stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxFailure("spawn_failed", f"seatbelt spawn failed: {exc}") from exc
        return StreamedProcess(process, spec, backend="macos-seatbelt")

    # -- helper client --------------------------------------------------

    async def _spawn_helper(
        self, spec: SpawnSpec, env: dict[str, str], home: Path, tmp_dir: Path
    ) -> _HelperProcess:
        helper_path = _helper_path()
        if helper_path is None:
            raise SandboxFailure(
                "backend_unavailable", "sandbox helper disappeared between probe and spawn"
            )
        request = {
            "op": "spawn",
            "argv": list(spec.argv) if spec.argv is not None else None,
            "shell": spec.shell,
            "cwd": spec.cwd or spec.workspace_root,
            "env": env,
            "workspace_root": spec.workspace_root,
            "writable_roots": list(spec.writable_roots),
            "read_only_roots": list(spec.read_only_roots),
            # Effective, not requested: an "outbound" spec without a granted
            # net.outbound capability must still read "none" to the helper —
            # the seatbelt profile denies it either way, but the field must
            # never license the helper to configure access itself.
            "network": "outbound" if _net_granted(spec) else "none",
            # The client created these under its own state dir — the helper
            # must chown/provision them for the restricted user (contract).
            "home_dir": str(home),
            "tmp_dir": str(tmp_dir),
            "limits": _helper_limits(spec),
            # The generated seatbelt profile is sent verbatim so the helper
            # wraps it around the child — kernel boundary + uid boundary.
            "seatbelt": _seatbelt_profile(spec, home, tmp_dir),
            "task_id": spec.task_id,
            "purpose": spec.purpose,
        }
        try:
            helper = await asyncio.create_subprocess_exec(
                str(helper_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # Protocol frames are JSON lines carrying base64 output
                # chunks; _MAX_FRAME_BYTES is the helper-side contract cap,
                # the reader limit leaves headroom for framing overhead.
                limit=_MAX_FRAME_BYTES * 2,
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxFailure("spawn_failed", f"helper launch failed: {exc}") from exc
        assert helper.stdin is not None and helper.stdout is not None
        helper.stdin.write(json.dumps(request).encode() + b"\n")
        await helper.stdin.drain()
        reply = await self._read_op(helper, "spawn")
        if not reply or not reply.get("ok"):
            helper.kill()
            await helper.wait()
            err = (reply or {}).get("error") or {}
            raise SandboxFailure(
                err.get("reason", "spawn_failed"),
                err.get("message", "helper rejected the spawn request"),
            )
        child = _HelperChild(helper, int(reply["pid"]))
        spawned = _HelperProcess(helper, child, spec)
        spawned.start()
        return spawned

    @staticmethod
    async def _read_op(
        helper: "asyncio.subprocess.Process", op: str, timeout: float = 10.0
    ) -> dict[str, Any] | None:
        assert helper.stdout is not None
        try:
            line = await asyncio.wait_for(helper.stdout.readline(), timeout)
        except asyncio.TimeoutError:
            return None
        try:
            frame = json.loads(line)
        except ValueError:
            return None
        return frame if frame.get("op") == op else None

    # -- shared internals ------------------------------------------------

    @staticmethod
    def _sandbox_path(
        host_path: str, spec: SpawnSpec, home: Path, tmp_dir: Path
    ) -> str:
        """Filter PATH entries to roots the sandbox can exec from.

        A host PATH that leaks $HOME-anchored prefixes (venvs, ~/.local/bin,
        nvm) resolves binaries the seatbelt then refuses at exec/read time —
        the tool fails for an opaque reason. Dropping unreachable entries
        gives the same fall-through linux-ns gets for free from unmounted
        dirs; a workspace-local bin/ entry survives because the workspace is
        an approved root.
        """
        allowed = [
            *(_canon(p) for p in _SYSTEM_EXEC_ROOTS),
            *(
                _canon(p)
                for p in (
                    spec.workspace_root,
                    *spec.writable_roots,
                    *spec.read_only_roots,
                    str(home),
                    str(tmp_dir),
                )
                if p
            ),
        ]
        entries = []
        for entry in host_path.split(os.pathsep):
            if not entry:
                continue
            resolved = _canon(entry)
            if any(resolved == root or root in Path(resolved).parents or
                   Path(resolved) in Path(root).parents or resolved.startswith(root + os.sep)
                   for root in allowed):
                entries.append(entry)
        return os.pathsep.join(entries) or "/usr/bin:/bin"

    def _sandbox_home(self, spec: SpawnSpec) -> Path:
        return self._state_dir / "home" / _safe_name(spec.task_id or self._profile)

    def _sandbox_tmp(self, spec: SpawnSpec) -> Path:
        return self._state_dir / "tmp" / _safe_name(spec.task_id or self._profile)


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:80] or "agent"


def _default_state_dir() -> Path:
    from termx.config import config_dir

    return config_dir()


__all__ = [
    "MacOSRunner",
    "macos_backend_available",
    "macos_helper_available",
    "macos_seatbelt_available",
]
