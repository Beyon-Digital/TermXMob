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


def current_branch(path: str) -> str | None:
    """Checked-out branch name, or None when HEAD is detached."""
    name = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    return None if name == "HEAD" else name


def create_worktree(base_repo: str, slug: str) -> dict[str, str | None]:
    """Create `<config>/worktrees/<slug>` on branch `termx/task-<slug>`.

    Kept outside the repository so the worktree itself never shows up as
    untracked content in the user's checkout. Records the base branch so a
    later apply can refuse to land on a drifted checkout.
    """
    branch = f"{BRANCH_PREFIX}{slug}"
    root = config_dir() / "worktrees"
    root.mkdir(parents=True, exist_ok=True)
    worktree_path = root / slug
    base_ref = head_sha(base_repo)
    base_branch = current_branch(base_repo)
    _git(base_repo, "worktree", "add", "-b", branch, str(worktree_path), base_ref)
    return {
        "worktree_path": str(worktree_path),
        "branch": branch,
        "base_ref": base_ref,
        "base_branch": base_branch,
    }


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


def apply_worktree(
    base_repo: str,
    worktree_path: str,
    branch: str,
    task_id: str,
    *,
    base_branch: str | None = None,
) -> str:
    """Commit outstanding worktree changes and merge the branch into base.

    Branch-safe: refuses to merge when the base checkout has moved off the
    branch recorded at creation. Conflict-transactional: a failed merge is
    aborted so the base repo is never left in MERGING state; the worktree
    and branch are kept for retry/discard.

    Returns the merged head SHA. The worktree and branch are removed after a
    successful merge — the base checkout becomes the source of truth.
    """
    checked_out = current_branch(base_repo)
    if checked_out is None:
        raise WorktreeApplyConflict(
            "base checkout is detached — checkout the base branch before applying"
        )
    if base_branch is not None and checked_out != base_branch:
        raise WorktreeApplyConflict(
            f"base checkout is on '{checked_out}' but the worktree was created "
            f"from '{base_branch}' — checkout '{base_branch}' or discard"
        )
    if worktree_dirty(worktree_path):
        _git(worktree_path, "add", "-A")
        _git(worktree_path, "commit", "-m", f"termx: task {task_id[:12]}")
    head = head_sha(worktree_path)
    try:
        _git(base_repo, "merge", "--no-ff", branch, "-m", f"termx: apply task {task_id[:12]}")
    except RuntimeError as exc:
        try:
            _git(base_repo, "merge", "--abort")
        except Exception:
            pass
        raise WorktreeApplyConflict(
            f"merge failed and was rolled back: {exc}"
        ) from exc
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


class WorktreeApplyConflict(RuntimeError):
    """Apply refused/failed safely — base repo left unmodified."""
