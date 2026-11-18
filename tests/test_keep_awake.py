"""Keep-awake lease: refcount semantics with a fake backend and the exact command per OS."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

from termx import keep_awake as K


class FakeBackend(K.Backend):
    name = "fake"
    supported = True

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.starts: list[str] = []
        self.stops = 0
        self.running = False

    def start(self, why: str) -> None:
        if self.fail:
            raise RuntimeError("no inhibitor available")
        self.starts.append(why)
        self.running = True

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    def alive(self) -> bool:
        return self.running


@pytest.fixture
def lease():
    awake = K.KeepAwake(FakeBackend())
    yield awake
    awake.close()


def test_the_inhibitor_is_held_exactly_while_a_lease_is_out(lease):
    backend = lease.backend
    assert not backend.running and lease.status()["active"] is False
    first = lease.acquire("agent task")
    assert backend.running and lease.count == 1 and lease.status()["active"] is True
    second = lease.acquire("cli session")
    assert backend.starts == ["agent task"] and lease.count == 2, "a second lease reuses the running inhibitor"
    assert lease.release(first) and backend.running and lease.count == 1
    assert lease.release(second) and not backend.running and backend.stops == 1 and lease.count == 0


def test_release_is_idempotent_and_unknown_tokens_do_nothing(lease):
    token = lease.acquire("x")
    assert lease.release(token) is True
    assert lease.release(token) is False and lease.release("nonsense") is False
    assert lease.backend.stops == 1, "the inhibitor is stopped once, not once per release call"


def test_reasons_are_reported_and_deduplicated(lease):
    lease.acquire("owner setting")
    lease.acquire("agent task")
    lease.acquire("agent task")
    status = lease.status()
    assert status["reasons"] == ["agent task", "owner setting"] and status["count"] == 3
    assert lease.backend.starts == ["owner setting"], "the first lease names the inhibitor; later ones join it"


def test_a_dead_inhibitor_is_restarted_while_leases_are_out(lease):
    lease.acquire("agent task")
    lease.backend.running = False  # caffeinate was killed
    assert lease.status()["active"] is False
    lease.refresh()
    assert lease.backend.running and len(lease.backend.starts) == 2


def test_refresh_without_leases_starts_nothing(lease):
    lease.refresh()
    assert lease.backend.starts == []


def test_a_backend_that_cannot_start_is_reported_not_hidden():
    awake = K.KeepAwake(FakeBackend(fail=True))
    token = awake.acquire("agent task")
    status = awake.status()
    assert status["active"] is False and status["count"] == 1 and "no inhibitor" in status["error"]
    assert awake.release(token) is True and awake.status()["error"] == "no inhibitor available"
    awake.close()


def test_an_unsupported_platform_says_so():
    awake = K.KeepAwake(K.backend_for("freebsd"))
    awake.acquire("agent task")
    status = awake.status()
    assert status["supported"] is False and status["backend"] is None and status["active"] is False and status["error"]
    awake.close()


def test_close_releases_everything(lease):
    lease.acquire("a")
    lease.acquire("b")
    lease.close()
    assert not lease.backend.running and lease.count == 0


def test_concurrent_acquire_and_release_keep_the_count_consistent(lease):
    tokens: list[str] = []
    lock = threading.Lock()

    def work() -> None:
        for _ in range(100):
            token = lease.acquire("t")
            with lock:
                tokens.append(token)
        for _ in range(100):
            with lock:
                token = tokens.pop()
            lease.release(token)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert lease.count == 0 and not lease.backend.running


# -- commands per OS --------------------------------------------------------------------


def test_caffeinate_command_is_tied_to_the_host_pid():
    assert K.caffeinate_command(4321) == ["caffeinate", "-i", "-w", "4321"]
    assert K.CaffeinateBackend(pid=77).command("why") == ["caffeinate", "-i", "-w", "77"]


def test_systemd_inhibit_command_blocks_idle_and_sleep_and_watches_the_host_pid():
    command = K.systemd_inhibit_command(99, "agent task running")
    assert command[:5] == ["systemd-inhibit", "--what=idle:sleep", "--who=TermX", "--why=agent task running", "--mode=block"]
    assert command[5:7] == ["sh", "-c"] and command[-1] == "99" and "kill -0" in command[7]
    assert "sleep infinity" not in " ".join(command), "the watcher must end with the host, not outlive it"


def test_why_text_is_bounded_and_a_pid_cannot_inject_arguments():
    assert len(K.systemd_inhibit_command(1, "x" * 1000)[3]) <= len("--why=") + K.MAX_REASON
    assert K.caffeinate_command(True) == ["caffeinate", "-i", "-w", "1"]
    with pytest.raises(ValueError):
        K.caffeinate_command("1; rm -rf /")  # type: ignore[arg-type]


@pytest.mark.parametrize("platform, expected", [("darwin", K.CaffeinateBackend), ("linux", K.SystemdInhibitBackend), ("linux2", K.SystemdInhibitBackend), ("win32", K.WindowsBackend)])
def test_backend_is_chosen_per_platform(platform, expected):
    assert type(K.backend_for(platform)) is expected


def test_process_backends_launch_the_command_and_stop_it():
    launched: list[list[str]] = []

    class Proc:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    def popen(command, **kwargs):
        launched.append(command)
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["close_fds"] is True
        return Proc()

    backend = K.CaffeinateBackend(popen=popen, pid=12)
    awake = K.KeepAwake(backend)
    token = awake.acquire("agent task")
    assert launched == [["caffeinate", "-i", "-w", "12"]] and awake.status()["active"]
    awake.release(token)
    assert not backend.alive()
    awake.close()


def test_a_process_backend_that_exits_at_once_is_an_error():
    class Dead:
        def poll(self):
            return 1

    awake = K.KeepAwake(K.SystemdInhibitBackend(popen=lambda *a, **k: Dead(), pid=5))
    awake.acquire("agent task")
    assert awake.status()["active"] is False and "exited immediately" in awake.status()["error"]
    awake.close()


def test_missing_executable_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    awake = K.KeepAwake(K.CaffeinateBackend())
    awake.acquire("x")
    assert "not installed" in awake.status()["error"]
    awake.close()


def test_windows_backend_sets_and_clears_the_execution_state_from_one_thread():
    calls: list[tuple[int, int]] = []

    def set_state(flags: int) -> int:
        calls.append((flags, threading.get_ident()))
        return 1

    backend = K.WindowsBackend(set_state=set_state)
    awake = K.KeepAwake(backend)
    token = awake.acquire("agent task")
    assert calls[0][0] == K.WindowsBackend.ES_CONTINUOUS | K.WindowsBackend.ES_SYSTEM_REQUIRED
    assert awake.status()["active"] is True and awake.status()["backend"] == "SetThreadExecutionState"
    awake.release(token)
    assert calls[-1][0] == K.WindowsBackend.ES_CONTINUOUS
    assert calls[0][1] == calls[-1][1] != threading.get_ident(), "the per-thread flag is set and cleared by the same helper thread"
    awake.close()


def test_windows_backend_refusal_is_reported():
    awake = K.KeepAwake(K.WindowsBackend(set_state=lambda flags: 0))
    awake.acquire("x")
    assert "refused" in awake.status()["error"] and awake.status()["active"] is False
    awake.close()


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("caffeinate") is None, reason="needs macOS caffeinate")
def test_real_caffeinate_holds_and_exits_with_its_host():
    """caffeinate -w <pid>: when the watched process ends the inhibitor ends with it (the crash property)."""
    watched = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        backend = K.CaffeinateBackend(pid=watched.pid)
        backend.start("test")
        assert backend.alive()
        proc = backend._proc
        watched.kill()
        watched.wait()
        deadline = time.monotonic() + 5
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None, "caffeinate must exit when the watched host process dies"
        backend.stop()
    finally:
        if watched.poll() is None:
            watched.kill()


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("pmset") is None, reason="needs macOS pmset")
def test_real_lease_is_visible_to_the_power_manager():
    awake = K.KeepAwake(K.CaffeinateBackend())
    token = awake.acquire("pytest")
    try:
        assert awake.status()["active"] is True
        out = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True).stdout
        assert "caffeinate" in out and "PreventUserIdleSystemSleep" in out
    finally:
        awake.release(token)
        awake.close()
    time.sleep(0.5)
    assert os.getpid() > 0
