"""Persistent HTTP runtime: one pooled httpx.AsyncClient per provider.

Provider requests previously built a fresh AsyncClient per POST. The runtime
keeps one client per provider id so connections are reused across turns and
tasks; close() on manager shutdown drains them.

Eviction removes a client from the pool so new adapters get a fresh client.
Retirement waits through a grace period and for all active requests/response
streams to close; an active stream may run longer than the grace period while
receiving data continuously. aclose() drains both live and retired clients at
shutdown, after the manager stops its workers.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx


# Grace period before an evicted client is closed, so an in-flight provider
# turn finishes on the old client instead of erroring mid-request.
_EVICT_GRACE_S = 300.0


class _LeasedStream(httpx.AsyncByteStream):
    """Hold the client lease through streaming, including explicit close."""

    def __init__(self, stream: httpx.AsyncByteStream, release: Callable[[], None]) -> None:
        self._stream = stream
        self._release = release
        self._released = False

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        finally:
            if not self._released:
                self._released = True
                self._release()


class _PooledAsyncClient(httpx.AsyncClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._active_requests = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def _release_request(self) -> None:
        self._active_requests -= 1
        if self._active_requests == 0:
            self._idle.set()

    async def send(self, *args: Any, **kwargs: Any) -> httpx.Response:
        self._active_requests += 1
        self._idle.clear()
        response_owns_lease = False
        try:
            response = await super().send(*args, **kwargs)
            if kwargs.get("stream") and not response.is_closed:
                response.stream = _LeasedStream(response.stream, self._release_request)
                response_owns_lease = True
            return response
        finally:
            if not response_owns_lease:
                self._release_request()

    async def wait_until_idle(self) -> None:
        while self._active_requests:
            await self._idle.wait()


class ProviderHttpRuntime:
    def __init__(self) -> None:
        self._clients: dict[str, _PooledAsyncClient] = {}
        self._retired: set[_PooledAsyncClient] = set()
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
            client = _PooledAsyncClient(
                timeout=timeout_s,
                headers=headers,
                follow_redirects=True,
            )
            self._clients[key] = client
        else:
            # httpx copies timeouts into each built request. Updating defaults
            # affects the next request without changing an in-flight stream.
            client.timeout = timeout_s
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

    async def _deferred_close(self, client: _PooledAsyncClient) -> None:
        await asyncio.sleep(_EVICT_GRACE_S)
        await client.wait_until_idle()
        try:
            await client.aclose()
        except Exception:
            pass
        else:
            self._retired.discard(client)

    async def aclose(self) -> None:
        clients = list(self._clients.values()) + list(self._retired)
        self._clients.clear()
        self._retired.clear()
        pending = list(self._pending)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(
            *(client.aclose() for client in clients),
            return_exceptions=True,
        )
