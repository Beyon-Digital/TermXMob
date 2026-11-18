"""Interactive coding-CLI sessions: real TUIs in PTYs, tracked as first-class sessions.

A CLI session is a ``SessionManager`` terminal (so the existing ``/api/sessions/{id}/pty``
websocket streams the TUI) plus a durable ``cli_session`` workspace record carrying its
status, worktree and exit. The service owns launch (typed argv only), the status monitor,
steering (send / interrupt / stop with a SIGINT -> SIGTERM -> SIGKILL ladder) and the
restart rule: when the host starts again, a record whose terminal the PTY holder kept
alive is re-adopted (``recover``); any other record still live is marked ``lost`` and,
if the CLI can resume its saved conversation, can be resumed into a new terminal.

Authorization is the caller's job (REST and the Agent tools each decide who may start,
see or steer what); this module trusts ``owner`` and enforces isolation by it.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from termx.audit import log_event
from termx.cli_sessions import catalogue as C
from termx.cli_sessions import hooks as H
from termx.cli_sessions import status as S
from termx.config import config_dir
from termx.engines.env import resolve_executable
from termx.terminals import TerminalError

KIND = "cli_session"
HOST_OWNER = "host"  # compatibility credentials have no managed principal
MAX_SEND_CHARS = 16_000
MAX_READ_CHARS = 20_000
DEFAULT_READ_CHARS = 4_000
TAIL_BYTES = 48_000
FINAL_TAIL_CHARS = 8_000  # the persisted, redacted last screen of a finished or lost session
CLI_COLS, CLI_ROWS = 120, 32
LIST_LIMIT = 200
_PASTE_START, _PASTE_END = b"\x1b[200~", b"\x1b[201~"
_HOOK_TOKEN = re.compile(r"clihook_[A-Za-z0-9_-]{8,}")


class CliSessionError(Exception):
    """A refusal with the HTTP status it maps to."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class LiveCli:
    id: str
    owner: str
    cli: str
    pty_id: str
    body: dict[str, Any]
    tracker: S.StatusTracker
    spec: C.CliSpec
    state_dir: Path | None = None
    stop_requested: bool = False
    closed: bool = False
    proc_gone_at: float | None = None
    task: asyncio.Task | None = None
    typed_prompt: str | None = None
    finishing: bool = False
    last_tail: str = ""
    background: set = field(default_factory=set)


def _clean_input(text: str) -> str:
    """Typed text only: line breaks and tabs survive, every other control byte is dropped."""
    return "".join(ch for ch in text.replace("\r\n", "\n").replace("\r", "\n") if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))


class CliSessionService:
    def __init__(
        self,
        state: Any,
        *,
        directory: Path | None = None,
        tick: float = 0.4,
        grace: tuple[float, float] = (2.0, 3.0),
        executables: dict[str, str] | None = None,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        self.state = state
        self.directory = Path(directory) if directory else config_dir() / "cli-sessions"
        self.tick = tick
        self.grace = grace
        self._executables = executables  # tests / config: cli id -> absolute path
        self.thresholds = dict(thresholds or {})  # StatusTracker quiet_after / idle_after / sustain
        self.hooks = H.HookTokens()
        self._live: dict[str, LiveCli] = {}
        self._tails: dict[str, str] = {}
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._closed = False

    # -- plumbing ----------------------------------------------------------

    @property
    def store(self):
        return self.state.workspace.store

    @property
    def manager(self):
        return self.state.sessions

    def add_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def _emit(self, kind: str, row: dict[str, Any]) -> None:
        public = self.public(row)
        for listener in list(self._listeners):
            try:
                listener(kind, public)
            except Exception:  # noqa: BLE001 - a listener must never break a session
                continue

    def hook_url(self, token: str) -> str:
        base = getattr(self.state, "cli_hook_base", None) or f"http://127.0.0.1:{getattr(self.state, 'port', 8787)}"
        return f"{base}/api/cli-sessions/hook/{token}"

    def executable(self, cli: str) -> str | None:
        if self._executables is not None:
            return self._executables.get(cli)
        spec = C.spec_for(cli)
        return resolve_executable(spec.executable)

    @staticmethod
    def public(row: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "id", "cli", "label", "title", "status", "status_source", "status_detail", "status_updated_at", "project_id",
            "cwd", "isolation", "worktree", "pty_session_id", "sandbox_profile", "mode", "started_at", "ended_at",
            "exit_code", "ended_reason", "native_session_id", "resumable", "prompt_chars", "prompt_digest",
            "resumed_from", "resumed_to",
        )
        out = {key: row.get(key) for key in keys}
        out["updated_at"] = row.get("updated_at")
        out["attention"] = row.get("status") in S.ATTENTION
        return out

    # -- records -----------------------------------------------------------

    def _persist(self, live: LiveCli) -> dict[str, Any]:
        live.body.update(
            status=live.tracker.status,
            status_source=live.tracker.source,
            status_detail=live.tracker.detail,
            status_updated_at=time.time(),
        )
        saved = self.store.update(KIND, live.id, live.body)
        live.body = {k: v for k, v in saved.items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}
        return saved

    def row(self, owner: str, session_id: str) -> dict[str, Any]:
        row = self.store.get(KIND, session_id)
        if row is None or row["owner"] != owner:
            raise CliSessionError(404, "CLI session not found")
        return row

    def get(self, owner: str, session_id: str) -> dict[str, Any]:
        return self.public(self.row(owner, session_id))

    def list(self, owner: str) -> list[dict[str, Any]]:
        return [self.public(row) for row in self.store.list(KIND, owner=owner)[:LIST_LIMIT]]

    def rows(self, owner: str) -> list[dict[str, Any]]:
        return self.store.list(KIND, owner=owner)[:LIST_LIMIT]

    def reconcile(self) -> int:
        """Whatever is not live in this process after a restart is gone: mark those records ``lost``.

        A lost session keeps ``resumable`` (set at launch from the CLI's catalogue entry) so it can be
        resumed into a new terminal, and its last persisted screen stays readable.
        """
        lost = 0
        for row in self.store.list(KIND):
            if row.get("status") in S.ENDED or row["id"] in self._live:
                continue
            body = {k: v for k, v in row.items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}
            body.update(status=S.LOST, status_source="heuristic", status_detail="host restarted",
                        status_updated_at=time.time(), ended_at=time.time(), ended_reason="lost")
            saved = self.store.update(KIND, row["id"], body)
            self._emit("cli.session.exited", saved)
            lost += 1
        return lost

    async def recover(self) -> dict[str, int]:
        """Host start: re-adopt sessions whose terminal the PTY holder kept alive, mark the rest ``lost``.

        Status is re-derived from the replayed output tail (hooks take over again at the next event: the
        hook token digest was persisted, the token itself never was). A held process that exited while
        the host was down is finished with its real exit code and final screen.
        """
        adopted = finished = 0
        try:
            self.manager.reattach()
        except Exception:  # noqa: BLE001 - a holder problem must not stop the host from starting
            log_event("cli_session_reattach_error")
        for row in self.store.list(KIND):
            if row.get("status") in S.ENDED or row["id"] in self._live:
                continue
            session = self.manager.get(row.get("pty_session_id") or "")
            if session is None or not getattr(session, "durable", False):
                continue
            try:
                spec = C.spec_for(row["cli"])
            except C.CatalogueError:
                continue
            if await self._adopt(row, spec, session):
                adopted += 1
            else:
                finished += 1
        lost = self.reconcile()
        if adopted or finished or lost:
            log_event("cli_sessions_recovered", adopted=adopted, finished=finished, lost=lost)
        return {"adopted": adopted, "finished": finished, "lost": lost}

    async def _adopt(self, row: dict[str, Any], spec: C.CliSpec, session) -> bool:
        """True when the session is live again; False when it had already exited and was finished."""
        session_id = row["id"]
        digest = row.get("hook_digest")
        restored = bool(spec.hooks and isinstance(digest, str) and self.hooks.restore(session_id, spec.id, digest))
        state_dir = self.directory / session_id if (self.directory / session_id).is_dir() else None
        tracker = S.StatusTracker(spec.id, lifecycle_hooks=spec.hooks == "claude-settings" and restored, **self.thresholds)
        tracker.detail = "recovered after host restart"
        body = {k: v for k, v in row.items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}
        live = LiveCli(session_id, row["owner"], spec.id, session.id, body, tracker, spec, state_dir)
        self._live[session_id] = live
        if session.exited:
            exit_code = session.exit_code
            tracker.observe(now=time.monotonic(), quiet=None, tail="", exited=True, exit_code=exit_code)
            await self._finish(live, exit_code, "exited")
            return False
        self._emit("cli.session.status", self._persist(live))
        live.task = asyncio.get_running_loop().create_task(self._watch(live), name=f"cli-session-{session_id[:8]}")
        log_event("cli_session_adopted", session_id=session_id, cli=spec.id)
        return True

    # -- launch ------------------------------------------------------------

    async def start(
        self,
        *,
        owner: str,
        cli: str,
        cwd: str,
        project_id: str | None = None,
        prompt: str | None = None,
        mode: str = "default",
        model: str | None = None,
        isolation: str = "none",
        sandbox_profile: str = "host",
        confirm_bypass: bool = False,
        title: str | None = None,
        claim: Callable[[str], None] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Launch one CLI. ``session_id`` lets a caller (the task graph) record its intent under the id before the
        launch, so a crash in between never leads to a second launch; it must be 32 lowercase hex characters."""
        if self._closed:
            raise CliSessionError(503, "The host is shutting down")
        if session_id is not None and not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise CliSessionError(400, "Invalid session id")
        if session_id is not None and self.store.get(KIND, session_id) is not None:
            raise CliSessionError(409, "A CLI session with this id already exists")
        try:
            spec = C.spec_for(cli)
            prompt = C.validate_prompt(prompt)
        except C.CatalogueError as exc:
            raise CliSessionError(400, str(exc)) from None
        if isolation not in ("none", "worktree"):
            raise CliSessionError(400, "Choose isolation none or worktree")
        if sandbox_profile not in ("host", "workspace"):
            raise CliSessionError(400, "sandbox_profile must be host or workspace")
        executable = self.executable(spec.id)
        if not executable:
            raise CliSessionError(409, f"{spec.label} is not installed on this host")
        allow_bypass = mode == "bypass" and sandbox_profile != "host" and confirm_bypass
        if mode == "bypass" and not allow_bypass:
            raise CliSessionError(
                403, "Permission-bypass mode is refused unless the session runs in a restricted sandbox profile and a human confirmed it"
            )
        session_id = session_id or uuid.uuid4().hex
        working_dir, worktree = await self._prepare_cwd(project_id, cwd, isolation, session_id)
        try:
            session, plan, state_dir, token = self._open_terminal(
                spec=spec, session_id=session_id, executable=executable, working_dir=working_dir,
                sandbox_profile=sandbox_profile, title=title, claim=claim, allow_bypass=allow_bypass,
                build=lambda extra: C.build_launch(
                    spec.id, executable=executable, working_dir=working_dir, prompt=prompt, mode=mode, model=model,
                    extra_args=extra, allow_bypass=allow_bypass,
                ),
            )
        except BaseException:
            if worktree is not None:
                await self._drop_worktree(project_id, cwd, worktree)
            raise
        now = time.time()
        body = {
            "cli": spec.id,
            "label": spec.label,
            "title": session.title,
            "pty_session_id": session.id,
            "cwd": working_dir,
            "source_cwd": cwd,
            "isolation": isolation,
            "worktree": worktree,
            "sandbox_profile": sandbox_profile,
            "mode": mode,
            "started_at": now,
            "ended_at": None,
            "exit_code": None,
            "ended_reason": None,
            "native_session_id": plan.native_session_id,
            "resumable": bool(spec.resume_args or spec.continue_args),
            "prompt_chars": len(prompt) if prompt else 0,
            # Enough to correlate a prompt with a report; the text itself is not stored.
            "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest()[:16] if prompt else None,
            "argv_display": plan.display[:300],
            "status": S.STARTING,
            "status_source": "heuristic",
            "status_detail": "launched",
            "status_updated_at": now,
            # SHA-256 of the 256-bit hook token, so a CLI that outlives a host restart can keep reporting.
            "hook_digest": self.hooks.digest_of(session_id) if token else None,
        }
        saved = self.store.create(KIND, owner, body, project_id, session_id)
        tracker = S.StatusTracker(
            spec.id, lifecycle_hooks=spec.hooks == "claude-settings" and token is not None, **self.thresholds
        )
        live = LiveCli(session_id, owner, spec.id, session.id, {k: v for k, v in saved.items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}},
                       tracker, spec, state_dir, typed_prompt=plan.typed_prompt)
        self._live[session_id] = live
        live.task = asyncio.get_running_loop().create_task(self._watch(live), name=f"cli-session-{session_id[:8]}")
        if plan.typed_prompt:
            self._spawn(live, self._deliver_prompt(live))
        log_event("cli_session_start", session_id=session_id, cli=spec.id, isolation=isolation, mode=mode)
        self._emit("cli.session.created", saved)
        return self.public(saved)

    def _open_terminal(
        self, *, spec: C.CliSpec, session_id: str, executable: str, working_dir: str, sandbox_profile: str,
        title: str | None, claim: Callable[[str], None] | None, allow_bypass: bool,
        build: Callable[[tuple[str, ...]], C.LaunchPlan],
    ):
        """Env scrubbing, hook wiring, the typed argv and the terminal itself (shared by start and resume).

        Returns ``(session, plan, state_dir, token)``. On any failure nothing is left behind: no hook token,
        no settings file, no terminal.
        """
        state_dir: Path | None = None
        extra: tuple[str, ...] = ()
        env: dict[str, str] | None = None
        token = None
        try:
            if sandbox_profile == "host":
                env = C.scrub_environment(spec, dict(os.environ))
                if spec.hooks:
                    token = self.hooks.mint(session_id, spec.id)
                    state_dir = self.directory / session_id
                    forwarder = H.write_forwarder(self.directory)
                    command = H.forwarder_command(forwarder)
                    if spec.hooks == "claude-settings":
                        extra = ("--settings", str(H.write_claude_settings(state_dir, command)))
                    elif spec.hooks == "codex-notify":
                        extra = H.codex_notify_args(forwarder)
                    env[H.HOOK_ENV] = self.hook_url(token)
            try:
                plan = build(extra)
                plan.check(allow_bypass=allow_bypass)
            except C.BypassRefused as exc:
                raise CliSessionError(403, str(exc)) from None
            except C.CatalogueError as exc:
                raise CliSessionError(400, str(exc)) from None
            try:
                session = self.manager.create(
                    cols=CLI_COLS, rows=CLI_ROWS, title=title or f"{spec.label} · {Path(working_dir).name or working_dir}",
                    argv=plan.argv, cwd=working_dir, sandbox_profile=sandbox_profile, env=env,
                )
            except TerminalError as exc:
                raise CliSessionError(503, str(exc)) from None
            except (OSError, ValueError) as exc:
                raise CliSessionError(503, f"Could not start {spec.label}: {exc.__class__.__name__}") from None
            if claim is not None:
                try:
                    claim(session.id)
                except BaseException:
                    self.manager.kill(session.id)
                    raise
        except BaseException:
            self._cleanup_files(state_dir)
            if token:
                self.hooks.revoke_session(session_id)
            raise
        return session, plan, state_dir, token

    async def _prepare_cwd(self, project_id, cwd, isolation, session_id):
        if isolation != "worktree":
            return cwd, None
        from termx.agent import worktrees

        if not project_id:
            raise CliSessionError(400, "Worktree isolation needs a project")
        delivery = getattr(self.state, "delivery", None)
        if delivery is None:
            raise CliSessionError(503, "The worktree service is unavailable")
        if not await asyncio.to_thread(worktrees.git_root, cwd):
            raise CliSessionError(400, "Worktree isolation needs a Git repository")
        try:
            made = await asyncio.to_thread(
                delivery.effect, project_id, cwd, "worktree-create", {"branch": "termx/cli-" + session_id[:12], "base": "HEAD"}
            )
        except Exception as exc:  # noqa: BLE001 - HTTPException or git failure
            detail = getattr(exc, "detail", None) or str(exc)
            raise CliSessionError(409, f"Could not create the worktree: {str(detail)[:200]}") from None
        return made["path"], {"id": made["id"], "path": made["path"], "branch": made["branch"]}

    async def _drop_worktree(self, project_id, root, worktree) -> None:
        """A launch that failed after its checkout was made leaves no orphan (a clean checkout only)."""
        try:
            await asyncio.to_thread(
                self.state.delivery.effect, project_id, root, "worktree-remove", {"worktree_id": worktree["id"]}
            )
        except Exception:  # noqa: BLE001 - best effort; a dirty or missing checkout stays for the owner
            pass

    def _spawn(self, live: LiveCli, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        live.background.add(task)
        task.add_done_callback(live.background.discard)

    async def _deliver_prompt(self, live: LiveCli) -> None:
        """CLIs with no argv prompt get it typed once the TUI has drawn and gone quiet."""
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and live.id in self._live:
            session = self.manager.get(live.pty_id)
            if session is None or session.exited:
                return
            quiet = session.quiet_seconds()
            if quiet is not None and quiet >= 1.5:
                break
            await asyncio.sleep(0.25)
        text, live.typed_prompt = live.typed_prompt, None
        if text and live.id in self._live:
            try:
                await self._write(live, text, True)
            except CliSessionError:
                return

    # -- monitor -----------------------------------------------------------

    async def _watch(self, live: LiveCli) -> None:
        try:
            while True:
                await asyncio.sleep(self.tick)
                if self._closed:
                    return
                session = self.manager.get(live.pty_id)
                exited = session is None or session.exited
                exit_code = session.exit_code if session is not None else None
                if session is not None and not exited:
                    proc = session.proc
                    poll = getattr(proc, "poll", None)
                    code = poll() if callable(poll) else None
                    if code is not None:
                        live.proc_gone_at = live.proc_gone_at or time.monotonic()
                        if time.monotonic() - live.proc_gone_at > 1.0:
                            exited, exit_code = True, code
                if session is None:
                    live.closed = True
                tail = S.clean_text(session.output_tail(4096), 3000) if session is not None else live.last_tail
                live.last_tail = tail
                change = live.tracker.observe(
                    now=time.monotonic(),
                    quiet=session.quiet_seconds() if session is not None else None,
                    tail=tail,
                    exited=exited,
                    exit_code=exit_code,
                    stop_requested=live.stop_requested or live.closed,
                )
                if change is not None:
                    if live.tracker.status in S.ENDED:
                        # One write carries the end state, exit code and time together.
                        await self._finish(live, exit_code, "stopped" if live.stop_requested else "closed" if live.closed else "exited")
                        return
                    self._emit("cli.session.status", self._persist(live))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a monitor crash must not leave a session forever "working"
            log_event("cli_session_monitor_error", session_id=live.id)
            live.tracker.status = S.FAILED
            live.tracker.source, live.tracker.detail = "heuristic", "monitor error"
            await self._finish(live, None, "monitor error")

    async def _finish(self, live: LiveCli, exit_code: int | None, reason: str) -> None:
        if live.finishing:
            return
        live.finishing = True
        session = self.manager.get(live.pty_id)
        if session is not None:
            self._tails[live.id] = S.clean_text(session.output_tail(TAIL_BYTES))
            if exit_code is None:
                exit_code = session.exit_code
            await asyncio.to_thread(self.manager.kill, live.pty_id, 1.5)
        else:
            self._tails[live.id] = live.last_tail
        while len(self._tails) > 50:
            self._tails.pop(next(iter(self._tails)))
        self.hooks.revoke_session(live.id)
        self._cleanup_files(live.state_dir)
        live.body.update(ended_at=time.time(), exit_code=exit_code, ended_reason=reason, hook_digest=None,
                         final_tail=self._persistable_tail(self._tails.get(live.id, "")))
        saved = self._persist(live)
        self._live.pop(live.id, None)
        for task in list(live.background):
            task.cancel()
        log_event("cli_session_end", session_id=live.id, status=live.tracker.status, exit_code=exit_code)
        self._emit("cli.session.exited", saved)

    @staticmethod
    def _persistable_tail(text: str) -> str:
        """The last screen as it is stored: cleaned, redacted and bounded. Never raw terminal bytes."""
        from termx.agent.policy import redact

        return _HOOK_TOKEN.sub("[hook-token]", redact(text))[-FINAL_TAIL_CHARS:]

    @staticmethod
    def _cleanup_files(state_dir: Path | None) -> None:
        if state_dir is not None:
            shutil.rmtree(state_dir, ignore_errors=True)

    # -- steering ----------------------------------------------------------

    def _live_for(self, owner: str, session_id: str) -> LiveCli:
        self.row(owner, session_id)
        live = self._live.get(session_id)
        if live is None:
            raise CliSessionError(409, "This CLI session has ended")
        return live

    async def _write(self, live: LiveCli, text: str, submit: bool) -> None:
        session = self.manager.get(live.pty_id)
        if session is None or session.exited:
            raise CliSessionError(409, "This CLI session has ended")
        text = _clean_input(text)
        if len(text) > MAX_SEND_CHARS:
            raise CliSessionError(400, f"Keep input under {MAX_SEND_CHARS} characters")
        if text:
            data = text.encode("utf-8", "replace")
            # Multi-line text is pasted so the TUI does not submit at each line break.
            session.write(_PASTE_START + data + _PASTE_END if "\n" in text else data)
        if submit:
            if text:
                await asyncio.sleep(0.15)
            session.write(b"\r")
            live.tracker.input_sent(time.monotonic())

    async def send(self, owner: str, session_id: str, text: str, submit: bool = True) -> dict[str, Any]:
        live = self._live_for(owner, session_id)
        if not isinstance(text, str) or (not text and not submit):
            raise CliSessionError(400, "Provide text to send")
        await self._write(live, text, bool(submit))
        saved = self._persist(live)
        self._emit("cli.session.status", saved)
        return self.public(saved)

    async def interrupt(self, owner: str, session_id: str) -> dict[str, Any]:
        live = self._live_for(owner, session_id)
        session = self.manager.get(live.pty_id)
        if session is None or session.exited:
            raise CliSessionError(409, "This CLI session has ended")
        session.write(live.spec.interrupt)
        return self.get(owner, session_id)

    async def stop(self, owner: str, session_id: str) -> dict[str, Any]:
        """SIGINT, then SIGTERM, then SIGKILL, each after a grace period; the PTY is always reaped."""
        row = self.row(owner, session_id)
        live = self._live.get(session_id)
        if live is None:
            return self.public(row)
        live.stop_requested = True
        session = self.manager.get(live.pty_id)
        if session is not None and not session.exited:
            for name, wait in (("int", self.grace[0]), ("term", self.grace[1])):
                session.send_signal(name)
                if await self._wait_exit(live, session, wait):
                    break
            else:
                session.send_signal("kill")
                await self._wait_exit(live, session, 1.0)
        if live.task is not None and not live.finishing:
            # Let the monitor observe the exit and finish; fall back to finishing here.
            try:
                await asyncio.wait_for(asyncio.shield(live.task), timeout=max(2.0, self.tick * 5))
            except (asyncio.TimeoutError, TimeoutError):
                pass
        if live.id in self._live and not live.finishing:
            live.tracker.observe(now=time.monotonic(), quiet=None, tail="", exited=True, exit_code=None, stop_requested=True)
            if live.task is not None:
                live.task.cancel()
            self._persist(live)
            await self._finish(live, None, "stopped")
        # The monitor may be mid-finish (killing the terminal, persisting): wait for the record to settle.
        settle_by = time.monotonic() + 5.0
        while live.id in self._live and live.finishing and time.monotonic() < settle_by:
            await asyncio.sleep(0.05)
        return self.get(owner, session_id)

    async def _wait_exit(self, live: LiveCli, session, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            proc = session.proc
            poll = getattr(proc, "poll", None)
            if session.exited or (callable(poll) and poll() is not None):
                return True
            await asyncio.sleep(0.05)
        return False

    # -- reading and hooks -------------------------------------------------

    def read(self, owner: str, session_id: str, max_chars: int = DEFAULT_READ_CHARS) -> dict[str, Any]:
        """The cleaned, redacted tail of the TUI. UNTRUSTED: the CLI (or a file it read) wrote it."""
        from termx.agent.policy import redact

        row = self.row(owner, session_id)
        try:
            limit = max(200, min(int(max_chars), MAX_READ_CHARS))
        except (TypeError, ValueError):
            limit = DEFAULT_READ_CHARS
        live = self._live.get(session_id)
        session = self.manager.get(row["pty_session_id"]) if live is not None else None
        raw = (
            S.clean_text(session.output_tail(TAIL_BYTES)) if session is not None
            else self._tails.get(session_id) or row.get("final_tail") or ""
        )
        text = "\n".join(line.rstrip() for line in redact(raw).splitlines())
        text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
        truncated = len(text) > limit
        return {
            "session_id": session_id,
            "status": row["status"],
            "text": text[-limit:],
            "truncated": truncated,
            "untrusted": True,
        }

    def apply_hook(self, token: str, body: bytes) -> dict[str, Any]:
        resolved = self.hooks.resolve(token)
        if resolved is None:
            raise CliSessionError(403, "invalid hook token")
        session_id, cli = resolved
        live = self._live.get(session_id)
        if live is None:
            raise CliSessionError(403, "invalid hook token")
        try:
            event = H.parse_hook_payload(cli, body)
        except H.HookRejected as exc:
            raise CliSessionError(400, str(exc)) from None
        change = live.tracker.on_hook(event.status, event.detail)
        if change is not None:
            self._emit("cli.session.status", self._persist(live))
        return {"ok": True}

    # -- resume ----------------------------------------------------------------

    def resume_plan(
        self, owner: str, session_id: str, working_dir: str | None = None, *, mode: str = "default", allow_bypass: bool = False,
        settings_args: tuple[str, ...] = (),
    ) -> C.LaunchPlan:
        row = self.row(owner, session_id)
        executable = self.executable(row["cli"])
        if not executable:
            raise CliSessionError(409, "The CLI is not installed on this host")
        try:
            return C.build_resume(
                row["cli"], executable=executable, working_dir=working_dir or row["cwd"], native_session_id=row.get("native_session_id"),
                settings_args=settings_args, mode=mode, allow_bypass=allow_bypass,
            )
        except C.BypassRefused as exc:
            raise CliSessionError(403, str(exc)) from None
        except C.CatalogueError as exc:
            raise CliSessionError(400, str(exc)) from None

    async def resume(
        self, owner: str, session_id: str, *, confirm_bypass: bool = False, claim: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Start a NEW terminal that reopens the CLI's own saved conversation for an ended or lost session.

        The old record is kept (linked ``resumed_to``); the new one carries ``resumed_from``. A session that
        ran in bypass mode needs the same human re-confirmation as creation, in a restricted sandbox profile.
        """
        if self._closed:
            raise CliSessionError(503, "The host is shutting down")
        row = self.row(owner, session_id)
        if row.get("status") not in S.ENDED or session_id in self._live:
            raise CliSessionError(409, "This CLI session is still running; resume applies to ended or lost sessions")
        if row.get("resumed_to") and self.store.get(KIND, row["resumed_to"]) is not None:
            raise CliSessionError(409, "This CLI session was already resumed")
        try:
            spec = C.spec_for(row["cli"])
        except C.CatalogueError as exc:
            raise CliSessionError(400, str(exc)) from None
        if not row.get("resumable") or (spec.resume_args is None and spec.continue_args is None):
            raise CliSessionError(409, f"{spec.label} cannot resume a saved conversation")
        executable = self.executable(spec.id)
        if not executable:
            raise CliSessionError(409, f"{spec.label} is not installed on this host")
        mode = row.get("mode") or "default"
        sandbox_profile = row.get("sandbox_profile") or "host"
        allow_bypass = mode == "bypass" and sandbox_profile != "host" and confirm_bypass
        if mode == "bypass" and not allow_bypass:
            raise CliSessionError(
                403, "Resuming a permission-bypass session needs a restricted sandbox profile and a human confirmation"
            )
        working_dir = row.get("cwd") or ""
        if not working_dir or not os.path.isdir(working_dir):
            raise CliSessionError(409, "The session's folder no longer exists, so it cannot be resumed there")
        new_id = uuid.uuid4().hex
        session, plan, state_dir, token = self._open_terminal(
            spec=spec, session_id=new_id, executable=executable, working_dir=working_dir, sandbox_profile=sandbox_profile,
            title=row.get("title"), claim=claim, allow_bypass=allow_bypass,
            build=lambda extra: C.build_resume(
                spec.id, executable=executable, working_dir=working_dir, native_session_id=row.get("native_session_id"),
                settings_args=extra, mode=mode, allow_bypass=allow_bypass,
            ),
        )
        now = time.time()
        body = {
            key: row.get(key) for key in ("cli", "label", "title", "cwd", "source_cwd", "isolation", "worktree", "sandbox_profile", "mode")
        }
        body.update(
            pty_session_id=session.id, started_at=now, ended_at=None, exit_code=None, ended_reason=None,
            native_session_id=plan.native_session_id or row.get("native_session_id"), resumable=True,
            prompt_chars=0, prompt_digest=None, argv_display=plan.display[:300], resumed_from=session_id,
            status=S.STARTING, status_source="heuristic", status_detail=f"resumed from {session_id[:8]}", status_updated_at=now,
            hook_digest=self.hooks.digest_of(new_id) if token else None,
        )
        saved = self.store.create(KIND, owner, body, row.get("project_id"), new_id)
        tracker = S.StatusTracker(spec.id, lifecycle_hooks=spec.hooks == "claude-settings" and token is not None, **self.thresholds)
        live = LiveCli(new_id, owner, spec.id, session.id, {k: v for k, v in saved.items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}},
                       tracker, spec, state_dir)
        self._live[new_id] = live
        live.task = asyncio.get_running_loop().create_task(self._watch(live), name=f"cli-session-{new_id[:8]}")
        previous = {k: v for k, v in row.items() if k not in {"id", "owner", "project_id", "revision", "created_at", "updated_at"}}
        previous["resumed_to"] = new_id
        self.store.update(KIND, session_id, previous)
        log_event("cli_session_resume", session_id=new_id, resumed_from=session_id, cli=spec.id, mode=mode)
        self._emit("cli.session.created", saved)
        return self.public(saved)

    # -- shutdown ----------------------------------------------------------

    async def close(self) -> None:
        """Host is stopping. A held terminal survives (its record is left as it is, to be re-adopted by the next
        host); a direct terminal is about to be killed, so that session is ``lost``. Either way the last screen
        is persisted (redacted, bounded) so it stays readable."""
        self._closed = True
        for live in list(self._live.values()):
            if live.task is not None:
                live.task.cancel()
            for task in list(live.background):
                task.cancel()
            session = self.manager.get(live.pty_id)
            survives = bool(session is not None and getattr(session, "durable", False) and not session.exited)
            if session is not None:
                live.last_tail = S.clean_text(session.output_tail(TAIL_BYTES))
            live.body["final_tail"] = self._persistable_tail(live.last_tail)
            if survives:
                try:
                    self._persist(live)
                except Exception:  # noqa: BLE001 - the store may already be closing
                    pass
                continue  # no token revoke needed: tokens are memory-only and die with this process; files stay for the CLI
            live.tracker.lost("host stopped")
            live.body.update(ended_at=time.time(), ended_reason="lost")
            try:
                self._persist(live)
            except Exception:  # noqa: BLE001 - the store may already be closing
                pass
            self.hooks.revoke_session(live.id)
            self._cleanup_files(live.state_dir)
        self._live.clear()
