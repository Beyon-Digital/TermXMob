from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Protocol

REPLAY_MAX_BYTES = 256_000
DEFAULT_COLS = 80
DEFAULT_ROWS = 24


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


def _winsize(rows: int, cols: int) -> bytes:
    return struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, _winsize(rows, cols))


def default_shell() -> str:
    return os.environ.get("SHELL") or "/bin/zsh"


def default_argv(shell: str | None = None) -> list[str]:
    sh = shell or default_shell()
    name = os.path.basename(sh)
    if name in {"zsh", "bash", "sh"}:
        return [sh, "-l"]
    return [sh]


def _preexec_controlling_tty(slave_fd: int) -> None:
    os.setsid()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
    except OSError:
        pass
    try:
        attrs = termios.tcgetattr(slave_fd)
        attrs[3] |= termios.ISIG
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
    except termios.error:
        pass


SIGNALS = {
    "int": signal.SIGINT,
    "term": signal.SIGTERM,
    "hup": signal.SIGHUP,
    "kill": signal.SIGKILL,
}


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
    proc: subprocess.Popen[bytes] | None = None
    replay: ReplayBuffer = field(default_factory=ReplayBuffer)
    subscribers: set[ByteSink] = field(default_factory=set)
    exited: bool = False
    exit_code: int | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _reader: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        master, slave = pty.openpty()
        set_winsize(master, self.rows, self.cols)
        try:
            set_winsize(slave, self.rows, self.cols)
        except OSError:
            pass
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("COLORTERM", "truecolor")
        self.proc = subprocess.Popen(
            self.argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=self.cwd,
            env=env,
            preexec_fn=lambda: _preexec_controlling_tty(slave),
        )
        os.close(slave)
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.master_fd = master
        self._reader = threading.Thread(target=self._read_loop, name=f"pty-{self.id}", daemon=True)
        self._reader.start()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _read_loop(self) -> None:
        while not self.exited:
            fd = self.master_fd
            if fd < 0:
                break
            try:
                ready, _, _ = select.select([fd], [], [], 0.25)
            except (OSError, ValueError):
                break
            if not ready:
                if self.proc is not None and self.proc.poll() is not None:
                    try:
                        leftover = os.read(fd, 8192)
                    except OSError:
                        leftover = b""
                    if leftover:
                        self._emit_threadsafe(leftover)
                    self._emit_threadsafe(b"")
                    break
                continue
            try:
                data = os.read(fd, 8192)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            self._emit_threadsafe(data)
            if not data:
                break

    def _emit_threadsafe(self, data: bytes) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._emit(data), loop)
            return
        if data:
            with self._lock:
                self.replay.append(data)

    async def _emit(self, data: bytes) -> None:
        if data:
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
            return
        await self._mark_exit()

    async def _mark_exit(self) -> None:
        if self.exited:
            return
        self.exited = True
        code = None
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                try:
                    code = self.proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    code = self.proc.poll()
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
        if self.exited or self.master_fd < 0 or not data:
            return
        os.write(self.master_fd, data)

    def send_signal(self, name: str) -> bool:
        if self.exited or self.proc is None:
            return False
        sig = SIGNALS.get(name.lower())
        if sig is None:
            return False
        if self.proc.poll() is not None:
            return False
        try:
            os.killpg(os.getpgid(self.proc.pid), sig)
            return True
        except OSError:
            try:
                self.proc.send_signal(sig)
                return True
            except OSError:
                return False

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        if self.master_fd >= 0 and not self.exited:
            try:
                set_winsize(self.master_fd, self.rows, self.cols)
            except OSError:
                pass

    def subscribe(self, sink: ByteSink) -> bytes:
        with self._lock:
            self.subscribers.add(sink)
            return self.replay.dump()

    def unsubscribe(self, sink: ByteSink) -> None:
        with self._lock:
            self.subscribers.discard(sink)

    def kill(self, timeout: float = 1.5) -> None:
        self.exited = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                try:
                    proc.terminate()
                except OSError:
                    pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        fd = self.master_fd
        self.master_fd = -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
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
