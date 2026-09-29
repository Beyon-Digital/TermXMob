"""Conservative policy fingerprinting.

A fingerprint is the deterministic id a remembered rule binds to. The design
goal is *never over-broad*: an approval for `python scripts/test.py` must not
authorize `python /tmp/other.py`, and compound shell is only ever matched
exactly. Slightly-more-asks is safe; silent cross-command escalation is not.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termx.agent.policy import redact
from termx.agent.policies.models import PolicyIntent

if TYPE_CHECKING:
    from termx.agent.providers import ProviderCall
    from termx.agent.tools.registry import ToolContext

_INTERPRETERS = frozenset(
    {
        "python", "python3", "python2", "pypy", "pypy3",
        "node", "nodejs", "deno", "bun", "bunx",
        "ruby", "perl", "php", "lua",
        "bash", "sh", "zsh", "dash", "ksh", "fish",
        "pwsh", "powershell", "julia", "r", "Rscript",
        "uv", "npx", "tsx", "ts-node",
    }
)
# Flags that make an interpreter run inline code / a module instead of a
# script file — these commands can only ever be matched exactly.
_INTERPRETER_INLINE_FLAGS = frozenset({"-c", "-e", "-m", "--eval", "-r"})
_SCRIPT_EXTS = frozenset(
    {
        ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts",
        ".rb", ".pl", ".php", ".lua", ".sh", ".bash", ".zsh", ".fish",
        ".ps1", ".jl", ".r",
    }
)
_COMPOUND = re.compile(r"(&&|\|\||;|\||>>?|<|`|\$\(|\(|\)|\{|\}|\n)")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _normalize_command(command: str) -> str:
    return " ".join(command.split())


def _is_compound(command: str) -> bool:
    return bool(_COMPOUND.search(command))


def _script_token(token: str, cwd: str) -> str | None:
    """Resolve an interpreter's script argument to a stable identity."""
    if token.startswith("-"):
        return None
    path = Path(token)
    if path.suffix.lower() not in _SCRIPT_EXTS:
        return None
    try:
        resolved = (Path(cwd) / path).resolve(strict=False) if not path.is_absolute() else path.resolve(strict=False)
        return str(resolved)
    except OSError:
        return token


def _fingerprint_simple(argv: list[str], cwd: str) -> tuple[str, str]:
    """(fingerprint, kind) for a single-token shell command."""
    while argv and _ENV_ASSIGN.match(argv[0]):
        argv = argv[1:]
    if not argv:
        return _digest("sh", "empty"), "exact"
    program = os.path.basename(argv[0]) or argv[0]
    args = argv[1:]
    if program in _INTERPRETERS:
        inline = any(arg in _INTERPRETER_INLINE_FLAGS for arg in args)
        script = next((s for a in args if (s := _script_token(a, cwd))), None)
        if not inline and script is not None:
            # Bind interpreter + resolved script path + remaining args:
            # `python scripts/test.py` never authorizes `python other.py`.
            rest = [a for a in args if _script_token(a, cwd) != script]
            return _digest("interp", program, script, *rest), "conservative"
        # inline code / REPL / flag-only invocations match the command exactly
        return _digest("sh", program, *args), "exact"
    # Everything else binds the full argv (env-assignments stripped, quoting
    # normalized): `rm -rf build/` never authorizes `rm -rf build/ other/`,
    # `docker run img1` never authorizes `docker run img2`, and flag changes
    # re-ask — slightly-more-asks is the safe side of remembered approvals.
    return _digest("cmd", program, *args), "conservative"


def fingerprint_command(command: str, cwd: str) -> tuple[str, str]:
    """Return the (fingerprint, kind) for a shell command string."""
    normalized = _normalize_command(command)
    if not normalized or _is_compound(normalized):
        return _digest("sh", normalized), "exact"
    try:
        argv = shlex.split(normalized, posix=True)
    except ValueError:
        return _digest("sh", normalized), "exact"
    if not argv:
        return _digest("sh", normalized), "exact"
    return _fingerprint_simple(argv, cwd)


def shell_intent(command: str, call: "ProviderCall", ctx: "ToolContext") -> PolicyIntent:
    fingerprint, kind = fingerprint_command(command, ctx.cwd)
    return PolicyIntent(
        action_type="tool",
        tool=call.name or "run_shell",
        fingerprint=fingerprint,
        fingerprint_kind=kind,
        display=redact(command),
        cwd=ctx.cwd,
        project_id=ctx.project_id,
        task_id=ctx.task_id,
        matcher={
            "kind": kind,
            "command": redact(_normalize_command(command)),
        },
        arguments_digest=_digest(redact(command)),
    )


def tool_intent(call: "ProviderCall", ctx: "ToolContext", *, fingerprint: str, display: str, matcher: dict[str, Any]) -> PolicyIntent:
    """Intent for non-shell tools whose identity is already structured."""
    return PolicyIntent(
        action_type="tool",
        tool=call.name or "",
        fingerprint=fingerprint,
        fingerprint_kind="conservative",
        display=display,
        cwd=ctx.cwd,
        project_id=ctx.project_id,
        task_id=ctx.task_id,
        matcher=matcher,
        arguments_digest=_digest(display),
    )


def runbook_fingerprint(runbook_id: str) -> str:
    """Runbooks match on their durable identity — the strongest matcher."""
    return _digest("runbook", runbook_id)


def tool_key(*parts: str) -> str:
    """Fingerprint for a structured (non-shell) tool action."""
    return _digest("tool", *parts)
