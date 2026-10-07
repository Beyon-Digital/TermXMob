"""Typed Agent tool registry and structured tool implementations."""
from __future__ import annotations

from termx.agent.tools import filesystem, git, legacy, project, runbooks, search, shell, subagents, browser
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
        subagents.register(registry)
        runbooks.register(registry)
        browser.register(registry)
        _registry = registry
    return _registry


__all__ = ["ToolContext", "ToolOutcome", "ToolRegistry", "ToolSpec", "default_registry"]
