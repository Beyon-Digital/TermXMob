"""Interactive coding-CLI sessions: real TUIs in PTYs, tracked as first-class sessions.

A CLI session is a ``SessionManager`` terminal (so the existing ``/api/sessions/{id}/pty``
websocket streams the TUI) plus a durable ``cli_session`` workspace record carrying its
status, worktree and exit. The service owns launch (typed argv only), the status monitor,
steering (send / interrupt / stop with a SIGINT -> SIGTERM -> SIGKILL ladder) and the
restart rule: a record still live when the host starts again is marked ``lost``.

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
CLI_COLS, CLI_ROWS = 120, 32
LIST_LIMIT = 200
_PASTE_START, _PASTE_END = b"\x1b[200~", b"\x1b[201~"


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
        """After a host restart nothing from a previous run is live: mark those records ``lost``."""
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
    ) -> dict[str, Any]:
        if self._closed:
            raise CliSessionError(503, "The host is shutting down")
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
        session_id = uuid.uuid4().hex
        working_dir, worktree = await self._prepare_cwd(project_id, cwd, isolation, session_id)
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
                plan = C.build_launch(
                    spec.id, executable=executable, working_dir=working_dir, prompt=prompt, mode=mode, model=model,
                    extra_args=extra, allow_bypass=allow_bypass,
                )
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
        live.body.update(ended_at=time.time(), exit_code=exit_code, ended_reason=reason)
        saved = self._persist(live)
        self._live.pop(live.id, None)
        for task in list(live.background):
            task.cancel()
        log_event("cli_session_end", session_id=live.id, status=live.tracker.status, exit_code=exit_code)
        self._emit("cli.session.exited", saved)

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
        raw = S.clean_text(session.output_tail(TAIL_BYTES)) if session is not None else self._tails.get(session_id, "")
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

    # -- resume (A6 builds the restart story on this) ------------------------

    def resume_plan(self, owner: str, session_id: str, working_dir: str | None = None) -> C.LaunchPlan:
        row = self.row(owner, session_id)
        executable = self.executable(row["cli"])
        if not executable:
            raise CliSessionError(409, "The CLI is not installed on this host")
        try:
            return C.build_resume(
                row["cli"], executable=executable, working_dir=working_dir or row["cwd"], native_session_id=row.get("native_session_id")
            )
        except C.CatalogueError as exc:
            raise CliSessionError(400, str(exc)) from None

    # -- shutdown ----------------------------------------------------------

    async def close(self) -> None:
        """Host is stopping: its terminals are about to be killed, so what was live is ``lost``."""
        self._closed = True
        for live in list(self._live.values()):
            if live.task is not None:
                live.task.cancel()
            for task in list(live.background):
                task.cancel()
            live.tracker.lost("host stopped")
            live.body.update(ended_at=time.time(), ended_reason="lost")
            try:
                self._persist(live)
            except Exception:  # noqa: BLE001 - the store may already be closing
                pass
            self.hooks.revoke_session(live.id)
            self._cleanup_files(live.state_dir)
        self._live.clear()
