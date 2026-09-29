"""`run_runbook` — agent-invoked runbook execution (PROD-006).

Executes a saved runbook's shell steps in the task's project boundary and
returns the persisted run record. Always requires user approval
(``approval="always"``); ``confirm:true`` steps still pause the run at
``awaiting_confirmation`` — the tool reports that state rather than bypassing
the human gate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from termx.agent.tools.helpers import call_string, sandbox_profile
from termx.agent.tools.registry import (
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    make_always,
)

_TERMINAL = {"completed", "failed", "cancelled"}


async def _run_runbook(call: Any, ctx: ToolContext) -> ToolOutcome:
    runbook_id = call_string(call, "runbook_id")
    runbook = ctx.store.get_runbook(runbook_id)
    if runbook is None:
        return ToolOutcome(result={"error": "runbook not found", "runbook_id": runbook_id})
    cwd = ctx.cwd
    project_id = runbook.get("project_id")
    if project_id:
        # A runbook bound to another project may not rewrite the task's
        # approved working directory — the agent's boundary is ctx.cwd.
        project = None
        if ctx.project_files is not None:
            try:
                project = ctx.project_files.project(project_id)
            except Exception:
                project = None
        if project is None:
            return ToolOutcome(
                result={"error": "runbook project not found", "project_id": project_id}
            )
        bound = str(Path(str(project.get("path") or "")).expanduser().resolve())
        if bound != str(Path(cwd).expanduser().resolve()):
            # Worktree tasks run inside an isolated checkout whose path differs
            # from the registered project — a runbook bound to the task's
            # recorded base repo is still within its boundary.
            record = ctx.store.task_worktree(ctx.task_id)
            base = (record or {}).get("base_repo")
            if not base or str(Path(str(base)).expanduser().resolve()) != bound:
                return ToolOutcome(
                    result={
                        "error": "runbook targets a different project than this task",
                        "runbook_id": runbook_id,
                        "project_id": project_id,
                    }
                )
    runner = getattr(ctx.manager, "runbooks", None)
    if runner is None:
        return ToolOutcome(result={"error": "runbook runner unavailable"})
    run = runner.start(runbook, cwd, profile=sandbox_profile(ctx))
    run_id = run["id"]
    while True:
        if ctx.cancel.is_set():
            await runner.cancel(run_id)
        current = ctx.store.get_runbook_run(run_id)
        if current is None:
            return ToolOutcome(result={"error": "run record vanished", "run_id": run_id})
        if current["status"] in _TERMINAL or current["status"] == "awaiting_confirmation":
            results = current.get("step_results") or []
            return ToolOutcome(
                result={
                    "run_id": run_id,
                    "status": current["status"],
                    "current_step": current["current_step"],
                    "steps_completed": sum(
                        1 for r in results if r.get("status") == "completed"
                    ),
                    "steps_total": len(runbook["steps"]),
                    "error": current.get("error"),
                    "needs_confirmation": current["status"] == "awaiting_confirmation",
                    "step_results": [
                        {
                            "index": r.get("index"),
                            "command": r.get("command"),
                            "status": r.get("status"),
                            "exit_code": r.get("exit_code"),
                            "output": (r.get("output") or "")[-2000:],
                        }
                        for r in results
                    ],
                }
            )
        await asyncio.sleep(0.25)


def _decide_runbook(call: Any, ctx: ToolContext):
    base = make_always(
        "Run runbook", lambda c: f"runbook {call_string(c, 'runbook_id')}"
    )(call, ctx)
    engine = getattr(ctx, "policy_engine", None)
    if engine is None:
        return base
    from termx.agent.policies.fingerprint import runbook_fingerprint

    runbook_id = call_string(call, "runbook_id")
    return engine.decide_tool(
        call,
        ctx,
        fingerprint=runbook_fingerprint(runbook_id),
        display=f"runbook {runbook_id}",
        matcher={"runbook_id": runbook_id},
        base=base,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="run_runbook",
            description=(
                "Run a saved runbook (a named multi-step shell workflow) by id. "
                "Returns the run record: status, per-step exit codes and output tails. "
                "Confirm-gated steps pause the run for the user."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "runbook_id": {"type": "string"},
                },
                "required": ["runbook_id"],
                "additionalProperties": False,
            },
            mutability="write",
            parallel_safe=False,
            approval="always",
            execute=_run_runbook,
            decide=_decide_runbook,
            expose_read_only=False,
        )
    )
