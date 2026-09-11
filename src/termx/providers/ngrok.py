from __future__ import annotations

import asyncio
import re
import shutil
import time
from typing import Any

from termx.providers.base import ProviderStatus, append_log, terminate_process

NGROK_URL_RE = re.compile(r"https://[a-z0-9-]+\.ngrok[-a-z0-9]*\.(app|io)")


class NgrokProvider:
    id = "ngrok"

    def __init__(self) -> None:
        self._status = ProviderStatus(provider=self.id, state="stopped")
        self._proc: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None
        self._buf = b""
        self._log: list[str] = []
        self._started_at: float | None = None

    def detect(self) -> dict[str, Any]:
        exe = shutil.which("ngrok")
        return {
            "id": self.id,
            "name": "ngrok",
            "available": exe is not None,
            "binary": exe,
            "kinds": ["http"],
            "install": "https://ngrok.com/download",
        }

    def status(self) -> ProviderStatus:
        self._status.log = list(self._log)
        self._status.started_at = self._started_at
        return self._status

    def _snapshot(self, state: str, *, url: str | None = None, detail: str | None = None) -> ProviderStatus:
        started = self._started_at
        if state == "connected":
            if started is None:
                started = time.time()
                self._started_at = started
        elif state == "stopped":
            started = None
            self._started_at = None
        self._status = ProviderStatus(
            provider=self.id,
            state=state,
            url=url,
            detail=detail,
            log=list(self._log),
            started_at=started,
        )
        return self._status

    async def start(self, port: int, profile: dict[str, Any]) -> ProviderStatus:
        await self.stop()
        self._log = []
        self._started_at = None
        exe = shutil.which("ngrok")
        if exe is None:
            return self._snapshot("error", detail="ngrok is not installed")
        extra = profile.get("extra") or {}
        args = [exe, "http", str(port), "--log", "stdout"]
        if extra.get("authtoken"):
            args.extend(["--authtoken", str(extra["authtoken"])])
        domain = extra.get("domain")
        if domain:
            args.extend(["--url", str(domain)])
        self._snapshot("starting")
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._buf = b""

        async def pump() -> None:
            assert self._proc is not None and self._proc.stdout is not None
            while True:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
                self._buf = (self._buf + chunk)[-12000:]
                append_log(self._log, chunk.decode("utf-8", "replace"))
                self._status.log = list(self._log)
                text = self._buf.decode("utf-8", "replace")
                match = NGROK_URL_RE.search(text)
                if match and self._status.state != "connected":
                    self._snapshot("connected", url=match.group(0))

        self._pump = asyncio.create_task(pump())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._status.state == "connected":
                return self._status
            if self._proc.returncode is not None:
                break
            await asyncio.sleep(0.2)
        if self._status.state != "connected":
            await self.stop()
            return self._snapshot("error", detail="ngrok did not publish a URL")
        return self._status

    async def stop(self) -> None:
        if self._proc is None and self._pump is None and self._status.state == "stopped":
            return
        self._snapshot("stopping", url=self._status.url)
        pump = self._pump
        self._pump = None
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        proc = self._proc
        self._proc = None
        await terminate_process(proc)
        self._snapshot("stopped")
