"""Per-session status hooks for CLIs that can report their own state.

A hook is a tiny forwarder the CLI runs on its lifecycle events. It posts the event JSON
to ``POST /api/cli-sessions/hook/{token}`` on the loopback interface. The token is
random, scoped to one session, revoked when the session ends and never persisted or
logged. A payload is *untrusted input*: it is size-capped, parsed against a closed set
of event names, reduced to a status hint plus a short sanitised note, and can do
nothing except move that one session's status.

Formats (checked where the CLI is installed):

* Claude Code: ``--settings <file>`` with ``hooks.<Event>[].hooks[] = {type: command}``;
  the event JSON arrives on stdin with ``hook_event_name`` (and, for ``Notification``,
  ``notification_type``). Event names were confirmed in the installed 2.1.326 binary.
* Codex: ``-c notify=[program, args...]``; Codex appends one JSON argument
  ``{"type": "agent-turn-complete", "thread-id", "turn-id", "cwd", ...}`` (the legacy
  notify channel, present in 0.169). It reports finished turns only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from termx.cli_sessions import status as S

MAX_HOOK_BYTES = 16_384
HOOK_ENV = "TERMX_CLI_HOOK_URL"
TOKEN_PREFIX = "clihook_"
# At most this many hook posts per token in the window; the rest are dropped.
RATE_LIMIT = (120, 10.0)

CLAUDE_EVENTS: dict[str, str | None] = {
    # Ready at its prompt; with an argv prompt UserPromptSubmit follows within moments and takes over.
    "SessionStart": S.WAITING_INPUT,
    "UserPromptSubmit": S.WORKING,
    "PostToolUse": S.WORKING,
    "PermissionRequest": S.NEEDS_APPROVAL,
    "Notification": None,  # decided by notification_type
    "Stop": S.WAITING_INPUT,
    "StopFailure": S.WAITING_INPUT,
    "SubagentStop": None,
    "SessionEnd": None,
}
CLAUDE_NOTIFICATIONS: dict[str, str | None] = {
    "permission_prompt": S.NEEDS_APPROVAL,
    "idle_prompt": S.WAITING_INPUT,
    "elicitation_dialog": S.WAITING_INPUT,
    "auth_success": None,
}
# Events the generated settings subscribe to (PostToolUse is how "working again" is seen after an approval).
CLAUDE_SUBSCRIBED = (
    "SessionStart", "UserPromptSubmit", "PostToolUse", "PermissionRequest", "Notification", "Stop", "StopFailure", "SessionEnd",
)
CODEX_EVENTS: dict[str, str | None] = {"agent-turn-complete": S.WAITING_INPUT}

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class HookRejected(ValueError):
    """The payload is not an acceptable hook event."""


@dataclass(frozen=True)
class HookEvent:
    name: str
    status: str | None
    note: str = ""

    @property
    def detail(self) -> str:
        return f"hook:{self.name}" + (f" ({self.note})" if self.note else "")


def _note(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub(" ", value).strip()[:200]


def parse_hook_payload(cli: str, body: bytes | dict) -> HookEvent:
    """Validate an untrusted hook body. Raises HookRejected; never raises anything else."""
    if isinstance(body, (bytes, bytearray)):
        if len(body) > MAX_HOOK_BYTES:
            raise HookRejected("hook payload too large")
        try:
            data = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HookRejected("hook payload is not JSON") from None
    else:
        data = body
    if not isinstance(data, dict):
        raise HookRejected("hook payload must be an object")
    if cli == "claude":
        name = data.get("hook_event_name")
        if not isinstance(name, str) or not _NAME.match(name) or name not in CLAUDE_EVENTS:
            raise HookRejected("unknown hook event")
        status = CLAUDE_EVENTS[name]
        note = ""
        if name == "Notification":
            kind = data.get("notification_type")
            if isinstance(kind, str) and kind in CLAUDE_NOTIFICATIONS:
                status = CLAUDE_NOTIFICATIONS[kind]
                note = kind
            else:
                note = "unrecognised notification"
        return HookEvent(name, status, note)
    if cli == "codex":
        name = data.get("type")
        if not isinstance(name, str) or not _NAME.match(name) or name not in CODEX_EVENTS:
            raise HookRejected("unknown hook event")
        return HookEvent(name, CODEX_EVENTS[name])
    raise HookRejected("this CLI does not send hooks")


@dataclass
class _Grant:
    session_id: str
    cli: str
    created_at: float
    stamps: deque


class HookTokens:
    """Random per-session capability tokens (mirrors McpGateway.mint): minted at launch,
    revoked at exit, held only in memory as digests, compared in constant time."""

    def __init__(self) -> None:
        self._grants: dict[str, _Grant] = {}

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def mint(self, session_id: str, cli: str) -> str:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        self._grants[self._digest(token)] = _Grant(session_id, cli, time.time(), deque())
        return token

    def revoke_session(self, session_id: str) -> int:
        doomed = [key for key, grant in self._grants.items() if grant.session_id == session_id]
        for key in doomed:
            del self._grants[key]
        return len(doomed)

    def resolve(self, token: str) -> tuple[str, str] | None:
        """(session id, cli) for a live token, else None. Rate-limited per token."""
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
            return None
        key = self._digest(token)
        grant = None
        for stored, candidate in self._grants.items():
            if hmac.compare_digest(stored, key):
                grant = candidate
        if grant is None:
            return None
        now = time.monotonic()
        limit, window = RATE_LIMIT
        while grant.stamps and now - grant.stamps[0] > window:
            grant.stamps.popleft()
        if len(grant.stamps) >= limit:
            return None
        grant.stamps.append(now)
        return grant.session_id, grant.cli

    def active(self) -> int:
        return len(self._grants)


FORWARDER_SOURCE = '''\
"""TermX CLI-session hook forwarder: posts a minimal status hint to the host and always exits 0.

The CLI's event JSON can carry prompts, tool inputs and replies; only the event-name fields are forwarded.
"""
import json
import os
import sys
import urllib.request

KEEP = ("hook_event_name", "notification_type", "type")


def main():
    url = os.environ.get("TERMX_CLI_HOOK_URL")
    if not url:
        return
    last = sys.argv[-1] if len(sys.argv) > 1 else ""
    if last.startswith("{"):
        raw = last
    else:
        try:
            raw = sys.stdin.buffer.read(8_000_000).decode("utf-8", "replace")
        except Exception:
            return
    try:
        event = json.loads(raw)
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    body = json.dumps({key: event[key] for key in KEEP if isinstance(event.get(key), str) and len(event[key]) < 100}).encode()
    try:
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=3).read(1)
    except Exception:
        pass


try:
    main()
except Exception:
    pass
'''


def write_forwarder(directory: Path) -> Path:
    """Install (or refresh) the forwarder script in a private directory."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        directory.chmod(0o700)
    path = directory / "hook_forward.py"
    if not path.exists() or path.read_text(encoding="utf-8") != FORWARDER_SOURCE:
        path.write_text(FORWARDER_SOURCE, encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)
    return path


def forwarder_command(forwarder: Path, python: str | None = None) -> str:
    python = python or sys.executable
    if os.name == "nt":
        return subprocess.list2cmdline([python, str(forwarder)])
    return shlex.join([python, str(forwarder)])


def claude_settings(command: str) -> dict:
    """``--settings`` payload: forwarder hooks for the subscribed lifecycle events."""
    return {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command, "timeout": 5}]}] for event in CLAUDE_SUBSCRIBED
        }
    }


def write_claude_settings(directory: Path, command: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        directory.chmod(0o700)
    path = directory / "claude-settings.json"
    path.write_text(json.dumps(claude_settings(command)), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def codex_notify_args(forwarder: Path, python: str | None = None) -> tuple[str, ...]:
    """``-c notify=[...]`` global override (TOML array of strings; JSON string quoting is valid TOML)."""
    argv = [python or sys.executable, str(forwarder)]
    return ("-c", "notify=" + json.dumps(argv))
