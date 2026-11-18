"""Always-on host health: what an operator, a watchdog and the app need to trust a host that never sleeps.

One service per host that

* re-adopts terminals and CLI sessions a previous daemon left running in the PTY holder (startup),
* keeps the machine awake while agent work runs (a policy over ``KeepAwake`` leases),
* answers ``host_status`` / ``host_status_events`` (GraphQL, ``machine-view``) with uptime, version, the
  service supervisor, the holder, recovery counts, keep-awake state and the last heartbeat, and
* writes ``<config>/heartbeat.json`` every ~10 s (atomic replace) for watchdogs that cannot authenticate.

The heartbeat file holds only non-secret liveness facts: pid, version, port, timestamps.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from termx import __version__
from termx.audit import log_event
from termx.config import atomic_write, config_dir
from termx.keep_awake import KeepAwake

HEARTBEAT_FILE = "heartbeat.json"
HEARTBEAT_INTERVAL_S = 10.0
POLL_INTERVAL_S = 5.0
RUNNING_TASK_STATUSES = ("planning", "running", "recovering")
RECOVERING_TASK_STATUSES = ("recovering", "recovery_confirmation_required")

REASON_SETTING = "Keep awake is turned on in the host settings"
REASON_TASKS = "An agent task is running"
REASON_CLI = "A CLI session is working"


class HostStatusService:
    def __init__(
        self, state: Any, *, keep_awake: KeepAwake | None = None, heartbeat_s: float = HEARTBEAT_INTERVAL_S,
        poll_s: float = POLL_INTERVAL_S,
    ) -> None:
        self.state = state
        self.keep_awake = keep_awake or KeepAwake()
        self.heartbeat_s = heartbeat_s
        self.poll_s = poll_s
        self.started_at = time.time()
        self.last_heartbeat: float | None = None
        self.heartbeat_error: str | None = None
        self._tokens: dict[str, str] = {}
        self._tasks: list[asyncio.Task] = []
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------------

    @property
    def heartbeat_path(self) -> Path:
        return config_dir() / HEARTBEAT_FILE

    async def start(self) -> None:
        """Re-adopt what survived the last run, then begin the heartbeat and the keep-awake policy."""
        self.started_at = time.time()
        self._stopping = False
        sessions = getattr(self.state, "sessions", None)
        terminals = 0
        if sessions is not None:
            try:
                adopted = sessions.reattach()
                terminals = len(adopted or ())
                if adopted:
                    log_event("terminals_reattached", count=len(adopted))
            except Exception:  # noqa: BLE001 - never block startup on the holder
                log_event("terminals_reattach_error")
        cli = getattr(self.state, "cli_sessions", None)
        recovered: dict[str, int] = {}
        if cli is not None:
            try:
                recovered = await cli.recover() or {}
            except Exception:  # noqa: BLE001
                log_event("cli_sessions_recover_error")
        self._announce_fallback()
        self._push_recovered(terminals, recovered)
        await asyncio.to_thread(self.write_heartbeat)
        self.evaluate()
        if any(not task.done() for task in self._tasks):
            return  # already running (start is idempotent)
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._heartbeat_loop(), name="host-heartbeat"), loop.create_task(self._policy_loop(), name="host-keep-awake")]

    async def close(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.keep_awake.close()
        self._tokens.clear()
        try:
            await asyncio.to_thread(self.write_heartbeat, "stopping")
        except Exception:  # noqa: BLE001
            pass

    def _push_recovered(self, terminals: int, cli: dict[str, int]) -> None:
        """One push ``host.recovered`` when a restart re-adopted or lost work: counts only, nothing from any session."""
        parts: list[str] = []
        plain = max(0, terminals - int(cli.get("adopted", 0)) - int(cli.get("finished", 0)))
        for count, text in ((plain, "terminal{s} kept"), (cli.get("adopted", 0), "CLI session{s} re-attached"),
                            (cli.get("finished", 0), "CLI session{s} finished while the host was down"), (cli.get("lost", 0), "CLI session{s} lost")):
            if count:
                parts.append(f"{count} " + text.format(s="" if count == 1 else "s"))
        try:
            tasks = len(self.state.agent.pending_recoveries())
        except Exception:  # noqa: BLE001
            tasks = 0
        if tasks:
            parts.append(f"{tasks} task{'' if tasks == 1 else 's'} recovering")
        push = getattr(self.state, "push", None)
        if not parts or push is None:
            return
        try:
            push.enqueue("host.recovered", "host", "TermX restarted and recovered work", "; ".join(parts) + ".", key=str(int(self.started_at)))
        except Exception:  # noqa: BLE001 - a notification problem never blocks startup
            log_event("host_recovered_push_error")

    def _announce_fallback(self) -> None:
        """Tell the owner, once, when terminals will NOT survive a restart (never a silent downgrade)."""
        holder = getattr(getattr(self.state, "sessions", None), "holder", None)
        if holder is None:
            return
        try:
            status = holder.status()
        except Exception:  # noqa: BLE001
            return
        if status["mode"] == "direct" and status["requested"] != "direct" and status.get("reason"):
            notifications = getattr(self.state, "notifications", None)
            if notifications is not None:
                notifications.publish("Terminals will end when this host restarts", str(status["reason"])[:300], kind="warning", source="host")

    # -- keep-awake policy ----------------------------------------------------------

    def desired_reasons(self) -> set[str]:
        reasons: set[str] = set()
        try:
            if self.state.store.get().host.keep_awake:
                reasons.add(REASON_SETTING)
        except Exception:  # noqa: BLE001
            pass
        if self._running_tasks():
            reasons.add(REASON_TASKS)
        if self._working_cli_sessions():
            reasons.add(REASON_CLI)
        return reasons

    def _running_tasks(self) -> int:
        store = getattr(self.state, "agent_store", None)
        if store is None:
            return 0
        try:
            return len(store.task_page(limit=100, statuses=RUNNING_TASK_STATUSES))
        except Exception:  # noqa: BLE001 - a closing store counts as nothing running
            return 0

    def _working_cli_sessions(self) -> int:
        from termx.cli_sessions import status as S

        cli = getattr(self.state, "cli_sessions", None)
        if cli is None:
            return 0
        return sum(1 for live in list(cli._live.values()) if live.tracker.status in (S.STARTING, S.WORKING))

    def evaluate(self) -> None:
        """Make the held leases match the reasons that apply now (one lease per reason)."""
        want = self.desired_reasons()
        for reason in [reason for reason in self._tokens if reason not in want]:
            self.keep_awake.release(self._tokens.pop(reason))
        for reason in sorted(want - set(self._tokens)):
            self._tokens[reason] = self.keep_awake.acquire(reason)
        self.keep_awake.refresh()

    async def _policy_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.poll_s)
            try:
                await asyncio.to_thread(self.evaluate)
            except Exception:  # noqa: BLE001
                log_event("keep_awake_policy_error")

    # -- heartbeat ------------------------------------------------------------------

    def write_heartbeat(self, state: str = "running") -> None:
        now = time.time()
        payload = {
            "schema": 1, "state": state, "pid": os.getpid(), "version": __version__, "port": getattr(self.state, "port", None),
            "at": now, "started_at": self.started_at, "interval_s": self.heartbeat_s,
        }
        try:
            atomic_write(self.heartbeat_path, json.dumps(payload))
        except OSError as exc:
            self.heartbeat_error = str(exc)[:160]
            raise
        self.last_heartbeat = now
        self.heartbeat_error = None

    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.heartbeat_s)
            try:
                await asyncio.to_thread(self.write_heartbeat)
            except Exception:  # noqa: BLE001 - a full disk must not stop the host; the status shows the error
                log_event("heartbeat_error")

    # -- status ---------------------------------------------------------------------

    def _service_state(self) -> dict[str, Any]:
        from termx.host_service import HostService

        try:
            described = HostService().describe()
        except Exception:  # noqa: BLE001 - unreadable service files are reported as not installed
            described = {"backend": "unsupported", "supported": False, "installed": False}
        runtime = getattr(self.state, "host_runtime", None) or {}
        managed = bool(runtime.get("managed"))
        if managed:
            state = "running_managed"
        elif described["installed"]:
            state = "installed_not_managing"
        else:
            state = "not_installed"
        return {"backend": described["backend"], "supported": described["supported"], "installed": described["installed"], "managed": managed, "state": state}

    def _holder_state(self) -> dict[str, Any]:
        sessions = getattr(self.state, "sessions", None)
        holder = getattr(sessions, "holder", None)
        if holder is None:
            status = {"mode": "direct", "requested": "direct", "reason": "no PTY holder is configured", "running": False, "pid": None,
                      "version": None, "protocol": None, "sessions": 0, "exited_sessions": 0, "draining": False}
        else:
            status = holder.status()
        terminals = sessions.list() if sessions is not None else []
        status["direct_terminals"] = sum(1 for item in terminals if not getattr(item, "durable", False) and not item.exited)
        # The saved choice, and whether TERMX_PTY_HOLDER overrides it: ``requested`` is what is in force.
        try:
            status["setting"] = self.state.store.get().host.pty_holder
        except Exception:  # noqa: BLE001
            status["setting"] = "auto"
        status["env_override"] = bool((os.environ.get("TERMX_PTY_HOLDER") or "").strip())
        return status

    def _recovery(self) -> dict[str, Any]:
        from termx.cli_sessions import status as S
        from termx.cli_sessions.service import KIND

        lost = resumable = live = 0
        cli = getattr(self.state, "cli_sessions", None)
        if cli is not None:
            try:
                rows = cli.store.list(KIND)
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows:
                if row.get("status") == S.LOST:
                    lost += 1
                    resumable += bool(row.get("resumable")) and not row.get("resumed_to")
            live = len(cli._live)
        store = getattr(self.state, "agent_store", None)
        try:
            recovering = len(store.task_page(limit=100, statuses=RECOVERING_TASK_STATUSES)) if store is not None else 0
        except Exception:  # noqa: BLE001
            recovering = 0
        engine_lost = engine_resumable = 0
        try:
            for record in store.list_engine_sessions("lost") if store is not None else []:
                engine_lost += 1
                engine_resumable += bool((record.get("payload") or {}).get("resumable"))
        except Exception:  # noqa: BLE001
            pass
        return {"recovering_tasks": recovering, "running_tasks": self._running_tasks(), "lost_cli_sessions": lost,
                "resumable_cli_sessions": resumable, "live_cli_sessions": live,
                "lost_engine_sessions": engine_lost, "resumable_engine_sessions": engine_resumable}

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        awake = self.keep_awake.status()
        try:
            enabled = bool(self.state.store.get().host.keep_awake)
        except Exception:  # noqa: BLE001
            enabled = False
        return {
            "version": __version__,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "uptime_s": max(0.0, now - self.started_at),
            "service": self._service_state(),
            "holder": self._holder_state(),
            "recovery": self._recovery(),
            "keep_awake": {"enabled": enabled, **awake},
            "heartbeat": {
                "at": self.last_heartbeat, "age_s": None if self.last_heartbeat is None else max(0.0, now - self.last_heartbeat),
                "interval_s": self.heartbeat_s, "file": HEARTBEAT_FILE, "error": self.heartbeat_error,
            },
        }

    def update_settings(self, keep_awake: bool | None = None, pty_holder: str | None = None) -> dict[str, Any]:
        prefs = self.state.store.update_host(keep_awake=keep_awake, pty_holder=pty_holder)
        log_event("host_settings_changed", keep_awake=prefs.keep_awake, pty_holder=prefs.pty_holder)
        self.evaluate()
        return self.snapshot()


# Fields that change every poll and so never count as a "change" for the event stream.
VOLATILE = ("uptime_s",)


def stable_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The snapshot without per-second noise, for change detection."""
    view = {key: value for key, value in snapshot.items() if key not in VOLATILE}
    heartbeat = dict(view.get("heartbeat") or {})
    heartbeat.pop("age_s", None)
    heartbeat.pop("at", None)
    view["heartbeat"] = heartbeat
    return view
