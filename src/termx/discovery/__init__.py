"""Trusted, scoped extension discovery for TermX.

Scans ``~/.agents/`` (user root), authorized project ``<root>/.agents``
directories, and compat locations (``.claude/skills``, ``.github/agents``,
``AGENTS.md``, ``.devin``, ``.codex``) into a catalog of typed entries.
Discovery never spawns processes, never connects to servers, and never
follows symlinks — it only reads bounded metadata.
"""

from .index import DiscoveryIndex, ExtensionEntry, ScanReport
from .roots import ScanRoot, default_roots
from .safety import DirWalkError, iter_entries, safe_read_text

__all__ = [
    "DirWalkError",
    "DiscoveryIndex",
    "ExtensionEntry",
    "ScanReport",
    "ScanRoot",
    "default_roots",
    "iter_entries",
    "safe_read_text",
]
