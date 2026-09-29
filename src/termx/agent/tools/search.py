"""Structured project search tool backed by ProjectFiles.search."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException

from termx.agent.providers import ProviderCall
from termx.agent.tools.helpers import call_bool, call_string, error_result, http_error_result
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec, decide_never


async def _search_project(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    query = call_string(call, "query")
    if not query:
        return ToolOutcome(error_result("search_project requires 'query'"))
    content = call_bool(call, "content", True)
    case_sensitive = call_bool(call, "case_sensitive", False)
    try:
        result = await asyncio.to_thread(
            ctx.project_files.search,
            ctx.project_id,
            query,
            content=content,
            case_sensitive=case_sensitive,
        )
    except HTTPException as exc:
        return ToolOutcome(http_error_result(exc))
    return ToolOutcome(
        {
            "ok": True,
            "query": query,
            "matches": result.get("results") or [],
            "truncated": bool(result.get("truncated")),
        }
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="search_project",
            description="Search project files for a text pattern. Prefer this over `grep`/`rg`.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for."},
                    "content": {
                        "type": "boolean",
                        "description": "Search file contents (default true); false searches file names.",
                    },
                    "case_sensitive": {"type": "boolean", "description": "Case-sensitive match (default false)."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_search_project,
            decide=decide_never,
        )
    )
