"""Effective tool resolution for agent profiles (tech-specs §4).

Resolved = (explicit ∪ toolsets ∪ connection.tools) − deny_tools,
then ∩ authorized ∩ policy (applied by the caller). Deny always wins.
An empty effective set is valid and never falls back to "all".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResolution:
    tools: list[str]                  # effective tool names (sorted)
    tools_mode: str                   # explicit|all
    review_required: bool = False     # tools omitted → imported "all" intent
    unreviewed: list[str] = field(default_factory=list)  # new server tools
    denied: list[str] = field(default_factory=list)      # dropped by deny
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tools": self.tools,
            "tools_mode": self.tools_mode,
            "review_required": self.review_required,
            "unreviewed": self.unreviewed,
            "denied": self.denied,
            "notes": self.notes,
        }


def _expand_wildcard(
    pattern: str, approved_server_tools: dict[str, list[str]]
) -> list[str]:
    """``server/*`` expands within the server's *approved* snapshot only."""
    if not pattern.endswith("/*"):
        return [pattern]
    server = pattern[:-2]
    return list(approved_server_tools.get(server, []))


def resolve_tools(
    agent: Any,
    *,
    toolset_tools: dict[str, list[str]] | None = None,
    mcp_approved: dict[str, list[str]] | None = None,
) -> ToolResolution:
    """Compute the effective tool set for an AgentFile-like object.

    ``toolset_tools``: toolset.<id> -> pinned tool names.
    ``mcp_approved``: connection.<id> -> approved tool names (the pinned
    snapshot). ``server/*`` patterns in any list expand against this map —
    new upstream tools are NOT auto-added; they surface via ``unreviewed``
    when the caller passes a broader live catalog.
    """
    tools_omitted = bool(getattr(agent, "tools_omitted", False))
    tools_mode = str(getattr(agent, "tools_mode", "explicit"))
    notes: list[str] = []
    if tools_omitted or tools_mode == "all":
        return ToolResolution(
            tools=["*"],
            tools_mode="all",
            review_required=tools_omitted,
            notes=(
                ["tools omitted — imported 'all' intent requires review"]
                if tools_omitted
                else []
            ),
        )

    effective: set[str] = set(getattr(agent, "tools", []) or [])
    toolset_tools = toolset_tools or {}
    for ts in getattr(agent, "toolsets", []) or []:
        if ts not in toolset_tools:
            notes.append(f"toolset '{ts}' not found")
            continue
        effective.update(toolset_tools[ts])
    mcp_approved = mcp_approved or {}
    for conn in getattr(agent, "mcp_connections", []) or []:
        conn_id = conn.get("connection")
        approved = mcp_approved.get(conn_id or "", [])
        wanted = conn.get("tools", [])
        if wanted == "*":
            effective.update(approved)
            continue
        for pattern in wanted if isinstance(wanted, list) else []:
            effective.update(_expand_wildcard(str(pattern), mcp_approved))
        if not wanted:
            notes.append(f"mcp connection '{conn_id}' grants no tools")

    # server/* entries in explicit lists expand too
    expanded: set[str] = set()
    for tool in effective:
        expanded.update(_expand_wildcard(tool, mcp_approved))
    effective = expanded

    denied = sorted(set(getattr(agent, "deny_tools", []) or []))
    for pattern in denied:
        if pattern.endswith("/*"):
            server = pattern[:-2]
            effective = {t for t in effective if not t.startswith(f"{server}:")}
        effective.discard(pattern)

    return ToolResolution(
        tools=sorted(effective),
        tools_mode="explicit",
        denied=denied,
        notes=notes,
    )
