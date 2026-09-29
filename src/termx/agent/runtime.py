"""Persistent HTTP runtime: one pooled httpx.AsyncClient per provider.

Provider requests previously built a fresh AsyncClient per POST. The runtime
keeps one client per provider id so connections are reused across turns and
tasks; close() on manager shutdown drains them.

Evicted clients are never closed eagerly: an in-flight provider turn may still
be awaiting a response on the shared client, and a synchronous save_provider
runs without an event loop to close on anyway. Retirement only removes the
client from the pool — new adapters get a fresh client — while aclose() drains
both live and retired clients at shutdown.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx


# Grace period before an evicted client is closed, so an in-flight provider
# turn finishes on the old client instead of erroring mid-request.
_EVICT_GRACE_S = 300.0


class ProviderHttpRuntime:
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._retired: set[httpx.AsyncClient] = set()
        self._pending: set[asyncio.Task[None]] = set()

    def client_for(
        self,
        key: str,
        *,
        timeout_s: float,
        headers: dict[str, str],
    ) -> httpx.AsyncClient:
        client = self._clients.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                timeout=timeout_s,
                headers=headers,
                follow_redirects=True,
            )
            self._clients[key] = client
        return client

    def evict(self, key: str) -> None:
        client = self._clients.pop(key, None)
        if client is None:
            return
        # Held in _retired until the grace task fires or aclose() drains it.
        self._retired.add(client)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop to defer on: aclose() at shutdown drains it.
            return
        task = loop.create_task(self._deferred_close(client))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _deferred_close(self, client: httpx.AsyncClient) -> None:
        await asyncio.sleep(_EVICT_GRACE_S)
        self._retired.discard(client)
        try:
            await client.aclose()
        except Exception:
            pass

    async def aclose(self) -> None:
        clients = list(self._clients.values()) + list(self._retired)
        self._clients.clear()
        self._retired.clear()
        for task in self._pending:
            task.cancel()
        await asyncio.gather(
            *(client.aclose() for client in clients),
            return_exceptions=True,
        )
