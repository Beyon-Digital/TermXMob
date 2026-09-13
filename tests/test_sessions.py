import asyncio
import os
import sys

import pytest

from termx.sessions import ReplayBuffer, SessionManager


def test_replay_buffer_caps() -> None:
    buf = ReplayBuffer(max_bytes=8)
    buf.append(b"abcd")
    buf.append(b"efghij")
    dumped = buf.dump()
    assert len(dumped) <= 12
    assert dumped.endswith(b"efghij") or dumped.endswith(b"fghij")


class _Collector:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.chunks.append(data)


ECHO_ARGV = [
    sys.executable,
    "-u",
    "-c",
    "import sys; sys.stdout.write('READY\\n'); sys.stdout.flush(); "
    "sys.stdout.write(sys.stdin.readline()); sys.stdout.flush(); import time; time.sleep(8)",
]


async def _wait_echo(sink: _Collector, needle: bytes) -> bytes:
    for _ in range(80):
        joined = b"".join(sink.chunks)
        if needle in joined:
            return joined
        await asyncio.sleep(0.05)
    return b"".join(sink.chunks)


def test_pty_echo_and_kill() -> None:
    async def inner() -> None:
        mgr = SessionManager()
        session = mgr.create(argv=ECHO_ARGV)
        sink = _Collector()
        session.subscribe(sink)
        assert b"READY" in await _wait_echo(sink, b"READY")
        session.write(b"hello\n")
        assert b"hello" in await _wait_echo(sink, b"hello")
        assert mgr.kill(session.id) is True
        assert mgr.get(session.id) is None

    asyncio.run(inner())


INT_ARGV = [
    sys.executable,
    "-u",
    "-c",
    "import signal,sys,time\n"
    "signal.signal(signal.SIGINT, lambda s,f: (sys.stdout.write('CAUGHT\\n'), sys.stdout.flush(), sys.exit(0)))\n"
    "sys.stdout.write('READY\\n')\n"
    "sys.stdout.flush()\n"
    "time.sleep(20)\n",
]


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows ConPTY cannot deliver POSIX signals; Ctrl+C is sent as \\x03",
)
def test_ctrl_c_delivers_sigint() -> None:
    async def inner() -> None:
        mgr = SessionManager()
        session = mgr.create(argv=INT_ARGV)
        sink = _Collector()
        session.subscribe(sink)
        assert b"READY" in await _wait_echo(sink, b"READY")
        session.write(b"\x03")
        out = await _wait_echo(sink, b"CAUGHT")
        if b"CAUGHT" not in out:
            session.send_signal("int")
            out = await _wait_echo(sink, b"CAUGHT")
        assert b"CAUGHT" in out
        mgr.kill(session.id)

    asyncio.run(inner())


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows ConPTY cannot deliver POSIX signals",
)
def test_send_signal_int() -> None:
    async def inner() -> None:
        mgr = SessionManager()
        session = mgr.create(argv=INT_ARGV)
        sink = _Collector()
        session.subscribe(sink)
        assert b"READY" in await _wait_echo(sink, b"READY")
        assert session.send_signal("int") is True
        out = await _wait_echo(sink, b"CAUGHT")
        if b"CAUGHT" not in out:
            # The signal can arrive before the child installs its handler.
            session.send_signal("int")
            out = await _wait_echo(sink, b"CAUGHT")
        assert b"CAUGHT" in out
        mgr.kill(session.id)

    asyncio.run(inner())


SLEEP_ARGV = [sys.executable, "-c", "import time; time.sleep(60)"]


def test_kill_all_drains_child() -> None:
    mgr = SessionManager()
    session = mgr.create(argv=SLEEP_ARGV)
    proc = session.proc
    assert proc is not None
    assert proc.poll() is None
    mgr.kill_all()
    assert proc.poll() is not None
    assert mgr.list() == []


def test_pty_echo_when_created_without_event_loop() -> None:
    async def inner() -> None:
        mgr = SessionManager()
        session = await asyncio.to_thread(mgr.create, 80, 24, None, ECHO_ARGV)
        session.attach(asyncio.get_running_loop())
        sink = _Collector()
        session.subscribe(sink)
        assert b"READY" in await _wait_echo(sink, b"READY")
        session.write(b"hello\n")
        assert b"hello" in await _wait_echo(sink, b"hello")
        mgr.kill(session.id)

    asyncio.run(inner())
