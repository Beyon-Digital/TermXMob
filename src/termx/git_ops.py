"""Minimal Git operations for the remote IDE, scoped to a project root.

All commands run with the project root as the working directory. The host's
own git credentials and configuration are used; nothing is provided by the
client. Commit and push are always separate, explicit actions.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_TIMEOUT = 30


def _git(
    root: str,
    *args: str,
    timeout: int = _TIMEOUT,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        raise HTTPException(400, "Git is not installed on this host")
    try:
        if input_text is None:
            return subprocess.run(
                ["git", "-C", root, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        result = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            input=input_text.encode("utf-8"),
            timeout=timeout,
            check=False,
        )
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"),
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - timing dependent
        raise HTTPException(504, "Git command timed out") from exc


def _require_ok(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise HTTPException(409, detail[:500])
    return result.stdout


def is_repo(root: str) -> bool:
    result = _git(root, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def status(root: str) -> dict[str, Any]:
    if not is_repo(root):
        return {"repo": False, "branch": None, "files": [], "ahead": 0, "behind": 0}
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    porcelain = _git(root, "status", "--porcelain=v1", "--branch").stdout
    files: list[dict[str, Any]] = []
    ahead = behind = 0
    for line in porcelain.splitlines():
        if line.startswith("##"):
            if "ahead " in line:
                ahead = int(line.split("ahead ")[1].split(",")[0].split("]")[0])
            if "behind " in line:
                behind = int(line.split("behind ")[1].split("]")[0])
            continue
        if not line.strip():
            continue
        index_status, worktree_status = line[0], line[1]
        path = line[3:]
        # Renames report "old -> new"; keep the new path for the client.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append({
            "path": path,
            "index": index_status,
            "worktree": worktree_status,
            "staged": index_status not in (" ", "?"),
            "untracked": index_status == "?",
        })
    return {"repo": True, "branch": branch, "files": files, "ahead": ahead, "behind": behind}


def diff(root: str, path: str, staged: bool = False) -> dict[str, Any]:
    args = ["diff", "--no-color"]
    if staged:
        args.append("--cached")
    args += ["--", path]
    result = _git(root, *args)
    return {"path": path, "staged": staged, "diff": _require_ok(result)}


def stage(root: str, paths: list[str], stage_files: bool) -> dict[str, Any]:
    if not paths:
        raise HTTPException(400, "No files selected")
    action = ["add", "--"] if stage_files else ["restore", "--staged", "--"]
    _require_ok(_git(root, *action, *paths))
    return status(root)


def stage_hunk(root: str, patch: str, stage_hunk: bool) -> dict[str, Any]:
    if not patch.strip() or len(patch.encode("utf-8")) > 1_000_000:
        raise HTTPException(400, "Invalid patch")
    for line in patch.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        value = line[4:].split("\t", 1)[0].strip()
        if value == "/dev/null":
            continue
        if value.startswith(("a/", "b/")):
            value = value[2:]
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise HTTPException(403, "Patch must stay inside the project")
    args = ["apply", "--cached", "--unidiff-zero", "--whitespace=nowarn"]
    if not stage_hunk:
        args.append("--reverse")
    _require_ok(_git(root, *args, input_text=patch))
    return status(root)


def commit(root: str, message: str) -> dict[str, Any]:
    _require_ok(_git(root, "commit", "-m", message))
    return status(root)


def branches(root: str) -> dict[str, Any]:
    result = _require_ok(_git(root, "branch", "--format=%(refname:short)"))
    current = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return {"branches": [b.strip() for b in result.splitlines() if b.strip()], "current": current}


def switch_branch(root: str, name: str, create: bool) -> dict[str, Any]:
    args = ["switch", "-c", name] if create else ["switch", name]
    _require_ok(_git(root, *args))
    return status(root)


def fetch(root: str) -> dict[str, Any]:
    _require_ok(_git(root, "fetch", "--prune", timeout=90))
    return status(root)


def pull(root: str) -> dict[str, Any]:
    # Fast-forward only: never create merge commits or rewrite history implicitly.
    _require_ok(_git(root, "pull", "--ff-only", timeout=120))
    return status(root)


def push(root: str) -> dict[str, Any]:
    _require_ok(_git(root, "push", timeout=120))
    return status(root)
