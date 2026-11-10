"""Typed Agent tool registry and structured tool implementations."""
from __future__ import annotations

from termx.agent.tools import desktop, filesystem, git, legacy, planning, project, runbooks, search, shell, subagents, browser,mcp,characters,memory,bots,skills,messaging
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec

_registry: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        registry = ToolRegistry()
        filesystem.register(registry)
        search.register(registry)
        git.register(registry)
        project.register(registry)
        shell.register(registry)
        legacy.register(registry)
        desktop.register(registry)
        planning.register(registry)
        subagents.register(registry)
        runbooks.register(registry)
        browser.register(registry)
        mcp.register(registry)
        characters.register(registry)
        memory.register(registry)
        bots.register(registry)
        messaging.register(registry)
        skills.register(registry)
        _registry = registry
    return _registry


__all__ = ["ToolContext", "ToolOutcome", "ToolRegistry", "ToolSpec", "default_registry"]
