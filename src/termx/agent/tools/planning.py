"""Plan mode: the read-only exploration phase ends when the model calls `exit_plan`."""
from __future__ import annotations

from termx.agent.providers import ProviderCall
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec, decide_never


async def _exit_plan(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    markdown = str(call.arguments.get("plan_markdown") or "").strip()
    if not markdown:
        return ToolOutcome({"ok": False, "error": "plan_markdown is required"})
    ctx.manager.submit_plan(ctx.task_id, markdown)
    return ToolOutcome(
        {"ok": True, "message": "Plan submitted. Wait for the user to approve or request changes."},
        event={"name": call.name},
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="exit_plan",
            description=(
                "Plan mode only. Call this once you have explored enough to propose a plan. "
                "Pass the full plan as markdown: the goal, the files to change, the steps in order, "
                "how to verify, and any risks. The user will approve it or ask for changes."
            ),
            parameters={
                "type": "object",
                "properties": {"plan_markdown": {"type": "string", "description": "The complete plan in markdown."}},
                "required": ["plan_markdown"],
                "additionalProperties": False,
            },
            mutability="read",
            parallel_safe=False,
            approval="never",
            execute=_exit_plan,
            decide=decide_never,
            plan_only=True,
        )
    )
