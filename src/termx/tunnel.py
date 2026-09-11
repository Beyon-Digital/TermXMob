from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable, Coroutine
from typing import Any

TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


async def start_cloudflare_tunnel(
    port: int, timeout: float = 30.0
) -> tuple[str | None, Callable[[], Coroutine[Any, Any, None]], asyncio.subprocess.Process | None]:
    exe = shutil.which("cloudflared")
    if exe is None:
        print("termx: cloudflared not found. Install with: brew install cloudflare/cloudflare/cloudflared")
        return None, _noop, None

    proc = await asyncio.create_subprocess_exec(
        exe,
        "tunnel",
        "--url",
        f"http://127.0.0.1:{port}",
        "--no-autoupdate",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    url: str | None = None
    leftover = b""

    async def _pump() -> None:
        nonlocal leftover
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            leftover += chunk
            leftover = leftover[-8000:]

    pump = asyncio.create_task(_pump())

    async def stop() -> None:
        if not pump.done():
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        if proc.returncode is None:
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

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        text = leftover.decode("utf-8", "replace")
        match = TUNNEL_URL_RE.search(text)
        if match:
            url = match.group(0)
            break
        if proc.returncode is not None:
            break
        await asyncio.sleep(0.2)

    if url is None:
        print("termx: cloudflared started but no trycloudflare URL appeared")
    return url, stop, proc


async def _noop() -> None:
    return None
