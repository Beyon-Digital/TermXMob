from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from termx.agent.execution import SENSITIVE_ENV

_MAX_MESSAGE = 4 * 1024 * 1024


@dataclass(frozen=True)
class ServerSpec:
    candidates: tuple[tuple[str, ...], ...]
    install: str


_SPECS = {
    "javascript": ServerSpec((("typescript-language-server", "--stdio"),), "Install typescript-language-server on the host."),
    "typescript": ServerSpec((("typescript-language-server", "--stdio"),), "Install typescript-language-server on the host."),
    "jsx": ServerSpec((("typescript-language-server", "--stdio"),), "Install typescript-language-server on the host."),
    "tsx": ServerSpec((("typescript-language-server", "--stdio"),), "Install typescript-language-server on the host."),
    "python": ServerSpec(
        (("basedpyright-langserver", "--stdio"), ("pyright-langserver", "--stdio"), ("pylsp",)),
        "Install basedpyright, pyright, or python-lsp-server on the host.",
    ),
    "rust": ServerSpec((("rust-analyzer",),), "Install rust-analyzer on the host."),
    "json": ServerSpec((("vscode-json-language-server", "--stdio"),), "Install vscode-langservers-extracted on the host."),
    "html": ServerSpec((("vscode-html-language-server", "--stdio"),), "Install vscode-langservers-extracted on the host."),
    "css": ServerSpec((("vscode-css-language-server", "--stdio"),), "Install vscode-langservers-extracted on the host."),
}


def _command(language: str) -> tuple[str, ...] | None:
    spec = _SPECS.get(language)
    if spec is None:
        return None
    for candidate in spec.candidates:
        binary = shutil.which(candidate[0])
        if binary:
            return (binary, *candidate[1:])
    return None


def server_snapshot() -> dict[str, dict[str, object]]:
    return {
        language: {
            "available": (command := _command(language)) is not None,
            "command": os.path.basename(command[0]) if command else spec.candidates[0][0],
            "install": spec.install,
        }
        for language, spec in _SPECS.items()
    }


async def _write_message(process: asyncio.subprocess.Process, message: str) -> None:
    if process.stdin is None:
        raise ConnectionError("Language server input closed")
    encoded = message.encode("utf-8")
    if len(encoded) > _MAX_MESSAGE:
        raise ValueError("Language server message is too large")
    json.loads(message)
    process.stdin.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded)
    await process.stdin.drain()


async def _read_message(process: asyncio.subprocess.Process) -> str:
    if process.stdout is None:
        raise ConnectionError("Language server output closed")
    length: int | None = None
    while True:
        line = await process.stdout.readline()
        if not line:
            raise EOFError
        if line in {b"\r\n", b"\n"}:
            break
        name, _, value = line.decode("ascii", "replace").partition(":")
        if name.lower() == "content-length":
            length = int(value.strip())
    if length is None or length < 0 or length > _MAX_MESSAGE:
        raise ValueError("Invalid language server frame")
    body = await process.stdout.readexactly(length)
    return body.decode("utf-8")


async def _from_client(websocket: WebSocket, process: asyncio.subprocess.Process) -> None:
    while True:
        await _write_message(process, await websocket.receive_text())


async def _to_client(websocket: WebSocket, process: asyncio.subprocess.Process) -> None:
    while True:
        await websocket.send_text(await _read_message(process))


async def _drain_stderr(process: asyncio.subprocess.Process) -> None:
    if process.stderr is None:
        return
    while await process.stderr.read(8192):
        pass


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        await process.wait()


async def serve(websocket: WebSocket, root: str, language: str) -> None:
    command = _command(language)
    if command is None:
        await websocket.close(code=4404, reason="Language server unavailable")
        return
    kwargs: dict[str, Any] = {
        "cwd": root,
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "env": {key: value for key, value in os.environ.items() if not SENSITIVE_ENV.search(key)},
    }
    if os.name == "nt":
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(*command, **kwargs)
    await websocket.accept()
    tasks = {
        asyncio.create_task(_from_client(websocket, process)),
        asyncio.create_task(_to_client(websocket, process)),
        asyncio.create_task(_drain_stderr(process)),
        asyncio.create_task(process.wait()),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled():
                try:
                    task.result()
                except (WebSocketDisconnect, EOFError, ConnectionError, asyncio.IncompleteReadError):
                    pass
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await _stop(process)
