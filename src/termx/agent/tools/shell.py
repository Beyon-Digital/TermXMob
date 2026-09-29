"""Streaming shell and check-runner tools."""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from termx.agent.execution import stream_shell
from termx.agent.policy import evaluate_shell, is_mutating_shell, redact
from termx.agent.tools.helpers import (
    call_int,
    call_string,
    error_result,
    sandbox_grants,
    sandbox_network,
    sandbox_profile,
    sandbox_spawn,
)
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec

if TYPE_CHECKING:
    from termx.agent.providers import ProviderCall

CHECK_OUTPUT_LIMIT = 64_000
STREAM_EVENT_LIMIT = 128_000
# Hold back this many trailing bytes from each emitted chunk so a secret split
# across pipe reads can never be persisted unredacted (redact() sees the join).
_STREAM_HOLD_BACK = 128
_ASK_REFUSAL = "Refused: Ask mode is read-only. Switch to Agent mode to change files or reach the network."


class _StreamRedactor:
    """Redact process output while a carry buffer guards chunk boundaries."""

    def __init__(self, hold_back: int = _STREAM_HOLD_BACK) -> None:
        self._pending = ""
        self._hold_back = hold_back

    def feed(self, text: str) -> str:
        self._pending += text
        if len(self._pending) <= self._hold_back:
            return ""
        emit, self._pending = self._pending[: -self._hold_back], self._pending[-self._hold_back :]
        return redact(emit)

    def flush(self) -> str:
        pending, self._pending = self._pending, ""
        return redact(pending)


async def _streamed_process(
    call: "ProviderCall",
    ctx: ToolContext,
    command: str,
    *,
    timeout_s: float,
) -> tuple[Any, int]:
    """Run ``command`` emitting process.* events as output arrives."""
    started = time.monotonic()
    ctx.emit(
        "process.started",
        {"call_id": call.call_id, "command": redact(command), "cwd": ctx.cwd},
    )
    emitted = 0
    redactor = _StreamRedactor()

    def on_output(data: bytes) -> None:
        nonlocal emitted
        if emitted >= STREAM_EVENT_LIMIT:
            return
        chunk = redactor.feed(data.decode("utf-8", "replace"))
        if not chunk:
            return
        emitted += len(chunk)
        ctx.emit(
            "process.output",
            {
                "call_id": call.call_id,
                "chunk": chunk,
                "truncated": emitted >= STREAM_EVENT_LIMIT,
            },
        )

    profile = sandbox_profile(ctx)
    result = await stream_shell(
        command,
        ctx.cwd,
        timeout_s=timeout_s,
        cancel=ctx.cancel,
        on_output=on_output,
        runner=sandbox_spawn(ctx, profile),
        profile=profile,
        workspace_root=ctx.cwd,
        network=sandbox_network(ctx, profile, call.call_id),
        granted_capabilities=sorted(sandbox_grants(ctx, profile, call.call_id)),
        task_id=str(getattr(ctx, "task_id", "") or "") or None,
    )
    tail = redactor.flush()
    if tail and emitted < STREAM_EVENT_LIMIT:
        emitted += len(tail)
        ctx.emit(
            "process.output",
            {
                "call_id": call.call_id,
                "chunk": tail,
                "truncated": emitted >= STREAM_EVENT_LIMIT,
            },
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    if result.cancelled:
        ctx.emit("process.cancelled", {"call_id": call.call_id, "duration_ms": duration_ms})
    elif result.timed_out:
        ctx.emit(
            "process.timed_out",
            {"call_id": call.call_id, "timeout_s": timeout_s, "duration_ms": duration_ms},
        )
    else:
        ctx.emit(
            "process.exited",
            {"call_id": call.call_id, "exit_code": result.exit_code, "duration_ms": duration_ms},
        )
    if ctx.metrics is not None:
        ctx.metrics.record_shell(duration_ms)
    return result, duration_ms


def _shell_timeout(call: "ProviderCall", ctx: ToolContext, default: float) -> float:
    limit = float(ctx.task["limits"]["shell_timeout_s"])
    requested = call.arguments.get("timeout_s")
    try:
        requested = float(requested) if requested is not None else default
    except (TypeError, ValueError):
        requested = default
    return min(limit, max(1.0, requested))


async def _run_shell(call: "ProviderCall", ctx: ToolContext) -> ToolOutcome:
    command = call_string(call, "command")
    if not command:
        raise ValueError("provider requested an empty shell command")
    if ctx.read_only and is_mutating_shell(command):
        # Ask mode is read-only: surface the refusal as a tool result so the
        # model can adjust rather than failing the whole conversation turn.
        return ToolOutcome(
            {"ok": False, "exit_code": None, "output": _ASK_REFUSAL, "refused": True}
        )
    timeout = _shell_timeout(call, ctx, float(ctx.task["limits"]["shell_timeout_s"]))
    result, duration_ms = await _streamed_process(call, ctx, command, timeout_s=timeout)
    public = result.public()
    public["duration_ms"] = duration_ms
    return ToolOutcome(public, cancelled=result.cancelled)


async def _run_check(call: "ProviderCall", ctx: ToolContext) -> ToolOutcome:
    kind = call_string(call, "kind") or "other"
    command = call_string(call, "command")
    if not command:
        return ToolOutcome(error_result("run_check requires 'command'"))
    if ctx.read_only:
        # Checks can mutate (fixtures, caches, installers) in ways the shell
        # heuristic cannot classify, so Ask mode refuses them outright.
        return ToolOutcome(
            {"ok": False, "exit_code": None, "output": _ASK_REFUSAL, "refused": True, "kind": kind}
        )
    timeout = _shell_timeout(call, ctx, 300.0)
    result, duration_ms = await _streamed_process(call, ctx, command, timeout_s=timeout)
    output = result.public()["output"]
    artifact: dict[str, Any] | None = None
    if len(output) > CHECK_OUTPUT_LIMIT:
        artifact = await asyncio.to_thread(
            ctx.store.save_artifact,
            ctx.task_id,
            "log",
            "text/plain",
            output.encode("utf-8", "replace"),
        )
        output = output[-CHECK_OUTPUT_LIMIT:]
    status = "passed" if result.exit_code == 0 else "failed"
    if result.cancelled:
        status = "cancelled"
    elif result.timed_out:
        status = "timed_out"
    tail = [line for line in output.splitlines() if line.strip()][-3:]
    payload: dict[str, Any] = {
        "kind": kind,
        "command": redact(command),
        "status": status,
        "exit_code": result.exit_code,
        "duration_ms": duration_ms,
        "summary": "\n".join(tail)[:500],
        "output": output,
        "truncated": result.truncated or len(result.output) > CHECK_OUTPUT_LIMIT,
    }
    if artifact is not None:
        payload["output_artifact"] = {
            "id": artifact["id"],
            "kind": artifact["kind"],
            "mime": artifact["mime"],
            "size": artifact["size"],
        }
    return ToolOutcome(payload, cancelled=result.cancelled)


def _decide_shell(call: "ProviderCall", ctx: ToolContext):
    command = call_string(call, "command")
    base = evaluate_shell(command, ctx.cwd)
    engine = getattr(ctx, "policy_engine", None)
    if engine is not None:
        return engine.decide_shell(call, ctx, command, base)
    return base


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="run_shell",
            description=(
                "Run one shell command in the approved project folder. Use it to inspect files, "
                "edit with repository-native tools, and verify work. Consequential commands pause for approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "purpose": {"type": "string"},
                    "timeout_s": {"type": "number", "minimum": 1, "maximum": 600},
                },
                "required": ["command", "purpose"],
                "additionalProperties": False,
            },
            description_read_only=(
                "Run one read-only shell command in the approved project folder to inspect files "
                "and answer the question. Do not modify files or reach the network."
            ),
            mutability="write",
            parallel_safe=False,
            approval="policy",
            execute=_run_shell,
            decide=_decide_shell,
        )
    )
    registry.register(
        ToolSpec(
            name="run_check",
            description=(
                "Run a project check (test, lint, typecheck, build) and report a structured "
                "result with status, duration, and a summary. Prefer this over run_shell for "
                "verification commands."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["test", "lint", "typecheck", "build", "other"]},
                    "command": {"type": "string"},
                    "timeout_s": {"type": "number", "minimum": 1, "maximum": 600},
                },
                "required": ["kind", "command"],
                "additionalProperties": False,
            },
            mutability="write",
            parallel_safe=False,
            approval="policy",
            execute=_run_check,
            decide=_decide_shell,
            expose_read_only=False,
        )
    )
