"""Project tooling utilities: preview_list, machine_info."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException

from termx.agent.providers import ProviderCall
from termx.agent.tools.helpers import error_result, http_error_result
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec, decide_never
from termx.machine import machine_snapshot


async def _preview_list(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    try:
        previews = await asyncio.to_thread(ctx.project_files.previews, ctx.project_id)
    except HTTPException as exc:
        return ToolOutcome(http_error_result(exc))
    except Exception as exc:
        return ToolOutcome(error_result(str(exc)))
    return ToolOutcome({"ok": True, "previews": previews})


async def _machine_info(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    try:
        snapshot = await asyncio.to_thread(machine_snapshot)
    except Exception as exc:
        return ToolOutcome(error_result(str(exc)))
    return ToolOutcome({"ok": True, "machine": snapshot})


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="preview_list",
            description="List the preview URLs registered for this project.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_preview_list,
            decide=decide_never,
        )
    )
    registry.register(
        ToolSpec(
            name="machine_info",
            description="Describe the host machine: OS, capabilities (desktop, PTY, webview), and hardware summary.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_machine_info,
            decide=decide_never,
        )
    )
