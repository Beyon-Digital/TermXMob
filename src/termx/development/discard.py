"""Discarding working tree changes, the only delivery operation that destroys user work.

Every step is deliberately narrow:

* only files the host's own status lists can be touched; a path with no change is reported as already clean;
* every path is resolved and must stay inside the checkout (a symlink that leaves it is refused);
* paths are literal (no globs, no pathspec magic) and the number of paths is capped;
* tracked changes are restored from the index or HEAD, untracked files are removed with ``git clean -f`` (never ``-x``);
* a folder is accepted only when everything git reports inside it is untracked;
* merge conflicts are never discarded here;
* by default only the unstaged side is discarded (restored from the index, so staged work stays); ``include_staged``
  also drops the staged side (restored from HEAD), which is what discarding from the Staged changes group means.

``review`` is the dry run: exactly what would be lost, shown in the confirmation. ``apply`` repeats it and acts on that.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from termx import git_ops

MAX_PATHS = 500
MAX_ENTRIES = 5000
MAX_PATH_LENGTH = 4096
CONFLICT = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def normalize(root: str, paths: Any) -> list[str]:
    """The requested paths, relative, normalised and confined to the checkout. Raises 400, 403 or 413."""
    if not isinstance(paths, list) or not paths:
        raise HTTPException(400, "Select the files to discard")
    if len(paths) > MAX_PATHS:
        raise HTTPException(413, f"Discard at most {MAX_PATHS} paths at once")
    base = Path(root).resolve()
    clean: list[str] = []
    for value in paths:
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_PATH_LENGTH or "\0" in value:
            raise HTTPException(400, "Invalid path")
        if value.startswith("/") or value.startswith("\\") or re.match(r"[A-Za-z]:", value):
            raise HTTPException(403, "Git paths must stay inside the project")
        relative = posixpath.normpath(value.replace("\\", "/"))
        if relative in ("", ".") or relative == ".." or relative.startswith("../") or relative == ".git" or relative.startswith(".git/"):
            raise HTTPException(403, "Git paths must stay inside the project")
        if not (base / relative).resolve().is_relative_to(base):
            raise HTTPException(403, "Git paths must stay inside the project")
        if relative not in clean:
            clean.append(relative)
    return clean


def _status(root: str) -> list[dict[str, Any]]:
    """The host's own changed-file list: index and worktree codes, and the original path of a rename.

    It is read for the whole checkout and filtered here, because git only pairs a rename's two sides when it sees both.
    """
    out = git_ops._require_ok(git_ops._git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    entries: list[dict[str, Any]] = []
    parts = iter(out.split("\0"))
    for item in parts:
        if not item:
            continue
        code, path = item[:2], item[3:]
        origin = next(parts, "") if "R" in code or "C" in code else None
        entries.append({"code": code, "path": path, "origin": origin})
    return entries


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


def review(root: str, paths: Any, include_staged: bool = False) -> dict[str, Any]:
    """Dry run. Returns what would be lost, per file, and what is already clean. Changes nothing."""
    requested = normalize(root, paths)
    base = Path(root).resolve()
    entries = _status(root)
    files: list[dict[str, Any]] = []
    folders: list[str] = []
    clean: list[str] = []
    for path in requested:
        below = [entry for entry in entries if _inside(entry["path"], path)]
        found = below
        absolute = base / path
        if absolute.is_dir() and not absolute.is_symlink():
            if not found:
                clean.append(path)
                continue
            if any(entry["code"] != "??" for entry in found):
                raise HTTPException(400, f"{path} is a folder with tracked changes. Choose its files instead.")
            folders.append(path)
        else:
            found = [entry for entry in below if entry["path"] == path]
            if not found and below:
                raise HTTPException(400, f"{path} is a folder with tracked changes. Choose its files instead.")
            if not found:
                clean.append(path)
                continue
        for entry in found:
            code = entry["code"]
            if code in CONFLICT:
                raise HTTPException(409, f"{entry['path']} has a merge conflict. Resolve it instead of discarding it.")
            if code == "!!":
                continue
            staged = code[0] not in " ?"
            worktree = code[1] not in " ?"
            keeps_staged = staged and not include_staged
            if keeps_staged:
                if not worktree:
                    clean.append(entry["path"])
                    continue
                staged = False
            if code == "??":
                kind = "untracked"
            elif staged:
                kind = "staged"
            elif code[1] == "D":
                kind = "deleted"
            else:
                kind = "modified"
            record = {"path": entry["path"], "kind": kind, "staged": staged, "worktree": worktree, "keeps_staged": keeps_staged}
            if entry["origin"]:
                record["origin"] = entry["origin"]
            if all(item["path"] != record["path"] for item in files):
                files.append(record)
    if len(files) > MAX_ENTRIES:
        raise HTTPException(413, "Too many changed files to discard at once")
    counts = {kind: sum(1 for item in files if item["kind"] == kind) for kind in ("modified", "deleted", "staged", "untracked")}
    return {"files": files, "folders": folders, "already_clean": clean, "counts": counts}


def apply(root: str, paths: Any, include_staged: bool = False) -> dict[str, Any]:
    """Discard what ``review`` lists. A change that is already gone is not an error."""
    plan = review(root, paths, include_staged)
    files = plan["files"]
    folders = plan["folders"]
    staged = []
    for item in files:
        if item["kind"] == "staged":
            staged.append(item["path"])
            if item.get("origin"):
                staged.append(item["origin"])
    tracked = [item["path"] for item in files if item["kind"] in ("modified", "deleted")]
    loose = [item["path"] for item in files if item["kind"] == "untracked" and not any(_inside(item["path"], folder) for folder in folders)]
    if staged:
        git_ops._require_ok(git_ops._git(root, "--literal-pathspecs", "restore", "--staged", "--worktree", "--", *staged))
    if tracked:
        git_ops._require_ok(git_ops._git(root, "--literal-pathspecs", "restore", "--worktree", "--", *tracked))
    if loose:
        git_ops._require_ok(git_ops._git(root, "--literal-pathspecs", "clean", "-f", "--", *loose))
    if folders:
        git_ops._require_ok(git_ops._git(root, "--literal-pathspecs", "clean", "-f", "-d", "--", *folders))
    return {"discarded": files, "folders": folders, "already_clean": plan["already_clean"], "counts": plan["counts"],
            "status": git_ops.status(root)}
