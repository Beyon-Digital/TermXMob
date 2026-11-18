"""CLI-session status machine.

Statuses::

    starting        launched, nothing seen yet
    working         the CLI is producing output / running a turn
    waiting_input   at its prompt or asking a question: the next move is a message
    needs_approval  blocked on a permission decision
    idle            quiet with no recognised prompt (unknown, not necessarily ready)
    done | failed   the process ended (clean / non-zero or killed)
    lost            the host restarted and the process is gone

Each change carries a ``source``: ``hook`` (the CLI told us, see ``hooks.py``) or
``heuristic`` (derived from the PTY: how long it has been quiet plus per-CLI prompt
patterns on the output tail). Hook-driven CLIs (Claude Code) hand lifecycle decisions
to the hooks and the heuristics only escalate to ``needs_approval`` when a quiet screen
shows an approval dialog the hooks did not report. The pattern tables below are
*best-effort*: they are plain data, tested against transcripts authored in the tests,
and never claim to be a stable contract of any CLI's rendering.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STARTING = "starting"
WORKING = "working"
WAITING_INPUT = "waiting_input"
NEEDS_APPROVAL = "needs_approval"
IDLE = "idle"
DONE = "done"
FAILED = "failed"
LOST = "lost"

STATUSES = (STARTING, WORKING, WAITING_INPUT, NEEDS_APPROVAL, IDLE, DONE, FAILED, LOST)
ENDED = frozenset({DONE, FAILED, LOST})
ATTENTION = frozenset({WAITING_INPUT, NEEDS_APPROVAL})
SOURCES = ("hook", "heuristic")

_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[()][A-Za-z0-9]"  # charset
    r"|\x1b[@-Z\\-_]"  # two-byte escapes
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(data: bytes | str, limit: int | None = None) -> str:
    """Terminal bytes as plain text: no escape sequences, redraw returns become line breaks."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    text = _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    return text[-limit:] if limit else text


@dataclass(frozen=True)
class PatternSet:
    """Best-effort markers on the output tail. The latest marker on screen wins."""

    approval: tuple[str, ...] = ()
    waiting: tuple[str, ...] = ()
    working: tuple[str, ...] = ()
    _compiled: dict[str, tuple[re.Pattern[str], ...]] = field(default_factory=dict, compare=False, repr=False)

    def compiled(self, kind: str) -> tuple[re.Pattern[str], ...]:
        if kind not in self._compiled:
            self._compiled[kind] = tuple(re.compile(p, re.I | re.M) for p in getattr(self, kind))
        return self._compiled[kind]

    def classify(self, tail: str) -> str | None:
        """'approval' | 'waiting' | 'working' | None, by the marker nearest the end of ``tail``."""
        best: tuple[int, int, str] | None = None
        # On a tie (same end offset) approval outranks waiting outranks working.
        for rank, kind, name in ((3, "approval", "approval"), (2, "waiting", "waiting"), (1, "working", "working")):
            for pattern in self.compiled(kind):
                last = None
                for last in pattern.finditer(tail):
                    pass
                if last is not None and (best is None or (last.end(), rank) > (best[0], best[1])):
                    best = (last.end(), rank, name)
        return best[2] if best else None


# Best-effort patterns. Wording drifts between versions; a miss degrades to the
# quiet-time heuristic, a false hit is bounded by "latest marker wins" and by only
# acting on a quiet screen.
PATTERNS: dict[str, PatternSet] = {
    "claude": PatternSet(
        approval=(
            r"Do you want to (?:proceed|make this edit|create|run)",
            r"\b1\.\s*Yes\b[\s\S]{0,200}\b(?:No|esc)\b",
            r"Do you trust the files in this folder",
            r"Allow (?:once|always)",
        ),
        waiting=(r"\?\s+for shortcuts", r"Enter to confirm", r"^\s*>\s*$"),
        working=(r"esc to interrupt", r"…\s*\(\d+s"),
    ),
    "codex": PatternSet(
        approval=(
            r"Would you like to run the following command",
            r"Yes, proceed",
            r"Allow Codex to",
            r"No, and tell Codex what to do differently",
        ),
        waiting=(r"\?\s+for shortcuts", r"^\s*›\s*", r"send message"),
        working=(r"esc to interrupt", r"Working\s*\("),
    ),
    "gemini": PatternSet(
        approval=(r"Allow execution of", r"Waiting for user confirmation", r"\b1\.\s*Allow once"),
        waiting=(r"Type your message or @path", r"^\s*>\s*$"),
        working=(r"esc to cancel",),
    ),
    "aider": PatternSet(
        approval=(r"\(Y\)es/\(N\)o", r"\[Y/n\]", r"\(Y/n\)", r"\[y/N\]"),
        waiting=(r"^\s*(?:\S+\s)?>\s*$",),
        working=(),
    ),
}
GENERIC = PatternSet(
    approval=(r"\[[Yy]/[Nn]\]", r"\([Yy]/[Nn]\)", r"Press Enter to continue"),
    waiting=(),
    working=(),
)


def patterns_for(cli: str) -> PatternSet:
    return PATTERNS.get(cli, GENERIC)


@dataclass
class StatusChange:
    status: str
    source: str
    detail: str


class StatusTracker:
    """Pure state machine: feed it hook events and periodic PTY observations."""

    def __init__(
        self,
        cli: str,
        *,
        lifecycle_hooks: bool = False,
        quiet_after: float = 1.2,
        idle_after: float = 8.0,
        sustain: float = 1.5,
        patterns: PatternSet | None = None,
    ) -> None:
        self.cli = cli
        self.patterns = patterns or patterns_for(cli)
        self.lifecycle_hooks = lifecycle_hooks
        self.quiet_after = quiet_after
        self.idle_after = idle_after
        self.sustain = sustain
        self.status = STARTING
        self.source = "heuristic"
        self.detail = "launched"
        self.hook_seen = False
        self._active_since: float | None = None

    # -- transitions -------------------------------------------------------

    def _set(self, status: str, source: str, detail: str) -> StatusChange | None:
        # A new detail alone (the same status from the same source) is recorded but not an event.
        same = (status, source) == (self.status, self.source)
        self.status, self.source, self.detail = status, source, detail
        return None if same else StatusChange(status, source, detail)

    def on_hook(self, status: str | None, detail: str) -> StatusChange | None:
        """A validated hook event. ``status`` None means "seen, no state implied"."""
        if self.status in ENDED:
            return None
        self.hook_seen = True
        if status is None:
            return None
        return self._set(status, "hook", detail)

    def observe(
        self,
        *,
        now: float,
        quiet: float | None,
        tail: str,
        exited: bool = False,
        exit_code: int | None = None,
        stop_requested: bool = False,
    ) -> StatusChange | None:
        if self.status in ENDED:
            return None
        if exited:
            if stop_requested or exit_code == 0:
                return self._set(DONE, "heuristic", "stopped" if stop_requested else "exited 0")
            return self._set(FAILED, "heuristic", f"exited {exit_code}" if exit_code is not None else "terminated")
        talking = quiet is not None and quiet < self.quiet_after
        if talking:
            if self._active_since is None:
                self._active_since = now
        else:
            self._active_since = None
        kind = self.patterns.classify(tail)

        if self.lifecycle_hooks and self.hook_seen:
            # Hooks own working/waiting. A quiet screen showing an approval dialog the hooks
            # missed still escalates; a hook-set approval clears itself on sustained output.
            if not talking and kind == "approval" and self.status in (WORKING, WAITING_INPUT, IDLE):
                return self._set(NEEDS_APPROVAL, "heuristic", "approval prompt on screen")
            if (
                self.status == NEEDS_APPROVAL
                and talking
                and self._active_since is not None
                and now - self._active_since >= self.sustain
                and kind != "approval"
            ):
                return self._set(WORKING, "heuristic", "output resumed")
            return None

        if talking:
            sustained = self._active_since is not None and now - self._active_since >= self.sustain
            if kind == "approval":
                # The dialog is still being drawn; settle on the quiet branch below.
                return None
            if sustained or self.status == STARTING or kind == "working":
                return self._set(WORKING, "heuristic", "producing output")
            return None
        if kind == "approval":
            return self._set(NEEDS_APPROVAL, "heuristic", "approval prompt on screen")
        if kind == "waiting":
            # A hook that already said "turn complete" keeps its attribution.
            if self.status == WAITING_INPUT and self.source == "hook":
                return None
            return self._set(WAITING_INPUT, "heuristic", "input prompt on screen")
        if kind == "working":
            return self._set(WORKING, "heuristic", "busy indicator on screen")
        if quiet is not None and quiet >= self.idle_after and self.status != IDLE:
            if self.status == WAITING_INPUT and self.source == "hook":
                return None
            return self._set(IDLE, "heuristic", f"quiet for {int(quiet)}s")
        return None

    def input_sent(self, now: float) -> StatusChange | None:
        """A message was submitted: the CLI is about to work, so do not report a stale prompt."""
        if self.status in ENDED or self.status == STARTING:
            return None
        self._active_since = now
        return self._set(WORKING, "heuristic", "input sent")

    def lost(self, detail: str = "host restarted") -> StatusChange | None:
        if self.status in ENDED:
            return None
        return self._set(LOST, "heuristic", detail)
