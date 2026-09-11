from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from typing import Any

from termx.providers.base import ProviderStatus, append_log

URL_RE = re.compile(r"https://[a-zA-Z0-9.-]+")


class TailscaleProvider:
    id = "tailscale"

    def __init__(self) -> None:
        self._status = ProviderStatus(provider=self.id, state="stopped")
        self._kind = "funnel"
        self._log: list[str] = []
        self._started_at: float | None = None

    def detect(self) -> dict[str, Any]:
        exe = shutil.which("tailscale")
        return {
            "id": self.id,
            "name": "Tailscale",
            "available": exe is not None,
            "binary": exe,
            "kinds": ["serve", "funnel"],
            "install": "https://tailscale.com/download",
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
        exe = shutil.which("tailscale")
        if exe is None:
            return self._snapshot("error", detail="tailscale is not installed")
        kind = profile.get("kind") or "funnel"
        self._kind = kind if kind in {"serve", "funnel"} else "funnel"
        self._snapshot("starting")
        args = [exe, self._kind, "--bg", f"{port}"]
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        text = (out or b"").decode("utf-8", "replace")
        append_log(self._log, text)
        if proc.returncode not in {0, None}:
            return self._snapshot("error", detail=text.strip() or "tailscale serve/funnel failed")
        url = await self._read_url(exe)
        if url:
            return self._snapshot("connected", url=url)
        match = URL_RE.search(text)
        if match:
            return self._snapshot("connected", url=match.group(0))
        return self._snapshot("error", detail="tailscale did not publish a URL")

    async def _read_url(self, exe: str) -> str | None:
        proc = await asyncio.create_subprocess_exec(
            exe,
            self._kind,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = (out or b"").decode("utf-8", "replace")
        append_log(self._log, text)
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                for value in payload.values():
                    if isinstance(value, str) and value.startswith("https://"):
                        return value
        except json.JSONDecodeError:
            pass
        match = URL_RE.search(text)
        return match.group(0) if match else None

    async def stop(self) -> None:
        if self._status.state == "stopped":
            return
        exe = shutil.which("tailscale")
        self._snapshot("stopping", url=self._status.url)
        if exe:
            proc = await asyncio.create_subprocess_exec(
                exe,
                self._kind,
                "reset",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        self._snapshot("stopped")
