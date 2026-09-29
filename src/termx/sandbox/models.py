"""Typed sandbox models shared by every execution surface.

Platform-neutral on purpose: backends translate these into namespace/jail/ACL
mechanics, callers never branch on ``sys.platform``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

EXECUTION_PROFILES = ("host", "workspace", "agent")

# Hard-deny capabilities: never granted to restricted profiles by ordinary
# remembered-allow rules, regardless of scope. A future power profile may
# expose them, but only as a distinct explicit profile.
HARD_DENIED_CAPABILITIES = frozenset(
    {
        "privilege.elevate",
        "process.host_signal",
        "docker.socket",
        "device.access",
        "credentials.host",
        "broker.control",
    }
)


@dataclass(frozen=True)
class ResourceLimits:
    """Bounds applied to one spawned process tree.

    ``wall_s`` duplicates the tool-level timeout so the sandbox can enforce
    time even when the caller forgot; ``output_bytes`` mirrors the rolling
    output bound.
    """

    cpu_s: float | None = None
    memory_bytes: int | None = None
    pids: int | None = None
    wall_s: float | None = None
    output_bytes: int = 256_000


@dataclass(frozen=True)
class SpawnSpec:
    """Validated process-spawn request accepted by every SandboxRunner.

    Exactly one of ``argv`` / ``shell`` is set. ``cwd`` must resolve inside
    ``workspace_root`` (or one of ``writable_roots``) — specs that would run
    outside approved roots are rejected before any platform backend sees them.
    """

    profile: str  # EXECUTION_PROFILES
    argv: tuple[str, ...] | None = None
    shell: str | None = None
    cwd: str = ""
    workspace_root: str = ""
    env: dict[str, str] = field(default_factory=dict)
    writable_roots: tuple[str, ...] = ()
    read_only_roots: tuple[str, ...] = ()
    # Restricted profiles deny by default; callers must set "outbound" AND
    # carry a granted net.outbound capability before a backend shares net.
    network: str = "none"  # "none" | "localhost" | "outbound"
    # Capability grants effective for this one spawn (remembered rules ∪
    # one-shot approvals). The backend intersects them with what it can
    # physically provide — they never widen its advertised grantable set.
    granted_capabilities: tuple[str, ...] = ()
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    pty: bool = False
    purpose: str = ""  # audit label e.g. "run_shell" / "runbook:deploy"
    task_id: str | None = None
    project_id: str | None = None
    stdin: "object | None" = None  # asyncio.subprocess stdin target, if any

    def validate(self) -> None:
        from pathlib import Path

        if self.profile not in EXECUTION_PROFILES:
            raise SandboxFailure("invalid_profile", f"unknown execution profile {self.profile!r}")
        if (self.argv is None) == (self.shell is None):
            raise SandboxFailure("invalid_spawn", "exactly one of argv or shell is required")
        if self.argv is not None and not all(self.argv):
            raise SandboxFailure("invalid_spawn", "argv must not contain empty entries")
        if self.shell is not None and not self.shell.strip():
            raise SandboxFailure("invalid_spawn", "shell command is empty")
        if self.network not in {"none", "localhost", "outbound"}:
            raise SandboxFailure("invalid_spawn", f"unknown network policy {self.network!r}")
        if self.profile != "host":
            # Restricted profiles must pin a workspace root, and cwd must live
            # inside an approved writable root. Canonical resolution here keeps
            # symlink/dotdot tricks out of the spawn path.
            if not self.workspace_root:
                raise SandboxFailure("workspace_required", "restricted profiles require workspace_root")
            try:
                cwd = Path(self.cwd).resolve(strict=False)
                roots = [
                    Path(p).resolve(strict=False)
                    for p in (self.workspace_root, *self.writable_roots)
                ]
            except OSError as exc:
                raise SandboxFailure("invalid_spawn", f"cannot resolve spawn paths: {exc}") from exc
            if not any(_within(cwd, root) for root in roots):
                raise SandboxFailure(
                    "outside_roots",
                    f"cwd {cwd} is outside the approved writable roots",
                )


def _within(path: "object", root: "object") -> bool:
    from pathlib import Path

    return path == root or Path(root) in Path(path).parents


@dataclass
class SpawnResult:
    """Final outcome of a finished sandbox process."""

    exit_code: int | None
    output: str = ""
    truncated: bool = False
    timed_out: bool = False
    cancelled: bool = False
    # "exit" | "timeout" | "cancelled" | "limit:<name>" | "failure:<reason>"
    finish_reason: str = "exit"
    limit: str | None = None  # which ResourceLimits bound fired, if any


class SandboxProcess(Protocol):
    """Handle for a running sandboxed process."""

    @property
    def pid(self) -> int | None: ...

    async def wait(self) -> int: ...

    async def terminate(self) -> None:
        """Kill the whole spawned tree, idempotent, never returns before reap."""
        ...

    def metadata(self) -> dict[str, Any]:
        """Effective sandbox metadata for audit events (backend, profile, pid)."""
        ...


@dataclass(frozen=True)
class SandboxCapabilities:
    """Truthful description of what one backend/profile can enforce.

    ``granted`` lists capabilities every spawn under this backend already has
    (host → effectively unrestricted). ``grantable`` lists capabilities that
    can be added without changing backend (e.g. enabling outbound network via
    policy). Anything in neither set is unavailable — policy cannot conjure it.
    """

    backend: str  # "host" | "linux-ns" | "macos-restricted-user" | "windows-token"
    profile: str
    strength: str  # "none" | "soft" | "restricted-user" | "kernel"
    granted: frozenset[str] = frozenset()
    grantable: frozenset[str] = frozenset()
    network_control: bool = False
    filesystem_isolation: bool = False
    identity_isolation: bool = False
    resource_limits: bool = False
    process_tree_kill: bool = True  # every backend must implement tree kill

    def covers(self, capability: str) -> bool:
        granted = self.granted
        if capability in granted or "*:any" in granted:
            return True
        # Namespace wildcards: granted "ns:any" covers capability "ns",
        # "ns:anything" and any deeper dotted name (ns.deeper, ns.deeper:x).
        ns = capability.split(":", 1)[0]
        parts = ns.split(".")
        for depth in range(len(parts), 0, -1):
            if ".".join(parts[:depth]) + ":any" in granted:
                return True
        return False

    def public(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "profile": self.profile,
            "strength": self.strength,
            "granted": sorted(self.granted),
            "grantable": sorted(self.grantable),
            "network_control": self.network_control,
            "filesystem_isolation": self.filesystem_isolation,
            "identity_isolation": self.identity_isolation,
            "resource_limits": self.resource_limits,
            "process_tree_kill": self.process_tree_kill,
        }


class SandboxFailure(Exception):
    """Structured spawn/sandbox rejection.

    ``reason`` is a stable machine-readable code surfaced to the Agent and the
    client so failures stay diagnosable without parsing message text.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def public(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_env_name(name: str) -> bool:
    return bool(_SAFE_NAME.match(name))
