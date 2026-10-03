"""MCP client pool (tech-specs §5).

One ``ClientSession`` per enabled+trusted connection, pooled across
engine sessions. Tool names are namespaced ``<conn>.<tool>``; catalogs are
fingerprinted (sha256 of sorted tool schema) so agent-profile wildcards pin
an approved snapshot instead of silently growing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import AsyncExitStack
from typing import Any

from .defs import ConnectionDef
from .ssrf import validate_url


class McpConnectionError(RuntimeError):
    pass


def _fingerprint(catalog: dict[str, Any]) -> str:
    tools = sorted(t["name"] for t in catalog.get("tools", []))
    digest_src = json.dumps(
        {"tools": tools,
         "schemas": sorted(
             json.dumps(
                 t.get("inputSchema") or t.get("input_schema") or {},
                 sort_keys=True,
             )
             for t in catalog.get("tools", [])
         )},
        sort_keys=True,
    )
    return hashlib.sha256(digest_src.encode()).hexdigest()[:16]


class _Connection:
    def __init__(self, conn: ConnectionDef, credentials: Any):
        self.conn = conn
        self._credentials = credentials
        self._stack = AsyncExitStack()
        self.session: Any = None
        self.catalog_data: dict[str, Any] | None = None
        self.fingerprint: str = ""

    def _resolve_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for name in self.conn.env_names:
            if name in os.environ:
                env[name] = os.environ[name]
        for ref in self.conn.secret_refs:
            # mcp.<conn>.<NAME> -> env var NAME for the spawned server
            value = self._credentials.get(ref) if self._credentials else None
            if value:
                env[ref.rsplit(".", 1)[-1].upper()] = value
        return env

    async def connect(self, auth_provider: Any = None) -> None:
        conn = self.conn
        if conn.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            params = StdioServerParameters(
                command=conn.command[0],
                args=conn.command[1:],
                env=self._resolve_env() or None,
            )
            read, write = await self._stack.enter_async_context(
                stdio_client(params)
            )
        else:
            validate_url(conn.url, lan=conn.lan)  # DNS revalidation here
            http_client = None
            if auth_provider is not None:
                from mcp.shared._httpx_utils import create_mcp_http_client
                http_client = create_mcp_http_client(auth=auth_provider)
            if conn.transport == "sse":
                from mcp.client.sse import sse_client
                read, write = await self._stack.enter_async_context(
                    sse_client(conn.url, auth=auth_provider)
                )
            else:
                from mcp.client.streamable_http import streamable_http_client
                read, write = await self._stack.enter_async_context(
                    streamable_http_client(conn.url, http_client=http_client)
                )
        from mcp import ClientSession
        self.session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        await self.session.initialize()

    async def refresh_catalog(self) -> dict[str, Any]:
        assert self.session is not None
        tools = (await self.session.list_tools()).tools
        try:
            resources = (await self.session.list_resources()).resources
        except Exception:  # noqa: BLE001 - server may not support resources
            resources = []
        try:
            prompts = (await self.session.list_prompts()).prompts
        except Exception:  # noqa: BLE001
            prompts = []
        self.catalog_data = {
            "tools": [
                {"name": t.name, "description": t.description or "",
                 "inputSchema": getattr(t, "input_schema", None)
                 or getattr(t, "inputSchema", {})}
                for t in tools
            ],
            "resources": [{"uri": str(r.uri), "name": r.name} for r in resources],
            "prompts": [{"name": p.name, "description": p.description or ""}
                        for p in prompts],
        }
        self.fingerprint = _fingerprint(self.catalog_data)
        return self.catalog_data

    async def close(self) -> None:
        await self._stack.aclose()


class McpPool:
    """Lazy pool of MCP client sessions keyed by connection id."""

    def __init__(self, credentials: Any = None):
        self._credentials = credentials
        self._connections: dict[str, _Connection] = {}
        self._pending_oauth: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def approved_tools(self, conn_id: str) -> list[str]:
        c = self._connections.get(conn_id)
        if c and c.catalog_data:
            return sorted(
                f"{c.conn.slug}.{t['name']}" for t in c.catalog_data["tools"]
            )
        return []

    async def connect(
        self,
        conn: ConnectionDef,
        *,
        on_auth_url: Any = None,
        loopback: Any = None,
    ) -> dict[str, Any]:
        if not conn.enabled:
            raise McpConnectionError(f"connection {conn.id} is disabled")
        if not conn.spawnable:
            raise McpConnectionError(
                f"connection {conn.id} is untrusted — set trust=trusted first"
            )
        async with self._lock:
            existing = self._connections.get(conn.id)
            if existing and existing.session is not None:
                return existing.catalog_data or {}
            handle = _Connection(conn, self._credentials)
            auth_provider = None
            if conn.auth_method == "oauth" and conn.transport in {"http", "sse"}:
                from .oauth import build_provider
                auth_provider = await build_provider(
                    conn, self._credentials,
                    on_auth_url=on_auth_url, loopback=loopback,
                )
            try:
                await handle.connect(auth_provider=auth_provider)
                catalog = await handle.refresh_catalog()
            except Exception as exc:  # noqa: BLE001
                await handle.close()
                raise McpConnectionError(
                    f"{conn.id}: {exc}"
                ) from exc
            self._connections[conn.id] = handle
            return catalog

    async def call_tool(
        self, conn_id: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        handle = self._connections.get(conn_id)
        if handle is None or handle.session is None:
            raise McpConnectionError(f"connection {conn_id} is not connected")
        result = await handle.session.call_tool(tool, arguments)
        return result.model_dump(mode="json")

    async def call_namespaced(
        self, qname: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        conn_id, _, tool = qname.partition(".")
        if not tool:
            raise McpConnectionError(
                f"tool name must be '<connection>.<tool>', got {qname!r}"
            )
        return await self.call_tool(f"connection.{conn_id}", tool, arguments)

    def catalog(self, conn_id: str) -> dict[str, Any] | None:
        handle = self._connections.get(conn_id)
        return handle.catalog_data if handle else None

    def status(self) -> dict[str, Any]:
        return {
            conn_id: {
                "connected": c.session is not None,
                "fingerprint": c.fingerprint,
                "tools": len((c.catalog_data or {}).get("tools", [])),
            }
            for conn_id, c in self._connections.items()
        }

    async def disconnect(self, conn_id: str) -> None:
        async with self._lock:
            handle = self._connections.pop(conn_id, None)
        if handle:
            await handle.close()

    async def shutdown(self) -> None:
        async with self._lock:
            handles = list(self._connections.values())
            self._connections.clear()
        for handle in handles:
            try:
                await handle.close()
            except Exception:  # noqa: BLE001
                pass
