"""``*.agent.md`` parse / serialize / validate (tech-specs §4).

Canonical location: ``~/.agents/agents/<id>.agent.md``. Frontmatter is a
safe-loaded YAML mapping; unknown vendor keys are preserved verbatim for
lossless round-trip but are never executed. The markdown body is the agent's
instructions.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from ..discovery.safety import slugify
from ..frontmatter import dump_frontmatter, split_frontmatter

SCHEMA_VERSION = 1
ENGINES = {"internal", "codex", "claude", "inherit"}
APPROVAL_MODES = {"standard", "remember", "autonomous"}
SANDBOX_PROFILES = {"host", "workspace", "agent"}
SKILL_MODES = {"auto", "manual", "off"}
TOOLS_MODES = {"explicit", "all"}

# top-level keys understood natively; everything else is preserved verbatim
KNOWN_TOP_LEVEL = {
    "name",
    "description",
    "tools",
    "target",
    "mcp-servers",
    "disable-model-invocation",
    "user-invocable",
    "model",
    "x-termx",
}


class AgentFileError(ValueError):
    pass


def _err_list() -> list[str]:
    return []


@dataclass
class AgentFile:
    """One parsed agent definition."""

    slug: str = ""
    name: str = ""
    description: str = ""
    instructions: str = ""
    engine: str = "inherit"          # x-termx.engine
    model: str | None = None         # x-termx.model or top-level model
    engine_mode: str | None = None
    config_options: dict[str, str | bool] = field(default_factory=dict)
    enabled: bool = True
    auto_use: bool = True
    tools_mode: str = "explicit"
    tools: list[str] = field(default_factory=list)
    tools_omitted: bool = False      # tools key absent → review_required
    toolsets: list[str] = field(default_factory=list)
    deny_tools: list[str] = field(default_factory=list)
    skills_mode: str = "auto"
    skills_include: list[str] = field(default_factory=list)
    skills_exclude: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    mcp_connections: list[dict[str, Any]] = field(default_factory=list)
    delegation: dict[str, Any] = field(default_factory=dict)
    policy_profile: str | None = None
    approval_mode: str = "standard"
    sandbox_profile: str = "agent"
    limits: dict[str, Any] = field(default_factory=dict)
    # Copilot interop (diagnostic only — never executed)
    copilot_target: str | None = None
    copilot_mcp_servers: dict[str, Any] | None = None
    # preserved verbatim for round-trip
    vendor_extra: dict[str, Any] = field(default_factory=dict)
    x_termx_extra: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=_err_list)
    revision: str = ""               # sha256 of file contents

    @property
    def qid_id(self) -> str:
        return f"agent.{self.slug}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.qid_id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "engine": self.engine,
            "model": self.model,
            "engine_mode": self.engine_mode,
            "config_options": dict(self.config_options),
            "enabled": self.enabled,
            "auto_use": self.auto_use,
            "tools_mode": self.tools_mode,
            "tools": list(self.tools),
            "tools_omitted": self.tools_omitted,
            "toolsets": list(self.toolsets),
            "deny_tools": list(self.deny_tools),
            "skills": {
                "mode": self.skills_mode,
                "include": list(self.skills_include),
                "exclude": list(self.skills_exclude),
            },
            "workflows": list(self.workflows),
            "mcp_connections": [dict(c) for c in self.mcp_connections],
            "delegation": dict(self.delegation),
            "policy_profile": self.policy_profile,
            "approval_mode": self.approval_mode,
            "sandbox_profile": self.sandbox_profile,
            "limits": dict(self.limits),
            "copilot_target": self.copilot_target,
            "vendor_extra_keys": sorted(self.vendor_extra),
            "revision": self.revision,
            "errors": list(self.errors),
        }


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _as_str_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def validate_config_options(value: Any) -> dict[str, str | bool]:
    if not isinstance(value, dict) or any(
        not isinstance(k, str) or not isinstance(v, (str, bool)) for k, v in value.items()
    ):
        raise AgentFileError("config_options must map option IDs to strings or booleans")
    return dict(value)


def parse_agent_file(text: str, *, slug_hint: str = "") -> AgentFile:
    """Parse agent markdown into an AgentFile. Collects errors; never raises
    for content problems (structural YAML errors raise AgentFileError)."""
    revision = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    try:
        meta, body = split_frontmatter(text)
    except Exception as exc:  # noqa: BLE001
        raise AgentFileError(f"invalid frontmatter: {exc}") from exc
    errors: list[str] = []
    xt = meta.get("x-termx") or {}
    if not isinstance(xt, dict):
        errors.append("x-termx must be a mapping")
        xt = {}

    name = str(meta.get("name") or "").strip()
    slug = slugify(str(xt.get("id") or name or slug_hint).removeprefix("agent."))
    if not name:
        errors.append("name is required")
    if len(name) > 200:
        errors.append("name exceeds 200 chars")
    desc = str(meta.get("description") or "")
    if len(desc) > 2000:
        errors.append("description exceeds 2000 chars")

    # tools semantics (spec §4): omitted → all + review_required
    tools_present = "tools" in meta
    tools = _as_str_list(meta.get("tools"))
    tools_mode = str(xt.get("tools_mode") or ("all" if not tools_present else "explicit"))
    if tools_mode not in TOOLS_MODES:
        errors.append(f"tools_mode must be one of {sorted(TOOLS_MODES)}")
        tools_mode = "explicit"

    engine = str(xt.get("engine") or "inherit")
    if engine not in ENGINES and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", engine):
        errors.append("engine must be a built-in engine or a valid ACP runner ID")
        engine = "inherit"
    approval = str(xt.get("approval_mode") or "standard")
    if approval not in APPROVAL_MODES:
        errors.append("approval_mode must be standard|remember|autonomous")
        approval = "standard"
    sandbox = str(xt.get("sandbox_profile") or "agent")
    if sandbox not in SANDBOX_PROFILES:
        errors.append("sandbox_profile must be host|workspace|agent")
        sandbox = "agent"

    skills = xt.get("skills") or {}
    if not isinstance(skills, dict):
        errors.append("x-termx.skills must be a mapping")
        skills = {}
    skills_mode = str(skills.get("mode") or "auto")
    if skills_mode not in SKILL_MODES:
        errors.append("skills.mode must be auto|manual|off")
        skills_mode = "auto"

    delegation = xt.get("delegation") or {}
    if not isinstance(delegation, dict):
        errors.append("x-termx.delegation must be a mapping")
        delegation = {}
    delegation.setdefault("enabled", bool(delegation.get("allowed_agents")))
    delegation.setdefault("max_depth", 1)
    delegation.setdefault("max_children", 4)

    mcp_conns = _as_str_dict_list(xt.get("mcp_connections"))
    for conn in mcp_conns:
        if "connection" not in conn:
            errors.append("mcp_connections entries need a 'connection' id")
    engine_mode = xt.get("engine_mode")
    if engine_mode is not None and (not isinstance(engine_mode, str) or not engine_mode.strip()):
        errors.append("engine_mode must be a non-empty string")
        engine_mode = None
    try:
        config_options = validate_config_options(xt.get("config_options", {}))
    except AgentFileError as exc:
        errors.append(str(exc))
        config_options = {}

    known_xt = {
        "schema-version", "id", "engine", "model", "enabled", "auto_use",
        "tools_mode", "toolsets", "deny_tools", "skills", "workflows",
        "mcp_connections", "delegation", "policy_profile", "approval_mode",
        "sandbox_profile", "limits", "engine_mode", "config_options",
    }

    af = AgentFile(
        slug=slug,
        name=name or slug,
        description=desc,
        instructions=body.strip(),
        engine=engine,
        model=(xt.get("model") or meta.get("model") or None),
        engine_mode=engine_mode,
        config_options=config_options,
        enabled=bool(xt.get("enabled", meta.get("user-invocable", True))),
        auto_use=not bool(meta.get("disable-model-invocation", False))
        and bool(xt.get("auto_use", True)),
        tools_mode=tools_mode,
        tools=tools,
        tools_omitted=not tools_present,
        toolsets=_as_str_list(xt.get("toolsets")),
        deny_tools=_as_str_list(xt.get("deny_tools")),
        skills_mode=skills_mode,
        skills_include=_as_str_list(skills.get("include")),
        skills_exclude=_as_str_list(skills.get("exclude")),
        workflows=_as_str_list(xt.get("workflows")),
        mcp_connections=mcp_conns,
        delegation=delegation,
        policy_profile=xt.get("policy_profile"),
        approval_mode=approval,
        sandbox_profile=sandbox,
        limits=dict(xt.get("limits") or {}),
        copilot_target=meta.get("target"),
        copilot_mcp_servers=meta.get("mcp-servers"),
        vendor_extra={
            k: v for k, v in meta.items() if k not in KNOWN_TOP_LEVEL
        },
        x_termx_extra={k: v for k, v in xt.items() if k not in known_xt},
        errors=errors,
        revision=revision,
    )
    return af


def serialize_agent(agent: AgentFile) -> str:
    """Render an AgentFile back to canonical ``*.agent.md`` text."""
    meta: dict[str, Any] = {"name": agent.name}
    if agent.description:
        meta["description"] = agent.description
    if agent.tools_omitted:
        pass  # keep key absent — means "all" on next parse
    else:
        meta["tools"] = list(agent.tools)
    if agent.copilot_target:
        meta["target"] = agent.copilot_target
    if agent.copilot_mcp_servers is not None:
        meta["mcp-servers"] = agent.copilot_mcp_servers
    if not agent.auto_use:
        meta["disable-model-invocation"] = True
    if not agent.enabled:
        meta["user-invocable"] = False
    if agent.model and agent.engine == "inherit":
        meta["model"] = agent.model
    xt: dict[str, Any] = {
        "schema-version": SCHEMA_VERSION,
        "id": agent.qid_id,
        "engine": agent.engine,
        "enabled": agent.enabled,
        "auto_use": agent.auto_use,
        "tools_mode": agent.tools_mode,
    }
    if agent.model and agent.engine != "inherit":
        xt["model"] = agent.model
    if agent.engine_mode:
        xt["engine_mode"] = agent.engine_mode
    if agent.config_options:
        xt["config_options"] = validate_config_options(agent.config_options)
    if agent.toolsets:
        xt["toolsets"] = list(agent.toolsets)
    if agent.deny_tools:
        xt["deny_tools"] = list(agent.deny_tools)
    xt["skills"] = {
        "mode": agent.skills_mode,
        "include": list(agent.skills_include),
        "exclude": list(agent.skills_exclude),
    }
    if agent.workflows:
        xt["workflows"] = list(agent.workflows)
    if agent.mcp_connections:
        xt["mcp_connections"] = [dict(c) for c in agent.mcp_connections]
    if agent.delegation:
        xt["delegation"] = dict(agent.delegation)
    if agent.policy_profile:
        xt["policy_profile"] = agent.policy_profile
    xt["approval_mode"] = agent.approval_mode
    xt["sandbox_profile"] = agent.sandbox_profile
    if agent.limits:
        xt["limits"] = dict(agent.limits)
    xt.update(agent.x_termx_extra)
    meta["x-termx"] = xt
    # vendor extras last, verbatim
    for key, value in agent.vendor_extra.items():
        if key not in meta:
            meta[key] = value
    body = agent.instructions.rstrip() + "\n" if agent.instructions else ""
    return dump_frontmatter(meta, body)
