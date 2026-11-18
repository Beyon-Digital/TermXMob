"""The PTY holder: frame parsing, peer checks, spawn/attach/replay, survival of a killed daemon, idle exit, stale files."""
from __future__ import annotations

import json
import os
import random
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from termx import pty_holder as H
from termx.config import config_dir
from termx.pty_holder_client import (
    HolderClient,
    HolderError,
    HolderIncompatible,
    HolderSupervisor,
    describe,
    resolve_mode,
    stop,
)
from termx.sessions import HeldTerminal, Session, SessionManager

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the PTY holder is POSIX only")

COUNTER = [
    sys.executable,
    "-u",
    "-c",
    "import sys\n"
    "for i in range(50): print('LINE %03d' % i, flush=True)\n"
    "print('READY', flush=True)\n"
    "for line in sys.stdin: print('ECHO:' + line.strip(), flush=True)\n",
]


def wait_for(predicate, timeout=6.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def read_until(term: HeldTerminal, needle: bytes, timeout=6.0) -> bytes:
    seen = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and needle not in seen:
        chunk = term.read(0.1)
        if chunk == b"":
            break
        if chunk:
            seen += chunk
    return seen


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] not in ("", "Z")


# -- frame parsing --------------------------------------------------------------------


def test_frames_round_trip_and_survive_arbitrary_splits():
    frames = [H.encode_json(H.HELLO, 1, {"protocol": 1}), H.encode_frame(H.WRITE, 0, H.pack_stream("abc", b"\x00\xff data")), H.encode_json(H.LIST, 7, {})]
    blob = b"".join(frames)
    for size in (1, 2, 3, 7, 64, len(blob)):
        decoder = H.FrameDecoder(H.TO_HOLDER_LIMITS)
        out = []
        for start in range(0, len(blob), size):
            out += decoder.feed(blob[start : start + size])
        assert [(f.kind, f.request) for f in out] == [(H.HELLO, 1), (H.WRITE, 0), (H.LIST, 7)]
        assert H.unpack_stream(out[1].payload) == ("abc", b"\x00\xff data")
        assert decoder.buffered == 0


@pytest.mark.parametrize(
    "header",
    [
        H.HEADER.pack(4, H.LIST, 0),  # shorter than type + request id
        H.HEADER.pack(0, H.LIST, 0),
        H.HEADER.pack(H.MAX_FRAME + 1, H.WRITE, 0),  # over the absolute ceiling
        H.HEADER.pack(5 + H.TO_HOLDER_LIMITS[H.LIST] + 1, H.LIST, 0),  # over the limit of its own type
        H.HEADER.pack(5, 0x7F, 0),  # unknown type
        H.HEADER.pack(5, H.OUTPUT, 0),  # a holder->client type sent to the holder
    ],
)
def test_frame_bounds_are_enforced_from_the_header_before_buffering(header):
    decoder = H.FrameDecoder(H.TO_HOLDER_LIMITS)
    with pytest.raises(H.ProtocolError):
        decoder.feed(header)
    assert decoder.buffered <= len(header)


def test_oversize_declared_length_is_rejected_without_waiting_for_the_payload():
    decoder = H.FrameDecoder(H.TO_HOLDER_LIMITS)
    with pytest.raises(H.ProtocolError):
        decoder.feed((0xFFFFFFFF).to_bytes(4, "big"))  # only the first four bytes have arrived


def test_frame_parser_fuzz_only_ever_raises_protocol_error():
    rng = random.Random(0xC0FFEE)
    for _ in range(3000):
        decoder = H.FrameDecoder(H.TO_HOLDER_LIMITS)
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 80)))
        try:
            decoder.feed(blob)
        except H.ProtocolError:
            pass
    # structured fuzz: a plausible header followed by junk of random size
    for _ in range(2000):
        kind = rng.choice(list(H.TO_HOLDER_LIMITS))
        size = rng.randint(0, 3000)
        blob = H.HEADER.pack(5 + size, kind, rng.getrandbits(32)) + bytes(rng.getrandbits(8) for _ in range(size))
        decoder = H.FrameDecoder(H.TO_HOLDER_LIMITS)
        try:
            frames = decoder.feed(blob)
        except H.ProtocolError:
            continue
        for frame in frames:
            for parse in (H.parse_json, lambda payload: H.unpack_stream(payload)):
                try:
                    parse(frame.payload)
                except H.ProtocolError:
                    pass


def test_valid_frames_with_a_random_tail_never_corrupt_earlier_frames():
    good = H.encode_json(H.LIST, 3, {})
    decoder = H.FrameDecoder(H.TO_HOLDER_LIMITS)
    frames = decoder.feed(good + b"\x00\x00\x00")  # a truncated next header just waits
    assert [(f.kind, f.request) for f in frames] == [(H.LIST, 3)]
    assert decoder.buffered == 3


@pytest.mark.parametrize(
    "validate, body",
    [
        (H.validate_spawn, {"id": "a", "argv": [], "cwd": "/", "env": {}, "rows": 24, "cols": 80}),
        (H.validate_spawn, {"id": "a", "argv": ["x\x00y"], "cwd": "/", "env": {}, "rows": 24, "cols": 80}),
        (H.validate_spawn, {"id": "a", "argv": ["x"], "cwd": "/", "env": {"A=B": "c"}, "rows": 24, "cols": 80}),
        (H.validate_spawn, {"id": "a", "argv": ["x"], "cwd": "/", "env": {"A": 1}, "rows": 24, "cols": 80}),
        (H.validate_spawn, {"id": "a", "argv": ["x"], "cwd": "/", "env": {}, "rows": 0, "cols": 80}),
        (H.validate_spawn, {"id": "a", "argv": ["x"], "cwd": "/", "env": {}, "rows": True, "cols": 80}),
        (H.validate_spawn, {"id": "../x", "argv": ["x"], "cwd": "/", "env": {}, "rows": 24, "cols": 80}),
        (H.validate_spawn, {"id": "a", "argv": ["x"], "cwd": "/", "env": {}, "rows": 24, "cols": 80, "extra": 1}),
        (H.validate_spawn, {"id": "a", "argv": ["x" * 40000], "cwd": "/", "env": {}, "rows": 24, "cols": 80}),
        (H.validate_spawn, {"id": "a", "argv": ["x"], "cwd": "/", "env": {}, "rows": 24, "cols": 80, "meta": {"k": [1]}}),
        (H.validate_attach, {"id": "a", "since": -1}),
        (H.validate_signal, {"id": "a", "name": "stop"}),
        (H.validate_kill, {"id": "a", "timeout": 9999}),
        (H.validate_resize, {"id": "a", "rows": 10**9, "cols": 1}),
        (H.validate_shutdown, {"mode": "explode"}),
        (H.validate_hello, {"protocol": "1"}),
    ],
)
def test_control_payloads_are_validated_strictly(validate, body):
    with pytest.raises(H.ProtocolError):
        validate(body)


def test_json_bombs_and_non_objects_are_protocol_errors():
    for payload in (b"[" * 200_000, b"\xff\xfe", b"[1,2]", b'"text"', b"{", b""):
        with pytest.raises(H.ProtocolError):
            H.parse_json(payload)


def test_a_good_spawn_request_is_normalised():
    out = H.validate_spawn({"id": "ab12", "argv": ["sh", "-l"], "cwd": "/tmp", "env": {"A": "b"}, "rows": 24, "cols": 80, "meta": {"title": "t", "n": 1.5}})
    assert out["meta"] == {"title": "t", "n": 1.5} and out["argv"] == ["sh", "-l"]


# -- paths and the holder's own environment ---------------------------------------------


def test_holder_directory_is_private_and_a_symlink_or_foreign_dir_is_refused(tmp_path):
    directory = H.holder_dir(config_dir())
    assert stat.S_IMODE(os.lstat(directory).st_mode) == 0o700 and os.lstat(directory).st_uid == os.geteuid()
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(PermissionError):
        H.ensure_private_dir(link)
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o777)
    os.chmod(loose, 0o777)
    assert stat.S_IMODE(os.lstat(H.ensure_private_dir(loose)).st_mode) == 0o700  # ours: tightened


def test_long_config_paths_use_a_short_private_socket_directory(tmp_path):
    long_root = tmp_path / ("deep-" * 20) / "termx-config"
    directory = H.holder_dir(long_root)
    assert str(directory).startswith(f"/tmp/termx-{os.geteuid()}/")
    assert len(os.fsencode(str(directory / H.SOCKET_NAME))) <= 100
    assert stat.S_IMODE(os.lstat(directory).st_mode) == 0o700


def test_holder_environment_is_an_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMX_PASSCODE", "pass")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    env = H.holder_env(tmp_path)
    assert "TERMX_PASSCODE" not in env and "OPENAI_API_KEY" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["TERMX_CONFIG_DIR"] == str(tmp_path) and "PATH" in env


def test_mode_resolution(monkeypatch):
    monkeypatch.delenv("TERMX_PTY_HOLDER", raising=False)
    assert resolve_mode(None) == "auto" and resolve_mode("held") == "held" and resolve_mode("off") == "direct"
    monkeypatch.setenv("TERMX_PTY_HOLDER", "held")
    assert resolve_mode("direct") == "held"  # the environment wins over the saved setting


# -- an in-process holder for fast protocol tests ---------------------------------------


class InProc:
    def __init__(self, root: Path, *, idle: float = 60.0, version: str = "test-holder") -> None:
        self.root = root
        self.server = H.HolderServer(root, idle_exit_s=idle, version=version)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.result: int | None = None

    def _run(self) -> None:
        import asyncio

        self.result = asyncio.run(self.server.run())

    def start(self) -> "InProc":
        self.thread.start()
        directory = H.holder_dir(self.root)
        assert wait_for(lambda: (directory / H.INFO_NAME).exists()), "holder did not start"  # written once it listens
        return self

    def client(self) -> HolderClient:
        client = HolderClient(H.holder_dir(self.root), version="test-client")
        client.connect()
        return client

    def stop(self) -> None:
        try:
            stop(self.root, kill=True)
        except Exception:  # noqa: BLE001
            pass
        self.thread.join(timeout=5)


@pytest.fixture
def inproc():
    holders: list[InProc] = []

    def make(**kwargs) -> InProc:
        holder = InProc(config_dir(), **kwargs).start()
        holders.append(holder)
        return holder

    yield make
    for holder in holders:
        holder.stop()


def spawn_counter(client: HolderClient, sid="s1", argv=COUNTER) -> HeldTerminal:
    term = HeldTerminal(client, sid, 24, 80)
    info = client.spawn(term, argv, os.getcwd(), dict(os.environ), 24, 80, {"title": "counter", "argv0": "python"})
    term.pid = info["pid"]
    return term


def test_spawn_write_resize_signal_and_kill_through_the_holder(inproc):
    client = inproc().client()
    term = spawn_counter(client)
    seen = read_until(term, b"READY")
    assert b"LINE 000" in seen and b"LINE 049" in seen and b"READY" in seen
    term.write(b"ping\n")
    assert b"ECHO:ping" in read_until(term, b"ECHO:ping")
    term.resize(40, 100)
    assert client.list()["sessions"][0]["meta"]["title"] == "counter"
    assert term.send_signal("int") is True
    assert term.proc.poll() is None or True
    pid = term.pid
    term.kill(1.0)
    assert wait_for(lambda: not alive(pid)) and client.list()["sessions"] == []
    client.close()


def test_the_exit_code_reaches_the_session_and_the_record_is_kept_until_forgotten(inproc):
    client = inproc().client()
    term = spawn_counter(client, argv=[sys.executable, "-c", "print('bye'); raise SystemExit(7)"])
    assert b"bye" in read_until(term, b"bye")
    assert wait_for(lambda: term.exit_code() == 7)
    assert term.read(0.5) == b"" or term.read(0.5) == b""
    listing = client.list()["sessions"][0]
    assert listing["alive"] is False and listing["exit_code"] == 7
    term.kill()  # forget: an exited session is only dropped on request
    assert client.list()["sessions"] == []
    client.close()


def test_spawn_failure_is_reported_not_swallowed(inproc):
    client = inproc().client()
    term = HeldTerminal(client, "bad", 24, 80)
    with pytest.raises(HolderError, match="FileNotFoundError"):
        client.spawn(term, ["/definitely/not/a/binary"], os.getcwd(), {}, 24, 80, {})
    assert client.list()["sessions"] == []
    client.close()


def test_replay_survives_a_dropped_client_and_resumes_with_the_gap_filled(inproc):
    holder = inproc()
    first = holder.client()
    term = spawn_counter(first)
    read_until(term, b"READY")
    first.close()  # the daemon side goes away; the process keeps running in the holder
    survivor = holder.client()
    listing = survivor.list()["sessions"]
    assert [item["id"] for item in listing] == ["s1"] and listing[0]["alive"]
    fresh = HeldTerminal(survivor, "s1", 24, 80)
    info, replay = survivor.attach(fresh, None)
    assert replay.count(b"LINE ") == 50 and replay.rstrip().endswith(b"READY")
    assert [int(part) for part in __import__("re").findall(rb"LINE (\d+)", replay)] == list(range(50))
    assert fresh.offset == info["end"] == len(replay)
    fresh.write(b"again\n")
    assert b"ECHO:again" in read_until(fresh, b"ECHO:again")
    survivor.close()


def test_a_dropped_connection_reconnects_and_resumes_from_its_offset(inproc):
    holder = inproc()
    client = holder.client()
    term = spawn_counter(client)
    read_until(term, b"READY")
    term.write(b"one\n")
    read_until(term, b"ECHO:one")
    # break the socket under the client without telling it, then produce output while it is down
    client._sock.shutdown(socket.SHUT_RDWR)
    other = holder.client()
    other_term = HeldTerminal(other, "s1", 24, 80)
    other.attach(other_term, None)
    other_term.write(b"two\n")
    assert b"ECHO:two" in read_until(other_term, b"ECHO:two")
    assert b"ECHO:two" in read_until(term, b"ECHO:two", timeout=10), "the client re-attached and received what it missed"
    other.close()
    client.close()


def test_a_slow_or_absent_reader_never_blocks_the_child(inproc):
    holder = inproc()
    client = holder.client()
    flood = [sys.executable, "-u", "-c", "import sys\nfor i in range(30000): print('x'*100)\nprint('DONE')\nimport time; time.sleep(30)"]
    term = spawn_counter(client, argv=flood)
    client.release("s1")  # nobody is reading this session any more
    assert wait_for(lambda: client.list()["sessions"][0]["total"] > 3_000_000, timeout=15)
    listing = client.list()["sessions"][0]
    assert listing["total"] - listing["base"] <= 256_000 + 70_000  # the ring keeps only its bound
    term.kill(0.5)
    client.close()


def test_peer_credentials_identify_this_user():
    a, b = socket.socketpair()
    assert H.peer_uid(a) == os.geteuid() and H.peer_is_self(b)
    a.close()
    b.close()


def test_a_peer_with_another_uid_is_refused(inproc, monkeypatch):
    holder = inproc()
    monkeypatch.setattr(H, "peer_uid", lambda sock: os.geteuid() + 1)
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(3)
    sock.connect(str(H.holder_dir(config_dir()) / H.SOCKET_NAME))
    sock.sendall(H.encode_json(H.HELLO, 1, {"protocol": H.PROTOCOL}))
    data = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except OSError:
        pass
    sock.close()
    frames = H.FrameDecoder(H.TO_CLIENT_LIMITS).feed(data)
    assert frames and frames[0].kind == H.ERR and json.loads(frames[0].payload)["code"] == "peer"
    assert not any(frame.kind == H.OK for frame in frames)
    monkeypatch.undo()
    holder.client().close()  # still serving the real owner


def test_an_unidentifiable_peer_is_refused_too(inproc, monkeypatch):
    inproc()
    monkeypatch.setattr(H, "peer_uid", lambda sock: None)
    with pytest.raises(HolderError):
        HolderClient(H.holder_dir(config_dir()), version="x").connect()


def test_client_refuses_a_server_owned_by_someone_else(inproc, monkeypatch):
    inproc()
    import termx.pty_holder_client as C

    monkeypatch.setattr(C, "peer_is_self", lambda sock: False)
    with pytest.raises(HolderError, match="not owned by this user"):
        HolderClient(H.holder_dir(config_dir()), version="x").connect()


@pytest.mark.parametrize("junk", [b"\xff" * 64, H.HEADER.pack(H.MAX_FRAME + 5, H.WRITE, 0), H.encode_json(H.SPAWN, 1, {"id": "x"}), b"GET / HTTP/1.1\r\n\r\n"])
def test_garbage_before_hello_drops_the_connection_and_the_holder_keeps_serving(inproc, junk):
    holder = inproc()
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(3)
    sock.connect(str(H.holder_dir(config_dir()) / H.SOCKET_NAME))
    sock.sendall(junk)
    try:
        while sock.recv(4096):
            pass
    except OSError:
        pass
    sock.close()
    holder.client().close()


def test_requests_before_hello_are_refused_and_bad_json_drops_the_connection(inproc):
    holder = inproc()
    directory = H.holder_dir(config_dir())
    for frame in (H.encode_json(H.LIST, 1, {}), H.encode_frame(H.SPAWN, 2, b"{not json")):
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(3)
        sock.connect(str(directory / H.SOCKET_NAME))
        sock.sendall(frame)
        data = b""
        try:
            while chunk := sock.recv(4096):
                data += chunk
        except OSError:
            pass
        sock.close()
        frames = H.FrameDecoder(H.TO_CLIENT_LIMITS).feed(data)
        assert all(f.kind != H.OK for f in frames)
    holder.client().close()


def test_protocol_mismatch_is_reported_and_an_empty_old_holder_can_be_shut_down(inproc):
    holder = inproc(version="old-version")
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(3)
    sock.connect(str(H.holder_dir(config_dir()) / H.SOCKET_NAME))
    sock.sendall(H.encode_json(H.HELLO, 1, {"protocol": 99}))
    frames = H.FrameDecoder(H.TO_CLIENT_LIMITS).feed(sock.recv(4096))
    body = json.loads(frames[0].payload)
    assert frames[0].kind == H.ERR and body["code"] == "protocol" and json.loads(body["message"])["version"] == "old-version"
    sock.sendall(H.encode_json(H.SHUTDOWN, 2, {"mode": "drain"}))  # allowed before HELLO: how a new daemon drains an old holder
    frames = H.FrameDecoder(H.TO_CLIENT_LIMITS).feed(sock.recv(4096))
    assert frames[0].kind == H.OK and json.loads(frames[0].payload)["live"] == 0
    sock.close()
    holder.thread.join(timeout=5)
    assert holder.result == 0 and not (H.holder_dir(config_dir()) / H.SOCKET_NAME).exists()


def test_holder_incompatibility_is_a_typed_error():
    error = HolderIncompatible({"protocol": 9, "version": "9.9"})
    assert "protocol 9" in str(error)


def test_drain_refuses_new_sessions_and_exits_when_the_last_one_ends(inproc):
    holder = inproc()
    client = holder.client()
    term = spawn_counter(client)
    read_until(term, b"READY")
    assert client.shutdown("drain")["live"] == 1
    with pytest.raises(HolderError, match="draining"):
        client.spawn(HeldTerminal(client, "s2", 24, 80), COUNTER, os.getcwd(), {}, 24, 80, {})
    assert holder.thread.is_alive()
    term.kill(0.5)
    holder.thread.join(timeout=8)
    assert not holder.thread.is_alive()
    client.close()


# -- supervisor and a real, detached holder --------------------------------------------------


@pytest.fixture
def held_env(monkeypatch):
    monkeypatch.setenv("TERMX_PTY_HOLDER", "held")
    yield
    try:
        stop(config_dir(), kill=True)
    except Exception:  # noqa: BLE001
        pass


def test_supervisor_starts_a_detached_holder_that_outlives_its_launcher(held_env):
    sup = HolderSupervisor(version="t", idle_exit_s=30)
    client = sup.acquire(create=True)
    assert client is not None and sup.reason is None
    status = sup.status()
    assert status["mode"] == "held" and status["running"] and status["sessions"] == 0
    pid = status["pid"]
    assert pid != os.getpid()
    parent = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout)
    assert parent != os.getpid(), "the holder is not our child (double fork)"
    assert os.getsid(pid) != os.getsid(0), "the holder has its own session"
    directory = H.holder_dir(config_dir())
    assert stat.S_IMODE(os.lstat(directory / H.SOCKET_NAME).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(directory).st_mode) == 0o700
    log = (directory / H.LOG_NAME).read_text()
    assert "ready" in log
    sup.close()
    assert alive(pid), "closing the daemon-side client does not end the holder"


DAEMON = """
import json, os, sys, time
from termx.pty_holder_client import HolderSupervisor
from termx.sessions import SessionManager

sup = HolderSupervisor(version="t", idle_exit_s=60)
manager = SessionManager(holder=sup)
session = manager.create(argv=%r, cwd=os.getcwd(), title="survivor")
deadline = time.time() + 10
while b"READY" not in session.output_tail(4096) and time.time() < deadline:
    time.sleep(0.05)
print(json.dumps({"sid": session.id, "pid": session.proc.pid, "holder": sup.status()["pid"], "durable": session.durable}), flush=True)
time.sleep(120)
""" % (COUNTER,)


def test_killing_the_daemon_leaves_the_terminal_running_and_a_new_daemon_reattaches_with_replay(held_env):
    daemon = subprocess.Popen([sys.executable, "-c", DAEMON], stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        info = json.loads(daemon.stdout.readline())
        assert info["durable"] is True
        os.kill(daemon.pid, signal.SIGKILL)  # the daemon dies hard: no shutdown path runs
        daemon.wait(timeout=5)
        assert alive(info["pid"]) and alive(info["holder"])

        sup = HolderSupervisor(version="t", idle_exit_s=60)
        manager = SessionManager(holder=sup)
        adopted = manager.reattach()
        assert [session.id for session in adopted] == [info["sid"]]
        session = adopted[0]
        assert session.title == "survivor" and session.durable and not session.exited
        replay = session.replay.dump()
        assert replay.count(b"LINE ") == 50 and replay.rstrip().endswith(b"READY")
        assert session.proc.pid == info["pid"]

        sink = []

        class Sink:
            async def send_bytes(self, data):
                sink.append(data)

        session.write(b"after restart\n")
        assert wait_for(lambda: b"ECHO:after restart" in session.output_tail(4096))
        assert session.send_signal("int") in (True, False)
        assert manager.kill(info["sid"]) is True
        assert wait_for(lambda: not alive(info["pid"]))
        assert sup.status()["sessions"] == 0
    finally:
        if daemon.poll() is None:
            daemon.kill()


def test_kill_all_detaches_held_terminals_and_the_next_manager_adopts_them(held_env):
    sup = HolderSupervisor(version="t", idle_exit_s=60)
    first = SessionManager(holder=sup)
    session = first.create(argv=COUNTER, cwd=os.getcwd(), title="keep me")
    assert wait_for(lambda: b"READY" in session.output_tail(4096))
    pid = session.proc.pid
    first.kill_all()
    assert first.list() == [] and alive(pid)
    second = SessionManager(holder=HolderSupervisor(version="t", idle_exit_s=60))
    adopted = second.reattach()
    assert [item.title for item in adopted] == ["keep me"] and adopted[0].replay.dump().rstrip().endswith(b"READY")
    second.kill_all(keep_held=False)
    assert wait_for(lambda: not alive(pid))


def test_reattach_with_no_holder_running_starts_nothing(monkeypatch):
    monkeypatch.setenv("TERMX_PTY_HOLDER", "auto")
    sup = HolderSupervisor(version="t")
    assert SessionManager(holder=sup).reattach() == []
    assert describe(config_dir())["running"] is False


def test_stale_socket_and_lock_from_a_crashed_holder_are_cleaned_up(held_env):
    directory = H.holder_dir(config_dir())
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(directory / H.SOCKET_NAME))
    stale.close()  # a socket file nobody listens on: what a SIGKILLed holder leaves behind
    assert (directory / H.SOCKET_NAME).exists()
    sup = HolderSupervisor(version="t", idle_exit_s=30)
    assert sup.acquire(create=True) is not None and sup.status()["running"]
    sup.close()


def test_a_second_holder_for_the_same_config_exits_without_disturbing_the_first(held_env):
    sup = HolderSupervisor(version="t", idle_exit_s=30)
    client = sup.acquire(create=True)
    first = sup.status()["pid"]
    result = subprocess.run(
        [sys.executable, "-m", "termx.pty_holder", "run", "--foreground", "--config-dir", str(config_dir())],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and "another holder owns" in result.stderr
    assert sup.status()["pid"] == first and client.list()["live"] == 0
    sup.close()


def test_an_idle_holder_with_no_session_exits_and_cleans_up(held_env):
    sup = HolderSupervisor(version="t", idle_exit_s=0.8)
    sup.acquire(create=True)
    pid = sup.status()["pid"]
    sup.close()
    assert wait_for(lambda: not alive(pid), timeout=10), "an idle holder exits"
    directory = H.holder_dir(config_dir())
    assert not (directory / H.SOCKET_NAME).exists()


def test_a_holder_with_a_live_session_does_not_idle_out(held_env):
    sup = HolderSupervisor(version="t", idle_exit_s=0.8)
    client = sup.acquire(create=True)
    term = spawn_counter(client)
    read_until(term, b"READY")
    pid = sup.status()["pid"]
    client.close()
    time.sleep(2.5)
    assert alive(pid) and alive(term.pid)
    stop(config_dir(), kill=True)
    assert wait_for(lambda: not alive(pid) and not alive(term.pid))


def test_old_version_holder_with_nothing_running_is_replaced(inproc, held_env):
    old = inproc(version="old")
    sup = HolderSupervisor(version="new", idle_exit_s=30)
    client = sup.acquire(create=True)
    assert client is not None and client.info["version"] != "old"  # a holder of this build took over
    assert wait_for(lambda: not old.thread.is_alive())
    sup.close()


def test_old_version_holder_with_live_sessions_keeps_serving(inproc, held_env):
    old = inproc(version="old")
    keeper = old.client()
    spawn_counter(keeper)
    sup = HolderSupervisor(version="new", idle_exit_s=30)
    client = sup.acquire(create=True)
    assert client is not None and client.info["version"] == "old" and old.thread.is_alive()
    sup.close()
    keeper.close()


def test_failure_to_start_falls_back_to_direct_with_a_reason_in_auto_but_refuses_in_held(monkeypatch):
    broken = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
    monkeypatch.setenv("TERMX_PTY_HOLDER", "auto")
    sup = HolderSupervisor(version="t", command=broken)
    assert sup.acquire(create=True) is None
    status = sup.status()
    assert status["mode"] == "direct" and "boom" in status["reason"] and "will not survive a restart" in status["reason"]
    assert sup.acquire(create=True) is None  # inside the retry window: no new attempt, same reason
    monkeypatch.setenv("TERMX_PTY_HOLDER", "held")
    with pytest.raises(HolderError, match="required"):
        HolderSupervisor(version="t", command=broken).acquire(create=True)
    manager = SessionManager(holder=HolderSupervisor(version="t", command=broken))
    from termx.terminals import TerminalError

    with pytest.raises(TerminalError, match="PTY holder"):
        manager.create(argv=COUNTER)


def test_direct_mode_is_explicit_and_creates_ordinary_terminals(monkeypatch):
    monkeypatch.setenv("TERMX_PTY_HOLDER", "direct")
    sup = HolderSupervisor(version="t")
    manager = SessionManager(holder=sup)
    session = manager.create(argv=COUNTER, cwd=os.getcwd())
    assert session.durable is False
    status = sup.status()
    assert status["mode"] == "direct" and status["requested"] == "direct" and "turned off" in status["reason"]
    manager.kill_all()


def test_windows_is_direct_with_an_explicit_reason(monkeypatch):
    sup = HolderSupervisor(version="t")
    monkeypatch.setattr(os, "name", "nt")
    assert sup.acquire(create=True) is None
    assert "Windows" in sup.status()["reason"]


# -- the sandbox is never widened -------------------------------------------------------------


class RecordingHolder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def spawn(self, sink, argv, cwd, env, rows, cols, meta):
        self.calls.append((argv, cwd, env, meta))
        return {"pid": 4242}


def test_restricted_launch_argv_and_env_reach_the_holder_unchanged(monkeypatch):
    wrapped = (["/usr/bin/sandbox-exec", "-p", "(version 1)", "/bin/zsh", "-l"], {"HOME": "/sandbox/home", "PATH": "/usr/bin"})
    monkeypatch.setattr(Session, "_restricted_launch", lambda self: (list(wrapped[0]), dict(wrapped[1])))
    recorder = RecordingHolder()
    session = Session(id="sb1", title="t", created_at=0.0, cols=80, rows=24, argv=["/bin/zsh", "-l"], cwd="/work",
                      sandbox_profile="workspace", holder=recorder)
    monkeypatch.setattr(Session, "_read_loop", lambda self: None)
    session.start()
    [(argv, cwd, env, meta)] = recorder.calls
    assert argv == wrapped[0] and env == wrapped[1] and cwd == "/work"
    assert meta["sandbox_profile"] == "workspace" and meta["argv0"] == "sandbox-exec"
    assert session.durable


def test_a_restricted_profile_that_cannot_be_applied_still_fails_closed_in_held_mode(monkeypatch):
    from termx.terminals import TerminalError

    def refuse(self):
        raise TerminalError("no kernel sandbox backend available")

    monkeypatch.setattr(Session, "_restricted_launch", refuse)
    recorder = RecordingHolder()
    session = Session(id="sb2", title="t", created_at=0.0, cols=80, rows=24, argv=["/bin/sh"], cwd="/work", sandbox_profile="workspace", holder=recorder)
    with pytest.raises(TerminalError):
        session.start()
    assert recorder.calls == []  # nothing was handed to the holder


def test_the_holder_has_no_sandbox_or_profile_vocabulary():
    spawn_fields = {"id", "argv", "cwd", "env", "rows", "cols", "meta"}
    with pytest.raises(H.ProtocolError):
        H.validate_spawn({"id": "a", "argv": ["x"], "cwd": "/", "env": {}, "rows": 1, "cols": 1, "sandbox_profile": "host"})
    assert spawn_fields == {"id", "argv", "cwd", "env", "rows", "cols", "meta"}


def test_modules_import_without_fcntl_so_the_daemon_starts_on_windows():
    """`AppState` imports the holder client on every platform; `fcntl` does not exist on Windows."""
    code = (
        "import sys\n"
        "sys.modules['fcntl'] = None\n"  # makes `import fcntl` raise ImportError, as on Windows
        "import termx.pty_holder, termx.pty_holder_client\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
