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
        self._owner: asyncio.Task | None = None
        self._close_requested: asyncio.Event | None = None

    async def start(self,auth_provider=None):
        """Keep SDK cancel scopes owned by one live task across HTTP requests."""
        ready=asyncio.get_running_loop().create_future()
        self._close_requested=asyncio.Event()
        async def lifecycle():
            try:
                await self.connect(auth_provider)
                catalog=await self.refresh_catalog()
                ready.set_result(catalog)
                await self._close_requested.wait()
            except BaseException as exc:
                if not ready.done():ready.set_exception(exc)
                else:raise
            finally:await self.close()
        self._owner=asyncio.create_task(lifecycle(),name='mcp:'+self.conn.id)
        try:return await ready
        except BaseException:
            self._owner.cancel()
            await asyncio.gather(self._owner,return_exceptions=True)
            raise

    async def stop(self):
        if self._close_requested:self._close_requested.set()
        if self._owner:
            await asyncio.gather(self._owner,return_exceptions=True)

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
            headers={}
            for name,ref in conn.header_secret_refs.items():
                value=self._credentials.get(ref) if self._credentials else None
                if value:headers[name]=value
            if auth_provider is not None:
                from mcp.shared._httpx_utils import create_mcp_http_client
                http_client = create_mcp_http_client(auth=auth_provider,headers=headers)
            elif headers:
                from mcp.shared._httpx_utils import create_mcp_http_client
                http_client=create_mcp_http_client(headers=headers)
            if conn.transport == "sse":
                from mcp.client.sse import sse_client
                read, write = await self._stack.enter_async_context(
                    sse_client(conn.url, auth=auth_provider,headers=headers)
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
        self.definition_resolver = None

    def _current(self,conn,project_id):
        from .scope import require_scope,definition_digest
        current=self.definition_resolver(conn.id) if self.definition_resolver else conn
        if current is None or not current.trusted or not current.enabled:
            raise McpConnectionError('MCP connection is disabled, untrusted or removed')
        require_scope(current,project_id)
        if definition_digest(current)!=definition_digest(conn):
            raise McpConnectionError('MCP configuration changed; reconnect before use')
        return current

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
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if not conn.enabled:
            raise McpConnectionError(f"connection {conn.id} is disabled")
        if not conn.spawnable:
            raise McpConnectionError(
                f"connection {conn.id} is untrusted — set trust=trusted first"
            )
        self._current(conn,project_id)
        async with self._lock:
            existing = self._connections.get(conn.id)
            if existing and existing.session is not None:
                self._current(existing.conn,project_id)
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
                catalog = await handle.start(auth_provider=auth_provider)
            except Exception as exc:  # noqa: BLE001
                await handle.stop()
                raise McpConnectionError(
                    f"{conn.id}: {exc}"
                ) from exc
            self._connections[conn.id] = handle
            return catalog

    async def call_tool(
        self, conn_id: str, tool: str, arguments: dict[str, Any], *, project_id: str | None = None, authorize=None
    ) -> dict[str, Any]:
        handle = self._connections.get(conn_id)
        if handle is None or handle.session is None:
            raise McpConnectionError(f"connection {conn_id} is not connected")
        conn=self._current(handle.conn,project_id)
        if authorize:authorize(conn)
        if tool not in {t['name'] for t in (handle.catalog_data or {}).get('tools',[])}:
            raise PermissionError('MCP tool is outside the reviewed catalog snapshot')
        if '*' not in conn.approved_tools and tool not in conn.approved_tools:
            raise PermissionError('MCP tool is not approved')
        result = await handle.session.call_tool(tool, arguments)
        conn=self._current(handle.conn,project_id)
        if authorize:authorize(conn)
        return result.model_dump(mode="json")

    async def call_namespaced(
        self, qname: str, arguments: dict[str, Any], *, project_id=None,authorize=None
    ) -> dict[str, Any]:
        conn_id, _, tool = qname.partition(".")
        if not tool:
            raise McpConnectionError(
                f"tool name must be '<connection>.<tool>', got {qname!r}"
            )
        return await self.call_tool(f"connection.{conn_id}", tool, arguments,project_id=project_id,authorize=authorize)

    def catalog(self, conn_id: str, *, project_id: str | None = None) -> dict[str, Any] | None:
        handle = self._connections.get(conn_id)
        if handle:
            try:self._current(handle.conn,project_id)
            except (McpConnectionError,PermissionError):return None
        return handle.catalog_data if handle else None

    def status(self) -> dict[str, Any]:
        def current(c):
            from .scope import projects
            allowed=projects(c.conn)
            try:self._current(c.conn,allowed[0] if allowed else None);return c.session is not None and bool(c._owner and not c._owner.done())
            except (McpConnectionError,PermissionError):return False
        return {
            conn_id: {
                "connected": current(c),
                "fingerprint": c.fingerprint,
                "tools": len((c.catalog_data or {}).get("tools", [])),
            }
            for conn_id, c in self._connections.items()
        }

    async def disconnect(self, conn_id: str) -> None:
        async with self._lock:
            handle = self._connections.pop(conn_id, None)
        if handle:
            await handle.stop()

    async def shutdown(self) -> None:
        async with self._lock:
            handles = list(self._connections.values())
            self._connections.clear()
        for handle in handles:
            try:
                await handle.stop()
            except Exception:  # noqa: BLE001
                pass
