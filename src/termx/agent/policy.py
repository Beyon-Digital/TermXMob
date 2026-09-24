from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    approval_required: bool
    reason: str
    consequence: str


_CONSEQUENTIAL: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"(^|[;&|]\s*)(sudo|doas)\b", re.I), "Privilege elevation", "Runs with elevated privileges"),
    (re.compile(r"\b(rm|rmdir)\b[^\n]*(?:-[^\s]*r|--recursive|\s/|\s~)", re.I), "Recursive deletion", "Can permanently delete files"),
    (re.compile(r"\b(git\s+push|npm\s+publish|pnpm\s+publish|twine\s+upload)\b", re.I), "External publication", "Sends changes to an external service"),
    (re.compile(r"\b(curl|wget)\b[^\n]*(?:--data|-d\s|--upload-file|-T\s)", re.I), "Data transmission", "Uploads data outside this machine"),
    (re.compile(r"\b(chmod|chown)\b[^\n]*(?:777|root|/)", re.I), "Permission change", "Changes security or ownership settings"),
    (re.compile(r"\b(passwd|security|secret-tool|ssh-add)\b", re.I), "Credential access", "Reads or changes credentials"),
    (re.compile(r"\b(git\s+reset\s+--hard|git\s+clean\s+-[^\n]*f)\b", re.I), "Destructive repository change", "Discards local work"),
)

_SECRET = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{16,}|(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s]+|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_SENSITIVE_PATH = re.compile(
    r"(?:^|[\s/\\])(?:\.env(?:\.[^/\\\s]+)?|id_(?:rsa|ed25519)[^/\\\s]*|credentials\.json|[^/\\\s]+\.(?:pem|key))(?:$|\s)",
    re.IGNORECASE,
)

# Commands that change files, state, or reach the network. Ask mode rejects these
# outright so a read-only conversation cannot mutate the project or exfiltrate data.
_MUTATING = re.compile(
    r"(?:^|[\s;&|(])(?:"
    r"rm|rmdir|mv|cp|dd|truncate|tee|touch|mkdir|ln|install|"
    r"chmod|chown|chgrp|"
    r"git\s+(?:commit|push|reset|checkout|merge|rebase|clean|add|rm|apply|stash|restore)|"
    r"npm\s+(?:install|i|publish|update|uninstall)|pnpm\s+(?:install|add|publish|remove|update)|yarn\s+(?:add|install|publish)|"
    r"pip\s+install|pipx\s+install|uv\s+(?:add|pip)|cargo\s+(?:install|add|publish)|"
    r"apt|apt-get|brew|dnf|yum|pacman|"
    r"curl|wget|scp|rsync|ftp|nc|ssh|kill|pkill|systemctl|launchctl|"
    r"sudo|doas"
    r")\b",
    re.IGNORECASE,
)
# Explicit redirection or in-place edit that writes to disk.
_WRITE_REDIRECT = re.compile(r"(?<![>0-9])>>?(?![>])|\bsed\b[^\n]*-i|\bperl\b[^\n]*-i")


def is_sensitive_path(path: str) -> bool:
    """Return True when a path names a credential, key, or environment file."""
    return bool(_SENSITIVE_PATH.search(f"{path} "))


def is_mutating_shell(command: str) -> bool:
    """Return True when a command could change files, state, or reach the network."""
    return bool(_MUTATING.search(command) or _WRITE_REDIRECT.search(command))


def redact(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


def evaluate_shell(command: str, root: str) -> PolicyDecision:
    for pattern, reason, consequence in _CONSEQUENTIAL:
        if pattern.search(command):
            return PolicyDecision(True, True, reason, consequence)
    if _SENSITIVE_PATH.search(f"{command} "):
        return PolicyDecision(
            True,
            True,
            "Sensitive file access",
            "May read a credential or environment file and expose its contents to the model",
        )
    if re.search(r"(?:^|[\s;&|])(?:cd\s+)?\.\.(?:[/\\]|\s|$)", command):
        return PolicyDecision(
            True,
            True,
            "Outside selected project",
            "Uses parent-directory traversal outside the approved folder",
        )
    outside = _outside_paths(command, Path(root).resolve())
    if outside:
        sample = outside[0]
        return PolicyDecision(
            True,
            True,
            "Outside selected project",
            f"May access {sample}, which is outside the approved folder",
        )
    return PolicyDecision(True, False, "Within approved task", "Runs on the paired host")


def evaluate_computer(actions: list[dict[str, Any]]) -> PolicyDecision:
    typed = " ".join(str(action.get("text") or "") for action in actions if action.get("type") == "type")
    if _SECRET.search(typed):
        return PolicyDecision(
            True,
            True,
            "Sensitive text entry",
            "Types information that resembles a credential or secret into another application",
        )
    if any(action.get("type") == "keypress" and "ENTER" in str(action.get("keys", "")).upper() for action in actions) and typed:
        return PolicyDecision(
            True,
            True,
            "Form submission",
            "Submits text through the remote desktop",
        )
    return PolicyDecision(True, False, "Within approved task", "Controls the selected desktop")


def _outside_paths(command: str, root: Path) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    outside: list[str] = []
    for token in tokens:
        candidate = token.strip("'\"")
        if not candidate.startswith(("/", "~")):
            continue
        # System executable and pseudo paths used as commands are not file targets.
        if candidate.startswith(("/usr/bin/", "/bin/", "/opt/homebrew/bin/", "/dev/null")):
            continue
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, ValueError):
            outside.append(str(path))
    return outside
