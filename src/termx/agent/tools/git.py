"""Structured Git tools backed by termx.git_ops."""
from __future__ import annotations

import asyncio
import re
import shutil
from typing import Any

from fastapi import HTTPException

from termx import git_ops
from termx.agent.policies.fingerprint import tool_key as _tool_key
from termx.agent.policy import PolicyDecision, is_sensitive_path
from termx.agent.providers import ProviderCall
from termx.agent.tools.helpers import (
    call_bool,
    call_string,
    denied_result,
    error_result,
    sandbox_profile,
    sandbox_spawn,
)
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


def _sanitize_status(status: dict[str, Any]) -> dict[str, Any]:
    """Strip protected filenames out of a git status payload."""
    files = status.get("files")
    if not isinstance(files, list):
        return status
    kept = [f for f in files if not is_sensitive_path(str(f.get("path", "")))]
    dropped = len(files) - len(kept)
    result = {**status, "files": kept}
    if dropped:
        result["filtered_sensitive"] = dropped
    return result


async def _run_mutating(
    ctx: ToolContext, *args: str, timeout: int = 120, stdin: Any = None
) -> None:
    """Run a mutating ``git`` command so task cancellation kills its process group.

    ``asyncio.to_thread`` cannot interrupt the subprocess inside ``git_ops``,
    so mutating actions run here where ``ctx.cancel`` terminates them — under
    the task's sandbox profile, never a second host-shell path.
    """
    if shutil.which("git") is None:
        raise HTTPException(400, "Git is not installed on this host")
    from termx.sandbox import SpawnSpec

    profile = sandbox_profile(ctx)
    spec = SpawnSpec(
        profile=profile,
        argv=("git", "-C", ctx.cwd, *args),
        cwd=ctx.cwd,
        workspace_root=ctx.cwd,
        writable_roots=[ctx.cwd],
        purpose=f"git_{args[0] if args else 'command'}",
        stdin=stdin,
    )
    spawned = await sandbox_spawn(ctx, profile).spawn(spec)
    process = spawned.process
    communicate = asyncio.create_task(process.communicate())
    cancel_wait = asyncio.create_task(ctx.cancel.wait())
    try:
        done, _ = await asyncio.wait(
            {communicate, cancel_wait}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_wait in done and communicate not in done:
            await spawned.terminate()
            raise asyncio.CancelledError
        if communicate not in done:
            await spawned.terminate()
            raise HTTPException(504, "Git command timed out")
        output = communicate.result()[0]
    finally:
        cancel_wait.cancel()
    if process.returncode != 0:
        detail = output.decode("utf-8", "replace").strip() or "git command failed"
        raise HTTPException(409, detail[:500])


async def _git_status(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    try:
        status = await asyncio.to_thread(git_ops.status, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **_sanitize_status(status)})


async def _git_diff(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    path = call_string(call, "path") or "."
    if path != "." and is_sensitive_path(path):
        return ToolOutcome(denied_result(path))
    try:
        diff = await asyncio.to_thread(git_ops.diff, ctx.cwd, path, call_bool(call, "staged"))
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    text, dropped = _filter_sensitive_diff(str(diff.get("diff") or ""))
    payload: dict[str, Any] = {"ok": True, **diff, "diff": text}
    if dropped:
        payload["filtered_sensitive"] = dropped
    return ToolOutcome(payload)


def _filter_sensitive_diff(diff_text: str) -> tuple[str, int]:
    """Drop per-file diff sections for credential/key/env paths."""
    if not diff_text:
        return diff_text, 0
    kept: list[str] = []
    dropped = 0
    for section in re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE):
        if not section.strip():
            continue
        header = section.splitlines()[0]
        match = re.match(r"diff --git a/(.+?) b/(.+)", header)
        if match and (is_sensitive_path(match.group(1)) or is_sensitive_path(match.group(2))):
            dropped += 1
            continue
        kept.append(section)
    return "".join(kept), dropped


async def _git_stage(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    paths = call.arguments.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return ToolOutcome(error_result("git_stage requires 'paths': a list of project-relative paths"))
    sensitive = [p for p in paths if is_sensitive_path(p)]
    if sensitive:
        return ToolOutcome(denied_result(sensitive[0]))
    unstage = call_bool(call, "unstage", False)
    try:
        # Directory args (".", "src/") stage every changed file beneath them —
        # expand via porcelain status and refuse if a protected path is inside.
        current = await asyncio.to_thread(git_ops.status, ctx.cwd)
        affected = [
            f["path"]
            for f in current.get("files", [])
            if any(
                p in (".", "", "./") or f["path"] == p or f["path"].startswith(p.rstrip("/") + "/")
                for p in paths
            )
        ]
        protected = [p for p in affected if is_sensitive_path(p)]
        if protected:
            return ToolOutcome(denied_result(protected[0]))
        args = (["restore", "--staged", "--"] if unstage else ["add", "--"]) + paths
        await _run_mutating(ctx, *args)
        status = await asyncio.to_thread(git_ops.status, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **_sanitize_status(status)})


async def _git_commit(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    message = call_string(call, "message").strip()
    if not message:
        return ToolOutcome(error_result("git_commit requires 'message'"))
    try:
        await _run_mutating(ctx, "commit", "-m", message)
        status = await asyncio.to_thread(git_ops.status, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **_sanitize_status(status)})


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
            args = ("switch", "-c", name) if call_bool(call, "create", False) else ("switch", name)
            await _run_mutating(ctx, *args)
            status = await asyncio.to_thread(git_ops.status, ctx.cwd)
        except Exception as exc:
            return ToolOutcome(_git_error(exc))
        return ToolOutcome({"ok": True, **_sanitize_status(status)})
    return ToolOutcome(error_result(f"unsupported git_branch action: {action}"))


def _engine_decide(
    call: ProviderCall,
    ctx: ToolContext,
    base: PolicyDecision,
    *,
    fingerprint: str,
    display: str,
    matcher: dict[str, Any],
    capabilities: tuple[str, ...] | None = None,
) -> PolicyDecision:
    engine = getattr(ctx, "policy_engine", None)
    if engine is not None:
        return engine.decide_tool(
            call,
            ctx,
            fingerprint=fingerprint,
            display=display,
            matcher=matcher,
            base=base,
            capabilities=capabilities,
        )
    return base


def _decide_git_stage(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    paths = call.arguments.get("paths")
    key = "|".join(sorted(p for p in paths if isinstance(p, str))) if isinstance(paths, list) else ""
    return _engine_decide(
        call,
        ctx,
        decide_never(call, ctx),
        fingerprint=_tool_key("git", "stage", key),
        display=f"git stage {key}".strip(),
        matcher={"action": "stage", "paths": paths if isinstance(paths, list) else []},
    )


def _decide_git_commit(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    base = make_always(
        "Project mutation",
        lambda c: f"Commits staged changes: {str(c.arguments.get('message') or '')[:200]}",
    )(call, ctx)
    return _engine_decide(
        call,
        ctx,
        base,
        fingerprint=_tool_key("git", "commit"),
        display="git commit",
        matcher={"action": "commit"},
    )


def _decide_git_branch(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    action = str(call.arguments.get("action") or "list")
    if action == "switch":
        name = str(call.arguments.get("name") or "").strip()
        create = bool(call.arguments.get("create"))
        detail = f"Creates and switches to branch {name}" if create else f"Switches to branch {name}"
        return _engine_decide(
            call,
            ctx,
            PolicyDecision(False, True, "Project mutation", detail),
            fingerprint=_tool_key("git", "branch", "switch", name, str(create)),
            display=detail,
            matcher={"action": "switch", "name": name, "create": create},
        )
    return decide_never(call, ctx)


def _decide_git_fetch(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    return _engine_decide(
        call,
        ctx,
        decide_never(call, ctx),
        fingerprint=_tool_key("git", "fetch"),
        display="git fetch",
        matcher={"action": "fetch"},
    )


def _decide_git_pull(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    return _engine_decide(
        call,
        ctx,
        decide_never(call, ctx),
        fingerprint=_tool_key("git", "pull"),
        display="git pull --ff-only",
        matcher={"action": "pull"},
    )


def _decide_git_push(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    base = make_always("External publication", "Pushes the current branch to the configured remote.")(call, ctx)
    return _engine_decide(
        call,
        ctx,
        base,
        fingerprint=_tool_key("git", "push"),
        display="git push",
        matcher={"action": "push"},
        capabilities=("process.execute", "git.publish", "net.outbound:any"),
    )


async def _git_fetch(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    try:
        await _run_mutating(ctx, "fetch", "--prune", timeout=90)
        status = await asyncio.to_thread(git_ops.status, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **_sanitize_status(status)})


async def _git_pull(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    try:
        # Fast-forward only, matching git_ops.pull.
        await _run_mutating(ctx, "pull", "--ff-only")
        status = await asyncio.to_thread(git_ops.status, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **_sanitize_status(status)})


async def _git_push(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    if ctx.read_only:
        return ToolOutcome(_read_only_result())
    try:
        await _run_mutating(ctx, "push")
        status = await asyncio.to_thread(git_ops.status, ctx.cwd)
    except Exception as exc:
        return ToolOutcome(_git_error(exc))
    return ToolOutcome({"ok": True, **_sanitize_status(status)})


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
            decide=_decide_git_stage,
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
            decide=_decide_git_commit,
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
            decide=_decide_git_fetch,
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
            decide=_decide_git_pull,
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
            decide=_decide_git_push,
            expose_read_only=False,
        )
    )
