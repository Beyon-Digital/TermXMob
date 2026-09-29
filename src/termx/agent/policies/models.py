"""PolicyIntent: the structured, deterministic identity of an approval-able
action, plus the PolicyRule record helpers shared by engine and store."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# action_type values: "tool" | "capability" | "publication" | "computer"
# scope_type values:   "task" | "project" | "custom_agent" | "host"
# effect values:       "allow" | "deny"
# fingerprint_kind:    "exact" | "conservative"

SCOPE_ORDER = {"task": 0, "custom_agent": 1, "project": 2, "host": 3}
REMEMBER_SCOPES = ("task", "project", "custom_agent")


@dataclass(frozen=True)
class PolicyIntent:
    """What a tool call wants to do, normalized for matching.

    ``fingerprint`` is the deterministic matcher id remembered rules bind to.
    ``required_capabilities`` names sandbox capabilities the action needs —
    remembered allow never implies them (see engine.evaluate).
    """

    action_type: str
    tool: str
    fingerprint: str
    fingerprint_kind: str
    display: str  # redacted human-readable summary, safe to persist
    cwd: str
    project_id: str = ""
    task_id: str = ""
    custom_agent_id: str | None = None
    sandbox_profile: str = "agent"
    required_capabilities: tuple[str, ...] = ()
    risk_class: str = "safe"  # safe|consequential|sensitive|publication|privilege|external|outside_root
    arguments_digest: str = ""
    matcher: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        """Serializable form embedded in approval payloads."""
        return {
            "action_type": self.action_type,
            "tool": self.tool,
            "fingerprint": self.fingerprint,
            "fingerprint_kind": self.fingerprint_kind,
            "display": self.display,
            "cwd": self.cwd,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "custom_agent_id": self.custom_agent_id,
            "sandbox_profile": self.sandbox_profile,
            "required_capabilities": list(self.required_capabilities),
            "risk_class": self.risk_class,
            "arguments_digest": self.arguments_digest,
            "matcher": self.matcher,
        }

    @staticmethod
    def from_record(data: dict[str, Any]) -> "PolicyIntent":
        return PolicyIntent(
            action_type=str(data.get("action_type") or "tool"),
            tool=str(data.get("tool") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
            fingerprint_kind=str(data.get("fingerprint_kind") or "exact"),
            display=str(data.get("display") or ""),
            cwd=str(data.get("cwd") or ""),
            project_id=str(data.get("project_id") or ""),
            task_id=str(data.get("task_id") or ""),
            custom_agent_id=data.get("custom_agent_id"),
            sandbox_profile=str(data.get("sandbox_profile") or "agent"),
            required_capabilities=tuple(data.get("required_capabilities") or ()),
            risk_class=str(data.get("risk_class") or "safe"),
            arguments_digest=str(data.get("arguments_digest") or ""),
            matcher=dict(data.get("matcher") or {}),
        )

    def to_public(self) -> dict[str, Any]:
        """Redacted view for approval payloads, events, and policy APIs."""
        return {
            "action_type": self.action_type,
            "tool": self.tool,
            "fingerprint": self.fingerprint,
            "fingerprint_kind": self.fingerprint_kind,
            "display": self.display,
            "required_capabilities": list(self.required_capabilities),
            "risk_class": self.risk_class,
            "sandbox_profile": self.sandbox_profile,
            "matcher": self.matcher,
        }


def rule_public(rule: dict[str, Any]) -> dict[str, Any]:
    """Store-row → API-safe policy rule view.

    ``display``/``matcher`` go through the shared redactor: engine-created
    rules are already redacted, but API-created rows could embed secrets the
    listing endpoint must not echo back.
    """
    from termx.agent.policy import redact

    matcher = {
        str(k): (redact(str(v)) if isinstance(v, str) else v)
        for k, v in dict(rule["matcher"] or {}).items()
    }
    return {
        "id": rule["id"],
        "version": rule["version"],
        "effect": rule["effect"],
        "scope_type": rule["scope_type"],
        "scope_id": rule["scope_id"],
        "action_type": rule["action_type"],
        "tool": rule["tool"],
        "fingerprint": rule["fingerprint"],
        "fingerprint_kind": rule["fingerprint_kind"],
        "matcher": matcher,
        "capabilities": rule["capabilities"],
        "sandbox_profile": rule["sandbox_profile"],
        "source_approval_id": rule["source_approval_id"],
        "task_id": rule["task_id"],
        "project_id": rule["project_id"],
        "display": redact(str(rule["display"] or "")),
        "created_at": rule["created_at"],
        "updated_at": rule["updated_at"],
        "last_used_at": rule["last_used_at"],
        "expires_at": rule["expires_at"],
        "times_used": rule["times_used"],
        "revoked": rule["revoked_at"] is not None,
        "revoked_at": rule["revoked_at"],
    }
