"""Bounded JSON-RPC 2.0 client over stdio JSONL (codex app-server wire).

Codex app-server speaks JSON-RPC 2.0 semantics with the ``"jsonrpc"`` member
omitted on the wire: newline-delimited ``{method, params, id}`` requests,
``{id, result|error}`` responses, ``{method, params}`` notifications, and
server→client requests the client must answer. ``write_jsonrpc=True`` adds the
version member for peers that require it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_PENDING = 512

NotificationHandler = Callable[[str, dict[str, Any]], None]
ServerRequestHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class TransportClosed(Exception):
    pass


class JsonlProcess:
    """One stdio JSONL peer. Owns the subprocess lifecycle."""

    def __init__(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        on_notification: NotificationHandler | None = None,
        on_server_request: ServerRequestHandler | None = None,
        on_close: Callable[[], None] | None = None,
        write_jsonrpc: bool = False,
        stderr_limit: int = 64 * 1024,
    ) -> None:
        self._argv = argv
        self._env = env
        self._cwd = cwd
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._on_close = on_close
        self._write_jsonrpc = write_jsonrpc
        self._stderr_limit = stderr_limit
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._stderr_tail = ""
        self.exit_code: int | None = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def stderr_tail(self) -> str:
        return self._stderr_tail

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
            limit=MAX_LINE_BYTES,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def request(self, method: str, params: dict[str, Any] | None = None,
                      *, timeout: float = 60.0) -> Any:
        if not self.running:
            raise TransportClosed(f"process exited ({self.exit_code}): {self._stderr_tail[-400:]}")
        if len(self._pending) >= MAX_PENDING:
            raise JsonRpcError(-32001, "client-side pending-request bound reached")
        self._next_id += 1
        req_id = self._next_id
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict[str, Any] = {"method": method, "id": req_id}
        if self._write_jsonrpc:
            msg["jsonrpc"] = "2.0"
        if params is not None:
            msg["params"] = params
        try:
            await self._send(msg)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.running:
            raise TransportClosed("process not running")
        msg: dict[str, Any] = {"method": method}
        if self._write_jsonrpc:
            msg["jsonrpc"] = "2.0"
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    async def respond(self, req_id: Any, result: Any = None, *,
                      error: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"id": req_id}
        if self._write_jsonrpc:
            msg["jsonrpc"] = "2.0"
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        await self._send(msg)

    async def _send(self, msg: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    asyncio.create_task(self._dispatch_server_request(msg))
                elif "method" in msg:
                    if self._on_notification:
                        try:
                            self._on_notification(str(msg["method"]), msg.get("params") or {})
                        except Exception:
                            pass
                elif "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is None or fut.done():
                        continue
                    if "error" in msg and msg["error"] is not None:
                        err = msg["error"]
                        fut.set_exception(JsonRpcError(
                            int(err.get("code", -32000)),
                            str(err.get("message", "rpc error")),
                            err.get("data"),
                        ))
                    else:
                        fut.set_result(msg.get("result"))
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            pass
        finally:
            self.exit_code = self._proc.returncode
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(TransportClosed("engine process closed stdout"))
            self._pending.clear()
            self._closed.set()
            if self._on_close:
                try:
                    self._on_close()
                except Exception:
                    pass

    async def _dispatch_server_request(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        method = str(msg.get("method", ""))
        params = msg.get("params") or {}
        if self._on_server_request is None:
            await self._safe_respond(req_id, error={"code": -32601, "message": "unsupported"})
            return
        try:
            result = await self._on_server_request(method, params)
            await self._safe_respond(req_id, result=result)
        except JsonRpcError as exc:
            await self._safe_respond(req_id, error={"code": exc.code, "message": str(exc)})
        except Exception as exc:  # never let a handler wedge the reader loop
            await self._safe_respond(req_id, error={"code": -32603, "message": str(exc)[:500]})

    async def _safe_respond(self, req_id: Any, **kw: Any) -> None:
        try:
            await self.respond(req_id, **kw)
        except (TransportClosed, OSError, asyncio.CancelledError):
            pass

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            while True:
                chunk = await self._proc.stderr.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                self._stderr_tail = (self._stderr_tail + text)[-self._stderr_limit:]
        except (OSError, ValueError):
            pass

    async def close(self, *, kill_after: float = 3.0) -> None:
        if self._proc is None:
            return
        if self._proc.stdin:
            try:
                self._proc.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=kill_after)
        except TimeoutError:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=kill_after)
            except TimeoutError:
                self._proc.kill()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=kill_after)
                except TimeoutError:
                    pass
        self.exit_code = self._proc.returncode
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        self._closed.set()

    async def wait_closed(self, timeout: float = 10.0) -> bool:
        try:
            await asyncio.wait_for(self._closed.wait(), timeout)
            return True
        except TimeoutError:
            return False


def redact_argv(argv: list[str]) -> list[str]:
    """argv snapshot for diagnostics — masks values after secret-looking flags."""
    out: list[str] = []
    mask_next = False
    for arg in argv:
        if mask_next:
            out.append("***")
            mask_next = False
            continue
        lowered = arg.lower()
        if any(k in lowered for k in ("key", "token", "secret", "password")):
            if "=" in arg:
                out.append(arg.split("=", 1)[0] + "=***")
            else:
                out.append(arg)
                mask_next = True
            continue
        out.append(arg)
    return out


def diagnostics_safe_env(env: dict[str, str]) -> dict[str, str]:
    """Keys only, with presence marker — never values."""
    return {k: "<set>" for k in sorted(env)}


def platform_spawn_env() -> dict[str, str]:
    import os
    env = dict(os.environ)
    if sys.platform == "darwin":
        env.setdefault("TERM", "xterm-256color")
    env.pop("TERMX_PASSCODE", None)
    env.pop("TERMX_TOKEN", None)
    env["TERMX_ENGINE_CHILD"] = str(int(time.time()))
    return env
