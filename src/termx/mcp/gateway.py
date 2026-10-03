"""Session-scoped MCP gateway (tech-specs §5).

Engines that can't take per-session MCP config reach MCP tools through a
loopback URL carrying a capability token::

    engine  ->  http://127.0.0.1:<port>/api/mcp-gw/<token>/...  ->  McpPool

Tokens are 256-bit random, scoped to one engine session *and* a fixed set
of connection ids + tools, are revocable on session end, and are never
persisted — they live only in the host broker. Requests are additionally
restricted to loopback clients.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any


class GatewayAuthError(PermissionError):
    pass


@dataclass
class _Grant:
    session_id: str
    connections: dict[str, list[str] | None]  # conn_id -> tool allowlist or None=all
    created_at: float
    expires_at: float | None


class McpGateway:
    def __init__(self, pool: Any, token_ttl_s: float = 24 * 3600):
        self._pool = pool
        self._grants: dict[str, _Grant] = {}
        self._ttl = token_ttl_s

    def mint(
        self,
        session_id: str,
        connections: dict[str, list[str] | None],
    ) -> str:
        token = f"mcpgw_{secrets.token_urlsafe(32)}"
        self._grants[token] = _Grant(
            session_id=session_id,
            connections=dict(connections),
            created_at=time.time(),
            expires_at=time.time() + self._ttl if self._ttl else None,
        )
        return token

    def revoke_session(self, session_id: str) -> int:
        doomed = [t for t, g in self._grants.items() if g.session_id == session_id]
        for t in doomed:
            del self._grants[t]
        return len(doomed)

    def _grant(self, token: str) -> _Grant:
        grant = self._grants.get(token)
        if grant is None:
            raise GatewayAuthError("invalid gateway token")
        if grant.expires_at and time.time() > grant.expires_at:
            del self._grants[token]
            raise GatewayAuthError("gateway token expired")
        return grant

    def _check_scope(self, grant: _Grant, conn_id: str, tool: str) -> None:
        if conn_id not in grant.connections:
            raise GatewayAuthError(f"connection {conn_id} not in token scope")
        allow = grant.connections[conn_id]
        if allow is not None and tool not in allow and f"{conn_id}.{tool}" not in allow:
            raise GatewayAuthError(f"tool {tool} not in token scope for {conn_id}")

    async def catalog(self, token: str) -> dict[str, Any]:
        grant = self._grant(token)
        out: dict[str, Any] = {}
        for conn_id, allow in grant.connections.items():
            cat = self._pool.catalog(conn_id) or {}
            tools = cat.get("tools", [])
            if allow is not None:
                tools = [t for t in tools if t["name"] in allow]
            out[conn_id] = {**cat, "tools": tools}
        return out

    async def call_tool(
        self, token: str, conn_id: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        grant = self._grant(token)
        self._check_scope(grant, conn_id, tool)
        return await self._pool.call_tool(conn_id, tool, arguments)

    def status(self) -> dict[str, Any]:
        now = time.time()
        live = {
            t: g for t, g in self._grants.items()
            if not g.expires_at or g.expires_at > now
        }
        return {
            "active_tokens": len(live),
            "sessions": sorted({g.session_id for g in live.values()}),
        }
