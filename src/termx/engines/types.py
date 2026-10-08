"""Frozen engine contracts (plans/engine-extensions/tech-specs.md §1-2)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

ENGINE_SCHEMA_VERSION = 1


class EngineKind(str, Enum):
    INTERNAL = "internal"
    CODEX = "codex"
    CLAUDE = "claude"


AUTH_UNKNOWN = "unknown"
AUTH_AUTHENTICATED = "authenticated"
AUTH_UNAUTHENTICATED = "unauthenticated"
AUTH_EXPIRED = "expired"
AUTH_NOT_REQUIRED = "not_required"

SESSION_ACTIVE = "active"
SESSION_IDLE = "idle"
SESSION_CLOSED = "closed"
SESSION_LOST = "lost"


@dataclass
class EngineDescriptor:
    id: str
    label: str
    installed: bool = False
    executable: str | None = None
    version: str | None = None
    auth_state: str = AUTH_UNKNOWN
    auth_detail: str = ""
    location: str = "local-process"
    transport: str = "in-process"
    protocol: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "installed": self.installed,
            "executable": self.executable,
            "version": self.version,
            "auth_state": self.auth_state,
            "auth_detail": self.auth_detail,
            "location": self.location,
            "transport": self.transport,
            "protocol": self.protocol,
            "error": self.error,
        }


@dataclass
class EngineCapabilities:
    """Honesty rule: every value is an enforcement status, never optimistic."""

    tools_filter: str = "unsupported"  # native|gateway|os|advisory|unsupported|unverified
    approvals: str = "unsupported"
    streaming: bool = False
    resume: str = "unverified"
    steer: str = "unverified"
    fork: str = "unverified"
    subagents: str = "unverified"
    skills_native: str = "unverified"
    mcp_native: str = "unverified"
    models: list[str] = field(default_factory=list)
    modes: list[dict[str, Any]] = field(default_factory=list)
    config_options: list[dict[str, Any]] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tools_filter": self.tools_filter,
            "approvals": self.approvals,
            "streaming": self.streaming,
            "resume": self.resume,
            "steer": self.steer,
            "fork": self.fork,
            "subagents": self.subagents,
            "skills_native": self.skills_native,
            "mcp_native": self.mcp_native,
            "models": self.models,
            "modes": self.modes,
            "config_options": self.config_options,
            "notes": self.notes,
        }


@dataclass
class EngineSessionBinding:
    binding_id: str
    engine: str
    native_session_id: str
    native_turn_id: str | None = None
    conversation_id: str | None = None
    project_id: str | None = None
    cwd: str = ""
    profile_revision: str = ""
    extensions_snapshot: dict[str, Any] = field(default_factory=dict)
    account_scope: str = ""
    status: str = SESSION_ACTIVE
    replay_cursor: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def new(cls, engine: str, native_session_id: str, **kw: Any) -> "EngineSessionBinding":
        return cls(
            binding_id=f"eng_{uuid.uuid4().hex[:16]}",
            engine=engine,
            native_session_id=native_session_id,
            **kw,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "engine": self.engine,
            "native_session_id": self.native_session_id,
            "native_turn_id": self.native_turn_id,
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "cwd": self.cwd,
            "profile_revision": self.profile_revision,
            "extensions_snapshot": self.extensions_snapshot,
            "account_scope": self.account_scope,
            "status": self.status,
            "replay_cursor": self.replay_cursor,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class EffectiveRunConfiguration:
    """Immutable per-session configuration resolved at create time."""

    engine: str = EngineKind.INTERNAL.value
    model: str | None = None
    mode: str | None = None
    config_options: dict[str, str | bool] = field(default_factory=dict)
    agent_profile_revision: str = ""
    agent_id: str | None = None
    instructions: str = ""
    tools: dict[str, Any] = field(default_factory=dict)  # ResolvedToolSet
    skills: list[dict[str, Any]] = field(default_factory=list)
    workflow: str | None = None
    mcp_bindings: list[dict[str, Any]] = field(default_factory=list)
    sandbox_profile: str = "agent"
    approval_mode: str = "standard"
    cwd: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "model": self.model,
            "mode": self.mode,
            "config_options": self.config_options,
            "agent_profile_revision": self.agent_profile_revision,
            "agent_id": self.agent_id,
            "tools": self.tools,
            "skills": self.skills,
            "workflow": self.workflow,
            "mcp_bindings": [{**b,'env':{name:'[credential]' for name in (b.get('env') or {})},'headers':{name:'[credential]' for name in (b.get('headers') or {})}} for b in self.mcp_bindings],
            "sandbox_profile": self.sandbox_profile,
            "approval_mode": self.approval_mode,
            "cwd": self.cwd,
        }


@dataclass
class EngineEvent:
    """Normalized envelope persisted via the existing events table."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    native: dict[str, Any] = field(default_factory=dict)

    def as_wire(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "payload": self.payload,
            "native": self.native,
            "schema": ENGINE_SCHEMA_VERSION,
        }
