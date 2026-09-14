from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from termx.config import ConfigStore, ForwardRule

STDERR_TAIL = 24


def ssh_binary() -> str:
    override = os.environ.get("TERMX_SSH_BIN")
    if override:
        return override
    return shutil.which("ssh") or "ssh"


@dataclass
class ForwardStatus:
    id: str
    state: str = "stopped"
    detail: str | None = None
    started_at: float | None = None
    log: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "detail": self.detail,
            "started_at": self.started_at,
            "uptime_s": round(time.time() - self.started_at, 1) if self.started_at else None,
            "log": self.log[-8:],
        }


def ssh_args(rule: ForwardRule) -> list[str]:
    if rule.kind == "dynamic":
        forward = f"-D{rule.listen_host}:{rule.listen_port}"
    elif rule.kind == "remote":
        forward = f"-R{rule.listen_host}:{rule.listen_port}:{rule.target_host}:{rule.target_port}"
    else:
        forward = f"-L{rule.listen_host}:{rule.listen_port}:{rule.target_host}:{rule.target_port}"
    return [
        ssh_binary(),
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        forward,
        rule.ssh_host,
    ]


class ForwardManager:
    """Runs ssh forward processes on a dedicated event loop thread.

    Requests may be served from different loops (uvicorn, TestClient), so the
    subprocesses and their monitors must not be bound to a caller's loop.
    """

    def __init__(self, store: ConfigStore) -> None:
        self.store = store
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._status: dict[str, ForwardStatus] = {}
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="termx-forwards")
        self._thread.start()

    def _run(self, coro: Any, timeout: float = 30.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def statuses(self) -> list[dict[str, Any]]:
        return [status.public() for status in self._status.values()]

    def status_for(self, rule_id: str) -> ForwardStatus:
        return self._status.setdefault(rule_id, ForwardStatus(id=rule_id))

    def start(self, rule: ForwardRule) -> ForwardStatus:
        return self._run(self._start(rule))

    def stop(self, rule_id: str) -> bool:
        return self._run(self._stop(rule_id))

    def start_auto(self) -> list[str]:
        return self._run(self._start_auto())

    def stop_all(self) -> None:
        self._run(self._stop_all())

    async def _start(self, rule: ForwardRule) -> ForwardStatus:
        async with self._lock:
            existing = self._procs.get(rule.id)
            if existing is not None and existing.returncode is None:
                return self.status_for(rule.id)
            status = self.status_for(rule.id)
            status.state = "starting"
            status.detail = None
            status.started_at = time.time()
            status.log = []
            argv = ssh_args(rule)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                status.state = "error"
                status.started_at = None
                status.detail = f"cannot run ssh: {exc}"
                return status
            self._procs[rule.id] = proc
            status.state = "running"
            monitor = asyncio.create_task(self._monitor(rule.id, proc))
            self._monitors[rule.id] = monitor
            return status

    async def _monitor(self, rule_id: str, proc: asyncio.subprocess.Process) -> None:
        status = self.status_for(rule_id)
        assert proc.stderr is not None
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text:
                    status.log.append(text)
                    if len(status.log) > STDERR_TAIL:
                        status.log = status.log[-STDERR_TAIL:]
        except asyncio.CancelledError:
            raise
        code = await proc.wait()
        if self._procs.get(rule_id) is proc:
            self._procs.pop(rule_id, None)
            status.state = "error" if code else "stopped"
            status.detail = status.log[-1] if code and status.log else (f"ssh exited ({code})" if code else None)
            status.started_at = None

    async def _stop(self, rule_id: str) -> bool:
        async with self._lock:
            proc = self._procs.pop(rule_id, None)
            monitor = self._monitors.pop(rule_id, None)
            if monitor is not None and not monitor.done():
                monitor.cancel()
            status = self.status_for(rule_id)
            if proc is None or proc.returncode is not None:
                status.state = "stopped"
                status.started_at = None
                return False
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            status.state = "stopped"
            status.started_at = None
            status.detail = None
            return True

    async def _start_auto(self) -> list[str]:
        started: list[str] = []
        for rule in self.store.list_rules():
            if not rule.auto_start:
                continue
            status = await self._start(rule)
            if status.state == "running":
                started.append(rule.id)
        return started

    async def _stop_all(self) -> None:
        for rule_id in list(self._procs):
            await self._stop(rule_id)
