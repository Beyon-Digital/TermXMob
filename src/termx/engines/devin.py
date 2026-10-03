"""Devin adapter — `devin acp` (ACP over stdio).

Docs: https://docs.devin.ai/cli/acp/zed — Devin runs as an ACP subprocess;
auth is user-driven via browser (the agent advertises auth methods and opens
the login flow); slash commands arrive as `available_commands_update`.
"""

from __future__ import annotations

from typing import Any

from .acp import AcpEngine, _client_capabilities, _client_info
from .types import AUTH_AUTHENTICATED, AUTH_UNKNOWN


class DevinEngine(AcpEngine):
    id = "devin"
    label = "Devin"
    executable_name = "devin"
    acp_args = ["acp"]
    version_args = ["--version"]

    def _auth_state(self) -> str:
        if not self._agent_info and not self._auth_methods:
            return AUTH_UNKNOWN
        # `initialize` succeeded: Devin answers ACP pre-auth (it must, to offer
        # browser login). Authenticated-ness is only proven by a session.
        if self._auth_methods:
            return AUTH_UNKNOWN  # auth required but state unproven
        return AUTH_AUTHENTICATED

    def _auth_detail(self) -> str:
        if self._auth_methods:
            names = [str(m.get("id") or m.get("name") or "?") for m in self._auth_methods]
            return f"auth methods: {', '.join(names)}"
        return ""

    async def authenticate(self, method_id: str | None = None) -> dict[str, Any]:
        """Trigger the user-driven login flow (opens a browser on the host)."""
        async with self._spawn() as (conn, _proc):
            await conn.initialize(
                protocol_version=1,
                client_info=_client_info(),
                client_capabilities=_client_capabilities(),
            )
            mid = method_id or (
                self._auth_methods[0].get("id") if self._auth_methods else "devin")
            resp = await conn.authenticate(str(mid))
            return {"ok": True, "method": str(mid)}
