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
_STEP_OUTPUT_PUBLISH_BYTES = 64 * 1024


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

    def __init__(
        self,
        store: Any,
        *,
        runner_for: Any | None = None,
        policy_engine: Any | None = None,
        project_id_for: Any | None = None,
    ) -> None:
        self._store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._procs: dict[str, set[asyncio.subprocess.Process]] = {}
        self._profiles: dict[str, str] = {}
        self._grant_ctx: dict[str, tuple[str | None, str | None, str | None]] = {}
        self._confirm_events: dict[str, asyncio.Event] = {}
        if runner_for is None:
            from termx.sandbox import runner_for as _default

            runner_for = _default
        self._runner_for = runner_for
        self._policy_engine = policy_engine
        self._project_id_for = project_id_for
        # Runs left 'running' by a dead host have ambiguous in-flight steps;
        # mark them failed rather than replaying. 'awaiting_confirmation' runs
        # parked BEFORE their step started can safely resume on confirm().
        for run in store.running_runbook_runs():
            store.update_runbook_run(
                run["id"],
                status="failed",
                finished_at=time.time(),
                error="Host restarted while the run was in-flight",
            )

    def start(
        self,
        runbook: dict[str, Any],
        cwd: str,
        *,
        profile: str = "host",
        task_id: str | None = None,
        project_id: str | None = None,
        custom_agent_id: str | None = None,
    ) -> dict[str, Any]:
        run = self._store.create_runbook_run(
            runbook["id"], cwd=cwd, steps=runbook["steps"]
        )
        run_id = run["id"]
        self._profiles[run_id] = profile
        self._grant_ctx[run_id] = (task_id, project_id, custom_agent_id)
        task = asyncio.create_task(self._execute(run_id, runbook, cwd, profile=profile))
        self._tasks[run_id] = task
        task.add_done_callback(
            lambda _t, rid=run_id: (
                self._tasks.pop(rid, None),
                self._grant_ctx.pop(rid, None),
            )
        )
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
            elif run_id not in self._tasks:
                # Host restarted while parked: the gated step never ran, so
                # resuming from it replays nothing — only its pre-step pause.
                # current_step is the gated step; re-run it with the gate
                # already satisfied by this confirm.
                runbook = self._store.get_runbook(run["runbook_id"])
                if runbook is not None:
                    # Resume from the run's immutable step snapshot — edits
                    # made to the runbook while parked must not change what
                    # the already-confirmed gate covers.
                    snapshot = run.get("steps") or runbook["steps"]
                    runbook = dict(runbook, steps=snapshot)
                    current_step = run.get("current_step")
                    gated = int(current_step) if current_step is not None else -1
                    task = asyncio.create_task(
                        self._execute(
                            run_id,
                            runbook,
                            run.get("cwd") or "",
                            resume={
                                "index": max(0, gated),
                                "step_results": list(run.get("step_results") or []),
                                "skip_confirm_at": gated,
                            },
                            profile=self._profiles.get(run_id, "host"),
                        )
                    )
                    self._tasks[run_id] = task
                    task.add_done_callback(
                        lambda _t, rid=run_id: self._tasks.pop(rid, None)
                    )
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

    async def _execute_step(
        self,
        run_id: str,
        step: dict[str, Any],
        cwd: str,
        on_progress: Any | None = None,
        *,
        profile: str = "host",
    ) -> dict[str, Any]:
        started = time.monotonic()
        from termx.sandbox import SpawnSpec

        grants, network = self._step_grants(run_id, cwd, profile)
        spec = SpawnSpec(
            profile=profile,
            shell=step["command"],
            cwd=cwd or None,
            workspace_root=cwd or None,
            writable_roots=[cwd] if cwd else [],
            network=network,
            granted_capabilities=sorted(grants),
            task_id=(self._grant_ctx.get(run_id) or (None, None))[0],
            purpose="runbook_step",
        )
        proc = (await self._runner_for(profile).spawn(spec)).process
        self._procs.setdefault(run_id, set()).add(proc)
        tail = bytearray()

        def result(status: str, exit_code: int | None, output: str) -> dict[str, Any]:
            return {
                "command": step["command"],
                "status": status,
                "exit_code": exit_code,
                "output": output,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }

        pending = 0
        try:
            while True:
                remaining = STEP_TIMEOUT_S - (time.monotonic() - started)
                if remaining <= 0:
                    self._kill_proc(proc)
                    return result("failed", None, "step timed out")
                try:
                    chunk = await asyncio.wait_for(
                        proc.stdout.read(8192), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    self._kill_proc(proc)
                    return result("failed", None, "step timed out")
                if not chunk:
                    break
                tail.extend(chunk)
                if len(tail) > MAX_STEP_OUTPUT:
                    del tail[: len(tail) - MAX_STEP_OUTPUT]
                pending += len(chunk)
                if on_progress is not None and pending >= _STEP_OUTPUT_PUBLISH_BYTES:
                    pending = 0
                    on_progress(
                        result("running", None, bytes(tail).decode("utf-8", "replace"))
                    )
            await proc.wait()
        finally:
            procs = self._procs.get(run_id)
            if procs is not None:
                procs.discard(proc)
        return result(
            "completed" if proc.returncode == 0 else "failed",
            proc.returncode,
            bytes(tail).decode("utf-8", "replace"),
        )

    def _step_grants(
        self, run_id: str, cwd: str, profile: str
    ) -> tuple[frozenset[str], str]:
        """Effective capability grants + network mode for a step spawn.

        Remembered capability rules apply to runbook steps too — a runbook
        executed under a restricted profile gets exactly the grants the
        policy engine resolves, never more.
        """
        if self._policy_engine is None or profile == "host":
            return frozenset(), "none" if profile != "host" else "outbound"
        task_id, project_id, custom_agent_id = self._grant_ctx.get(
            run_id, (None, None, None)
        )
        if project_id is None and self._project_id_for is not None and cwd:
            try:
                project_id = self._project_id_for(cwd)
            except Exception:
                project_id = None
        try:
            grants = self._policy_engine.capability_grant_set(
                profile,
                task_id=task_id,
                project_id=project_id or "",
                custom_agent_id=custom_agent_id,
            )
        except Exception:
            grants = frozenset()
        network = "outbound" if any(
            c.startswith("net.outbound") for c in grants
        ) else "none"
        return frozenset(grants), network

    async def _execute(
        self,
        run_id: str,
        runbook: dict[str, Any],
        cwd: str,
        resume: dict[str, Any] | None = None,
        *,
        profile: str = "host",
    ) -> None:
        steps = runbook["steps"]
        results: list[dict[str, Any]] = list(
            (resume or {}).get("step_results") or []
        )
        index = int((resume or {}).get("index") or 0)
        skip_confirm_at = (resume or {}).get("skip_confirm_at")
        status = "completed"
        error: str | None = None
        try:
            while index < len(steps):
                step = steps[index]
                group = [step]
                if step.get("parallel"):
                    while (
                        index + len(group) < len(steps)
                        and steps[index + len(group)].get("parallel")
                        and not steps[index + len(group)].get("confirm")
                    ):
                        group.append(steps[index + len(group)])
                for member in group:
                    if (
                        member.get("confirm")
                        and index + group.index(member) != skip_confirm_at
                    ):
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
                live_group: dict[int, dict[str, Any]] = {}

                def make_progress(member_idx: int):
                    def _publish(result: dict[str, Any]) -> None:
                        result["index"] = member_idx
                        live_group[member_idx] = result
                        self._store.update_runbook_run(
                            run_id,
                            current_step=member_idx,
                            step_results=[
                                *results,
                                *sorted(
                                    live_group.values(), key=lambda r: r["index"]
                                ),
                            ],
                        )

                    return _publish

                batch = [
                    self._execute_step(
                        run_id,
                        member,
                        cwd,
                        on_progress=make_progress(index + group.index(member)),
                        profile=profile,
                    )
                    for member in group
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
        except Exception as exc:
            # A step that could not even spawn (sandbox construction, argv
            # resolution) must fail the run — never report completed.
            status = "failed"
            error = error or f"run aborted: {exc}"
        finally:
            self._confirm_events.pop(run_id, None)
            self._procs.pop(run_id, None)
            self._profiles.pop(run_id, None)
            self._store.update_runbook_run(
                run_id,
                status=status if status != "cancelled" else "cancelled",
                current_step=len(results) - 1 if results else -1,
                step_results=results,
                error=error,
                finished_at=time.time(),
            )
