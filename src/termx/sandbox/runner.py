"""SandboxRunner: the single spawn surface for every Termx execution profile.

Callers build a validated ``SpawnSpec``; the backend owns launch, streaming,
tree-kill, and truthful capability reporting. Implementations must never
silently degrade a restricted profile to host execution — if the backend
cannot honor the requested isolation, ``spawn`` raises ``SandboxFailure``.
"""
from __future__ import annotations

import asyncio
import signal
from typing import Any, Protocol

from termx.sandbox.models import (
    ResourceLimits,
    SandboxCapabilities,
    SandboxFailure,
    SpawnSpec,
)


class _ProcessGroup:
    """Kill a whole spawned tree (POSIX killpg TERM→KILL / Windows terminate→kill).

    Timing matches the historical Agent shell terminator: a 1.5s grace window
    between TERM and KILL, then reap without ever hanging the caller.
    """

    def __init__(self, process: "asyncio.subprocess.Process") -> None:
        self._process = process

    async def terminate(self) -> None:
        import os

        proc = self._process
        if proc.returncode is not None:
            return
        try:
            if os.name == "nt":
                proc.terminate()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.5)
            return
        except asyncio.TimeoutError:
            pass
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            await proc.wait()
        except Exception:
            pass


class StreamedProcess:
    """SandboxProcess wrapping an asyncio subprocess.

    ``stdout``/``stderr`` are the asyncio pipes — callers stream from them
    exactly as they did before the sandbox abstraction existed.
    """

    def __init__(
        self,
        process: "asyncio.subprocess.Process",
        spec: SpawnSpec,
        *,
        backend: str,
    ) -> None:
        self.process = process
        self.spec = spec
        self._backend = backend
        self._group = _ProcessGroup(process)

    @property
    def pid(self) -> int | None:
        return self.process.pid

    async def wait(self) -> int:
        return await self.process.wait()

    async def terminate(self) -> None:
        await self._group.terminate()

    def metadata(self) -> dict[str, Any]:
        return {
            "backend": self._backend,
            "profile": self.spec.profile,
            "pid": self.pid,
            "purpose": self.spec.purpose,
            "network": self.spec.network,
        }


class SandboxRunner(Protocol):
    """Execution-profile spawn interface; one instance per backend."""

    @property
    def profile(self) -> str: ...

    async def spawn(self, spec: SpawnSpec) -> StreamedProcess:
        """Launch a process under the backend's isolation.

        ``spec`` is already schema-validated; a restricted backend must raise
        ``SandboxFailure`` rather than run under weaker isolation than asked.
        """
        ...

    def capabilities(self) -> SandboxCapabilities:
        """Truthful capability/strength report — never advertise unenforced
        isolation. Machine snapshot and the policy engine both consume this."""
        ...


__all__ = [
    "ResourceLimits",
    "SandboxCapabilities",
    "SandboxFailure",
    "SandboxRunner",
    "SpawnSpec",
    "StreamedProcess",
]
