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


class ProviderHttpRuntime:
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._retired: set[httpx.AsyncClient] = set()

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
        if client is not None:
            # Retire instead of closing: in-flight requests keep their client
            # alive, and aclose() guarantees eventual closure at shutdown.
            self._retired.add(client)

    async def aclose(self) -> None:
        clients = list(self._clients.values()) + list(self._retired)
        self._clients.clear()
        self._retired.clear()
        await asyncio.gather(
            *(client.aclose() for client in clients),
            return_exceptions=True,
        )
