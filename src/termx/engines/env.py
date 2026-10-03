"""Engine executable resolution + GUI-launch env diagnostics.

Fixes the "works in terminal, not in TermX" gap: GUI launches (Finder,
Electron shell) carry a stripped PATH missing user dirs like ~/.local/bin —
where `devin`, `grok`, `claude` install. Resolution checks PATH *plus* known
install dirs explicitly, so a stripped PATH never hides an installed engine.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
from pathlib import Path

# Candidate dirs appended for engine lookup regardless of PATH. Order matters:
# explicit user dirs first (newest installs), then system package dirs.
def _user_dirs() -> list[Path]:
    home = Path.home()
    out = [
        home / ".local" / "bin",
        home / ".local" / "share" / "mise" / "shims",
        home / ".volta" / "bin",
        home / ".cargo" / "bin",
        home / "bin",
    ]
    if sys.platform == "darwin":
        out += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    elif sys.platform.startswith("linux"):
        out += [Path("/usr/local/bin"), home / ".local" / "share" / "mise" / "bin"]
    return out


def engine_search_path(env: dict[str, str] | None = None) -> list[str]:
    """PATH entries plus known engine dirs that exist — no duplicates."""
    env = env if env is not None else os.environ
    seen: set[str] = set()
    out: list[str] = []
    for entry in env.get("PATH", "").split(os.pathsep):
        if entry and entry not in seen:
            seen.add(entry)
            out.append(entry)
    for candidate in _user_dirs():
        s = str(candidate)
        if s not in seen and candidate.is_dir():
            seen.add(s)
            out.append(s)
    return out


def resolve_executable(
    name: str,
    *,
    env: dict[str, str] | None = None,
    override: str | None = None,
) -> str | None:
    """Absolute path to `name` or None. `override` = explicit config path."""
    if override:
        p = Path(override).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        return None
    found = shutil.which(name, path=os.pathsep.join(engine_search_path(env)))
    if found:
        return str(Path(found).resolve())
    return None


async def probe_version(executable: str, args: list[str], timeout: float = 8.0) -> str | None:
    """Run `<exe> <args>` bounded; first non-empty line of output, truncated."""
    try:
        proc = await asyncio.create_subprocess_exec(
            executable, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return None
    for line in out.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return None


def is_executable_file(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and bool(st.st_mode & 0o111)


def env_diff_report(child_env: dict[str, str]) -> dict[str, object]:
    """Redacted comparison of parent env vs the env a child would get."""
    parent_path = os.environ.get("PATH", "")
    return {
        "parent_path_entries": len([e for e in parent_path.split(os.pathsep) if e]),
        "effective_search_path": engine_search_path(child_env),
        "home_set": bool(child_env.get("HOME")),
        "user_dirs_present": [str(d) for d in _user_dirs() if d.is_dir()],
    }
