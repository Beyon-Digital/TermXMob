"""Bounded, symlink-safe filesystem helpers for discovery.

Discovery is metadata-only: it never follows symlinks, caps depth/file
count/bytes read, and surfaces problems as diagnostics instead of raising.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

MAX_DEPTH = 4
MAX_FILES_PER_ROOT = 2000
MAX_FILE_BYTES = 512 * 1024


class DirWalkError(RuntimeError):
    pass


@dataclass
class WalkEntry:
    path: str
    name: str
    is_dir: bool
    size: int
    depth: int
    skipped_reason: str | None = None


def iter_entries(
    root: str,
    *,
    max_depth: int = MAX_DEPTH,
    max_files: int = MAX_FILES_PER_ROOT,
) -> Iterator[WalkEntry]:
    """Yield entries under ``root`` without ever following symlinks.

    Symlinked directories/files are yielded once with
    ``skipped_reason="symlink"`` so the caller can report them, but never
    traversed or read.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return
    seen = 0
    stack: list[tuple[Path, int]] = [(root_path, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for child in children:
            try:
                is_link = child.is_symlink()
                is_dir = child.is_dir(follow_symlinks=False)
                st = child.stat(follow_symlinks=False)
            except OSError:
                continue
            yield WalkEntry(
                path=child.path,
                name=child.name,
                is_dir=is_dir,
                size=0 if is_dir else st.st_size,
                depth=depth,
                skipped_reason="symlink" if is_link else None,
            )
            if is_link:
                continue
            if is_dir:
                if depth + 1 <= max_depth:
                    stack.append((Path(child.path), depth + 1))
            else:
                seen += 1
                if seen > max_files:
                    return


def safe_read_text(path: str, *, max_bytes: int = MAX_FILE_BYTES) -> str:
    """Read a file's text refusing symlinks and oversized files.

    Uses ``O_NOFOLLOW`` where available so a symlink swapped in between the
    directory listing and the read still cannot redirect the read.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not os.path.isfile(path):  # pragma: no cover - fstat check below
            raise DirWalkError(f"not a regular file: {path}")
        if st.st_size > max_bytes:
            raise DirWalkError(f"file exceeds {max_bytes} bytes: {path}")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise DirWalkError(f"file exceeds {max_bytes} bytes: {path}")
        return data.decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def slugify(value: str) -> str:
    """Lowercase-hyphen slug safe for filenames and qualified ids."""
    out = []
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:64] or "unnamed"
