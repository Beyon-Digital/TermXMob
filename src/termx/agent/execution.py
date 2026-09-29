from __future__ import annotations

import asyncio
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from termx.agent.policy import redact

OUTPUT_LIMIT = 256_000
SENSITIVE_ENV = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSCODE|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ShellResult:
    command: str
    cwd: str
    output: str
    exit_code: int | None
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "command": redact(self.command),
            "cwd": self.cwd,
            "output": redact(self.output),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "truncated": self.truncated,
        }


async def run_shell(
    command: str,
    cwd: str,
    *,
    timeout_s: float = 120,
    cancel: asyncio.Event | None = None,
) -> ShellResult:
    return await stream_shell(command, cwd, timeout_s=timeout_s, cancel=cancel, on_output=None)


async def stream_shell(
    command: str,
    cwd: str,
    *,
    timeout_s: float = 120,
    cancel: asyncio.Event | None = None,
    on_output: Any = None,
    chunk_size: int = 4096,
) -> ShellResult:
    """Run a shell command, streaming output chunks to ``on_output``.

    ``on_output`` is a synchronous callable receiving each decoded chunk as it
    arrives (before process exit). The returned ShellResult carries the full
    (possibly truncated) output exactly like run_shell.
    """
    root = Path(cwd).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("working directory is not a directory")
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
        "env": {key: value for key, value in os.environ.items() if not SENSITIVE_ENV.search(key)},
    }
    if os.name == "nt":
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_shell(command, **kwargs)
    # Rolling tail: memory stays bounded no matter how much the process prints.
    tail = bytearray()
    dropped = 0

    async def pump() -> None:
        nonlocal dropped
        assert process.stdout is not None
        while True:
            data = await process.stdout.read(chunk_size)
            if not data:
                return
            if on_output is not None:
                on_output(data)
            tail.extend(data)
            if len(tail) > 2 * OUTPUT_LIMIT:
                drop = len(tail) - OUTPUT_LIMIT
                del tail[:drop]
                dropped += drop

    reader = asyncio.create_task(pump())
    exited = asyncio.create_task(process.wait())
    cancelled = asyncio.create_task(cancel.wait()) if cancel is not None else None
    timed_out = False
    was_cancelled = False
    reader_open = True
    deadline = asyncio.get_running_loop().time() + max(1.0, timeout_s)
    try:
        while True:
            waiters: set[asyncio.Task[Any]] = {exited}
            if reader_open:
                waiters.add(reader)
            if cancelled is not None:
                waiters.add(cancelled)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                timed_out = True
                await _terminate(process)
                break
            done, _ = await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            if reader_open and reader in done:
                reader_open = False
                if reader.exception() is not None:
                    # Output delivery failed — stop the process promptly rather
                    # than letting it block on a full stdout pipe until timeout.
                    await _terminate(process)
                    break
                # stdout drained early; keep waiting for exit/cancel/deadline.
                continue
            if exited in done:
                break
            if cancelled is not None and cancelled in done:
                was_cancelled = True
                await _terminate(process)
                break
            timed_out = True
            await _terminate(process)
            break
        await reader
    finally:
        if cancelled is not None:
            cancelled.cancel()
        exited.cancel()
    raw = bytes(tail)[-OUTPUT_LIMIT:]
    truncated = dropped > 0 or len(tail) > OUTPUT_LIMIT
    text = raw.decode("utf-8", "replace")
    return ShellResult(
        command=command,
        cwd=str(root),
        output=text,
        exit_code=process.returncode,
        timed_out=timed_out,
        cancelled=was_cancelled,
        truncated=truncated,
    )


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=1.5)
        return
    except asyncio.TimeoutError:
        pass
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        await process.wait()
    except Exception:
        pass
