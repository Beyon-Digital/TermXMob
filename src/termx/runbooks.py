"""Runbooks (PROD-006) — named multi-step shell workflows with run history.

A runbook is ``{name, project_id?, steps[]}`` where each step is
``{kind:"shell", command, confirm?, parallel?}``. Runs execute sequentially;
consecutive ``parallel:true`` steps form a batch run concurrently. Any nonzero
exit stops the run (stop-on-failure). A ``confirm:true`` step pauses the run
at ``awaiting_confirmation`` until ``confirm()`` is called — consequential
steps always get an explicit human gate, including agent-invoked runs.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from typing import Any

MAX_STEP_OUTPUT = 48 * 1024
STEP_TIMEOUT_S = 900


def validate_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps or len(steps) > 50:
        raise ValueError("steps must be a list of 1..50 steps")
    out: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        if step.get("kind", "shell") != "shell":
            raise ValueError("only kind='shell' steps are supported")
        command = str(step.get("command") or "").strip()
        if not command:
            raise ValueError("each step needs a command")
        out.append(
            {
                "kind": "shell",
                "command": command,
                "confirm": bool(step.get("confirm", False)),
                "parallel": bool(step.get("parallel", False)),
            }
        )
    return out


class RunbookRunner:
    """Executes runbook steps as subprocesses and records history in the store."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._procs: dict[str, set[asyncio.subprocess.Process]] = {}
        self._confirm_events: dict[str, asyncio.Event] = {}

    def start(self, runbook: dict[str, Any], cwd: str) -> dict[str, Any]:
        run = self._store.create_runbook_run(runbook["id"], cwd=cwd)
        run_id = run["id"]
        task = asyncio.create_task(self._execute(run_id, runbook, cwd))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))
        return run

    def confirm(self, run_id: str) -> dict[str, Any]:
        run = self._store.get_runbook_run(run_id)
        if run is None:
            raise KeyError("run not found")
        if run["status"] == "awaiting_confirmation":
            self._store.update_runbook_run(run_id, status="running")
            event = self._confirm_events.get(run_id)
            if event is not None:
                event.set()
        return self._store.get_runbook_run(run_id) or run

    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = self._store.get_runbook_run(run_id)
        if run is None:
            raise KeyError("run not found")
        if run["status"] not in {"running", "awaiting_confirmation"}:
            return run
        event = self._confirm_events.get(run_id)
        if event is not None:
            event.set()  # unblock a confirm gate so the task can observe cancel
        for proc in list(self._procs.get(run_id, ())):
            self._kill_proc(proc)
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if (self._store.get_runbook_run(run_id) or {}).get("status") in {
            "running",
            "awaiting_confirmation",
        }:
            self._store.update_runbook_run(
                run_id, status="cancelled", finished_at=time.time(), error="Cancelled by user"
            )
        return self._store.get_runbook_run(run_id) or run

    async def shutdown(self) -> None:
        for run_id in list(self._tasks):
            try:
                await self.cancel(run_id)
            except Exception:
                pass

    @staticmethod
    def _kill_proc(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                proc.kill()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _execute_step(self, run_id: str, step: dict[str, Any], cwd: str) -> dict[str, Any]:
        started = time.monotonic()
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["preexec_fn"] = os.setsid
        proc = await asyncio.create_subprocess_shell(
            step["command"],
            cwd=cwd or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **kwargs,
        )
        self._procs.setdefault(run_id, set()).add(proc)
        output = b""
        try:
            try:
                chunks = await asyncio.wait_for(proc.communicate(), timeout=STEP_TIMEOUT_S)
                output = chunks[0] or b""
            except asyncio.TimeoutError:
                self._kill_proc(proc)
                return {
                    "command": step["command"],
                    "status": "failed",
                    "exit_code": None,
                    "output": "step timed out",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
        finally:
            procs = self._procs.get(run_id)
            if procs is not None:
                procs.discard(proc)
        if len(output) > MAX_STEP_OUTPUT:
            output = output[-MAX_STEP_OUTPUT:]
        return {
            "command": step["command"],
            "status": "completed" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "output": output.decode("utf-8", "replace"),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    async def _execute(self, run_id: str, runbook: dict[str, Any], cwd: str) -> None:
        steps = runbook["steps"]
        results: list[dict[str, Any]] = []
        index = 0
        status = "completed"
        error: str | None = None
        try:
            while index < len(steps):
                step = steps[index]
                group = [step]
                if step["parallel"]:
                    while (
                        index + len(group) < len(steps)
                        and steps[index + len(group)]["parallel"]
                        and not steps[index + len(group)]["confirm"]
                    ):
                        group.append(steps[index + len(group)])
                for member in group:
                    if member["confirm"]:
                        self._store.update_runbook_run(
                            run_id,
                            status="awaiting_confirmation",
                            current_step=index + group.index(member),
                            step_results=results,
                        )
                        event = self._confirm_events.setdefault(run_id, asyncio.Event())
                        await event.wait()
                        event.clear()
                        if (self._store.get_runbook_run(run_id) or {}).get("status") != "running":
                            status = "cancelled"
                            raise asyncio.CancelledError
                batch = [
                    self._execute_step(run_id, member, cwd) for member in group
                ]
                group_results = await asyncio.gather(*batch)
                for member, result in zip(group, group_results):
                    result["index"] = index + group.index(member)
                    results.append(result)
                index += len(group)
                failed = next((r for r in group_results if r["status"] != "completed"), None)
                self._store.update_runbook_run(
                    run_id, current_step=index - 1, step_results=results
                )
                if failed is not None:
                    status = "failed"
                    error = f"step {failed['index'] + 1} failed: {failed['command']}"
                    break
        except asyncio.CancelledError:
            status = "cancelled"
            error = error or "Cancelled by user"
            raise
        finally:
            self._confirm_events.pop(run_id, None)
            self._procs.pop(run_id, None)
            self._store.update_runbook_run(
                run_id,
                status=status if status != "cancelled" else "cancelled",
                current_step=len(results) - 1 if results else -1,
                step_results=results,
                error=error,
                finished_at=time.time(),
            )
