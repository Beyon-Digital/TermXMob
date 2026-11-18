"""Daemon-side access to the PTY holder (see ``pty_holder.py`` for the protocol and security model).

``HolderClient`` is one authenticated connection (peer uid verified on this end as well)
multiplexing every held terminal; a reader thread turns the holder's events into
``HeldTerminal`` queues. ``HolderSupervisor`` decides *whether* terminals are held
(setting, platform, health), starts or replaces the holder process, and records the
reason whenever it falls back to direct mode so the host can show it.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from termx.audit import log_event
from termx.pty_holder import (
    ATTACH,
    DEFAULT_IDLE_EXIT_S,
    ERR,
    EXIT,
    FORGET,
    HELLO,
    KILL,
    LIST,
    LOG_NAME,
    OK,
    OUTPUT,
    PROTOCOL,
    REPLAY,
    RESIZE,
    SHUTDOWN,
    SIGNAL,
    SOCKET_NAME,
    SPAWN,
    STREAM_CHUNK,
    TO_CLIENT_LIMITS,
    WRITE,
    Frame,
    FrameDecoder,
    ProtocolError,
    encode_frame,
    encode_json,
    holder_dir,
    holder_env,
    pack_stream,
    parse_json,
    peer_is_self,
    unpack_stream,
)

REQUEST_TIMEOUT_S = 5.0
START_TIMEOUT_S = 8.0
RETRY_AFTER_FAILURE_S = 30.0
RECONNECT_DELAYS = (0.2, 0.5, 1.0, 2.0)


class HolderError(Exception):
    """The holder is unreachable or refused: callers fall back or surface the message."""


class HolderNotRunning(HolderError):
    pass


class HolderIncompatible(HolderError):
    def __init__(self, info: dict[str, Any]) -> None:
        super().__init__(f"PTY holder speaks protocol {info.get('protocol')} (version {info.get('version')}), this host speaks {PROTOCOL}")
        self.info = info


class HolderRefused(HolderError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(eq=False)
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    replay: list[bytes] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: HolderError | None = None
    on_ok: Callable[[dict[str, Any]], None] | None = None


def _open(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(REQUEST_TIMEOUT_S)
    try:
        sock.connect(str(path))
    except (FileNotFoundError, ConnectionRefusedError, NotADirectoryError) as exc:
        sock.close()
        raise HolderNotRunning("no PTY holder is listening") from exc
    except OSError as exc:
        sock.close()
        raise HolderError(f"could not reach the PTY holder: {exc.strerror or exc}") from exc
    if not peer_is_self(sock):
        sock.close()
        raise HolderError("the PTY holder socket is not owned by this user; refusing to use it")
    return sock


def _read_until(sock: socket.socket, decoder: FrameDecoder, request: int, deadline: float) -> tuple[list[bytes], Frame, list[Frame]]:
    """Read frames until the reply to ``request``: (replay chunks before it, the reply, frames after it in the same read)."""
    replay: list[bytes] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HolderError("the PTY holder did not answer")
        sock.settimeout(remaining)
        try:
            data = sock.recv(1 << 16)
        except socket.timeout:
            raise HolderError("the PTY holder did not answer") from None
        except OSError as exc:
            raise HolderError(f"the PTY holder connection failed: {exc.strerror or exc}") from None
        if not data:
            raise HolderError("the PTY holder closed the connection")
        try:
            frames = decoder.feed(data)
        except ProtocolError as exc:
            raise HolderError(f"the PTY holder broke the protocol: {exc}") from None
        for index, frame in enumerate(frames):
            if frame.kind == REPLAY and frame.request == request:
                replay.append(unpack_stream(frame.payload)[1])
            elif frame.kind in (OK, ERR) and frame.request == request:
                return replay, frame, frames[index + 1 :]
            elif frame.kind == ERR and frame.request == 0:
                raise _refusal(frame)


def _refusal(frame: Frame) -> HolderError:
    try:
        body = parse_json(frame.payload)
    except ProtocolError:
        return HolderError("the PTY holder refused the request")
    code, message = str(body.get("code", "")), str(body.get("message", ""))
    if code == "protocol":
        try:
            return HolderIncompatible(json.loads(message))
        except ValueError:
            return HolderIncompatible({})
    return HolderRefused(code, message or "the PTY holder refused the request")


class HolderClient:
    def __init__(self, directory: Path, *, version: str) -> None:
        self.directory = Path(directory)
        self.socket_path = self.directory / SOCKET_NAME
        self.version = version
        self.info: dict[str, Any] = {}
        self.alive = False
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._state = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._sinks: dict[str, Any] = {}
        self._next = 1
        self._closing = False
        self._thread: threading.Thread | None = None

    # -- connection --------------------------------------------------------------

    def connect(self) -> dict[str, Any]:
        sock = _open(self.socket_path)
        decoder = FrameDecoder(TO_CLIENT_LIMITS)
        try:
            self.info = self._handshake(sock, decoder)
        except BaseException:
            sock.close()
            raise
        sock.settimeout(1.0)
        self._sock = sock
        self.alive = True
        self._thread = threading.Thread(target=self._run, args=(decoder,), name="pty-holder-client", daemon=True)
        self._thread.start()
        return self.info

    def _handshake(self, sock: socket.socket, decoder: FrameDecoder) -> dict[str, Any]:
        sock.sendall(encode_json(HELLO, 1, {"protocol": PROTOCOL, "version": self.version, "pid": os.getpid()}))
        _, frame, _ = _read_until(sock, decoder, 1, time.monotonic() + REQUEST_TIMEOUT_S)
        if frame.kind == ERR:
            raise _refusal(frame)
        return parse_json(frame.payload)

    def close(self) -> None:
        """Drop the connection. Held sessions keep running in the holder."""
        self._closing = True
        self.alive = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._fail_pending(HolderError("the PTY holder connection was closed"))
        self._lose_sinks()

    # -- requests ----------------------------------------------------------------

    def _send(self, frame: bytes) -> None:
        sock = self._sock
        if sock is None or not self.alive:
            raise HolderError("the PTY holder connection is closed")
        with self._send_lock:
            try:
                sock.sendall(frame)
            except OSError as exc:
                raise HolderError(f"could not reach the PTY holder: {exc.strerror or exc}") from None

    def request(self, kind: int, payload: dict[str, Any], *, timeout: float = REQUEST_TIMEOUT_S, on_ok: Callable[[dict[str, Any]], None] | None = None) -> tuple[dict[str, Any], bytes]:
        pending = _Pending(on_ok=on_ok)
        with self._state:
            request = self._next
            self._next = request + 1 if request < 0xFFFFFFF0 else 2
            self._pending[request] = pending
        try:
            self._send(encode_json(kind, request, payload))
            if not pending.event.wait(timeout):
                raise HolderError("the PTY holder did not answer")
        finally:
            with self._state:
                self._pending.pop(request, None)
        if pending.error is not None:
            raise pending.error
        return pending.result or {}, b"".join(pending.replay)

    def list(self) -> dict[str, Any]:
        return self.request(LIST, {})[0]

    def spawn(self, sink: Any, argv: list[str], cwd: str, env: dict[str, str], rows: int, cols: int, meta: dict[str, Any]) -> dict[str, Any]:
        with self._state:
            self._sinks[sink.sid] = sink
        try:
            return self.request(SPAWN, {"id": sink.sid, "argv": argv, "cwd": cwd, "env": env, "rows": rows, "cols": cols, "meta": meta})[0]
        except BaseException:
            with self._state:
                self._sinks.pop(sink.sid, None)
            raise

    def attach(self, sink: Any, since: int | None = None) -> tuple[dict[str, Any], bytes]:
        """Subscribe ``sink`` to a held session; returns (info, replay bytes) and leaves live output queued on the sink."""
        with self._state:
            self._sinks[sink.sid] = sink

        def mark(info: dict[str, Any]) -> None:
            sink.offset = int(info.get("end", 0))  # set in the reader thread, before any later frame is fed

        try:
            return self.request(ATTACH, {"id": sink.sid, "since": since}, on_ok=mark)
        except BaseException:
            with self._state:
                self._sinks.pop(sink.sid, None)
            raise

    def write(self, sid: str, data: bytes) -> None:
        for offset in range(0, len(data), STREAM_CHUNK):
            self._send(encode_frame(WRITE, 0, pack_stream(sid, data[offset : offset + STREAM_CHUNK])))

    def resize(self, sid: str, rows: int, cols: int) -> None:
        self._send(encode_json(RESIZE, 0, {"id": sid, "rows": rows, "cols": cols}))

    def signal(self, sid: str, name: str) -> bool:
        return bool(self.request(SIGNAL, {"id": sid, "name": name})[0].get("ok"))

    def kill(self, sid: str, timeout: float) -> None:
        try:
            self.request(KILL, {"id": sid, "timeout": timeout}, timeout=timeout + REQUEST_TIMEOUT_S)
        except HolderRefused as exc:
            if exc.code != "not_found":
                raise
        finally:
            with self._state:
                self._sinks.pop(sid, None)

    def forget(self, sid: str) -> None:
        self.request(FORGET, {"id": sid})
        with self._state:
            self._sinks.pop(sid, None)

    def release(self, sid: str) -> None:
        """Stop routing events for a session to this process (it keeps running in the holder)."""
        with self._state:
            self._sinks.pop(sid, None)

    def shutdown(self, mode: str = "drain") -> dict[str, Any]:
        return self.request(SHUTDOWN, {"mode": mode})[0]

    # -- reader thread -----------------------------------------------------------

    def _run(self, decoder: FrameDecoder) -> None:
        while not self._closing:
            sock = self._sock
            if sock is None:
                return
            try:
                data = sock.recv(1 << 16)
                if not data:
                    raise OSError("closed")
                frames = decoder.feed(data)
            except socket.timeout:
                continue
            except (OSError, ProtocolError):
                if self._closing:
                    return
                self.alive = False
                self._fail_pending(HolderError("the PTY holder connection was lost"))
                decoder = self._reconnect() or decoder
                if not self.alive:
                    self._lose_sinks()
                    return
                continue
            for frame in frames:
                self._dispatch(frame)

    def _dispatch(self, frame: Frame) -> None:
        kind = frame.kind
        if kind == OUTPUT:
            try:
                sid, data = unpack_stream(frame.payload)
            except ProtocolError:
                return
            sink = self._sinks.get(sid)
            if sink is not None:
                sink._feed(data)
        elif kind == EXIT:
            try:
                body = parse_json(frame.payload)
            except ProtocolError:
                return
            sink = self._sinks.get(str(body.get("id")))
            if sink is not None:
                code = body.get("code")
                sink._set_exit(code if isinstance(code, int) and not isinstance(code, bool) else None)
        elif kind == REPLAY:
            pending = self._pending.get(frame.request)
            if pending is not None:
                try:
                    pending.replay.append(unpack_stream(frame.payload)[1])
                except ProtocolError:
                    pass
        elif kind in (OK, ERR):
            pending = self._pending.get(frame.request)
            if pending is None:
                return
            if kind == ERR:
                pending.error = _refusal(frame)
            else:
                try:
                    pending.result = parse_json(frame.payload)
                except ProtocolError:
                    pending.error = HolderError("the PTY holder sent an unreadable reply")
                else:
                    if pending.on_ok is not None:
                        pending.on_ok(pending.result)
            pending.event.set()

    def _fail_pending(self, error: HolderError) -> None:
        with self._state:
            pending = list(self._pending.values())
        for item in pending:
            if not item.event.is_set():
                item.error = error
                item.event.set()

    def _lose_sinks(self) -> None:
        with self._state:
            sinks, self._sinks = list(self._sinks.values()), {}
        for sink in sinks:
            sink._set_lost()

    def _reconnect(self) -> FrameDecoder | None:
        """The connection dropped but the holder may be fine: re-attach every sink and resume from its offset."""
        old, self._sock = self._sock, None
        if old is not None:
            try:
                old.close()
            except OSError:
                pass
        for delay in RECONNECT_DELAYS:
            if self._closing:
                return None
            time.sleep(delay)
            try:
                sock = _open(self.socket_path)
                decoder = FrameDecoder(TO_CLIENT_LIMITS)
                self.info = self._handshake(sock, decoder)
                with self._state:
                    sinks = list(self._sinks.values())
                backlog: list[Frame] = []
                for index, sink in enumerate(sinks, start=2):
                    sock.sendall(encode_json(ATTACH, index, {"id": sink.sid, "since": sink.offset}))
                    replay, frame, rest = _read_until(sock, decoder, index, time.monotonic() + REQUEST_TIMEOUT_S)
                    backlog.extend(rest)
                    if frame.kind == ERR:
                        sink._set_lost()  # the holder no longer has it: the process is gone
                        continue
                    info = parse_json(frame.payload)
                    for chunk in replay:
                        sink._feed(chunk)
                    sink.offset = int(info.get("end", sink.offset))
                    if not info.get("alive", True):
                        sink._set_exit(info.get("exit_code"))
                sock.settimeout(1.0)
                self._sock = sock
                self.alive = True
                for frame in backlog:
                    self._dispatch(frame)
                log_event("pty_holder_reconnected", sessions=len(sinks))
                return decoder
            except (HolderError, OSError):
                continue
        log_event("pty_holder_lost")
        return None


# -- supervisor ----------------------------------------------------------------------

_TRUE = {"1", "true", "yes", "on", "held"}
_FALSE = {"0", "false", "no", "off", "direct"}


def resolve_mode(setting: str | None = None) -> str:
    """``TERMX_PTY_HOLDER`` (operations, tests) over the saved host setting; one of auto, held, direct."""
    raw = (os.environ.get("TERMX_PTY_HOLDER") or setting or "auto").strip().lower()
    if raw in _TRUE:
        return "held"
    if raw in _FALSE:
        return "direct"
    return "auto"


class HolderSupervisor:
    """Whether new terminals are held, and the one place that starts, replaces and talks to the holder."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        setting: Callable[[], str | None] | None = None,
        version: str | None = None,
        idle_exit_s: float | None = None,
        command: list[str] | None = None,
    ) -> None:
        from termx import __version__
        from termx.config import config_dir

        self.root = Path(root or config_dir()).expanduser().resolve()
        self.version = version or __version__
        self.idle_exit_s = idle_exit_s if idle_exit_s is not None else _env_float("TERMX_PTY_HOLDER_IDLE_S", DEFAULT_IDLE_EXIT_S)
        self._setting = setting
        self._command = command
        self._lock = threading.RLock()
        self._client: HolderClient | None = None
        self.reason: str | None = None
        self._retry_at = 0.0
        self._logged: str | None = None
        self.draining = False

    # -- policy ------------------------------------------------------------------

    @property
    def requested(self) -> str:
        return resolve_mode(self._setting() if self._setting else None)

    def unsupported(self) -> str | None:
        if os.name != "posix":
            return "the PTY holder is not supported on Windows; terminals end when the host stops"
        return None

    def _directory(self) -> Path:
        return holder_dir(self.root)

    def acquire(self, *, create: bool) -> HolderClient | None:
        """A connected client, or None for direct mode.

        ``create`` is for a new terminal: it honours the mode, starts a holder if needed and, when the mode is
        ``held``, raises instead of falling back. Without ``create`` it only looks for a holder that is already
        running (to re-attach what survived a restart).
        """
        with self._lock:
            client = self._client
            if client is not None and not client.alive:
                self._client = client = None
            mode = self.requested
            why = self.unsupported()
            if why is not None:
                self.reason = why
                return None
            if create and mode == "direct":
                self.reason = "held terminals are turned off (TERMX_PTY_HOLDER or the host setting)"
                return None
            if client is not None:
                if create:
                    self.reason = None
                return client
            if create and time.monotonic() < self._retry_at:
                if mode == "held":
                    raise HolderError(f"the PTY holder is required (held mode) but unavailable: {self.reason}")
                return None  # recently failed: stay direct (the reason is already recorded) until the retry time
            try:
                client = self._connect(start=create)
            except HolderNotRunning:
                return None
            except HolderError as exc:
                return self._fail(mode, str(exc), create)
            self._client = client
            if create:
                self.reason = None
                self._logged = None
            return client

    def _fail(self, mode: str, detail: str, create: bool) -> None:
        reason = f"terminals will not survive a restart: {detail}"
        self.reason = reason
        self._retry_at = time.monotonic() + RETRY_AFTER_FAILURE_S
        if self._logged != reason:
            self._logged = reason
            log_event("pty_holder_unavailable", mode=mode, reason=detail[:300])
        if create and mode == "held":
            raise HolderError(f"the PTY holder is required (held mode) but unavailable: {detail}")
        return None

    def _connect(self, *, start: bool) -> HolderClient:
        directory = self._directory()
        client = HolderClient(directory, version=self.version)
        try:
            info = client.connect()
        except HolderNotRunning:
            if not start:
                raise
            self._start_process()
            return self._connect_wait(directory)
        except HolderIncompatible as exc:
            return self._replace_incompatible(directory, exc.info, start)
        if start and info.get("version") != self.version and not info.get("live") and not info.get("draining"):
            # An old holder with nothing running: swap it for this version now, while that costs nothing.
            try:
                client.shutdown("now")
            finally:
                client.close()
            self._wait_gone(directory)
            self._start_process()
            return self._connect_wait(directory)
        self.draining = bool(info.get("draining"))
        if self.draining and start:
            client.close()
            raise HolderError("an older PTY holder is draining its sessions; new terminals are direct until it exits")
        return client

    def _connect_wait(self, directory: Path) -> HolderClient:
        deadline = time.monotonic() + START_TIMEOUT_S
        last: Exception | None = None
        while time.monotonic() < deadline:
            client = HolderClient(directory, version=self.version)
            try:
                client.connect()
                return client
            except HolderNotRunning as exc:
                last = exc
                time.sleep(0.05)
        tail = self._log_tail(directory)
        raise HolderError("the PTY holder did not start" + (f" ({tail})" if tail else "")) from last

    def _replace_incompatible(self, directory: Path, info: dict[str, Any], start: bool) -> HolderClient:
        probe = HolderClient(directory, version=self.version)
        sock = _open(probe.socket_path)
        try:
            sock.sendall(encode_json(SHUTDOWN, 2, {"mode": "drain"}))
            decoder = FrameDecoder(TO_CLIENT_LIMITS)
            _, frame, _ = _read_until(sock, decoder, 2, time.monotonic() + REQUEST_TIMEOUT_S)
        finally:
            sock.close()
        live = parse_json(frame.payload).get("live", 0) if frame.kind == OK else 1
        if live:
            raise HolderError(
                f"an older PTY holder (version {info.get('version')}) still owns {live} session(s) and speaks protocol "
                f"{info.get('protocol')}; it is draining and new terminals are direct until it exits"
            )
        self._wait_gone(directory)
        if not start:
            raise HolderNotRunning("no PTY holder is listening")
        self._start_process()
        return self._connect_wait(directory)

    def _wait_gone(self, directory: Path) -> None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                sock = _open(directory / SOCKET_NAME)
            except HolderError:
                return
            sock.close()
            time.sleep(0.05)

    def holder_command(self) -> list[str]:
        if self._command is not None:
            return list(self._command)
        base = [sys.executable, "pty-holder"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "termx.pty_holder"]
        return [*base, "run", "--config-dir", str(self.root), "--idle-exit", str(self.idle_exit_s)]

    def _start_process(self) -> None:
        proc = subprocess.Popen(
            self.holder_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=holder_env(self.root),
            start_new_session=True,
            close_fds=True,
        )
        try:
            code = proc.wait(timeout=START_TIMEOUT_S)  # the launcher double-forks and returns at once
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise HolderError("the PTY holder launcher did not return") from None
        if code != 0:
            detail = b""
            try:
                os.set_blocking(proc.stderr.fileno(), False)  # type: ignore[union-attr]
                detail = proc.stderr.read(2000) or b""  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass
            finally:
                proc.stderr.close()  # type: ignore[union-attr]
            raise HolderError(f"the PTY holder exited with status {code}" + (f": {detail.decode('utf-8', 'replace').strip()[-300:]}" if detail else ""))
        if proc.stderr is not None:
            proc.stderr.close()
        log_event("pty_holder_started", version=self.version)

    @staticmethod
    def _log_tail(directory: Path) -> str:
        try:
            data = (directory / LOG_NAME).read_bytes()[-400:]
        except OSError:
            return ""
        return data.decode("utf-8", "replace").strip().splitlines()[-1][:200] if data.strip() else ""

    # -- status ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Holder state for the host health surface. Never starts a holder."""
        with self._lock:
            client = self._client if self._client is not None and self._client.alive else None
            requested = self.requested
            unsupported = self.unsupported()
        out: dict[str, Any] = {
            "mode": "direct", "requested": requested, "reason": None, "running": False, "pid": None, "version": None,
            "protocol": PROTOCOL, "sessions": 0, "exited_sessions": 0, "draining": False,
        }
        if unsupported:
            out["reason"] = unsupported
            return out
        if client is not None:
            try:
                listing = client.list()
            except HolderError as exc:
                out["reason"] = f"the PTY holder is not answering: {exc}"
                return out
            sessions = listing.get("sessions", [])
            live = sum(1 for item in sessions if item.get("alive"))
            out.update(running=True, pid=client.info.get("pid"), version=client.info.get("version"), sessions=live,
                       exited_sessions=len(sessions) - live, draining=bool(listing.get("draining")))
            if requested != "direct" and not out["draining"]:
                out["mode"] = "held"
                return out
            out["reason"] = self.reason or ("held terminals are turned off; existing held sessions keep running" if requested == "direct" else None)
            return out
        out["reason"] = self.reason or (
            "held terminals are turned off (TERMX_PTY_HOLDER or the host setting)" if requested == "direct" else
            "no holder is running yet; it starts with the first terminal"
        )
        if requested != "direct" and self.reason is None:
            out["mode"] = "held"  # will start on first use
        return out

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# -- command-line helpers ------------------------------------------------------------


def describe(root: Path) -> dict[str, Any]:
    client = HolderClient(holder_dir(root), version="cli")
    try:
        info = client.connect()
        listing = client.list()
    except HolderIncompatible as exc:
        return {"running": True, "compatible": False, **{k: v for k, v in exc.info.items() if k in ("protocol", "version", "live")}}
    except HolderError as exc:
        return {"running": False, "detail": str(exc)}
    finally:
        client.close()
    return {"running": True, "compatible": True, "pid": info.get("pid"), "version": info.get("version"),
            "live": listing.get("live"), "sessions": len(listing.get("sessions", []))}


def stop(root: Path, *, kill: bool = False) -> dict[str, Any]:
    client = HolderClient(holder_dir(root), version="cli")
    try:
        client.connect()
        return {"stopped": True, **client.shutdown("now" if kill else "drain")}
    except HolderIncompatible:
        pass
    except HolderError as exc:
        return {"stopped": False, "detail": str(exc)}
    finally:
        client.close()
    sock = _open(holder_dir(root) / SOCKET_NAME)
    try:
        sock.sendall(encode_json(SHUTDOWN, 2, {"mode": "now" if kill else "drain"}))
        _, frame, _ = _read_until(sock, FrameDecoder(TO_CLIENT_LIMITS), 2, time.monotonic() + REQUEST_TIMEOUT_S)
    finally:
        sock.close()
    return {"stopped": frame.kind == OK, **(parse_json(frame.payload) if frame.kind == OK else {})}
