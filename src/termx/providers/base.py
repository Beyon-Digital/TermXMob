from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

LOG_KEEP = 80
LOG_PUBLIC = 40

_REDACT = (
    (re.compile(r"(authtoken=)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(Bearer\s+)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"([?&]k=)[^&\s]+"), r"\1***"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{32,}\b"), "***"),
)


def redact_log_line(line: str) -> str:
    text = line.replace("\r", "").rstrip()
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


def append_log(log: list[str], text: str, *, limit: int = LOG_KEEP) -> None:
    for raw in text.splitlines():
        line = redact_log_line(raw)
        if line:
            log.append(line)
    overflow = len(log) - limit
    if overflow > 0:
        del log[:overflow]


async def terminate_process(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=1)
        except TimeoutError:
            pass


@dataclass
class ProviderStatus:
    provider: str
    state: str
    url: str | None = None
    detail: str | None = None
    log: list[str] = field(default_factory=list)
    started_at: float | None = None

    def public(self) -> dict[str, Any]:
        started = self.started_at
        uptime_s = max(0, int(time.time() - started)) if started is not None else None
        return {
            "provider": self.provider,
            "state": self.state,
            "url": self.url,
            "detail": self.detail,
            "log": [redact_log_line(line) for line in self.log[-LOG_PUBLIC:]],
            "started_at": started,
            "uptime_s": uptime_s,
        }


class TunnelProvider(Protocol):
    id: str

    def detect(self) -> dict[str, Any]: ...

    async def start(self, port: int, profile: dict[str, Any]) -> ProviderStatus: ...

    async def stop(self) -> None: ...

    def status(self) -> ProviderStatus: ...
