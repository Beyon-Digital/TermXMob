"""Context Engine v1: compact project snapshot plus bounded history.

The snapshot replaces the raw flat manifest as provider context: it keeps the
existing ``files``/``omitted`` keys (the provider prompt contract) and adds
git state, detected manifests, language histogram, likely check commands, and
recently modified files. Snapshots are cached per task and invalidated after
any mutating tool call so later turns see fresh state without re-walking the
tree every turn.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from termx import git_ops
from termx.agent.context.manifest import SKIP_DIRS, _gitignore, _ignored, _secret_name, workspace_manifest

DEFAULT_CONTEXT_LIMITS = {
    "max_context_estimate_tokens": 120_000,
    "max_tool_output_chars_per_turn": 50_000,
    "max_history_events": 160,
    "max_images_in_context": 4,
}

_RECENT_FILES = 12
_SNAPSHOT_DEPTH = 2
_SNAPSHOT_TTL_S = 30.0
# 1x1 transparent PNG — placeholder for screenshots dropped by the image budget
# (keeps computer_call_output schema-valid without shipping stale pixels).
_BLANK_IMAGE_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def project_snapshot(root: str) -> dict[str, Any]:
    """Build a compact snapshot of the project at ``root`` (blocking; run via to_thread)."""
    base = Path(root).resolve(strict=True)
    manifest = workspace_manifest(str(base))
    ignored = _gitignore(base)
    files = [str(item) for item in manifest.get("files") or []]
    snapshot: dict[str, Any] = {
        "kind": "project_snapshot",
        "root": str(base),
        "name": base.name,
        "files": manifest["files"],
        "omitted": manifest["omitted"],
        "truncated": manifest["truncated"],
        "manifests": _manifests(base),
        "languages": _languages(files),
        "commands": _commands(base),
        "tree": _tree(base, ignored),
        "recent": _recent(base, ignored),
        "git": _git(base),
    }
    return snapshot


def _manifests(base: Path) -> list[str]:
    candidates = [
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "requirements.txt",
        "uv.lock",
        "pnpm-lock.yaml",
        "package-lock.json",
        "Makefile",
    ]
    return [name for name in candidates if (base / name).is_file()]


def _languages(files: list[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for rel in files:
        suffix = Path(rel).suffix.lstrip(".").lower()
        if suffix:
            counts[suffix] += 1
    return dict(counts.most_common(5))


def _commands(base: Path) -> list[str]:
    commands: list[str] = []
    if (base / "pyproject.toml").is_file() or (base / "tests").is_dir():
        commands.append("uv run pytest -q" if (base / "uv.lock").is_file() else "pytest -q")
    package = base / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8", errors="replace")).get("scripts") or {}
        except ValueError:
            scripts = {}
        runner = "pnpm" if (base / "pnpm-lock.yaml").is_file() else "npm"
        for key in ("test", "lint", "typecheck", "build"):
            if key in scripts:
                commands.append(f"{runner} run {key}" if key != "test" else f"{runner} test")
    if (base / "Cargo.toml").is_file():
        commands.append("cargo test")
    if (base / "go.mod").is_file():
        commands.append("go test ./...")
    return commands


def _tree(base: Path, ignored: list[str]) -> list[str]:
    """Top-level layout: root entries plus their immediate children."""
    entries: list[str] = []
    try:
        for top in sorted(base.iterdir()):
            if top.name in SKIP_DIRS or top.name.startswith(".") or _secret_name(top.name) or _ignored(top.name, ignored):
                continue
            if top.is_dir():
                entries.append(top.name + "/")
                if len(entries) >= 60:
                    break
                for child in sorted(top.iterdir())[:20]:
                    rel_child = f"{top.name}/{child.name}"
                    if (
                        child.name.startswith(".")
                        or child.name in SKIP_DIRS
                        or _secret_name(child.name)
                        or _ignored(rel_child, ignored)
                    ):
                        continue
                    entries.append(f"{rel_child}{'/' if child.is_dir() else ''}")
            else:
                entries.append(top.name)
            if len(entries) >= 120:
                break
    except OSError:
        pass
    return entries[:120]


def _recent(base: Path, ignored: list[str]) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for current, dirnames, filenames in os.walk(base):
        current_path = Path(current)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".") and not _secret_name(name)
        ]
        depth = len(current_path.relative_to(base).parts)
        if depth >= 4:
            dirnames[:] = []
        for name in filenames:
            rel = str((current_path / name).relative_to(base))
            if (
                name.startswith(".")
                or any(_secret_name(part) for part in Path(rel).parts)
                or _ignored(rel, ignored)
            ):
                continue
            try:
                mtime = (current_path / name).stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, rel))
    candidates.sort(reverse=True)
    return [rel for _mtime, rel in candidates[:_RECENT_FILES]]


def _git(base: Path) -> dict[str, Any] | None:
    try:
        status = git_ops.status(str(base))
    except Exception:
        return None
    changes = status.get("files") or []
    return {
        "branch": status.get("branch"),
        "changed": len(changes),
        "staged": sum(1 for item in changes if item.get("staged")),
    }


class ContextEngine:
    """Per-task context: cached project snapshot plus history budgeting."""

    def __init__(self, root: str, limits: dict[str, Any] | None = None) -> None:
        self._root = root
        self._limits = {**DEFAULT_CONTEXT_LIMITS, **(limits or {})}
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_at = 0.0

    async def snapshot(self) -> dict[str, Any]:
        import asyncio
        import time

        if self._snapshot is None or time.monotonic() - self._snapshot_at > _SNAPSHOT_TTL_S:
            self._snapshot = await asyncio.to_thread(project_snapshot, self._root)
            self._snapshot_at = time.monotonic()
        return self._snapshot

    def note_mutation(self) -> None:
        self._snapshot = None
        self._snapshot_at = 0.0

    def slim_history(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bound transcript size without breaking call/output pairing.

        Keeps the leading user item, bounds the transcript to the newest
        ``max_history_events`` items (never severing a tool call from its
        output), applies ``max_tool_output_chars_per_turn`` as a cumulative
        newest-first budget across tool outputs, and keeps at most
        ``max_images_in_context`` image parts (newest kept).
        """
        char_cap = int(self._limits["max_tool_output_chars_per_turn"])
        image_cap = int(self._limits["max_images_in_context"])
        max_events = int(self._limits["max_history_events"])
        head: list[dict[str, Any]] = []
        if items and isinstance(items[0], dict) and items[0].get("role") == "user":
            head, items = items[:1], items[1:]
        if len(items) > max(0, max_events - len(head)):
            items = items[max(0, len(items) - max(0, max_events - len(head))):]
            while items and isinstance(items[0], dict) and items[0].get("type") == "function_call_output":
                items = items[1:]
        slimmed: list[dict[str, Any]] = []
        for item in head + list(items):
            if not isinstance(item, dict):
                slimmed.append(item)
                continue
            if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
                output = item["output"]
                if len(output) > char_cap:
                    item = {**item, "output": output[:char_cap] + "\n...[truncated by context engine]"}
            slimmed.append(item)
        # Newest outputs keep their budget; older ones collapse once the
        # cumulative allowance is spent.
        remaining = char_cap
        for index in range(len(slimmed) - 1, -1, -1):
            item = slimmed[index]
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call_output" or not isinstance(item.get("output"), str):
                continue
            output = item["output"]
            if len(output) <= remaining:
                remaining -= len(output)
                continue
            keep = max(0, remaining)
            remaining = 0
            marker = "\n...[elided by context budget]"
            slimmed[index] = {**item, "output": output[:keep] + marker if keep else marker.strip()}
        # Walk from newest to oldest to decide which image parts survive.
        # Covers both user `input_image` parts and native `computer_call_output`
        # screenshots (whose image lives in output.image_url).
        image_positions: list[tuple[int, int | None]] = []
        for index, item in enumerate(slimmed):
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, list):
                for part_index, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "input_image":
                        image_positions.append((index, part_index))
            if isinstance(item, dict) and item.get("type") == "computer_call_output":
                output = item.get("output")
                if isinstance(output, dict) and output.get("image_url"):
                    image_positions.append((index, None))
        drop = set(image_positions[:-image_cap]) if len(image_positions) > image_cap else set()
        for index, part_index in sorted(drop):
            item = slimmed[index]
            if part_index is None:
                slimmed[index] = {
                    **item,
                    "output": {**item["output"], "image_url": _BLANK_IMAGE_URL},
                }
                continue
            content = list(item["content"])
            content[part_index] = {"type": "input_text", "text": "[image omitted by context budget]"}
            slimmed[index] = {**item, "content": content}
        return slimmed
