"""Execution sandbox abstraction.

The sandbox is the security boundary for Agent/workspace process execution;
the policy engine only improves UX on top of it. Every process Termx spawns is
classified into one execution profile:

- ``host``      — existing unrestricted human remote terminal behavior
- ``workspace`` — restricted interactive project terminal
- ``agent``     — restricted default for Agent shell/check/runbook execution

``SandboxRunner`` is the single spawn surface; platform backends live behind
it so ``manager.py``/tool code stays platform-neutral. The ``host`` backend
ships in this package as an explicit compatibility runner — it does not hide
behind "no sandbox": ``capabilities()`` truthfully reports unrestricted
authority so policy and the machine snapshot never pretend otherwise.
"""
from __future__ import annotations

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
    "ResourceLimits",
    "SandboxCapabilities",
    "SandboxFailure",
    "SandboxProcess",
    "SandboxRunner",
    "SpawnResult",
    "SpawnSpec",
    "build_environment",
    "host_environment",
    "runner_for",
]


def runner_for(profile: str = "agent") -> SandboxRunner:
    """Return the default runner for an execution profile.

    Only the ``host`` backend exists so far; every profile resolves to it and
    reports truthful (unrestricted) capabilities. Strong backends (Linux
    namespaces, macOS restricted user, Windows token+job) plug in here without
    call-site changes.
    """
    return HostSandboxRunner(profile=profile)
