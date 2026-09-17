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
    communicate = asyncio.create_task(process.communicate())
    cancelled = asyncio.create_task(cancel.wait()) if cancel is not None else None
    timed_out = False
    was_cancelled = False
    try:
        waiters: set[asyncio.Task[Any]] = {communicate}
        if cancelled is not None:
            waiters.add(cancelled)
        done, _ = await asyncio.wait(waiters, timeout=max(1.0, timeout_s), return_when=asyncio.FIRST_COMPLETED)
        if communicate in done:
            output, _ = communicate.result()
        else:
            timed_out = cancelled not in done
            was_cancelled = cancelled in done
            await _terminate(process)
            output, _ = await communicate
    finally:
        if cancelled is not None:
            cancelled.cancel()
    raw = output or b""
    truncated = len(raw) > OUTPUT_LIMIT
    if truncated:
        raw = raw[-OUTPUT_LIMIT:]
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
