"""Subagent handoff: a structured brief goes in, a final message plus changed files comes out."""
from __future__ import annotations

import subprocess
from collections.abc import Iterable

from termx.agent import worktrees


def build_brief(
    goal: str,
    *,
    constraints: str = "",
    files: Iterable[str] = (),
    context: str = "",
) -> str:
    """The child's first prompt: goal, constraints, file paths and the earlier work it builds on."""
    sections = [f"Goal\n{goal.strip()}"]
    if constraints.strip():
        sections.append(f"Constraints\n{constraints.strip()}")
    paths = [str(path).strip() for path in files if str(path).strip()]
    if paths:
        sections.append("Files\n" + "\n".join(f"- {path}" for path in paths))
    if context.strip():
        sections.append(f"Context\n{context.strip()}")
    return "\n\n".join(sections)


def changed_files(worktree_path: str, base_ref: str) -> list[str]:
    """Paths a child changed in its worktree since ``base_ref``, committed or not. Empty when unreadable."""
    try:
        paths = {entry["path"] for entry in worktrees.diff_against("", base_ref, worktree_path)}
        for line in worktrees.worktree_dirty(worktree_path):
            # The status column may have lost its leading space; split on it instead of slicing.
            paths.add(line.split(None, 1)[1].split(" -> ")[-1].strip().strip('"'))
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return []
    return sorted(paths)
