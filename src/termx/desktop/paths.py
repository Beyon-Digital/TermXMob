from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def host_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    return machine


def bundle_root() -> Path | None:
    """Root of the PyInstaller bundle when frozen, else None."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
    return None


def helpers_bin_dir() -> Path:
    bundled = bundle_root()
    if bundled is not None:
        candidate = bundled / "helpers" / "macos" / "bin"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[3] / "helpers" / "macos" / "bin"


def _ensure_executable(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode
        if not mode & stat.S_IXUSR:
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def _matches_host(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name != "posix":
        return True
    try:
        magic = path.read_bytes()[:4]
    except OSError:
        return False
    if magic in {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }:
        return sys.platform == "darwin"
    try:
        info = subprocess.check_output(["lipo", "-info", str(path)], text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError):
        return True
    return host_arch() in info


def resolve_macos_helper(name: str, env_var: str | None = None) -> str | None:
    env_name = env_var or f"TERMX_{name.upper().replace('-', '_')}_BIN"
    candidates: list[Path | None] = []
    env = os.environ.get(env_name)
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_file():
            _ensure_executable(candidate)
            return str(candidate.resolve())
        found = shutil.which(env)
        if found:
            return found
    arch = host_arch()
    bin_dir = helpers_bin_dir()
    repo_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            Path(found) if (found := shutil.which(f"{name}-{arch}")) else None,
            Path(found) if (found := shutil.which(name)) else None,
            bin_dir / f"{name}-{arch}",
            bin_dir / name,
            repo_root / "helpers" / "macos" / "TermxCapture" / ".build" / "release" / name,
            repo_root / "helpers" / "macos" / "TermxVirtualDisplay" / ".build" / "release" / name,
        ]
    )
    for candidate in candidates:
        if candidate is None:
            continue
        if _matches_host(candidate):
            _ensure_executable(candidate)
            return str(candidate.resolve())
    return None
