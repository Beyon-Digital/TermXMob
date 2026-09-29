"""Persistent HTTP runtime: one pooled httpx.AsyncClient per provider.

Provider requests previously built a fresh AsyncClient per POST. The runtime
keeps one client per provider id so connections are reused across turns and
tasks; close() on manager shutdown drains them.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx


class ProviderHttpRuntime:
    def __init__(self) -> None:
        self._clients: dict[str, httpx.AsyncClient] = {}

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
            try:
                asyncio.get_running_loop().create_task(client.aclose())
            except RuntimeError:
                pass

    async def aclose(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        await asyncio.gather(
            *(client.aclose() for client in clients),
            return_exceptions=True,
        )
