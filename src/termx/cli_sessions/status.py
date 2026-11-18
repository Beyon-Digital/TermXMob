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

_TOKEN = re.compile(
    r"\x1b\[(?P<args>[0-?]*)[ -/]*(?P<final>[@-~])"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[()][A-Za-z0-9]"  # charset
    r"|\x1b[@-Z\\-_]"  # two-byte escapes
    r"|(?P<ctl>[\r\n\x08\t])"
    r"|(?P<text>[^\x00-\x1f\x7f\x1b]+)"
)
_MAX_LINES = 400


def _numbers(args: str, default: int = 1) -> list[int]:
    out = []
    for part in args.lstrip("?>").split(";"):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return [n or default for n in out] or [default]


def clean_text(data: bytes | str, limit: int | None = None) -> str:
    """Terminal bytes as the plain text a person would see on the last lines of the screen.

    A small line-oriented terminal model: cursor moves and erases are honoured (Ink-style TUIs place every
    word with ``CSI n G``, so dropping the escapes would glue words together), colours and modes are ignored,
    and a clear-screen discards what came before it. Not a full emulator; good enough to read and match on.
    """
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    lines: list[str] = []
    row = col = top = 0

    def put(run: str) -> None:
        nonlocal col
        while len(lines) <= row:
            lines.append("")
        line = lines[row]
        if len(line) < col:
            line += " " * (col - len(line))
        lines[row] = line[:col] + run + line[col + len(run):]
        col += len(run)

    for match in _TOKEN.finditer(text):
        if match.group("text") is not None:
            put(match.group("text"))
        elif match.group("ctl") is not None:
            ctl = match.group("ctl")
            if ctl == "\r":
                col = 0
            elif ctl == "\n":
                row, col = row + 1, 0
            elif ctl == "\x08":
                col = max(0, col - 1)
            else:
                col = (col // 8 + 1) * 8
        elif match.group("final") is not None:
            final, args = match.group("final"), match.group("args")
            if args.startswith(("?", ">")):
                continue
            n = _numbers(args)[0]
            if final == "A":
                row = max(top, row - n)
            elif final == "B":
                row += n
            elif final == "C":
                col += n
            elif final == "D":
                col = max(0, col - n)
            elif final == "E":
                row, col = row + n, 0
            elif final == "F":
                row, col = max(top, row - n), 0
            elif final == "G":
                col = max(0, n - 1)
            elif final in "Hf":
                nums = _numbers(args)
                row, col = top + nums[0] - 1, max(0, (nums[1] if len(nums) > 1 else 1) - 1)
            elif final == "K":
                mode = _numbers(args, 0)[0] if args else 0
                while len(lines) <= row:
                    lines.append("")
                line = lines[row]
                lines[row] = line[:col] if mode == 0 else (" " * min(col + 1, len(line)) + line[col + 1:]) if mode == 1 else ""
            elif final == "J":
                mode = _numbers(args, 0)[0] if args else 0
                if mode in (2, 3):
                    lines.clear()
                    row = col = top = 0
                elif mode == 0:
                    del lines[row + 1:]
                    if row < len(lines):
                        lines[row] = lines[row][:col]
        if len(lines) > _MAX_LINES:
            drop = len(lines) - _MAX_LINES
            del lines[:drop]
            row, top = max(0, row - drop), max(0, top - drop)
    rendered = "\n".join(line.rstrip() for line in lines).rstrip("\n")
    return rendered[-limit:] if limit else rendered


@dataclass(frozen=True)
class PatternSet:
    """Best-effort markers on the output tail. The latest marker on screen wins."""

    approval: tuple[str, ...] = ()
    waiting: tuple[str, ...] = ()
    working: tuple[str, ...] = ()
    # Footers of choice menus: "waiting" only when nothing more specific (an approval dialog is also a menu).
    menu: tuple[str, ...] = ()
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
        if best is None and any(pattern.search(tail) for pattern in self.compiled("menu")):
            return "waiting"
        return best[2] if best else None


# Best-effort patterns. Wording drifts between versions; a miss degrades to the
# quiet-time heuristic, a false hit is bounded by "latest marker wins" and by only
# acting on a quiet screen.
PATTERNS: dict[str, PatternSet] = {
    "claude": PatternSet(
        approval=(
            r"Do you want to (?:proceed|make this edit|create|run)",
            r"\b1\.\s*Yes\b[\s\S]{0,200}\b(?:No|esc)\b",
            # The folder-trust dialog every fresh worktree meets (wording seen on 2.1.326, best-effort).
            r"Is this a project you created or one you trust",
            r"Yes, I trust this folder",
            r"Allow (?:once|always)",
        ),
        waiting=(r"\?\s+for shortcuts", r"^\s*>\s*$"),
        working=(r"esc to interrupt", r"…\s*\(\d+s"),
        menu=(r"Enter to confirm", r"Esc to cancel"),
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
