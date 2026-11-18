from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time as _time
import uuid
from collections import deque
from urllib.parse import unquote
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Protocol

from termx.terminals import TerminalError, spawn_terminal, set_winsize

REPLAY_MAX_BYTES = 256_000
DEFAULT_COLS = 80
DEFAULT_ROWS = 24
OSC_BUF_MAX = 8192
OSC7_PATTERN = re.compile(rb"\x1b\]7;file://[^/]*?(/[^\x07\x1b]*)(?:\x07|\x1b\\)")


def parse_osc7(data: bytes) -> tuple[str | None, bytes]:
    """Find the last OSC 7 (shell cwd) sequence and return (decoded path, leftover)."""
    match = None
    for match in OSC7_PATTERN.finditer(data):
        pass
    if match is None:
        keep = OSC_BUF_MAX if len(data) > OSC_BUF_MAX else len(data)
        # keep a tail that could still contain the start of a sequence
        return None, data[-keep:]
    cwd = unquote(match.group(1).decode("utf-8", "replace"))
    return cwd or None, data[match.end():]


def activity_idle_seconds() -> float:
    try:
        return max(0.1, float(os.environ.get("TERMX_ACTIVITY_IDLE_S", "2.5")))
    except ValueError:
        return 2.5


def activity_notify_seconds() -> float:
    try:
        return max(0.1, float(os.environ.get("TERMX_ACTIVITY_NOTIFY_S", "10")))
    except ValueError:
        return 10.0

SIGNALS = {
    "int": signal.SIGINT,
    "term": signal.SIGTERM,
}
if hasattr(signal, "SIGHUP"):
    SIGNALS["hup"] = signal.SIGHUP
if hasattr(signal, "SIGKILL"):
    SIGNALS["kill"] = signal.SIGKILL


class ByteSink(Protocol):
    async def send_bytes(self, data: bytes) -> None: ...


class ReplayBuffer:
    def __init__(self, max_bytes: int = REPLAY_MAX_BYTES) -> None:
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._max = max_bytes

    def append(self, data: bytes) -> None:
        if not data:
            return
        self._chunks.append(data)
        self._size += len(data)
        while self._size > self._max and self._chunks:
            old = self._chunks.popleft()
            self._size -= len(old)

    def dump(self) -> bytes:
        return b"".join(self._chunks)


class _HeldProc:
    """The slice of ``subprocess.Popen`` the session layer reads, backed by a holder-owned process."""

    def __init__(self, terminal: "HeldTerminal") -> None:
        self._terminal = terminal

    @property
    def pid(self) -> int | None:
        return self._terminal.pid

    @property
    def returncode(self) -> int | None:
        return self._terminal.exit_code()

    def poll(self) -> int | None:
        return self._terminal.exit_code()

    def wait(self, timeout: float | None = None) -> int | None:
        if not self._terminal.wait_exit(timeout):
            raise subprocess.TimeoutExpired("held terminal", timeout or 0)
        return self._terminal.exit_code()

    def send_signal(self, sig: int) -> None:
        for name, value in SIGNALS.items():
            if value == sig:
                self._terminal.send_signal(name)
                return


class HeldTerminal:
    """A terminal whose PTY is owned by the PTY holder process (see ``pty_holder.py``).

    Same interface as ``PosixTerminal``: the session layer cannot tell the difference, except
    that the process outlives this daemon. Output arrives from the holder client's reader thread.
    """

    MAX_QUEUED = 4 * 1024 * 1024

    def __init__(self, client: object, sid: str, rows: int, cols: int) -> None:
        self.client = client
        self.sid = sid
        self.rows, self.cols = rows, cols
        self.pid: int | None = None
        self.master_fd = -1
        self.offset = 0  # bytes of output seen, in the holder's coordinates (resumes a dropped connection)
        self.proc = _HeldProc(self)
        self._cond = threading.Condition()
        self._chunks: deque[bytes] = deque()
        self._queued = 0
        self._exited = False
        self._exit_code: int | None = None
        self._lost = False

    # -- fed by the holder client's reader thread -----------------------------

    def _feed(self, data: bytes) -> None:
        with self._cond:
            self.offset += len(data)
            self._chunks.append(data)
            self._queued += len(data)
            while self._queued > self.MAX_QUEUED and len(self._chunks) > 1:
                self._queued -= len(self._chunks.popleft())
            self._cond.notify_all()

    def _set_exit(self, code: int | None) -> None:
        with self._cond:
            self._exited = True
            self._exit_code = code
            self._cond.notify_all()

    def _set_lost(self) -> None:
        with self._cond:
            self._lost = True
            self._cond.notify_all()

    # -- the PosixTerminal interface ------------------------------------------

    def read(self, timeout: float = 0.25) -> bytes | None:
        """Bytes, b'' at end of output, or None when idle."""
        with self._cond:
            if not self._chunks and not (self._exited or self._lost):
                self._cond.wait(timeout)
            if self._chunks:
                data = self._chunks.popleft()
                self._queued -= len(data)
                return data
            if self._exited or self._lost:
                return b""
            return None

    def write(self, data: bytes) -> None:
        if not data or self._exited or self._lost:
            return
        from termx.pty_holder_client import HolderError

        try:
            self.client.write(self.sid, data)  # type: ignore[attr-defined]
        except HolderError:
            return

    def send_signal(self, name: str) -> bool:
        if self._exited or self._lost:
            return False
        from termx.pty_holder_client import HolderError

        try:
            return bool(self.client.signal(self.sid, name.lower()))  # type: ignore[attr-defined]
        except HolderError:
            return False

    def resize(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        if self._exited or self._lost:
            return
        from termx.pty_holder_client import HolderError

        try:
            self.client.resize(self.sid, rows, cols)  # type: ignore[attr-defined]
        except HolderError:
            return

    def alive(self) -> bool:
        return not (self._exited or self._lost)

    def exit_code(self) -> int | None:
        return self._exit_code

    def wait_exit(self, timeout: float | None) -> bool:
        with self._cond:
            if not (self._exited or self._lost):
                self._cond.wait(timeout)
            return self._exited or self._lost

    def kill(self, timeout: float = 1.5) -> None:
        """End the process and make the holder forget it."""
        from termx.pty_holder_client import HolderError

        try:
            if self._exited or self._lost:
                self.client.forget(self.sid)  # type: ignore[attr-defined]
            else:
                self.client.kill(self.sid, timeout)  # type: ignore[attr-defined]
        except HolderError:
            pass
        if not self._lost:
            self._set_exit(self._exit_code)

    def detach(self) -> None:
        """Let go without ending the process: it keeps running in the holder."""
        try:
            self.client.release(self.sid)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - best effort at shutdown
            pass
        self._set_lost()


_INTEGRATION_DIR: Path | None = None
_ZSH_SOURCERS = (".zshenv", ".zprofile", ".zlogin", ".zshrc")
_BASH_RC = """[ -f ~/.bash_profile ] && . ~/.bash_profile || { [ -f ~/.bashrc ] && . ~/.bashrc; }
_termx_osc7() { printf '\\033]7;file://%s%s\\033\\\\' "${HOSTNAME:-localhost}" "$PWD"; }
case ";${PROMPT_COMMAND:-};" in *";_termx_osc7;"*) ;; *) PROMPT_COMMAND="_termx_osc7${PROMPT_COMMAND:+;$PROMPT_COMMAND}";; esac
"""


def shell_integration_dir() -> Path:
    """Lazily create the shell rc directory used for OSC 7 cwd reporting."""
    global _INTEGRATION_DIR
    if _INTEGRATION_DIR is not None:
        return _INTEGRATION_DIR
    root = Path(tempfile.mkdtemp(prefix="termx-shell-"))
    (root / "bashrc").write_text(_BASH_RC, encoding="utf-8")
    for name in _ZSH_SOURCERS:
        body = f'[[ -f "$TERMX_ZDOTDIR/{name}" ]] && source "$TERMX_ZDOTDIR/{name}"\n'
        if name == ".zshrc":
            body += (
                'autoload -Uz add-zsh-hook 2>/dev/null\n'
                "_termx_osc7() { printf '\\033]7;file://%s%s\\033\\\\' "
                '"${HOST:-localhost}" "$PWD"; }\n'
                "if (( $+functions[add-zsh-hook] )); then add-zsh-hook precmd _termx_osc7; "
                "else precmd_functions+=(_termx_osc7); fi\n"
            )
        (root / name).write_text(body, encoding="utf-8")
    _INTEGRATION_DIR = root
    return root


def with_shell_integration(argv: list[str], env: dict[str, str]) -> list[str]:
    """Rewrite argv/env so zsh and bash report cwd via OSC 7."""
    if not argv:
        return argv
    name = os.path.basename(argv[0]).lower()
    if name == "zsh":
        root = shell_integration_dir()
        env["TERMX_ZDOTDIR"] = env.get("ZDOTDIR") or env.get("HOME") or str(Path.home())
        env["ZDOTDIR"] = str(root)
        return argv
    if name == "bash":
        rc = shell_integration_dir() / "bashrc"
        rest: list[str] = []
        skip = False
        for arg in argv[1:]:
            if skip:
                skip = False
                continue
            if arg == "--rcfile":
                skip = True
                continue
            rest.append(arg)
        return [argv[0], "--rcfile", str(rc), *rest]
    return argv


def default_shell() -> str:
    if os.name == "nt":
        return os.environ.get("COMSPEC") or "powershell.exe"
    configured = os.environ.get("SHELL")
    if configured:
        return configured
    # No SHELL (containers, services): prefer zsh, then whichever POSIX shell
    # this host actually has, so a new session never starts a missing binary.
    for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


def default_argv(shell: str | None = None) -> list[str]:
    sh = shell or default_shell()
    name = os.path.basename(sh).lower()
    if name in {"zsh", "bash", "sh"}:
        return [sh, "-l"]
    if name in {"powershell.exe", "pwsh.exe", "powershell", "pwsh"}:
        return [sh, "-NoLogo"]
    if name in {"cmd.exe", "cmd"}:
        return [sh]
    return [sh]


@dataclass
class Session:
    id: str
    title: str
    created_at: float
    cols: int
    rows: int
    argv: list[str]
    cwd: str = field(default_factory=lambda: str(Path.home()))
    shell: str = field(default_factory=default_shell)
    sandbox_profile: str = "host"
    # Complete child environment for the host profile (callers scrub it); None
    # inherits the daemon's. Restricted profiles always build their own.
    env: dict[str, str] | None = None
    # PTY holder client: when set the terminal is owned by the holder process and survives this daemon.
    holder: object | None = None
    master_fd: int = -1
    proc: object | None = None
    replay: ReplayBuffer = field(default_factory=ReplayBuffer)
    subscribers: set[ByteSink] = field(default_factory=set)
    exited: bool = False
    exit_code: int | None = None
    live_cwd: str | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _reader: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _terminal: object | None = None
    _osc_buf: bytes = b""
    _last_output_at: float = 0.0
    _busy_since: float | None = None
    _busy_reported: bool = False
    _reported_cwd: str | None = None
    _activity_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        terminal = None
        if self.sandbox_profile != 'host' and sys.platform == 'win32':
            runner, spec = self._restricted_context()
            spawn = getattr(runner, 'spawn_terminal', None)
            if spawn is None:
                raise TerminalError('Windows sandbox cannot create restricted ConPTY terminals')
            try:
                terminal = spawn(spec, self.rows, self.cols)
            except Exception as exc:
                raise TerminalError(f'restricted Windows terminal unavailable: {exc}') from exc
        elif self.sandbox_profile != "host":
            argv, env = self._restricted_launch()
        else:
            env = dict(self.env) if self.env is not None else os.environ.copy()
            env.setdefault("TERM", "xterm-256color")
            env.setdefault("COLORTERM", "truecolor")
            argv = with_shell_integration(self.argv, env)
        try:
            if terminal is None:
                terminal = self._spawn(argv, env)
        except TerminalError:
            raise
        self._terminal = terminal
        self.master_fd = getattr(terminal, "master_fd", -1)
        self.proc = getattr(terminal, "proc", None)
        self._reader = threading.Thread(target=self._read_loop, name=f"pty-{self.id}", daemon=True)
        self._reader.start()

    def _spawn(self, argv: list[str], env: dict[str, str]):
        """Direct child of this process, or (held mode) a terminal the PTY holder owns.

        ``argv`` and ``env`` are final: for a restricted profile they already carry the sandbox
        wrapper from ``_restricted_launch``; the holder execs exactly this and adds nothing.
        """
        holder = self.holder
        if holder is None:
            return spawn_terminal(argv, self.cwd, env, self.rows, self.cols)
        from termx.pty_holder_client import HolderError, HolderRefused

        terminal = HeldTerminal(holder, self.id, self.rows, self.cols)
        meta = {
            "title": self.title[:200], "cwd": self.cwd[:900], "shell": self.shell[:300],
            "sandbox_profile": self.sandbox_profile, "created_at": self.created_at,
            "argv0": os.path.basename(argv[0])[:200] if argv else "",
        }
        try:
            info = holder.spawn(terminal, argv, self.cwd, env, self.rows, self.cols, meta)  # type: ignore[attr-defined]
        except HolderRefused as exc:
            raise TerminalError(str(exc)) from None
        except HolderError as exc:
            raise TerminalError(f"the PTY holder is unavailable: {exc}") from None
        terminal.pid = info.get("pid")
        return terminal

    @property
    def durable(self) -> bool:
        """True when the process is owned by the PTY holder and so survives a daemon restart."""
        return isinstance(self._terminal, HeldTerminal)

    def adopt(self, terminal: "HeldTerminal", replay: bytes, *, exited: bool = False, exit_code: int | None = None) -> None:
        """Take over a terminal the holder kept alive across a daemon restart, replay intact."""
        self._terminal = terminal
        self.master_fd = -1
        self.proc = terminal.proc
        self.holder = terminal.client
        if replay:
            self.replay.append(replay)
            cwd, self._osc_buf = parse_osc7(replay)
            if cwd:
                self.live_cwd = self._reported_cwd = cwd
            self._last_output_at = _time.monotonic()
        if exited:
            self.exited = True
            self.exit_code = exit_code
            return
        self._reader = threading.Thread(target=self._read_loop, name=f"pty-{self.id}", daemon=True)
        self._reader.start()

    def detach(self) -> None:
        """Stop managing this terminal without ending it; only meaningful for a held terminal."""
        terminal = self._terminal
        self.exited = True
        if self._activity_task is not None and not self._activity_task.done():
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._activity_task.cancel)
        if isinstance(terminal, HeldTerminal):
            terminal.detach()
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=0.5)

    def _restricted_launch(self) -> tuple[list[str], dict[str, str]]:
        """Route this terminal through the sandbox backend for its profile.

        The terminal layer keeps owning the pty; the backend's ``spawn_argv``
        resolves the wrapped command line so a workspace terminal gets the
        same kernel isolation as agent spawns. Fails closed — a backend with
        no pty story raises TerminalError instead of spawning unsandboxed.
        """
        runner, spec = self._restricted_context()
        spawn_argv = getattr(runner, "spawn_argv", None)
        if spawn_argv is None:
            raise TerminalError(
                f"sandbox backend {runner.capabilities().backend} cannot spawn pty terminals"
            )
        try:
            return spawn_argv(spec), spec.env or {}
        except Exception as exc:
            raise TerminalError(f"sandbox spawn rejected: {exc}") from exc

    def _restricted_context(self):
        from termx.sandbox import SpawnSpec, runner_for
        from termx.sandbox.environment import build_environment, sandbox_home
        from termx.config import config_dir

        home = sandbox_home(config_dir(), f"term-{self.id}")
        env = build_environment(self.sandbox_profile, home=str(home), tmp_dir="/tmp")
        env.setdefault("COLORTERM", "truecolor")
        argv = with_shell_integration(list(self.argv), env)
        # OSC-7 rc files live under a private tmp dir — mount them read-only
        # inside the sandbox so bash/zsh integration keeps working.
        integration = shell_integration_dir()
        spec = SpawnSpec(
            profile=self.sandbox_profile,
            argv=tuple(argv),
            cwd=self.cwd,
            workspace_root=self.cwd,
            writable_roots=(self.cwd,),
            read_only_roots=(str(integration),),
            env=env,
            # An interactive workspace terminal is the human's own project
            # shell: network is permitted (dev tools need it). Any other
            # restricted profile stays deny-by-default — grants only come
            # from remembered capability rules, never from session params.
            network=(
                "outbound" if self.sandbox_profile == "workspace" else "none"
            ),
            granted_capabilities=(
                ("net.outbound:any",) if self.sandbox_profile == "workspace" else ()
            ),
            pty=True,
            purpose="terminal",
            task_id=f"term-{self.id}",
            home=str(home),
        )
        runner = runner_for(self.sandbox_profile)
        if runner.capabilities().backend == "host":
            # A restricted terminal must never silently resolve to the
            # unrestricted backend — unavailable sandbox = honest failure.
            raise TerminalError(
                "no kernel sandbox backend available for "
                f"profile {self.sandbox_profile!r} — see provisioning status"
            )
        return runner, spec

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _read_loop(self) -> None:
        while not self.exited:
            terminal = self._terminal
            if terminal is None:
                break
            try:
                data = terminal.read(0.25)  # type: ignore[attr-defined]
            except (OSError, ValueError):
                break
            except Exception:
                break
            if data is None:
                continue
            if not data:
                self._emit_threadsafe(b"")
                break
            self._emit_threadsafe(data)

    def _emit_threadsafe(self, data: bytes) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._emit(data), loop)
            return
        if data:
            self._track_output(data)
            with self._lock:
                self.replay.append(data)

    def _track_output(self, data: bytes) -> tuple[str | None, bool]:
        """Update cwd/activity from a chunk. Returns (new_cwd, became_busy)."""
        cwd = None
        if b"\x1b]7;" in data or self._osc_buf:
            cwd, self._osc_buf = parse_osc7(self._osc_buf + data)
            if cwd:
                self.live_cwd = cwd
        became_busy = False
        now = _time.monotonic()
        if self._busy_since is None:
            self._busy_since = now
            became_busy = True
        self._last_output_at = now
        return cwd, became_busy

    async def _send_control(self, payload: dict[str, object]) -> None:
        text = json.dumps(payload)
        with self._lock:
            sinks = list(self.subscribers)
        for sink in sinks:
            send_text = getattr(sink, "send_text", None)
            if send_text is None:
                continue
            try:
                await send_text(text)
            except Exception:
                continue

    async def _activity_monitor(self) -> None:
        idle_after = activity_idle_seconds()
        notify_min = activity_notify_seconds()
        while not self.exited:
            await asyncio.sleep(0.25)
            if self._busy_since is None:
                return
            gap = _time.monotonic() - self._last_output_at
            if gap < idle_after:
                continue
            started = self._busy_since
            duration = max(0.0, self._last_output_at - started)
            self._busy_since = None
            self._busy_reported = False
            if duration >= notify_min:
                await self._send_control(
                    {"type": "activity", "state": "idle", "duration_s": int(round(duration))}
                )
            return

    async def _emit(self, data: bytes) -> None:
        if data:
            cwd, became_busy = self._track_output(data)
            with self._lock:
                self.replay.append(data)
                sinks = list(self.subscribers)
            dead: list[ByteSink] = []
            for sink in sinks:
                try:
                    await sink.send_bytes(data)
                except Exception:
                    dead.append(sink)
            if dead:
                with self._lock:
                    for sink in dead:
                        self.subscribers.discard(sink)
            if self.live_cwd and self.live_cwd != self._reported_cwd:
                self._reported_cwd = self.live_cwd
                await self._send_control({"type": "cwd", "cwd": self.live_cwd})
            if self._busy_since is not None and not self._busy_reported:
                self._busy_reported = True
                if self._activity_task is None or self._activity_task.done():
                    self._activity_task = asyncio.create_task(self._activity_monitor())
                await self._send_control({"type": "activity", "state": "busy"})
            return
        await self._mark_exit()

    async def _mark_exit(self) -> None:
        if self.exited:
            return
        self.exited = True
        terminal = self._terminal
        code = None
        if terminal is not None:
            try:
                code = terminal.exit_code()  # type: ignore[attr-defined]
            except Exception:
                code = None
            if code is None and getattr(terminal, "proc", None) is not None:
                try:
                    proc = terminal.proc  # type: ignore[attr-defined]
                    wait = getattr(proc, "wait", None)
                    if callable(wait):
                        code = proc.wait(timeout=0.2)
                except Exception:
                    code = None
        self.exit_code = code
        with self._lock:
            sinks = list(self.subscribers)
            self.subscribers.clear()
        for sink in sinks:
            send_text = getattr(sink, "send_text", None)
            if send_text is not None:
                try:
                    await send_text(
                        '{"type":"exit","code":%s}'
                        % (code if code is not None else "null")
                    )
                except Exception:
                    pass

    def write(self, data: bytes) -> None:
        if self.exited or not data:
            return
        terminal = self._terminal
        if terminal is None:
            return
        terminal.write(data)  # type: ignore[attr-defined]

    def send_signal(self, name: str) -> bool:
        if self.exited:
            return False
        terminal = self._terminal
        if terminal is None:
            return False
        return bool(terminal.send_signal(name))  # type: ignore[attr-defined]

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        terminal = self._terminal
        if terminal is not None and not self.exited:
            terminal.resize(self.rows, self.cols)  # type: ignore[attr-defined]

    def subscribe(self, sink: ByteSink) -> bytes:
        with self._lock:
            self.subscribers.add(sink)
            return self.replay.dump()

    def unsubscribe(self, sink: ByteSink) -> None:
        with self._lock:
            self.subscribers.discard(sink)

    def kill(self, timeout: float = 1.5) -> None:
        self.exited = True
        if self._activity_task is not None and not self._activity_task.done():
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._activity_task.cancel)
        terminal = self._terminal
        if terminal is not None:
            try:
                terminal.kill(timeout)  # type: ignore[attr-defined]
            except Exception:
                pass
            self.master_fd = getattr(terminal, "master_fd", -1)
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=0.5)

    def output_tail(self, max_bytes: int = 8192) -> bytes:
        """The last ``max_bytes`` of replayed output (a status/read window, not a transcript)."""
        with self._lock:
            data = self.replay.dump()
        return data[-max_bytes:] if max_bytes > 0 else b""

    def quiet_seconds(self) -> float | None:
        """Seconds since the last output chunk, or None when nothing was ever written."""
        if not self._last_output_at:
            return None
        return max(0.0, _time.monotonic() - self._last_output_at)

    def snapshot(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "cols": self.cols,
            "rows": self.rows,
            "exited": self.exited,
            "exit_code": self.exit_code,
            "cwd": self.cwd,
            "live_cwd": self.live_cwd or self.cwd,
            "shell": self.shell,
        }


class SessionManager:
    def __init__(self, argv: list[str] | None = None, *, holder: object | None = None) -> None:
        self._argv = argv
        self._sessions: dict[str, Session] = {}
        self._seq = 0
        # A ``HolderSupervisor``: None means every terminal is a direct child of this process.
        self.holder = holder

    def _holder_client(self) -> object | None:
        if self.holder is None:
            return None
        from termx.pty_holder_client import HolderError

        try:
            return self.holder.acquire(create=True)  # type: ignore[attr-defined]
        except HolderError as exc:
            raise TerminalError(str(exc)) from None

    def reattach(self) -> list[Session]:
        """Rebuild sessions for terminals a previous daemon left running in the PTY holder (replay intact)."""
        if self.holder is None:
            return []
        from termx.pty_holder_client import HolderError

        client = self.holder.acquire(create=False)  # type: ignore[attr-defined]
        if client is None:
            return []
        try:
            listing = client.list()  # type: ignore[attr-defined]
        except HolderError:
            return []
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        adopted: list[Session] = []
        for item in listing.get("sessions", []):
            sid = item.get("id")
            if not isinstance(sid, str) or sid in self._sessions:
                continue
            terminal = HeldTerminal(client, sid, int(item.get("rows", DEFAULT_ROWS)), int(item.get("cols", DEFAULT_COLS)))
            try:
                _info, replay = client.attach(terminal, None)  # type: ignore[attr-defined]
            except HolderError:
                continue
            terminal.pid = item.get("pid")
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            argv0 = str(meta.get("argv0") or "")
            self._seq += 1
            session = Session(
                id=sid,
                title=str(meta.get("title") or f"{argv0 or 'terminal'} {self._seq}"),
                created_at=float(item.get("created_at") or time()),
                cols=terminal.cols,
                rows=terminal.rows,
                argv=[argv0] if argv0 else [],
                cwd=str(meta.get("cwd") or Path.home()),
                shell=str(meta.get("shell") or default_shell()),
                sandbox_profile=str(meta.get("sandbox_profile") or "host"),
                holder=client,
            )
            session.adopt(terminal, replay, exited=not item.get("alive", True), exit_code=item.get("exit_code"))
            if loop is not None:
                session.attach(loop)
            self._sessions[sid] = session
            adopted.append(session)
        return adopted

    def create(
        self,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        title: str | None = None,
        argv: list[str] | None = None,
        cwd: str | None = None,
        shell: str | None = None,
        sandbox_profile: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Session:
        self._seq += 1
        sid = uuid.uuid4().hex[:12]
        sh = shell or default_shell()
        cmd = argv or self._argv or default_argv(sh)
        shell_name = os.path.basename(cmd[0])
        session = Session(
            id=sid,
            title=title or f"{shell_name} {self._seq}",
            created_at=time(),
            cols=cols,
            rows=rows,
            argv=cmd,
            cwd=cwd or str(Path.home()),
            shell=sh,
            sandbox_profile=sandbox_profile or "host",
            env=env,
            holder=self._holder_client(),
        )
        session.start()
        try:
            session.attach(asyncio.get_running_loop())
        except RuntimeError:
            pass
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list(self) -> list[Session]:
        return list(self._sessions.values())

    def rename(self, session_id: str, title: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        session.title = title
        return session

    def kill(self, session_id: str, timeout: float = 1.5) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.kill(timeout=timeout)
        return True

    def kill_all(self, timeout: float = 3.0, *, keep_held: bool = True) -> None:
        """End every terminal. Held terminals are only detached (they survive); ``keep_held=False`` ends them too."""
        for sid in list(self._sessions):
            session = self._sessions.get(sid)
            if keep_held and session is not None and session.durable:
                self._sessions.pop(sid, None)
                session.detach()
                continue
            self.kill(sid, timeout=timeout)
        if self.holder is not None:
            self.holder.close()  # type: ignore[attr-defined]
