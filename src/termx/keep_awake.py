"""Keep the machine awake while agent work runs: a refcounted lease over the OS's own mechanism.

``acquire(reason)`` returns a token and ``release(token)`` gives it back; the OS inhibitor is held
while at least one lease is out and dropped when the count reaches zero. The mechanisms are the
platform's documented ones, none of them a power-management hack:

* macOS: ``caffeinate -i -w <host pid>``: prevents idle sleep and **exits by itself when the
  host process does**, so a crash never leaves the Mac awake forever.
* Linux: ``systemd-inhibit --what=idle:sleep`` around a tiny watcher that exits when the host
  process does (same crash property). Without systemd/logind the lease is recorded but
  ``supported`` is false: the status says so, nothing pretends.
* Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`` from a dedicated thread
  (the flag is per thread); the thread clears it on release.

A display may still sleep: only idle system sleep is inhibited.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable

WHO = "TermX"
MAX_REASON = 120


def caffeinate_command(pid: int) -> list[str]:
    return ["caffeinate", "-i", "-w", str(int(pid))]


def systemd_inhibit_command(pid: int, why: str) -> list[str]:
    watcher = 'while kill -0 "$1" 2>/dev/null; do sleep 5; done'
    return [
        "systemd-inhibit", "--what=idle:sleep", f"--who={WHO}", f"--why={why[:MAX_REASON]}", "--mode=block",
        "sh", "-c", watcher, "termx-keep-awake", str(int(pid)),
    ]


class Backend:
    """One OS mechanism. ``start`` raises OSError/RuntimeError when it cannot hold the inhibitor."""

    name = "none"
    supported = False

    def start(self, why: str) -> None:  # pragma: no cover - interface
        raise RuntimeError("unsupported platform")

    def stop(self) -> None:  # pragma: no cover - interface
        pass

    def alive(self) -> bool:
        return False


class _ProcessBackend(Backend):
    supported = True

    def __init__(self, popen: Callable[..., Any] | None = None, pid: int | None = None) -> None:
        self._popen = popen or subprocess.Popen
        self._pid = pid if pid is not None else os.getpid()
        self._proc: Any = None

    def command(self, why: str) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def start(self, why: str) -> None:
        if shutil.which(self.command(why)[0]) is None and self._popen is subprocess.Popen:
            raise RuntimeError(f"{self.command(why)[0]} is not installed")
        self._proc = self._popen(
            self.command(why), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True,
        )
        time.sleep(0.05)
        if self._proc.poll() is not None:  # e.g. systemd-inhibit without logind exits at once
            code = self._proc.poll()
            self._proc = None
            raise RuntimeError(f"{self.name} exited immediately with status {code}")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 - best effort; the -w/watcher property still ends it with the host
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


class CaffeinateBackend(_ProcessBackend):
    name = "caffeinate"

    def command(self, why: str) -> list[str]:
        return caffeinate_command(self._pid)


class SystemdInhibitBackend(_ProcessBackend):
    name = "systemd-inhibit"

    def command(self, why: str) -> list[str]:
        return systemd_inhibit_command(self._pid, why)


class WindowsBackend(Backend):
    name = "SetThreadExecutionState"
    supported = True
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

    def __init__(self, set_state: Callable[[int], int] | None = None) -> None:
        self._set_state = set_state
        self._thread: threading.Thread | None = None
        self._release = threading.Event()
        self._ready = threading.Event()
        self._ok = False

    def _resolve(self) -> Callable[[int], int]:
        if self._set_state is not None:
            return self._set_state
        import ctypes

        return ctypes.windll.kernel32.SetThreadExecutionState  # type: ignore[attr-defined]

    def start(self, why: str) -> None:
        function = self._resolve()
        self._release.clear()
        self._ready.clear()

        def hold() -> None:
            self._ok = bool(function(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED))
            self._ready.set()
            self._release.wait()
            function(self.ES_CONTINUOUS)  # clears the requirement for this thread

        self._thread = threading.Thread(target=hold, name="keep-awake", daemon=True)
        self._thread.start()
        self._ready.wait(2)
        if not self._ok:
            self.stop()
            raise RuntimeError("SetThreadExecutionState was refused")

    def stop(self) -> None:
        self._release.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def backend_for(platform: str | None = None) -> Backend:
    platform = platform or sys.platform
    if platform == "darwin":
        return CaffeinateBackend()
    if platform.startswith("linux"):
        return SystemdInhibitBackend()
    if platform == "win32":
        return WindowsBackend()
    return Backend()


class KeepAwake:
    """Refcounted lease. Thread-safe; the OS inhibitor exists exactly while ``count > 0``."""

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend or backend_for()
        self._lock = threading.Lock()
        self._leases: dict[str, str] = {}
        self._held = False
        self.error: str | None = None
        atexit.register(self.close)

    def acquire(self, reason: str) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._leases[token] = str(reason)[:MAX_REASON]
            self._ensure_locked()
        return token

    def release(self, token: str) -> bool:
        with self._lock:
            if self._leases.pop(token, None) is None:
                return False
            if not self._leases and self._held:
                self._stop_locked()
        return True

    def refresh(self) -> None:
        """Restart the inhibitor if it died while leases are out (e.g. caffeinate was killed)."""
        with self._lock:
            if self._leases:
                self._ensure_locked()

    def _ensure_locked(self) -> None:
        if self._held and self.backend.alive():
            return
        if self._held:
            self._stop_locked()
        why = "; ".join(sorted(set(self._leases.values())))
        try:
            self.backend.start(why or "agent work")
        except (OSError, RuntimeError, ValueError) as exc:
            self.error = str(exc)[:200]
            self._held = False
            return
        self.error = None
        self._held = True

    def _stop_locked(self) -> None:
        try:
            self.backend.stop()
        finally:
            self._held = False

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._leases)

    def reasons(self) -> list[str]:
        with self._lock:
            return sorted(set(self._leases.values()))

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self._held and self.backend.alive(),
                "supported": self.backend.supported,
                "backend": self.backend.name if self.backend.supported else None,
                "reasons": sorted(set(self._leases.values())),
                "count": len(self._leases),
                "error": self.error,
            }

    def close(self) -> None:
        with self._lock:
            self._leases.clear()
            if self._held:
                self._stop_locked()
