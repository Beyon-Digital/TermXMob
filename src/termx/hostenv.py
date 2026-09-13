from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepend(candidates: list[str]) -> None:
    existing = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    fresh: list[str] = []
    for candidate in candidates:
        if not candidate or candidate in existing or candidate in fresh:
            continue
        if os.path.isdir(candidate):
            fresh.append(candidate)
    if fresh:
        os.environ["PATH"] = os.pathsep.join(fresh + existing)


def _darwin_candidates() -> list[str]:
    out: list[str] = []
    paths_file = Path("/etc/paths")
    try:
        out.extend(
            line.strip()
            for line in paths_file.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.startswith("#")
        )
    except OSError:
        pass
    paths_dir = Path("/etc/paths.d")
    try:
        for entry in sorted(paths_dir.iterdir()):
            if not entry.is_file():
                continue
            try:
                out.extend(
                    line.strip()
                    for line in entry.read_text(encoding="utf-8", errors="replace").splitlines()
                    if line.strip()
                )
            except OSError:
                continue
    except (OSError, PermissionError):
        pass
    out.extend(
        [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/opt/local/bin",
            "/opt/local/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
        ]
    )
    return out


def _windows_candidates() -> list[str]:
    out: list[str] = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData", "LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        value = os.environ.get(key)
        if not value:
            continue
        if key.startswith("ProgramFiles"):
            out.extend(
                [
                    str(Path(value) / "cloudflared"),
                    str(Path(value) / "Cloudflare" / "cloudflared"),
                ]
            )
        elif key == "ProgramData":
            out.append(str(Path(value) / "chocolatey" / "bin"))
        elif key == "LOCALAPPDATA":
            out.extend(
                [
                    str(Path(value) / "Microsoft" / "WinGet" / "Links"),
                    str(Path(value) / "Programs" / "cloudflared"),
                ]
            )
        elif key == "USERPROFILE":
            out.extend(
                [
                    str(Path(value) / "scoop" / "shims"),
                    str(Path(value) / "go" / "bin"),
                ]
            )
    out.extend([r"C:\Program Files\Tailscale", r"C:\Program Files (x86)\Tailscale"])
    return out


def augment_path() -> None:
    """Restore a usable PATH when launched from Finder/Explorer instead of a terminal."""
    if os.name == "nt":
        _prepend(_windows_candidates())
    elif sys.platform == "darwin":
        _prepend(_darwin_candidates())
