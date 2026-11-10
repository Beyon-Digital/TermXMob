"""Readiness waits must keep working when the process holds many open files.

select.select() raises for descriptors numbered 1024 or higher, which the
terminal code treated as end of stream, so terminals silently died in busy
host processes.
"""
from __future__ import annotations

import os
import sys

import pytest

from termx.fdwait import wait_readable

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptors")


@pytest.fixture()
def crowded_descriptors():
    held: list[int] = []
    try:
        while True:
            read_end, write_end = os.pipe()
            held.extend((read_end, write_end))
            if read_end > 1100 and write_end > 1100:
                break
            if len(held) > 8000:
                pytest.skip("cannot open enough descriptors on this host")
        yield held
    except OSError:
        pytest.skip("descriptor limit too low to reproduce")
    finally:
        for fd in held:
            try:
                os.close(fd)
            except OSError:
                pass


def test_wait_readable_reports_data_on_a_high_descriptor(crowded_descriptors):
    read_end, write_end = crowded_descriptors[-2], crowded_descriptors[-1]
    assert read_end > 1024
    assert wait_readable(read_end, 0.01) is False
    os.write(write_end, b"x")
    assert wait_readable(read_end, 1.0) is True


def test_wait_readable_times_out_without_data():
    read_end, write_end = os.pipe()
    try:
        assert wait_readable(read_end, 0.02) is False
    finally:
        os.close(read_end)
        os.close(write_end)


def test_wait_readable_reports_eof_as_readable():
    read_end, write_end = os.pipe()
    os.close(write_end)
    try:
        assert wait_readable(read_end, 0.5) is True
    finally:
        os.close(read_end)


def test_wait_readable_raises_for_a_closed_descriptor():
    read_end, write_end = os.pipe()
    os.close(read_end)
    os.close(write_end)
    with pytest.raises((OSError, ValueError)):
        wait_readable(read_end, 0.01)


def test_a_terminal_session_works_with_descriptors_above_1024(crowded_descriptors):
    import asyncio

    from termx.sessions import SessionManager

    argv = [sys.executable, "-u", "-c", "import sys; print('READY', flush=True); print(sys.stdin.readline().strip(), flush=True)"]

    class Sink:
        def __init__(self):
            self.chunks: list[bytes] = []

        async def send_bytes(self, data: bytes) -> None:
            self.chunks.append(data)

    async def wait_for(sink: Sink, needle: bytes) -> bool:
        for _ in range(100):
            if needle in b"".join(sink.chunks):
                return True
            await asyncio.sleep(0.05)
        return False

    async def scenario() -> None:
        manager = SessionManager()
        session = manager.create(argv=argv)
        assert session.id
        sink = Sink()
        session.subscribe(sink)
        assert await wait_for(sink, b"READY")
        session.write(b"hello\n")
        assert await wait_for(sink, b"hello")
        manager.kill(session.id)

    asyncio.run(scenario())
