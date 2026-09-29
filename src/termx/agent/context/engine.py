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
from termx.agent.context.manifest import SKIP_DIRS, workspace_manifest

DEFAULT_CONTEXT_LIMITS = {
    "max_context_estimate_tokens": 120_000,
    "max_tool_output_chars_per_turn": 50_000,
    "max_history_events": 160,
    "max_images_in_context": 4,
}

_RECENT_FILES = 12
_SNAPSHOT_DEPTH = 2


def project_snapshot(root: str) -> dict[str, Any]:
    """Build a compact snapshot of the project at ``root`` (blocking; run via to_thread)."""
    base = Path(root).resolve(strict=True)
    manifest = workspace_manifest(str(base))
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
        "tree": _tree(base),
        "recent": _recent(base),
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


def _tree(base: Path) -> list[str]:
    """Top-level layout: root entries plus their immediate children."""
    entries: list[str] = []
    try:
        for top in sorted(base.iterdir()):
            if top.name in SKIP_DIRS or top.name.startswith("."):
                continue
            if top.is_dir():
                entries.append(top.name + "/")
                if len(entries) >= 60:
                    break
                for child in sorted(top.iterdir())[:20]:
                    if child.name.startswith(".") or child.name in SKIP_DIRS:
                        continue
                    entries.append(f"{top.name}/{child.name}{'/' if child.is_dir() else ''}")
            else:
                entries.append(top.name)
            if len(entries) >= 120:
                break
    except OSError:
        pass
    return entries[:120]


def _recent(base: Path) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for current, dirnames, filenames in os.walk(base):
        current_path = Path(current)
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".")]
        depth = len(current_path.relative_to(base).parts)
        if depth >= 4:
            dirnames[:] = []
        for name in filenames:
            if name.startswith("."):
                continue
            try:
                mtime = (current_path / name).stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, str((current_path / name).relative_to(base))))
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

    async def snapshot(self) -> dict[str, Any]:
        import asyncio

        if self._snapshot is None:
            self._snapshot = await asyncio.to_thread(project_snapshot, self._root)
        return self._snapshot

    def note_mutation(self) -> None:
        self._snapshot = None

    def slim_history(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bound transcript size without breaking call/output pairing.

        Caps each tool-output string at ``max_tool_output_chars_per_turn`` and
        keeps at most ``max_images_in_context`` image parts (newest kept).
        """
        char_cap = int(self._limits["max_tool_output_chars_per_turn"])
        image_cap = int(self._limits["max_images_in_context"])
        slimmed: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                slimmed.append(item)
                continue
            if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
                output = item["output"]
                if len(output) > char_cap:
                    item = {**item, "output": output[:char_cap] + "\n...[truncated by context engine]"}
            slimmed.append(item)
        # Walk from newest to oldest to decide which image parts survive.
        image_positions: list[tuple[int, int]] = []
        for index, item in enumerate(slimmed):
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, list):
                for part_index, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "input_image":
                        image_positions.append((index, part_index))
        drop = set(image_positions[:-image_cap]) if len(image_positions) > image_cap else set()
        for index, part_index in sorted(drop):
            item = slimmed[index]
            content = list(item["content"])
            content[part_index] = {"type": "input_text", "text": "[image omitted by context budget]"}
            slimmed[index] = {**item, "content": content}
        return slimmed
