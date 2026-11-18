"""Interactive coding-CLI session tools (slice A5)."""
from __future__ import annotations

from termx.agent.tools.registry import ToolRegistry


def register(registry: ToolRegistry) -> None:
    """Registered by default_registry(); the owning slice adds its ToolSpecs here."""
