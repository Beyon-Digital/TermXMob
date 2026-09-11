from __future__ import annotations

import asyncio
import re
import shutil
import time
from typing import Any

from termx.providers.base import ProviderStatus, append_log, terminate_process
from termx.tunnel import start_cloudflare_tunnel

NAMED_URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+")


def named_tunnel_cmd(exe: str, token: str) -> list[str]:
    return [exe, "tunnel", "--no-autoupdate", "run", "--token", token]


def _configured_url(extra: dict[str, Any]) -> str | None:
    raw = extra.get("url") or extra.get("hostname") or extra.get("host")
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"https://{value}"


class CloudflareProvider:
    id = "cloudflare"

    def __init__(self) -> None:
        self._status = ProviderStatus(provider=self.id, state="stopped")
        self._stop = None
        self._proc = None
        self._pump: asyncio.Task[None] | None = None
        self._log: list[str] = []
        self._started_at: float | None = None

    def detect(self) -> dict[str, Any]:
        exe = shutil.which("cloudflared")
        return {
            "id": self.id,
            "name": "Cloudflare",
            "available": exe is not None,
            "binary": exe,
            "kinds": ["quick", "named"],
            "install": "brew install cloudflare/cloudflare/cloudflared",
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
        elif state in {"stopped", "error"}:
            if state == "stopped":
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
        kind = profile.get("kind") or "quick"
        extra = profile.get("extra") or {}
        token = extra.get("token")
        self._snapshot("starting")
        if kind == "named" and isinstance(token, str) and token.strip():
            return await self._start_named(token.strip(), extra)
        url, stop, proc = await start_cloudflare_tunnel(port)
        self._stop = stop
        self._proc = proc
        if url:
            return self._snapshot("connected", url=url)
        await stop()
        self._stop = None
        self._proc = None
        return self._snapshot("error", detail="cloudflared did not publish a URL")

    async def _start_named(self, token: str, extra: dict[str, Any]) -> ProviderStatus:
        exe = shutil.which("cloudflared")
        if exe is None:
            return self._snapshot("error", detail="cloudflared is not installed")
        url = _configured_url(extra)
        self._proc = await asyncio.create_subprocess_exec(
            *named_tunnel_cmd(exe, token),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def pump() -> None:
            proc = self._proc
            if proc is None or proc.stdout is None:
                return
            leftover = b""
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                leftover += chunk
                while b"\n" in leftover:
                    line, leftover = leftover.split(b"\n", 1)
                    append_log(self._log, line.decode("utf-8", "replace"))
                if len(leftover) > 8000:
                    append_log(self._log, leftover.decode("utf-8", "replace"))
                    leftover = b""
                self._status.log = list(self._log)
                if self._status.state == "connected":
                    continue
                text = "\n".join(self._log[-40:])
                match = NAMED_URL_RE.search(text)
                found = url or (match.group(0) if match else None)
                if match or "Registered tunnel connection" in text:
                    self._snapshot("connected", url=found)

        self._pump = asyncio.create_task(pump())
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self._status.state == "connected":
                return self._status
            if self._proc.returncode is not None:
                break
            await asyncio.sleep(0.2)
        if self._proc is not None and self._proc.returncode is None:
            return self._snapshot("connected", url=url)
        await self.stop()
        return self._snapshot("error", detail="named tunnel exited")

    async def stop(self) -> None:
        if self._stop is None and self._proc is None and self._pump is None and self._status.state == "stopped":
            return
        current_url = self._status.url
        self._snapshot("stopping", url=current_url)
        pump = self._pump
        self._pump = None
        if pump is not None:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        stop = self._stop
        self._stop = None
        if stop is not None:
            await stop()
        proc = self._proc
        self._proc = None
        await terminate_process(proc)
        self._snapshot("stopped")
