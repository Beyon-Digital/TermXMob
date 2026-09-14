from __future__ import annotations

import asyncio
import json
import os
import re
import signal
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
        env["TERMX_ZDOTDIR"] = env.get("ZDOTDIR") or str(Path.home())
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
    return os.environ.get("SHELL") or "/bin/zsh"


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
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")
        argv = with_shell_integration(self.argv, env)
        try:
            terminal = spawn_terminal(argv, self.cwd, env, self.rows, self.cols)
        except TerminalError:
            raise
        self._terminal = terminal
        self.master_fd = getattr(terminal, "master_fd", -1)
        self.proc = getattr(terminal, "proc", None)
        self._reader = threading.Thread(target=self._read_loop, name=f"pty-{self.id}", daemon=True)
        self._reader.start()

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
    def __init__(self, argv: list[str] | None = None) -> None:
        self._argv = argv
        self._sessions: dict[str, Session] = {}
        self._seq = 0

    def create(
        self,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        title: str | None = None,
        argv: list[str] | None = None,
        cwd: str | None = None,
        shell: str | None = None,
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

    def kill_all(self, timeout: float = 3.0) -> None:
        for sid in list(self._sessions):
            self.kill(sid, timeout=timeout)
