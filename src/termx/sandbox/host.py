"""Explicit host backend: today's unrestricted spawn semantics behind the
SandboxRunner interface.

This is the compatibility runner, not a hidden bypass — ``capabilities()``
reports ``strength="none"`` and grants ``*:any`` so callers, the policy engine
and the machine snapshot all see the truth: processes under this backend run
with full host-user authority. Restricted profiles on this backend only get
the clean-environment soft layer (documented as such, never kernel).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

from termx.sandbox.environment import build_environment, host_environment
from termx.sandbox.models import (
    SandboxCapabilities,
    SandboxFailure,
    SpawnSpec,
)
from termx.sandbox.runner import StreamedProcess

# Capabilities the host backend grants to every spawn — an unrestricted host
# process can do anything the Termx user can do, so this is truthfully "*".
_UNRESTRICTED = frozenset({"*:any"})


class HostSandboxRunner:
    """SandboxRunner backed by plain subprocess creation on the host."""

    def __init__(self, *, profile: str = "host") -> None:
        if profile not in {"host", "workspace", "agent"}:
            raise SandboxFailure("invalid_profile", f"unknown execution profile {profile!r}")
        self._profile = profile

    @property
    def profile(self) -> str:
        return self._profile

    async def spawn(self, spec: SpawnSpec) -> StreamedProcess:
        spec.validate()
        env = spec.env if spec.env else self._default_env(spec.profile)
        kwargs: dict[str, Any] = {
            "cwd": spec.cwd or spec.workspace_root,
            "env": env,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if spec.stdin is not None:
            kwargs["stdin"] = spec.stdin
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            if spec.argv is not None:
                process = await asyncio.create_subprocess_exec(*spec.argv, **kwargs)
            else:
                process = await asyncio.create_subprocess_shell(spec.shell or "", **kwargs)
        except OSError as exc:
            raise SandboxFailure("spawn_failed", str(exc)) from exc
        return StreamedProcess(process, spec, backend="host")

    def spawn_argv(self, spec: SpawnSpec) -> list[str]:
        """PTY embedding contract: host adds no wrapper — argv is already the
        command the caller wants under the terminal's own spawn."""
        spec.validate()
        if spec.argv is not None:
            return list(spec.argv)
        if os.name == "nt":
            return [os.environ.get("COMSPEC", "cmd.exe"), "/c", spec.shell or ""]
        return ["/bin/sh", "-c", spec.shell or ""]

    def _default_env(self, profile: str) -> dict[str, str]:
        # host keeps the historical inherit-and-strip env for compatibility;
        # restricted profiles get the allowlist build — that is the only soft
        # layer the host backend can provide, and capabilities() says so.
        if profile == "host":
            return host_environment()
        return build_environment(profile)

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            backend="host",
            profile=self._profile,
            strength="none",
            granted=_UNRESTRICTED,
            grantable=_UNRESTRICTED,
            network_control=False,
            filesystem_isolation=False,
            identity_isolation=False,
            resource_limits=False,
            process_tree_kill=True,
        )


def host_runner(profile: str = "host") -> HostSandboxRunner:
    return HostSandboxRunner(profile=profile)


# Kept importable for back-compat / tests that assert the interface shape.
if sys.platform == "win32":
    _PLATFORM_LABEL = "windows"
else:
    _PLATFORM_LABEL = sys.platform
