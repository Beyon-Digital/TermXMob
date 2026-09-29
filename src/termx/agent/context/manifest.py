from __future__ import annotations

import fnmatch
import os
from pathlib import Path

SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".venv", "venv", "dist", "build", "target", "__pycache__"})
SECRET_NAMES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "credentials.json",
    "secrets.*",
)


def workspace_manifest(root: str, *, max_files: int = 500, max_depth: int = 8) -> dict[str, object]:
    base = Path(root).resolve(strict=True)
    ignored = _gitignore(base)
    files: list[str] = []
    omitted = 0
    for current, dirnames, filenames in os.walk(base):
        current_path = Path(current)
        depth = len(current_path.relative_to(base).parts)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS and not _ignored(str((current_path / name).relative_to(base)), ignored)
        ]
        if depth >= max_depth:
            omitted += len(dirnames)
            dirnames[:] = []
        for name in sorted(filenames):
            rel = str((current_path / name).relative_to(base))
            if _ignored(rel, ignored) or _secret_name(name):
                omitted += 1
                continue
            try:
                if (current_path / name).stat().st_size > 1_000_000:
                    omitted += 1
                    continue
            except OSError:
                continue
            if len(files) >= max_files:
                omitted += 1
                continue
            files.append(rel)
    return {"root": str(base), "files": files, "omitted": omitted, "truncated": omitted > 0}


def _gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line.strip().lstrip("/") for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern) for pattern in patterns)


def _secret_name(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in SECRET_NAMES)
