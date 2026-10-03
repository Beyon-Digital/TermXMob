"""Extension catalog builder.

Walks trusted roots and produces ``ExtensionEntry`` records for skills,
agents, workflows, toolsets, MCP connections and ACP definitions. The index
is pure discovery: no process spawning, no network, no state mutation —
enable/trust overlays are applied by the caller from persisted state.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..frontmatter import split_frontmatter
from .roots import ScanRoot
from .safety import iter_entries, safe_read_text, slugify

# kind id prefix -> the subdir of a root that declares it
KIND_DIRS = {
    "skills": "skill",
    "agents": "agent",
    "workflows": "workflow",
    "toolsets": "toolset",
    "mcp": "mcp",
    "acp": "acp",
}


@dataclass
class ExtensionEntry:
    qid: str            # kind.slug@source  (unique, collision-suffixed)
    kind: str           # skill|agent|workflow|toolset|mcp|acp
    id: str             # kind.slug
    slug: str
    name: str
    description: str
    path: str
    source: str         # user|project:<id>|compat:<vendor>
    trusted: bool
    compat: bool = False
    enabled: bool = False
    authorized: bool = False
    digest: str = ""    # sha256 of the declaring file
    errors: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, include_body: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "qid": self.qid,
            "kind": self.kind,
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "source": self.source,
            "trusted": self.trusted,
            "compat": self.compat,
            "enabled": self.enabled,
            "authorized": self.authorized,
            "digest": self.digest,
            "errors": list(self.errors),
        }
        out.update(self.extra)
        return out


@dataclass
class ScanReport:
    scanned_roots: list[dict[str, Any]]
    skipped: list[dict[str, str]]        # {path, reason}
    collisions: list[dict[str, str]]     # {qid, kept, shadowed}
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned_roots": self.scanned_roots,
            "skipped": self.skipped,
            "collisions": self.collisions,
            "duration_ms": self.duration_ms,
        }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _meta_preview(text: str) -> tuple[dict[str, Any], str | None]:
    """Return (frontmatter-dict, error). Never raises."""
    try:
        meta, _ = split_frontmatter(text)
        return meta, None
    except Exception as exc:  # noqa: BLE001 - discovery reports, never fails
        return {}, f"frontmatter: {exc}"


class DiscoveryIndex:
    """Scans roots into a catalog; caller supplies enable/trust overlays."""

    def __init__(self, roots: list[ScanRoot]):
        self._roots = roots

    def scan(self) -> tuple[dict[str, ExtensionEntry], ScanReport]:
        started = time.monotonic()
        entries: dict[str, ExtensionEntry] = {}
        skipped: list[dict[str, str]] = []
        collisions: list[dict[str, str]] = []
        scanned: list[dict[str, Any]] = []
        for root in self._roots:
            count = 0
            if not Path(root.path).is_dir():
                scanned.append({**root.as_dict(), "entries": 0, "present": False})
                continue
            if root.compat:
                count = self._scan_compat_root(root, entries, skipped)
            else:
                for sub, kind in KIND_DIRS.items():
                    if sub not in root.kinds:
                        continue
                    count += self._scan_kind_dir(
                        root, Path(root.path) / sub, kind, entries, skipped
                    )
            scanned.append({**root.as_dict(), "entries": count, "present": True})
        # Collision handling: first (highest-precedence) root wins; later
        # entries with the same qid are reported, not merged.
        ordered: dict[str, ExtensionEntry] = {}
        for qid, entry in entries.items():
            if qid in ordered:
                collisions.append(
                    {"qid": qid, "kept": ordered[qid].path, "shadowed": entry.path}
                )
                continue
            ordered[qid] = entry
        report = ScanReport(
            scanned_roots=scanned,
            skipped=skipped,
            collisions=collisions,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return ordered, report

    # ------------------------------------------------------------------

    def _scan_kind_dir(
        self,
        root: ScanRoot,
        directory: Path,
        kind: str,
        entries: dict[str, ExtensionEntry],
        skipped: list[dict[str, str]],
    ) -> int:
        if not directory.is_dir():
            return 0
        count = 0
        if kind == "skill":
            # skills/<id>/SKILL.md
            for e in iter_entries(str(directory), max_depth=1):
                if e.skipped_reason:
                    skipped.append({"path": e.path, "reason": e.skipped_reason})
                    continue
                if not e.is_dir:
                    continue
                skill_file = Path(e.path) / "SKILL.md"
                if not skill_file.is_file() or skill_file.is_symlink():
                    continue
                ent = self._entry_from_markdown(
                    root, kind, skill_file, e.name, skipped
                )
                if ent:
                    entries[ent.qid] = ent
                    count += 1
            return count
        patterns = {
            "agent": ("*.agent.md", "*.md"),
            "workflow": ("*.md",),
            "toolset": ("*.json",),
            "mcp": ("*.json",),
            "acp": ("*.json",),
        }[kind]
        seen_files: set[str] = set()
        for e in iter_entries(str(directory), max_depth=0):
            if e.skipped_reason:
                skipped.append({"path": e.path, "reason": e.skipped_reason})
                continue
            if e.is_dir:
                continue
            if e.path in seen_files:
                continue
            if not any(e.name.endswith(p.lstrip("*")) for p in patterns):
                continue
            seen_files.add(e.path)
            stem = e.name
            for suffix in (".agent.md", ".md", ".json"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            if kind in {"toolset", "mcp", "acp"}:
                ent = self._entry_from_json(
                    root, kind, Path(e.path), stem, skipped
                )
            else:
                ent = self._entry_from_markdown(
                    root, kind, Path(e.path), stem, skipped
                )
            if ent:
                entries[ent.qid] = ent
                count += 1
        return count

    def _scan_compat_root(
        self,
        root: ScanRoot,
        entries: dict[str, ExtensionEntry],
        skipped: list[dict[str, str]],
    ) -> int:
        """Compat roots contribute metadata-only entries."""
        count = 0
        if "skills" in root.kinds:
            for e in iter_entries(root.path, max_depth=1):
                if e.skipped_reason or not e.is_dir:
                    continue
                skill_file = Path(e.path) / "SKILL.md"
                if not skill_file.is_file() or skill_file.is_symlink():
                    continue
                ent = self._entry_from_markdown(
                    root, "skill", skill_file, e.name, skipped
                )
                if ent:
                    ent.compat = True
                    entries[ent.qid] = ent
                    count += 1
        elif "agents" in root.kinds:
            for e in iter_entries(root.path, max_depth=0):
                if e.is_dir or e.skipped_reason:
                    continue
                if not (e.name.endswith(".agent.md") or e.name.endswith(".md")):
                    continue
                stem = e.name
                for suffix in (".agent.md", ".md"):
                    if stem.endswith(suffix):
                        stem = stem[: -len(suffix)]
                        break
                ent = self._entry_from_markdown(
                    root, "agent", Path(e.path), stem, skipped
                )
                if ent:
                    ent.compat = True
                    entries[ent.qid] = ent
                    count += 1
        else:
            # bare metadata root (e.g. .codex): record presence only
            for e in iter_entries(root.path, max_depth=1):
                if e.skipped_reason:
                    skipped.append({"path": e.path, "reason": e.skipped_reason})
                count += 0
        return count

    def _entry_from_markdown(
        self,
        root: ScanRoot,
        kind: str,
        path: Path,
        fallback_slug: str,
        skipped: list[dict[str, str]],
    ) -> ExtensionEntry | None:
        try:
            text = safe_read_text(str(path))
        except Exception as exc:  # noqa: BLE001 - discovery reports, never fails
            skipped.append({"path": str(path), "reason": str(exc)})
            return None
        meta, err = _meta_preview(text)
        errors: list[str] = [err] if err else []
        slug = slugify(str(meta.get("name") or fallback_slug))
        ent = ExtensionEntry(
            qid=f"{kind}.{slug}@{root.source}",
            kind=kind,
            id=f"{kind}.{slug}",
            slug=slug,
            name=str(meta.get("name") or slug),
            description=str(meta.get("description") or "")[:1024],
            path=str(path),
            source=root.source,
            trusted=root.trusted,
            compat=root.compat,
            digest=_sha256_text(text),
            errors=errors,
        )
        return ent

    def _entry_from_json(
        self,
        root: ScanRoot,
        kind: str,
        path: Path,
        fallback_slug: str,
        skipped: list[dict[str, str]],
    ) -> ExtensionEntry | None:
        try:
            text = safe_read_text(str(path))
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("definition must be a JSON object")
        except Exception as exc:  # noqa: BLE001
            skipped.append({"path": str(path), "reason": str(exc)})
            return None
        slug = slugify(str(data.get("name") or data.get("id") or fallback_slug))
        ent = ExtensionEntry(
            qid=f"{kind}.{slug}@{root.source}",
            kind=kind,
            id=f"{kind}.{slug}",
            slug=slug,
            name=str(data.get("name") or data.get("id") or slug),
            description=str(data.get("label") or data.get("description") or "")[:1024],
            path=str(path),
            source=root.source,
            trusted=root.trusted,
            compat=root.compat,
            digest=_sha256_text(text),
            errors=[] if data.get("schema") else ["missing schema field"],
        )
        return ent
