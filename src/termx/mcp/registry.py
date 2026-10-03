"""Loads ``connection.*.json`` defs from trusted roots."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .defs import ConnectionDef, ConnectionError_, load_connection_file


class ConnectionRegistry:
    """Connection defs on disk are authority; loaded per call so edits and
    newly discovered files take effect without a restart."""

    def __init__(self, mcp_dirs: list[str] | None = None):
        self._dirs = [Path(d) for d in (mcp_dirs or [])]

    def dirs(self) -> list[str]:
        return [str(d) for d in self._dirs]

    def list(self) -> list[ConnectionDef]:
        out: list[ConnectionDef] = []
        seen: set[str] = set()
        for directory in self._dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                if path.is_symlink():
                    continue
                try:
                    conn = load_connection_file(path)
                except ConnectionError_:
                    continue
                if conn.id in seen:  # first root wins; collisions surface via discovery report
                    continue
                seen.add(conn.id)
                out.append(conn)
        return out

    def get(self, conn_id: str) -> ConnectionDef | None:
        slug = conn_id.removeprefix("connection.")
        for conn in self.list():
            if conn.id == conn_id or conn.slug == slug:
                return conn
        return None

    def save(self, conn: ConnectionDef) -> str:
        """Persist to the first (user) mcp dir; returns file path."""
        if not self._dirs:
            raise ConnectionError_("no mcp definition dir configured")
        directory = self._dirs[0]
        directory.mkdir(parents=True, exist_ok=True)
        now = time.time()
        conn.created_at = conn.created_at or now
        conn.updated_at = now
        path = directory / f"{conn.slug}.json"
        payload = {
            "schema": 1,
            "id": conn.id,
            "label": conn.label,
            "transport": conn.transport,
            "enabled": conn.enabled,
            "owner": conn.owner,
            "auth": {
                "method": conn.auth_method,
                "env_names": conn.env_names,
                "scopes": conn.scopes,
            },
            "secret_refs": conn.secret_refs,
            "approved_tools": conn.approved_tools,
            "trust": conn.trust,
            "lan": conn.lan,
            "created_at": conn.created_at,
            "updated_at": conn.updated_at,
        }
        if conn.command:
            payload["command"] = conn.command
        if conn.url:
            payload["url"] = conn.url
        if conn.client_id:
            payload["client_id"] = conn.client_id
        if conn.client_metadata_url:
            payload["client_metadata_url"] = conn.client_metadata_url
        if conn.client_secret_ref:
            payload["client_secret_ref"] = conn.client_secret_ref
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        import os
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        conn.path = str(path)
        return str(path)
