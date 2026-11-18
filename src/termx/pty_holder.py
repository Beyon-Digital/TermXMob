"""Detached PTY holder: terminals that outlive the daemon.

The daemon asks this small helper to spawn a terminal. The helper owns the PTY master
and a per-session replay ring (the same ``ReplayBuffer`` the daemon uses, 256 KB), so
when the daemon stops or restarts the processes keep running and their recent output is
kept. A new daemon connects, lists what is still held and re-attaches with the replay
intact. The holder is the only process that ever holds a PTY master for a held session.

Security model
--------------
* **One owner.** The socket lives in a 0700 directory owned by the user (the TermX
  config directory, or ``/tmp/termx-<uid>/<hash>`` when that path would be too long for
  an AF_UNIX address). The directory is verified (owner, mode, not a symlink) on both
  ends, and **every connection is peer-checked** with ``SO_PEERCRED`` (Linux) or
  ``LOCAL_PEERCRED`` (macOS/BSD); a different uid, or a platform that cannot tell, is
  refused (fail closed). The client checks the server the same way.
* **The holder only execs what it is told.** Restricted/sandboxed argv and environment
  are built by the daemon (``Session._restricted_launch``) *before* the spawn request.
  The holder adds nothing: no wrapper, no environment, no widening of a profile. It does
  not carry the daemon's environment either; a spawn request carries the complete child
  environment.
* **Strict framing, no pickle.** Frames are length-prefixed, every type has its own size
  limit that is enforced from the header, before the payload is buffered; control frames
  are JSON validated against a closed schema. Anything else drops the connection.
* **No content in logs.** The log records session ids, pids and exit codes, never argv,
  environment or terminal output.

Wire format
-----------
``frame = u32 length | u8 type | u32 request id | payload`` (big endian; ``length``
counts everything after itself, so ``payload = length - 5``). Requests carry a request
id the holder echoes on the reply (an ``OK`` or ``ERR``, preceded for ``ATTACH`` by
``REPLAY`` chunks). ``WRITE`` and ``RESIZE`` have no reply. ``OUTPUT`` and ``EXIT`` are
unsolicited events for the sessions a connection is attached to. Stream frames
(``WRITE``/``OUTPUT``/``REPLAY``) start with ``u8 id length | id``; offsets count bytes
produced by the child since it started so a reconnecting client can resume exactly.

Lifecycle
---------
One holder per config directory (an ``flock``-ed ``holder.lock``). It starts
double-forked and detached, removes a stale socket left by a crashed predecessor, exits
when it holds no live session, has no client and has been idle ``--idle-exit`` seconds
(default 600) or on ``SHUTDOWN``. ``SHUTDOWN drain`` refuses new sessions and exits once
none is live; ``SHUTDOWN now`` ends them first. A holder never kills a live session on
its own initiative except when it is itself told to terminate (SIGTERM/SIGINT).
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from termx.sessions import REPLAY_MAX_BYTES, SIGNALS, ReplayBuffer
from termx.terminals import PosixTerminal, TerminalError

PROTOCOL = 1

# -- frames --------------------------------------------------------------------------

HEADER = struct.Struct(">IBI")  # length (type + request id + payload), type, request id
HEADER_BYTES = HEADER.size
MAX_FRAME = 1 << 20  # absolute ceiling on length
STREAM_CHUNK = 32_768  # most bytes a stream frame carries
MAX_ID = 64

# client -> holder
HELLO, SPAWN, ATTACH, DETACH, WRITE, RESIZE, SIGNAL, LIST, KILL, SHUTDOWN, FORGET = range(1, 12)
# holder -> client
OK, ERR, OUTPUT, EXIT, REPLAY = 0x81, 0x82, 0x83, 0x84, 0x85

TO_HOLDER_LIMITS: dict[int, int] = {
    HELLO: 4_096,
    SPAWN: 512 * 1024,
    ATTACH: 1_024,
    DETACH: 1_024,
    WRITE: STREAM_CHUNK + MAX_ID + 1,
    RESIZE: 1_024,
    SIGNAL: 1_024,
    LIST: 64,
    KILL: 1_024,
    SHUTDOWN: 1_024,
    FORGET: 1_024,
}
TO_CLIENT_LIMITS: dict[int, int] = {
    OK: MAX_FRAME - 16,
    ERR: 16_384,
    OUTPUT: 65_536 + MAX_ID + 1,
    EXIT: 1_024,
    REPLAY: STREAM_CHUNK + MAX_ID + 1,
}

MAX_LIVE_SESSIONS = 256
MAX_EXITED_KEPT = 64
EXITED_KEEP_S = 6 * 3600
MAX_CLIENT_BACKLOG = 4 * 1024 * 1024  # a client this far behind is dropped; it can re-attach and resume
MAX_PENDING_WRITE = 1024 * 1024
HELLO_TIMEOUT_S = 10.0
DEFAULT_IDLE_EXIT_S = 600.0

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SIGNAL_NAMES = frozenset(SIGNALS)


class ProtocolError(Exception):
    """The peer sent something outside the protocol: the connection is dropped."""


class Refused(Exception):
    """A well-formed request the holder will not do; carried back in an ERR frame."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Frame:
    kind: int
    request: int
    payload: bytes


def encode_frame(kind: int, request: int, payload: bytes = b"") -> bytes:
    if len(payload) + 5 > MAX_FRAME:
        raise ProtocolError("frame too large")
    return HEADER.pack(len(payload) + 5, kind, request) + payload


def encode_json(kind: int, request: int, value: dict[str, Any]) -> bytes:
    return encode_frame(kind, request, json.dumps(value, separators=(",", ":")).encode("utf-8"))


def pack_stream(session_id: str, data: bytes) -> bytes:
    raw = session_id.encode("ascii")
    return bytes((len(raw),)) + raw + data


def unpack_stream(payload: bytes) -> tuple[str, bytes]:
    if not payload:
        raise ProtocolError("empty stream frame")
    size = payload[0]
    if size < 1 or size > MAX_ID or len(payload) < 1 + size:
        raise ProtocolError("malformed stream frame")
    try:
        session_id = payload[1 : 1 + size].decode("ascii")
    except UnicodeDecodeError:
        raise ProtocolError("malformed stream id") from None
    if not _ID.match(session_id):
        raise ProtocolError("malformed stream id")
    return session_id, payload[1 + size :]


class FrameDecoder:
    """Incremental frame parser. Bounds are checked from the header, before any payload is buffered."""

    def __init__(self, limits: dict[int, int]) -> None:
        self._limits = limits
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer += data
        frames: list[Frame] = []
        while True:
            have = len(self._buffer)
            if have >= 4:
                length = int.from_bytes(self._buffer[:4], "big")
                if length < 5 or length > MAX_FRAME:
                    raise ProtocolError("bad frame length")
            if have < HEADER_BYTES:
                break
            length, kind, request = HEADER.unpack_from(self._buffer)
            limit = self._limits.get(kind)
            if limit is None:
                raise ProtocolError("unknown frame type")
            if length - 5 > limit:
                raise ProtocolError("frame exceeds the limit for its type")
            if have < 4 + length:
                break
            payload = bytes(self._buffer[HEADER_BYTES : 4 + length])
            del self._buffer[: 4 + length]
            frames.append(Frame(kind, request, payload))
        return frames

    @property
    def buffered(self) -> int:
        return len(self._buffer)


def parse_json(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProtocolError("payload is not valid JSON") from None
    if not isinstance(value, dict):
        raise ProtocolError("payload must be a JSON object")
    return value


# -- validation ----------------------------------------------------------------------


def _keys(value: dict[str, Any], allowed: set[str], required: set[str] = frozenset()) -> None:  # type: ignore[assignment]
    extra = set(value) - allowed
    if extra:
        raise ProtocolError("unexpected field: " + sorted(extra)[0][:40])
    missing = set(required) - set(value)
    if missing:
        raise ProtocolError("missing field: " + sorted(missing)[0])


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ProtocolError(f"{name} is out of range")
    return value


def _text(value: Any, name: str, limit: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (not empty and not value):
        raise ProtocolError(f"{name} is not acceptable text")
    return value


def _session_id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ProtocolError("session id is not acceptable")
    return value


def validate_hello(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"protocol", "version", "pid"}, {"protocol"})
    return {
        "protocol": _int(value["protocol"], "protocol", 0, 1_000_000),
        "version": _text(value.get("version", ""), "version", 64),
        "pid": _int(value.get("pid", 0), "pid", 0, 2**31),
    }


def validate_spawn(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"id", "argv", "cwd", "env", "rows", "cols", "meta"}, {"id", "argv", "cwd", "env", "rows", "cols"})
    argv = value["argv"]
    if not isinstance(argv, list) or not 1 <= len(argv) <= 256:
        raise ProtocolError("argv must be a list of 1 to 256 strings")
    total = 0
    for item in argv:
        total += len(_text(item, "argv item", 32_768))
    if total > 256 * 1024 or not argv[0]:
        raise ProtocolError("argv is too large or empty")
    env = value["env"]
    if not isinstance(env, dict) or len(env) > 4_096:
        raise ProtocolError("env must be an object of at most 4096 entries")
    size = 0
    for name, text in env.items():
        _text(name, "env name", 256, empty=False)
        if "=" in name:
            raise ProtocolError("env name cannot contain '='")
        size += len(name) + len(_text(text, "env value", 65_536))
    if size > 384 * 1024:
        raise ProtocolError("env is too large")
    meta = value.get("meta") or {}
    if not isinstance(meta, dict) or len(meta) > 16:
        raise ProtocolError("meta must be a small object")
    clean_meta: dict[str, Any] = {}
    for name, item in meta.items():
        _text(name, "meta name", 64, empty=False)
        if isinstance(item, str):
            clean_meta[name] = _text(item, "meta value", 1_024)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            clean_meta[name] = item
        else:
            raise ProtocolError("meta values must be text or numbers")
    return {
        "id": _session_id(value["id"]),
        "argv": list(argv),
        "cwd": _text(value["cwd"], "cwd", 4_096, empty=False),
        "env": dict(env),
        "rows": _int(value["rows"], "rows", 1, 10_000),
        "cols": _int(value["cols"], "cols", 1, 10_000),
        "meta": clean_meta,
    }


def validate_attach(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"id", "since"}, {"id"})
    since = value.get("since")
    return {"id": _session_id(value["id"]), "since": None if since is None else _int(since, "since", 0, 2**62)}


def validate_id_only(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"id"}, {"id"})
    return {"id": _session_id(value["id"])}


def validate_resize(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"id", "rows", "cols"}, {"id", "rows", "cols"})
    return {"id": _session_id(value["id"]), "rows": _int(value["rows"], "rows", 1, 10_000), "cols": _int(value["cols"], "cols", 1, 10_000)}


def validate_signal(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"id", "name"}, {"id", "name"})
    name = _text(value["name"], "signal", 16, empty=False).lower()
    if name not in _SIGNAL_NAMES:
        raise ProtocolError("unknown signal")
    return {"id": _session_id(value["id"]), "name": name}


def validate_kill(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"id", "timeout"}, {"id"})
    timeout = value.get("timeout", 1.5)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 30:
        raise ProtocolError("timeout is out of range")
    return {"id": _session_id(value["id"]), "timeout": float(timeout)}


def validate_shutdown(value: dict[str, Any]) -> dict[str, Any]:
    _keys(value, {"mode"})
    mode = value.get("mode", "drain")
    if mode not in ("drain", "now"):
        raise ProtocolError("mode must be drain or now")
    return {"mode": mode}


# -- peer credentials ----------------------------------------------------------------

_SOL_LOCAL = 0  # macOS / BSD
_LOCAL_PEERCRED = 1


def peer_uid(sock: Any) -> int | None:
    """The effective uid of the process on the other end of a connected AF_UNIX socket, or None if unknowable."""
    try:
        if sys.platform.startswith("linux"):
            data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", data)
            return uid
        if sys.platform == "darwin" or "bsd" in sys.platform:
            data = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, 128)
            _version, uid = struct.unpack_from("=II", data)
            return uid
    except (OSError, struct.error, AttributeError):
        return None
    return None


def peer_is_self(sock: Any) -> bool:
    uid = peer_uid(sock)
    return uid is not None and uid == os.geteuid()


# -- paths ---------------------------------------------------------------------------

SOCKET_NAME, LOCK_NAME, INFO_NAME, LOG_NAME = "holder.sock", "holder.lock", "holder.json", "holder.log"
_MAX_SOCKET_PATH = 100  # AF_UNIX addresses are 104 bytes on macOS (108 on Linux), terminator included


def ensure_private_dir(path: Path) -> Path:
    """Create (if needed) and verify a 0700 directory owned by this user; never follows a symlink."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except FileExistsError:
        pass
    info = os.lstat(path)
    import stat

    if not stat.S_ISDIR(info.st_mode):
        raise PermissionError("the PTY holder directory is not a real directory")
    if info.st_uid != os.geteuid():
        raise PermissionError("the PTY holder directory belongs to another user")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)
        if os.lstat(path).st_mode & 0o077:
            raise PermissionError("the PTY holder directory is not private")
    return path


def holder_dir(root: Path) -> Path:
    """Where the socket, lock and log live: inside the config directory when it fits an AF_UNIX path."""
    root = Path(root).expanduser().resolve()
    preferred = root / "pty-holder"
    if len(os.fsencode(str(preferred / SOCKET_NAME))) <= _MAX_SOCKET_PATH:
        ensure_private_dir(root)
        return ensure_private_dir(preferred)
    digest = hashlib.sha256(os.fsencode(root)).hexdigest()[:16]
    base = ensure_private_dir(Path(f"/tmp/termx-{os.geteuid()}"))
    return ensure_private_dir(base / digest)


def holder_env(root: Path) -> dict[str, str]:
    """The holder's own environment: a short allowlist, never the daemon's secrets."""
    keep = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "SYSTEMROOT")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env["TERMX_CONFIG_DIR"] = str(root)
    return env


# -- the holder ----------------------------------------------------------------------


def _log(message: str) -> None:
    sys.stderr.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + message + "\n")
    sys.stderr.flush()


@dataclass(eq=False)
class _Conn:
    writer: asyncio.StreamWriter
    hello: bool = False
    attached: set[str] = field(default_factory=set)
    closed: bool = False

    def send(self, frame: bytes) -> bool:
        if self.closed:
            return False
        transport = self.writer.transport
        if transport.is_closing() or transport.get_write_buffer_size() > MAX_CLIENT_BACKLOG:
            self.close()
            return False
        self.writer.write(frame)
        return True

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001 - already gone
                pass


@dataclass(eq=False)
class _Held:
    id: str
    terminal: PosixTerminal
    ring: ReplayBuffer
    rows: int
    cols: int
    meta: dict[str, Any]
    created_at: float
    total: int = 0
    exited: bool = False
    exit_code: int | None = None
    exited_at: float = 0.0
    eof_at: float | None = None
    subscribers: set[_Conn] = field(default_factory=set)
    pending: bytearray = field(default_factory=bytearray)
    reading: bool = True

    @property
    def pid(self) -> int:
        return self.terminal.proc.pid

    @property
    def fd(self) -> int:
        return self.terminal.master_fd

    def base(self) -> int:
        return self.total - len(self.ring.dump())

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pid": self.pid,
            "alive": not self.exited,
            "exit_code": self.exit_code,
            "rows": self.rows,
            "cols": self.cols,
            "created_at": self.created_at,
            "meta": self.meta,
            "total": self.total,
            "base": self.base(),
        }


class HolderServer:
    def __init__(self, root: Path, *, idle_exit_s: float = DEFAULT_IDLE_EXIT_S, version: str | None = None) -> None:
        from termx import __version__

        self.root = Path(root).expanduser().resolve()
        self.idle_exit_s = idle_exit_s
        self.version = version or __version__
        self.started_at = time.time()
        self.sessions: dict[str, _Held] = {}
        self.conns: set[_Conn] = set()
        self.draining = False
        self._last_busy = time.monotonic()
        self._done: asyncio.Event | None = None
        self._tasks: set[asyncio.Task] = set()
        self._lock_file: Any = None
        self.directory: Path | None = None

    # -- lifecycle ---------------------------------------------------------------

    def acquire_lock(self) -> bool:
        directory = holder_dir(self.root)
        self.directory = directory
        fd = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_file = fd
        return True

    async def run(self) -> int:
        if not self.acquire_lock():
            _log("another holder owns this configuration directory; exiting")
            return 0
        assert self.directory is not None
        socket_path = self.directory / SOCKET_NAME
        # We hold the lock, so a socket file is a leftover of a crashed holder.
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        loop = asyncio.get_running_loop()
        self._done = asyncio.Event()
        old_umask = os.umask(0o177)
        try:
            server = await asyncio.start_unix_server(self._client, path=str(socket_path), limit=1 << 16)
        finally:
            os.umask(old_umask)
        os.chmod(socket_path, 0o600)
        from termx.config import atomic_write

        atomic_write(
            self.directory / INFO_NAME,
            json.dumps({"pid": os.getpid(), "protocol": PROTOCOL, "version": self.version, "started_at": self.started_at}),
        )
        for sig, handler in ((signal.SIGTERM, self._terminate), (signal.SIGINT, self._terminate)):
            try:
                loop.add_signal_handler(sig, handler)
            except (NotImplementedError, RuntimeError, ValueError):
                pass  # not the main thread (tests run the server in a thread)
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        except ValueError:
            pass
        self._spawn_task(self._reaper())
        self._spawn_task(self._idle_watch())
        _log(f"holder {self.version} protocol {PROTOCOL} ready pid {os.getpid()}")
        try:
            await self._done.wait()
        finally:
            server.close()
            for conn in list(self.conns):
                conn.close()
            for held in list(self.sessions.values()):
                self._kill_now(held)
            for task in list(self._tasks):
                task.cancel()
            for name in (SOCKET_NAME, INFO_NAME):
                try:
                    os.unlink(self.directory / name)
                except OSError:
                    pass
            if self._lock_file is not None:
                os.close(self._lock_file)
                self._lock_file = None
            _log("holder stopped")
        return 0

    def _spawn_task(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _terminate(self) -> None:
        _log("terminate requested")
        if self._done is not None:
            self._done.set()

    def _live(self) -> list[_Held]:
        return [held for held in self.sessions.values() if not held.exited]

    def _touch(self) -> None:
        self._last_busy = time.monotonic()

    async def _idle_watch(self) -> None:
        while True:
            await asyncio.sleep(min(5.0, max(0.05, self.idle_exit_s / 4)))
            if self._live():
                self._touch()
                continue
            if self.conns and not self.draining:
                self._touch()
                continue
            if self.draining or time.monotonic() - self._last_busy >= self.idle_exit_s:
                _log("idle; exiting")
                self._done.set()  # type: ignore[union-attr]
                return

    # -- connections -------------------------------------------------------------

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = _Conn(writer)
        self.conns.add(conn)
        self._touch()
        try:
            if not peer_is_self(writer.get_extra_info("socket")):
                _log("rejected a connection from another user or an unidentifiable peer")
                conn.send(encode_json(ERR, 0, {"code": "peer", "message": "peer is not the owning user"}))
                return
            decoder = FrameDecoder(TO_HOLDER_LIMITS)
            first = True
            while not conn.closed:
                try:
                    data = await asyncio.wait_for(reader.read(1 << 16), HELLO_TIMEOUT_S if first else None)
                except asyncio.TimeoutError:
                    return
                if not data:
                    return
                for frame in decoder.feed(data):
                    first = False
                    await self._handle(conn, frame)
        except ProtocolError as exc:
            conn.send(encode_json(ERR, 0, {"code": "protocol", "message": str(exc)[:200]}))
        except (ConnectionError, OSError):
            pass
        finally:
            for session_id in list(conn.attached):
                held = self.sessions.get(session_id)
                if held is not None:
                    held.subscribers.discard(conn)
            self.conns.discard(conn)
            conn.close()
            self._touch()

    async def _handle(self, conn: _Conn, frame: Frame) -> None:
        self._touch()
        kind = frame.kind
        if not conn.hello and kind not in (HELLO, SHUTDOWN):
            raise ProtocolError("expected HELLO first")
        try:
            if kind == WRITE:
                session_id, data = unpack_stream(frame.payload)
                held = self.sessions.get(session_id)
                if held is not None and not held.exited:
                    self._write(held, data)
                return
            if kind == RESIZE:
                args = validate_resize(parse_json(frame.payload))
                held = self.sessions.get(args["id"])
                if held is not None and not held.exited:
                    held.rows, held.cols = args["rows"], args["cols"]
                    held.terminal.resize(args["rows"], args["cols"])
                return
            if kind == DETACH:
                args = validate_id_only(parse_json(frame.payload))
                held = self.sessions.get(args["id"])
                if held is not None:
                    held.subscribers.discard(conn)
                conn.attached.discard(args["id"])
                conn.send(encode_json(OK, frame.request, {}))
                return
            reply = await self._request(conn, frame)
            conn.send(encode_json(OK, frame.request, reply))
        except Refused as exc:
            conn.send(encode_json(ERR, frame.request, {"code": exc.code, "message": exc.message}))

    def _held(self, session_id: str) -> _Held:
        held = self.sessions.get(session_id)
        if held is None:
            raise Refused("not_found", "no such session")
        return held

    async def _request(self, conn: _Conn, frame: Frame) -> dict[str, Any]:
        kind = frame.kind
        if kind == HELLO:
            args = validate_hello(parse_json(frame.payload))
            info = {"protocol": PROTOCOL, "version": self.version, "pid": os.getpid(), "started_at": self.started_at,
                    "live": len(self._live()), "draining": self.draining}
            if args["protocol"] != PROTOCOL:
                raise Refused("protocol", json.dumps(info))
            conn.hello = True
            return info
        if kind == SHUTDOWN:
            args = validate_shutdown(parse_json(frame.payload))
            live = len(self._live())
            if args["mode"] == "now":
                for held in self._live():
                    self._kill_now(held)
                self._done.set()  # type: ignore[union-attr]
            else:
                self.draining = True
                if not live:
                    self._done.set()  # type: ignore[union-attr]
            return {"mode": args["mode"], "live": live}
        if kind == LIST:
            return {"sessions": [held.summary() for held in self.sessions.values()], "live": len(self._live()),
                    "draining": self.draining}
        if kind == SPAWN:
            return self._spawn(conn, validate_spawn(parse_json(frame.payload)), frame.request)
        if kind == ATTACH:
            return self._attach(conn, validate_attach(parse_json(frame.payload)), frame.request)
        if kind == SIGNAL:
            args = validate_signal(parse_json(frame.payload))
            held = self._held(args["id"])
            return {"ok": bool(not held.exited and held.terminal.send_signal(args["name"]))}
        if kind == KILL:
            args = validate_kill(parse_json(frame.payload))
            held = self._held(args["id"])
            await self._kill(held, args["timeout"])
            return {}
        if kind == FORGET:
            args = validate_id_only(parse_json(frame.payload))
            held = self.sessions.get(args["id"])
            if held is not None and held.exited:
                self.sessions.pop(held.id, None)
            return {}
        raise ProtocolError("unexpected frame")

    # -- sessions ----------------------------------------------------------------

    def _spawn(self, conn: _Conn, args: dict[str, Any], request: int) -> dict[str, Any]:
        if self.draining:
            raise Refused("draining", "this holder is draining and starts no new sessions")
        if args["id"] in self.sessions:
            raise Refused("exists", "a session with this id already exists")
        if len(self._live()) >= MAX_LIVE_SESSIONS:
            raise Refused("limit", "too many live sessions")
        try:
            terminal = PosixTerminal(args["argv"], args["cwd"], args["env"], args["rows"], args["cols"])
        except (TerminalError, OSError, ValueError, subprocess.SubprocessError) as exc:
            reason = getattr(exc, "strerror", None) or str(exc)
            raise Refused("spawn", f"{type(exc).__name__}: {reason}"[:300]) from None
        held = _Held(args["id"], terminal, ReplayBuffer(REPLAY_MAX_BYTES), args["rows"], args["cols"], args["meta"], time.time())
        self.sessions[held.id] = held
        # Attach before the loop can run the reader, so the spawner misses no byte.
        held.subscribers.add(conn)
        conn.attached.add(held.id)
        asyncio.get_running_loop().add_reader(held.fd, self._on_readable, held)
        _log(f"spawned {held.id} pid {held.pid}")
        return {"id": held.id, "pid": held.pid, "start": 0}

    def _attach(self, conn: _Conn, args: dict[str, Any], request: int) -> dict[str, Any]:
        held = self._held(args["id"])
        data = held.ring.dump()
        base = held.total - len(data)
        since = args["since"]
        gap = since is not None and since < base
        start = base if since is None or since < base else min(since, held.total)
        replay = data[start - base :]
        for offset in range(0, len(replay), STREAM_CHUNK):
            conn.send(encode_frame(REPLAY, request, pack_stream(held.id, replay[offset : offset + STREAM_CHUNK])))
        held.subscribers.add(conn)
        conn.attached.add(held.id)
        info = held.summary()
        info.update(start=start, end=held.total, gap=gap)
        return info

    def _on_readable(self, held: _Held) -> None:
        try:
            data = os.read(held.fd, 65_536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self._output(held, data)
        else:
            self._eof(held)

    def _output(self, held: _Held, data: bytes) -> None:
        held.ring.append(data)
        held.total += len(data)
        if held.subscribers:
            frame = encode_frame(OUTPUT, 0, pack_stream(held.id, data))
            for conn in list(held.subscribers):
                if not conn.send(frame):
                    held.subscribers.discard(conn)
        self._touch()

    def _eof(self, held: _Held) -> None:
        if held.reading:
            held.reading = False
            try:
                asyncio.get_running_loop().remove_reader(held.fd)
            except (ValueError, OSError):
                pass
        held.eof_at = held.eof_at or time.monotonic()

    def _write(self, held: _Held, data: bytes) -> None:
        if held.pending:
            if len(held.pending) + len(data) <= MAX_PENDING_WRITE:
                held.pending += data
            return
        try:
            written = os.write(held.fd, data)
        except BlockingIOError:
            written = 0
        except OSError:
            return
        if written < len(data):
            held.pending += data[written:]
            asyncio.get_running_loop().add_writer(held.fd, self._flush, held)

    def _flush(self, held: _Held) -> None:
        loop = asyncio.get_running_loop()
        try:
            written = os.write(held.fd, bytes(held.pending))
        except BlockingIOError:
            return
        except OSError:
            held.pending.clear()
        else:
            del held.pending[:written]
        if not held.pending:
            try:
                loop.remove_writer(held.fd)
            except (ValueError, OSError):
                pass

    async def _reaper(self) -> None:
        while True:
            await asyncio.sleep(0.4)
            now = time.monotonic()
            for held in list(self._live()):
                code = held.terminal.proc.poll()
                if code is not None and held.reading:
                    # The child is gone: drain what the pty still holds, then call it finished.
                    try:
                        data = os.read(held.fd, 65_536)
                    except BlockingIOError:
                        data = b""
                    except OSError:
                        data = b""
                    if data:
                        self._output(held, data)
                        continue
                    self._eof(held)
                if not held.reading and (code is not None or (held.eof_at is not None and now - held.eof_at > 2.0)):
                    await self._finish(held, code)
            self._prune()

    async def _finish(self, held: _Held, code: int | None) -> None:
        if held.exited:
            return
        held.exited = True
        held.exit_code = held.terminal.proc.poll() if code is None else code
        held.exited_at = time.time()
        if held.pending:
            held.pending.clear()
            try:
                asyncio.get_running_loop().remove_writer(held.fd)
            except (ValueError, OSError):
                pass
        await asyncio.get_running_loop().run_in_executor(None, held.terminal.kill, 0.5)  # reaps and closes the master
        frame = encode_json(EXIT, 0, {"id": held.id, "code": held.exit_code})
        for conn in list(held.subscribers):
            conn.send(frame)
        _log(f"exited {held.id} code {held.exit_code}")

    async def _kill(self, held: _Held, timeout: float) -> None:
        if held.reading:
            held.reading = False
            try:
                asyncio.get_running_loop().remove_reader(held.fd)
            except (ValueError, OSError):
                pass
        if held.pending:
            held.pending.clear()
            try:
                asyncio.get_running_loop().remove_writer(held.fd)
            except (ValueError, OSError):
                pass
        if not held.exited:
            await asyncio.get_running_loop().run_in_executor(None, held.terminal.kill, timeout)
            held.exited = True
            held.exit_code = held.terminal.proc.poll()
            held.exited_at = time.time()
            frame = encode_json(EXIT, 0, {"id": held.id, "code": held.exit_code})
            for conn in list(held.subscribers):
                conn.send(frame)
            _log(f"killed {held.id}")
        self.sessions.pop(held.id, None)

    def _kill_now(self, held: _Held) -> None:
        if held.exited:
            return
        if held.reading:
            held.reading = False
            try:
                asyncio.get_running_loop().remove_reader(held.fd)
            except (ValueError, OSError):
                pass
        held.terminal.kill(0.5)
        held.exited = True
        held.exit_code = held.terminal.proc.poll()
        held.exited_at = time.time()

    def _prune(self) -> None:
        exited = sorted((held for held in self.sessions.values() if held.exited), key=lambda held: held.exited_at)
        cutoff = time.time() - EXITED_KEEP_S
        for index, held in enumerate(exited):
            if held.exited_at < cutoff or len(exited) - index > MAX_EXITED_KEPT:
                self.sessions.pop(held.id, None)


# -- process entry -------------------------------------------------------------------


def daemonize(log_path: Path) -> None:
    """Classic double fork: detach from the launcher's session and terminal; the launcher can reap and return."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir("/")
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 0)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        if log_path.exists() and log_path.stat().st_size > 256 * 1024:
            log_path.write_bytes(b"")
    except OSError:
        pass
    log_fd = os.open(log_path, flags, 0o600)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    if log_fd > 2:
        os.close(log_fd)
    if null > 2:
        os.close(null)


def run_holder(root: Path, *, foreground: bool = False, idle_exit_s: float = DEFAULT_IDLE_EXIT_S) -> int:
    if os.name != "posix":
        print("The PTY holder is only available on macOS and Linux.", file=sys.stderr)
        return 2
    root = Path(root).expanduser().resolve()
    directory = holder_dir(root)
    if not foreground:
        daemonize(directory / LOG_NAME)
    os.umask(0o077)
    return asyncio.run(HolderServer(root, idle_exit_s=idle_exit_s).run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="termx pty-holder", description="Detached holder of terminal sessions")
    parser.add_argument("action", nargs="?", choices=("run", "status", "stop"), default="run")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--foreground", action="store_true", help="do not detach (tests, debugging)")
    parser.add_argument("--idle-exit", type=float, default=None, help="seconds idle with no session before exiting")
    parser.add_argument("--kill", action="store_true", help="stop: end live sessions too (default drains)")
    args = parser.parse_args(argv)
    if args.config_dir:
        os.environ["TERMX_CONFIG_DIR"] = str(Path(args.config_dir).expanduser().resolve())
    from termx.config import config_dir

    root = config_dir()
    if args.action == "run":
        idle = args.idle_exit
        if idle is None:
            try:
                idle = float(os.environ.get("TERMX_PTY_HOLDER_IDLE_S", DEFAULT_IDLE_EXIT_S))
            except ValueError:
                idle = DEFAULT_IDLE_EXIT_S
        return run_holder(root, foreground=args.foreground, idle_exit_s=max(0.2, idle))
    from termx.pty_holder_client import describe, stop

    if args.action == "status":
        print(json.dumps(describe(root)))
        return 0
    print(json.dumps(stop(root, kill=args.kill)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
