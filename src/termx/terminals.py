from __future__ import annotations

import os
import signal
import struct
import subprocess
import sys
from typing import Any


class TerminalError(RuntimeError):
    pass


def _winsize(rows: int, cols: int) -> bytes:
    return struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)


def _preexec_controlling_tty(slave_fd: int) -> None:
    import fcntl
    import termios

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


def set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, _winsize(rows, cols))


class PosixTerminal:
    """PTY-backed terminal process built on pty.openpty + subprocess.Popen."""

    def __init__(self, argv: list[str], cwd: str, env: dict[str, str], rows: int, cols: int) -> None:
        import fcntl
        import pty

        self._fcntl = fcntl
        self.master_fd = -1
        self._exited = False
        master, slave = pty.openpty()
        try:
            set_winsize(master, rows, cols)
            try:
                set_winsize(slave, rows, cols)
            except OSError:
                pass
            self.proc = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                preexec_fn=lambda: _preexec_controlling_tty(slave),
            )
        finally:
            os.close(slave)
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.master_fd = master

    def read(self, timeout: float = 0.25) -> bytes | None:
        """Return bytes (b'' on EOF), or None when idle."""
        import select

        fd = self.master_fd
        if fd < 0:
            return b""
        try:
            ready, _, _ = select.select([fd], [], [], timeout)
        except (OSError, ValueError):
            return b""
        if not ready:
            if self.proc.poll() is not None:
                try:
                    return os.read(fd, 8192)
                except OSError:
                    return b""
            return None
        try:
            data = os.read(fd, 8192)
        except BlockingIOError:
            return None
        except OSError:
            return b""
        return data

    def write(self, data: bytes) -> None:
        if self._exited or self.master_fd < 0 or not data:
            return
        try:
            os.write(self.master_fd, data)
        except OSError:
            return

    def send_signal(self, name: str) -> bool:
        from termx.sessions import SIGNALS

        if self._exited or self.proc.poll() is not None:
            return False
        sig = SIGNALS.get(name.lower())
        if sig is None:
            return False
        try:
            os.killpg(self._signal_pgrp(), sig)
            return True
        except OSError:
            try:
                self.proc.send_signal(sig)
                return True
            except OSError:
                return False

    def _signal_pgrp(self) -> int:
        """Foreground process group of the pty.

        Interactive jobs run in their own foreground group on the pty, so
        signals must target it to reach them. Falls back to the session
        leader's group when the foreground group is unavailable.
        """
        try:
            pgrp = os.tcgetpgrp(self.master_fd)
        except OSError:
            pgrp = 0
        if pgrp > 0:
            return pgrp
        return os.getpgid(self.proc.pid)

    def resize(self, rows: int, cols: int) -> None:
        if self.master_fd >= 0 and not self._exited:
            try:
                set_winsize(self.master_fd, rows, cols)
            except OSError:
                pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def exit_code(self) -> int | None:
        return self.proc.poll()

    def kill(self, timeout: float = 1.5) -> None:
        self._exited = True
        proc = self.proc
        if proc.poll() is None:
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


class _WinProcFacade:
    """Minimal subprocess.Popen-compatible surface for session snapshots/tests."""

    def __init__(self, terminal: WinTerminal) -> None:
        self._terminal = terminal

    @property
    def pid(self) -> int | None:
        return self._terminal.pid

    def poll(self) -> int | None:
        return self._terminal.exit_code()


class WinTerminal:
    """ConPTY terminal process backed by pywinpty."""

    def __init__(self, argv: list[str], cwd: str, env: dict[str, str], rows: int, cols: int) -> None:
        try:
            import winpty
        except ImportError as exc:
            raise TerminalError("pywinpty is required for terminal sessions on Windows") from exc
        try:
            self._proc = winpty.PtyProcess.spawn(list(argv), cwd=cwd, env=env, dimensions=(rows, cols))
        except Exception as exc:
            raise TerminalError(f"failed to spawn terminal: {exc}") from exc
        self.master_fd = -1
        self._exited = False
        self.proc = _WinProcFacade(self)

    @property
    def pid(self) -> int | None:
        value = getattr(self._proc, "pid", None)
        return int(value) if isinstance(value, int) else None

    def read(self, timeout: float = 0.25) -> bytes | None:
        try:
            data = self._proc.read(8192)
        except (EOFError, OSError, ValueError):
            return b""
        except Exception:
            return b""
        if isinstance(data, str):
            return data.encode("utf-8", "surrogateescape")
        return data

    def write(self, data: bytes) -> None:
        self._write_raw(data)

    def _write_raw(self, data: bytes) -> None:
        """pywinpty 3.x expects str; pywinpty 2.x expects bytes. Support both."""
        if self._exited or not data:
            return
        try:
            self._proc.write(data.decode("utf-8", "surrogateescape"))
            return
        except TypeError:
            pass
        except Exception:
            return
        try:
            self._proc.write(data)
        except Exception:
            return

    def send_signal(self, name: str) -> bool:
        name = name.lower()
        if self._exited or not self.alive():
            return False
        if name == "int":
            self._write_raw(b"\x03")
            return True
        if name in {"term", "hup"}:
            return self._terminate(force=False)
        if name == "kill":
            return self._terminate(force=True)
        return False

    def resize(self, rows: int, cols: int) -> None:
        if self._exited:
            return
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:
            return

    def alive(self) -> bool:
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def exit_code(self) -> int | None:
        if self.alive():
            return None
        status = getattr(self._proc, "exitstatus", None)
        return int(status) if isinstance(status, int) else None

    def _terminate(self, force: bool) -> bool:
        try:
            self._proc.terminate(force=force)
            return True
        except Exception:
            return False

    def kill(self, timeout: float = 1.5) -> None:
        self._exited = True
        if self.alive():
            self._terminate(force=True)
            deadline = timeout
            import time

            while deadline > 0 and self.alive():
                time.sleep(0.05)
                deadline -= 0.05
        try:
            self._proc.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def spawn_terminal(argv: list[str], cwd: str, env: dict[str, str], rows: int, cols: int) -> PosixTerminal | WinTerminal:
    if sys.platform == "win32":
        return WinTerminal(argv, cwd, env, rows, cols)
    if os.name != "posix":
        raise TerminalError(f"terminal sessions are not supported on {sys.platform}")
    return PosixTerminal(argv, cwd, env, rows, cols)


def terminal_proc(terminal: Any) -> Any:
    return terminal.proc
