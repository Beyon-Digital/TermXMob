"""Execution sandbox abstraction.

The sandbox is the security boundary for Agent/workspace process execution;
the policy engine only improves UX on top of it. Every process Termx spawns is
classified into one execution profile:

- ``host``      — existing unrestricted human remote terminal behavior
- ``workspace`` — restricted interactive project terminal
- ``agent``     — restricted default for Agent shell/check/runbook execution

``SandboxRunner`` is the single spawn surface; platform backends live behind
it so ``manager.py``/tool code stays platform-neutral. The ``host`` backend
is the explicit compatibility runner — it does not hide behind "no sandbox":
``capabilities()`` truthfully reports unrestricted authority. On Linux with
bubblewrap + unprivileged user namespaces, restricted profiles resolve to the
kernel-enforcing ``linux-ns`` backend; everywhere else they stay on ``host``
and report so. ``TERMX_SANDBOX_BACKEND`` (``auto``|``host``|``linux-ns``)
overrides selection for debugging — never silently.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from termx.sandbox.environment import build_environment, host_environment
from termx.sandbox.host import HostSandboxRunner
from termx.sandbox.models import (
    SandboxCapabilities,
    SandboxFailure,
    SandboxProcess,
    ResourceLimits,
    SpawnResult,
    SpawnSpec,
)
from termx.sandbox.runner import SandboxRunner

__all__ = [
    "HostSandboxRunner",
    "LinuxNamespaceRunner",
    "ResourceLimits",
    "SandboxCapabilities",
    "SandboxFailure",
    "SandboxProcess",
    "SandboxRunner",
    "SpawnResult",
    "SpawnSpec",
    "build_environment",
    "host_environment",
    "linux_ns_available",
    "runner_for",
]

_HOST_BACKEND = "host"
_LINUX_NS_BACKEND = "linux-ns"


from termx.sandbox.linux_ns import LinuxNamespaceRunner, linux_ns_available


def _default_backend(profile: str) -> str:
    """Pick the strongest available backend for a profile — truthful, never
    silently downgraded per-spawn (a failed spawn raises, no fallback)."""
    if profile == "host":
        return _HOST_BACKEND
    override = os.environ.get("TERMX_SANDBOX_BACKEND", "").strip().lower()
    if override:
        return override
    if linux_ns_available():
        return _LINUX_NS_BACKEND
    return _HOST_BACKEND


def runner_for(
    profile: str = "agent",
    *,
    backend: str | None = None,
    state_dir: "str | Path | None" = None,
) -> SandboxRunner:
    """Return a runner for an execution profile.

    ``backend``/``TERMX_SANDBOX_BACKEND`` select explicitly; the default is
    ``host`` for the host profile and the best available restricted backend
    (Linux: ``linux-ns``) otherwise. An unavailable requested backend raises —
    restricted work never silently degrades to unsandboxed execution.
    """
    backend = backend or _default_backend(profile)
    if backend == _LINUX_NS_BACKEND:
        return LinuxNamespaceRunner(profile=profile, state_dir=state_dir)
    if backend == _HOST_BACKEND:
        return HostSandboxRunner(profile=profile)
    raise SandboxFailure("invalid_backend", f"unknown sandbox backend {backend!r}")
