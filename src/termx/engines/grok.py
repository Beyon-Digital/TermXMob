"""Grok adapter — `grok agent stdio` (ACP over stdio).

Docs: https://docs.x.ai/build/cli/headless-scripting — auth via `xai.api_key`
(`grok config set`) or XAI_API_KEY env, or cached browser login token.
`--no-auto-update` keeps headless runs deterministic.
"""

from __future__ import annotations

import os

from .acp import AcpEngine
from .types import (
    AUTH_AUTHENTICATED,
    AUTH_UNKNOWN,
)


class GrokEngine(AcpEngine):
    id = "grok"
    label = "Grok"
    executable_name = "grok"
    # --no-auto-update is a global flag; it must precede the subcommand —
    # `grok agent stdio --no-auto-update` exits immediately (verified live).
    acp_args = ["--no-auto-update", "agent", "stdio"]
    version_args = ["--version"]

    def _auth_state(self) -> str:
        # Detect credential presence without reading secret values: env var,
        # ~/.grok/auth.json cached token, or api_key in ~/.grok/config.toml.
        if os.environ.get("XAI_API_KEY"):
            return AUTH_AUTHENTICATED
        if self._grok_configured():
            return AUTH_AUTHENTICATED
        return AUTH_UNKNOWN

    def _grok_configured(self) -> bool:
        from pathlib import Path
        home = Path.home()
        auth = home / ".grok" / "auth.json"
        try:
            if auth.is_file() and auth.stat().st_size > 2:
                return True
        except OSError:
            pass
        cfg = home / ".grok" / "config.toml"
        try:
            if cfg.is_file() and "api_key" in cfg.read_text(errors="replace"):
                return True
        except OSError:
            pass
        return False

    def _auth_detail(self) -> str:
        if os.environ.get("XAI_API_KEY"):
            return "XAI_API_KEY"
        if self._grok_configured():
            return "grok config (key or cached login)"
        return ""
