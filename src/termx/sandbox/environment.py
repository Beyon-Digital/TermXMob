"""Allowlist environment construction for sandboxed processes.

Agent/workspace spawns never start from ``os.environ.copy()``: a fake secret
named anything the old ``SENSITIVE_ENV`` regex missed cannot leak because only
explicitly listed variables cross. The host profile keeps the legacy
inherit-and-strip behavior for backward compatibility.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

# Variables allowed to cross from the host environment into restricted
# profiles. Prefixes (ending in _) match a whole family.
_ENV_ALLOW_EXACT = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TERM",
        "COLORTERM",
        "LANG",
        "SHELL",
        "USER",
        "LOGNAME",
        "TZ",
        "CI",
        "EDITOR",
        "VISUAL",
        # Corporate/dev proxies are config, not credentials — commands like
        # installs still work behind them. HTTPS_PROXY only mirrors value.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        # Toolchain niceties that carry no secrets.
        "NPM_CONFIG_PREFIX",
        "PIP_INDEX_URL",
        "UV_INDEX_URL",
        "CARGO_HOME",
        "RUSTUP_HOME",
        "GOPATH",
        "GOROOT",
        "JAVA_HOME",
        "NODE_ENV",
    }
)
_ENV_ALLOW_PREFIX = (
    "LC_",  # locale family (LANG handled exactly above)
    "TERMX_AGENT_",  # explicit agent-scoped config only (never TERMX_AI_*)
)

# Names that must never cross even if a future allowlist entry would match —
# credential-material surfaces. SSH_AUTH_SOCK is the classic example: its name
# doesn't look like a secret but grants signing with host keys.
_ENV_DENY_EXACT = frozenset(
    {
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "KUBECONFIG",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "NPM_CONFIG__AUTH",
        "NPM_CONFIG__AUTH_TOKEN",
        "PIP_EXTRA_INDEX_URL",
    }
)
_ENV_DENY_PREFIX = (
    "AWS_",
    "AZURE_",
    "GCP_",
    "GCLOUD_",
    "GOOGLE_",
    "OPENAI_",
    "ANTHROPIC_",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "TERMX_AI_",  # provider keys are host-process only
)


def _denied(name: str) -> bool:
    return name in _ENV_DENY_EXACT or name.startswith(_ENV_DENY_PREFIX)


def _allowed(name: str) -> bool:
    if _denied(name):
        return False
    return name in _ENV_ALLOW_EXACT or name.startswith(_ENV_ALLOW_PREFIX)


def _strip_url_credentials(value: str) -> str:
    """Remove userinfo from URL values (proxy/index settings).

    Allowed config variables are copied verbatim — but a value like
    ``http://user:pass@proxy:8080`` carries credentials, which restricted
    profiles must not receive. Strips the authority's userinfo; leaves
    non-URL values untouched.
    """
    if "://" not in value:
        return value
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    host = parts.hostname or ""
    if not host:
        return value
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def build_environment(
    profile: str,
    *,
    base: dict[str, str] | None = None,
    home: str | None = None,
    tmp_dir: str | None = None,
    extra_allow: Iterable[str] = (),
) -> dict[str, str]:
    """Construct a clean env for a restricted profile.

    ``base`` defaults to ``os.environ`` and exists for tests. ``extra_allow``
    names additional safe variables the caller configured (e.g. a project's
    declared toolchain vars) — they still must pass the deny list, so caller
    config cannot leak credential material.
    """
    if profile == "host":
        raise ValueError("host profile uses host_environment for compatibility")
    base = os.environ if base is None else base
    extra = set(extra_allow)
    env = {
        name: _strip_url_credentials(value)
        for name, value in base.items()
        if not _denied(name) and (_allowed(name) or name in extra)
    }
    if home is not None:
        env["HOME"] = home
        env["USERPROFILE"] = home
    if tmp_dir is not None:
        for key in ("TMPDIR", "TEMP", "TMP"):
            env[key] = tmp_dir
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("LANG", "C.UTF-8")
    return env


# The historical host regex — kept byte-identical to agent.execution's so the
# host profile's env behavior doesn't drift.
_SENSITIVE_ENV = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSCODE|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL)",
    re.IGNORECASE,
)


def host_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Legacy host-profile env: inherit everything, strip credential-looking
    names (kept for backward compatibility — restricted profiles must use
    ``build_environment`` instead)."""
    base = os.environ if base is None else base
    return {name: value for name, value in base.items() if not _SENSITIVE_ENV.search(name)}


def sandbox_home(config_dir: Path, task_id: str | None) -> Path:
    """Private minimal HOME for a restricted spawn (created lazily)."""
    root = config_dir / "sandbox-home" / (task_id or "shared")
    root.mkdir(parents=True, exist_ok=True)
    return root


def sandbox_tmp(config_dir: Path, task_id: str | None) -> Path:
    root = config_dir / "sandbox-tmp" / (task_id or "shared")
    root.mkdir(parents=True, exist_ok=True)
    return root
