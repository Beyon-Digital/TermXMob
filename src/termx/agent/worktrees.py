"""Worktree/checkpoint task execution (PROD-003).

Git worktrees give Agent-mode tasks a dedicated checkout+branch so file
changes never touch the user's working tree until an explicit apply. All
mutations go through plain `git -C` subprocesses and every destructive path
requires a dirty check or explicit confirmation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from termx.config import config_dir

BRANCH_PREFIX = "termx/task-"


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def git_root(path: str) -> str | None:
    """Top-level of the Git worktree containing path, or None for non-repos."""
    try:
        return _git(path, "rev-parse", "--show-toplevel")
    except Exception:
        return None


def head_sha(path: str) -> str:
    return _git(path, "rev-parse", "HEAD")


def create_worktree(base_repo: str, slug: str) -> dict[str, str]:
    """Create `<config>/worktrees/<slug>` on branch `termx/task-<slug>`.

    Kept outside the repository so the worktree itself never shows up as
    untracked content in the user's checkout.
    """
    branch = f"{BRANCH_PREFIX}{slug}"
    root = config_dir() / "worktrees"
    root.mkdir(parents=True, exist_ok=True)
    worktree_path = root / slug
    base_ref = head_sha(base_repo)
    _git(base_repo, "worktree", "add", "-b", branch, str(worktree_path), base_ref)
    return {"worktree_path": str(worktree_path), "branch": branch, "base_ref": base_ref}


def worktree_dirty(path: str) -> list[str]:
    """Porcelain status lines — any non-empty result means unknown changes."""
    out = _git(path, "status", "--porcelain")
    return [line for line in out.splitlines() if line.strip()]


def diff_against(base_repo: str, base_ref: str, worktree_path: str) -> list[dict[str, str]]:
    """name-status diff of the task worktree vs its base ref."""
    try:
        out = _git(worktree_path, "diff", "--name-status", f"{base_ref}..HEAD")
    except Exception:
        return []
    entries = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            entries.append({"status": parts[0], "path": parts[-1]})
    return entries


def apply_worktree(base_repo: str, worktree_path: str, branch: str, task_id: str) -> str:
    """Commit outstanding worktree changes and merge the branch into base.

    Returns the merged head SHA. The worktree and branch are removed after a
    successful merge — the base checkout becomes the source of truth.
    """
    if worktree_dirty(worktree_path):
        _git(worktree_path, "add", "-A")
        _git(worktree_path, "commit", "-m", f"termx: task {task_id[:12]}")
    head = head_sha(worktree_path)
    _git(base_repo, "merge", "--no-ff", branch, "-m", f"termx: apply task {task_id[:12]}")
    _git(base_repo, "worktree", "remove", str(worktree_path))
    try:
        _git(base_repo, "branch", "-D", branch)
    except Exception:
        pass
    return head


def discard_worktree(base_repo: str, worktree_path: str, branch: str) -> None:
    """Remove the worktree and its branch. Callers must dirty-check first."""
    _git(base_repo, "worktree", "remove", "--force", str(worktree_path))
    try:
        _git(base_repo, "branch", "-D", branch)
    except Exception:
        pass


def worktree_exists(path: str) -> bool:
    return Path(path).is_dir()


class WorktreeConfirmRequired(Exception):
    """Raised when discarding a worktree that still has uncommitted changes."""

    def __init__(self, dirty: list[str]) -> None:
        super().__init__("worktree has uncommitted changes")
        self.dirty = dirty
