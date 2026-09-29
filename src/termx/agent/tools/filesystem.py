"""Structured filesystem tools: list_files, read_file, write_file, apply_patch.

All path handling goes through the existing ProjectFiles boundary checks
(absolute paths, '..', and escaping symlinks are rejected). Sensitive paths
are refused inline through every route — they never become approvals.
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from termx.agent.context.manifest import _secret_name
from termx.agent.policy import is_sensitive_path
from termx.agent.providers import ProviderCall
from termx.agent.tools.helpers import (
    call_bool,
    call_int,
    call_string,
    denied_result,
    error_result,
    http_error_result,
)
from termx.agent.tools.registry import (
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    decide_never,
)

_SKIP_DIRS = {".git", ".termx", "__pycache__", ".venv", "venv", "node_modules", "dist", "build", "target", ".next", ".mypy_cache", ".pytest_cache"}
_MAX_LIST_ENTRIES = 500
# How far a hunk may drift from its stated line and still apply; matches
# must also be unique within that window so stale patches can't silently
# rewrite a later identical block.
_FUZZ_WINDOW_LINES = 10


def _create_exclusive(path: Path, content: str) -> None:
    """Create ``path`` only if it does not exist (no check-then-write race)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


_DEFAULT_READ_LIMIT = 400
_PATCH_PREVIEW_LINES = 40


def _list_entries(root: Path, base: Path, pattern: str, recursive: bool, limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if recursive:
        walker = os.walk(base)
        paths: list[Path] = []
        for current, dirnames, filenames in walker:
            current_path = Path(current)
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".") and not _secret_name(d)]
            for name in sorted(dirnames):
                paths.append(current_path / name)
            for name in sorted(filenames):
                paths.append(current_path / name)
    else:
        paths = sorted(base.iterdir())
        paths = [p for p in paths if p.name not in _SKIP_DIRS and not p.name.startswith(".")]
    for path in paths:
        if len(entries) >= limit:
            break
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _SKIP_DIRS or part.startswith(".") or _secret_name(part) for part in rel.parts):
            continue
        if pattern and not fnmatch.fnmatch(rel.as_posix(), pattern):
            continue
        if path.is_symlink():
            try:
                if not path.resolve(strict=True).is_relative_to(root):
                    continue
            except OSError:
                continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_dir():
            entries.append({"path": rel.as_posix() + "/", "kind": "dir"})
        else:
            entries.append({"path": rel.as_posix(), "kind": "file", "size": stat.st_size})
    return entries


async def _list_files(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    rel = call_string(call, "path") or "."
    try:
        base = await asyncio.to_thread(ctx.project_files.resolve, ctx.project_id, rel)
    except HTTPException as exc:
        return ToolOutcome(http_error_result(exc))
    if is_sensitive_path(str(base)):
        return ToolOutcome(denied_result(rel))
    if not base.is_dir():
        return ToolOutcome(error_result(f"{rel} is not a directory"))
    pattern = call_string(call, "pattern")
    recursive = call_bool(call, "recursive", True)
    limit = min(call_int(call, "limit", _MAX_LIST_ENTRIES), _MAX_LIST_ENTRIES)
    entries = await asyncio.to_thread(_list_entries, Path(ctx.cwd), base, pattern, recursive, limit)
    return ToolOutcome(
        {
            "ok": True,
            "path": rel,
            "entries": entries,
            "truncated": len(entries) >= limit,
        }
    )


async def _read_file(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    rel = call_string(call, "path")
    if not rel:
        return ToolOutcome(error_result("read_file requires 'path'"))
    try:
        resolved = await asyncio.to_thread(ctx.project_files.resolve, ctx.project_id, rel)
    except HTTPException as exc:
        return ToolOutcome(http_error_result(exc))
    if is_sensitive_path(str(resolved)):
        return ToolOutcome(denied_result(rel))
    try:
        document = await asyncio.to_thread(ctx.project_files.read, ctx.project_id, rel)
    except HTTPException as exc:
        return ToolOutcome(http_error_result(exc))
    if document.get("reason"):
        return ToolOutcome(
            {
                "ok": False,
                "path": rel,
                "revision": document.get("revision") or "",
                "output": document["reason"],
            }
        )
    text = document["content"]
    lines = text.splitlines(keepends=True)
    offset = max(call_int(call, "offset", 1), 1)
    limit = min(max(call_int(call, "limit", _DEFAULT_READ_LIMIT), 1), 2_000)
    max_chars = min(max(call_int(call, "max_chars", 50_000), 1), 200_000)
    sliced = "".join(lines[offset - 1 : offset - 1 + limit])[:max_chars]
    truncated = offset - 1 + limit < len(lines) or len("".join(lines[offset - 1 : offset - 1 + limit])) > max_chars
    return ToolOutcome(
        {
            "ok": True,
            "path": rel,
            "revision": document["revision"],
            "encoding": document.get("encoding"),
            "start_line": offset,
            "line_count": len(lines),
            "content": sliced,
            "truncated": truncated,
        }
    )


async def _write_file(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(denied_result(call_string(call, "path"), "Task is running in read-only mode"))
    rel = call_string(call, "path")
    content = call.arguments.get("content")
    if not rel or content is None:
        return ToolOutcome(error_result("write_file requires 'path' and 'content'"))
    expected = call.arguments.get("expected_revision")
    try:
        resolved = await asyncio.to_thread(
            ctx.project_files.resolve, ctx.project_id, rel, exists=False
        )
    except HTTPException as exc:
        return ToolOutcome(http_error_result(exc))
    if is_sensitive_path(str(resolved)):
        return ToolOutcome(denied_result(rel))
    if expected is not None:
        try:
            # Revision-checked save through ProjectFiles (atomic replace, mode preserved).
            saved = await asyncio.to_thread(
                ctx.project_files.save, ctx.project_id, rel, str(content), str(expected)
            )
        except HTTPException as exc:
            return ToolOutcome(http_error_result(exc))
        return ToolOutcome(
            {"ok": True, "path": rel, "revision": saved["revision"], "size": saved["size"]}
        )
    if resolved.exists():
        return ToolOutcome(
            error_result(
                f"{rel} already exists; pass its revision from read_file as 'expected_revision' to overwrite.",
                conflict=True,
            )
        )
    try:
        await asyncio.to_thread(_create_exclusive, resolved, str(content))
    except FileExistsError:
        return ToolOutcome(
            error_result(
                f"{rel} already exists; pass its revision from read_file as 'expected_revision' to overwrite.",
                conflict=True,
            )
        )
    except OSError as exc:
        return ToolOutcome(error_result(str(exc)))
    return ToolOutcome(
        {
            "ok": True,
            "path": rel,
            "revision": ctx.project_files.revision(resolved.read_bytes()),
            "size": resolved.stat().st_size,
            "created": True,
        }
    )


def _split_unified_diff(patch: str) -> list[tuple[str | None, str | None, list[str]]]:
    """Split a unified diff into per-file sections: (old_path, new_path, lines)."""
    files: list[tuple[str | None, str | None, list[str]]] = []
    current: list[str] | None = None
    old_path: str | None = None
    new_path: str | None = None
    for line in patch.splitlines():
        if line.startswith("--- "):
            if current is not None:
                files.append((old_path, new_path, current))
            current = [line]
            raw = line[4:].split("\t", 1)[0].strip()
            old_path = None if raw == "/dev/null" else (raw[2:] if raw.startswith("a/") else raw)
            new_path = None
        elif current is not None:
            current.append(line)
            if line.startswith("+++ "):
                raw = line[4:].split("\t", 1)[0].strip()
                if raw == "/dev/null":
                    new_path = None
                else:
                    new_path = raw[2:] if raw.startswith("b/") else raw
    if current is not None:
        files.append((old_path, new_path, current))
    return files


def _apply_hunks(original: list[str], lines: list[str]) -> tuple[list[str], int]:
    """Apply the hunks of one file section. Returns (new_lines, hunks_applied)."""
    output = list(original)
    shift = 0
    applied = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            i += 1
            continue
        meta = line[2 : line.index("@@", 2)].strip()
        old_part = meta.split(" ", 1)[0]
        old_start = int(old_part.split(",")[0].lstrip("-"))
        hunk: list[str] = []
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            if lines[i].startswith("\\"):
                i += 1
                continue
            hunk.append(lines[i])
            i += 1
        removed = [h[1:] for h in hunk if h[:1] in {" ", "-"}]
        added = [h[1:] for h in hunk if h[:1] in {" ", "+"}]
        hint = max(old_start - 1 + shift, 0)
        if not removed:
            found = min(hint, len(output))  # pure insertion at the stated line
        else:
            lo = max(0, hint - _FUZZ_WINDOW_LINES)
            hi = min(len(output) - len(removed), hint + _FUZZ_WINDOW_LINES)
            candidates = [
                pos for pos in range(lo, hi + 1)
                if output[pos : pos + len(removed)] == removed
            ]
            if not candidates:
                raise ValueError(f"hunk at line {old_start} does not apply")
            found = min(candidates, key=lambda p: abs(p - hint))
            if any(p != found and abs(p - hint) == abs(found - hint) for p in candidates):
                raise ValueError(f"hunk at line {old_start} is ambiguous — patch is stale")
        output[found : found + len(removed)] = added
        shift += len(added) - len(removed)
        applied += 1
    return output, applied


def _diff_summary(path: str, before: list[str], after: list[str]) -> dict[str, Any]:
    diff = list(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    plus = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    minus = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return {"path": path, "added": plus, "removed": minus, "preview": diff[:_PATCH_PREVIEW_LINES]}


def _trailing_newline(lines: list[str], existed: bool, had_nl: bool) -> bool:
    """Final-file trailing-newline status, honoring `\\ No newline at end of file`.

    The marker applies to the line immediately before it: after a '+' line the
    new content lacks the newline; after a '-' line the old side lacked it (so
    the new side keeps one); after context both sides lack it.
    """
    last_marker = -1
    for index, line in enumerate(lines):
        if line.startswith("\\"):
            last_marker = index
    if last_marker == -1:
        return had_nl if existed else True
    for index in range(last_marker - 1, -1, -1):
        line = lines[index]
        if line.startswith("+") and not line.startswith("+++"):
            return False
        if line.startswith("-") and not line.startswith("---"):
            return True
        if line.startswith(" "):
            return False
    return had_nl if existed else True


async def _apply_patch(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(denied_result("", "Task is running in read-only mode"))
    patch = call_string(call, "patch")
    if not patch.strip():
        return ToolOutcome(error_result("apply_patch requires 'patch' (unified diff text)"))
    if len(patch.encode("utf-8")) > 1_000_000:
        return ToolOutcome(error_result("Patch exceeds the 1 MB limit"))
    dry_run = call_bool(call, "dry_run", False)
    try:
        sections = _split_unified_diff(patch)
    except Exception as exc:
        return ToolOutcome(error_result(f"invalid patch: {exc}"))
    if not sections:
        return ToolOutcome(error_result("invalid patch: no file sections found"))
    changed: list[dict[str, Any]] = []
    writes: list[tuple[Path, list[str] | None, bool]] = []
    try:
        for old_path, new_path, lines in sections:
            target = new_path or old_path
            if not target:
                raise ValueError("patch section without a target path")
            resolved = ctx.project_files.resolve(ctx.project_id, target, exists=False)
            if is_sensitive_path(str(resolved)):
                return ToolOutcome(denied_result(target))
            source_path = old_path or target
            source_resolved = ctx.project_files.resolve(ctx.project_id, source_path, exists=False)
            if is_sensitive_path(str(source_resolved)):
                return ToolOutcome(denied_result(source_path))
            before: list[str] = []
            had_nl = True
            newline = "\n"
            existed = source_resolved.exists()
            if existed:
                raw = source_resolved.read_bytes()
                source_text = raw.decode("utf-8", errors="replace")
                had_nl = source_text.endswith("\n") or not source_text
                newline = "\r\n" if b"\r\n" in raw else "\n"
                before = source_text.splitlines()
            elif old_path:
                raise ValueError(f"{source_path} does not exist")
            after, hunks = _apply_hunks(before, lines)
            if hunks == 0:
                raise ValueError(f"{target}: patch section contains no hunks")
            deleted = new_path is None
            renamed = old_path is not None and new_path is not None and old_path != new_path
            final_nl = _trailing_newline(lines, existed, had_nl)
            if not dry_run:
                if deleted:
                    writes.append((source_resolved, None, final_nl, newline))
                else:
                    writes.append((resolved, after, final_nl, newline))
                    if renamed:
                        writes.append((source_resolved, None, had_nl, newline))
            changed.append(
                {
                    **_diff_summary(target, before, after),
                    "hunks": hunks,
                    "created": not resolved.exists() and old_path is None,
                    "deleted": deleted,
                    "renamed_from": old_path if renamed else None,
                }
            )
    except (ValueError, HTTPException, OSError) as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        return ToolOutcome(
            error_result(f"patch failed: {detail}", changed_paths=[c["path"] for c in changed])
        )
    if dry_run:
        return ToolOutcome(
            {"ok": True, "dry_run": True, "changed_paths": [c["path"] for c in changed], "changes": changed}
        )
    write_error = _commit_patch_writes(writes)
    if write_error is not None:
        return ToolOutcome(
            error_result(f"patch failed: {write_error}", changed_paths=[c["path"] for c in changed])
        )
    return ToolOutcome(
        {
            "ok": True,
            "dry_run": False,
            "changed_paths": [c["path"] for c in changed],
            "changes": changed,
        }
    )


def _commit_patch_writes(
    writes: list[tuple[Path, list[str] | None, bool, str]],
) -> str | None:
    """Apply writes/deletes atomically-ish: temp-file each write first, then
    commit with os.replace and roll back completed replacements on failure.
    ``after`` of None deletes ``resolved``; ``final_nl`` controls whether the
    written content ends with a newline; ``newline`` preserves the source
    file's line-ending convention."""
    prepared: list[tuple[Path, Path | None]] = []
    applied: list[tuple[Path, Path | None]] = []
    try:
        for resolved, after, final_nl, newline in writes:
            if after is None:
                prepared.append((resolved, None))
                continue
            resolved.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=resolved.parent, prefix=".termx-patch-")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(newline.join(after) + (newline if final_nl and after else ""))
            tmp = Path(tmp_name)
            if resolved.exists():
                os.chmod(tmp, stat.S_IMODE(resolved.stat().st_mode))
            prepared.append((resolved, tmp))
        for resolved, tmp in prepared:
            backup = None
            if resolved.exists():
                fd, backup_name = tempfile.mkstemp(dir=resolved.parent, prefix=".termx-bak-")
                os.close(fd)
                backup = Path(backup_name)
                os.replace(resolved, backup)
            # Registered before the replace so a failed replace still rolls
            # the original back into place.
            applied.append((resolved, backup))
            if tmp is not None:
                os.replace(tmp, resolved)
    except OSError as exc:
        for resolved, backup in reversed(applied):
            try:
                if backup is not None:
                    os.replace(backup, resolved)
                else:
                    resolved.unlink(missing_ok=True)
            except OSError:
                pass
        return str(exc)
    finally:
        for _resolved, tmp in prepared:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    for _resolved, backup in applied:
        if backup is not None:
            backup.unlink(missing_ok=True)
    return None


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="list_files",
            description="List project files under a path (project-relative). Prefer this over `ls`/`find`.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project-relative directory; default is project root."},
                    "pattern": {"type": "string", "description": "Optional glob filter applied to relative paths."},
                    "recursive": {"type": "boolean", "description": "Recurse into subdirectories (default true)."},
                    "limit": {"type": "integer", "description": f"Maximum entries (cap {_MAX_LIST_ENTRIES})."},
                },
                "additionalProperties": False,
            },
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_list_files,
            decide=decide_never,
        )
    )
    registry.register(
        ToolSpec(
            name="read_file",
            description="Read a project file with line pagination. Prefer this over `cat`/`sed`/`head`.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project-relative file path."},
                    "offset": {"type": "integer", "description": "1-based starting line (default 1)."},
                    "limit": {"type": "integer", "description": f"Maximum lines to return (default {_DEFAULT_READ_LIMIT})."},
                    "max_chars": {"type": "integer", "description": "Maximum characters returned (default 50000)."},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_read_file,
            decide=decide_never,
        )
    )
    registry.register(
        ToolSpec(
            name="write_file",
            description=(
                "Create a project file, or overwrite one when 'expected_revision' from read_file "
                "is supplied (the write then fails if the file changed on the host)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project-relative file path."},
                    "content": {"type": "string", "description": "Complete new file contents."},
                    "expected_revision": {
                        "type": "string",
                        "description": "Revision from read_file; required to overwrite an existing file.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            mutability="write",
            parallel_safe=False,
            approval="policy",
            execute=_write_file,
            decide=decide_never,
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="apply_patch",
            description="Apply a unified diff to project files (preferred edit tool). Set dry_run to validate first.",
            parameters={
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "Unified diff text covering one or more files."},
                    "dry_run": {"type": "boolean", "description": "Validate without writing (default false)."},
                },
                "required": ["patch"],
                "additionalProperties": False,
            },
            mutability="write",
            parallel_safe=False,
            approval="policy",
            execute=_apply_patch,
            decide=decide_never,
            expose_read_only=False,
        )
    )
