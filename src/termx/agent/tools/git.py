"""Structured Git tools backed by termx.git_ops."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from termx import git_ops
from termx.agent.policy import PolicyDecision
from termx.agent.providers import ProviderCall
from termx.agent.tools.helpers import call_bool, call_string, denied_result, error_result
from termx.agent.tools.registry import (
    ToolContext,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    decide_never,
    make_always,
)


def _git_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        result: dict[str, Any] = {"ok": False, "output": str(exc.detail)}
        if exc.status_code == 409:
            result["conflict"] = True
        return result
    return error_result(str(exc))


def _read_only_result() -> dict[str, Any]:
    return denied_result("", "Task is running in read-only mode")


async def _git_status(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    try:
        status = await asyncio.to_thread(git_ops.status, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **status})


async def _git_diff(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    path = call_string(call, "path") or "."
    try:
        diff = await asyncio.to_thread(git_ops.diff, ctx.cwd, path, call_bool(call, "staged"))
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **diff})


async def _git_stage(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    paths = call.arguments.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return ToolOutcome(error_result("git_stage requires 'paths': a list of project-relative paths"))
    unstage = call_bool(call, "unstage", False)
    try:
        status = await asyncio.to_thread(git_ops.stage, ctx.cwd, paths, not unstage)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **status})


async def _git_commit(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    message = call_string(call, "message").strip()
    if not message:
        return ToolOutcome(error_result("git_commit requires 'message'"))
    try:
        status = await asyncio.to_thread(git_ops.commit, ctx.cwd, message)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **status})


async def _git_branch(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    action = call_string(call, "action", "list") or "list"
    if action == "list":
        try:
            branches = await asyncio.to_thread(git_ops.branches, ctx.cwd)
        except Exception as exc:
            return ToolOutcome(_git_error(exc))
        return ToolOutcome({"ok": True, **branches})
    if action == "switch":
        if ctx.read_only:
            return ToolOutcome(_read_only_result())
        name = call_string(call, "name").strip()
        if not name:
            return ToolOutcome(error_result("git_branch switch requires 'name'"))
        try:
            status = await asyncio.to_thread(
                git_ops.switch_branch, ctx.cwd, name, call_bool(call, "create", False)
            )
        except Exception as exc:
            return ToolOutcome(_git_error(exc))
        return ToolOutcome({"ok": True, **status})
    return ToolOutcome(error_result(f"unsupported git_branch action: {action}"))


def _decide_git_branch(call: ProviderCall, _ctx: ToolContext) -> PolicyDecision:
    action = str(call.arguments.get("action") or "list")
    if action == "switch":
        name = str(call.arguments.get("name") or "").strip()
        create = bool(call.arguments.get("create"))
        detail = f"Creates and switches to branch {name}" if create else f"Switches to branch {name}"
        return PolicyDecision(False, True, "Project mutation", detail)
    return decide_never(call, _ctx)


async def _git_fetch(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    try:
        status = await asyncio.to_thread(git_ops.fetch, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **status})


async def _git_pull(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    try:
        status = await asyncio.to_thread(git_ops.pull, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **status})


async def _git_push(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    try:
        status = await asyncio.to_thread(git_ops.push, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **status})


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="git_status",
            description="Show the git status (branch, staged/unstaged changes, ahead/behind) of the project.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_git_status,
            decide=decide_never,
        )
    )
    registry.register(
        ToolSpec(
            name="git_diff",
            description="Show the git diff for the project or one path; set staged for the index.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project-relative path; default is the whole project."},
                    "staged": {"type": "boolean", "description": "Diff staged changes instead of the worktree."},
                },
                "additionalProperties": False,
            },
            mutability="read",
            parallel_safe=True,
            approval="never",
            execute=_git_diff,
            decide=decide_never,
        )
    )
    registry.register(
        ToolSpec(
            name="git_stage",
            description="Stage project paths for commit (or unstage with 'unstage').",
            parameters={
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Project-relative paths."},
                    "unstage": {"type": "boolean", "description": "Unstage the paths instead of staging them."},
                },
                "required": ["paths"],
                "additionalProperties": False,
            },
            mutability="write",
            parallel_safe=False,
            approval="policy",
            execute=_git_stage,
            decide=decide_never,
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="git_commit",
            description="Commit the staged changes with a message.",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
            mutability="write",
            parallel_safe=False,
            approval="always",
            execute=_git_commit,
            decide=make_always(
                "Project mutation",
                lambda call: f"Commits staged changes: {str(call.arguments.get('message') or '')[:200]}",
            ),
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="git_branch",
            description="List branches or switch branch (action: list|switch; 'create' makes a new branch).",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "switch"]},
                    "name": {"type": "string", "description": "Branch name when action is 'switch'."},
                    "create": {"type": "boolean", "description": "Create the branch when switching."},
                },
                "additionalProperties": False,
            },
            mutability="write",
            parallel_safe=False,
            approval="policy",
            execute=_git_branch,
            decide=_decide_git_branch,
        )
    )
    registry.register(
        ToolSpec(
            name="git_fetch",
            description="Fetch the latest objects from the remote (prunes stale branches).",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            mutability="write",
            parallel_safe=False,
            approval="never",
            execute=_git_fetch,
            decide=decide_never,
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="git_pull",
            description="Fast-forward pull the current branch (never merges or rebases).",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            mutability="write",
            parallel_safe=False,
            approval="never",
            execute=_git_pull,
            decide=decide_never,
            expose_read_only=False,
        )
    )
    registry.register(
        ToolSpec(
            name="git_push",
            description="Push the current branch to its configured remote.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            mutability="external",
            parallel_safe=False,
            approval="always",
            execute=_git_push,
            decide=make_always("External publication", "Pushes the current branch to the configured remote."),
            expose_read_only=False,
        )
    )
